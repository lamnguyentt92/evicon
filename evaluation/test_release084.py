"""Release 084: the review experiments of PREREG_extra_084.md ((a) per-identity fence intake, (c) PERIODIC_FULL), the delivery
binding rule ((b)) and the Level-2 analyses of the external review.

J1  the release is 084 everywhere (VERSION, buildinfo, CITATION.cff, the VM set-up banners, CHANGELOG)
J2  every earlier plan is byte-identical and still verifies (PREREG_review_077: db67640d..., PREREG_extra_083: 6e3f24a9...); the
    084 plan is frozen with every file the 083 plan froze (same SHA-256), the 083 plan itself and its own runners, binding rule
    and predictions; every 084 config carries its SHA-256 and passes the driver's check
J3  the authority arguments of every policy of releases <= 083 are unchanged; EVICON_FI and PERIODIC_FULL map as planned
J4  exclude_cells: campaign.run_campaign runs unchanged except for the one skip (K0S/EVICON_FI is not run)
J5  each 084 config differs from the run it replays only where the plan says, and replays its schedules (byte for byte through
    campaign.schedule)
J6  the driver runs every 084 campaign on the planned runner, the verify stage runs this test, the report runs fi084, esim084 and
    the Level-2 analyses
J7  the delivery binding rule: QoS 1 only with structural suppression (CONFIG_EVC_GUARD_EVICTED=y and
    CONFIG_EVC_DEDUP_HORIZON_MS=-1, in the source and in the build) or T with e + epsilon <= h; a finite measured D_h alone gets
    QoS 0, so the 077 gateway's auto choice is refused for it; the current firmware configs agree; the host test has the
    late-copy and positive-horizon cases
J8  the Level-2 analyses on synthetic cells: M10 per-request service time (p50/p95/p99 of lock + eval + commit), N11 admission,
    the ThingsBoard settings and deliveries, and "not available" everywhere when the raw data are missing
J9  fi084 (E-FI/E-PF trace counts) and esim084 (E-SIM)
J10 the plan's blocks: families disjoint, every run named, the 0.80 rule (FI-2, FI-3 estimands; 5 paired-difference hypotheses,
    13 rules), the simulator's power file lists the same hypotheses
J11 paper_tables: the E-FI and Level-2 macros, among them one per row of the M10 table
J12 safety_total: the guarded cells with faults whose Phi1 rests on the fault model (the plain firmware at QoS 1, ThingsBoard's
    guarded cells) are counted apart, per experiment, with the cells that test Theorem 1, and become macros
J13 expiry-revealed evidence needs unbounded record retention (the Go compiler; rubric.py is frozen and unchanged): the
    emulated device and the simulator's K3T contract declare it, nothing else does
J14 report sources: a review run that the archive holds and that is also read live (--from, or this run) is read once, from
    the live directory (found by the simulated-testbed regression: the final report counted such a run's campaigns twice)
"""
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "evaluation"))
sys.path.insert(0, str(REPO / "conformance"))
import evicon_review as er  # noqa: E402
import campaign as C  # noqa: E402
import ext083  # noqa: E402
import ext084  # noqa: E402
from reference import delivery_binding as DB  # noqa: E402

PLAN = "evaluation/prereg/PREREG_extra_084.md"
PLAN083 = "evaluation/prereg/PREREG_extra_083.md"
PLAN077 = "evaluation/prereg/PREREG_review_077.md"
SAFETY = ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def cfg(name: str) -> dict:
    return json.loads((REPO / "evaluation" / "campaigns" / f"{name}.json").read_text())


BASE = {"name", "regime", "notes", "preregistration"}
# config -> (the reference config it replays, the keys it changes)
CONFIGS = {
    "expFI_s6": ("expS_w6", {"activation_gate", "contracts", "episodes", "exclude_cells", "policies"}),
    "expFI_sinf": ("expS_winf", {"activation_gate", "contracts", "episodes", "policies"}),
    "expPF_zephyr_w6": ("expC2_zephyr", {"activation_gate", "contracts", "exclude_cells", "policies", "real_owner.contracts"}),
    "expPF_zephyr_winf": ("expR_zephyr_winf", {"activation_gate", "contracts", "policies", "real_owner.contracts"}),
    "expPF_minio_w6": ("expC_minio", {"contracts", "policies", "real_owner.contracts"}),
    "expPF_minio_winf": ("expR_minio_winf", {"contracts", "policies", "real_owner.contracts"}),
}


def flat(d: dict, pre="") -> dict:
    out = {}
    for k, v in d.items():
        if isinstance(v, dict) and k in ("authority", "faults", "workload", "real_owner", "remote", "ports"):
            out.update(flat(v, f"{pre}{k}."))
        else:
            out[pre + k] = v
    return out


def tmpdir(tc) -> Path:
    t = Path(tempfile.mkdtemp())
    tc.addCleanup(shutil.rmtree, t, ignore_errors=True)
    return t


class J1Version(unittest.TestCase):
    def test_version(self):
        v = (REPO / "VERSION").read_text().strip()
        self.assertGreaterEqual(v, "084")
        self.assertIn(f'const Release = "{v}"', (REPO / "internal" / "buildinfo" / "buildinfo.go").read_text())
        self.assertIn(f'version: "{v}"', (REPO / "CITATION.cff").read_text())
        for f in sorted((REPO / "deploy" / "setup").glob("vm-*.sh")):
            t = f.read_text()
            self.assertIn(f"(EviCon {v})", t, f.name)
            self.assertNotIn("(EviCon 083)", t, f.name)
        self.assertIn("## 084", (REPO / "CHANGELOG.md").read_text())


