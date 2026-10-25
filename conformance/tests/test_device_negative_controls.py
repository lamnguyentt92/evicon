"""Negative controls: EviCon-Probe must detect weaker contracts on evicon-device
(no dedup, finite horizon, cutoff without dedup) and owner expiry, with truth
read from evicon-plant (never from the device)."""
import json
import tempfile
import unittest
from pathlib import Path

from evicon_probe.runner import run_campaign

from .helpers import Proc, free_port, go_binary, wait_http


def have_go():
    return go_binary("evicon-device") is not None and go_binary("evicon-plant") is not None


@unittest.skipUnless(have_go(), "Go toolchain not available")
class DeviceNegativeControls(unittest.TestCase):
    def run_preset(self, preset, probes, device_flags=(), declared=None, extra_adapter=None, params=None):
        tmp = tempfile.mkdtemp(prefix=f"evicon-probe-dev-{preset}-")
        pport, dport, aport = free_port(), free_port(), free_port()
        plant = Proc([str(go_binary("evicon-plant")), "-log", f"{tmp}/plant.jsonl", "-http", f"127.0.0.1:{pport}", "-unix", f"{tmp}/plant.sock", "-fsync=false"])
        dev = None
        try:
            wait_http(f"http://127.0.0.1:{pport}/healthz")
            dev = Proc([str(go_binary("evicon-device")), "-count", "1", "-contract", preset, "-listen", f"127.0.0.1:{dport}",
                        "-admin-listen", f"127.0.0.1:{aport}", "-state-dir", f"{tmp}/dev", "-plant", f"unix:{tmp}/plant.sock", *device_flags])
            wait_http(f"http://127.0.0.1:{dport}/healthz")
            acfg = {"contract": preset, "device_id": "pump-1", "data_url": "http://{proxy}/d/pump-1", "control_url": f"http://127.0.0.1:{dport}/d/pump-1",
                    "admin_url": f"http://127.0.0.1:{aport}/d/pump-1", "plant_url": f"http://127.0.0.1:{pport}", "client_timeout_s": 0.6}
            if declared is not None:
                acfg["declared"] = declared
            acfg.update(extra_adapter or {})
            p = {"settle_s": 0.4, "late_factor": 2.0, "within_gap_s": 0.3, "beyond_gap_s": 3.0, "offline_s": 3.0}
            p.update(params or {})
            cfg = {"schema_version": 1, "name": f"device-{preset.lower()}", "adapter": "evicon_device", "adapter_config": acfg,
                   "faults": {"kind": "proxy", "proto": "tcp", "target_host": "127.0.0.1", "target_port": dport, "linger_s": 2.0},
                   "probes": probes, "params": p}
            rb, contract, out = run_campaign(cfg, tmp + "/out", log=lambda s: None)
            self.results = [json.loads(line) for line in (Path(out) / "results.jsonl").read_text().splitlines() if line.strip()]
        finally:
            if dev:
                dev.stop()
            plant.stop()
        self.assertFalse(rb.errors, rb.errors)
        return rb, contract

    def test_k0_no_dedup(self):
        rb, c = self.run_preset("K0", ["P4", "P2", "P1"])
        self.assertIs(rb.observed["D_h"], False)
        self.assertIs(rb.observed["Q_A"], False)
        self.assertEqual(rb.level, "K0")
        self.assertIn("P4", rb.duplicates)
        self.assertIn("P2", rb.duplicates)  # resend after a lost reply duplicates the dose

    def test_k1_finite_horizon_is_bracketed(self):
        rb, c = self.run_preset("K1", ["P4", "P5"], device_flags=["-dedup-horizon", "2s"], declared={"D_h": 2.0})
        self.assertTrue(rb.observed["D_h"])
        self.assertEqual(rb.horizon_interval_s, [0.3, 3.0])
        self.assertEqual(c["dedup_horizon_ms"], 300)  # conservative lower bound
        self.assertEqual(rb.level, "K1")
        self.assertEqual(rb.deviations, [])

    def test_k1_declared_horizon_too_long_is_a_deviation(self):
        rb, _ = self.run_preset("K1", ["P4", "P5"], device_flags=["-dedup-horizon", "2s"], declared={"D_h": 120.0})
        self.assertTrue(any(d.startswith("D_h: declared 120.0s") for d in rb.deviations), rb.deviations)

    def test_k1_horizon_bisection_narrows_the_bracket(self):
        # P5B (release 065): retries fresh identities between the P4 gap (no
        # duplicate) and the P5 gap (duplicate) until the bracket is narrow.
        rb, c = self.run_preset("K1", ["P4", "P5", "P5B"], device_flags=["-dedup-horizon", "2s"], declared={"D_h": 2.0},
                                params={"beyond_gap_s": 6.0, "bisect_resolution_s": 0.8, "bisect_max_steps": 5})
        p5b = [r for r in self.results if r["probe_id"] == "P5B"][0]
        self.assertTrue(p5b["applicable"], p5b.get("skip_reason"))
        lo, hi = p5b["parameters"]["interval_s"]
        self.assertTrue(lo <= 2.0 < hi, (lo, hi, p5b["parameters"]["steps"]))
        self.assertLessEqual(hi - lo, 0.8)
        self.assertTrue(all(s["decided"] for s in p5b["parameters"]["steps"]))
        self.assertTrue(rb.observed["D_h"])
        self.assertEqual(rb.horizon_interval_s, [lo, hi])
        self.assertEqual(c["dedup_horizon_ms"], int(lo * 1000))  # still the lower bound
        self.assertEqual(rb.deviations, [])

    def test_p5b_needs_a_duplicate_in_p5(self):
        rb, _ = self.run_preset("K2", ["P4", "P5", "P5B"], params={"beyond_gap_s": 1.0})
        p5b = [r for r in self.results if r["probe_id"] == "P5B"][0]
        self.assertFalse(p5b["applicable"])
        self.assertIn("no duplicate", p5b["skip_reason"])

    def test_k0f_is_fence_only(self):
        # experiment A's fence upgrade of K0 (release 065): F without dedup or query
        rb, c = self.run_preset("K0F", ["P4", "P2", "P1", "P9", "P9F"])
        self.assertIs(rb.observed["D_h"], False)
        self.assertIs(rb.observed["Q_A"], False)
        self.assertTrue(rb.observed["F"])
        self.assertEqual(set(c["capabilities"]), {"F"})
        self.assertEqual(rb.level, "K3")
        self.assertEqual(rb.false_evidence, [])

    def test_k1f_is_horizon_plus_fence(self):
        rb, c = self.run_preset("K1F", ["P4", "P2", "P5", "P9F"], device_flags=["-dedup-horizon", "2s"], declared={"D_h": 2.0, "F": True})
        self.assertTrue(rb.observed["D_h"])
        self.assertTrue(rb.observed["F"])
        self.assertEqual(set(c["capabilities"]), {"D_h", "F"})
        self.assertEqual(rb.deviations, [])

    def test_cutoff_device_is_k3_without_dedup(self):
        rb, c = self.run_preset("CUTOFF", ["P4", "P2", "P9F"])
        self.assertIs(rb.observed["D_h"], False)
        self.assertTrue(rb.observed["Q_A"])
        self.assertTrue(rb.observed["F"])
        self.assertEqual(rb.level, "K3")
        self.assertEqual(set(c["capabilities"]), {"Q_A", "F"})

    def test_owner_expiry_bounds_late_effects(self):
        rb, c = self.run_preset("K3T", ["P4", "P2", "P3", "P9T", "P7"], device_flags=["-expiry", "2s"],
                                declared={"D_h": -1, "Q_A": True, "T": 2.0}, extra_adapter={"expiry_s": 2.0})
        self.assertTrue(rb.observed["T"])
        self.assertEqual(c["expiry_ms"], 2000)
        # A measurement never establishes an unbounded horizon, so T + Q_A does
        # not reveal STOPPED from measurement alone (review RV03): K2 + T.
        self.assertEqual(rb.level, "K2")
        self.assertIn("D_h_unbounded", c["declared_unverified"])
        # P3 (delay < expiry) still shows the late effect; P7 (offline > expiry) does not
        self.assertTrue(rb.late_effect)
        late_note = [n for n in rb.notes if n.startswith("late effect")]
        self.assertEqual(late_note, ["late effect after a client-visible failure in P3"], rb.notes)

    def test_dedup_retained_under_pressure_is_not_a_duplicate(self):
        # Regression (062 self-test): the device adapter reads per-identity
        # plant truth; counting the first effect twice turned every P5N on a
        # deduplicating device into a false "duplicate".
        rb, c = self.run_preset("K3S", ["P4", "P5N"], params={"pressure_n": 16})
        p5n = [r for r in self.results if r["probe_id"] == "P5N"][0]
        self.assertEqual(p5n["effects_final"], 1)
        self.assertFalse(p5n["duplicate"])
        self.assertTrue(rb.observed["D_h"])
        self.assertIn("D_h", c["capabilities"])

    def test_no_dedup_under_pressure_is_a_duplicate(self):
        rb, _ = self.run_preset("K0", ["P4", "P5N"], params={"pressure_n": 4})
        p5n = [r for r in self.results if r["probe_id"] == "P5N"][0]
        self.assertEqual(p5n["effects_final"], 2)
        self.assertIs(rb.observed["D_h"], False)

    def test_offline_queue_without_expiry_executes_late(self):
        rb, _ = self.run_preset("K2", ["P7"])
        self.assertTrue(rb.late_effect, "queued command must execute after reconnect without owner expiry")


if __name__ == "__main__":
    unittest.main()
