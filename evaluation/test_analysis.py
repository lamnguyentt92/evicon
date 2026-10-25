"""Unit tests for evaluation/analyze.py and power.py (synthetic data)."""
import csv
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyze  # noqa: E402
import power  # noqa: E402


def read_csv(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def write(rows, path):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(dict.fromkeys(k for r in rows for k in r)), restval="")
        w.writeheader()
        w.writerows(rows)


class AnalyzeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        rows = []
        for ep in range(40):
            base = 100 + (ep % 7)
            for pol, delta, contract in (("ORACLE", 20, "sim-k0"), ("B2_LEDGER", 0, "sim-k0"), ("EVICON", 0, "sim-k0"),
                                         ("EVICON", 15, "sim-k0+F"), ("RES_IMMEDIATE", 12, "sim-k0+F"), ("B0_NAIVE_NEW_ID", 18, "sim-k0")):
                regime = "iot/" + contract
                rows.append({"regime": regime, "policy": pol, "episode": ep, "contract": contract, "level": "K0",
                             "offered": 200, "clean_fulfilled": base + delta, "stranded_resolved_unit_sec": 50 - delta,
                             "stranded_censored_unit_sec": 0, "bound_violations": 1 if pol.startswith("B0") and ep == 3 else 0,
                             "duplicate_effects": 0, "false_stopped": 0, "late_effects": 2 if ep == 5 else 0,
                             "queries": 0, "stops": 0, "fences": 1 if contract.endswith("+F") else 0})
        rows.append({**rows[-1], "episode": 99, "cell_status": "INVALID:fault_injection_failed"})
        write(rows, self.tmp / "episodes.csv")
        (self.tmp / "manifest.json").write_text(json.dumps({"scope": "SIMULATION_ONLY"}))

    def test_pairing_contracts_and_safety_separation(self):
        out = self.tmp / "out"
        analyze.main(["--csv", str(self.tmp / "episodes.csv"), "--reference", "B2_LEDGER", "--reference", "RES_IMMEDIATE",
                      "--out", str(out), "--bootstrap", "300"])
        summ = {(r["policy"], r["contract"]): r for r in read_csv(out / "summary.csv")}
        # the fence upgrade is a different contract, never merged into the base contract (review RV07)
        self.assertIn(("EVICON", "sim-k0+F"), summ)
        self.assertIn(("EVICON", "sim-k0"), summ)
        self.assertEqual(summ[("EVICON", "sim-k0")]["activation"], "NOT_ACTIVATED")
        self.assertEqual(summ[("EVICON", "sim-k0+F")]["activation"], "ACTIVATED")
        g = float(summ[("EVICON", "sim-k0")]["gap_to_full_info_reference"])
        self.assertAlmostEqual(g, 1 - sum(100 + e % 7 for e in range(40)) / (sum(100 + e % 7 for e in range(40)) + 20 * 40), places=4)
        # experiment B: same effective contract only
        paired = [r for r in read_csv(out / "paired.csv") if r["metric"] == "clean_fulfilled"]
        self.assertEqual({(r["policy"], r["reference"], r["contract"]) for r in paired},
                         {("EVICON", "B2_LEDGER", "sim-k0"), ("B0_NAIVE_NEW_ID", "B2_LEDGER", "sim-k0"), ("ORACLE", "B2_LEDGER", "sim-k0"),
                          ("EVICON", "RES_IMMEDIATE", "sim-k0+F")})
        evb = next(r for r in paired if r["policy"] == "EVICON" and r["reference"] == "RES_IMMEDIATE")
        self.assertEqual(float(evb["mean_diff"]), 3.0)
        # experiment A: the same policy on c+F vs c
        up = [r for r in read_csv(out / "contract_upgrade.csv") if r["metric"] == "clean_fulfilled"]
        self.assertEqual(len(up), 1)
        self.assertEqual((up[0]["policy"], up[0]["contract"], up[0]["upgraded"], float(up[0]["mean_diff"])), ("EVICON", "sim-k0", "sim-k0+F", 15.0))
        rep = json.loads((out / "report.json").read_text())
        self.assertEqual([g[3] for g in rep["groups_with_safety_violations"]], ["B0_NAIVE_NEW_ID"])  # late effects are not "unsafe"
        self.assertGreater(rep["groups_with_late_effects"], 0)
        self.assertEqual(rep["groups_with_fulfilled_late"], 0)
        self.assertEqual(len(rep["invalid_cells_excluded"]), 1)
        self.assertIn(("SIMULATION_ONLY", "iot/sim-k0", "sim-k0", "EVICON"), [tuple(x) for x in rep["resolver_groups_not_activated"]])

    def test_holm_monotone(self):
        adj = analyze.holm([0.01, 0.04, 0.03, 0.5])
        self.assertAlmostEqual(adj[0], 0.04)
        self.assertAlmostEqual(adj[2], 0.09)
        self.assertAlmostEqual(adj[1], 0.09)  # monotone step-down
        self.assertAlmostEqual(adj[3], 0.5)

    def test_paired_p(self):
        self.assertEqual(analyze.paired_p([0, 0, 0]), 1.0)
        self.assertLess(analyze.paired_p([1.0 + 0.01 * i for i in range(30)]), 1e-6)
        self.assertTrue(math.isnan(analyze.paired_p([1])))

    def test_power_formula(self):
        # sd 10, delta 5, alpha .05, power .8, m 1 -> ((1.96+0.8416)*2)^2 = 31.4 -> 32
        self.assertEqual(power.required_n(10, 5, 0.05, 0.8, 1), 32)
        self.assertGreater(power.required_n(10, 5, 0.05, 0.8, 10), 32)
        self.assertIsNone(power.required_n(0, 5, 0.05, 0.8, 1))  # zero variance never yields "n = 2"

    def test_power_flags_not_activated(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            power.main(["--csv", str(self.tmp / "episodes.csv"), "--policy", "EVICON", "--reference", "B2_LEDGER", "--delta", "1"])
        self.assertIn("NOT_ACTIVATED", buf.getvalue())


def _runtime_rows(episodes, contracts=("K0", "K0F", "K2", "K3F"), policies=("M1", "EVICON", "RES_PERIODIC")):
    """Runtime-shaped rows: one regime for every contract (no sweep suffix)."""
    rows = []
    for ep in range(episodes):
        for c in contracts:
            for pol in policies:
                gain = 40 if c == "K0F" else (1 if c == "K3F" else 0)
                rows.append({"regime": "expA-dosing", "contract": c, "policy": pol, "episode": ep, "devices": 4, "cell_status": "VALID",
                             "scope": "TESTBED", "offered": 600, "clean_fulfilled": 300 + (ep % 5) * 3 + gain + (ep % 3 if pol == "EVICON" else 0),
                             "fulfilled_late": 2 if c == "K2" else 0, "late_effects": 0, "late_effects_after_client_gave_up": 0,
                             "bound_violations": 0, "duplicate_effects": 0, "false_stopped": 0, "false_applied": 0, "logical_duplicates": 0,
                             "stranded_resolved_unit_sec": 10, "stranded_censored_unit_sec": 0, "queries": 0,
                             "stops": 0, "fences": 3 if c.endswith("F") and pol != "M1" else 0,
                             "activated": (c.endswith("F") and pol != "M1") if pol != "M1" else ""})
    return rows


class RuntimeAnalysisTest(unittest.TestCase):
    """Release 065: runtime experiment A pairs, late-effect labels, TOST, and
    power planning that never stays silent."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def _power(self, *args):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            power.main(list(args))
        return buf.getvalue()

    def test_runtime_upgrade_pairs_and_late_labels(self):
        write(_runtime_rows(8), self.tmp / "runtime_episodes.csv")
        out = self.tmp / "out"
        analyze.main(["--csv", str(self.tmp / "runtime_episodes.csv"), "--reference", "M1", "--out", str(out),
                      "--bootstrap", "200", "--margin-pp", "2"])
        up = {(r["policy"], r["contract"], r["upgraded"]): r for r in read_csv(out / "contract_upgrade.csv") if r["metric"] == "clean_fulfilled"}
        self.assertEqual(set(up), {(p, b, u) for p in ("M1", "EVICON", "RES_PERIODIC") for b, u in (("K0", "K0F"), ("K2", "K3F"))})
        self.assertEqual(float(up[("EVICON", "K0", "K0F")]["mean_diff"]), 40.0)
        # K2 -> K3F: +1 request of 600 is inside a 2 pp margin (12 requests): equivalent
        self.assertEqual(up[("EVICON", "K2", "K3F")]["equivalent_at_0.05"], "True")
        self.assertEqual(up[("EVICON", "K0", "K0F")]["equivalent_at_0.05"], "False")
        rep = json.loads((out / "report.json").read_text())
        # fulfilled_late (a deadline miss) is not a late effect (validity violation)
        self.assertEqual(rep["groups_with_late_effects"], 0)
        self.assertEqual(rep["late_effects_total"], 0)
        self.assertEqual(rep["groups_with_fulfilled_late"], 3)
        self.assertEqual(rep["fulfilled_late_total"], 2 * 8 * 3)

    def test_escalate_only_is_no_action_in_contract_066(self):
        # 065 testbed: K0 EVICON, K0 RES_PERIODIC and K2 RES_PERIODIC were listed as
        # "not activated" although the contract gives them no action (every decision ESCALATE)
        rows = _runtime_rows(5)
        for r in rows:
            if r["policy"] != "M1":
                acting = r["contract"].endswith("F")
                r["decisions_by_action"] = json.dumps({"FENCE": 3} if acting else {"ESCALATE": 7})
        rows.append(dict(rows[1], contract="K9", decisions_by_action="", activated="False"))  # silent: still NOT_ACTIVATED
        write(rows, self.tmp / "r.csv")
        out = self.tmp / "out"
        analyze.main(["--csv", str(self.tmp / "r.csv"), "--reference", "M1", "--out", str(out), "--bootstrap", "50"])
        rep = json.loads((out / "report.json").read_text())
        noact = {(g[2], g[3]) for g in rep["resolver_groups_no_action_in_contract"]}
        self.assertEqual(noact, {("K0", "EVICON"), ("K0", "RES_PERIODIC"), ("K2", "EVICON"), ("K2", "RES_PERIODIC")})
        self.assertEqual([(g[2], g[3]) for g in rep["resolver_groups_not_activated"]], [("K9", "EVICON")])

    def test_power_labels_no_action_and_infeasible_067(self):
        # 066: K0/K2 groups where the contract gives the resolver nothing were NOT_ACTIVATED, and an
        # equivalence design whose pilot difference sat at the margin asked n = 813042 (10 min of bootstrap)
        rows = _runtime_rows(5, contracts=("K0", "K0F"), policies=("EVICON", "RES_PERIODIC"))
        for r in rows:
            acting = r["contract"] == "K0F"
            r["decisions_by_action"] = json.dumps({"FENCE": 3} if acting else {"ESCALATE": 9})
            if acting:  # EVICON - RES_PERIODIC = 12.9 -/+ 5 per episode: mean 11.9, margin 12 (2 pp of 600)
                r["clean_fulfilled"] = 300.0 + ((12.9 + (5 if r["episode"] % 2 else -5)) if r["policy"] == "EVICON" else 0.0)
        write(rows, self.tmp / "p.csv")
        js = self.tmp / "power.json"
        self._power("--csv", str(self.tmp / "p.csv"), "--policy", "EVICON", "--reference", "RES_PERIODIC",
                    "--delta-pp", "2", "--equivalence", "--json", str(js))
        st = {r["contract"]: r["status"] for r in json.loads(js.read_text())["rows"]}
        self.assertEqual(st["K0"], "NO_ACTION_IN_CONTRACT")
        self.assertEqual(st["K0F"], "INFEASIBLE")

    def test_power_reports_insufficient_pilot(self):
        # 064: a 2-episode pilot printed only the header (every group skipped silently)
        write(_runtime_rows(2), self.tmp / "p.csv")
        js = self.tmp / "power.json"
        text = self._power("--csv", str(self.tmp / "p.csv"), "--policy", "EVICON", "--reference", "RES_PERIODIC",
                           "--delta", "0.05", "--relative", "--comparisons", "6", "--json", str(js))
        self.assertIn("INSUFFICIENT_PILOT", text)
        rows = json.loads(js.read_text())["rows"]
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(r["status"] == "INSUFFICIENT_PILOT" for r in rows))

    def test_power_no_pairs_is_explicit(self):
        write(_runtime_rows(5), self.tmp / "p.csv")
        js = self.tmp / "power.json"
        text = self._power("--csv", str(self.tmp / "p.csv"), "--policy", "EVICON", "--reference", "RES_IMMEDIATE",
                           "--delta", "1", "--json", str(js))
        self.assertIn("NO_PAIRS", text)
        self.assertEqual(json.loads(js.read_text())["status"], "NO_PAIRS")

    def test_power_upgrade_mode_sizes_experiment_a(self):
        write(_runtime_rows(6), self.tmp / "p.csv")
        js = self.tmp / "power.json"
        self._power("--csv", str(self.tmp / "p.csv"), "--upgrade", "K0:K0F", "--upgrade", "K2:K3F", "--policy", "EVICON",
                    "--delta-pp", "2", "--comparisons", "3", "--json", str(js))
        rows = {r["contract"]: r for r in json.loads(js.read_text())["rows"]}
        self.assertEqual(set(rows), {"K0->K0F", "K2->K3F"})
        self.assertEqual(rows["K0->K0F"]["status"], "UNDETERMINED")  # constant +40: zero variance
        self.assertEqual(rows["K0->K0F"]["mean_d"], 40.0)
        self.assertEqual(rows["K0->K0F"]["delta"], 12.0)  # 2 pp of 600 offered

    def test_power_equivalence_design(self):
        self.assertIsNone(power.required_n_equivalence(0, 5, 0, 0.05, 0.8, 1))
        self.assertEqual(power.required_n_equivalence(10, 5, 6, 0.05, 0.8, 1), 0)  # pilot outside the margin
        # sd 10, margin 5, mu 0: ((1.645 + 1.2816) * 2)^2 = 34.3 -> 35
        self.assertEqual(power.required_n_equivalence(10, 5, 0, 0.05, 0.8, 1), 35)

    def test_regime_base(self):
        self.assertEqual(analyze.regime_base("iot-dosing/sim-k0+F", "sim-k0+F"), "iot-dosing")
        self.assertEqual(analyze.regime_base("iot-dosing/sim-k0", "sim-k0"), "iot-dosing")
        self.assertEqual(analyze.regime_base("expA-dosing", "K0F"), "expA-dosing")
        self.assertEqual(power.regime_base("iot-dosing/sim-k2+F", "sim-k2+F"), "iot-dosing")


if __name__ == "__main__":
    unittest.main()
