#!/usr/bin/env python3
"""Simulator predictions against the testbed (release 073).

Every runtime experiment since 067 was planned with a simulator prediction frozen in its plan
(evaluation/sim_predictions/: the 072 sweep for A, W, S and L; sim_expC.py for C; sim_expC2.py for C2; sim_expC3.py for C3). This script puts
each testbed result next to the prediction made for the same window, channel, contract and policy:

  levels   per run, contract and policy: testbed mean over episodes of clean / confirmed fulfilment
           (% of offered; 95 % t interval) against the simulator's (ratio of totals over 200 episodes)
  effects  per run and policy, for each contract upgrade present in the run (K0 -> K0F, K0 -> K0S,
           K0F -> K0S, K2 -> K3F, K2 -> K3T, MINIO_PLAIN -> MINIO_COND): testbed mean of the per-episode
           paired differences (95 % t interval) against the simulator's difference of levels
  agreement  per effect: same sign (both within +-2 pp counts as agreeing on "no effect"), and whether the
           prediction lies inside the testbed's 95 % interval; over all effects: counts, mean absolute
           error, Spearman rank correlation of predicted and observed effects

Run -> simulator point (sweep tags of evaluation/sim_sweep.py):
  expW-queue-w6, expS-w6 -> channelqueue_w6; expW-queue-winf, expS-winf -> channelqueue_w1000000;
  expW-direct-w6 -> channeldirect_w6; expW-direct-winf -> channeldirect_w1000000;
  expL-sleepy -> offline_mean_ms120000_w6; expA-confirm-k0 / -k2 (runtime <= 071: effective window about 6
  because of the configuration cap) -> channelqueue_w6, marked approximate; expC-minio -> sim_expC.py.
Contracts: K0 sim-k0, K0F sim-k0+F, K0S sim-k0+QS, K2 sim-k2, K3F sim-k2+F, K3T sim-k3-expiry; MINIO_* as
measured. Policies with a prediction: EVICON, RES_PERIODIC (sweep), EVICON, M1, NAIVE (C).

Reads only (the frozen pairs_report.py computes the per-cell metrics, confirmed fulfilment included).
Writes PREDICT.md, predict.json, levels.csv, effects.csv into --out.

    python3 evaluation/predict_check.py --out <dir> <run dir> [<run dir> ...]
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from cadence_report import t_interval  # noqa: E402  (frozen; reused)
from pairs_report import load_run, paired  # noqa: E402  (frozen; reused)

PRED = HERE / "sim_predictions"
INF = 1000000
RUN_POINT = {
    "expW-queue-w6": ("channelqueue_w6", False), "expS-w6": ("channelqueue_w6", False),
    "expW-queue-winf": (f"channelqueue_w{INF}", False), "expS-winf": (f"channelqueue_w{INF}", False),
    "expW-direct-w6": ("channeldirect_w6", False), "expW-direct-winf": (f"channeldirect_w{INF}", False),
    "expL-sleepy": ("offline_mean_ms120000_w6", False),
    "expA-confirm-k0": ("channelqueue_w6", True), "expA-confirm-k2": ("channelqueue_w6", True),
    "expC-minio": ("expC", False),
    "expC2-zephyr": ("expC2", False),  # release 074: sim_expC2.py
    "expC3-ablation": ("expC3", False),  # release 075: sim_expC3.py (scenario owner_up, the fault model of C3)
}
C3 = ["ZEPHYR_PLAIN_DIRECT", "ZEPHYR_QA_DIRECT", "ZEPHYR_F_DIRECT", "ZEPHYR_QS_DIRECT", "ZEPHYR_QA_QUEUE", "ZEPHYR_F_QUEUE", "ZEPHYR_QS_QUEUE"]
SIM_CONTRACT = {"K0": "sim-k0", "K0F": "sim-k0+F", "K0S": "sim-k0+QS", "K2": "sim-k2", "K3F": "sim-k2+F", "K3T": "sim-k3-expiry",
                "MINIO_PLAIN": "MINIO_PLAIN", "MINIO_COND": "MINIO_COND",
                "ZEPHYR_PLAIN": "ZEPHYR_PLAIN", "ZEPHYR_CUTOFF": "ZEPHYR_CUTOFF", "ZEPHYR_STOP": "ZEPHYR_STOP", **{c: c for c in C3}}
UPGRADES = [("K0", "K0F"), ("K0", "K0S"), ("K0F", "K0S"), ("K2", "K3F"), ("K2", "K3T"), ("MINIO_PLAIN", "MINIO_COND"),
            ("ZEPHYR_PLAIN", "ZEPHYR_CUTOFF"), ("ZEPHYR_CUTOFF", "ZEPHYR_STOP"), ("ZEPHYR_PLAIN", "ZEPHYR_STOP"),
            # release 075, experiment C3: the firmware's evidence decomposed, per channel and across channels
            ("ZEPHYR_PLAIN_DIRECT", "ZEPHYR_QA_DIRECT"), ("ZEPHYR_PLAIN_DIRECT", "ZEPHYR_F_DIRECT"), ("ZEPHYR_PLAIN_DIRECT", "ZEPHYR_QS_DIRECT"),
            ("ZEPHYR_QA_DIRECT", "ZEPHYR_F_DIRECT"), ("ZEPHYR_QA_DIRECT", "ZEPHYR_QS_DIRECT"), ("ZEPHYR_F_DIRECT", "ZEPHYR_QS_DIRECT"),
            ("ZEPHYR_QA_DIRECT", "ZEPHYR_QA_QUEUE"), ("ZEPHYR_QA_QUEUE", "ZEPHYR_F_QUEUE"), ("ZEPHYR_QA_QUEUE", "ZEPHYR_QS_QUEUE"),
            ("ZEPHYR_F_QUEUE", "ZEPHYR_QS_QUEUE")]
METRICS = (("clean_pp", "clean_pct"), ("confirmed_pp", "confirmed_pct"))
NULL_PP = 2.0


def direction(x: float) -> int:
    return 0 if abs(x) < NULL_PP else (1 if x > 0 else -1)


def load_sim(sweep: Path, expc: Path, expc2: Path = None, expc3: Path = None) -> dict:
    """(point, sim contract, policy) -> {"clean_pct", "confirmed_pct"}; expc2 (release 074): sim_expC2.py; expc3 (075):
    sim_expC3.py, its owner_up scenario (the fault model of experiment C3; owner_crash is the contrast, not a prediction)."""
    out = {}
    if sweep.is_file():
        with open(sweep, newline="") as fh:
            for r in csv.DictReader(fh):
                out[(r["point"], r["contract"], r["policy"])] = {"clean_pct": float(r["clean_pct"]), "confirmed_pct": float(r["confirmed_pct"])}
    if expc.is_file():
        with open(expc, newline="") as fh:
            for r in csv.DictReader(fh):
                out[("expC", r["contract"], r["policy"])] = {"clean_pct": float(r["clean_pct"]), "confirmed_pct": float(r["confirmed_pct"])}
    if expc2 is not None and Path(expc2).is_file():
        with open(expc2, newline="") as fh:
            for r in csv.DictReader(fh):
                out[("expC2", r["contract"], r["policy"])] = {"clean_pct": float(r["clean_pct"]), "confirmed_pct": float(r["confirmed_pct"])}
    if expc3 is not None and Path(expc3).is_file():
        with open(expc3, newline="") as fh:
            for r in csv.DictReader(fh):
                if r.get("scenario", "owner_up") == "owner_up":
                    out[("expC3", r["contract"], r["policy"])] = {"clean_pct": float(r["clean_pct"]), "confirmed_pct": float(r["confirmed_pct"])}
    return out


def curves(sweep: Path) -> dict:
    """EVICON clean / confirmed fulfilment against the window, per channel and contract (phase diagram)."""
    out = {}
    if not sweep.is_file():
        return out
    with open(sweep, newline="") as fh:
        for r in csv.DictReader(fh):
            if r["policy"] != "EVICON" or not r["point"].startswith("channel"):
                continue
            out.setdefault(r["channel"], {}).setdefault(r["contract"], []).append([int(r["w"]), float(r["clean_pct"]), float(r["confirmed_pct"])])
    for ch in out.values():
        for v in ch.values():
            v.sort()
    return out


def describe(x: list) -> dict:
    x = [v for v in x if not math.isnan(v)]
    if not x:
        return {"n": 0}
    ci = t_interval(x, 0.95) if len(x) > 1 else None
    return {"n": len(x), "mean": round(statistics.mean(x), 3), "t95": [round(v, 3) for v in ci] if ci else None}


def spearman(a: list, b: list):
    if len(a) < 3:
        return None

    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2.0
            i = j + 1
        return r
    ra, rb = ranks(a), ranks(b)
    ma, mb = statistics.mean(ra), statistics.mean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb))
    return round(num / den, 3) if den else None


def check(runs: list, sim: dict) -> dict:
    levels, effects, skipped = [], [], []
    for i, run in enumerate(runs):
        r = load_run(f"r{i}", Path(run))
        name = r["name"]
        if name not in RUN_POINT:
            skipped.append(f"{name} ({run}): no simulator prediction")
            continue
        point, approx = RUN_POINT[name]
        label = f"{name}/{Path(run).name}"
        groups = {}
        for (ct, pol, _ep), m in r["cells"].items():
            groups.setdefault((ct, pol), []).append(m)
        for (ct, pol), ms in sorted(groups.items()):
            p = sim.get((point, SIM_CONTRACT.get(ct, ct), pol))
            row = {"run": label, "point": point, "approximate": approx, "contract": ct, "policy": pol, "n": len(ms)}
            for tm, sm in METRICS:
                d = describe([m[tm] for m in ms])
                row[f"{tm}_testbed"], row[f"{tm}_t95"] = d.get("mean"), d.get("t95")
                row[f"{tm}_sim"] = p[sm] if p else None
            levels.append(row)
        for a, b in UPGRADES:
            for pol in sorted({pol for (ct, pol) in groups if ct in (a, b)}):
                if (a, pol) not in groups or (b, pol) not in groups:
                    continue
                pa, pb = sim.get((point, SIM_CONTRACT[a], pol)), sim.get((point, SIM_CONTRACT[b], pol))
                for tm, sm in METRICS:
                    d = describe(paired(r, pol, a, b, tm))
                    if not d.get("n"):
                        continue
                    pred = round(pb[sm] - pa[sm], 2) if pa and pb else None
                    e = {"run": label, "point": point, "approximate": approx, "policy": pol, "from": a, "to": b, "metric": tm,
                         "n": d["n"], "testbed": d["mean"], "t95": d["t95"], "sim": pred}
                    if pred is not None:
                        obs = d["mean"]
                        e["same_sign"] = (abs(obs) < NULL_PP and abs(pred) < NULL_PP) or (obs > 0) == (pred > 0)
                        # release 074: three-way direction (negative / null / positive, null = within +-NULL_PP). "same
                        # sign" (073) counts a predicted null with an observed effect of the same sign as agreement
                        e["direction_agrees"] = direction(obs) == direction(pred)
                        e["sim_in_t95"] = bool(d["t95"]) and d["t95"][0] <= pred <= d["t95"][1]
                        e["error"] = round(obs - pred, 2)
                    effects.append(e)
    scored = [e for e in effects if e.get("sim") is not None and not e["approximate"] and e["t95"]]
    lv = [x for x in levels if x["clean_pp_sim"] is not None and x["clean_pp_testbed"] is not None and not x["approximate"] and x["n"] > 1]
    summary = {"effects_scored": len(scored), "same_sign": sum(e["same_sign"] for e in scored),
               "direction_agrees": sum(e["direction_agrees"] for e in scored),
               "sim_in_t95": sum(e["sim_in_t95"] for e in scored),
               "effect_mae_pp": round(statistics.mean(abs(e["error"]) for e in scored), 2) if scored else None,
               "effect_spearman": spearman([e["sim"] for e in scored], [e["testbed"] for e in scored]),
               "levels_scored": len(lv),
               "level_mae_clean_pp": round(statistics.mean(abs(x["clean_pp_testbed"] - x["clean_pp_sim"]) for x in lv), 2) if lv else None,
               "level_bias_clean_pp": round(statistics.mean(x["clean_pp_testbed"] - x["clean_pp_sim"] for x in lv), 2) if lv else None,
               "note": "approximate runs (experiment A, runtime <= 071) and groups of one episode are listed but not scored"}
    return {"levels": levels, "effects": effects, "summary": summary, "skipped": skipped}


def fmt(v, d=1):
    return "--" if v is None else f"{v:.{d}f}"


def ci(t):
    return "--" if not t else f"[{t[0]:.1f}, {t[1]:.1f}]"


def markdown(res: dict) -> str:
    s = res["summary"]
    L = ["# Simulator predictions against the testbed", "",
         "Predictions: `evaluation/sim_predictions/` (frozen in the 072, 073, 074 and 075 plans before the runs). Testbed: mean over "
         "episodes with a 95 % t interval; effects are per-episode paired differences (to minus from).", "",
         f"**Effects scored: {s['effects_scored']}; same sign {s['same_sign']}; same direction (negative / null within "
         f"+-{NULL_PP:g} pp / positive) {s['direction_agrees']}; prediction inside the testbed's 95 % interval "
         f"{s['sim_in_t95']}; mean absolute error {fmt(s['effect_mae_pp'])} pp; Spearman {s['effect_spearman']}. Levels scored: "
         f"{s['levels_scored']}; clean fulfilment, mean absolute error {fmt(s['level_mae_clean_pp'])} pp, mean bias (testbed minus "
         f"simulator) {fmt(s['level_bias_clean_pp'])} pp.** {s['note']}.", "",
         "## Effects of contract upgrades", "",
         "| run | point | policy | upgrade | metric | n | testbed [95 %] | simulator | same sign | same direction | inside | error |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for e in res["effects"]:
        pred = "--" if e["sim"] is None else f"{e['sim']:+.1f}"
        L.append(f"| {e['run']}{' (approx.)' if e['approximate'] else ''} | {e['point']} | {e['policy']} | {e['from']} -> {e['to']} | "
                 f"{e['metric']} | {e['n']} | {e['testbed']:+.1f} {ci(e['t95'])} | {pred} | "
                 f"{e.get('same_sign', '--')} | {e.get('direction_agrees', '--')} | {e.get('sim_in_t95', '--')} | {fmt(e.get('error'))} |")
    L += ["", "## Levels", "", "| run | contract | policy | n | clean testbed [95 %] | clean sim | confirmed testbed [95 %] | confirmed sim |",
          "|---|---|---|---|---|---|---|---|"]
    for x in res["levels"]:
        L.append(f"| {x['run']}{' (approx.)' if x['approximate'] else ''} | {x['contract']} | {x['policy']} | {x['n']} | "
                 f"{fmt(x['clean_pp_testbed'])} {ci(x['clean_pp_t95'])} | {fmt(x['clean_pp_sim'])} | "
                 f"{fmt(x['confirmed_pp_testbed'])} {ci(x['confirmed_pp_t95'])} | {fmt(x['confirmed_pp_sim'])} |")
    if res["skipped"]:
        L += ["", "Not compared: " + "; ".join(res["skipped"])]
    return "\n".join(L) + "\n"


def write_csv(path: Path, rows: list):
    if not rows:
        path.write_text("")
        return
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow({k: json.dumps(v) if isinstance(v, list) else v for k, v in r.items()})


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--sweep", default=str(PRED / "sweep_072_summary.csv"))
    ap.add_argument("--expc", default=str(PRED / "expC_073_summary.csv"))
    ap.add_argument("--expc2", default=str(PRED / "expC2_074_summary.csv"))
    ap.add_argument("--expc3", default=str(PRED / "expC3_075_summary.csv"))
    ap.add_argument("runs", nargs="+", help="campaign run directories (complete runs)")
    a = ap.parse_args(argv)
    res = check(a.runs, load_sim(Path(a.sweep), Path(a.expc), Path(a.expc2), Path(a.expc3)))
    res["curves"] = curves(Path(a.sweep))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "predict.json").write_text(json.dumps(res, indent=1) + "\n")
    (out / "PREDICT.md").write_text(markdown(res))
    write_csv(out / "levels.csv", res["levels"])
    write_csv(out / "effects.csv", res["effects"])
    s = res["summary"]
    print(f"predict_check: {s['effects_scored']} effects scored, same sign {s['same_sign']}, inside 95 % {s['sim_in_t95']}, "
          f"MAE {s['effect_mae_pp']} pp; {s['levels_scored']} levels, MAE {s['level_mae_clean_pp']} pp -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
