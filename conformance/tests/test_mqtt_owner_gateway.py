"""MQTT owner gateway of experiment C2 (release 074) against an in-process model of the Zephyr EviCon
firmware (the decision rules of firmware/zephyr-cutoff/src/evc_core.c, with a small receipt ring), so the
test needs no broker. The real firmware, Mosquitto and the gateway's paho connections are exercised by
`measure zephyr*` and the C2 campaign on the testbed (and by the pre-release run on the simulated testbed).

Z1 the advertised contract must be MEASURED and match the mode; the firmware must answer in that mode
Z2 effects: cutoff mode dedups a repeated identity (one effect), plain mode executes it again
Z3 queries: terminal receipts the firmware returned earlier; NOT_FOUND -> 404; never forwarded -> 404;
   plain mode -> 409
Z4 barrier: every forwarded identity at or below the cutoff is reported: applied (reply seen), applied
   (reply lost: asked after the cutoff), never delivered (sealed STOPPED), evicted from the RAM ring
   without a seen reply (UNCERTAIN); the state is the firmware's level at the fence
Z5 link down: the command waits (504 within the budget) and is delivered after the link is up again;
   its late reply becomes a receipt the next barrier reports
Z6 crash point: the gateway exits after the firmware applied, before replying; a restarted gateway
   (same durable record) reports that receipt
Z7 stop mode: tombstone for a command not delivered yet, a later copy is STOPPED, a stop after the effect
   is APPLIED; cutoff mode answers 409 STOP_UNSUPPORTED
Z8 the link relay resets live connections and refuses new ones while down (real TCP)
Z9 the HTTP protocol (/d/<device> prefix, status codes) end to end through the handlers
"""
import argparse
import json
import os
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from collections import OrderedDict
from http.server import ThreadingHTTPServer
from pathlib import Path

from reference import mqtt_owner_gateway as G
from reference.s3_owner_gateway import Reject, canonical_hash
from tests.helpers import free_port

REPO = Path(__file__).resolve().parents[2]
DEV = "pump0-1"


def contract(mode: str, tmp: Path, caps=None, source="MEASURED") -> str:
    c = {"schema_version": 1, "id": f"test-zephyr-{mode.lower()}", "source": source, "system": "test", "operation": "test",
         "capabilities": sorted(G.MODES[mode] if caps is None else caps), "dedup_horizon_ms": 600000,
         "fault_model": "network loss/delay, client abandon; owner up (no owner crash or reboot)"}
    p = tmp / f"contract-{mode}.json"
    p.write_text(json.dumps(c))
    return str(p)


