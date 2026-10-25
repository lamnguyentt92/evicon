import datetime as dt
import json
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from evicon_probe import adapters as registry
from evicon_probe import contract_map
from evicon_probe.adapters.s3_conditional import EMPTY_SHA256, sigv4_headers
from evicon_probe.model import APPLIED, STOPPED
from evicon_probe.rubric import classify, level, merge, to_contract
from evicon_probe.runner import load_config, run_campaign

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent


class SigV4Test(unittest.TestCase):
    def test_aws_documented_get_object_example(self):
        # AWS S3 docs, "Signature Calculations for the Authorization Header", GET Object example
        h = sigv4_headers("GET", "https://examplebucket.s3.amazonaws.com/test.txt", {"Range": "bytes=0-9"}, EMPTY_SHA256,
                          "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "us-east-1",
                          now=dt.datetime(2013, 5, 24, tzinfo=dt.timezone.utc))
        self.assertTrue(h["Authorization"].endswith("Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"))


class RegistryTest(unittest.TestCase):
    def test_every_adapter_module_imports(self):
        for name in registry.REGISTRY:
            cls = registry.load(name)
            # release 068: TESTED_TESTBED for adapters measured on the 5-VM testbed (CoAP, MQTT, NATS, Temporal)
            self.assertTrue(cls.implementation_status in ("TESTED_TESTBED", "TESTED_LOCAL", "UNTESTED_NEEDS_TESTBED"), name)

    def test_shipped_configs_validate(self):
        files = sorted((ROOT / "configs").glob("*.json"))
        self.assertGreaterEqual(len(files), 12)
        for f in files:
            cfg = load_config(f)
            self.assertIn(cfg["adapter"], registry.REGISTRY, f.name)


class _IdemServer(BaseHTTPRequestHandler):
    """Idempotency-Key reference: keys retained for RETAIN_S, then pruned
    (like a 24 h retention, shortened); concurrent duplicates get 409."""
    RETAIN_S = 1.5
    keys: dict = {}
    inflight: set = set()
    effects: dict = {}
    lock = threading.Lock()

    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        key = self.headers.get("Idempotency-Key")
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        now = time.monotonic()
        with self.lock:
            for k in [k for k, (t, _) in self.keys.items() if now - t > self.RETAIN_S]:
                del self.keys[k]
            if key in self.keys:
                return self._json(200, self.keys[key][1])
            if key in self.inflight:
                return self._json(409, {"error": "in progress"})
            self.inflight.add(key)
        time.sleep(0.15)
        with self.lock:
            self.effects[body["cmd"]] = self.effects.get(body["cmd"], 0) + 1
            resp = {"outcome": "APPLIED", "cmd": body["cmd"]}
            self.keys[key] = (time.monotonic(), resp)
            self.inflight.discard(key)
        self._json(200, resp)

    def do_GET(self):
        cmd = self.path.split("cmd=")[-1]
        with self.lock:
            self._json(200, {"effects": self.effects.get(cmd, 0)})

    def log_message(self, *a):
        pass


class HttpIdempotencyLocal(unittest.TestCase):
    def test_retention_window_and_conflict(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _IdemServer)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        port = srv.server_address[1]
        try:
            cfg = {"schema_version": 1, "name": "idem-ref", "adapter": "http_idempotency",
                   "adapter_config": {"data_url": "http://{proxy}/v1/doses", "truth": {"url": f"http://127.0.0.1:{port}/truth?cmd={{cmd}}"},
                                      "declared": {"D_h": 1.5}, "client_timeout_s": 0.6},
                   "faults": {"kind": "proxy", "target_host": "127.0.0.1", "target_port": port, "linger_s": 1.0},
                   "probes": ["P4", "P5", "P6", "P2"], "params": {"settle_s": 0.3, "within_gap_s": 0.3, "beyond_gap_s": 2.5}}
            rb, c, _ = run_campaign(cfg, tempfile.mkdtemp(), log=lambda s: None)
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertTrue(rb.observed["D_h"])
        self.assertEqual(rb.horizon_interval_s, [0.3, 2.5])
        self.assertIn("P5", rb.duplicates)
        self.assertNotIn("P6", rb.duplicates)
        self.assertEqual(rb.level, "K1")


