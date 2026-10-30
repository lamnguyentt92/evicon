"""Release 077 extension of the MQTT owner gateway: the delivery binding of command messages and the
"cut" fault, against a real Mosquitto (skipped when the mosquitto binary or paho is missing).

Q1 the binding rule: auto is QoS 0 for a contract without D_h, QoS 1 with D_h; qos1/qos0 force it
Q2 the packet parser of the cut link (PUBLISH topic, packet split across reads)
Q3 plain firmware, QoS 1, a cut while the command is in flight: the client library re-sends the command
   after the reconnect and the firmware executes it twice (a duplicate the runtime never sent)
Q4 plain firmware, binding auto (QoS 0), the same cut: the command is executed at most once
Q5 qos_dup_check reproduces Q3/Q4 without the gateway
Q6 the crash fault is armed 100 ms after the arming request (its answer leaves first); disarming cancels a
   pending arming
"""
import argparse
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from reference import mqtt_owner_gateway as G
from reference import mqtt_owner_gateway_ext077 as X7

DEV = "pump0-1"
HAVE_BROKER = shutil.which("mosquitto") is not None
try:
    import paho.mqtt.client as mqtt  # noqa: F401
except ImportError:  # pragma: no cover
    HAVE_BROKER = False


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Broker:
    def __init__(self, tmp: Path):
        self.port = free_port()
        conf = tmp / "m.conf"
        conf.write_text(f"listener {self.port} 127.0.0.1\nallow_anonymous true\npersistence false\n")
        self.p = subprocess.Popen(["mosquitto", "-c", str(conf)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=0.2).close()
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("mosquitto did not start")

    def stop(self):
        self.p.terminate()
        self.p.wait(5)


class PlainFirmware:
    """An MQTT command handler without an EviCon core (the firmware's plain mode): persistent session,
    QoS 1 subscription, executes every delivered command and answers APPLIED."""

    def __init__(self, port: int, ns: str):
        import paho.mqtt.client as mqtt
        self.effects, self.level, self.lock = {}, 0, threading.Lock()
        self.c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{ns}-{DEV}-fw", protocol=mqtt.MQTTv311, clean_session=False)
        ready = threading.Event()
        self.c.on_connect = lambda c, *a, **k: c.subscribe([(f"{ns}/{DEV}/cmd", 1), (f"{ns}/{DEV}/ping", 1)])
        self.c.on_subscribe = lambda *a, **k: ready.set()
        self.c.on_message = self._msg
        self.c.connect("127.0.0.1", port, keepalive=30)
        self.c.loop_start()
        if not ready.wait(5):
            raise RuntimeError("firmware stand-in did not subscribe")

    def _msg(self, c, u, m):
        body = json.loads(m.payload)
        if m.topic.endswith("/ping"):
            rep = {"device": DEV, "mode": "plain", "level": self.level}
        else:
            with self.lock:
                cid = body["cmd"]
                self.effects[cid] = self.effects.get(cid, 0) + 1
                self.level += int(body["value"])
                rep = {"cmd": cid, "seq": body["seq"], "outcome": "APPLIED", "reason": "APPLIED", "level": self.level,
                       "owner_version": sum(self.effects.values())}
        c.publish(body["reply"], json.dumps(rep), qos=1)

    def close(self):
        self.c.loop_stop()
        self.c.disconnect()


def plain_contract(tmp: Path) -> str:
    p = tmp / "plain.json"
    p.write_text(json.dumps({"id": "zephyr-mqtt-plain", "source": "MEASURED", "capabilities": [], "level": "K0",
                             "fault_model": "network loss/delay, client abandon; owner up (no owner crash or reboot)"}))
    return str(p)


def gw_args(tmp: Path, port: int, ns: str, binding: str) -> argparse.Namespace:
    return argparse.Namespace(mode="PLAIN", device_id=DEV, ns=ns, generation=1, epoch=1, listen="", admin_listen="",
                              broker=f"127.0.0.1:{port}", contract_file=plain_contract(tmp), state=str(tmp / f"{ns}.state.jsonl"),
                              budget_s=0.6, qos_cmd=1, keepalive_s=30, offline_mode="", log=str(tmp / f"{ns}.jsonl"),
                              binding=binding, cut=True)


def effect_req(cid: str, seq: int) -> dict:
    return {"schema_version": 1, "domain_id": DEV, "generation": 1, "command_id": cid, "operation": "ADD", "value": [1], "sequence": seq}


class BindingRule(unittest.TestCase):
    def test_q1_binding(self):
        self.assertEqual(X7.binding_qos("auto", []), 0)
        self.assertEqual(X7.binding_qos("auto", ["Q_A", "F"]), 0)
        self.assertEqual(X7.binding_qos("auto", ["D_h", "Q_A", "F"]), 1)
        self.assertEqual(X7.binding_qos("qos1", []), 1)
        self.assertEqual(X7.binding_qos("qos0", ["D_h"]), 0)

    def test_q2_parser(self):
        topic = b"ns/dev/cmd"
        payload = b'{"cmd":"c1"}'
        var = len(topic).to_bytes(2, "big") + topic + (7).to_bytes(2, "big") + payload
        pkt = bytes([0x32, len(var)]) + var  # PUBLISH, QoS 1
        puback = bytes([0x40, 2, 0, 7])
        pkts, rest = X7._split(pkt + puback[:2])
        self.assertEqual((pkts, rest), ([pkt], puback[:2]))
        pkts, rest = X7._split(rest + puback[2:])
        self.assertEqual((pkts, rest), ([puback], b""))
        self.assertEqual(X7._publish_topic(pkt), "ns/dev/cmd")
        self.assertEqual(X7._publish_topic(puback), "")


class DeferredArm(unittest.TestCase):
    def test_q6_arm_after_answer(self):
        from tests.test_mqtt_owner_gateway import FakeConn, FakeFirmware, FakeLink, contract
        tmp = Path(tempfile.mkdtemp())
        fw, link = FakeFirmware("cutoff"), FakeLink()
        a = argparse.Namespace(mode="CUTOFF", device_id=DEV, ns="t1", generation=1, epoch=1, listen="", admin_listen="", broker="127.0.0.1:1",
                               contract_file=contract("CUTOFF", tmp), state=str(tmp / "gw.state.jsonl"), budget_s=0.3, qos_cmd=1,
                               keepalive_s=60, offline_mode="", log=str(tmp / "gw.jsonl"), binding="", cut=False)
        gw = X7.Ext077Gateway(a, net=lambda g: (link, FakeConn(g, fw, link, "data"), FakeConn(g, fw, link, "ctl")))
        try:
            self.assertEqual(gw.a.qos_cmd, 1)  # auto: the cutoff contract has D_h
            gw.arm("AFTER_EFFECT_BEFORE_RECEIPT")
            self.assertEqual(gw.armed, "")
            time.sleep(X7.ARM_DELAY_S + 0.1)
            self.assertEqual(gw.armed, "AFTER_EFFECT_BEFORE_RECEIPT")
            gw.arm("")
            self.assertEqual(gw.armed, "")
            gw.arm("AFTER_EFFECT_BEFORE_RECEIPT")
            gw.arm("")  # cancelled before it took effect
            time.sleep(X7.ARM_DELAY_S + 0.1)
            self.assertEqual(gw.armed, "")
        finally:
            gw.close()
            shutil.rmtree(tmp, ignore_errors=True)


@unittest.skipUnless(HAVE_BROKER, "needs the mosquitto binary and paho-mqtt")
class CutAgainstBroker(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.broker = Broker(self.tmp)

    def tearDown(self):
        self.broker.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _cut_once(self, binding: str, ns: str) -> dict:
        fw = PlainFirmware(self.broker.port, ns)
        gw = X7.Ext077Gateway(gw_args(self.tmp, self.broker.port, ns, binding))
        try:
            self.assertEqual(gw.effect(effect_req("c1", 1), time.monotonic())["outcome"], "APPLIED")  # link up: one effect
            gw.set_online(False)  # arms the cut: the next command goes through, its PUBACK and reply are lost
            with self.assertRaises(Exception):
                gw.effect(effect_req("c2", 2), time.monotonic())  # 504: no reply in time
            time.sleep(0.5)
            self.assertFalse(gw.link.describe()["up"])
            gw.set_online(True)
            # paho reconnects after 1-2 s and, at QoS 1, re-sends the unacknowledged command. Wait at least 4 s (the
            # at-most-once check needs the full wait); 081: at QoS 1 wait up to 12 s for the re-sent copy, as a loaded
            # host reconnects later (a fixed 4 s missed it once while a report ran on the same machine)
            t0, limit = time.monotonic(), (12.0 if binding == "qos1" else 4.0)
            while True:
                waited = time.monotonic() - t0
                if waited >= limit or (waited >= 4.0 and fw.effects.get("c2", 0) >= 2):
                    break
                time.sleep(0.2)
            return {"qos": gw.a.qos_cmd, "effects": dict(fw.effects), "cuts": gw.link.describe()["cuts"]}
        finally:
            fw.close()
            for m in (gw.data, gw.ctl):
                m.close()
            gw.close()

    def test_q3_qos1_duplicates_below_the_runtime(self):
        r = self._cut_once("qos1", "q3")
        self.assertEqual(r["qos"], 1)
        self.assertEqual(r["cuts"]["on_command"], 1)
        self.assertEqual(r["effects"].get("c1"), 1)
        self.assertEqual(r["effects"].get("c2"), 2, r)  # sent once by the gateway, executed twice

    def test_q4_auto_binding_at_most_once(self):
        r = self._cut_once("auto", "q4")
        self.assertEqual(r["qos"], 0)
        self.assertEqual(r["cuts"]["on_command"], 1)
        self.assertEqual(r["effects"].get("c1"), 1)
        self.assertLessEqual(r["effects"].get("c2", 0), 1, r)

    def test_q5_qos_dup_check(self):
        from reference import qos_dup_check as Q
        res = {b: Q.run_binding(b, ("127.0.0.1", self.broker.port), 20, 4, 4.0, log=lambda *_: None) for b in ("qos1", "qos0")}
        self.assertGreater(res["qos1"]["commands_delivered_twice_or_more"], 0, res["qos1"])
        self.assertEqual(res["qos0"]["commands_delivered_twice_or_more"], 0, res["qos0"])
        self.assertEqual(res["qos1"]["lost_commands"], 0)


if __name__ == "__main__":
    unittest.main()
