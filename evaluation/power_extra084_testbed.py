#!/usr/bin/env python3
"""Power of the PREREG_extra_084.md decision rules from the testbed's own per-episode spread (release 084, planning only).

As for the 077 and 083 plans (power_review077_testbed.py, power_extra083_testbed.py), the simulator's per-episode
differences are bimodal and more spread than the testbed's, so its power (sim_predictions/extra084_power.csv) is a
bound. This script takes the spread from the testbed runs closest to each new run: per-episode clean fulfilment in pp
of offered, from the final report of release 081 (run review-20261030T182927, report/pairs/PREREG_review_077/cells.csv
and report/pairs/PREREG_expS_072/cells.csv), centred, plus a true mean; each rule tested as pairs_ext083.py does
(Student t, TOST), Holm within its family with the other members at their simulator means, alpha 0.05; 4,000
resamples, seed 84. Templates:

  s6-gap        S (expS-w6) K0F -> K0S, episodes 0-7 (the runs FIs6 replays), SD 8.1: FI-1, FI-2 (the fence arm's
                spread under R5, the only fence-versus-stop spread on these schedules: conservative for R5')
  si-gap        Si (expS-winf) K0F -> K0S, episodes 0-3, SD 0.9: FI-3 (n 4) and the near-null sensitivity of FI-2
  fw-cross      C2 -> Rz ZEPHYR_CUTOFF / EVICON (one policy, two runs, the same schedules: run-to-run spread), plus
                Wqi K0F RES_PERIODIC -> EVICON (two resolvers on the same schedules: policy spread), per episode,
                SD 1.1: PF-1, PF-2 (cross-run, PERIODIC_FULL against the reference run's EVICON); x3
  minio-cross   C -> Rm MINIO_COND / EVICON plus the same policy spread, SD 1.3: PF-3, PF-4; x3

Writes nothing; prints two markdown tables (the plan's section 5 quotes them): the rules' power at the simulator's
mean and nearby means, and the plan's 0.80 rule applied to every paired-difference rule of the draft.

    python3 evaluation/power_extra084_testbed.py
"""
from __future__ import annotations

import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pairs_report import holm, one_sided  # noqa: E402  (frozen; reused, not edited)

# per-episode clean fulfilment, % of offered (081 final report, cells.csv)
S6_K0F = [69.58, 56.82, 54.61, 51.22, 67.65, 67.92, 58.01, 44.05, 59.14, 58.35, 35.27, 54.91]
S6_K0S = [85.14, 79.9, 83.73, 78.75, 78.2, 78.39, 76.8, 74.54, 72.49, 71.14, 79.27, 72.47]
SI_K0F = [81.99, 77.45, 81.37, 77.7, 76.64, 77.7, 75.0, 72.86, 71.83, 68.95, 74.18, 70.25]
SI_K0S = [84.97, 79.72, 83.73, 78.57, 78.2, 78.39, 76.8, 74.37, 72.65, 71.14, 79.45, 72.63]
C2_CUT = [85.14, 79.2, 83.73, 78.57, 78.2, 78.39, 76.8, 74.37]
RZ_CUT = [84.79, 79.72, 83.73, 78.57, 78.03, 78.22, 76.8, 74.37]
C_COND = [72.06, 77.92, 78.27, 78.6, 76.58, 74.88, 75.32, 75.16]
RM_COND = [72.38, 77.76, 78.44, 78.77, 76.58, 75.37, 75.16, 75.16]
WQI_K0F_EV = [84.27, 77.97, 82.46, 77.35, 76.64, 78.22, 75.98, 73.37, 72.65, 70.05, 75.27, 70.89]
WQI_K0F_RP = [80.77, 77.62, 82.64, 76.48, 75.09, 77.87, 75.65, 73.37, 70.02, 68.17, 76.0, 64.87]

# the simulator's means (sim_predictions/extra084_power.csv, the calibrated twin for (a))
SIM = {"FI-1": 22.22, "FI-2": 0.16, "FI-3": 1.29, "PF-1": -0.01, "PF-2": -0.01, "PF-3": 0.0, "PF-4": 0.02}


def centred(d):
    m = statistics.mean(d)
    return [v - m for v in d]


def diff(a, b, n=None):
    d = [y - x for x, y in zip(a, b)]
    return d[:n] if n else d


def combine(*ds):
    return [sum(x) for x in zip(*ds)]


