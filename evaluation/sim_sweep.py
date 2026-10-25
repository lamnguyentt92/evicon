#!/usr/bin/env python3
"""Simulator sweep behind the release-072 plans (SIMULATION_ONLY, exploratory).

Value of a contract upgrade as a function of the window W of unresolved commands, the owner channel
(queue: offline requests delivered late; direct: lost), capacity pressure, request loss and crash
orphans, around the testbed twin experiments/sim/iot_dosing_testbed.json. Also the sleepy-device
point of experiment L, and the power of the 072 hypotheses under the simulator's per-episode paired
differences. The numbers quoted in sections 1 and 5 of PREREG_expW_072.md, PREREG_expS_072.md and
PLAN_expL_072.md come from this script (200 episodes, release 072 simulator).

    python3 evaluation/sim_sweep.py --des bin/evicon-des --out results/sim_sweep [--episodes 200]

Writes <out>/runs/<point>/ (config, episodes.csv), <out>/summary.csv (one row per point, contract and
policy: clean and confirmed fulfilment in % of offered, stranded capacity-time, stops, fences, safety
violations, late effects), <out>/power.csv and <out>/SWEEP.md. A point already run is not re-run.
"""
from __future__ import annotations

import argparse
import collections
import copy
import csv
import json
import random
import statistics
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "evaluation"))
from pairs_report import holm, one_sided  # noqa: E402

CONTRACTS = ["sim-k0", "sim-k0+F", "sim-k0+QS", "sim-k2", "sim-k2+F"]
POLICIES = ["EVICON", "RES_PERIODIC", "ORACLE"]
INF = 1000000


def points():
    pts = [dict(w=w, channel=ch) for ch in ("queue", "direct") for w in (1, 2, 4, 6, 16, INF)]
    pts += [dict(w=w, pressure=p) for p in (0.8, 1.0, 2.0) for w in (6, INF)]
    pts += [dict(w=w, loss=x) for x in (0.05, 0.10) for w in (6, INF)]
    pts += [dict(w=w, crash=x) for x in (0.0, 0.02) for w in (6, INF)]
    pts.append(dict(w=6, offline_mean_ms=120000, contracts=["sim-k2", "sim-k3-expiry"], policies=["EVICON", "ORACLE"]))  # experiment L
    return pts


def tag(pt: dict) -> str:
    return "_".join(f"{k}{v}" for k, v in sorted(pt.items()) if k not in ("contracts", "policies"))


def config(base: dict, pt: dict) -> dict:
    c = copy.deepcopy(base)
    c["authority"]["max_outstanding"] = pt["w"]
    c["workload"]["component_max"] = [int(round(345 * 1.3 / pt.get("pressure", 1.3)))]  # demand / bound
    c["owner"]["channel"] = pt.get("channel", "queue")
    if "loss" in pt:
        c["network"]["req_loss_prob"] = pt["loss"]
    if "crash" in pt:
        c["owner"]["crash_mid_prob"] = pt["crash"]
    if "offline_mean_ms" in pt:
        c["owner"]["offline_mean_ms"] = pt["offline_mean_ms"]
    c["policies"] = pt.get("policies", POLICIES)
    c["sweep"] = [{"name": k, "patch": {"owner": {"contract": k}}} for k in pt.get("contracts", CONTRACTS)]
    return c


def run(des: str, base: dict, out: Path, episodes: int) -> list:
    rows = []
    for pt in points():
        d = out / "runs" / tag(pt)
        if not (d / "episodes.csv").exists():
            d.mkdir(parents=True, exist_ok=True)
            (d / "config.json").write_text(json.dumps(config(base, pt), indent=1))
            r = subprocess.run([des, "-catalog", str(REPO / "experiments" / "contracts"), "-config", str(d / "config.json"),
                                "-out", str(d), "-episodes", str(episodes)], capture_output=True, text=True)
            (d / "run.log").write_text(r.stdout + r.stderr)
            if r.returncode != 0:
                raise SystemExit(f"{tag(pt)}: evicon-des failed: {r.stderr[-400:]}")
        groups = collections.defaultdict(list)
        with open(d / "episodes.csv", newline="") as fh:
            for e in csv.DictReader(fh):
                groups[(e["contract"], e["policy"])].append(e)
        for (ct, pol), es in sorted(groups.items()):
            off = sum(float(e["offered"]) for e in es)

            def tot(k):
                return sum(float(e[k]) for e in es)
            rows.append({"point": tag(pt), "w": pt["w"], "channel": pt.get("channel", "queue"), "pressure": pt.get("pressure", 1.3),
                         "loss": pt.get("loss", base["network"]["req_loss_prob"]), "crash": pt.get("crash", base["owner"]["crash_mid_prob"]),
                         "offline_mean_ms": pt.get("offline_mean_ms", base["owner"]["offline_mean_ms"]), "contract": ct, "policy": pol,
                         "episodes": len(es), "clean_pct": round(100 * tot("clean_fulfilled") / off, 2),
                         "confirmed_pct": round(100 * tot("clean_fulfilled_confirmed") / off, 2),
                         "stranded_resolved": round(tot("stranded_resolved_unit_sec") / len(es), 1),
                         "stranded_censored": round(tot("stranded_censored_unit_sec") / len(es), 1),
                         "stops": round(tot("stops") / len(es), 1), "fences": round(tot("fences") / len(es), 1),
                         "safety_violations": int(tot("safety_violations")), "late_effects": int(tot("late_effects"))})
    return rows


