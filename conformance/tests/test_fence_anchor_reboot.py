"""The fence anchor after a firmware reboot (release 086, verifier finding B2-02), end to end: the real MQTT owner gateway
(conformance/reference/mqtt_owner_gateway.py, cutoff mode) in front of the cutoff firmware's own wire path and decision
core (firmware/zephyr-cutoff/tests/host/evc_wire_shim.c: evc_parse, evc_core.c and the reply formats of src/main.c,
compiled here as a host library), and the real authority (cmd/evicon-authority, profile DEVICE, policy EVICON, the
measured cutoff contract {D_h, Q_A, F}).

The scenario of B2-02, scaled to a budget of 20 units: four doses (5, 5, 5, 4 = 19) are applied and their receipts are
cached at the gateway; the firmware reboots (its RAM level becomes 0; its durable claim mark hw_limit makes every
sequence up to it uncertain); the client takes the remedy of the paper's Appendix E and submits the next command with a
sequence above the claim mark; that command is lost (it never reaches the firmware) and is ambiguous; a 2-unit candidate
is blocked; the resolver queries (NOT_FOUND) and fences. The gateway lists the four doses APPLIED from its cache and the
lost command STOPPED (the firmware certifies it never started: above evicted_max, at or below the cutoff): a complete
fence answer whose state is the rebooted level [0].

  FA1 default (-fence-anchor evidence): the computed anchor is [19], the reported state [0]: FENCE_ANCHOR_CHECK
      MISMATCH, an EVIDENCE_CONFLICT (FENCE_ANCHOR_MISMATCH, both vectors), the fence aborted, nothing anchored; the
      plant never exceeds the budget.
  FA2 negative control (-fence-anchor owner-state, the anchor of releases 061-085): the same history anchors [0], the
      guard admits the blocked candidates, and the plant exceeds the budget.
"""
import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from reference import mqtt_owner_gateway as G
from tests.helpers import REPO, free_port, go_binary, wait_http
from tests.test_mqtt_owner_gateway import FakeConn, FakeLink, args

sys.path.insert(0, str(REPO / "evaluation"))
import campaign as C  # noqa: E402  (the authority's EVICON arguments of every testbed run)

DEV = "pump0-1"
BOUND = 20
HOST = REPO / "firmware" / "zephyr-cutoff" / "tests" / "host"
KIND = {"cmd": 0, "fence": 1, "query": 2, "stop": 3, "ping": 4}
_LIB = {}
# the contract `measure zephyr` produces for the cutoff mode (its structure: provenance per capability)
MEASURED_CUTOFF = {
    "schema_version": 1, "id": "test-meas-zephyr-cutoff-086", "source": "MEASURED", "system": "test: Zephyr EviCon firmware (cutoff mode)",
    "operation": "request/response PUBLISH (correlation data)", "capabilities": ["D_h", "Q_A", "F"], "dedup_horizon_ms": 600000,
    "fault_model": "network loss/delay, client abandon; owner up (no owner crash or reboot)",
    "provenance": {"D_h": "P4,P2,P6,P8,P5,P5N", "D_h_horizon": "lower bound 600.0s", "F": "P9F", "Q_A": "P2,P2N:receipts_retained=False@64"},
    "verified_test_ids": ["P2", "P2N", "P4", "P5", "P5N", "P6", "P8", "P9F"]}


def shim_library() -> ctypes.CDLL:
    """Compiles the firmware's wire path (evc_wire_shim.c + evc_core.c + evc_proto.c) into a host library."""
    if "lib" in _LIB:
        return _LIB["lib"]
    cc = shutil.which(os.environ.get("CC", "cc")) or shutil.which("gcc")
    if cc is None:
        raise RuntimeError("no C compiler: the firmware host tests need one (firmware/zephyr-cutoff/tests/host)")
    out = Path(tempfile.mkdtemp(prefix="evicon-shim-")) / "libevcshim.so"
    src = HOST.parent.parent / "src"
    subprocess.run([cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-O1", "-shared", "-fPIC", "-o", str(out),
                    str(HOST / "evc_wire_shim.c"), str(src / "evc_core.c"), str(src / "evc_proto.c")], check=True, timeout=120)
    lib = ctypes.CDLL(str(out))
    lib.evc_shim_handle.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
    lib.evc_shim_handle.restype = ctypes.c_int
    lib.evc_shim_boot.argtypes = [ctypes.c_int, ctypes.c_int]
    lib.evc_shim_plant_total.restype = ctypes.c_long
    lib.evc_shim_hw_limit.restype = ctypes.c_ulonglong
    lib.evc_shim_evicted_max.restype = ctypes.c_ulonglong
    lib.evc_shim_level.restype = ctypes.c_ulong
    _LIB["lib"] = lib
    return lib


