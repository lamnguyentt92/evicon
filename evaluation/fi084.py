#!/usr/bin/env python3
"""Estimands E-FI and E-PF of PREREG_extra_084.md (release 084): what the fence intake and the full periodic reconciliation
did, from the traces of the plan's runs and their reference runs.

Per run, contract and policy (VALID cells; per-episode means), from the rows (runtime_episodes.csv) and the authority traces:

  E-FI   fences sent, fences completed (2xx), fences aborted because the owner listed an identity UNCERTAIN (rule R5:
         BARRIER_ABORTED ...OWNER_UNCERTAIN), fences completed with per-identity intake (rule R5': BARRIER_PER_IDENTITY_COMMITTED),
         terminal answers per completed fence (EVIDENCE_COMMITTED with reason BARRIER_RECEIPT: or BARRIER_SEALED_UNCLAIMED:,
         by token), crash windows that crashed the owner (the row's crashes) and distinct identities the owner listed UNCERTAIN
         (orphans seen by a fence), unresolved at the end, clean and confirmed fulfilment
  E-PF   owner calls per episode (queries, stops, fences and their sum), clean and confirmed fulfilment, mean time from admission
         to terminal evidence (the row's mean_time_to_terminal_s), unresolved at the end

A cell without traces has the trace counts "not available". An estimand: no claim, no test.

    python3 evaluation/fi084.py --run KEY=DIR ... --out <report>/fi084
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
from collections import defaultdict
from pathlib import Path

NA = "not available"
ROW = ("clean_fulfilled", "offered", "fences", "fences_ok", "stops", "stops_terminal", "queries", "unresolved_at_end", "crashes",
       "mean_time_to_terminal_s")


def authority_traces(celldir: Path) -> list:
    """The cell's authority traces: VM-A's copy (raw/A/), else raw/ itself, else anywhere under raw/ (as overhead.py reads them;
    a copy fetched from another VM is not counted twice)."""
    return (sorted(celldir.glob("raw/A/authority*.jsonl")) or sorted(celldir.glob("raw/authority*.jsonl"))
            or sorted(celldir.glob("raw/**/authority*.jsonl")))


def cell_traces(celldir: Path) -> dict:
    trs = authority_traces(celldir)
    if not trs:
        return {}
    c = {"aborted_owner_uncertain": 0, "per_identity": 0, "anchored": 0, "fence_terminal_answers": 0, "fences_with_answers": 0}
    orphans = set()
    for tr in trs:
        by_token = defaultdict(int)
        with open(tr, errors="replace") as fh:
            for line in fh:
                if not any(k in line for k in ("BARRIER_", "OWNER_UNCERTAIN")):
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                ev, reason = e.get("event", ""), e.get("reason") or ""
                if ev == "BARRIER_ABORTED" and "OWNER_UNCERTAIN" in reason:
                    c["aborted_owner_uncertain"] += 1
                    orphans |= {x for x in reason.split("OWNER_UNCERTAIN:", 1)[-1].split(",") if x}
                elif ev == "BARRIER_PER_IDENTITY_COMMITTED":
                    c["per_identity"] += 1
                    orphans |= {x for x in reason.split("OWNER_UNCERTAIN:", 1)[-1].split(",") if x}
                elif ev == "BARRIER_ANCHOR_COMMITTED":
                    c["anchored"] += 1
                elif ev == "EVIDENCE_COMMITTED" and (reason.startswith("BARRIER_RECEIPT:") or reason.startswith("BARRIER_SEALED_UNCLAIMED:")):
                    by_token[reason.split(":", 1)[1]] += 1
        c["fence_terminal_answers"] += sum(by_token.values())
        c["fences_with_answers"] += len(by_token)
    c["orphans_listed"] = len(orphans)
    return c


def analyse(runs: dict) -> dict:
    out = []
    for key, run in runs.items():
        p = Path(run) / "runtime_episodes.csv"
        if not p.exists():
            continue
        with open(p, newline="") as fh:
            rows = [r for r in csv.DictReader(fh) if str(r.get("cell_status", "")).startswith("VALID")]
        groups = defaultdict(list)
        for r in rows:
            groups[(r["contract"], r["policy"])].append(r)
        for (ct, pol), rs in sorted(groups.items()):
            g = {"run": key, "dir": str(run), "contract": ct, "policy": pol, "cells": len(rs)}
            for k in ROW:
                vals = [float(r[k]) for r in rs if r.get(k) not in (None, "")]
                g[k] = round(statistics.mean(vals), 3) if vals else None
            g["clean_pp"] = round(statistics.mean(100.0 * float(r["clean_fulfilled"]) / max(1.0, float(r["offered"])) for r in rs), 3)
            g["owner_calls"] = round(sum((g.get(k) or 0.0) for k in ("queries", "stops", "fences")), 3)
            tcs = [cell_traces(Path(run) / "cells" / f"ep{r['episode']}" / ct / pol) for r in rs]
            have = [t for t in tcs if t]
            g["cells_with_traces"] = len(have)
            for k in ("aborted_owner_uncertain", "per_identity", "anchored", "fence_terminal_answers", "orphans_listed"):
                g[k] = round(statistics.mean(t[k] for t in have), 3) if have else NA
            fences = sum(t["fences_with_answers"] for t in have)
            g["terminal_answers_per_fence"] = round(sum(t["fence_terminal_answers"] for t in have) / fences, 3) if fences else (NA if not have else 0.0)
            out.append(g)
    return {"groups": out}


def markdown(res: dict) -> str:
    def f(x, d=1):
        return x if isinstance(x, str) else ("-" if x is None else f"{x:.{d}f}")
    L = ["# E-FI and E-PF of PREREG_extra_084 (estimands; per-episode means of the VALID cells)", "",
         "| run | contract | policy | cells | clean % | fences | completed | aborted (OWNER_UNCERTAIN) | per-identity | terminal answers / fence | "
         "orphans listed | crashes | stops | queries | owner calls | time to terminal (s) | unresolved at end |", "|" + "---|" * 17]
    for g in res["groups"]:
        L.append(f"| {g['run']} | {g['contract']} | {g['policy']} | {g['cells']} | {f(g['clean_pp'])} | {f(g['fences'])} | {f(g['fences_ok'])} | "
                 f"{f(g['aborted_owner_uncertain'])} | {f(g['per_identity'])} | {f(g['terminal_answers_per_fence'], 2)} | {f(g['orphans_listed'])} | "
                 f"{f(g['crashes'])} | {f(g['stops'])} | {f(g['queries'])} | {f(g['owner_calls'])} | {f(g['mean_time_to_terminal_s'], 2)} | "
                 f"{f(g['unresolved_at_end'])} |")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", action="append", default=[], help="KEY=DIR")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    runs = dict(x.split("=", 1) for x in a.run)
    res = analyse(runs)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "fi084.json").write_text(json.dumps(res, indent=2) + "\n")
    (out / "FI084.md").write_text(markdown(res))
    print(f"E-FI/E-PF (084): {len(res['groups'])} groups -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