class FakeFirmware:
    """evc_core.c's decisions (cutoff, receipt ring with eviction watermark and guard, tombstones), main.c's
    plain mode and ping; the plant is self.effects."""

    def __init__(self, mode: str, ring: int = 32, device: str = DEV):
        self.mode, self.ring_n, self.device = mode, ring, device
        self.ring, self.tombs = OrderedDict(), {}
        self.cutoff = self.evicted_max = self.level = self.ov = 0
        self.effects = []

    def rcpt(self, cid, seq, outcome, reason, level=None, ov=None):
        return {"device": self.device, "cmd": cid, "seq": seq, "outcome": outcome, "reason": reason,
                "level": self.level if level is None else level, "owner_version": self.ov if ov is None else ov}

    def store(self, r):
        self.ring[r["cmd"]] = r
        self.ring.move_to_end(r["cmd"])
        while len(self.ring) > self.ring_n:
            _, old = self.ring.popitem(last=False)
            if old["outcome"] == "APPLIED":
                self.evicted_max = max(self.evicted_max, old["seq"])
        return r

    def handle(self, kind, b):
        if kind == "ping":
            return {"device": self.device, "mode": self.mode, "ok": True, "level": self.level, "cutoff": self.cutoff}
        if self.mode == "plain":
            if kind != "cmd":
                return None  # not subscribed
            self.effects.append(b["cmd"])
            self.level += b["value"]
            self.ov += 1
            return self.rcpt(b["cmd"], b["seq"], "APPLIED", "APPLIED")
        cid, seq = b.get("cmd", ""), int(b.get("seq", 0))
        if kind == "cmd":
            if cid in self.tombs:
                return self.rcpt(cid, seq, "STOPPED", "STOP_TOMBSTONE", 0, 0)
            if cid in self.ring:
                r = dict(self.ring[cid])
                if r["outcome"] == "APPLIED":
                    r["reason"] = "REPLAY"
                return r
            if seq <= self.evicted_max:
                return {"cmd": cid, "outcome": "UNCERTAIN", "reason": "RECEIPT_MAY_BE_EVICTED", "error": -11}
            self.ov += 1
            if seq <= self.cutoff:
                return self.store(self.rcpt(cid, seq, "STOPPED", "SEALED_BY_FENCE"))
            self.effects.append(cid)
            self.level += int(b["value"])
            return self.store(self.rcpt(cid, seq, "APPLIED", "APPLIED"))
        if kind == "query":
            if cid in self.tombs:
                return self.rcpt(cid, self.tombs[cid], "STOPPED", "STOP_TOMBSTONE", 0, 0)
            if cid in self.ring:
                return self.ring[cid]
            if seq <= self.evicted_max:
                return self.rcpt(cid, seq, "UNCERTAIN", "RECEIPT_MAY_BE_EVICTED", 0, 0)
            if seq <= self.cutoff:
                return self.rcpt(cid, seq, "STOPPED", "SEALED_NOT_STARTED", 0, 0)
            return self.rcpt(cid, seq, "NOT_FOUND", "NOT_STARTED", 0, 0)
        if kind == "fence":
            c = int(b["cutoff"])
            if c > self.cutoff:
                self.cutoff = c
                self.ov += 1
                self.tombs = {k: v for k, v in self.tombs.items() if v > c}
            return {"cutoff": self.cutoff, "token": b["token"], "ok": True, "owner_version": self.ov, "level": self.level}
        if kind == "stop":
            if self.mode != "stop":
                return {"cmd": cid, "outcome": "UNCERTAIN", "reason": "STOP_FAILED", "error": -95}
            if cid in self.tombs:
                return self.rcpt(cid, seq, "STOPPED", "STOP_TOMBSTONE", 0, 0)
            if cid in self.ring:
                return self.ring[cid]
            certain = seq > self.evicted_max
            if seq <= self.cutoff or not certain:
                return self.rcpt(cid, seq, "STOPPED" if certain else "UNCERTAIN",
                                 "SEALED_NOT_STARTED" if certain else "FUTURE_BLOCKED_PAST_UNKNOWN", 0, 0)
            self.tombs[cid] = seq
            self.ov += 1
            return self.rcpt(cid, seq, "STOPPED", "STOP_TOMBSTONE", 0, self.ov)
        return None


class FakeLink:
    def __init__(self):
        self.up, self.port, self.pending = True, 0, []

    def set_up(self, v):
        self.up = v
        if v:
            pend, self.pending = self.pending, []
            for conn, kind, body in pend:
                conn.deliver(kind, body)

    def describe(self):
        return {"up": self.up, "pending": len(self.pending)}


class FakeConn:
    def __init__(self, gw, fw, link, role):
        self.gw, self.fw, self.link, self.role = gw, fw, link, role
        self.subscribed = threading.Event()
        self.subscribed.set()
        self.lose_replies = 0  # the next n replies never reach the gateway

    def publish(self, kind, body, qos):
        if not self.link.up:
            self.link.pending.append((self, kind, dict(body)))  # paho keeps it and sends it after reconnecting
            return
        self.deliver(kind, body)

    def deliver(self, kind, body):
        rep = self.fw.handle(kind, body)
        if rep is None:
            return
        if self.lose_replies:
            self.lose_replies -= 1
            return
        self.gw.on_reply(kind, rep)

    def close(self):
        pass


def args(mode, tmp: Path, state=None, budget=0.3, cfile=None):
    return argparse.Namespace(mode=mode, device_id=DEV, ns="t1", generation=1, epoch=1, listen="", admin_listen="", broker="127.0.0.1:1",
                              contract_file=cfile or contract(mode, tmp), state=str(state or tmp / "gw.state.jsonl"), budget_s=budget,
                              qos_cmd=1, keepalive_s=60, log=str(tmp / "gw.jsonl"))


