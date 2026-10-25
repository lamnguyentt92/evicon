"""Release 067: a preregistered confirmatory campaign runs only on its frozen plan
(evicon_review._check_preregistration, scripts/freeze_prereg.py), and the
committed confirmatory configs name a frozen plan whose files are unchanged.
Release 071: experiment B2 (expB2_cadence.json, PREREG_expB2_cadence_071.md) is frozen
too, with cadence_report.py in its frozen-extra block, and the 067 plan is unchanged.
Release 072: the W, S, L and negative-control plans are frozen with pairs_report.py (and
cadence_report.py, which it imports); their analysis spec parses; each config differs from
expA_confirm_k0 only where its plan says; the 067 and 071 plans are byte-identical.
Release 073: experiment C (expC_minio.json, PREREG_expC_073.md) is frozen with its runner, the
gateway and the measured MinIO contracts (byte-identical to the 070 measurement); the 072 plans
are byte-identical; the driver fails a real-owner campaign on any violation outside the planned
negative control.
Release 074: experiment C2 (expC2_zephyr.json, PREREG_expC2_074.md) is frozen with its runner, the MQTT
gateway, the firmware sources, the probe configs and code that measure the contracts, and the simulator's
predicted contracts; the 073 plan is byte-identical; the C2 config replays the regime and seed of
expW_queue_w6 (common random numbers with the W and S runs) and differs from it only where its plan says.
Release 075: experiment C3 (expC3_ablation.json, PREREG_expC3_075.md) is frozen with the C3 runner and gateway
extensions and every file the C2 plan froze; the C2 plan is byte-identical; the C3 config is C2's except where its
plan says and replays the C2, W and S schedules.
Release 077: the review experiments (PREREG_review_077.md, nine configs) are frozen with every file the C3 plan
froze; the C3 plan is byte-identical; each config differs from the run it replays only where the plan says."""
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import evicon_review as er  # noqa: E402


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


class PreregTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.old = er.REPO
        er.REPO = self.tmp
        (self.tmp / "a.py").write_text("x = 1\n")
        self.plan = self.tmp / "plan.md"
        self.plan.write_text(f"# plan\n\n```frozen-files\n{sha(self.tmp / 'a.py')}  a.py\n```\n")

    def tearDown(self):
        er.REPO = self.old

    def test_frozen_plan_passes(self):
        msg = er._check_preregistration({"path": "plan.md", "sha256": sha(self.plan)})
        self.assertIn("1 frozen files unchanged", msg)

    def test_edited_plan_is_refused(self):
        pre = {"path": "plan.md", "sha256": sha(self.plan)}
        self.plan.write_text(self.plan.read_text() + "\nlater edit\n")
        with self.assertRaises(er.StageFail):
            er._check_preregistration(pre)

    def test_changed_analysis_file_is_refused(self):
        pre = {"path": "plan.md", "sha256": sha(self.plan)}
        (self.tmp / "a.py").write_text("x = 2\n")
        with self.assertRaisesRegex(er.StageFail, "a.py"):
            er._check_preregistration(pre)

    def test_committed_confirmatory_configs_are_frozen(self):
        er.REPO = self.old
        for name in ("expA_confirm_k0.json", "expA_confirm_k2.json", "expB2_cadence.json"):
            cfg = json.loads((REPO / "evaluation" / "campaigns" / name).read_text())
            self.assertIn("verified", er._check_preregistration(cfg["preregistration"]), name)

    def test_committed_plan_is_frozen(self):
        # --check writes nothing; it fails when freezing would change the plan or a config
        for plan in ("evaluation/prereg/PREREG_expA_confirm_067.md", "evaluation/prereg/PREREG_expB2_cadence_071.md"):
            out = subprocess.run([sys.executable, str(REPO / "scripts" / "freeze_prereg.py"), "--check", plan],
                                 capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertIn("check: frozen", out.stdout)

    def test_067_plan_unchanged_and_b2_freezes_its_analysis(self):
        # the 067 plan's SHA-256 is the one the 067 configs and runs recorded
        self.assertEqual(sha(REPO / "evaluation" / "prereg" / "PREREG_expA_confirm_067.md"),
                         "d3ff30681d44146272080effd73ae78978c7587fc250d17ac6b7e446544ea79e")
        text = (REPO / "evaluation" / "prereg" / "PREREG_expB2_cadence_071.md").read_text()
        self.assertIn(f"{sha(REPO / 'evaluation' / 'cadence_report.py')}  evaluation/cadence_report.py", text)
        self.assertIn(f"{sha(REPO / 'evaluation' / 'campaign.py')}  evaluation/campaign.py", text)
        b2 = json.loads((REPO / "evaluation" / "campaigns" / "expB2_cadence.json").read_text())
        k0 = json.loads((REPO / "evaluation" / "campaigns" / "expA_confirm_k0.json").read_text())
        # same regime and seed as the K0 confirmatory config, so episodes 0-15 replay its schedules
        for k in ("seed", "devices", "duration_s", "workload", "faults"):
            self.assertEqual(b2[k], k0[k], k)
        self.assertEqual((b2["contracts"], b2["policies"], b2["episodes"]), (["K0F"], ["EVICON", "RES_PERIODIC"], 32))
        self.assertEqual((b2["authority"]["resolve_sweep"], b2["authority"]["periodic"]), ("10s", "10s"))
        self.assertEqual({k: v for k, v in b2["authority"].items() if k != "resolve_sweep"},
                         {k: v for k, v in k0["authority"].items() if k != "resolve_sweep"})


PLANS_072 = {
    "evaluation/prereg/PREREG_expW_072.md": ["expW_queue_w6.json", "expW_queue_winf.json", "expW_direct_w6.json", "expW_direct_winf.json"],
    "evaluation/prereg/PREREG_expS_072.md": ["expS_w6.json", "expS_winf.json"],
    "evaluation/prereg/PLAN_expL_072.md": ["expL_sleepy.json"],
    "evaluation/prereg/NEGCTL_072.md": ["negctl_072.json"],
}


class Release072(unittest.TestCase):
    def test_earlier_plans_unchanged(self):
        self.assertEqual(sha(REPO / "evaluation" / "prereg" / "PREREG_expA_confirm_067.md"),
                         "d3ff30681d44146272080effd73ae78978c7587fc250d17ac6b7e446544ea79e")
        self.assertEqual(sha(REPO / "evaluation" / "prereg" / "PREREG_expB2_cadence_071.md"),
                         "1881e7fe6afc0ca086044a5aa80c27d0ddc8486abcffcbf47a4b0dc22d68b752")

    def test_072_plans_frozen(self):
        import re
        for plan, configs in PLANS_072.items():
            out = subprocess.run([sys.executable, str(REPO / "scripts" / "freeze_prereg.py"), "--check", plan], capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            text = (REPO / plan).read_text()
            for f in ("evaluation/pairs_report.py", "evaluation/cadence_report.py", "evaluation/campaign.py"):
                self.assertIn(f"{sha(REPO / f)}  {f}", text, (plan, f))
            spec = json.loads(re.search(r"```pairs-spec\n(.*?)```", text, re.S).group(1))
            names = {json.loads((REPO / "evaluation" / "campaigns" / c).read_text())["name"] for c in configs}
            own = {v["campaign"] for v in spec["runs"].values() if not v.get("reference")}
            self.assertEqual(own, names, plan)
            for c in configs:
                cfg = json.loads((REPO / "evaluation" / "campaigns" / c).read_text())
                self.assertEqual(cfg["preregistration"]["path"], plan)
                self.assertIn("verified", er._check_preregistration(cfg["preregistration"]), c)

    def test_072_configs_differ_only_where_planned(self):
        k0 = json.loads((REPO / "evaluation" / "campaigns" / "expA_confirm_k0.json").read_text())
        expect = {  # config: (max_outstanding, device_args, offline_s, seed)
            "expW_queue_w6.json": (6, None, None, 20261017), "expW_queue_winf.json": (1000, None, None, 20261017),
            "expW_direct_w6.json": (6, ["-offline-mode", "drop"], None, 20261017),
            "expW_direct_winf.json": (1000, ["-offline-mode", "drop"], None, 20261017),
            "expS_w6.json": (6, None, None, 20261017), "expS_winf.json": (1000, None, None, 20261017),
            "expL_sleepy.json": (6, None, [60.0, 180.0], 20261024), "negctl_072.json": (6, None, None, 20261017)}
        for name, (w, dev, off, seed) in expect.items():
            cfg = json.loads((REPO / "evaluation" / "campaigns" / name).read_text())
            for k in ("devices", "duration_s", "tail_s", "workload", "mode", "remote", "regime"):
                self.assertEqual(cfg[k], k0[k], (name, k))
            self.assertEqual(cfg["seed"], seed, name)
            self.assertEqual(cfg["authority"]["max_outstanding"], w, name)
            self.assertEqual({k: v for k, v in cfg["authority"].items() if k != "max_outstanding"},
                             {k: v for k, v in k0["authority"].items() if k != "max_outstanding"}, name)
            self.assertEqual(cfg["faults"].get("device_args"), dev, name)
            self.assertEqual(cfg["faults"]["offline_s"], off or k0["faults"]["offline_s"], name)
            self.assertEqual({k: v for k, v in cfg["faults"].items() if k not in ("device_args", "offline_s")},
                             {k: v for k, v in k0["faults"].items() if k != "offline_s"}, name)
        neg = json.loads((REPO / "evaluation" / "campaigns" / "negctl_072.json").read_text())
        self.assertTrue(all(c.startswith("NEG_") for c in neg["contracts"]))  # the driver treats it as a negative control

    def test_incremental_bundle(self):
        import argparse
        import zipfile
        tmp = Path(tempfile.mkdtemp())
        old = er.REPO
        er.REPO = tmp
        try:
            run = tmp / "results" / "review-20261022T000000"
            (run / "campaigns" / "x").mkdir(parents=True)
            (run / "campaigns" / "x" / "a.csv").write_text("a\n")
            (run / "REVIEW_LOG.md").write_text("log\n")
            a = argparse.Namespace(incremental=False, no_remote=True)
            self.assertEqual(er.make_bundle(a), 0)
            full = zipfile.ZipFile(tmp / f"EVICON_{er.RELEASE}_result.zip").namelist()
            self.assertTrue(any(n.endswith("campaigns/x/a.csv") for n in full))
            (run / "REVIEW_LOG.md").write_text("log, more\n")
            (run / "campaigns" / "y.csv").write_text("y\n")
            a.incremental = True
            self.assertEqual(er.make_bundle(a), 0)
            inc = [z for z in tmp.glob(f"EVICON_{er.RELEASE}_result_*.zip")]
            self.assertEqual(len(inc), 1)
            names = zipfile.ZipFile(inc[0]).namelist()
            self.assertFalse(any(n.endswith("campaigns/x/a.csv") for n in names))  # unchanged: already sent
            self.assertTrue(any(n.endswith("REVIEW_LOG.md") for n in names))
            self.assertTrue(any(n.endswith("campaigns/y.csv") for n in names))
            self.assertFalse(any(n.endswith(".bundle_index.json") for n in names))
        finally:
            er.REPO = old

    def test_negative_control_rule_in_driver(self):
        import inspect
        src = inspect.getsource(er._analyze_runtime)
        self.assertIn("negative_control", src)
        self.assertIn("NO safety violation", src)


SHA_072_PLANS = {
    "evaluation/prereg/PREREG_expW_072.md": "fa408a17",
    "evaluation/prereg/PREREG_expS_072.md": "b701c9a2",
    "evaluation/prereg/PLAN_expL_072.md": "7d6bcdd7",
    "evaluation/prereg/NEGCTL_072.md": "a968491c",
}


class Release073(unittest.TestCase):
    def test_072_plans_byte_identical(self):
        for plan, prefix in SHA_072_PLANS.items():
            self.assertTrue(sha(REPO / plan).startswith(prefix), plan)

    def test_expC_plan_frozen(self):
        import re
        plan = "evaluation/prereg/PREREG_expC_073.md"
        out = subprocess.run([sys.executable, str(REPO / "scripts" / "freeze_prereg.py"), "--check", plan], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        text = (REPO / plan).read_text()
        for f in ("evaluation/pairs_report.py", "evaluation/campaign.py", "evaluation/campaign_real.py",
                  "conformance/reference/s3_owner_gateway.py", "conformance/reference/s3_client.py",
                  "experiments/owners/minio/meas-minio-cond-070.json", "experiments/owners/minio/meas-minio-plain-070.json"):
            self.assertIn(f"{sha(REPO / f)}  {f}", text, f)
        spec = json.loads(re.search(r"```pairs-spec\n(.*?)```", text, re.S).group(1))
        cfg = json.loads((REPO / "evaluation" / "campaigns" / "expC_minio.json").read_text())
        self.assertEqual({v["campaign"] for v in spec["runs"].values()}, {cfg["name"]})
        self.assertEqual(cfg["preregistration"]["path"], plan)
        self.assertIn("verified", er._check_preregistration(cfg["preregistration"]))
        ids = {h["id"] for h in spec["hypotheses"]}
        self.assertEqual(ids, {"C1", "C2", "C3", "C4", "C-S1", "C-S2", "C-S3", "C-S4"})
        self.assertNotIn("safety", spec)  # NAIVE on PLAIN is the planned violation: safety is checked per group

    def test_measured_contracts_are_the_070_ones(self):
        d = REPO / "experiments" / "owners" / "minio"
        self.assertEqual(sha(d / "meas-minio-cond-070.json"), "dbe4797d7c73cf99049931ec1c5b97b1a657bd7d407b34e5f02b9049986017eb")
        self.assertEqual(sha(d / "meas-minio-plain-070.json"), "5215bebd11b54436b37063c8bcfd925327da389f9c75daf541bb68b0587d3d59")

    def test_expC_config_differs_only_where_planned(self):
        k0 = json.loads((REPO / "evaluation" / "campaigns" / "expA_confirm_k0.json").read_text())
        c = json.loads((REPO / "evaluation" / "campaigns" / "expC_minio.json").read_text())
        for k in ("devices", "duration_s", "tail_s", "workload", "faults", "mode"):
            self.assertEqual(c[k], k0[k], k)
        self.assertEqual({k: v for k, v in c["authority"].items() if k != "max_outstanding"},
                         {k: v for k, v in k0["authority"].items() if k != "max_outstanding"})
        self.assertEqual((c["authority"]["max_outstanding"], c["seed"], c["episodes"]), (6, 20261025, 8))
        self.assertEqual((c["contracts"], c["policies"]), (["MINIO_PLAIN", "MINIO_COND"], ["EVICON", "M1", "NAIVE"]))
        self.assertEqual(c["real_owner"]["policies"], {"NAIVE": {"base": "M1", "authority": {"retry_guard": False}}})
        self.assertNotIn("activation_gate", c)
        self.assertEqual(er._expected_unsafe(c), {("MINIO_PLAIN", "NAIVE")})

    def test_real_owner_violation_rule_in_driver(self):
        import types
        tmp = Path(tempfile.mkdtemp())
        rep = {"groups_with_safety_violations": [["TESTBED", "expC-minio", "MINIO_PLAIN", "NAIVE"]], "late_effects_total": 0}
        old_sh, old_problem = er.sh, er.RUN

        def fake_sh(args, **kw):
            (tmp / "report.json").write_text(json.dumps(rep))
            return 0, ""
        er.sh = fake_sh
        er.RUN = types.SimpleNamespace(problem=lambda *a, **k: None, log=lambda *a, **k: None)
        try:
            er._analyze_runtime([tmp / "x.csv"], tmp, ["M1"], expected_unsafe={("MINIO_PLAIN", "NAIVE")})  # planned: passes
            rep["groups_with_safety_violations"].append(["TESTBED", "expC-minio", "MINIO_COND", "EVICON"])
            with self.assertRaisesRegex(er.StageFail, "outside the planned negative control"):
                er._analyze_runtime([tmp / "x.csv"], tmp, ["M1"], expected_unsafe={("MINIO_PLAIN", "NAIVE")})
        finally:
            er.sh, er.RUN = old_sh, old_problem


class Release074(unittest.TestCase):
    PLAN = "evaluation/prereg/PREREG_expC2_074.md"

    def test_073_plan_byte_identical(self):
        self.assertEqual(sha(REPO / "evaluation" / "prereg" / "PREREG_expC_073.md"),
                         "ac16f2387cebc0f2e45594d0bbc1ef43c2036aeeba61b326ededce48080e6623")
        for plan, prefix in SHA_072_PLANS.items():
            self.assertTrue(sha(REPO / plan).startswith(prefix), plan)

    def test_expC2_plan_frozen(self):
        import re
        out = subprocess.run([sys.executable, str(REPO / "scripts" / "freeze_prereg.py"), "--check", self.PLAN], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        text = (REPO / self.PLAN).read_text()
        for f in ("evaluation/pairs_report.py", "evaluation/campaign.py", "evaluation/campaign_real.py", "evaluation/campaign_zephyr.py",
                  "conformance/reference/mqtt_owner_gateway.py", "conformance/reference/s3_owner_gateway.py",
                  "conformance/configs/zephyr-mqtt.json", "conformance/configs/zephyr-mqtt-stop.json", "conformance/configs/zephyr-mqtt-plain.json",
                  "conformance/evicon_probe/probes.py", "conformance/evicon_probe/rubric.py", "conformance/evicon_probe/adapters/mqtt5.py",
                  "firmware/zephyr-cutoff/src/main.c", "firmware/zephyr-cutoff/src/evc_core.c", "firmware/zephyr-cutoff/src/evc_proto.c",
                  "experiments/owners/zephyr/pred-zephyr-plain-074.json", "experiments/owners/zephyr/pred-zephyr-cutoff-074.json",
                  "experiments/owners/zephyr/pred-zephyr-stop-074.json"):
            self.assertIn(f"{sha(REPO / f)}  {f}", text, f)
        self.assertNotIn("PRERELEASE_CHECKS_PLACEHOLDER", text)
        spec = json.loads(re.search(r"```pairs-spec\n(.*?)```", text, re.S).group(1))
        cfg = json.loads((REPO / "evaluation" / "campaigns" / "expC2_zephyr.json").read_text())
        self.assertEqual(spec["runs"]["C2"], {"campaign": cfg["name"]})
        self.assertEqual({k: v["campaign"] for k, v in spec["runs"].items() if v.get("reference")}, {"W": "expW-queue-w6", "S": "expS-w6"})
        self.assertEqual(cfg["preregistration"]["path"], self.PLAN)
        self.assertIn("verified", er._check_preregistration(cfg["preregistration"]))
        ids = {h["id"] for h in spec["hypotheses"]}
        self.assertEqual(ids, {"C2-1", "C2-2", "C2-3", "C2-4", "C2-5", "C2-S1", "C2-S2", "C2-S3", "C2-S4", "C2-S5"})
        self.assertEqual({h["id"] for h in spec["hypotheses"] if h.get("family") == "primary"}, {"C2-3", "C2-4", "C2-5"})
        self.assertNotIn("safety", spec)  # NAIVE on PLAIN is the planned violation: safety is checked per group
        # the cross-run estimands pair only replayed episodes (same seed): their reference runs are named
        self.assertEqual({(e["from_run"], e["from"], e["to"]) for e in spec["estimands"] if "from_run" in e},
                         {("W", "K0", "ZEPHYR_PLAIN"), ("W", "K0F", "ZEPHYR_CUTOFF"), ("S", "K0S", "ZEPHYR_STOP")})

    def test_expC2_config_differs_only_where_planned(self):
        w = json.loads((REPO / "evaluation" / "campaigns" / "expW_queue_w6.json").read_text())
        c = json.loads((REPO / "evaluation" / "campaigns" / "expC2_zephyr.json").read_text())
        for k in ("devices", "duration_s", "tail_s", "workload", "faults", "authority", "seed", "mode", "schema_version"):
            self.assertEqual(c[k], w[k], k)
        self.assertEqual({k: v for k, v in c["remote"].items() if k not in ("vm_b", "vm_b_ip")}, w["remote"])
        self.assertEqual((c["name"], c["regime"], c["episodes"]), ("expC2-zephyr", "expC2-zephyr", 8))
        self.assertEqual((c["contracts"], c["policies"]), (["ZEPHYR_PLAIN", "ZEPHYR_CUTOFF", "ZEPHYR_STOP"], ["EVICON", "M1", "NAIVE"]))
        self.assertEqual(c["exclude_cells"], [["ZEPHYR_STOP", "M1"], ["ZEPHYR_STOP", "NAIVE"]])
        ro = c["real_owner"]
        self.assertEqual(ro["system"], "zephyr")
        self.assertEqual({n: x["mode"] for n, x in ro["contracts"].items()}, {"ZEPHYR_PLAIN": "PLAIN", "ZEPHYR_CUTOFF": "CUTOFF", "ZEPHYR_STOP": "STOP"})
        self.assertTrue(all(x["contract_file"] == "MEASURED_IN_THIS_REVIEW_RUN" for x in ro["contracts"].values()))
        self.assertEqual(ro["policies"], {"NAIVE": {"base": "M1", "authority": {"retry_guard": False}}})
        self.assertEqual((ro["qos_cmd"], ro["plant_tcp_port"]), (1, 18093))
        self.assertNotIn("activation_gate", c)
        self.assertEqual(er._expected_unsafe(c), {("ZEPHYR_PLAIN", "NAIVE")})
        self.assertEqual(er._cells(c), 56)
        # the gate expects exactly the contracts the plan names (section 2)
        self.assertEqual({m: caps for m, (_, _, caps) in er.ZEPHYR_MODE_STACK.items()},
                         {"PLAIN": set(), "CUTOFF": {"D_h", "Q_A", "F"}, "STOP": {"D_h", "Q_A", "Q_S", "F"}})

    def test_expC2_replays_the_W_and_S_schedules(self):
        sys.path.insert(0, str(REPO / "evaluation"))
        import campaign as C
        w = json.loads((REPO / "evaluation" / "campaigns" / "expW_queue_w6.json").read_text())
        s = json.loads((REPO / "evaluation" / "campaigns" / "expS_w6.json").read_text())
        c = json.loads((REPO / "evaluation" / "campaigns" / "expC2_zephyr.json").read_text())
        for ep in range(c["episodes"]):
            self.assertEqual(C.schedule(c, ep), C.schedule(w, ep), ep)
            self.assertEqual(C.schedule(c, ep), C.schedule(s, ep), ep)


class Release075(unittest.TestCase):
    """Release 075: experiment C3 (expC3_ablation.json, PREREG_expC3_075.md) is frozen with the C3 runner and gateway
    extensions, every file the C2 plan froze (same SHA-256), the restricted predicted contracts and the simulator
    prediction; the C2 plan is byte-identical and still verifies; the C3 config is C2's except where its plan says and
    replays the schedules of C2, expW-queue-w6, expW-direct-w6 and expS-w6."""
    PLAN = "evaluation/prereg/PREREG_expC3_075.md"
    C2_PLAN = "evaluation/prereg/PREREG_expC2_074.md"

    def test_074_plan_byte_identical_and_verified(self):
        self.assertEqual(sha(REPO / self.C2_PLAN), "7f4abfd6f75e34ad5272a58d5a3a1c96b3bfe2ebf2cd313d490eb41dd59bd48a")
        out = subprocess.run([sys.executable, str(REPO / "scripts" / "freeze_prereg.py"), "--check", self.C2_PLAN], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_expC3_plan_frozen(self):
        import re
        out = subprocess.run([sys.executable, str(REPO / "scripts" / "freeze_prereg.py"), "--check", self.PLAN], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        text = (REPO / self.PLAN).read_text()
        self.assertNotIn("PRERELEASE_CHECKS_PLACEHOLDER", text)
        c2_frozen = re.search(r"```frozen-files\n(.*?)```", (REPO / self.C2_PLAN).read_text(), re.S).group(1).strip().splitlines()
        for line in c2_frozen:  # every file C2 froze, with the same SHA-256
            self.assertIn(line, text, line)
        for f in ("evaluation/campaign_zephyr_ext.py", "conformance/reference/mqtt_owner_gateway_ext.py", "evaluation/sim_expC3.py",
                  "evaluation/sim_predictions/expC3_075_summary.csv", "evaluation/sim_predictions/expC3_075_power.csv",
                  "evaluation/power_expC3_testbed.py", "experiments/owners/zephyr/pred-zephyr-qa-075.json",
                  "experiments/owners/zephyr/pred-zephyr-f-075.json", "experiments/owners/zephyr/pred-zephyr-qs-075.json"):
            self.assertIn(f"{sha(REPO / f)}  {f}", text, f)
        spec = json.loads(re.search(r"```pairs-spec\n(.*?)```", text, re.S).group(1))
        cfg = json.loads((REPO / "evaluation" / "campaigns" / "expC3_ablation.json").read_text())
        self.assertEqual(spec["runs"]["C3"], {"campaign": cfg["name"]})
        self.assertEqual({k: v["campaign"] for k, v in spec["runs"].items() if v.get("reference")},
                         {"C2": "expC2-zephyr", "Wq": "expW-queue-w6", "Wd": "expW-direct-w6", "S": "expS-w6"})
        self.assertEqual(cfg["preregistration"]["path"], self.PLAN)
        self.assertIn("verified", er._check_preregistration(cfg["preregistration"]))
        hyp = {h["id"]: h for h in spec["hypotheses"]}
        self.assertEqual(set(hyp), {f"C3-{i}" for i in range(1, 7)})
        self.assertTrue(all(h["family"] == "primary" and h["policy"] == "EVICON" and h["metric"] == "clean_pp" for h in hyp.values()))
        self.assertEqual(spec["safety"], {"id": "C3-S", "runs": ["C3"], "exempt_contracts": []})
        # the decision rules are the ones the simulator's power was computed for (sim_expC3.HYPOTHESES)
        sys.path.insert(0, str(REPO / "evaluation"))
        import sim_expC3
        self.assertEqual({(h[0], h[2], h[3], h[5], float(h[6])) for h in sim_expC3.HYPOTHESES},
                         {(k, h["from"], h["to"], h["test"], h["margin"]) for k, h in hyp.items()})
        contracts = set(cfg["contracts"])
        for h in hyp.values():
            self.assertTrue({h["from"], h["to"]} <= contracts, h["id"])
        cross = {(e["from_run"], e["from"], e["to"]) for e in spec["estimands"] if "from_run" in e}
        self.assertEqual(cross, {("C2", "ZEPHYR_PLAIN", "ZEPHYR_PLAIN_DIRECT"), ("C2", "ZEPHYR_PLAIN", "ZEPHYR_QA_QUEUE"),
                                 ("C2", "ZEPHYR_CUTOFF", "ZEPHYR_QA_QUEUE"), ("C2", "ZEPHYR_CUTOFF", "ZEPHYR_F_QUEUE"),
                                 ("C2", "ZEPHYR_STOP", "ZEPHYR_QS_QUEUE"), ("Wd", "K0", "ZEPHYR_PLAIN_DIRECT"),
                                 ("Wd", "K0F", "ZEPHYR_F_DIRECT"), ("Wq", "K0F", "ZEPHYR_F_QUEUE"), ("S", "K0S", "ZEPHYR_QS_QUEUE")})
        self.assertTrue(all(e["to"] in contracts for e in spec["estimands"]))

    def test_expC3_config_differs_only_where_planned(self):
        c2 = json.loads((REPO / "evaluation" / "campaigns" / "expC2_zephyr.json").read_text())
        c = json.loads((REPO / "evaluation" / "campaigns" / "expC3_ablation.json").read_text())
        for k in ("devices", "duration_s", "tail_s", "workload", "faults", "authority", "seed", "mode", "schema_version", "remote", "episodes"):
            self.assertEqual(c[k], c2[k], k)
        self.assertEqual((c["name"], c["regime"], c["policies"]), ("expC3-ablation", "expC3-zephyr", ["EVICON"]))
        self.assertNotIn("exclude_cells", c)
        self.assertNotIn("activation_gate", c)
        ro, ro2 = c["real_owner"], c2["real_owner"]
        self.assertEqual({k: v for k, v in ro.items() if k not in ("contracts", "policies")},
                         {k: v for k, v in ro2.items() if k not in ("contracts", "policies")})
        self.assertNotIn("policies", ro)
        want = {"ZEPHYR_PLAIN_DIRECT": ("PLAIN", "direct", None), "ZEPHYR_QA_DIRECT": ("CUTOFF", "direct", ["Q_A"]),
                "ZEPHYR_F_DIRECT": ("CUTOFF", "direct", ["F"]), "ZEPHYR_QS_DIRECT": ("STOP", "direct", ["Q_S"]),
                "ZEPHYR_QA_QUEUE": ("CUTOFF", "queue", ["Q_A"]), "ZEPHYR_F_QUEUE": ("CUTOFF", "queue", ["F"]),
                "ZEPHYR_QS_QUEUE": ("STOP", "queue", ["Q_S"])}
        self.assertEqual(c["contracts"], list(want))
        self.assertEqual({n: (x["mode"], x["channel"], x.get("restrict_to")) for n, x in ro["contracts"].items()}, want)
        self.assertTrue(all(x["contract_file"] == "MEASURED_IN_THIS_REVIEW_RUN" for x in ro["contracts"].values()))
        self.assertEqual(er._expected_unsafe(c), set())
        self.assertEqual(er._cells(c), 56)

    def test_expC3_replays_the_C2_W_and_S_schedules(self):
        sys.path.insert(0, str(REPO / "evaluation"))
        import campaign as C
        c = json.loads((REPO / "evaluation" / "campaigns" / "expC3_ablation.json").read_text())
        for name in ("expC2_zephyr", "expW_queue_w6", "expW_direct_w6", "expS_w6"):
            o = json.loads((REPO / "evaluation" / "campaigns" / f"{name}.json").read_text())
            for ep in range(c["episodes"]):
                self.assertEqual(C.schedule(c, ep), C.schedule(o, ep), (name, ep))



def _cfg(name):
    return json.loads((REPO / "evaluation" / "campaigns" / f"{name}.json").read_text())


def _diff(a, b, path=""):
    """Paths whose values differ between two configs (nested dicts compared key by key)."""
    out = set()
    for k in set(a) | set(b):
        if isinstance(a.get(k), dict) and isinstance(b.get(k), dict):
            out |= _diff(a[k], b[k], f"{path}{k}.")
        elif a.get(k) != b.get(k):
            out.add(f"{path}{k}")
    return out


class Release077(unittest.TestCase):
    """Release 077: the review experiments (PREREG_review_077.md: fourteen configs with hypotheses, the scale configs)
    are frozen with the analyses, runners, gateways, firmware sources and variants, checks, simulator predictions and
    every file the C3 plan froze (same SHA-256); the C3 plan is byte-identical and still verifies; each config differs
    from the run it replays only where its plan says, and replays its schedules."""
    PLAN = "evaluation/prereg/PREREG_review_077.md"
    C3_PLAN = "evaluation/prereg/PREREG_expC3_075.md"
    CONFIGS = {"Rm": "expR_minio_winf", "Rz": "expR_zephyr_winf", "O": "expO_noorphan", "K2": "expK2_w6", "V": "expV_mild",
               "RTq": "expRT_queue", "RTd": "expRT_direct", "RRq": "expRR_queue", "RRd": "expRR_direct", "L2": "expL2_sleepy",
               "Q": "expQ_zephyr", "WAN": "expWAN_s6", "C16": "expC16_minio", "TB": "expTB_thingsboard"}
    SCALE = ("rq4_scale_d8", "rq4_scale_d16", "rq4_scale_d32")

    def test_075_plan_byte_identical_and_verified(self):
        self.assertEqual(sha(REPO / self.C3_PLAN), "781aa7c62d802073f963663ab204a979fbc7f3089d9268c7aa0e5468c45e1545")
        out = subprocess.run([sys.executable, str(REPO / "scripts" / "freeze_prereg.py"), "--check", self.C3_PLAN], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_review077_plan_frozen(self):
        import re
        out = subprocess.run([sys.executable, str(REPO / "scripts" / "freeze_prereg.py"), "--check", self.PLAN], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        text = (REPO / self.PLAN).read_text()
        self.assertNotIn("FILLED_BY_FREEZE", text)
        self.assertNotIn("To be written before freezing", text)
        c3 = re.search(r"```frozen-files\n(.*?)```", (REPO / self.C3_PLAN).read_text(), re.S).group(1).strip().splitlines()
        for line in c3:  # every file C3 froze, with the same SHA-256
            self.assertIn(line, text, line)
        for f in ("evaluation/policy_compare.py", "evaluation/latency_control.py", "evaluation/campaign_ext077.py", "evaluation/sim_review077.py",
                  "evaluation/sim_predictions/review077_summary.csv", "evaluation/sim_predictions/review077_power.csv",
                  "evaluation/sim_sensitivity077.py", "evaluation/sim_predictions/sensitivity077_summary.csv",
                  "evaluation/power_review077_testbed.py", "experiments/owners/minio/meas-minio-plain-070.json",
                  "experiments/owners/minio/meas-minio-cond-070.json", "evaluation/late_attribution.py",
                  "evaluation/campaign_zephyr_ext077.py", "evaluation/campaign_tb.py", "conformance/reference/mqtt_owner_gateway_ext077.py",
                  "conformance/reference/tb_owner_gateway.py", "conformance/reference/mqtt_device.py",
                  "conformance/reference/qos_dup_check.py", "conformance/reference/zephyr_auth_check.py",
                  "conformance/configs/thingsboard-rpc-r0.json", "conformance/configs/zephyr-mqtt-mcu.json",
                  "firmware/zephyr-mcu/CMakeLists.txt", "firmware/zephyr-mcu/prj.conf", "firmware/zephyr-mcu/boards/mps2_an385.conf",
                  "firmware/zephyr-mcu/boards/nrf52840dk_nrf52840.conf", "firmware/zephyr-auth/src/main_auth.c",
                  "firmware/zephyr-auth/src/evc_auth.c", "firmware/zephyr-auth/src/evc_auth.h", "evaluation/sim_theta077.py",
                  "evaluation/sim_predictions/theta077_summary.csv"):
            self.assertIn(f"{sha(REPO / f)}  {f}", text, f)
        for name in self.SCALE:  # exploratory configs of the plan: preregistered, no hypothesis
            cfg = _cfg(name)
            self.assertEqual(cfg["preregistration"]["path"], self.PLAN, name)
            self.assertIn("verified", er._check_preregistration(cfg["preregistration"]), name)
        spec = json.loads(re.search(r"```pairs-spec\n(.*?)```", text, re.S).group(1))
        for key, name in self.CONFIGS.items():
            cfg = _cfg(name)
            self.assertEqual(spec["runs"][key], {"campaign": cfg["name"]}, key)
            self.assertEqual(cfg["preregistration"]["path"], self.PLAN, name)
            self.assertIn("verified", er._check_preregistration(cfg["preregistration"]), name)
        self.assertEqual({k: v["campaign"] for k, v in spec["runs"].items() if v.get("reference")},
                         {"C": "expC-minio", "C2": "expC2-zephyr", "C3": "expC3-ablation", "Wqi": "expW-queue-winf", "Wdi": "expW-direct-winf",
                          "S": "expS-w6", "A2": "expA-confirm-k2", "L": "expL-sleepy"})
        hyp = {h["id"]: h for h in spec["hypotheses"]}
        fams = {}
        for h in hyp.values():
            fams.setdefault(h.get("family"), set()).add(h["id"])
        self.assertEqual(fams, {"unbounded": {"U-1", "U-2", "U-3", "U-4"}, "orphan": {"O-1", "O-2"}, "k2": {"K-1", "K-2"},
                                "regime": {"V-1", "V-2"}, "wan": {"WAN-1", "WAN-2"},
                                None: {"RT-1", "RT-2", "RT-3", "RR-1", "RR-2", "RR-S1", "RR-S2", "L2-2", "Q-1", "Q-2", "Q-3", "TB-1", "TB-S1",
                                       "TB-S2"}})
        for h in hyp.values():
            cfg = _cfg(self.CONFIGS[h["run"]])
            if "from" in h:
                self.assertTrue({h["from"], h["to"]} <= set(cfg["contracts"]) and h["policy"] in cfg["policies"], h["id"])
            else:
                self.assertTrue(h["contract"] in cfg["contracts"] and h["policy"] in cfg["policies"], h["id"])
        self.assertEqual(spec["safety"], {"id": "R-S", "runs": ["Rm", "Rz", "O", "K2", "V", "L2", "WAN", "C16"], "exempt_contracts": []})
        for key in spec["safety"]["runs"]:  # no baseline in a run whose every cell must be safe
            self.assertFalse(set(_cfg(self.CONFIGS[key])["policies"]) & set(er.RELEASE_BASELINES), key)
        pc = json.loads(re.search(r"```policy-compare\n(.*?)```", text, re.S).group(1))
        names = {_cfg(p.stem)["name"] for p in (REPO / "evaluation" / "campaigns").glob("*.json")}
        self.assertTrue({r["campaign"] for r in pc["runs"].values()} <= names)

    def test_configs_differ_only_where_planned(self):
        base = {"name", "regime", "notes", "preregistration"}
        want = {"expR_minio_winf": ("expC_minio", {"authority.max_outstanding", "policies"}),
                "expR_zephyr_winf": ("expC3_ablation", {"authority.max_outstanding", "contracts", "real_owner.contracts"}),
                "expO_noorphan": ("expS_w6", {"episodes", "faults.device_args"}),
                "expK2_w6": ("expA_confirm_k2", {"authority.max_outstanding", "authority.resend_until", "policies"}),
                "expV_mild": ("expS_w6", {"episodes", "faults.windows_per_min", "seed"}),
                "expRT_queue": ("expW_queue_w6", {"activation_gate", "authority.release_after", "episodes", "policies"}),
                "expRT_direct": ("expW_direct_w6", {"activation_gate", "authority.release_after", "contracts", "episodes", "policies"}),
                "expRR_queue": ("expW_queue_w6", {"activation_gate", "authority.release_after", "authority.resend_until", "contracts", "episodes",
                                                  "policies"}),
                "expRR_direct": ("expW_direct_w6", {"activation_gate", "authority.release_after", "authority.resend_until", "contracts", "episodes",
                                                    "policies"}),
                "expL2_sleepy": ("expL_sleepy", {"authority.resend_until"}),
                "expQ_zephyr": ("expC2_zephyr", {"contracts", "episodes", "exclude_cells", "policies", "real_owner.contracts",
                                                 "real_owner.offline_cut", "real_owner.policies"}),
                "expWAN_s6": ("expS_w6", {"episodes", "faults.device_args"}),
                "expC16_minio": ("expC_minio", {"authority.max_outstanding", "policies"}),
                "expTB_thingsboard": ("expC_minio", {"contracts", "episodes", "real_owner.access_key_env", "real_owner.contracts",
                                                     "real_owner.device_admin_port", "real_owner.expiration_s", "real_owner.rpc_timeout_ms",
                                                     "real_owner.s3_url", "real_owner.secret_key_env", "real_owner.system", "real_owner.tb_mqtt",
                                                     "real_owner.tb_url"}),
                "rq4_scale_d8": ("rq4_load_r5", {"devices", "episodes"}),
                "rq4_scale_d16": ("rq4_load_r5", {"devices", "episodes"}),
                "rq4_scale_d32": ("rq4_load_r5", {"devices", "episodes"})}
        for name, (ref, allowed) in want.items():
            c, r = _cfg(name), _cfg(ref)
            d = {x for x in _diff(c, r) if x.split(".")[0] not in base}
            d = {x if not x.startswith("real_owner.contracts.") else "real_owner.contracts" for x in d}
            self.assertEqual(d, allowed, name)
        self.assertEqual(_cfg("expR_minio_winf")["authority"]["max_outstanding"], 1000)
        self.assertEqual(_cfg("expR_zephyr_winf")["authority"]["max_outstanding"], 1000)
        self.assertEqual(_cfg("expO_noorphan")["faults"]["device_args"], ["-crash-point-override", "AFTER_RECEIPT_BEFORE_REPLY"])
        self.assertEqual(_cfg("expK2_w6")["authority"]["max_outstanding"], 6)
        self.assertEqual(_cfg("expV_mild")["faults"]["windows_per_min"], 1.0)
        for n in ("expRT_queue", "expRT_direct"):
            self.assertEqual(_cfg(n)["policies"], ["RELEASE_ON_TIMEOUT"])
        for n in ("expRR_queue", "expRR_direct"):
            self.assertEqual((_cfg(n)["contracts"], _cfg(n)["policies"]), (["K2"], ["EVICON", "RELEASE_ON_READ"]))
        zc = _cfg("expR_zephyr_winf")["real_owner"]["contracts"]
        self.assertEqual({k: (v["mode"], v["channel"], v.get("restrict_to"), v.get("binding")) for k, v in zc.items()},
                         {"ZEPHYR_PLAIN": ("PLAIN", "queue", None, "qos1"), "ZEPHYR_CUTOFF": ("CUTOFF", "queue", None, "qos1"),
                          "ZEPHYR_PLAIN_DIRECT": ("PLAIN", "direct", None, "qos1"), "ZEPHYR_F_DIRECT": ("CUTOFF", "direct", ["F"], "qos1")})
        for n in ("expK2_w6", "expRR_queue", "expRR_direct", "expL2_sleepy"):  # the validity cut of resends
            w = _cfg(n)["workload"]
            self.assertEqual(_cfg(n)["authority"]["resend_until"], "%ds" % (w["validity_s"] - w["deadline_s"]), n)
        qc = _cfg("expQ_zephyr")["real_owner"]
        self.assertTrue(qc["offline_cut"])
        self.assertEqual({k: (v["mode"], v["channel"], v["binding"]) for k, v in qc["contracts"].items()},
                         {"ZEPHYR_PLAIN_QOS1": ("PLAIN", "queue", "qos1"), "ZEPHYR_PLAIN_QOS0": ("PLAIN", "queue", "auto"),
                          "ZEPHYR_CUTOFF_QOS1": ("CUTOFF", "queue", "auto")})
        self.assertEqual(er._expected_unsafe(_cfg("expQ_zephyr")), {("ZEPHYR_PLAIN_QOS1", "EVICON")})
        for n in ("expR_minio_winf", "expR_zephyr_winf", "expO_noorphan", "expK2_w6", "expV_mild", "expL2_sleepy", "expWAN_s6", "expC16_minio"):
            self.assertEqual(er._expected_unsafe(_cfg(n)), set(), n)
        self.assertEqual(er._expected_unsafe(_cfg("expTB_thingsboard")), {("TB_RPC", "NAIVE")})
        self.assertEqual(_cfg("expWAN_s6")["faults"]["device_args"], ["-wan-delay", "100ms", "-wan-jitter", "25ms"])
        self.assertEqual(_cfg("expC16_minio")["authority"]["max_outstanding"], 16)
        tb = _cfg("expTB_thingsboard")
        self.assertEqual((tb["contracts"], tb["policies"], tb["real_owner"]["system"]), (["TB_RPC"], ["EVICON", "M1", "NAIVE"], "thingsboard"))
        self.assertEqual(tb["real_owner"]["policies"]["NAIVE"], {"base": "M1", "authority": {"retry_guard": False}})
        self.assertEqual([_cfg(n)["devices"] for n in self.SCALE], [8, 16, 32])

    def test_review077_replays_the_reference_schedules(self):
        sys.path.insert(0, str(REPO / "evaluation"))
        import campaign as C
        replays = {"expR_minio_winf": ["expC_minio"], "expR_zephyr_winf": ["expC2_zephyr", "expC3_ablation", "expW_queue_winf", "expW_direct_winf"],
                   "expO_noorphan": ["expS_w6"], "expK2_w6": ["expA_confirm_k2"], "expRT_queue": ["expW_queue_w6"],
                   "expRT_direct": ["expW_direct_w6"], "expRR_queue": ["expW_queue_w6"], "expRR_direct": ["expW_direct_w6"],
                   "expL2_sleepy": ["expL_sleepy"], "expQ_zephyr": ["expC2_zephyr"], "expWAN_s6": ["expS_w6"], "expC16_minio": ["expC_minio"],
                   "expTB_thingsboard": ["expC_minio"]}
        for name, refs in replays.items():
            c = _cfg(name)
            for ref in refs:
                o = _cfg(ref)
                for ep in range(c["episodes"]):
                    self.assertEqual(C.schedule(c, ep), C.schedule(o, ep), (name, ref, ep))
        v, s = _cfg("expV_mild"), _cfg("expS_w6")
        self.assertNotEqual(C.schedule(v, 0), C.schedule(s, 0))


if __name__ == "__main__":
    unittest.main()