class J2Plans(unittest.TestCase):
    def test_earlier_plans_unchanged(self):
        self.assertEqual(sha(REPO / PLAN077), "db67640d1709b06dfa5e61f77e3444c07e7c5828dfe24e0e85d163f06a711a29")
        self.assertEqual(sha(REPO / PLAN083), "6e3f24a918de659c1feb8aaa886d9e9681195353528b5df10e615e8897ff3a06")
        for p in sorted((REPO / "evaluation" / "prereg").glob("*.md")):
            if "```frozen-files" not in p.read_text():
                continue
            out = subprocess.run([sys.executable, str(REPO / "scripts" / "freeze_prereg.py"), "--check", str(p.relative_to(REPO))],
                                 capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, p.name + out.stdout + out.stderr)

    def test_extra084_frozen(self):
        text = (REPO / PLAN).read_text()
        for marker in ("TO_BE_FROZEN", "PRE_RELEASE_CHECKS", "FROZEN_EXTRA_LIST"):
            self.assertNotIn(marker, text)
        b083 = re.search(r"```frozen-files\n(.*?)```", (REPO / PLAN083).read_text(), re.S).group(1).strip().splitlines()
        for line in b083:
            self.assertIn(line, text, line)
        for f in (PLAN083, "evaluation/ext084.py", "evaluation/campaign_ext084.py", "evaluation/campaign_real_ext084.py",
                  "evaluation/campaign_zephyr_ext084.py", "conformance/reference/delivery_binding.py",
                  "experiments/owners/bindings/delivery-binding-084.json", "evaluation/sim_extra084.py", "evaluation/power_extra084_testbed.py",
                  "evaluation/sim_predictions/extra084_summary.csv", "evaluation/sim_predictions/extra084_effects.csv",
                  "evaluation/sim_predictions/extra084_power.csv", "evaluation/sim_predictions/extra084_calibration.csv"):
            self.assertIn(f"{sha(REPO / f)}  {f}", text, f)
        digest = sha(REPO / PLAN)
        for name in CONFIGS:
            c = cfg(name)
            self.assertEqual(c["preregistration"], {"path": PLAN, "sha256": digest}, name)
            self.assertIn("verified", er._check_preregistration(c["preregistration"]), name)


class J3PolicyArgs(unittest.TestCase):
    A = {"resolve_sweep": "1s", "ambiguity_after": "1s", "hold_cost": 1.0, "stop_cost": 1.0, "fence_cost": 5.0, "release_after": "10s",
         "resend_until": "30s", "periodic": "10s"}

    def setUp(self):
        saved = (C.LEGACY, C.RESOLVERS, C.CONFIG_KEYS, C.policy_args, ext083._INSTALLED, ext084._INSTALLED)

        def restore():
            C.LEGACY, C.RESOLVERS, C.CONFIG_KEYS, C.policy_args, ext083._INSTALLED, ext084._INSTALLED = saved
        self.addCleanup(restore)

    def test_old_policies_unchanged_and_new_ones(self):
        ext083.install()
        old = ("EVICON", "M1", "M0", "RES_PERIODIC", "RES_IMMEDIATE", "RELEASE_ON_TIMEOUT", "RELEASE_ON_READ", "M1W", "RELEASE_ON_READ_ANSWERED",
               "RELEASE_ON_TIMEOUT_60S", "EVICON_GREEDY", "EVICON_FC100")
        before = {p: C.policy_args(p, dict(self.A, release_settle="7s")) for p in old}
        ext084._INSTALLED = False
        ext084.install()
        for p, args in before.items():
            self.assertEqual(C.policy_args(p, dict(self.A, release_settle="7s")), args, p)
        ev = before["EVICON"]
        self.assertEqual(C.policy_args("EVICON_FI", dict(self.A)), ev + ["-fence-intake", "per-identity"])
        pf = C.policy_args("PERIODIC_FULL", dict(self.A))
        i = ev.index("-resolve-trigger")
        self.assertEqual(pf[:i + 1] + pf[i + 2:], ev[:i + 1] + ev[i + 2:])
        self.assertEqual(pf[i + 1], "PERIODIC_FULL")
        self.assertIn("-resolve-sweep", pf)
        for p in ext084.POLICIES:
            self.assertIn(p, C.RESOLVERS)
            self.assertFalse(ext083.is_unsafe_by_design(p))


class J4Exclusion(unittest.TestCase):
    def setUp(self):
        saved = (C.run_campaign, getattr(C, "_RUN_CAMPAIGN_084", False), getattr(C, "_EXCLUDED_084", set()))

        def restore():
            C.run_campaign, C._RUN_CAMPAIGN_084, C._EXCLUDED_084 = saved
        self.addCleanup(restore)

    def test_one_skip(self):
        src = ext084.excluded_run_campaign_source()
        import inspect
        orig = inspect.getsource(C.run_campaign) if not getattr(C, "_RUN_CAMPAIGN_084", False) else None
        if orig is not None:
            self.assertEqual(src.replace(ext084.EXCLUDE_LINES, ""), orig)
        c = cfg("expFI_s6")
        ext084.install_exclusion(c)
        self.assertEqual(C._EXCLUDED_084, {("K0S", "EVICON_FI")})
        with self.assertRaises(ValueError):
            ext084.install_exclusion(dict(c, exclude_cells=[["K9", "EVICON"]]))
        cells = [(ct, p) for ct in c["contracts"] for p in c["policies"] if (ct, p) not in C._EXCLUDED_084]
        self.assertEqual(len(cells) * c["episodes"], 24)


