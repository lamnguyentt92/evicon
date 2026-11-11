"""The reboot recovery of release 087 (PREREG_extra_087.md item B; reviewer comment M3), end to end on one machine: the
release-087 MQTT owner gateway (conformance/reference/mqtt_owner_gateway_ext087.py: the claim floor GW-1, claim_floor in
every answer GW-2, the seal GW-3) in front of the boot-report firmware's wire path (firmware/zephyr-boot/tests/host/
evc_boot_shim.c: the frozen host shim of the cutoff firmware, its evc_parse, evc_core.c and reply formats, with the
floor appended to every reply as firmware/zephyr-boot publishes it; compiled here as a host library), and the real
authority (cmd/evicon-authority, profile DEVICE, the campaign arguments of EVICON_FI, EVICON_FI_REC or
EVICON_FI_REC_OS, the cutoff firmware's measured contract restricted to {F}, as the campaign expRB_zephyr_w6).

The history, with a budget of 30 units: four doses (5, 5, 5, 4 = 19) are applied (the claim mark becomes 65: the first
sequence plus the firmware's claim block of 64); the firmware reboots (RAM level 0; the claim floor F = 65: nothing at
or below 65 is executed or certified again) and announces F in its hello; the client goes on with its own next sequences (5, 6, ...), 2 units
each; the third command after the reboot is lost before the firmware (ambiguous), so the guard blocks and the resolver
fences ({F} has no query).

  RB1 (iii) EVICON_FI_REC, gateway seal on: client sequence 5 is sealed by the gateway (STOPPED, claim_floor 65), the
      authority learns F (OWNER_FLOOR), advances its sequence offset (SEQUENCE_ADVANCED: the next identity is 66),
      keeps admitting, and the fence after the reboot commits the evidence anchor on the floor-relative check
      (FENCE_ANCHOR_CHECK MATCH_FLOOR); the plant never exceeds the budget.
  RB2 (ii) EVICON_FI, release 086 without recovery: every identity after the reboot lies in the claimed range and is
      answered UNCERTAIN; nothing takes effect after the reboot (liveness lost), and the plant stays at 19.
  RB3 (i) EVICON_FI_REC_OS, the negative control: the same history with the owner-state anchor adopts the reset level
      at the fence after the reboot, the guard admits again, and the plant exceeds the budget.
"""
import argparse
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
from http.server import ThreadingHTTPServer
from pathlib import Path

from reference import mqtt_owner_gateway_ext087 as G87
from tests.helpers import REPO, free_port, go_binary, wait_http
from tests.test_fence_anchor_reboot import KIND, MEASURED_CUTOFF, http
from tests.test_mqtt_owner_gateway import FakeConn, FakeLink

sys.path.insert(0, str(REPO / "evaluation"))
import ext087  # noqa: E402  (the policies of release 087: EVICON_FI_REC, EVICON_FI_REC_OS)
from campaign_zephyr_ext import restrict_contract  # noqa: E402

C = ext087.install()
DEV = "pump0-1"
BOUND = 30
BOOT = REPO / "firmware" / "zephyr-boot"
CUT = REPO / "firmware" / "zephyr-cutoff" / "src"
_LIB = {}


def boot_library() -> ctypes.CDLL:
    """Compiles the boot-report firmware's wire path (evc_boot_shim.c + evc_boot_report.c + evc_core.c + evc_proto.c)."""
    if "lib" in _LIB:
        return _LIB["lib"]
    cc = shutil.which(os.environ.get("CC", "cc")) or shutil.which("gcc")
    if cc is None:
        raise RuntimeError("no C compiler: the firmware host tests need one (firmware/zephyr-boot/tests/host)")
    out = Path(tempfile.mkdtemp(prefix="evicon-bootshim-")) / "libevcbootshim.so"
    subprocess.run([cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-O1", "-shared", "-fPIC", "-DEVC_BOOT_HOST_CORE_ONLY",
                    f"-I{BOOT / 'src'}", f"-I{CUT}", "-o", str(out), str(BOOT / "tests" / "host" / "evc_boot_shim.c"),
                    str(BOOT / "src" / "evc_boot_report.c"), str(CUT / "evc_core.c"), str(CUT / "evc_proto.c")], check=True, timeout=120)
    lib = ctypes.CDLL(str(out))
    lib.evc_boot_shim_handle.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
    lib.evc_boot_shim_handle.restype = ctypes.c_int
    lib.evc_shim_boot.argtypes = [ctypes.c_int, ctypes.c_int]
    lib.evc_shim_plant_total.restype = ctypes.c_long
    lib.evc_shim_hw_limit.restype = ctypes.c_ulonglong
    lib.evc_shim_level.restype = ctypes.c_ulong
    lib.evc_boot_shim_floor.restype = ctypes.c_ulonglong
    _LIB["lib"] = lib
    return lib


