#!/usr/bin/env python3
"""Resource sampler for RQ4 (release 068): CPU time, RSS and I/O of EviCon's own processes.

Runs on a testbed VM (VM-A: evicon-authority; VM-E: evicon-device, evicon-plant) while a campaign
runs; started and stopped by `evicon_review.py overhead` through a pid file. Every interval it
reads /proc for processes whose executable name is in --names and writes one JSON line per
process:

  {"t": wall seconds, "pid", "name", "ticks": utime+stime (clock ticks), "hz", "rss_kb",
   "threads", "read_bytes", "write_bytes", "cell": the campaign cell directory found in the
   process's arguments (-db / -state-dir / -log), "run_id": -run-id}

Only /proc is read; nothing is changed. Stdlib only (Python 3.8+).

    python3 rq4_sampler.py --out samples.jsonl --interval 1 --names evicon-authority
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import time

STOP = False


def _stop(*_a):
    global STOP
    STOP = True


def cmd_args(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return [a.decode(errors="replace") for a in f.read().split(b"\0") if a]
    except OSError:
        return []


def arg(argv, flag):
    for i, a in enumerate(argv):
        if a == flag and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith(flag + "="):
            return a.split("=", 1)[1]
    return ""


def sample(pid, names, hz):
    argv = cmd_args(pid)
    if not argv:
        return None
    name = os.path.basename(argv[0])
    if name not in names:
        return None
    try:
        with open(f"/proc/{pid}/stat") as f:
            st = f.read()
        fields = st[st.rindex(")") + 2:].split()
        ticks = int(fields[11]) + int(fields[12])  # utime + stime
        threads = int(fields[17])
        rss_kb = 0
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    rss_kb = int(line.split()[1])
                    break
        rb = wb = None
        try:
            with open(f"/proc/{pid}/io") as f:
                io = dict(l.split(":", 1) for l in f.read().splitlines() if ":" in l)
            rb, wb = int(io.get("read_bytes", 0)), int(io.get("write_bytes", 0))
        except (OSError, ValueError):
            pass
    except (OSError, ValueError, IndexError):
        return None
    where = arg(argv, "-db") or arg(argv, "-state-dir") or arg(argv, "-log")
    cell = os.path.dirname(where) if where.endswith((".db", ".jsonl")) else where
    if name == "evicon-device":
        cell = os.path.dirname(cell)  # -state-dir <cell>/dev<d>
    return {"t": round(time.time(), 3), "pid": pid, "name": name, "ticks": ticks, "hz": hz, "rss_kb": rss_kb, "threads": threads,
            "read_bytes": rb, "write_bytes": wb, "cell": cell, "run_id": arg(argv, "-run-id")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--names", default="evicon-authority,evicon-device,evicon-plant")
    ap.add_argument("--duration", type=float, default=0.0, help="stop after this many seconds (0 = until SIGTERM)")
    a = ap.parse_args()
    names = set(a.names.split(","))
    hz = os.sysconf("SC_CLK_TCK")
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    t_end = time.monotonic() + a.duration if a.duration > 0 else None
    with open(a.out, "a", buffering=1) as out:
        out.write(json.dumps({"t": round(time.time(), 3), "start": True, "names": sorted(names), "hz": hz,
                              "cpus": os.cpu_count(), "host": os.uname().nodename}) + "\n")
        while not STOP and (t_end is None or time.monotonic() < t_end):
            t0 = time.monotonic()
            for d in os.listdir("/proc"):
                if d.isdigit():
                    s = sample(int(d), names, hz)
                    if s:
                        out.write(json.dumps(s) + "\n")
            time.sleep(max(0.05, a.interval - (time.monotonic() - t0)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
