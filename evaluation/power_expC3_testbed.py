#!/usr/bin/env python3
"""Power of the C3 decision rules from the testbed's own per-episode spread (release 075, planning only).

The simulator's per-episode differences are bimodal (in some episodes no device locks up) and far more
spread than the testbed's, so its power (sim_predictions/expC3_075_power.csv) is a lower bound. This
script takes the spread from the real IoT stack instead: the per-episode EVICON clean fulfilment (pp of
offered) of experiment C2 (N6, run expC2-zephyr/20261024T044342, release 074, the same seed and
schedules as C3), centred, and asks how often the C3 rules are MET at n episodes for a range of true
mean effects. Templates:

  large   ZEPHYR_PLAIN -> ZEPHYR_CUTOFF (the plain mode against a contract with evidence of
          non-occurrence; C3-1 to C3-4 have this shape), SD 11.3 pp
  null    ZEPHYR_CUTOFF -> ZEPHYR_STOP (two contracts that both reach the ceiling; C3-5, C3-6), SD 0.3 pp
  null x10  the null template scaled by 10 (SD 2.9 pp): C3-5 and C3-6 compare contracts without Q_A,
          whose per-episode differences may spread more than those of C2's two contracts with Q_A

Resampling: n episodes drawn with replacement from the centred template, plus the true mean; C3-1 to
C3-6 tested as pairs_report.py does (Student t, TOST), Holm over the six, alpha 0.05; the other five
hypotheses at the simulator's means (sim_predictions/expC3_075_power.csv, owner_up). 4,000 resamples,
seed 75. Writes nothing; prints a markdown table.

    python3 evaluation/power_expC3_testbed.py
"""
from __future__ import annotations

import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pairs_report import holm, one_sided  # noqa: E402  (frozen; reused, not edited)

# N6, expC2-zephyr/20261024T044342, EVICON, episodes 0-7, clean_fulfilled / offered in pp
C2_PLAIN = [24.0, 48.3, 36.7, 12.0, 24.4, 36.9, 25.7, 30.3]
C2_CUTOFF = [85.1, 79.2, 83.7, 78.6, 78.2, 78.4, 76.8, 74.4]
C2_STOP = [85.1, 79.7, 83.7, 78.4, 78.2, 78.6, 76.3, 74.4]

# (id, test, margin, template, simulator mean, owner_up)
RULES = [("C3-1", "at_least", 10.0, "large", 43.09), ("C3-2", "at_least", 10.0, "large", 39.88),
         ("C3-3", "at_least", 10.0, "large", 39.99), ("C3-4", "at_least", 10.0, "large", 38.43),
         ("C3-5", "equivalent", 5.0, "null", 0.10), ("C3-6", "equivalent", 5.0, "null", -0.12)]


def centred(x):
    m = statistics.mean(x)
    return [v - m for v in x]


TEMPLATES = {"large": centred([b - a for a, b in zip(C2_PLAIN, C2_CUTOFF)]), "null": centred([b - a for a, b in zip(C2_CUTOFF, C2_STOP)])}
TEMPLATES["null x10"] = [10 * v for v in TEMPLATES["null"]]


def p_value(d, test, m):
    if test == "at_least":
        return one_sided(d, m, upper=False)
    return max(one_sided(d, -m, upper=False), one_sided(d, m, upper=True))


def power(target: str, mean: float, n: int, resamples: int = 4000, template: str | None = None) -> float:
    rng = random.Random(75)
    met = 0
    for _ in range(resamples):
        ps = {}
        for hid, test, margin, tpl, sim_mean in RULES:
            mu = mean if hid == target else sim_mean
            t = TEMPLATES[template if (template and hid == target) else tpl]
            ps[hid] = p_value([mu + t[rng.randrange(len(t))] for _ in range(n)], test, margin)
        met += holm(ps).get(target, 1.0) < 0.05
    return met / resamples


def main():
    print("template SD: " + ", ".join(f"{k} {statistics.stdev(v):.2f} pp" for k, v in TEMPLATES.items()))
    print("\n| rule | template | true mean effect (pp) | n = 6 | n = 8 | n = 10 |\n|---|---|---|---|---|---|")
    for mean in (15.0, 17.5, 20.0, 25.0, 30.0, 40.0):
        print(f"| C3-2 at_least 10 | large | {mean:+.1f} | " + " | ".join(f"{power('C3-2', mean, n):.2f}" for n in (6, 8, 10)) + " |")
    for tpl in ("null", "null x10"):
        for mean in (0.0, 2.0, 3.0, 4.0):
            print(f"| C3-6 equivalent 5 | {tpl} | {mean:+.1f} | " + " | ".join(f"{power('C3-6', mean, n, template=tpl):.2f}" for n in (6, 8, 10)) + " |")


if __name__ == "__main__":
    main()
