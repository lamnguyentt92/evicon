#!/usr/bin/env python3
"""Late effects attributed to the first send or to a resend (release 077, PREREG_review_077.md, E-L).

campaign.py counts a late effect when an intent's first effect at the plant comes after the intent's validity
(arrival + workload.validity_s). From release 077 on, the authority's dispatcher sends the attempt number with
every request (header X-EviCon-Attempt) and evicon-device records, for every effect, which attempt of the
identity took effect (<device>.effects.jsonl in its state directory). This script joins the three logs of every
cell: the clients' intents (clients.jsonl), the truth (plant.jsonl) and the devices' effect records, and splits
the late effects by the attempt that caused them:

  late_first_send   the first effect came from attempt 1 (the original request, delivered late)
  late_resend       it came from a resend of the same identity (attempt >= 2), split by when the authority sent
                    that attempt (its OUTBOX_DISPATCH_ATTEMPT trace event):
                      late_resend_sent_in_validity     sent before the intent's validity ended, delivered late
                      late_resend_sent_after_validity  sent after it: what the validity cut of the retry guard
                                                       (evicon-authority -resend-until) rules out
  late_unknown      no effect record (a run before release 077, or an owner that does not record attempts); of
                    those, late_unknown_dispatched_after_validity: the identity was dispatched after the validity and
                    before it took effect (the runtime may have caused it: an upper bound for runs before 077)

It also counts all effects by attempt. A cell is recounted from the raw files; `late_effects` is compared with
the runner's row (a mismatch is listed).

    python3 evaluation/late_attribution.py --campaign <run dir> [--campaign <run dir> ...] --out <dir>
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path


def _jsonl(path: Path):
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        yield json.loads(line)
                    except ValueError:
                        continue
    except OSError:
        return


def _wall(s: str):
    """RFC 3339 wall time of a trace event (nanoseconds allowed) -> Unix seconds, or None."""
    from datetime import datetime, timezone
    try:
        base, _, frac = s.rstrip("Z").partition(".")
        t = datetime.strptime(base, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
        return t + (float("0." + frac) if frac else 0.0)
    except ValueError:
        return None


def _kind(attempt: int) -> str:
    return "first_send" if attempt == 1 else ("resend" if attempt >= 2 else "unknown")


def cell_attribution(cell: Path, validity: float) -> dict:
    raw = cell / "raw"
    plants = sorted(raw.glob("**/plant.jsonl"))
    first_effect = {}
    for p in plants[:1]:  # one plant per cell (a single-host run may hold a copy under raw/A and raw/E)
        for e in _jsonl(p):
            cid, t = e.get("cmd"), e.get("t_ns")
            if cid and t:
                first_effect[cid] = min(first_effect.get(cid, t), t)
    # every effect record once (a single-host run fetches the same state directory as raw/A and raw/E)
    records = set()
    for f in sorted(raw.glob("**/*.effects.jsonl")):
        for e in _jsonl(f):
            if e.get("command_id"):
                records.add((e["command_id"], int(e.get("at_unix_ms") or 0), int(e.get("attempt") or 0)))
    attempt = {}  # identity -> attempt of its first effect
    by_attempt = Counter()
    for cid, at, a in sorted(records, key=lambda r: (r[1], r[0], r[2])):
        attempt.setdefault(cid, a)
        by_attempt[_kind(a)] += 1
    sent = {}  # (identity, attempt) -> wall time of the dispatch (s)
    sends = {}  # identity -> wall times of all its dispatches (runs before 077 log no attempt number)
    for f in sorted(raw.glob("**/authority*.jsonl")):
        for e in _jsonl(f):
            if e.get("event") == "OUTBOX_DISPATCH_ATTEMPT" and e.get("command_id"):
                t = _wall(e.get("wall_time", ""))
                if t is None:
                    continue
                sends.setdefault(e["command_id"], set()).add(t)
                if e.get("attempt"):
                    sent.setdefault((e["command_id"], int(e["attempt"])), t)
    out = Counter()
    for c in _jsonl(cell / "clients.jsonl"):
        firsts = [(first_effect[i], i) for i in c.get("ids", []) if i in first_effect]
        if not firsts:
            continue
        t_ns, cid = min(firsts)
        if t_ns / 1e9 > float(c["arrival"]) + validity:
            out["late_effects"] += 1
            a = attempt.get(cid, 0)
            out["late_" + _kind(a)] += 1
            if a == 0 and any(float(c["arrival"]) + validity < x <= t_ns / 1e9 for x in sends.get(cid, ())):
                # no effect record: the identity was dispatched after the validity and before it took effect, so the
                # runtime may have caused it (an upper bound for runs before release 077)
                out["late_unknown_dispatched_after_validity"] += 1
            if a >= 2:
                ts = sent.get((cid, a))
                when = "unknown" if ts is None else ("after" if ts > float(c["arrival"]) + validity else "in")
                out[f"late_resend_sent_{when}_validity"] += 1
    out.update({f"effects_{k}": v for k, v in by_attempt.items()})
    return dict(out)


def run_attribution(run: Path) -> dict:
    cfg = json.loads((run / "config.json").read_text())
    w = cfg["workload"]
    validity = float(w.get("validity_s", w["deadline_s"]))
    rows = {}
    if (run / "runtime_episodes.csv").exists():
        with open(run / "runtime_episodes.csv", newline="") as fh:
            rows = {(r["contract"], r["policy"], int(r["episode"])): r for r in csv.DictReader(fh)
                    if str(r.get("cell_status", "")).startswith("VALID")}
    cells, groups, mismatch = [], defaultdict(Counter), []
    for (ct, pol, ep), r in sorted(rows.items()):
        cell = run / "cells" / f"ep{ep}" / ct / pol
        a = cell_attribution(cell, validity)
        cells.append({"contract": ct, "policy": pol, "episode": ep, **a})
        groups[f"{ct}/{pol}"].update(a)
        groups[f"{ct}/{pol}"]["cells"] += 1
        if int(a.get("late_effects", 0)) != int(float(r.get("late_effects") or 0)):
            mismatch.append(f"ep{ep}/{ct}/{pol}: recounted {a.get('late_effects', 0)}, row {r.get('late_effects')}")
    return {"run": str(run), "campaign": cfg.get("name"), "validity_s": validity, "groups": {k: dict(v) for k, v in groups.items()},
            "cells": cells, "late_mismatch": mismatch}


def markdown(res: list) -> str:
    L = ["# Late effects by the attempt that caused them (release 077)", "",
         "A late effect: an intent's first effect at the plant after its validity. `first send` = attempt 1 of the identity "
         "that took effect; `resend` = a later attempt of the same identity; `unknown` = no effect record (a run before 077).", "",
         "| campaign | contract/policy | cells | late effects | first send | resend (sent in validity / after) | unknown (dispatched after validity) | effects: first send / resend / unknown |",
         "|---|---|---|---|---|---|---|---|"]
    for r in res:
        for g, c in sorted(r["groups"].items()):
            L.append(f"| {r['campaign']} | {g} | {c.get('cells', 0)} | {c.get('late_effects', 0)} | {c.get('late_first_send', 0)} | "
                     f"{c.get('late_resend', 0)} ({c.get('late_resend_sent_in_validity', 0)} / {c.get('late_resend_sent_after_validity', 0)}) | "
                     f"{c.get('late_unknown', 0)} ({c.get('late_unknown_dispatched_after_validity', 0)}) | {c.get('effects_first_send', 0)} / "
                     f"{c.get('effects_resend', 0)} / {c.get('effects_unknown', 0)} |")
    mm = [m for r in res for m in r["late_mismatch"]]
    if mm:
        L += ["", "Recount differs from the runner's row: " + "; ".join(mm[:10])]
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--campaign", action="append", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    res = [run_attribution(Path(c)) for c in a.campaign]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "late_attribution.json").write_text(json.dumps(res, indent=2) + "\n")
    (out / "LATE_ATTRIBUTION.md").write_text(markdown(res))
    print(f"late_attribution: {len(res)} run(s) -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