class J5Configs(unittest.TestCase):
    def test_differ_only_where_planned(self):
        for name, (ref, keys) in CONFIGS.items():
            a, b = flat(cfg(name)), flat(cfg(ref))
            diff = {k for k in set(a) | set(b) if a.get(k) != b.get(k)} - BASE
            self.assertEqual(diff, keys, name)
        z = cfg("expPF_zephyr_w6")["real_owner"]["contracts"]["ZEPHYR_CUTOFF"]
        self.assertEqual((z["mode"], z.get("channel"), z.get("binding")), ("CUTOFF", "queue", "auto"))
        self.assertEqual(cfg("expFI_sinf")["authority"]["max_outstanding"], cfg("expS_winf")["authority"]["max_outstanding"])

    def test_replay_schedules(self):
        for name, (ref, _) in CONFIGS.items():
            c, o = cfg(name), cfg(ref)
            self.assertLessEqual(c["episodes"], o["episodes"], name)
            for ep in range(c["episodes"]):
                self.assertEqual(C.schedule(c, ep), C.schedule(o, ep), (name, ref, ep))


class J6Driver(unittest.TestCase):
    def test_runners(self):
        self.assertEqual(er._emulated_runner(cfg("expFI_s6"))[0], "evaluation/campaign_ext084.py")
        self.assertEqual(er._emulated_runner(cfg("expFI_sinf"))[0], "evaluation/campaign_ext084.py")
        self.assertEqual(er._emulated_runner(cfg("expG_s_winf"))[0], "evaluation/campaign_ext083.py")  # 083 unchanged
        self.assertEqual(er._emulated_runner(cfg("expS_w6"))[0], "evaluation/campaign.py")
        self.assertEqual(er._zephyr_runner(cfg("expPF_zephyr_w6")), "evaluation/campaign_zephyr_ext084.py")
        self.assertEqual(er._zephyr_runner(cfg("expT4_zephyr_w6")), "evaluation/campaign_zephyr_ext077.py")  # 083 unchanged
        self.assertTrue(er._uses_084_policies(cfg("expPF_minio_w6")))
        self.assertFalse(er._uses_084_policies(cfg("expG_minio_winf")))
        self.assertEqual(er._pairs_ext_runs(REPO / PLAN), {})
        src = (REPO / "evicon_review.py").read_text()
        for s in ('"evaluation.test_release084"', "evaluation/fi084.py", "evaluation/esim084.py", "evaluation/review_l2_084.py",
                  "campaign_real_ext084.py", "_check_delivery_binding(cfg, "):
            self.assertIn(s, src)


