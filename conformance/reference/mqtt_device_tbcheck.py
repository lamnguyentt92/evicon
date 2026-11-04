"""Reference MQTT device for the ThingsBoard RPC checks (release 085, review M3a): reference/mqtt_device.py in
ThingsBoard mode, imported unchanged (it is frozen by the 077-084 plans), with two test-only admin switches, both off
by default:

  POST /_test/hold?v=true|false     true: the NEXT RPC handler blocks after the request was received and before the
                                    plant write, until v=false releases it or --hold-timeout-s passes; false releases
                                    a held handler (and disarms a hold that no RPC has taken yet)
  POST /_test/ackmode?v=after|before  after (default): the MQTT acknowledgement (PUBACK) of the RPC request is sent
                                    after the handler has returned, i.e. after the plant write and the reply, as
                                    paho-mqtt does by default and as reference/mqtt_device.py does; before: a
                                    negative-control device that acknowledges the request before the plant write
  GET  /_test/state                 the modes, whether a handler is held now (and its params.cmd), the counts
  GET  /_test/events?cmd=<cmd>      this device's log lines for one command (the check reads them per trial)
  GET  /_test/clock                 this host's wall clock (time.time_ns()), taken in the handler: the check measures
                                    the clock offset VM-E - VM-C over HTTP (the plant runs on the same host)
  POST /_test/online?v=false|true   as reference/mqtt_device.py
  GET  /healthz                     as reference/mqtt_device.py (role mqtt-device)

With both switches at their defaults the device runs reference/mqtt_device.py's own handler (paho's automatic
acknowledgement after the handler returns), byte for byte the behaviour of the TB runs. While a switch is on, the
handler runs on a worker thread with paho's manual acknowledgement, so that the MQTT keep-alive goes on while a
handler is held; in ackmode "after" the PUBACK then follows the plant write and the reply, as without the switch.
The device keeps subscribing to v1/devices/me/rpc/request/+ at QoS 1. Every step is a JSON line in --check-log
(wall time and monotonic time): rpc_received (request id, mid, qos, dup flag, method, params.cmd), ack_sent,
hold_start, hold_end (released | timeout), effect_done (plant write returned), reply_sent, mode changes.

  TB_DEVICE_TOKEN=... python3 mqtt_device_tbcheck.py --broker 192.168.107.152:1885 --device tb-1 \\
      --plant http://192.168.107.154:18090 --admin-port 18202 --check-log tbcheck-device.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))  # conformance/: evicon_probe, reference

from reference import mqtt_device as MD  # noqa: E402  (frozen; imported, never edited)
from reference._health import health_body  # noqa: E402

ACKMODES = ("after", "before")


class CheckDevice(MD.Device):
    MAX_EVENTS = 20000

    def __init__(self, a):
        super().__init__(a)
        self.mode_lock = threading.Lock()
        self.hold_armed = False
        self.ackmode = "after"
        self.release_ev = threading.Event()
        self.held_now = ""          # request id of the handler that is held now
        self.pending_manual = 0     # handlers on the manual-acknowledgement path that have not acknowledged yet
        self.counts = {"rpc": 0, "held": 0, "hold_timeouts": 0, "acks_before": 0, "dup": 0}
        self.events: list = []
        self.held_cmd = ""
        self.log_fh = open(a.check_log, "a", buffering=1) if a.check_log else None
        self.log("start", device=a.device, mode=a.mode, hold_timeout_s=a.hold_timeout_s)

    # ---- log -------------------------------------------------------------------
    def log(self, ev, **kv):
        kv.update(ev=ev, t=round(time.time(), 6), mono=round(time.monotonic(), 6))
        with self.lock:
            self.events.append(kv)
            del self.events[:-self.MAX_EVENTS]
            if self.log_fh is not None:
                self.log_fh.write(json.dumps(kv, sort_keys=True) + "\n")

    def events_of(self, cmd: str) -> list:
        with self.lock:
            return [dict(e) for e in self.events if e.get("cmd") == cmd or (cmd == "" and "cmd" not in e)]

    # ---- switches --------------------------------------------------------------
    def _manual_needed(self) -> bool:
        return self.hold_armed or self.ackmode == "before" or self.pending_manual > 0

    def _update_manual(self):
        self.c.manual_ack_set(self._manual_needed())

    def set_hold(self, on: bool):
        with self.mode_lock:
            if on:
                self.release_ev.clear()
                self.hold_armed = True
            else:
                self.hold_armed = False
                self.release_ev.set()
            self._update_manual()
        self.log("mode", hold=on)

    def set_ackmode(self, mode: str):
        with self.mode_lock:
            self.ackmode = mode
            self._update_manual()
        self.log("mode", ackmode=mode)

    def state(self) -> dict:
        with self.mode_lock:
            return {"hold_armed": self.hold_armed, "held_now": self.held_now, "held_cmd": self.held_cmd, "ackmode": self.ackmode,
                    "manual_ack": self._manual_needed(), "counts": dict(self.counts), "online": self.online,
                    "connected": self.c.is_connected()}

    # ---- the RPC handler ---------------------------------------------------------
    def on_message(self, client, userdata, msg):
        if self.a.mode != "thingsboard":
            return super().on_message(client, userdata, msg)
        req_id = msg.topic.rsplit("/", 1)[-1]
        try:
            body = json.loads(msg.payload or b"{}")
        except ValueError:
            body = {}
        params = body.get("params") if isinstance(body, dict) else {}
        cmd = str((params or {}).get("cmd"))
        with self.mode_lock:
            self.counts["rpc"] += 1
            self.counts["dup"] += bool(msg.dup)
            manual = self._manual_needed()
            hold = self.hold_armed
            ackmode = self.ackmode
            if hold:
                self.hold_armed = False  # the NEXT handler only
            if manual:
                self.pending_manual += 1
        self.log("rpc_received", req=req_id, mid=msg.mid, qos=msg.qos, dup=bool(msg.dup), method=body.get("method") if isinstance(body, dict) else None,
                 cmd=cmd, path="manual" if manual else "default")
        if not manual:
            # both switches at their defaults: the frozen handler (plant write, reply), then paho's own PUBACK
            super().on_message(client, userdata, msg)
            self.log("effect_done", req=req_id, cmd=cmd, note="default path: plant write and reply in mqtt_device.Device.on_message")
            with self.mode_lock:
                switched = self._manual_needed()  # a switch was turned on while this handler ran: paho will not acknowledge
            if switched:
                client.ack(msg.mid, msg.qos)
                self.log("ack_sent", req=req_id, cmd=cmd, mid=msg.mid, when="after the handler returned (switch turned on meanwhile)")
            else:
                self.log("ack_after_return", req=req_id, cmd=cmd, mid=msg.mid, note="paho sends the PUBACK when on_message returns")
            return
        if ackmode == "before":
            client.ack(msg.mid, msg.qos)
            with self.mode_lock:
                self.counts["acks_before"] += 1
            self.log("ack_sent", req=req_id, cmd=cmd, mid=msg.mid, when="before the plant write")
        threading.Thread(target=self._work, args=(client, msg, req_id, body, cmd, hold, ackmode), daemon=True).start()

    def _work(self, client, msg, req_id, body, cmd, hold, ackmode):
        try:
            if hold:
                with self.mode_lock:
                    self.held_now, self.held_cmd = req_id, cmd
                    self.counts["held"] += 1
                self.log("hold_start", req=req_id, cmd=cmd)
                released = self.release_ev.wait(self.a.hold_timeout_s)
                with self.mode_lock:
                    self.held_now, self.held_cmd = "", ""
                    if not released:
                        self.counts["hold_timeouts"] += 1
                self.log("hold_end", req=req_id, cmd=cmd, how="released" if released else "timeout")
            params = (body.get("params") or {}) if isinstance(body, dict) else {}
            if isinstance(body, dict) and body.get("method") == "actuate":
                out = self.execute(cmd, int(params.get("value", 1)))
            else:
                out = {"error": "unknown method"}
            self.log("effect_done", req=req_id, cmd=cmd, outcome=out.get("outcome"))
            client.publish(f"v1/devices/me/rpc/response/{req_id}", json.dumps(out), qos=1)
            self.log("reply_sent", req=req_id, cmd=cmd)
        except Exception as e:  # noqa: BLE001 - the check reads the log
            self.log("handler_error", req=req_id, cmd=cmd, error=f"{type(e).__name__}: {e}")
        finally:
            if ackmode == "after":
                client.ack(msg.mid, msg.qos)
                self.log("ack_sent", req=req_id, cmd=cmd, mid=msg.mid, when="after the handler returned")
            with self.mode_lock:
                self.pending_manual -= 1
                self._update_manual()


def admin(dev: CheckDevice, port: int, bind: str = "0.0.0.0"):
    class H(BaseHTTPRequestHandler):
        def _send(self, code, obj):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/healthz":
                data = health_body("mqtt-device", lambda: {"device": dev.a.device, "online": dev.online, "connected": dev.c.is_connected(),
                                                           "broker": dev.a.broker, "tbcheck": True})
                self.send_response(200 if dev.c.is_connected() else 503)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if u.path == "/_test/state":
                return self._send(200, dev.state())
            if u.path == "/_test/events":
                return self._send(200, {"events": dev.events_of(parse_qs(u.query).get("cmd", [""])[0])})
            if u.path == "/_test/clock":
                return self._send(200, {"t_ns": time.time_ns()})
            self.send_error(404)

        def do_POST(self):
            u = urlparse(self.path)
            v = parse_qs(u.query).get("v", [""])[0]
            if u.path == "/_test/online":
                if v == "false":
                    dev.disconnect()
                elif v == "true":
                    dev.connect()
                else:
                    return self._send(400, {"error": "v must be true or false"})
                return self._send(200, {"ok": True})
            if u.path == "/_test/hold":
                if v not in ("true", "false"):
                    return self._send(400, {"error": "v must be true or false"})
                dev.set_hold(v == "true")
                return self._send(200, dev.state())
            if u.path == "/_test/ackmode":
                if v not in ACKMODES:
                    return self._send(400, {"error": f"v must be one of {ACKMODES}"})
                dev.set_ackmode(v)
                return self._send(200, dev.state())
            self.send_error(404)

        def log_message(self, *a):
            pass

    ThreadingHTTPServer((bind, port), H).serve_forever()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--broker", required=True)
    ap.add_argument("--device", default="tb-1")
    ap.add_argument("--prefix", default="evicon")
    ap.add_argument("--mode", choices=["evicon", "thingsboard"], default="thingsboard")
    ap.add_argument("--token", default="", help="ThingsBoard device access token (prefer the TB_DEVICE_TOKEN environment variable)")
    ap.add_argument("--plant", default="http://127.0.0.1:18090")
    ap.add_argument("--plant-unix", default="")
    ap.add_argument("--app-dedup", action="store_true")
    ap.add_argument("--session-expiry", type=int, default=3600)
    ap.add_argument("--admin-port", type=int, default=0)
    ap.add_argument("--admin-bind", default="0.0.0.0")
    ap.add_argument("--hold-timeout-s", type=float, default=60.0, help="a held handler goes on after this long without a release")
    ap.add_argument("--check-log", default="tbcheck-device.jsonl")
    a = ap.parse_args(argv)
    d = CheckDevice(a)
    d.connect()
    print(f"mqtt_device_tbcheck {a.device} mode={a.mode} broker={a.broker}", flush=True)
    if a.admin_port:
        admin(d, a.admin_port, a.admin_bind)
    else:
        threading.Event().wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
