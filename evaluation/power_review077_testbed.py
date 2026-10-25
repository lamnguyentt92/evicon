#!/usr/bin/env python3
"""Power of the PREREG_review_077.md decision rules from the testbed's own per-episode spread
(release 077, planning only).

The simulator's per-episode differences are bimodal (in some episodes no device locks up) and far more
spread than the testbed's, so its power (sim_predictions/review077_power.csv) is a lower bound. This
script takes the spread from the testbed runs closest to each new run instead: per-episode EVICON clean
(or confirmed) fulfilment, pp of offered, episodes 0-7 (0-4 for K), centred, plus a true mean; each rule
tested as pairs_report.py does (Student t, TOST), Holm within its family with the other members at
their simulator means, alpha 0.05; 4,000 resamples, seed 77. Templates (release 072-075 runs, the N8
report of release 076):

  lossy-inf     expW-direct-winf/20261022T130854 K0 -> K0F clean (emulated owner, direct channel, W
                unbounded; SD 9.3): U-1 (MinIO at W unbounded; a lost PUT never takes effect) and U-2
                (the firmware on the direct channel at W unbounded)
  queue-inf-c   expW-queue-winf/20261022T021601 K0 -> K0F confirmed (SD 5.7): U-3
  queue-inf     the same, clean (SD 0.38): U-4
  fw-null       expC3-ablation/20261024T161324 ZEPHYR_F_QUEUE -> ZEPHYR_QS_QUEUE clean (the firmware, owner
                up: fence and stop without orphans; SD 0.7): O-1
  stop-w6       expS-w6/20261022T161003 K0 -> K0S clean (SD 9.3): O-2 (without orphans the fence should
                reach the stop's level) and V-1, V-2; WAN-2 (the same run with a WAN)
  fence-w6      the same run, K0 -> K0F clean: WAN-1
  k2-null       expA-confirm-k2/20261017T111506 K2 -> K3F clean, EVICON (SD 0.12) and RES_PERIODIC (SD
                0.08): K-1, K-2; also with ten times the spread

Writes nothing; prints a markdown table (the plan's section 5 quotes it).

    python3 evaluation/power_review077_testbed.py
"""
from __future__ import annotations

import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pairs_report import holm, one_sided  # noqa: E402  (frozen; reused, not edited)

# EVICON clean_fulfilled (or confirmed) / offered in pp, per episode
W_DINF_K0 = [56.47, 70.63, 68.35, 38.85, 56.92, 63.64, 47.88, 57.62]
W_DINF_K0F = [80.42, 75.17, 76.49, 70.21, 76.47, 73.93, 67.16, 66.0]
W_QINF_K0 = [83.39, 76.75, 82.28, 76.31, 75.95, 78.04, 75.49, 72.7]
W_QINF_K0F = [84.27, 77.97, 82.46, 77.35, 76.64, 78.22, 75.98, 73.37]
W_QINF_K0_CONF = [60.66, 69.41, 66.18, 49.65, 59.52, 65.01, 54.9, 60.47]
W_QINF_K0F_CONF = [79.2, 74.13, 73.96, 69.69, 75.26, 73.07, 69.61, 66.67]
C3_F_QUEUE = [85.14, 79.55, 83.73, 77.18, 78.2, 78.22, 76.8, 74.37]
C3_QS_QUEUE = [85.14, 79.55, 83.73, 78.92, 78.2, 78.56, 76.47, 74.37]
S6_K0 = [24.83, 37.76, 35.62, 12.02, 25.43, 36.88, 18.14, 29.31]
S6_K0S = [85.14, 79.9, 83.73, 78.75, 78.2, 78.39, 76.8, 74.54]
S6_K0F = [69.58, 56.82, 54.61, 51.22, 67.65, 67.92, 58.01, 44.05]
A2_K2_EV = [81.42, 80.86, 77.4, 77.48, 73.8]
A2_K3F_EV = [81.42, 80.86, 77.23, 77.65, 73.8]
A2_K2_RP = [81.42, 80.86, 77.23, 77.48, 73.8]
A2_K3F_RP = [81.42, 80.86, 77.4, 77.48, 73.8]


def centred(a, b):
    d = [y - x for x, y in zip(a, b)]
    m = statistics.mean(d)
    return [v - m for v in d]