class J7Binding(unittest.TestCase):
    CUT = {"capabilities": ["D_h", "Q_A", "F"], "dedup_horizon_ms": 600000}

    def fake_repo(self, kconfig_sub=None, conf_line=None) -> Path:
        t = tmpdir(self)
        fw = t / "firmware" / "zephyr-cutoff"
        shutil.copytree(REPO / "firmware" / "zephyr-cutoff" / "boards", fw / "boards")
        k = (REPO / "firmware" / "zephyr-cutoff" / "Kconfig").read_text()
        if kconfig_sub:
            k = re.sub(kconfig_sub[0], kconfig_sub[1], k)
        (fw / "Kconfig").write_text(k)
        (fw / "prj.conf").write_text((REPO / "firmware" / "zephyr-cutoff" / "prj.conf").read_text() + (conf_line or ""))
        return t

    def test_rule(self):
        self.assertEqual(DB.rule_qos(self.CUT, True), (1, "STRUCTURAL_SUPPRESSION"))
        self.assertEqual(DB.rule_qos(self.CUT, False), (0, "FINITE_MEASURED_HORIZON_ONLY"))  # a measured horizon alone: QoS 0
        self.assertEqual(DB.rule_qos({"capabilities": ["D_h", "T"], "dedup_horizon_ms": 600000, "expiry_ms": 90000, "clock_skew_ms": 100}, False),
                         (1, "EXPIRY_WITHIN_HORIZON"))
        self.assertEqual(DB.rule_qos({"capabilities": ["D_h", "T"], "dedup_horizon_ms": 60000, "expiry_ms": 90000}, False), (0, "EXPIRY_BEYOND_HORIZON"))
        self.assertEqual(DB.rule_qos({"capabilities": []}, False), (0, "NO_DEDUPLICATION"))

    def test_current_configs_agree(self):
        self.assertEqual(DB.source_premises(), [])
        self.assertTrue(DB.guard_enabled())
        good = {"CONFIG_EVC_GUARD_EVICTED": "y", "CONFIG_EVC_DEDUP_HORIZON_MS": "-1"}
        self.assertEqual(DB.check_contract("ZC", "CUTOFF", self.CUT, build_config=good), [])
        self.assertEqual(DB.check_contract("ZS", "STOP", {"capabilities": ["D_h", "Q_A", "Q_S"]}), [])
        self.assertEqual(DB.check_contract("ZP", "PLAIN", {"capabilities": []}), [])
        import campaign_zephyr_ext084 as Z84
        for name in ("expPF_zephyr_w6", "expPF_zephyr_winf", "expC2_zephyr", "expT6_zephyr_w3"):
            c = cfg(name)
            for spec in c["real_owner"]["contracts"].values():
                mode = spec["mode"].lower()
                spec["contract_file"] = f"experiments/owners/zephyr/pred-zephyr-{mode}-074.json"
            self.assertEqual(Z84.check_binding(c), [], name)

    def test_negative_cases(self):
        # a finite measured horizon without structural suppression: the 077 gateway's auto would send QoS 1, the rule refuses
        self.assertTrue(DB.check_contract("X", "PLAIN", self.CUT))
        # the build that runs: a positive horizon or no eviction guard voids structural suppression
        for bc in ({"CONFIG_EVC_GUARD_EVICTED": "y", "CONFIG_EVC_DEDUP_HORIZON_MS": "600000"}, {"CONFIG_EVC_DEDUP_HORIZON_MS": "-1"},
                   {"CONFIG_EVC_GUARD_EVICTED": "y", "CONFIG_EVC_DEDUP_HORIZON_MS": "0"}):
            bad = DB.check_contract("ZC", "CUTOFF", self.CUT, build_config=bc)
            self.assertEqual(len(bad), 1, bc)
            self.assertIn("allows QoS 0", bad[0])
        # the source configuration: a Kconfig default or a project setting that breaks a premise
        r1 = self.fake_repo(kconfig_sub=(r"(config EVC_DEDUP_HORIZON_MS\n.*?\n)\tdefault -1", r"\1\tdefault 600000"))
        self.assertTrue(DB.source_premises(r1))
        self.assertFalse(DB.structural("CUTOFF", r1))
        self.assertTrue(DB.check_contract("ZC", "CUTOFF", self.CUT, repo=r1))
        r2 = self.fake_repo(conf_line="\nCONFIG_EVC_GUARD_EVICTED=n\n")
        self.assertFalse(DB.structural("CUTOFF", r2))
        r3 = self.fake_repo(conf_line="\nCONFIG_EVC_DEDUP_HORIZON_MS=600000\n")
        self.assertFalse(DB.structural("CUTOFF", r3))
        r4 = self.fake_repo()
        self.assertTrue(DB.structural("CUTOFF", r4))
        decl = DB.declarations()
        self.assertEqual(sorted(decl["CUTOFF"]["requires"]), ["CONFIG_EVC_DEDUP_HORIZON_MS=-1", "CONFIG_EVC_GUARD_EVICTED=y"])
        self.assertFalse(decl["PLAIN"]["structural_suppression"])

    def test_host_test_cases(self):
        t = (REPO / "firmware" / "zephyr-cutoff" / "tests" / "host" / "test_evc_late_copy.c").read_text()
        for fn in ("late_copy_after_ring_eviction", "late_copy_after_reboot", "late_copy_beyond_positive_horizon"):
            self.assertIn(f"{fn}()", t)
        mk = (REPO / "firmware" / "zephyr-cutoff" / "tests" / "host" / "Makefile").read_text()
        self.assertIn("-DEVC_LATE_COPY_NO_GUARD", mk)
        self.assertIn("./test_evc_late_copy_neg", mk)


