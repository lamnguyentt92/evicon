"""Release 073 analysis additions on synthetic runs: predict_check.py (levels, effects, agreement against
the simulator predictions shipped in evaluation/sim_predictions/), the paper outputs built from it
(tab_predict.tex, tab_levels.tex, fig_phase.pdf), expC_summary.py, and t3_attribution.py leaving the
negative-control contracts out of every total."""
import csv
import json
import tempfile
import unittest
from pathlib import Path

from evaluation import expC2_summary, expC_summary, fidelity_check, paper_tables, predict_check, t3_attribution
from evaluation.test_pairs_report import make_run

REPO = Path(__file__).resolve().parents[1]


class PredictTest(unittest.TestCase):
    def test_predictions_against_synthetic_runs(self):
        tmp = Path(tempfile.mkdtemp())
        # the simulator predicts K0 37.5 / K0F 72.7 clean at queue W = 6 (EVICON); the synthetic testbed: 30 / 61
        w6 = make_run(tmp, "expW-queue-w6", {"K0": 30, "K0F": 61})
        neg = make_run(tmp, "negctl-072", {"K0": 30})
        res = predict_check.check([str(w6), str(neg)], predict_check.load_sim(REPO / "evaluation" / "sim_predictions" / "sweep_072_summary.csv",
                                                                             REPO / "evaluation" / "sim_predictions" / "expC_073_summary.csv"))
        self.assertTrue(any("negctl-072" in s for s in res["skipped"]))
        eff = {(e["from"], e["to"], e["metric"]): e for e in res["effects"]}
        e = eff[("K0", "K0F", "clean_pp")]
        self.assertAlmostEqual(e["testbed"], 31.0, places=1)
        self.assertAlmostEqual(e["sim"], 72.66 - 37.46, places=2)
        self.assertTrue(e["same_sign"])
        self.assertEqual(res["summary"]["effects_scored"], 2)
        lv = {(x["contract"], x["policy"]): x for x in res["levels"]}
        self.assertAlmostEqual(lv[("K0", "EVICON")]["clean_pp_sim"], 37.46)
        self.assertLess(res["summary"]["level_bias_clean_pp"], 0)
        # sign disagreement and the +-2 pp "no effect" band
        flat = make_run(tmp / "b", "expW-queue-winf", {"K0": 75, "K0F": 74})
        r2 = predict_check.check([str(flat)], predict_check.load_sim(REPO / "evaluation" / "sim_predictions" / "sweep_072_summary.csv", Path("/none")))
        e2 = next(x for x in r2["effects"] if x["metric"] == "clean_pp")
        self.assertTrue(e2["same_sign"])  # -1 pp against +1.0 pp: both inside the no-effect band
        self.assertEqual(predict_check.spearman([1, 2, 3, 4], [10, 20, 30, 40]), 1.0)

    def test_paper_outputs(self):
        tmp = Path(tempfile.mkdtemp())
        w6 = make_run(tmp, "expW-queue-w6", {"K0": 30, "K0F": 61})
        rep = tmp / "report"
        self.assertEqual(predict_check.main(["--out", str(rep / "predict"), str(w6)]), 0)
        out = tmp / "paper"
        out.mkdir()
        N = {}
        msg = paper_tables.predict(rep, out, N)
        self.assertIn("2 effects scored", msg)
        tab = (out / "tab_predict.tex").read_text()
        self.assertIn("K0$\\to$K0F, clean", tab)
        self.assertEqual(N["evPredEffects"], "2")
        self.assertTrue((out / "tab_levels.tex").is_file())
        fig = paper_tables.fig_phase(rep, out)
        if paper_tables._plt() is not None:
            self.assertTrue((out / "fig_phase.pdf").is_file(), fig)
            with open(out / "fig_phase_data.csv", newline="") as fh:
                rows = list(csv.DictReader(fh))
            self.assertTrue(any(r["source"].startswith("testbed") for r in rows))
            self.assertEqual({r["channel"] for r in rows}, {"queue", "direct"})


