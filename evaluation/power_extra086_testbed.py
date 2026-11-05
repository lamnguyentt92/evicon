#!/usr/bin/env python3
"""Power of the second-seed replication rules SR-1 to SR-4 under the testbed's own spread (release 086,
PREREG_extra_086.md section 5; the 0.80 rule of plans 083-085).

Templates (`sim_predictions/extra086_templates.csv`, copied from the 085 final report's report/pairs/<plan>/cells.csv
with the SHA-256 of each source): the per-episode differences, clean fulfilment in pp, of the original runs' contrasts,
EVICON, episodes 0-7:
  within    the within-run spread of the original owner: SR-1 C (expC-minio, MINIO_PLAIN -> MINIO_COND), SR-2 Rm
            (expR-minio-winf, the same), SR-3 C3 (expC3-ablation, ZEPHYR_PLAIN_DIRECT -> ZEPHYR_F_DIRECT, plain at QoS 1),
            SR-4 Rz (expR-zephyr-winf, the same): the primary template (the SR runs are within-run contrasts);
  original  the original headline contrast itself (MinIO: the same as within; firmware: T4-3 = Z6 plain at QoS 0 ->
            C3 {F}, T4-4 = Zi -> Rz, across runs paired by schedule): the sensitivity template.
Each resample draws n per-episode differences d = mu + (x_j - mean(x)) * k from a template x (with replacement), at the
true mean mu (the prediction: the original estimate; sensitivity 0.75 and 0.6 times it) and a spread factor k (1;
sensitivity 1.5, 2); the rule is decided as pairs_report.py decides it (at_least m: one-sided t, p = 1 - T((mean - m) /
se), Holm within the family, the other member of the family drawn at its own prediction from its own template; MET when
the Holm-adjusted p < 0.05). Two margin rules are evaluated: the plan's rule (m = half the original estimate) and the
stricter alternative (m = the original's lower 95 % t bound), so the plan can state why it chose the first.

    python3 evaluation/power_extra086_testbed.py [--resamples 4000] [--seed 86] [--out sim_predictions/extra086_power.csv]
"""
from __future__ import annotations

import argparse
import csv
import random
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pairs_report as P  # noqa: E402  (frozen: the decision rule)

FAMILY = {"SR-1": "sr-minio", "SR-2": "sr-minio", "SR-3": "sr-fw", "SR-4": "sr-fw"}
# the original estimates and 95 % t intervals of the 085 final report (claims/CLAIMS.md): C3 (073), U-1 (077), T4-3, T4-4 (083)
ORIGINAL = {"SR-1": ("C3", 53.40, 43.45), "SR-2": ("U-1", 16.50, 11.83), "SR-3": ("T4-3", 53.65, 45.71), "SR-4": ("T4-4", 18.59, 12.23)}
MARGIN_HALF = {"SR-1": 26.70, "SR-2": 8.25, "SR-3": 26.83, "SR-4": 9.30}  # half the original estimate, rounded up to 0.01
MARGIN_LOWER = {k: v[2] for k, v in ORIGINAL.items()}


def templates(path: Path) -> dict:
    t = {}
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            t.setdefault((r["rule"], r["template"]), []).append(float(r["diff_pp"]))
    return t


def draw(rng, x, mu, k, n):
    m = statistics.mean(x)
    return [mu + (rng.choice(x) - m) * k for _ in range(n)]


def power(tpl: dict, rule: str, template: str, mu_f: float, k: float, n: int, margins: dict, resamples: int, seed: int) -> float:
    rng = random.Random(f"{seed}/{rule}/{template}/{mu_f}/{k}/{n}/{margins[rule]}")
    other = [r for r in FAMILY if FAMILY[r] == FAMILY[rule] and r != rule][0]
    hits = 0
    for _ in range(resamples):
        d = draw(rng, tpl[(rule, template)], ORIGINAL[rule][1] * mu_f, k, n)
        d2 = draw(rng, tpl[(other, template)], ORIGINAL[other][1], 1.0, n)  # the other member at its prediction
        ps = {rule: P.one_sided(d, margins[rule], upper=False), other: P.one_sided(d2, margins[other], upper=False)}
        if P.holm(ps)[rule] < 0.05:
            hits += 1
    return hits / resamples


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--templates", default=str(HERE / "sim_predictions" / "extra086_templates.csv"))
    ap.add_argument("--resamples", type=int, default=4000)
    ap.add_argument("--seed", type=int, default=86)
    ap.add_argument("--out", default=str(HERE / "sim_predictions" / "extra086_power.csv"))
    a = ap.parse_args(argv)
    tpl = templates(Path(a.templates))
    rows = []
    for rule in ("SR-1", "SR-2", "SR-3", "SR-4"):
        for template in ("within", "original"):
            x = tpl[(rule, template)]
            for name, margins in (("half", MARGIN_HALF), ("lower95", MARGIN_LOWER)):
                for mu_f, k, n in ((1.0, 1.0, 8), (0.75, 1.0, 8), (0.6, 1.0, 8), (1.0, 1.5, 8), (1.0, 2.0, 8), (1.0, 1.0, 6), (1.0, 1.0, 10)):
                    if name == "lower95" and (mu_f, k) != (1.0, 1.0):
                        continue
                    pw = power(tpl, rule, template, mu_f, k, n, margins, a.resamples, a.seed)
                    rows.append({"rule": rule, "family": FAMILY[rule], "original": ORIGINAL[rule][0], "original_estimate": ORIGINAL[rule][1],
                                 "margin_rule": name, "margin": margins[rule], "template": template, "template_sd": round(statistics.stdev(x), 3),
                                 "true_mean": round(ORIGINAL[rule][1] * mu_f, 3), "spread_factor": k, "n": n, "power": round(pw, 4)})
    out = Path(a.out)
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    for r in rows:
        if r["n"] == 8 and r["spread_factor"] == 1.0 and r["true_mean"] == r["original_estimate"]:
            print(f"{r['rule']} {r['margin_rule']:8s} m={r['margin']:6.2f} template {r['template']:8s} SD {r['template_sd']:6.2f}: power {r['power']:.3f}")
    print(f"power_extra086_testbed: {len(rows)} rows -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
