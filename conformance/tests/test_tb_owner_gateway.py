"""Release 077, experiment TB: the ThingsBoard owner gateway (conformance/reference/tb_owner_gateway.py) against an
in-process model of ThingsBoard's persistent RPC REST API (login, create, read, list, device delete) and of a device
that executes every RPC it receives.

G1 an effect creates a persistent two-way RPC without platform retries, with expirationTime = the request's
   expires_at_ms and the intent in its params; SUCCESSFUL -> an APPLIED receipt
G2 device offline: the effect is not a receipt (504); the query reads the RPC: UNCERTAIN TB_QUEUED; once the device
   is back it executes the RPC and the query answers APPLIED
G3 the RPC expires before the device is back: EXPIRED, never executed, the query answers UNCERTAIN TB_EXPIRED
G4 a gateway without the cached rpcId (a restart) finds the identity's RPC in the device's list; an unknown
   identity is 404; a repeated effect creates a second RPC (no idempotency key: the measured contract has no D_h)
G5 the crash fault is armed 100 ms after the arming request; the contract must be the MEASURED {Q_A, T};
   an expired JWT is renewed
"""
import argparse
import json
import tempfile
import threading
import time
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from reference import tb_owner_gateway as T
from reference.s3_owner_gateway import Reject


class FakeTB:
    def __init__(self):
        self.rpcs = {}  # id -> record
        self.effects = {}
        self.online = True
        self.lose_response = False
        self.tokens = set()
        self.lock = threading.Lock()
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def send(self, code, obj=None):
                data = b"" if obj is None else json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def authed(self):
                return self.headers.get("X-Authorization", "")[7:] in fake.tokens

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                if self.path == "/api/auth/login":
                    tok = uuid.uuid4().hex
                    fake.tokens.add(tok)
                    return self.send(200, {"token": tok})
                if not self.authed():
                    return self.send(401, {"message": "token expired"})
                if self.path.startswith("/api/rpc/twoway/"):
                    dev = self.path.rsplit("/", 1)[1]
                    rid = str(uuid.uuid4())
                    with fake.lock:
                        fake.rpcs[rid] = {"id": {"id": rid}, "deviceId": {"id": dev}, "status": "QUEUED", "createdTime": time.time(),
                                          "expirationTime": body.get("expirationTime"), "request": {"body": body}, "raw": body}
                    fake.deliver()
                    return self.send(200, {"rpcId": rid})
                self.send(404)

            def do_GET(self):
                if not self.authed():
                    return self.send(401, {"message": "token expired"})
                fake.deliver()
                if self.path.startswith("/api/rpc/persistent/device/"):
                    dev = self.path.split("/")[5].split("?")[0]
                    with fake.lock:
                        data = sorted((r for r in fake.rpcs.values() if r["deviceId"]["id"] == dev), key=lambda r: -r["createdTime"])
                    return self.send(200, {"data": [dict(r, request={"body": dict(r["raw"], params=json.dumps(r["raw"]["params"]))})
                                                    for r in data], "hasNext": False})
                if self.path.startswith("/api/rpc/persistent/"):
                    rid = self.path.rsplit("/", 1)[1]
                    with fake.lock:
                        r = fake.rpcs.get(rid)
                    return self.send(200, r) if r else self.send(404)
                self.send(404)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def deliver(self):
        """The device: executes every RPC still QUEUED while online and not expired; EXPIRED otherwise once past."""
        now = time.time() * 1000
        with self.lock:
            for r in self.rpcs.values():
                if r["status"] != "QUEUED":
                    continue
                if r["expirationTime"] and now > r["expirationTime"]:
                    r["status"] = "EXPIRED"
                elif self.online:
                    cid = r["raw"]["params"]["cmd"]
                    self.effects[cid] = self.effects.get(cid, 0) + 1
                    r["status"] = "TIMEOUT" if self.lose_response else "SUCCESSFUL"
                    r["response"] = {"outcome": "APPLIED"}

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def contract(tmp: Path, caps=("Q_A", "T"), source="MEASURED") -> str:
    p = tmp / "tb.json"
    p.write_text(json.dumps({"id": "meas-thingsboard-rpc-r0", "source": source, "capabilities": list(caps), "expiry_ms": 60000,
                             "fault_model": "network loss/delay, client abandon; owner up"}))
    return str(p)