def make(mode, tmp: Path, fw=None, **kw):
    fw = fw or FakeFirmware(G.FW_MODE[mode])
    link = FakeLink()
    holder = {}

    def net(gw):
        holder["data"], holder["ctl"] = FakeConn(gw, fw, link, "data"), FakeConn(gw, fw, link, "ctl")
        return link, holder["data"], holder["ctl"]
    gw = G.Gateway(args(mode, tmp, **kw), net=net)
    OPEN.append(gw)
    return gw, fw, link, holder


OPEN = []


def tearDownModule():
    for gw in OPEN:
        gw.close()


def req(cid, seq, value=2):
    r = {"schema_version": 1, "domain_id": DEV, "generation": 1, "command_id": cid, "operation": "ADD", "value": [value], "epoch": 1,
         "sequence": seq}
    return r


def stop_req(cid, seq, value=2):
    return {"schema_version": 1, "domain_id": DEV, "generation": 1, "command_id": cid, "intent_hash": canonical_hash(req(cid, seq, value)),
            "sequence": seq}


def barrier(cut, token="t1"):
    return {"schema_version": 1, "domain_id": DEV, "generation": 1, "token": token, "cutoff_sequence": cut}


def code(fn, *a):
    try:
        return 200, fn(*a)
    except Reject as e:
        return e.code, e.body


class GatewayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_z1_contract_and_mode(self):
        with self.assertRaises(SystemExit):
            make("CUTOFF", self.tmp, cfile=contract("CUTOFF", self.tmp, caps={"D_h", "Q_A"}))
        with self.assertRaises(SystemExit):
            make("STOP", self.tmp, cfile=contract("STOP", self.tmp, source="DECLARED"))
        with self.assertRaises(SystemExit):  # the firmware runs another mode
            make("STOP", self.tmp, fw=FakeFirmware("cutoff"))
        gw, _, _, _ = make("CUTOFF", self.tmp)
        cap = gw.capabilities()
        self.assertEqual(cap["profile"], "DEVICE")
        self.assertEqual(set(cap["evidence_contract"]["capabilities"]), {"D_h", "Q_A", "F"})

    def test_z2_dedup_and_plain_duplicates(self):
        gw, fw, _, _ = make("CUTOFF", self.tmp)
        t = time.monotonic()
        r1 = gw.effect(req("c1", 1), t)
        r2 = gw.effect(req("c1", 1), time.monotonic())
        self.assertEqual((r1["outcome"], r2["outcome"]), ("APPLIED", "APPLIED"))
        self.assertEqual(fw.effects, ["c1"])
        self.assertEqual(r1["intent_hash"], canonical_hash(req("c1", 1)))
        self.assertEqual(r1["state"], [2])
        self.assertEqual(code(gw.effect, dict(req("c1", 1), value=[3]), time.monotonic())[0], 409)  # another intent
        p, pfw, _, _ = make("PLAIN", Path(tempfile.mkdtemp()))
        p.effect(req("c1", 1), time.monotonic())
        p.effect(req("c1", 1), time.monotonic())
        self.assertEqual(pfw.effects, ["c1", "c1"])  # no dedup: the repeat is a second effect

    def test_z3_queries(self):
        gw, fw, _, h = make("CUTOFF", self.tmp)
        gw.effect(req("c1", 1), time.monotonic())
        self.assertEqual(gw.query("c1", time.monotonic())["outcome"], "APPLIED")
        self.assertEqual(code(gw.query, "nope", time.monotonic())[0], 404)
        h["data"].lose_replies = 1
        self.assertEqual(code(gw.effect, req("c2", 2), time.monotonic())[0], 504)  # applied, reply lost
        self.assertEqual(gw.query("c2", time.monotonic())["outcome"], "APPLIED")
        gw.link.set_up(False)
        self.assertEqual(code(gw.effect, req("c3", 3), time.monotonic())[0], 504)  # held by the client library
        gw.link.pending.clear()  # ... and lost
        gw.link.set_up(True)
        self.assertEqual(code(gw.query, "c3", time.monotonic())[0], 404)  # NOT_FOUND: not evidence
        p, _, _, _ = make("PLAIN", Path(tempfile.mkdtemp()))
        p.effect(req("c1", 1), time.monotonic())
        self.assertEqual(code(p.query, "c1", time.monotonic())[0], 409)
        self.assertEqual(code(p.barrier, barrier(1), time.monotonic())[0], 409)

    def test_z4_barrier_reports_every_identity(self):
        gw, fw, _, h = make("CUTOFF", self.tmp, fw=FakeFirmware("cutoff", ring=4))
        h["data"].lose_replies = 1
        code(gw.effect, req("x1", 1), time.monotonic())                # applied, reply lost, later evicted
        for i in range(2, 6):
            gw.effect(req(f"x{i}", i), time.monotonic())               # applied, reply seen
        h["data"].lose_replies = 1
        code(gw.effect, req("x6", 6), time.monotonic())                # applied, reply lost, still in the ring
        gw.link.set_up(False)
        code(gw.effect, req("x7", 7), time.monotonic())                # never delivered
        gw.link.pending.clear()
        gw.link.set_up(True)
        br = gw.barrier(barrier(7), time.monotonic())
        got = {r["command_id"]: (r["outcome"], r["reason"]) for r in br["receipts"]}
        self.assertEqual(set(got), {f"x{i}" for i in range(1, 8)})     # every forwarded identity
        self.assertEqual(got["x1"], ("UNCERTAIN", "FIRMWARE_RECEIPT_MAY_BE_EVICTED"))
        self.assertTrue(all(got[f"x{i}"][0] == "APPLIED" for i in range(2, 7)))
        self.assertEqual(got["x7"], ("STOPPED", "FIRMWARE_SEALED_NOT_STARTED"))
        self.assertEqual(br["state"], [fw.level])
        self.assertEqual(br["cutoff_sequence"], 7)
        self.assertGreater(br["owner_version"], 0)
        late = gw.effect(req("x7", 7), time.monotonic())  # a late copy below the cutoff is sealed
        self.assertEqual(late["outcome"], "STOPPED")
        self.assertNotIn("x7", fw.effects)
        self.assertEqual(code(gw.barrier, barrier(0), time.monotonic())[0], 400)

    def test_z5_link_down_late_delivery(self):
        gw, fw, link, _ = make("CUTOFF", self.tmp)
        link.set_up(False)
        self.assertEqual(code(gw.effect, req("c1", 1), time.monotonic())[0], 504)
        self.assertEqual(fw.effects, [])
        link.set_up(True)  # paho sends it after reconnecting: a late effect
        self.assertEqual(fw.effects, ["c1"])
        self.assertIn("c1", gw.rcpt)  # the late reply is owner evidence
        br = gw.barrier(barrier(1), time.monotonic())
        self.assertEqual([(r["command_id"], r["outcome"]) for r in br["receipts"]], [("c1", "APPLIED")])

    def test_z6_crash_point_and_restart(self):
        gw, fw, _, _ = make("CUTOFF", self.tmp)
        gw.arm("AFTER_EFFECT_BEFORE_RECEIPT")
        exits = []
        orig = os._exit
        os._exit = lambda c: (exits.append(c), (_ for _ in ()).throw(SystemExit(c)))
        try:
            with self.assertRaises(SystemExit):
                gw.effect(req("c1", 1), time.monotonic())
        finally:
            os._exit = orig
        self.assertEqual(exits, [17])
        self.assertEqual(fw.effects, ["c1"])
        gw2, _, _, _ = make("CUTOFF", self.tmp, fw=fw)  # same durable record, same firmware
        self.assertIn("c1", gw2.fwd)
        self.assertEqual(gw2.rcpt["c1"]["outcome"], "APPLIED")
        br = gw2.barrier(barrier(1), time.monotonic())
        self.assertEqual(br["receipts"][0]["outcome"], "APPLIED")

    def test_z7_stop_mode(self):
        gw, fw, link, _ = make("STOP", self.tmp)
        link.set_up(False)
        code(gw.effect, req("s1", 1), time.monotonic())  # held
        link.set_up(True)
        link.pending.clear()
        self.assertEqual(fw.effects, ["s1"])  # delivered when the link came back
        s = gw.stop(stop_req("s1", 1), time.monotonic())
        self.assertEqual(s["outcome"], "APPLIED")
        link.set_up(False)
        code(gw.effect, req("s2", 2), time.monotonic())  # held by the client library
        held = list(link.pending)
        link.pending.clear()
        link.set_up(True)
        s2 = gw.stop(stop_req("s2", 2), time.monotonic())
        self.assertEqual((s2["outcome"], s2["reason"]), ("STOPPED", "FIRMWARE_STOP_TOMBSTONE"))
        for conn, kind, body in held:  # the held copy arrives after the stop
            conn.deliver(kind, body)
        self.assertNotIn("s2", fw.effects)
        c, _, _, _ = make("CUTOFF", Path(tempfile.mkdtemp()))
        self.assertEqual(code(c.stop, stop_req("x", 1), time.monotonic())[0], 409)