# id, point tag, policy, from, to, metric, test, margin (the decision rules of the 072 plans)
HYPOTHESES = [
    ("H-W1", "channelqueue_w6", "EVICON", "sim-k0", "sim-k0+F", "clean_fulfilled", "at_least", 10),
    ("H-W1", "channelqueue_w6", "RES_PERIODIC", "sim-k0", "sim-k0+F", "clean_fulfilled", "at_least", 10),
    ("H-W2", f"channelqueue_w{INF}", "EVICON", "sim-k0", "sim-k0+F", "clean_fulfilled", "equivalent", 3),
    ("H-W2", f"channelqueue_w{INF}", "RES_PERIODIC", "sim-k0", "sim-k0+F", "clean_fulfilled", "equivalent", 3),
    ("H-W3", f"channelqueue_w{INF}", "EVICON", "sim-k0", "sim-k0+F", "clean_fulfilled_confirmed", "at_least", 5),
    ("H-W3", f"channelqueue_w{INF}", "RES_PERIODIC", "sim-k0", "sim-k0+F", "clean_fulfilled_confirmed", "at_least", 5),
    ("H-W4", f"channeldirect_w{INF}", "EVICON", "sim-k0", "sim-k0+F", "clean_fulfilled", "at_least", 5),
    ("H-W5", "channeldirect_w6", "EVICON", "sim-k0", "sim-k0+F", "clean_fulfilled", "at_least", 10),
    ("H-S1", "channelqueue_w6", "EVICON", "sim-k0", "sim-k0+QS", "clean_fulfilled", "at_least", 10),
    ("H-S4", f"channelqueue_w{INF}", "EVICON", "sim-k0", "sim-k0+QS", "clean_fulfilled_confirmed", "at_least", 5),
    ("H-S5", f"channelqueue_w{INF}", "EVICON", "sim-k0", "sim-k0+QS", "clean_fulfilled", "equivalent", 3),
    ("E-S1", "channelqueue_w6", "EVICON", "sim-k0+F", "sim-k0+QS", "clean_fulfilled", "at_least", 0),
]


def power(out: Path, ns=(8, 12, 16), resamples=2000) -> list:
    cache, rows = {}, []
    for n in ns:
        fams = collections.defaultdict(list)
        for h in HYPOTHESES:
            fams[h[0]].append(h)
        for fid, hs in fams.items():
            ds = []
            for h in hs:
                if h[1] not in cache:
                    g = collections.defaultdict(dict)
                    with open(out / "runs" / h[1] / "episodes.csv", newline="") as fh:
                        for e in csv.DictReader(fh):
                            g[(e["contract"], e["policy"])][int(e["episode"])] = e
                    cache[h[1]] = g
                g = cache[h[1]]
                a, b = g[(h[3], h[2])], g[(h[4], h[2])]
                ds.append([100 * float(b[i][h[5]]) / float(b[i]["offered"]) - 100 * float(a[i][h[5]]) / float(a[i]["offered"])
                           for i in a if i in b])
            rng = random.Random(72)
            met = [0] * len(hs)
            for _ in range(resamples):
                ps = {}
                for j, (h, d) in enumerate(zip(hs, ds)):
                    s = [d[rng.randrange(len(d))] for _ in range(n)]
                    ps[j] = (one_sided(s, h[7], upper=False) if h[6] == "at_least"
                             else max(one_sided(s, -h[7], upper=False), one_sided(s, h[7], upper=True)))
                adj = holm(ps)
                for j in ps:
                    met[j] += adj.get(j, 1.0) < 0.05
            for h, d, m in zip(hs, ds, met):
                rows.append({"n": n, "id": h[0], "point": h[1], "policy": h[2], "from": h[3], "to": h[4], "metric": h[5], "test": h[6],
                             "margin": h[7], "sim_mean": round(statistics.mean(d), 2), "sim_sd": round(statistics.stdev(d), 2),
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
    ap.add_argument("--out", default=str(REPO / "results" / "sim_sweep"))
    ap.add_argument("--episodes", type=int, default=200)
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = run(a.des, json.loads(Path(a.base).read_text()), out, a.episodes)
    write_csv(out / "summary.csv", rows)
    pw = power(out)
    write_csv(out / "power.csv", pw)
    L = ["# Simulator sweep (release 072, SIMULATION_ONLY)", "", f"{a.episodes} episodes per point; base `{a.base}`.", "",
         "## EVICON, clean / confirmed fulfilment (% of offered)", "", "| point | " + " | ".join(CONTRACTS) + " | ORACLE |",
         "|---|" + "---|" * (len(CONTRACTS) + 1)]
    by = {(r["point"], r["contract"], r["policy"]): r for r in rows}
    for pt in dict.fromkeys(r["point"] for r in rows):
        cells = [f"{by[(pt, c, 'EVICON')]['clean_pct']} / {by[(pt, c, 'EVICON')]['confirmed_pct']}" if (pt, c, "EVICON") in by else "-"
                 for c in CONTRACTS]
        orc = next((r["clean_pct"] for r in rows if r["point"] == pt and r["policy"] == "ORACLE"), "-")
        L.append(f"| {pt} | " + " | ".join(cells) + f" | {orc} |")
    L += ["", "## Power of the 072 decision rules under the simulator's paired differences", "",
          "| n | ID | point | policy | comparison | metric | rule | sim mean (SD) | power |", "|---|---|---|---|---|---|---|---|---|"]
    for r in pw:
        L.append(f"| {r['n']} | {r['id']} | {r['point']} | {r['policy']} | {r['from']} -> {r['to']} | {r['metric']} | {r['test']} {r['margin']} | "
                 f"{r['sim_mean']:+.2f} ({r['sim_sd']:.2f}) | {r['power']} |")
    (out / "SWEEP.md").write_text("\n".join(L) + "\n")
    bad = [r for r in rows if r["safety_violations"] and r["policy"] != "ORACLE"]
    print(f"sim sweep: {len(rows)} rows, {len(pw)} power rows; safety violations in {len(bad)} groups -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
