"""Release 080: the remaining estimand and checks of PREREG_review_077.md in the report.

E1 esim077 (estimand E-SIM): the plan's scenarios of section 1(g) mapped to the runs (release on timeout per channel),
   levels and effects next to the simulator with predict_check's rules, the simulator's own mapping left unchanged
E2 claims_check lists L2-1 (from late_attribution.py's output, the plan's rule), X-Q0, X-MCU and X-AUTH with their
   verdicts, and leaves out a check without data
E3 the report runs esim077 on the earliest complete run of each config and copies the MCU measurement for X-MCU
"""
import csv
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "evaluation"))
import claims_check as CC  # noqa: E402
import esim077 as E  # noqa: E402
import predict_check as P  # noqa: E402

FIELDS = ["regime", "contract", "policy", "episode", "devices", "cell_status", "scope", "offered", "admitted", "clean_fulfilled",
          "bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied", "late_effects", "reissues",
          "unresolved_at_end"]


def write_run(d: Path, name: str, rows: list) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text(json.dumps({"name": name, "workload": {"deadline_s": 60}}))
    with open(d / "runtime_episodes.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, 0) for k in FIELDS})
    return d


def row(ct, pol, ep, clean, **kw):
    r = {"regime": "x", "contract": ct, "policy": pol, "episode": ep, "devices": 4, "cell_status": "VALID", "scope": "TESTBED",
         "offered": 100, "admitted": 80, "clean_fulfilled": clean}
    r.update(kw)
    return r


class E1Esim(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_scenarios_and_runs(self):
        lv, viol = E.load_sim()
        self.assertEqual(lv[("release_timeout_queue", "K0", "RELEASE_ON_TIMEOUT")]["clean_pct"], 69.8)
        self.assertEqual(lv[("release_timeout_direct", "K0", "RELEASE_ON_TIMEOUT")]["clean_pct"], 75.7)
        self.assertAlmostEqual(viol[("release_timeout_queue", "K0", "RELEASE_ON_TIMEOUT")], 35.78)
        self.assertEqual(lv[("k2rep", "K3F", "RES_PERIODIC")]["clean_pct"], 76.89)
        for point in set(E.RUNS.values()):
            self.assertTrue(any(k[0] == point for k in lv), point)
        names = {json.loads(p.read_text()).get("name") for p in (REPO / "evaluation" / "campaigns").glob("*.json")}
        self.assertTrue(set(E.RUNS) <= names)

    def test_levels_effects_and_predict_check_unchanged(self):
        before = (dict(P.RUN_POINT), dict(P.SIM_CONTRACT))
        v = write_run(self.tmp / "expV-mild" / "20261029T023316", "expV-mild",
                      [row("K0", "EVICON", e, 50 + e) for e in range(3)] + [row("K0F", "EVICON", e, 74 + e) for e in range(3)])
        rt = write_run(self.tmp / "expRT-queue" / "20261029T052600", "expRT-queue",
                       [row("K0", "RELEASE_ON_TIMEOUT", e, 68, false_stopped=100, logical_duplicates=90) for e in range(2)])
        out = self.tmp / "out"
        self.assertEqual(E.main(["--out", str(out), str(v), str(rt), str(self.tmp)]), 0)
        res = json.loads((out / "esim077.json").read_text())
        lev = {(x["point"], x["contract"], x["policy"]): x for x in res["levels"]}
        self.assertEqual(lev[("mild", "K0", "EVICON")]["clean_pp_sim"], 55.03)
        self.assertEqual(lev[("mild", "K0", "EVICON")]["clean_pp_testbed"], 51.0)
        eff = [e for e in res["effects"] if e["from"] == "K0" and e["to"] == "K0F" and e["metric"] == "clean_pp"][0]
        self.assertEqual((eff["testbed"], eff["sim"], eff["direction_agrees"]), (24.0, 21.32, True))
        self.assertEqual(res["safety"][0]["testbed_per_episode"], 190.0)
        self.assertIn("E-SIM", (out / "ESIM077.md").read_text())
        self.assertEqual((dict(P.RUN_POINT), dict(P.SIM_CONTRACT)), before)  # the simulator claim's mapping untouched


class E2Claims(unittest.TestCase):
    def setUp(self):
        self.r = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.r, ignore_errors=True)

    def _write(self, rel, obj):
        p = self.r / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj))

    def test_checks(self):
        self.assertEqual(CC.checks_077(self.r), [])
        cells = [{"contract": "K2", "policy": "EVICON", "episode": 0, "late_effects": 17, "late_resend": 12, "late_resend_sent_in_validity": 12,
                  "late_first_send": 5}, {"contract": "K3T", "policy": "EVICON", "episode": 0}]
        self._write("late/late_attribution.json", [{"campaign": "expL2-sleepy", "cells": cells}])
        self._write("qos_dup/qos_dup_check.json", {"results": [{"qos": 1, "commands": 300, "commands_delivered_twice_or_more": 117},
                                                                {"qos": 0, "commands": 300, "commands_delivered_twice_or_more": 0}]})
        self._write("mcu/measure.json", {"results": [{"config": "zephyr-mqtt-mcu", "capabilities": ["D_h", "Q_A", "F"], "level": "K3"}]})
        self._write("auth/auth.json", {"verdict": "PASS", "cases": [{"case": f"A{i}", "result": "PASS"} for i in range(1, 14)]})
        got = {h["id"]: h["verdict"] for h in CC.checks_077(self.r)}
        self.assertEqual(got, {"L2-1": "MET", "X-Q0": "PASS", "X-MCU": "PASS", "X-AUTH": "PASS"})
        cells[0]["late_resend_sent_in_validity"] = 11
        self._write("late/late_attribution.json", [{"campaign": "expL2-sleepy", "cells": cells}])
        self._write("qos_dup/qos_dup_check.json", {"results": [{"qos": 1, "commands_delivered_twice_or_more": 0},
                                                                {"qos": 0, "commands_delivered_twice_or_more": 0}]})
        got = {h["id"]: h["verdict"] for h in CC.checks_077(self.r)}
        self.assertEqual((got["L2-1"], got["X-Q0"]), ("NOT MET", "FAIL"))
        self.assertEqual(CC.main(["--report", str(self.r)]), 0)
        self.assertIn("| PREREG_review_077 | L2-1 |", (self.r / "claims" / "CLAIMS.md").read_text())


class E3Report(unittest.TestCase):
    def test_driver(self):
        src = (REPO / "evicon_review.py").read_text()
        self.assertIn('"evaluation/esim077.py"', src)
        self.assertIn('out / "mcu" / "measure.json"', src)
        self.assertIn("evaluation.test_release080", src)


if __name__ == "__main__":
    unittest.main()
