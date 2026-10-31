#!/usr/bin/env python3
"""Level-2 analyses of release 084: answers to the external review of 31 October 2026 from the existing raw traces
(no new cells; not hypotheses of any plan).

`evicon_review.py report` runs this over every run it reads (campaign runs, measurement runs and the report's own files).
Every analysis copes with a lean archive: a cell or run without the files it needs is counted as "not available" and
named, never guessed. Each writes <out>/<name>/<NAME>.md and <name>.json; the index is <out>/L2_084.md and every
number the paper may quote is in <out>/l2_084.json (paper_tables.py turns it into macros and tables).

  eoo        (M8)  End of observation: the runner's exact procedure (evaluation/campaign.py run_cell, frozen), then per
                   campaign and policy: commands unresolved at the truth read, commands possibly in flight at the end
                   (sent, no terminal answer, not known STOPPED), effects during the drain, the last effect relative to
                   the drain start, effects logged after the truth read, and the clean / duplicate / late counts
                   recomputed from the plant log against the row (the boundary treated alike)
  tb_late    (M6)  Every late effect in the ThingsBoard runs: request time, creation t0, validity V_i, t0 + e + epsilon
                   and the effect time, classified
  readrelease (N6) The releases of RELEASE_ON_READ that followed an answered read (NOT_FOUND), with trace context
  admission  (N11) Per campaign, contract and policy: offered, admitted, not admitted, admitted after the deadline (A2),
                   deadline misses (not fulfilled exactly once by the deadline), next to the latency tables
  latency_n  (N7)  The sample count behind every fence and stop latency percentile (report/latency/latency.json),
                   percentiles with n < 20 flagged
  r5cf       (M3)  For every fence aborted with OWNER_UNCERTAIN: the identities the owner listed with terminal answers
                   and the unlisted pending identities at or below the cutoff that rule R5' would have resolved
  rubric     (M4)  Probe results re-classified with split Q_A semantics (contradicted only by APPLIED without effect or by
                   NOT_FOUND / STOPPED for an applied identity; UNCERTAIN or PENDING = not observed); every label that
                   changes, and whether any measured contract changes. The frozen rubric is not touched
  tb_delivery      ThingsBoard's delivery of RPCs to the reference device: the QoS and retry settings from the code, and
                   per cell the deliveries per identity at the device and the duplicate effects (the device executes every
                   delivery: no deduplication)
  service    (M10) The authority's own work per request in the overhead runs (rq4-load-*, rq4-scale-*): per admitted
                   first-attempt request the sum lock + evaluation + commit (the components of overhead.py: lock = the
                   ADMISSION_RECEIVED wait, evaluation = request read -> ADMISSION_PREPARE, commit = ADMISSION_PREPARE ->
                   DECISION_RECORDED), its p50 / p95 / p99, next to the sum of the component medians (the paper's figure)

    python3 evaluation/review_l2_084.py --out <report>/l2_084 --report <report> --campaign RUN ... --measure RUN_ROOT ...
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
NA = "not available"
TERMINAL = ("APPLIED", "STOPPED")
SAFETY = ("bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied")

PROCEDURE = (
    "The cell procedure of evaluation/campaign.py (run_cell, frozen since release 062; every runner of every release uses it): "
    "requests arrive during the first duration_s (300 s) of the cell; one submitter per device submits each intent, and "
    "re-submits a STOPPED one under a new identity, until the intent's deadline (arrival + deadline_s, 60 s), so submission "
    "ends at t0 + duration_s + deadline_s (360 s). Then the runner sets its stop flag (the drain starts): the submitters stop "
    "(joined, at most 10 s), the fault runners end their current window early (joined, at most 30 s; a device in an offline "
    "window is set online, a crash window is disarmed), every fault proxy is cleared (FaultProxy.clear: both directions back "
    "to PASS; a fault applies to bytes that arrive after the call, so bytes already delayed keep their schedule and are "
    "delivered, dropped bytes stay lost), every device is set online (a queue-mode device executes the requests it held), "
    "and the runner waits tail_s (60 s) with the authorities and owners running (the authorities keep dispatching, "
    "resending under the retry guard and resolving in their sweeps). Then it reads the truth from the plant and each "
    "admitted identity's status from its authority (campaign.collect_live: the truth read), and stops the processes. "
    "Every count of the row is taken at the truth read: clean fulfilment (exactly one effect, the first by the deadline), "
    "duplicate effects (identities with more than one effect), late effects (first effect after arrival + validity_s) and "
    "unresolved at the end (admitted identity not APPLIED or STOPPED at its authority). An effect after the truth read is "
    "unobserved by all of them alike (censoring); the plant log written until the processes stop shows whether any occurred.")


# ------------------------------------------------------------------ shared readers
def read_json(p: Path, default=None):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return default


def read_jsonl(p: Path, needles=None):
    """Parsed lines of a JSONL file; with needles, only lines containing one of them (fast prefilter)."""
    out = []
    try:
        with open(p, errors="replace") as fh:
            for line in fh:
                if needles and not any(n in line for n in needles):
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return None
    return out


_FRAC = re.compile(r"^(.*T\d\d:\d\d:\d\d)(?:\.(\d+))?(.*)$")


def ts(s):
    """RFC 3339 wall time -> epoch seconds (campaign._ts)."""
    if not s:
        return None
    m = _FRAC.match(str(s).strip().replace("Z", "+00:00"))
    if not m:
        return None
    head, frac, tz = m.group(1), m.group(2) or "", m.group(3)
    try:
        base = datetime.fromisoformat(head + tz).timestamp()
    except ValueError:
        return None
    return base + (int(frac) / 10 ** len(frac) if frac else 0.0)


def run_label(run: Path) -> str:
    return f"{run.parent.name}@{run.name}"


def campaign_rows(run: Path) -> list:
    p = run / "runtime_episodes.csv"
    if not p.exists():
        return []
    with open(p, newline="") as fh:
        return [r for r in csv.DictReader(fh)]


def cell_dir(run: Path, r: dict) -> Path:
    return run / "cells" / f"ep{r['episode']}" / r["contract"] / r["policy"]


def num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def files(celldir: Path, pattern: str) -> list:
    return sorted(celldir.glob(f"raw/**/{pattern}")) or sorted(celldir.glob(pattern))


def authority_traces(celldir: Path) -> list:
    """The cell's authority traces: VM-A's copy (raw/A/), else raw/ itself, else anywhere under raw/ (as overhead.py reads
    them; a copy fetched from another VM, e.g. on a one-machine simulated testbed, is not counted twice)."""
    return (sorted(celldir.glob("raw/A/authority*.jsonl")) or sorted(celldir.glob("raw/authority*.jsonl"))
            or sorted(celldir.glob("raw/**/authority*.jsonl")))


def plant_log(celldir: Path):
    fs = files(celldir, "plant.jsonl")
    if not fs:
        return None
    return read_jsonl(fs[0]) or []


def clients(celldir: Path):
    rows = read_jsonl(celldir / "clients.jsonl")
    return rows if rows else None


def schedule_t0(run: Path, episode, cl: list):
    """The cell's start t0 (wall seconds): a client's arrival minus its scheduled offset."""
    sch = read_json(run / f"schedule_ep{episode}.json")
    if not sch or not cl:
        return None
    off = {f"d{a['device']}-c{a['seq']}": a["t"] for a in sch.get("arrivals", [])}
    vals = [c["arrival"] - off[c["cid"]] for c in cl if c.get("cid") in off]
    return statistics.median(vals) if vals else None


def md_table(header: list, rows: list) -> list:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for r in rows:
        out.append("| " + " | ".join(str(x) for x in r) + " |")
    return out


def fmt(v, nd=1):
    if v is None or v == NA:
        return NA
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


# ------------------------------------------------------------------ 1. end of observation (M8)
def eoo_cell(run: Path, cfg: dict, r: dict) -> dict:
    cd = cell_dir(run, r)
    w = cfg.get("workload", {})
    duration, deadline = float(cfg.get("duration_s", 300)), float(w.get("deadline_s", 60))
    validity, tail = float(w.get("validity_s", deadline)), float(cfg.get("tail_s", 10))
    out = {"unresolved_at_end": int(num(r.get("unresolved_at_end"))), "available": False}
    cl, pl = clients(cd), plant_log(cd)
    if cl is None or pl is None:
        return out
    t0 = schedule_t0(run, r["episode"], cl)
    if t0 is None:
        return out
    drain = t0 + duration + deadline
    faults = read_jsonl(cd / "faults.jsonl") or []
    last_fault_end = max([f.get("actual_end") or 0 for f in faults] + [drain])
    truth_read = last_fault_end + tail
    eff = defaultdict(list)  # identities are unique within a cell (d<device>-c<n>[-r<k>])
    for e in pl:
        eff[e.get("cmd")].append(e.get("t_ns", 0) / 1e9)
    times = sorted(t for v in eff.values() for t in v)
    out.update(available=True, t0=t0, drain_start=drain, truth_read_nominal=truth_read,
               effects_during_drain=sum(1 for t in times if drain <= t <= truth_read),
               effects_after_truth_read=sum(1 for t in times if t > truth_read + 2.0),
               last_effect_rel_drain_s=(round(times[-1] - drain, 3) if times else None))
    # recompute the row's boundary-sensitive counts from the plant log, per intent: with the effects up to the nominal truth
    # read and with every effect logged until the processes stopped
    def recount(cap):
        clean = late = logical = first_drain = dup_drain = 0
        dup_ids = set()
        for c in cl:
            effects_total, ids_eff, first = 0, 0, None
            for cid in c.get("ids", []):
                ts_ = sorted(t for t in eff.get(cid, []) if t <= cap)
                if ts_:
                    effects_total += len(ts_)
                    ids_eff += 1
                    first = ts_[0] if first is None else min(first, ts_[0])
                    if len(ts_) > 1:
                        dup_ids.add(cid)
                        dup_drain += sum(1 for t in ts_[1:] if t >= drain)
                    if ts_[0] >= drain:
                        first_drain += 1
            logical += ids_eff > 1
            clean += bool(effects_total == 1 and first <= c["arrival"] + deadline)
            late += bool(first is not None and first > c["arrival"] + validity)
        return {"clean_fulfilled": clean, "late_effects": late, "duplicate_effects": len(dup_ids), "logical_duplicates": logical}, first_drain, dup_drain
    at_read, first_drain, dup_drain = recount(truth_read + 2.0)
    full = recount(float("inf"))[0]
    out.update(recomputed=at_read, recomputed_all_logged=full,
               row={k: int(num(r.get(k))) for k in ("clean_fulfilled", "late_effects", "duplicate_effects", "logical_duplicates")},
               first_effects_during_drain=first_drain, duplicate_effects_during_drain=dup_drain)
    out["consistent"] = out["row"] in (out["recomputed"], out["recomputed_all_logged"])
    # possibly in flight at the end: dispatched, no terminal answer, not known STOPPED (authority traces)
    traces = authority_traces(cd)
    if traces:
        sent, terminal = set(), set()
        for tr in traces:
            for e in read_jsonl(tr, ("OUTBOX_DISPATCH_ATTEMPT", "EVIDENCE_COMMITTED", "OUTBOX_RECEIPT_COMMITTED", "\"RELEASE_ON_")) or []:
                cid, ev, txt = e.get("command_id"), e.get("event", ""), (e.get("outcome") or "") + (e.get("reason") or "")
                if not cid:
                    continue
                if ev == "OUTBOX_DISPATCH_ATTEMPT":
                    sent.add(cid)
                elif ev in ("EVIDENCE_COMMITTED", "OUTBOX_RECEIPT_COMMITTED") and any(x in txt for x in TERMINAL):
                    terminal.add(cid)
                elif ev in ("RELEASE_ON_TIMEOUT", "RELEASE_ON_READ"):
                    terminal.add(cid)
        admitted = {cid for c in cl for cid in (c.get("admitted") or {})}
        fl = sorted((sent & admitted) - terminal)
        with_eff = [cid for cid in fl if eff.get(cid)]
        out.update(in_flight_at_end=len(fl), in_flight_with_effect=len(with_eff), in_flight_without_effect=len(fl) - len(with_eff))
    else:
        out.update(in_flight_at_end=NA, in_flight_with_effect=NA, in_flight_without_effect=NA)
    return out


def eoo(runs: list) -> dict:
    groups = {}
    na_runs = []
    for run in runs:
        cfg = read_json(run / "config.json", {})
        rows = [r for r in campaign_rows(run) if str(r.get("cell_status", "")).startswith("VALID")]
        if not rows:
            continue
        for r in rows:
            k = (run_label(run), r["contract"], r["policy"])
            g = groups.setdefault(k, {"run": k[0], "contract": k[1], "policy": k[2], "cells": 0, "cells_with_logs": 0,
                                      "unresolved_at_end": 0, "in_flight_at_end": 0, "in_flight_with_effect": 0, "in_flight_without_effect": 0,
                                      "cells_with_traces": 0, "effects_during_drain": 0, "first_effects_during_drain": 0,
                                      "duplicate_effects_during_drain": 0, "effects_after_truth_read": 0,
                                      "last_effect_rel_drain_s_max": None, "inconsistent_cells": []})
            c = eoo_cell(run, cfg, r)
            g["cells"] += 1
            g["unresolved_at_end"] += c["unresolved_at_end"]
            if not c["available"]:
                continue
            g["cells_with_logs"] += 1
            for f in ("effects_during_drain", "first_effects_during_drain", "duplicate_effects_during_drain", "effects_after_truth_read"):
                g[f] += c[f]
            if c.get("in_flight_at_end") != NA:
                g["cells_with_traces"] += 1
                for f in ("in_flight_at_end", "in_flight_with_effect", "in_flight_without_effect"):
                    g[f] += c[f]
            le = c.get("last_effect_rel_drain_s")
            if le is not None:
                g["last_effect_rel_drain_s_max"] = le if g["last_effect_rel_drain_s_max"] is None else max(g["last_effect_rel_drain_s_max"], le)
            if not c["consistent"]:
                g["inconsistent_cells"].append({"cell": f"ep{r['episode']}", "row": c["row"], "recomputed": c["recomputed"]})
        if all(g["cells_with_logs"] == 0 for k, g in groups.items() if k[0] == run_label(run)):
            na_runs.append(run_label(run))
    rows = sorted(groups.values(), key=lambda g: (g["run"], g["contract"], g["policy"]))
    tot = {f: sum(g[f] for g in rows) for f in ("cells", "cells_with_logs", "cells_with_traces", "unresolved_at_end", "in_flight_at_end",
                                                "in_flight_without_effect", "effects_during_drain", "first_effects_during_drain",
                                                "duplicate_effects_during_drain", "effects_after_truth_read")}
    tot["inconsistent_cells"] = sum(len(g["inconsistent_cells"]) for g in rows)
    lasts = [g["last_effect_rel_drain_s_max"] for g in rows if g["last_effect_rel_drain_s_max"] is not None]
    tot["last_effect_rel_drain_s_max"] = max(lasts) if lasts else None
    return {"procedure": PROCEDURE, "groups": rows, "totals": tot, "runs_without_logs": na_runs}


def eoo_md(res: dict) -> str:
    L = ["# End of observation (review M8)", "", res["procedure"], "",
         "Per campaign and policy, from the plant log, the clients' log, the fault log and the authority traces of every VALID cell "
         "(times in seconds; the drain starts at t0 + duration_s + deadline_s; the truth read is taken as the end of the last fault "
         "window, or the drain start if later, plus tail_s; the plant's wall clock is used as the row uses it).", ""]
    t = res["totals"]
    L += [f"Totals: {t['cells']} cells ({t['cells_with_logs']} with plant and client logs, {t['cells_with_traces']} with authority traces); "
          f"unresolved at the truth read {t['unresolved_at_end']}; possibly in flight at the end {t['in_flight_at_end']} "
          f"({t['in_flight_without_effect']} of them without an effect); effects during the drain {t['effects_during_drain']} "
          f"(first effects {t['first_effects_during_drain']}, duplicates {t['duplicate_effects_during_drain']}); effects logged after the "
          f"truth read {t['effects_after_truth_read']}; last effect {fmt(t['last_effect_rel_drain_s_max'])} s after the drain start; "
          f"cells whose clean / duplicate / late / logical-duplicate counts recomputed from the plant log differ from the row: "
          f"{t['inconsistent_cells']}.", ""]
    rows = [[g["run"], g["contract"], g["policy"], g["cells"], g["cells_with_logs"], g["unresolved_at_end"],
             g["in_flight_at_end"] if g["cells_with_traces"] else NA, g["in_flight_without_effect"] if g["cells_with_traces"] else NA,
             g["effects_during_drain"], g["duplicate_effects_during_drain"], fmt(g["last_effect_rel_drain_s_max"]), g["effects_after_truth_read"],
             len(g["inconsistent_cells"])] for g in res["groups"]]
    L += md_table(["run", "contract", "policy", "cells", "with logs", "unresolved at truth read", "in flight at end", "of which no effect",
                   "effects in drain", "duplicates in drain", "last effect after drain start (s)", "effects after truth read", "inconsistent cells"], rows)
    if res["runs_without_logs"]:
        L += ["", f"Runs whose cells have no plant or client logs in this archive ({NA}): " + ", ".join(res["runs_without_logs"])]
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------ 2. ThingsBoard late effects (M6)
def tb_runs(runs: list) -> list:
    return [r for r in runs if ((read_json(r / "config.json", {}) or {}).get("real_owner") or {}).get("system") == "thingsboard"]


def tb_late(runs: list) -> dict:
    out, na = [], []
    for run in tb_runs(runs):
        cfg = read_json(run / "config.json", {})
        w = cfg.get("workload", {})
        validity = float(w.get("validity_s", w.get("deadline_s", 60)))
        e_s = float((cfg.get("real_owner") or {}).get("expiration_s", 60))
        eps_s = 0.0
        for spec in ((cfg.get("real_owner") or {}).get("contracts") or {}).values():
            c = read_json(Path(str(spec.get("contract_file", ""))), None) or read_json(run / "contracts" / Path(str(spec.get("contract_file", ""))).name, None)
            if c:
                e_s = float(c.get("expiry_ms", e_s * 1000)) / 1000.0
                eps_s = float(c.get("clock_skew_ms", 0)) / 1000.0
        for r in campaign_rows(run):
            if not str(r.get("cell_status", "")).startswith("VALID") or int(num(r.get("late_effects"))) == 0:
                continue
            cd = cell_dir(run, r)
            cl, pl = clients(cd), plant_log(cd)
            if cl is None or pl is None:
                na.append(f"{run_label(run)} ep{r['episode']}/{r['contract']}/{r['policy']} ({r['late_effects']} late)")
                continue
            created = {}
            for tr in authority_traces(cd):
                for e in read_jsonl(tr, ("RESERVATION_COMMITTED",)) or []:
                    if e.get("command_id") and e.get("event") == "RESERVATION_COMMITTED":
                        created.setdefault(e["command_id"], ts(e.get("wall_time")))
            gw = {}
            for g in files(cd, "gateway*.jsonl"):
                for e in read_jsonl(g, ("\"effect\"",)) or []:
                    if e.get("op") == "effect" and e.get("cid"):
                        gw.setdefault(e["cid"], e)
            eff = defaultdict(list)
            for e in pl:
                eff[e.get("cmd")].append(e.get("t_ns", 0) / 1e9)
            for c in cl:
                firsts = [(min(eff[cid]), cid) for cid in c.get("ids", []) if eff.get(cid)]
                if not firsts:
                    continue
                t_eff, cid = min(firsts)
                v_i = c["arrival"] + validity
                if t_eff <= v_i:
                    continue
                t0 = created.get(cid)
                bound = (t0 + e_s + eps_s) if t0 is not None else None
                if bound is None:
                    cls = "creation time not available (authority trace missing)"
                elif t_eff <= bound:
                    cls = ("within the owner expiry: the effect is before t0 + e + epsilon; V_i < t0 + e + epsilon because the identity was "
                           f"created {t0 - c['arrival']:.1f} s after the request (T bounds the effect from creation, validity from the request)")
                else:
                    cls = "after t0 + e + epsilon: an effect the measured expiry T should have prevented"
                out.append({"run": run_label(run), "cell": f"ep{r['episode']}/{r['contract']}/{r['policy']}", "intent": c["cid"], "identity": cid,
                            "reissue": cid != c["cid"], "request_s": 0.0, "created_s": None if t0 is None else round(t0 - c["arrival"], 3),
                            "validity_s": round(validity, 3), "expiry_bound_s": None if bound is None else round(bound - c["arrival"], 3),
                            "effect_s": round(t_eff - c["arrival"], 3), "rpc_status": gw.get(cid, {}).get("status", NA), "class": cls})
    return {"expiry_s": None, "late_effects": out, "not_available": na,
            "within_expiry": sum(1 for x in out if x["class"].startswith("within")), "after_expiry": sum(1 for x in out if x["class"].startswith("after"))}


def tb_late_md(res: dict) -> str:
    L = ["# ThingsBoard late effects (review M6)", "",
         "Every intent of a ThingsBoard run whose first effect came after its validity V_i = request + validity_s. Times in seconds "
         "after the request (the client's arrival); t0 = the identity's creation at the authority (RESERVATION_COMMITTED), e = the "
         "contract's expiry (60 s), epsilon = its clock skew; the gateway sends expirationTime = t0 + e to ThingsBoard.", "",
         f"Within the owner expiry: {res['within_expiry']}; after it: {res['after_expiry']}.", ""]
    rows = [[x["run"], x["cell"], x["intent"], x["identity"] + (" (reissue)" if x["reissue"] else ""), fmt(x["created_s"]), fmt(x["validity_s"]),
             fmt(x["expiry_bound_s"]), fmt(x["effect_s"]), x["rpc_status"], x["class"]] for x in res["late_effects"]]
    L += md_table(["run", "cell", "intent", "identity", "t0", "V_i", "t0 + e + eps", "effect", "RPC status", "classification"], rows) if rows else ["No late effect in the ThingsBoard runs read."]
    if res["not_available"]:
        L += ["", f"Cells with late effects but without logs ({NA}): " + "; ".join(res["not_available"])]
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------ 3. ReadRelease after an answered read (N6)
def readrelease(runs: list) -> dict:
    found, counts, na = [], [], []
    for run in runs:
        rows = [r for r in campaign_rows(run) if r.get("policy") == "RELEASE_ON_READ" and str(r.get("cell_status", "")).startswith("VALID")]
        if not rows:
            continue
        cfg = read_json(run / "config.json", {})
        by = Counter()
        for r in rows:
            cd = cell_dir(run, r)
            traces = authority_traces(cd)
            if not traces:
                na.append(f"{run_label(run)} ep{r['episode']}/{r['contract']}")
                continue
            for tr in traces:
                evs = read_jsonl(tr) or []
                for e in evs:
                    if e.get("event") != "RELEASE_ON_READ":
                        continue
                    reason = e.get("reason", "")
                    kind = reason.rsplit("_", 1)[-1] if "NOT_REPORTED" in reason else reason.split(":")[-1]
                    by[kind] += 1
                    if "NOT_FOUND" in reason:
                        cid = e.get("command_id")
                        ctx = [{k: x.get(k) for k in ("wall_time", "event", "outcome", "reason", "attempt", "decision") if x.get(k) not in (None, "")}
                               for x in evs if x.get("command_id") == cid or x.get("query_command_id") == cid]
                        d = int(re.sub(r"\D", "", tr.stem) or 0)
                        pl = plant_log(cd) or []
                        effects = [p for p in pl if p.get("cmd") == cid]
                        flt = [f for f in (read_jsonl(cd / "faults.jsonl") or []) if f.get("device") == d]
                        jr = files(cd, f"pump{d}-1.journal")
                        journal = [j for j in (read_jsonl(jr[0]) or []) if (j.get("r") or {}).get("id") == cid] if jr else NA
                        rel_t = ts(e.get("wall_time"))
                        near = [f for f in flt if rel_t and f.get("actual_start") and abs(f["actual_start"] - rel_t) < 60]
                        if effects:
                            expl = (f"the owner took the command's effect at plant time {effects[0].get('t_ns', 0) / 1e9:.1f} "
                                    f"({len(effects)} effect(s)), after the read found no record: a late copy")
                        elif journal == NA:
                            expl = "no effect in the plant log; the device journal is not available"
                        elif not journal:
                            expl = ("no effect and no device record: the request never reached the owner (lost on the way), so the "
                                    "answered NOT_FOUND was true and the release cost nothing")
                        else:
                            expl = f"no effect; the device journal holds {[ (j.get('r') or {}).get('status') for j in journal]}"
                        found.append({"run": run_label(run), "channel": "direct" if "direct" in run.parent.name else "queue",
                                      "cell": f"ep{r['episode']}/{r['contract']}", "authority": tr.name, "command": cid, "reason": reason,
                                      "trace": ctx[:40], "plant_effects": len(effects), "device_journal": journal,
                                      "fault_windows_within_60s": near, "explanation": expl})
        counts.append({"run": run_label(run), "offline_mode": (cfg.get("faults") or {}).get("device_args", []), "releases": dict(by)})
    return {"answered_releases": found, "counts": counts, "not_available": na}


def readrelease_md(res: dict) -> str:
    L = ["# ReadRelease after an answered read (review N6)", "",
         "Every release of RELEASE_ON_READ (release 077) by the read's outcome; a release after NOT_FOUND followed a read the owner "
         "answered, every other one a read it did not answer (UNREACHABLE).", ""]
    L += md_table(["run", "releases by read outcome"], [[c["run"], ", ".join(f"{k} {v}" for k, v in sorted(c["releases"].items()))] for c in res["counts"]])
    for x in res["answered_releases"]:
        L += ["", f"## {x['run']} {x['cell']} {x['command']} ({x['channel']} channel)", "", f"Explanation: {x['explanation']}.", "",
              f"Plant effects of this identity: {x['plant_effects']}; device journal: `{json.dumps(x['device_journal'])[:600]}`", "",
              "Authority trace of the identity:", ""]
        L += md_table(["wall time", "event", "outcome", "reason"], [[t.get("wall_time", ""), t.get("event", ""), t.get("outcome", ""), str(t.get("reason", ""))[:120]]
                                                                   for t in x["trace"]])
        if x["fault_windows_within_60s"]:
            L += ["", "Fault windows of the device within 60 s of the release:", ""]
            L += md_table(["kind", "planned start", "duration (s)", "detail"], [[f.get("kind"), f.get("planned_start"), f.get("planned_dur"), f.get("detail", "")]
                                                                               for f in x["fault_windows_within_60s"]])
    if not res["answered_releases"]:
        L += ["", "No release after an answered read in the runs read."]
    if res["not_available"]:
        L += ["", f"Cells without authority traces ({NA}): " + "; ".join(res["not_available"])]
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------ 4. admission accounting (N11)
def admission(runs: list) -> dict:
    groups = {}
    for run in runs:
        cfg = read_json(run / "config.json", {})
        deadline = float((cfg.get("workload") or {}).get("deadline_s", 60))
        for r in campaign_rows(run):
            if not str(r.get("cell_status", "")).startswith("VALID"):
                continue
            k = (run_label(run), r["contract"], r["policy"])
            g = groups.setdefault(k, {"run": k[0], "contract": k[1], "policy": k[2], "cells": 0, "offered": 0, "admitted": 0, "not_admitted": 0,
                                      "admitted_after_deadline": 0, "clients_available": 0, "clean_fulfilled": 0, "fulfilled_late": 0,
                                      "deadline_misses": 0})
            g["cells"] += 1
            off, adm, clean = int(num(r["offered"])), int(num(r["admitted"])), int(num(r["clean_fulfilled"]))
            g["offered"] += off
            g["admitted"] += adm
            g["not_admitted"] += off - adm
            g["clean_fulfilled"] += clean
            g["fulfilled_late"] += int(num(r.get("fulfilled_late")))
            g["deadline_misses"] += off - clean
            cl = clients(cell_dir(run, r))
            if cl is not None:
                g["clients_available"] += 1
                for c in cl:
                    adm_t = [t for t in (c.get("admitted") or {}).values()]
                    if adm_t and min(adm_t) > c["arrival"] + deadline:
                        g["admitted_after_deadline"] += 1
    rows = sorted(groups.values(), key=lambda g: (g["run"], g["contract"], g["policy"]))
    for g in rows:
        g["admitted_pct"] = round(100.0 * g["admitted"] / g["offered"], 2) if g["offered"] else None
        g["deadline_miss_pct"] = round(100.0 * g["deadline_misses"] / g["offered"], 2) if g["offered"] else None
        if g["clients_available"] < g["cells"]:
            g["admitted_after_deadline"] = NA if not g["clients_available"] else g["admitted_after_deadline"]
    return {"groups": rows}


def admission_md(res: dict) -> str:
    L = ["# Admission accounting (review N11)", "",
         "Latency over admitted requests is conditional on admission. Per campaign, contract and policy: requests offered, admitted, not "
         "admitted (expired while deferred or blocked), admitted after their deadline (the first identity's admission later than arrival + "
         "deadline_s, from the clients' log; A2 of release 083), and deadline misses (not fulfilled exactly once by the deadline: offered "
         "minus clean fulfilment; of them fulfilled late = one effect after the deadline).", ""]
    rows = [[g["run"], g["contract"], g["policy"], g["cells"], g["offered"], g["admitted"], g["not_admitted"], g["admitted_after_deadline"],
             g["deadline_misses"], fmt(g["deadline_miss_pct"], 1), g["fulfilled_late"]] for g in res["groups"]]
    L += md_table(["run", "contract", "policy", "cells", "offered", "admitted", "not admitted", "admitted after deadline", "deadline misses",
                   "misses %", "of which fulfilled late"], rows)
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------ 5. sample counts of latency percentiles (N7)
def latency_n(report: Path) -> dict:
    rows = read_json(report / "latency" / "latency.json", None) if report else None
    if not isinstance(rows, list):
        return {"rows": [], "available": False}
    newest = {}
    for x in rows:  # the paper table's selection: newest review per (profile, action), with terminal answers
        k = (x.get("profile"), x.get("action"))
        if x.get("n_terminal") and (k not in newest or x["review"] > newest[k]["review"]):
            newest[k] = x
    out = []
    for (prof, act), x in sorted(newest.items()):
        if act not in ("query", "stop", "fence", "query_after"):
            continue
        n = int(x["n_terminal"])
        out.append({"profile": prof, "system": x.get("system"), "action": act, "review": x.get("review"), "n": n,
                    "p50_ms": x.get("p50_ms"), "p95_ms": x.get("p95_ms"), "max_ms": x.get("max_ms"),
                    "flag": "n < 20: p95 is (close to) the maximum of few samples" if n < 20 else ""})
    return {"rows": out, "available": True, "flagged": sum(1 for r in out if r["flag"]),
            "fence_min_n": min([r["n"] for r in out if r["action"] == "fence"], default=None),
            "stop_min_n": min([r["n"] for r in out if r["action"] == "stop"], default=None)}


def latency_n_md(res: dict) -> str:
    L = ["# Sample counts behind the evidence-latency percentiles (review N7)", ""]
    if not res["available"]:
        return "\n".join(L + [f"report/latency/latency.json: {NA}."]) + "\n"
    L += [f"The rows of the paper's evidence-latency table (the newest review per profile and action; nearest-rank percentiles over the "
          f"terminal answers). {res['flagged']} percentile rows rest on fewer than 20 samples.", ""]
    L += md_table(["profile", "system", "action", "review", "n", "p50 (ms)", "p95 (ms)", "max (ms)", "flag"],
                  [[r["profile"], r["system"], r["action"], r["review"], r["n"], fmt(r["p50_ms"], 3), fmt(r["p95_ms"], 3), fmt(r["max_ms"], 3), r["flag"]]
                   for r in res["rows"]])
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------ 6. R5 counterfactual (M3)
def device_state_at(journal: list, token: str):
    """(cutoff requested, {id: (seq, status)}) as the device answered the fence of token: the journal replayed up to its
    CUTOFF entry and the seals of claims it wrote right after it; None if the token is not in the journal."""
    recs, cut, idx = {}, None, None
    for i, e in enumerate(journal):
        if e.get("k") == "REC" and e.get("r"):
            r = e["r"]
            recs[r["id"]] = (int(r.get("seq", 0)), r.get("status"))
        elif e.get("k") == "CUTOFF" and e.get("tok") == token:
            cut, idx = int(e.get("req") or e.get("c") or 0), i
            break
    if idx is None:
        return None
    for e in journal[idx + 1:]:
        if e.get("k") == "REC" and e.get("r") and (e["r"].get("reason") in ("SEALED_BY_FENCE", "NEG_CONTROL_SEALED_ORPHAN")):
            r = e["r"]
            recs[r["id"]] = (int(r.get("seq", 0)), r.get("status"))
        else:
            break
    return cut, recs


def r5cf(runs: list) -> dict:
    out, na = [], []
    for run in runs:
        rows = [r for r in campaign_rows(run) if str(r.get("cell_status", "")).startswith("VALID") and int(num(r.get("fences"))) > 0]
        if not rows:
            continue
        g = {"run": run_label(run), "groups": {}}
        for r in rows:
            cd = cell_dir(run, r)
            traces = authority_traces(cd)
            if not traces:
                na.append(f"{run_label(run)} ep{r['episode']}/{r['contract']}/{r['policy']}")
                continue
            cl = clients(cd) or []
            seq = {cid: int(s) for c in cl for cid, s in (c.get("seqs") or {}).items()}
            key = (r["contract"], r["policy"])
            s = g["groups"].setdefault(key, {"contract": key[0], "policy": key[1], "cells": 0, "aborted_owner_uncertain": 0, "with_journal": 0,
                                             "listed_terminal_pending": 0, "unlisted_sealable": 0, "listed_uncertain": 0,
                                             "distinct_resolvable": 0, "fences_resolving_any": 0})
            s["cells"] += 1
            for tr in traces:
                d = int(re.sub(r"\D", "", tr.stem) or 0)
                jr = files(cd, f"pump{d}-1.journal")
                journal = read_jsonl(jr[0]) if jr else None
                admitted, terminal = set(), set()
                resolvable = set()
                for e in read_jsonl(tr, ("RESERVATION_COMMITTED", "EVIDENCE_COMMITTED", "OUTBOX_RECEIPT_COMMITTED", "BARRIER_ABORTED", "\"RELEASE_ON_")) or []:
                    ev, cid = e.get("event"), e.get("command_id")
                    if ev == "RESERVATION_COMMITTED" and cid:
                        admitted.add(cid)
                    elif ev in ("EVIDENCE_COMMITTED", "OUTBOX_RECEIPT_COMMITTED") and cid and any(x in (e.get("outcome") or "") + (e.get("reason") or "") for x in TERMINAL):
                        terminal.add(cid)
                    elif ev in ("RELEASE_ON_TIMEOUT", "RELEASE_ON_READ") and cid:
                        terminal.add(cid)
                    elif ev == "BARRIER_ABORTED" and "OWNER_UNCERTAIN" in (e.get("reason") or ""):
                        s["aborted_owner_uncertain"] += 1
                        if journal is None:
                            continue
                        token = (e.get("reason") or "").split(":OWNER_UNCERTAIN", 1)[0]
                        st = device_state_at(journal, token)
                        if st is None:
                            continue
                        s["with_journal"] += 1
                        cut, recs = st
                        pending = {c for c in admitted - terminal if seq.get(c, 10 ** 12) <= cut}
                        lt = {c for c in pending if c in recs and recs[c][1] in TERMINAL}
                        un = {c for c in pending if c in recs and recs[c][1] not in TERMINAL}
                        sealable = {c for c in pending if c not in recs}
                        s["listed_terminal_pending"] += len(lt)
                        s["unlisted_sealable"] += len(sealable)
                        s["listed_uncertain"] += len(un)
                        s["fences_resolving_any"] += bool(lt or sealable)
                        resolvable |= lt | sealable
                if journal is None:
                    na.append(f"{run_label(run)} ep{r['episode']}/{r['contract']}/{r['policy']} device {d} journal")
                s["distinct_resolvable"] += len(resolvable)
        for s in g["groups"].values():
            s["per_aborted_fence"] = round((s["listed_terminal_pending"] + s["unlisted_sealable"]) / s["with_journal"], 2) if s["with_journal"] else None
            if s["aborted_owner_uncertain"]:
                out.append(dict(s, run=g["run"]))
    return {"groups": out, "not_available": sorted(set(na))[:200], "not_available_count": len(set(na)),
            "aborted_total": sum(s["aborted_owner_uncertain"] for s in out), "resolvable_total": sum(s["distinct_resolvable"] for s in out)}


def r5cf_md(res: dict) -> str:
    L = ["# Rule R5 counterfactual (review M3)", "",
         "Every fence the authority aborted because the owner's answer listed an identity UNCERTAIN (rule R5). The device's answer is "
         "reconstructed from its journal (its records at the fence's CUTOFF entry, with the claims it sealed right after); the pending "
         "identities from the authority trace (admitted, no terminal evidence before the abort, sequence at or below the cutoff). Rule R5' "
         "would have taken in the listed terminal answers of pending identities and sealed the unlisted pending ones as STOPPED; the "
         "UNCERTAIN ones stay pending under both rules. Distinct = identities resolvable by at least one aborted fence of the cell.", "",
         f"Aborted fences: {res['aborted_total']}; distinct identities R5' would have resolved: {res['resolvable_total']}.", ""]
    rows = [[s["run"], s["contract"], s["policy"], s["cells"], s["aborted_owner_uncertain"], s["with_journal"], s["listed_terminal_pending"],
             s["unlisted_sealable"], s["listed_uncertain"], fmt(s["per_aborted_fence"], 2), s["fences_resolving_any"], s["distinct_resolvable"]]
            for s in res["groups"]]
    L += md_table(["run", "contract", "policy", "cells", "aborted (OWNER_UNCERTAIN)", "with device journal", "listed terminal (pending)",
                   "unlisted, sealable", "listed UNCERTAIN", "resolvable per aborted fence", "fences that would resolve any", "distinct resolvable"], rows) if rows else ["No fence aborted with OWNER_UNCERTAIN in the runs read."]
    if res["not_available_count"]:
        L += ["", f"Cells or devices without traces or journals ({NA}): {res['not_available_count']} (first: " + "; ".join(res["not_available"][:10]) + ")"]
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------ 7. rubric re-classification (M4)
def split_qa(rows: list):
    """Q_A under split semantics for one repetition: (status, reason)."""
    by = {}
    for r in rows:
        by.setdefault(r.get("probe_id"), r)
    ok = lambda r: bool(r) and not r.get("error") and r.get("applicable", True)  # noqa: E731
    if any(ok(r) and r.get("false_applied") for r in rows):
        return "CONTRADICTED", "APPLIED for an identity without effect"
    p2 = by.get("P2")
    if not ok(p2):
        return "UNKNOWN", "P2 not run, not applicable or errored"
    q = p2.get("query")
    if q is None or not q.get("supported", False):
        return "ABSENT", "no identity-scoped query"
    if (p2.get("effects") or 0) < 1:
        return "UNKNOWN", "no positive control"
    if q.get("outcome") == "APPLIED" and q.get("terminal"):
        return "OBSERVED", ""
    if q.get("outcome") in ("NOT_FOUND", "STOPPED"):
        return "CONTRADICTED", f"query answered {q.get('outcome')} for an applied identity"
    if q.get("outcome") == "APPLIED":
        return "UNKNOWN", "APPLIED (true) but not terminal: not observed as evidence"
    return "UNKNOWN", f"query answered {q.get('outcome')}: not observed (unavailable)"


def merge_split(sts: list) -> str:
    if "CONTRADICTED" in sts:
        return "CONTRADICTED"
    if sts and all(s == "OBSERVED" for s in sts):
        return "OBSERVED"
    if "ABSENT" in sts and all(s in ("ABSENT", "UNKNOWN") for s in sts):
        return "ABSENT"
    return "UNKNOWN"


def rubric(measure_roots: list) -> dict:
    out = []
    seen = set()
    for root in measure_roots:
        for res in sorted(Path(root).glob("*/runs/*/*/results.jsonl")):
            d = res.parent
            rb = read_json(d / "rubric.json", None)
            ct = read_json(d / "contract.json", None)
            if rb is None or ct is None:
                continue
            key = (d.parent.name, d.name)
            if key in seen:
                continue
            seen.add(key)
            rows = read_jsonl(res) or []
            reps = sorted({r.get("repetition", 0) for r in rows})
            per = [split_qa([r for r in rows if r.get("repetition", 0) == rep]) for rep in reps]
            new = merge_split([p[0] for p in per])
            old = (rb.get("status") or {}).get("Q_A", "UNKNOWN")
            in_contract = "Q_A" in (ct.get("capabilities") or [])
            out.append({"stack": d.parent.parent.parent.name, "config": d.parent.name, "run": d.name, "contract_id": ct.get("id", ""),
                        "release_label": old, "split_label": new, "changed": old != new, "reasons": sorted({p[1] for p in per if p[1]}),
                        "q_a_in_contract": in_contract, "contract_changes": (new == "OBSERVED") != in_contract})
    return {"rows": out, "changed": [r for r in out if r["changed"]], "contracts_changed": [r for r in out if r["contract_changes"]]}


def rubric_md(res: dict) -> str:
    L = ["# Q_A re-classified with split semantics (review M4)", "",
         "The release's rubric (conformance/evicon_probe/rubric.py, frozen) calls Q_A CONTRADICTED whenever the query of an applied identity "
         "answers anything but terminal APPLIED. Split semantics separate answer soundness from availability: Q_A is contradicted only by "
         "APPLIED for an identity without effect, or by NOT_FOUND or STOPPED for an applied identity; UNCERTAIN or PENDING (and an APPLIED that "
         "is not terminal) is not observed (UNKNOWN). Re-classified from every measurement's results.jsonl, per repetition, merged as the "
         "rubric merges (a contradiction in any repetition wins; OBSERVED only if every repetition observed it). The stricter release rubric "
         "can only withhold Q_A, never grant it.", "",
         f"Measurements: {len(res['rows'])}; labels that change: {len(res['changed'])}; measured contracts that change: {len(res['contracts_changed'])}.", ""]
    rows = [[r["stack"], r["config"], r["run"], r["release_label"], r["split_label"], "; ".join(r["reasons"])[:160], "yes" if r["q_a_in_contract"] else "no",
             "YES" if r["contract_changes"] else "no"] for r in res["rows"] if r["changed"] or r["contract_changes"]]
    L += md_table(["stack", "config", "run", "release label", "split label", "reason", "Q_A in the measured contract", "contract changes"], rows) if rows else ["No label changes."]
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------ 8. ThingsBoard delivery to the reference device
def tb_settings() -> dict:
    """The QoS and retry settings of the ThingsBoard runs, read from the release's own files."""
    gw = (REPO / "conformance" / "reference" / "tb_owner_gateway.py").read_text()
    dev = (REPO / "conformance" / "reference" / "mqtt_device.py").read_text()
    run = (REPO / "evaluation" / "campaign_tb.py").read_text()
    m = re.search(r'"retries":\s*(\d+)', gw)
    sub = re.search(r'subscribe\("v1/devices/me/rpc/request/\+",\s*qos=(\d)\)', dev)
    resp = re.search(r'publish\(f"v1/devices/me/rpc/response/\{req_id\}",[^\n]*?qos=(\d)\)', dev)
    se = re.search(r'"--session-expiry", type=int, default=(\d+)', dev)
    argv = re.search(r'argv = \["python3", f"\{self.root_e\}/conformance/reference/mqtt_device.py".*?\]', run, re.S)
    return {"rpc": {"persistent": '"persistent": True' in gw, "retries": int(m.group(1)) if m else None, "two_way": "/api/rpc/twoway/" in gw,
                    "expiration": "expirationTime = the request's expires_at_ms (creation + the contract's expiry)"},
            "device": {"subscribe_qos": int(sub.group(1)) if sub else None, "response_qos": int(resp.group(1)) if resp else None,
                       "mqtt": "MQTT 5, clean_start=False" if "clean_start=False" in dev else "?", "session_expiry_s": int(se.group(1)) if se else None,
                       "app_dedup": bool(argv and "--app-dedup" in argv.group(0)),
                       "executes_every_delivery": "if self.a.app_dedup and cmd in self.done" in dev},
            "offline_fault": "POST /_test/online?v=false: the device sends DISCONNECT and stops its network loop; v=true reconnects with "
                             "the same client id (clean_start=False)"}


