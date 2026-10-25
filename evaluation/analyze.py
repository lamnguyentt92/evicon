#!/usr/bin/env python3
"""Analysis of simulator (evicon-des episodes.csv) and runtime campaign
(campaign.py runtime_episodes.csv) results (release 065).

  python3 evaluation/analyze.py --csv out/sim/*/episodes.csv --csv out/runtime/*/*/runtime_episodes.csv \
      --reference B2_LEDGER --reference RES_IMMEDIATE --out out/analysis

Outputs (CSV + Markdown):
  summary          per (scope, regime, effective contract, policy): clean useful
                   work, stranded capacity-time (resolved and censored), every
                   safety counter as a total (never averaged away), validity
                   (late) effects separately, resolution actions, activation;
  paired           experiment B (policy value): policy vs reference on the SAME
                   effective contract and the same episodes (common random
                   numbers), bootstrap CI, Holm correction per metric;
  contract_upgrade experiment A (contract value): the same policy on the
                   upgraded contract vs the base contract, paired by episode:
                   "<c>+F" vs "<c>" (simulator), the runtime presets K0F/K0,
                   K1F/K1, K3F/K2, and any --upgrade BASE:UPGRADED.
With --margin-pp X, every clean_fulfilled comparison also gets a TOST
equivalence p-value for |difference| < X percentage points of the offered
requests (hypotheses H-A2 and H-B of the preregistration).
Rows marked INVALID by the campaign are excluded and listed; resolver cells
whose policy never acted are flagged NOT_ACTIVATED, or NO_ACTION_IN_CONTRACT
when every decision was ESCALATE (the contract offers that policy no
evidence action, e.g. K0, or K2 for RES_PERIODIC). DEV_LOCAL,
SIMULATION_ONLY and TESTBED rows are never mixed in one group.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
from collections import defaultdict
from pathlib import Path

# Safety (fixed before any run; must be zero for EviCon): reported as totals.
SAFETY = ("bound_violations", "duplicate_effects", "false_stopped", "false_applied", "logical_duplicates")
# Validity: an effect after the request's validity window / after the client
# gave up. Reported separately: without owner expiry (T) a queued delivery can
# apply late under every policy, the full-information reference included.
VALIDITY = ("late_effects", "late_effects_after_client_gave_up")
# Deadline miss: exactly one effect, but after the request's deadline (it may
# still be inside the validity window). Not a validity violation (release 065:
# 064 counted it under "late effects").
DEADLINE = ("fulfilled_late",)
LATE = VALIDITY + DEADLINE
# Experiment A pairs of the runtime presets (upgraded -> base); "<c>+F" is
# recognised by name (simulator).
RUNTIME_UPGRADES = {"K0F": "K0", "K1F": "K1", "K3F": "K2"}
ACTIONS = ("queries", "stops", "fences", "reissues", "escalations", "evicon_decisions", "stops_terminal", "fences_ok")
USEFUL = "clean_fulfilled"
STRANDED = ("stranded_resolved_unit_sec", "stranded_censored_unit_sec")
RESOLVERS = {"EVICON", "RES_IMMEDIATE", "RES_QUERY_FIRST", "RES_FIXED", "RES_PERIODIC"}
LABELS = {
    "ORACLE": "full-information reference (not an offline optimum)",
    "B0_NAIVE_NEW_ID": "negative control: timeout then new identity",
    "B1_DURABLE_RETRY": "same-identity retry without horizon check",
    "B2_LEDGER": "safe ledger (no backward evidence)",
    "B3_TIMEOUT_CANCEL": "negative control: best-effort cancel",
    "B4_UNSAFE_FRONTIER": "negative control: simplified frontier release (NOT Atomix)",
    "B5_SELECTOR_M4": "ledger + M4 query selector (not M5/M5V2)",
    "EVICON": "EviCon (ski-rental threshold, certificates)",
    "RES_IMMEDIATE": "same-capability baseline: immediate stop/fence",
    "RES_QUERY_FIRST": "same-capability baseline: query, then immediate",
    "RES_FIXED": "same-capability baseline: fixed wait",
    "RES_PERIODIC": "same-capability baseline: periodic resolution",
    "M0": "legacy runtime baseline (no stop/fence)",
    "M1": "legacy query selector (no stop/fence)",
}


def _num(v):
    if v is None:
        return v
    try:
        if v.strip() == "":
            return v
        f = float(v)
        return int(f) if f.is_integer() and "." not in v else f
    except (ValueError, AttributeError):
        return v


def read_rows(paths):
    rows, invalid = [], []
    for p in paths:
        p = Path(p)
        scope = "SIMULATION_ONLY"
        man = p.parent / "manifest.json"
        if man.exists():
            try:
                scope = json.loads(man.read_text()).get("scope", scope)
            except ValueError:
                pass
        with open(p, newline="") as fh:
            for r in csv.DictReader(fh):
                r = {k: _num(v) for k, v in r.items()}
                r["scope"] = r.get("scope") or scope
                r["contract"] = str(r.get("contract", ""))           # effective contract, "+F" kept
                r["contract_base"] = r["contract"].removesuffix("+F")
                r["_source"] = str(p)
                if str(r.get("cell_status", "VALID")).upper().startswith("INVALID"):
                    invalid.append(r)
                    continue
                rows.append(r)
    return rows, invalid


def _f(r, k):
    v = r.get(k)
    return float(v) if isinstance(v, (int, float)) else 0.0


def bootstrap_ci(x, n=2000, seed=61, alpha=0.05):
    if not x:
        return (math.nan, math.nan)
    rng = random.Random(seed)
    m = len(x)
    means = sorted(sum(x[rng.randrange(m)] for _ in range(m)) / m for _ in range(n))
    return means[int(alpha / 2 * n)], means[int((1 - alpha / 2) * n) - 1]


def paired_p(x):
    """Two-sided p for mean(x) == 0 from the paired t statistic with a normal
    reference (adequate for >= 30 CRN pairs; flagged otherwise). Zero
    variance: p = 1 if the mean is 0, else ~0."""
    m = len(x)
    if m < 2:
        return math.nan
    mu = sum(x) / m
    var = sum((v - mu) ** 2 for v in x) / (m - 1)
    if var == 0:
        return 1.0 if mu == 0 else 0.0
    t = mu / math.sqrt(var / m)
    return math.erfc(abs(t) / math.sqrt(2))


def holm(pvals):
    order = sorted(range(len(pvals)), key=lambda i: pvals[i])
    adj = [0.0] * len(pvals)
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (len(pvals) - rank) * pvals[i]))
        adj[i] = running
    return adj


def activated(r) -> bool:
    """A resolver cell acted at least once (runtime: EVICON_* decisions that
    executed an action; simulator: a query, stop or fence)."""
    if "activated" in r:
        return str(r["activated"]).lower() in ("1", "true")
    return (_f(r, "queries") + _f(r, "stops") + _f(r, "fences")) > 0


def escalate_only(r) -> bool:
    """A runtime resolver cell whose every decision was ESCALATE: the contract
    offers this policy no evidence action (K0 has no Q/F/X; RES_PERIODIC does
    not query, so K2 gives it nothing). Expected, not a regime fault (066)."""
    raw = r.get("decisions_by_action") or ""
    if not raw:
        return False
    try:
        d = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (ValueError, TypeError):
        return False
    return bool(d) and set(d) == {"ESCALATE"}


def summarize(rows):
    groups = defaultdict(list)
    for r in rows:
        groups[(r["scope"], r.get("regime", ""), r["contract"], r["policy"])].append(r)
    out = []
    for (scope, regime, contract, policy), g in sorted(groups.items()):
        offered = sum(_f(r, "offered") for r in g)
        useful = sum(_f(r, USEFUL) for r in g)
        s = {"scope": scope, "regime": regime, "contract": contract, "contract_base": g[0]["contract_base"],
             "level": g[0].get("level", ""), "policy": policy, "policy_role": LABELS.get(policy, ""),
             "episodes": len(g), "clean_fulfilment_rate": round(useful / offered, 4) if offered else math.nan,
             "clean_fulfilled_mean": round(useful / len(g), 3)}
        for k in STRANDED:
            if k in g[0]:
                s[k + "_mean"] = round(sum(_f(r, k) for r in g) / len(g), 3)
        for k in SAFETY + LATE:
            if k in g[0]:
                s[k + "_total"] = int(sum(_f(r, k) for r in g))
        for k in ACTIONS:
            if k in g[0]:
                s[k + "_mean"] = round(sum(_f(r, k) for r in g) / len(g), 3)
        if policy in RESOLVERS:
            act = sum(1 for r in g if activated(r))
            s["episodes_activated"] = act
            s["activation"] = "NOT_ACTIVATED" if act == 0 else ("PARTIAL" if act < len(g) else "ACTIVATED")
            if act == 0 and all(escalate_only(r) for r in g):
                s["activation"] = "NO_ACTION_IN_CONTRACT"
        ref = groups.get((scope, regime, contract, "ORACLE"))
        if ref and policy != "ORACLE":
            ru = sum(_f(r, USEFUL) for r in ref)
            # signed: negative means the policy beat the reference heuristic
            s["gap_to_full_info_reference"] = round(1 - useful / ru, 4) if ru else math.nan
        out.append(s)
    return out, groups


def _paired(mine, theirs, metric):
    eps = sorted(set(mine) & set(theirs))
    return eps, [_f(mine[e], metric) - _f(theirs[e], metric) for e in eps]


def _finish(comps, metrics):
    for metric in metrics:
        idx = [i for i, c in enumerate(comps) if c["metric"] == metric]
        adj = holm([comps[i]["p_t"] for i in idx])
        for i, a in zip(idx, adj):
            comps[i]["p_holm"] = float(f"{a:.3g}")
            comps[i]["p_t"] = float(f"{comps[i]['p_t']:.3g}")
        tidx = [i for i in idx if not math.isnan(comps[i].get("p_tost", math.nan))]
        for i, a in zip(tidx, holm([comps[i]["p_tost"] for i in tidx])):
            comps[i]["p_tost_holm"] = float(f"{a:.3g}")
            comps[i]["p_tost"] = float(f"{comps[i]['p_tost']:.3g}")
            comps[i]["equivalent_at_0.05"] = a < 0.05
    return comps


def policy_comparisons(groups, references, n_boot, margin_pp=None):
    """Experiment B: same effective contract, same episodes."""
    comps = []
    metrics = (USEFUL,) + STRANDED
    for (scope, regime, contract, policy), g in sorted(groups.items()):
        mine = {r["episode"]: r for r in g}
        for ref in references:
            if ref == policy or (scope, regime, contract, ref) not in groups:
                continue
            theirs = {r["episode"]: r for r in groups[(scope, regime, contract, ref)]}
            for metric in metrics:
                if metric not in g[0]:
                    continue
                eps, d = _paired(mine, theirs, metric)
                if len(eps) < 2:
                    continue
                lo, hi = bootstrap_ci(d, n_boot)
                comps.append({"experiment": "B_policy", "scope": scope, "regime": regime, "contract": contract,
                              "policy": policy, "reference": ref, "metric": metric, "pairs": len(eps),
                              "mean_diff": round(sum(d) / len(d), 4), "ci95_lo": round(lo, 4), "ci95_hi": round(hi, 4),
                              "p_t": paired_p(d), "p_note": "" if len(eps) >= 30 else "n<30: normal approx. optimistic"})
                _equivalence(comps[-1], d, mine, theirs, eps, margin_pp)
    return _finish(comps, metrics)


def regime_base(regime: str, contract: str) -> str:
    """The simulator names a sweep entry's regime '<regime>/<contract>'."""
    for c in (contract, contract.removesuffix("+F")):
        if regime.endswith("/" + c):
            return regime[: -len(c) - 1]
    return regime.removesuffix("+F")


