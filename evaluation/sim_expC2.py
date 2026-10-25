#!/usr/bin/env python3
"""Simulator prediction for experiment C2 (release 074, SIMULATION_ONLY, written before any C2 cell runs).

Runs the testbed twin (experiments/sim/iot_dosing_testbed.json) with the three contracts experiment C2
expects the Zephyr EviCon firmware to measure (experiments/owners/zephyr/pred-*.json: plain = none,
cutoff = D_h >= 600 s, Q_A, F as measured in 070, stop = cutoff + Q_S), window 6, queue owner channel
(in experiment C2 an offline window resets the gateway's MQTT connections; paho keeps what is published
meanwhile and sends it after reconnecting, so a request sent then arrives late, not lost), no assumed
bound on the network delay (max_network_delay -1ns: T4 allows no same-identity retry, so EVICON resolves
with Q_A, F and, in the stop mode, Q_S), and the policies that correspond to experiment C2's:

  EVICON            EVICON
  M1                M1 (legacy, T4 retry guard on)
  B1_DURABLE_RETRY  NAIVE (same-identity retry until a receipt, no T4 guard)
  ORACLE            reference

The numbers quoted in section 1 of evaluation/prereg/PREREG_expC2_074.md come from this script (200
episodes, release 074 simulator). Writes <out>/runs/ (config, episodes.csv), <out>/summary.csv,
<out>/power.csv and <out>/SIM_EXPC2.md. A run already present is not re-run.

    python3 evaluation/sim_expC2.py --des bin/evicon-des --out results/sim_expC2 [--episodes 200]
"""
from __future__ import annotations

import argparse
import collections
import copy
import csv
import json
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "evaluation"))
from pairs_report import holm, one_sided  # noqa: E402

OWNERS = REPO / "experiments" / "owners" / "zephyr"
CONTRACTS = {"ZEPHYR_PLAIN": "pred-zephyr-plain-074", "ZEPHYR_CUTOFF": "pred-zephyr-cutoff-074", "ZEPHYR_STOP": "pred-zephyr-stop-074"}
POLICIES = {"EVICON": "EVICON", "M1": "M1", "NAIVE": "B1_DURABLE_RETRY", "ORACLE": "ORACLE"}
SAFETY = "safety_violations"

# id, policy, from, to, metric, test, margin (the decision rules of PREREG_expC2_074.md, sized here)
HYPOTHESES = [
    ("C2-3", "EVICON", "ZEPHYR_PLAIN", "ZEPHYR_CUTOFF", "clean_fulfilled", "at_least", 10),
    ("C2-4", "EVICON", "ZEPHYR_PLAIN", "ZEPHYR_CUTOFF", "clean_fulfilled_confirmed", "at_least", 10),
    ("C2-5", "EVICON", "ZEPHYR_CUTOFF", "ZEPHYR_STOP", "clean_fulfilled", "at_least", 0),
]


def config(base: dict) -> dict:
    c = copy.deepcopy(base)
    c["authority"]["max_outstanding"] = 6
    # the testbed authority assumes no bound on the network delay (max_network_delay -1ns, as in every
    # campaign); with a finite measured horizon (D_h >= 600 s) T4 then allows no same-identity retry
    c["authority"]["assumed_max_delay_ms"] = -1
    c["owner"]["channel"] = "queue"
    c["policies"] = list(POLICIES.values())
    c["sweep"] = [{"name": k, "patch": {"owner": {"contract": v}}} for k, v in CONTRACTS.items()]
    return c


def run(des: str, base: dict, out: Path, episodes: int) -> Path:
    d = out / "runs" / "expC2_w6_queue"
    if (d / "episodes.csv").exists():
        return d
    d.mkdir(parents=True, exist_ok=True)
    cat = Path(tempfile.mkdtemp(prefix="evicon-catalog-"))
    try:
        for f in (REPO / "experiments" / "contracts").glob("*.json"):
            shutil.copy2(f, cat / f.name)
        for f in OWNERS.glob("pred-*.json"):
            shutil.copy2(f, cat / f.name)
        (d / "config.json").write_text(json.dumps(config(base), indent=1))
        r = subprocess.run([des, "-catalog", str(cat), "-config", str(d / "config.json"), "-out", str(d), "-episodes", str(episodes)],
                           capture_output=True, text=True)
        (d / "run.log").write_text(r.stdout + r.stderr)
        if r.returncode != 0:
            raise SystemExit(f"evicon-des failed: {r.stderr[-600:]}")
    finally:
        shutil.rmtree(cat, ignore_errors=True)
    return d


def load(d: Path) -> dict:
    g = collections.defaultdict(dict)
    with open(d / "episodes.csv", newline="") as fh:
        for e in csv.DictReader(fh):
            g[(e["contract"], e["policy"])][int(e["episode"])] = e
    return g