def tb_delivery(runs: list) -> dict:
    cells, na = [], []
    for run in tb_runs(runs):
        for r in campaign_rows(run):
            if not str(r.get("cell_status", "")).startswith("VALID"):
                continue
            cd = cell_dir(run, r)
            pl = plant_log(cd)
            gws = files(cd, "gateway*.jsonl")
            if pl is None:
                na.append(f"{run_label(run)} ep{r['episode']}/{r['contract']}/{r['policy']}")
                continue
            per_id = Counter(e.get("cmd") for e in pl)
            rpcs = defaultdict(set)
            for g in gws:
                for e in read_jsonl(g, ("\"effect\"",)) or []:
                    if e.get("op") == "effect" and e.get("cid") and e.get("rpc"):
                        rpcs[e["cid"]].add(e["rpc"])
            dups = {cid: n for cid, n in per_id.items() if n > 1}
            one_rpc_twice = [cid for cid in dups if gws and len(rpcs.get(cid, ())) <= 1]
            cells.append({"run": run_label(run), "cell": f"ep{r['episode']}/{r['contract']}/{r['policy']}", "identities_with_effect": len(per_id),
                          "deliveries": sum(per_id.values()), "max_deliveries_per_identity": max(per_id.values(), default=0),
                          "duplicate_effects": len(dups), "row_duplicate_effects": int(num(r.get("duplicate_effects"))),
                          "gateway_logs": bool(gws), "rpcs": sum(len(v) for v in rpcs.values()) if gws else NA,
                          "duplicates_one_rpc": len(one_rpc_twice) if gws else NA,
                          "duplicates_several_rpcs": (len(dups) - len(one_rpc_twice)) if gws else NA})
    by_policy = defaultdict(lambda: Counter())
    for c in cells:
        k = (c["run"], c["cell"].split("/", 1)[1])
        by_policy[k]["cells"] += 1
        for f in ("deliveries", "identities_with_effect", "duplicate_effects"):
            by_policy[k][f] += c[f]
        if c["duplicates_one_rpc"] != NA:
            by_policy[k]["duplicates_one_rpc"] += c["duplicates_one_rpc"]
    return {"settings": tb_settings(), "cells": cells, "groups": [dict(run=k[0], group=k[1], **v) for k, v in sorted(by_policy.items())],
            "not_available": na, "duplicates_total": sum(c["duplicate_effects"] for c in cells),
            "duplicates_one_rpc_total": sum(c["duplicates_one_rpc"] for c in cells if c["duplicates_one_rpc"] != NA)}


