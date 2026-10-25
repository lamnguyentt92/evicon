"""EviCon owner gateway for the Zephyr EviCon firmware behind Mosquitto: experiment C2, release 074.

One gateway per device speaks the EviCon device-owner protocol of cmd/evicon-device
(internal/device/server.go) to one authority and turns each request into the MQTT messages whose
evidence contract the same night's EviCon-Probe measurement MEASURED for the firmware mode
(conformance/configs/zephyr-mqtt*.json; the driver passes the measured contract file):

  POST /v1/effects        <ns>/<dev>/cmd {"cmd","seq","value","reply"} on the data connection, QoS 1 (the
                          measured call shape; at least once: paho re-sends a publish whose PUBACK a
                          reset lost, after it reconnects).
                          Reply APPLIED / STOPPED -> that receipt. UNCERTAIN -> 503 OWNER_UNCERTAIN.
                          No reply within the budget -> 504 OWNER_TIMEOUT. Every attempt is published,
                          a repeated identity too (a plain firmware executes it again)
  GET  /v1/receipts/<id>  CUTOFF/STOP (Q_A): a terminal receipt the firmware returned earlier, else
                          <ns>/<dev>/query {"cmd","seq"} on the control connection: APPLIED / STOPPED -> the
                          receipt, NOT_FOUND -> 404, UNCERTAIN -> 200 with outcome UNCERTAIN (not evidence).
                          An identity this gateway never forwarded -> 404. PLAIN: 409 UNSUPPORTED
  POST /v1/stop           STOP (Q_S): <ns>/<dev>/stop {"cmd","seq"}; APPLIED / STOPPED -> the receipt,
                          UNCERTAIN (future blocked, past unknown; tombstone table full) -> 200 UNCERTAIN.
                          Otherwise 409 STOP_UNSUPPORTED
  POST /v1/barriers       CUTOFF/STOP (F): <ns>/<dev>/fence {"cutoff","token"}; when the firmware confirms
                          the durable cutoff, a receipt for EVERY identity this gateway forwarded with a
                          sequence at or below the cutoff: the terminal receipt the firmware returned
                          earlier, else the firmware's answer to a query after the cutoff (STOPPED
                          SEALED_NOT_STARTED when it certainly never started; UNCERTAIN when its receipt
                          may have left the RAM ring). The state is the firmware's level at the fence.
                          The authority seals unreported identities below the cutoff as STOPPED, so the
                          gateway never leaves one out. PLAIN: 409 BARRIER_UNSUPPORTED
  GET  /v1/capabilities   profile DEVICE, ADD only, and the measured contract of this mode
  GET  /healthz           role "gateway", this release, the pid, the firmware's mode (checked at start)

Durable record (--state, JSON lines, fsync before use): every identity forwarded (sequence, intent,
value) before its first message, and every terminal receipt the firmware returned (an IoT platform's
command log). A restarted gateway (crash_after_effect: client abandon) answers barriers and queries
from it, and uses a new MQTT client id, so the broker's session of the crashed process (with any
half-finished QoS 2 exchange) is never resumed: such a command is lost, as for any client abandon.

Network faults (admin, test-only): /_test/online?v=false puts the gateway's link to the broker down:
both MQTT connections (data and control: separate TCP connections, so a held command never delays a
fence) are reset and new connections are refused until v=true. paho keeps QoS>0 messages published
meanwhile and sends them after it reconnects (persistent session), so they reach the firmware late,
as the measurement's late-effect probes saw. /_test/crash?at=AFTER_EFFECT_BEFORE_RECEIPT: the next
effect the firmware answers APPLIED makes the gateway exit (status 17) before it replies; at=NONE
disarms. The firmware itself is never crashed (the measured fault model: "owner up").

  python3 conformance/reference/mqtt_owner_gateway.py --mode CUTOFF --device-id pump0-1 --ns c1a2b3 \\
      --listen 127.0.0.1:18100 --admin-listen 127.0.0.1:18101 --broker 192.168.107.152:1883 \\
      --contract-file <measured contract.json> --state gateway0.state.jsonl --log gateway0.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))  # conformance/: evicon_probe, reference

from reference._health import release  # noqa: E402
from reference.s3_owner_gateway import Reject, canonical_hash, hostport  # noqa: E402  (frozen in 073; reused unchanged)

ROLE = "gateway"
MODES = {"PLAIN": set(), "CUTOFF": {"D_h", "Q_A", "F"}, "STOP": {"D_h", "Q_A", "Q_S", "F"}}
FW_MODE = {"PLAIN": "plain", "CUTOFF": "cutoff", "STOP": "stop"}
CRASH_POINTS = ("AFTER_EFFECT_BEFORE_RECEIPT",)
TERMINAL = ("APPLIED", "STOPPED")


class Link:
    """The gateway's network link to the broker: an in-process TCP relay that is up, or down (every
    relayed connection reset, new ones accepted and closed at once)."""

    def __init__(self, host: str, port: int):
        self.target = (host, port)
        self.up = True
        self.lock = threading.Lock()
        self.pairs = set()
        self.resets = 0
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(16)
        self.port = self.srv.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            with self.lock:
                up = self.up
            if not up:
                c.close()
                continue
            try:
                u = socket.create_connection(self.target, timeout=3)
            except OSError:
                c.close()
                continue
            for s in (c, u):
                s.settimeout(None)
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            pair = (c, u)
            with self.lock:
                if not self.up:
                    self._close(pair, locked=True)
                    continue
                self.pairs.add(pair)
            threading.Thread(target=self._pump, args=(c, u, pair), daemon=True).start()
            threading.Thread(target=self._pump, args=(u, c, pair), daemon=True).start()

    def _pump(self, a, b, pair):
        try:
            while True:
                d = a.recv(65536)
                if not d:
                    break
                b.sendall(d)
        except OSError:
            pass
        self._close(pair)

    def _close(self, pair, locked=False):
        if not locked:
            with self.lock:
                self.pairs.discard(pair)
        else:
            self.pairs.discard(pair)
        for s in pair:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                s.close()
            except OSError:
                pass

    def set_up(self, v: bool):
        with self.lock:
            self.up = v
            pairs = list(self.pairs) if not v else []
            self.pairs.clear() if not v else None
            if not v:
                self.resets += len(pairs)
        for p in pairs:
            self._close(p)

    def describe(self) -> dict:
        with self.lock:
            return {"target": f"{self.target[0]}:{self.target[1]}", "listen": f"127.0.0.1:{self.port}", "up": self.up,
                    "connections": len(self.pairs), "resets": self.resets}


class Mqtt:
    """One MQTT 3.1.1 connection through the link, persistent session, replies on <ns>/<dev>/resp/<client>/#."""

    def __init__(self, gw, role: str, port: int):
        import paho.mqtt.client as mqtt  # optional dependency: VM-C has it for `measure zephyr`
        self.gw, self.role = gw, role
        self.client_id = f"{gw.a.ns}-{gw.a.device_id}-gw{os.getpid()}-{role}"
        self.resp = f"{gw.a.ns}/{gw.a.device_id}/resp/{self.client_id}"
        self.subscribed = threading.Event()
        self.c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.client_id, protocol=mqtt.MQTTv311, clean_session=False)
        self.c.reconnect_delay_set(min_delay=1, max_delay=2)
        self.c.on_connect = self._on_connect
        self.c.on_subscribe = lambda *_a, **_k: self.subscribed.set()
        self.c.on_message = self._on_message
        self.c.on_disconnect = lambda *_a, **_k: gw.log(op="mqtt", role=role, event="disconnected")
        self.c.connect("127.0.0.1", port, keepalive=int(gw.a.keepalive_s))
        self.c.loop_start()

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        self.gw.log(op="mqtt", role=self.role, event="connected", rc=str(reason_code))
        client.subscribe(self.resp + "/#", qos=1)

    def _on_message(self, client, userdata, msg):
        try:
            body = json.loads(msg.payload or b"{}")
        except ValueError:
            return
        self.gw.on_reply(msg.topic.rsplit("/", 1)[-1], body)

    def publish(self, kind: str, body: dict, qos: int):
        body = dict(body, reply=f"{self.resp}/{kind}")
        return self.c.publish(f"{self.gw.a.ns}/{self.gw.a.device_id}/{kind}", json.dumps(body, separators=(",", ":")), qos=qos)

    def close(self):
        try:
            self.c.disconnect()
        except Exception:  # noqa: BLE001
            pass
        t = threading.Thread(target=self.c.loop_stop, daemon=True)
        t.start()
        t.join(3)