def args(tmp: Path, cfile: str) -> argparse.Namespace:
    return argparse.Namespace(device_id="pump0-1", tb_device="dev-1", generation=1, epoch=1, listen="", admin_listen="", tb_url="",
                              device_admin="http://127.0.0.1:1", contract_file=cfile, budget_s=0.6, rpc_timeout_ms=5000,
                              expiration_s=60.0, poll_s=0.02, user_env="", password_env="", log=str(tmp / "gw.jsonl"))


def req(cid: str, seq: int, exp_ms: int = 0) -> dict:
    r = {"schema_version": 1, "domain_id": "pump0-1", "generation": 1, "command_id": cid, "operation": "ADD", "value": [2], "epoch": 1,
         "sequence": seq}
    if exp_ms:
        r["expires_at_ms"] = exp_ms
    return r


class TBGatewayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.fake = FakeTB()
        self.api = T.TB(self.fake.url, "tenant", "pw")
        self.assertTrue(self.api.login())
        self.g = T.Gateway(args(self.tmp, contract(self.tmp)), self.api)

    def tearDown(self):
        self.g.close()
        self.fake.close()

    def test_g1_effect(self):
        exp = int((time.time() + 30) * 1000)
        r = self.g.effect(req("c1", 1, exp), time.monotonic())
        self.assertEqual((r["outcome"], r["state"], r["reason"]), ("APPLIED", [2], "TB_RPC_SUCCESSFUL"))
        rec = next(iter(self.fake.rpcs.values()))["raw"]
        self.assertEqual((rec["persistent"], rec["retries"], rec["expirationTime"], rec["method"]), (True, 0, exp, "actuate"))
        self.assertEqual(rec["params"]["intent"], r["intent_hash"])
        self.assertEqual(self.fake.effects, {"c1": 1})

    def test_g2_offline_then_back(self):
        self.fake.online = False
        with self.assertRaises(Reject) as e:
            self.g.effect(req("c2", 2), time.monotonic())
        self.assertEqual(e.exception.code, 504)
        q = self.g.query("c2", time.monotonic())
        self.assertEqual((q["outcome"], q["reason"]), ("UNCERTAIN", "TB_QUEUED"))
        self.fake.online = True
        q = self.g.query("c2", time.monotonic())
        self.assertEqual(q["outcome"], "APPLIED")
        self.assertTrue(q["intent_hash"])
        self.assertEqual(self.fake.effects, {"c2": 1})

    def test_g3_expired(self):
        self.fake.online = False
        with self.assertRaises(Reject):
            self.g.effect(req("c3", 3, int((time.time() + 0.2) * 1000)), time.monotonic())
        time.sleep(0.3)
        self.fake.online = True
        q = self.g.query("c3", time.monotonic())
        self.assertEqual((q["outcome"], q["reason"]), ("UNCERTAIN", "TB_EXPIRED"))
        self.assertEqual(self.fake.effects, {})

    def test_g4_listing_and_no_dedup(self):
        self.g.effect(req("c4", 4), time.monotonic())
        g2 = T.Gateway(args(self.tmp, contract(self.tmp)), self.api)  # a restarted gateway: no cache
        self.addCleanup(g2.close)
        self.assertEqual(g2.query("c4", time.monotonic())["outcome"], "APPLIED")
        with self.assertRaises(Reject) as e:
            g2.query("nope", time.monotonic())
        self.assertEqual(e.exception.code, 404)
        g2.effect(req("c4", 4), time.monotonic())  # a blind retry: a second RPC, a second effect
        self.assertEqual(self.fake.effects["c4"], 2)

    def test_g5_arm_contract_relogin(self):
        self.g.arm("AFTER_EFFECT_BEFORE_RECEIPT")
        self.assertEqual(self.g.armed, "")
        time.sleep(T.ARM_DELAY_S + 0.1)
        self.assertEqual(self.g.armed, "AFTER_EFFECT_BEFORE_RECEIPT")
        self.g.arm("")
        self.assertEqual(self.g.armed, "")
        for caps, src in ((("Q_A",), "MEASURED"), (("Q_A", "T"), "DECLARED"), (("D_h", "Q_A", "T"), "MEASURED")):
            with self.assertRaises(SystemExit):
                T.Gateway(args(self.tmp, contract(self.tmp, caps, src)), self.api)
        self.fake.tokens.clear()  # the JWT expired
        self.assertEqual(self.g.effect(req("c5", 5), time.monotonic())["outcome"], "APPLIED")


if __name__ == "__main__":
    unittest.main()
