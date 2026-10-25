#!/usr/bin/env python3
"""Simulator prediction for the experiments of PREREG_review_077.md (release 077, SIMULATION_ONLY,
written before any of their cells runs).

The review of the paper draft asked for five things that only new testbed runs can answer. This script
runs the testbed twin (experiments/sim/iot_dosing_testbed.json, the twin every earlier plan used) in the
corresponding settings and sizes the decision rules of the plan:

  unbounded_zephyr  the Zephyr firmware's contracts (experiments/owners/zephyr/pred-*.json) at an
                    unbounded window (max_outstanding 1e6): plain and cutoff on the queue channel (as C2),
                    plain and {F} on the direct channel (as C3); owner up (no crash orphans: the fault model
                    of the firmware's measurement and of C2/C3)
  unbounded_minio   MinIO's two measured contracts (experiments/owners/minio/, as experiment C) at an
                    unbounded window on the direct channel; owner up
  noorphan          the emulated owner of experiment S (K0, K0+F, K0+Q_S; queue channel, window 6) with
                    crash_mid_prob 0. The testbed run keeps every crash window but moves the crash after the
                    durable receipt (evicon-device -crash-point-override); the twin has no such crash, so
                    it predicts the no-orphan limit. The twin's own crash rate is the "orphan" contrast
  k2rep             K2 against K3F (window 6, queue), EVICON and RES_PERIODIC: the replication of
                    experiment A's K2 configuration on the corrected runtime
  mild              the regime with half the fault rate: every loss and late probability, the crash rate
                    and the offline share of the twin halved (online mean such that the offline share
                    halves); K0, K0+F, K0+Q_S at window 6, queue
  release_timeout   the unsafe baseline: B0_NAIVE_NEW_ID (a timed-out command is released and its intent
                    re-issued under a new identity) with the twin's dispatch timeout set to the testbed
                    baseline's 10 s release timeout; K0 and K0+F, window 6, queue and direct channel

Every setting assumes no bound on the network delay (assumed_max_delay_ms -1, as every campaign).
Writes <out>/runs/, <out>/summary.csv, <out>/power.csv and <out>/SIM_REVIEW077.md.

    python3 evaluation/sim_review077.py --des bin/evicon-des --out results/sim_review077 [--episodes 200]
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
from pairs_report import holm, one_sided  # noqa: E402  (frozen by earlier plans; imported, never edited)

INF = 1000000
ZEPHYR = REPO / "experiments" / "owners" / "zephyr"
MINIO = REPO / "experiments" / "owners" / "minio"
SAFETY = "safety_violations"


def _half(base: dict) -> dict:
    """The mild regime: half of every fault rate of the twin."""
    c = copy.deepcopy(base)
    n = c["network"]
    for k in ("req_loss_prob", "req_late_prob", "resp_loss_prob", "query_loss_prob", "control_loss_prob"):
        n[k] = n[k] / 2
    o = c["owner"]
    o["crash_mid_prob"] = o["crash_mid_prob"] / 2
    on, off = o["online_mean_ms"], o["offline_mean_ms"]
    f = off / (on + off)
    o["online_mean_ms"] = int(round(off * (1 - f / 2) / (f / 2)))
    return c


def _wan(base: dict) -> dict:
    """Run WAN: a WAN between the authorities and the devices (evicon-device -wan-delay 100ms -wan-jitter 25ms): every
    request and reply 100 ms longer one way (jitter at least 25 ms), queries and control messages 200 ms longer."""
    c = copy.deepcopy(base)
    n = c["network"]
    n["one_way_base_ms"] = n["one_way_base_ms"] + 100
    n["one_way_jitter_ms"] = max(n["one_way_jitter_ms"], 25)
    n["query_rtt_ms"] = n["query_rtt_ms"] + 200
    n["control_rtt_ms"] = n["control_rtt_ms"] + 200
    return c


# scenario -> (patch function, window, channel or None, crash_mid_prob or None (keep), policies, {cell: (contract, channel)})
SCENARIOS = {
    "unbounded_zephyr": dict(w=INF, crash=0.0, policies=["EVICON"], catalog=[ZEPHYR],
                             cells={"ZEPHYR_PLAIN": ("pred-zephyr-plain-074", "queue"),
                                    "ZEPHYR_CUTOFF": ("pred-zephyr-cutoff-074", "queue"),
                                    "ZEPHYR_PLAIN_DIRECT": ("pred-zephyr-plain-074", "direct"),
                                    "ZEPHYR_F_DIRECT": ("pred-zephyr-f-075", "direct")}),
    "unbounded_minio": dict(w=INF, crash=0.0, policies=["EVICON"], catalog=[MINIO],
                            cells={"MINIO_PLAIN": ("meas-minio-s3-api-put-object-unconditional", "direct"),
                                   "MINIO_COND": ("meas-minio-s3-api-put-object-if-none-match", "direct")}),
    "noorphan": dict(w=6, crash=0.0, policies=["EVICON"], catalog=[],
                     cells={"K0": ("sim-k0", "queue"), "K0F": ("sim-k0+F", "queue"), "K0S": ("sim-k0+QS", "queue")}),
    "orphan": dict(w=6, crash=None, policies=["EVICON"], catalog=[],
                   cells={"K0": ("sim-k0", "queue"), "K0F": ("sim-k0+F", "queue"), "K0S": ("sim-k0+QS", "queue")}),
    "k2rep": dict(w=6, crash=None, policies=["EVICON", "RES_PERIODIC"], catalog=[],
                  cells={"K2": ("sim-k2", "queue"), "K3F": ("sim-k2+F", "queue")}),
    "mild": dict(w=6, crash=None, policies=["EVICON"], catalog=[], patch=_half,
                 cells={"K0": ("sim-k0", "queue"), "K0F": ("sim-k0+F", "queue"), "K0S": ("sim-k0+QS", "queue")}),
    "wan": dict(w=6, crash=None, policies=["EVICON"], catalog=[], patch=_wan,
                cells={"K0": ("sim-k0", "queue"), "K0F": ("sim-k0+F", "queue"), "K0S": ("sim-k0+QS", "queue")}),
    "release_timeout": dict(w=6, crash=None, policies=["B0_NAIVE_NEW_ID"], catalog=[], dispatch_timeout_ms=10000,
                            cells={"K0_QUEUE": ("sim-k0", "queue"), "K0F_QUEUE": ("sim-k0+F", "queue"),
                                   "K0_DIRECT": ("sim-k0", "direct")}),
}
# the plan's policy names for the twin's
POLICY_NAME = {"EVICON": "EVICON", "RES_PERIODIC": "RES_PERIODIC", "B0_NAIVE_NEW_ID": "RELEASE_ON_TIMEOUT"}

# id, family, scenario, policy, from, to, metric, test, margin, n: the decision rules of PREREG_review_077.md
HYPOTHESES = [
    ("U-1", "unbounded", "unbounded_minio", "EVICON", "MINIO_PLAIN", "MINIO_COND", "clean_fulfilled", "at_least", 5, 8),
    ("U-2", "unbounded", "unbounded_zephyr", "EVICON", "ZEPHYR_PLAIN_DIRECT", "ZEPHYR_F_DIRECT", "clean_fulfilled", "at_least", 5, 8),
    ("U-3", "unbounded", "unbounded_zephyr", "EVICON", "ZEPHYR_PLAIN", "ZEPHYR_CUTOFF", "clean_fulfilled_confirmed", "at_least", 5, 8),
    ("U-4", "unbounded", "unbounded_zephyr", "EVICON", "ZEPHYR_PLAIN", "ZEPHYR_CUTOFF", "clean_fulfilled", "equivalent", 3, 8),
    ("O-1", "orphan", "noorphan", "EVICON", "K0F", "K0S", "clean_fulfilled", "equivalent", 5, 8),
    ("O-2", "orphan", "noorphan", "EVICON", "K0", "K0F", "clean_fulfilled", "at_least", 10, 8),
    ("K-1", "k2", "k2rep", "EVICON", "K2", "K3F", "clean_fulfilled", "equivalent", 2, 5),
    ("K-2", "k2", "k2rep", "RES_PERIODIC", "K2", "K3F", "clean_fulfilled", "equivalent", 2, 5),
    ("V-1", "regime", "mild", "EVICON", "K0", "K0F", "clean_fulfilled", "at_least", 5, 8),
    ("V-2", "regime", "mild", "EVICON", "K0", "K0S", "clean_fulfilled", "at_least", 5, 8),
    ("WAN-1", "wan", "wan", "EVICON", "K0", "K0F", "clean_fulfilled", "at_least", 10, 8),
    ("WAN-2", "wan", "wan", "EVICON", "K0", "K0S", "clean_fulfilled", "at_least", 10, 8),
]


def config(base: dict, sc: dict) -> dict:
    c = sc["patch"](base) if sc.get("patch") else copy.deepcopy(base)
    c["authority"]["max_outstanding"] = sc["w"]
    c["authority"]["assumed_max_delay_ms"] = -1  # as every campaign: no bound on the network delay
    if sc.get("dispatch_timeout_ms"):
        c["authority"]["dispatch_timeout_ms"] = sc["dispatch_timeout_ms"]
    if sc["crash"] is not None:
        c["owner"]["crash_mid_prob"] = sc["crash"]
    c["policies"] = list(sc["policies"])
    c["sweep"] = [{"name": k, "patch": {"owner": {"contract": cid, "channel": ch}}} for k, (cid, ch) in sc["cells"].items()]
    return c


def run(des: str, base: dict, out: Path, name: str, episodes: int) -> Path:
    sc = SCENARIOS[name]
    d = out / "runs" / name
    if (d / "episodes.csv").exists():
        return d
    d.mkdir(parents=True, exist_ok=True)
    cat = Path(tempfile.mkdtemp(prefix="evicon-catalog-"))
    try:
        for f in (REPO / "experiments" / "contracts").glob("*.json"):
            shutil.copy2(f, cat / f.name)
        for src in sc["catalog"]:
            for f in src.glob("*.json"):
                shutil.copy2(f, cat / f.name)
        (d / "config.json").write_text(json.dumps(config(base, sc), indent=1))
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
            g[(e["regime"].rsplit("/", 1)[-1], POLICY_NAME.get(e["policy"], e["policy"]))][int(e["episode"])] = e
    return g


def summary(name: str, g: dict) -> list:
    rows = []
    for (cell, pol), eps in sorted(g.items()):
        es = list(eps.values())
        off = sum(float(e["offered"]) for e in es)

        def tot(k, es=es):
            return sum(float(e.get(k) or 0) for e in es)
        rows.append({"scenario": name, "cell": cell, "policy": pol, "episodes": len(es),
                     "clean_pct": round(100 * tot("clean_fulfilled") / off, 2),
                     "confirmed_pct": round(100 * tot("clean_fulfilled_confirmed") / off, 2),
                     "stranded_censored": round(tot("stranded_censored_unit_sec") / len(es), 1),
                     "safety_violations": int(tot(SAFETY)), "unsafe_releases": int(tot("unsafe_releases")),
                     "late_effects": int(tot("late_effects"))})
    return rows


def diffs(g: dict, h) -> list:
    a, b = g[(h[4], h[3])], g[(h[5], h[3])]
    m = h[6]
    return [100 * float(b[i][m]) / float(b[i]["offered"]) - 100 * float(a[i][m]) / float(a[i]["offered"]) for i in a if i in b]


def p_value(d: list, test: str, m: float) -> float:
    if test == "at_least":
        return one_sided(d, m, upper=False)
    return max(one_sided(d, -m, upper=False), one_sided(d, m, upper=True))  # equivalent (TOST)


def power(data: dict, resamples=2000) -> list:
    """Power of every rule at its planned n, Holm within each family, under the simulator's paired
    differences (resampled episodes)."""
    rows = []
    fams = collections.defaultdict(list)
    for h in HYPOTHESES:
        fams[h[1]].append(h)
    for fam, hs in fams.items():
        ds = [diffs(data[h[2]], h) for h in hs]
        rng = random.Random(77)
        met = [0] * len(hs)
        for _ in range(resamples):
            ps = {j: p_value([d[rng.randrange(len(d))] for _ in range(h[9])], h[7], h[8]) for j, (h, d) in enumerate(zip(hs, ds))}
            adj = holm(ps)
            for j in ps:
                met[j] += adj.get(j, 1.0) < 0.05
        for h, d, m in zip(hs, ds, met):
            rows.append({"id": h[0], "family": fam, "scenario": h[2], "policy": h[3], "from": h[4], "to": h[5], "metric": h[6],
                         "test": h[7], "margin": h[8], "n": h[9], "sim_mean": round(statistics.mean(d), 2),
                         "sim_sd": round(statistics.stdev(d), 2), "power": round(m / resamples, 2)})
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
    ap.add_argument("--out", default=str(REPO / "results" / "sim_review077"))
    ap.add_argument("--episodes", type=int, default=200)
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    base = json.loads(Path(a.base).read_text())
    data, rows = {}, []
    for name in SCENARIOS:
        data[name] = load(run(a.des, base, out, name, a.episodes))
        rows += summary(name, data[name])
    pw = power(data)
    write_csv(out / "summary.csv", rows)
    write_csv(out / "power.csv", pw)
    L = ["# Simulator prediction for PREREG_review_077.md (release 077, SIMULATION_ONLY)", "",
         f"{a.episodes} episodes per scenario; base `{Path(a.base).name}`; no assumed bound on the network delay.", "",
         "| scenario | cell | policy | clean % | confirmed % | stranded censored (unit*s / episode) | safety violations | unsafe releases | late effects |",
         "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['scenario']} | {r['cell']} | {r['policy']} | {r['clean_pct']} | {r['confirmed_pct']} | {r['stranded_censored']} | "
                 f"{r['safety_violations']} | {r['unsafe_releases']} | {r['late_effects']} |")
    L += ["", "## Decision rules of the plan under the simulator's paired differences (Holm within each family)", "",
          "| ID | family | comparison | metric | rule | n | sim mean (SD) | power |", "|---|---|---|---|---|---|---|---|"]
    for r in pw:
        L.append(f"| {r['id']} | {r['family']} | {r['policy']}: {r['from']} -> {r['to']} ({r['scenario']}) | {r['metric']} | "
                 f"{r['test']} {r['margin']} | {r['n']} | {r['sim_mean']:+.2f} ({r['sim_sd']:.2f}) | {r['power']} |")
    (out / "SIM_REVIEW077.md").write_text("\n".join(L) + "\n")
    print(f"sim review077: {len(rows)} groups -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
