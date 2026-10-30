#!/usr/bin/env python3
"""Estimand E-SIM of PREREG_review_077.md (release 080): the runs of the 077 plan against the simulator prediction
of the plan's section 1(g) (evaluation/sim_predictions/review077_summary.csv, sim_review077.py, frozen).

  levels   per run, contract and policy: testbed mean over episodes of clean / confirmed fulfilment (% of offered,
           95 % t interval) next to the simulator's (ratio of totals over 200 episodes)
  effects  per run and policy, for each contract upgrade in the run (K0 -> K0F, K0 -> K0S, K0F -> K0S, K2 -> K3F,
           MINIO_PLAIN -> MINIO_COND, ZEPHYR_PLAIN -> ZEPHYR_CUTOFF, ZEPHYR_PLAIN_DIRECT -> ZEPHYR_F_DIRECT): the
           testbed's mean per-episode paired difference (95 % t) next to the simulator's difference of levels, with
           the agreement rules of predict_check.py (direction within +-2 pp, prediction inside the interval)
  safety   the release-on-timeout runs: safety violations per episode on the testbed and in the simulator

No claim: E-SIM is an estimand. The scoring of the simulator claim (predict_check.py, claims_check.py) is not
changed by it. Run -> scenario of the plan's table in section 1(g):
  expR-minio-winf -> unbounded_minio, expR-zephyr-winf -> unbounded_zephyr, expO-noorphan -> noorphan,
  expK2-w6 -> k2rep, expV-mild -> mild, expWAN-s6 -> wan, expRT-queue / expRT-direct -> release_timeout
  (K0_QUEUE, K0F_QUEUE / K0_DIRECT).

    python3 evaluation/esim077.py --out <dir> <campaign run dir> ...
Writes ESIM077.md and esim077.json.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import predict_check as P  # noqa: E402  (its check(): the same levels, effects and agreement rules)

SUMMARY = HERE / "sim_predictions" / "review077_summary.csv"
KINDS = ("bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied")
RUNS = {
    "expR-minio-winf": "unbounded_minio", "expR-zephyr-winf": "unbounded_zephyr", "expO-noorphan": "noorphan",
    "expK2-w6": "k2rep", "expV-mild": "mild", "expWAN-s6": "wan",
    "expRT-queue": "release_timeout_queue", "expRT-direct": "release_timeout_direct",
}
RT_CELLS = {"K0_QUEUE": ("release_timeout_queue", "K0"), "K0F_QUEUE": ("release_timeout_queue", "K0F"),
            "K0_DIRECT": ("release_timeout_direct", "K0")}


def load_sim(path: Path = SUMMARY) -> tuple:
    """(scenario point, contract, policy) -> {clean_pct, confirmed_pct}, and the same key -> safety violations per
    episode of the simulator."""
    levels, viol = {}, {}
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            point, cell = r["scenario"], r["cell"]
            if point == "release_timeout":
                point, cell = RT_CELLS[cell]
            key = (point, cell, r["policy"])
            levels[key] = {"clean_pct": float(r["clean_pct"]), "confirmed_pct": float(r["confirmed_pct"])}
            eps = int(r.get("episodes") or 0)
            if eps:
                viol[key] = round(float(r.get("safety_violations") or 0) / eps, 3)
    return levels, viol


def testbed_violations(run: Path) -> dict:
    """(contract, policy) -> mean safety violations (the five counts) per VALID episode."""
    per = {}
    with open(run / "runtime_episodes.csv", newline="") as fh:
        for r in csv.DictReader(fh):
            if not str(r.get("cell_status", "")).startswith("VALID"):
                continue
            per.setdefault((r["contract"], r["policy"]), []).append(sum(float(r.get(k) or 0) for k in KINDS))
    return {k: round(statistics.mean(v), 3) for k, v in per.items()}


def run(runs: list, sim_path: Path = SUMMARY) -> dict:
    sim, sim_viol = load_sim(sim_path)
    saved = (P.RUN_POINT, P.SIM_CONTRACT)
    try:
        P.RUN_POINT = {name: (point, False) for name, point in RUNS.items()}
        P.SIM_CONTRACT = _Identity()
        res = P.check([str(r) for r in runs], sim)
    finally:
        P.RUN_POINT, P.SIM_CONTRACT = saved
    safety = []
    for r in runs:
        cfg = json.loads((Path(r) / "config.json").read_text()) if (Path(r) / "config.json").exists() else {}
        point = RUNS.get(cfg.get("name", ""))
        if not point or not point.startswith("release_timeout"):
            continue
        for (ct, pol), v in sorted(testbed_violations(Path(r)).items()):
            safety.append({"run": f"{cfg['name']}/{Path(r).name}", "contract": ct, "policy": pol, "testbed_per_episode": v,
                           "sim_per_episode": sim_viol.get((point, ct, pol))})
    res["safety"] = safety
    res["summary"]["note"] = "E-SIM of PREREG_review_077.md (estimand, no claim); groups of one episode are listed but not scored"
    return res


class _Identity(dict):
    def get(self, k, default=None):
        return k

    def __getitem__(self, k):
        return k


def _f(x, nd=2):
    return "-" if x is None else f"{x:.{nd}f}"


def markdown(res: dict) -> str:
    s = res["summary"]
    L = ["# E-SIM: the runs of PREREG_review_077 against the simulator prediction of its section 1(g)", "",
         "Testbed (mean over episodes, 95 % t) next to `evaluation/sim_predictions/review077_summary.csv` (200 episodes, "
         "SIMULATION_ONLY). An estimand, no claim.", "",
         f"Effects scored: {s.get('effects_scored')}; direction agrees (null within +-2 pp): {s.get('direction_agrees')}; "
         f"same sign: {s.get('same_sign')}; prediction inside the testbed's 95 % interval: {s.get('sim_in_t95')}; "
         f"mean absolute error {s.get('effect_mae_pp')} pp; Spearman {s.get('effect_spearman')}. Levels scored: "
         f"{s.get('levels_scored')}; level bias (testbed - simulator) {s.get('level_bias_clean_pp')} pp, mean absolute "
         f"difference {s.get('level_mae_clean_pp')} pp.", "",
         "## Levels", "", "| run | scenario | contract | policy | n | clean testbed [95 %] | clean sim | confirmed testbed | confirmed sim |",
         "|---|---|---|---|---|---|---|---|---|"]
    for x in res["levels"]:
        ci = x.get("clean_pp_t95")
        L.append(f"| {x['run']} | {x['point']} | {x['contract']} | {x['policy']} | {x['n']} | {_f(x.get('clean_pp_testbed'))}"
                 + (f" [{ci[0]:.2f}, {ci[1]:.2f}]" if ci else "") + f" | {_f(x.get('clean_pp_sim'))} | {_f(x.get('confirmed_pp_testbed'))} | "
                 f"{_f(x.get('confirmed_pp_sim'))} |")
    L += ["", "## Effects", "", "| run | policy | from -> to | metric | n | testbed [95 %] | simulator | direction agrees | sim in 95 % |",
          "|---|---|---|---|---|---|---|---|---|"]
    for e in res["effects"]:
        ci = e.get("t95")
        L.append(f"| {e['run']} | {e['policy']} | {e['from']} -> {e['to']} | {e['metric']} | {e['n']} | {_f(e.get('testbed'))}"
                 + (f" [{ci[0]:.2f}, {ci[1]:.2f}]" if ci else "") + f" | {_f(e.get('sim'))} | "
                 f"{'-' if e.get('direction_agrees') is None else ('yes' if e['direction_agrees'] else 'no')} | "
                 f"{'-' if e.get('sim_in_t95') is None else ('yes' if e['sim_in_t95'] else 'no')} |")
    if res.get("safety"):
        L += ["", "## Release on timeout: safety violations per episode", "", "| run | contract | policy | testbed | simulator |",
              "|---|---|---|---|---|"]
        for x in res["safety"]:
            L.append(f"| {x['run']} | {x['contract']} | {x['policy']} | {_f(x['testbed_per_episode'], 1)} | {_f(x['sim_per_episode'], 1)} |")
    if res.get("skipped"):
        L += ["", "Not compared: " + "; ".join(res["skipped"])]
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--sim", default=str(SUMMARY))
    ap.add_argument("runs", nargs="+")
    a = ap.parse_args(argv)
    runs = []
    for d in a.runs:
        p = Path(d)
        cfg = json.loads((p / "config.json").read_text()) if (p / "config.json").exists() else {}
        if cfg.get("name") in RUNS and (p / "runtime_episodes.csv").exists():
            runs.append(p)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if not runs:
        print("esim077: no run of the 077 plan with a simulator prediction")
        return 1
    res = run(runs, Path(a.sim))
    (out / "esim077.json").write_text(json.dumps(res, indent=1, default=str) + "\n")
    (out / "ESIM077.md").write_text(markdown(res))
    s = res["summary"]
    print(f"esim077: {len(runs)} runs, {s.get('effects_scored')} effects scored, direction agrees {s.get('direction_agrees')} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
