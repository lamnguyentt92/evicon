"""EviCon owner gateway for ThingsBoard CE server-side RPC: experiment TB, release 077.

ThingsBoard is an IoT device-management platform with a command lifecycle of the kind cloud IoT services
offer (AWS IoT Jobs and Commands, Azure IoT Hub cloud-to-device methods): a server-side RPC to a device is
a persistent record (QUEUED, SENT, DELIVERED, SUCCESSFUL, TIMEOUT, EXPIRED, FAILED) that a client creates
over REST and reads back; the platform delivers it to the device over MQTT and expires it at its
expirationTime. One gateway per device speaks the EviCon device-owner protocol of cmd/evicon-device
(internal/device/server.go) to one authority and turns each request into the ThingsBoard calls whose
evidence contract `measure thingsboard` MEASURED in the same review run (conformance/configs/
thingsboard-rpc-r0.json: persistent two-way RPC, no platform retries):

  POST /v1/effects        POST /api/rpc/twoway/<device> {"method":"actuate","params":{"cmd","seq","value","intent"},
                          "persistent":true,"retries":0,"timeout":<rpc_timeout_ms>,"expirationTime":<the request's
                          expires_at_ms, else now + --expiration-s>} -> {"rpcId"}; then the record is read until
                          it is final or the budget ends: SUCCESSFUL -> APPLIED receipt. Anything else is not a
                          receipt: 504 OWNER_TIMEOUT (not final in time, or TIMEOUT/EXPIRED: the effect may or
                          may not have happened), 502 OWNER_ERROR (FAILED, an HTTP error), 503 OWNER_UNREACHABLE.
                          Every attempt creates a new RPC: ThingsBoard has no idempotency key (the measured
                          contract has no D_h), so a repeated identity can take effect twice
  GET  /v1/receipts/<id>  Q_A: the RPC of this identity (the rpcId this gateway created, else the device's RPC
                          list matched on params.cmd): SUCCESSFUL -> APPLIED receipt; QUEUED, SENT, DELIVERED,
                          TIMEOUT, EXPIRED, FAILED -> 200 with outcome UNCERTAIN and the status (not evidence);
                          no RPC -> 404
  POST /v1/stop           409 STOP_UNSUPPORTED (deleting an RPC record is not owner evidence: measured P9)
  POST /v1/barriers       409 BARRIER_UNSUPPORTED
  GET  /v1/capabilities   profile DEVICE, ADD only, the measured contract (Q_A, T)
  GET  /healthz           role "gateway", this release, the pid

Admin (test-only, its own port):
  POST /_test/online?v=false|true   forwarded to the device's admin (conformance/reference/mqtt_device.py on
                                    VM-E): the device's MQTT connection to ThingsBoard goes down / up, so RPCs
                                    wait in ThingsBoard and reach the device when it reconnects (or expire)
  POST /_test/crash?at=AFTER_EFFECT_BEFORE_RECEIPT   armed 100 ms after the answer: the next effect whose RPC
                                    ThingsBoard reports SUCCESSFUL makes the gateway exit (status 17) before it
                                    replies (a client abandon); at=NONE disarms

The gateway keeps no state of its own besides a cache of the rpcIds it created: ThingsBoard's RPC records
are the owner record, so a restarted gateway finds an identity's RPC by listing. Credentials: the tenant
user in the environment variables named by --user-env / --password-env (default TB_USERNAME, TB_PASSWORD),
set by the runner; never on the command line, never logged.

  python3 conformance/reference/tb_owner_gateway.py --device-id pump0-1 --tb-device <uuid> \\
      --listen 127.0.0.1:18100 --admin-listen 127.0.0.1:18101 --tb-url http://192.168.107.152:18088 \\
      --device-admin http://192.168.107.154:18220 --contract-file <measured contract.json> --log gateway0.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))  # conformance/: reference
from reference._health import release  # noqa: E402
from reference.s3_owner_gateway import Reject, canonical_hash, hostport  # noqa: E402

ROLE = "gateway"
CRASH_POINTS = ("AFTER_EFFECT_BEFORE_RECEIPT",)
FINAL = {"SUCCESSFUL", "TIMEOUT", "EXPIRED", "FAILED", "DELETED"}
CAPS = {"Q_A", "T"}
ARM_DELAY_S = 0.1


class TB:
    """ThingsBoard REST with a tenant JWT (logged in again on 401)."""

    def __init__(self, url: str, user: str, password: str):
        self.url, self.user, self.password = url.rstrip("/"), user, password
        self.jwt = ""
        self.lock = threading.Lock()

    def login(self, timeout=5.0) -> bool:
        st, p = self._raw("POST", "/api/auth/login", {"username": self.user, "password": self.password}, timeout, auth=False)
        if st == 200 and isinstance(p, dict) and p.get("token"):
            with self.lock:
                self.jwt = p["token"]
            return True
        return False

    def _raw(self, method, path, body, timeout, auth=True):
        data = None if body is None else json.dumps(body).encode()
        h = {"Content-Type": "application/json", "Connection": "close"}
        if auth:
            h["X-Authorization"] = "Bearer " + self.jwt
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=max(0.05, timeout)) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, (json.loads(raw) if raw else None)
            except ValueError:
                return e.code, raw.decode(errors="replace")[:200]
        except (socket.timeout, TimeoutError):
            return None, "timeout"
        except (urllib.error.URLError, OSError) as e:
            return None, f"unreachable: {e}"

    def call(self, method, path, body=None, timeout=5.0):
        t_end = time.monotonic() + timeout
        st, p = self._raw(method, path, body, timeout)
        if st == 401 and self.login(timeout=max(0.5, t_end - time.monotonic())):
            st, p = self._raw(method, path, body, max(0.05, t_end - time.monotonic()))
        return st, p


class Gateway:
    def __init__(self, a, tb: TB):
        self.a = a
        self.tb = tb
        with open(a.contract_file) as fh:
            self.contract = json.load(fh)
        if self.contract.get("source") != "MEASURED" or set(self.contract.get("capabilities", [])) != CAPS:
            raise SystemExit(f"--contract-file {a.contract_file}: {self.contract.get('source')} {sorted(self.contract.get('capabilities', []))}, "
                             f"experiment TB needs the MEASURED contract {sorted(CAPS)} of ThingsBoard's persistent RPC")
        self.rpc_ids = {}  # identity -> rpcId (created by this process)
        self.armed = ""
        self._arming = 0
        self.lock = threading.Lock()
        self.log_fh = open(a.log, "a", buffering=1) if a.log else None
        self.counts = {"rpcs_created": 0, "successful": 0, "requests": 0}

    # ---- helpers ----------------------------------------------------------
    def log(self, **kv):
        if self.log_fh is not None:
            kv["t"] = round(time.time(), 6)
            with self.lock:
                self.log_fh.write(json.dumps(kv, sort_keys=True) + "\n")

    def close(self):
        if self.log_fh is not None:
            self.log_fh.close()
            self.log_fh = None

    def left(self, t0: float) -> float:
        return self.a.budget_s - (time.monotonic() - t0)

    def receipt(self, cid: str, intent: str, value: int, reason: str) -> dict:
        # owner_version / receipt_version: 1 marks "an owner record exists" (ThingsBoard has no counter the gateway
        # could read); state: the value this RPC carried, not a level (the authority stores it only)
        return {"schema_version": 1, "domain_id": self.a.device_id, "generation": self.a.generation, "command_id": cid,
                "intent_hash": intent, "outcome": "APPLIED", "owner_version": 1, "receipt_version": 1, "state": [int(value)],
                "reason": reason}

    def uncertain(self, cid: str, intent: str, status: str) -> dict:
        return {"schema_version": 1, "domain_id": self.a.device_id, "generation": self.a.generation, "command_id": cid,
                "intent_hash": intent, "outcome": "UNCERTAIN", "owner_version": 0, "receipt_version": 0, "state": [],
                "reason": f"TB_{status or 'UNKNOWN'}"}

    def fail(self, st, p, what: str):
        if st is None:
            if p == "timeout":
                raise Reject(504, {"error": "OWNER_TIMEOUT", "detail": f"{what}: ThingsBoard did not answer in time"})
            raise Reject(503, {"error": "OWNER_UNREACHABLE", "detail": f"{what}: {str(p)[:120]}"})
        raise Reject(502, {"error": "OWNER_ERROR", "detail": f"{what}: HTTP {st}"})

    @staticmethod
    def _params(rec: dict) -> dict:
        body = (rec.get("request") or {}).get("body") or {}
        params = body.get("params") if isinstance(body, dict) else None
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except ValueError:
                params = None
        return params if isinstance(params, dict) else {}

    def find(self, cid: str, t0: float):
        """(rpcId, record) of the identity's RPC, or (None, None): the cached rpcId, else the device's RPC list."""
        rid = self.rpc_ids.get(cid)
        if rid:
            st, rec = self.tb.call("GET", f"/api/rpc/persistent/{rid}", timeout=self.left(t0))
            if st == 200 and isinstance(rec, dict):
                return rid, rec
            if st != 404:
                self.fail(st, rec, "query GET rpc")
        for page in range(10):
            st, p = self.tb.call("GET", f"/api/rpc/persistent/device/{self.a.tb_device}?pageSize=100&page={page}"
                                        f"&sortProperty=createdTime&sortOrder=DESC", timeout=self.left(t0))
            if st != 200 or not isinstance(p, dict):
                self.fail(st, p, "query list")
            for rec in p.get("data", []):
                if str(self._params(rec).get("cmd")) == cid:
                    return rec["id"]["id"], rec
            if not p.get("hasNext"):
                break
        return None, None

    # ---- protocol ---------------------------------------------------------
    def capabilities(self) -> dict:
        return {"schema_version": 1, "profile": "DEVICE", "adapter_version": f"tb-owner-gateway/{release()}/PERSISTENT_RPC",
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
        seq = int(req.get("sequence", 0))
        if seq <= 0:
            raise Reject(400, {"error": "BAD_REQUEST", "detail": "sequence must be greater than zero"})
        h = canonical_hash(req)
        if req.get("intent_hash") and req["intent_hash"] != h:
            raise Reject(400, {"error": "BAD_REQUEST", "detail": "intent_hash mismatch"})
        value = int(req["value"][0])
        exp = int(req.get("expires_at_ms") or 0) or int((time.time() + self.a.expiration_s) * 1000)
        body = {"method": "actuate", "params": {"cmd": cid, "seq": seq, "value": value, "intent": h}, "persistent": True, "retries": 0,
                "timeout": self.a.rpc_timeout_ms, "expirationTime": exp}
        st, p = self.tb.call("POST", f"/api/rpc/twoway/{self.a.tb_device}", body, timeout=self.left(t0))
        if st != 200 or not isinstance(p, dict) or not p.get("rpcId"):
            self.log(op="effect", cid=cid, tb=st)
            self.fail(st, p, "effect POST rpc")
        rid = p["rpcId"]
        with self.lock:
            self.rpc_ids[cid] = rid
            self.counts["rpcs_created"] += 1
        status = ""
        while self.left(t0) > 0:
            s2, rec = self.tb.call("GET", f"/api/rpc/persistent/{rid}", timeout=self.left(t0))
            if s2 == 200 and isinstance(rec, dict):
                status = str(rec.get("status", ""))
                if status in FINAL:
                    break
            elif s2 is None:
                break  # the path to ThingsBoard failed while reading: the gateway does not know
            time.sleep(min(self.a.poll_s, max(0.0, self.left(t0))))
        self.log(op="effect", cid=cid, rpc=rid, status=status, ms=round((time.monotonic() - t0) * 1000, 1))
        if status == "SUCCESSFUL":
            with self.lock:
                self.counts["successful"] += 1
                crash = self.armed
            if crash:
                self.log(op="crash", cid=cid, at=crash)
                os._exit(17)  # the device executed it; the authority gets no receipt (client abandon)
            return self.receipt(cid, h, value, "TB_RPC_SUCCESSFUL")
        if status == "FAILED":
            raise Reject(502, {"error": "OWNER_ERROR", "detail": "RPC FAILED"})
        raise Reject(504, {"error": "OWNER_TIMEOUT", "detail": f"RPC {status or 'not read'}: the effect may or may not happen"})

    def query(self, cid: str, t0: float) -> dict:
        rid, rec = self.find(cid, t0)
        if rid is None:
            raise Reject(404, {"error": "NOT_FOUND"})
        status = str(rec.get("status", ""))
        params = self._params(rec)
        intent = str(params.get("intent", ""))
        if status == "SUCCESSFUL":
            return self.receipt(cid, intent, int(params.get("value", 0)), "TB_RPC_SUCCESSFUL")
        return self.uncertain(cid, intent, status)

    # ---- admin ------------------------------------------------------------
    def set_online(self, v: bool):
        req = urllib.request.Request(f"{self.a.device_admin}/_test/online?v={'true' if v else 'false'}", data=b"", method="POST")
        try:
            urllib.request.urlopen(req, timeout=5).read()
        finally:
            self.log(op="online", v=v)

    def arm(self, at: str):
        with self.lock:
            self._arming += 1
            gen = self._arming
            if not at:
                self.armed = ""
        if not at:
            self.log(op="arm", at="")
            return
        self.log(op="arm_requested", at=at, delay_s=ARM_DELAY_S)

        def later():
            time.sleep(ARM_DELAY_S)  # after the admin answer has left (see mqtt_owner_gateway_ext077.py)
            with self.lock:
                if gen == self._arming:
                    self.armed = at
        threading.Thread(target=later, daemon=True).start()


def make_handlers(g: Gateway):
    class Base(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def route(self) -> str:
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
                                           "owner": "thingsboard-persistent-rpc", "tb_device": g.a.tb_device})
                if path == "/v1/capabilities":
                    return self.send(200, g.capabilities())
                if path.startswith("/v1/receipts/"):
                    return self.send(200, g.query(urllib.parse.unquote(path[len("/v1/receipts/"):]), t0))
                if path == "/v1/state":
                    return self.send(200, {"schema_version": 1, "domain_id": g.a.device_id, "generation": g.a.generation,
                                           "evidence": "NONE_TB_GATEWAY_HAS_NO_LEVEL"})
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
                    self.body()
                    return self.send(409, {"error": "STOP_UNSUPPORTED"})
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
                return self.send(200, {"device": g.a.device_id, "armed": g.armed, "counts": g.counts, "rpcs": len(g.rpc_ids)})
            self.send(404, {"error": "NOT_FOUND"})

        def do_POST(self):
            path = self.route()
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            if path == "/_test/online":
                v = (q.get("v") or [""])[0]
                if v not in ("true", "false"):
                    return self.send(400, {"error": "v must be true or false"})
                try:
                    g.set_online(v == "true")
                except OSError as e:
                    return self.send(502, {"error": f"device admin: {e}"})
                return self.send(200, {"online": v == "true"})
            if path == "/_test/crash":
                at = (q.get("at") or [""])[0]
                if at == "NONE":
                    g.arm("")
                    return self.send(200, {"armed": ""})
                if at not in CRASH_POINTS:
                    return self.send(400, {"error": f"crash point {at!r}: this gateway has {CRASH_POINTS}"})
                g.arm(at)
                return self.send(200, {"armed": at})
            self.send(404, {"error": "NOT_FOUND"})

    return Data, Admin


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--device-id", required=True)
    ap.add_argument("--tb-device", required=True, help="ThingsBoard device UUID")
    ap.add_argument("--generation", type=int, default=1)
    ap.add_argument("--epoch", type=int, default=1)
    ap.add_argument("--listen", required=True)
    ap.add_argument("--admin-listen", required=True)
    ap.add_argument("--tb-url", required=True, help="ThingsBoard REST, e.g. http://192.168.107.152:18088")
    ap.add_argument("--device-admin", required=True, help="the device's admin URL (mqtt_device.py --admin-port)")
    ap.add_argument("--contract-file", required=True, help="the MEASURED contract of ThingsBoard's persistent RPC")
    ap.add_argument("--budget-s", type=float, default=1.8, help="time for the REST calls of one request (the authority waits 2 s)")
    ap.add_argument("--rpc-timeout-ms", type=int, default=5000, help="the RPC's timeout (the measured call shape)")
    ap.add_argument("--expiration-s", type=float, default=60.0, help="expirationTime when the request carries no expires_at_ms")
    ap.add_argument("--poll-s", type=float, default=0.1)
    ap.add_argument("--user-env", default="TB_USERNAME")
    ap.add_argument("--password-env", default="TB_PASSWORD")
    ap.add_argument("--log", default="gateway.jsonl")
    a = ap.parse_args(argv)
    user, password = os.environ.get(a.user_env, ""), os.environ.get(a.password_env, "")
    if not user or not password:
        raise SystemExit(f"credentials missing: set {a.user_env} and {a.password_env} in the environment")
    tb = TB(a.tb_url, user, password)
    if not tb.login(timeout=10):
        raise SystemExit(f"ThingsBoard login at {a.tb_url} failed")
    g = Gateway(a, tb)
    Data, Admin = make_handlers(g)
    data = ThreadingHTTPServer(hostport(a.listen), Data)
    admin = ThreadingHTTPServer(hostport(a.admin_listen), Admin)
    data.daemon_threads = admin.daemon_threads = True
    threading.Thread(target=admin.serve_forever, daemon=True).start()
    g.log(op="start", pid=os.getpid(), device=a.device_id, tb_device=a.tb_device, release=release(), contract=g.contract.get("id"))
    print(f"tb owner gateway {a.device_id} -> ThingsBoard {a.tb_url} device {a.tb_device} on {a.listen} (admin {a.admin_listen})", flush=True)
    try:
        data.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
