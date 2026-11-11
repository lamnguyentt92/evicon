#!/usr/bin/env python3
"""Release 088 checks (release 087 with two unit tests made independent of the disk's speed; plan PREREG_extra_087.md runs
unchanged).

L1 the release is 088 everywhere (VERSION, buildinfo, CITATION.cff version and date, the VM set-up banners, CHANGELOG, the
   release notes, README, ARTIFACT)
L2 the two tests: TestShadowReconcileK2Safe086 with a 1 s reconciliation window and a 1.1 s wait; TestRecWindow087 waits
   5 ms before its sweeps; their assertions are those of release 087
L3 plan 087 and every earlier plan are byte-identical (plan 087's frozen files included); the measured firmware hash
L4 run088.sh replaces run087.sh (same steps, the 088 directory and bundles, nohup); the 088 guide replaces the 087 guide
L5 provenance: plan 087 as published by release 087 (2e98edc) and the deviation (it runs under release 088)
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

PLAN = "evaluation/prereg/PREREG_extra_087.md"
PLAN_SHA = "f42bafffc96187f04d93e39d8f9d51bebd3cbf2fc306cb55765515ae2b86f100"
EARLIER = {"evaluation/prereg/PREREG_review_077.md": "db67640d1709b06dfa5e61f77e3444c07e7c5828dfe24e0e85d163f06a711a29",
           "evaluation/prereg/PREREG_extra_083.md": "6e3f24a918de659c1feb8aaa886d9e9681195353528b5df10e615e8897ff3a06",
           "evaluation/prereg/PREREG_extra_084.md": "b11351645c1e0f05f4a270412539a072f17148ea8607223dcb6182a171505345",
           "evaluation/prereg/PREREG_extra_085.md": "22586c782aa3ac704bcc56376324faeb7c4047da6e35c1668a65a34924bf4790",
           "evaluation/prereg/PREREG_extra_086.md": "d28956e98e4fff3ba032f14e1a3a48c1e92d9e0ffa1b75e14d9a36ca3adba9c6"}
FIRMWARE_086 = "907b2ccdc11ed3d32c6d311e6870741fd256a23885a8db8a75732beb73896f9d"
GUIDE = "EVICON_088_" + "RUN_GUIDE" + "_VI.md"
OLD_GUIDE = "EVICON_087_" + "RUN_GUIDE" + "_VI.md"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def go_func(path: Path, name: str) -> str:
    s = path.read_text()
    i = s.index(f"func {name}(")
    j = s.find("\nfunc ", i + 1)
    return s[i:j if j > 0 else len(s)]


class L1Version(unittest.TestCase):
    def test_version(self):
        v = (REPO / "VERSION").read_text().strip()
        self.assertGreaterEqual(v, "088")
        self.assertIn(f'const Release = "{v}"', (REPO / "internal" / "buildinfo" / "buildinfo.go").read_text())
        cit = (REPO / "CITATION.cff").read_text()
        self.assertIn(f'version: "{v}"', cit)
        for f in sorted((REPO / "deploy" / "setup").glob("vm-*.sh")):
            self.assertIn(f"(EviCon {v})", f.read_text(), f.name)
        if v == "088":
            self.assertIn('date-released: "2026-11-11"', cit)
            self.assertTrue((REPO / "CHANGELOG.md").read_text().split("\n## ")[1].startswith("088"))
            first = (REPO / "RELEASE_NOTES_VI.md").read_text().splitlines()[0]
            self.assertTrue(first.startswith("# EviCon") and first.endswith("088 (11/11/2026)"), first)
            self.assertIn("Release **088**", (REPO / "README.md").read_text())
            self.assertIn("Release **088**", (REPO / "ARTIFACT.md").read_text())
        self.assertIn("## Release 088", (REPO / "evaluation" / "README.md").read_text())


class L2Tests(unittest.TestCase):
    def test_shadow_window(self):
        f = go_func(REPO / "internal" / "runtime" / "shadow_reconcile_test.go", "TestShadowReconcileK2Safe086")
        self.assertIn("shadowAuthority(t, r, time.Second)", f)
        self.assertNotIn("30*time.Millisecond", f)
        self.assertIn("time.Sleep(1100 * time.Millisecond)", f)
        self.assertLess(f.index("before the reconciliation timeout nothing is read"), f.index("time.Sleep(1100 * time.Millisecond)"))
        for x in ("B must be recorded APPLIED from the reported state", "A must stay unresolved (never released)",
                  "the next read waits for the next timeout", "each command takes effect once (level 8, no duplicate)"):
            self.assertIn(x, f)

    def test_rec_window_wait(self):
        f = go_func(REPO / "internal" / "runtime" / "reboot_recovery_test.go", "TestRecWindow087")
        i, j = f.index("r.deliverHeld(1 << 10)"), f.index("time.Sleep(5 * time.Millisecond)")
        self.assertLess(i, j)
        self.assertLess(j, f.index("_ = a.SweepOnce() // a fence: h1 listed UNCERTAIN"))
        for x in ("h1 must stay pending, fence-uncertain and without effect", "REC-4 must keep h1's reservation",
                  "a candidate of 22 fits with h1 reserved"):
            self.assertIn(x, f)
        rig = go_func(REPO / "internal" / "runtime" / "reboot_recovery_test.go", "recAuthority")
        self.assertIn("AmbiguityAfter: time.Millisecond", rig)


class L3Frozen(unittest.TestCase):
    def test_plans(self):
        self.assertEqual(sha(REPO / PLAN), PLAN_SHA)
        for p, h in EARLIER.items():
            self.assertEqual(sha(REPO / p), h, p)
        m = re.search(r"```frozen-files\n(.*?)```", (REPO / PLAN).read_text(), re.S)
        files = dict(reversed(ln.split("  ", 1)) for ln in m.group(1).splitlines() if ln.strip())
        self.assertGreaterEqual(len(files), 140)
        for path, h in files.items():
            self.assertEqual(sha(REPO / path), h, path)

    def test_firmware(self):
        self.assertIn(FIRMWARE_086, (REPO / "evicon_review.py").read_text())


class L4Script(unittest.TestCase):
    def test_run088(self):
        self.assertFalse((REPO / "run087.sh").exists())
        s = (REPO / "run088.sh").read_text()
        for x in ('cd "${EVICON088_DIR:-/root/EVICON_TOIT_088}"', 'LABEL="run088"', "EVICON_088_result_", "######## EviCon 088",
                  "nohup bash /root/EVICON_TOIT_088/run088.sh >> /root/run088.log", 'CAMPAIGNS="expSHR_zephyr_w6 expSHR_minio_w6 expRB_zephyr_w6"'):
            self.assertIn(x, s)
        self.assertNotIn("EVICON_087_result_", s)
        self.assertNotIn("tmux new", s)
        r = subprocess.run(["bash", "-n", str(REPO / "run088.sh")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_guide(self):
        self.assertTrue((REPO / GUIDE).exists())
        self.assertFalse((REPO / OLD_GUIDE).exists())
        g = (REPO / GUIDE).read_text()
        for x in ("run088.sh", "/root/EVICON_TOIT_088", "nohup", "PREREG_extra_087.md", "f42baff"):
            self.assertIn(x, g)
        self.assertNotIn("tmux new", g)
        self.assertNotIn("tmux attach", g)


class L5Provenance(unittest.TestCase):
    def test_published(self):
        import provenance as PV
        self.assertEqual((PV.NEWEST_PLAN, PV.NEWEST_RELEASE), ("PREREG_extra_087.md", "088"))
        p = PV.PUBLISHED["PREREG_extra_087.md"]
        self.assertEqual((p[0]["commit"], p[0]["utc"], p[0]["sha256"]), ("2e98edc", "2026-11-11 04:50", PLAN_SHA))
        d = " ".join(PV.DEVIATIONS["PREREG_extra_087.md"])
        for x in ("release 088", "review-20261111T120106", "TestShadowReconcileK2Safe086", "TestRecWindow087", "no runtime code"):
            self.assertIn(x, d)
        top = (REPO / "PROVENANCE.md").read_text()
        for x in ("2e98edc", "(release 088)", "expRB-zephyr-w6/<to be filled by the 088 report>"):
            self.assertIn(x, top)


if __name__ == "__main__":
    unittest.main()