def _row(pid, **kw):
    base = {"probe_id": pid, "system": "s", "operation": "o", "command_id": "x", "applicable": True, "error": "",
            "effects": 0, "effects_final": 0, "duplicate": False, "late_effect": False, "false_stopped": False,
            "false_applied": False, "parameters": {}, "skip_reason": ""}
    base.update(kw)
    return base


class RubricUnitTest(unittest.TestCase):
    def test_level_mirror_matches_go_lattice(self):
        self.assertEqual(level(set(), 0), "K0")
        self.assertEqual(level({"D_h"}, 45000), "K1")
        self.assertEqual(level({"D_h", "Q_A"}, -1), "K2")
        self.assertEqual(level({"D_h", "Q_A", "T"}, -1), "K3")   # expiry reveals STOPPED
        self.assertEqual(level({"D_h", "Q_A", "T"}, 45000), "K2")  # ... not with a finite horizon
        self.assertEqual(level({"F"}, 0), "K3")
        self.assertEqual(level({"D_h", "X"}, -1), "K4")

    def test_false_stopped_is_a_deviation_and_denies_qs(self):
        stop = {"supported": True, "outcome": STOPPED, "terminal": True, "latency_ms": 1.0}
        rows = [P4_OK, _row("P9", stop=stop, effects=0, effects_final=1, late_effect=True, false_stopped=True)]
        rb = classify(rows, "s", "o", {"Q_S": True}, {"within_gap_s": 1})
        self.assertIs(rb.observed["Q_S"], False)
        self.assertEqual(rb.status["Q_S"], "CONTRADICTED")
        self.assertTrue(any(d.startswith("Q_S: declared but contradicted") for d in rb.deviations), rb.deviations)
        self.assertTrue(any("contradicted by truth" in d for d in rb.deviations))

    def test_merge_requires_every_repetition(self):
        q = {"supported": True, "outcome": APPLIED, "terminal": True, "latency_ms": 1.0}
        good = classify([P4_OK, _row("P2", query=q, effects=1, effects_final=1)], "s", "o", {}, {})
        bad = classify([P4_OK, _row("P2", query={**q, "outcome": "PENDING", "terminal": False}, effects=1, effects_final=1)], "s", "o", {}, {})
        m = merge([good, bad])
        self.assertIs(m.observed["Q_A"], False)
        self.assertEqual(to_contract(m, [])["capabilities"], ["D_h"])


OK_SEND = {"status": "OK", "outcome": "APPLIED"}
P4_OK = _row("P4", effects_final=1, client=OK_SEND, resend=OK_SEND)


