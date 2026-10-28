"""Release 078: re-provisioning the testbed after a re-installation (new addresses, no earlier runs on disk).

C1 the shipped configs' addresses are replaced in one pass, as whole addresses (a new address may equal another
   role's shipped one)
C2 testbed.json is required on the testbed, its five addresses are IPv4 and distinct, and VM-C's is one of this
   machine's own; the setup scripts take the addresses only from it (deploy/setup/testbed_env.py)
C3 deploy/setup/testbed_init.sh write: testbed.json from the given addresses, VM-C's detected, checked as above
C4 scripts/import_results.py: result bundles -> one archive (and scripts/make_data_archive.py packs its runs); incremental bundles of one review run merged, later
   files replace earlier ones; a checksum file that does not match skips its bundle; members outside the bundle
   are refused; missing schedule files regenerated from the run's config with campaign.schedule, archived ones
   checked byte for byte; --lean keeps the traces of the 077 plan's reference runs only
C5 `report --archive DIR` reads the runs the newest report in the archive read (a run it lacks: named, with its bundle)
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from dataclasses import asdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "evaluation"))
sys.path.insert(0, str(REPO / "scripts"))
import campaign as C  # noqa: E402
import evicon_review as er  # noqa: E402
import import_results as IR  # noqa: E402


class C1Substitute(unittest.TestCase):
    def test_one_pass_whole_addresses(self):
        tb = er.Testbed({"vm_a": "192.168.107.152", "vm_b": "192.168.107.151", "vm_c": "10.0.0.3", "vm_d": "10.0.0.4",
                         "vm_e": "10.0.0.5"}, local=False)
        text = '{"a": "192.168.107.151:19000", "b": "http://192.168.107.152:9000", "x": "192.168.107.1510", "y": "10.192.168.107.150"}'
        out = json.loads(tb.substitute(text))
        self.assertEqual(out, {"a": "192.168.107.152:19000", "b": "http://192.168.107.151:9000", "x": "192.168.107.1510",
                               "y": "10.192.168.107.150"})

    def test_every_shipped_config_gets_the_testbed_addresses(self):
        tb = er.Testbed({"vm_a": "10.1.1.11", "vm_b": "10.1.1.12", "vm_c": "10.1.1.10", "vm_d": "10.1.1.13", "vm_e": "10.1.1.14"}, local=False)
        for p in list((REPO / "evaluation" / "campaigns").glob("*.json")) + list((REPO / "conformance" / "configs").glob("*.json")):
            out = tb.substitute(p.read_text())
            self.assertNotRegex(out, r"192\.168\.107\.15[0-4](?!\d)", p.name)


class C2Testbed(unittest.TestCase):
    CFG = {"vm_a": "10.1.1.11", "vm_b": "10.1.1.12", "vm_c": "10.1.1.10", "vm_d": "10.1.1.13", "vm_e": "10.1.1.14"}

    def test_checks(self):
        er.check_testbed_cfg(dict(self.CFG), "t", own={"10.1.1.10"})
        for bad, msg in ((dict(self.CFG, vm_b="10.1.1.11"), "same address"), (dict(self.CFG, vm_d="10.1.1.300"), "IPv4"),
                         (dict(self.CFG, vm_e="vm-e"), "IPv4")):
            with self.assertRaises(SystemExit) as e:
                er.check_testbed_cfg(bad, "t", own={"10.1.1.10"})
            self.assertIn(msg, str(e.exception))
        with self.assertRaises(SystemExit) as e:
            er.check_testbed_cfg(dict(self.CFG), "t", own={"192.168.107.150"})
        self.assertIn("another installation", str(e.exception))

    def test_required_on_the_testbed(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        with self.assertRaises(SystemExit) as e:
            er.load_testbed(str(tmp / "none.json"), local=False)
        self.assertIn("not found", str(e.exception))
        orig, orig_run = er.REPO, er.RUN

        class _Run:
            path = tmp
        try:
            er.REPO, er.RUN = tmp, _Run()  # no testbed.json there
            with self.assertRaises(SystemExit) as e:
                er.load_testbed(None, local=False)
            self.assertIn("testbed_init.sh", str(e.exception))
            self.assertEqual(er.load_testbed(None, local=True).ip("B"), "127.0.0.1")  # the simulated testbed needs none
            (tmp / "testbed.json").write_text(json.dumps(dict(self.CFG, vm_c="192.0.2.77")))
            with self.assertRaises(SystemExit) as e:
                er.load_testbed(None, local=False)
            self.assertIn("another installation", str(e.exception))
        finally:
            er.REPO, er.RUN = orig, orig_run

    def test_setup_scripts_take_addresses_from_testbed_json(self):
        for p in sorted((REPO / "deploy" / "setup").glob("*.sh")):
            self.assertNotRegex(p.read_text(), r"192\.168\.107\.15[0-4]", p.name)
        self.assertIn("testbed_env.py", (REPO / "deploy" / "setup" / "setup_all.sh").read_text())
        self.assertIn("testbed_env.py", (REPO / "deploy" / "setup" / "build_iot.sh").read_text())
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        p = subprocess.run([sys.executable, str(REPO / "deploy" / "setup" / "testbed_env.py"), "--file", str(tmp / "testbed.json")],
                           capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("testbed_init.sh", p.stderr)
        (tmp / "testbed.json").write_text(json.dumps(dict(self.CFG, vm_c="127.0.0.1")))
        p = subprocess.run([sys.executable, str(REPO / "deploy" / "setup" / "testbed_env.py"), "--file", str(tmp / "testbed.json")],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("VM_B=10.1.1.12", p.stdout)


@unittest.skipUnless(os.geteuid() == 0 and shutil.which("ip"), "testbed_init.sh runs as root")
class C3TestbedInit(unittest.TestCase):
    def test_write(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        env = dict(os.environ, TESTBED_JSON=str(tmp / "testbed.json"))
        sh = ["bash", str(REPO / "deploy" / "setup" / "testbed_init.sh"), "write"]
        p = subprocess.run(sh + ["--vm-a", "10.2.0.11", "--vm-b", "10.2.0.12", "--vm-d", "10.2.0.13", "--vm-e", "10.2.0.14", "--vm-c", "127.0.0.1"],
                           capture_output=True, text=True, env=env)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(json.loads((tmp / "testbed.json").read_text()),
                         {"vm_a": "10.2.0.11", "vm_b": "10.2.0.12", "vm_c": "127.0.0.1", "vm_d": "10.2.0.13", "vm_e": "10.2.0.14", "ssh_user": "root"})
        p = subprocess.run(sh + ["--vm-a", "10.2.0.11", "--vm-b", "10.2.0.11", "--vm-d", "10.2.0.13", "--vm-e", "10.2.0.14", "--vm-c", "127.0.0.1"],
                           capture_output=True, text=True, env=env)
        self.assertNotEqual(p.returncode, 0)
        p = subprocess.run(sh + ["--vm-a", "10.2.0.11", "--vm-b", "10.2.0.12", "--vm-d", "10.2.0.13", "--vm-e", "10.2.0.14"],
                           capture_output=True, text=True, env=env)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)  # VM-C's address detected on this machine
        self.assertIn(json.loads((tmp / "testbed.json").read_text())["vm_c"], er.local_ipv4())
        self.assertTrue(list(tmp.glob("testbed.json.bak-*")))


def _bundle(path: Path, files: dict):
    top = path.stem
    with zipfile.ZipFile(path, "w") as z:
        for rel, data in files.items():
            z.writestr(f"{top}/{rel}", data)


class C4Import(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bk = self.tmp / "backup"
        self.bk.mkdir()

    def _cfg(self, name, eps):
        cfg = C.load_config(REPO / "evaluation" / "campaigns" / f"{name}.json")
        cfg["episodes"] = eps
        return cfg

    def _sched(self, cfg, ep):
        a, w = C.schedule(cfg, ep)
        return json.dumps({"arrivals": [asdict(x) for x in a], "faults": [asdict(x) for x in w]})

    def test_merge_schedules_lean_and_refusals(self):
        rr = "results/review-20261021T173508"
        wq = self._cfg("expW_queue_w6", 2)
        sw = self._cfg("expS_winf", 1)
        wq_run, sw_run = f"{rr}/campaigns/expW-queue-w6/20261021T194415", f"{rr}/campaigns/expS-winf/20261023T010003"
        _bundle(self.bk / "EVICON_072_result_20261022T0136.zip", {
            f"{rr}/source.json": json.dumps({"release": "072"}),
            f"{wq_run}/config.json": json.dumps(wq), f"{wq_run}/schedule_ep0.json": self._sched(wq, 0),
            f"{wq_run}/runtime_episodes.csv": "regime,contract,policy,episode,cell_status\nexpW-queue-w6,K0,EVICON,0,VALID\n",
            f"{wq_run}/cells/ep0/K0/EVICON/raw/A/authority0.jsonl": "{}\n",
            f"{rr}/stages.json": '{"v": 1}', "bundle/environment.json": "{}"})
        _bundle(self.bk / "EVICON_072_result_20261023T0802.zip", {
            f"{rr}/stages.json": '{"v": 2}',
            f"{sw_run}/config.json": json.dumps(sw), f"{sw_run}/schedule_ep0.json": self._sched(sw, 0),
            f"{sw_run}/runtime_episodes.csv": "regime,contract,policy,episode,cell_status\nexpS-winf,K0,EVICON,0,VALID\n",
            f"{sw_run}/cells/ep0/K0/EVICON/raw/A/authority0.jsonl": "{}\n"})
        (self.bk / "EVICON_072_result_20261023T0802.zip.sha256").write_text(
            IR.sha256_file(self.bk / "EVICON_072_result_20261023T0802.zip") + "  EVICON_072_result_20261023T0802.zip\n")
        _bundle(self.bk / "EVICON_073_result_20261023T1447.zip", {"results/review-20261023T083022/x.json": "{}"})
        (self.bk / "EVICON_073_result_20261023T1447.zip.sha256").write_text("0" * 64 + "  EVICON_073_result_20261023T1447.zip\n")
        with zipfile.ZipFile(self.bk / "EVICON_074_result_20261024T1158.zip", "w") as z:
            z.writestr("EVICON_074_result_20261024T1158/results/review-20261024T031941/ok.json", "{}")
            z.writestr("EVICON_074_result_20261024T1158/../../evil.txt", "x")
        arch = self.tmp / "arch"
        out = io.StringIO()
        import contextlib
        with contextlib.redirect_stdout(out):
            rc = IR.main([str(self.bk), "--archive", str(arch), "--lean", "--force-space"])
        self.assertEqual(rc, 1, out.getvalue())  # two bundles refused
        st = json.loads((arch / "archive.json").read_text())
        self.assertEqual({f["bundle"] for f in st["failed"]}, {"EVICON_073_result_20261023T1447.zip", "EVICON_074_result_20261024T1158.zip"})
        self.assertFalse((self.tmp / "evil.txt").exists() or (arch.parent / "evil.txt").exists())
        run = arch / "runs" / "review-20261021T173508"
        self.assertEqual(json.loads((run / "stages.json").read_text()), {"v": 2})  # the later bundle's copy
        wq_dir = arch / "runs" / wq_run[len("results/"):]
        self.assertEqual((wq_dir / "schedule_ep1.json").read_text(), self._sched(wq, 1))  # regenerated
        self.assertEqual(json.loads((wq_dir / "schedule_regenerated.json").read_text())["episodes"], [1])
        self.assertTrue((wq_dir / "cells/ep0/K0/EVICON/raw/A/authority0.jsonl").exists())  # a reference run of the plan: kept
        sw_dir = arch / "runs" / sw_run[len("results/"):]
        self.assertFalse((sw_dir / "cells/ep0/K0/EVICON/raw/A/authority0.jsonl").exists())  # --lean: left out
        self.assertTrue((sw_dir / "runtime_episodes.csv").exists())
        self.assertTrue((arch / "bundles" / "EVICON_072_result_20261022T0136" / "bundle" / "environment.json").exists())
        checks = {s["run"]: s["check"] for s in st["schedules"]}
        self.assertEqual(set(checks.values()), {"identical"})
        md = (arch / "ARCHIVE.md").read_text()
        self.assertIn("| Wq | `expW-queue-w6` |", md)
        self.assertIn("**MISSING**", md)
        self.assertIn("No review run of the archive holds a report", md)
        # importing again changes nothing
        with contextlib.redirect_stdout(io.StringIO()):
            IR.main([str(self.bk / "EVICON_072_result_20261022T0136.zip"), "--archive", str(arch), "--lean", "--force-space"])
        st2 = json.loads((arch / "archive.json").read_text())
        b = [x for x in st2["bundles"] if x["bundle"] == "EVICON_072_result_20261022T0136.zip"][0]
        self.assertEqual((b["written"], b["superseded"]), (0, 1))  # stages.json: the later bundle's copy stays
        self.assertEqual(json.loads((run / "stages.json").read_text()), {"v": 2})
        self.assertFalse(list((arch / "runs").glob("review-20261024T031941")))  # the refused bundle wrote nothing
        self.assertEqual(st2["runs"]["review-20261021T173508"]["release"], "072")
        # the data archive packs an archived run under the release directory of its bundles
        import make_data_archive as MDA
        out = self.tmp / "data"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(MDA.main(["pack", "--run", str(run), "--out", str(out)]), 0)
        z = out / "EVICON_TOIT_072__review-20261021T173508.zip"
        with zipfile.ZipFile(z) as f:
            self.assertIn(f"EVICON_TOIT_072/{wq_run}/config.json", f.namelist())
        self.assertEqual((out / "SOURCES.txt").read_text().split(), ["EVICON_TOIT_072/results/review-20261021T173508"])

    def test_plan_references(self):
        self.assertEqual(IR.plan_references(), {"C": "expC-minio", "C2": "expC2-zephyr", "C3": "expC3-ablation", "Wqi": "expW-queue-winf",
                                                "Wdi": "expW-direct-winf", "S": "expS-w6", "A2": "expA-confirm-k2", "L": "expL-sleepy",
                                                "Wq": "expW-queue-w6", "Wd": "expW-direct-w6"})

    def test_bundle_order(self):
        names = ["EVICON_072_result_20261023T0802.zip", "EVICON_067_result.zip", "EVICON_072_result_20261022T0136.zip"]
        for n in names:
            _bundle(self.bk / n, {"results/review-20261016T181012/a.json": "{}"})
        self.assertEqual([p.name for p in IR.expand([str(self.bk)])],
                         ["EVICON_067_result.zip", "EVICON_072_result_20261022T0136.zip", "EVICON_072_result_20261023T0802.zip"])


class C5ReportArchive(unittest.TestCase):
    def test_archive_sources(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for r in ("review-20261016T181012", "review-20261021T010856", "review-20261023T083022", "review-20261025T030437"):
            (tmp / "runs" / r).mkdir(parents=True)
        (tmp / "runs" / "review-20261023T083022" / "report").mkdir()
        (tmp / "runs" / "review-20261023T083022" / "report" / "REPORT.md").write_text(
            "# EviCon report\n\nRelease 073; sources:\n\n- `/root/EVICON_TOIT_067/results/review-20261016T181012`\n\n- [x](y)\n")
        (tmp / "runs" / "review-20261025T030437" / "report").mkdir()
        (tmp / "runs" / "review-20261025T030437" / "report" / "REPORT.md").write_text(
            "# EviCon report\n\nRelease 076; written x; sources:\n\n- `/root/EVICON_TOIT_067/results/review-20261016T181012`\n"
            "- `/root/EVICON_TOIT_070/results/review-20261021T010856`\n- `/root/EVICON_TOIT_073/results/review-20261023T083022`\n"
            "- `/root/EVICON_TOIT_076/results/review-20261025T030437`\n\n- [Fence](fence_audit/fence_audit.md)\n")

        class _Run:
            def log(self, *a):
                pass
        orig = er.RUN
        er.RUN = _Run()
        try:
            # the newest report's sources, not every archived run
            self.assertEqual(er._archive_sources(str(tmp)), [str(tmp / "runs" / r) for r in (
                "review-20261016T181012", "review-20261021T010856", "review-20261023T083022", "review-20261025T030437")])
            shutil.rmtree(tmp / "runs" / "review-20261021T010856")
            with self.assertRaises(er.StageFail) as e:
                er._archive_sources(str(tmp))
            self.assertIn("EVICON_070_result", str(e.exception))
            self.assertEqual(len(er._archive_sources(str(tmp), missing_ok=True)), 3)
            with self.assertRaises(er.StageFail):
                er._archive_sources(str(tmp / "empty"))
        finally:
            er.RUN = orig
        src = (REPO / "evicon_review.py").read_text()
        self.assertTrue(re.search(r'add_argument\("--archive"', src))
        self.assertIn('"import-results"', src)


if __name__ == "__main__":
    unittest.main()
