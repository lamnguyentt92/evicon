#!/usr/bin/env python3
"""Estimand E-SIM of PREREG_extra_083.md (release 083): the runs of the plan against its simulator prediction.

Reads the plan's decided results (report/pairs/PREREG_extra_083/results.json, written by pairs_ext083.py: the levels
of every run with the extra metrics, and the hypotheses with their per-episode estimates) and the frozen prediction of
the plan's section 1(h) (evaluation/sim_predictions/extra083_summary.csv and extra083_power.csv). Writes, per run and
cell, the testbed's clean fulfilment next to the simulator's (the scenario and cell named in RUN_CELLS), and per
hypothesis with a test the testbed estimate next to the simulator's mean, with the sign agreement. An estimand: no
claim, no test (the simulator's scoring is part 2 of the claims check).

    python3 evaluation/esim083.py --results report/pairs/PREREG_extra_083/results.json --out report/esim083
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUMMARY = HERE / "sim_predictions" / "extra083_summary.csv"
POWER = HERE / "sim_predictions" / "extra083_power.csv"
T7 = {"rate05": "t7_rate05", "rate4": "t7_rate4", "off5": "t7_off5_15", "off40": "t7_off40_120", "seedA": "t7_base", "seedB": "t7_base"}

# run key -> (scenario, {testbed "contract/policy": simulator (cell, policy)})
RUN_CELLS = {
    "Wk": ("t1_k2", {f"K2/{p}": ("K2", p) for p in ("EVICON", "M1", "M1W")}),
    "TBw": ("t1_tb", {f"TB_RPC/{p}": ("TB_RPC", p) for p in ("EVICON", "M1", "M1W")}),
    "RTsq": ("t3_rt", {f"K0/RELEASE_ON_TIMEOUT_{t}S": ("K0_QUEUE", f"RELEASE_ON_TIMEOUT_{t}S") for t in (30, 60, 120)}),
    "RTsd": ("t3_rt", {f"K0/RELEASE_ON_TIMEOUT_{t}S": ("K0_DIRECT", f"RELEASE_ON_TIMEOUT_{t}S") for t in (30, 60, 120)}),
    "Z6": ("t4_w6", {"ZEPHYR_PLAIN_QOS0/EVICON": ("ZEPHYR_PLAIN_QOS0", "EVICON"), "ZEPHYR_PLAIN_DIRECT_QOS0/EVICON": ("ZEPHYR_PLAIN_DIRECT_QOS0", "EVICON")}),
    "Zi": ("t4_winf", {"ZEPHYR_PLAIN_QOS0/EVICON": ("ZEPHYR_PLAIN_QOS0", "EVICON"), "ZEPHYR_PLAIN_DIRECT_QOS0/EVICON": ("ZEPHYR_PLAIN_DIRECT_QOS0", "EVICON")}),
    "Gs": ("t5_s_winf", {f"{c}/{p}": (c, p) for c in ("K0F", "K0S") for p in ("EVICON", "EVICON_GREEDY")}),
    "Gm": ("t5_minio", {f"{c}/EVICON_GREEDY": (c, "EVICON_GREEDY") for c in ("MINIO_PLAIN", "MINIO_COND")}),
    "Th": ("t5_theta", {"K0F/EVICON": ("K0F", "EVICON_FC5"), "K0F/EVICON_FC100": ("K0F", "EVICON_FC100")}),
}
for w in (3, 16, 32):
    RUN_CELLS[f"Z{w}w"] = ("t6_fw", {f"{c}/EVICON": (c, f"EVICON@W{w}") for c in ("ZEPHYR_PLAIN_QOS0", "ZEPHYR_CUTOFF")})
for w, s in ((3, "3"), (16, "16"), ("inf", "inf")):
    RUN_CELLS[f"TBw{w}"] = ("t6_tb", {f"TB_RPC/{p}": ("TB_RPC", f"{p}@W{s}") for p in ("EVICON", "M1W")})
for lv, sc in T7.items():
    RUN_CELLS[f"T7q-{lv}"] = (f"{sc}_q6", {f"{c}/EVICON": (c, "EVICON") for c in ("K0", "K0F", "K0S")})
    RUN_CELLS[f"T7d-{lv}"] = (f"{sc}_dinf", {f"{c}/EVICON": (c, "EVICON") for c in ("K0", "K0F")})


def load_sim() -> dict:
    with open(SUMMARY, newline="") as fh:
        return {(r["scenario"], r["cell"], r["policy"]): r for r in csv.DictReader(fh)}


def load_power() -> dict:
    with open(POWER, newline="") as fh:
        return {r["id"]: r for r in csv.DictReader(fh)}


def analyse(results: dict) -> dict:
    sim, power = load_sim(), load_power()
    levels = []
    for run, groups in (results.get("ext_levels") or {}).items():
        if run not in RUN_CELLS:
            continue
        sc, cells = RUN_CELLS[run]
        for g, v in groups.items():
            if g not in cells:
                continue
            s = sim.get((sc, *cells[g]))
            levels.append({"run": run, "cell": g, "n": v.get("n"), "testbed_clean_pct": v.get("clean_pp"), "testbed_violations": v.get("violations"),
                           "scenario": sc, "sim_cell": "/".join(cells[g]), "sim_clean_pct": float(s["clean_pct"]) if s else None,
                           "sim_violations_per_episode": float(s["violations_per_episode"]) if s else None})
    effects = []
    for h in results.get("hypotheses", []):
        p = power.get(h["id"])
        if not p or h.get("test") not in ("at_least", "at_most", "equivalent"):
            continue
        est, sm = h.get("mean"), float(p["sim_mean"])
        agree = None
        if est is not None and abs(sm) >= 1.0:
            agree = (est > 0) == (sm > 0)
        effects.append({"id": h["id"], "verdict": h.get("verdict"), "testbed_mean": est, "testbed_t95": h.get("t95"), "n": h.get("n"),
                        "sim_mean": sm, "sim_sd": float(p["sim_sd"]), "sign_agrees": agree})
    return {"levels": levels, "effects": effects}


def markdown(res: dict) -> str:
    def f(x, d=1):
        return "-" if x is None else f"{x:.{d}f}"
    L = ["# E-SIM: the runs of PREREG_extra_083 against its simulator prediction", "",
         "Estimand (no claim). The prediction is the frozen `sim_predictions/extra083_summary.csv` (200 episodes per scenario, "
         "SIMULATION_ONLY); the testbed levels are the plan's runs as decided by `pairs_ext083.py`.", "",
         "## Levels (clean fulfilment, % of offered)", "",
         "| run | cell | n | testbed | simulator (scenario, cell) | testbed violations / ep. | simulator violations / ep. |", "|---|---|---|---|---|---|---|"]
    for r in res["levels"]:
        L.append(f"| {r['run']} | {r['cell']} | {r['n']} | {f(r['testbed_clean_pct'])} | {f(r['sim_clean_pct'])} ({r['scenario']}, {r['sim_cell']}) | "
                 f"{f(r['testbed_violations'], 2)} | {f(r['sim_violations_per_episode'], 2)} |")
    L += ["", "## Effects of the hypotheses with a test (pp; sign compared where the simulator's mean is at least 1 pp)", "",
          "| ID | verdict | testbed mean [95 % t] (n) | simulator mean (SD) | sign agrees |", "|---|---|---|---|---|"]
    for e in res["effects"]:
        ci = e["testbed_t95"] or []
        est = "-" if e["testbed_mean"] is None else f"{e['testbed_mean']:+.2f}" + (f" [{ci[0]:+.2f}, {ci[1]:+.2f}]" if len(ci) == 2 else "") + f" ({e['n']})"
        L.append(f"| {e['id']} | {e['verdict']} | {est} | {e['sim_mean']:+.2f} ({e['sim_sd']:.2f}) | "
                 f"{'-' if e['sign_agrees'] is None else ('yes' if e['sign_agrees'] else '**no**')} |")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    res = analyse(json.loads(Path(a.results).read_text()))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "esim083.json").write_text(json.dumps(res, indent=2) + "\n")
    (out / "ESIM083.md").write_text(markdown(res))
    miss = sum(1 for e in res["effects"] if e["sign_agrees"] is False)
    print(f"E-SIM (083): {len(res['levels'])} cells, {len(res['effects'])} effects, {miss} sign misses -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
