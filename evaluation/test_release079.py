"""Release 079: the report's safety total and the archive's completeness.

D1 safety_total classifies a cell as the driver does: the cells planned to be able to violate safety (the practice
   baselines, a blind retry on ThingsBoard, QoS 1 on the plain firmware under the offline cut) are "unsafe by
   design", apart from the guarded cells and from the negative controls (NEG_*, a blind retry on an owner without
   dedup), whose every cell must detect a violation; evicon_review.py takes the planned groups from
   safety_total.planned_unsafe, and it equals the 078 driver's rule on every shipped config
D2 import_results / report --archive: a campaign run the newest report names that the archive lacks is reported
   (a review run can be there while only some of its incremental bundles were imported)
D3 vm-c.sh installs the mosquitto binary for the gateway tests and disables only the service it installed
"""
import csv
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "evaluation"))
sys.path.insert(0, str(REPO / "scripts"))
import evicon_review as er  # noqa: E402
import import_results as IR  # noqa: E402
import safety_total as T  # noqa: E402

FIELDS = ["contract", "policy", "episode", "cell_status", "offered", "admitted", "dispatch_attempts", "late_effects"] + list(T.KINDS)


def cfg_of(name):
    return json.loads((REPO / "evaluation" / "campaigns" / f"{name}.json").read_text())


