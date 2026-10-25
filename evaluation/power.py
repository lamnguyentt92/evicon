#!/usr/bin/env python3
"""Sample-size planning for the paired (CRN) comparisons (release 065).

From a pilot CSV (evicon-des episodes.csv or runtime_episodes.csv) it
estimates the SD of the paired per-episode difference and returns the
episodes needed for one of three designs:

  superiority (default)   two-sided test that detects a difference of delta:
      n = ceil(((z_{1-alpha/(2m)} + z_{power}) * sd_d / delta)^2)
  equivalence (--equivalence)  TOST that shows |difference| < delta, assuming
      the pilot mean difference mu is the truth:
      n = ceil(((z_{1-alpha/m} + z_{1-(1-power)/2}) * sd_d / (delta - |mu|))^2)

Two kinds of comparison:

  experiment B (--policy P --reference R): P vs R on the same contract;
  experiment A (--upgrade BASE:UPGRADED, repeatable): the same policy on the
      upgraded contract vs the base contract (e.g. K0:K0F, K2:K3F, or the
      simulator's sim-k0:sim-k0+F), for every policy given with --policy
      (default: every policy present on both contracts).

delta is absolute (metric units), relative to the reference mean (--relative),
or in percentage points of the offered requests (--delta-pp; for
clean_fulfilled that is percentage points of the clean fulfilment rate).

Every group is printed with a status, never skipped silently:
  SIZED               n and its bootstrap power
  INFEASIBLE          n > 5000: the pilot difference is too close to delta (067)
  NO_ACTION_IN_CONTRACT  the contract offers the resolver no evidence action
                      (every decision ESCALATE): nothing to size (release 067)
  INSUFFICIENT_PILOT  fewer than 3 paired episodes (release 064 printed nothing)
  NOT_ACTIVATED       a resolver policy never acted (fix the regime first)
  UNDETERMINED        zero variance of the paired difference
  NOT_EQUIVALENT      equivalence design, but |pilot difference| >= delta
and "NO_PAIRS" when no group has both sides at all. --json writes the rows.
INVALID cells are excluded.

  python3 evaluation/power.py --csv pilot/runtime_episodes.csv --policy EVICON --reference RES_IMMEDIATE \\
      --metric clean_fulfilled --delta 0.05 --relative --comparisons 12
  python3 evaluation/power.py --csv pilot/runtime_episodes.csv --upgrade K0:K0F --upgrade K2:K3F --policy EVICON \\
      --delta-pp 10 --comparisons 3
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
from collections import defaultdict
from statistics import NormalDist, mean, stdev

MIN_PILOT = 3
RESOLVERS = {"EVICON", "RES_IMMEDIATE", "RES_QUERY_FIRST", "RES_FIXED", "RES_PERIODIC"}


def z(p):
    return NormalDist().inv_cdf(p)


def required_n(sd, delta, alpha, power, m):
    """Superiority: episodes needed, or None when the pilot cannot size the
    study (sd == 0: no variation observed or the design cannot observe it)."""
    if delta <= 0:
        raise ValueError("delta must be > 0")
    if sd == 0:
        return None
    return max(2, math.ceil(((z(1 - alpha / (2 * m)) + z(power)) * sd / delta) ** 2))


def required_n_equivalence(sd, delta, mu, alpha, power, m):
    """TOST with margin delta: None for sd == 0, 0 when |mu| >= delta (the
    pilot itself is outside the margin: no n shows equivalence)."""
    if delta <= 0:
        raise ValueError("delta must be > 0")
    if sd == 0:
        return None
    room = delta - abs(mu)
    if room <= 0:
        return 0
    return max(2, math.ceil(((z(1 - alpha / m) + z(1 - (1 - power) / 2)) * sd / room) ** 2))


def _activated(r) -> bool:
    if "activated" in r and r["activated"] != "":
        return str(r["activated"]).lower() in ("1", "true")
    return sum(float(r.get(k) or 0) for k in ("queries", "stops", "fences")) > 0


MAX_FEASIBLE_N = 5000  # episodes per cell; beyond this a design is not a plan (release 067)


def _escalate_only(r) -> bool:
    """Every decision of a runtime resolver cell was ESCALATE: the contract offers this policy no
    evidence action (K0; K2 for RES_PERIODIC). Same rule as analyze.escalate_only (release 066/067)."""
    raw = r.get("decisions_by_action") or ""
    try:
        d = json.loads(raw) if isinstance(raw, str) and raw else (dict(raw) if raw else {})
    except (ValueError, TypeError):
        return False
    return bool(d) and set(d) == {"ESCALATE"}


def regime_base(regime: str, contract: str) -> str:
    """The simulator names each sweep entry's regime '<regime>/<contract>';
    the runtime uses one regime for every contract."""
    for c in (contract, contract.removesuffix("+F")):
        if regime.endswith("/" + c):
            return regime[: -len(c) - 1]
    return regime.removesuffix("+F")


def bootstrap_power(diffs, n, delta_sign, alpha, m, reps=500, seed=61):
    """Fraction of resampled n-episode studies whose (normal-approx) test
    rejects H0 at alpha/m in the direction of the pilot effect."""
    rng = random.Random(seed)
    crit = z(1 - alpha / (2 * m))
    hits = 0
    for _ in range(reps):
        s = [diffs[rng.randrange(len(diffs))] for _ in range(n)]
        sd = stdev(s) if n > 1 else 0.0
        if sd == 0:
            hits += (mean(s) * delta_sign) > 0
            continue
        t = mean(s) / (sd / math.sqrt(n))
        hits += (t * delta_sign) > crit
    return hits / reps


def bootstrap_power_equivalence(diffs, n, delta, alpha, m, reps=500, seed=61):
    rng = random.Random(seed)
    crit = z(1 - alpha / m)
    hits = 0
    for _ in range(reps):
        s = [diffs[rng.randrange(len(diffs))] for _ in range(n)]
        sd = stdev(s) if n > 1 else 0.0
        mu = mean(s)
        if sd == 0:
            hits += abs(mu) < delta
            continue
        se = sd / math.sqrt(n)
        hits += (mu + delta) / se > crit and (delta - mu) / se > crit
    return hits / reps


def load(paths, metric):
    """{(scope, regime_base, contract, policy): {episode: row}} of VALID cells."""
    cells = defaultdict(dict)
    for path in paths:
        with open(path, newline="") as fh:
            for r in csv.DictReader(fh):
                if str(r.get("cell_status", "VALID")).upper().startswith("INVALID"):
                    continue
                if metric not in r:
                    raise SystemExit(f"metric {metric!r} not in {path}")
                c = str(r.get("contract", ""))
                key = (r.get("scope", ""), regime_base(r.get("regime", ""), c), c, r["policy"])
                cells[key][int(r["episode"])] = r
    return cells


def _row(kind, scope, regime, contract, policy, reference, eps, d, ref_mean, delta, a, acted_ok):
    out = {"comparison": kind, "scope": scope, "regime": regime, "contract": contract, "policy": policy, "reference": reference,
           "pilot_n": len(eps), "ref_mean": None, "mean_d": None, "sd_d": None, "delta": None, "n_required": None,
           "bootstrap_power": None, "status": "", "design": "equivalence" if a.equivalence else "superiority"}
    if len(eps) < MIN_PILOT:
        out["status"] = "INSUFFICIENT_PILOT"
        out["note"] = f"{len(eps)} paired episode(s) < {MIN_PILOT}: run a pilot with >= 5 episodes"
        return out
    sd = stdev(d)
    out.update(ref_mean=round(ref_mean, 4), mean_d=round(mean(d), 4), sd_d=round(sd, 4), delta=round(delta, 4))
    if delta <= 0:
        out["status"], out["note"] = "UNDETERMINED", "delta is 0 (reference mean or offered is 0)"
        return out
    if not acted_ok[0]:
        if len(acted_ok) > 2 and acted_ok[2]:
            out["status"], out["note"] = "NO_ACTION_IN_CONTRACT", (f"{acted_ok[1]}: the contract offers no evidence action "
                                                                     f"(every decision ESCALATE); nothing to size")
            return out
        out["status"], out["note"] = "NOT_ACTIVATED", f"{acted_ok[1]} never acted in this pilot; fix the regime before sizing"
        return out
    if a.equivalence:
        n = required_n_equivalence(sd, delta, mean(d), a.alpha, a.power, a.comparisons)
        if n == 0:
            out["status"], out["note"] = "NOT_EQUIVALENT", f"|pilot difference| {abs(mean(d)):.3f} >= margin {delta:.3f}"
            return out
    else:
        n = required_n(sd, delta, a.alpha, a.power, a.comparisons)
    if n is None:
        out["status"], out["note"] = "UNDETERMINED", "zero variance of the paired difference; enlarge or redesign the pilot"
        return out
    if n > MAX_FEASIBLE_N:
        # 066: an equivalence test whose pilot difference sat at the margin asked for n = 813042, and
        # the bootstrap at that n took 10 minutes to say nothing new
        out.update(status="INFEASIBLE", n_required=n,
                   note=f"n = {n} > {MAX_FEASIBLE_N}: the pilot difference {mean(d):.3f} is too close to delta {delta:.3f} "
                        f"for any feasible campaign; report the estimate with its CI instead")
        return out
    pw = (bootstrap_power_equivalence(d, n, delta, a.alpha, a.comparisons) if a.equivalence
          else bootstrap_power(d, n, 1 if mean(d) >= 0 else -1, a.alpha, a.comparisons))
    out.update(status="SIZED", n_required=n, bootstrap_power=round(pw, 3))
    return out


def _delta(a, ref_rows, eps, ref_mean):
    if a.delta_pp is not None:
        return a.delta_pp / 100.0 * mean(float(ref_rows[e].get("offered") or 0) for e in eps)
    return a.delta * abs(ref_mean) if a.relative else a.delta


def plan(a):
    cells = load(a.csv, a.metric)
    rows = []

    def val(r):
        return float(r.get(a.metric) or 0)

    if a.upgrade:
        for spec in a.upgrade:
            base, _, up = spec.partition(":")
            if not base or not up:
                raise SystemExit(f"--upgrade wants BASE:UPGRADED, got {spec!r}")
            for (scope, regime, contract, policy), mine in sorted(cells.items()):
                if contract != up or (a.policy and policy not in a.policy):
                    continue
                theirs = cells.get((scope, regime, base, policy))
                if theirs is None:
                    continue
                eps = sorted(set(mine) & set(theirs))
                d = [val(mine[e]) - val(theirs[e]) for e in eps]
                ref_mean = mean(val(theirs[e]) for e in eps) if eps else 0.0
                # the base contract may offer no backward evidence at all (K0):
                # only the upgraded side must have acted
                acted = policy not in RESOLVERS or any(_activated(mine[e]) for e in eps)
                rows.append(_row("A_contract", scope, regime, f"{base}->{up}", policy, base, eps, d, ref_mean,
                                 _delta(a, theirs, eps, ref_mean) if eps else 0.0, a, (acted, f"{policy} on {up}")))
    else:
        if not a.policy or not a.reference:
            raise SystemExit("experiment B needs --policy and --reference (or use --upgrade for experiment A)")
        for policy in a.policy:
            for (scope, regime, contract, pol), mine in sorted(cells.items()):
                if pol != policy:
                    continue
                theirs = cells.get((scope, regime, contract, a.reference))
                if theirs is None:
                    continue
                eps = sorted(set(mine) & set(theirs))
                d = [val(mine[e]) - val(theirs[e]) for e in eps]
                ref_mean = mean(val(theirs[e]) for e in eps) if eps else 0.0
                sides = ((policy, mine), (a.reference, theirs))
                lazy = [p for p, side in sides if p in RESOLVERS and not any(_activated(side[e]) for e in eps)]
                noact = bool(lazy) and all(all(_escalate_only(side[e]) for e in eps) for p, side in sides if p in lazy)
                rows.append(_row("B_policy", scope, regime, contract, policy, a.reference, eps, d, ref_mean,
                                 _delta(a, theirs, eps, ref_mean) if eps else 0.0, a, (not lazy, ",".join(lazy), noact)))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--csv", required=True, action="append")
    ap.add_argument("--policy", action="append", default=[], help="repeatable")
    ap.add_argument("--reference", help="experiment B reference policy")
    ap.add_argument("--upgrade", action="append", default=[], help="experiment A: BASE:UPGRADED contract pair (repeatable)")
    ap.add_argument("--metric", default="clean_fulfilled")
    ap.add_argument("--delta", type=float, default=None)
    ap.add_argument("--relative", action="store_true", help="delta is a fraction of the reference mean")
    ap.add_argument("--delta-pp", type=float, default=None, help="delta in percentage points of the offered requests")
    ap.add_argument("--equivalence", action="store_true", help="size a TOST equivalence test with margin delta")
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument("--power", type=float, default=0.8)
    ap.add_argument("--comparisons", type=int, default=1)
    ap.add_argument("--json", help="also write the rows as JSON here")
    a = ap.parse_args(argv)
    if (a.delta is None) == (a.delta_pp is None):
        raise SystemExit("give exactly one of --delta or --delta-pp")
    rows = plan(a)
    print(f"{'comparison':10s} {'regime':22s} {'contract':16s} {'policy':14s} {'vs':14s} {'pilot_n':>7s} {'ref_mean':>9s} "
          f"{'mean_d':>9s} {'sd_d':>9s} {'delta':>8s} {'n_req':>18s} {'boot_pow@n':>10s}")
    for r in rows:
        f = lambda k, w, spec: (format(r[k], spec) if isinstance(r[k], (int, float)) else "-").rjust(w)  # noqa: E731
        n_txt = str(r["n_required"]) if r["status"] == "SIZED" else r["status"]
        print(f"{r['comparison']:10s} {r['regime'][:22]:22s} {r['contract'][:16]:16s} {r['policy'][:14]:14s} {r['reference'][:14]:14s} "
              f"{r['pilot_n']:7d} {f('ref_mean', 9, '.3f')} {f('mean_d', 9, '.3f')} {f('sd_d', 9, '.3f')} {f('delta', 8, '.3f')} "
              f"{n_txt:>18s} {f('bootstrap_power', 10, '.2f')}")
        if r.get("note"):
            print(f"  note: {r['note']}")
    if not rows:
        what = f"--upgrade {' '.join(a.upgrade)}" if a.upgrade else f"{','.join(a.policy)} vs {a.reference}"
        print(f"NO_PAIRS: no (regime, contract) group has both sides of {what} in {', '.join(a.csv)}")
    if a.json:
        with open(a.json, "w") as fh:
            json.dump({"design": "equivalence" if a.equivalence else "superiority", "metric": a.metric, "alpha": a.alpha,
                       "power": a.power, "comparisons": a.comparisons, "rows": rows,
                       "status": "NO_PAIRS" if not rows else ("SIZED" if all(r["status"] in ("SIZED", "NO_ACTION_IN_CONTRACT")
                                                                             for r in rows) else "INCOMPLETE")},
                      fh, indent=2)
            fh.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
