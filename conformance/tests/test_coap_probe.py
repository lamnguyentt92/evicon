"""CoAP: EviCon-Probe separates the message-layer dedup horizon (identical
CON datagram, EXCHANGE_LIFETIME) from application-level identity (a new
request with the same command ID is not deduplicated)."""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

from evicon_probe.adapters.coap import CON, POST, decode, encode
from evicon_probe.runner import run_campaign

from .helpers import Proc, free_port, go_binary, wait_http

REF = Path(__file__).resolve().parents[1] / "reference" / "coap_device.py"


class CoapCodecTest(unittest.TestCase):
    def test_roundtrip_with_extended_options(self):
        long_seg = b"x" * 20  # length 20 -> extended length nibble
        b = encode(CON, POST, 0xBEEF, b"\x01\x02", [(11, b"actuate"), (11, long_seg), (300, b"q")], b'{"a":1}')
        m = decode(b)
        self.assertEqual((m["type"], m["code"], m["mid"], m["token"], m["payload"]), (CON, POST, 0xBEEF, b"\x01\x02", b'{"a":1}'))


_SKIP = ("Go toolchain not available" if go_binary("evicon-plant") is None else
         "aiocoap not installed (optional on VM-C: pip3 install aiocoap)" if importlib.util.find_spec("aiocoap") is None else "")


@unittest.skipIf(_SKIP, _SKIP)
class CoapProbe(unittest.TestCase):
    def run_level(self, level, probes, params=None):
        tmp = tempfile.mkdtemp(prefix="evicon-probe-coap-")
        pport, cport = free_port(), free_port()
        plant = Proc([str(go_binary("evicon-plant")), "-log", f"{tmp}/plant.jsonl", "-http", f"127.0.0.1:{pport}", "-fsync=false"])
        srv = None
        try:
            wait_http(f"http://127.0.0.1:{pport}/healthz")
            srv = Proc([sys.executable, str(REF), "--port", str(cport), "--plant", f"http://127.0.0.1:{pport}", "--status", "--exchange-lifetime", "2", "--transports", "simplesocketserver"])
            import time
            time.sleep(1.0)
            cfg = {"schema_version": 1, "name": f"coap-{level}", "adapter": "coap",
                   "adapter_config": {"data_uri": "coap://{proxy}/actuate", "control_uri": f"coap://127.0.0.1:{cport}/actuate",
                                      "status_path": "status", "identity_level": level, "plant_url": f"http://127.0.0.1:{pport}",
                                      "client_timeout_s": 1.0, "ack_timeout_s": 0.3, "exchange_lifetime_s": 2.0, "stack": "aiocoap"},
                   "faults": {"kind": "proxy", "proto": "udp", "target_host": "127.0.0.1", "target_port": cport},
                   "probes": probes, "params": dict({"settle_s": 0.4, "late_factor": 2.0, "within_gap_s": 0.3, "beyond_gap_s": 3.5},
                                                     **(params or {}))}
            rb, c, _ = run_campaign(cfg, tmp + "/out", log=lambda s: None)
        finally:
            if srv:
                srv.stop()
            plant.stop()
        self.assertFalse(rb.errors, rb.errors)
        return rb, c

    def test_message_level_horizon(self):
        rb, c = self.run_level("message", ["P4", "P2", "P5", "P3"])
        self.assertTrue(rb.observed["D_h"], rb.to_dict())
        self.assertEqual(rb.horizon_interval_s, [0.3, 3.5])
        self.assertNotIn("P2", rb.duplicates)  # retransmissions of one exchange are deduplicated
        self.assertIn("P5", rb.duplicates)     # identical datagram after EXCHANGE_LIFETIME re-executes
        self.assertTrue(rb.late_effect)        # CoAP has no owner-side expiry

    def test_message_level_horizon_bisection(self):
        # P5B (release 065) narrows [P4 gap, P5 gap) around EXCHANGE_LIFETIME (2 s here)
        rb, c = self.run_level("message", ["P4", "P5", "P5B"], {"beyond_gap_s": 6.0, "bisect_resolution_s": 0.8, "bisect_max_steps": 5})
        lo, hi = rb.horizon_interval_s
        self.assertLessEqual(hi - lo, 0.8, rb.horizon_interval_s)
        self.assertTrue(1.0 <= lo <= 2.6 and 1.5 <= hi <= 3.4, rb.horizon_interval_s)  # aiocoap expires the cache entry lazily
        self.assertTrue(rb.observed["D_h"])

    def test_application_level_resend_duplicates(self):
        rb, c = self.run_level("application", ["P4", "P2"])
        self.assertIs(rb.observed["D_h"], False)
        self.assertIn("P4", rb.duplicates)
        self.assertTrue(rb.observed["Q_A"])  # the reference app exposes /status
        # lattice order (contract.Level): Q_A alone is K2 even without dedup;
        # the rubric still records that a same-identity retry duplicates.
        self.assertEqual(rb.level, "K2")


if __name__ == "__main__":
    unittest.main()
