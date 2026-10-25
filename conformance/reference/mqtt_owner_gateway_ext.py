"""EviCon MQTT owner gateway, release 075 extensions for experiment C3 (on the frozen gateway of C2).

conformance/reference/mqtt_owner_gateway.py (frozen by the C2 plan, imported here, never edited) speaks
the EviCon device-owner protocol for the Zephyr EviCon firmware behind Mosquitto. Experiment C3 needs two
things it does not have:

1. A restricted contract. The gateway may advertise a restriction of the contract measured for its
   firmware mode to a subset of its capabilities (experiments/owners/zephyr/README.md): a contract with
   source MEASURED, capabilities a proper subset of the mode's, the provenance of each kept capability
   copied from the measured contract, and provenance["restricted_from"] naming the measured contract
   (id and SHA-256 of its file). Requests that need a capability the advertised contract lacks are
   refused (409), as by an owner that offers only that capability: a receipt query needs Q_A, a stop
   Q_S, a barrier F. A barrier still answers with a receipt for every identity at or below its cutoff
   (that is what F is), so the restriction to {F} is the emulated K0F's contract.

2. A direct channel. Offline mode "reset" (default: the frozen behaviour, the queue channel of C2): the
   link to the broker goes down, both MQTT connections are reset, and paho delivers what was published
   meanwhile after it reconnects (late). Offline mode "drop" (the direct channel): the connections stay
   up and every message either way is lost while offline: a command published then never reaches the
   firmware, a reply the firmware sends then never reaches the gateway. A lost command never takes
   effect, as with an MQTT client without a session or a broker that expires what it holds (and as
   the emulated owner's -offline-mode drop of experiments W and S).

The mode comes from --offline-mode, else from the file gateway.channel next to --state ("queue" or
"direct"; the C3 runner writes it per cell, and it is fetched with the gateway's other files).

  python3 conformance/reference/mqtt_owner_gateway_ext.py <the frozen gateway's options> [--offline-mode reset|drop]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import reference.mqtt_owner_gateway as G  # noqa: E402  (frozen by the C2 plan; reused unchanged)
from reference._health import release  # noqa: E402
from reference.s3_owner_gateway import Reject, hostport  # noqa: E402

CHANNEL_MODE = {"queue": "reset", "direct": "drop"}
NEEDS = {"query": "Q_A", "stop": "Q_S", "barrier": "F"}


def sha256_file(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def check_contract(mode: str, c: dict) -> str:
    """'' when the contract may be served in this firmware mode, else the reason."""
    caps, full = set(c.get("capabilities", [])), G.MODES[mode]
    if c.get("source") != "MEASURED":
        return f"source {c.get('source')}: experiments C2 and C3 need MEASURED"
    if caps == full:
        return ""
    if not caps < full:
        return f"capabilities {sorted(caps)}: mode {mode} serves {sorted(full)} or a restriction of it"
    rf = (c.get("provenance") or {}).get("restricted_from", "")
    if "sha256:" not in rf:
        return f"capabilities {sorted(caps)} restrict mode {mode}'s {sorted(full)} but provenance.restricted_from names no measured contract"
    for cap in caps:
        if not (c.get("provenance") or {}).get(cap):
            return f"restricted contract: capability {cap} has no provenance"
    if "D_h" not in caps and c.get("dedup_horizon_ms", 0):
        return "restricted contract: dedup_horizon_ms without D_h"
    return ""


class _DropConn:
    """An Mqtt connection whose publishes are lost while the gateway's direct channel is offline."""

    def __init__(self, gw, inner):
        self.gw, self.inner = gw, inner

    def publish(self, kind: str, body: dict, qos: int):
        if self.gw.dropping:
            self.gw.log(op="dropped", dir="out", role=self.inner.role, kind=kind, cmd=body.get("cmd", body.get("token", "")))
            with self.gw.lock:
                self.gw.dropped["out"] += 1
            return None
        return self.inner.publish(kind, body, qos)

    def __getattr__(self, name):
        return getattr(self.inner, name)


