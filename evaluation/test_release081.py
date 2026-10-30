"""Release 081: Theorem 3 without the practice baselines, and plan 077's rule for K-1, K-2 in the paper tables.

F1 t3_attribution lists the cells of RELEASE_ON_TIMEOUT and RELEASE_ON_READ (unsafe by design: they release without
   evidence of non-occurrence, PREREG_review_077 rules RT, RR) apart, leaves them out of the totals and of the
   prediction and counts them in baseline_total; a guarded counterexample in the same run is still reported; the
   baseline set is safety_total's
F2 claims_check's claim T3 leaves the practice baselines out (and names them with their re-openings) and is still
   NOT SUPPORTED by a guarded counterexample; paper_tables' T3 macros count them apart
F3 paper_tables follows PREREG_review_077 for K-1, K-2: not both MET -> the K2 rows of tab_expA (and the K2 points
   of fig_expA_effects) are the plan-077 run's, the 072 K2 replication's macros are left out; both MET -> as before
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "evaluation"))
from evaluation import claims_check as CC  # noqa: E402
from evaluation import confirm_report, paper_tables, t3_attribution  # noqa: E402
from evaluation import safety_total as T  # noqa: E402
from evaluation.test_reports import k0_run, k2_run  # noqa: E402

D = "DECISION_RECORDED"
REOPEN_WITHOUT_EVIDENCE = [{"event": D, "command_id": "c1", "decision": "BLOCK", "reason": "CAPACITY"},
                           {"event": D, "command_id": "c1", "decision": "ADMITTED", "reason": "RELEASED_ON_TIMEOUT"}]
CLEAN = [{"event": D, "command_id": "c1", "decision": "BLOCK", "reason": "CAPACITY"},
         {"event": "EVIDENCE_COMMITTED", "command_id": "c0", "outcome": "APPLIED"},
         {"event": D, "command_id": "c1", "decision": "BLOCK", "reason": "CAPACITY"}]
COUNTER = [{"event": D, "command_id": "c1", "decision": "BLOCK", "reason": "CAPACITY"},
           {"event": "EVIDENCE_COMMITTED", "command_id": "c0", "outcome": "APPLIED"},
           {"event": D, "command_id": "c1", "decision": "ADMITTED", "reason": ""}]


def run_with(root: Path, name: str, cells: dict) -> Path:
    run = root / "campaigns" / name / "20261029T052600"
    for (ct, pol), lines in cells.items():
        d = run / "cells" / "ep0" / ct / pol / "raw" / "A"
        d.mkdir(parents=True)
        (d / "authority0.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    (run / "config.json").write_text(json.dumps({"name": name}))
    return run


class F1T3Attribution(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_baselines_apart(self):
        self.assertEqual(set(t3_attribution.BASELINES), set(T.BASELINES))
        self.assertEqual(set(CC.T3_BASELINES), set(T.BASELINES))
        mixed = run_with(self.tmp / "m", "expRR-queue", {("K2", "RELEASE_ON_READ"): REOPEN_WITHOUT_EVIDENCE, ("K2", "EVICON"): CLEAN})
        only = run_with(self.tmp / "o", "expRT-queue", {("K0", "RELEASE_ON_TIMEOUT"): REOPEN_WITHOUT_EVIDENCE})
        res = t3_attribution.scan([mixed, only])
        m, o = res["expRR-queue/20261029T052600"], res["expRT-queue/20261029T052600"]
        self.assertEqual(m["baseline_groups"], ["K2/RELEASE_ON_READ"])
        self.assertEqual(m["capacity_total"], {})
        self.assertEqual(m["retested_total"], {"BLOCK": 1})
        self.assertEqual(m["baseline_total"], {"NONE": 1})
        self.assertTrue(m["prediction_holds"])
        self.assertEqual((len(m["counterexamples"]), len(m["baseline_examples"])), (0, 1))
        self.assertTrue(o["prediction_holds"])
        md = t3_attribution.markdown(res)
        self.assertIn("K2/RELEASE_ON_READ (practice baseline, excluded)", md)
        self.assertIn("Practice baselines only", md)
        self.assertNotIn("**FAILS**", md)

    def test_guarded_counterexample_still_fails(self):
        run = run_with(self.tmp, "expRT-queue", {("K0", "RELEASE_ON_TIMEOUT"): REOPEN_WITHOUT_EVIDENCE, ("K0F", "EVICON"): COUNTER})
        r = next(iter(t3_attribution.scan([run]).values()))
        self.assertFalse(r["prediction_holds"])
        self.assertEqual(r["capacity_total"], {"APPLIED_ONLY": 1})
        self.assertEqual(len(r["counterexamples"]), 1)
        self.assertIn("K0F/EVICON", r["counterexamples"][0])
        self.assertIn("**FAILS**", t3_attribution.markdown({"x": r}))


class F2ClaimAndMacros(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def report(self, cells):
        rep = self.tmp / "report"
        shutil.rmtree(rep, ignore_errors=True)
        run = run_with(self.tmp / "r", "expRT-queue", cells)
        self.assertEqual(t3_attribution.main(["--out", str(rep / "t3"), str(run)]), 0)
        shutil.rmtree(self.tmp / "r")
        return rep

    def test_claim(self):
        rep = self.report({("K0", "RELEASE_ON_TIMEOUT"): REOPEN_WITHOUT_EVIDENCE, ("K0F", "EVICON"): CLEAN})
        c = CC.claim_t3(rep)
        self.assertEqual(c["status"], "SUPPORTED")
        self.assertIn("practice baselines left out", c["evidence"])
        self.assertIn("1 re-openings without evidence of non-occurrence in 1 runs", c["evidence"])
        N = {}
        paper_tables.t3(rep, self.tmp, N)
        self.assertEqual((N["evTThreeCounterexamples"], N["evTThreeBaselineReopen"], N["evTThreeRetested"]), ("0", "1", "1"))
        rep = self.report({("K0", "RELEASE_ON_TIMEOUT"): REOPEN_WITHOUT_EVIDENCE, ("K0F", "EVICON"): COUNTER})
        self.assertEqual(CC.claim_t3(rep)["status"], "NOT SUPPORTED")


class F3K2Rule(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.rep = self.tmp / "report"
        confirm_report.main(["--k0", str(k0_run(self.tmp / "a", "20261016T234202", 29.9)), "--k2", str(k2_run(self.tmp / "a")),
                             "--k0-rep", str(k0_run(self.tmp / "b", "20261020T073331", 30.7)), "--out", str(self.rep / "confirm_expA")])

    def pairs(self, k1, k2):
        def h(i, pol, mean, lo, hi, verdict):
            return {"id": i, "test": "equivalent", "spec": {"id": i, "policy": pol, "from": "K2", "to": "K3F"}, "n": 5, "mean": mean,
                    "t95": [lo, hi], "verdict": verdict}
        p = self.rep / "pairs" / "PREREG_review_077"
        p.mkdir(parents=True, exist_ok=True)
        (p / "results.json").write_text(json.dumps({"hypotheses": [h("K-1", "EVICON", 0.0, 0.0, 0.0, k1),
                                                                    h("K-2", "RES_PERIODIC", 3.965, -1.953, 9.884, k2)]}))

    def test_not_met_replaces_A(self):
        self.pairs("MET", "NOT MET")
        self.assertEqual(sorted(paper_tables.k2_077(self.rep)), ["EVICON", "RES_PERIODIC"])
        N, out = {}, self.tmp / "p"
        out.mkdir()
        msg = paper_tables.expA(self.rep, out, N)
        self.assertIn("K-1, K-2", msg)
        tab = (out / "tab_expA.tex").read_text()
        self.assertIn("K-2 (periodic reconciliation, release-077 runtime, $W$ 6) & K2 $\\to$ K3F, pp & +3.96 [$-$1.95, +9.88], not equivalent & --", tab)
        self.assertIn("K-1 (EviCon, release-077 runtime", tab)
        self.assertNotIn("H-A2", tab)
        self.assertFalse({"evATwoEviconPP", "evATwoResPP", "evKTwoReplicates"} & set(N))
        self.assertIn("evAEviconPP", N)  # experiment A's K0 results stay

    def test_both_met_keeps_A(self):
        self.pairs("MET", "MET")
        self.assertEqual(paper_tables.k2_077(self.rep), {})
        N, out = {}, self.tmp / "p"
        out.mkdir()
        paper_tables.expA(self.rep, out, N)
        self.assertIn("H-A2", (out / "tab_expA.tex").read_text())
        self.assertIn("evATwoResPP", N)

    def test_no_plan_077(self):
        self.assertEqual(paper_tables.k2_077(self.rep), {})


if __name__ == "__main__":
    unittest.main()