def summary(g: dict) -> list:
    """g: (contract name, experiment C policy) -> {episode: row}."""
    rows = []
    for ct in CONTRACTS:
        for pol in POLICIES:
            es = list(g[(ct, pol)].values())
            off = sum(float(e["offered"]) for e in es)

            def tot(k, es=es):
                return sum(float(e[k]) for e in es)
            rows.append({"contract": ct, "policy": pol, "sim_policy": POLICIES[pol], "episodes": len(es),
                         "clean_pct": round(100 * tot("clean_fulfilled") / off, 2), "confirmed_pct": round(100 * tot("clean_fulfilled_confirmed") / off, 2),
                         "stranded_resolved": round(tot("stranded_resolved_unit_sec") / len(es), 1),
                         "stranded_censored": round(tot("stranded_censored_unit_sec") / len(es), 1), "stops": round(tot("stops") / len(es), 1),
                         "safety_violations": int(tot(SAFETY)), "episodes_with_violation": sum(1 for e in es if float(e[SAFETY]) > 0),
                         "late_effects": int(tot("late_effects"))})
    return rows


def power(g: dict, ns=(6, 8, 10), resamples=2000) -> list:
    rows = []
    for n in ns:
        ds = []
        for h in HYPOTHESES:
            a, b = g[(h[2], h[1])], g[(h[3], h[1])]
            ds.append([100 * float(b[i][h[4]]) / float(b[i]["offered"]) - 100 * float(a[i][h[4]]) / float(a[i]["offered"]) for i in a if i in b])
        rng = random.Random(74)
        met = [0] * len(HYPOTHESES)
        for _ in range(resamples):
            ps = {}
            for j, (h, d) in enumerate(zip(HYPOTHESES, ds)):
                s = [d[rng.randrange(len(d))] for _ in range(n)]
                ps[j] = one_sided(s, h[6], upper=False)
            adj = holm(ps)
            for j in ps:
                met[j] += adj.get(j, 1.0) < 0.05
        for h, d, m in zip(HYPOTHESES, ds, met):
            rows.append({"n": n, "id": h[0], "policy": h[1], "from": h[2], "to": h[3], "metric": h[4], "test": h[5], "margin": h[6],
                         "sim_mean": round(statistics.mean(d), 2), "sim_sd": round(statistics.stdev(d), 2), "power": round(m / resamples, 2)})
    return rows


def write_csv(path: Path, rows: list):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--des", default=str(REPO / "bin" / "evicon-des"))
    ap.add_argument("--base", default=str(REPO / "experiments" / "sim" / "iot_dosing_testbed.json"))
    ap.add_argument("--out", default=str(REPO / "results" / "sim_expC2"))
    ap.add_argument("--episodes", type=int, default=200)
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    raw = load(run(a.des, json.loads(Path(a.base).read_text()), out, a.episodes))  # (contract id, sim policy)
    g = {(name, pol): raw[(cid, sp)] for name, cid in CONTRACTS.items() for pol, sp in POLICIES.items()}
    rows = summary(g)
    write_csv(out / "summary.csv", rows)
    pw = power(g)
    write_csv(out / "power.csv", pw)
    L = ["# Simulator prediction for experiment C2 (release 074, SIMULATION_ONLY)", "",
         f"{a.episodes} episodes; base `{Path(a.base).name}`, window 6, queue owner channel; the contracts C2 expects the "
         f"Zephyr firmware's modes to measure (experiments/owners/zephyr/pred-*.json).", "",
         "| contract | policy (sim) | clean % | confirmed % | stranded resolved / censored (unit*s per episode) | stops / episode | safety violations (episodes) | late effects |",
         "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['contract']} | {r['policy']} ({r['sim_policy']}) | {r['clean_pct']} | {r['confirmed_pct']} | {r['stranded_resolved']} / "
                 f"{r['stranded_censored']} | {r['stops']} | {r['safety_violations']} ({r['episodes_with_violation']}) | {r['late_effects']} |")
    L += ["", "## Power of the C2 decision rules under the simulator's paired differences (Holm over C2-3 to C2-5)", "",
          "| n | ID | policy | comparison | metric | rule | sim mean (SD) | power |", "|---|---|---|---|---|---|---|---|"]
    for r in pw:
        L.append(f"| {r['n']} | {r['id']} | {r['policy']} | {r['from']} -> {r['to']} | {r['metric']} | {r['test']} {r['margin']} | "
                 f"{r['sim_mean']:+.2f} ({r['sim_sd']:.2f}) | {r['power']} |")
    (out / "SIM_EXPC2.md").write_text("\n".join(L) + "\n")
    print(f"sim expC2: {len(rows)} groups -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
