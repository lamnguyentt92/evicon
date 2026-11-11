#!/usr/bin/env python3
"""Power of the paired rules of release 087 under the testbed's own spread (PREREG_extra_087.md section 5; the 0.80 rule of
plans 083-086).

Templates (`sim_predictions/extra087_templates.csv`, copied from the 086 final report's report/pairs/<plan>/cells.csv with
the SHA-256 of each source; `--make-templates <086 report>` writes the file): per-episode differences, clean fulfilment in
pp, episodes 0-7:
  pm_fw      within run, two guarded policies on the cutoff firmware at W = 6 (plan 085, run PMz6: ZEPHYR_CUTOFF,
             EVICON -> PERIODIC_FULL): the primary template of SHR-1 and SHR-2 (the 087 arms are two policies on one
             owner and one schedule set)
  pm_minio   the same on MinIO's conditional PUT (PMm6, MINIO_COND): the primary template of SHR-3 and SHR-4
  shd        the documented shadow loop against EVICON on K2 with crash orphans (plan 086, run SHd): the pessimistic
             template (the loop's re-publishes past the resend cut made per-episode differences far more variable)
  srz6       within run on the firmware at W = 6, the plain firmware at QoS 0 -> {F} (plan 086, run SRz6, EVICON): the
             template of RB-1 (a large within-run effect on the same schedules)
Each resample draws n differences d = mu + (x_j - mean(x)) * k from a template x (with replacement), at the true mean mu
and a spread factor k, and decides the rule as pairs_report.py decides it: equivalent m (TOST: p = max(p(mean <= -m),
p(mean >= m)), Holm within the family with the other member drawn at its own prediction from the same template) or
at_least m (one-sided t; RB-1 is alone in its family). MET when the (Holm-adjusted) p < 0.05.

    python3 evaluation/power_extra087_testbed.py [--resamples 4000] [--seed 87] [--out sim_predictions/extra087_power.csv]
    python3 evaluation/power_extra087_testbed.py --make-templates <086 final report dir>
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import random
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pairs_report as P  # noqa: E402  (frozen: the decision rule)

# rule -> (family, template of the primary analysis, predicted mean in pp, test, margin)
RULES = {"SHR-1": ("shr-fw", "pm_fw", -1.0, "equivalent", 3.0), "SHR-2": ("shr-fw", "pm_fw", 0.0, "equivalent", 3.0),
         "SHR-3": ("shr-minio", "pm_minio", -1.0, "equivalent", 3.0), "SHR-4": ("shr-minio", "pm_minio", 0.0, "equivalent", 3.0),
         "RB-1": ("rb", "srz6", 30.0, "at_least", 15.0)}
SOURCES = {"pm_fw": ("PREREG_extra_085", "PMz6", "ZEPHYR_CUTOFF", "EVICON", "ZEPHYR_CUTOFF", "PERIODIC_FULL"),
           "pm_minio": ("PREREG_extra_085", "PMm6", "MINIO_COND", "EVICON", "MINIO_COND", "PERIODIC_FULL"),
           "shd": ("PREREG_extra_086", "SHd", "K2", "EVICON", "K2", "SHADOW_RECONCILE"),
           "srz6": ("PREREG_extra_086", "SRz6", "ZEPHYR_PLAIN_DIRECT_QOS0", "EVICON", "ZEPHYR_F_DIRECT", "EVICON")}


def make_templates(report: Path, out: Path):
    rows = []
    for name, (plan, run, ca, pa, cb, pb) in SOURCES.items():
        src = report / "pairs" / plan / "cells.csv"
        sha = hashlib.sha256(src.read_bytes()).hexdigest()[:16]
        by = {}
        with open(src, newline="") as fh:
            for r in csv.DictReader(fh):
                if r["run"] == run and r.get("cell_status", "VALID").startswith("VALID"):
                    by[(r["contract"], r["policy"], int(r["episode"]))] = float(r["clean_pp"])
        for e in sorted({k[2] for k in by}):
            if (ca, pa, e) in by and (cb, pb, e) in by:
                rows.append({"template": name, "plan": plan, "run": run, "from": f"{ca}/{pa}", "to": f"{cb}/{pb}", "episode": e,
                             "from_clean_pp": by[(ca, pa, e)], "to_clean_pp": by[(cb, pb, e)],
                             "diff_pp": round(by[(cb, pb, e)] - by[(ca, pa, e)], 4), "source": f"report/pairs/{plan}/cells.csv",
                             "source_sha256_16": sha})
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"templates: {len(rows)} rows -> {out}")


def templates(path: Path) -> dict:
    t = {}
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            t.setdefault(r["template"], []).append(float(r["diff_pp"]))
    return t


def draw(rng, x, mu, k, n):
    m = statistics.mean(x)
    return [mu + (rng.choice(x) - m) * k for _ in range(n)]


def p_rule(d, test, margin):
    if test == "equivalent":
        return max(P.one_sided(d, -margin, upper=False), P.one_sided(d, margin, upper=True))
    return P.one_sided(d, margin, upper=False)


def power(tpl: dict, rule: str, template: str, mu: float, k: float, n: int, resamples: int, seed: int) -> float:
    fam, _, _, test, margin = RULES[rule]
    others = [r for r in RULES if RULES[r][0] == fam and r != rule]
    rng = random.Random(f"{seed}/{rule}/{template}/{mu}/{k}/{n}")
    hits = 0
    for _ in range(resamples):
        ps = {rule: p_rule(draw(rng, tpl[template], mu, k, n), test, margin)}
        for o in others:  # the other member of the family at its own prediction, from the same template
            ps[o] = p_rule(draw(rng, tpl[template], RULES[o][2], k, n), RULES[o][3], RULES[o][4])
        if P.holm(ps)[rule] < 0.05:
            hits += 1
    return hits / resamples


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--templates", default=str(HERE / "sim_predictions" / "extra087_templates.csv"))
    ap.add_argument("--make-templates", default="", help="the 086 final report directory: (re)write --templates from it")
    ap.add_argument("--resamples", type=int, default=4000)
    ap.add_argument("--seed", type=int, default=87)
    ap.add_argument("--out", default=str(HERE / "sim_predictions" / "extra087_power.csv"))
    a = ap.parse_args(argv)
    if a.make_templates:
        make_templates(Path(a.make_templates), Path(a.templates))
        return 0
    tpl = templates(Path(a.templates))
    rows = []
    for rule, (fam, primary, pred, test, margin) in RULES.items():
        grid = [(primary, pred, 1.0, 8), (primary, pred, 1.0, 6), (primary, pred, 3.0, 8), (primary, pred, 3.0, 6),
                (primary, pred, 10.0, 8), (primary, pred, 10.0, 6)]
        if test == "equivalent":
            grid += [(primary, pred - 1.0, 1.0, 8), (primary, pred - 1.0, 3.0, 8), (primary, pred - 1.0, 10.0, 8), ("shd", pred, 1.0, 8),
                     ("shd", pred, 1.0, 10)]
        else:
            grid = [(primary, mu, k, n) for mu in (pred, pred - 5.0, pred - 10.0) for k in (1.0, 2.0) for n in (4, 6, 8)]
        for template, mu, k, n in grid:
            pw = power(tpl, rule, template, mu, k, n, a.resamples, a.seed)
            rows.append({"rule": rule, "family": fam, "test": test, "margin": margin, "prediction": pred, "template": template,
                         "template_sd": round(statistics.stdev(tpl[template]), 3), "true_mean": mu, "spread_factor": k, "n": n,
                         "power": round(pw, 4)})
    out = Path(a.out)
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    for r in rows:
        print(f"{r['rule']:6s} {r['test']:10s} m={r['margin']:5.2f} template {r['template']:9s} SD {r['template_sd']:6.3f} mean {r['true_mean']:6.2f} "
              f"k {r['spread_factor']:4.1f} n {r['n']:2d}: power {r['power']:.3f}")
    print(f"power_extra087_testbed: {len(rows)} rows -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