class BootFirmware:
    """The boot-report firmware's wire path behind FakeConn: every message through evc_parse, evc_core, the reply
    formats of src/main.c and evc_boot_append_floor. drop: command ids lost on the way (no effect, no reply)."""

    def __init__(self, device: str = DEV):
        self.lib, self.device, self.lock = boot_library(), device, threading.Lock()
        self.drop = set()
        self.lib.evc_shim_boot(0, 0)

    def reboot(self) -> int:
        with self.lock:
            self.lib.evc_shim_boot(1, 0)
            return int(self.lib.evc_boot_shim_floor())

    def total(self) -> int:
        return int(self.lib.evc_shim_plant_total())

    def handle(self, kind, b):
        with self.lock:
            if kind == "cmd" and b.get("cmd") in self.drop:
                return None
            body = dict(b)
            body.setdefault("reply", "evicon/r/test")
            buf = ctypes.create_string_buffer(640)
            n = self.lib.evc_boot_shim_handle(KIND[kind], json.dumps(body).encode(), buf, 640)
            if n <= 0:
                return None
            rep = json.loads(buf.value.decode())
            if "device" in rep:
                rep["device"] = self.device
            if kind == "ping":
                rep["mode"] = "cutoff"
            return rep


def gateway_args(tmp: Path, cfile: Path, recovery: bool) -> argparse.Namespace:
    return argparse.Namespace(mode="CUTOFF", device_id=DEV, ns="t87", generation=1, epoch=1, listen="", admin_listen="",
                              broker="127.0.0.1:1", contract_file=str(cfile), state=str(tmp / "gateway0.state.jsonl"), budget_s=0.5,
                              qos_cmd=1, keepalive_s=60, offline_mode="drop", binding="qos1", cut=False, recovery=recovery,
                              log=str(tmp / "gateway0.jsonl"))