def write_run(d: Path, cfg: dict, rows: list) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text(json.dumps(cfg))
    (d / "manifest.json").write_text(json.dumps({"release": "078", "scope": "TESTBED"}))
    with open(d / "runtime_episodes.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, 0) for k in FIELDS})
    return d


def row(contract, policy, ep=0, **kw):
    r = {"contract": contract, "policy": policy, "episode": ep, "cell_status": "VALID", "offered": 100, "admitted": 60,
         "dispatch_attempts": 70}
    r.update(kw)
    return r


def old_expected_unsafe(cfg: dict) -> set:
    """The 078 driver's rule, verbatim (the reference D1 checks against)."""
    ro = cfg.get("real_owner") or {}
    out = set()
    for pol, p in ro.get("policies", {}).items():
        if pol in cfg["policies"] and p.get("authority", {}).get("retry_guard") is False:
            out |= {(ct, pol) for ct in cfg["contracts"] if ro["contracts"][ct].get("mode") == "PLAIN" or ro.get("system") == "thingsboard"}
    if ro.get("offline_cut"):
        out |= {(ct, pol) for ct in cfg["contracts"] for pol in cfg["policies"]
                if ro["contracts"][ct]["mode"] == "PLAIN" and ro["contracts"][ct].get("binding") == "qos1"}
    return out


class D1SafetyTotal(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_driver_and_report_share_one_rule(self):
        for p in sorted((REPO / "evaluation" / "campaigns").glob("*.json")):
            cfg = json.loads(p.read_text())
            if "contracts" not in cfg or "policies" not in cfg:
                continue
            self.assertEqual(er._expected_unsafe(cfg), old_expected_unsafe(cfg), p.name)
            self.assertEqual(T.planned_unsafe(cfg), old_expected_unsafe(cfg), p.name)
        self.assertEqual(T.planned_unsafe(cfg_of("expQ_zephyr")), {("ZEPHYR_PLAIN_QOS1", "EVICON")})
        self.assertEqual(T.planned_unsafe(cfg_of("expTB_thingsboard")), {("TB_RPC", "NAIVE")})
        self.assertEqual(T.planned_unsafe(cfg_of("expC_minio")), {("MINIO_PLAIN", "NAIVE")})

    def test_kinds(self):
        q, tb, c = cfg_of("expQ_zephyr"), cfg_of("expTB_thingsboard"), cfg_of("expC_minio")
        self.assertEqual(T.kind(q, "ZEPHYR_PLAIN_QOS1", "EVICON"), "unsafe")
        self.assertEqual(T.kind(q, "ZEPHYR_PLAIN_QOS0", "EVICON"), "guarded")
        self.assertEqual(T.kind(q, "ZEPHYR_CUTOFF_QOS1", "EVICON"), "guarded")
        self.assertEqual(T.kind(tb, "TB_RPC", "NAIVE"), "unsafe")
        self.assertEqual(T.kind(tb, "TB_RPC", "M1"), "guarded")
        self.assertEqual(T.kind(c, "MINIO_PLAIN", "NAIVE"), "control")
        self.assertEqual(T.kind(c, "MINIO_COND", "NAIVE"), "unguarded_dedup")
        self.assertEqual(T.kind(cfg_of("expRT_queue"), "K0", "RELEASE_ON_TIMEOUT"), "unsafe")
        self.assertEqual(T.kind({}, "NEG_DEDUP", "EVICON"), "control")

    def test_total_over_the_078_shapes(self):
        q = write_run(self.tmp / "expQ-zephyr" / "20261028T124113", cfg_of("expQ_zephyr"),
                      [row("ZEPHYR_PLAIN_QOS1", "EVICON", 0, duplicate_effects=3), row("ZEPHYR_PLAIN_QOS1", "EVICON", 1),
                       row("ZEPHYR_PLAIN_QOS0", "EVICON"), row("ZEPHYR_CUTOFF_QOS1", "EVICON")])
        tb = write_run(self.tmp / "expTB-thingsboard" / "20261030T000000", cfg_of("expTB_thingsboard"),
                       [row("TB_RPC", "NAIVE", duplicate_effects=2), row("TB_RPC", "EVICON"), row("TB_RPC", "M1")])
        c = write_run(self.tmp / "expC-minio" / "20261023T083658", cfg_of("expC_minio"),
                      [row("MINIO_PLAIN", "NAIVE", duplicate_effects=5), row("MINIO_COND", "NAIVE"), row("MINIO_PLAIN", "EVICON")])
        out = self.tmp / "out"
        self.assertEqual(T.main(["--out", str(out), str(q), str(tb), str(c)]), 0)
        s = json.loads((out / "safety.json").read_text())["summary"]
        self.assertEqual(s["guarded_violations"], 0)
        self.assertEqual(s["guarded"]["cells"], 5)
        self.assertEqual(s["controls"], {"cells": 1, "detecting_cells": 1})
        self.assertEqual(s["unsafe_by_design"], {"cells": 3, "cells_with_violation": 2})
        self.assertEqual(s["unguarded_dedup"], {"cells": 1, "cells_with_violation": 0})
        md = (out / "SAFETY.md").read_text()
        self.assertIn("NO VIOLATION IN ANY GUARDED CELL", md)
        self.assertIn("ZEPHYR_PLAIN_QOS1/EVICON 1/2", md)
        # the claims check: supported (the unsafe-by-design cells need not all detect)
        import claims_check as CC
        rep = self.tmp / "report"
        (rep / "safety").mkdir(parents=True)
        shutil.copy(out / "safety.json", rep / "safety" / "safety.json")
        self.assertEqual(CC.claim_safety(rep)["status"], "SUPPORTED")


class D2ArchiveCompleteness(unittest.TestCase):
    def test_missing_campaign_run_of_a_present_review_run(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        rr = tmp / "runs" / "review-20261021T173508"
        (rr / "campaigns" / "expS-w6" / "20261022T161003").mkdir(parents=True)
        (rr / "campaigns" / "expS-w6" / "20261022T161003" / "runtime_episodes.csv").write_text("contract\n")
        rep = tmp / "runs" / "review-20261025T030437" / "report"
        (rep / "safety").mkdir(parents=True)
        (rep / "REPORT.md").write_text("# EviCon report\n\nRelease 076; sources:\n\n- `/root/EVICON_TOIT_072/results/review-20261021T173508`\n\n- [x](y)\n")
        (rep / "safety" / "SAFETY.md").write_text("| expS-w6 | `/root/EVICON_TOIT_072/results/review-20261021T173508/campaigns/expS-w6/20261022T161003` |\n"
                                                  "| expL-sleepy | `/root/EVICON_TOIT_072/results/review-20261021T173508/campaigns/expL-sleepy/20261023T053522` |\n")
        named, lacking = IR.newest_report_campaigns(tmp)
        self.assertEqual(named, ["review-20261021T173508/campaigns/expL-sleepy/20261023T053522", "review-20261021T173508/campaigns/expS-w6/20261022T161003"])
        self.assertEqual(lacking, ["review-20261021T173508/campaigns/expL-sleepy/20261023T053522"])

        class _Run:
            def log(self, *a):
                pass
        orig = er.RUN
        er.RUN = _Run()
        try:
            with self.assertRaises(er.StageFail) as e:
                er._archive_sources(str(tmp))
            self.assertIn("expL-sleepy", str(e.exception))
            self.assertEqual(len(er._archive_sources(str(tmp), missing_ok=True)), 2)
        finally:
            er.RUN = orig


class D3VmC(unittest.TestCase):
    def test_mosquitto_for_the_gateway_tests(self):
        s = (REPO / "deploy" / "setup" / "vm-c.sh").read_text()
        i = s.index("if ! command -v mosquitto")
        block = s[i:s.index("fi", i)]
        self.assertIn("apt_install mosquitto", block)
        self.assertIn("systemctl disable --now mosquitto", block)
        self.assertLess(block.index("apt_install mosquitto"), block.index("systemctl disable"))


if __name__ == "__main__":
    unittest.main()
