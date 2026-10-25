"""EviCon owner gateway for MinIO (S3 API): experiment C, release 073.

One gateway per device speaks the EviCon device-owner protocol of cmd/evicon-device
(internal/device/server.go) to one authority, and turns each request into the S3 calls whose
evidence contract release 070 MEASURED on the testbed's MinIO (conformance/configs/minio-*.json):

  POST /v1/effects        PUT <bucket>/<prefix>/<command_id>, body {"command_id","value","intent_hash"},
                          metadata evicon-kind=effect, evicon-intent, evicon-value
                            COND : with If-None-Match: *. 200 -> APPLIED. 412 -> HEAD: an effect with the
                                   same intent -> APPLIED (replay), a tombstone -> STOPPED
                            PLAIN: unconditional; 200 -> APPLIED (a repeated PUT is a new version: a second effect)
  GET  /v1/receipts/<id>  COND: HEAD of the key: effect -> APPLIED, tombstone -> STOPPED, 404 -> 404 (Q_A)
                          PLAIN: 409 UNSUPPORTED (the 070 measurement contradicted Q_A for an unconditional PUT)
  POST /v1/stop           COND: PUT of an empty tombstone under the key with If-None-Match: *; 200 -> STOPPED,
                          412 -> HEAD as above (Q_S). PLAIN: 409 STOP_UNSUPPORTED
  POST /v1/barriers       409 BARRIER_UNSUPPORTED (S3 has no owner fence)
  GET  /v1/capabilities   profile DEVICE, ADD only, and the contract measured in 070 for this call shape
  GET  /healthz           role "gateway", this release, the pid

The gateway keeps no state of its own: the object store is the only record, so a gateway that
crashed and restarted answers exactly as before. The owner is MinIO; the gateway is the client-side
adapter (like an IoT platform's command service in front of a device). Every fault of experiment C
stays inside the fault model of the 070 measurement ("network loss/delay, client abandon; owner up"):
a gateway crash is a client abandon, the offline window is network loss on the path to MinIO.

Answers that are not receipts: 504 OWNER_TIMEOUT (MinIO did not answer within the budget: the
effect may or may not have happened), 503 OWNER_UNREACHABLE (no connection), 502 OWNER_ERROR (any
other S3 status), 409 IDENTITY_CONFLICT (the key holds another intent). The authority treats them
as a failed attempt, exactly like the device's 503 DEVICE_OFFLINE.

Admin (test-only, its own port):
  POST /_test/online?v=false|true   the path gateway -> MinIO (an in-process TCP fault proxy) drops
                                    every byte in both directions / passes again
  POST /_test/crash?at=AFTER_EFFECT_BEFORE_RECEIPT   the next effect PUT that MinIO answers 200 makes
                                    the gateway exit (status 17) before it replies; at=NONE disarms
  GET  /_debug/state

Credentials: the environment variables named by --access-key-env / --secret-key-env (default
MINIO_ROOT_USER / MINIO_ROOT_PASSWORD), set by the runner; never on the command line, never logged.
Log: one JSON line per request in --log (no credentials, no bodies).

  python3 conformance/reference/s3_owner_gateway.py --mode COND --device-id pump0-1 \\
      --listen 127.0.0.1:18100 --admin-listen 127.0.0.1:18101 --s3-url http://192.168.107.152:9000 \\
      --bucket evc-... --contract-file experiments/owners/minio/meas-minio-cond-070.json --log gateway.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))  # conformance/: evicon_probe, reference

from evicon_probe.faults import DOWN, UP, FaultProxy  # noqa: E402
from reference._health import release  # noqa: E402
from reference.s3_client import S3, TIMEOUT  # noqa: E402

ROLE = "gateway"
CRASH_POINTS = ("AFTER_EFFECT_BEFORE_RECEIPT", "AFTER_RECEIPT_BEFORE_REPLY")  # one point for S3: the object is the receipt
EFFECT, TOMBSTONE = "effect", "tombstone"


def canonical_hash(req: dict) -> str:
    """protocol.CommandRequest.CanonicalHash (Go json.Marshal of canonicalIntent): compact JSON, fields
    in struct order. Command IDs, domain IDs and operations here are plain ASCII."""
    c = {"schema_version": req.get("schema_version", 1), "domain_id": req.get("domain_id", ""), "generation": req.get("generation", 0),
         "command_id": req.get("command_id", ""), "operation": req.get("operation", ""), "value": req.get("value"),
         "epoch": req.get("epoch", 0), "sequence": req.get("sequence", 0)}
    raw = json.dumps(c, separators=(",", ":"), ensure_ascii=False)
    for a, b in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026")):  # Go's HTML-safe escaping
        raw = raw.replace(a, b)
    return hashlib.sha256(raw.encode()).hexdigest()


class Reject(Exception):
    def __init__(self, code: int, body: dict):
        super().__init__(f"{code} {body}")
        self.code, self.body = code, body


class Gateway:
    def __init__(self, a, access: str, secret: str):
        self.a = a
        self.mode = a.mode
        self.contract = json.loads(open(a.contract_file).read())
        caps = set(self.contract.get("capabilities", []))
        want = {"COND": {"D_h", "Q_A", "Q_S"}, "PLAIN": set()}[self.mode]
        if caps != want:
            raise SystemExit(f"--contract-file {a.contract_file}: capabilities {sorted(caps)}, mode {self.mode} serves {sorted(want)}")
        if self.contract.get("source") != "MEASURED":
            raise SystemExit(f"--contract-file {a.contract_file}: source {self.contract.get('source')}, experiment C needs MEASURED")
        u = urllib.parse.urlsplit(a.s3_url)
        self.upstream = FaultProxy(u.hostname, u.port or 80, proto="tcp", listen_host="127.0.0.1", listen_port=0, linger_s=1.0)
        host, port = self.upstream.start()
        self.s3 = S3(f"http://{host}:{port}", access, secret)
        self.bucket, self.prefix = a.bucket, a.prefix.strip("/")
        self.online = True
        self.armed = ""
        self.lock = threading.Lock()
        self.log_fh = open(a.log, "a", buffering=1) if a.log else None
        self.counts = {"effects_put_200": 0, "requests": 0}

    # ---- helpers ----------------------------------------------------------
    def log(self, **kv):
        if self.log_fh is not None:
            kv["t"] = round(time.time(), 6)
            with self.lock:
                self.log_fh.write(json.dumps(kv, sort_keys=True) + "\n")

    def key(self, cid: str) -> str:
        return f"{self.prefix}/{cid}" if self.prefix else cid

    def receipt(self, cid: str, intent: str, outcome: str, value: int, reason: str) -> dict:
        # owner_version / receipt_version: S3 has no counter the gateway could read without a list; 1 marks
        # "an owner record exists" (protocol.ValidateReceipt wants non-zero for terminal receipts). state:
        # the value this record carries ([0] for a tombstone), not a level: the authority stores it only.
        return {"schema_version": 1, "domain_id": self.a.device_id, "generation": self.a.generation, "command_id": cid,
                "intent_hash": intent, "outcome": outcome, "owner_version": 1, "receipt_version": 1,
                "state": [int(value) if outcome == "APPLIED" else 0], "reason": reason}

    def left(self, t0: float) -> float:
        return self.a.budget_s - (time.monotonic() - t0)

    def fail(self, r, what: str):
        if r.status is None:
            if r.error == TIMEOUT:
                raise Reject(504, {"error": "OWNER_TIMEOUT", "detail": f"{what}: MinIO did not answer in time"})
            raise Reject(503, {"error": "OWNER_UNREACHABLE", "detail": f"{what}: {r.body[:120].decode(errors='replace')}"})
        raise Reject(502, {"error": "OWNER_ERROR", "detail": f"{what}: S3 HTTP {r.status}"})

    def existing(self, cid: str, intent: str, t0: float, what: str) -> dict:
        """The owner record under the key (after a 412, or for a query)."""
        r = self.s3.head(self.bucket, self.key(cid), timeout=self.left(t0))
        if r.status == 404:
            if what == "query":
                raise Reject(404, {"error": "NOT_FOUND"})
            # a conditional PUT failed 412 and the key is gone: never seen (nothing deletes); not evidence
            raise Reject(502, {"error": "OWNER_INCONSISTENT", "detail": f"{what}: 412 but HEAD 404"})
        if r.status != 200:
            self.fail(r, what + "/HEAD")
        kind, have = r.meta("evicon-kind"), r.meta("evicon-intent")
        if intent and have and have != intent:
            raise Reject(409, {"error": "IDENTITY_CONFLICT"})
        if kind == EFFECT:
            return self.receipt(cid, have or intent, "APPLIED", int(r.meta("evicon-value") or 0), "S3_EXISTING_EFFECT")
        if kind == TOMBSTONE:
            return self.receipt(cid, have or intent, "STOPPED", 0, "S3_TOMBSTONE")
        raise Reject(502, {"error": "OWNER_ERROR", "detail": f"{what}: object without evicon-kind"})

    # ---- protocol ---------------------------------------------------------
    def capabilities(self) -> dict:
        return {"schema_version": 1, "profile": "DEVICE", "adapter_version": f"s3-owner-gateway/{release()}/{self.mode}",
                "domain_id": self.a.device_id, "generation": self.a.generation, "installed_epoch": self.a.epoch, "dimension": 1,
                "supported_operations": ["ADD"], "component_min": [0], "verified_test_ids": self.contract.get("verified_test_ids", []),
                "evidence_contract": self.contract}

    def effect(self, req: dict, t0: float) -> dict:
        if req.get("schema_version", 1) != 1:
            raise Reject(400, {"error": "BAD_REQUEST", "detail": "schema_version"})
        cid = str(req.get("command_id", ""))
        if not cid or req.get("domain_id") != self.a.device_id or int(req.get("generation", 0)) != self.a.generation:
            raise Reject(400, {"error": "BAD_REQUEST", "detail": "domain/generation/command_id"})
        if req.get("operation") != "ADD" or not isinstance(req.get("value"), list) or len(req["value"]) != 1:
            raise Reject(400, {"error": "BAD_REQUEST", "detail": "ADD with one value only"})
        if int(req.get("sequence", 0)) <= 0:
            raise Reject(400, {"error": "BAD_REQUEST", "detail": "sequence must be greater than zero"})
        h = canonical_hash(req)
        if req.get("intent_hash") and req["intent_hash"] != h:
            raise Reject(400, {"error": "BAD_REQUEST", "detail": "intent_hash mismatch"})
        value = int(req["value"][0])
        body = json.dumps({"command_id": cid, "value": value, "intent_hash": h}, sort_keys=True).encode()
        cond = self.mode == "COND"
        r = self.s3.put(self.bucket, self.key(cid), body, {"evicon-kind": EFFECT, "evicon-intent": h, "evicon-value": value},
                        if_none_match=cond, timeout=self.left(t0))
        self.log(op="effect", cid=cid, s3=r.status, err=r.error, ms=round(r.ms, 1))
        if r.status == 200:
            with self.lock:
                self.counts["effects_put_200"] += 1
                crash = self.armed
            if crash:
                self.log(op="crash", cid=cid, at=crash)
                os._exit(17)  # the effect exists in MinIO; the authority gets no receipt
            return self.receipt(cid, h, "APPLIED", value, "S3_PUT_200")
        if r.status == 412 and cond:
            return self.existing(cid, h, t0, "effect")
        self.fail(r, "effect PUT")

    def query(self, cid: str, t0: float) -> dict:
        if self.mode != "COND":
            raise Reject(409, {"error": "UNSUPPORTED"})
        return self.existing(cid, "", t0, "query")

    def stop(self, req: dict, t0: float) -> dict:
        if self.mode != "COND":
            raise Reject(409, {"error": "STOP_UNSUPPORTED"})
        cid, intent = str(req.get("command_id", "")), str(req.get("intent_hash", ""))
        if req.get("schema_version") != 1 or not cid or not intent or int(req.get("sequence", 0)) <= 0 \
                or req.get("domain_id") != self.a.device_id or int(req.get("generation", 0)) != self.a.generation:
            raise Reject(400, {"error": "BAD_REQUEST", "detail": "stop requires domain_id, generation, command_id, intent_hash, sequence"})
        r = self.s3.put(self.bucket, self.key(cid), b"", {"evicon-kind": TOMBSTONE, "evicon-intent": intent},
                        if_none_match=True, timeout=self.left(t0))
        self.log(op="stop", cid=cid, s3=r.status, err=r.error, ms=round(r.ms, 1))
        if r.status == 200:
            return self.receipt(cid, intent, "STOPPED", 0, "S3_TOMBSTONE_WRITTEN")
        if r.status == 412:
            return self.existing(cid, intent, t0, "stop")
        self.fail(r, "stop PUT")

    # ---- admin ------------------------------------------------------------
    def set_online(self, v: bool):
        with self.lock:
            self.online = v
        if v:
            self.upstream.clear()
        else:
            self.upstream.drop(UP)
            self.upstream.drop(DOWN)
        self.log(op="online", v=v)

    def arm(self, at: str):
        with self.lock:
            self.armed = at
        self.log(op="arm", at=at)


def make_handlers(g: Gateway):
    class Base(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def route(self) -> str:
            """The request path without the optional /d/<device-id> prefix (the endpoint URL the
            campaign hands to the authority, as for cmd/evicon-device); another device id is not ours."""
            path = urllib.parse.urlsplit(self.path).path
            if path.startswith("/d/"):
                _, _, dev, rest = (path.split("/", 3) + [""])[:4]
                if dev != g.a.device_id:
                    return "/_not_this_device"
                path = "/" + rest
            return path

        def log_message(self, *a):
            pass

        def send(self, code: int, obj):
            data = (json.dumps(obj) + "\n").encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)
            self.close_connection = True

        def body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > 1 << 16:
                raise Reject(413, {"error": "TOO_LARGE"})
            try:
                return json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                raise Reject(400, {"error": "BAD_REQUEST", "detail": "JSON"})

    class Data(Base):
        def do_GET(self):
            t0 = time.monotonic()
            path = self.route()
            try:
                if path == "/healthz":
                    return self.send(200, {"ok": True, "role": ROLE, "pid": os.getpid(), "release": release(), "device": g.a.device_id,
                                           "online": g.online, "mode": g.mode, "owner": "minio-s3", "bucket": g.bucket})
                if path == "/v1/capabilities":
                    return self.send(200, g.capabilities())
                if path.startswith("/v1/receipts/"):
                    cid = urllib.parse.unquote(path[len("/v1/receipts/"):])
                    return self.send(200, g.query(cid, t0))
                if path == "/v1/state":
                    return self.send(200, {"schema_version": 1, "domain_id": g.a.device_id, "generation": g.a.generation,
                                           "evidence": "NONE_S3_GATEWAY_HAS_NO_LEVEL"})
                self.send(404, {"error": "NOT_FOUND"})
            except Reject as e:
                g.log(op="reply", path=path, code=e.code, err=e.body.get("error"))
                self.send(e.code, e.body)

        def do_POST(self):
            t0 = time.monotonic()
            path = self.route()
            try:
                if path == "/v1/effects":
                    return self.send(200, g.effect(self.body(), t0))
                if path == "/v1/stop":
                    return self.send(200, g.stop(self.body(), t0))
                if path == "/v1/barriers":
                    self.body()
                    return self.send(409, {"error": "BARRIER_UNSUPPORTED"})
                self.send(404, {"error": "NOT_FOUND"})
            except Reject as e:
                g.log(op="reply", path=path, code=e.code, err=e.body.get("error"))
                self.send(e.code, e.body)

    class Admin(Base):
        def do_GET(self):
            path = self.route()
            if path == "/healthz":
                return self.send(200, {"ok": True, "role": ROLE + "-admin", "pid": os.getpid(), "release": release()})
            if path == "/_debug/state":
                return self.send(200, {"device": g.a.device_id, "mode": g.mode, "online": g.online, "armed": g.armed,
                                       "bucket": g.bucket, "prefix": g.prefix, "counts": g.counts, "upstream": g.upstream.describe()})
            self.send(404, {"error": "NOT_FOUND"})

        def do_POST(self):
            path = self.route()
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            if path == "/_test/online":
                v = (q.get("v") or [""])[0]
                if v not in ("true", "false"):
                    return self.send(400, {"error": "v must be true or false"})
                g.set_online(v == "true")
                return self.send(200, {"online": v == "true"})
            if path == "/_test/crash":
                at = (q.get("at") or [""])[0]
                if at == "NONE":
                    g.arm("")
                    return self.send(200, {"armed": ""})
                if at not in CRASH_POINTS:
                    return self.send(400, {"error": f"crash point {at!r}: an S3 owner has {CRASH_POINTS} (no claim before the effect)"})
                g.arm(at)
                return self.send(200, {"armed": at})
            self.send(404, {"error": "NOT_FOUND"})

    return Data, Admin


def hostport(s: str):
    h, p = s.rsplit(":", 1)
    return h, int(p)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mode", choices=("COND", "PLAIN"), required=True)
    ap.add_argument("--device-id", required=True)
    ap.add_argument("--generation", type=int, default=1)
    ap.add_argument("--epoch", type=int, default=1)
    ap.add_argument("--listen", required=True, help="host:port of the protocol server")
    ap.add_argument("--admin-listen", required=True, help="host:port of the test-only admin server")
    ap.add_argument("--s3-url", required=True, help="MinIO, e.g. http://192.168.107.152:9000 (reached through the in-process fault proxy)")
    ap.add_argument("--bucket", required=True)
    ap.add_argument("--prefix", default="", help="key prefix (default: the device id)")
    ap.add_argument("--contract-file", required=True, help="the MEASURED contract advertised in /v1/capabilities")
    ap.add_argument("--budget-s", type=float, default=1.8, help="time for all S3 calls of one request (the authority waits 2 s)")
    ap.add_argument("--access-key-env", default="MINIO_ROOT_USER")
    ap.add_argument("--secret-key-env", default="MINIO_ROOT_PASSWORD")
    ap.add_argument("--log", default="gateway.jsonl")
    a = ap.parse_args(argv)
    a.prefix = a.prefix or a.device_id
    access, secret = os.environ.get(a.access_key_env, ""), os.environ.get(a.secret_key_env, "")
    if not access or not secret:
        raise SystemExit(f"credentials missing: set {a.access_key_env} and {a.secret_key_env} in the environment")
    g = Gateway(a, access, secret)
    Data, Admin = make_handlers(g)
    data = ThreadingHTTPServer(hostport(a.listen), Data)
    admin = ThreadingHTTPServer(hostport(a.admin_listen), Admin)
    data.daemon_threads = admin.daemon_threads = True
    threading.Thread(target=admin.serve_forever, daemon=True).start()
    g.log(op="start", pid=os.getpid(), mode=a.mode, device=a.device_id, bucket=a.bucket, prefix=a.prefix, release=release(),
          contract=g.contract.get("id"), upstream=g.upstream.describe())
    print(f"s3 owner gateway {a.device_id} mode={a.mode} on {a.listen} (admin {a.admin_listen}) -> {a.s3_url}/{a.bucket}/{a.prefix}/",
          flush=True)
    try:
        data.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