def tb_delivery_md(res: dict) -> str:
    s = res["settings"]
    L = ["# ThingsBoard: delivery of RPCs to the reference device", "",
         "Settings of every ThingsBoard run, from the release's files: the gateway creates a "
         f"{'persistent ' if s['rpc']['persistent'] else ''}{'two-way ' if s['rpc']['two_way'] else ''}RPC with retries = {s['rpc']['retries']} "
         f"({s['rpc']['expiration']}); the reference device subscribes to the RPC topic at QoS {s['device']['subscribe_qos']}, answers at QoS "
         f"{s['device']['response_qos']}, connects with {s['device']['mqtt']} and a session expiry of {s['device']['session_expiry_s']} s, and runs "
         f"{'with' if s['device']['app_dedup'] else 'without'} --app-dedup, so it executes every delivery it receives (one plant effect per "
         f"delivery). The offline fault: {s['offline_fault']}. Phi1 on the platform-to-device hop therefore rests on ThingsBoard's delivery "
         "behaviour with these settings (no platform retry, one delivery per RPC), not on a D_h of the owner: the same situation as the plain "
         "firmware at QoS 1.", "",
         "Deliveries per identity at the device are counted from the plant (the device has no per-delivery log; each delivery is one effect). "
         "A duplicate with one RPC per identity in the gateway log is a second delivery of the same RPC by the platform or its transport; with "
         "several RPCs it is a second RPC (a resend of the identity by the client side).", "",
         f"Duplicate effects in all ThingsBoard cells read: {res['duplicates_total']} (of them one RPC delivered twice: {res['duplicates_one_rpc_total']}).", ""]
    L += md_table(["run", "contract/policy", "cells", "deliveries", "identities with effect", "duplicate effects", "one RPC delivered twice"],
                  [[g["run"], g["group"], g["cells"], g["deliveries"], g["identities_with_effect"], g["duplicate_effects"], g.get("duplicates_one_rpc", NA)]
                   for g in res["groups"]]) if res["groups"] else ["No ThingsBoard cell with a plant log in the runs read."]
    if res["not_available"]:
        L += ["", f"Cells without a plant log ({NA}): " + "; ".join(res["not_available"][:20])]
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------ 9. per-request service time (M10)
def pctl(v: list, q: float):
    """Nearest-rank percentile, as evaluation/overhead.py's pct (index round(q/100 (n-1)) of the sorted values)."""
    if not v:
        return None
    v = sorted(v)
    return round(v[min(len(v) - 1, max(0, int(round(q / 100.0 * (len(v) - 1)))))], 3)