class CoreFirmware:
    """The cutoff firmware's wire path behind FakeConn (the interface of the gateway tests' FakeFirmware): every
    message goes through evc_parse, evc_core and the reply format of src/main.c. drop: command ids lost on the way."""

    def __init__(self, device: str = DEV):
        self.lib, self.device, self.lock = shim_library(), device, threading.Lock()
        self.drop = set()
        self.lib.evc_shim_boot(0, 0)

    def reboot(self):
        with self.lock:
            self.lib.evc_shim_boot(1, 0)

    def total(self) -> int:
        return int(self.lib.evc_shim_plant_total())

    def handle(self, kind, b):
        with self.lock:
            if kind == "ping":
                return {"device": self.device, "mode": "cutoff", "ok": True, "level": int(self.lib.evc_shim_level())}
            if kind == "cmd" and b.get("cmd") in self.drop:
                return None  # lost before the firmware: no effect, no reply
            body = dict(b)
            body.setdefault("reply", "evicon/r/test")
            buf = ctypes.create_string_buffer(512)
            n = self.lib.evc_shim_handle(KIND[kind], json.dumps(body).encode(), buf, 512)
            if n <= 0:
                return None
            rep = json.loads(buf.value.decode())
            if "device" in rep:
                rep["device"] = self.device
            return rep


def http(method, url, body=None, timeout=5.0):
    r = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(), method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as x:
            return x.status, json.loads(x.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"null")
        except ValueError:
            return e.code, None