class RubricReviewRegressions(unittest.TestCase):
    """The counterexamples of review RV02/RV03 (release 062)."""

    def test_zero_effect_p4_is_not_dedup_evidence(self):
        rb = classify([_row("P4", effects_final=0, client=OK_SEND, resend=OK_SEND)], "s", "o", {}, {"within_gap_s": 1})
        self.assertEqual(rb.status["D_h"], "UNKNOWN")
        self.assertNotIn("D_h", to_contract(rb, [])["capabilities"])

    def test_p6_duplicate_vetoes_dedup(self):
        rb = classify([P4_OK, _row("P6", effects_final=2, duplicate=True)], "s", "o", {"D_h": 60}, {"within_gap_s": 1})
        self.assertEqual(rb.status["D_h"], "CONTRADICTED")
        self.assertEqual(rb.evidence["D_h"], ["P6"])
        self.assertNotIn("D_h", to_contract(rb, [])["capabilities"])

    def test_timeout_retry_is_not_a_positive_control(self):
        rb = classify([_row("P4", effects_final=1, client=OK_SEND, resend={"status": "TIMEOUT"})], "s", "o", {}, {})
        self.assertEqual(rb.status["D_h"], "UNKNOWN")

    def test_declarations_never_become_measured(self):
        rb = classify([P4_OK], "s", "o", {"D_h": -1, "X": True}, {"within_gap_s": 1.0})
        c = to_contract(rb, [])
        self.assertEqual(c["capabilities"], ["D_h"])
        self.assertEqual(c["dedup_horizon_ms"], 1000)           # observed lower bound, never -1
        self.assertIn("X", c["declared_unverified"])
        self.assertIn("D_h_unbounded", c["declared_unverified"])
        self.assertEqual(rb.status["X"], "DECLARED_UNVERIFIED")
        self.assertIn("D_h", c["provenance"])

    def test_pressure_probes_veto_dedup_and_stop(self):
        stop = {"supported": True, "outcome": STOPPED, "terminal": True, "latency_ms": 1.0}
        p9 = _row("P9", stop=stop, effects=0, effects_final=0)
        p5n = _row("P5N", effects=1, effects_final=2, duplicate=True, parameters={"intervening": 64})
        p9n = _row("P9N", stop=stop, effects=0, effects_final=1, late_effect=True, false_stopped=True, parameters={"intervening": 64})
        rb = classify([P4_OK, p9, p5n, p9n], "s", "o", {"D_h": -1, "Q_S": True}, {"within_gap_s": 1})
        self.assertEqual(rb.status["D_h"], "CONTRADICTED")
        self.assertEqual(rb.retention["dedup"], {"intervening": 64, "retained": False})
        self.assertEqual(rb.status["Q_S"], "CONTRADICTED")
        self.assertEqual(to_contract(rb, [])["capabilities"], [])

    def test_measured_contract_validates_in_go(self):
        from .helpers import go_binary
        exe = go_binary("evicon-contract")
        if exe is None:
            self.skipTest("Go toolchain not available")
        q = {"supported": True, "outcome": APPLIED, "terminal": True, "latency_ms": 1.0}
        stop = {"supported": True, "outcome": STOPPED, "terminal": True, "latency_ms": 1.0}
        rb = classify([P4_OK, _row("P2", query=q, effects=1, effects_final=1), _row("P9", stop=stop, effects=0, effects_final=0)],
                      "s", "o", {"D_h": -1, "Q_A": True, "Q_S": True, "X": True}, {"within_gap_s": 1})
        tmp = Path(tempfile.mkdtemp())
        (tmp / "contract.json").write_text(json.dumps(to_contract(rb, [])))
        out = subprocess.run([str(exe), "validate", str(tmp / "contract.json")], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)


class ContractMapTest(unittest.TestCase):
    def test_map_combines_catalog_and_measured(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "contract.json").write_text(json.dumps({"schema_version": 1, "id": "meas-x", "system": "NATS JetStream", "operation": "publish",
                                                       "source": "MEASURED", "capabilities": ["D_h"], "dedup_horizon_ms": 115000}))
        rs = contract_map.rows([REPO / "experiments" / "contracts"], [tmp / "contract.json"])
        ids = [r["id"] for r in rs]
        self.assertIn("meas-x", ids)
        self.assertIn("hyp-nats-jetstream", ids)
        self.assertNotIn("sim-k0", ids)
        nats = [r for r in rs if r["system"] == "NATS JetStream"]
        self.assertEqual(nats[0]["source"], "MEASURED")  # measured rows sort first
        md = contract_map.markdown(rs)
        self.assertIn("| NATS JetStream |", md)
        self.assertEqual(nats[0]["measured_in"], "-")  # a contract file outside a driver run
        self.assertEqual([r for r in rs if r["id"] == "hyp-nats-jetstream"][0]["measured_in"], "")
        self.assertEqual(nats[0]["D_h_lower_s"], "")  # no rubric.json next to this contract
        (tmp / "rubric.json").write_text(json.dumps({"horizon_interval_s": [114.0, 122.0], "declared_level": "K1", "late_effect": True}))
        nats = [r for r in contract_map.rows([REPO / "experiments" / "contracts"], [tmp / "contract.json"]) if r["id"] == "meas-x"][0]
        self.assertEqual((nats["D_h_lower_s"], nats["D_h_upper_s"], nats["declared_level"], nats["late_effect"]), (114.0, 122.0, "K1", True))
        p = "/root/EVICON_TOIT_070/results/review-20261021T101500/measure/nats/runs/nats-jetstream/20261021T120000/contract.json"
        self.assertEqual(contract_map.measured_in(p), "release 070 review-20261021T101500 20261021T120000")


