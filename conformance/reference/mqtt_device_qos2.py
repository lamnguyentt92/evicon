"""Reference MQTT 5 actuator device at QoS 2 (release 086, PREREG_extra_086.md rule set QB; reviewer comment M3(2) and
question Q6: what does MQTT QoS 2 certify after a timeout?).

The frozen reference device (reference/mqtt_device.py, unchanged and imported) subscribes its command and query topics at
QoS 1 and replies at QoS 1. This module runs the same device with two differences, and nothing else:
  * it subscribes <prefix>/<device>/cmd and <prefix>/<device>/query at QoS 2 (so the broker delivers a QoS 2 PUBLISH to
    it at QoS 2: PUBLISH, PUBREC, PUBREL, PUBCOMP, with the broker's and paho's packet-identifier state);
  * it publishes its replies at QoS 2.
The session stays persistent (clean_start False, session expiry --session-expiry, default 3600 s), as the frozen
device's; the admin endpoints (/_test/online, /healthz) and the plant reporting are the frozen device's. paho-mqtt 2.x
implements the QoS 2 receiver flow (the message is handed to on_message once per packet identifier and session).

  python3 mqtt_device_qos2.py --broker 192.168.107.152:1883 --device pump-mqtt-1 --plant http://127.0.0.1:18090 \\
      --admin-port 18201
"""
from __future__ import annotations

import os
import sys
import threading

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import mqtt_device as base  # noqa: E402  (frozen; imported, never edited)

QOS = 2


class Qos2Device(base.Device):
    def __init__(self, a):
        super().__init__(a)
        publish = self.c.publish

        def publish_qos2(topic, payload=None, qos=0, retain=False, properties=None):
            return publish(topic, payload, qos=QOS, retain=retain, properties=properties)
        self.c.publish = publish_qos2  # every reply of the frozen handler at QoS 2

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if self.a.mode == "thingsboard":
            raise SystemExit("mqtt_device_qos2.py: evicon mode only")
        client.subscribe(f"{self.a.prefix}/{self.a.device}/cmd", qos=QOS)
        client.subscribe(f"{self.a.prefix}/{self.a.device}/query", qos=QOS)


def main(argv=None):
    ap = base.argparse.ArgumentParser(description="reference MQTT 5 device at QoS 2 (release 086)")
    ap.add_argument("--broker", required=True)
    ap.add_argument("--device", default="mqtt-1")
    ap.add_argument("--prefix", default="evicon")
    ap.add_argument("--plant", default="http://127.0.0.1:18090")
    ap.add_argument("--plant-unix", default="")
    ap.add_argument("--app-dedup", action="store_true")
    ap.add_argument("--session-expiry", type=int, default=3600)
    ap.add_argument("--admin-port", type=int, default=0)
    ap.add_argument("--admin-bind", default="0.0.0.0")
    a = ap.parse_args(argv)
    a.mode, a.token = "evicon", ""
    d = Qos2Device(a)
    d.c.on_connect = d.on_connect
    d.connect()
    print(f"mqtt_device_qos2 {a.device} broker={a.broker} qos={QOS} (subscriptions and replies)", flush=True)
    if a.admin_port:
        base.admin(d, a.admin_port, a.admin_bind)
    else:
        threading.Event().wait()


if __name__ == "__main__":
    main()
