#!/usr/bin/env python3
"""Latency control of RQ4 (release 077; PREREG_review_077.md section 2, exploratory).

The RQ4 decomposition (evaluation/overhead.py) defines `client` as the time from a command's scheduled
arrival to the start of the authority's handler, i.e. the load generator (timer, per-device submitter
queue, HTTP client) plus the network, and the paper attributed the growth of the admission latency with
the load to that component. The review asked for a control. This script replays the RQ4 load (the same
configs rq4_load_r1/r5/r20, the same seeded arrivals of campaign.schedule, the same generator design as
campaign.py: one timer per arrival, one submitter thread per device, a fresh HTTP connection per request
with campaign.http, one watcher per admitted command polling every retry_s) against evicon-noop, an
endpoint that admits every command at once and does nothing else. For every command it records

  arrival      the scheduled arrival (VM-C wall clock)
  sent         when the submitter started the POST (VM-C)
  handler      when evicon-noop's handler started (VM-A wall clock; VM-C and VM-A synchronised by chrony)
  replied      when evicon-noop replied (VM-A)
  admitted     when the generator read the reply (VM-C)

and reports, per load, p50/p95/p99 of

  gen_ms       sent - arrival          the generator alone (one clock)
  net_ms       handler - sent          connection set-up, request transfer and accept (two clocks)
  client_ms    handler - arrival       overhead.py's `client` component, against a no-op endpoint
  admit_ms     admitted - arrival      what the client sees (overhead.py's admit_ms)
  noop_ms      replied - handler       the endpoint's own work (about nothing)

With --overhead <overhead.json> (overhead.py's output of the RQ4 runs) it puts the authority's client_ms
and admit_ms of the same loads next to them.

    python3 evaluation/latency_control.py local --out <dir> [--episodes 1 --duration 30]   (one machine)
    python3 evaluation/latency_control.py generate --config <rq4 config> --urls <u1,u2,u3,u4> --episode 0 --out <clients.jsonl>
    python3 evaluation/latency_control.py analyse --clients <jsonl> ... --noop <jsonl> ... [--overhead <json>] --out <dir>
"""
from __future__ import annotations

import argparse
import json
import socket
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "evaluation"))
import campaign as C  # noqa: E402  (frozen by the 067-075 plans; imported, never edited)

CONFIGS = ("rq4_load_r1", "rq4_load_r5", "rq4_load_r20")
METRICS = ("gen_ms", "net_ms", "client_ms", "admit_ms", "noop_ms")


def generate(cfg: dict, episode: int, urls: list, out: Path, duration_s: float | None = None) -> int:
    """One episode of the RQ4 load against `urls` (one per device). Returns the number of commands."""
    if duration_s:
        cfg = dict(cfg, duration_s=duration_s)
    w = cfg["workload"]
    arrivals, _ = C.schedule(cfg, episode)
    recs, lock = [], threading.Lock()
    queues = [[] for _ in range(cfg["devices"])]
    conds = [threading.Condition() for _ in range(cfg["devices"])]
    stop = threading.Event()
    t0 = time.time() + 1.0

    def enqueue(a):
        with conds[a.device]:
            queues[a.device].append(a)
            conds[a.device].notify()

    def watch(d, cid, deadline):
        while time.time() < deadline and not stop.is_set():
            _, o = C.http("GET", f"{urls[d]}/v1/commands/{cid}")
            if isinstance(o, dict) and o.get("status") in ("APPLIED", "STOPPED"):
                return
            time.sleep(w["retry_s"])

    def submitter(d):
        n = 0
        while not stop.is_set():
            with conds[d]:
                while not queues[d] and not stop.is_set():
                    conds[d].wait(0.2)
                if stop.is_set():
                    return
                a = queues[d].pop(0)
            n += 1
            cid = f"lc-{cfg['name']}-e{episode}-d{d}-{a.seq}"
            arrival = t0 + a.t
            body = {"schema_version": 1, "domain_id": f"noop-{d}", "generation": 1, "command_id": cid, "operation": "ADD",
                    "value": [a.value], "epoch": 1, "sequence": n}
            sent = time.time()
            code, o = C.http("POST", f"{urls[d]}/v1/commands", body, timeout=max(1.0, w["retry_s"] * 3))
            got = time.time()
            ok = isinstance(o, dict) and o.get("decision") == "ADMITTED"
            with lock:
                recs.append({"cid": cid, "device": d, "rate_per_s": w["rate_per_s"], "episode": episode, "arrival": arrival,
                             "sent": sent, "admitted": got if ok else None, "status": code})
            if ok:
                threading.Thread(target=watch, args=(d, cid, arrival + w["deadline_s"]), daemon=True).start()

    subs = [threading.Thread(target=submitter, args=(d,), daemon=True) for d in range(cfg["devices"])]
    for t in subs:
        t.start()
    timers = [threading.Timer(max(0.0, t0 + a.t - time.time()), enqueue, args=(a,)) for a in arrivals]
    for t in timers:
        t.daemon = True
        t.start()
    time.sleep(max(0.0, t0 + cfg["duration_s"] + float(cfg.get("tail_s", 10)) - time.time()))
    stop.set()
    for t in subs:
        t.join(timeout=10)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "a") as fh:
        for r in recs:
            fh.write(json.dumps(r) + "\n")
    return len(recs)