TEMPLATES = {
    "s6-gap": centred(diff(S6_K0F, S6_K0S, 8)),
    "si-gap": centred(diff(SI_K0F, SI_K0S, 4)),
    "fw-cross": centred(combine(centred(diff(C2_CUT, RZ_CUT)), centred(diff(WQI_K0F_RP, WQI_K0F_EV, 8)))),
    "minio-cross": centred(combine(centred(diff(C_COND, RM_COND)), centred(diff(WQI_K0F_RP, WQI_K0F_EV, 8)))),
}
# id: family, template, test, margin, n
RULES = {
    "FI-1": ("fi-w6", "s6-gap", "at_least", 10.0, 8),
    "FI-2": ("fi-w6", "s6-gap", "equivalent", 5.0, 8),
    "FI-3": ("fi-winf", "si-gap", "at_least", 0.0, 4),
    "PF-1": ("pf-fw", "fw-cross", "equivalent", 3.0, 8),
    "PF-2": ("pf-fw", "fw-cross", "equivalent", 3.0, 8),
    "PF-3": ("pf-minio", "minio-cross", "equivalent", 3.0, 8),
    "PF-4": ("pf-minio", "minio-cross", "equivalent", 3.0, 8),
}


def p_value(d, test, m):
    if test == "at_least":
        return one_sided(d, m, upper=False)
    if test == "at_most":
        return one_sided(d, m, upper=True)
    return max(one_sided(d, -m, upper=False), one_sided(d, m, upper=True))


def power(rule: str, mean: float, scale: float = 1.0, template: str = "", rules=RULES, resamples=4000) -> float:
    fam = rules[rule][0]
    members = [r for r in rules if rules[r][0] == fam]
    rng = random.Random(84)
    hits = 0
    for _ in range(resamples):
        ps = {}
        for r in members:
            _, tpl, test, margin, n = rules[r]
            tv = TEMPLATES[template or tpl] if r == rule else TEMPLATES[tpl]
            mu = mean if r == rule else SIM[r]
            sc = scale if r == rule else 1.0
            ps[r] = p_value([mu + sc * tv[rng.randrange(len(tv))] for _ in range(n)], test, margin)
        hits += holm(ps).get(rule, 1.0) < 0.05
    return hits / resamples


def main():
    sd = {k: statistics.stdev(v) for k, v in TEMPLATES.items()}
    print("Templates (SD of the centred per-episode differences, pp): " + ", ".join(f"{k} {v:.1f}" for k, v in sd.items()))
    print()
    print("The 0.80 rule applied to the plan as drafted (power at the simulator's mean under the primary template, Holm in the draft's families):")
    print()
    print("| Rule as drafted | Predicted mean | Power | Decision |")
    print("|---|---|---|---|")
    kept = {}
    for r, (fam, tpl, test, margin, n) in RULES.items():
        p = power(r, SIM[r])
        if p >= 0.80:
            kept[r] = RULES[r]
        print(f"| {r} (`{test} {margin:g}`) | {SIM[r]:+.2f} | {p:.2f} | {'hypothesis' if p >= 0.80 else 'estimand'} |")
    print()
    print("The hypotheses kept (Holm within the kept families), at the simulator's mean and nearby means:")
    print()
    print("| Rule | Template (SD, pp) | True mean -> power |")
    print("|---|---|---|")
    for r, (fam, tpl, test, margin, n) in kept.items():
        pts = [SIM[r]] + ([5.0, 10.0, 15.0] if test == "at_least" and margin > 0 else [0.0, 1.0, 2.0])
        cells = [f"{m:+.2f}{' (simulator)' if i == 0 else ''}: {power(r, m, rules=kept):.2f}" for i, m in enumerate(pts)]
        extra = f"; x3: {power(r, SIM[r], 3.0, rules=kept):.2f} at {SIM[r]:+.2f}" if test == "equivalent" else f"; x2: {power(r, SIM[r], 2.0, rules=kept):.2f} at {SIM[r]:+.2f}"
        print(f"| {r} (`{test} {margin:g}`, n {n}) | {tpl} ({sd[tpl]:.1f}) | " + ", ".join(cells) + extra + " |")
    print()
    print("Sensitivity of the estimand FI-2 (`equivalent 5`) to its template: s6-gap "
          f"{power('FI-2', SIM['FI-2']):.2f}, si-gap (a near-null fence/stop pair) {power('FI-2', SIM['FI-2'], template='si-gap'):.2f}")


if __name__ == "__main__":
    main()
