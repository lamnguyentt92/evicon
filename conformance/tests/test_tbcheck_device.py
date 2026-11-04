"""Release 085: the reference device of the ThingsBoard RPC checks (reference/mqtt_device_tbcheck.py) against a real
Mosquitto in ThingsBoard's topic layout (skipped without the mosquitto binary or paho), and the fixed rules of the
checks (tb_rpc_checks.rules) on synthetic trials.

B1 default modes: the frozen handler of reference/mqtt_device.py (effect, reply); the PUBACK leaves after the effect
B2 hold: no effect while held; release -> effect, reply; the PUBACK leaves after the effect (ackmode after)
B3 ackmode before + hold (the negative control): the PUBACK leaves while the handler is held, before the effect
B4 a hold times out after --hold-timeout-s; after the switches go back, the default path (paho's own PUBACK) again
B5 the admin endpoints reject bad values; /_test/events lists one command's steps
B6 the rules TB-1 to TB-4 on synthetic trials (MET / NOT MET / NOT REACHED; ABSENT / DETECTED; not interpretable)
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import tb_rpc_checks as K

HERE = Path(__file__).resolve().parents[1]
HAVE = shutil.which("mosquitto") is not None
try:
    import paho.mqtt.client as mqtt
except ImportError:  # pragma: no cover
    HAVE = False


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class FakePlant:
    """POST /v1/apply and GET /v1/truth of cmd/evicon-plant, with the time of every effect."""

    def __init__(self):
        self.effects = {}
        plant = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                plant.effects.setdefault(body["cmd"], []).append(time.time())
                self._send({"ok": True})

            def do_GET(self):
                q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                ts = plant.effects.get(q.get("cmd", [""])[0], [])
                self._send({"effects": len(ts), "first_effect_ns": int(ts[0] * 1e9) if ts else 0})

            def _send(self, obj):
                data = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


class PubackTap:
    """A TCP relay between the device and the broker that timestamps every PUBACK the device sends (MQTT packet type 4)."""

    def __init__(self, broker_port: int):
        self.pubacks, self.broker_port = [], broker_port
        self.ls = socket.socket()
        self.ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.ls.bind(("127.0.0.1", 0))
        self.ls.listen(4)
        self.port = self.ls.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                c, _ = self.ls.accept()
            except OSError:
                return
            b = socket.create_connection(("127.0.0.1", self.broker_port))
            threading.Thread(target=self._pipe, args=(b, c, False), daemon=True).start()
            threading.Thread(target=self._pipe, args=(c, b, True), daemon=True).start()

    def _pipe(self, src, dst, upstream):
        buf = b""
        while True:
            try:
                d = src.recv(65536)
            except OSError:
                d = b""
            if not d:
                for s in (src, dst):
                    try:
                        s.close()
                    except OSError:
                        pass
                return
            if upstream:
                buf += d
                while len(buf) >= 2:
                    n, mult, i = 0, 1, 1
                    while i < len(buf) and buf[i] & 0x80:
                        n += (buf[i] & 0x7F) * mult
                        mult *= 128
                        i += 1
                    if i >= len(buf):
                        break
                    n += (buf[i] & 0x7F) * mult
                    if len(buf) < i + 1 + n:
                        break
                    if buf[0] >> 4 == 4:
                        self.pubacks.append(time.time())
                    buf = buf[i + 1 + n:]
            try:
                dst.sendall(d)
            except OSError:
                return

    def close(self):
        self.ls.close()


@unittest.skipUnless(HAVE, "mosquitto binary or paho-mqtt missing")
class DeviceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.bport = free_port()
        conf = self.tmp / "m.conf"
        conf.write_text(f"listener {self.bport} 127.0.0.1\nallow_anonymous true\npersistence false\n")
        self.broker = subprocess.Popen(["mosquitto", "-c", str(conf)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", self.bport), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        self.plant = FakePlant()
        self.tap = PubackTap(self.bport)
        self.aport = free_port()
        env = dict(os.environ, TB_DEVICE_TOKEN="test-token")
        self.dev = subprocess.Popen([sys.executable, str(HERE / "reference" / "mqtt_device_tbcheck.py"), "--broker", f"127.0.0.1:{self.tap.port}",
                                     "--device", "tb-1", "--plant", self.plant.url, "--admin-port", str(self.aport), "--admin-bind", "127.0.0.1",
                                     "--hold-timeout-s", "2", "--check-log", str(self.tmp / "dev.jsonl")],
                                    env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.admin = f"http://127.0.0.1:{self.aport}"
        for _ in range(100):
            try:
                with urllib.request.urlopen(self.admin + "/healthz", timeout=1) as r:
                    if r.status == 200:
                        break
            except Exception:  # noqa: BLE001
                time.sleep(0.1)
        self.replies = []
        self.cli = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="platform", protocol=mqtt.MQTTv5)
        ready = threading.Event()
        self.cli.on_connect = lambda c, *a, **k: c.subscribe("v1/devices/me/rpc/response/+", qos=1)
        self.cli.on_subscribe = lambda *a, **k: ready.set()
        self.cli.on_message = lambda c, u, m: self.replies.append((m.topic, json.loads(m.payload), time.time()))
        self.cli.connect("127.0.0.1", self.bport)
        self.cli.loop_start()
        self.assertTrue(ready.wait(5))
        time.sleep(0.3)

    def tearDown(self):
        self.cli.loop_stop()
        self.dev.terminate()
        self.dev.wait(5)
        self.broker.terminate()
        self.broker.wait(5)
        self.plant.close()
        self.tap.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def post(self, path, v):
        req = urllib.request.Request(f"{self.admin}{path}?v={v}", data=b"", method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, None

    def get(self, path):
        with urllib.request.urlopen(self.admin + path, timeout=5) as r:
            return json.loads(r.read())

    def rpc(self, req_id, cmd):
        self.cli.publish(f"v1/devices/me/rpc/request/{req_id}", json.dumps({"method": "actuate", "params": {"cmd": cmd, "value": 1}}), qos=1)

    def wait(self, pred, timeout=5.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if pred():
                return True
            time.sleep(0.02)
        return False

    def evs(self, cmd):
        return [e["ev"] for e in self.get(f"/_test/events?cmd={cmd}")["events"]]

    def test_b1_default(self):
        n0 = len(self.tap.pubacks)
        self.rpc(7, "c-default")
        self.assertTrue(self.wait(lambda: self.replies and len(self.tap.pubacks) > n0))
        self.assertEqual(self.replies[0][0], "v1/devices/me/rpc/response/7")
        self.assertEqual(self.replies[0][1]["outcome"], "APPLIED")
        self.assertEqual(len(self.plant.effects["c-default"]), 1)
        self.assertGreater(self.tap.pubacks[-1], self.plant.effects["c-default"][0])
        self.assertEqual(self.evs("c-default"), ["rpc_received", "effect_done", "ack_after_return"])
        self.assertFalse(self.get("/_test/state")["manual_ack"])

    def test_b2_hold_ack_after(self):
        self.assertEqual(self.post("/_test/hold", "true")[0], 200)
        n0 = len(self.tap.pubacks)
        self.rpc(8, "c-hold")
        self.assertTrue(self.wait(lambda: self.get("/_test/state")["held_cmd"] == "c-hold"))
        time.sleep(0.8)
        self.assertNotIn("c-hold", self.plant.effects)
        self.assertEqual(len(self.tap.pubacks), n0, "no PUBACK while held in ackmode after")
        self.assertEqual(self.replies, [])
        self.post("/_test/hold", "false")
        self.assertTrue(self.wait(lambda: self.replies and len(self.tap.pubacks) > n0))
        self.assertEqual(len(self.plant.effects["c-hold"]), 1)
        self.assertGreater(self.tap.pubacks[-1], self.plant.effects["c-hold"][0])
        self.assertEqual(self.evs("c-hold"), ["rpc_received", "hold_start", "hold_end", "effect_done", "reply_sent", "ack_sent"])
        self.assertTrue(self.wait(lambda: not self.get("/_test/state")["manual_ack"]))

    def test_b3_ack_before(self):
        self.post("/_test/ackmode", "before")
        self.post("/_test/hold", "true")
        n0 = len(self.tap.pubacks)
        self.rpc(9, "c-ackb")
        self.assertTrue(self.wait(lambda: len(self.tap.pubacks) > n0), "the PUBACK leaves while the handler is held")
        self.assertEqual(self.get("/_test/state")["held_cmd"], "c-ackb")
        self.assertNotIn("c-ackb", self.plant.effects)
        t_ack = self.tap.pubacks[-1]
        self.post("/_test/hold", "false")
        self.assertTrue(self.wait(lambda: "c-ackb" in self.plant.effects and self.replies))
        self.assertLess(t_ack, self.plant.effects["c-ackb"][0])
        self.assertEqual(self.evs("c-ackb"), ["rpc_received", "ack_sent", "hold_start", "hold_end", "effect_done", "reply_sent"])
        self.post("/_test/ackmode", "after")
        self.assertTrue(self.wait(lambda: not self.get("/_test/state")["manual_ack"]))

    def test_b4_timeout_then_default(self):
        self.post("/_test/hold", "true")
        self.rpc(10, "c-timeout")
        self.assertTrue(self.wait(lambda: "c-timeout" in self.plant.effects, timeout=6))
        ev = self.get("/_test/events?cmd=c-timeout")["events"]
        self.assertEqual([e.get("how") for e in ev if e["ev"] == "hold_end"], ["timeout"])
        self.assertTrue(self.wait(lambda: not self.get("/_test/state")["manual_ack"]))
        self.rpc(11, "c-after")
        self.assertTrue(self.wait(lambda: len(self.replies) >= 2))
        self.assertEqual(self.evs("c-after"), ["rpc_received", "effect_done", "ack_after_return"])
        self.assertEqual(self.get("/_test/state")["counts"]["hold_timeouts"], 1)

    def test_b5_admin(self):
        self.assertEqual(self.post("/_test/hold", "maybe")[0], 400)
        self.assertEqual(self.post("/_test/ackmode", "never")[0], 400)
        st = self.get("/_test/state")
        self.assertEqual((st["hold_armed"], st["ackmode"], st["manual_ack"]), (False, "after", False))
        self.assertEqual(self.get("/_test/events?cmd=nothing")["events"], [])
        # the clock the check reads (same host here: offset about 0, within the uncertainty plus scheduling)
        self.assertLess(abs(self.get("/_test/clock")["t_ns"] / 1e9 - time.time()), 1.0)
        rig = K.Rig({"device_uuid": "d", "device_admin_url": self.admin, "plant_url": self.plant.url, "clock_offset_ms": 120.0,
                     "clock_uncertainty_ms": 5.0}, None)
        self.assertEqual((rig.off_ms, rig.u_ms), (120.0, 5.0))  # the driver's values until clock() replaces them
        c = rig.clock(10)
        self.assertLess(abs(c["offset_ms"]), c["uncertainty_ms"] + 5.0)
        self.assertLess(c["uncertainty_ms"], 50.0)
        self.assertIsNone(K.Rig({"device_uuid": "d", "device_admin_url": "http://127.0.0.1:1", "plant_url": self.plant.url}, None).clock(2))


def _seq(d, i, seen, eff, n=1, final="SUCCESSFUL"):
    return {"check": "SEQ", "direction": d, "trial": i, "valid": True, "final": final, "applied_seen_ms": seen,
            "applied_after_effect": seen is not None and eff is not None and n == 1 and seen > eff,
            "margin_ms": (seen - eff) if seen is not None and eff is not None else None}


def _held(check, d, i, applied=False, effect=False, exercised=True):
    return {"check": check, "direction": d, "trial": i, "valid": True, "exercised": exercised, "applied_while_held": applied,
            "effect_while_held": effect, "delivered_while_held": check == "ACK"}


def _ttl(k, i, ok=True):
    return {"check": "TTL", "kind": k, "trial": i, "valid": True, "expiration_ok": ok, "effect_ok": True}


class RulesTest(unittest.TestCase):
    P = {"seq": 3, "cut": 2, "ack": 2, "ttl": 2}

    def base(self):
        t = [_seq("oneway", i, 40.0, 30.0) for i in range(3)] + [_seq("twoway", i, 50.0, 20.0) for i in range(3)]
        t += [_held("CUT", d, i) for d in ("oneway", "twoway") for i in range(2)]
        t += [_held("ACK", "oneway", i, applied=True) for i in range(2)] + [_held("ACK", "twoway", i) for i in range(2)]
        t += [_ttl(k, i) for k in ("past", "within", "none") for i in range(2)]
        return t

    def test_b6_rules(self):
        r = K.rules(self.base(), self.P)
        self.assertEqual({k: v["verdict"] for k, v in r.items()},
                         {"TB-1": "MET", "TB-2": "ABSENT", "TB-3a": "DETECTED", "TB-3b": "ABSENT", "TB-4": "MET"})
        t = self.base()
        t[0] = _seq("oneway", 0, 25.0, 30.0)   # APPLIED seen before the plant effect
        t[7] = _held("CUT", "oneway", 1, effect=True)
        t[-1] = _ttl("none", 1, ok=False)
        r = K.rules(t, self.P)
        self.assertEqual((r["TB-1"]["verdict"], r["TB-2"]["verdict"], r["TB-4"]["verdict"]), ("NOT MET", "DETECTED", "NOT MET"))
        t = self.base()
        for i in range(3):
            t[i] = _seq("oneway", i, None, 30.0, final="DELIVERED")
        self.assertEqual(K.rules(t, self.P)["TB-1"]["verdict"], "NOT REACHED")
        t = self.base()
        t[6:10] = [_held("CUT", d, i, exercised=False) for d in ("oneway", "twoway") for i in range(2)]
        self.assertEqual(K.rules(t, self.P)["TB-2"]["verdict"], "NOT INTERPRETABLE")
        self.assertEqual(K.rules(self.base()[3:], self.P)["TB-1"]["verdict"], "NOT INTERPRETABLE")


if __name__ == "__main__":
    unittest.main()
