#!/usr/bin/env python3
"""Simulator sensitivity of EviCon's resolution policy to its action costs (release 077, SIMULATION_ONLY,
exploratory; PREREG_review_077.md section 1(i)).

EVICON resolves an ambiguous command by the ski-rental rule: it holds the capacity the command reserved
and acts (stop, or fence) once the holding cost paid reaches the cost of the action, theta = action cost /
holding cost per unit-second. Every campaign runs the costs of the twin (holding 1 per unit-second, stop 1,
fence 5). The review asked how the results depend on these values. This script varies, around the testbed
twin (experiments/sim/iot_dosing_testbed.json), one cost at a time:

  fence     fence cost 0.5, 1, 2, 5*, 10, 20, 50, 100 (holding 1): contracts K0+F and K0+Q_S+F
  stop      stop cost 0.1, 0.25, 0.5, 1*, 2, 4, 10 (holding 1): contract K0+Q_S
  hold      holding cost 0.25, 0.5, 1*, 2, 4 per unit-second (stop 1, fence 5): K0+F and K0+Q_S

each at W = 6 and W = unbounded on the queue channel (no bound on the network delay, as every campaign),
with the immediate rule (act at once, theta = 0) as the reference point of each contract. Reports clean
and confirmed fulfilment, stops and fences per episode, capacity-time held for ambiguous commands
(stranded, resolved and censored, unit-seconds per episode) and safety. 100 episodes per point. A point
already run is not re-run.

    python3 evaluation/sim_theta077.py --des bin/evicon-des --out results/sim_theta077
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
CONTRACTS = {"K0F": "sim-k0+F", "K0S": "sim-k0+QS", "K0SF": "sim-k0+QS+F"}
FACTORS = {"fence": (0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0), "stop": (0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 10.0),
           "hold": (0.25, 0.5, 1.0, 2.0, 4.0), "immediate": (0.0,)}
TWIN = {"fence": 5.0, "stop": 1.0, "hold": 1.0}
WHICH = {"fence": ("K0F", "K0SF"), "stop": ("K0S",), "hold": ("K0F", "K0S"), "immediate": ("K0F", "K0S", "K0SF")}


def patch(base: dict, factor: str, v: float) -> dict:
    c = copy.deepcopy(base)
    a = c["authority"]
    costs = a["costs"]
    if factor == "fence":
        costs["fence_cost"] = v
    elif factor == "stop":
        costs["stop_cost"] = v
    elif factor == "hold":
        costs["hold_per_unit_per_sec"] = v
    elif factor == "immediate":
        a["resolve"] = dict(a.get("resolve", {}), mode="IMMEDIATE")
    else:
        raise SystemExit(f"unknown factor {factor}")
    return c


def points():
    for factor, vals in FACTORS.items():
        for v in vals:
            for w in (6, INF):
                yield f"{factor}{v}_w{'inf' if w == INF else w}", factor, v, w


def run(des: str, base: dict, out: Path, name: str, factor: str, v: float, w: int, episodes: int) -> Path:
    d = out / "runs" / name
    if (d / "episodes.csv").exists():
        return d
    d.mkdir(parents=True, exist_ok=True)
    c = patch(base, factor, v)
    c["authority"]["max_outstanding"] = w
    c["authority"]["assumed_max_delay_ms"] = -1
    c["policies"] = ["EVICON"]
    c["sweep"] = [{"name": k, "patch": {"owner": {"contract": CONTRACTS[k], "channel": "queue"}}} for k in WHICH[factor]]
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
    g = collections.defaultdict(list)
    with open(d / "episodes.csv", newline="") as fh:
        for e in csv.DictReader(fh):
            if e["policy"] == "EVICON":
                g[e["regime"].rsplit("/", 1)[-1]].append(e)
    return g


def summarise(name, factor, v, w, g) -> list:
    rows = []
    for k, es in sorted(g.items()):
        off = sum(float(e["offered"]) for e in es)
        n = len(es)
        clean = [100.0 * float(e["clean_fulfilled"]) / float(e["offered"]) for e in es if float(e["offered"])]
        rows.append({"point": name, "factor": factor, "value": v, "window": "inf" if w == INF else w, "contract": k, "episodes": n,
                     "clean_pct": round(100 * sum(float(e["clean_fulfilled"]) for e in es) / off, 2),
                     "clean_sd": round(statistics.stdev(clean), 2) if len(clean) > 1 else 0.0,
                     "confirmed_pct": round(100 * sum(float(e["clean_fulfilled_confirmed"]) for e in es) / off, 2),
                     "stops_per_ep": round(sum(float(e["stops"]) for e in es) / n, 2),
                     "fences_per_ep": round(sum(float(e["fences"]) for e in es) / n, 2),
                     "stranded_resolved_per_ep": round(sum(float(e["stranded_resolved_unit_sec"]) for e in es) / n, 1),
                     "stranded_censored_per_ep": round(sum(float(e["stranded_censored_unit_sec"]) for e in es) / n, 1),
                     "unresolved_at_end_per_ep": round(sum(float(e["unresolved_at_end"]) for e in es) / n, 2),
                     "safety": int(sum(float(e.get("safety_violations") or 0) for e in es))})
    return rows


def markdown(rows: list, episodes: int) -> str:
    L = ["# Simulator sensitivity of EviCon to its action costs (release 077, SIMULATION_ONLY)", "",
         f"{episodes} episodes per point; base `experiments/sim/iot_dosing_testbed.json` (queue channel); EVICON with the ski-rental "
         "rule; one cost varied at a time, the twin's value marked *; `immediate`: act at once (theta = 0). Clean and confirmed "
         "fulfilment in % of offered; stops, fences and capacity-time held for ambiguous commands (unit-seconds) per episode.", ""]
    for factor in FACTORS:
        L += [f"## {factor}", "", "| value | W | contract | clean % (SD) | confirmed % | stops | fences | held, resolved | held, censored | unresolved at end | safety |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in rows:
            if r["factor"] != factor:
                continue
            star = "*" if TWIN.get(factor) == r["value"] else ""
            L.append(f"| {r['value']}{star} | {r['window']} | {r['contract']} | {r['clean_pct']} ({r['clean_sd']}) | {r['confirmed_pct']} | "
                     f"{r['stops_per_ep']} | {r['fences_per_ep']} | {r['stranded_resolved_per_ep']} | {r['stranded_censored_per_ep']} | "
                     f"{r['unresolved_at_end_per_ep']} | {r['safety']} |")
        L.append("")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--des", default=str(REPO / "bin" / "evicon-des"))
    ap.add_argument("--base", default=str(REPO / "experiments" / "sim" / "iot_dosing_testbed.json"))
    ap.add_argument("--out", default=str(REPO / "results" / "sim_theta077"))
    ap.add_argument("--episodes", type=int, default=100)
    ap.add_argument("--jobs", type=int, default=2)
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    base = json.loads(Path(a.base).read_text())
    from concurrent.futures import ThreadPoolExecutor
    pts = list(points())
    with ThreadPoolExecutor(max_workers=max(1, a.jobs)) as ex:
        dirs = list(ex.map(lambda p: run(a.des, base, out, p[0], p[1], p[2], p[3], a.episodes), pts))
    rows = [r for p, d in zip(pts, dirs) for r in summarise(p[0], p[1], p[2], p[3], load(d))]
    with open(out / "summary.csv", "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        wr.writeheader()
        wr.writerows(rows)
    (out / "THETA077.md").write_text(markdown(rows, a.episodes))
    print(f"sim_theta077: {len(pts)} points, {len(rows)} rows -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
