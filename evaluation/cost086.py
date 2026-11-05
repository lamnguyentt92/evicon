#!/usr/bin/env python3
"""Physical-unit cost table (release 086, PREREG_extra_086.md estimand E-COST; reviewer comment M3(1)). A report
analysis: no new cells, no hypothesis.

For the matched runs of release 085 (PMz6, PMzi: the cutoff firmware, EVICON against PERIODIC_FULL at W = 6 and
unbounded; PMm6, PMmi: MinIO's conditional PUT) and the runs of release 086 (SRz6, SRzi: the plain firmware at QoS 0
against the firmware fence; SRm6, SRmi: MinIO's plain against conditional PUT; SHq, SHd: EVICON against
SHADOW_RECONCILE on the emulated owner), per cell (VALID cells only), arm and offered request:

  owner calls        logical owner calls (queries, stops, fences; the row of the cell), and in the SH runs the shadow
                     loop's reads of the reported state (shadow reads: one SHADOW_RECONCILE_RESULT trace event each; the
                     row's queries do not count them). Exact
  republishes        SH runs: the shadow loop's re-publishes of the same identity (action=REPUBLISH). Exact
  gateway requests   MQTT PUBLISH messages the MQTT owner gateway sent to the firmware: command forwards, queries and
                     stops it did not answer from its receipt cache, fence messages, and the per-identity queries of
                     each answered fence (the gateways' operation logs raw/C/gateway<d>.jsonl; every publish is logged
                     once, also when no reply came). Exact, 085 and 086
  broker packets     every MQTT packet the broker received and sent during the cell (publishes, acknowledgements,
                     connects, subscriptions, keepalives) from the broker's $SYS counters (raw/C/broker_sys.json,
                     written by the 086 runner campaign_zephyr_ext086.py). 086 firmware runs only: the 085 runs kept no
                     broker counter and the frozen gateway and firmware log operations, not packets (not recoverable:
                     replies arriving after a timeout, acknowledgements and the broker's redeliveries after a reconnect
                     appear in no 085 trace)
  flash writes       durable writes of the firmware (settings saves): the cutoff (evicon/cutoff), the claim mark
                     (evicon/hw) and tombstones (evicon/tomb). 086 firmware runs: counted from the flash files
                     (raw/E/flash_writes.json, evaluation/nvs_flash.py). 085 and 086: derived from the traces with the
                     firmware's write rule (firmware/zephyr-cutoff/src/evc_core.c, unchanged): a claim-mark write for
                     every executed command (the plant's effect log, in its order per device) whose sequence exceeds
                     the current mark, which then becomes the sequence + 64; a cutoff write for every fence the firmware
                     answered whose cutoff exceeds every earlier one; no tombstone in cutoff or plain mode, and no flash
                     write at all in plain mode. A cell with a fence that got no answer (gateway result TIMEOUT) has no
                     derived cutoff count (the firmware may or may not have installed it later: not recoverable); the
                     086 counter checks the derivation where both exist
  paused time        domain paused time (seconds of fence pause summed over the cell's domains), with the end events
                     of release 085 (evaluation/overhead.py: anchor, abort, per-identity commit), and per fence

Per run and arm: the means per offered request; per run: the paired difference between the arms (b - a, per episode,
the arms' cells of the same episode), mean, Student t 95 % interval and a paired bootstrap percentile interval
(10,000 resamples, seed 86). Break-even, PM runs: r* = -mean(dqueries)/mean(dfences or dstops) on logical calls (as
pm085.py), next to the measured cost of one query and of one fence (or stop) in each unit (unit total / calls of that
kind, EVICON and PERIODIC_FULL cells together) and their ratio: EVICON has the lower cost in a unit iff that ratio
exceeds r* (when EVICON makes more queries and fewer fences or stops), the paired difference in that unit says the
same directly.

    python3 evaluation/cost086.py --run PMz6=<dir> ... --out <dir>
Writes COST086.md and cost086.json; runs not given are "not available".
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import overhead as OV  # noqa: E402  (the fence pause with the end events of release 085)
from review_l2_085 import t_q975  # noqa: E402

BOOT, SEED, HW_BLOCK = 10000, 86, 64
# run key -> (label, arm a (contract, policy), arm b, the other call kind of the break-even or None)
RUNS = {
    "PMz6": ("085 firmware W = 6: EVICON vs PERIODIC_FULL", ("ZEPHYR_CUTOFF", "EVICON"), ("ZEPHYR_CUTOFF", "PERIODIC_FULL"), "fences"),
    "PMzi": ("085 firmware W unbounded: EVICON vs PERIODIC_FULL", ("ZEPHYR_CUTOFF", "EVICON"), ("ZEPHYR_CUTOFF", "PERIODIC_FULL"), "fences"),
    "PMm6": ("085 MinIO W = 6: EVICON vs PERIODIC_FULL", ("MINIO_COND", "EVICON"), ("MINIO_COND", "PERIODIC_FULL"), "stops"),
    "PMmi": ("085 MinIO W unbounded: EVICON vs PERIODIC_FULL", ("MINIO_COND", "EVICON"), ("MINIO_COND", "PERIODIC_FULL"), "stops"),
    "SRz6": ("086 firmware W = 6: plain at QoS 0 vs {F}", ("ZEPHYR_PLAIN_DIRECT_QOS0", "EVICON"), ("ZEPHYR_F_DIRECT", "EVICON"), None),
    "SRzi": ("086 firmware W unbounded: plain at QoS 0 vs {F}", ("ZEPHYR_PLAIN_DIRECT_QOS0", "EVICON"), ("ZEPHYR_F_DIRECT", "EVICON"), None),
    "SRm6": ("086 MinIO W = 6: plain PUT vs conditional PUT", ("MINIO_PLAIN", "EVICON"), ("MINIO_COND", "EVICON"), None),
    "SRmi": ("086 MinIO W unbounded: plain PUT vs conditional PUT", ("MINIO_PLAIN", "EVICON"), ("MINIO_COND", "EVICON"), None),
    "SHq": ("086 emulated owner K2, queue: EVICON vs SHADOW_RECONCILE", ("K2", "EVICON"), ("K2", "SHADOW_RECONCILE"), None),
    "SHd": ("086 emulated owner K2, direct: EVICON vs SHADOW_RECONCILE", ("K2", "EVICON"), ("K2", "SHADOW_RECONCILE"), None),
}
UNITS = ("owner_calls", "shadow_reads", "republishes", "gateway_requests", "broker_packets", "broker_publishes", "flash_writes_counted",
         "flash_writes_derived", "paused_s")
ANSWERED = ("OK", "QUERY_TIMEOUT", "NOT_INSTALLED")


def _jsonl(p: Path):
    out = []
    try:
        with open(p, errors="replace") as fh:
            for line in fh:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def _json(p: Path):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def shadow_run(run: Path) -> bool:
    """A run with SHADOW_RECONCILE among its policies (SHq, SHd)."""
    return "SHADOW_RECONCILE" in ((_json(run / "config.json") or {}).get("policies") or [])


def shadow_counts(cell: Path):
    """The documented shadow loop's owner reads (one SHADOW_RECONCILE_RESULT trace event per read of a command's
    reported state) and its re-publishes (those with action=REPUBLISH), from the authorities' traces. The row's
    queries do not include these reads (campaign.py counts EVICON_QUERY_RESULT and PROBE_ATTEMPT)."""
    reads = rep = 0
    for tr in sorted((cell / "raw" / "A").glob("authority*.jsonl")):
        for e in _jsonl(tr):
            if e.get("event") == "SHADOW_RECONCILE_RESULT":
                reads += 1
                rep += str(e.get("reason", "")).startswith("action=REPUBLISH")
    return reads, rep


def fw_mode(run: Path, contract: str) -> str:
    cfg = _json(run / "config.json") or {}
    spec = ((cfg.get("real_owner") or {}).get("contracts") or {}).get(contract) or {}
    if (cfg.get("real_owner") or {}).get("system") != "zephyr":
        return ""
    return str(spec.get("mode", "")).upper()


def gateway_ops(cell: Path):
    ops, fwd = [], {}
    for g in sorted((cell / "raw" / "C").glob("gateway*.jsonl")):
        if g.name.endswith(".state.jsonl"):
            for e in _jsonl(g):
                if e.get("k") == "fwd":
                    fwd[e.get("cid")] = int(e.get("seq") or 0)
        else:
            ops.append(_jsonl(g))
    return ops, fwd


def gateway_requests(ops) -> int:
    n = 0
    for log in ops:
        for e in log:
            op = e.get("op")
            if op == "effect":
                n += 1
            elif op in ("query", "stop") and not e.get("cached"):
                n += 1
            elif op == "barrier":
                n += 1 + (int(e.get("queried") or 0) if e.get("result") in ("OK", "QUERY_TIMEOUT") else 0)
    return n


def derived_flash(cell: Path, mode: str, ops, fwd) -> dict:
    """The firmware's writes implied by the traces (evc_core.c's rule), per kind; cutoff None when not recoverable."""
    if mode == "PLAIN":
        return {"hw": 0, "cutoff": 0, "tomb": 0, "total": 0, "note": "plain mode: no durable write"}
    hw_writes, marks = 0, {}
    missing = 0
    for e in _jsonl(cell / "raw" / "E" / "plant.jsonl"):
        cid, dev = e.get("cmd"), e.get("device")
        if cid not in fwd:
            missing += 1
            continue
        seq = fwd[cid]
        if seq > marks.get(dev, 0):
            hw_writes += 1
            marks[dev] = seq + HW_BLOCK
    # cutoff writes: one per distinct cutoff the firmware installs (evc_core_fence writes only a larger cutoff). The
    # authority's full-frontier fences request non-decreasing cutoffs, one at a time per domain, and the gateway sends
    # them in order on one MQTT session, so the firmware installs a distinct value exactly once. An answered fence (OK,
    # QUERY_TIMEOUT: the firmware replied with its cutoff >= the request) installed its value or found it installed;
    # a fence without an answer (TIMEOUT) may have been installed later or never: lower bound = the distinct values of
    # the answered fences, upper bound = the distinct values of every fence sent; exact when they agree.
    lo_n = hi_n = 0
    monotone = True
    unknown = 0
    for log in ops:
        lo, hi, last = set(), set(), 0
        for e in log:
            if e.get("op") != "barrier":
                continue
            c = int(e.get("cutoff") or 0)
            if c < last:
                monotone = False
            last = max(last, c)
            if e.get("result") in ("OK", "QUERY_TIMEOUT"):
                lo.add(c)
                hi.add(c)
            elif e.get("result") == "TIMEOUT":
                hi.add(c)
                unknown += 1
        lo_n += len(lo)
        hi_n += len(hi)
    exact = monotone and lo_n == hi_n
    out = {"hw": hw_writes, "cutoff": lo_n if exact else None, "cutoff_bounds": [lo_n, hi_n] if monotone else None,
           "tomb": 0 if mode == "CUTOFF" else None, "effects_without_sequence": missing, "fences_unanswered": unknown}
    out["total"] = None if (out["cutoff"] is None or out["tomb"] is None or missing) else out["hw"] + out["cutoff"] + out["tomb"]
    if not monotone:
        out["note"] = "fence cutoffs not in increasing order: the cutoff writes are not recoverable from the traces"
    elif not exact:
        out["note"] = (f"{unknown} fence(s) without an answer: the cutoff writes lie in [{lo_n}, {hi_n}] (not recoverable exactly "
                       f"from the traces)")
    return out


def counted_flash(cell: Path):
    d = _json(cell / "raw" / "E" / "flash_writes.json")
    if not d or d.get("error") or not d.get("files"):
        return None, (d or {}).get("error", "no counter (release 085 runs have none)") if d else "no counter (release 085 runs have none)"
    by = {"hw": 0, "cutoff": 0, "tomb": 0}
    complete = True
    for f in d["files"]:
        if f.get("error"):
            return None, f["error"]
        complete &= bool(f.get("complete"))
        for k, v in (f.get("value_writes") or {}).items():
            by[k.split("/", 1)[-1]] = by.get(k.split("/", 1)[-1], 0) + int(v)
    by["total"] = by["hw"] + by["cutoff"] + by["tomb"]
    by["complete"] = complete
    return by, "" if complete else "a flash file was garbage-collected: the count is a lower bound"


def broker(cell: Path):
    d = _json(cell / "raw" / "C" / "broker_sys.json")
    if not d or not d.get("cell"):
        return None
    c = d["cell"]
    return {"packets": c["$SYS/broker/messages/received"] + c["$SYS/broker/messages/sent"],
            "publishes": c["$SYS/broker/publish/messages/received"] + c["$SYS/broker/publish/messages/sent"],
            "received": c["$SYS/broker/messages/received"], "sent": c["$SYS/broker/messages/sent"]}


def cell_costs(run: Path, contract: str, policy: str) -> dict:
    """{episode: {unit: value per cell}, plus offered and the call counts}."""
    out = {}
    mode = fw_mode(run, contract)
    for cell in sorted(run.glob(f"cells/ep*/{contract}/{policy}")):
        row = _json(cell / "row.json")
        if not row or not str(row.get("cell_status", "")).startswith("VALID"):
            continue
        ep = int(cell.parent.parent.name[2:])
        m = OV.cell_metrics(cell)
        x = {"offered": int(row.get("offered") or 0), "queries": int(row.get("queries") or 0), "stops": int(row.get("stops") or 0),
             "fences": int(row.get("fences") or 0), "paused_s": m["paused_ms"] / 1000.0, "fence_pauses": len(m["fence_pause_ms"])}
        x["owner_calls"] = x["queries"] + x["stops"] + x["fences"]
        if shadow_run(run):  # the shadow loop's reads are owner calls too; its re-publishes are resends of the same identity
            x["shadow_reads"], x["republishes"] = shadow_counts(cell)
            x["owner_calls"] += x["shadow_reads"]
        if mode:
            ops, fwd = gateway_ops(cell)
            x["gateway_requests"] = gateway_requests(ops)
            der = derived_flash(cell, mode, ops, fwd)
            x["flash_derived"] = der
            x["flash_writes_derived"] = der["total"]
            cnt, why = counted_flash(cell)
            x["flash_counted"] = cnt or {"why": why}
            x["flash_writes_counted"] = cnt["total"] if cnt else None
            b = broker(cell)
            x["broker_packets"] = b["packets"] if b else None
            x["broker_publishes"] = b["publishes"] if b else None
        out[ep] = x
    return out


def per_offered(x: dict, unit: str):
    v = x.get(unit)
    return None if v is None or not x["offered"] else v / x["offered"]


def interval(d: list, key: str) -> dict:
    d = [v for v in d if v is not None]
    if len(d) < 2:
        return {"n": len(d), "mean": statistics.mean(d) if d else None}
    m, sd, n = statistics.mean(d), statistics.stdev(d), len(d)
    h = t_q975(n - 1) * sd / math.sqrt(n)
    rng = random.Random(f"{SEED}/{key}")
    boots = sorted(statistics.mean(rng.choice(d) for _ in d) for _ in range(BOOT))
    return {"n": n, "mean": m, "t95": [m - h, m + h], "boot95": [boots[int(0.025 * (BOOT - 1))], boots[int(round(0.975 * (BOOT - 1)))]]}


def arm_summary(eps: dict) -> dict:
    s = {"cells": len(eps), "offered": sum(x["offered"] for x in eps.values())}
    for u in UNITS:
        vals = [per_offered(x, u) for x in eps.values()]
        s[u] = statistics.mean([v for v in vals if v is not None]) if any(v is not None for v in vals) else None
        s[u + "_cells"] = sum(1 for v in vals if v is not None)
    tot_f, tot_p = sum(x["fences"] for x in eps.values()), sum(x["paused_s"] for x in eps.values())
    s["paused_s_per_fence"] = tot_p / tot_f if tot_f else None
    b = [x.get("flash_derived") for x in eps.values() if x.get("flash_derived")]
    if b and all(d.get("cutoff_bounds") or d.get("cutoff") is not None for d in b) and all(d.get("hw") is not None for d in b):
        lo = [(d["hw"] + (d["cutoff"] if d.get("cutoff") is not None else d["cutoff_bounds"][0])) / max(1, x["offered"])
              for d, x in zip(b, [x for x in eps.values() if x.get("flash_derived")])]
        hi = [(d["hw"] + (d["cutoff"] if d.get("cutoff") is not None else d["cutoff_bounds"][1])) / max(1, x["offered"])
              for d, x in zip(b, [x for x in eps.values() if x.get("flash_derived")])]
        s["flash_writes_derived_bounds"] = [statistics.mean(lo), statistics.mean(hi)]
        s["cells_flash_exact"] = sum(1 for d in b if d.get("total") is not None)
    agree = [(x["flash_writes_counted"], x["flash_writes_derived"]) for x in eps.values()
             if x.get("flash_writes_counted") is not None and x.get("flash_writes_derived") is not None]
    s["flash_check"] = {"cells_both": len(agree), "cells_equal": sum(1 for a, b in agree if a == b)} if agree else None
    return s


def unit_costs(run: Path, contract: str, policies: tuple, other: str) -> dict:
    """Per call kind in each unit, over the cells of both arms of a PM run."""
    q_pub = x_pub = q_calls = x_calls = 0
    flash_x = flash_lo = flash_hi = 0
    paused = 0.0
    have_flash = True
    mode = fw_mode(run, contract)
    for pol in policies:
        for cell in sorted(run.glob(f"cells/ep*/{contract}/{pol}")):
            row = _json(cell / "row.json")
            if not row or not str(row.get("cell_status", "")).startswith("VALID"):
                continue
            q_calls += int(row.get("queries") or 0)
            x_calls += int(row.get(other) or 0)
            paused += OV.cell_metrics(cell)["paused_ms"] / 1000.0
            if mode:
                ops, fwd = gateway_ops(cell)
                for log in ops:
                    for e in log:
                        if e.get("op") == "query" and not e.get("cached"):
                            q_pub += 1
                        elif e.get("op") == "barrier":
                            x_pub += 1 + (int(e.get("queried") or 0) if e.get("result") in ("OK", "QUERY_TIMEOUT") else 0)
                        elif e.get("op") == "stop" and not e.get("cached"):
                            x_pub += 1
                der = derived_flash(cell, mode, ops, fwd)
                if der.get("cutoff") is None:
                    have_flash = False
                    if der.get("cutoff_bounds"):
                        flash_lo += der["cutoff_bounds"][0]
                        flash_hi += der["cutoff_bounds"][1]
                else:
                    flash_x += der["cutoff"]
                    flash_lo += der["cutoff"]
                    flash_hi += der["cutoff"]
    out = {"queries": q_calls, other: x_calls}
    if mode:
        out["gateway_requests"] = {"per_query": q_pub / q_calls if q_calls else None, f"per_{other[:-1]}": x_pub / x_calls if x_calls else None}
        out["flash_writes"] = {"per_query": 0.0, f"per_{other[:-1]}": (flash_x / x_calls if x_calls and have_flash else None),
                               f"per_{other[:-1]}_bounds": ([flash_lo / x_calls, flash_hi / x_calls] if x_calls else None),
                               "note": "a query writes nothing; the claim-mark writes follow the commands, not the calls"}
    out["paused_s"] = {"per_query": 0.0, f"per_{other[:-1]}": paused / x_calls if x_calls else None}
    return out


def break_even(a: dict, b: dict, other: str) -> dict:
    eps = sorted(set(a) & set(b))
    dq = [a[e]["queries"] - b[e]["queries"] for e in eps]
    dx = [a[e][other] - b[e][other] for e in eps]
    if len(eps) < 1 or not dx or statistics.mean(dx) == 0:
        return {"n": len(eps), "ratio": None}
    mq, mx = statistics.mean(dq), statistics.mean(dx)
    r = -mq / mx if (mq > 0) != (mx > 0) and mq != 0 else None
    return {"n": len(eps), "dq_mean": mq, "dx_mean": mx, "ratio": r}


def analyse(runs: dict) -> dict:
    res = {"runs": {}, "units": list(UNITS)}
    for key, (label, a, b, other) in RUNS.items():
        if key not in runs:
            res["runs"][key] = {"label": label, "available": False}
            continue
        run = runs[key]
        A, B = cell_costs(run, *a), cell_costs(run, *b)
        r = {"label": label, "available": True, "run": f"{run.parent.name}/{run.name}", "arm_a": f"{a[0]}/{a[1]}", "arm_b": f"{b[0]}/{b[1]}",
             "a": arm_summary(A), "b": arm_summary(B), "paired": {}}
        eps = sorted(set(A) & set(B))
        for u in UNITS:
            d = [None if per_offered(A[e], u) is None or per_offered(B[e], u) is None else per_offered(B[e], u) - per_offered(A[e], u) for e in eps]
            if any(v is not None for v in d):
                r["paired"][u] = interval(d, f"{key}/{u}")
        if other:
            r["break_even"] = break_even(A, B, other)
            uc = unit_costs(run, a[0], (a[1], b[1]), other)
            r["unit_costs"] = uc
            rs = r["break_even"].get("ratio")
            r["break_even_units"] = {}
            for u in ("gateway_requests", "flash_writes", "paused_s"):
                c = uc.get(u) or {}
                cq, cx = c.get("per_query"), c.get(f"per_{other[:-1]}")
                ratio = (cx / cq) if (cq and cx is not None) else (math.inf if (cq == 0 and cx) else None)
                r["break_even_units"][u] = {"per_query": cq, f"per_{other[:-1]}": cx, "ratio": ratio,
                                            "evicon_cheaper": (None if ratio is None or rs is None else
                                                               (ratio > rs) if r["break_even"]["dq_mean"] > 0 else (ratio < rs))}
        res["runs"][key] = r
    return res


def _f(x, nd=3):
    if x is None:
        return "-"
    if isinstance(x, float) and math.isinf(x):
        return "inf"
    return f"{x:.{nd}f}"


def _ci(v, nd=3):
    return f"[{v[0]:+.{nd}f}, {v[1]:+.{nd}f}]" if v else "-"


def markdown(res: dict) -> str:
    L = ["# Costs in physical units per offered request (PREREG_extra_086.md, E-COST)", "",
         "Per run and arm, the mean over VALID cells of each quantity divided by the cell's offered requests; the paired "
         "difference b - a per episode with a Student t and a paired bootstrap 95 % interval (10,000, seed 86). Owner calls: "
         "logical calls (in the SH runs including the shadow loop's reads of the reported state, also shown as shadow_reads; republishes: its re-publishes of the same identity). Gateway requests: MQTT PUBLISH messages from the gateway to the firmware (the operation logs; "
         "exact). Broker packets / publishes: every MQTT packet through the broker in the cell ($SYS counters; 086 firmware "
         "runs only, not recoverable for 085). Flash writes: counted from the flash files (086) and derived from the traces "
         "with the firmware's write rule (085 and 086; a cell with an unanswered fence has no derived count). Paused time: "
         "domain seconds of fence pause.", ""]
    for key, r in res["runs"].items():
        if not r.get("available"):
            L += [f"## {key}: {r['label']}", "", "not available (run not among the sources)", ""]
            continue
        L += [f"## {key}: {r['label']} (`{r['run']}`)", "", f"a = {r['arm_a']}, b = {r['arm_b']}", "",
              "| per offered request | a | b | b - a (n) | t 95 % | bootstrap 95 % |", "|---|---|---|---|---|---|"]
        for u in res["units"]:
            pa, pb, p = r["a"].get(u), r["b"].get(u), r["paired"].get(u)
            if pa is None and pb is None:
                continue
            L.append(f"| {u} | {_f(pa, 4)} | {_f(pb, 4)} | {_f((p or {}).get('mean'), 4)} ({(p or {}).get('n', 0)}) | {_ci((p or {}).get('t95'), 4)} | "
                     f"{_ci((p or {}).get('boot95'), 4)} |")
        L.append("")
        for arm in ("a", "b"):
            if r[arm].get("flash_writes_derived_bounds"):
                lo, hi = r[arm]["flash_writes_derived_bounds"]
                L.append(f"- arm {arm}: flash writes derived per offered request in [{lo:.4f}, {hi:.4f}] (exact in {r[arm]['cells_flash_exact']} "
                         f"of {r[arm]['cells']} cells; the rest have fences without an answer)")
            fc = r[arm].get("flash_check")
            if fc:
                L.append(f"- arm {arm}: flash writes counted = derived in {fc['cells_equal']} of {fc['cells_both']} cells with both")
            if r[arm].get("paused_s_per_fence") is not None:
                L.append(f"- arm {arm}: paused time per fence {r[arm]['paused_s_per_fence']:.3f} s")
        if r.get("break_even"):
            be = r["break_even"]
            L.append(f"- break-even on logical calls r* = {_f(be.get('ratio'))} (mean dqueries {_f(be.get('dq_mean'), 1)}, mean d{['stops', 'fences'][r['arm_a'].startswith('ZEPHYR')]} "
                     f"{_f(be.get('dx_mean'), 1)} per episode)")
            for u, v in r["break_even_units"].items():
                other = [k for k in v if k.startswith("per_") and k != "per_query"][0]
                bnd = (r["unit_costs"].get(u) or {}).get(f"{other}_bounds")
                if v[other] is None and bnd:
                    L.append(f"- {u}: one query costs 0, one {other[4:]} costs between {bnd[0]:.3f} and {bnd[1]:.3f} (bounds): "
                             + ("EVICON has the lower cost in this unit" if r["break_even"].get("dq_mean", 0) > 0 and bnd[0] > 0 else "not decidable"))
                    continue
                L.append(f"- {u}: one query costs {_f(v['per_query'])}, one {other[4:]} costs {_f(v[other])}; ratio {_f(v['ratio'])} against r*: "
                         + ("EVICON has the lower cost in this unit" if v["evicon_cheaper"] else
                            "PERIODIC_FULL has the lower cost in this unit" if v["evicon_cheaper"] is False else "not decidable"))
        L.append("")
    return "\n".join(L) + "\n"


def tex_rows(res: dict) -> list:
    """(run key, arm label, values per offered request) for the paper table."""
    rows = []
    for key, r in res["runs"].items():
        if not r.get("available"):
            continue
        for arm, name in (("a", r["arm_a"]), ("b", r["arm_b"])):
            rows.append((key, name, {u: r[arm].get(u) for u in UNITS}))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", action="append", default=[], help="KEY=run dir")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    runs = {}
    for x in a.run:
        k, _, v = x.partition("=")
        if v and (Path(v) / "cells").is_dir():
            runs[k] = Path(v)
    res = analyse(runs)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "cost086.json").write_text(json.dumps(res, indent=1, default=lambda o: None) + "\n")
    (out / "COST086.md").write_text(markdown(res))
    print(f"cost086: {sum(1 for r in res['runs'].values() if r.get('available'))} run(s) -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
