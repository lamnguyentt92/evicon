"""evaluation/campaign_real.py (experiment C, release 073) against an in-process fake S3.

R1 the shipped config loads; NAIVE is M1 without the T4 retry guard, every other cell keeps the guard
R2 S3 LastModified parsing, bucket names
R3 truth from object versions: effects, duplicates, tombstones, the bound, foreign objects, the
   evicon-plant line format that pairs_report.py reads
R4 one local campaign (2 cells, real authorities from bin/, gateways, fault windows): NAIVE on the
   unconditional PUT duplicates effects, NAIVE on the conditional PUT does not. Needs the binaries
   (`make build`; `make verify` does it) and fails, rather than skips, without them.
"""
import csv
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "conformance"))
import campaign  # noqa: E402
import campaign_real as CR  # noqa: E402
import pairs_report  # noqa: E402
from reference.s3_client import S3  # noqa: E402
from tests.fake_s3 import FakeS3  # noqa: E402
from tests.helpers import free_port  # noqa: E402

CREDS = {"MINIO_ROOT_USER": "test-access", "MINIO_ROOT_PASSWORD": "test-secret"}


class ConfigTest(unittest.TestCase):
    def test_r1_shipped_config(self):
        cfg = CR.load_config(REPO / "evaluation" / "campaigns" / "expC_minio.json")
        self.assertEqual(cfg["contracts"], ["MINIO_PLAIN", "MINIO_COND"])
        naive, base = CR.cell_config(cfg, "NAIVE")
        self.assertEqual(base, "M1")
        self.assertFalse(naive["authority"]["retry_guard"])
        self.assertTrue(cfg["authority"]["retry_guard"])  # the original is not modified
        args = campaign.authority_args(naive, "MINIO_PLAIN", base, "evicon-authority", "pump0-1", "http://x", "db", "l", "t", "c")
        self.assertNotIn("-retry-guard", args)
        self.assertEqual(args[args.index("-policy") + 1], "M1")
        for pol in ("EVICON", "M1"):
            c, b = CR.cell_config(cfg, pol)
            self.assertEqual(b, pol)
            self.assertIn("-retry-guard", campaign.authority_args(c, "MINIO_COND", b, "a", "pump0-1", "u", "d", "l", "t", "c"))

    def test_r1_bad_configs(self):
        cfg = json.loads((REPO / "evaluation" / "campaigns" / "expC_minio.json").read_text())
        tmp = Path(tempfile.mkdtemp())
        for mutate, why in ((lambda c: c["real_owner"]["policies"]["NAIVE"].update(base="BLIND"), "not a campaign.py policy"),
                            (lambda c: c["real_owner"]["policies"]["NAIVE"]["authority"].update(hold_cost=0), "not allowed"),
                            (lambda c: c["real_owner"]["contracts"]["MINIO_COND"].update(mode="X"), "MINIO_COND"),
                            (lambda c: c["policies"].append("WHATEVER"), "unknown policy"),
                            (lambda c: c.pop("real_owner"), "real_owner section required"),
                            (lambda c: c["remote"].pop("vm_b_ip"), "vm_b_ip")):
            c = json.loads(json.dumps(cfg))
            mutate(c)
            p = tmp / "c.json"
            p.write_text(json.dumps(c))
            with self.assertRaises(ValueError) as cm:
                CR.load_config(p)
            self.assertIn(why, str(cm.exception))

    def test_r2_times_and_names(self):
        self.assertEqual(CR._iso_ns("2026-10-24T21:03:04.123Z"), 1792875784123000000)
        self.assertEqual(CR._iso_ns("2026-10-24T21:03:04Z"), 1792875784000000000)
        self.assertEqual(CR._iso_ns("2026-10-24T21:03:04.5+00:00"), 1792875784500000000)
        n = CR.bucket_name(Path("/x/run"), campaign.Cell(3, "MINIO_COND", "NAIVE"))
        self.assertRegex(n, r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")
        self.assertNotEqual(n, CR.bucket_name(Path("/x/run"), campaign.Cell(3, "MINIO_PLAIN", "NAIVE")))


class _TB:
    def __init__(self, s3, bucket, wd):
        self.s3, self.bucket = s3, {wd: bucket}

    def b_offset(self):
        return 0.25


class TruthTest(unittest.TestCase):
    def test_r3_truth_from_versions(self):
        with FakeS3(page_size=3) as fake:
            s3 = S3(fake.url, "a", "b")
            s3.create_versioned_bucket("evc-truth")
            t = 1792875784.0
            fake.put_raw("evc-truth", "pump0-1/d0-c1", {"evicon-kind": "effect", "evicon-value": 3}, t=t + 1)
            fake.put_raw("evc-truth", "pump0-1/d0-c2", {"evicon-kind": "effect", "evicon-value": 4}, t=t + 2)
            fake.put_raw("evc-truth", "pump0-1/d0-c2", {"evicon-kind": "effect", "evicon-value": 4}, t=t + 3)  # duplicate
            fake.put_raw("evc-truth", "pump0-1/d0-c3", {"evicon-kind": "tombstone"}, b"", t=t + 4)
            fake.put_raw("evc-truth", "pump0-1/d0-c3-r1", {"evicon-kind": "effect", "evicon-value": 2}, t=t + 5)
            fake.put_raw("evc-truth", "pump1-1/d1-c1", {"evicon-kind": "effect", "evicon-value": 5}, t=t + 6)
            logs = {"d0-c1": campaign.CmdLog("d0-c1", 0, t, 3, ids=["d0-c1"]),
                    "d0-c2": campaign.CmdLog("d0-c2", 0, t, 4, ids=["d0-c2"]),
                    "d0-c3": campaign.CmdLog("d0-c3", 0, t, 2, ids=["d0-c3", "d0-c3-r1"]),
                    "d1-c1": campaign.CmdLog("d1-c1", 1, t, 5, ids=["d1-c1"]),
                    "d1-c2": campaign.CmdLog("d1-c2", 1, t, 1, ids=["d1-c2"])}
            st = campaign.Stack(plant_url="", device_ids=["pump0-1", "pump1-1"], authorities=["http://127.0.0.1:1", "http://127.0.0.1:1"])
            cfg = {"workload": {"bound": 10}, "mode": "remote"}
            cd = Path(tempfile.mkdtemp())
            summ, truth, status, problems = CR.collect_truth(cfg, _TB(s3, "evc-truth", "wd"), st, logs, "wd", cd)
            self.assertEqual(problems, [])
            self.assertEqual(summ["identities_with_duplicate_effects"], 1)
            self.assertEqual(summ["tombstones"], 1)
            self.assertEqual(summ["effect_versions"], 5)
            dev = {d["device"]: d for d in summ["devices"]}
            self.assertEqual((dev["pump0-1"]["total"], dev["pump0-1"]["limit_exceeded"]), (13, True))
            self.assertEqual((dev["pump1-1"]["total"], dev["pump1-1"]["limit_exceeded"]), (5, False))
            self.assertEqual(truth["d0-c2"]["effects"], 2)
            self.assertEqual((truth["d0-c3"]["effects"], truth["d0-c3"]["tombstones"], truth["d0-c3-r1"]["effects"]), (0, 1, 1))
            self.assertEqual(truth["d1-c2"], {"effects": 0, "first_effect_ns": 0, "tombstones": 0})
            self.assertEqual(truth["d0-c1"]["first_effect_ns"], int(round((t + 1) * 1e9)) - 250000000)  # VM-B offset removed
            lines = [json.loads(x) for x in (cd / "raw" / "E" / "plant.jsonl").read_text().splitlines()]
            self.assertEqual(len(lines), 5)
            self.assertEqual(sorted({x["cmd"] for x in lines}), ["d0-c1", "d0-c2", "d0-c3-r1", "d1-c1"])
            self.assertTrue((cd / "raw" / "truth" / "versions.json").is_file())
            # an object no client identity explains makes the cell INVALID; so does a value mismatch
            fake.put_raw("evc-truth", "pump0-1/zz", {"evicon-kind": "effect", "evicon-value": 1})
            fake.put_raw("evc-truth", "pump1-1/d1-c2", {"evicon-kind": "effect", "evicon-value": 4})
            _, _, _, problems = CR.collect_truth(cfg, _TB(s3, "evc-truth", "wd"), st, logs, "wd", Path(tempfile.mkdtemp()))
            self.assertTrue(any("matches no identity" in p for p in problems), problems)
            self.assertTrue(any("carries value 4" in p for p in problems), problems)


class LocalCampaignTest(unittest.TestCase):
    def test_r4_two_cells(self):
        bin_dir = REPO / "bin"
        self.assertTrue((bin_dir / "evicon-authority").exists(), "bin/evicon-authority missing: run `make build` first")
        tmp = Path(tempfile.mkdtemp())
        old = {k: os.environ.get(k) for k in CREDS}
        os.environ.update(CREDS)
        try:
            with FakeS3() as fake:
                cfg = json.loads((REPO / "evaluation" / "campaigns" / "expC_minio.json").read_text())
                cfg.pop("remote")
                cfg.pop("preregistration")
                cfg.update(name="expC-local-test", mode="local", episodes=1, devices=2, duration_s=14, tail_s=3, contracts=["MINIO_PLAIN", "MINIO_COND"],
                           policies=["NAIVE"], ports={"device": free_port_block(), "authority": free_port_block(), "proxy": free_port_block()})
                cfg["workload"].update(rate_per_s=1.5, bound=1000, deadline_s=6, validity_s=8)
                cfg["faults"] = {"windows_per_min": 10, "window_s": [2.5, 3.5], "min_gap_s": 1.0, "linger_s": 1.0, "crash_recovery_s": 0.5,
                                 "mix": {"drop_down": 0.7, "crash_after_effect": 0.3}}
                cfg["real_owner"]["s3_url"] = fake.url
                p = tmp / "cfg.json"
                p.write_text(json.dumps(cfg))
                rc = CR.main(["--config", str(p), "--out", str(tmp / "out"), "--bin-dir", str(bin_dir)])
                self.assertEqual(rc, 0)
            run = next((tmp / "out" / "expC-local-test").glob("2*"))
            with open(run / "runtime_episodes.csv", newline="") as fh:
                rows = {r["contract"]: r for r in csv.DictReader(fh)}
            self.assertEqual(set(rows), {"MINIO_PLAIN", "MINIO_COND"})
            for r in rows.values():
                self.assertEqual(r["cell_status"], "VALID", r["cell_status"])
                self.assertGreater(int(r["clean_fulfilled"]), 0)
            self.assertGreater(int(rows["MINIO_PLAIN"]["duplicate_effects"]), 0, rows["MINIO_PLAIN"])  # blind retry, real duplicates
            self.assertEqual(int(rows["MINIO_COND"]["duplicate_effects"]), 0, rows["MINIO_COND"])     # If-None-Match deduplicates
            cell = run / "cells" / "ep0" / "MINIO_COND" / "NAIVE"
            self.assertTrue((cell / "raw" / "truth" / "truth.json").is_file())
            got = pairs_report.confirmed(cell, 6.0)
            self.assertEqual(got[0], int(rows["MINIO_COND"]["clean_fulfilled"]))
            text = "".join(f.read_text(errors="replace") for f in run.rglob("*") if f.is_file() and f.suffix in (".log", ".jsonl", ".json", ".txt"))
            self.assertNotIn("test-secret", text)  # credentials never reach a file
            self.assertTrue(re.search(r'"gateway_sha256": "[0-9a-f]{64}"', (run / "manifest.json").read_text()))
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def free_port_block(n=8):
    """A base port whose next n ports are free (best effort)."""
    import socket
    for _ in range(50):
        base = free_port()
        if base + n > 65000:
            continue
        ok = True
        for p in range(base, base + n):
            with socket.socket() as s:
                try:
                    s.bind(("127.0.0.1", p))
                except OSError:
                    ok = False
                    break
        if ok:
            return base
    raise RuntimeError("no free port block")


if __name__ == "__main__":
    unittest.main()
