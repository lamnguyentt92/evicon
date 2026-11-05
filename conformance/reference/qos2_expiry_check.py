#!/usr/bin/env python3
"""MQTT 5 message expiry at QoS 2 under a constant request-path delay (release 086, PREREG_extra_086.md QB; reviewer
comment M3(2), question Q6).

Why this check exists. Probe P9T (frozen, conformance/evicon_probe/probes.py) delays every request-path byte by
d = expiry + settle + 0.5 s (31.5 s at the 30 s expiry of mosquitto-rpc-qos2.json) and calls T OBSERVED when the
request never takes effect. At QoS 1 that is a direct test: the broker receives the PUBLISH after d, counts the expiry
from its own receipt and forwards it at once, so a late PUBLISH takes effect (the QoS 1 row: T not observed). At QoS 2 a
broker that forwards an incoming message only when the publisher's PUBREL arrives (Mosquitto stores the message on the
PUBLISH and releases it on the PUBREL) sees the PUBREL, sent after the PUBREC, also delayed by d: the message is released
about 2d after the client sent it and d after the broker took it. P9T's single delay d > expiry therefore tests only
"released after the expiry, counted from the broker's receipt"; it does not test whether the device can execute a QoS 2
request after the expiry counted from the client's send. This check measures both: the same broker, device, plant,
adapter (mqtt5_rpc_qos2) and frozen fault proxy as the probe run, with the request path delayed by d for the whole trial
(a path with a constant one-way delay d), at

  d = 0.75 x expiry   (22.5 s at 30 s): the PUBLISH reaches the broker before the expiry; a release on the PUBREL comes
                      after the expiry counted from the client's send
  d = P9T's delay     (31.5 s): P9T's own case, observed over 2d + 6 s

and records per trial when the effect appeared (seconds after the client's send, from the plant's truth) and whether it
appeared after the expiry. Nothing is assumed about the broker: the outcome is what the plant shows.

    python3 conformance/reference/qos2_expiry_check.py --config <derived mosquitto-rpc-qos2.json> --out <dir> [--reps 3]

Output: <dir>/qos2_expiry.json and <dir>/QOS2_EXPIRY.md. Run by `evicon_review.py measure mosquitto-qos2` after the
probe run, on VM-C with the stack still up.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evicon_probe import adapters as registry  # noqa: E402
from evicon_probe.faults import UP, FaultProxy, from_config  # noqa: E402
from evicon_probe.runner import _substitute, load_config  # noqa: E402

POLL_S = 0.25


def p9t_delay_s(expiry_s: float, settle_s: float) -> float:
    """P9T's delay (probes._late_then_backward with action expiry): expiry + settle + 0.5 s."""
    return expiry_s + settle_s + 0.5


def trial(adapter, faults, delay_s: float, expiry_s: float, tag: str) -> dict:
    cid = f"qx-{uuid.uuid4().hex[:8]}-{tag}"
    base = adapter.truth_baseline()
    faults.delay(UP, int(delay_s * 1000))
    t0 = time.monotonic()
    obs = adapter.send(cid)
    first = None
    end = t0 + 2 * delay_s + 6.0
    while time.monotonic() < end:
        n = adapter.effects(cid, base) or 0
        if n > 0 and first is None:
            first = round(time.monotonic() - t0, 2)
        time.sleep(POLL_S)
    faults.clear()
    waited = faults.quiesce() if hasattr(faults, "quiesce") else 0.0
    time.sleep(1.0)
    final = adapter.effects(cid, base) or 0
    return {"command_id": cid, "delay_s": delay_s, "expiry_s": expiry_s, "client": obs.status,
            "first_effect_s": first, "effects_final": final,
            "late_effect": bool(final > 0 and (first is None or first > expiry_s)),
            "observed_s": round(end - t0, 1), "quiesce_wait_s": round(waited, 1)}


def summarise(rows: list, expiry_s: float, p9t_s: float) -> dict:
    cases = []
    for d in sorted({r["delay_s"] for r in rows}):
        rs = [r for r in rows if r["delay_s"] == d]
        eff = [r["first_effect_s"] for r in rs if r["first_effect_s"] is not None]
        cases.append({"delay_s": d, "delay_over_expiry": round(d / expiry_s, 3), "trials": len(rs),
                      "with_effect": sum(1 for r in rs if r["effects_final"] > 0),
                      "late_effects": sum(1 for r in rs if r["late_effect"]),
                      "duplicates": sum(1 for r in rs if r["effects_final"] > 1),
                      "first_effect_s_median": round(statistics.median(eff), 2) if eff else None,
                      "first_effect_s_range": [min(eff), max(eff)] if eff else None,
                      "p9t_case": abs(d - p9t_s) < 1e-6})
    late = sum(c["late_effects"] for c in cases)
    return {"expiry_s": expiry_s, "p9t_delay_s": p9t_s, "cases": cases,
            "bounds_execution_by_expiry_from_send": late == 0,
            "finding": ("no QoS 2 request took effect after the expiry counted from the client's send" if late == 0 else
                        f"{late} QoS 2 request(s) took effect after the expiry counted from the client's send: the message "
                        "expiry does not bound the device's execution at QoS 2")}