class Gateway:
    def __init__(self, a, net=None):
        """net: test hook, a function (gateway) -> (link, data, ctl) with the interfaces of Link and Mqtt
        (evaluation/test_mqtt_owner_gateway.py runs the protocol against an in-process firmware model);
        None builds the real link and the two paho connections."""
        self.a = a
        self.mode = a.mode
        self.contract = json.loads(open(a.contract_file).read())
        caps = set(self.contract.get("capabilities", []))
        if caps != MODES[self.mode]:
            raise SystemExit(f"--contract-file {a.contract_file}: capabilities {sorted(caps)}, mode {self.mode} serves {sorted(MODES[self.mode])}")
        if self.contract.get("source") != "MEASURED":
            raise SystemExit(f"--contract-file {a.contract_file}: source {self.contract.get('source')}, experiment C2 needs MEASURED")
        self.lock = threading.Lock()
        self.log_fh = open(a.log, "a", buffering=1) if a.log else None
        self.state_fh = None
        try:
            self._open(a, net)
        except BaseException:
            self.close()
            raise

    def _open(self, a, net):
        self.fwd, self.rcpt = {}, {}
        self.contradictions = 0
        self._load_state()
        self.state_fh = open(a.state, "a", buffering=1)
        self.waiters = {}
        self.armed = ""
        self.counts = {"requests": 0, "published": 0, "replies": 0}
        if net is None:
            host, port = hostport(a.broker)
            self.link = Link(host, port)
            self.data = Mqtt(self, "data", self.link.port)
            self.ctl = Mqtt(self, "ctl", self.link.port)
        else:
            self.link, self.data, self.ctl = net(self)
        for m in (self.data, self.ctl):
            if not m.subscribed.wait(10):
                raise SystemExit(f"MQTT {m.role}: no SUBACK from {a.broker} within 10 s")
        self.fw = self.rpc(self.ctl, "ping", "", {}, 1, 5.0)
        if self.fw is None:
            raise SystemExit(f"firmware {a.device_id} (topic prefix {a.ns}) does not answer a ping through {a.broker} within 5 s")
        if self.fw.get("mode") != FW_MODE[self.mode] or self.fw.get("device") != a.device_id:
            raise SystemExit(f"firmware answers {self.fw}: gateway mode {self.mode} needs firmware mode {FW_MODE[self.mode]} for {a.device_id}")

    # ---- durable record ---------------------------------------------------
    def _load_state(self):
        if not os.path.exists(self.a.state):
            return
        with open(self.a.state) as fh:
            for line in fh:
                try:
                    x = json.loads(line)
                except ValueError:
                    continue  # a torn last line after a crash
                if x.get("k") == "fwd":
                    self.fwd.setdefault(x["cid"], x)
                elif x.get("k") == "rcpt":
                    self.rcpt.setdefault(x["cid"], x)

    def _append(self, x: dict):
        self.state_fh.write(json.dumps(x, separators=(",", ":")) + "\n")
        self.state_fh.flush()
        os.fsync(self.state_fh.fileno())

    def log(self, **kv):
        if self.log_fh is not None:
            kv["t"] = round(time.time(), 6)
            with self.lock:
                self.log_fh.write(json.dumps(kv, sort_keys=True) + "\n")

    # ---- replies ----------------------------------------------------------
    def rpc(self, conn: Mqtt, kind: str, key: str, body: dict, qos: int, timeout: float):
        w = {"ev": threading.Event(), "body": None}
        with self.lock:
            self.waiters.setdefault((kind, key), []).append(w)
            self.counts["published"] += 1
        conn.publish(kind, body, qos)
        w["ev"].wait(max(0.0, timeout))
        with self.lock:
            ws = self.waiters.get((kind, key), [])
            if w in ws:
                ws.remove(w)
            if not ws:
                self.waiters.pop((kind, key), None)
        return w["body"]

    def on_reply(self, kind: str, body: dict):
        key = body.get("token", "") if kind == "fence" else ("" if kind == "ping" else str(body.get("cmd", "")))
        with self.lock:
            self.counts["replies"] += 1
            if kind in ("cmd", "query", "stop") and body.get("outcome") in TERMINAL and key in self.fwd and self.mode != "PLAIN":
                prev = self.rcpt.get(key)
                if prev is None:
                    x = {"k": "rcpt", "cid": key, "outcome": body["outcome"], "reason": body.get("reason", ""), "level": int(body.get("level", 0)),
                         "ov": int(body.get("owner_version", 0)), "seq": int(body.get("seq", 0)), "t": round(time.time(), 6)}
                    self.rcpt[key] = x
                    self._append(x)
                elif prev["outcome"] != body["outcome"]:
                    self.contradictions += 1  # terminal receipts never change; reported, the first one stays
                    self.log_fh and self.log_fh.write(json.dumps({"op": "OWNER_CONTRADICTION", "cid": key, "first": prev["outcome"],
                                                                  "now": body["outcome"], "t": round(time.time(), 6)}) + "\n")
            for w in self.waiters.get((kind, key), []):
                w["body"] = body
                w["ev"].set()

    # ---- helpers ----------------------------------------------------------
    def left(self, t0: float) -> float:
        return self.a.budget_s - (time.monotonic() - t0)

    def receipt(self, cid: str, outcome: str, level: int, ov: int, reason: str) -> dict:
        f = self.fwd[cid]
        terminal = outcome in TERMINAL
        return {"schema_version": 1, "domain_id": self.a.device_id, "generation": self.a.generation, "command_id": cid,
                "intent_hash": f["intent"], "outcome": outcome, "owner_version": max(1, int(ov)) if terminal else int(ov),
                "receipt_version": max(1, int(ov)) if terminal else int(ov), "state": [int(level)] if terminal else [], "reason": reason}

    def cached(self, cid: str):
        r = self.rcpt.get(cid)
        return None if r is None else self.receipt(cid, r["outcome"], r["level"], r["ov"], "FIRMWARE_" + (r["reason"] or r["outcome"]))

    def forward(self, cid: str, seq: int, intent: str, value: int, via: str):
        with self.lock:
            f = self.fwd.get(cid)
            if f is not None:
                if f["intent"] != intent or f["seq"] != seq:
                    raise Reject(409, {"error": "IDENTITY_CONFLICT"})
                return f
            f = {"k": "fwd", "cid": cid, "seq": seq, "intent": intent, "value": value, "via": via, "t": round(time.time(), 6)}
            self._append(f)  # durable before the first message that carries the identity
            self.fwd[cid] = f
            return f

    def _validate(self, req: dict, what: str):
        if req.get("schema_version", 1) != 1:
            raise Reject(400, {"error": "BAD_REQUEST", "detail": "schema_version"})
        if req.get("domain_id") != self.a.device_id or int(req.get("generation", 0)) != self.a.generation:
            raise Reject(400, {"error": "BAD_REQUEST", "detail": f"{what}: domain/generation"})

    # ---- protocol ---------------------------------------------------------
    def capabilities(self) -> dict:
        return {"schema_version": 1, "profile": "DEVICE", "adapter_version": f"mqtt-owner-gateway/{release()}/{self.mode}",
                "domain_id": self.a.device_id, "generation": self.a.generation, "installed_epoch": self.a.epoch, "dimension": 1,
                "supported_operations": ["ADD"], "component_min": [0], "verified_test_ids": self.contract.get("verified_test_ids", []),
                "evidence_contract": self.contract}

    def effect(self, req: dict, t0: float) -> dict:
        self._validate(req, "effect")
        cid = str(req.get("command_id", ""))
        if not cid:
            raise Reject(400, {"error": "BAD_REQUEST", "detail": "command_id"})
        if req.get("operation") != "ADD" or not isinstance(req.get("value"), list) or len(req["value"]) != 1:
            raise Reject(400, {"error": "BAD_REQUEST", "detail": "ADD with one value only"})
        seq = int(req.get("sequence", 0))
        if seq <= 0:
            raise Reject(400, {"error": "BAD_REQUEST", "detail": "sequence must be greater than zero"})
        h = canonical_hash(req)
        if req.get("intent_hash") and req["intent_hash"] != h:
            raise Reject(400, {"error": "BAD_REQUEST", "detail": "intent_hash mismatch"})
        value = int(req["value"][0])
        self.forward(cid, seq, h, value, "effect")
        rep = self.rpc(self.data, "cmd", cid, {"cmd": cid, "seq": seq, "value": value}, self.a.qos_cmd, self.left(t0))
        self.log(op="effect", cid=cid, seq=seq, outcome=(rep or {}).get("outcome"), reason=(rep or {}).get("reason"),
                 ms=round((time.monotonic() - t0) * 1000, 1))
        if rep is None:
            raise Reject(504, {"error": "OWNER_TIMEOUT", "detail": "no reply from the firmware in time: the effect may or may not happen"})
        out = rep.get("outcome")
        if out in TERMINAL:
            with self.lock:
                crash = self.armed if out == "APPLIED" else ""
            if crash:
                self.log(op="crash", cid=cid, at=crash)
                os._exit(17)  # the firmware applied it; the authority gets no receipt (client abandon)
            return self.receipt(cid, out, rep.get("level", 0), rep.get("owner_version", 0), "FIRMWARE_" + str(rep.get("reason", out)))
        if rep.get("reason") == "IDENTITY_CONFLICT":
            raise Reject(409, {"error": "IDENTITY_CONFLICT"})
        raise Reject(503, {"error": "OWNER_UNCERTAIN", "detail": str(rep.get("reason", ""))})

    def query(self, cid: str, t0: float) -> dict:
        if self.mode == "PLAIN":
            raise Reject(409, {"error": "UNSUPPORTED"})
        f = self.fwd.get(cid)
        if f is None:
            raise Reject(404, {"error": "NOT_FOUND", "detail": "never forwarded"})
        c = self.cached(cid)
        if c is not None:
            self.log(op="query", cid=cid, outcome=c["outcome"], cached=True)
            return c
        rep = self.rpc(self.ctl, "query", cid, {"cmd": cid, "seq": f["seq"]}, 1, self.left(t0))
        self.log(op="query", cid=cid, outcome=(rep or {}).get("outcome"), reason=(rep or {}).get("reason"), ms=round((time.monotonic() - t0) * 1000, 1))
        if rep is None:
            raise Reject(504, {"error": "OWNER_TIMEOUT"})
        out = rep.get("outcome")
        if out in TERMINAL:
            return self.receipt(cid, out, rep.get("level", 0), rep.get("owner_version", 0), "FIRMWARE_" + str(rep.get("reason", out)))
        if out == "NOT_FOUND":
            raise Reject(404, {"error": "NOT_FOUND", "detail": str(rep.get("reason", ""))})
        return self.receipt(cid, "UNCERTAIN", 0, 0, "FIRMWARE_" + str(rep.get("reason", "UNCERTAIN")))

    def stop(self, req: dict, t0: float) -> dict:
        if self.mode != "STOP":
            raise Reject(409, {"error": "STOP_UNSUPPORTED"})
        self._validate(req, "stop")
        cid, intent, seq = str(req.get("command_id", "")), str(req.get("intent_hash", "")), int(req.get("sequence", 0))
        if not cid or not intent or seq <= 0:
            raise Reject(400, {"error": "BAD_REQUEST", "detail": "stop requires command_id, intent_hash, sequence"})
        self.forward(cid, seq, intent, 0, "stop")
        c = self.cached(cid)
        if c is not None:
            self.log(op="stop", cid=cid, outcome=c["outcome"], cached=True)
            return c
        rep = self.rpc(self.ctl, "stop", cid, {"cmd": cid, "seq": seq}, 1, self.left(t0))
        self.log(op="stop", cid=cid, outcome=(rep or {}).get("outcome"), reason=(rep or {}).get("reason"), ms=round((time.monotonic() - t0) * 1000, 1))
        if rep is None:
            raise Reject(504, {"error": "OWNER_TIMEOUT"})
        out = rep.get("outcome")
        if out in TERMINAL:
            return self.receipt(cid, out, rep.get("level", 0), rep.get("owner_version", 0), "FIRMWARE_" + str(rep.get("reason", out)))
        if rep.get("reason") == "IDENTITY_CONFLICT":
            raise Reject(409, {"error": "IDENTITY_CONFLICT"})
        return self.receipt(cid, "UNCERTAIN", 0, 0, "FIRMWARE_" + str(rep.get("reason", "UNCERTAIN")))

    def barrier(self, req: dict, t0: float) -> dict:
        if "F" not in MODES[self.mode]:
            raise Reject(409, {"error": "BARRIER_UNSUPPORTED"})
        self._validate(req, "barrier")
        token, cutoff = str(req.get("token", "")), int(req.get("cutoff_sequence", 0))
        if not token or cutoff <= 0:
            raise Reject(400, {"error": "BAD_REQUEST", "detail": "barrier requires token and cutoff_sequence > 0"})
        rep = self.rpc(self.ctl, "fence", token, {"cutoff": cutoff, "token": token}, 1, self.left(t0))
        if rep is None:
            self.log(op="barrier", token=token, cutoff=cutoff, result="TIMEOUT")
            raise Reject(504, {"error": "OWNER_TIMEOUT", "detail": "no fence reply"})
        if not rep.get("ok") or int(rep.get("cutoff", 0)) < cutoff:
            self.log(op="barrier", token=token, cutoff=cutoff, result="NOT_INSTALLED", reply=rep)
            raise Reject(502, {"error": "OWNER_ERROR", "detail": f"cutoff not installed: {rep}"})
        with self.lock:
            ids = sorted(cid for cid, f in self.fwd.items() if f["seq"] <= cutoff)
            need = [cid for cid in ids if cid not in self.rcpt]
        # one query per identity without a terminal receipt, sent together, answered after the cutoff
        waits = {}
        for cid in need:
            w = {"ev": threading.Event(), "body": None}
            with self.lock:
                self.waiters.setdefault(("query", cid), []).append(w)
                self.counts["published"] += 1
            self.ctl.publish("query", {"cmd": cid, "seq": self.fwd[cid]["seq"]}, 1)
            waits[cid] = w
        missing = []
        for cid, w in waits.items():
            w["ev"].wait(max(0.0, self.left(t0)))
            with self.lock:
                ws = self.waiters.get(("query", cid), [])
                if w in ws:
                    ws.remove(w)
                if not ws:
                    self.waiters.pop(("query", cid), None)
            if w["body"] is None and cid not in self.rcpt:
                missing.append(cid)
        if missing:
            self.log(op="barrier", token=token, cutoff=cutoff, result="QUERY_TIMEOUT", missing=missing[:10])
            raise Reject(504, {"error": "OWNER_TIMEOUT", "detail": f"no query reply after the cutoff for {len(missing)} identities"})
        receipts, uncertain = [], []
        for cid in ids:
            c = self.cached(cid)
            if c is not None:
                receipts.append(c)
                continue
            body = waits[cid]["body"] or {}
            reason = str(body.get("reason", body.get("outcome", "UNCERTAIN")))
            receipts.append(self.receipt(cid, "UNCERTAIN", 0, 0, "FIRMWARE_" + reason))
            uncertain.append(cid)
        self.log(op="barrier", token=token, cutoff=cutoff, result="OK", receipts=len(receipts), queried=len(need), uncertain=uncertain[:10],
                 level=rep.get("level"), ms=round((time.monotonic() - t0) * 1000, 1))
        return {"schema_version": 1, "domain_id": self.a.device_id, "generation": self.a.generation, "token": token, "cutoff_sequence": cutoff,
                "owner_version": max(1, int(rep.get("owner_version", 0))), "state": [int(rep.get("level", 0))], "receipts": receipts,
                "reason": "FIRMWARE_CUTOFF_INSTALLED"}

    def close(self):
        for fh in (self.log_fh, self.state_fh):
            if fh is not None:
                try:
                    fh.close()
                except OSError:
                    pass

    # ---- admin ------------------------------------------------------------
    def set_online(self, v: bool):
        self.link.set_up(v)
        self.log(op="online", v=v)

    def arm(self, at: str):
        with self.lock:
            self.armed = at
        self.log(op="arm", at=at)


