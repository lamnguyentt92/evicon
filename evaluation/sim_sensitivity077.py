#!/usr/bin/env python3
"""Simulator sensitivity of the value of backward evidence to the fault regime (release 077,
SIMULATION_ONLY, exploratory; PREREG_review_077.md section 1).

The review of the paper draft noted that every runtime experiment uses one extreme fault regime (fault
windows twice a minute, a quarter of the time offline, owner crashes) and asked for a sensitivity
analysis. The 072 sweep (sim_sweep.py) varied the window W, the channel, the capacity pressure, the
request loss and the crash rate. This script varies, one factor at a time around the testbed twin
(experiments/sim/iot_dosing_testbed.json, the twin of every plan), the factors the review named and the
072 sweep did not:

  scale     every fault rate of the twin multiplied by s (request loss, late requests, reply loss, query
            and control loss, the crash rate, and the offline share s * f0, where f0 = 20.5 % is the
            twin's offline share): s in {0.25, 0.5, 1, 1.5, 2}
  offline   the offline share alone (the online mean set so that offline / (online + offline) = f, the
            offline mean kept at 23.7 s): f in {0, 0.05, 0.10, 0.205, 0.30, 0.40}
  outage    the mean outage alone (the offline share kept at f0): 5, 12, 23.7, 60, 120 s
  crash     the crash rate alone (crash between effect and receipt, a crash orphan): 0, 0.0025, 0.005,
            0.01, 0.02 per request

each at W = 6 and W = unbounded, on the queue and the direct channel, for the contracts K0, K0+F and
K0+Q_S under EVICON (no bound on the network delay, as every campaign). 100 episodes per point.
Writes <out>/runs/<point>/, <out>/summary.csv (clean and confirmed fulfilment per point and contract,
the effects K0 -> K0+F, K0 -> K0+Q_S and K0+F -> K0+Q_S with their per-episode SD) and
<out>/SENSITIVITY077.md. A point already run is not re-run.

    python3 evaluation/sim_sensitivity077.py --des bin/evicon-des --out results/sim_sensitivity077
"""
from __future__ import annotations

import argparse
import collections
import copy
import csv
import json
import shutil
import statistics
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
INF = 1000000
CONTRACTS = {"K0": "sim-k0", "K0F": "sim-k0+F", "K0S": "sim-k0+QS"}
EFFECTS = (("K0", "K0F"), ("K0", "K0S"), ("K0F", "K0S"))
FACTORS = {"scale": (0.25, 0.5, 1.0, 1.5, 2.0), "offline": (0.0, 0.05, 0.10, 0.205, 0.30, 0.40),
           "outage": (5000, 12000, 23700, 60000, 120000), "crash": (0.0, 0.0025, 0.005, 0.01, 0.02)}


def _offline_share(o: dict) -> float:
    tot = o["online_mean_ms"] + o["offline_mean_ms"]
    return o["offline_mean_ms"] / tot if tot else 0.0


def _set_share(o: dict, f: float) -> None:
    """Offline share f with the outage mean kept; f = 0: always online (the simulator's "both zero")."""
    if f <= 0:
        o["offline_mean_ms"] = 0
        o["online_mean_ms"] = 0
        return
    o["online_mean_ms"] = int(round(o["offline_mean_ms"] * (1 - f) / f))


def patch(base: dict, factor: str, v: float) -> dict:
    c = copy.deepcopy(base)
    n, o = c["network"], c["owner"]
    if factor == "scale":
        for k in ("req_loss_prob", "req_late_prob", "resp_loss_prob", "query_loss_prob", "control_loss_prob"):
            n[k] = min(1.0, n[k] * v)
        o["crash_mid_prob"] = min(1.0, o["crash_mid_prob"] * v)
        _set_share(o, min(0.9, _offline_share(base["owner"]) * v))
    elif factor == "offline":
        _set_share(o, v)
    elif factor == "outage":
        f = _offline_share(base["owner"])
        o["offline_mean_ms"] = int(v)
        _set_share(o, f)
    elif factor == "crash":
        o["crash_mid_prob"] = v
    else:
        raise SystemExit(f"unknown factor {factor}")
    return c


def points():
    for factor, vals in FACTORS.items():
        for v in vals:
            for w in (6, INF):
                for ch in ("queue", "direct"):
                    yield f"{factor}{v}_w{'inf' if w == INF else w}_{ch}", factor, v, w, ch


