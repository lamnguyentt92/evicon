#!/usr/bin/env python3
"""Owner reboot check of the Zephyr EviCon firmware (release 075; `evicon_review.py measure zephyr-reboot`).

The contracts EviCon-Probe measures for the firmware hold under the fault model "network loss/delay,
client abandon; owner up (no owner crash or reboot)". This check records what the firmware certifies
across a reboot (the process killed and started again with the same flash file: the durable cutoff,
claim high-water mark and tombstones survive, the RAM receipt ring does not), so the paper's
statement of that fault model rests on a measurement, not on a reading of the code.

  before   cmds c1..c5 (applied), a fence at cutoff 8, a stop of c9 (tombstone), cmd c10 (applied
           after the cutoff); queries of c3 and c7
  (the driver restarts the firmware)
  after    ping; queries of every identity class: applied before the reboot (c3), sealed by the cutoff
           and never sent (c7), tombstoned (c9), applied after the cutoff (c10), never sent above the
           cutoff but inside the claimed block (c11); the same-identity replays c2 and c10; a stop of c3;
           a new fence at cutoff 12; a new identity inside the claimed block (c13) and one above it (c70)
  summarize  with the plant's effect log: effects per identity (a second effect of one identity is a
           duplicate), and per class what the firmware answered before and after the reboot

Writes the phases into --state (JSON) and the summary as JSON on stdout.

  python3 zephyr_reboot_check.py before --broker 192.168.107.152:1883 --device zephyr-1 --state reboot.json
  python3 zephyr_reboot_check.py after  --broker 192.168.107.152:1883 --device zephyr-1 --state reboot.json
  python3 zephyr_reboot_check.py summarize --state reboot.json --plant-log plant.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from collections import Counter

BEFORE = [("ping", {}, None), ("cmd", {"cmd": "c1", "seq": 1, "value": 1}, None), ("cmd", {"cmd": "c2", "seq": 2, "value": 1}, None),
          ("cmd", {"cmd": "c3", "seq": 3, "value": 1}, None), ("cmd", {"cmd": "c4", "seq": 4, "value": 1}, None),
          ("cmd", {"cmd": "c5", "seq": 5, "value": 1}, None), ("fence", {"cutoff": 8, "token": "rb-1"}, None),
          ("stop", {"cmd": "c9", "seq": 9}, None), ("cmd", {"cmd": "c10", "seq": 10, "value": 1}, None),
          ("query", {"cmd": "c3", "seq": 3}, "applied, in the receipt ring"), ("query", {"cmd": "c7", "seq": 7}, "sealed by the cutoff, never sent")]
AFTER = [("ping", {}, "durable cutoff"),
         ("query", {"cmd": "c3", "seq": 3}, "applied before the reboot"),
         ("query", {"cmd": "c7", "seq": 7}, "sealed by the cutoff, never sent"),
         ("query", {"cmd": "c9", "seq": 9}, "tombstoned (stop)"),
         ("query", {"cmd": "c10", "seq": 10}, "applied after the cutoff"),
         ("query", {"cmd": "c11", "seq": 11}, "never sent, above the cutoff, inside the claimed block"),
         ("cmd", {"cmd": "c2", "seq": 2, "value": 1}, "same-identity replay of an applied command"),
         ("cmd", {"cmd": "c10", "seq": 10, "value": 1}, "same-identity replay of an applied command"),
         ("stop", {"cmd": "c3", "seq": 3}, "stop of an applied command whose receipt was lost"),
         ("fence", {"cutoff": 12, "token": "rb-2"}, "new fence"),
         ("query", {"cmd": "c11", "seq": 11}, "never sent, below the new cutoff"),
         ("cmd", {"cmd": "c13", "seq": 13, "value": 1}, "new identity inside the claimed block"),
         ("cmd", {"cmd": "c70", "seq": 70, "value": 1}, "new identity above the claimed block"),
         ("query", {"cmd": "c70", "seq": 70}, "the identity above the block")]
APPLIED_BEFORE = {"c1", "c2", "c3", "c4", "c5", "c10"}
NEVER_SENT = {"c7", "c11"}


class Client:
    def __init__(self, broker: str, ns: str, device: str):
        import paho.mqtt.client as mqtt
        host, port = broker.rsplit(":", 1)
        self.ns, self.device = ns, device
        self.tag = f"evicon-reboot-{os.getpid()}-{os.urandom(3).hex()}"
        self.resp = f"{ns}/{device}/resp/{self.tag}"
        self.got, self.replies = threading.Event(), []
        self.c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.tag, protocol=mqtt.MQTTv311, clean_session=True)
        self.c.on_message = self._msg
        self.c.connect(host, int(port), keepalive=30)
        self.c.loop_start()
        sub = threading.Event()
        self.c.on_subscribe = lambda *_a, **_k: sub.set()
        self.c.subscribe(self.resp + "/#", qos=1)
        if not sub.wait(10):
            raise SystemExit(f"no SUBACK from {broker}")

    def _msg(self, _c, _u, m):
        try:
            self.replies.append(json.loads(m.payload or b"{}"))
        except ValueError:
            self.replies.append({})
        self.got.set()

    def rpc(self, kind: str, body: dict, timeout: float = 5.0):
        n = len(self.replies)
        self.got.clear()
        self.c.publish(f"{self.ns}/{self.device}/{kind}", json.dumps(dict(body, reply=f"{self.resp}/{kind}")), qos=1)
        end = time.monotonic() + timeout
        while time.monotonic() < end and len(self.replies) == n:
            self.got.wait(0.05)
        return self.replies[n] if len(self.replies) > n else None

    def close(self):
        self.c.loop_stop()
        self.c.disconnect()


def run_phase(a, steps):
    c = Client(a.broker, a.ns, a.device)
    out = []
    try:
        for kind, body, what in steps:
            rep = c.rpc(kind, body)
            out.append({"kind": kind, "sent": body, "what": what, "reply": rep, "t": round(time.time(), 3)})
    finally:
        c.close()
    return out


def summarize(state: dict, plant_lines: list) -> dict:
    eff = Counter(str(x.get("cmd", "")) for x in plant_lines if x.get("device") == state.get("device"))
    dups = {k: v for k, v in eff.items() if v > 1}
    after = state.get("after", [])
    rows = []
    for st in after:
        rep = st.get("reply") or {}
        rows.append({"what": st["what"], "kind": st["kind"], "identity": st["sent"].get("cmd", st["sent"].get("token", "")),
                     "outcome": rep.get("outcome") or ("ok" if rep.get("ok") else None), "reason": rep.get("reason"),
                     "cutoff": rep.get("cutoff")})
    false_stopped = [r["identity"] for r in rows if r["outcome"] == "STOPPED" and r["identity"] in APPLIED_BEFORE]
    false_applied = [r["identity"] for r in rows if r["outcome"] == "APPLIED" and r["identity"] in NEVER_SENT]
    ping = next((r for r in rows if r["kind"] == "ping"), {})
    uncertain = sorted({r["identity"] for r in rows if r["outcome"] == "UNCERTAIN"})
    return {"device": state.get("device"), "effects": dict(eff), "duplicates": dups, "false_stopped": false_stopped, "false_applied": false_applied,
            "cutoff_after_reboot": ping.get("cutoff"), "uncertain_after_reboot": uncertain, "rows": rows,
            "before": [{"kind": s["kind"], "identity": s["sent"].get("cmd", s["sent"].get("token", "")),
                        "outcome": (s.get("reply") or {}).get("outcome") or ("ok" if (s.get("reply") or {}).get("ok") else None),
                        "reason": (s.get("reply") or {}).get("reason")} for s in state.get("before", [])],
            "safe": not dups and not false_stopped and not false_applied}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("phase", choices=("before", "after", "summarize"))
    ap.add_argument("--broker", default="")
    ap.add_argument("--ns", default="evicon")
    ap.add_argument("--device", default="zephyr-1")
    ap.add_argument("--state", required=True)
    ap.add_argument("--plant-log", default="")
    a = ap.parse_args(argv)
    state = json.load(open(a.state)) if os.path.exists(a.state) else {"device": a.device, "ns": a.ns}
    if a.phase in ("before", "after"):
        if not a.broker:
            raise SystemExit("--broker required")
        state[a.phase] = run_phase(a, BEFORE if a.phase == "before" else AFTER)
        with open(a.state, "w") as fh:
            json.dump(state, fh, indent=1)
        missing = [s["what"] or s["kind"] for s in state[a.phase] if s["reply"] is None]
        print(json.dumps({"phase": a.phase, "steps": len(state[a.phase]), "no_reply": missing}))
        return 1 if a.phase == "before" and missing else 0
    lines = []
    if a.plant_log and os.path.exists(a.plant_log):
        with open(a.plant_log) as fh:
            for line in fh:
                try:
                    lines.append(json.loads(line))
                except ValueError:
                    pass
    print(json.dumps(summarize(state, lines), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
