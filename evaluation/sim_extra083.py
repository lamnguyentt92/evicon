#!/usr/bin/env python3
"""Simulator prediction for the experiments of PREREG_extra_083.md (release 083, SIMULATION_ONLY, written
before any of their cells runs).

The second set of review experiments (T1-T7). The script runs the testbed twin
(experiments/sim/iot_dosing_testbed.json, the twin of every earlier plan) in the settings of each run that
the simulator models, and sizes the plan's decision rules on its paired per-episode differences:

  t1_k2          QueryOnly (QUERY_ONLY: queries on a guard block, as the runtime's M1), QueryOnly with the
                 window-full trigger (QUERY_ONLY_W, the runtime's M1W) and EVICON on K2, queue channel, W = 6
  t1_tb          the same three on ThingsBoard's measured contract {Q_A, T(60 s)} without D_h
                 (experiments/sim/contracts083/sim-tb-083.json), queue channel (persistent RPC delivered
                 on reconnect until its expiry), W = 6
  t3_rt          the release-on-timeout baseline (B0_NAIVE_NEW_ID with the dispatch timeout set to the
                 release time) at 10, 30, 60 and 120 s, K0, queue and direct channel, W = 6
  t4_w6, t4_winf the plain firmware at QoS 0 (pred-zephyr-plain-074 on the direct channel: at most once,
                 a command published while the gateway's link is down is not queued) next to plain at QoS 1
                 (queue), the cutoff firmware (queue) and {F} (direct), W = 6 and unbounded; owner up
  t5_s_winf      EVICON with the bounded enumeration (gamma = 3) and with the greedy certificate
                 (authority.certificate greedy) on K0+Q_S and K0+F, queue channel, W unbounded
  t5_minio       the same on MinIO's measured contracts, direct channel, W unbounded
  t5_gamma       the enumeration's largest certificate gamma in {1, 2, 3, 4} and the greedy certificate (no
                 bound) on K0+F, queue channel, W = 6 and unbounded
  t5_theta       the resolution threshold theta = kappa_F / (lambda sum |v|) through the fence cost (0, 0.5,
                 5 = the campaigns', 50, 100, 500) on K0+F, queue channel, W = 6 (the testbed point: 100)
  t6_fw          the firmware's window sweep: plain at QoS 0 and the cutoff, W in {3, 6, 16, 32, unbounded}
  t6_tb          ThingsBoard's window sweep: EVICON and QUERY_ONLY_W, W in {3, 6, 16, unbounded}
  t7_<level>     the fault regime one factor at a time (fault windows 0.5 and 4 per minute; offline 5-15 s
                 and 40-120 s; the base regime) mapped onto the twin from the testbed's own schedules
                 (campaign.schedule: the share of time in each fault kind and the crash windows per second,
                 relative to the base regime, scale the twin's calibrated rates; offline periods uniform in
                 the level's range); K0, K0+F, K0+Q_S at W = 6 (queue) and K0, K0+F at W unbounded (direct)

The twin has no model of RELEASE_ON_READ_ANSWERED (T2), of ThingsBoard's own RPC queue beyond the owner
expiry, or of the testbed's search failures at an unbounded window (the twin's windows stay small enough
for the enumeration); the plan states what it predicts for those from the mechanism.

Every setting assumes no bound on the network delay (assumed_max_delay_ms -1, as every campaign).
Writes <out>/runs/, <out>/summary.csv, <out>/effects.csv, <out>/power.csv and <out>/SIM_EXTRA083.md.

    python3 evaluation/sim_extra083.py --des bin/evicon-des --out results/sim_extra083 [--episodes 200]
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
EXTRA = REPO / "experiments" / "sim" / "contracts083"
EMU = {"K0": "sim-k0", "K0F": "sim-k0+F", "K0S": "sim-k0+QS", "K2": "sim-k2"}
BASE_FAULTS = {"windows_per_min": 2.0, "window_s": [1.0, 4.0], "offline_s": [10.0, 40.0], "min_gap_s": 3.0,
               "mix": {"offline": 0.3, "drop_up": 0.2, "drop_down": 0.2, "delay_up": 0.1, "crash_after_effect": 0.2}}
# the T7 levels: one factor at a time around the base regime (the testbed configs' faults sections)
T7_LEVELS = {"base": {}, "rate05": {"windows_per_min": 0.5}, "rate4": {"windows_per_min": 4.0},
             "off5_15": {"offline_s": [5.0, 15.0]}, "off40_120": {"offline_s": [40.0, 120.0]}}


def realized(faults: dict, episodes: int = 60, devices: int = 4, duration: float = 300.0) -> dict:
    """Share of time in each fault kind, crash windows per second and the mean offline period, from the
    testbed's own seeded schedules (evaluation/campaign.py schedule, frozen; imported, never edited)."""
    import campaign as C  # noqa: E402
    cfg = {"seed": 8300, "devices": devices, "duration_s": duration, "faults": faults,
           "workload": {"rate_per_s": 0.5, "value_min": 1, "value_max": 5}}
    t = collections.Counter()
    crashes, offs = 0, []
    for ep in range(episodes):
        _, wins = C.schedule(cfg, ep)
        for w in wins:
            dur = min(w.dur, duration - w.t)
            t[w.kind] += dur
            crashes += w.kind == "crash_after_effect"
            if w.kind == "offline":
                offs.append(w.dur)
    total = episodes * devices * duration
    return {"offline": t["offline"] / total, "drop_up": t["drop_up"] / total, "drop_down": t["drop_down"] / total,
            "delay_up": t["delay_up"] / total, "crash_per_s": crashes / total, "offline_mean_s": statistics.mean(offs) if offs else 0.0}


