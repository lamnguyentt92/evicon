"""EviCon MQTT owner gateway, release 087: the claim floor of the boot-report firmware (on the 077 gateway).

PREREG_extra_087.md item B (reviewer comment M3; EVICON_087 design section 2). The cutoff firmware keeps its cutoff, its
claim mark and its tombstones in flash and its receipt ring and level in RAM. After a reboot it restores the claim mark
and treats every sequence at or below it as possibly started: that claim floor F is never executed again and never
certified (the eviction guard answers UNCERTAIN). The boot-report firmware (firmware/zephyr-boot: the frozen sources of
firmware/zephyr-cutoff plus src/evc_boot_report.c) adds "floor": F to every reply and publishes a hello
{"device", "hello": true, "floor": F} on <ns>/<dev>/hello after every (re)connect. This gateway (on
conformance/reference/mqtt_owner_gateway_ext077.py, frozen by PREREG_review_077.md, imported, never edited) adds:

  GW-1  floor tracking: the floor of every firmware reply and of every hello (subscribed on the control connection) raises
      the gateway's floor (kept durably in --state as {"k": "floor"} records; a lower report is logged, never adopted)
  GW-2  claim_floor in every answer to the authority: receipts (terminal or not), the barrier answer (the floor of the
      fence reply itself, so that the level and the floor come from the same boot; none when that reply had none) and
      the bodies of refusals (503, 504, 409); nothing when no floor was ever reported (the measured firmware, whose
      replies carry none: the answers are then those of the 077 gateway byte for byte)
  GW-3  the seal (switch gateway.recovery, per cell; --recovery): an effect request for an identity this gateway never
      forwarded whose sequence is at or below the known floor is not published. The gateway writes a durable sealed
      record ({"k": "seal"}, fsync) and answers STOPPED (reason GATEWAY_SEALED_BELOW_CLAIM_FLOOR); it never forwards
      that identity later (a request with another intent or sequence is refused 409 IDENTITY_CONFLICT). Safety rests
      on the premise GP (the gateway is the only publisher on the cell's command topic) and on the durable record:
      an identity never forwarded is never delivered, and the seal makes "never forwarded" permanent

Everything else is the 077 gateway's (binding, cut, crash arming), and so the 075 gateway's (restricted contracts,
direct channel) and the frozen C2 gateway's. With the measured firmware and gateway.recovery absent or "0" the gateway
behaves exactly as the 077 gateway (test_release087).

  python3 conformance/reference/mqtt_owner_gateway_ext087.py <the 077 gateway's options> [--recovery]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import reference.mqtt_owner_gateway as G  # noqa: E402  (frozen by the C2 plan; reused unchanged)
import reference.mqtt_owner_gateway_ext as X  # noqa: E402  (frozen by the C3 plan; reused unchanged)
import reference.mqtt_owner_gateway_ext077 as E77  # noqa: E402  (frozen by PREREG_review_077.md; reused unchanged)
from reference._health import release  # noqa: E402
from reference.s3_owner_gateway import Reject, canonical_hash, hostport  # noqa: E402

SEAL_REASON = "GATEWAY_SEALED_BELOW_CLAIM_FLOOR"


def floor_of(body) -> int | None:
    """The firmware's claim floor in a reply or hello (None when it carries none)."""
    if not isinstance(body, dict):
        return None
    f = body.get("floor")
    if isinstance(f, bool) or not isinstance(f, int) or f < 0:
        return None
    return f


class HelloMqtt(G.Mqtt):
    """The frozen gateway's MQTT connection that also subscribes the firmware's hello topic after every connect."""

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        super()._on_connect(client, userdata, flags, reason_code, properties)
        client.subscribe(f"{self.gw.a.ns}/{self.gw.a.device_id}/hello", qos=1)