def service_cell(celldir: Path):
    """Per-request components (ms) of the admitted first-attempt requests of one cell: [(lock, eval, commit)], or a reason
    string when the cell lacks the raw data."""
    cl = clients(celldir)
    trs = authority_traces(celldir)
    if cl is None:
        return "no clients.jsonl"
    if not trs:
        return "no authority traces"
    recv, prep, dec = {}, {}, {}
    for tf in trs:
        rows = read_jsonl(tf, ("ADMISSION_RECEIVED", "ADMISSION_PREPARE", "DECISION_RECORDED")) or []
        for e in rows:
            cid, ev = e.get("command_id"), e.get("event")
            if not cid:
                continue
            if ev == "ADMISSION_RECEIVED" and cid not in recv:
                recv[cid] = (ts(e.get("wall_time")), int(e.get("wait_ns") or 0) / 1e6)
            elif ev == "ADMISSION_PREPARE" and cid not in prep:
                prep[cid] = (ts(e.get("wall_time")), e.get("monotonic_ns"))
            elif ev == "DECISION_RECORDED" and cid in prep and cid not in dec and e.get("decision") == "ADMITTED":
                dec[cid] = e.get("monotonic_ns")
    if not recv:
        return "no ADMISSION_RECEIVED events (authority before release 069)"
    out = []
    for c in cl:
        if c.get("decision") != "ADMITTED" or int(c.get("attempts") or 0) != 1:
            continue
        cid = c.get("cid")
        if cid not in recv or cid not in prep or cid not in dec:
            continue
        (tr, lock_ms), (tp, mp), md = recv[cid], prep[cid], dec[cid]
        if tr is None or tp is None or mp is None or md is None:
            continue
        out.append((lock_ms, (tp - tr) * 1000.0, (md - mp) / 1e6))
    return out