def regime_patch(level: str):
    """A function base-twin -> twin at the T7 level (rates scaled by the realized shares relative to the base)."""
    f = copy.deepcopy(BASE_FAULTS)
    f.update(T7_LEVELS[level])
    b, r = realized(BASE_FAULTS), realized(f)

    def ratio(k):
        return r[k] / b[k] if b[k] > 0 else 1.0

    def patch(base: dict) -> dict:
        c = copy.deepcopy(base)
        n, o = c["network"], c["owner"]
        n["req_loss_prob"] = min(0.5, n["req_loss_prob"] * ratio("drop_up"))
        n["resp_loss_prob"] = min(0.5, n["resp_loss_prob"] * ratio("drop_down"))
        n["req_late_prob"] = min(0.5, n["req_late_prob"] * ratio("delay_up"))
        q = (ratio("drop_up") + ratio("drop_down")) / 2
        n["query_loss_prob"] = min(0.5, n["query_loss_prob"] * q)
        n["control_loss_prob"] = min(0.5, n["control_loss_prob"] * q)
        o["crash_mid_prob"] = min(0.5, o["crash_mid_prob"] * ratio("crash_per_s"))
        off = max(1e-3, r["offline"])
        lo, hi = f["offline_s"]
        o["offline_uniform_ms"] = [lo * 1000.0, hi * 1000.0]
        o["online_mean_ms"] = int(round(1000.0 * r["offline_mean_s"] * (1 - off) / off))
        o["offline_mean_ms"] = int(round(1000.0 * r["offline_mean_s"]))
        return c
    return patch, r


def _cells(names, channel):
    return {n: (EMU[n], channel) for n in names}