class RebootRecoveryTest(unittest.TestCase):
    def run_arm(self, policy: str, gateway_recovery: bool, after: int) -> dict:
        exe = go_binary("evicon-authority")
        if exe is None:
            self.skipTest("Go toolchain not available (run_tests.py --require-go turns this into a failure)")
        tmp = Path(tempfile.mkdtemp(prefix="evicon-rb087-"))
        fw = BootFirmware()
        link = FakeLink()
        measured = tmp / "measured-cutoff.json"
        measured.write_text(json.dumps(MEASURED_CUTOFF))
        import hashlib
        cfile = tmp / "ZEPHYR_F_BOOT.json"
        cfile.write_text(json.dumps(restrict_contract(MEASURED_CUTOFF, hashlib.sha256(measured.read_bytes()).hexdigest(), {"F"})))

        def net(gw):
            return link, FakeConn(gw, fw, link, "data"), FakeConn(gw, fw, link, "ctl")
        gw = G87.Ext087Gateway(gateway_args(tmp, cfile, gateway_recovery), net=net)
        Data, _ = G87.make_handlers(gw)
        gport = free_port()
        srv = ThreadingHTTPServer(("127.0.0.1", gport), Data)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        aport = free_port()
        a = {"ambiguity_after": "1s", "resolve_sweep": "1s"}
        argv = C.authority_args({"name": "rb087", "authority": a, "workload": {"bound": BOUND}}, "ZEPHYR_F_BOOT", policy, str(exe), DEV,
                                f"http://127.0.0.1:{gport}/d/{DEV}", str(tmp / "authority.db"), f"127.0.0.1:{aport}",
                                str(tmp / "authority.jsonl"), str(REPO / "experiments" / "configs" / "query_costs.json"))
        log = open(tmp / "authority.log", "w")
        proc = subprocess.Popen(argv, cwd=REPO, stdout=log, stderr=subprocess.STDOUT)
        auth = f"http://127.0.0.1:{aport}"
        try:
            wait_http(auth + "/healthz", 20)

            def submit(cid, value, seq):
                body = {"schema_version": 1, "domain_id": DEV, "generation": 1, "command_id": cid, "operation": "ADD", "value": [value],
                        "epoch": 1, "sequence": seq}
                return http("POST", auth + "/v1/commands", body)

            def wait_status(cid, want, timeout=6.0):
                end = time.monotonic() + timeout
                while time.monotonic() < end:
                    _, o = http("GET", f"{auth}/v1/commands/{cid}")
                    if isinstance(o, dict) and o.get("status") in want:
                        return o.get("status")
                    time.sleep(0.1)
                return None
            for i, v in enumerate((5, 5, 5, 4), start=1):
                _, out = submit(f"d{i}", v, i)
                self.assertEqual(out.get("decision"), "ADMITTED", out)
                self.assertEqual(wait_status(f"d{i}", ("APPLIED",)), "APPLIED")
            self.assertEqual(fw.total(), 19)
            floor = fw.reboot()
            self.assertEqual(floor, 1 + 64, "the claim mark of the first boot: the first sequence plus one claim block")
            gw.on_reply("hello", {"device": DEV, "hello": True, "floor": floor})  # what the firmware publishes after reconnecting
            fw.drop.add("c3")
            seq, n, admitted, last_admit = 4, 0, [], time.monotonic()
            end = time.monotonic() + 45
            while time.monotonic() < end and len(admitted) < after:
                n += 1
                seq += 1
                cid = f"c{n}"
                deadline = time.monotonic() + 4
                got = False
                while time.monotonic() < deadline:
                    _, out = submit(cid, 2, seq)
                    dec = out.get("decision") if isinstance(out, dict) else None
                    if dec == "ADMITTED":
                        admitted.append(cid)
                        got = True
                        last_admit = time.monotonic()
                        wait_status(cid, ("APPLIED", "STOPPED"), 3)
                        break
                    if dec == "REJECT":
                        break
                    time.sleep(0.3)
                if not got and time.monotonic() - last_admit > 12:
                    break  # nothing admitted for 12 s: the domain does not admit any more
            time.sleep(1.0)
            events = [json.loads(x) for x in (tmp / "authority.jsonl").read_text().splitlines() if x.strip()]
            res = {"total": fw.total(), "admitted": admitted, "events": events, "floor": floor, "gateway": gw.describe087(),
                   "level": int(fw.lib.evc_shim_level())}
            print(f"\n  reboot recovery {policy}: floor {floor}, plant total {res['total']} (budget {BOUND}), level since the reboot "
                  f"{res['level']}, admitted after the reboot {len(admitted)}, gateway {res['gateway']}; FENCE_ANCHOR_CHECK "
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

    def test_rb1_recovery_restores_liveness_safely(self):
        r = self.run_arm(ext087.EVICON_FI_REC, True, after=14)
        ev = r["events"]
        floors = [e for e in ev if e["event"] == "OWNER_FLOOR"]
        self.assertTrue(any(f"floor={r['floor']};" in e.get("reason", "") for e in floors), floors)
        adv = [e for e in ev if e["event"] == "SEQUENCE_ADVANCED"]
        self.assertTrue(adv and f"owner={r['floor'] + 1};" in adv[0]["reason"], adv)
        self.assertGreaterEqual(r["gateway"]["sealed"], 1, "client sequence 5 lies in the claimed range: the gateway seals it")
        checks = [e["outcome"] for e in ev if e["event"] == "FENCE_ANCHOR_CHECK"]
        self.assertIn("MATCH_FLOOR", checks)
        self.assertNotIn("MISMATCH_FLOOR", checks)
        self.assertFalse([e for e in ev if e["event"] == "EVIDENCE_CONFLICT"])
        self.assertGreater(r["level"], 0, "commands took effect after the reboot (liveness restored)")
        self.assertLessEqual(r["total"], BOUND, f"the plant exceeded the budget: {r['total']} > {BOUND}")
        self.assertGreaterEqual(r["total"], BOUND - 3, "the guard admits up to the budget after the fence")

    def test_rb2_release_086_loses_liveness(self):
        r = self.run_arm("EVICON_FI", False, after=14)
        ev = r["events"]
        self.assertFalse([e for e in ev if e["event"] in ("OWNER_FLOOR", "SEQUENCE_ADVANCED")], "no recovery without the flag")
        self.assertEqual(r["level"], 0, "nothing takes effect after the reboot: every new identity is in the claimed range")
        self.assertEqual(r["total"], 19)
        self.assertEqual(r["gateway"]["sealed"], 0)

    def test_rb3_owner_state_negative_control_violates(self):
        r = self.run_arm(ext087.EVICON_FI_REC_OS, True, after=20)
        checks = [e["outcome"] for e in r["events"] if e["event"] == "FENCE_ANCHOR_CHECK"]
        self.assertTrue(any(c.startswith("MISMATCH") for c in checks), checks)
        self.assertGreater(r["total"], BOUND, f"negative control: the owner-state anchor after the reboot must let the plant exceed "
                                              f"the budget (total {r['total']}, admitted {r['admitted']})")


if __name__ == "__main__":
    unittest.main()
