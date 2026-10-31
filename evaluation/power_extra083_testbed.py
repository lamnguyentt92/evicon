#!/usr/bin/env python3
"""Power of the PREREG_extra_083.md decision rules from the testbed's own per-episode spread (release 083,
planning only).

As for the 077 plan (power_review077_testbed.py), the simulator's per-episode differences are bimodal and more
spread than the testbed's, so its power (sim_predictions/extra083_power.csv) is a bound. This script takes the
spread from the testbed runs closest to each new run: per-episode clean (or confirmed) fulfilment in pp of
offered, from the final report of release 081 (run review-20261030T182927, report/pairs/PREREG_review_077/
cells.csv), centred, plus a true mean; each rule tested as pairs_report.py / pairs_ext083.py do (Student t, TOST),
Holm within its family with the other members at their simulator means, alpha 0.05; 4,000 resamples, seed 83.
Templates:

  tb-gap        TB (expTB-thingsboard) M1 -> EVICON clean, SD 12.5: T1-2 (M1 -> M1W on ThingsBoard)
  tb-null       TB NAIVE -> EVICON clean (two policies at similar levels on the same schedules), SD 3.6: T1-4; x2
  k2-trigger    K2 (expK2-w6) RES_PERIODIC -> EVICON on K2 (no query -> query on a full window), SD 4.8: T1-1
  resolver-null Wqi (expW-queue-winf) K0F RES_PERIODIC -> EVICON clean, SD 1.2: T1-3, T5-1..T5-4; x3
  fw-w6         C2 ZEPHYR_PLAIN -> ZEPHYR_CUTOFF clean (SD 11.3) and confirmed (SD 11.8): T4-1, T4-2
  fw-dir-w6     C3 ZEPHYR_PLAIN_DIRECT -> ZEPHYR_F_DIRECT clean, SD 9.5: T4-3
  fw-dir-inf    Rz ZEPHYR_PLAIN_DIRECT -> ZEPHYR_F_DIRECT clean, SD 7.6: T4-4
  fw-cut-inf    Rz ZEPHYR_PLAIN_DIRECT -> ZEPHYR_CUTOFF clean (QoS 0 makes the queue channel behave as the direct one): T4-6
  fw-conf-inf   Rz ZEPHYR_PLAIN_DIRECT -> ZEPHYR_CUTOFF confirmed, SD 7.2: T4-5
  fw-did        per episode (C2 cutoff - C3 plain direct) - (Rz cutoff - Rz plain direct): the firmware's effect at W = 6
                minus at W unbounded (the plain firmware at QoS 0 behaves as on the direct channel), SD 3.6: T6-1, T6-2
  tb-did        tb-null times sqrt 2 (SD 5.0): T6-3
  s6-fence, s6-stop  S (expS-w6) K0 -> K0F (SD 12.0) and K0 -> K0S (SD 9.3): T7 queue rules
  dinf-fence    Wdi (expW-direct-winf) K0 -> K0F, SD 9.3: T7 direct rules

Writes nothing; prints two markdown tables (the plan's section 5 quotes them): the frozen rules' power, and the plan's
0.80 rule applied to every paired-difference rule of the draft (power at the predicted mean; below 0.80: an estimand).

    python3 evaluation/power_extra083_testbed.py
"""
from __future__ import annotations

import math
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pairs_report import holm, one_sided  # noqa: E402  (frozen; reused, not edited)