def run(des: str, base: dict, out: Path, name: str, factor: str, v: float, w: int, ch: str, episodes: int) -> Path:
    d = out / "runs" / name
    if (d / "episodes.csv").exists():
        return d
    d.mkdir(parents=True, exist_ok=True)
    c = patch(base, factor, v)
    c["authority"]["max_outstanding"] = w
    c["authority"]["assumed_max_delay_ms"] = -1
    c["policies"] = ["EVICON"]
    c["sweep"] = [{"name": k, "patch": {"owner": {"contract": cid, "channel": ch}}} for k, cid in CONTRACTS.items()]
    (d / "config.json").write_text(json.dumps(c, indent=1))
    cat = Path(tempfile.mkdtemp(prefix="evicon-catalog-"))
    try:
        for f in (REPO / "experiments" / "contracts").glob("*.json"):
            shutil.copy2(f, cat / f.name)
        r = subprocess.run([des, "-catalog", str(cat), "-config", str(d / "config.json"), "-out", str(d), "-episodes", str(episodes)],
                           capture_output=True, text=True)
        (d / "run.log").write_text(r.stdout + r.stderr)
        if r.returncode != 0:
            raise SystemExit(f"evicon-des failed ({name}): {r.stderr[-600:]}")
    finally:
        shutil.rmtree(cat, ignore_errors=True)
    return d


def load(d: Path) -> dict:
    g = collections.defaultdict(dict)
    with open(d / "episodes.csv", newline="") as fh:
        for e in csv.DictReader(fh):
            if e["policy"] == "EVICON":
                g[e["regime"].rsplit("/", 1)[-1]][int(e["episode"])] = e
    return g


def pct(e: dict, k: str) -> float:
    return 100.0 * float(e[k]) / float(e["offered"]) if float(e["offered"]) else 0.0


def summarise(name, factor, v, w, ch, g) -> dict:
    row = {"point": name, "factor": factor, "value": v, "window": "inf" if w == INF else w, "channel": ch}
    for k in CONTRACTS:
        es = list(g[k].values())
        off = sum(float(e["offered"]) for e in es)
        row[f"{k}_clean"] = round(100 * sum(float(e["clean_fulfilled"]) for e in es) / off, 2)
        row[f"{k}_confirmed"] = round(100 * sum(float(e["clean_fulfilled_confirmed"]) for e in es) / off, 2)
        row[f"{k}_safety"] = int(sum(float(e.get("safety_violations") or 0) for e in es))
    for a, b in EFFECTS:
        d = [pct(g[b][i], "clean_fulfilled") - pct(g[a][i], "clean_fulfilled") for i in g[a] if i in g[b]]
        row[f"{a}->{b}"] = round(statistics.mean(d), 2)
        row[f"{a}->{b}_sd"] = round(statistics.stdev(d), 2) if len(d) > 1 else 0.0
    return row


def markdown(rows: list, episodes: int) -> str:
    L = ["# Simulator sensitivity of the value of backward evidence (release 077, SIMULATION_ONLY)", "",
         f"{episodes} episodes per point; base `experiments/sim/iot_dosing_testbed.json`; EVICON; no assumed bound on the "
         "network delay. One factor varied at a time; the twin's own value is marked *. Clean fulfilment in % of offered; "
         "effects are mean per-episode differences in pp (SD).", ""]
    base = {"scale": 1.0, "offline": 0.205, "outage": 23700, "crash": 0.005}
    for factor in FACTORS:
        L += [f"## {factor}", "", "| value | W | channel | K0 | K0+F | K0+Q_S | K0 -> K0+F | K0 -> K0+Q_S | K0+F -> K0+Q_S | safety |",
              "|---|---|---|---|---|---|---|---|---|---|"]
        for r in rows:
            if r["factor"] != factor:
                continue
            star = "*" if r["value"] == base[factor] else ""
            L.append(f"| {r['value']}{star} | {r['window']} | {r['channel']} | {r['K0_clean']} | {r['K0F_clean']} | {r['K0S_clean']} | "
                     f"{r['K0->K0F']:+.1f} ({r['K0->K0F_sd']}) | {r['K0->K0S']:+.1f} ({r['K0->K0S_sd']}) | "
                     f"{r['K0F->K0S']:+.1f} ({r['K0F->K0S_sd']}) | {r['K0_safety'] + r['K0F_safety'] + r['K0S_safety']} |")
        L.append("")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--des", default=str(REPO / "bin" / "evicon-des"))
    ap.add_argument("--base", default=str(REPO / "experiments" / "sim" / "iot_dosing_testbed.json"))
    ap.add_argument("--out", default=str(REPO / "results" / "sim_sensitivity077"))
    ap.add_argument("--episodes", type=int, default=100)
    ap.add_argument("--jobs", type=int, default=2)
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    base = json.loads(Path(a.base).read_text())
    from concurrent.futures import ThreadPoolExecutor
    pts = list(points())
    with ThreadPoolExecutor(max_workers=max(1, a.jobs)) as ex:
        dirs = list(ex.map(lambda p: run(a.des, base, out, p[0], p[1], p[2], p[3], p[4], a.episodes), pts))
    rows = [summarise(p[0], p[1], p[2], p[3], p[4], load(d)) for p, d in zip(pts, dirs)]
    with open(out / "summary.csv", "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        wr.writeheader()
        wr.writerows(rows)
    (out / "SENSITIVITY077.md").write_text(markdown(rows, a.episodes))
    print(f"sim_sensitivity077: {len(rows)} points -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
