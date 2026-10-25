#!/usr/bin/env python3
"""Evidence latency per system (release 072): how long one query, stop or fence takes on each measured
system, from the raw EviCon-Probe results (controls/ and measure/ of a review run).

For the paper's holding-time corollary (capacity stays reserved for at least one evidence round
trip after the owner could answer) and for the per-system columns of the theory table.

Input: one or more review result directories (e.g. results/review-20261021T010856 of release 070).
Every .../runs/<profile>/<started>/results.jsonl found below them (controls/runs, measure/<system>/runs) is read; with several runs of
the same profile, all are kept and labelled by their review directory.

Per probe row, the sub-results client (the original request), resend, query, query_after, stop and
fence carry latency_ms, outcome and (for evidence actions) supported/terminal. Reported per
(review, profile, action):
  n            attempts with a latency
  n_terminal   answers that are evidence (terminal = true; for client/resend: status OK and an outcome
               other than UNKNOWN)
  p50/p95/max  latency of the terminal answers, ms (nearest-rank percentiles)
  n_timeout    client/resend attempts with status TIMEOUT (their latency is the client timeout and is
               not evidence latency)
Latency is measured by the probe harness on VM-C through its fault proxy, so it includes the
proxy hop; fault windows are not active during the evidence actions of a probe, except where the
probe itself delays the request (P3, P8: those rows are reported with the probe id).

Reads only. Writes LATENCY.md, latency.csv and latency.json into --out.

    python3 evaluation/evidence_latency.py --out <dir> <review dir> [<review dir> ...]
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

ACTIONS = ("client", "resend", "query", "query_after", "stop", "fence")
EVIDENCE = ("query", "query_after", "stop", "fence")


def pct(xs: list, q: float) -> float:
    if not xs:
        return math.nan
    s = sorted(xs)
    return s[max(0, min(len(s) - 1, math.ceil(q * len(s)) - 1))]


def is_terminal(action: str, v: dict) -> bool:
    if action in EVIDENCE:
        return bool(v.get("terminal")) and v.get("supported", True) is not False
    return v.get("status") == "OK" and v.get("outcome") not in (None, "", "UNKNOWN")


def scan(roots: list) -> list:
    rows = []
    for root in roots:
        root = Path(root)
        label = root.name
        for res in sorted(root.glob("**/runs/*/*/results.jsonl")):
            run = res.parent
            group, profile = run.parents[1].relative_to(root).parent.as_posix(), run.parent.name
            system, contract = "", {}
            try:
                contract = json.loads((run / "contract.json").read_text())
                system = contract.get("system", "")
            except (OSError, ValueError):
                pass
            caps = ",".join(contract.get("capabilities", [])) if isinstance(contract.get("capabilities"), list) else ""
            acc = defaultdict(lambda: {"lat": [], "n": 0, "timeout": 0, "unsupported": 0, "probes": set()})
            with open(res) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    if not e.get("applicable", True):
                        continue
                    for a in ACTIONS:
                        v = e.get(a)
                        if not isinstance(v, dict):
                            continue
                        b = acc[a]
                        if a in EVIDENCE and v.get("supported") is False:
                            b["unsupported"] += 1
                            continue
                        lat = v.get("latency_ms")
                        if lat is None:
                            continue
                        b["n"] += 1
                        if v.get("status") == "TIMEOUT":
                            b["timeout"] += 1
                            continue
                        if is_terminal(a, v):
                            b["lat"].append(float(lat))
                            b["probes"].add(e.get("probe_id", ""))
            for a in ACTIONS:
                if a not in acc:
                    continue
                b = acc[a]
                rows.append({"review": label, "group": group, "profile": profile, "started": run.name, "system": system,
                             "capabilities": caps, "action": a, "n": b["n"], "n_terminal": len(b["lat"]), "n_timeout": b["timeout"],
                             "n_unsupported": b["unsupported"], "p50_ms": round(pct(b["lat"], 0.50), 3),
                             "p95_ms": round(pct(b["lat"], 0.95), 3), "max_ms": round(max(b["lat"]), 3) if b["lat"] else math.nan,
                             "probes": " ".join(sorted(p for p in b["probes"] if p))})
    return rows


def markdown(rows: list) -> str:
    L = ["# Evidence latency per system", "",
         "Terminal answers only; nearest-rank percentiles in ms; measured by the probe harness on VM-C through its fault proxy.", "",
         "| review | profile | system | capabilities | action | n | terminal | timeout | p50 | p95 | max |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        f = lambda v: "-" if isinstance(v, float) and math.isnan(v) else f"{v:g}"  # noqa: E731
        L.append(f"| {r['review']} | {r['profile']} | {r['system']} | {r['capabilities']} | {r['action']} | {r['n']} | {r['n_terminal']} | "
                 f"{r['n_timeout']} | {f(r['p50_ms'])} | {f(r['p95_ms'])} | {f(r['max_ms'])} |")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("roots", nargs="+")
    a = ap.parse_args(argv)
    rows = scan(a.roots)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cols = list(rows[0].keys()) if rows else ["review"]
    with open(out / "latency.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    (out / "latency.json").write_text(json.dumps(rows, indent=1, default=str) + "\n")
    (out / "LATENCY.md").write_text(markdown(rows))
    ev = [r for r in rows if r["action"] in EVIDENCE and r["n_terminal"]]
    print(f"evidence latency: {len(rows)} rows, {len(ev)} with terminal evidence, from {len({(r['review'], r['profile']) for r in rows})} probe runs -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