class ExtGateway(G.Gateway):
    def __init__(self, a, net=None):
        """The frozen Gateway.__init__ with the restricted-contract rule (check_contract) in place of its
        exact-capability check; the rest is the frozen gateway's."""
        self.a = a
        self.mode = a.mode
        with open(a.contract_file) as fh:
            self.contract = json.load(fh)
        why = check_contract(self.mode, self.contract)
        if why:
            raise SystemExit(f"--contract-file {a.contract_file}: {why}")
        self.caps = set(self.contract.get("capabilities", []))
        self.offline_mode = a.offline_mode or self._channel_file() or "reset"
        if self.offline_mode not in CHANNEL_MODE.values():
            raise SystemExit(f"offline mode {self.offline_mode!r}: reset (queue channel) or drop (direct channel)")
        self.dropping = False
        self.dropped = {"out": 0, "in": 0}
        self.lock = threading.Lock()
        self.log_fh = open(a.log, "a", buffering=1) if a.log else None
        self.state_fh = None
        try:
            self._open(a, net)
        except BaseException:
            self.close()
            raise
        if self.offline_mode == "drop":
            self.data, self.ctl = _DropConn(self, self.data), _DropConn(self, self.ctl)

    def _channel_file(self) -> str:
        p = os.path.join(os.path.dirname(os.path.abspath(self.a.state)), "gateway.channel")
        if not os.path.exists(p):
            return ""
        with open(p) as fh:
            ch = fh.read().strip()
        if ch not in CHANNEL_MODE:
            raise SystemExit(f"{p}: channel {ch!r}, expected queue or direct")
        return CHANNEL_MODE[ch]

    # ---- direct channel -----------------------------------------------------
    def on_reply(self, kind: str, body: dict):
        if self.dropping:
            self.log(op="dropped", dir="in", kind=kind, cmd=str(body.get("cmd", body.get("token", ""))))
            with self.lock:
                self.dropped["in"] += 1
            return
        super().on_reply(kind, body)

    def set_online(self, v: bool):
        if self.offline_mode == "drop":
            self.dropping = not v
            self.log(op="online", v=v, mode="drop")
            return
        super().set_online(v)

    # ---- restricted contract ------------------------------------------------
    def _needs(self, what: str):
        cap = NEEDS[what]
        if cap not in self.caps:
            raise Reject(409, {"error": "UNSUPPORTED", "detail": f"{what} needs {cap}; the advertised contract {self.contract.get('id')} "
                                                                   f"has {sorted(self.caps)}"})

    def query(self, cid: str, t0: float) -> dict:
        if self.mode != "PLAIN":
            self._needs("query")
        return super().query(cid, t0)

    def stop(self, req: dict, t0: float) -> dict:
        if self.mode == "STOP":
            self._needs("stop")
        return super().stop(req, t0)

    def barrier(self, req: dict, t0: float) -> dict:
        if "F" in G.MODES[self.mode]:
            self._needs("barrier")
        return super().barrier(req, t0)


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
    ap.add_argument("--qos-cmd", type=int, choices=(1, 2), default=1, help="QoS of the command messages (the measured call shape: 1)")
    ap.add_argument("--keepalive-s", type=int, default=60)
    ap.add_argument("--offline-mode", choices=sorted(CHANNEL_MODE.values()), default="",
                    help="reset: queue channel (C2); drop: direct channel; default: gateway.channel next to --state, else reset")
    ap.add_argument("--log", default="gateway.jsonl")
    a = ap.parse_args(argv)
    g = ExtGateway(a)
    Data, Admin = G.make_handlers(g)
    data = ThreadingHTTPServer(hostport(a.listen), Data)
    admin = ThreadingHTTPServer(hostport(a.admin_listen), Admin)
    data.daemon_threads = admin.daemon_threads = True
    threading.Thread(target=admin.serve_forever, daemon=True).start()
    g.log(op="start", pid=os.getpid(), mode=a.mode, device=a.device_id, ns=a.ns, broker=a.broker, release=release(),
          contract=g.contract.get("id"), capabilities=sorted(g.caps), offline_mode=g.offline_mode, firmware=g.fw,
          forwarded=len(g.fwd), receipts=len(g.rcpt), qos_cmd=a.qos_cmd, contract_sha256=sha256_file(a.contract_file))
    print(f"mqtt owner gateway (075 ext) {a.device_id} mode={a.mode} caps={sorted(g.caps)} offline={g.offline_mode} ns={a.ns} "
          f"on {a.listen} (admin {a.admin_listen}) -> {a.broker}", flush=True)
    try:
        data.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for m in (g.data, g.ctl):
            m.close()
        g.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
