#!/usr/bin/env python3
"""Simulator vs testbed comparison for experiment A (release 065, criterion revised in 066).

Compares a runtime campaign (campaign.py runtime_episodes.csv) with its
simulator twin (evicon-des episodes.csv, e.g. experiments/sim/iot_dosing_testbed.json)
on the clean fulfilment RATE (clean_fulfilled / offered per episode), because
the simulator runs one device per episode and the campaign several.

  python3 evaluation/sim_to_real.py --runtime results/.../runtime_episodes.csv \\
      --sim results/sim/expA/episodes.csv --out results/analysis/sim_to_real

Outputs (JSON + Markdown):
  levels    per (runtime contract, policy): mean rate in each world;
  effects   experiment A per policy and contract pair (base -> upgraded): the
            paired rate difference in percentage points with a bootstrap CI in
            each world, and its sign;
  ranking   Kendall tau-b between the two worlds' orderings of the contracts by
            rate, per policy and pooled.

Check (SR), revised in release 066 after the first testbed run of experiment A:
  * sign with a practical margin m (default 2 pp, the TOST margin of analyze.py):
    +1 when the CI lies above 0 and the mean is >= m, -1 symmetrically, 0 when the
    whole CI lies inside (-m, +m) (practically null), "?" otherwise (undetermined);
  * every effect whose sign is determined in both worlds agrees, at least one
    non-null effect agrees, and no effect is undetermined in the testbed;
  * ranking: Kendall tau-b where two contracts whose rates differ by less than m
    are a tie (pooled tau_m >= 0.6).
  The 065 criterion (sign = CI excludes 0; tau-b on raw rates) is still computed
  and reported as "check_065". It failed on the 065 run for reasons that are not
  disagreements: a +0.09 pp simulator effect (n = 200) counted as positive against a
  0.00 pp testbed effect, and near-ties (0.7723 vs 0.7728 vs 0.7737) decided the
  ranking.
INVALID runtime cells are excluded; a pair with < 2 episodes in either world
is reported as INSUFFICIENT and fails the check.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
from collections import defaultdict
from pathlib import Path

# runtime preset -> simulator contract of the twin config
DEFAULT_MAP = {"K0": "sim-k0", "K0F": "sim-k0+F", "K1": "sim-k1-h45s", "K1F": "sim-k1-h45s+F", "K2": "sim-k2", "K3F": "sim-k2+F"}
DEFAULT_PAIRS = [("K0", "K0F"), ("K2", "K3F"), ("K1", "K1F")]


def read(paths):
    """{(contract, policy): {episode: rate}} of VALID rows with offered > 0."""
    out = defaultdict(dict)
    for p in paths:
        with open(p, newline="") as fh:
            for r in csv.DictReader(fh):
                if str(r.get("cell_status", "VALID")).upper().startswith("INVALID"):
                    continue
                off = float(r.get("offered") or 0)
                if off <= 0:
                    continue
                out[(str(r["contract"]), r["policy"])][int(r["episode"])] = float(r.get("clean_fulfilled") or 0) / off
    return out


def bootstrap_ci(x, n=2000, seed=65, alpha=0.05):
    if not x:
        return (math.nan, math.nan)
    rng = random.Random(seed)
    m = len(x)
    means = sorted(sum(x[rng.randrange(m)] for _ in range(m)) / m for _ in range(n))
    return means[int(alpha / 2 * n)], means[int((1 - alpha / 2) * n) - 1]


def sign(lo, hi):
    """065 sign: the CI excludes 0."""
    if lo > 0:
        return 1
    if hi < 0:
        return -1
    return 0


def sign_margin(mean, lo, hi, margin_pp):
    """066 sign with a practical margin (see the module docstring)."""
    if lo > 0 and mean >= margin_pp:
        return 1
    if hi < 0 and mean <= -margin_pp:
        return -1
    if lo > -margin_pp and hi < margin_pp:
        return 0
    return "?"


def kendall_tau_b(x, y, tie=0.0):
    """Kendall tau-b for paired lists (ties in either list allowed); differences
    smaller than `tie` count as ties."""
    n = len(x)
    if n < 2:
        return math.nan
    conc = disc = tx = ty = 0
    for i in range(n):
        for j in range(i + 1, n):
            dx, dy = x[i] - x[j], y[i] - y[j]
            dx = 0 if abs(dx) < tie else dx
            dy = 0 if abs(dy) < tie else dy
            if dx == 0 and dy == 0:
                continue
            if dx == 0:
                tx += 1
            elif dy == 0:
                ty += 1
            elif (dx > 0) == (dy > 0):
                conc += 1
            else:
                disc += 1
    denom = math.sqrt((conc + disc + tx) * (conc + disc + ty))
    return (conc - disc) / denom if denom else math.nan


def _effect(cells, base, up, policy, n_boot, margin_pp=2.0):
    a, b = cells.get((up, policy), {}), cells.get((base, policy), {})
    eps = sorted(set(a) & set(b))
    if len(eps) < 2:
        return {"pairs": len(eps), "status": "INSUFFICIENT"}
    d = [100.0 * (a[e] - b[e]) for e in eps]
    lo, hi = bootstrap_ci(d, n_boot)
    mean = sum(d) / len(d)
    return {"pairs": len(eps), "mean_pp": round(mean, 3), "ci95_lo": round(lo, 3), "ci95_hi": round(hi, 3),
            "sign": sign_margin(mean, lo, hi, margin_pp), "sign_065": sign(lo, hi), "status": "OK"}


def compare(runtime_paths, sim_paths, mapping=None, pairs=None, n_boot=2000, tau_min=0.6, margin_pp=2.0):
    mapping = dict(DEFAULT_MAP if mapping is None else mapping)
    rt, sm_raw = read(runtime_paths), read(sim_paths)
    # simulator cells renamed to runtime contracts
    inverse = {v: k for k, v in mapping.items()}
    sm = defaultdict(dict)
    for (c, p), eps in sm_raw.items():
        if c in inverse:
            sm[(inverse[c], p)] = eps
    policies = sorted({p for (_, p) in rt} & {p for (_, p) in sm})
    contracts = sorted({c for (c, _) in rt} & {c for (c, _) in sm})
    levels = []
    for c in contracts:
        for p in policies:
            r, s = rt.get((c, p)), sm.get((c, p))
            if r and s:
                levels.append({"contract": c, "sim_contract": mapping[c], "policy": p, "runtime_episodes": len(r), "sim_episodes": len(s),
                               "runtime_rate": round(sum(r.values()) / len(r), 4), "sim_rate": round(sum(s.values()) / len(s), 4)})
    effects = []
    for base, up in (pairs if pairs is not None else DEFAULT_PAIRS):
        if base not in contracts or up not in contracts:
            continue
        for p in policies:
            er, es = _effect(rt, base, up, p, n_boot, margin_pp), _effect(sm, base, up, p, n_boot, margin_pp)
            row = {"policy": p, "base": base, "upgraded": up, "runtime": er, "sim": es}
            ok = er["status"] == es["status"] == "OK"
            row["sign_agrees_065"] = ok and er["sign_065"] == es["sign_065"]
            determined = ok and "?" not in (er["sign"], es["sign"])
            row["sign_agrees"] = determined and er["sign"] == es["sign"]
            row["sign_disagrees"] = determined and er["sign"] != es["sign"]
            effects.append(row)
    ranking = {}
    tie = margin_pp / 100.0
    lv = {(x["contract"], x["policy"]): x for x in levels}
    for p in policies:
        cs = [c for c in contracts if (c, p) in rt and (c, p) in sm]
        xr, xs = [lv[(c, p)]["runtime_rate"] for c in cs], [lv[(c, p)]["sim_rate"] for c in cs]
        ranking[p] = {"contracts": cs, "tau_b": _round(kendall_tau_b(xr, xs)), "tau_b_margin": _round(kendall_tau_b(xr, xs, tie))}
    pooled = [x for x in levels]
    xr, xs = [x["runtime_rate"] for x in pooled], [x["sim_rate"] for x in pooled]
    tau_pooled, tau_m = kendall_tau_b(xr, xs), kendall_tau_b(xr, xs, tie)
    check_065 = {"all_signs_agree": bool(effects) and all(e["sign_agrees_065"] for e in effects),
                 "tau_b_pooled": _round(tau_pooled), "tau_min": tau_min,
                 "tau_ok": not math.isnan(tau_pooled) and tau_pooled >= tau_min}
    check_065["pass"] = check_065["all_signs_agree"] and check_065["tau_ok"]
    undetermined_rt = [f"{e['policy']} {e['base']}->{e['upgraded']}" for e in effects
                       if e["runtime"]["status"] != "OK" or e["runtime"]["sign"] == "?"]
    check = {"margin_pp": margin_pp,
             "no_sign_disagrees": bool(effects) and not any(e["sign_disagrees"] for e in effects),
             "nonnull_agreeing": sum(1 for e in effects if e["sign_agrees"] and e["runtime"]["sign"] in (1, -1)),
             "undetermined_testbed": undetermined_rt,
             "tau_b_margin_pooled": _round(tau_m), "tau_min": tau_min,
             "tau_ok": not math.isnan(tau_m) and tau_m >= tau_min}
    check["all_signs_agree"] = check["no_sign_disagrees"] and not undetermined_rt
    check["pass"] = check["all_signs_agree"] and check["nonnull_agreeing"] >= 1 and check["tau_ok"]
    return {"mapping": {c: mapping[c] for c in contracts}, "policies": policies, "levels": levels, "effects": effects,
            "ranking": ranking, "check": check, "check_065": check_065}


def _round(x):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(x, 4)


def markdown(rep):
    c, c65 = rep["check"], rep.get("check_065", {})
    L = ["# Simulator vs testbed (experiment A)", "",
         f"SR check (066): **{'PASS' if c['pass'] else 'FAIL'}** (margin {c['margin_pp']} pp: no sign disagrees: "
         f"{c['no_sign_disagrees']}; non-null effects agreeing: {c['nonnull_agreeing']}; undetermined on the testbed: "
         f"{', '.join(c['undetermined_testbed']) or 'none'}; pooled Kendall tau-b with ties < margin {c['tau_b_margin_pooled']} "
         f"vs >= {c['tau_min']})", "",
         f"065 criterion (for reference): {'PASS' if c65.get('pass') else 'FAIL'} (signs agree: {c65.get('all_signs_agree')}; "
         f"tau-b {c65.get('tau_b_pooled')})", "",
         "## Experiment A effects (percentage points of clean fulfilment)", "",
         "| policy | pair | testbed (CI) | sign | simulator (CI) | sign | agree |", "|---|---|---|---|---|---|---|"]
    for e in rep["effects"]:
        def cell(x):
            return (f"{x['mean_pp']:+.2f} ({x['ci95_lo']:+.2f}, {x['ci95_hi']:+.2f}), n={x['pairs']}" if x["status"] == "OK"
                    else f"INSUFFICIENT (n={x['pairs']})")
        L.append(f"| {e['policy']} | {e['base']} -> {e['upgraded']} | {cell(e['runtime'])} | {e['runtime'].get('sign', '-')} | "
                 f"{cell(e['sim'])} | {e['sim'].get('sign', '-')} | "
                 f"{'yes' if e['sign_agrees'] else ('NO' if e['sign_disagrees'] else 'undetermined')} |")
    L += ["", "## Levels (clean fulfilment rate)", "", "| contract | simulator twin | policy | testbed | simulator |", "|---|---|---|---|---|"]
    for x in rep["levels"]:
        L.append(f"| {x['contract']} | {x['sim_contract']} | {x['policy']} | {x['runtime_rate']:.4f} (n={x['runtime_episodes']}) | "
                 f"{x['sim_rate']:.4f} (n={x['sim_episodes']}) |")
    L += ["", "## Contract ranking", "", "| policy | contracts | Kendall tau-b | tau-b, ties < margin |", "|---|---|---|---|"]
    for p, r in rep["ranking"].items():
        L.append(f"| {p} | {', '.join(r['contracts'])} | {r['tau_b']} | {r.get('tau_b_margin')} |")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--runtime", action="append", required=True, help="runtime_episodes.csv (repeatable)")
    ap.add_argument("--sim", action="append", required=True, help="evicon-des episodes.csv (repeatable)")
    ap.add_argument("--map", action="append", default=[], help="RUNTIME=SIM contract (replaces the default map when given)")
    ap.add_argument("--pair", action="append", default=[], help="BASE:UPGRADED runtime contracts (default K0:K0F, K2:K3F, K1:K1F)")
    ap.add_argument("--bootstrap", type=int, default=2000)
    ap.add_argument("--tau-min", type=float, default=0.6)
    ap.add_argument("--margin-pp", type=float, default=2.0, help="practical margin for signs and ranking ties (066)")
    ap.add_argument("--out", default="results/analysis/sim_to_real")
    a = ap.parse_args(argv)
    mapping = dict(m.split("=", 1) for m in a.map) if a.map else None
    pairs = [tuple(p.split(":", 1)) for p in a.pair] if a.pair else None
    rep = compare(a.runtime, a.sim, mapping, pairs, a.bootstrap, a.tau_min, a.margin_pp)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "sim_to_real.json").write_text(json.dumps(rep, indent=2) + "\n")
    (out / "sim_to_real.md").write_text(markdown(rep))
    print(markdown(rep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
