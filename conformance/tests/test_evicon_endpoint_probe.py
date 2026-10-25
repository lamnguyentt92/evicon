"""Positive control: EviCon-Probe must recover the known evidence contracts of
EviCon's own endpoint profiles (E1 -> K2, E2 -> K3 via F, E2+stop -> K3 via
Q_S and F; E3 -> K2 measured with X declared-unverified) and must flag the
late effect that every profile without owner expiry shows (P3). Durable
receipts/tombstones must survive the pressure probes."""
import tempfile
import unittest

from evicon_probe.runner import run_campaign

from .helpers import Proc, free_port, go_binary, wait_http

PROBES = ["P4", "P6", "P2", "P1", "P8", "P3", "P9", "P9F", "P5", "P2N", "P5N", "P9N"]


def campaign(profile_alias, port, tmp):
    return {
        "schema_version": 1,
        "name": f"evicon-{profile_alias.lower()}",
        "adapter": "evicon_endpoint",
        "adapter_config": {"profile": profile_alias, "data_url": "http://{proxy}", "control_url": f"http://127.0.0.1:{port}",
                           "client_timeout_s": 0.6, "domain": "atlas-001"},
        "faults": {"kind": "proxy", "proto": "tcp", "target_host": "127.0.0.1", "target_port": port, "linger_s": 2.0},
        "probes": PROBES,
        "params": {"settle_s": 0.4, "late_factor": 2.0, "within_gap_s": 0.3, "beyond_gap_s": 1.5, "pressure_n": 12},
    }


@unittest.skipIf(go_binary("evicon-endpoint") is None, "Go toolchain not available")
class EviconEndpointPositiveControl(unittest.TestCase):
    def run_profile(self, alias):
        ep = go_binary("evicon-endpoint")
        port = free_port()
        tmp = tempfile.mkdtemp(prefix=f"evicon-probe-{alias}-")
        prof = "E2" if alias == "E2_STOP" else alias
        args = [str(ep), "-profile", prof, "-db", f"{tmp}/ep.db", "-listen", f"127.0.0.1:{port}", "-component-max", "1000000"]
        if alias == "E2_STOP":
            args.append("-per-identity-stop")
        proc = Proc(args)
        try:
            wait_http(f"http://127.0.0.1:{port}/healthz")
            rb, contract, out = run_campaign(campaign(alias, port, tmp), tmp + "/out", log=lambda s: None)
        finally:
            proc.stop()
        self.assertFalse(rb.errors, rb.errors)
        self.assertFalse(rb.false_evidence, rb.false_evidence)
        self.assertFalse(rb.duplicates, rb.duplicates)
        self.assertTrue(rb.late_effect, "no owner expiry: a delayed request must apply after the client timed out (P3)")
        return rb, contract

    def test_e1_is_k2(self):
        rb, c = self.run_profile("E1")
        self.assertEqual(rb.level, "K2", rb.to_dict())
        self.assertEqual(set(c["capabilities"]), {"D_h", "Q_A"})
        self.assertEqual(c["dedup_horizon_ms"], 1500)  # lower bound from P5, never "unbounded"
        self.assertIn("D_h_unbounded", c["declared_unverified"])
        self.assertEqual(rb.retention["receipts"], {"intervening": 12, "retained": True})
        self.assertEqual(rb.retention["dedup"], {"intervening": 12, "retained": True})
        self.assertEqual(rb.deviations, [])

    def test_e2_is_k3_via_fence(self):
        rb, c = self.run_profile("E2")
        self.assertEqual(rb.level, "K3", rb.to_dict())
        self.assertEqual(set(c["capabilities"]), {"D_h", "Q_A", "F"})
        self.assertIs(rb.observed["Q_S"], False)  # no stop interface -> capability absent
        self.assertEqual(rb.deviations, [])

    def test_e2_stop_is_k3_with_qs(self):
        rb, c = self.run_profile("E2_STOP")
        self.assertEqual(set(c["capabilities"]), {"D_h", "Q_A", "Q_S", "F"})
        self.assertEqual(rb.level, "K3")
        self.assertEqual(rb.retention["tombstones"], {"intervening": 12, "retained": True})
        self.assertEqual(rb.deviations, [])

    def test_e3_x_is_declared_unverified(self):
        rb, c = self.run_profile("E3")
        self.assertEqual(rb.level, "K2", rb.to_dict())
        self.assertNotIn("X", c["capabilities"])
        self.assertEqual(rb.status["X"], "DECLARED_UNVERIFIED")
        self.assertIn("X", c["declared_unverified"])


if __name__ == "__main__":
    unittest.main()
