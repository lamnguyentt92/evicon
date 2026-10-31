#!/usr/bin/env python3
"""RQ4: runtime overhead of the EviCon authority, from campaign cells (release 068; 069, 070).

Inputs: campaign run directories (the RQ4 load campaigns rq4_load_r*, and optionally the
experiment A campaigns for the cost of evidence actions under faults) and the resource samples of
evaluation/rq4_sampler.py (VM-A: authorities; VM-E: devices and plant).

Per campaign, contract and policy (aggregated over the cells):

  offered_per_s         offered commands per second (all devices)
  admit_ms p50/p95/p99  client-observed admission latency, first attempt of admitted commands
                        (client on VM-C -> authority on VM-A -> decision; includes the network)
  decide_ms p50/p95     the authority's commit: ADMISSION_PREPARE -> DECISION_RECORDED (trace;
                        reservation and decision written to the journal)
  dispatch_ms p50/p95   authority -> device -> receipt committed (OUTBOX_DISPATCH_ATTEMPT ->
                        OUTBOX_RECEIPT_COMMITTED): the owner round trip EviCon adds nothing to
  admitted_after_deadline  (release 083, A2) admitted requests whose first admission came after arrival + deadline_s,
                        and their share of the admitted requests (admitted_after_deadline_share)
  terminal_ms p50/p95   admitted -> terminal outcome seen by the client; the client polls every
                        workload.retry_s (0.5 s in the RQ4 campaigns), so this is about one poll
                        interval: use dispatch_ms for the owner round trip
  release 069, where the admission latency goes (the authority trace's wall clock against the
  client's; VM-C and VM-A synchronised by chrony):
    admit = in + decide + out
    in_ms               client's scheduled arrival -> ADMISSION_PREPARE
    out_ms              DECISION_RECORDED -> the client sees the admission
  and, from authorities of release 069 on (trace event ADMISSION_RECEIVED with wait_ns),
    in = client + lock + eval
    client_ms           scheduled arrival -> the authority's handler starts (load generator: timer,
                        per-device submitter queue, HTTP client; and the network)
    lock_ms             handler start -> request read (the authority's lock, then the body)
    eval_ms             request read -> ADMISSION_PREPARE (domain state, policy and analyzer)
  Release 068 traces have no ADMISSION_RECEIVED: in_ms then mixes client, network and the
  authority's work before ADMISSION_PREPARE.
  cpu_pct, rss_mb       per authority process: mean CPU over its lifetime, maximum RSS
  events_per_cmd        trace events per offered command; db_bytes_per_cmd: SQLite journal size
  fences/queries/stops per offered command; fence_pause_ms p50/p95/max (BARRIER_PAUSE_COMMITTED ->
                        anchor or abort of the same token) and the fraction of cell time a domain
                        was paused

    python3 evaluation/overhead.py --campaign <run dir> [...] [--samples <jsonl> ...] --out <dir>

Writes overhead.json, overhead.csv, overhead.md.
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import glob
import json
import re
import statistics
import sys
from pathlib import Path


def pct(v, q):
    if not v:
        return None
    v = sorted(v)
    i = min(len(v) - 1, max(0, int(round(q / 100.0 * (len(v) - 1)))))
    return round(v[i], 3)


def load_jsonl(p: Path):
    out = []
    try:
        for line in p.read_text(errors="replace").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    except OSError:
        pass
    return out


def wall(s: str):
    """RFC 3339 with nanoseconds -> epoch seconds (None when absent)."""
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?Z", s or "")
    if not m:
        return None
    base = dt.datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc).timestamp()
    return base + (float("0." + m.group(2)) if m.group(2) else 0.0)


def traces(cell: Path):
    return sorted(cell.glob("raw/A/authority*.jsonl")) or sorted(cell.glob("raw/authority*.jsonl"))


def dbs(cell: Path):
    return sorted(cell.glob("raw/A/authority*.db")) or sorted(cell.glob("raw/authority*.db"))


def cell_metrics(cell: Path, deadline_s: float = None) -> dict:
    row = json.loads((cell / "row.json").read_text())
    m = {"offered": int(row.get("offered") or 0), "admit_ms": [], "terminal_ms": [], "decide_ms": [], "dispatch_ms": [],
         "admitted_requests": 0, "admitted_after_deadline": 0, "identities_admitted_after_deadline": 0,
         "in_ms": [], "out_ms": [], "client_ms": [], "lock_ms": [], "eval_ms": [],
         "events": 0, "fence_pause_ms": [], "paused_ms": 0.0, "cell_ms": 0.0, "db_bytes": 0,
         "fences": int(row.get("fences") or 0), "queries": int(row.get("queries") or 0), "stops": int(row.get("stops") or 0),
         "dispatch_attempts": int(row.get("dispatch_attempts") or 0)}
    wall_recv, wall_prep, wall_dec = {}, {}, {}
    for tf in traces(cell):
        for e in load_jsonl(tf):
            cid, ev = e.get("command_id"), e.get("event")
            if cid and ev == "ADMISSION_RECEIVED":
                wall_recv.setdefault(cid, (wall(e.get("wall_time", "")), int(e.get("wait_ns") or 0) / 1e9))
            elif cid and ev == "ADMISSION_PREPARE":
                wall_prep.setdefault(cid, wall(e.get("wall_time", "")))
            elif cid and ev == "DECISION_RECORDED" and e.get("decision") == "ADMITTED":
                wall_dec.setdefault(cid, wall(e.get("wall_time", "")))
    for c in load_jsonl(cell / "clients.jsonl"):
        adm = c.get("admitted") or {}
        if adm and deadline_s is not None:
            # release 083 (A2): a request whose (first) admission came after its deadline; the client submits until the
            # deadline, and an answer that arrives after it is recorded at its arrival
            m["admitted_requests"] += 1
            late = [t for t in adm.values() if t - c["arrival"] > deadline_s]
            m["identities_admitted_after_deadline"] += len(late)
            m["admitted_after_deadline"] += min(adm.values()) - c["arrival"] > deadline_s
        if c.get("decision") == "ADMITTED" and int(c.get("attempts") or 0) == 1:
            t_adm = (c.get("admitted") or {}).get(c["cid"])
            if t_adm:
                m["admit_ms"].append((t_adm - c["arrival"]) * 1000.0)
                t_term = (c.get("terminal_seen") or {}).get(c["cid"])
                if t_term:
                    m["terminal_ms"].append((t_term - t_adm) * 1000.0)
                tp, td = wall_prep.get(c["cid"]), wall_dec.get(c["cid"])
                if tp and td:
                    m["in_ms"].append((tp - c["arrival"]) * 1000.0)
                    m["out_ms"].append((t_adm - td) * 1000.0)
                    tr, wait_s = wall_recv.get(c["cid"], (None, 0.0))
                    if tr:
                        m["client_ms"].append((tr - wait_s - c["arrival"]) * 1000.0)
                        m["lock_ms"].append(wait_s * 1000.0)
                        m["eval_ms"].append((tp - tr) * 1000.0)
    t_first, t_last = None, None
    for tf in traces(cell):
        prep, disp, pause = {}, {}, {}
        for e in load_jsonl(tf):
            m["events"] += 1
            ns = e.get("monotonic_ns")
            if ns is None:
                continue
            t_first = ns if t_first is None else min(t_first, ns)
            t_last = ns if t_last is None else max(t_last, ns)
            ev, cid = e.get("event"), e.get("command_id")
            if ev == "ADMISSION_PREPARE" and cid:
                prep[cid] = ns
            elif ev == "DECISION_RECORDED" and cid in prep:
                m["decide_ms"].append((ns - prep.pop(cid)) / 1e6)
            elif ev == "OUTBOX_DISPATCH_ATTEMPT" and cid:
                disp[cid] = ns
            elif ev == "OUTBOX_RECEIPT_COMMITTED" and cid in disp:
                m["dispatch_ms"].append((ns - disp.pop(cid)) / 1e6)
            elif ev == "BARRIER_PAUSE_COMMITTED":
                pause.setdefault(str(e.get("reason", "")), ns)
            elif ev in ("BARRIER_ANCHOR_COMMITTED", "BARRIER_ABORTED"):
                tok = str(e.get("reason", "")).split(":", 1)[0]
                if tok in pause:
                    d = (ns - pause.pop(tok)) / 1e6
                    m["fence_pause_ms"].append(d)
                    m["paused_ms"] += d
        if t_last is not None:
            for tok, ns in pause.items():  # still paused when the cell ended
                m["paused_ms"] += (t_last - ns) / 1e6
    if t_first is not None:
        m["cell_ms"] = (t_last - t_first) / 1e6 * max(1, len(traces(cell)))  # domain-time: one domain per authority
    m["db_bytes"] = sum(p.stat().st_size for p in dbs(cell))
    m["duration_s"] = float(row.get("duration_s") or 0) or None
    return m


def resource_metrics(samples: list) -> dict:
    """cell key -> {"cpu_pct": [per process], "rss_mb": [per process max], "name"}."""
    by = collections.defaultdict(list)
    for s in samples:
        if "pid" in s and s.get("cell"):
            by[(s["cell"], s["name"], s["pid"])].append(s)
    out = collections.defaultdict(lambda: collections.defaultdict(lambda: {"cpu_pct": [], "rss_mb": []}))
    for (cell, name, pid), ss in by.items():
        ss.sort(key=lambda x: x["t"])
        if len(ss) < 2:
            continue
        dt = ss[-1]["t"] - ss[0]["t"]
        if dt <= 0:
            continue
        cpu = (ss[-1]["ticks"] - ss[0]["ticks"]) / ss[0]["hz"] / dt * 100.0
        out[cell][name]["cpu_pct"].append(cpu)
        out[cell][name]["rss_mb"].append(max(x["rss_kb"] for x in ss) / 1024.0)
    return out


def cell_key(cell: Path) -> str:
    """The suffix both a campaign cell directory and a sampled process path end with."""
    ep, contract, policy = cell.parts[-3], cell.parts[-2], cell.parts[-1]
    return f"{ep}-{contract}-{policy}"


def match_resources(cell: Path, run: Path, res: dict) -> dict:
    key, local = cell_key(cell), str(cell / "raw")
    for k, v in res.items():
        if k.rstrip("/") == local or (k.endswith("/" + key) and f"/{run.parent.name}/{run.name}/" in k + "/"):
            return v
    return {}


def summarize(campaigns: list, samples: list) -> list:
    res = resource_metrics(samples)
    groups = collections.defaultdict(lambda: collections.defaultdict(list))
    names = {}
    for run in campaigns:
        cfg = json.loads((run / "config.json").read_text()) if (run / "config.json").exists() else {}
        names[run] = cfg.get("name", run.parent.name)
    seen = collections.Counter(names.values())
    for run in campaigns:
        cfg = json.loads((run / "config.json").read_text()) if (run / "config.json").exists() else {}
        # release 070: two runs of one campaign (a replication) stay apart: name@<run stamp>
        label = names[run] if seen[names[run]] == 1 else f"{names[run]}@{run.name}"
        for cell in sorted(run.glob("cells/ep*/*/*")):
            if not (cell / "row.json").exists():
                continue
            m = cell_metrics(cell, float((cfg.get("workload") or {}).get("deadline_s", 60)))
            g = groups[(label, cell.parts[-2], cell.parts[-1])]
            g["cells"].append(1)
            g["duration_s"].append(float(cfg.get("duration_s", 0)) or 1.0)
            for k in ("admit_ms", "terminal_ms", "decide_ms", "dispatch_ms", "fence_pause_ms", "in_ms", "out_ms", "client_ms",
                      "lock_ms", "eval_ms"):
                g[k] += m[k]
            for k in ("offered", "events", "db_bytes", "fences", "queries", "stops", "dispatch_attempts", "paused_ms", "cell_ms",
                      "admitted_requests", "admitted_after_deadline", "identities_admitted_after_deadline"):
                g[k].append(m[k])
            r = match_resources(cell, run, res)
            for name, v in r.items():
                g[f"cpu_pct:{name}"] += v["cpu_pct"]
                g[f"rss_mb:{name}"] += v["rss_mb"]
    out = []
    for (camp, contract, policy), g in sorted(groups.items()):
        offered = sum(g["offered"])
        row = {"campaign": camp, "contract": contract, "policy": policy, "cells": len(g["cells"]),
               "offered_per_s": round(offered / sum(g["duration_s"]), 2) if g["duration_s"] else None}
        for k in ("admit_ms", "decide_ms", "dispatch_ms", "terminal_ms", "in_ms", "out_ms", "client_ms", "lock_ms", "eval_ms"):
            row[f"{k}_p50"], row[f"{k}_p95"] = pct(g[k], 50), pct(g[k], 95)
        row["admit_ms_p99"] = pct(g["admit_ms"], 99)
        row["admit_n"] = len(g["admit_ms"])
        for name in ("evicon-authority", "evicon-device", "evicon-plant"):
            cp, rs = g.get(f"cpu_pct:{name}", []), g.get(f"rss_mb:{name}", [])
            short = name.split("-")[1]
            row[f"{short}_cpu_pct_mean"] = round(statistics.mean(cp), 2) if cp else None
            row[f"{short}_rss_mb_max"] = round(max(rs), 1) if rs else None
        row["events_per_cmd"] = round(sum(g["events"]) / offered, 2) if offered else None
        row["db_bytes_per_cmd"] = round(sum(g["db_bytes"]) / offered, 1) if offered else None
        for k in ("fences", "queries", "stops", "dispatch_attempts"):
            row[f"{k}_per_cmd"] = sum(g[k]) / offered if offered else None  # 083: unrounded (paper_tables rounds once)
        row["fence_pause_ms_p50"], row["fence_pause_ms_p95"] = pct(g["fence_pause_ms"], 50), pct(g["fence_pause_ms"], 95)
        row["fence_pause_ms_max"] = round(max(g["fence_pause_ms"]), 1) if g["fence_pause_ms"] else None
        row["paused_fraction"] = sum(g["paused_ms"]) / sum(g["cell_ms"]) if sum(g["cell_ms"]) else None  # 083: unrounded
        # release 083 (A2): admissions after the request's deadline
        row["admitted_requests"] = sum(g["admitted_requests"])
        row["admitted_after_deadline"] = sum(g["admitted_after_deadline"])
        row["identities_admitted_after_deadline"] = sum(g["identities_admitted_after_deadline"])
        row["admitted_after_deadline_share"] = (row["admitted_after_deadline"] / row["admitted_requests"]) if row["admitted_requests"] else None
        out.append(row)
    return out


COLS = ["campaign", "contract", "policy", "cells", "offered_per_s", "admit_n", "admit_ms_p50", "admit_ms_p95", "admit_ms_p99",
        "in_ms_p50", "in_ms_p95", "client_ms_p50", "client_ms_p95", "lock_ms_p50", "lock_ms_p95", "eval_ms_p50", "eval_ms_p95",
        "out_ms_p50", "out_ms_p95",
        "decide_ms_p50", "decide_ms_p95", "dispatch_ms_p50", "dispatch_ms_p95", "terminal_ms_p50", "terminal_ms_p95",
        "authority_cpu_pct_mean", "authority_rss_mb_max", "device_cpu_pct_mean", "device_rss_mb_max", "events_per_cmd",
        "db_bytes_per_cmd", "dispatch_attempts_per_cmd", "queries_per_cmd", "stops_per_cmd", "fences_per_cmd",
        "fence_pause_ms_p50", "fence_pause_ms_p95", "fence_pause_ms_max", "paused_fraction",
        "admitted_requests", "admitted_after_deadline", "identities_admitted_after_deadline", "admitted_after_deadline_share"]


def markdown(rows: list) -> str:
    L = ["# RQ4: runtime overhead of the EviCon authority", "",
         "Per campaign, contract and policy; latencies in ms (p50/p95/p99 over commands), CPU in % of one core per process "
         "(mean over the process's lifetime), RSS maximum in MB. `admit` is seen by the client (VM-C), `decide` is the "
         "authority's commit (trace), `dispatch` the owner round trip; `terminal` is bounded below by the client's poll "
         "interval (`retry_s`).", "",
         "| campaign | contract | policy | offered/s | admit p50/p95/p99 | decide p50/p95 | dispatch p50/p95 | terminal p50/p95 | "
         "authority CPU % | authority RSS MB | events/cmd | DB bytes/cmd |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    f = lambda *x: "/".join("-" if v is None else f"{v:g}" for v in x)  # noqa: E731
    for r in rows:
        L.append(f"| {r['campaign']} | {r['contract']} | {r['policy']} | {r['offered_per_s']} | "
                 f"{f(r['admit_ms_p50'], r['admit_ms_p95'], r['admit_ms_p99'])} | {f(r['decide_ms_p50'], r['decide_ms_p95'])} | "
                 f"{f(r['dispatch_ms_p50'], r['dispatch_ms_p95'])} | {f(r['terminal_ms_p50'], r['terminal_ms_p95'])} | "
                 f"{f(r['authority_cpu_pct_mean'])} | {f(r['authority_rss_mb_max'])} | {f(r['events_per_cmd'])} | {f(r['db_bytes_per_cmd'])} |")
    L += ["", "Where the admission latency goes (release 069; p50/p95 ms; trace wall clock against the client's, VM-C and "
          "VM-A synchronised by chrony). admit = in + decide + out, and in = client + lock + eval: `client` = scheduled "
          "arrival -> the authority's handler starts (load generator and network), `lock` = waiting for the authority's "
          "lock and reading the request, `eval` = domain state and policy before ADMISSION_PREPARE. `client`, `lock`, "
          "`eval` need authorities of release 069 or later (`-` otherwise).", "",
          "| campaign | contract | policy | offered/s | in | client | lock | eval | decide | out |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['campaign']} | {r['contract']} | {r['policy']} | {r['offered_per_s']} | {f(r['in_ms_p50'], r['in_ms_p95'])} | "
                 f"{f(r['client_ms_p50'], r['client_ms_p95'])} | {f(r['lock_ms_p50'], r['lock_ms_p95'])} | "
                 f"{f(r['eval_ms_p50'], r['eval_ms_p95'])} | {f(r['decide_ms_p50'], r['decide_ms_p95'])} | "
                 f"{f(r['out_ms_p50'], r['out_ms_p95'])} |")
    adm = [r for r in rows if r.get("admitted_requests")]
    if adm:
        tot, late = sum(r["admitted_requests"] for r in adm), sum(r["admitted_after_deadline"] for r in adm)
        L += ["", f"Admissions after the deadline (release 083, A2): {late} of {tot} admitted requests ({100 * late / tot:.3f} %) were "
              "admitted after their deadline (first admission later than arrival + deadline_s; the client submits until the deadline "
              "and records an answer when it arrives).", "",
              "| campaign | contract | policy | admitted requests | admitted after the deadline | share % | identities admitted after the deadline |",
              "|---|---|---|---|---|---|---|"]
        for r in adm:
            L.append(f"| {r['campaign']} | {r['contract']} | {r['policy']} | {r['admitted_requests']} | {r['admitted_after_deadline']} | "
                     f"{100 * (r['admitted_after_deadline_share'] or 0):.3f} | {r['identities_admitted_after_deadline']} |")
    act = [r for r in rows if (r["fences_per_cmd"] or 0) + (r["queries_per_cmd"] or 0) + (r["stops_per_cmd"] or 0) > 0]
    if act:
        L += ["", "Evidence actions (cells with faults):", "",
              "| campaign | contract | policy | dispatch/cmd | queries/cmd | stops/cmd | fences/cmd | fence pause p50/p95/max ms | paused fraction |",
              "|---|---|---|---|---|---|---|---|---|"]
        for r in act:
            L.append(f"| {r['campaign']} | {r['contract']} | {r['policy']} | {f(r['dispatch_attempts_per_cmd'])} | {f(r['queries_per_cmd'])} | "
                     f"{f(r['stops_per_cmd'])} | {f(r['fences_per_cmd'])} | {f(r['fence_pause_ms_p50'], r['fence_pause_ms_p95'], r['fence_pause_ms_max'])} | "
                     f"{f(r['paused_fraction'])} |")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--campaign", action="append", required=True, help="campaign run directory (repeatable; globs allowed)")
    ap.add_argument("--samples", action="append", default=[], help="rq4_sampler.py output (repeatable; globs allowed)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    runs = [Path(p) for c in a.campaign for p in (sorted(glob.glob(c)) or [c])]
    samples = [s for g in a.samples for p in (sorted(glob.glob(g)) or [g]) for s in load_jsonl(Path(p))]
    rows = summarize(runs, samples)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "overhead.json").write_text(json.dumps(rows, indent=2) + "\n")
    with open(out / "overhead.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    (out / "overhead.md").write_text(markdown(rows))
    print(f"overhead: {len(rows)} groups from {len(runs)} campaign run(s), {len(samples)} resource samples -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
