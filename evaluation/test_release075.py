"""Release 075 analyses: safety over every testbed campaign, claims against evidence, the C3 paper table and
safety macros, the C3 simulator prediction in predict_check, and the owner reboot check's summary.

S1 safety_total: guarded cells against negative controls (NEG_*, blind retry on an owner without dedup) and
   blind retry on owners with dedup; a violation in a guarded cell fails; the exact zero-event bound
S2 claims_check: hypotheses of every plan listed with their verdicts (076: experiment A's secondary hypotheses
   with their own verdicts); claims SUPPORTED / NOT SUPPORTED / NO DATA from their files; the simulator's sign misses
   named
S3 paper_tables: tab_expC3.tex and the safety macros
S4 predict_check: the C3 predictions are the owner_up scenario of sim_expC3.py
S5 zephyr_reboot_check.summarize: duplicates and false receipts across the reboot
S6 results_record: a plan's outcome recorded next to it, copied from the report, with the plan's deviations and
   the interpretation note; the plan itself is not touched
S7 (076) expC2_summary.gateway_stats: UNCERTAIN stops by reason, device-episodes with a full tombstone table
"""
import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "evaluation"))
sys.path.insert(0, str(REPO / "conformance"))
import claims_check  # noqa: E402
import expC2_summary  # noqa: E402
import paper_tables  # noqa: E402
import predict_check  # noqa: E402
import results_record  # noqa: E402
import safety_total  # noqa: E402
from reference import zephyr_reboot_check as RB  # noqa: E402

FIELDS = ["contract", "policy", "episode", "cell_status", "offered", "admitted", "dispatch_attempts", "late_effects"] + list(safety_total.KINDS)