def markdown(res: dict) -> str:
    L = ["# MQTT 5 message expiry at QoS 2 under a constant request-path delay (release 086, QB)", "",
         f"Broker, device, plant and adapter of `measure mosquitto-qos2` ({res.get('system_version', '?')[:200]}); expiry "
         f"{res['expiry_s']:g} s (MessageExpiryInterval on every request); P9T's delay {res['p9t_delay_s']:g} s. The "
         "request path is delayed by d for the whole trial (every byte, so also the publisher's PUBREL); the effect time is "
         "seconds after the client's send, from the plant's truth.", "",
         "| d (s) | d / expiry | trials | with effect | after the expiry (from send) | duplicates | first effect, median (range) s | P9T's case |",
         "|---|---|---|---|---|---|---|---|"]
    for c in res["cases"]:
        rng = f"{c['first_effect_s_median']} ({c['first_effect_s_range'][0]}-{c['first_effect_s_range'][1]})" if c["first_effect_s_range"] else "-"
        L.append(f"| {c['delay_s']:g} | {c['delay_over_expiry']} | {c['trials']} | {c['with_effect']} | {c['late_effects']} | "
                 f"{c['duplicates']} | {rng} | {'yes' if c['p9t_case'] else 'no'} |")
    L += ["", f"**Finding:** {res['finding']}.", "",
          "Reading: P9T's T label for this row says only that a QoS 2 request whose release reaches the broker after the "
          "expiry, counted from the broker's receipt of the PUBLISH, does not take effect. Whether QoS 2 bounds execution by "
          "the expiry counted from the client's send is the row d < expiry above.", ""]
    for r in res["trials"]:
        L.append(f"- {r['command_id']}: d {r['delay_s']:g} s, client {r['client']}, first effect {r['first_effect_s']}, "
                 f"effects {r['effects_final']}, late {r['late_effect']}")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", required=True, help="the derived probe config of mosquitto-rpc-qos2 (adapter mqtt5_rpc_qos2)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--fractions", default="0.75", help="delays as fractions of the expiry, besides P9T's own delay")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    if cfg["adapter"] != "mqtt5_rpc_qos2":
        raise SystemExit("qos2_expiry_check: the config's adapter must be mqtt5_rpc_qos2")
    faults = from_config(_substitute(cfg.get("faults") or {"kind": "none"}, {}))
    if not isinstance(faults, FaultProxy):
        raise SystemExit("qos2_expiry_check: needs the config's fault proxy (faults.kind proxy)")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    adapter = registry.load(cfg["adapter"])(cfg["adapter_config"])
    expiry = float(adapter.expiry_s() or 0)
    if expiry <= 0:
        raise SystemExit("qos2_expiry_check: the config sets no message_expiry_s")
    settle = float(cfg.get("params", {}).get("settle_s", 1.0))
    p9t = p9t_delay_s(expiry, settle)
    delays = [round(float(f) * expiry, 3) for f in a.fractions.split(",") if f.strip()] + [p9t]
    rows = []
    try:
        adapter.setup()
        for rep in range(a.reps):
            for i, d in enumerate(delays):
                r = trial(adapter, faults, d, expiry, f"{rep}{i}")
                r["repetition"] = rep
                rows.append(r)
                print(f"qos2-expiry rep={rep} d={d:g}s: client={r['client']} first_effect={r['first_effect_s']} "
                      f"effects={r['effects_final']} late={r['late_effect']}", flush=True)
    finally:
        try:
            faults.clear()
        except Exception:  # noqa: BLE001
            pass
        adapter.teardown()
        faults.stop()
    res = summarise(rows, expiry, p9t)
    res.update(trials=rows, system_version=cfg.get("system_version", ""), config=cfg["name"], reps=a.reps,
               release="086", scope="MEASUREMENT")
    (out / "qos2_expiry.json").write_text(json.dumps(res, indent=2) + "\n")
    (out / "QOS2_EXPIRY.md").write_text(markdown(res))
    print(json.dumps({k: res[k] for k in ("cases", "finding")}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
