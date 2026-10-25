"""Unit tests for evaluation/sim_to_real.py (synthetic data, release 065)."""
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sim_to_real  # noqa: E402
from test_analysis import write  # noqa: E402

# expected clean fulfilment rates; the F-upgrade helps only without a forward path
RATES = {"K0": 0.55, "K0F": 0.70, "K2": 0.76, "K3F": 0.76}
SIM = {"sim-k0": "K0", "sim-k0+F": "K0F", "sim-k2": "K2", "sim-k2+F": "K3F"}


def rows(world, episodes, offered, flip=None, invalid_ep=None):
    out = []
    names = SIM if world == "sim" else {c: c for c in RATES}
    for ep in range(episodes):
        for name, c in names.items():
            for pol in ("M1", "EVICON"):
                rate = RATES[c] if pol == "EVICON" or c in ("K2", "K3F") else RATES["K0"] if c == "K0F" else RATES[c]
                if flip and (c, pol) == flip:
                    rate = RATES["K0"] - 0.1  # the testbed contradicts the simulator
                noise = ((ep * 7 + len(c)) % 5 - 2) * 0.004
                r = {"regime": "expA-dosing" + ("/" + name if world == "sim" else ""), "contract": name, "policy": pol,
                     "episode": ep, "offered": offered, "clean_fulfilled": round((rate + noise) * offered, 3)}
                if world == "runtime":
                    r["cell_status"] = "INVALID:x" if ep == invalid_ep else "VALID"
                out.append(r)
    return out


class SimToRealTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def run_compare(self, runtime_rows):
        write(runtime_rows, self.tmp / "rt.csv")
        write(rows("sim", 50, 150), self.tmp / "sim.csv")
        return sim_to_real.compare([str(self.tmp / "rt.csv")], [str(self.tmp / "sim.csv")], n_boot=300)

    def test_matching_worlds_pass(self):
        rep = self.run_compare(rows("runtime", 6, 600, invalid_ep=5))
        self.assertTrue(rep["check"]["pass"], rep["check"])
        e = {(x["policy"], x["base"]): x for x in rep["effects"]}
        self.assertEqual(e[("EVICON", "K0")]["runtime"]["sign"], 1)
        self.assertEqual(e[("EVICON", "K0")]["sim"]["sign"], 1)
        self.assertEqual(e[("EVICON", "K0")]["runtime"]["pairs"], 5)  # the INVALID episode is excluded
        self.assertAlmostEqual(e[("EVICON", "K0")]["runtime"]["mean_pp"], 15.0, delta=1.0)
        self.assertEqual(e[("M1", "K0")]["runtime"]["sign"], 0)  # no backward evidence: F cannot help M1
        self.assertGreaterEqual(rep["check"]["tau_b_margin_pooled"], 0.6)
        self.assertIn("SR check (066): **PASS**", sim_to_real.markdown(rep))

    def test_tiny_effect_is_practically_null_066(self):
        # 065 testbed run: a +0.09 pp simulator effect (n = 200, CI excludes 0) against a 0.00 pp
        # testbed effect counted as a sign disagreement; with the 2 pp margin both are null
        self.assertEqual(sim_to_real.sign_margin(0.09, 0.03, 0.16, 2.0), 0)
        self.assertEqual(sim_to_real.sign(0.03, 0.16), 1)
        self.assertEqual(sim_to_real.sign_margin(31.9, 25.4, 37.4, 2.0), 1)
        self.assertEqual(sim_to_real.sign_margin(-5.0, -9.0, -1.0, 2.0), -1)
        self.assertEqual(sim_to_real.sign_margin(1.0, -1.0, 3.0, 2.0), "?")  # neither clearly null nor clearly positive

    def test_near_ties_do_not_decide_the_ranking_066(self):
        # 065 testbed run: 0.7723 / 0.7728 / 0.7737 in the simulator against 0.7814 / 0.7810 / 0.5979
        rt, sm = [0.2793, 0.5979, 0.7814, 0.7810], [0.6829, 0.7737, 0.7723, 0.7728]
        self.assertLess(sim_to_real.kendall_tau_b(rt, sm), 0.6)
        self.assertGreaterEqual(sim_to_real.kendall_tau_b(rt, sm, tie=0.02), 0.6)

    def test_sign_disagreement_fails(self):
        rep = self.run_compare(rows("runtime", 6, 600, flip=("K0F", "EVICON")))
        self.assertFalse(rep["check"]["all_signs_agree"])
        self.assertFalse(rep["check"]["pass"])

    def test_insufficient_pairs_fail(self):
        rep = self.run_compare(rows("runtime", 1, 600))
        self.assertTrue(all(x["runtime"]["status"] == "INSUFFICIENT" for x in rep["effects"]))
        self.assertFalse(rep["check"]["pass"])

    def test_kendall_tau_b(self):
        self.assertAlmostEqual(sim_to_real.kendall_tau_b([1, 2, 3, 4], [1, 2, 3, 4]), 1.0)
        self.assertAlmostEqual(sim_to_real.kendall_tau_b([1, 2, 3, 4], [4, 3, 2, 1]), -1.0)
        self.assertAlmostEqual(sim_to_real.kendall_tau_b([1, 2, 2, 3], [1, 2, 3, 4]), 5 / math.sqrt(30), places=6)
        self.assertTrue(math.isnan(sim_to_real.kendall_tau_b([1], [1])))

    def test_cli_writes_outputs(self):
        write(rows("runtime", 4, 600), self.tmp / "rt.csv")
        write(rows("sim", 20, 150), self.tmp / "sim.csv")
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            sim_to_real.main(["--runtime", str(self.tmp / "rt.csv"), "--sim", str(self.tmp / "sim.csv"), "--out", str(self.tmp / "o"),
                              "--bootstrap", "200"])
        rep = json.loads((self.tmp / "o" / "sim_to_real.json").read_text())
        self.assertEqual(rep["mapping"]["K3F"], "sim-k2+F")
        self.assertTrue((self.tmp / "o" / "sim_to_real.md").exists())


if __name__ == "__main__":
    unittest.main()