class Ext087Gateway(E77.Ext077Gateway):
    def __init__(self, a, net=None):
        self._a087 = a
        self.floor = None  # the highest floor the firmware reported (None: never)
        self.floor_reports = {"replies": 0, "hellos": 0, "raised": 0, "lower": 0}
        self.fence_floor = {}  # fence token -> the floor of that fence reply
        self.sealed = {}
        self.last_level, self.last_ov = 0, 0
        self.recovery = bool(getattr(a, "recovery", False)) or self._side_087("gateway.recovery") == "1"
        super().__init__(a, net)

    def _side_087(self, name: str) -> str:
        p = os.path.join(os.path.dirname(os.path.abspath(self._a087.state)), name)
        if not os.path.exists(p):
            return ""
        with open(p) as fh:
            return fh.read().strip()

    # ---- network: the hello subscription ---------------------------------------
    def _make_net(self, gw):
        host, port = hostport(gw.a.broker)
        link = E77.CutLink(host, port, gw.log) if self.cut else G.Link(host, port)
        data = G.Mqtt(gw, "data", link.port)
        ctl = HelloMqtt(gw, "ctl", link.port)
        return link, data, ctl

    # ---- durable record ----------------------------------------------------------
    def _load_state(self):
        super()._load_state()
        if not os.path.exists(self.a.state):
            return
        with open(self.a.state) as fh:
            for line in fh:
                try:
                    x = json.loads(line)
                except ValueError:
                    continue
                if x.get("k") == "floor" and isinstance(x.get("floor"), int):
                    self.floor = x["floor"] if self.floor is None else max(self.floor, x["floor"])
                elif x.get("k") == "seal":
                    self.sealed.setdefault(x["cid"], x)

    # ---- GW-1: the floor of every reply and hello ------------------------------------
    def _note_floor(self, f: int, source: str):
        with self.lock:
            self.floor_reports["hellos" if source == "hello" else "replies"] += 1
            prev = self.floor
            if prev is not None and f <= prev:
                if f < prev:
                    self.floor_reports["lower"] += 1
                    lower = True
                else:
                    return
            else:
                lower = False
                self.floor = f
                self.floor_reports["raised"] += 1
                if self.state_fh is not None:
                    self._append({"k": "floor", "floor": f, "src": source, "t": round(time.time(), 6)})
        if lower:
            self.log(op="floor_lower", floor=f, known=prev, src=source)
        else:
            self.log(op="floor", floor=f, previous=prev, src=source)

    def on_reply(self, kind: str, body: dict):
        if getattr(self, "dropping", False):
            return super().on_reply(kind, body)  # the direct channel loses it (075 gateway), floor included
        f = floor_of(body)
        if f is not None:
            self._note_floor(f, kind)
            if kind == "fence":
                with self.lock:
                    self.fence_floor[str(body.get("token", ""))] = f
        if kind in ("cmd", "query", "stop", "fence", "ping") and isinstance(body, dict):
            with self.lock:
                if isinstance(body.get("level"), int):
                    self.last_level = body["level"]
                if isinstance(body.get("owner_version"), int):
                    self.last_ov = max(self.last_ov, body["owner_version"])
        if kind == "hello":
            self.log(op="hello", floor=f, device=(body or {}).get("device"))
            return
        super().on_reply(kind, body)

    # ---- GW-2: claim_floor in every answer --------------------------------------------
    def _with_floor(self, fn, *args, fence_token: str = ""):
        try:
            r = fn(*args)
        except Reject as e:
            with self.lock:
                f = self.floor
                self.fence_floor.pop(fence_token, None)
            if f is not None and isinstance(e.body, dict):
                e.body.setdefault("claim_floor", f)
            raise
        with self.lock:
            f = self.fence_floor.pop(fence_token, None) if fence_token else self.floor
        if f is None or not isinstance(r, dict):
            return r
        return dict(r, claim_floor=f)

    def effect(self, req: dict, t0: float) -> dict:
        return self._with_floor(self._effect087, req, t0)

    def query(self, cid: str, t0: float) -> dict:
        return self._with_floor(super().query, cid, t0)

    def stop(self, req: dict, t0: float) -> dict:
        return self._with_floor(super().stop, req, t0)

    def barrier(self, req: dict, t0: float) -> dict:
        return self._with_floor(super().barrier, req, t0, fence_token=str((req or {}).get("token", "")))

    # ---- GW-3: the seal of a never-forwarded identity at or below the floor -----------------
    def _effect087(self, req: dict, t0: float) -> dict:
        if self.recovery:
            r = self._seal(req)
            if r is not None:
                return r
        return super().effect(req, t0)

    def _seal(self, req: dict):
        self._validate(req, "effect")
        cid = str(req.get("command_id", ""))
        seq = int(req.get("sequence", 0) or 0)
        if not cid or seq <= 0 or req.get("operation") != "ADD" or not isinstance(req.get("value"), list) or len(req["value"]) != 1:
            return None  # the 077 gateway refuses it
        h = canonical_hash(req)
        if req.get("intent_hash") and req["intent_hash"] != h:
            return None
        with self.lock:
            s = self.sealed.get(cid)
            if s is None:
                if cid in self.fwd or self.floor is None or seq > self.floor:
                    return None
                s = {"k": "seal", "cid": cid, "seq": seq, "intent": h, "value": int(req["value"][0]), "floor": self.floor,
                     "t": round(time.time(), 6)}
                self._append(s)  # durable before the answer: this identity is never forwarded later
                self.sealed[cid] = s
                fresh = True
            else:
                if s["intent"] != h or s["seq"] != seq:
                    raise Reject(409, {"error": "IDENTITY_CONFLICT"})
                fresh = False
            level, ov = self.last_level, max(1, self.last_ov)
        self.log(op="seal", cid=cid, seq=seq, floor=s["floor"], fresh=fresh)
        return {"schema_version": 1, "domain_id": self.a.device_id, "generation": self.a.generation, "command_id": cid,
                "intent_hash": h, "outcome": "STOPPED", "owner_version": ov, "receipt_version": ov, "state": [int(level)],
                "reason": SEAL_REASON}

    def forward(self, cid: str, seq: int, intent: str, value: int, via: str):
        with self.lock:
            sealed = cid in self.sealed
        if sealed:
            raise Reject(409, {"error": "IDENTITY_CONFLICT", "detail": "sealed by the gateway below the claim floor: never forwarded"})
        return super().forward(cid, seq, intent, value, via)

    def describe087(self) -> dict:
        with self.lock:
            return {"floor": self.floor, "floor_reports": dict(self.floor_reports), "sealed": len(self.sealed), "recovery": self.recovery}