# scenario -> settings; "variants": run name -> (twin policy, authority patch); "cells": cell -> (contract, channel)
SCENARIOS = {
    "t1_k2": dict(w=6, variants={"EVICON": ("EVICON", {}), "M1": ("QUERY_ONLY", {}), "M1W": ("QUERY_ONLY_W", {})},
                  cells=_cells(["K2"], "queue")),
    "t1_tb": dict(w=6, crash=0.0, catalog=[EXTRA], variants={"EVICON": ("EVICON", {}), "M1": ("QUERY_ONLY", {}), "M1W": ("QUERY_ONLY_W", {})},
                  cells={"TB_RPC": ("sim-tb-083", "queue")}),
    "t3_rt": dict(w=6, variants={f"RELEASE_ON_TIMEOUT_{s}S": ("B0_NAIVE_NEW_ID", {"dispatch_timeout_ms": s * 1000}) for s in (10, 30, 60, 120)},
                  cells={"K0_QUEUE": ("sim-k0", "queue"), "K0_DIRECT": ("sim-k0", "direct")}),
    "t4_w6": dict(w=6, crash=0.0, catalog=[ZEPHYR], variants={"EVICON": ("EVICON", {})},
                  cells={"ZEPHYR_PLAIN_QOS1": ("pred-zephyr-plain-074", "queue"), "ZEPHYR_PLAIN_QOS0": ("pred-zephyr-plain-074", "direct"),
                         "ZEPHYR_CUTOFF": ("pred-zephyr-cutoff-074", "queue"), "ZEPHYR_PLAIN_DIRECT_QOS0": ("pred-zephyr-plain-074", "direct"),
                         "ZEPHYR_F_DIRECT": ("pred-zephyr-f-075", "direct")}),
    "t4_winf": dict(w=INF, crash=0.0, catalog=[ZEPHYR], variants={"EVICON": ("EVICON", {})},
                    cells={"ZEPHYR_PLAIN_QOS1": ("pred-zephyr-plain-074", "queue"), "ZEPHYR_PLAIN_QOS0": ("pred-zephyr-plain-074", "direct"),
                           "ZEPHYR_CUTOFF": ("pred-zephyr-cutoff-074", "queue"), "ZEPHYR_PLAIN_DIRECT_QOS0": ("pred-zephyr-plain-074", "direct"),
                           "ZEPHYR_F_DIRECT": ("pred-zephyr-f-075", "direct")}),
    "t5_s_winf": dict(w=INF, variants={"EVICON": ("EVICON", {}), "EVICON_GREEDY": ("EVICON", {"certificate": "greedy"})},
                      cells=_cells(["K0S", "K0F"], "queue")),
    "t5_minio": dict(w=INF, crash=0.0, catalog=[MINIO], variants={"EVICON": ("EVICON", {}), "EVICON_GREEDY": ("EVICON", {"certificate": "greedy"})},
                     cells={"MINIO_PLAIN": ("meas-minio-s3-api-put-object-unconditional", "direct"),
                            "MINIO_COND": ("meas-minio-s3-api-put-object-if-none-match", "direct")}),
    "t5_gamma_w6": dict(w=6, variants=dict({f"EVICON_G{g}": ("EVICON", {"certificate_depth": g}) for g in (1, 2, 3, 4)},
                                           EVICON_GREEDY=("EVICON", {"certificate": "greedy"})), cells=_cells(["K0F"], "queue")),
    "t5_gamma_winf": dict(w=INF, variants=dict({f"EVICON_G{g}": ("EVICON", {"certificate_depth": g}) for g in (1, 2, 3, 4)},
                                               EVICON_GREEDY=("EVICON", {"certificate": "greedy"})), cells=_cells(["K0F"], "queue")),
    "t5_theta": dict(w=6, variants=dict({"EVICON_THETA0": ("EVICON", {"resolve": {"mode": "IMMEDIATE", "query_first": True}})},
                                        **{f"EVICON_FC{fc:g}": ("EVICON", {"costs": {"fence_cost": fc}}) for fc in (0.5, 5, 50, 100, 500)}),
                     cells=_cells(["K0F"], "queue")),
    "t6_fw": dict(w=None, ws=[3, 6, 16, 32, INF], crash=0.0, catalog=[ZEPHYR], variants={"EVICON": ("EVICON", {})},
                  cells={"ZEPHYR_PLAIN_QOS0": ("pred-zephyr-plain-074", "direct"), "ZEPHYR_CUTOFF": ("pred-zephyr-cutoff-074", "queue")}),
    "t6_tb": dict(w=None, ws=[3, 6, 16, INF], crash=0.0, catalog=[EXTRA], variants={"EVICON": ("EVICON", {}), "M1W": ("QUERY_ONLY_W", {})},
                  cells={"TB_RPC": ("sim-tb-083", "queue")}),
}
for _lv in T7_LEVELS:
    SCENARIOS[f"t7_{_lv}_q6"] = dict(w=6, t7=_lv, variants={"EVICON": ("EVICON", {})}, cells=_cells(["K0", "K0F", "K0S"], "queue"))
    SCENARIOS[f"t7_{_lv}_dinf"] = dict(w=INF, t7=_lv, variants={"EVICON": ("EVICON", {})}, cells=_cells(["K0", "K0F"], "direct"))