def _jsonl(paths):
    for p in paths:
        with open(p) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)


def pct(v, q):
    if not v:
        return None
    v = sorted(v)
    k = (len(v) - 1) * q / 100.0
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return round(v[lo] + (v[hi] - v[lo]) * (k - lo), 2)


def analyse(clients: list, noop: list, overhead: str | None = None) -> dict:
    srv = {r["command_id"]: r for r in _jsonl(noop) if r.get("method") == "POST" and r.get("command_id")}
    by = {}
    for c in _jsonl(clients):
        g = by.setdefault(float(c["rate_per_s"]), {m: [] for m in METRICS} | {"offered": 0, "admitted": 0, "unmatched": 0})
        g["offered"] += 1
        if not c.get("admitted"):
            continue
        g["admitted"] += 1
        s = srv.get(c["cid"])
        g["gen_ms"].append((c["sent"] - c["arrival"]) * 1000.0)
        g["admit_ms"].append((c["admitted"] - c["arrival"]) * 1000.0)
        if s is None:
            g["unmatched"] += 1
            continue
        h, rp = s["handler_start_unix_ns"] / 1e9, s["reply_unix_ns"] / 1e9
        g["net_ms"].append((h - c["sent"]) * 1000.0)
        g["client_ms"].append((h - c["arrival"]) * 1000.0)
        g["noop_ms"].append((rp - h) * 1000.0)
    res = {"loads": []}
    for rate, g in sorted(by.items()):
        row = {"rate_per_device_s": rate, "offered": g["offered"], "admitted": g["admitted"], "unmatched": g["unmatched"]}
        for m in METRICS:
            row[m] = {"p50": pct(g[m], 50), "p95": pct(g[m], 95), "p99": pct(g[m], 99),
                      "mean": round(statistics.mean(g[m]), 2) if g[m] else None}
        res["loads"].append(row)
    if overhead:
        rows = json.loads(Path(overhead).read_text())
        res["authority"] = [{k: r.get(k) for k in ("campaign", "contract", "policy", "offered_per_s", "admit_ms_p50", "admit_ms_p95",
                                                   "admit_ms_p99", "client_ms_p50", "client_ms_p95")}
                            for r in rows if str(r.get("campaign", "")).startswith("rq4")]
    return res


