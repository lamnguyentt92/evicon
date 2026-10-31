#!/usr/bin/env python3
"""Estimand E-SIM of PREREG_extra_084.md (release 084): the runs of the plan against its simulator prediction.

esim083.py (frozen by PREREG_extra_083.md; imported, never edited) with the prediction of the 084 plan's section 1(c)
(sim_predictions/extra084_summary.csv and extra084_power.csv) and the 084 runs: per run and cell the testbed's clean fulfilment next
to the simulator's, per hypothesis with a test the testbed estimate next to the simulator's mean. The (a) runs are compared with the
calibrated twin (scenarios a_s6, a_sinf); the cross-run PERIODIC_FULL runs (c) with c_*. An estimand: no claim, no test.

    python3 evaluation/esim084.py --results report/pairs/PREREG_extra_084/results.json --out report/esim084
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import esim083 as E  # noqa: E402  (frozen by PREREG_extra_083.md; imported, never edited)

SUMMARY = HERE / "sim_predictions" / "extra084_summary.csv"
POWER = HERE / "sim_predictions" / "extra084_power.csv"
RUN_CELLS = {
    "FIs6": ("a_s6", {"K0F/EVICON": ("K0F", "EVICON"), "K0F/EVICON_FI": ("K0F", "EVICON_FI"), "K0S/EVICON": ("K0S", "EVICON")}),
    "FIsi": ("a_sinf", {"K0F/EVICON": ("K0F", "EVICON"), "K0F/EVICON_FI": ("K0F", "EVICON_FI")}),
    "PFz6": ("c_fw_w6", {"ZEPHYR_CUTOFF/PERIODIC_FULL": ("ZEPHYR_CUTOFF", "PERIODIC_FULL")}),
    "PFzi": ("c_fw_winf", {"ZEPHYR_CUTOFF/PERIODIC_FULL": ("ZEPHYR_CUTOFF", "PERIODIC_FULL")}),
    "PFm6": ("c_minio_w6", {"MINIO_COND/PERIODIC_FULL": ("MINIO_COND", "PERIODIC_FULL")}),
    "PFmi": ("c_minio_winf", {"MINIO_COND/PERIODIC_FULL": ("MINIO_COND", "PERIODIC_FULL")}),
}


def analyse(results: dict) -> dict:
    """esim083's analysis with this plan's prediction and runs; esim083's own tables are restored afterwards, so that
    esim083 used in the same process still reads the 083 prediction."""
    saved = E.SUMMARY, E.POWER, E.RUN_CELLS
    E.SUMMARY, E.POWER, E.RUN_CELLS = SUMMARY, POWER, RUN_CELLS
    try:
        return E.analyse(results)
    finally:
        E.SUMMARY, E.POWER, E.RUN_CELLS = saved


def markdown(res: dict) -> str:
    return E.markdown(res).replace("PREREG_extra_083", "PREREG_extra_084").replace("extra083_summary.csv", "extra084_summary.csv")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    res = analyse(json.loads(Path(a.results).read_text()))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "esim084.json").write_text(json.dumps(res, indent=2) + "\n")
    (out / "ESIM084.md").write_text(markdown(res))
    miss = sum(1 for e in res["effects"] if e["sign_agrees"] is False)
    print(f"E-SIM (084): {len(res['levels'])} cells, {len(res['effects'])} effects, {miss} sign misses -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
