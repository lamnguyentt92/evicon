#!/usr/bin/env python3
"""Experiment C at a glance (release 073): one row per contract and policy of an expC-minio run.

Clean and confirmed fulfilment come from the frozen pairs_report.py (per episode, % of offered; mean
and 95 % t interval); safety counts, actions and T4 suppressions from runtime_episodes.csv (release 074:
also the distinct commands the T4 guard withheld a blind retry from; the CSV column counts dispatcher
passes, one per poll while the command waits); tombstones
and effect versions from each cell's MinIO truth (raw/truth/truth.json). Descriptive only: the
hypotheses are decided by pairs_report.py against PREREG_expC_073.md.

    python3 evaluation/expC_summary.py --out <dir> <expC-minio run dir>
Writes EXPC.md and expC.json into --out.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from cadence_report import t_interval  # noqa: E402
from expC2_summary import t4_commands  # noqa: E402  (release 074: commands, not dispatcher passes)
from pairs_report import load_run  # noqa: E402

COUNTS = ("duplicate_effects", "bound_violations", "logical_duplicates", "false_stopped", "false_applied", "late_effects",
          "unresolved_at_end", "queries", "stops", "stops_terminal", "reissues", "retry_suppressed_t4", "dispatch_attempts")


def summarize(run: Path) -> dict:
    r = load_run("C", run)
    rows = {}
    with open(run / "runtime_episodes.csv", newline="") as fh:
        for x in csv.DictReader(fh):
            if x["cell_status"].startswith("VALID"):
                rows.setdefault((x["contract"], x["policy"]), []).append(x)
    groups = []
    for (ct, pol), xs in sorted(rows.items(), key=lambda k: (k[0][0] != "MINIO_PLAIN", k[0][0], ["EVICON", "M1", "NAIVE"].index(k[0][1])
                                                              if k[0][1] in ("EVICON", "M1", "NAIVE") else 9)):
        g = {"contract": ct, "policy": pol, "cells": len(xs)}
        for k in COUNTS:
            g[k] = sum(int(float(x.get(k) or 0)) for x in xs)
        g["cells_with_duplicates"] = sum(1 for x in xs if int(x["duplicate_effects"] or 0) > 0)
        for m in ("clean_pp", "confirmed_pp"):
            v = [c[m] for (a, b, _), c in r["cells"].items() if a == ct and b == pol and c[m] == c[m]]
            g[m] = round(statistics.mean(v), 2) if v else None
            ci = t_interval(v, 0.95) if len(v) > 1 else None
            g[m + "_t95"] = [round(ci[0], 2), round(ci[1], 2)] if ci else None
        tomb = eff = t4 = 0
        for cell in (run / "cells").glob(f"ep*/{ct}/{pol}"):
            t4 += t4_commands(cell)
            t = cell / "raw" / "truth" / "truth.json"
            if t.is_file():
                s = json.loads(t.read_text())["summary"]
                tomb += int(s.get("tombstones", 0))
                eff += int(s.get("effect_versions", 0))
        g["tombstones"], g["effect_versions"] = tomb, eff
        g["t4_withheld_commands"] = t4
        groups.append(g)
    owner = {}
    if (run / "owner.json").is_file():
        o = json.loads((run / "owner.json").read_text())
        owner = {"broker": o.get("broker", {}), "what": o.get("what", "")}
    return {"run": str(run), "invalid": r["invalid"], "groups": groups, "owner": owner}


def markdown(s: dict) -> str:
    L = ["# Experiment C: EviCon on MinIO, at a glance", "", f"Run `{s['run']}`; INVALID cells: {len(s['invalid'])} {s['invalid'][:8]}", "",
         "| contract | policy | cells | clean % [95 %] | confirmed % [95 %] | duplicate effects (cells) | bound violations | other safety | "
         "late effects | unresolved at end | queries | stops (terminal) | tombstones | re-issues | T4: commands withheld (dispatcher passes) |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for g in s["groups"]:
        def c(m):
            return "--" if g[m] is None else f"{g[m]:.1f} [{g[m + '_t95'][0]:.1f}, {g[m + '_t95'][1]:.1f}]" if g[m + "_t95"] else f"{g[m]:.1f}"
        other = g["logical_duplicates"] + g["false_stopped"] + g["false_applied"]
        L.append(f"| {g['contract']} | {g['policy']} | {g['cells']} | {c('clean_pp')} | {c('confirmed_pp')} | {g['duplicate_effects']} "
                 f"({g['cells_with_duplicates']}) | {g['bound_violations']} | {other} | {g['late_effects']} | {g['unresolved_at_end']} | "
                 f"{g['queries']} | {g['stops']} ({g['stops_terminal']}) | {g['tombstones']} | {g['reissues']} | "
                 f"{g['t4_withheld_commands']} ({g['retry_suppressed_t4']}) |")
    if s["owner"]:
        L += ["", f"Owner as run: `{json.dumps(s['owner'])[:600]}`"]
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("run")
    a = ap.parse_args(argv)
    s = summarize(Path(a.run))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "expC.json").write_text(json.dumps(s, indent=1) + "\n")
    (out / "EXPC.md").write_text(markdown(s))
    print(f"expC summary: {len(s['groups'])} groups -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