class ExpCSummaryTest(unittest.TestCase):
    def test_summary(self):
        tmp = Path(tempfile.mkdtemp())
        run = make_run(tmp, "expC-minio", {"MINIO_PLAIN": 30, "MINIO_COND": 75})
        with open(run / "runtime_episodes.csv", newline="") as fh:
            rows = list(csv.DictReader(fh))
        for r in rows:
            r.update(retry_suppressed_t4=5, dispatch_attempts=100)
        rows[0]["duplicate_effects"] = 3
        with open(run / "runtime_episodes.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        cell = run / "cells" / "ep0" / "MINIO_COND" / "EVICON" / "raw" / "truth"
        cell.mkdir(parents=True)
        (cell / "truth.json").write_text(json.dumps({"summary": {"tombstones": 4, "effect_versions": 70}, "truth": {}}))
        s = expC_summary.summarize(run)
        g = {(x["contract"], x["policy"]): x for x in s["groups"]}
        self.assertEqual([x["contract"] for x in s["groups"]], ["MINIO_PLAIN", "MINIO_COND"])
        self.assertEqual(g[("MINIO_COND", "EVICON")]["tombstones"], 4)
        self.assertEqual(sum(x["duplicate_effects"] for x in s["groups"]), 3)
        self.assertIn("MINIO_COND", expC_summary.markdown(s))
        out = tmp / "paper"
        out.mkdir()
        (tmp / "rep" / "expC").mkdir(parents=True)
        (tmp / "rep" / "expC" / "expC.json").write_text(json.dumps(s))
        N = {}
        paper_tables.expC(tmp / "rep", out, N)
        self.assertIn("If-None-Match", (out / "tab_expC.tex").read_text())
        self.assertTrue(all(k.isalpha() for k in N))


class Release074Test(unittest.TestCase):
    """Experiment C2 (release 074): predictions, summary and the emulated-against-real comparison."""

    def test_c2_predictions(self):
        tmp = Path(tempfile.mkdtemp())
        run = make_run(tmp, "expC2-zephyr", {"ZEPHYR_PLAIN": 25, "ZEPHYR_CUTOFF": 62, "ZEPHYR_STOP": 76})
        sim = predict_check.load_sim(REPO / "evaluation" / "sim_predictions" / "sweep_072_summary.csv",
                                     REPO / "evaluation" / "sim_predictions" / "expC_073_summary.csv",
                                     REPO / "evaluation" / "sim_predictions" / "expC2_074_summary.csv")
        res = predict_check.check([str(run)], sim)
        eff = {(e["from"], e["to"], e["metric"]): e for e in res["effects"]}
        e = eff[("ZEPHYR_PLAIN", "ZEPHYR_CUTOFF", "clean_pp")]
        self.assertAlmostEqual(e["testbed"], 37.0, places=1)
        self.assertAlmostEqual(e["sim"], 76.56 - 37.46, places=2)
        self.assertTrue(e["same_sign"])
        e5 = eff[("ZEPHYR_CUTOFF", "ZEPHYR_STOP", "clean_pp")]
        self.assertTrue(e5["same_sign"])  # 073's two-way rule: +14 against +0.3 counts as the same sign
        self.assertFalse(e5["direction_agrees"])  # three-way (074): the simulator predicted no effect
        self.assertEqual(res["summary"]["direction_agrees"], res["summary"]["effects_scored"] - 2)  # clean and confirmed

    def test_c2_summary_and_fidelity(self):
        tmp = Path(tempfile.mkdtemp())
        c2 = make_run(tmp / "a", "expC2-zephyr", {"ZEPHYR_PLAIN": 25, "ZEPHYR_CUTOFF": 62, "ZEPHYR_STOP": 76}, eps=4, sched="same")
        w = make_run(tmp / "b", "expW-queue-w6", {"K0": 28, "K0F": 60}, eps=6, sched="same")
        cell = c2 / "cells" / "ep0" / "ZEPHYR_CUTOFF" / "EVICON"
        (cell / "raw" / "A" / "authority0.jsonl").write_text("".join(json.dumps(
            {"event": "OUTBOX_RETRY_SUPPRESSED_T4", "domain_id": "pump0-1", "command_id": c}) + "\n" for c in ("d0-c1", "d0-c1", "d0-c2")))
        (cell / "raw" / "C").mkdir(parents=True)
        (cell / "raw" / "C" / "gateway0.jsonl").write_text("".join(json.dumps(x) + "\n" for x in (
            {"op": "barrier", "result": "OK", "uncertain": []}, {"op": "barrier", "result": "OK", "uncertain": ["d0-c9"]},
            {"op": "barrier", "result": "TIMEOUT"}, {"op": "start"}, {"op": "crash"}, {"op": "online", "v": False})))
        (c2 / "contracts").mkdir()
        (c2 / "contracts" / "ZEPHYR_CUTOFF.json").write_text(json.dumps({"id": "m", "source": "MEASURED", "capabilities": ["D_h", "Q_A", "F"]}))
        s = expC2_summary.summarize(c2)
        g = {(x["contract"], x["policy"]): x for x in s["groups"]}
        self.assertEqual([x["contract"] for x in s["groups"]], ["ZEPHYR_PLAIN", "ZEPHYR_CUTOFF", "ZEPHYR_STOP"])
        self.assertEqual(g[("ZEPHYR_CUTOFF", "EVICON")]["t4_withheld_commands"], 2)
        self.assertEqual(g[("ZEPHYR_CUTOFF", "EVICON")]["gateway"],
                         {"barrier_OK": 1, "barrier_OK_WITH_UNCERTAIN": 1, "barrier_TIMEOUT": 1, "gateway_crashes": 1, "gateway_starts": 1,
                          "link_down": 1})
        self.assertIn("ZEPHYR_CUTOFF", expC2_summary.markdown(s))
        out = tmp / "paper"
        out.mkdir()
        (tmp / "rep" / "expC2").mkdir(parents=True)
        (tmp / "rep" / "expC2" / "expC2.json").write_text(json.dumps(s))
        N = {}
        self.assertIn("3 groups", paper_tables.expC2(tmp / "rep", out, N))
        self.assertIn("cutoff", (out / "tab_expC2.tex").read_text())
        self.assertTrue(all(k.isalpha() for k in N))
        self.assertIn("evCtwoCutoffEviconClean", N)
        res = fidelity_check.compare(fidelity_check.parse(f"{c2}:ZEPHYR_PLAIN:EVICON={w}:K0:EVICON"), {})
        m = res["metrics"]["clean_pp"]
        self.assertEqual((m["real_n"], m["emulated_n"]), (4, 6))
        self.assertEqual(m["paired"]["n"], 4)  # episodes 0-3 replayed the same schedule
        self.assertAlmostEqual(m["paired"]["diff"], -3.0, places=3)
        self.assertTrue(-4 < m["unpaired"]["diff"] < -2)
        self.assertLess(m["unpaired"]["tost"]["10.0"], 0.05)


class T3NegTest(unittest.TestCase):
    def test_negative_controls_left_out(self):
        tmp = Path(tempfile.mkdtemp())
        run = tmp / "campaigns" / "negctl-072" / "20261021T000000"
        lines = [{"event": "DECISION_RECORDED", "command_id": "c1", "decision": "BLOCK", "reason": "CAPACITY"},
                 {"event": "EVIDENCE_COMMITTED", "command_id": "c0", "outcome": "APPLIED"},
                 {"event": "DECISION_RECORDED", "command_id": "c1", "decision": "ADMITTED", "reason": ""}]
        for ct in ("NEG_SEAL", "K0F"):
            d = run / "cells" / "ep0" / ct / "EVICON" / "raw" / "A"
            d.mkdir(parents=True)
            (d / "authority0.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
        (run / "config.json").write_text(json.dumps({"name": "negctl-072"}))
        res = t3_attribution.scan([run])
        r = next(iter(res.values()))
        self.assertEqual(r["excluded_groups"], ["NEG_SEAL/EVICON"])
        self.assertEqual(r["capacity_total"].get("APPLIED_ONLY"), 1)  # from K0F only, not doubled by NEG_SEAL
        self.assertEqual(r["retested_total"].get("ADMITTED"), 1)
        self.assertFalse(r["prediction_holds"])
        self.assertEqual(len(r["negative_control_examples"]), 1)
        self.assertEqual(len(r["counterexamples"]), 1)
        self.assertIn("negative control, excluded", t3_attribution.markdown(res))


if __name__ == "__main__":
    unittest.main()