class LinkTest(unittest.TestCase):
    def test_z8_reset_and_refuse(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(8)

        def echo():
            while True:
                try:
                    c, _ = srv.accept()
                except OSError:
                    return

                def pump(c=c):
                    try:
                        while True:
                            d = c.recv(1024)
                            if not d:
                                break
                            c.sendall(d)
                    except OSError:
                        pass
                    c.close()
                threading.Thread(target=pump, daemon=True).start()
        threading.Thread(target=echo, daemon=True).start()
        link = G.Link("127.0.0.1", srv.getsockname()[1])
        c = socket.create_connection(("127.0.0.1", link.port), timeout=2)
        c.sendall(b"ping")
        self.assertEqual(c.recv(4), b"ping")
        link.set_up(False)
        c.settimeout(2)
        self.assertIn(c.recv(4), (b"",))  # reset: the peer sees the connection closed
        d = socket.create_connection(("127.0.0.1", link.port), timeout=2)
        d.settimeout(2)
        try:
            got = d.recv(4)
        except ConnectionResetError:
            got = b""
        self.assertEqual(got, b"")  # refused while down
        link.set_up(True)
        e = socket.create_connection(("127.0.0.1", link.port), timeout=2)
        e.sendall(b"pong")
        e.settimeout(2)
        self.assertEqual(e.recv(4), b"pong")
        self.assertEqual(link.describe()["resets"], 1)
        srv.close()


class HttpTest(unittest.TestCase):
    def test_z9_http_protocol(self):
        tmp = Path(tempfile.mkdtemp())
        gw, fw, _, _ = make("CUTOFF", tmp)
        Data, Admin = G.make_handlers(gw)
        port, aport = free_port(), free_port()
        data, admin = ThreadingHTTPServer(("127.0.0.1", port), Data), ThreadingHTTPServer(("127.0.0.1", aport), Admin)
        for s in (data, admin):
            s.daemon_threads = True
            threading.Thread(target=s.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{port}/d/{DEV}"

        def call(method, url, body=None):
            r = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(), method=method,
                                       headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(r, timeout=5) as x:
                    return x.status, json.loads(x.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read() or b"null")
        self.assertEqual(call("GET", base + "/healthz")[1]["role"], "gateway")
        self.assertEqual(call("GET", base + "/healthz")[1]["firmware"]["mode"], "cutoff")
        st, rc = call("POST", base + "/v1/effects", req("h1", 1))
        self.assertEqual((st, rc["outcome"]), (200, "APPLIED"))
        self.assertEqual(call("GET", base + "/v1/receipts/h1")[1]["outcome"], "APPLIED")
        self.assertEqual(call("GET", base + "/v1/receipts/nope")[0], 404)
        self.assertEqual(call("POST", base + "/v1/stop", stop_req("h1", 1))[0], 409)
        st, br = call("POST", base + "/v1/barriers", barrier(1))
        self.assertEqual((st, br["receipts"][0]["command_id"]), (200, "h1"))
        self.assertEqual(call("GET", f"http://127.0.0.1:{port}/d/other/v1/receipts/h1")[0], 404)
        self.assertEqual(call("POST", f"http://127.0.0.1:{aport}/_test/online?v=false")[0], 200)
        self.assertFalse(gw.link.up)
        self.assertEqual(call("POST", f"http://127.0.0.1:{aport}/_test/crash?at=BEFORE")[0], 400)
        for s in (data, admin):
            s.shutdown()


if __name__ == "__main__":
    unittest.main()