class J8Level2(unittest.TestCase):
    def run_dir(self, root: Path, name: str, rows: list, cfg_: dict = None) -> Path:
        run = root / name / "20261101T000000"
        run.mkdir(parents=True)
        (run / "config.json").write_text(json.dumps(cfg_ or {"name": name, "workload": {"deadline_s": 60}}))
        hdr = sorted({k for r in rows for k in r})
        (run / "runtime_episodes.csv").write_text(",".join(hdr) + "\n" + "".join(",".join(str(r.get(k, "")) for k in hdr) + "\n" for r in rows))
        return run

    def test_service_time(self):
        import review_l2_084 as L2
        root = tmpdir(self)
        run = self.run_dir(root, "rq4-load-r5", [{"episode": 0, "contract": "K0F", "policy": "EVICON", "cell_status": "VALID", "offered": 4,
                                                  "admitted": 4, "clean_fulfilled": 4}])
        cell = run / "cells" / "ep0" / "K0F" / "EVICON"
        (cell / "raw" / "A").mkdir(parents=True)
        cl, tr = [], []
        for i in range(4):
            cid = f"d0-c{i}"
            cl.append({"cid": cid, "arrival": 1000.0 + i, "decision": "ADMITTED", "attempts": 1, "admitted": {cid: 1000.01 + i}})
            tr += [{"event": "ADMISSION_RECEIVED", "command_id": cid, "wall_time": f"2026-11-01T00:00:0{i}.000100000Z", "wait_ns": 50000},
                   {"event": "ADMISSION_PREPARE", "command_id": cid, "wall_time": f"2026-11-01T00:00:0{i}.001100000Z", "monotonic_ns": 10 ** 9 * i},
                   {"event": "DECISION_RECORDED", "command_id": cid, "decision": "ADMITTED", "monotonic_ns": 10 ** 9 * i + 1000000 * (1 + 3 * i)}]
        cl.append({"cid": "d0-c9", "arrival": 1009.0, "decision": "ADMITTED", "attempts": 2, "admitted": {"d0-c9": 1009.5}})  # not a first attempt
        (cell / "clients.jsonl").write_text("".join(json.dumps(x) + "\n" for x in cl))
        (cell / "raw" / "A" / "authority-d0.jsonl").write_text("".join(json.dumps(x) + "\n" for x in tr))
        lean = self.run_dir(root, "rq4-load-r1", [{"episode": 0, "contract": "K0F", "policy": "EVICON", "cell_status": "VALID", "offered": 1,
                                                   "admitted": 1, "clean_fulfilled": 1}])
        rep = root / "report"
        (rep / "overhead").mkdir(parents=True)
        (rep / "overhead" / "overhead.json").write_text(json.dumps([{"campaign": "rq4-load-r5", "contract": "K0F", "policy": "EVICON",
                                                                     "lock_ms_p50": 0.05, "eval_ms_p50": 1.0, "decide_ms_p50": 4.0}]))
        res = L2.service([run, lean, root / "not-rq4"], rep)
        self.assertTrue(res["available"])
        g = next(x for x in res["rows"] if x["campaign"] == "rq4-load-r5")
        # per request: 0.05 + 1.0 + commit (1, 4, 7, 10 ms) = 2.05, 5.05, 8.05, 11.05
        self.assertEqual(g["n"], 4)
        self.assertEqual((g["sum_p50_ms"], g["sum_p95_ms"], g["sum_p99_ms"], g["sum_max_ms"]), (8.05, 11.05, 11.05, 11.05))
        self.assertEqual(g["sum_of_medians_ms"], 8.05)
        self.assertEqual(g["sum_of_medians_report_ms"], 5.05)
        lg = next(x for x in res["rows"] if x["campaign"] == "rq4-load-r1")
        self.assertEqual((lg["n"], lg["sum_p99_ms"]), (0, L2.NA))
        self.assertEqual(res["missing_cells"], 1)
        md = L2.service_md(res)
        self.assertIn("sum p99", md)
        self.assertIn("not available", md)
        empty = L2.service([lean], None)
        self.assertFalse(empty["available"])
        self.assertIn("no clients.jsonl", empty["reason"])
        self.assertIn("not available", L2.service_md(empty))

    def test_admission_and_tb(self):
        import review_l2_084 as L2
        root = tmpdir(self)
        run = self.run_dir(root, "expW-queue-w6", [{"episode": 0, "contract": "K0", "policy": "EVICON", "cell_status": "VALID", "offered": 10,
                                                    "admitted": 8, "clean_fulfilled": 7, "fulfilled_late": 1}])
        a = L2.admission([run])["groups"][0]
        self.assertEqual((a["offered"], a["admitted"], a["not_admitted"], a["deadline_misses"], a["admitted_after_deadline"]), (10, 8, 2, 3, L2.NA))
        s = L2.tb_settings()
        self.assertEqual((s["rpc"]["retries"], s["rpc"]["persistent"], s["device"]["subscribe_qos"], s["device"]["response_qos"]), (0, True, 1, 1))
        self.assertFalse(s["device"]["app_dedup"])
        self.assertTrue(s["device"]["executes_every_delivery"])
        tb = self.run_dir(root, "expTB-thingsboard", [{"episode": 0, "contract": "TB_RPC", "policy": "EVICON", "cell_status": "VALID",
                                                       "offered": 3, "admitted": 3, "clean_fulfilled": 2, "duplicate_effects": 1}],
                          {"name": "expTB-thingsboard", "real_owner": {"system": "thingsboard"}})
        cd = tb / "cells" / "ep0" / "TB_RPC" / "EVICON" / "raw" / "E"
        cd.mkdir(parents=True)
        (cd / "plant.jsonl").write_text("".join(json.dumps({"cmd": c}) + "\n" for c in ("a", "b", "b")))
        g = tb / "cells" / "ep0" / "TB_RPC" / "EVICON" / "raw" / "C"
        g.mkdir(parents=True)
        (g / "gateway-d0.jsonl").write_text("".join(json.dumps({"op": "effect", "cid": c, "rpc": r}) + "\n" for c, r in (("a", "r1"), ("b", "r2"))))
        d = L2.tb_delivery([tb, run])
        self.assertEqual((d["duplicates_total"], d["duplicates_one_rpc_total"]), (1, 1))
        self.assertEqual(d["cells"][0]["max_deliveries_per_identity"], 2)
        lean = self.run_dir(root, "expTB-w3", [{"episode": 0, "contract": "TB_RPC", "policy": "EVICON", "cell_status": "VALID", "offered": 3,
                                                "admitted": 3, "clean_fulfilled": 3}], {"name": "expTB-w3", "real_owner": {"system": "thingsboard"}})
        self.assertEqual(len(L2.tb_delivery([lean])["not_available"]), 1)

    def test_main_on_missing_data(self):
        import review_l2_084 as L2
        root = tmpdir(self)
        run = self.run_dir(root, "expS-w6", [{"episode": 0, "contract": "K0F", "policy": "EVICON", "cell_status": "VALID", "offered": 10,
                                              "admitted": 10, "clean_fulfilled": 9, "fences": 3}])
        out = root / "out"
        self.assertEqual(L2.main(["--out", str(out), "--campaign", str(run), "--report", str(root / "no-report")]), 0)
        s = json.loads((out / "l2_084.json").read_text())
        self.assertEqual(s["service_available"], 0)
        for name in ("eoo", "tb_late", "readrelease", "admission", "latency_n", "r5cf", "rubric", "tb_delivery", "service"):
            self.assertTrue((out / name / f"{name.upper()}.md").exists(), name)
        self.assertIn("not available", (out / "service" / "SERVICE.md").read_text())