def deep_update(d: dict, p: dict):
    for k, v in p.items():
        if isinstance(v, dict) and isinstance(d.get(k), dict):
            deep_update(d[k], v)
        else:
            d[k] = v


def config(base: dict, sc: dict, policy: str, apatch: dict, w: int) -> dict:
    c = copy.deepcopy(base)
    if sc.get("t7"):
        c = regime_patch(sc["t7"])[0](c)
    c["authority"]["max_outstanding"] = w
    c["authority"]["assumed_max_delay_ms"] = -1  # as every campaign: no bound on the network delay
    deep_update(c["authority"], copy.deepcopy(apatch))
    if sc.get("crash") is not None:
        c["owner"]["crash_mid_prob"] = sc["crash"]
    c["policies"] = [policy]
    c["sweep"] = [{"name": k, "patch": {"owner": {"contract": cid, "channel": ch}}} for k, (cid, ch) in sc["cells"].items()]
    return c


def run_one(des: str, base: dict, d: Path, sc: dict, policy: str, apatch: dict, w: int, episodes: int) -> Path:
    if (d / "episodes.csv").exists():
        return d
    d.mkdir(parents=True, exist_ok=True)
    cat = Path(tempfile.mkdtemp(prefix="evicon-catalog-"))
    try:
        for f in (REPO / "experiments" / "contracts").glob("*.json"):
            shutil.copy2(f, cat / f.name)
        for src in sc.get("catalog", []):
            for f in src.glob("*.json"):
                shutil.copy2(f, cat / f.name)
        (d / "config.json").write_text(json.dumps(config(base, sc, policy, apatch, w), indent=1))
        r = subprocess.run([des, "-catalog", str(cat), "-config", str(d / "config.json"), "-out", str(d), "-episodes", str(episodes)],
                           capture_output=True, text=True)
        (d / "run.log").write_text(r.stdout + r.stderr)
        if r.returncode != 0:
            raise SystemExit(f"evicon-des failed ({d}): {r.stderr[-600:]}")
    finally:
        shutil.rmtree(cat, ignore_errors=True)
    return d


def windows(sc):
    return sc["ws"] if sc.get("ws") else [sc["w"]]


def wlabel(w):
    return "inf" if w >= INF else str(w)


def run(des: str, base: dict, out: Path, name: str, episodes: int) -> dict:
    """{(cell, policy[, W]): {episode: row}} for one scenario."""
    sc = SCENARIOS[name]
    g = collections.defaultdict(dict)
    for w in windows(sc):
        for vname, (pol, apatch) in sc["variants"].items():
            sub = f"{vname}" + (f"_W{wlabel(w)}" if sc.get("ws") else "")
            d = run_one(des, base, out / "runs" / name / sub, sc, pol, apatch, w, episodes)
            with open(d / "episodes.csv", newline="") as fh:
                for e in csv.DictReader(fh):
                    cell = e["regime"].rsplit("/", 1)[-1]
                    key = (cell, vname + (f"@W{wlabel(w)}" if sc.get("ws") else ""))
                    g[key][int(e["episode"])] = e
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
                     "safety_violations": int(tot("safety_violations")), "violations_per_episode": round(tot("safety_violations") / len(es), 2),
                     "unsafe_releases": int(tot("unsafe_releases")), "certificate_errors": int(tot("certificate_errors")),
                     "queries": round(tot("queries") / len(es), 1), "fences": round(tot("fences") / len(es), 1),
                     "stranded_resolved": round(tot("stranded_resolved_unit_sec") / len(es), 1),
                     "late_effects": int(tot("late_effects"))})
    return rows


def pp(e, m):
    return 100 * float(e[m]) / float(e["offered"])


