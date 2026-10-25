#!/usr/bin/env python3
"""Transport duplicates of MQTT command messages when a reply is lost (release 077).

The C2/C3 gateway (conformance/reference/mqtt_owner_gateway.py) publishes commands with QoS 1 on a
persistent session, as IoT command channels do. QoS 1 is at least once: when the connection is reset
after the broker has taken a PUBLISH but before its PUBACK reaches the publisher, the client library
sends the PUBLISH again after it reconnects (DUP=1) and the broker forwards it again. A firmware without
deduplication (the plain mode) then executes the command twice, although the gateway and the EviCon
runtime sent it once: the duplicate is created below the runtime, so the runtime's retry guard cannot
prevent it. EviCon's contract model states this as the adapter premise DRIVER_NO_DUPLICATE_EFFECT
(internal/contract): an owner without D_h is guarded only on a channel that delivers a dispatch at most
once.

This check reproduces the case on a real broker and measures it per channel binding:

  qos1   the gateway's binding (QoS 1, persistent session, paho re-sends after a reconnect)
  qos0   at most once (QoS 0: no acknowledgement, nothing is re-sent; a command published while the
         link is down is lost, and the runtime sees an ambiguous command, never a duplicate)

For each binding it publishes N commands through a TCP relay (the gateway's link) and, for every k-th
command, swallows the broker's PUBACK and resets the publisher's connection at that instant. A
subscriber in the firmware's configuration (QoS 1 subscription, persistent session, acknowledging after
handling) counts the deliveries of every command id.

    python3 conformance/reference/qos_dup_check.py --broker 127.0.0.1:1883 --out <dir> [-n 200] [--every 5]

Output: <dir>/qos_dup_check.json and QOS_DUP_CHECK.md (published, delivered, commands delivered more
than once, resets injected, per binding).
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
import uuid
from collections import Counter
from pathlib import Path


def _varint_len(buf: bytes, i: int):
    """(remaining length, bytes used) of the MQTT fixed header starting at buf[i] (the byte after the type),
    or None if incomplete."""
    mult, val, n = 1, 0, 0
    while True:
        if i + n >= len(buf):
            return None
        b = buf[i + n]
        val += (b & 0x7F) * mult
        n += 1
        if b & 0x80 == 0:
            return val, n
        mult *= 128
        if n > 4:
            raise ValueError("malformed remaining length")


class PubackCutRelay:
    """TCP relay client <-> broker. When armed, the next PUBACK from the broker is not forwarded and both
    sides of the connection are reset (the reply is lost with the connection, as at the start of the
    gateway's offline window)."""

    def __init__(self, broker: tuple):
        self.broker = broker
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(8)
        self.port = self.srv.getsockname()[1]
        self.armed = threading.Event()
        self.cuts = 0
        self.closed = False
        self.pairs = []
        self.lock = threading.Lock()
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while not self.closed:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            try:
                u = socket.create_connection(self.broker, timeout=5)
            except OSError:
                c.close()
                continue
            u.settimeout(None)
            pair = [c, u]
            with self.lock:
                self.pairs = [p for p in self.pairs if p[0].fileno() >= 0] + [pair]
            threading.Thread(target=self._up, args=(c, u, pair), daemon=True).start()
            threading.Thread(target=self._down, args=(u, c, pair), daemon=True).start()

    @staticmethod
    def _reset(pair):
        for s in pair:
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")
            except OSError:
                pass
            try:
                s.close()
            except OSError:
                pass

    def _up(self, a, b, pair):
        try:
            while True:
                d = a.recv(65536)
                if not d:
                    break
                b.sendall(d)
        except OSError:
            pass
        self._reset(pair)

    def _down(self, a, b, pair):
        buf = b""
        try:
            while True:
                d = a.recv(65536)
                if not d:
                    break
                buf += d
                out = b""
                while buf:
                    r = _varint_len(buf, 1)
                    if r is None:
                        break
                    ln, n = r
                    total = 1 + n + ln
                    if len(buf) < total:
                        break
                    pkt, buf = buf[:total], buf[total:]
                    if pkt[0] >> 4 == 4 and self.armed.is_set():  # PUBACK
                        self.armed.clear()
                        self.cuts += 1
                        if out:
                            b.sendall(out)
                        self._reset(pair)
                        return
                    out += pkt
                if out:
                    b.sendall(out)
        except OSError:
            pass
        self._reset(pair)

    def cut_now(self):
        with self.lock:
            pairs, self.pairs = self.pairs, []
        for p in pairs:
            self._reset(p)
        self.cuts += 1

    def close(self):
        self.closed = True
        try:
            self.srv.close()
        except OSError:
            pass


def run_binding(binding: str, broker: tuple, n: int, every: int, settle_s: float, log=print) -> dict:
    import paho.mqtt.client as mqtt

    ns = f"qd-{binding}-{uuid.uuid4().hex[:8]}"
    topic = f"{ns}/dev/cmd"
    deliveries, dup_flags = Counter(), Counter()
    sub_ready = threading.Event()

    def on_msg(c, u, m):
        try:
            cid = json.loads(m.payload)["cmd"]
        except (ValueError, KeyError):
            return
        deliveries[cid] += 1
        if m.dup:
            dup_flags[cid] += 1

    # the firmware's configuration: persistent session, QoS 1 subscription, acknowledged after handling
    sub = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{ns}-fw", protocol=mqtt.MQTTv311, clean_session=False)
    sub.on_message = on_msg
    sub.on_subscribe = lambda *a, **k: sub_ready.set()
    sub.on_connect = lambda c, *a, **k: c.subscribe(topic, qos=1)
    sub.connect(*broker, keepalive=30)
    sub.loop_start()
    if not sub_ready.wait(10):
        raise RuntimeError("subscriber did not subscribe")

    relay = PubackCutRelay(broker)
    qos = 1 if binding == "qos1" else 0
    # the gateway's configuration: persistent session through its link, reconnect after 1-2 s
    pub = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{ns}-gw", protocol=mqtt.MQTTv311, clean_session=False)
    pub.reconnect_delay_set(min_delay=1, max_delay=2)
    connected = threading.Event()
    pub.on_connect = lambda *a, **k: connected.set()
    pub.on_disconnect = lambda *a, **k: connected.clear()
    pub.connect("127.0.0.1", relay.port, keepalive=30)
    pub.loop_start()
    if not connected.wait(10):
        raise RuntimeError("publisher did not connect")
    published, not_sent, cuts_wanted = 0, 0, 0
    for i in range(n):
        if not connected.wait(15):
            raise RuntimeError("publisher did not reconnect")
        cut = every > 0 and i % every == every - 1
        if cut:
            if qos == 1:
                relay.armed.set()
            cuts_wanted += 1
        info = pub.publish(topic, json.dumps({"cmd": f"c{i}", "value": 1}), qos=qos)
        if info.rc == mqtt.MQTT_ERR_SUCCESS:
            published += 1
        else:
            not_sent += 1
        if cut and qos == 0:
            # QoS 0 has no PUBACK: reset the link at the same instant, right after the publish
            time.sleep(0.001)
            relay.cut_now()
        time.sleep(0.02)
    deadline = time.time() + settle_s
    while time.time() < deadline:
        time.sleep(0.2)
    pub.loop_stop()
    pub.disconnect()
    sub.loop_stop()
    sub.disconnect()
    relay.close()
    sent = [f"c{i}" for i in range(n)]
    dup = {c: deliveries[c] for c in sent if deliveries[c] > 1}
    res = {"binding": binding, "qos": qos, "commands": n, "published_ok": published, "publish_refused": not_sent,
           "resets": relay.cuts, "delivered_commands": sum(1 for c in sent if deliveries[c] > 0),
           "lost_commands": sum(1 for c in sent if deliveries[c] == 0), "deliveries": sum(deliveries[c] for c in sent),
           "commands_delivered_twice_or_more": len(dup), "extra_deliveries": sum(v - 1 for v in dup.values()),
           "duplicates_with_dup_flag": sum(1 for c in dup if dup_flags[c] > 0), "examples": dict(list(dup.items())[:5])}
    log(f"  {binding}: {json.dumps({k: v for k, v in res.items() if k != 'examples'})}")
    return res


def markdown(res: list, broker: str) -> str:
    L = ["# MQTT command duplicates when a reply is lost (release 077)", "",
         f"Broker `{broker}`. Every k-th command: the broker's PUBACK is swallowed and the publisher's connection reset "
         "at that instant; the publisher reconnects with its persistent session (as the C2/C3 gateway). A subscriber "
         "in the firmware's configuration counts deliveries per command id.", "",
         "| binding | commands | resets | delivered | lost | delivered twice or more | extra deliveries |",
         "|---|---|---|---|---|---|---|"]
    for r in res:
        L.append(f"| {r['binding']} | {r['commands']} | {r['resets']} | {r['delivered_commands']} | {r['lost_commands']} | "
                 f"{r['commands_delivered_twice_or_more']} | {r['extra_deliveries']} |")
    L += ["", "QoS 1 duplicates are created by the client library and the broker, below the runtime: on an owner "
          "without D_h (the plain firmware) each is a duplicate effect. QoS 0 delivers at most once: a command "
          "caught by a reset is lost (the runtime holds it as ambiguous), never duplicated."]
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--broker", default="127.0.0.1:1883")
    ap.add_argument("--out", required=True)
    ap.add_argument("-n", type=int, default=200)
    ap.add_argument("--every", type=int, default=5)
    ap.add_argument("--settle-s", type=float, default=8.0)
    ap.add_argument("--bindings", default="qos1,qos0")
    a = ap.parse_args(argv)
    host, _, port = a.broker.rpartition(":")
    broker = (host, int(port))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    res = [run_binding(b, broker, a.n, a.every, a.settle_s) for b in a.bindings.split(",")]
    (out / "qos_dup_check.json").write_text(json.dumps({"broker": a.broker, "results": res}, indent=2) + "\n")
    (out / "QOS_DUP_CHECK.md").write_text(markdown(res, a.broker))
    return 0


if __name__ == "__main__":
    sys.exit(main())