class J9Estimands(unittest.TestCase):
    def test_fi084(self):
        import fi084
        root = tmpdir(self)
        run = root / "expFI-s6" / "20261101T000000"
        hdr = "episode,contract,policy,cell_status,offered,clean_fulfilled,fences,fences_ok,stops,stops_terminal,queries,unresolved_at_end,crashes,mean_time_to_terminal_s"
        rows = [f"0,K0F,EVICON,VALID,100,50,10,1,0,0,0,3,2,1.5", f"0,K0F,EVICON_FI,VALID,100,80,4,4,0,0,0,1,2,1.0"]
        run.mkdir(parents=True)
        (run / "runtime_episodes.csv").write_text(hdr + "\n" + "\n".join(rows) + "\n")
        a = run / "cells" / "ep0" / "K0F" / "EVICON" / "raw" / "A"
        a.mkdir(parents=True)
        (a / "authority-d0.jsonl").write_text("".join(json.dumps(x) + "\n" for x in (
            {"event": "BARRIER_ABORTED", "reason": "t1:OWNER_UNCERTAIN:d0-c3"}, {"event": "BARRIER_ABORTED", "reason": "t2:OWNER_UNCERTAIN:d0-c3,d0-c7"})))
        b = run / "cells" / "ep0" / "K0F" / "EVICON_FI" / "raw" / "A"
        b.mkdir(parents=True)
        (b / "authority-d0.jsonl").write_text("".join(json.dumps(x) + "\n" for x in (
            {"event": "BARRIER_PER_IDENTITY_COMMITTED", "reason": "t1:OWNER_UNCERTAIN:d0-c3"},
            {"event": "EVIDENCE_COMMITTED", "reason": "BARRIER_RECEIPT:t1"}, {"event": "EVIDENCE_COMMITTED", "reason": "BARRIER_SEALED_UNCLAIMED:t1"},
            {"event": "EVIDENCE_COMMITTED", "reason": "BARRIER_RECEIPT:t2"})))
        g = {(x["policy"]): x for x in fi084.analyse({"FIs6": str(run)})["groups"]}
        self.assertEqual((g["EVICON"]["aborted_owner_uncertain"], g["EVICON"]["orphans_listed"], g["EVICON"]["clean_pp"]), (2, 2, 50.0))
        self.assertEqual((g["EVICON_FI"]["per_identity"], g["EVICON_FI"]["terminal_answers_per_fence"]), (1, 1.5))
        self.assertIn("E-FI and E-PF", fi084.markdown({"groups": list(g.values())}))

    def test_esim084(self):
        import esim084
        res = {"ext_levels": {"FIs6": {"K0F/EVICON_FI": {"clean_pp": 75.0, "violations": 0.0, "n": 8}},
                              "PFm6": {"MINIO_COND/PERIODIC_FULL": {"clean_pp": 76.0, "violations": 0.0, "n": 8}}},
               "hypotheses": [{"id": "FI-1", "test": "at_least", "verdict": "MET", "mean": 18.0, "t95": [12.0, 24.0], "n": 8},
                              {"id": "PF-3", "test": "equivalent", "verdict": "MET", "mean": 0.2, "t95": [-0.5, 0.9], "n": 8},
                              {"id": "FI-A1", "test": "absent", "verdict": "ABSENT"}]}
        out = esim084.analyse(res)
        lv = {(r["run"], r["cell"]): r for r in out["levels"]}
        self.assertEqual(lv[("FIs6", "K0F/EVICON_FI")]["sim_clean_pct"], 76.13)
        self.assertEqual(lv[("PFm6", "MINIO_COND/PERIODIC_FULL")]["scenario"], "c_minio_w6")
        ef = {e["id"]: e for e in out["effects"]}
        self.assertTrue(ef["FI-1"]["sign_agrees"])
        self.assertNotIn("FI-A1", ef)
        self.assertIn("PREREG_extra_084", esim084.markdown(out))
        import esim083  # the frozen module keeps its own prediction afterwards (the 083 test passes in any order)
        self.assertEqual((esim083.SUMMARY.name, esim083.POWER.name), ("extra083_summary.csv", "extra083_power.csv"))
        self.assertIn("TBw", esim083.RUN_CELLS)


