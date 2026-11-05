"""Release 086 (PREREG_extra_086.md QB): the reference MQTT device at QoS 2 (reference/mqtt_device_qos2.py) and the
QoS 2 probe adapter (evicon_probe/adapters/mqtt5_qos2.py) against a real Mosquitto (skipped without the mosquitto
binary or paho).

Q1 the device subscribes cmd and query at QoS 2: a request published at QoS 2 arrives at QoS 2, is executed once and
   answered at QoS 2 with the request's correlation data; the frozen device's handler and plant report are used
Q2 the adapter: requests at QoS 2, its response subscription at QoS 2, operation name of its own (a new contract-map
   row), send -> APPLIED with one effect; a second send of the same identity is a new PUBLISH (QoS 2 does not
   deduplicate across publishes): the frozen device without --app-dedup executes it again
Q3 the measure stack: the driver's mosquitto-qos2 stack starts this device and uses this config
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
from argparse import Namespace
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "reference"))
HAVE = shutil.which("mosquitto") is not None
try:
    import paho.mqtt.client as mqtt
    from paho.mqtt.packettypes import PacketTypes
    from paho.mqtt.properties import Properties
except ImportError:  # pragma: no cover
    HAVE = False

from tests.test_tbcheck_device import FakePlant, free_port  # noqa: E402


@unittest.skipUnless(HAVE, "mosquitto binary or paho-mqtt missing")
class Qos2Test(unittest.TestCase):
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
        import mqtt_device_qos2 as D
        self.D = D
        a = Namespace(broker=f"127.0.0.1:{self.bport}", device="q2-1", prefix="evicon", plant=self.plant.url, plant_unix="", app_dedup=False,
                      session_expiry=3600, admin_port=0, admin_bind="127.0.0.1", mode="evicon", token="")
        self.dev = D.Qos2Device(a)
        self.seen_qos = []
        handler = self.dev.on_message

        def record(client, userdata, msg):
            self.seen_qos.append(msg.qos)
            handler(client, userdata, msg)
        self.dev.c.on_message = record
        self.dev.connect()
        time.sleep(0.5)

    def tearDown(self):
        self.dev.disconnect()
        self.broker.terminate()
        self.broker.wait(5)
        self.plant.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_q1_device_at_qos2(self):
        got, ready = [], threading.Event()
        cli = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="probe-q1", protocol=mqtt.MQTTv5)
        cli.on_connect = lambda c, *a, **k: c.subscribe("evicon/q2-1/resp/x", qos=2)
        cli.on_subscribe = lambda *a, **k: ready.set()
        cli.on_message = lambda c, u, m: got.append((m.qos, m.properties.CorrelationData, json.loads(m.payload)))
        cli.connect("127.0.0.1", self.bport)
        cli.loop_start()
        try:
            self.assertTrue(ready.wait(5))
            p = Properties(PacketTypes.PUBLISH)
            p.ResponseTopic, p.CorrelationData = "evicon/q2-1/resp/x", b"c1"
            cli.publish("evicon/q2-1/cmd", json.dumps({"cmd": "c1", "value": 2}), qos=2, properties=p)
            for _ in range(50):
                if got:
                    break
                time.sleep(0.1)
            self.assertEqual(got, [(2, b"c1", {"outcome": "APPLIED"})])
            self.assertEqual(self.seen_qos, [2], "the device's subscription delivers the request at QoS 2")
            self.assertEqual(len(self.plant.effects.get("c1", [])), 1)
        finally:
            cli.loop_stop()
            cli.disconnect()

    def test_q2_adapter(self):
        from evicon_probe import adapters
        cls = adapters.load("mqtt5_rpc_qos2")
        self.assertEqual(cls.operation, "request/response PUBLISH at QoS 2 (correlation data)")
        cfg = json.loads((HERE / "configs" / "mosquitto-rpc-qos2.json").read_text())["adapter_config"]
        self.assertEqual(cfg["qos"], 2)
        cfg = dict(cfg, broker=f"127.0.0.1:{self.bport}", device_id="q2-1", plant_url=self.plant.url, device_admin_url="")
        ad = cls(cfg)
        ad.setup()
        try:
            obs = ad.send("p1", timeout_s=3.0)
            self.assertEqual((obs.status, obs.outcome), ("OK", "APPLIED"))
            obs2 = ad.send("p1", timeout_s=3.0)  # the same identity again: a new PUBLISH
            self.assertEqual(obs2.outcome, "APPLIED")
            self.assertEqual(len(self.plant.effects.get("p1", [])), 2, "QoS 2 does not deduplicate a resend of the same identity")
            self.assertEqual(self.seen_qos, [2, 2])
        finally:
            ad.teardown()
        with self.assertRaises(ValueError):
            cls(dict(cfg, qos=1))

    def test_q3_driver_stack(self):
        sys.path.insert(0, str(HERE.parent))
        import evicon_review as er
        s = er.STACKS["mosquitto-qos2"]
        self.assertEqual((s["configs"], s["owner"], s["device_script"]), (["mosquitto-rpc-qos2"], "mosquitto", "mqtt_device_qos2.py"))
        self.assertEqual(er.STACK_PROCS["mosquitto-qos2"], er.STACK_PROCS["mosquitto"])


if __name__ == "__main__":
    unittest.main()
