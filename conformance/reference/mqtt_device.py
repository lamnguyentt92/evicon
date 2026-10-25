"""Reference MQTT 5 actuator device (paho-mqtt) reporting effects to evicon-plant.

Two modes:
  * evicon (default): subscribes <prefix>/<device>/cmd (QoS 1, persistent
    session); executes {"cmd","value"}; replies on the request's Response Topic
    with the request's Correlation Data. Optional <prefix>/<device>/query.
  * thingsboard: connects to ThingsBoard's MQTT device API with an access
    token, executes RPC "actuate" requests from v1/devices/me/rpc/request/+.

Admin (test-only): POST /_test/online?v=false disconnects the device (the
broker keeps its persistent session and queues QoS>=1 messages), v=true
reconnects. GET /healthz on the admin port names the role (mqtt-device), the
release and the pid (release 065: the reviewer driver checks them).

  python3 mqtt_device.py --broker 192.168.107.152:1883 --device pump-1 --plant http://127.0.0.1:18090
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))  # conformance/: evicon_probe, reference

import paho.mqtt.client as mqtt  # noqa: E402
from paho.mqtt.packettypes import PacketTypes  # noqa: E402
from paho.mqtt.properties import Properties  # noqa: E402

from evicon_probe.plant import PlantClient  # noqa: E402
from reference._health import health_body  # noqa: E402


class Device:
    def __init__(self, a):
        self.a = a
        self.plant = PlantClient(url=a.plant, unix=a.plant_unix)
        self.done: set = set()
        self.lock = threading.Lock()
        host, port = a.broker.rsplit(":", 1)
        self.broker = (host, int(port))
        cid = f"evicon-device-{a.device}"
        self.c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cid, protocol=mqtt.MQTTv5)
        if a.mode == "thingsboard":
            # the device access token is a credential: from TB_DEVICE_TOKEN (release 068), not argv
            token = a.token or os.environ.get("TB_DEVICE_TOKEN", "")
            if not token:
                raise SystemExit("thingsboard mode needs the device access token in TB_DEVICE_TOKEN")
            self.c.username_pw_set(token)
        self.c.on_connect = self.on_connect
        self.c.on_message = self.on_message
        self.online = False

    def connect(self):
        props = Properties(PacketTypes.CONNECT)
        props.SessionExpiryInterval = self.a.session_expiry
        self.c.connect(self.broker[0], self.broker[1], keepalive=10, clean_start=False, properties=props)
        self.c.loop_start()
        self.online = True

    def disconnect(self):
        self.online = False
        self.c.disconnect()
        self.c.loop_stop()

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if self.a.mode == "thingsboard":
            client.subscribe("v1/devices/me/rpc/request/+", qos=1)
        else:
            client.subscribe(f"{self.a.prefix}/{self.a.device}/cmd", qos=1)
            client.subscribe(f"{self.a.prefix}/{self.a.device}/query", qos=1)

    def execute(self, cmd: str, value: int) -> dict:
        with self.lock:
            if self.a.app_dedup and cmd in self.done:
                return {"outcome": "APPLIED", "replay": True}
            self.plant.apply(self.a.device, cmd, value)
            self.done.add(cmd)
        return {"outcome": "APPLIED"}

    def on_message(self, client, userdata, msg):
        try:
            body = json.loads(msg.payload or b"{}")
        except ValueError:
            return
        if self.a.mode == "thingsboard":
            req_id = msg.topic.rsplit("/", 1)[1]
            params = body.get("params") or {}
            out = self.execute(str(params.get("cmd")), int(params.get("value", 1))) if body.get("method") == "actuate" else {"error": "unknown method"}
            client.publish(f"v1/devices/me/rpc/response/{req_id}", json.dumps(out), qos=1)
            return
        rt = getattr(msg.properties, "ResponseTopic", None)
        corr = getattr(msg.properties, "CorrelationData", None)
        if msg.topic.endswith("/query"):
            out = {"outcome": "APPLIED" if body.get("cmd") in self.done else "NOT_FOUND"}
        else:
            out = self.execute(str(body.get("cmd")), int(body.get("value", 1)))
        if rt:
            props = Properties(PacketTypes.PUBLISH)
            if corr is not None:
                props.CorrelationData = corr
            client.publish(rt, json.dumps(out), qos=1, properties=props)


def admin(dev: Device, port: int, bind: str = "0.0.0.0"):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if urlparse(self.path).path != "/healthz":
                self.send_error(404)
                return
            data = health_body("mqtt-device", lambda: {"device": dev.a.device, "online": dev.online,
                                                       "connected": dev.c.is_connected(), "broker": dev.a.broker})
            # 503 until the device is connected to the broker (the driver waits for it)
            self.send_response(200 if dev.c.is_connected() else 503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            u = urlparse(self.path)
            if u.path != "/_test/online":
                self.send_error(404)
                return
            v = parse_qs(u.query).get("v", [""])[0]
            if v == "false":
                dev.disconnect()
            elif v == "true":
                dev.connect()
            else:
                self.send_error(400)
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        def log_message(self, *a):
            pass

    ThreadingHTTPServer((bind, port), H).serve_forever()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--broker", required=True)
    ap.add_argument("--device", default="mqtt-1")
    ap.add_argument("--prefix", default="evicon")
    ap.add_argument("--mode", choices=["evicon", "thingsboard"], default="evicon")
    ap.add_argument("--token", default="", help="ThingsBoard device access token (prefer the TB_DEVICE_TOKEN environment variable)")
    ap.add_argument("--plant", default="http://127.0.0.1:18090")
    ap.add_argument("--plant-unix", default="")
    ap.add_argument("--app-dedup", action="store_true")
    ap.add_argument("--session-expiry", type=int, default=3600)
    ap.add_argument("--admin-port", type=int, default=0)
    ap.add_argument("--admin-bind", default="0.0.0.0")
    a = ap.parse_args()
    d = Device(a)
    d.connect()
    print(f"mqtt_device {a.device} mode={a.mode} broker={a.broker}", flush=True)
    if a.admin_port:
        admin(d, a.admin_port, a.admin_bind)
    else:
        threading.Event().wait()