class J10PlanBlocks(unittest.TestCase):
    ESTIMANDS = {"FI-2", "FI-3"}

    def test_blocks_and_power_rule(self):
        import csv
        import pairs_ext083 as PX
        std, ext, _ = PX.spec_blocks(REPO / PLAN)
        hs = std["hypotheses"] + ext["hypotheses"]
        fs = {h.get("family") for h in std["hypotheses"] if h["test"] not in ("detects", "absent")}
        fe = {h.get("family") for h in ext["hypotheses"] if h["test"] not in ("detects", "absent", "safety")}
        self.assertFalse(fs & fe)
        names = {cfg(p.stem)["name"] for p in (REPO / "evaluation" / "campaigns").glob("*.json")}
        for key, r in std["runs"].items():
            self.assertIn(r["campaign"], names, key)
        own = {k for k, r in std["runs"].items() if not r.get("reference")}
        self.assertEqual({std["runs"][k]["campaign"] for k in own}, {cfg(n)["name"] for n in CONFIGS})
        for h in hs + ext["estimands"] + std["estimands"]:
            for arm in ([h["a"], h["b"]] if "a" in h else [h]):
                for k in {arm.get("run"), arm.get("from_run")} - {None}:
                    self.assertIn(k, std["runs"], h.get("id", h.get("label")))
            for k in h.get("runs", []):
                self.assertIn(k, std["runs"])
        ids = [h["id"] for h in hs]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertFalse(set(ids) & self.ESTIMANDS)
        paired = [h for h in hs if h["test"] in ("at_least", "at_most", "equivalent")]
        self.assertEqual({h["id"] for h in paired}, {"FI-1", "PF-1", "PF-2", "PF-3", "PF-4"})
        self.assertEqual(len(hs) + 1, 13)  # + X-S1, pairs_report's safety rule
        self.assertEqual(std["safety"]["runs"], ["FIs6", "FIsi"])
        for h in std["hypotheses"]:
            self.assertEqual((h["test"], h["any_of"]), ("absent", SAFETY), h["id"])
        fi1 = next(h for h in paired if h["id"] == "FI-1")
        self.assertEqual((fi1["run"], fi1["contract"], fi1["from_policy"], fi1["to_policy"], fi1["test"], fi1["margin"]),
                         ("FIs6", "K0F", "EVICON", "EVICON_FI", "at_least", 10.0))
        refs = {"PF-1": "C2", "PF-2": "Rz", "PF-3": "C", "PF-4": "Rm"}
        for h in paired:
            if h["id"] in refs:
                self.assertEqual((h["from_run"], h["from_policy"], h["to_policy"], h["test"], h["margin"]),
                                 (refs[h["id"]], "EVICON", "PERIODIC_FULL", "equivalent", 3.0))
        self.assertTrue(any(e.get("label", "").startswith("FI-2") and "clean_pp" in e["metrics"] for e in ext["estimands"]))
        self.assertTrue(any(e.get("label", "").startswith("FI-3") and e["run"] == "FIsi" and "clean_pp" in e["metrics"] for e in ext["estimands"]))
        with open(REPO / "evaluation" / "sim_predictions" / "extra084_power.csv") as fh:
            self.assertEqual({r["id"] for r in csv.DictReader(fh)}, {h["id"] for h in paired})
        pc = json.loads(re.search(r"```policy-compare\n(.*?)```", (REPO / PLAN).read_text(), re.S).group(1))
        for c in pc["comparisons"]:
            for arm in (c["a"], c["b"]):
                self.assertIn(arm["run"], pc["runs"], c["id"])


class J11PaperTables(unittest.TestCase):
    def test_macros(self):
        from evaluation import paper_tables
        rep = tmpdir(self)
        (rep / "fi084").mkdir()
        (rep / "fi084" / "fi084.json").write_text(json.dumps({"groups": [{"run": "FIs6", "contract": "K0F", "policy": "EVICON_FI", "per_identity": 3.0,
                                                                         "aborted_owner_uncertain": 0.0, "terminal_answers_per_fence": 2.5}]}))
        l2 = rep / "l2_084"
        (l2 / "service").mkdir(parents=True)
        (l2 / "l2_084.json").write_text(json.dumps({"service_available": 1, "service_sum_p99_max_ms": 11.05}))
        (l2 / "service" / "service.json").write_text(json.dumps({"rows": [
            {"campaign": "rq4-load-r5", "contract": "K0F", "policy": "EVICON", "n": 4, "sum_p50_ms": 8.05, "sum_p95_ms": 11.05,
             "sum_p99_ms": 11.05, "sum_of_medians_ms": 8.05}]}))
        N = {}
        made = paper_tables.extra084(rep, rep, N)
        self.assertIn("tab_l2_service.tex", made)
        self.assertEqual(N["evLServiceAvailable"], "1")
        svc = [k for k in N if k.startswith("evLsvc")]
        self.assertEqual(len(svc), 4)
        self.assertTrue(any(k.endswith("PNinetyNine") and N[k] == "11.05" for k in svc))
        self.assertTrue(any(k.startswith("evFI") and k.endswith("PerIdentity") for k in N))
        self.assertIn("Sum p99", (rep / "tab_l2_service.tex").read_text())


