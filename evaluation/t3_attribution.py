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

Practice baselines (release 081): cells of the policies RELEASE_ON_TIMEOUT and RELEASE_ON_READ
(PREREG_review_077.md; release 083: also RELEASE_ON_READ_ANSWERED and RELEASE_ON_TIMEOUT_30S, _60S, _120S of
PREREG_extra_083.md; the one definition is safety_total.BASELINES) are listed in their own groups and left out
of every total and of the prediction too: they release a request without evidence of non-occurrence by
design, so they are outside the theorem's premise (the guard's belief), and their re-openings without
evidence are what the plan's rules RT-1 to RT-3 and RR-1, RR-2 detect. Their totals are reported apart
(baseline_total, baseline_retested_total).

Window re-openings without evidence (release 083, analysis A3): a window re-opening with no evidence committed
between the window DEFER and the admission (NONE) is listed with its trace context: the events of that domain's
trace between the DEFER and the ADMITTED (OUTBOX_RETRY_SUPPRESSED_T4 lines counted, not listed), and an
explanation derived from them (explain_window_none): which change other than committed evidence made room in the
window. The practice baselines' and the negative controls' NONE re-openings are counted, not listed.

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
sys.path.insert(0, str(Path(__file__).resolve().parent))
from safety_total import BASELINES  # noqa: E402  (release 083: one definition of the practice baselines, unsafe by design)
CONTEXT_MAX = 60  # A3: events listed per window re-opening without evidence


def is_baseline(group: str) -> bool:
    """'<contract>/<policy>' of a practice baseline that releases without evidence."""
    return group.split("/")[-1] in BASELINES
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


def trace(path: Path, acc: dict, examples: list, label: str, window_none: list = None):
    cnt = [0, 0]  # committed STOPPED, APPLIED evidence so far in this domain
    blocked, deferred, admitted = {}, {}, set()
    found = []  # A3: (cid, defer line, admit line, defer reason) of window re-openings without evidence
    with open(path) as fh:
        for lineno, line in enumerate(fh):
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
                deferred[cid] = (tuple(cnt), lineno, reason)
            elif dec == "ADMITTED":
                admitted.add(cid)
                if cid in blocked:
                    c = classify(blocked.pop(cid), cnt)
                    acc["capacity"][c] += 1
                    if c != "STOPPED" and len(examples) < 50:
                        examples.append(f"{label} {path.name} {cid}: {c}")
                elif cid in deferred:
                    before, dline, dreason = deferred.pop(cid)
                    c = classify(before, cnt)
                    acc["window"][c] += 1
                    if c == "NONE":
                        found.append((cid, dline, lineno, dreason))
    acc["never_reopened"] += len(blocked)
    if window_none is not None and found:
        for cid, d0, d1, dreason in found:
            window_none.append(dict(window_context(path, d0, d1), label=label, trace=path.name, command_id=cid, defer_reason=dreason))


def window_context(path: Path, start: int, end: int) -> dict:
    """A3: the events of one domain trace from line start (the window DEFER) to line end (the ADMITTED)."""
    events, suppressed, names = [], 0, defaultdict(int)
    with open(path) as fh:
        for i, line in enumerate(fh):
            if i < start:
                continue
            if i > end:
                break
            try:
                e = json.loads(line)
            except ValueError:
                continue
            name = e.get("event", "")
            names[name] += 1
            if name == "OUTBOX_RETRY_SUPPRESSED_T4":
                suppressed += 1
                continue
            if len(events) < CONTEXT_MAX:
                events.append({k: e.get(k) for k in ("wall_time", "event", "command_id", "decision", "outcome", "reason", "revision")
                               if e.get(k) not in (None, "")})
    ctx = {"events": events, "event_counts": dict(names), "retry_suppressed_lines": suppressed, "lines": end - start + 1}
    ctx["explanation"] = explain_window_none(ctx)
    return ctx


def explain_window_none(ctx: dict) -> str:
    """Which change other than committed terminal evidence made room in the window between the DEFER and the
    admission (derived from the trace context; no evidence of non-occurrence is involved in any branch)."""
    noise = {"OUTBOX_RETRY_SUPPRESSED_T4", "OUTBOX_DISPATCH_ATTEMPT", "PLANNER_CONFIGURATION"}
    seen = ", ".join(f"{k} x{v}" for k, v in sorted(ctx["event_counts"].items()) if k not in noise) or "none"
    return _explain(ctx) + f" (events in between: {seen})"