class FenceAnchorRebootTest(unittest.TestCase):
    def run_scenario(self, anchor_mode: str) -> dict:
        exe = go_binary("evicon-authority")
        if exe is None:
            self.skipTest("Go toolchain not available (run_tests.py --require-go turns this into a failure)")
        tmp = Path(tempfile.mkdtemp(prefix="evicon-fa086-"))
        fw = CoreFirmware()
        link = FakeLink()

        def net(gw):
            return link, FakeConn(gw, fw, link, "data"), FakeConn(gw, fw, link, "ctl")
        cfile = tmp / "contract.json"
        cfile.write_text(json.dumps(MEASURED_CUTOFF))
        gw = G.Gateway(args("CUTOFF", tmp, budget=0.5, cfile=str(cfile)), net=net)
        Data, Admin = G.make_handlers(gw)
        gport = free_port()
        srv = ThreadingHTTPServer(("127.0.0.1", gport), Data)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        aport = free_port()
        a = {"ambiguity_after": "1s", "resolve_sweep": "1s"}
        argv = C.authority_args({"name": "fa086", "authority": a, "workload": {"bound": BOUND}}, "ZEPHYR_CUTOFF", "EVICON", str(exe), DEV,
                                f"http://127.0.0.1:{gport}/d/{DEV}", str(tmp / "authority.db"), f"127.0.0.1:{aport}",
                                str(tmp / "authority.jsonl"), str(REPO / "experiments" / "configs" / "query_costs.json"))
        argv += ["-fence-anchor", anchor_mode]
        log = open(tmp / "authority.log", "w")
        proc = subprocess.Popen(argv, cwd=REPO, stdout=log, stderr=subprocess.STDOUT)
        auth = f"http://127.0.0.1:{aport}"
        try:
            wait_http(auth + "/healthz", 20)

            def submit(cid, value, seq):
                body = {"schema_version": 1, "domain_id": DEV, "generation": 1, "command_id": cid, "operation": "ADD", "value": [value],
                        "epoch": 1, "sequence": seq}
                return http("POST", auth + "/v1/commands", body)

            def wait_status(cid, want, timeout=10.0):
                end = time.monotonic() + timeout
                while time.monotonic() < end:
                    _, o = http("GET", f"{auth}/v1/commands/{cid}")
                    if isinstance(o, dict) and o.get("status") in want:
                        return o.get("status")
                    time.sleep(0.1)
                return None
            for i, v in enumerate((5, 5, 5, 4), start=1):
                code, out = submit(f"d{i}", v, i)
                self.assertEqual(out.get("decision"), "ADMITTED", out)
                self.assertEqual(wait_status(f"d{i}", ("APPLIED",)), "APPLIED")
            self.assertEqual(fw.total(), 19)
            fw.reboot()  # RAM level 0; evicted_max = the durable claim mark
            hw = int(fw.lib.evc_shim_hw_limit())
            self.assertEqual(int(fw.lib.evc_shim_level()), 0)
            self.assertGreaterEqual(int(fw.lib.evc_shim_evicted_max()), 4)
            seq = hw + 1  # the remedy of Appendix E: the next sequence above the claim mark
            fw.drop.add("lost")
            code, out = submit("lost", 1, seq)
            self.assertEqual(out.get("decision"), "ADMITTED", out)
            # blocked candidates: 19 applied + 1 ambiguous = 20 = the budget; each candidate adds 2
            admitted, end = [], time.monotonic() + 25
            n = 0
            while time.monotonic() < end and len(admitted) < 6:
                n += 1
                seq += 1
                cid = f"c{n}"
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline:
                    code, out = submit(cid, 2, seq)
                    if isinstance(out, dict) and out.get("decision") == "ADMITTED":
                        admitted.append(cid)
                        wait_status(cid, ("APPLIED", "STOPPED"), 5)
                        break
                    if isinstance(out, dict) and out.get("decision") == "REJECT":
                        break
                    time.sleep(0.3)
                if anchor_mode == "evidence" and n >= 3:
                    break
            time.sleep(1.0)
            events = [json.loads(x) for x in (tmp / "authority.jsonl").read_text().splitlines() if x.strip()]
            res = {"total": fw.total(), "admitted": admitted, "events": events, "hw": hw}
            print(f"\n  fence anchor {anchor_mode}: claim mark {hw}, plant total {res['total']} (budget {BOUND}), candidates admitted "
                  f"after the reboot {len(admitted)}; FENCE_ANCHOR_CHECK "
                  f"{[e['outcome'] for e in events if e['event'] == 'FENCE_ANCHOR_CHECK']}", file=sys.stderr)
            return res
        finally:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
            log.close()
            srv.shutdown()
            srv.server_close()
            gw.close()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_fa1_evidence_anchor_aborts_after_reboot(self):
        r = self.run_scenario("evidence")
        checks = [e for e in r["events"] if e["event"] == "FENCE_ANCHOR_CHECK"]
        self.assertTrue(checks, "the resolver must have fenced")
        self.assertIn("MISMATCH", [e["outcome"] for e in checks])
        mm = [e for e in checks if e["outcome"] == "MISMATCH"][0]
        self.assertIn("computed=[19]", mm["reason"])
        self.assertIn("reported=[0]", mm["reason"])
        conflicts = [e for e in r["events"] if e["event"] == "EVIDENCE_CONFLICT"]
        self.assertTrue(any("FENCE_ANCHOR_MISMATCH" in e.get("reason", "") for e in conflicts), conflicts)
        self.assertFalse([e for e in r["events"] if e["event"] == "BARRIER_ANCHOR_COMMITTED"], "no anchor may be committed")
        self.assertTrue([e for e in r["events"] if e["event"] == "BARRIER_ABORTED" and "EVIDENCE_CONFLICT" in e.get("reason", "")])
        self.assertLessEqual(r["total"], BOUND, f"the plant exceeded the budget: {r['total']} > {BOUND}")
        self.assertEqual(r["admitted"], [], "no candidate fits while the lost command is ambiguous")

    def test_fa2_owner_state_anchor_violates_the_bound(self):
        r = self.run_scenario("owner-state")
        checks = [e for e in r["events"] if e["event"] == "FENCE_ANCHOR_CHECK"]
        self.assertIn("MISMATCH_OWNER_STATE_ADOPTED", [e["outcome"] for e in checks], checks)
        self.assertTrue([e for e in r["events"] if e["event"] == "BARRIER_ANCHOR_COMMITTED"])
        self.assertGreater(r["total"], BOUND, f"negative control: the owner-state anchor must let the plant exceed the budget "
                                              f"(total {r['total']}, admitted {r['admitted']})")


if __name__ == "__main__":
    unittest.main()