def service(runs: list, report: Path = None) -> dict:
    """Per (campaign, contract, policy) of the overhead runs: the distribution of the per-request sum lock + eval + commit."""
    ov = {}
    for r in (read_json(report / "overhead" / "overhead.json", []) if report else []) or []:
        if str(r.get("campaign", "")).startswith("rq4"):
            ov[(str(r["campaign"]).split("@")[0], r.get("contract"), r.get("policy"))] = r
    groups, missing = {}, []
    for run in runs:
        if not run.parent.name.startswith("rq4"):
            continue
        for r in campaign_rows(run):
            if not str(r.get("cell_status", "")).startswith("VALID"):
                continue
            k = (run_label(run), r["contract"], r["policy"])
            g = groups.setdefault(k, {"run": k[0], "campaign": run.parent.name, "contract": k[1], "policy": k[2], "cells": 0,
                                      "cells_with_data": 0, "parts": []})
            g["cells"] += 1
            got = service_cell(cell_dir(run, r))
            if isinstance(got, str):
                missing.append(f"{k[0]} ep{r['episode']} {k[1]}/{k[2]}: {got}")
                continue
            g["cells_with_data"] += 1
            g["parts"] += got
    rows = []
    for g in sorted(groups.values(), key=lambda g: (g["run"], g["contract"], g["policy"])):
        parts = g.pop("parts")
        tot = [a + b + c for a, b, c in parts]
        g["n"] = len(tot)
        if tot:
            med = [pctl([x[i] for x in parts], 50) for i in range(3)]
            g.update({"sum_p50_ms": pctl(tot, 50), "sum_p95_ms": pctl(tot, 95), "sum_p99_ms": pctl(tot, 99), "sum_max_ms": round(max(tot), 3),
                      "lock_p50_ms": med[0], "eval_p50_ms": med[1], "commit_p50_ms": med[2], "sum_of_medians_ms": round(sum(med), 3)})
        else:
            g.update({k: NA for k in ("sum_p50_ms", "sum_p95_ms", "sum_p99_ms", "sum_max_ms", "lock_p50_ms", "eval_p50_ms", "commit_p50_ms",
                                     "sum_of_medians_ms")})
        o = ov.get((g["campaign"], g["contract"], g["policy"]))
        parts_rep = [o.get(f"{x}_ms_p50") for x in ("lock", "eval", "decide")] if o else []
        g["sum_of_medians_report_ms"] = round(sum(parts_rep), 3) if parts_rep and all(isinstance(x, (int, float)) for x in parts_rep) else NA
        rows.append(g)
    have = [g for g in rows if g["n"]]
    return {"rows": rows, "available": bool(have), "missing": missing[:200], "missing_cells": len(missing),
            "reason": "" if have else ("no overhead run (rq4-load-*, rq4-scale-*) among the runs read" if not rows else
                                       "the overhead runs read have no per-request raw data: " + "; ".join(sorted({m.split(': ', 1)[1] for m in missing})))}