def diffs(g: dict, a: tuple, b: tuple, metric: str) -> list:
    A, B = g[a], g[b]
    return [pp(B[i], metric) - pp(A[i], metric) for i in sorted(A) if i in B]


# the plan's hypotheses with a simulator counterpart (the paired-difference rules that are confirmatory: power under the
# testbed's own spread >= 0.80 at the predicted mean, plan section 3; the others are estimands, in effects()):
# id, family, scenario, (cell, policy) from, (cell, policy) to, metric,
# test, margin, n. A difference of contrasts ("did", T6) gives ("did", (scenario, from, to) of a, (scenario, from, to) of b) as the
# scenario and None as from/to: per episode, contrast b minus contrast a (the twin's episodes share their schedules)
HYPOTHESES = [
    ("T1-3", "trigger-k2", "t1_k2", ("K2", "M1W"), ("K2", "EVICON"), "clean_fulfilled", "equivalent", 3.0, 8),
    ("T1-2", "trigger-tb", "t1_tb", ("TB_RPC", "M1"), ("TB_RPC", "M1W"), "clean_fulfilled", "at_least", 10.0, 6),
    ("T1-4", "trigger-tb", "t1_tb", ("TB_RPC", "M1W"), ("TB_RPC", "EVICON"), "clean_fulfilled", "equivalent", 5.0, 6),
    ("T4-1", "binding", "t4_w6", ("ZEPHYR_PLAIN_QOS0", "EVICON"), ("ZEPHYR_CUTOFF", "EVICON"), "clean_fulfilled", "at_least", 10.0, 8),
    ("T4-2", "binding", "t4_w6", ("ZEPHYR_PLAIN_QOS0", "EVICON"), ("ZEPHYR_CUTOFF", "EVICON"), "clean_fulfilled_confirmed", "at_least", 10.0, 8),
    ("T4-3", "binding", "t4_w6", ("ZEPHYR_PLAIN_DIRECT_QOS0", "EVICON"), ("ZEPHYR_F_DIRECT", "EVICON"), "clean_fulfilled", "at_least", 10.0, 8),
    ("T4-4", "binding", "t4_winf", ("ZEPHYR_PLAIN_DIRECT_QOS0", "EVICON"), ("ZEPHYR_F_DIRECT", "EVICON"), "clean_fulfilled", "at_least", 5.0, 8),
    ("T4-5", "binding", "t4_winf", ("ZEPHYR_PLAIN_QOS0", "EVICON"), ("ZEPHYR_CUTOFF", "EVICON"), "clean_fulfilled_confirmed", "at_least", 5.0, 8),
    ("T4-6", "binding", "t4_winf", ("ZEPHYR_PLAIN_QOS0", "EVICON"), ("ZEPHYR_CUTOFF", "EVICON"), "clean_fulfilled", "at_least", 5.0, 8),
    ("T5-1", "greedy", "t5_s_winf", ("K0F", "EVICON"), ("K0F", "EVICON_GREEDY"), "clean_fulfilled", "at_least", -2.0, 8),
    ("T5-2", "greedy", "t5_s_winf", ("K0S", "EVICON"), ("K0S", "EVICON_GREEDY"), "clean_fulfilled", "at_least", -2.0, 8),
    ("T5-3", "greedy", "t5_minio", ("MINIO_COND", "EVICON"), ("MINIO_COND", "EVICON_GREEDY"), "clean_fulfilled", "at_least", -2.0, 8),
    ("T5-4", "theta", "t5_theta", ("K0F", "EVICON_FC5"), ("K0F", "EVICON_FC100"), "clean_fulfilled", "equivalent", 5.0, 8),
    ("T6-1", "window", ("did", ("t6_fw", ("ZEPHYR_PLAIN_QOS0", "EVICON@W3"), ("ZEPHYR_CUTOFF", "EVICON@W3")),
                        ("t6_fw", ("ZEPHYR_PLAIN_QOS0", "EVICON@W32"), ("ZEPHYR_CUTOFF", "EVICON@W32"))), None, None, "clean_fulfilled", "at_most", 0.0, 8),
    ("T6-2", "window", ("did", ("t6_fw", ("ZEPHYR_PLAIN_QOS0", "EVICON@W6"), ("ZEPHYR_CUTOFF", "EVICON@W6")),
                        ("t6_fw", ("ZEPHYR_PLAIN_QOS0", "EVICON@Winf"), ("ZEPHYR_CUTOFF", "EVICON@Winf"))), None, None, "clean_fulfilled", "at_most", 0.0, 8),
]
T7_ID = {"rate05": "rate05", "rate4": "rate4", "off5_15": "off5", "off40_120": "off40"}
T7_ESTIMANDS = {"T7-rate05-qF", "T7-rate05-qS", "T7-rate05-dF", "T7-off5-dF"}  # power < 0.80 under the testbed's spread
for _lv, _id in T7_ID.items():
    HYPOTHESES += [h for h in ((f"T7-{_id}-qF", f"t7-{_id}", f"t7_{_lv}_q6", ("K0", "EVICON"), ("K0F", "EVICON"), "clean_fulfilled", "at_least", 0.0, 4),
                               (f"T7-{_id}-qS", f"t7-{_id}", f"t7_{_lv}_q6", ("K0", "EVICON"), ("K0S", "EVICON"), "clean_fulfilled", "at_least", 0.0, 4),
                               (f"T7-{_id}-dF", f"t7-{_id}", f"t7_{_lv}_dinf", ("K0", "EVICON"), ("K0F", "EVICON"), "clean_fulfilled", "at_least", 0.0, 4))
                   if h[0] not in T7_ESTIMANDS]
