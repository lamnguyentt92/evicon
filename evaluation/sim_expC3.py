#!/usr/bin/env python3
"""Simulator prediction for experiment C3 (release 075, SIMULATION_ONLY, written before any C3 cell runs).

Experiment C3 decomposes the evidence of the Zephyr EviCon firmware on the real IoT stack of experiment
C2. EVICON runs on restrictions of the contracts measured for the cutoff and stop modes to one capability
each: {Q_A} (evidence of what happened only), {F} (cutoff fence only) and {Q_S} (per-identity stop only),
and on the plain mode's measured contract (none). A restricted contract is weaker than the measured one,
so it is sound wherever the measured one is. Two owner channels: "queue" (as C2: an offline window resets
the gateway's MQTT connections and paho delivers what was published meanwhile late) and "direct" (an
offline window loses every message either way: a lost command never takes effect).

The testbed twin (experiments/sim/iot_dosing_testbed.json) is run as for experiment C2 (window 6, no
assumed bound on the network delay, fence_uncertain abort) on both channels, in two owner scenarios:

  owner_up     crash_mid_prob 0: the owner never crashes between effect and receipt. This is the fault
               model of the measured contracts and of the C3 campaign (the firmware stays up; a "crash"
               window there is a client abandon at the gateway), so the decision rules are sized here.
  owner_crash  crash_mid_prob of the twin (0.005 per request): the owner crashes between effect and
               receipt and leaves crash orphans, as the emulated owner does in experiments W and S. The
               contrast to owner_up is the simulator's account of what the orphans cost each capability.

Contracts (experiments/owners/zephyr/pred-*.json): plain none, QA {Q_A}, F {F}, QS {Q_S}, and for reference
cutoff {D_h >= 600 s, Q_A, F}; policies EVICON and the full-information ORACLE. The C3 cell names are
ZEPHYR_<contract>_<channel> (the campaign runs PLAIN on the direct channel only; the queue-channel plain,
cutoff and stop cells are those of experiment C2, same seed).
Writes <out>/runs/, <out>/summary.csv, <out>/power.csv and <out>/SIM_EXPC3.md.

    python3 evaluation/sim_expC3.py --des bin/evicon-des --out results/sim_expC3 [--episodes 200]
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
from pairs_report import holm, one_sided  # noqa: E402  (frozen; reused)

OWNERS = REPO / "experiments" / "owners" / "zephyr"
BASE_CONTRACTS = {"PLAIN": "pred-zephyr-plain-074", "QA": "pred-zephyr-qa-075", "F": "pred-zephyr-f-075",
                  "QS": "pred-zephyr-qs-075", "CUTOFF": "pred-zephyr-cutoff-074"}
CHANNELS = ("direct", "queue")
CONTRACTS = {f"ZEPHYR_{k}_{ch.upper()}": (v, ch) for ch in CHANNELS for k, v in BASE_CONTRACTS.items()}
POLICIES = {"EVICON": "EVICON", "ORACLE": "ORACLE"}
SCENARIOS = {"owner_up": 0.0, "owner_crash": None}  # crash_mid_prob; None keeps the twin's value
SAFETY = "safety_violations"

# id, policy, from, to, metric, test, margin: the decision rules of PREREG_expC3_075.md, sized here
HYPOTHESES = [
    ("C3-1", "EVICON", "ZEPHYR_PLAIN_DIRECT", "ZEPHYR_F_DIRECT", "clean_fulfilled", "at_least", 10),
    ("C3-2", "EVICON", "ZEPHYR_QA_DIRECT", "ZEPHYR_F_DIRECT", "clean_fulfilled", "at_least", 10),
    ("C3-3", "EVICON", "ZEPHYR_QA_DIRECT", "ZEPHYR_QS_DIRECT", "clean_fulfilled", "at_least", 10),
    ("C3-4", "EVICON", "ZEPHYR_QA_DIRECT", "ZEPHYR_QA_QUEUE", "clean_fulfilled", "at_least", 10),
    ("C3-5", "EVICON", "ZEPHYR_F_DIRECT", "ZEPHYR_QS_DIRECT", "clean_fulfilled", "equivalent", 5),
    ("C3-6", "EVICON", "ZEPHYR_F_QUEUE", "ZEPHYR_QS_QUEUE", "clean_fulfilled", "equivalent", 5),
]


def config(base: dict, crash) -> dict:
    c = copy.deepcopy(base)
    c["authority"]["max_outstanding"] = 6
    c["authority"]["assumed_max_delay_ms"] = -1  # as every campaign: no bound on the network delay (T4: no same-identity retry)
    if crash is not None:
        c["owner"]["crash_mid_prob"] = crash
    c["policies"] = list(POLICIES.values())
    c["sweep"] = [{"name": k, "patch": {"owner": {"contract": cid, "channel": ch}}} for k, (cid, ch) in CONTRACTS.items()]
    return c


def run(des: str, base: dict, out: Path, scenario: str, episodes: int) -> Path:
    d = out / "runs" / f"expC3_w6_{scenario}"
    if (d / "episodes.csv").exists():
        return d
    d.mkdir(parents=True, exist_ok=True)
    cat = Path(tempfile.mkdtemp(prefix="evicon-catalog-"))
    try:
        for f in (REPO / "experiments" / "contracts").glob("*.json"):
            shutil.copy2(f, cat / f.name)
        for f in OWNERS.glob("pred-*.json"):
            shutil.copy2(f, cat / f.name)
        (d / "config.json").write_text(json.dumps(config(base, SCENARIOS[scenario]), indent=1))
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
            # the sweep point's name (the regime suffix): two points share a contract id (one per channel)
            g[(e["regime"].rsplit("/", 1)[-1], e["policy"])][int(e["episode"])] = e
    return g


def summary(scenario: str, g: dict) -> list:
    rows = []
    for ct in CONTRACTS:
        for pol in POLICIES:
            es = list(g[(ct, pol)].values())
            off = sum(float(e["offered"]) for e in es)

            def tot(k, es=es):
                return sum(float(e[k]) for e in es)
            rows.append({"scenario": scenario, "contract": ct, "policy": pol, "episodes": len(es),
                         "clean_pct": round(100 * tot("clean_fulfilled") / off, 2),
                         "confirmed_pct": round(100 * tot("clean_fulfilled_confirmed") / off, 2),
                         "stranded_resolved": round(tot("stranded_resolved_unit_sec") / len(es), 1),
                         "stranded_censored": round(tot("stranded_censored_unit_sec") / len(es), 1),
                         "stops": round(tot("stops") / len(es), 1), "owner_crashes": round(tot("owner_crashes") / len(es), 2),
                         "safety_violations": int(tot(SAFETY)), "late_effects": int(tot("late_effects"))})
    return rows


def diffs(g: dict, h) -> list:
    a, b = g[(h[2], h[1])], g[(h[3], h[1])]
    return [100 * float(b[i][h[4]]) / float(b[i]["offered"]) - 100 * float(a[i][h[4]]) / float(a[i]["offered"]) for i in a if i in b]


def p_value(d: list, test: str, m: float) -> float:
    if test == "at_least":
        return one_sided(d, m, upper=False)
    return max(one_sided(d, -m, upper=False), one_sided(d, m, upper=True))  # equivalent (TOST)


def power(scenario: str, g: dict, ns=(6, 8, 10), resamples=2000) -> list:
    rows = []
    ds = [diffs(g, h) for h in HYPOTHESES]
    for n in ns:
        rng = random.Random(75)
        met = [0] * len(HYPOTHESES)
        for _ in range(resamples):
            ps = {j: p_value([d[rng.randrange(len(d))] for _ in range(n)], h[5], h[6]) for j, (h, d) in enumerate(zip(HYPOTHESES, ds))}
            adj = holm(ps)
            for j in ps:
                met[j] += adj.get(j, 1.0) < 0.05
        for h, d, m in zip(HYPOTHESES, ds, met):
            rows.append({"scenario": scenario, "n": n, "id": h[0], "policy": h[1], "from": h[2], "to": h[3], "metric": h[4], "test": h[5],
                         "margin": h[6], "sim_mean": round(statistics.mean(d), 2), "sim_sd": round(statistics.stdev(d), 2),
                         "power": round(m / resamples, 2)})
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
    ap.add_argument("--out", default=str(REPO / "results" / "sim_expC3"))
    ap.add_argument("--episodes", type=int, default=200)
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    base = json.loads(Path(a.base).read_text())
    rows, pw = [], []
    for sc in SCENARIOS:
        raw = load(run(a.des, base, out, sc, a.episodes))
        g = {(name, pol): raw[(name, sp)] for name in CONTRACTS for pol, sp in POLICIES.items()}
        rows += summary(sc, g)
        pw += power(sc, g)
    write_csv(out / "summary.csv", rows)
    write_csv(out / "power.csv", pw)
    L = ["# Simulator prediction for experiment C3 (release 075, SIMULATION_ONLY)", "",
         f"{a.episodes} episodes per scenario; base `{Path(a.base).name}`, window 6, fence_uncertain abort, both owner channels; "
         "owner_up: no owner crash (the fault model of C3); owner_crash: the twin's owner crashes between effect and receipt.", "",
         "| scenario | contract | policy | clean % | confirmed % | stranded resolved / censored (unit*s per episode) | stops / episode | owner crashes / episode | safety violations | late effects |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['scenario']} | {r['contract']} | {r['policy']} | {r['clean_pct']} | {r['confirmed_pct']} | {r['stranded_resolved']} / "
                 f"{r['stranded_censored']} | {r['stops']} | {r['owner_crashes']} | {r['safety_violations']} | {r['late_effects']} |")
    L += ["", "## Power of the C3 decision rules under the simulator's paired differences (Holm over C3-1 to C3-6)", "",
          "| scenario | n | ID | comparison | rule | sim mean (SD) | power |", "|---|---|---|---|---|---|---|"]
    for r in pw:
        L.append(f"| {r['scenario']} | {r['n']} | {r['id']} | {r['from']} -> {r['to']} | {r['test']} {r['margin']} | "
                 f"{r['sim_mean']:+.2f} ({r['sim_sd']:.2f}) | {r['power']} |")
    (out / "SIM_EXPC3.md").write_text("\n".join(L) + "\n")
    print(f"sim expC3: {len(rows)} groups -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
