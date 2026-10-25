#!/usr/bin/env python3
"""Safety over every testbed campaign (release 075).

Each preregistered plan checks safety in its own run. This script adds them up: over every VALID cell of
every TESTBED campaign it reads, the safety counts (bound_violations, duplicate_effects,
logical_duplicates, false_stopped, false_applied) of the cells where EviCon's guards were on, against the
negative controls, where the plan expected violations:

  negative control   a NEG_* contract (owners built to break their contract, release 072), or a
                     pseudo-policy with the T4 retry guard off (NAIVE) on an owner without dedup (a real
                     owner's mode PLAIN, experiments C and C2): violations planned
  unguarded, dedup   NAIVE on an owner with dedup (MinIO's conditional PUT, the firmware's cutoff mode):
                     no violation planned (C2 of experiment C, C2-2 of experiment C2)
  guarded            every other cell: EVICON, M1, the same-capability baselines, the pilots, the RQ4
                     load campaigns

For the guarded cells it reports the totals and, with zero violations among N admitted commands (or
cells), the exact one-sided 95 % upper bound on the violation probability, 1 - 0.05^(1/N) (about 3/N).
For the negative controls it reports how many cells detected a violation (the harness's sensitivity).
Late effects (validity, not safety) are reported separately. DEV_LOCAL campaigns are listed, not counted.

    python3 evaluation/safety_total.py --out <dir> <campaign run dir> ...
Writes SAFETY.md and safety.json.
"""
from __future__ import annotations

import argparse
import csv
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


def read_run(run: Path) -> dict:
    cfg = json.loads((run / "config.json").read_text()) if (run / "config.json").exists() else {}
    man = json.loads((run / "manifest.json").read_text()) if (run / "manifest.json").exists() else {}
    off = unguarded(cfg)
    out = {"run": f"{cfg.get('name', run.parent.name)}/{run.name}", "dir": str(run), "release": man.get("release", ""),
           "scope": man.get("scope", ""), "cells": 0, "invalid": 0, "guarded": Counter(), "control": defaultdict(Counter),
           "unguarded_dedup": defaultdict(Counter)}
    with open(run / "runtime_episodes.csv", newline="") as fh:
        for r in csv.DictReader(fh):
            if not str(r.get("cell_status", "")).startswith("VALID"):
                out["invalid"] += 1
                continue
            out["cells"] += 1
            # release 077: the RELEASE_ON_TIMEOUT and RELEASE_ON_READ baselines break rule R2 on purpose
            # (planned violations)
            neg = (r["contract"].startswith("NEG_") or (r["policy"] in off and plain(cfg, r["contract"]))
                   or r["policy"] in ("RELEASE_ON_TIMEOUT", "RELEASE_ON_READ"))
            viol = {k: _f(r.get(k)) for k in KINDS}
            if neg or r["policy"] in off:
                c = out["control" if neg else "unguarded_dedup"][f"{r['contract']}/{r['policy']}"]
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
    return out


def summarize(runs: list) -> dict:
    tb = [r for r in runs if r["scope"] == "TESTBED"]
    g = Counter()
    for r in tb:
        g.update(r["guarded"])
    viol = int(sum(g[k] for k in KINDS))
    ctl, ud = Counter(), Counter()
    for r in tb:
        for c in r["control"].values():
            ctl["cells"] += c["cells"]
            ctl["detecting_cells"] += c["detecting_cells"]
        for c in r["unguarded_dedup"].values():
            ud["cells"] += c["cells"]
            ud["cells_with_violation"] += c["detecting_cells"]
    return {"testbed_runs": len(tb), "guarded": {k: int(v) for k, v in g.items()}, "guarded_violations": viol,
            "upper95_per_admitted_command": upper95(int(g["admitted"])) if viol == 0 else None,
            "upper95_per_dispatch_attempt": upper95(int(g["dispatch_attempts"])) if viol == 0 else None,
            "upper95_per_cell": upper95(int(g["cells"])) if viol == 0 else None,
            "controls": {k: int(v) for k, v in ctl.items()}, "unguarded_dedup": {k: int(v) for k, v in ud.items()},
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
    L += [f"Negative controls (violations planned): {s['controls'].get('detecting_cells', 0)} of {s['controls'].get('cells', 0)} cells detected a "
          f"violation. Blind retry on owners with dedup (no violation planned): {s['unguarded_dedup'].get('cells_with_violation', 0)} of "
          f"{s['unguarded_dedup'].get('cells', 0)} cells with a violation.", "",
          "| campaign | release | scope | VALID cells | INVALID | guarded cells | admitted | guarded violations | late effects | negative controls: cells detecting / cells |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for r in runs:
        gv = int(sum(r["guarded"][k] for k in KINDS))
        ctl = "; ".join(f"{k} {int(c['detecting_cells'])}/{int(c['cells'])}" for k, c in sorted(r["control"].items())) or "-"
        L.append(f"| {r['run']} | {r['release']} | {r['scope']} | {r['cells']} | {r['invalid']} | {int(r['guarded']['cells'])} | "
                 f"{int(r['guarded']['admitted'])} | {gv} | {int(r['guarded']['late_effects'])} | {ctl} |")
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
                                      unguarded_dedup={k: dict(v) for k, v in r["unguarded_dedup"].items()}) for r in runs]}
    (out / "safety.json").write_text(json.dumps(js, indent=1) + "\n")
    (out / "SAFETY.md").write_text(markdown(runs, s))
    print(f"safety_total: {len(runs)} campaigns, {s['verdict']} -> {out}")
    return 0 if s["guarded_violations"] == 0 else 3


if __name__ == "__main__":
    sys.exit(main())