def make_handlers(g: Ext087Gateway):
    """The frozen handlers, with the floor state added to the admin server's /_debug/state."""
    Data, Admin = G.make_handlers(g)

    class Admin087(Admin):
        def do_GET(self):
            if self.route() == "/_debug/floor":
                return self.send(200, g.describe087())
            return super().do_GET()
    return Data, Admin087


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
    ap.add_argument("--state", required=True, help="durable record (JSON lines): forwarded identities, terminal receipts, floors, seals")
    ap.add_argument("--budget-s", type=float, default=1.8, help="time for the MQTT exchanges of one request (the authority waits 2 s)")
    ap.add_argument("--qos-cmd", type=int, choices=(0, 1, 2), default=1, help="ignored: the binding decides (release 077)")
    ap.add_argument("--keepalive-s", type=int, default=60)
    ap.add_argument("--offline-mode", choices=sorted(X.CHANNEL_MODE.values()), default="",
                    help="reset: queue channel (C2); drop: direct channel; default: gateway.channel next to --state, else reset")
    ap.add_argument("--binding", choices=E77.BINDINGS, default="", help="default: gateway.binding next to --state, else auto")
    ap.add_argument("--cut", action="store_true", help="offline windows of the queue channel start with a cut (default: gateway.cut)")
    ap.add_argument("--recovery", action="store_true", help="GW-3, the seal below the claim floor (default: gateway.recovery next to --state)")
    ap.add_argument("--log", default="gateway.jsonl")
    a = ap.parse_args(argv)
    g = Ext087Gateway(a)
    Data, Admin = make_handlers(g)
    data = ThreadingHTTPServer(hostport(a.listen), Data)
    admin = ThreadingHTTPServer(hostport(a.admin_listen), Admin)
    data.daemon_threads = admin.daemon_threads = True
    threading.Thread(target=admin.serve_forever, daemon=True).start()
    g.log(op="start", pid=os.getpid(), mode=a.mode, device=a.device_id, ns=a.ns, broker=a.broker, release=release(),
          contract=g.contract.get("id"), capabilities=sorted(g.caps), offline_mode=g.offline_mode, firmware=g.fw,
          forwarded=len(g.fwd), receipts=len(g.rcpt), binding=g.binding, qos_cmd=a.qos_cmd, cut=g.cut, recovery=g.recovery,
          floor=g.floor, sealed=len(g.sealed), contract_sha256=X.sha256_file(a.contract_file))
    print(f"mqtt owner gateway (087 ext) {a.device_id} mode={a.mode} caps={sorted(g.caps)} offline={g.offline_mode} "
          f"binding={g.binding} (QoS {a.qos_cmd}) cut={g.cut} recovery={g.recovery} floor={g.floor} ns={a.ns} on {a.listen} "
          f"(admin {a.admin_listen}) -> {a.broker}", flush=True)
    try:
        data.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        g.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
