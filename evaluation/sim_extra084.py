#!/usr/bin/env python3
"""Simulator prediction for the experiments of PREREG_extra_084.md (release 084, SIMULATION_ONLY, written
before any of their cells runs).

The external review of 31 October 2026 (M3, M5) asked for two controls the release-084 simulator models:

  a_s6, a_sinf   per-identity fence intake (rule R5', the twin's authority.fence_uncertain "per_identity") against
                 the all-or-nothing intake (rule R5, "abort", the runtime's default and the twin's since release
                 066) and against the stop, on the stop experiment's setting: the emulated owner with crash orphans
                 (the twin's crash_mid_prob), queue channel, K0+F and K0+Q_S, W = 6 and unbounded
  c_fw_w6, c_fw_winf      full periodic reconciliation (PERIODIC_FULL: every second every aged ambiguous command,
                 query first, then stop or fence under EVICON's threshold rule; no certificate, no blocked-request
                 trigger) against EVICON on the firmware's cutoff contract (pred-zephyr-cutoff-074: D_h, Q_A, F),
                 queue channel, owner up (no crash), W = 6 and unbounded
  c_minio_w6, c_minio_winf  the same on MinIO's conditional PUT (meas-minio-cond-070: D_h, Q_A, Q_S), direct
                 channel, owner up, W = 6 and unbounded

The twin is experiments/sim/iot_dosing_testbed.json, as in every earlier plan; every setting assumes no bound on
the network delay (assumed_max_delay_ms -1, as every campaign). The functions that build and run one twin setting
are those of evaluation/sim_extra083.py (frozen by PREREG_extra_083.md; imported, never edited).
Writes <out>/runs/, <out>/summary.csv, <out>/effects.csv, <out>/power.csv and <out>/SIM_EXTRA084.md.

    python3 evaluation/sim_extra084.py --des bin/evicon-des --out results/sim_extra084 [--episodes 200]
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import random
import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "evaluation"))
import sim_extra083 as E83  # noqa: E402  (frozen by PREREG_extra_083.md; imported, never edited)
from pairs_report import holm  # noqa: E402  (frozen by earlier plans; imported, never edited)

INF = E83.INF
ZEPHYR, MINIO = E83.ZEPHYR, E83.MINIO
FI = {"EVICON": ("EVICON", {"fence_uncertain": "abort"}), "EVICON_FI": ("EVICON", {"fence_uncertain": "per_identity"})}
PF = {"EVICON": ("EVICON", {}), "PERIODIC_FULL": ("PERIODIC_FULL", {})}
FW = {"ZEPHYR_CUTOFF": ("pred-zephyr-cutoff-074", "queue")}
MN = {"MINIO_COND": ("meas-minio-s3-api-put-object-if-none-match", "direct")}
# The twin's crash orphans: the base twin's crash_mid_prob (0.005) gives about 0.5 owner crashes per episode, the
# testbed's stop experiment about 8 crash windows per episode (campaign.schedule, seed 20261017, episodes 0-11: 7.8),
# and the base twin's K0F -> K0S gap at W = 6 is +4.4 pp (a_s6_base, 200 episodes) against the testbed's +20.7 pp
# (S, episodes 0-7: K0F 59.9, K0S 80.6 %). The (a) prediction therefore uses the twin with its crash probability fitted to that gap (calibrate():
# 0.020 -> +15.6, 0.025 -> +18.1, 0.030 -> +22.8, 0.035 -> +25.6 pp at 100 episodes; 0.028 is the closest to +20.7),
# fixed here before any cell of the plan; the base twin's prediction is reported next to it (a_s6_base, a_sinf_base).
CAL_CRASH = 0.028
CAL_GRID = (0.02, 0.025, 0.03, 0.035)
TESTBED_GAP_S6 = 20.7  # S (expS-w6/20261022T161003), episodes 0-7, K0F -> K0S clean pp (081 final report, cells.csv)
SCENARIOS = {
    "a_s6": dict(w=6, crash=CAL_CRASH, variants=FI, cells=E83._cells(["K0F", "K0S"], "queue")),
    "a_sinf": dict(w=INF, crash=CAL_CRASH, variants=FI, cells=E83._cells(["K0F", "K0S"], "queue")),
    "a_s6_base": dict(w=6, variants=FI, cells=E83._cells(["K0F", "K0S"], "queue")),
    "a_sinf_base": dict(w=INF, variants=FI, cells=E83._cells(["K0F", "K0S"], "queue")),
    "c_fw_w6": dict(w=6, crash=0.0, catalog=[ZEPHYR], variants=PF, cells=FW),
    "c_fw_winf": dict(w=INF, crash=0.0, catalog=[ZEPHYR], variants=PF, cells=FW),
    "c_minio_w6": dict(w=6, crash=0.0, catalog=[MINIO], variants=PF, cells=MN),
    "c_minio_winf": dict(w=INF, crash=0.0, catalog=[MINIO], variants=PF, cells=MN),
}


def run(des: str, base: dict, out: Path, name: str, episodes: int) -> dict:
    """{(cell, policy): {episode: row}} for one scenario."""
    sc = SCENARIOS[name]
    g = collections.defaultdict(dict)
    for vname, (pol, apatch) in sc["variants"].items():
        d = E83.run_one(des, base, out / "runs" / name / vname, sc, pol, apatch, sc["w"], episodes)
        with open(d / "episodes.csv", newline="") as fh:
            for e in csv.DictReader(fh):
                g[(e["regime"].rsplit("/", 1)[-1], vname)][int(e["episode"])] = e
    return g


def summary(name: str, g: dict) -> list:
    rows = E83.summary(name, g)
    for r in rows:
        es = list(g[(r["cell"], r["policy"])].values())
        n = len(es)

        def per(k, es=es, n=n):
            return round(sum(float(e.get(k) or 0) for e in es) / n, 2)
        r.update({"stops": per("stops"), "fence_aborts": per("fence_aborts"), "owner_calls": round(per("queries") + per("stops") + per("fences"), 1),
                  "unresolved_at_end": per("unresolved_at_end") if "unresolved_at_end" in es[0] else "",
                  "owner_crashes": per("owner_crashes")})
    return rows


# id, family, scenario, (cell, policy) from, (cell, policy) to, metric, test, margin, n: the plan's paired-difference hypotheses after
# its 0.80 rule (power under the testbed's own spread at the predicted mean, power_extra084_testbed.py); the draft's FI-2 (stop -> R5'
# equivalent 5, power 0.05) and FI-3 (W unbounded R5 -> R5' at_least 0, n 4, power 0.75) are estimands, their means in effects.csv
DRAFT_ESTIMANDS = [
    ("FI-2", "fi-w6", "a_s6", ("K0S", "EVICON"), ("K0F", "EVICON_FI"), "clean_fulfilled", "equivalent", 5.0, 8),
    ("FI-3", "fi-winf", "a_sinf", ("K0F", "EVICON"), ("K0F", "EVICON_FI"), "clean_fulfilled", "at_least", 0.0, 4),
]
HYPOTHESES = [
    ("FI-1", "fi-w6", "a_s6", ("K0F", "EVICON"), ("K0F", "EVICON_FI"), "clean_fulfilled", "at_least", 10.0, 8),
    ("PF-1", "pf-fw", "c_fw_w6", ("ZEPHYR_CUTOFF", "EVICON"), ("ZEPHYR_CUTOFF", "PERIODIC_FULL"), "clean_fulfilled", "equivalent", 3.0, 8),
    ("PF-2", "pf-fw", "c_fw_winf", ("ZEPHYR_CUTOFF", "EVICON"), ("ZEPHYR_CUTOFF", "PERIODIC_FULL"), "clean_fulfilled", "equivalent", 3.0, 8),
    ("PF-3", "pf-minio", "c_minio_w6", ("MINIO_COND", "EVICON"), ("MINIO_COND", "PERIODIC_FULL"), "clean_fulfilled", "equivalent", 3.0, 8),
    ("PF-4", "pf-minio", "c_minio_winf", ("MINIO_COND", "EVICON"), ("MINIO_COND", "PERIODIC_FULL"), "clean_fulfilled", "equivalent", 3.0, 8),
]


def power(data: dict, resamples=2000) -> list:
    rows = []
    fams = collections.defaultdict(list)
    for h in HYPOTHESES:
        fams[h[1]].append(h)
    for fam, hs in fams.items():
        ds = [E83.diffs(data[h[2]], h[3], h[4], h[5]) for h in hs]
        rng = random.Random(84)
        met = [0] * len(hs)
        for _ in range(resamples):
            ps = {j: E83.p_value([d[rng.randrange(len(d))] for _ in range(h[8])], h[6], h[7]) for j, (h, d) in enumerate(zip(hs, ds))}
            adj = holm(ps)
            for j in ps:
                met[j] += adj.get(j, 1.0) < 0.05
        for h, d, m in zip(hs, ds, met):
            rows.append({"id": h[0], "family": fam, "scenario": h[2], "from": "/".join(h[3]), "to": "/".join(h[4]), "metric": h[5],
                         "test": h[6], "margin": h[7], "n": h[8], "sim_mean": round(statistics.mean(d), 2),
                         "sim_sd": round(statistics.stdev(d), 2), "power": round(m / resamples, 2)})
    return rows


def effects(data: dict) -> list:
    rows = []

    def add(sc, a, b, metric="clean_fulfilled", label=""):
        g = data.get(sc, {})
        if a in g and b in g:
            d = E83.diffs(g, a, b, metric)
            rows.append({"scenario": sc, "from": "/".join(a), "to": "/".join(b), "metric": metric, "n": len(d),
                         "mean_pp": round(statistics.mean(d), 2), "sd_pp": round(statistics.stdev(d), 2), "label": label})
    for sc, w in (("a_s6", "W=6"), ("a_sinf", "W=inf"), ("a_s6_base", "W=6, base twin"), ("a_sinf_base", "W=inf, base twin")):
        add(sc, ("K0F", "EVICON"), ("K0F", "EVICON_FI"), label=f"(a) {w} K0F R5 -> R5'")
        add(sc, ("K0S", "EVICON"), ("K0F", "EVICON_FI"), label=f"(a) {w} stop -> K0F R5'")
        add(sc, ("K0F", "EVICON"), ("K0S", "EVICON"), label=f"(a) {w} K0F R5 -> stop (the stop experiment's contrast)")
        add(sc, ("K0F", "EVICON"), ("K0F", "EVICON_FI"), "clean_fulfilled_confirmed", label=f"(a) {w} K0F R5 -> R5' confirmed")
    for sc, cell, w in (("c_fw_w6", "ZEPHYR_CUTOFF", "W=6"), ("c_fw_winf", "ZEPHYR_CUTOFF", "W=inf"),
                        ("c_minio_w6", "MINIO_COND", "W=6"), ("c_minio_winf", "MINIO_COND", "W=inf")):
        add(sc, (cell, "EVICON"), (cell, "PERIODIC_FULL"), label=f"(c) {cell} {w} EVICON -> PERIODIC_FULL")
        add(sc, (cell, "EVICON"), (cell, "PERIODIC_FULL"), "clean_fulfilled_confirmed", label=f"(c) {cell} {w} EVICON -> PERIODIC_FULL confirmed")
    return rows


def calibrate(des: str, base: dict, out: Path, episodes: int = 100) -> list:
    """The twin's K0F (R5) -> K0S gap at W = 6 for each crash probability of CAL_GRID (the fit of CAL_CRASH)."""
    rows = []
    for cp in CAL_GRID:
        sc = dict(w=6, crash=cp, variants={"EVICON": FI["EVICON"]}, cells=E83._cells(["K0F", "K0S"], "queue"))
        SCENARIOS["_cal"] = sc
        g = run(des, base, out / "calibrate" / f"crash{cp}", "_cal", episodes)
        d = E83.diffs(g, ("K0F", "EVICON"), ("K0S", "EVICON"), "clean_fulfilled")
        rows.append({"crash_mid_prob": cp, "episodes": episodes, "gap_k0f_k0s_pp": round(statistics.mean(d), 2), "testbed_gap_pp": TESTBED_GAP_S6})
    SCENARIOS.pop("_cal", None)
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--des", default=str(REPO / "bin" / "evicon-des"))
    ap.add_argument("--base", default=str(REPO / "experiments" / "sim" / "iot_dosing_testbed.json"))
    ap.add_argument("--out", default=str(REPO / "results" / "sim_extra084"))
    ap.add_argument("--episodes", type=int, default=200)
    ap.add_argument("--only", default="", help="run only scenarios whose name contains this")
    ap.add_argument("--calibrate", action="store_true", help="also print the fit of CAL_CRASH (100 episodes per grid point)")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    base = json.loads(Path(a.base).read_text())
    data, rows = {}, []
    for name in SCENARIOS:
        if name.startswith("_"):
            continue
        if a.only and a.only not in name:
            continue
        data[name] = run(a.des, base, out, name, a.episodes)
        rows += summary(name, data[name])
    E83.write_csv(out / "summary.csv", rows)
    cal = calibrate(a.des, base, out) if a.calibrate else []
    if cal:
        E83.write_csv(out / "calibration.csv", cal)
    ef = effects(data)
    pw = power(data) if not a.only else []
    if ef:
        E83.write_csv(out / "effects.csv", ef)
    if pw:
        E83.write_csv(out / "power.csv", pw)
    L = ["# Simulator prediction for PREREG_extra_084.md (release 084, SIMULATION_ONLY)", "",
         f"{a.episodes} episodes per scenario; base `{Path(a.base).name}`; no assumed bound on the network delay.", "",
         "## Levels", "",
         "| scenario | cell | policy | clean % | confirmed % | safety violations | queries / ep | stops / ep | fences / ep | fence aborts / ep | owner calls / ep | owner crashes / ep |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['scenario']} | {r['cell']} | {r['policy']} | {r['clean_pct']} | {r['confirmed_pct']} | {r['safety_violations']} | "
                 f"{r['queries']} | {r['stops']} | {r['fences']} | {r['fence_aborts']} | {r['owner_calls']} | {r['owner_crashes']} |")
    if ef:
        L += ["", "## Effects (per-episode paired differences, pp of offered)", "", "| effect | from -> to | metric | n | mean | SD |", "|---|---|---|---|---|---|"]
        for r in ef:
            L.append(f"| {r['label']} | {r['from']} -> {r['to']} | {r['metric']} | {r['n']} | {r['mean_pp']:+.2f} | {r['sd_pp']:.2f} |")
    if pw:
        L += ["", "## The plan's paired-difference hypotheses under the simulator's paired differences (Holm within each family)", "",
              "| ID | family | comparison | rule | n | sim mean (SD) | power |", "|---|---|---|---|---|---|---|"]
        for r in pw:
            L.append(f"| {r['id']} | {r['family']} | {r['from']} -> {r['to']} ({r['scenario']}) | {r['test']} {r['margin']} | {r['n']} | "
                     f"{r['sim_mean']:+.2f} ({r['sim_sd']:.2f}) | {r['power']} |")
    if cal:
        L += ["", f"## Fit of the twin's crash probability (CAL_CRASH = {CAL_CRASH})", "", "| crash_mid_prob | K0F (R5) -> K0S, twin | testbed (S, episodes 0-7) |", "|---|---|---|"]
        for r in cal:
            L.append(f"| {r['crash_mid_prob']} | {r['gap_k0f_k0s_pp']:+.2f} | {r['testbed_gap_pp']:+.1f} |")
    (out / "SIM_EXTRA084.md").write_text("\n".join(L) + "\n")
    print(f"sim extra084: {len(rows)} groups -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
