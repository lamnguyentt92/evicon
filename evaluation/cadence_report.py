#!/usr/bin/env python3
"""Experiment B2 (release 071): does EVICON's resolution cadence, not its decision logic, explain
why it fences about four times as often as RES_PERIODIC for the same clean fulfilment?

Plan: evaluation/prereg/PREREG_expB2_cadence_071.md (this file is frozen by it).

Inputs
  --b2   the expB2-cadence run: K0F, EVICON and RES_PERIODIC, both resolving every 10 s
  --ref  the reference run with EVICON at a 1 s cadence and the same seed (the 069 replication
         of expA-confirm-k0: same fault schedule for episodes 0-15, common random numbers)

Decision rule of the primary hypothesis H-C (section 3 of the plan)
  per episode d = clean fulfilment (pp of offered) of EVICON minus RES_PERIODIC, B2 run, K0F;
  equivalent  when the 90 % t-interval of mean(d) lies inside (-5, +5) pp (TOST, alpha 0.05 per side);
  different   when instead the 95 % t-interval excludes 0;
  inconclusive otherwise.
H-S: no safety violation in the B2 run (analysis/report.json).

Estimands reported with 95 % intervals and no further claims (section 4 of the plan)
  E1  fences per episode, EVICON / RES_PERIODIC, in the B2 run and in the reference run
  E2  EVICON - RES_PERIODIC in the B2 run: stranded capacity-time (resolved, censored), time to a
      terminal outcome, fences
  E3  cadence effect, difference in differences over the episodes common to both runs:
      (EVICON - RES_PERIODIC)_ref - (EVICON - RES_PERIODIC)_B2 for clean fulfilment, stranded
      capacity-time, time to terminal and fences; computed only on episodes whose fault and
      arrival schedules are identical in both runs (checked here)

Reads only. Writes RESULTS.md and results.json.

    python3 evaluation/cadence_report.py --b2 <expB2-cadence run> --ref <expA-confirm-k0 run> --out <dir>
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
import sys
from pathlib import Path

PLAN = "evaluation/prereg/PREREG_expB2_cadence_071.md"
MARGIN_PP = 5.0
POL, REF = "EVICON", "RES_PERIODIC"
METRICS = [("clean_pp", "clean fulfilment (pp of offered)"), ("stranded_resolved_unit_sec", "stranded capacity-time, resolved (unit*s)"),
           ("stranded_censored_unit_sec", "stranded capacity-time, censored (unit*s)"),
           ("mean_time_to_terminal_s", "mean time to a terminal outcome (s)"), ("fences", "fences per episode")]


# ------------------------------------------------------------------ Student t (no scipy on the testbed)
def _betacf(a, b, x):
    """Continued fraction of the regularized incomplete beta function (Numerical Recipes 6.4)."""
    fpmin, qab, qap, qam = 1e-300, a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > fpmin else fpmin)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        for aa in (m * (b - m) * x / ((qam + m2) * (a + m2)), -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))):
            d = 1.0 + aa * d
            d = 1.0 / (d if abs(d) > fpmin else fpmin)
            c = 1.0 + aa / c
            c = c if abs(c) > fpmin else fpmin
            de = d * c
            h *= de
        if abs(de - 1.0) < 3e-14:
            break
    return h


def betai(a, b, x):
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lbt = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x)
    if x < (a + 1) / (a + b + 2):
        return math.exp(lbt) * _betacf(a, b, x) / a
    return 1.0 - math.exp(lbt) * _betacf(b, a, 1 - x) / b


def t_cdf(t, df):
    p = 0.5 * betai(df / 2.0, 0.5, df / (df + t * t))
    return 1.0 - p if t > 0 else p


def t_quantile(q, df):
    lo, hi = -1e3, 1e3
    for _ in range(200):
        mid = (lo + hi) / 2
        if t_cdf(mid, df) < q:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def t_interval(x, level):
    n = len(x)
    if n < 2:
        return None
    m, s = statistics.mean(x), statistics.stdev(x)
    h = t_quantile(1 - (1 - level) / 2, n - 1) * s / math.sqrt(n)
    return m - h, m + h


def boot_ci(x, n=2000, seed=61):
    """Percentile bootstrap of the mean (same resampling as evaluation/analyze.py)."""
    import random
    if not x:
        return (math.nan, math.nan)
    rng = random.Random(seed)
    m = len(x)
    means = sorted(sum(x[rng.randrange(m)] for _ in range(m)) / m for _ in range(n))
    return means[int(0.025 * n)], means[int(0.975 * n) - 1]


def boot_ratio(num, den, n=2000, seed=62):
    import random
    rng = random.Random(seed)
    m, out = len(num), []
    for _ in range(n):
        idx = [rng.randrange(m) for _ in range(m)]
        d = sum(den[i] for i in idx)
        out.append(sum(num[i] for i in idx) / d if d else math.nan)
    out = sorted(v for v in out if not math.isnan(v))
    return (out[int(0.025 * len(out))], out[int(0.975 * len(out)) - 1]) if out else (math.nan, math.nan)


# ------------------------------------------------------------------ data
def cells(run: Path) -> tuple:
    """(contract, policy, episode) -> metrics of VALID K0F cells; and the INVALID cells."""
    out, invalid = {}, []
    with open(run / "runtime_episodes.csv", newline="") as fh:
        for r in csv.DictReader(fh):
            key = (r["contract"], r["policy"], int(r["episode"]))
            if not str(r.get("cell_status", "")).startswith("VALID"):
                invalid.append(f"ep{key[2]}/{key[0]}/{key[1]}")
                continue
            off = float(r["offered"] or 0)
            m = {"clean_pp": 100.0 * float(r["clean_fulfilled"]) / off if off else math.nan}
            for k, _ in METRICS[1:]:
                try:
                    m[k] = float(r.get(k) or 0)
                except ValueError:
                    m[k] = math.nan
            out[key] = m
    return out, invalid


def schedule_digest(run: Path, ep: int) -> str:
    p = run / f"schedule_ep{ep}.json"
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else ""


def diffs(c: dict, metric: str, eps) -> list:
    return [c[("K0F", POL, e)][metric] - c[("K0F", REF, e)][metric] for e in eps
            if ("K0F", POL, e) in c and ("K0F", REF, e) in c]


def summary(x) -> dict:
    if not x:
        return {"n": 0}
    lo, hi = boot_ci(x)
    return {"n": len(x), "mean": round(statistics.mean(x), 3), "boot95": [round(lo, 3), round(hi, 3)],
            "t95": [round(v, 3) for v in (t_interval(x, 0.95) or (math.nan, math.nan))]}


def run_name(r: Path) -> str:
    return f"{r.parent.name}/{r.name}"


def analyse(b2: Path, ref: Path) -> dict:
    cb, inv_b = cells(b2)
    cr, _ = cells(ref)
    eps_b = sorted({k[2] for k in cb})
    rep = json.loads((b2 / "analysis" / "report.json").read_text()) if (b2 / "analysis" / "report.json").exists() else {}
    total = 2 * len(eps_b) + len(inv_b)
    res = {"plan": PLAN, "b2_run": run_name(b2), "ref_run": run_name(ref), "margin_pp": MARGIN_PP,
           "invalid_cells": inv_b, "interpretable": (len(inv_b) <= 0.10 * total) if total else False}
    # H-C
    d = diffs(cb, "clean_pp", eps_b)
    ci90, ci95 = t_interval(d, 0.90), t_interval(d, 0.95)
    if ci90 is None:
        verdict = "not enough episodes"
    elif -MARGIN_PP < ci90[0] and ci90[1] < MARGIN_PP:
        verdict = "equivalent"
    elif ci95[0] > 0 or ci95[1] < 0:
        verdict = "different (" + ("EVICON higher" if ci95[0] > 0 else "RES_PERIODIC higher") + ")"
    else:
        verdict = "inconclusive"
    res["H-C"] = {"rule": f"90 % t-interval of mean(EVICON - RES_PERIODIC) clean fulfilment within (-{MARGIN_PP:g}, +{MARGIN_PP:g}) pp",
                  "n": len(d), "mean_pp": round(statistics.mean(d), 3) if d else None,
                  "t90": [round(v, 3) for v in ci90] if ci90 else None, "t95": [round(v, 3) for v in ci95] if ci95 else None,
                  "verdict": verdict, "pass": verdict == "equivalent"}
    viol = rep.get("groups_with_safety_violations", None)
    res["H-S"] = {"rule": "analysis/report.json groups_with_safety_violations empty", "violations": viol,
                  "late_effects": rep.get("late_effects_total"), "pass": viol == []}
    # E1, E2
    def fences(c, pol, eps):
        return [c[("K0F", pol, e)]["fences"] for e in eps if ("K0F", POL, e) in c and ("K0F", REF, e) in c]
    eps_r = sorted({k[2] for k in cr})
    e1 = {}
    for label, c, eps in (("b2", cb, eps_b), ("ref", cr, eps_r)):
        num, den = fences(c, POL, eps), fences(c, REF, eps)
        e1[label] = {"n": len(num), "fences_per_episode": {POL: round(statistics.mean(num), 2) if num else None,
                                                           REF: round(statistics.mean(den), 2) if den else None},
                     "ratio": round(sum(num) / sum(den), 3) if den and sum(den) else None,
                     "ratio_boot95": [round(v, 3) for v in boot_ratio(num, den)] if den and sum(den) else None}
    res["E1"] = e1
    res["E2"] = {m: summary(diffs(cb, m, eps_b)) for m, _ in METRICS}
    # E3: difference in differences over episodes with identical schedules
    common = [e for e in eps_b if e in eps_r]
    same = [e for e in common if schedule_digest(b2, e) and schedule_digest(b2, e) == schedule_digest(ref, e)]
    res["E3"] = {"episodes_common": common, "episodes_same_schedule": same,
                 "estimands": {m: summary([a - b for a, b in zip(diffs(cr, m, same), diffs(cb, m, same))])
                               for m, _ in METRICS} if same else {}}
    # per-policy levels in the B2 run
    lv = {}
    for pol in (POL, REF):
        rows = [cb[("K0F", pol, e)] for e in eps_b if ("K0F", pol, e) in cb]
        lv[pol] = {m: round(statistics.mean(r[m] for r in rows), 3) for m, _ in METRICS} if rows else {}
    res["levels_b2"] = lv
    return res


def fmt(s: dict) -> str:
    if not s or not s.get("n"):
        return "-"
    return f"{s['mean']:+.2f} (95 % CI {s['t95'][0]:+.2f}, {s['t95'][1]:+.2f}; bootstrap {s['boot95'][0]:+.2f}, {s['boot95'][1]:+.2f}), n={s['n']}"


def markdown(r: dict) -> str:
    h = r["H-C"]
    L = ["# Experiment B2: resolution cadence", "",
         f"Plan: `{r['plan']}`. B2 run: `{r['b2_run']}` (EVICON and RES_PERIODIC, 10 s cadence). Reference run: `{r['ref_run']}` "
         "(EVICON at 1 s).", "",
         f"Excluded (INVALID) cells: {len(r['invalid_cells'])}{'' if r['interpretable'] else ' - more than 10 %: NOT INTERPRETABLE'}.", "",
         "## Preregistered hypotheses", "", "| ID | Result | Evidence |", "|---|---|---|",
         f"| H-C | **{h['verdict']}** | EVICON - RES_PERIODIC clean fulfilment {h['mean_pp']:+.2f} pp; 90 % t-interval "
         f"({h['t90'][0]:+.2f}, {h['t90'][1]:+.2f}); margin ±{r['margin_pp']:g} pp; n={h['n']} |" if h.get("t90") else
         f"| H-C | **{h['verdict']}** | - |",
         f"| H-S | **{'MET' if r['H-S']['pass'] else 'NOT MET'}** | violations {r['H-S']['violations']}; late effects "
         f"{r['H-S']['late_effects']} |", "",
         "## Estimands (no further claims)", "", "E1, fences per episode:", "",
         "| run | cadence | EVICON | RES_PERIODIC | ratio (95 % bootstrap) |", "|---|---|---|---|---|"]
    for label, cad in (("ref", "EVICON 1 s, RES_PERIODIC 10 s"), ("b2", "both 10 s")):
        e = r["E1"][label]
        rb = e["ratio_boot95"]
        L.append(f"| {label} | {cad} | {e['fences_per_episode'][POL]} | {e['fences_per_episode'][REF]} | {e['ratio']} "
                 f"({rb[0]}, {rb[1]}) |" if rb else f"| {label} | {cad} | - | - | - |")
    L += ["", "E2, EVICON - RES_PERIODIC in the B2 run (per episode):", "", "| metric | difference |", "|---|---|"]
    for m, label in METRICS:
        L.append(f"| {label} | {fmt(r['E2'].get(m))} |")
    e3 = r["E3"]
    L += ["", f"E3, cadence effect = (EVICON - RES_PERIODIC) at 1 s minus at 10 s, over {len(e3['episodes_same_schedule'])} episodes "
              f"with identical schedules (of {len(e3['episodes_common'])} in both runs):", "", "| metric | difference in differences |", "|---|---|"]
    for m, label in METRICS:
        L.append(f"| {label} | {fmt(e3['estimands'].get(m))} |")
    L += ["", "Levels in the B2 run (means per episode):", "", "| policy | " + " | ".join(lbl for _, lbl in METRICS) + " |",
          "|---|" + "---|" * len(METRICS)]
    for pol, v in r["levels_b2"].items():
        L.append(f"| {pol} | " + " | ".join(str(v.get(m, "-")) for m, _ in METRICS) + " |")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--b2", required=True)
    ap.add_argument("--ref", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    res = analyse(Path(a.b2), Path(a.ref))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(res, indent=2) + "\n")
    (out / "RESULTS.md").write_text(markdown(res))
    print(f"experiment B2: H-C {res['H-C']['verdict']}; H-S {'MET' if res['H-S']['pass'] else 'NOT MET'}; fence ratio "
          f"{res['E1']['b2']['ratio']} at 10 s vs {res['E1']['ref']['ratio']} at 1 s -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