def markdown(res: dict) -> str:
    f = lambda d: f"{d['p50']} / {d['p95']} / {d['p99']}" if d and d.get("p50") is not None else "-"  # noqa: E731
    L = ["# RQ4 latency control: the RQ4 load against a no-op endpoint (release 077)", "",
         "p50 / p95 / p99 in ms. `gen` = scheduled arrival -> the submitter starts the POST (load generator alone, one clock); "
         "`net` = POST start -> the endpoint's handler starts (connection, transfer, accept; two clocks synchronised by chrony); "
         "`client` = arrival -> handler start (overhead.py's component, here without the authority); `admit` = arrival -> "
         "the generator reads ADMITTED; `noop` = the endpoint's own time.", "",
         "| load (cmd/s per device) | offered | admitted | gen | net | client | admit | noop |", "|---|---|---|---|---|---|---|---|"]
    for r in res["loads"]:
        L.append(f"| {r['rate_per_device_s']:g} | {r['offered']} | {r['admitted']} | {f(r['gen_ms'])} | {f(r['net_ms'])} | "
                 f"{f(r['client_ms'])} | {f(r['admit_ms'])} | {f(r['noop_ms'])} |")
    if res.get("authority"):
        L += ["", "The authority under the same loads (overhead.py, RQ4 runs; p50 / p95 ms):", "",
              "| campaign | contract | policy | offered/s | admit p50 / p95 / p99 | client p50 / p95 |", "|---|---|---|---|---|---|"]
        for r in res["authority"]:
            L.append(f"| {r['campaign']} | {r['contract']} | {r['policy']} | {r['offered_per_s']} | {r['admit_ms_p50']} / "
                     f"{r['admit_ms_p95']} / {r['admit_ms_p99']} | {r['client_ms_p50']} / {r['client_ms_p95']} |")
    return "\n".join(L) + "\n"


def _free_ports(n):
    socks, ports = [], []
    for _ in range(n):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        socks.append(s)
        ports.append(s.getsockname()[1])
    for s in socks:
        s.close()
    return ports


def local(out: Path, episodes: int, duration: float | None, configs=CONFIGS, exe: str | None = None) -> dict:
    """The whole control on one machine (tests, make verify): evicon-noop locally, the generator here."""
    out.mkdir(parents=True, exist_ok=True)
    exe = exe or str(REPO / "bin" / "evicon-noop")
    clients, noops = [], []
    for name in configs:
        cfg = C.load_config(REPO / "evaluation" / "campaigns" / f"{name}.json") if hasattr(C, "load_config") else None
        cfg = cfg or json.loads((REPO / "evaluation" / "campaigns" / f"{name}.json").read_text())
        ports = _free_ports(cfg["devices"])
        log = out / f"noop-{name}.jsonl"
        p = subprocess.Popen([exe, "-listen", ",".join(f"127.0.0.1:{x}" for x in ports), "-log", str(log)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            urls = [f"http://127.0.0.1:{x}" for x in ports]
            for u in urls:
                C.wait_http(f"{u}/healthz", timeout=10)
            for ep in range(episodes):
                generate(cfg, ep, urls, out / f"clients-{name}.jsonl", duration_s=duration)
        finally:
            p.terminate()
            p.wait(timeout=10)
        clients.append(out / f"clients-{name}.jsonl")
        noops.append(log)
    res = analyse(clients, noops)
    (out / "latency_control.json").write_text(json.dumps(res, indent=2) + "\n")
    (out / "LATENCY_CONTROL.md").write_text(markdown(res))
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sp = ap.add_subparsers(dest="cmd", required=True)
    g = sp.add_parser("generate")
    g.add_argument("--config", required=True)
    g.add_argument("--urls", required=True)
    g.add_argument("--episode", type=int, default=0)
    g.add_argument("--duration", type=float)
    g.add_argument("--out", required=True)
    an = sp.add_parser("analyse")
    an.add_argument("--clients", action="append", required=True)
    an.add_argument("--noop", action="append", required=True)
    an.add_argument("--overhead")
    an.add_argument("--out", required=True)
    lo = sp.add_parser("local")
    lo.add_argument("--out", required=True)
    lo.add_argument("--episodes", type=int, default=1)
    lo.add_argument("--duration", type=float)
    lo.add_argument("--exe")
    a = ap.parse_args(argv)
    if a.cmd == "generate":
        cfg = json.loads(Path(a.config).read_text())
        n = generate(cfg, a.episode, a.urls.split(","), Path(a.out), duration_s=a.duration)
        print(f"latency_control: {n} commands -> {a.out}")
    elif a.cmd == "analyse":
        res = analyse(a.clients, a.noop, a.overhead)
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "latency_control.json").write_text(json.dumps(res, indent=2) + "\n")
        (out / "LATENCY_CONTROL.md").write_text(markdown(res))
        print(f"latency_control: {len(res['loads'])} loads -> {out}")
    else:
        res = local(Path(a.out), a.episodes, a.duration, exe=a.exe)
        print(json.dumps(res["loads"], indent=1)[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