for _id in ("seedA", "seedB"):  # the base regime at a new seed: the twin's base prediction
    HYPOTHESES += [(f"T7-{_id}-qF", f"t7-{_id}", "t7_base_q6", ("K0", "EVICON"), ("K0F", "EVICON"), "clean_fulfilled", "at_least", 0.0, 4),
                   (f"T7-{_id}-qS", f"t7-{_id}", "t7_base_q6", ("K0", "EVICON"), ("K0S", "EVICON"), "clean_fulfilled", "at_least", 0.0, 4),
                   (f"T7-{_id}-dF", f"t7-{_id}", "t7_base_dinf", ("K0", "EVICON"), ("K0F", "EVICON"), "clean_fulfilled", "at_least", 0.0, 4)]


def h_diffs(data: dict, h) -> list:
    sc = h[2]
    if isinstance(sc, tuple) and sc[0] == "did":
        (sa, aa, ab), (sb, ba, bb) = sc[1], sc[2]
        da = dict(zip(sorted(data[sa][aa]), diffs(data[sa], aa, ab, h[5])))
        db = dict(zip(sorted(data[sb][ba]), diffs(data[sb], ba, bb, h[5])))
        return [db[e] - da[e] for e in sorted(set(da) & set(db))]
    return diffs(data[sc], h[3], h[4], h[5])


def h_label(h) -> tuple:
    sc = h[2]
    if isinstance(sc, tuple):
        (sa, aa, ab), (sb, ba, bb) = sc[1], sc[2]
        return (f"did {sa}/{sb}", f"{'/'.join(aa)} -> {'/'.join(ab)}", f"{'/'.join(ba)} -> {'/'.join(bb)}")
    return sc, "/".join(h[3]), "/".join(h[4])


def p_value(d: list, test: str, m: float) -> float:
    if test == "at_least":
        return one_sided(d, m, upper=False)
    if test == "at_most":
        return one_sided(d, m, upper=True)
    return max(one_sided(d, -m, upper=False), one_sided(d, m, upper=True))  # equivalent (TOST)