def _explain(ctx: dict) -> str:
    """Facts only: the kind of window, and the belief-changing events of the interval (no evidence was committed)."""
    ev = ctx["events"]
    first = ev[0] if ev else {}
    reason = str(first.get("reason", ""))
    names = ctx["event_counts"]
    others = sum(1 for e in ev[1:-1] if e.get("event") == "DECISION_RECORDED" and e.get("decision") == "ADMITTED")
    other_ev = sum(v for k, v in names.items() if k.startswith(("BARRIER", "EVIDENCE", "ANCHOR")) or "ANCHOR" in k or k == "OUTBOX_SKIPPED_TERMINAL")
    if reason.startswith("CONFIGURATION_BUDGET_EXHAUSTED"):
        kind = ("the DEFER was CONFIGURATION_BUDGET_EXHAUSTED: in releases <= 071 the window was the guard's analysis bound (a cap of "
                "256 belief configurations), not a count of unresolved commands, so it can re-open when the belief's configuration "
                "count falls under the cap without any command being resolved")
    elif reason.startswith("OUTSTANDING_BUDGET_EXHAUSTED"):
        kind = "the DEFER was OUTSTANDING_BUDGET_EXHAUSTED (a count of unresolved commands)"
    else:
        kind = f"the interval does not start with a window DEFER ({reason[:40] or 'no reason'})"
    if other_ev:
        how = f"{other_ev} barrier, anchor or non-terminal evidence event(s) changed the belief in between"
    elif others:
        how = f"the only belief changes in between were {others} admission(s) of other commands"
    else:
        how = "the trace records no change of the belief in between (the guard's verdict changed on the same belief)"
    return f"{kind}; no terminal evidence was committed; {how}"


def scan(runs: list) -> dict:
    out = {}
    for run in runs:
        run = Path(run)
        cfg = json.loads((run / "config.json").read_text()) if (run / "config.json").is_file() else {}
        key = f"{cfg.get('name', run.parent.name)}/{run.name}"
        groups = defaultdict(lambda: {"capacity": defaultdict(int), "window": defaultdict(int), "retested_after_applied": defaultdict(int),
                                      "never_reopened": 0, "cells": 0})
        examples, window_none = [], []
        for cell in sorted((run / "cells").glob("ep*/*/*")):
            if not cell.is_dir():
                continue
            ep, ct, pol = cell.parts[-3], cell.parts[-2], cell.parts[-1]
            g = groups[f"{ct}/{pol}"]
            g["cells"] += 1
            listed = not (ct.startswith("NEG_") or pol in BASELINES)  # A3: guarded cells only
            for tr in sorted(cell.glob("raw/**/authority*.jsonl")):
                trace(tr, g, examples, f"{ep}/{ct}/{pol}", window_none if listed else None)
        out[key] = {"groups": {k: {"cells": v["cells"], "capacity": dict(v["capacity"]), "window": dict(v["window"]),
                                   "retested_after_applied": dict(v["retested_after_applied"]), "never_reopened": v["never_reopened"]} for k, v in sorted(groups.items())},
                    "counterexamples": examples}
        cap, ret, bcap, bret = defaultdict(int), defaultdict(int), defaultdict(int), defaultdict(int)
        out[key]["excluded_groups"] = sorted(g for g in groups if g.split("/")[0].startswith("NEG_"))
        out[key]["baseline_groups"] = sorted(g for g in groups if is_baseline(g) and g not in out[key]["excluded_groups"])
        for g, v in groups.items():
            if g in out[key]["excluded_groups"]:
                continue  # 073: negative controls are not evidence about Theorem 3
            c_acc, r_acc = (bcap, bret) if g in out[key]["baseline_groups"] else (cap, ret)  # 081: baselines apart
            for c, n in v["capacity"].items():
                c_acc[c] += n
            for c, n in v["retested_after_applied"].items():
                r_acc[c] += n
        out[key]["capacity_total"] = dict(cap)
        out[key]["retested_total"] = dict(ret)
        out[key]["baseline_total"] = dict(bcap)
        out[key]["baseline_retested_total"] = dict(bret)
        out[key]["prediction_holds"] = cap.get("APPLIED_ONLY", 0) == 0 and cap.get("NONE", 0) == 0 and ret.get("ADMITTED", 0) == 0

        def grp(x):  # "ep0/K0/EVICON file cid: kind" -> "K0/EVICON"
            return "/".join(x.split()[0].split("/")[1:3])
        neg = [x for x in examples if grp(x).startswith("NEG_")]
        base = [x for x in examples if x not in neg and is_baseline(grp(x))]
        out[key]["counterexamples"] = [x for x in examples if x not in neg and x not in base]
        out[key]["negative_control_examples"] = neg
        out[key]["baseline_examples"] = base
        out[key]["window_none_guarded"] = window_none
    return out