TB_EV = [70.95, 75.95, 73.17, 76.88, 74.87, 74.22]
TB_M1 = [40.0, 27.35, 30.22, 9.76, 23.59, 15.76]
TB_NAIVE = [66.51, 68.7, 71.48, 65.41, 70.43, 65.19]
K2_EV = [81.42, 80.86, 77.4, 77.48, 73.8]
K2_RP = [76.86, 77.24, 65.58, 77.48, 73.8]
WQI_K0F_EV = [84.27, 77.97, 82.46, 77.35, 76.64, 78.22, 75.98, 73.37]
WQI_K0F_RP = [80.77, 77.62, 82.64, 76.48, 75.09, 77.87, 75.65, 73.37]
C2_PLAIN = [23.95, 48.25, 36.71, 12.02, 24.39, 36.88, 25.65, 30.32]
C2_CUT = [85.14, 79.2, 83.73, 78.57, 78.2, 78.39, 76.8, 74.37]
C2_PLAIN_C = [19.93, 46.15, 32.73, 8.19, 20.42, 32.76, 22.55, 27.14]
C2_CUT_C = [84.97, 78.85, 83.73, 78.57, 78.03, 78.04, 76.63, 74.2]
C3_PD = [20.28, 36.71, 33.09, 8.71, 20.42, 33.28, 22.88, 27.64]
C3_FD = [85.14, 79.72, 83.36, 77.87, 78.03, 78.22, 76.31, 74.2]
RZ_PD = [59.27, 70.98, 68.72, 48.08, 59.69, 66.21, 53.1, 59.8]
RZ_FD = [84.97, 79.72, 83.73, 79.09, 77.85, 78.04, 76.8, 74.2]
RZ_CUT = [84.79, 79.72, 83.73, 78.57, 78.03, 78.22, 76.8, 74.37]
RZ_PD_C = [57.17, 68.88, 65.82, 46.86, 58.13, 63.81, 51.96, 58.29]
RZ_CUT_C = [84.79, 79.55, 83.73, 78.57, 78.03, 78.04, 76.8, 74.2]
S6_K0 = [24.83, 37.76, 35.62, 12.02, 25.43, 36.88, 18.14, 29.31]
S6_K0F = [69.58, 56.82, 54.61, 51.22, 67.65, 67.92, 58.01, 44.05]
S6_K0S = [85.14, 79.9, 83.73, 78.75, 78.2, 78.39, 76.8, 74.54]
WDI_K0 = [56.47, 70.63, 68.35, 38.85, 56.92, 63.64, 47.88, 57.62]
WDI_K0F = [80.42, 75.17, 76.49, 70.21, 76.47, 73.93, 67.16, 66.0]


def centred(a, b):
    d = [y - x for x, y in zip(a, b)]
    m = statistics.mean(d)
    return [v - m for v in d]


def did(a1, b1, a2, b2):
    d = [(y1 - x1) - (y2 - x2) for x1, y1, x2, y2 in zip(a1, b1, a2, b2)]
    m = statistics.mean(d)
    return [v - m for v in d]


TEMPLATES = {"tb-gap": centred(TB_M1, TB_EV), "tb-null": centred(TB_NAIVE, TB_EV), "k2-trigger": centred(K2_RP, K2_EV),
             "resolver-null": centred(WQI_K0F_RP, WQI_K0F_EV), "fw-w6": centred(C2_PLAIN, C2_CUT), "fw-w6-c": centred(C2_PLAIN_C, C2_CUT_C),
             "fw-dir-w6": centred(C3_PD, C3_FD), "fw-dir-inf": centred(RZ_PD, RZ_FD), "fw-conf-inf": centred(RZ_PD_C, RZ_CUT_C),
             "fw-did": did(C3_PD, C2_CUT, RZ_PD, RZ_CUT), "s6-fence": centred(S6_K0, S6_K0F), "s6-stop": centred(S6_K0, S6_K0S),
             "dinf-fence": centred(WDI_K0, WDI_K0F), "fw-cut-inf": centred(RZ_PD, RZ_CUT)}
TEMPLATES["tb-null x2"] = [2 * v for v in TEMPLATES["tb-null"]]
TEMPLATES["tb-did"] = [math.sqrt(2) * v for v in TEMPLATES["tb-null"]]
TEMPLATES["resolver-null x3"] = [3 * v for v in TEMPLATES["resolver-null"]]