def make_handlers(g: Gateway):
    class Base(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def route(self) -> str:
            """The request path without the optional /d/<device-id> prefix (as for cmd/evicon-device)."""
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
            with g.lock:
                g.counts["requests"] += 1
            try:
                if path == "/healthz":
                    return self.send(200, {"ok": True, "role": ROLE, "pid": os.getpid(), "release": release(), "device": g.a.device_id,
                                           "online": g.link.up, "mode": g.mode, "owner": "mosquitto+zephyr-firmware", "ns": g.a.ns,
                                           "firmware": g.fw})
                if path == "/v1/capabilities":
                    return self.send(200, g.capabilities())
                if path.startswith("/v1/receipts/"):
                    cid = urllib.parse.unquote(path[len("/v1/receipts/"):])
                    return self.send(200, g.query(cid, t0))
                if path == "/v1/state":
                    return self.send(200, {"schema_version": 1, "domain_id": g.a.device_id, "generation": g.a.generation,
                                           "evidence": "NONE_MQTT_GATEWAY_STATE_IS_NOT_EVIDENCE"})
                self.send(404, {"error": "NOT_FOUND"})
            except Reject as e:
                g.log(op="reply", path=path, code=e.code, err=e.body.get("error"))
                self.send(e.code, e.body)

        def do_POST(self):
            t0 = time.monotonic()
            path = self.route()
            with g.lock:
                g.counts["requests"] += 1
            try:
                if path == "/v1/effects":
                    return self.send(200, g.effect(self.body(), t0))
                if path == "/v1/stop":
                    return self.send(200, g.stop(self.body(), t0))
                if path == "/v1/barriers":
                    return self.send(200, g.barrier(self.body(), t0))
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
                with g.lock:
                    st = {"device": g.a.device_id, "mode": g.mode, "ns": g.a.ns, "armed": g.armed, "forwarded": len(g.fwd),
                          "receipts": len(g.rcpt), "contradictions": g.contradictions, "counts": dict(g.counts)}
                st["link"] = g.link.describe()
                return self.send(200, st)
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
                    return self.send(400, {"error": f"crash point {at!r}: the gateway has {CRASH_POINTS}"})
                g.arm(at)
                return self.send(200, {"armed": at})
            self.send(404, {"error": "NOT_FOUND"})

    return Data, Admin


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mode", choices=sorted(MODES), required=True)
    ap.add_argument("--device-id", required=True)
    ap.add_argument("--ns", required=True, help="topic prefix of this cell (the firmware's -evc_ns)")
    ap.add_argument("--generation", type=int, default=1)
    ap.add_argument("--epoch", type=int, default=1)
    ap.add_argument("--listen", required=True, help="host:port of the protocol server")
    ap.add_argument("--admin-listen", required=True, help="host:port of the test-only admin server")
    ap.add_argument("--broker", required=True, help="Mosquitto host:port (reached through the gateway's link)")
    ap.add_argument("--contract-file", required=True, help="the MEASURED contract of this firmware mode, advertised in /v1/capabilities")
    ap.add_argument("--state", required=True, help="durable record (JSON lines): forwarded identities and the firmware's terminal receipts")
    ap.add_argument("--budget-s", type=float, default=1.8, help="time for the MQTT exchanges of one request (the authority waits 2 s)")
    ap.add_argument("--qos-cmd", type=int, choices=(1, 2), default=1, help="QoS of the command messages (the measured call shape: 1)")
    ap.add_argument("--keepalive-s", type=int, default=60)
    ap.add_argument("--log", default="gateway.jsonl")
    a = ap.parse_args(argv)
    g = Gateway(a)
    Data, Admin = make_handlers(g)
    data = ThreadingHTTPServer(hostport(a.listen), Data)
    admin = ThreadingHTTPServer(hostport(a.admin_listen), Admin)
    data.daemon_threads = admin.daemon_threads = True
    threading.Thread(target=admin.serve_forever, daemon=True).start()
    g.log(op="start", pid=os.getpid(), mode=a.mode, device=a.device_id, ns=a.ns, broker=a.broker, release=release(),
          contract=g.contract.get("id"), firmware=g.fw, forwarded=len(g.fwd), receipts=len(g.rcpt), qos_cmd=a.qos_cmd)
    print(f"mqtt owner gateway {a.device_id} mode={a.mode} ns={a.ns} on {a.listen} (admin {a.admin_listen}) -> {a.broker}", flush=True)
    try:
        data.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for m in (g.data, g.ctl):
            m.close()
        g.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
