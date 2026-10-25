"""EviCon MQTT owner gateway, release 077: the delivery binding of the command messages (on the 075 gateway).

The C2/C3 gateways publish commands with QoS 1 on a persistent session. QoS 1 is at least once: when the
link is reset after the broker has taken a command but before its PUBACK reached the gateway, the client
library sends the command again after it reconnects, and the broker forwards it again
(conformance/reference/qos_dup_check.py reproduces it). On a firmware without deduplication (the plain
mode, contract without D_h) that second delivery is a second effect, created below the EviCon runtime,
whose retry guard never sent it. EviCon's contract model states the requirement as the adapter premise
DRIVER_NO_DUPLICATE_EFFECT: the adapter must not create effects the runtime did not dispatch.

This gateway chooses the binding from the contract it advertises:

  qos1   commands at QoS 1 (the 074/075 call shape): when the advertised contract has D_h, the firmware's
         deduplication absorbs every redelivery within its horizon
  qos0   commands at QoS 0: at most once (no acknowledgement, nothing is re-sent; a command caught by a
         reset is lost, which the runtime holds as ambiguous and resolves with the contract's evidence)
  auto   (default) qos0 when the advertised contract has no D_h, qos1 otherwise

The binding comes from --binding, else from the file gateway.binding next to --state (the release-077
Zephyr runner writes it per cell), else auto. Queries, stops, fences and pings stay at QoS 1 (they have
no effect at the firmware: a repeated one is answered again).

Fault "cut" (offline mode of the queue channel, release 077): when the link is told to go offline, it
stays up until the gateway next sends a command, lets that command through to the broker, drops every
byte from the broker from then on (the command's PUBACK and any reply are lost), resets the connections
50 ms later and stays down until it is told to go online (or, when no command is sent within 2 s, it
goes down then). The offline window then starts at the worst instant for an at-least-once binding: a
command the broker has taken, unacknowledged. The cut is on with --cut, else when the file gateway.cut
next to --state contains "1".

Arming the crash fault (release 077): /_test/crash?at=AFTER_EFFECT_BEFORE_RECEIPT arms the crash 100 ms after it
answers. The frozen gateway armed it at once, so when a command's APPLIED reply arrived within the same
millisecond, the gateway exited before its answer to the arming request had left, the runner read an incomplete
answer and did not restart the gateway (the cell became INVALID). Disarming (at=NONE) stays immediate.

  python3 conformance/reference/mqtt_owner_gateway_ext077.py <the 075 gateway's options> [--binding auto|qos1|qos0] [--cut]
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import reference.mqtt_owner_gateway as G  # noqa: E402  (frozen by the C2 plan; reused unchanged)
import reference.mqtt_owner_gateway_ext as X  # noqa: E402  (frozen by the C3 plan; reused unchanged)
from reference._health import release  # noqa: E402
from reference.s3_owner_gateway import hostport  # noqa: E402

BINDINGS = ("auto", "qos1", "qos0")
CUT_RESET_AFTER_S = 0.05
CUT_WAIT_S = 2.0
ARM_DELAY_S = 0.1


def binding_qos(binding: str, caps) -> int:
    if binding == "qos1":
        return 1
    if binding == "qos0":
        return 0
    return 1 if "D_h" in set(caps) else 0


def _publish_topic(pkt: bytes) -> str:
    """Topic of an MQTT PUBLISH packet (fixed header included), '' if it is not one."""
    if not pkt or pkt[0] >> 4 != 3:
        return ""
    i = 1
    while pkt[i] & 0x80:
        i += 1
    i += 1
    n = int.from_bytes(pkt[i:i + 2], "big")
    return pkt[i + 2:i + 2 + n].decode(errors="replace")


def _split(buf: bytes):
    """Complete MQTT packets at the start of buf, and the rest."""
    out = []
    while len(buf) >= 2:
        mult, val, n = 1, 0, 0
        while True:
            if 1 + n >= len(buf):
                return out, buf
            b = buf[1 + n]
            val += (b & 0x7F) * mult
            n += 1
            if b & 0x80 == 0:
                break
            mult *= 128
            if n > 4:
                raise ValueError("malformed MQTT remaining length")
        total = 1 + n + val
        if len(buf) < total:
            break
        out.append(buf[:total])
        buf = buf[total:]
    return out, buf


class CutLink(G.Link):
    """The frozen gateway's link with the 077 "cut": going offline waits for the next command, lets it
    reach the broker, loses everything the broker sends from then on, and resets 50 ms later."""

    def __init__(self, host: str, port: int, log):
        self.cut_armed = False
        self.cutting = False
        self.cuts = {"armed": 0, "on_command": 0, "on_timeout": 0}
        self.arming = 0  # generation of the current arming (a timer of an earlier window never cuts a later one)
        self.log = log
        super().__init__(host, port)

    def _pump(self, a, b, pair):
        upstream = a is pair[0]  # gateway -> broker
        buf = b""
        try:
            while True:
                d = a.recv(65536)
                if not d:
                    break
                if upstream:
                    buf += d
                    pkts, buf = _split(buf)
                    for p in pkts:
                        b.sendall(p)
                        if self.cut_armed and _publish_topic(p).endswith("/cmd"):
                            self._cut_now("on_command", _publish_topic(p))
                else:
                    if self.cutting:
                        continue  # the broker's PUBACK and replies after the cut are lost
                    b.sendall(d)
        except (OSError, ValueError):
            pass
        self._close(pair)

    def _cut_now(self, how: str, topic: str = "", gen: int = -1):
        with self.lock:
            if not self.cut_armed or (gen >= 0 and gen != self.arming):
                return
            self.cut_armed = False
            self.cutting = True
            self.cuts[how] += 1
        self.log(op="cut", how=how, topic=topic)

        def later():
            time.sleep(CUT_RESET_AFTER_S if how == "on_command" else 0)
            G.Link.set_up(self, False)
            with self.lock:
                self.cutting = False
        threading.Thread(target=later, daemon=True).start()

    def arm_cut(self):
        with self.lock:
            self.cut_armed = True
            self.cuts["armed"] += 1
            self.arming += 1
            gen = self.arming

        def timeout():
            time.sleep(CUT_WAIT_S)
            self._cut_now("on_timeout", gen=gen)
        threading.Thread(target=timeout, daemon=True).start()

    def set_up(self, v: bool):
        if v:
            with self.lock:
                self.cut_armed = False
                self.cutting = False
        super().set_up(v)

    def describe(self) -> dict:
        d = super().describe()
        d["cuts"] = dict(self.cuts)
        return d


class Ext077Gateway(X.ExtGateway):
    def __init__(self, a, net=None):
        self._a077 = a
        self.binding = a.binding or self._side_file("gateway.binding") or "auto"
        if self.binding not in BINDINGS:
            raise SystemExit(f"binding {self.binding!r}: auto, qos1 or qos0")
        self.cut = bool(a.cut) or self._side_file("gateway.cut") == "1"
        self._net = net
        super().__init__(a, net if net is not None else self._make_net)

    def _side_file(self, name: str) -> str:
        p = os.path.join(os.path.dirname(os.path.abspath(self._a077.state)), name)
        if not os.path.exists(p):
            return ""
        with open(p) as fh:
            return fh.read().strip()

    def _make_net(self, gw):
        host, port = hostport(gw.a.broker)
        link = CutLink(host, port, gw.log) if self.cut else G.Link(host, port)
        data = G.Mqtt(gw, "data", link.port)
        ctl = G.Mqtt(gw, "ctl", link.port)
        return link, data, ctl

    def _open(self, a, net):
        # the binding is fixed before the first command: the frozen effect() publishes at a.qos_cmd
        a.qos_cmd = binding_qos(self.binding, self.caps)
        super()._open(a, net)

    def arm(self, at: str):
        if not at:
            with self.lock:
                self._arming = getattr(self, "_arming", 0) + 1  # a pending arming is cancelled
            super().arm(at)
            return
        with self.lock:
            self._arming = getattr(self, "_arming", 0) + 1
            gen = self._arming
        self.log(op="arm_requested", at=at, delay_s=ARM_DELAY_S)

        def later():
            time.sleep(ARM_DELAY_S)  # after the admin answer has left (see the module docstring)
            with self.lock:
                if gen != self._arming:
                    return
            X.ExtGateway.arm(self, at)
        threading.Thread(target=later, daemon=True).start()

    def set_online(self, v: bool):
        if not v and self.cut and self.offline_mode == "reset":
            self.link.arm_cut()
            self.log(op="online", v=v, mode="cut")
            return
        super().set_online(v)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mode", choices=sorted(G.MODES), required=True)
    ap.add_argument("--device-id", required=True)
    ap.add_argument("--ns", required=True, help="topic prefix of this cell (the firmware's -evc_ns)")
    ap.add_argument("--generation", type=int, default=1)
    ap.add_argument("--epoch", type=int, default=1)
    ap.add_argument("--listen", required=True, help="host:port of the protocol server")
    ap.add_argument("--admin-listen", required=True, help="host:port of the test-only admin server")
    ap.add_argument("--broker", required=True, help="Mosquitto host:port (reached through the gateway's link)")
    ap.add_argument("--contract-file", required=True, help="the MEASURED contract of this firmware mode or a restriction of it")
    ap.add_argument("--state", required=True, help="durable record (JSON lines): forwarded identities and the firmware's terminal receipts")
    ap.add_argument("--budget-s", type=float, default=1.8, help="time for the MQTT exchanges of one request (the authority waits 2 s)")
    ap.add_argument("--qos-cmd", type=int, choices=(0, 1, 2), default=1, help="ignored: the binding decides (release 077)")
    ap.add_argument("--keepalive-s", type=int, default=60)
    ap.add_argument("--offline-mode", choices=sorted(X.CHANNEL_MODE.values()), default="",
                    help="reset: queue channel (C2); drop: direct channel; default: gateway.channel next to --state, else reset")
    ap.add_argument("--binding", choices=BINDINGS, default="", help="default: gateway.binding next to --state, else auto")
    ap.add_argument("--cut", action="store_true", help="offline windows of the queue channel start with a cut (default: gateway.cut)")
    ap.add_argument("--log", default="gateway.jsonl")
    a = ap.parse_args(argv)
    g = Ext077Gateway(a)
    Data, Admin = G.make_handlers(g)
    data = ThreadingHTTPServer(hostport(a.listen), Data)
    admin = ThreadingHTTPServer(hostport(a.admin_listen), Admin)
    data.daemon_threads = admin.daemon_threads = True
    threading.Thread(target=admin.serve_forever, daemon=True).start()
    g.log(op="start", pid=os.getpid(), mode=a.mode, device=a.device_id, ns=a.ns, broker=a.broker, release=release(),
          contract=g.contract.get("id"), capabilities=sorted(g.caps), offline_mode=g.offline_mode, firmware=g.fw,
          forwarded=len(g.fwd), receipts=len(g.rcpt), binding=g.binding, qos_cmd=a.qos_cmd, cut=g.cut,
          contract_sha256=X.sha256_file(a.contract_file))
    print(f"mqtt owner gateway (077 ext) {a.device_id} mode={a.mode} caps={sorted(g.caps)} offline={g.offline_mode} "
          f"binding={g.binding} (QoS {a.qos_cmd}) cut={g.cut} ns={a.ns} on {a.listen} (admin {a.admin_listen}) -> {a.broker}", flush=True)
    try:
        data.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        g.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
