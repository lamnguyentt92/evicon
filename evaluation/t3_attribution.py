#!/usr/bin/env python3
"""Theorem 3 on the testbed (release 072): what evidence re-opens a request that the guard blocked?

Theorem 3 of the paper (ADD-only domains): evidence that a pending command was APPLIED never turns a
BLOCK into an admission, because the worst case of the belief already counted that command as applied;
only STOPPED evidence (a stop, a sealed fence, a STOPPED receipt) can lower the worst case. On the
testbed the dosing domain is ADD-only (anchor 0, bound = workload.bound), so the theorem predicts that
every request admitted after a capacity BLOCK has STOPPED evidence committed in its domain between its
last BLOCK and its admission.

A DEFER caused by the window of unresolved commands (OUTSTANDING_BUDGET_EXHAUSTED,
CONFIGURATION_BUDGET_EXHAUSTED) is different: resolving a command, as APPLIED or as STOPPED, shrinks
the window, so APPLIED evidence can re-open it. Those re-openings are counted separately as a contrast.

Input: campaign run directories; every cells/ep*/<contract>/<policy>/raw/**/authority*.jsonl is read
(one authority trace per device domain, in commit order). Evidence events:
  STOPPED  EVIDENCE_COMMITTED with outcome STOPPED or a reason naming STOPPED (POLICY_STOP:STOPPED,
           BARRIER_RECEIPT, BARRIER_SEALED_UNCLAIMED), OUTBOX_RECEIPT_COMMITTED with reason STOPPED
  APPLIED  EVIDENCE_COMMITTED with outcome APPLIED (barrier receipts, query answers),
           OUTBOX_RECEIPT_COMMITTED with reason APPLIED, BARRIER_ANCHOR_COMMITTED
Per (run, contract, policy):
  capacity re-openings  ADMITTED after a BLOCK of the same command id; split by the evidence committed in
                        the domain between the last BLOCK and the admission: STOPPED (some STOPPED
                        evidence), APPLIED_ONLY, NONE. Theorem 3 predicts APPLIED_ONLY = NONE = 0
  window re-openings    ADMITTED after a window DEFER (and no BLOCK) of the same command id, split the
                        same way (no prediction; APPLIED_ONLY expected to be common)
  never re-opened       command ids with a BLOCK and no later admission
  re-tested after APPLIED  guard verdicts (BLOCK or ADMITTED) on a blocked command when only APPLIED evidence
                        was committed since its last BLOCK; Theorem 3 predicts BLOCK every time. This is the
                        non-vacuous count: in the dosing regime most blocks are final (the capacity was
                        consumed by applied effects), so capacity re-openings are rare

Negative controls (release 073): cells whose contract starts with NEG_ (devices that break their own
contract on purpose, NEGCTL_072.md) are reported in their own group but left out of every total and of
the prediction: their evidence is false by construction, so they test the checker, not Theorem 3 (072
counted them, 19 re-tests in the N1 run).

Reads only. Writes T3.md and t3.json into --out.

    python3 evaluation/t3_attribution.py --out <dir> <run dir> [<run dir> ...]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

WINDOW = ("OUTSTANDING_BUDGET_EXHAUSTED", "CONFIGURATION_BUDGET_EXHAUSTED")
RELEVANT = ('"DECISION_RECORDED"', '"EVIDENCE_COMMITTED"', '"OUTBOX_RECEIPT_COMMITTED"', '"BARRIER_ANCHOR_COMMITTED"')


def evidence_kind(e: dict):
    ev = e.get("event")
    reason, outcome = str(e.get("reason", "")), str(e.get("outcome", ""))
    if ev == "EVIDENCE_COMMITTED":
        if outcome == "STOPPED" or (not outcome and "STOPPED" in reason) or reason.startswith("BARRIER_SEALED"):
            return "STOPPED"
        if outcome == "APPLIED" or (not outcome and "APPLIED" in reason):
            return "APPLIED"
        return None
    if ev == "OUTBOX_RECEIPT_COMMITTED":
        return {"STOPPED": "STOPPED", "APPLIED": "APPLIED"}.get(reason)
    if ev == "BARRIER_ANCHOR_COMMITTED":
        return "APPLIED"
    return None


def classify(before, after) -> str:
    ds, da = after[0] - before[0], after[1] - before[1]
    return "STOPPED" if ds > 0 else ("APPLIED_ONLY" if da > 0 else "NONE")


def trace(path: Path, acc: dict, examples: list, label: str):
    cnt = [0, 0]  # committed STOPPED, APPLIED evidence so far in this domain
    blocked, deferred, admitted = {}, {}, set()
    with open(path) as fh:
        for line in fh:
            if not any(m in line for m in RELEVANT):  # traces hold millions of OUTBOX_RETRY_SUPPRESSED_T4 lines
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            k = evidence_kind(e)
            if k == "STOPPED":
                cnt[0] += 1
            elif k == "APPLIED":
                cnt[1] += 1
            if e.get("event") != "DECISION_RECORDED":
                continue
            cid, dec, reason = e.get("command_id"), e.get("decision"), str(e.get("reason", ""))
            if cid is None:
                continue
            if cid in blocked and dec in ("BLOCK", "ADMITTED"):
                # a new guard verdict on a command that was blocked: if only APPLIED evidence arrived since
                # its last BLOCK, Theorem 3 says the verdict is BLOCK again (a non-vacuous test)
                if classify(blocked[cid], cnt) == "APPLIED_ONLY":
                    acc["retested_after_applied"]["BLOCK" if dec == "BLOCK" else "ADMITTED"] += 1
            if dec == "BLOCK":
                blocked[cid] = tuple(cnt)
            elif dec == "DEFER" and reason.replace(";", ":").split(":")[0] in WINDOW and cid not in blocked:
                deferred[cid] = tuple(cnt)
            elif dec == "ADMITTED":
                admitted.add(cid)
                if cid in blocked:
                    c = classify(blocked.pop(cid), cnt)
                    acc["capacity"][c] += 1
                    if c != "STOPPED" and len(examples) < 50:
                        examples.append(f"{label} {path.name} {cid}: {c}")
                elif cid in deferred:
                    acc["window"][classify(deferred.pop(cid), cnt)] += 1
    acc["never_reopened"] += len(blocked)


def scan(runs: list) -> dict:
    out = {}
    for run in runs:
        run = Path(run)
        cfg = json.loads((run / "config.json").read_text()) if (run / "config.json").is_file() else {}
        key = f"{cfg.get('name', run.parent.name)}/{run.name}"
        groups = defaultdict(lambda: {"capacity": defaultdict(int), "window": defaultdict(int), "retested_after_applied": defaultdict(int),
                                      "never_reopened": 0, "cells": 0})
        examples = []
        for cell in sorted((run / "cells").glob("ep*/*/*")):
            if not cell.is_dir():
                continue
            ep, ct, pol = cell.parts[-3], cell.parts[-2], cell.parts[-1]
            g = groups[f"{ct}/{pol}"]
            g["cells"] += 1
            for tr in sorted(cell.glob("raw/**/authority*.jsonl")):
                trace(tr, g, examples, f"{ep}/{ct}/{pol}")
        out[key] = {"groups": {k: {"cells": v["cells"], "capacity": dict(v["capacity"]), "window": dict(v["window"]),
                                   "retested_after_applied": dict(v["retested_after_applied"]), "never_reopened": v["never_reopened"]} for k, v in sorted(groups.items())},
                    "counterexamples": examples}
        cap, ret = defaultdict(int), defaultdict(int)
        out[key]["excluded_groups"] = sorted(g for g in groups if g.split("/")[0].startswith("NEG_"))
        for g, v in groups.items():
            if g in out[key]["excluded_groups"]:
                continue  # 073: negative controls are not evidence about Theorem 3
            for c, n in v["capacity"].items():
                cap[c] += n
            for c, n in v["retested_after_applied"].items():
                ret[c] += n
        out[key]["capacity_total"] = dict(cap)
        out[key]["retested_total"] = dict(ret)
        out[key]["prediction_holds"] = cap.get("APPLIED_ONLY", 0) == 0 and cap.get("NONE", 0) == 0 and ret.get("ADMITTED", 0) == 0
        neg = [x for x in examples if x.split()[0].split("/")[1].startswith("NEG_")]
        out[key]["counterexamples"] = [x for x in examples if x not in neg]
        out[key]["negative_control_examples"] = neg
    return out


def markdown(res: dict) -> str:
    L = ["# Theorem 3: evidence that re-opens a blocked request", "",
         "Capacity re-openings: ADMITTED after a BLOCK of the same command, split by the evidence committed in the domain in between. "
         "Theorem 3 predicts APPLIED_ONLY = NONE = 0. Window re-openings (after a window DEFER) are the contrast.", ""]
    for run, r in res.items():
        tot = r["capacity_total"]
        rt = r["retested_total"]
        excl = r.get("excluded_groups", [])
        L += [f"## {run}", "", f"Prediction {'holds' if r['prediction_holds'] else '**FAILS**'}"
              + (f" (negative controls {', '.join(excl)} excluded from the totals)" if excl else "") + ": capacity re-openings "
              f"STOPPED {tot.get('STOPPED', 0)}, APPLIED_ONLY {tot.get('APPLIED_ONLY', 0)}, NONE {tot.get('NONE', 0)}; "
              f"verdicts after APPLIED-only evidence: BLOCK {rt.get('BLOCK', 0)}, ADMITTED {rt.get('ADMITTED', 0)}.", "",
              "| contract/policy | cells | capacity: STOPPED | APPLIED_ONLY | NONE | never re-opened | re-tested after APPLIED: BLOCK | ADMITTED "
              "| window: STOPPED | APPLIED_ONLY | NONE |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
        for g, v in r["groups"].items():
            c, w = v["capacity"], v["window"]
            rr = v["retested_after_applied"]
            L.append(f"| {g}{' (negative control, excluded)' if g in excl else ''} | {v['cells']} | {c.get('STOPPED', 0)} | {c.get('APPLIED_ONLY', 0)} | {c.get('NONE', 0)} | {v['never_reopened']} | "
                     f"{rr.get('BLOCK', 0)} | {rr.get('ADMITTED', 0)} | {w.get('STOPPED', 0)} | {w.get('APPLIED_ONLY', 0)} | {w.get('NONE', 0)} |")
        if r["counterexamples"]:
            L += ["", "Counterexamples (first 50):", ""] + [f"- {x}" for x in r["counterexamples"]]
        L.append("")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("runs", nargs="+")
    a = ap.parse_args(argv)
    res = scan(a.runs)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "t3.json").write_text(json.dumps(res, indent=2) + "\n")
    (out / "T3.md").write_text(markdown(res))
    for run, r in res.items():
        t = r["capacity_total"]
        rt = r["retested_total"]
        print(f"{run}: capacity re-openings STOPPED {t.get('STOPPED', 0)}, APPLIED_ONLY {t.get('APPLIED_ONLY', 0)}, NONE {t.get('NONE', 0)}; "
              f"after APPLIED-only evidence BLOCK {rt.get('BLOCK', 0)}, ADMITTED {rt.get('ADMITTED', 0)} "
              f"-> prediction {'holds' if r['prediction_holds'] else 'FAILS'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
