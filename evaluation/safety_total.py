#!/usr/bin/env python3
"""Safety over every testbed campaign (release 075; release 079: the cells unsafe by design apart).

Each preregistered plan checks safety in its own run. This script adds them up: over every VALID cell of
every TESTBED campaign it reads, the safety counts (bound_violations, duplicate_effects,
logical_duplicates, false_stopped, false_applied) of the cells where EviCon's guards were on, against the
negative controls, where the plan expected violations:

  negative control   a NEG_* contract (owners built to break their contract, release 072), or a
                     pseudo-policy with the T4 retry guard off (NAIVE) on an owner without dedup (a real
                     owner's mode PLAIN, experiments C and C2): violations planned
  unsafe by design   (release 079) cells planned to be able to violate safety, without a violation being
                     required in every cell: the practice baselines RELEASE_ON_TIMEOUT and RELEASE_ON_READ
                     (release 077), a blind retry (NAIVE) on ThingsBoard's RPC (experiment TB), and the plain
                     firmware with an at-least-once binding under the offline cut (experiment Q); the same
                     (contract, policy) groups as the driver's planned violations (planned_unsafe)
  unguarded, dedup   NAIVE on an owner with dedup (MinIO's conditional PUT, the firmware's cutoff mode):
                     no violation planned (C2 of experiment C, C2-2 of experiment C2)
  guarded            every other cell: EVICON, M1, the same-capability baselines, the pilots, the RQ4
                     load campaigns

For the guarded cells it reports the totals and, with zero violations among N admitted commands (or
cells), the exact one-sided 95 % upper bound on the violation probability, 1 - 0.05^(1/N) (about 3/N).
Release 083 (analysis A1): cells of one episode share one fault schedule (common random numbers), and several
campaigns replay the same seeded schedules, so the cells are not independent trials; the defensible unit is
the distinct fault schedule. The report counts the distinct fault schedules (the "faults" part of each cell's
schedule_ep<e>.json, compared byte for byte after canonical JSON) behind the guarded VALID cells with at least
one fault window, N_s, and gives the per-schedule bound 1 - 0.05^(1/N_s) next to the per-cell one.
For the negative controls it reports how many cells detected a violation (the harness's sensitivity; every
cell must); for the cells unsafe by design, how many had a violation (the plans' detection rules decide).
Late effects (validity, not safety) are reported separately. DEV_LOCAL campaigns are listed, not counted.

    python3 evaluation/safety_total.py --out <dir> <campaign run dir> ...
Writes SAFETY.md and safety.json.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

KINDS = ("bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied")


def upper95(n: int) -> float | None:
    """Exact (Clopper-Pearson) one-sided 95 % upper bound on a probability with 0 events in n trials."""
    return None if n <= 0 else 1.0 - 0.05 ** (1.0 / n)


def _f(x) -> float:
    try:
        return float(x or 0)
    except ValueError:
        return 0.0


def unguarded(cfg: dict) -> set:
    ro = cfg.get("real_owner") or {}
    return {p for p, spec in (ro.get("policies") or {}).items() if (spec.get("authority") or {}).get("retry_guard") is False}


def plain(cfg: dict, contract: str) -> bool:
    return ((cfg.get("real_owner") or {}).get("contracts") or {}).get(contract, {}).get("mode") == "PLAIN"


# The practice baselines, unsafe by design (release 077; release 083: RELEASE_ON_READ_ANSWERED and the release-time
# variants of RELEASE_ON_TIMEOUT, PREREG_extra_083.md T2, T3). The one definition: evaluation/t3_attribution.py,
# evaluation/claims_check.py (T3_BASELINES) and evicon_review.py (RELEASE_BASELINES) use this tuple.
BASELINES = ("RELEASE_ON_TIMEOUT", "RELEASE_ON_READ", "RELEASE_ON_READ_ANSWERED",
             "RELEASE_ON_TIMEOUT_30S", "RELEASE_ON_TIMEOUT_60S", "RELEASE_ON_TIMEOUT_120S")


def planned_unsafe(cfg: dict) -> set:
    """(contract, policy) groups of a real-owner campaign planned to violate safety (release 079: the one
    definition, also used by evicon_review.py): a pseudo-policy with the T4 retry guard off on an owner call
    shape without dedup (mode PLAIN; ThingsBoard's RPC, which has no idempotency key), and, with the
    offline cut (experiment Q), the plain firmware with an at-least-once binding (QoS 1), which breaks the
    adapter premise of no duplicate below the runtime on purpose."""
    ro = cfg.get("real_owner") or {}
    cts = ro.get("contracts") or {}
    out = set()
    for pol in unguarded(cfg):
        if pol in cfg.get("policies", []):
            out |= {(ct, pol) for ct in cfg.get("contracts", [])
                    if (cts.get(ct) or {}).get("mode") == "PLAIN" or ro.get("system") == "thingsboard"}
    if ro.get("offline_cut"):
        out |= {(ct, pol) for ct in cfg.get("contracts", []) for pol in cfg.get("policies", [])
                if (cts.get(ct) or {}).get("mode") == "PLAIN" and (cts.get(ct) or {}).get("binding") == "qos1"}
    return out


def kind(cfg: dict, contract: str, policy: str, planned: set = None) -> str:
    """control (a violation must be detected in every cell), unsafe (by design), unguarded_dedup, guarded."""
    planned = planned_unsafe(cfg) if planned is None else planned
    if contract.startswith("NEG_"):
        return "control"
    if policy in unguarded(cfg) and plain(cfg, contract) and (cfg.get("real_owner") or {}).get("system") != "thingsboard":
        return "control"  # a blind retry on an owner without dedup under the testbed's faults (experiments C, C2)
    if policy in BASELINES or (contract, policy) in planned:
        return "unsafe"
    if policy in unguarded(cfg):
        return "unguarded_dedup"
    return "guarded"


def fault_schedule_digest(run: Path, episode) -> str:
    """SHA-256 of the fault windows of one episode's schedule (release 083, A1): the 'faults' list of
    schedule_ep<e>.json in canonical JSON. Empty when the file is missing or has no fault window."""
    p = run / f"schedule_ep{episode}.json"
    if not p.is_file():
        return ""
    try:
        faults = json.loads(p.read_text()).get("faults") or []
    except (ValueError, AttributeError):
        return ""
    if not faults:
        return ""
    return hashlib.sha256(json.dumps(faults, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def read_run(run: Path) -> dict:
    cfg = json.loads((run / "config.json").read_text()) if (run / "config.json").exists() else {}
    man = json.loads((run / "manifest.json").read_text()) if (run / "manifest.json").exists() else {}
    planned = planned_unsafe(cfg)
    out = {"run": f"{cfg.get('name', run.parent.name)}/{run.name}", "dir": str(run), "release": man.get("release", ""),
           "scope": man.get("scope", ""), "cells": 0, "invalid": 0, "guarded": Counter(), "control": defaultdict(Counter),
           "unsafe": defaultdict(Counter), "unguarded_dedup": defaultdict(Counter), "guarded_fault_schedules": {}}
    digests = {}
    with open(run / "runtime_episodes.csv", newline="") as fh:
        for r in csv.DictReader(fh):
            if not str(r.get("cell_status", "")).startswith("VALID"):
                out["invalid"] += 1
                continue
            out["cells"] += 1
            k_ = kind(cfg, r["contract"], r["policy"], planned)
            viol = {k: _f(r.get(k)) for k in KINDS}
            if k_ != "guarded":
                c = out[k_][f"{r['contract']}/{r['policy']}"]
                c["cells"] += 1
                c["detecting_cells"] += any(viol.values())
                for k, v in viol.items():
                    c[k] += v
            else:
                g = out["guarded"]
                g["cells"] += 1
                g["offered"] += _f(r.get("offered"))
                g["admitted"] += _f(r.get("admitted"))
                g["dispatch_attempts"] += _f(r.get("dispatch_attempts"))
                g["late_effects"] += _f(r.get("late_effects"))
                for k, v in viol.items():
                    g[k] += v
                g["cells_with_violation"] += any(viol.values())
                if _f(r.get("fault_windows")) > 0:
                    ep = r.get("episode")
                    if ep not in digests:
                        digests[ep] = fault_schedule_digest(run, ep)
                    if digests[ep]:
                        g["cells_with_faults"] += 1
                        out["guarded_fault_schedules"][digests[ep]] = out["guarded_fault_schedules"].get(digests[ep], 0) + 1
                    else:
                        g["cells_with_faults_no_schedule"] += 1
    return out


def summarize(runs: list) -> dict:
    tb = [r for r in runs if r["scope"] == "TESTBED"]
    g = Counter()
    for r in tb:
        g.update(r["guarded"])
    viol = int(sum(g[k] for k in KINDS))
    ctl, ud, us = Counter(), Counter(), Counter()
    for r in tb:
        for c in r["control"].values():
            ctl["cells"] += c["cells"]
            ctl["detecting_cells"] += c["detecting_cells"]
        for c in r.get("unsafe", {}).values():
            us["cells"] += c["cells"]
            us["cells_with_violation"] += c["detecting_cells"]
        for c in r["unguarded_dedup"].values():
            ud["cells"] += c["cells"]
            ud["cells_with_violation"] += c["detecting_cells"]
    sched = Counter()
    for r in tb:
        sched.update(r.get("guarded_fault_schedules", {}))
    n_s = len(sched)
    return {"testbed_runs": len(tb), "guarded": {k: int(v) for k, v in g.items()}, "guarded_violations": viol,
            "distinct_fault_schedules": n_s, "cells_with_faults": int(g["cells_with_faults"]),
            "cells_with_faults_no_schedule": int(g["cells_with_faults_no_schedule"]),
            "max_cells_per_schedule": max(sched.values()) if sched else 0,
            "upper95_per_schedule": upper95(n_s) if viol == 0 else None,
            "upper95_per_cell_with_faults": upper95(int(g["cells_with_faults"])) if viol == 0 else None,
            "upper95_per_admitted_command": upper95(int(g["admitted"])) if viol == 0 else None,
            "upper95_per_dispatch_attempt": upper95(int(g["dispatch_attempts"])) if viol == 0 else None,
            "upper95_per_cell": upper95(int(g["cells"])) if viol == 0 else None,
            "controls": {k: int(v) for k, v in ctl.items()}, "unsafe_by_design": {k: int(v) for k, v in us.items()},
            "unguarded_dedup": {k: int(v) for k, v in ud.items()},
            "verdict": "NO VIOLATION IN ANY GUARDED CELL" if viol == 0 else "VIOLATIONS IN GUARDED CELLS"}


def markdown(runs: list, s: dict) -> str:
    g = s["guarded"]
    L = ["# Safety over every testbed campaign", "",
         f"**{s['verdict']}**: {g.get('cells', 0)} guarded VALID cells in {s['testbed_runs']} TESTBED campaigns, "
         f"{g.get('admitted', 0)} admitted commands, {g.get('dispatch_attempts', 0)} dispatch attempts; "
         f"violations {s['guarded_violations']} (bound, duplicate, logical duplicate, false STOPPED, false APPLIED); late effects "
         f"{g.get('late_effects', 0)} (validity, reported separately).", ""]
    if s["guarded_violations"] == 0 and g.get("admitted"):
        L += [f"One-sided 95 % upper bound (exact, zero events): per admitted command {s['upper95_per_admitted_command']:.2e}, per dispatch "
              f"attempt {s['upper95_per_dispatch_attempt']:.2e}, per cell {s['upper95_per_cell']:.3f}.", ""]
    if s.get("distinct_fault_schedules"):
        ub = s.get("upper95_per_schedule")
        L += [f"Distinct fault schedules (A1): the {s['cells_with_faults']} guarded VALID cells with at least one fault window ran on "
              f"**{s['distinct_fault_schedules']} distinct fault schedules** (cells of one episode share its schedule, and several campaigns "
              f"replay the same seeded schedules; at most {s['max_cells_per_schedule']} cells on one schedule)"
              + (f"; {s['cells_with_faults_no_schedule']} cells without a schedule file are not counted" if s.get("cells_with_faults_no_schedule") else "")
              + (f". Exact one-sided 95 % upper bound per schedule, 1 - 0.05^(1/N_s): **{ub:.3f}** (per cell with faults: "
                 f"{s['upper95_per_cell_with_faults']:.4f})." if ub is not None else "."), ""]
    L += [f"Negative controls (violations planned): {s['controls'].get('detecting_cells', 0)} of {s['controls'].get('cells', 0)} cells detected a "
          f"violation. Blind retry on owners with dedup (no violation planned): {s['unguarded_dedup'].get('cells_with_violation', 0)} of "
          f"{s['unguarded_dedup'].get('cells', 0)} cells with a violation. Unsafe by design (the practice baselines, a blind retry on "
          f"ThingsBoard, QoS 1 on the plain firmware; a violation allowed, not required in every cell): "
          f"{s.get('unsafe_by_design', {}).get('cells_with_violation', 0)} of {s.get('unsafe_by_design', {}).get('cells', 0)} cells with a violation.", "",
          "| campaign | release | scope | VALID cells | INVALID | guarded cells | admitted | guarded violations | late effects | negative controls: cells detecting / cells | unsafe by design: cells with a violation / cells |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in runs:
        gv = int(sum(r["guarded"][k] for k in KINDS))
        ctl = "; ".join(f"{k} {int(c['detecting_cells'])}/{int(c['cells'])}" for k, c in sorted(r["control"].items())) or "-"
        uns = "; ".join(f"{k} {int(c['detecting_cells'])}/{int(c['cells'])}" for k, c in sorted(r.get("unsafe", {}).items())) or "-"
        L.append(f"| {r['run']} | {r['release']} | {r['scope']} | {r['cells']} | {r['invalid']} | {int(r['guarded']['cells'])} | "
                 f"{int(r['guarded']['admitted'])} | {gv} | {int(r['guarded']['late_effects'])} | {ctl} | {uns} |")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("runs", nargs="+")
    a = ap.parse_args(argv)
    seen, runs = set(), []
    for d in a.runs:
        p = Path(d).resolve()
        if p in seen or not (p / "runtime_episodes.csv").exists():
            continue
        seen.add(p)
        runs.append(read_run(p))
    runs.sort(key=lambda r: (r["release"], r["run"]))
    s = summarize(runs)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    js = {"summary": s, "runs": [dict(r, guarded=dict(r["guarded"]), control={k: dict(v) for k, v in r["control"].items()},
                                      guarded_fault_schedules=len(r.get("guarded_fault_schedules", {})),
                                      unsafe={k: dict(v) for k, v in r["unsafe"].items()},
                                      unguarded_dedup={k: dict(v) for k, v in r["unguarded_dedup"].items()}) for r in runs]}
    (out / "safety.json").write_text(json.dumps(js, indent=1) + "\n")
    (out / "SAFETY.md").write_text(markdown(runs, s))
    print(f"safety_total: {len(runs)} campaigns, {s['verdict']} -> {out}")
    return 0 if s["guarded_violations"] == 0 else 3


if __name__ == "__main__":
    sys.exit(main())
