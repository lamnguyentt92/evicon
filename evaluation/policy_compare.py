#!/usr/bin/env python3
"""Cross-policy comparisons of testbed campaigns (release 077, PREREG_review_077.md).

pairs_report.py (frozen by the 072-075 plans) compares two contracts under one policy. The release-077
baseline RELEASE_ON_TIMEOUT has to be compared with EVICON on the same owner and the same schedules, i.e.
across policies and, because the baseline runs in campaigns of its own, across runs. This script does
that and nothing else; the plan declares the comparisons in one ```policy-compare``` block (JSON) and
freezes this script with it.

Unit of analysis: one episode. Arm a and arm b of a comparison are used for an episode only when both
cells are VALID and the episode's arrival and fault schedule file (schedule_ep<e>.json) is byte-identical
in the two runs (common random numbers). The difference is b - a.

Per-cell metrics (runtime_episodes.csv of the run):
  clean_pp, admitted_pp            clean_fulfilled and admitted in pp of offered
  violations                       bound_violations + duplicate_effects + logical_duplicates + false_stopped
                                   + false_applied (the safety components)
  bound_violations, duplicate_effects, logical_duplicates, false_stopped, false_applied, late_effects,
  reissues, unresolved_at_end      as recorded

Spec:
  {"name": "...",
   "runs": {"<key>": {"campaign": "<config name>"}, ...},          # --run <key>=<run dir>
   "comparisons": [{"id": "RT-E1", "a": {"run": "<key>", "contract": "K0", "policy": "EVICON"},
                    "b": {"run": "<key>", "contract": "K0", "policy": "RELEASE_ON_TIMEOUT"},
                    "metrics": ["clean_pp", "violations", ...]}]}
Each comparison reports, per metric, the mean of the per-episode differences with a 95 % Student-t
interval (and n), the per-arm means, and the per-arm totals of the safety components. For an arm of a release
baseline it also counts, from the authority traces (cells/ep*/<contract>/<policy>/raw/**/authority*.jsonl), the
commands committed STOPPED without owner evidence, by the baseline's reason (RELEASE_ON_TIMEOUT_NO_EVIDENCE;
RELEASE_ON_READ_NOT_REPORTED_NOT_FOUND or _UNREACHABLE: the read found no record, or the owner did not answer),
each command once. No claim, no test.

    python3 evaluation/policy_compare.py --plan evaluation/prereg/PREREG_review_077.md \
        --run RTq=<run> --run Wq=<run> ... --out <dir>
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import statistics
import sys
from pathlib import Path

SAFETY = ("bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied")
RAW = SAFETY + ("late_effects", "reissues", "unresolved_at_end")
T975 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201,
        12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086, 25: 2.060, 30: 2.042}


def t975(df: int) -> float:
    if df in T975:
        return T975[df]
    keys = sorted(T975)
    for k in keys:
        if k > df:
            return T975[k]
    return 1.96


def spec_of(plan: Path) -> dict:
    m = re.search(r"```policy-compare\n(.*?)```", plan.read_text(), re.S)
    if not m:
        raise SystemExit(f"{plan}: no ```policy-compare``` block")
    return json.loads(m.group(1))


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return float("nan")


def load_run(run: Path) -> dict:
    cells = {}
    with open(run / "runtime_episodes.csv", newline="") as fh:
        for r in csv.DictReader(fh):
            if not str(r.get("cell_status", "")).startswith("VALID"):
                continue
            off = _f(r["offered"])
            m = {"clean_pp": 100.0 * _f(r["clean_fulfilled"]) / off if off else float("nan"),
                 "admitted_pp": 100.0 * _f(r["admitted"]) / off if off else float("nan")}
            for k in RAW:
                m[k] = _f(r.get(k))
            m["violations"] = sum(m[k] for k in SAFETY)
            ep = int(r["episode"])
            sched = run / f"schedule_ep{ep}.json"
            m["schedule"] = hashlib.sha256(sched.read_bytes()).hexdigest() if sched.is_file() else ""
            cells[(r["contract"], r["policy"], ep)] = m
    return cells


def diff(ca: dict, a: dict, cb: dict, b: dict, metric: str) -> list:
    out = []
    eps = sorted({k[2] for k in cb if k[0] == b["contract"] and k[1] == b["policy"]})
    for e in eps:
        x, y = ca.get((a["contract"], a["policy"], e)), cb.get((b["contract"], b["policy"], e))
        if x is None or y is None or not x["schedule"] or x["schedule"] != y["schedule"]:
            continue
        if math.isnan(x[metric]) or math.isnan(y[metric]):
            continue
        out.append(y[metric] - x[metric])
    return out


def ci(d: list) -> dict:
    n = len(d)
    if n == 0:
        return {"n": 0}
    mu = statistics.mean(d)
    if n < 2:
        return {"n": n, "mean": round(mu, 3)}
    h = t975(n - 1) * statistics.stdev(d) / math.sqrt(n)
    return {"n": n, "mean": round(mu, 3), "t95": [round(mu - h, 3), round(mu + h, 3)]}


def releases(run: Path, x: dict) -> dict:
    """Commands a release baseline committed STOPPED without owner evidence, by reason (unique per domain and
    command: a single-host run may hold the same trace twice)."""
    seen = {}
    for tf in run.glob(f"cells/ep*/{x['contract']}/{x['policy']}/raw/**/authority*.jsonl"):
        with open(tf) as fh:
            for line in fh:
                if '"RELEASE_ON_' not in line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("event") not in ("RELEASE_ON_TIMEOUT", "RELEASE_ON_READ"):
                    continue
                reason = str(e.get("reason", "")).split(":", 1)[-1]
                ep = next((q for q in tf.relative_to(run).parts if q.startswith("ep")), "")
                seen[(ep, e.get("domain_id"), e.get("command_id"))] = reason
    out = {}
    for r in seen.values():
        out[r] = out.get(r, 0) + 1
    return out


def arm(cells: dict, x: dict) -> dict:
    ms = [m for (c, p, _), m in cells.items() if c == x["contract"] and p == x["policy"]]
    out = {"cells": len(ms)}
    for k in ("clean_pp", "admitted_pp"):
        v = [m[k] for m in ms if not math.isnan(m[k])]
        out[k] = round(statistics.mean(v), 3) if v else None
    for k in RAW + ("violations",):
        out[k] = int(sum(m[k] for m in ms if not math.isnan(m[k])))
    out["cells_with_violation"] = sum(1 for m in ms if m["violations"] > 0)
    return out


def analyse(spec: dict, runs: dict) -> dict:
    loaded = {k: load_run(Path(v)) for k, v in runs.items()}
    res = {"name": spec.get("name", ""), "runs": runs, "comparisons": []}
    for c in spec["comparisons"]:
        a, b = c["a"], c["b"]
        if a["run"] not in loaded or b["run"] not in loaded:
            res["comparisons"].append({"id": c["id"], "status": "NOT RUN"})
            continue
        ca, cb = loaded[a["run"]], loaded[b["run"]]
        row = {"id": c["id"], "a": a, "b": b, "arm_a": arm(ca, a), "arm_b": arm(cb, b), "differences": {}}
        for k, x in (("a", a), ("b", b)):
            if x["policy"].startswith("RELEASE_ON_"):
                row[f"arm_{k}"]["releases_without_evidence"] = releases(Path(runs[x["run"]]), x)
        for m in c.get("metrics", ["clean_pp"]):
            row["differences"][m] = ci(diff(ca, a, cb, b, m))
        res["comparisons"].append(row)
    return res


def markdown(res: dict) -> str:
    L = [f"# {res['name'] or 'Policy comparisons'}", "",
         "Per-episode differences b - a over episodes whose schedule files are byte-identical in both runs (95 % t). No test, no claim.", "",
         "| ID | a | b | metric | n | mean b - a [95 %] |", "|---|---|---|---|---|---|"]
    for c in res["comparisons"]:
        if c.get("status"):
            L.append(f"| {c['id']} | | | | | {c['status']} |")
            continue
        lab = lambda x: f"{x['run']}: {x['contract']}/{x['policy']}"
        for m, d in c["differences"].items():
            iv = f"{d['mean']:+.2f} [{d['t95'][0]:+.2f}, {d['t95'][1]:+.2f}]" if "t95" in d else (f"{d.get('mean')}" if d.get("n") else "-")
            L.append(f"| {c['id']} | {lab(c['a'])} | {lab(c['b'])} | {m} | {d['n']} | {iv} |")
    L += ["", "## Arms", "", "| ID | arm | cells | clean % | admitted % | violations (cells) | bound | duplicate | logical dup. | false STOPPED | "
          "false APPLIED | late | reissues |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for c in res["comparisons"]:
        if c.get("status"):
            continue
        for k in ("a", "b"):
            x, s = c[k], c[f"arm_{k}"]
            L.append(f"| {c['id']} | {x['run']}: {x['contract']}/{x['policy']} | {s['cells']} | {s['clean_pp']} | {s['admitted_pp']} | "
                     f"{s['violations']} ({s['cells_with_violation']}) | {s['bound_violations']} | {s['duplicate_effects']} | "
                     f"{s['logical_duplicates']} | {s['false_stopped']} | {s['false_applied']} | {s['late_effects']} | {s['reissues']} |")
    rel = [(c["id"], c[k], c[f"arm_{k}"]["releases_without_evidence"]) for c in res["comparisons"] if not c.get("status")
           for k in ("a", "b") if "releases_without_evidence" in c.get(f"arm_{k}", {})]
    if rel:
        L += ["", "## Commands committed STOPPED without owner evidence (authority traces, by reason)", "",
              "| ID | arm | releases |", "|---|---|---|"]
        for cid, x, r in rel:
            L.append(f"| {cid} | {x['run']}: {x['contract']}/{x['policy']} | "
                     f"{'; '.join(f'{k} {v}' for k, v in sorted(r.items())) or 'none'} |")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--plan", required=True)
    ap.add_argument("--run", action="append", default=[], help="<key>=<run dir>")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    spec = spec_of(Path(a.plan))
    runs = dict(r.split("=", 1) for r in a.run)
    res = analyse(spec, runs)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(res, indent=2) + "\n")
    (out / "RESULTS.md").write_text(markdown(res))
    print(f"policy_compare: {len(res['comparisons'])} comparisons -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