# The plan's 0.80 rule (section 3, release 083 before freezing): a paired-difference rule is a confirmatory hypothesis only
# if its power under the testbed's own spread, at the predicted (simulator) mean, with Holm in its family, is >= 0.80;
# otherwise it is an estimand. DRAFT is the plan as first written; FAMILIES is the plan as frozen.
# family -> [(id, test, margin, n, template, simulator mean)]   (sim_predictions/extra083_power.csv; T7 from extra083_effects.csv)
DRAFT = {
    "trigger-k2": [("T1-1", "at_least", 0.0, 8, "k2-trigger", 0.44), ("T1-3", "equivalent", 3.0, 8, "resolver-null", 0.0)],
    "trigger-tb": [("T1-2", "at_least", 10.0, 6, "tb-gap", 35.77), ("T1-4", "equivalent", 5.0, 6, "tb-null", -0.74)],
    "binding": [("T4-1", "at_least", 10.0, 8, "fw-w6", 43.02), ("T4-2", "at_least", 10.0, 8, "fw-w6-c", 43.8),
                ("T4-3", "at_least", 10.0, 8, "fw-dir-w6", 43.09), ("T4-4", "at_least", 5.0, 8, "fw-dir-inf", 14.6),
                ("T4-5", "at_least", 5.0, 8, "fw-conf-inf", 16.01), ("T4-6", "equivalent", 3.0, 8, "fw-dir-inf", 14.53)],
    "greedy": [("T5-1", "at_least", -2.0, 8, "resolver-null", 0.0), ("T5-2", "at_least", -2.0, 8, "resolver-null", 0.0),
               ("T5-3", "at_least", -2.0, 8, "resolver-null", 0.0)],
    "theta": [("T5-4", "equivalent", 5.0, 8, "resolver-null", -1.78)],
    "window": [("T6-1", "at_most", 0.0, 8, "fw-did", -32.27), ("T6-2", "at_most", 0.0, 8, "fw-did", -28.49), ("T6-3", "at_most", 5.0, 6, "tb-did", 0.23)],
}
FAMILIES = {
    "trigger-k2": [("T1-3", "equivalent", 3.0, 8, "resolver-null", 0.0)],
    "trigger-tb": [("T1-2", "at_least", 10.0, 6, "tb-gap", 35.77), ("T1-4", "equivalent", 5.0, 6, "tb-null", -0.74)],
    "binding": [("T4-1", "at_least", 10.0, 8, "fw-w6", 43.02), ("T4-2", "at_least", 10.0, 8, "fw-w6-c", 43.8),
                ("T4-3", "at_least", 10.0, 8, "fw-dir-w6", 43.09), ("T4-4", "at_least", 5.0, 8, "fw-dir-inf", 14.6),
                ("T4-5", "at_least", 5.0, 8, "fw-conf-inf", 16.01), ("T4-6", "at_least", 5.0, 8, "fw-cut-inf", 14.53)],
    "greedy": [("T5-1", "at_least", -2.0, 8, "resolver-null", 0.0), ("T5-2", "at_least", -2.0, 8, "resolver-null", 0.0),
               ("T5-3", "at_least", -2.0, 8, "resolver-null", 0.0)],
    "theta": [("T5-4", "equivalent", 5.0, 8, "resolver-null", -1.78)],
    "window": [("T6-1", "at_most", 0.0, 8, "fw-did", -32.27), ("T6-2", "at_most", 0.0, 8, "fw-did", -28.49)],
}
SCAN = {"T1-1": (0.44, 2, 4, 6), "T1-2": (15, 20, 35.77, 50), "T1-3": (0, 1, 2), "T1-4": (-0.74, 0, 1, 2, 3), "T4-1": (15, 20, 43.02, 50),
        "T4-2": (15, 20, 43.8, 50), "T4-3": (15, 20, 43.09, 50), "T4-4": (8, 10, 14.6, 18), "T4-5": (8, 10, 16.01, 20),
        "T4-6": (8, 10, 14.53, 18), "T5-1": (-1, 0, 1), "T5-2": (-1, 0, 1), "T5-3": (-1, 0, 1), "T5-4": (-3, -1.78, 0, 1),
        "T6-1": (-5, -10, -20, -32.27), "T6-2": (-5, -10, -20, -28.49), "T6-3": (0, 0.23, 2, 3)}
T7_ESTIMANDS = {"T7-rate05-qF", "T7-rate05-qS", "T7-rate05-dF", "T7-off5-dF"}


def p_value(d, test, m):
    if test == "at_least":
        return one_sided(d, m, upper=False)
    if test == "at_most":
        return one_sided(d, m, upper=True)
    return max(one_sided(d, -m, upper=False), one_sided(d, m, upper=True))