class J12SafetyDagger(unittest.TestCase):
    def test_counts_and_macros(self):
        import safety_total as ST
        from evaluation import paper_tables
        root = tmpdir(self)
        runs = []
        hdr = "contract,policy,episode,cell_status,offered,admitted,dispatch_attempts,late_effects,fault_windows," + ",".join(ST.KINDS)
        for name, cfg_, cells in (
                ("expC2-zephyr", cfg("expC2_zephyr"), [("ZEPHYR_PLAIN", "EVICON"), ("ZEPHYR_PLAIN", "M1"), ("ZEPHYR_PLAIN", "NAIVE"), ("ZEPHYR_CUTOFF", "EVICON")]),
                ("expT4-zephyr-w6", cfg("expT4_zephyr_w6"), [("ZEPHYR_PLAIN_QOS0", "EVICON"), ("ZEPHYR_PLAIN_DIRECT_QOS0", "EVICON")]),
                ("expR-zephyr-winf", cfg("expR_zephyr_winf"), [("ZEPHYR_PLAIN", "EVICON"), ("ZEPHYR_F_DIRECT", "EVICON")]),
                ("expTB-thingsboard", cfg("expTB_thingsboard"), [("TB_RPC", "EVICON"), ("TB_RPC", "NAIVE")]),
                ("expS-w6", cfg("expS_w6"), [("K0F", "EVICON")])):
            run = root / name / "20261101T000000"
            run.mkdir(parents=True)
            (run / "config.json").write_text(json.dumps(cfg_))
            (run / "manifest.json").write_text(json.dumps({"release": "084", "scope": "TESTBED"}))
            rows = [hdr]
            for ep in (0, 1):
                (run / f"schedule_ep{ep}.json").write_text(json.dumps({"arrivals": [], "faults": [{"t": ep, "kind": "offline"}]}))
                for ct, pol in cells:
                    rows.append(f"{ct},{pol},{ep},VALID,10,10,10,0,{1 if ep == 0 or name != 'expS-w6' else 0}," + ",".join("0" for _ in ST.KINDS))
            (run / "runtime_episodes.csv").write_text("\n".join(rows) + "\n")
            runs.append(ST.read_run(run))
        s = ST.summarize(runs)
        pf = s["phi1_fault_model"]
        # C2: plain EVICON + M1 (NAIVE is a control) x 2 = 4; T4: QoS 0 (binding auto) = 0; Rz plain qos1 = 2; TB EVICON (NAIVE unsafe) = 2
        self.assertEqual(pf["by_experiment"], {"expC2-zephyr": 4, "expR-zephyr-winf": 2, "expTB-thingsboard": 2})
        self.assertEqual(pf["by_reason"], {"plain_firmware_qos1": 6, "thingsboard": 2})
        self.assertEqual(pf["cells"], 8)
        # guarded with faults: C2 6, T4 4, Rz 4, TB 2, S 1 (its episode 1 cell had no fault window) = 17
        self.assertEqual(s["cells_with_faults"], 17)
        self.assertEqual(s["theorem1_cells_with_faults"], 9)
        self.assertIn("**8 of the 17 guarded cells with faults**", ST.markdown(runs, s))
        rep = tmpdir(self)
        (rep / "safety").mkdir()
        (rep / "safety" / "safety.json").write_text(json.dumps({"summary": s, "runs": []}))
        N = {}
        paper_tables.safety(rep, rep, N)
        self.assertEqual((N["evSafetyCellsDagger"], N["evSafetyCellsTheoremOne"]), ("8", "9"))
        self.assertEqual(N["evSafetyCellsDaggerExpCTwoZephyr"], "4")
        self.assertEqual(N["evSafetyCellsDaggerThingsboard"], "2")
        self.assertEqual(ST.phi1_on_fault_model(cfg("expQ_zephyr"), "ZEPHYR_CUTOFF_QOS1"), "")
        self.assertEqual(ST.phi1_on_fault_model(cfg("expPF_zephyr_w6"), "ZEPHYR_CUTOFF"), "")


class J13ExpiryRevealedRetention(unittest.TestCase):
    def test_declarations(self):
        lat = (REPO / "internal" / "contract" / "lattice.go").read_text()
        self.assertIn("c.Has(CapQueryApplied) && c.RecordRetentionMS == UnboundedRetentionMS", lat)
        self.assertIn("a measurement cannot establish unbounded record retention", lat)
        self.assertIn("c.RecordRetentionMS = contract.UnboundedRetentionMS", (REPO / "internal" / "device" / "device.go").read_text())
        declared = []
        for p in sorted(REPO.rglob("*.json")):
            if "results" in p.parts:
                continue
            try:
                d = json.loads(p.read_text())
            except ValueError:
                continue
            if isinstance(d, dict) and d.get("record_retention_ms") is not None:
                declared.append((p.relative_to(REPO).as_posix(), d.get("source"), d["record_retention_ms"]))
        self.assertEqual(declared, [("experiments/contracts/sim-k3-expiry.json", "HYPOTHESIS", -1)])
        # the frozen Python mirror of the lattice keeps the release-083 rule; no measured contract has T, Q_A and D_h(h=-1)
        self.assertIn('"Q_A" in caps and "D_h" in caps and horizon_ms == UNBOUNDED', (REPO / "conformance" / "evicon_probe" / "rubric.py").read_text())


class J14ReportSources(unittest.TestCase):
    def test_archived_run_read_live_once(self):
        tmp = tmpdir(self)
        arc, live = tmp / "archive", tmp / "EVICON_TOIT_083" / "results"
        old, r083, r084 = "review-20261020T010000", "review-20261031T000000", "review-20261101T000000"
        for r in (old, r083, r084):
            (arc / "runs" / r).mkdir(parents=True)
        (arc / "runs" / r084 / "report").mkdir()
        (arc / "runs" / r084 / "report" / "REPORT.md").write_text(
            f"# EviCon report\n\nRelease 084; sources:\n\n- `/root/EVICON_TOIT_081/results/{old}`\n"
            f"- `/root/EVICON_TOIT_083/results/{r083}`\n- `/root/EVICON_TOIT_084/results/{r084}`\n\n- [x](y)\n")
        (live / r083).mkdir(parents=True)
        cur = tmp / "EVICON_TOIT_084" / "results" / r084
        cur.mkdir(parents=True)
        logged = []

        class _Run:
            path = cur

            def log(self, msg):
                logged.append(msg)

        class _A:
            archive, archive_missing_ok, sources_of, from_run = str(arc), False, "", [str(live / r083)]
        orig = er.RUN
        er.RUN = _Run()
        try:
            got = er._sources(_A())
            # the archive copies of the 083 run and of this run are dropped, the run only the archive holds is kept
            self.assertEqual(got, [(arc / "runs" / old).resolve(), (live / r083).resolve(), cur.resolve()])
            self.assertTrue(any("2 archived run(s) also read live" in m for m in logged), logged)
            # nothing read live twice: every archive copy stays
            _A.from_run = []
            er.RUN.path = tmp / "elsewhere" / "review-20261102T000000"
            er.RUN.path.mkdir(parents=True)
            self.assertEqual(er._sources(_A()), [(arc / "runs" / r).resolve() for r in (old, r083, r084)] + [er.RUN.path.resolve()])
        finally:
            er.RUN = orig


if __name__ == "__main__":
    unittest.main()