def upgrade_pairs(groups, extra=None):
    """[(upgraded group key, base group key)] for experiment A."""
    pairs = dict(RUNTIME_UPGRADES)
    for spec in extra or []:
        base, _, up = spec.partition(":")
        if not base or not up:
            raise SystemExit(f"--upgrade wants BASE:UPGRADED, got {spec!r}")
        pairs[up] = base
    by_base = {}
    for (scope, regime, contract, policy) in groups:
        by_base[(scope, regime_base(regime, contract), contract, policy)] = (scope, regime, contract, policy)
    out = []
    for (scope, rb, contract, policy), key in sorted(by_base.items()):
        base = pairs.get(contract) or (contract.removesuffix("+F") if contract.endswith("+F") else None)
        if base is None:
            continue
        other = by_base.get((scope, rb, base, policy))
        if other is not None:
            out.append((key, other, rb, base))
    return out


def _tost(d, margin):
    """TOST p-value (normal approximation) for |mean(d)| < margin."""
    m = len(d)
    if m < 2 or margin <= 0:
        return math.nan
    mu = sum(d) / m
    var = sum((v - mu) ** 2 for v in d) / (m - 1)
    if var == 0:
        return 0.0 if abs(mu) < margin else 1.0
    se = math.sqrt(var / m)
    phi = lambda x: 0.5 * math.erfc(-x / math.sqrt(2))  # noqa: E731
    return max(1 - phi((mu + margin) / se), 1 - phi((margin - mu) / se))