def power(data: dict, resamples=2000) -> list:
    rows = []
    fams = collections.defaultdict(list)
    for h in HYPOTHESES:
        fams[h[1]].append(h)
    for fam, hs in fams.items():
        ds = [h_diffs(data, h) for h in hs]
        rng = random.Random(83)
        met = [0] * len(hs)
        for _ in range(resamples):
            ps = {j: p_value([d[rng.randrange(len(d))] for _ in range(h[8])], h[6], h[7]) for j, (h, d) in enumerate(zip(hs, ds))}
            adj = holm(ps)
            for j in ps:
                met[j] += adj.get(j, 1.0) < 0.05
        for h, d, m in zip(hs, ds, met):
            sc, fr, to = h_label(h)
            rows.append({"id": h[0], "family": fam, "scenario": sc, "from": fr, "to": to, "metric": h[5],
                         "test": h[6], "margin": h[7], "n": h[8], "sim_mean": round(statistics.mean(d), 2),
                         "sim_sd": round(statistics.stdev(d), 2), "power": round(m / resamples, 2)})
    return rows


def effects(data: dict) -> list:
    """The effect sizes the plan's estimands and direction rules use (mean and SD of the per-episode differences)."""
    rows = []

    def add(sc, a, b, metric="clean_fulfilled", label=""):
        g = data[sc]
        if a in g and b in g:
            d = diffs(g, a, b, metric)
            rows.append({"scenario": sc, "from": "/".join(a), "to": "/".join(b), "metric": metric, "n": len(d),
                         "mean_pp": round(statistics.mean(d), 2), "sd_pp": round(statistics.stdev(d), 2), "label": label})
    add("t1_k2", ("K2", "M1"), ("K2", "M1W"), label="T1 K2 M1 -> M1W (estimand, was T1-1)")
    add("t1_k2", ("K2", "M1"), ("K2", "EVICON"), label="T1 K2 M1 -> EVICON")
    add("t1_tb", ("TB_RPC", "M1"), ("TB_RPC", "EVICON"), label="T1 ThingsBoard M1 -> EVICON")
    for lv in T7_LEVELS:
        for to in ("K0F", "K0S"):
            add(f"t7_{lv}_q6", ("K0", "EVICON"), (to, "EVICON"), label=f"T7 queue W=6 {lv}")
        add(f"t7_{lv}_dinf", ("K0", "EVICON"), ("K0F", "EVICON"), label=f"T7 direct W=inf {lv}")
    for w in (3, 6, 16, 32, INF):
        add("t6_fw", ("ZEPHYR_PLAIN_QOS0", f"EVICON@W{wlabel(w)}"), ("ZEPHYR_CUTOFF", f"EVICON@W{wlabel(w)}"), label=f"T6 firmware W={wlabel(w)}")
    for w in (3, 6, 16, INF):
        add("t6_tb", ("TB_RPC", f"M1W@W{wlabel(w)}"), ("TB_RPC", f"EVICON@W{wlabel(w)}"), label=f"T6 ThingsBoard W={wlabel(w)}")
    g = data["t6_tb"]
    a, b = (("TB_RPC", "M1W@W3"), ("TB_RPC", "EVICON@W3")), (("TB_RPC", "M1W@Winf"), ("TB_RPC", "EVICON@Winf"))
    if all(x in g for x in a + b):  # the estimand that was T6-3: the gap at W unbounded minus at W = 3
        d = [y - x for x, y in zip(diffs(g, *a, "clean_fulfilled"), diffs(g, *b, "clean_fulfilled"))]
        rows.append({"scenario": "t6_tb", "from": "gap at W=3", "to": "gap at W=inf", "metric": "clean_fulfilled", "n": len(d),
                     "mean_pp": round(statistics.mean(d), 2), "sd_pp": round(statistics.stdev(d), 2),
                     "label": "T6 ThingsBoard gap W=inf minus W=3 (estimand, was T6-3)"})
    for s in (30, 60, 120):
        for ch in ("K0_QUEUE", "K0_DIRECT"):
            add("t3_rt", (ch, "RELEASE_ON_TIMEOUT_10S"), (ch, f"RELEASE_ON_TIMEOUT_{s}S"), label=f"T3 {ch} 10 s -> {s} s")
    for g in (1, 2, 4):
        add("t5_gamma_w6", ("K0F", "EVICON_G3"), ("K0F", f"EVICON_G{g}"), label=f"T5 gamma 3 -> {g} (W=6)")
        add("t5_gamma_winf", ("K0F", "EVICON_G3"), ("K0F", f"EVICON_G{g}"), label=f"T5 gamma 3 -> {g} (W=inf)")
    add("t5_gamma_w6", ("K0F", "EVICON_G3"), ("K0F", "EVICON_GREEDY"), label="T5 gamma 3 -> greedy (W=6)")
    add("t5_gamma_winf", ("K0F", "EVICON_G3"), ("K0F", "EVICON_GREEDY"), label="T5 gamma 3 -> greedy (W=inf)")
    for fc in ("THETA0", "FC0.5", "FC50", "FC100", "FC500"):
        add("t5_theta", ("K0F", "EVICON_FC5"), ("K0F", f"EVICON_{fc}"), label=f"T5 fence cost 5 -> {fc}")
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
    ap.add_argument("--out", default=str(REPO / "results" / "sim_extra083"))
    ap.add_argument("--episodes", type=int, default=200)
    ap.add_argument("--only", default="", help="run only scenarios whose name contains this")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    base = json.loads(Path(a.base).read_text())
    data, rows = {}, []
    for name in SCENARIOS:
        if a.only and a.only not in name:
            continue
        data[name] = run(a.des, base, out, name, a.episodes)
        rows += summary(name, data[name])
    write_csv(out / "summary.csv", rows)
    ef = effects(data) if not a.only else []
    pw = power(data) if not a.only else []
    if ef:
        write_csv(out / "effects.csv", ef)
    if pw:
        write_csv(out / "power.csv", pw)
    regimes = {lv: realized(dict(BASE_FAULTS, **T7_LEVELS[lv])) for lv in T7_LEVELS}
    L = ["# Simulator prediction for PREREG_extra_083.md (release 083, SIMULATION_ONLY)", "",
         f"{a.episodes} episodes per scenario; base `{Path(a.base).name}`; no assumed bound on the network delay.", "",
         "## Levels", "",
         "| scenario | cell | policy | clean % | confirmed % | safety violations (per episode) | unsafe releases | certificate errors | queries / ep | fences / ep | late effects |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['scenario']} | {r['cell']} | {r['policy']} | {r['clean_pct']} | {r['confirmed_pct']} | {r['safety_violations']} "
                 f"({r['violations_per_episode']}) | {r['unsafe_releases']} | {r['certificate_errors']} | {r['queries']} | {r['fences']} | {r['late_effects']} |")
    if ef:
        L += ["", "## Effects (per-episode paired differences, clean fulfilment in pp)", "", "| effect | from -> to | n | mean | SD |", "|---|---|---|---|---|"]
        for r in ef:
            L.append(f"| {r['label']} | {r['from']} -> {r['to']} | {r['n']} | {r['mean_pp']:+.2f} | {r['sd_pp']:.2f} |")
    if pw:
        L += ["", "## Decision rules of the plan under the simulator's paired differences (Holm within each family)", "",
              "| ID | family | comparison | metric | rule | n | sim mean (SD) | power |", "|---|---|---|---|---|---|---|---|"]
        for r in pw:
            L.append(f"| {r['id']} | {r['family']} | {r['from']} -> {r['to']} ({r['scenario']}) | {r['metric']} | "
                     f"{r['test']} {r['margin']} | {r['n']} | {r['sim_mean']:+.2f} ({r['sim_sd']:.2f}) | {r['power']} |")
    L += ["", "## T7: the testbed's fault levels as realized by its schedules (campaign.schedule, 60 episodes x 4 devices)", "",
          "| level | offline share | drop_up | drop_down | delay_up | crash windows / device-hour | mean offline (s) |", "|---|---|---|---|---|---|---|"]
    for lv, r in regimes.items():
        L.append(f"| {lv} | {r['offline']:.3f} | {r['drop_up']:.3f} | {r['drop_down']:.3f} | {r['delay_up']:.3f} | {3600 * r['crash_per_s']:.1f} | {r['offline_mean_s']:.1f} |")
    (out / "SIM_EXTRA083.md").write_text("\n".join(L) + "\n")
    print(f"sim extra083: {len(rows)} groups -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