if __name__ == "__main__":
    unittest.main()


class GoLatticeCrossCheck(unittest.TestCase):
    """The Python level() mirror must agree with internal/contract.Level on
    every catalog contract (the Go code is authoritative)."""

    def test_python_mirror_equals_go(self):
        import subprocess
        from .helpers import go_binary
        exe = go_binary("evicon-contract")
        if exe is None:
            self.skipTest("Go toolchain not available")
        out = subprocess.run([str(exe), "levels", str(REPO / "experiments" / "contracts")], capture_output=True, text=True, check=True).stdout
        n = 0
        for line in out.splitlines():
            g = json.loads(line)
            c = json.loads(next(f for f in (REPO / "experiments" / "contracts").glob("*.json") if json.loads(f.read_text())["id"] == g["id"]).read_text())
            self.assertEqual(level(set(c.get("capabilities", [])), int(c.get("dedup_horizon_ms", 0))), g["level"], g["id"])
            n += 1
        self.assertGreaterEqual(n, 20)


class MqttFenceNeverInfersStopped(unittest.TestCase):
    """Review RV04: fence OK + query NOT_FOUND/UNCERTAIN must not become STOPPED."""

    def _adapter(self, replies):
        from evicon_probe.adapters.mqtt5 import Mqtt5RpcAdapter
        a = Mqtt5RpcAdapter.__new__(Mqtt5RpcAdapter)  # no broker, no paho
        a.cfg, a.reply_mode, a.run_tag, a._seq = {"firmware_stop": True}, "payload", "t", 0
        calls = []

        def rpc(kind, key, body, timeout):
            calls.append((kind, body))
            return True, replies[kind], 1.0
        a._payload_rpc = rpc
        return a, calls

    def test_fence_with_missing_receipt_is_not_terminal(self):
        for q in ({"outcome": "UNCERTAIN", "reason": "RECEIPT_MAY_BE_EVICTED"}, {"outcome": "NOT_FOUND", "reason": "NOT_STARTED"}):
            a, calls = self._adapter({"fence": {"ok": True, "cutoff": 7}, "query": q})
            ev = a.fence(["p9f-t-7"])
            self.assertFalse(ev.terminal, q)
            self.assertNotEqual(ev.outcome, STOPPED)
            self.assertEqual(calls[-1], ("query", {"cmd": "p9f-t-7", "seq": 7}))

    def test_fence_passes_certified_outcomes(self):
        a, _ = self._adapter({"fence": {"ok": True, "cutoff": 7}, "query": {"outcome": "STOPPED", "reason": "SEALED_NOT_STARTED"}})
        ev = a.fence(["p9f-t-7"])
        self.assertTrue(ev.terminal)
        self.assertEqual(ev.outcome, STOPPED)
        a, _ = self._adapter({"fence": {"ok": True, "cutoff": 7}, "query": {"outcome": "APPLIED", "reason": "APPLIED"}})
        self.assertEqual(a.fence(["p9f-t-7"]).outcome, APPLIED)

    def test_uncertain_stop_is_not_terminal(self):
        a, _ = self._adapter({"stop": {"outcome": "UNCERTAIN", "reason": "FUTURE_BLOCKED_PAST_UNKNOWN"}})
        self.assertFalse(a.stop("p9-t-3").terminal)