def markdown(res: dict) -> str:
    L = ["# Theorem 3: evidence that re-opens a blocked request", "",
         "Capacity re-openings: ADMITTED after a BLOCK of the same command, split by the evidence committed in the domain in between. "
         "Theorem 3 predicts APPLIED_ONLY = NONE = 0. Window re-openings (after a window DEFER) are the contrast. "
         "Negative controls (NEG_*) and the practice baselines that release without evidence (" + ", ".join(BASELINES) + "; "
         "unsafe by design, detected by the rules RT and RR of PREREG_review_077 and RA, RTs of PREREG_extra_083) are listed apart and left out of the totals.", ""]
    for run, r in res.items():
        tot = r["capacity_total"]
        rt = r["retested_total"]
        excl = r.get("excluded_groups", [])
        base = r.get("baseline_groups", [])
        left = ([f"negative controls {', '.join(excl)}"] if excl else []) + ([f"practice baselines {', '.join(base)}"] if base else [])
        L += [f"## {run}", ""]
        if base and len(base) == len(set(r["groups"]) - set(excl)):
            bt, bq = r.get("baseline_total", {}), r.get("baseline_retested_total", {})
            L.append(f"Practice baselines only (no guarded cell; outside the theorem's premise): capacity re-openings STOPPED "
                     f"{bt.get('STOPPED', 0)}, APPLIED_ONLY {bt.get('APPLIED_ONLY', 0)}, NONE {bt.get('NONE', 0)}; verdicts after "
                     f"APPLIED-only evidence: BLOCK {bq.get('BLOCK', 0)}, ADMITTED {bq.get('ADMITTED', 0)}.")
        else:
            L.append(f"Prediction {'holds' if r['prediction_holds'] else '**FAILS**'}"
                     + (f" ({'; '.join(left)} excluded from the totals)" if left else "") + ": capacity re-openings "
                     f"STOPPED {tot.get('STOPPED', 0)}, APPLIED_ONLY {tot.get('APPLIED_ONLY', 0)}, NONE {tot.get('NONE', 0)}; "
                     f"verdicts after APPLIED-only evidence: BLOCK {rt.get('BLOCK', 0)}, ADMITTED {rt.get('ADMITTED', 0)}.")
        L += ["", "| contract/policy | cells | capacity: STOPPED | APPLIED_ONLY | NONE | never re-opened | re-tested after APPLIED: BLOCK | ADMITTED "
              "| window: STOPPED | APPLIED_ONLY | NONE |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
        for g, v in r["groups"].items():
            c, w = v["capacity"], v["window"]
            rr = v["retested_after_applied"]
            tag = " (negative control, excluded)" if g in excl else (" (practice baseline, excluded)" if g in base else "")
            L.append(f"| {g}{tag} | {v['cells']} | {c.get('STOPPED', 0)} | {c.get('APPLIED_ONLY', 0)} | {c.get('NONE', 0)} | {v['never_reopened']} | "
                     f"{rr.get('BLOCK', 0)} | {rr.get('ADMITTED', 0)} | {w.get('STOPPED', 0)} | {w.get('APPLIED_ONLY', 0)} | {w.get('NONE', 0)} |")
        if r["counterexamples"]:
            L += ["", "Counterexamples (first 50):", ""] + [f"- {x}" for x in r["counterexamples"]]
        for w in r.get("window_none_guarded", []):
            L += ["", f"**Window re-opening without committed evidence** (A3): {w['label']} {w['trace']} {w['command_id']} "
                  f"(DEFER {w['defer_reason'][:80]}; {w['lines']} trace lines, {w['retry_suppressed_lines']} OUTBOX_RETRY_SUPPRESSED_T4). "
                  f"Why: {w['explanation']}.", "", "| wall time | event | command | decision / outcome | reason | revision |", "|---|---|---|---|---|---|"]
            for e in w["events"]:
                L.append(f"| {e.get('wall_time', '')} | {e.get('event', '')} | {e.get('command_id', '')} | "
                         f"{e.get('decision', e.get('outcome', ''))} | {str(e.get('reason', ''))[:70]} | {e.get('revision', '')} |")
        L.append("")
    return "\n".join(L)


def window_none_summary(res: dict) -> list:
    """A3: every window re-opening without committed evidence in a guarded cell, with its explanation."""
    return [dict(run=run, label=w["label"], trace=w["trace"], command_id=w["command_id"], defer_reason=w["defer_reason"],
                 explanation=w["explanation"], lines=w["lines"]) for run, r in res.items() for w in r.get("window_none_guarded", [])]


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
        if r.get("baseline_groups") and len(r["baseline_groups"]) == len(set(r["groups"]) - set(r.get("excluded_groups", []))):
            b = r["baseline_total"]
            print(f"{run}: practice baselines only (excluded): capacity re-openings APPLIED_ONLY {b.get('APPLIED_ONLY', 0)}, NONE {b.get('NONE', 0)}")
            continue
        print(f"{run}: capacity re-openings STOPPED {t.get('STOPPED', 0)}, APPLIED_ONLY {t.get('APPLIED_ONLY', 0)}, NONE {t.get('NONE', 0)}; "
              f"after APPLIED-only evidence BLOCK {rt.get('BLOCK', 0)}, ADMITTED {rt.get('ADMITTED', 0)} "
              f"-> prediction {'holds' if r['prediction_holds'] else 'FAILS'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
