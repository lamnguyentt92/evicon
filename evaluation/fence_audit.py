#!/usr/bin/env python3
"""Fence and evidence audit of runtime campaign cells (release 068).

Reads the raw traces a campaign fetched (cells/ep*/<contract>/<policy>/raw/A/authority*.jsonl and
raw/E/dev*/*.journal) and reports, per contract and policy:

  * every fence (owner barrier) result, classified by its outcome: OK, DEVICE_OFFLINE,
    BARRIER_UNCERTAIN_RECEIPTS (crash orphan, T2), BARRIER_QUERY_FAILED, EVIDENCE_CONFLICT,
    "terminal outcome reversal" (067 and earlier: HTTP 500), other errors - by the error text, so
    that an error without an upper-case code is counted too (the 066 report missed 637 of them);
  * EVIDENCE_CONFLICT events (release 068 authority: owner evidence contradicting a committed
    terminal outcome) and domains whose fences kept failing on the same conflict (stuck);
  * device journals in which one identity has APPLIED and later STOPPED (the 067 emulator bug,
    fixed in 068 by device test D09): these should be 0 for runs of release 068 and later.

    python3 evaluation/fence_audit.py --campaign <run dir> [--campaign <run dir> ...] --out <dir>

Writes fence_audit.json and fence_audit.md. Exit status 0; the tables are evidence, not a test.
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import re
import sys
from pathlib import Path

CODES = ("DEVICE_OFFLINE", "BARRIER_UNCERTAIN_RECEIPTS", "BARRIER_QUERY_FAILED", "EVIDENCE_CONFLICT", "BARRIER_ABORTED",
         "BARRIER_IN_PROGRESS", "BARRIER_TOKEN_CONFLICT", "INVALID_BARRIER_EVIDENCE", "INVALID_BARRIER_RECEIPT")


def classify(event: dict) -> str:
    """Outcome class of one EVICON_FENCE_RESULT trace event."""
    outcome = str(event.get("outcome", ""))
    reason = str(event.get("reason", "") or "")
    if outcome.startswith("2"):
        return "OK"
    if "terminal outcome reversal" in reason:
        return "TERMINAL_OUTCOME_REVERSAL"
    for c in CODES:
        if c in reason:
            return c
    m = re.search(r'"error":"([^"\\]{1,60})', reason)
    return f"HTTP_{outcome}:{m.group(1)}" if m else f"HTTP_{outcome}"


def journal_flips(path: Path) -> list:
    """Identities whose device journal shows APPLIED and later STOPPED (or the reverse)."""
    last, flips = {}, []
    for line in path.read_text(errors="replace").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("k") != "REC":
            continue
        r = e.get("r") or {}
        cid, st = r.get("id"), r.get("status")
        prev = last.get(cid)
        if prev in ("APPLIED", "STOPPED") and st in ("APPLIED", "STOPPED") and st != prev:
            flips.append({"id": cid, "from": prev, "to": st, "reason": r.get("reason", "")})
        last[cid] = st
    return flips


def audit_cell(cell: Path) -> dict:
    out = {"fences": collections.Counter(), "conflicts": 0, "stuck": {}, "flips": []}
    for f in sorted(cell.glob("raw/A/authority*.jsonl")):
        reversal = collections.Counter()
        first = {}
        for line in f.read_text(errors="replace").splitlines():
            if '"EVICON_FENCE_RESULT"' not in line and '"EVIDENCE_CONFLICT"' not in line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("event") == "EVIDENCE_CONFLICT":
                out["conflicts"] += 1
                continue
            c = classify(e)
            out["fences"][c] += 1
            if c in ("TERMINAL_OUTCOME_REVERSAL", "EVIDENCE_CONFLICT"):
                d = e.get("domain_id", f.stem)
                reversal[d] += 1
                first.setdefault(d, e.get("monotonic_ns", 0) / 1e9)
        for d, n in reversal.items():
            if n >= 3:  # the same failure again and again: the domain could not fence any more
                out["stuck"][d] = {"failures": n, "from_s": round(first[d], 1)}
    for j in sorted(cell.glob("raw/E/*/*.journal")):
        out["flips"] += [dict(x, journal=j.name) for x in journal_flips(j)]
    return out


def run_labels(runs) -> dict:
    """Campaign name per run directory, plus @<run stamp> when one campaign has several runs
    (release 070: the 067 confirmatory run and its 069 replication must not be merged)."""
    runs = [Path(r) for r in runs]
    names = collections.Counter(r.parent.name for r in runs)
    return {r: r.parent.name if names[r.parent.name] == 1 else f"{r.parent.name}@{r.name}" for r in runs}


def audit(campaigns: list) -> dict:
    groups = collections.defaultdict(lambda: {"cells": 0, "fences": collections.Counter(), "conflicts": 0,
                                              "stuck_cells": [], "flip_cells": [], "flips": 0})
    labels = run_labels(campaigns)
    for c in campaigns:
        for cell in sorted(Path(c).glob("cells/ep*/*/*")):
            if not (cell / "row.json").exists():
                continue
            ep, contract, policy = cell.parts[-3], cell.parts[-2], cell.parts[-1]
            g = groups[(labels[Path(c)], contract, policy)]
            a = audit_cell(cell)
            g["cells"] += 1
            g["fences"].update(a["fences"])
            g["conflicts"] += a["conflicts"]
            if a["stuck"]:
                g["stuck_cells"].append({"cell": f"{ep}/{contract}/{policy}", "domains": a["stuck"]})
            if a["flips"]:
                g["flip_cells"].append({"cell": f"{ep}/{contract}/{policy}", "flips": a["flips"][:5]})
                g["flips"] += len(a["flips"])
    rows = []
    for (camp, contract, policy), g in sorted(groups.items()):
        total = sum(g["fences"].values())
        rows.append({"campaign": camp, "contract": contract, "policy": policy, "cells": g["cells"], "fence_calls": total,
                     "fence_ok": g["fences"].get("OK", 0), "fence_ok_rate": round(g["fences"].get("OK", 0) / total, 4) if total else None,
                     "by_outcome": dict(g["fences"].most_common()), "evidence_conflicts": g["conflicts"],
                     "stuck_cells": g["stuck_cells"], "device_receipt_flips": g["flips"], "flip_cells": g["flip_cells"]})
    return {"rows": rows, "campaigns": [str(c) for c in campaigns],
            "flips_total": sum(r["device_receipt_flips"] for r in rows),
            "stuck_cells_total": sum(len(r["stuck_cells"]) for r in rows)}


def markdown(rep: dict) -> str:
    L = ["# Fence and evidence audit", "",
         "Every owner barrier (fence) result in the authority traces, classified by its error text; evidence "
         "conflicts; and device journals in which one identity changed between APPLIED and STOPPED (release 068 "
         "audit; should be 0 from release 068 on).", "",
         "| campaign | contract | policy | cells | fence calls | OK | OK rate | outcomes | conflicts | stuck cells | receipt flips |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rep["rows"]:
        if not r["fence_calls"] and not r["device_receipt_flips"] and not r["evidence_conflicts"]:
            continue
        oc = ", ".join(f"{k} {v}" for k, v in r["by_outcome"].items() if k != "OK")
        L.append(f"| {r['campaign']} | {r['contract']} | {r['policy']} | {r['cells']} | {r['fence_calls']} | {r['fence_ok']} | "
                 f"{r['fence_ok_rate'] if r['fence_ok_rate'] is not None else '-'} | {oc or '-'} | {r['evidence_conflicts']} | "
                 f"{len(r['stuck_cells'])} | {r['device_receipt_flips']} |")
    L += ["", f"Device receipt flips in total: {rep['flips_total']}; cells with a domain stuck on the same failed fence: "
              f"{rep['stuck_cells_total']}.", ""]
    for r in rep["rows"]:
        for s in r["stuck_cells"]:
            L.append(f"- stuck: {r['campaign']} {s['cell']}: {json.dumps(s['domains'])}")
        for s in r["flip_cells"]:
            L.append(f"- receipt flip: {r['campaign']} {s['cell']}: {json.dumps(s['flips'])}")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--campaign", action="append", required=True, help="campaign run directory (repeatable; globs allowed)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    runs = []
    for c in a.campaign:
        runs += [Path(p) for p in sorted(glob.glob(c))] or [Path(c)]
    rep = audit(runs)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "fence_audit.json").write_text(json.dumps(rep, indent=2) + "\n")
    (out / "fence_audit.md").write_text(markdown(rep))
    print(f"fence audit: {len(rep['rows'])} groups, receipt flips {rep['flips_total']}, stuck cells {rep['stuck_cells_total']} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