def _equivalence(comp, d, mine, theirs, eps, margin_pp):
    if margin_pp is None or comp["metric"] != USEFUL:
        return
    offered = sum(_f(theirs[e], "offered") for e in eps) / len(eps)
    margin = margin_pp / 100.0 * offered
    comp["margin"] = round(margin, 3)
    comp["p_tost"] = _tost(d, margin)


def contract_upgrades(groups, n_boot, extra=None, margin_pp=None):
    """Experiment A: the same policy on the upgraded contract vs its base,
    paired by episode (the simulator's regime suffix is normalised)."""
    comps = []
    metrics = (USEFUL,) + STRANDED
    for key, base_key, breg, base in upgrade_pairs(groups, extra):
        scope, regime, contract, policy = key
        g = groups[key]
        mine = {r["episode"]: r for r in g}
        theirs = {r["episode"]: r for r in groups[base_key]}
        for metric in metrics:
            if metric not in g[0]:
                continue
            eps, d = _paired(mine, theirs, metric)
            if len(eps) < 2:
                continue
            lo, hi = bootstrap_ci(d, n_boot)
            comps.append({"experiment": "A_contract", "scope": scope, "regime": breg, "policy": policy,
                          "contract": base, "upgraded": contract, "metric": metric, "pairs": len(eps),
                          "mean_diff": round(sum(d) / len(d), 4), "ci95_lo": round(lo, 4), "ci95_hi": round(hi, 4),
                          "p_t": paired_p(d), "p_note": "" if len(eps) >= 30 else "n<30: normal approx. optimistic"})
            _equivalence(comps[-1], d, mine, theirs, eps, margin_pp)
    return _finish(comps, metrics)