def power(rules, target: str, mean: float, template: str | None = None, resamples: int = 4000) -> float:
    rng = random.Random(83)
    met = 0
    for _ in range(resamples):
        ps = {}
        for rid, test, m, n, tpl, sim in rules:
            t = TEMPLATES[template if (rid == target and template) else tpl]
            mu = mean if rid == target else sim
            ps[rid] = p_value([mu + t[rng.randrange(len(t))] for _ in range(n)], test, m)
        met += holm(ps)[target] < 0.05
    return met / resamples


def t7_family(level: str, n: int, means: tuple, final: bool = False) -> list:
    q_f, q_s, d_f = means
    rules = [(f"T7-{level}-qF", "at_least", 0.0, n, "s6-fence", q_f), (f"T7-{level}-qS", "at_least", 0.0, n, "s6-stop", q_s),
             (f"T7-{level}-dF", "at_least", 0.0, n, "dinf-fence", d_f)]
    return [r for r in rules if not (final and r[0] in T7_ESTIMANDS)]


# the simulator's mean effects per T7 level (sim_predictions/extra083_effects.csv): queue K0 -> K0F, K0 -> K0S, direct K0 -> K0F
T7_SIM = {"rate05": (17.65, 17.8, 4.58), "rate4": (47.1, 58.91, 23.72), "off5": (33.2, 37.1, 6.99), "off40": (41.14, 41.73, 26.89),
          "seedA": (40.12, 44.95, 14.66), "seedB": (40.12, 44.95, 14.66)}


def main():
    print("Rules as frozen (FAMILIES; T7 with its estimands removed): power under the testbed's spread, Holm in the family")
    print()
    print("| family | rule | test | n | template (SD) | true mean -> power |")
    print("|---|---|---|---|---|---|")
    for fam, rules in FAMILIES.items():
        for rid, test, m, n, tpl, sim in rules:
            tpls = [tpl] + [f"{tpl} x{k}" for k in (2, 3) if f"{tpl} x{k}" in TEMPLATES]
            for t in tpls:
                sd = statistics.stdev(TEMPLATES[t])
                cells = ", ".join(f"{mu:g}: {power(rules, rid, mu, t):.2f}" for mu in SCAN[rid])
                print(f"| {fam} | {rid} | {test} {m:g} | {n} | {t} ({sd:.2f}) | {cells} |")
    for level, means in T7_SIM.items():
        rules = t7_family(level, 4, means, final=True)
        if rules:
            cells = "; ".join(f"{rid.split('-')[-1]}: " + ", ".join(f"{mu:g}: {power(rules, rid, mu):.2f}" for mu in (5, 10, sim))
                              for rid, _, _, _, _, sim in rules)
            print(f"| t7-{level} | {', '.join(r[0] for r in rules)} | at_least 0 | 4 | s6-fence/s6-stop/dinf-fence | {cells} |")
    print()
    print("The 0.80 rule: every paired-difference rule of the draft, its power at the predicted mean (draft families), the decision,")
    print("and the power of the hypotheses kept in the frozen families")
    print()
    print("| rule | test | predicted mean | power (draft) | decision | power (frozen) |")
    print("|---|---|---|---|---|---|")
    final = {r[0]: (r, rules) for rules in FAMILIES.values() for r in rules}
    for level, means in T7_SIM.items():
        fr = t7_family(level, 4, means, final=True)
        final.update({r[0]: (r, fr) for r in fr})
    draft = [(r, rules) for rules in DRAFT.values() for r in rules]
    for level, means in T7_SIM.items():
        dr = t7_family(level, 4, means)
        draft += [(r, dr) for r in dr]
    for (rid, test, m, n, tpl, sim), rules in draft:
        pd = power(rules, rid, sim)
        if rid in final:
            fr, frules = final[rid]
            pf = power(frules, rid, fr[5])
            changed = "" if fr[1:3] == (test, m) else f" (rule now {fr[1]} {fr[2]:g}, template {fr[4]})"
            dec = ("hypothesis" if pf >= 0.80 else "ESTIMAND") + changed
            print(f"| {rid} | {test} {m:g} | {sim:+g} | {pd:.2f} | {dec} | {pf:.2f} |")
        else:
            print(f"| {rid} | {test} {m:g} | {sim:+g} | {pd:.2f} | estimand | - |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