def service_md(res: dict) -> str:
    L = ["# The authority's own work per request: the distribution of the per-request sum (review M10)", "",
         "Per admitted first-attempt request of the overhead runs, the sum lock + evaluation + commit in the authority (the components of "
         "evaluation/overhead.py, from the authority traces: lock = the wait recorded with ADMISSION_RECEIVED, evaluation = request read -> "
         "ADMISSION_PREPARE, commit = ADMISSION_PREPARE -> DECISION_RECORDED), its nearest-rank p50 / p95 / p99 and maximum, next to the sum "
         "of the component medians over the same requests and the one the paper quotes (report/overhead/overhead.json; its commit median "
         "is over every decision, not only the admitted first attempts).", ""]
    if not res["available"]:
        return "\n".join(L + [f"{NA}: {res['reason']}."]) + "\n"
    L += md_table(["run", "contract", "policy", "cells (with data)", "requests", "sum p50 (ms)", "sum p95 (ms)", "sum p99 (ms)", "sum max (ms)",
                   "lock / eval / commit p50 (ms)", "sum of medians (ms)", "sum of medians, paper (ms)"],
                  [[g["run"], g["contract"], g["policy"], f"{g['cells']} ({g['cells_with_data']})", g["n"], fmt(g["sum_p50_ms"], 2),
                    fmt(g["sum_p95_ms"], 2), fmt(g["sum_p99_ms"], 2), fmt(g["sum_max_ms"], 2),
                    f"{fmt(g['lock_p50_ms'], 3)} / {fmt(g['eval_p50_ms'], 2)} / {fmt(g['commit_p50_ms'], 2)}", fmt(g["sum_of_medians_ms"], 2),
                    fmt(g["sum_of_medians_report_ms"], 2)] for g in res["rows"]])
    if res["missing_cells"]:
        L += ["", f"{res['missing_cells']} cells without the raw data ({NA}), e.g. " + "; ".join(res["missing"][:5])]
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------ main
def summary(res: dict) -> dict:
    """The numbers the paper may quote (paper_tables.py: macros)."""
    t = res["eoo"]["totals"]
    s = {"eoo_cells": t["cells"], "eoo_cells_with_logs": t["cells_with_logs"], "eoo_unresolved_at_end": t["unresolved_at_end"],
         "eoo_in_flight_at_end": t["in_flight_at_end"], "eoo_in_flight_without_effect": t["in_flight_without_effect"],
         "eoo_effects_during_drain": t["effects_during_drain"], "eoo_duplicates_during_drain": t["duplicate_effects_during_drain"],
         "eoo_effects_after_truth_read": t["effects_after_truth_read"], "eoo_inconsistent_cells": t["inconsistent_cells"],
         "eoo_last_effect_after_drain_s": t["last_effect_rel_drain_s_max"],
         "tb_late_effects": len(res["tb_late"]["late_effects"]), "tb_late_within_expiry": res["tb_late"]["within_expiry"],
         "tb_late_after_expiry": res["tb_late"]["after_expiry"],
         "rr_answered_releases": len(res["readrelease"]["answered_releases"]),
         "r5_aborted_fences": res["r5cf"]["aborted_total"], "r5_resolvable_identities": res["r5cf"]["resolvable_total"],
         "rubric_measurements": len(res["rubric"]["rows"]), "rubric_label_changes": len(res["rubric"]["changed"]),
         "rubric_contract_changes": len(res["rubric"]["contracts_changed"]),
         "latency_rows_small_n": res["latency_n"].get("flagged"), "latency_fence_min_n": res["latency_n"].get("fence_min_n"),
         "latency_stop_min_n": res["latency_n"].get("stop_min_n"),
         "tb_duplicate_effects": res["tb_delivery"]["duplicates_total"], "tb_duplicates_one_rpc": res["tb_delivery"]["duplicates_one_rpc_total"],
         "tb_rpc_retries": res["tb_delivery"]["settings"]["rpc"]["retries"], "tb_device_qos": res["tb_delivery"]["settings"]["device"]["subscribe_qos"]}
    sv = [g for g in res["service"]["rows"] if g["n"]]
    s["service_available"] = int(bool(sv))
    if sv:
        s.update({"service_requests": sum(g["n"] for g in sv), "service_sum_p50_max_ms": max(g["sum_p50_ms"] for g in sv),
                  "service_sum_p95_max_ms": max(g["sum_p95_ms"] for g in sv), "service_sum_p99_max_ms": max(g["sum_p99_ms"] for g in sv),
                  "service_sum_of_medians_max_ms": max(g["sum_of_medians_ms"] for g in sv)})
    return s


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", default="", help="the report directory (latency/latency.json is read from it)")
    ap.add_argument("--campaign", action="append", default=[], help="a campaign run directory (repeatable)")
    ap.add_argument("--measure", action="append", default=[], help="a review run's measure/ directory (repeatable)")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    runs = [Path(c) for c in a.campaign if Path(c).is_dir()]
    res = {"eoo": eoo(runs), "tb_late": tb_late(runs), "readrelease": readrelease(runs), "admission": admission(runs),
           "latency_n": latency_n(Path(a.report)) if a.report else {"rows": [], "available": False}, "r5cf": r5cf(runs),
           "rubric": rubric([Path(m) for m in a.measure]), "tb_delivery": tb_delivery(runs),
           "service": service(runs, Path(a.report) if a.report else None)}
    mds = {"eoo": eoo_md, "tb_late": tb_late_md, "readrelease": readrelease_md, "admission": admission_md, "latency_n": latency_n_md,
           "r5cf": r5cf_md, "rubric": rubric_md, "tb_delivery": tb_delivery_md, "service": service_md}
    index = ["# Level-2 analyses of release 084 (answers to the external review; existing traces, no new cells)", "",
             f"Campaign runs read: {len(runs)}; measurement roots: {len(a.measure)}.", ""]
    for name, f in mds.items():
        d = out / name
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}.json").write_text(json.dumps(res[name], indent=2, default=str) + "\n")
        (d / f"{name.upper()}.md").write_text(f(res[name]))
        index.append(f"- [{f(res[name]).splitlines()[0].lstrip('# ')}]({name}/{name.upper()}.md)")
    s = summary(res)
    (out / "l2_084.json").write_text(json.dumps(s, indent=2, default=str) + "\n")
    (out / "L2_084.md").write_text("\n".join(index) + "\n\n" + "".join(f"- {k}: {v}\n" for k, v in s.items()))
    print(f"level-2 analyses (084): {len(runs)} campaign runs -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