def run_dir(tmp: Path, name: str, rows: list, cfg_extra=None, scope="TESTBED") -> Path:
    d = tmp / name / "20261025T000000"
    d.mkdir(parents=True)
    (d / "config.json").write_text(json.dumps(dict({"name": name}, **(cfg_extra or {}))))
    (d / "manifest.json").write_text(json.dumps({"release": "075", "scope": scope}))
    with open(d / "runtime_episodes.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, 0) for k in FIELDS})
    return d


def row(contract, policy, ep=0, status="VALID", **kw):
    return dict({"contract": contract, "policy": policy, "episode": ep, "cell_status": status, "offered": 100, "admitted": 90,
                 "dispatch_attempts": 120}, **kw)


class SafetyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_s1_classification_and_bound(self):
        real = {"real_owner": {"policies": {"NAIVE": {"base": "M1", "authority": {"retry_guard": False}}},
                               "contracts": {"P": {"mode": "PLAIN"}, "D": {"mode": "CUTOFF"}}}}
        runs = [run_dir(self.tmp, "w", [row("K0", "EVICON"), row("K0F", "EVICON", 1, late_effects=1), row("K0", "EVICON", 2, status="INVALID: x",
                                                                                                          duplicate_effects=9)]),
                run_dir(self.tmp, "neg", [row("NEG_SEAL", "EVICON", false_stopped=3), row("NEG_DEDUP", "EVICON", duplicate_effects=2)]),
                run_dir(self.tmp, "c", [row("P", "NAIVE", duplicate_effects=4), row("D", "NAIVE"), row("P", "EVICON")], real),
                run_dir(self.tmp, "local", [row("K0", "EVICON", bound_violations=5)], scope="DEV_LOCAL")]
        out = self.tmp / "out"
        rc = safety_total.main(["--out", str(out)] + [str(r) for r in runs])
        s = json.loads((out / "safety.json").read_text())["summary"]
        self.assertEqual(rc, 0)
        self.assertEqual((s["guarded"]["cells"], s["guarded"]["admitted"], s["guarded_violations"]), (3, 270, 0))
        self.assertEqual(s["controls"], {"cells": 3, "detecting_cells": 3})
        self.assertEqual(s["unguarded_dedup"], {"cells": 1, "cells_with_violation": 0})
        self.assertAlmostEqual(s["upper95_per_admitted_command"], 1 - 0.05 ** (1 / 270))
        self.assertAlmostEqual(safety_total.upper95(1000), 0.002991, places=5)
        bad = run_dir(self.tmp, "bad", [row("K0F", "EVICON", false_applied=1)])
        self.assertEqual(safety_total.main(["--out", str(self.tmp / "out2"), str(bad)]), 3)


class ClaimsTest(unittest.TestCase):
    def test_s2_claims(self):
        rep = Path(tempfile.mkdtemp())
        (rep / "pairs" / "PLAN_X").mkdir(parents=True)
        (rep / "pairs" / "PLAN_X" / "results.json").write_text(json.dumps({"hypotheses": [
            {"id": "X-1", "test": "at_least", "verdict": "MET", "mean": 12.0, "t95": [5.0, 19.0], "p_holm": 0.01},
            {"id": "X-2", "test": "at_least", "verdict": "NOT MET", "mean": 0.1, "t95": [-1.0, 1.2], "p_holm": 0.4},
            {"id": "X-3", "test": "detects", "verdict": "DETECTED", "cells": 4, "cells_detecting": 4, "counts": {"duplicate_effects": 7}}]}))
        (rep / "safety").mkdir()
        (rep / "safety" / "safety.json").write_text(json.dumps({"summary": {"guarded": {"cells": 10, "admitted": 1000}, "guarded_violations": 0,
                                                                            "upper95_per_admitted_command": 0.003,
                                                                            "controls": {"cells": 2, "detecting_cells": 2}}}))
        (rep / "predict").mkdir()
        (rep / "predict" / "predict.json").write_text(json.dumps({"summary": {"effects_scored": 4, "same_sign": 3, "direction_agrees": 3,
                                                                              "effect_spearman": 0.9, "effect_mae_pp": 3, "level_bias_clean_pp": -2}}))
        claims_check.main(["--report", str(rep)])
        got = json.loads((rep / "claims" / "claims.json").read_text())
        self.assertEqual([h["verdict"] for h in got["hypotheses"]], ["MET", "NOT MET", "DETECTED"])
        status = {c["id"]: c["status"] for c in got["claims"]}
        self.assertEqual(status["safety"], "SUPPORTED")
        self.assertEqual(status["simulator"], "NOT SUPPORTED")  # 3 of 4 signs
        self.assertEqual(status["T3"], "NO DATA")
        self.assertIn("NOT MET", (rep / "claims" / "CLAIMS.md").read_text())
        (rep / "confirm_expA").mkdir()  # release 076: the secondary hypotheses of experiment A keep their own verdicts
        (rep / "confirm_expA" / "results.json").write_text(json.dumps({"hypotheses": {
            "H-A1": {"pass": True}, "H-B": {"conclusion": "inconclusive", "mean_diff_commands": 10.5, "ci95_commands": [-18.4, 38.5]},
            "SR": {"k0": {"pass": True}, "k2": {"pass": False}, "k2_not_applicable": True}}}))
        claims_check.main(["--report", str(rep)])
        got = {h["id"]: h for h in json.loads((rep / "claims" / "claims.json").read_text())["hypotheses"]}
        self.assertEqual((got["H-A1"]["verdict"], got["H-B"]["verdict"], got["SR"]["verdict"]), ("MET", "INCONCLUSIVE", "K0 MET; K2 not applicable"))
        self.assertIn("+10.50 commands per episode [-18.40, +38.50]", got["H-B"]["estimate"])
        (rep / "predict" / "predict.json").write_text(json.dumps({"summary": {"effects_scored": 2, "same_sign": 1, "direction_agrees": 1,
                                                                              "effect_spearman": 1.0, "effect_mae_pp": 1, "level_bias_clean_pp": 0},
                                                                  "effects": [{"run": "expC3-ablation/1", "policy": "EVICON", "from": "F", "to": "QS",
                                                                               "metric": "clean_pp", "testbed": -2.3, "sim": 0.1, "same_sign": False}]}))
        claims_check.main(["--report", str(rep)])
        sim = {c["id"]: c for c in json.loads((rep / "claims" / "claims.json").read_text())["claims"]}["simulator"]
        self.assertEqual(sim["status"], "NOT SUPPORTED")
        self.assertIn("sign misses: expC3-ablation EVICON F -> QS clean_pp: testbed -2.30, simulator +0.10", sim["evidence"])
        (rep / "predict" / "predict.json").write_text(json.dumps({"summary": {"effects_scored": 0, "same_sign": 0}}))
        claims_check.main(["--report", str(rep)])
        got = json.loads((rep / "claims" / "claims.json").read_text())
        self.assertEqual({c["id"]: c["status"] for c in got["claims"]}["simulator"], "NO DATA")  # nothing scored is not a failure


class PaperTest(unittest.TestCase):
    def test_s3_expC3_and_safety(self):
        rep = Path(tempfile.mkdtemp())
        (rep / "expC3").mkdir()
        g = {"policy": "EVICON", "cells": 8, "clean_pp": 77.0, "confirmed_pp": 76.5, "queries": 10, "fences": 3, "fences_ok": 2, "stops": 0,
             "stops_terminal": 0, "unresolved_at_end": 0}
        (rep / "expC3" / "expC3.json").write_text(json.dumps({"groups": [dict(g, contract="ZEPHYR_F_DIRECT"), dict(g, contract="ZEPHYR_QA_QUEUE")]}))
        (rep / "safety").mkdir()
        (rep / "safety" / "safety.json").write_text(json.dumps({"summary": {"testbed_runs": 3, "guarded": {"cells": 1234, "admitted": 56789},
                                                                            "guarded_violations": 0, "upper95_per_admitted_command": 5.3e-5,
                                                                            "controls": {"cells": 4, "detecting_cells": 4},
                                                                            "unguarded_dedup": {"cells": 2, "cells_with_violation": 0},
                                                                            "verdict": "NO VIOLATION IN ANY GUARDED CELL"}}))
        N, out = {}, rep / "paper"
        out.mkdir()
        paper_tables.expC3(rep, out, N)
        paper_tables.safety(rep, out, N)
        self.assertIn("direct & $F$ & 77.0", (out / "tab_expC3.tex").read_text())
        self.assertEqual((N["evCthreeFDirectClean"], N["evCthreeQaQueueClean"]), ("77.0", "77.0"))
        self.assertEqual((N["evSafetyCells"], N["evSafetyCommands"], N["evNegDetect"]), ("1{,}234", "56{,}789", "4/4"))
        self.assertEqual(N["evSafetyBound"], "5.3\\times10^{-5}")


class PredictTest(unittest.TestCase):
    def test_s4_c3_predictions(self):
        sim = predict_check.load_sim(Path("/nonexistent"), Path("/nonexistent"), None, REPO / "evaluation" / "sim_predictions" / "expC3_075_summary.csv")
        self.assertIn(("expC3", "ZEPHYR_F_DIRECT", "EVICON"), sim)
        with open(REPO / "evaluation" / "sim_predictions" / "expC3_075_summary.csv", newline="") as fh:
            up = {r["contract"]: float(r["clean_pct"]) for r in csv.DictReader(fh) if r["scenario"] == "owner_up" and r["policy"] == "EVICON"}
        self.assertEqual(sim[("expC3", "ZEPHYR_QA_DIRECT", "EVICON")]["clean_pct"], up["ZEPHYR_QA_DIRECT"])
        self.assertEqual(predict_check.RUN_POINT["expC3-ablation"], ("expC3", False))
        self.assertTrue(all(c in predict_check.SIM_CONTRACT for c in predict_check.C3))


class RebootTest(unittest.TestCase):
    def test_s5_summary(self):
        state = {"device": "zephyr-1", "before": [], "after": [
            {"kind": "query", "sent": {"cmd": "c3", "seq": 3}, "what": "applied before the reboot", "reply": {"outcome": "UNCERTAIN"}},
            {"kind": "query", "sent": {"cmd": "c7", "seq": 7}, "what": "never sent", "reply": {"outcome": "UNCERTAIN"}},
            {"kind": "ping", "sent": {}, "what": "durable cutoff", "reply": {"ok": True, "cutoff": 8}}]}
        plant = [{"device": "zephyr-1", "cmd": "c3"}]
        s = RB.summarize(state, plant)
        self.assertTrue(s["safe"])
        self.assertEqual((s["cutoff_after_reboot"], s["uncertain_after_reboot"]), (8, ["c3", "c7"]))
        state["after"][0]["reply"] = {"outcome": "STOPPED"}  # an applied command certified as never run
        self.assertEqual(RB.summarize(state, plant)["false_stopped"], ["c3"])
        self.assertFalse(RB.summarize(state, plant + [{"device": "zephyr-1", "cmd": "c3"}])["safe"])  # a second effect


class RecordTest(unittest.TestCase):
    def test_s6_results_record(self):
        tmp = Path(tempfile.mkdtemp())
        rep = tmp / "review-1" / "report"
        (rep / "pairs" / "PREREG_expC2_074").mkdir(parents=True)
        (rep / "pairs" / "PREREG_expC2_074" / "RESULTS.md").write_text("# Paired analysis: X\n\n## Hypotheses\n\n| C2-5 | **NOT MET** |\n")
        (rep / "pairs" / "NO_SUCH_PLAN").mkdir()
        (rep / "pairs" / "NO_SUCH_PLAN" / "RESULTS.md").write_text("# y\n")
        bundle = tmp / "b.zip"
        bundle.write_bytes(b"zip")
        notes = tmp / "notes"
        notes.mkdir()
        (notes / "PREREG_expC2_074.md").write_text("the ceiling")
        plan = REPO / "evaluation" / "prereg" / "PREREG_expC2_074.md"
        before = plan.read_bytes()
        out = tmp / "out"
        out.mkdir()
        self.assertEqual(results_record.main(["--report", str(rep), "--bundle", str(bundle), "--out", str(out), "--notes", str(notes)]), 0)
        self.assertEqual(sorted(p.name for p in out.iterdir()), ["RESULTS_expC2_074.md"])
        t = (out / "RESULTS_expC2_074.md").read_text()
        self.assertIn(results_record.sha256(bundle), t)
        self.assertIn("review-1", t)
        self.assertIn("## Analysis (frozen script): X", t)
        self.assertIn("### Hypotheses", t)
        self.assertIn("| C2-5 | **NOT MET** |", t)
        self.assertIn("## Deviations (from the plan)\n\nNone so far.", t)
        self.assertIn("## Interpretation (written after the run; not part of the plan)\n\nthe ceiling", t)
        self.assertEqual(plan.read_bytes(), before)
        self.assertEqual(results_record.record_name("PLAN_expL_072"), "RESULTS_expL_072.md")
        self.assertEqual(results_record.record_name("NEGCTL_072"), "RESULTS_NEGCTL_072.md")


class StopReasonTest(unittest.TestCase):
    def test_s7_stop_reasons(self):
        cell = Path(tempfile.mkdtemp())
        (cell / "raw" / "C").mkdir(parents=True)
        lines = [{"op": "stop", "outcome": "UNCERTAIN", "reason": "TOMBSTONE_TABLE_FULL"}, {"op": "stop", "outcome": "STOPPED", "reason": "STOP_TOMBSTONE"},
                 {"op": "stop", "outcome": "UNCERTAIN", "reason": "FUTURE_BLOCKED_PAST_UNKNOWN"}, {"op": "stop", "outcome": "APPLIED", "cached": True}]
        (cell / "raw" / "C" / "gateway0.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
        (cell / "raw" / "C" / "gateway1.jsonl").write_text(json.dumps({"op": "stop"}) + "\n")
        c = expC2_summary.gateway_stats(cell)
        self.assertEqual((c["stop_UNCERTAIN_TOMBSTONE_TABLE_FULL"], c["stop_UNCERTAIN_FUTURE_BLOCKED_PAST_UNKNOWN"], c["stop_STOPPED"],
                          c["stop_NO_REPLY"], c["device_episodes_with_full_tombstone_table"]), (1, 1, 1, 1, 1))
        self.assertNotIn("stop_APPLIED", c)  # cached answers are not the firmware's


if __name__ == "__main__":
    unittest.main()