def write_table(rows, path: Path, cols=None):
    if not rows:
        return
    cols = cols or list(dict.fromkeys(k for r in rows for k in r))
    with open(path.with_suffix(".csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        lines.append("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")
    path.with_suffix(".md").write_text("\n".join(lines) + "\n")


def figures(summary, out: Path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return []
    made = []
    by = defaultdict(list)
    for s in summary:
        by[(s["scope"], s["regime"].split("/")[0])].append(s)
    for (scope, regime), ss in by.items():
        contracts = sorted({s["contract"] for s in ss}, key=lambda c: (str(next((x["level"] for x in ss if x["contract"] == c), "")), c))
        policies = sorted({s["policy"] for s in ss})
        if len(policies) < 2:
            continue
        fig, axes = plt.subplots(1, 2, figsize=(max(8, 1.2 * len(contracts) * 2), 3.8))
        width = 0.8 / len(policies)
        for ai, (metric, label) in enumerate((("clean_fulfilment_rate", "clean fulfilment rate"),
                                              ("stranded_resolved_unit_sec_mean", "stranded capacity-time, resolved (unit·s)"))):
            ax = axes[ai]
            for pi, p in enumerate(policies):
                ys = [next((s.get(metric, math.nan) for s in ss if s["contract"] == c and s["policy"] == p), math.nan) for c in contracts]
                ax.bar([i + pi * width for i in range(len(contracts))], ys, width, label=p)
            ax.set_xticks([i + 0.4 - width / 2 for i in range(len(contracts))])
            ax.set_xticklabels(contracts, rotation=30, ha="right", fontsize=8)
            ax.set_ylabel(label)
        axes[0].legend(fontsize=6, ncol=2)
        fig.suptitle(f"{regime} [{scope}]", fontsize=10)
        fig.tight_layout()
        f = out / f"fig_{scope.lower()}_{regime}.png".replace("/", "_")
        fig.savefig(f, dpi=150)
        plt.close(fig)
        made.append(str(f))
    return made


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", action="append", required=True)
    ap.add_argument("--reference", action="append", default=[])
    ap.add_argument("--out", default="out/analysis")
    ap.add_argument("--bootstrap", type=int, default=2000)
    ap.add_argument("--upgrade", action="append", default=[], help="experiment A pair BASE:UPGRADED (besides K0F/K1F/K3F and <c>+F)")
    ap.add_argument("--margin-pp", type=float, default=None, help="TOST equivalence margin, percentage points of offered")
    a = ap.parse_args(argv)
    rows, invalid = read_rows(a.csv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    summary, groups = summarize(rows)
    comps = policy_comparisons(groups, a.reference or ["B2_LEDGER", "RES_IMMEDIATE", "M1"], a.bootstrap, a.margin_pp)
    upgrades = contract_upgrades(groups, a.bootstrap, a.upgrade, a.margin_pp)
    write_table(summary, out / "summary")
    write_table(comps, out / "paired")
    write_table(upgrades, out / "contract_upgrade")
    unsafe = [s for s in summary if any(s.get(k + "_total", 0) for k in SAFETY)]
    validity = [s for s in summary if any(s.get(k + "_total", 0) for k in VALIDITY)]
    deadline = [s for s in summary if any(s.get(k + "_total", 0) for k in DEADLINE)]
    not_act = [s for s in summary if s.get("activation") == "NOT_ACTIVATED"]
    figs = figures(summary, out)
    report = {"inputs": a.csv, "scopes": sorted({r["scope"] for r in rows}), "groups": len(summary),
              "policy_comparisons": len(comps), "contract_upgrade_comparisons": len(upgrades),
              "invalid_cells_excluded": [(r.get("regime"), r.get("contract"), r.get("policy"), r.get("episode"), r.get("cell_status")) for r in invalid],
              "groups_with_safety_violations": [(s["scope"], s["regime"], s["contract"], s["policy"]) for s in unsafe],
              # validity violations: an effect after the validity window or after the client gave up
              "groups_with_late_effects": len(validity),
              "late_effects_total": int(sum(s.get("late_effects_total", 0) for s in summary)),
              "late_effects_after_client_gave_up_total": int(sum(s.get("late_effects_after_client_gave_up_total", 0) for s in summary)),
              # deadline misses: one effect, after the deadline (not a validity violation)
              "groups_with_fulfilled_late": len(deadline),
              "fulfilled_late_total": int(sum(s.get("fulfilled_late_total", 0) for s in summary)),
              "resolver_groups_not_activated": [(s["scope"], s["regime"], s["contract"], s["policy"]) for s in not_act],
              # 066: the resolver ran but the contract gave it nothing to do (every decision ESCALATE)
              "resolver_groups_no_action_in_contract": [(s["scope"], s["regime"], s["contract"], s["policy"]) for s in summary
                                                        if s.get("activation") == "NO_ACTION_IN_CONTRACT"],
              "figures": figs}
    (out / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
