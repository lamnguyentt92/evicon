#!/usr/bin/env python3
"""Power of the PREREG_extra_085.md decision rules from the testbed's own per-episode spread (release 085, planning only).

The matched comparison (review M5a) runs EVICON and PERIODIC_FULL in one campaign on the schedules of the 084 PF runs, so
the closest testbed spread is that of the 084 PF pairs and of two guarded arms within one run of the same owner, both from
the 084 final report's cells (review run review-20261102T174310, report/pairs/PREREG_extra_084/cells.csv; the per-episode
differences are copied into sim_predictions/extra085_templates.csv with that file's SHA-256):

  pf-cross-<owner>-<W>  the 084 PF pair: PERIODIC_FULL of the 084 run minus EVICON of its reference run, on the same
                        schedule files (PF-1 to PF-4; one policy each in two runs, release and day apart: includes the
                        run-to-run spread, conservative for a within-run pair). Primary template of PM-1 to PM-4.
  within-fw / within-minio  two guarded arms within one run of the same owner on the same schedules (C2: ZEPHYR_STOP
                        against ZEPHYR_CUTOFF, EVICON; C: MINIO_COND, NAIVE against EVICON): the within-run spread
                        (sensitivity)

Each rule is tested as pairs_ext083.py tests it (pairs_report.one_sided, TOST with margin 3 pp, Holm within its family
with the other member at its predicted mean, alpha 0.05), on resamples of n = 8 per-episode differences drawn with
replacement from the template, centred and shifted to the true mean; 4,000 resamples, seed 85. The predicted mean is the
simulator's (sim_predictions/extra084_effects.csv, scenarios c_fw_* and c_minio_*: the 084 twin, whose PERIODIC_FULL and
EVICON run on the same schedules, i.e. exactly the matched design); the 084 testbed's cross-run means are shown next to it.
The power is also given at nearby true means and with the spread multiplied by 3 and 10.

Writes sim_predictions/extra085_power.csv and prints the tables of the plan's section 5.

    python3 evaluation/power_extra085_testbed.py
"""
from __future__ import annotations

import csv
import random
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pairs_report as PR  # noqa: E402  (frozen; its tests are the ones the analysis uses)

TEMPLATES = HERE / "sim_predictions" / "extra085_templates.csv"
SIM = HERE / "sim_predictions" / "extra084_effects.csv"
OUT = HERE / "sim_predictions" / "extra085_power.csv"
N, ALPHA, MARGIN, REPS, SEED = 8, 0.05, 3.0, 4000, 85
RULES = [  # id, family, primary template, within-run template, simulator scenario
    ("PM-1", "pm-fw", "pf-cross-fw-w6", "within-fw", "c_fw_w6"),
    ("PM-2", "pm-fw", "pf-cross-fw-winf", "within-fw", "c_fw_winf"),
    ("PM-3", "pm-minio", "pf-cross-minio-w6", "within-minio", "c_minio_w6"),
    ("PM-4", "pm-minio", "pf-cross-minio-winf", "within-minio", "c_minio_winf"),
]


def templates() -> dict:
    out = {}
    with open(TEMPLATES) as fh:
        rows = csv.DictReader(line for line in fh if not line.startswith("#"))
        for r in rows:
            out.setdefault(r["template"], []).append(float(r["diff_pp"]))
    return out


def sim_means() -> dict:
    with open(SIM) as fh:
        return {r["scenario"]: float(r["mean_pp"]) for r in csv.DictReader(fh)
                if r["metric"] == "clean_fulfilled" and r["scenario"].startswith(("c_fw", "c_minio"))}


def tost_p(d: list) -> float:
    return max(PR.one_sided(d, -MARGIN, upper=False), PR.one_sided(d, MARGIN, upper=True))


def power(rule: str, mus: dict, tpl: dict, scale: float = 1.0, rng=None) -> float:
    """P(rule MET under Holm in its family) with each member's true mean mus[id] and template tpl[id] (spread x scale)."""
    rng = rng or random.Random(SEED)
    fam = [r for r in RULES if r[1] == next(x[1] for x in RULES if x[0] == rule)]
    cent = {r[0]: [scale * (v - statistics.mean(tpl[r[0]])) for v in tpl[r[0]]] for r in fam}
    hit = 0
    for _ in range(REPS):
        ps = {}
        for r in fam:
            d = [mus[r[0]] + rng.choice(cent[r[0]]) for _ in range(N)]
            ps[r[0]] = tost_p(d)
        hit += PR.holm(ps)[rule] < ALPHA
    return hit / REPS


def main():
    T, sim = templates(), sim_means()
    obs = {r[0]: statistics.mean(T[r[2]]) for r in RULES}
    pred = {r[0]: sim[r[4]] for r in RULES}
    prim = {r[0]: T[r[2]] for r in RULES}
    within = {r[0]: T[r[3]] for r in RULES}
    rows = []
    print("| Rule | Family | Predicted mean (simulator) | 084 cross-run mean | Template SD, primary / within | Power (primary) | Power (within) | Decision |")
    print("|---|---|---|---|---|---|---|---|")
    for rid, fam, tp, tw, sc in RULES:
        p1 = power(rid, pred, prim)
        p2 = power(rid, pred, within)
        dec = "hypothesis" if p1 >= 0.80 else "estimand"
        rows.append({"id": rid, "family": fam, "template": tp, "true_mean_pp": pred[rid], "scale": 1, "power": p1, "what": "primary at the prediction"})
        rows.append({"id": rid, "family": fam, "template": tw, "true_mean_pp": pred[rid], "scale": 1, "power": p2, "what": "within-run at the prediction"})
        print(f"| {rid} | {fam} | {pred[rid]:+.2f} | {obs[rid]:+.2f} | {statistics.stdev(T[tp]):.2f} / {statistics.stdev(T[tw]):.2f} | "
              f"{p1:.2f} | {p2:.2f} | {dec} |")
    print()
    print("| Rule | True mean -> power (primary template) | Spread x3 / x10 at the prediction |")
    print("|---|---|---|")
    for rid, fam, tp, tw, sc in RULES:
        cells = []
        for mu in (pred[rid], 1.0, 2.0, 2.5, -2.0):
            mus = dict(pred, **{rid: mu})
            pw = power(rid, mus, prim)
            rows.append({"id": rid, "family": fam, "template": tp, "true_mean_pp": mu, "scale": 1, "power": pw, "what": "primary, nearby mean"})
            cells.append(f"{mu:+.2f}: {pw:.2f}")
        s3, s10 = power(rid, pred, prim, 3.0), power(rid, pred, prim, 10.0)
        rows += [{"id": rid, "family": fam, "template": tp, "true_mean_pp": pred[rid], "scale": 3, "power": s3, "what": "primary x3"},
                 {"id": rid, "family": fam, "template": tp, "true_mean_pp": pred[rid], "scale": 10, "power": s10, "what": "primary x10"}]
        print(f"| {rid} | {', '.join(cells)} | {s3:.2f} / {s10:.2f} |")
    with open(OUT, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["id", "family", "template", "true_mean_pp", "scale", "power", "what"])
        w.writeheader()
        for r in rows:
            w.writerow(dict(r, power=f"{r['power']:.4f}", true_mean_pp=f"{r['true_mean_pp']:+.2f}"))
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