TEMPLATES = {"lossy-inf": centred(W_DINF_K0, W_DINF_K0F), "queue-inf-c": centred(W_QINF_K0_CONF, W_QINF_K0F_CONF),
             "queue-inf": centred(W_QINF_K0, W_QINF_K0F), "fw-null": centred(C3_F_QUEUE, C3_QS_QUEUE),
             "stop-w6": centred(S6_K0, S6_K0S), "fence-w6": centred(S6_K0, S6_K0F), "k2-null-ev": centred(A2_K2_EV, A2_K3F_EV), "k2-null-rp": centred(A2_K2_RP, A2_K3F_RP)}
TEMPLATES["k2-null-ev x10"] = [10 * v for v in TEMPLATES["k2-null-ev"]]
TEMPLATES["k2-null-rp x10"] = [10 * v for v in TEMPLATES["k2-null-rp"]]
TEMPLATES["fw-null x5"] = [5 * v for v in TEMPLATES["fw-null"]]

# simulator means of the WAN rules (sim_predictions/review077_power.csv, scenario wan)
WAN_SIM = {"WAN-1": 35.66, "WAN-2": 40.27}

# family -> [(id, test, margin, n, template, simulator mean)]   (sim_predictions/review077_power.csv)
FAMILIES = {
    "unbounded": [("U-1", "at_least", 5.0, 8, "lossy-inf", 14.75), ("U-2", "at_least", 5.0, 8, "lossy-inf", 14.60),
                  ("U-3", "at_least", 5.0, 8, "queue-inf-c", 14.97), ("U-4", "equivalent", 3.0, 8, "queue-inf", 1.38)],
    "orphan": [("O-1", "equivalent", 5.0, 8, "fw-null", -0.12), ("O-2", "at_least", 10.0, 8, "stop-w6", 39.11)],
    "k2": [("K-1", "equivalent", 2.0, 5, "k2-null-ev", 0.06), ("K-2", "equivalent", 2.0, 5, "k2-null-rp", 0.09)],
    "regime": [("V-1", "at_least", 5.0, 8, "stop-w6", 21.64), ("V-2", "at_least", 5.0, 8, "stop-w6", 22.32)],
    "wan": [("WAN-1", "at_least", 10.0, 8, "fence-w6", WAN_SIM["WAN-1"]), ("WAN-2", "at_least", 10.0, 8, "stop-w6", WAN_SIM["WAN-2"])],
}
# true means to scan for each rule (the others of the family at their simulator means)
SCAN = {"U-1": (6, 8, 10, 12, 14.75), "U-2": (6, 8, 10, 12, 14.60), "U-3": (6, 8, 10, 11, 14.97), "U-4": (0, 0.67, 1.0, 1.5, 2.0),
        "O-1": (0, 1, 2, 3, 4), "O-2": (12, 15, 20, 25, 39.11), "K-1": (0, 0.5, 1.0, 1.5), "K-2": (0, 0.5, 1.0, 1.5),
        "V-1": (8, 10, 12, 15, 21.64), "V-2": (8, 10, 12, 15, 22.32), "WAN-1": (12, 15, 20, 25, 35.66), "WAN-2": (12, 15, 20, 30, 40.27)}


def p_value(d, test, m):
    if test == "at_least":
        return one_sided(d, m, upper=False)
    return max(one_sided(d, -m, upper=False), one_sided(d, m, upper=True))


def power(fam: str, target: str, mean: float, template: str | None = None, resamples: int = 4000) -> float:
    rng = random.Random(77)
    rules = FAMILIES[fam]
    met = 0
    for _ in range(resamples):
        ps = {}
        for rid, test, m, n, tpl, sim in rules:
            t = TEMPLATES[template if (rid == target and template) else tpl]
            mu = mean if rid == target else sim
            ps[rid] = p_value([mu + t[rng.randrange(len(t))] for _ in range(n)], test, m)
        met += holm(ps)[target] < 0.05
    return met / resamples


def main():
    print("| family | rule | test | n | template (SD) | true mean -> power |")
    print("|---|---|---|---|---|---|")
    for fam, rules in FAMILIES.items():
        for rid, test, m, n, tpl, sim in rules:
            tpls = [tpl] + ([f"{tpl} x10"] if f"{tpl} x10" in TEMPLATES else []) + ([f"{tpl} x5"] if f"{tpl} x5" in TEMPLATES else [])
            for t in tpls:
                sd = statistics.stdev(TEMPLATES[t])
                cells = ", ".join(f"{mu:g}: {power(fam, rid, mu, t):.2f}" for mu in SCAN[rid])
                print(f"| {fam} | {rid} | {test} {m:g} | {n} | {t} ({sd:.2f}) | {cells} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
