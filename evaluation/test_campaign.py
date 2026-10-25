"""Regression tests for evaluation/campaign.py (release 063).

- RFC 3339 timestamps with nanoseconds (Go) parse on Python 3.10 (VM-C).
- A process that exits at start-up is reported at once, with its log tail.
- A relative --out works: every process is started after `cd <cell dir>`.
- Another program answering on the port is never taken for ours (release 064).

The last test runs one real cell and needs the binaries: `make build` first
(`make verify` does this). It fails, rather than skips, when they are missing.
"""
import json
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
import campaign  # noqa: E402


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TimestampTest(unittest.TestCase):
    def test_go_rfc3339nano(self):
        base = campaign._ts("2026-10-13T04:14:03Z")
        self.assertEqual(base, 1791864843.0)
        self.assertAlmostEqual(campaign._ts("2026-10-13T11:14:03.123456789+07:00"), base + 0.123456789, places=6)
        self.assertAlmostEqual(campaign._ts("2026-10-13T04:14:03.5Z"), base + 0.5, places=6)
        self.assertIsNone(campaign._ts(""))
        self.assertIsNone(campaign._ts("not a time"))

    def test_trace_evidence_times_are_kept(self):
        tmp = Path(tempfile.mkdtemp())
        line = {"event": "EVIDENCE_COMMITTED", "command_id": "c1", "outcome": "STOPPED", "reason": "",
                "wall_time": "2026-10-13T11:14:03.123456789+07:00"}
        (tmp / "authority0.jsonl").write_text(json.dumps(line) + "\n")
        _, _, _, _, evidence_at, _ = campaign.parse_traces([tmp / "authority0.jsonl"])
        self.assertIn("c1", evidence_at)


class ReadinessTest(unittest.TestCase):
    def test_exited_process_fails_fast_with_log_tail(self):
        wd = Path(tempfile.mkdtemp())
        host = campaign.Host("local-E", "127.0.0.1")
        pid = host.spawn("plant", ["bash", "-c", "echo 'open plant.jsonl: no such file or directory' >&2; exit 1"], wd)
        t0 = time.monotonic()
        with self.assertRaises(RuntimeError) as cm:
            campaign.Testbed.ready(host, f"http://127.0.0.1:{free_port()}/healthz", "plant", pid, wd, timeout=15)
        msg = str(cm.exception)
        self.assertLess(time.monotonic() - t0, 6, "an exited process must not wait for the full timeout")
        self.assertIn("exited", msg)
        self.assertIn("no such file or directory", msg)


class _Foreign(BaseHTTPRequestHandler):
    """An older program bound to the port (063 on VM-B: an older deployment's endpoint on 18081)."""

    def do_GET(self):
        body = json.dumps({"ok": True, "profile": "E3", "release": "016-multivm-linux-g2-readiness"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class ForeignProgramTest(unittest.TestCase):
    def test_another_program_on_the_port_is_never_measured(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _Foreign)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        wd = Path(tempfile.mkdtemp())
        host = campaign.Host("local-B", "127.0.0.1")
        pid = host.spawn("endpoint", ["sleep", "30"], wd)  # our process, alive but not (yet) answering
        try:
            t0 = time.monotonic()
            with self.assertRaises(RuntimeError) as cm:
                campaign.Testbed.ready(host, f"http://127.0.0.1:{srv.server_address[1]}/healthz", "endpoint", pid, wd, timeout=15)
            self.assertLess(time.monotonic() - t0, 5, "a foreign answer must fail at once")
            self.assertIn("another program", str(cm.exception))
            self.assertIn("016-multivm", str(cm.exception))
        finally:
            host.stop_all(wd)
            srv.shutdown()
            srv.server_close()


class RelativeOutTest(unittest.TestCase):
    def test_relative_out_runs_a_valid_cell(self):
        bins = [REPO / "bin" / n for n in ("evicon-plant", "evicon-device", "evicon-authority")]
        missing = [str(b) for b in bins if not b.exists()]
        self.assertFalse(missing, f"binaries missing (run `make build` first): {missing}")
        tmp = Path(tempfile.mkdtemp())
        cfg = json.loads((REPO / "evaluation" / "campaigns" / "local_smoke.json").read_text())
        cfg.update({"name": "rel-out", "contracts": ["K3S"], "policies": ["EVICON"], "duration_s": 8, "tail_s": 2})
        (tmp / "cfg.json").write_text(json.dumps(cfg))
        p = subprocess.run([sys.executable, str(HERE / "campaign.py"), "--config", "cfg.json", "--out", "rel/out",
                            "--bin-dir", str(REPO / "bin")], cwd=tmp, capture_output=True, text=True, timeout=240)
        runs = sorted((tmp / "rel" / "out" / "rel-out").glob("*"))
        self.assertTrue(runs, p.stdout + p.stderr)
        done = [json.loads(x) for x in (runs[-1] / "done.jsonl").read_text().splitlines() if x.strip()]
        self.assertEqual([d["status"] for d in done], ["VALID"], (runs[-1] / "campaign.log").read_text())
        self.assertTrue((runs[-1] / "campaign.log").exists())


if __name__ == "__main__":
    unittest.main()
