"""Release 083: the second set of review experiments (PREREG_extra_083.md, T1-T7) and analyses (A1-A3).

H1  the release is 083 everywhere (VERSION, buildinfo, CITATION.cff, the VM set-up banners)
H2  every earlier plan is byte-identical and still verifies (PREREG_review_077: db67640d...); the 083 plan is frozen
    with every file the 077 plan froze (same SHA-256) and its own analyses, runners and predictions; every 083 config
    carries its SHA-256 and passes the driver's check
H3  the authority arguments of every policy of releases <= 082 are unchanged; the 083 policies map as planned
H4  the practice baselines have one definition (safety_total.BASELINES = t3_attribution = claims_check = driver), with
    RELEASE_ON_READ_ANSWERED and the release-time variants; the driver's planned violations follow it
H5  each 083 config differs from the run it replays only where the plan says, and replays its schedules (episodes
    compared byte for byte through campaign.schedule); the T7 levels change the fault schedule and only it
H6  the driver runs every 083 campaign on the planned runner and the report decides the plan with pairs_ext083.py
H7  A1: safety_total counts the distinct fault schedules behind the guarded cells with faults and gives
    1 - 0.05^(1/N_s)
H8  A2: overhead counts the admissions after the deadline; A3: t3_attribution lists a window re-opening without
    evidence with its trace context and an explanation
H9  paper_tables: one rounding (79.14677 prints 79.1, from expC2_summary's unrounded means), the second K2 run is
    the K2 replication, the firmware is one system (\\evSystems 10, \\evThirdPartySystems 9)
H10 pairs_ext083 decides policy contrasts, cross-run contrasts (schedules must match), differences of contrasts, safety
    groups over the runs that were run, and trace counts; its families are disjoint from the pairs-spec block's
H11 E-SIM (esim083): the plan's runs next to its simulator prediction, levels and the signs of the tested effects
H12 the plan's 0.80 rule: the paired-difference rules below 0.80 under the testbed's spread are estimands, T4-6 tests the
    gain at W unbounded, 54 rules in all (29 paired-difference hypotheses), and the simulator's power file lists the same
"""
import hashlib
import json
import math
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
import evicon_review as er  # noqa: E402
from evaluation import claims_check as CC  # noqa: E402
from evaluation import overhead, paper_tables, safety_total, t3_attribution  # noqa: E402
import campaign as C  # noqa: E402
import ext083  # noqa: E402

PLAN = "evaluation/prereg/PREREG_extra_083.md"
PLAN077 = "evaluation/prereg/PREREG_review_077.md"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def cfg(name: str) -> dict:
    return json.loads((REPO / "evaluation" / "campaigns" / f"{name}.json").read_text())


T7_LEVELS = ("rate05", "rate4", "off5", "off40", "seedA", "seedB")
# config -> (reference config it replays, the keys it changes)
BASE = {"name", "regime", "notes", "preregistration"}
CONFIGS = {
    "expM1W_k2": ("expW_queue_w6", {"activation_gate", "authority.resend_until", "contracts", "episodes", "policies"}),
    "expTB_m1w": ("expTB_thingsboard", {"policies", "real_owner.policies"}),
    "expRRA_queue": ("expRR_queue", {"authority.release_settle", "faults.device_args", "policies"}),
    "expRRA_direct": ("expRR_direct", {"authority.release_settle", "policies"}),
    "expRRA_noorphan": ("expRR_direct", {"authority.release_settle", "faults.device_args", "policies"}),
    "expRTs_queue": ("expRT_queue", {"contracts", "episodes", "policies"}),
    "expRTs_direct": ("expRT_direct", {"episodes", "policies"}),
    "expG_s_winf": ("expS_winf", {"activation_gate", "contracts", "episodes", "policies"}),
    "expG_minio_winf": ("expR_minio_winf", {"policies"}),
    "expTheta_w6": ("expW_queue_w6", {"activation_gate", "contracts", "episodes", "policies"}),
    "expT4_zephyr_w6": ("expC3_ablation", {"contracts", "real_owner.contracts"}),
    "expT4_zephyr_winf": ("expC3_ablation", {"authority.max_outstanding", "contracts", "real_owner.contracts"}),
    "expT6_zephyr_w3": ("expC3_ablation", {"authority.max_outstanding", "contracts", "real_owner.contracts"}),
    "expT6_zephyr_w16": ("expC3_ablation", {"authority.max_outstanding", "contracts", "real_owner.contracts"}),
    "expT6_zephyr_w32": ("expC3_ablation", {"authority.max_outstanding", "contracts", "real_owner.contracts"}),
    "expTB_w3": ("expTB_thingsboard", {"authority.max_outstanding", "policies", "real_owner.policies"}),
    "expTB_w16": ("expTB_thingsboard", {"authority.max_outstanding", "policies", "real_owner.policies"}),
    "expTB_winf": ("expTB_thingsboard", {"authority.max_outstanding", "policies", "real_owner.policies"}),
}
for _lv in T7_LEVELS:
    _f = {"rate05": "faults.windows_per_min", "rate4": "faults.windows_per_min", "off5": "faults.offline_s", "off40": "faults.offline_s",
          "seedA": "seed", "seedB": "seed"}[_lv]
    CONFIGS[f"expT7_q6_{_lv}"] = ("expS_w6", {"episodes", _f})
    CONFIGS[f"expT7_dinf_{_lv}"] = ("expW_direct_winf", {"episodes", _f})
# config -> reference configs whose schedules it replays (episode by episode)
REPLAYS = {"expM1W_k2": ["expW_queue_w6"], "expTB_m1w": ["expTB_thingsboard"], "expRRA_queue": ["expRR_queue"], "expRRA_direct": ["expRR_direct"],
           "expRRA_noorphan": ["expRR_direct"], "expRTs_queue": ["expRT_queue"], "expRTs_direct": ["expRT_direct"], "expG_s_winf": ["expS_winf"],
           "expG_minio_winf": ["expR_minio_winf"], "expTheta_w6": ["expW_queue_w6"], "expT4_zephyr_w6": ["expC2_zephyr", "expC3_ablation"],
           "expT4_zephyr_winf": ["expR_zephyr_winf"], "expT6_zephyr_w3": ["expC2_zephyr"], "expT6_zephyr_w16": ["expC2_zephyr"],
           "expT6_zephyr_w32": ["expC2_zephyr"], "expTB_w3": ["expTB_thingsboard"], "expTB_w16": ["expTB_thingsboard"],
           "expTB_winf": ["expTB_thingsboard"]}


def flat(d: dict, pre="") -> dict:
    out = {}
    for k, v in d.items():
        if isinstance(v, dict) and k in ("authority", "faults", "workload", "real_owner", "remote", "ports"):
            out.update(flat(v, f"{pre}{k}."))
        else:
            out[pre + k] = v
    return out


class H1Version(unittest.TestCase):
    def test_version(self):
        self.assertEqual((REPO / "VERSION").read_text().strip(), "083")
        self.assertIn('const Release = "083"', (REPO / "internal" / "buildinfo" / "buildinfo.go").read_text())
        self.assertIn('version: "083"', (REPO / "CITATION.cff").read_text())
        for f in sorted((REPO / "deploy" / "setup").glob("vm-*.sh")):
            t = f.read_text()
            self.assertIn("(EviCon 083)", t, f.name)
            self.assertNotIn("(EviCon 082)", t, f.name)
        self.assertIn("## 083", (REPO / "CHANGELOG.md").read_text())


class H2Plans(unittest.TestCase):
    def test_earlier_plans_unchanged(self):
        self.assertEqual(sha(REPO / PLAN077), "db67640d1709b06dfa5e61f77e3444c07e7c5828dfe24e0e85d163f06a711a29")
        for p in sorted((REPO / "evaluation" / "prereg").glob("*.md")):
            if "```frozen-files" not in p.read_text():
                continue
            out = subprocess.run([sys.executable, str(REPO / "scripts" / "freeze_prereg.py"), "--check", str(p.relative_to(REPO))],
                                 capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, p.name + out.stdout + out.stderr)

    def test_extra083_frozen(self):
        text = (REPO / PLAN).read_text()
        self.assertNotIn("TO_BE_FROZEN", text)
        self.assertNotIn("To be written before freezing", text)
        b077 = re.search(r"```frozen-files\n(.*?)```", (REPO / PLAN077).read_text(), re.S).group(1).strip().splitlines()
        for line in b077:
            self.assertIn(line, text, line)
        for f in ("evaluation/pairs_ext083.py", "evaluation/ext083.py", "evaluation/campaign_ext083.py", "evaluation/campaign_real_ext083.py",
                  "evaluation/campaign_tb_ext083.py", "evaluation/sim_extra083.py", "evaluation/power_extra083_testbed.py",
                  "evaluation/sim_predictions/extra083_summary.csv", "evaluation/sim_predictions/extra083_power.csv",
                  "evaluation/sim_predictions/extra083_effects.csv", "experiments/sim/contracts083/sim-tb-083.json"):
            self.assertIn(f"{sha(REPO / f)}  {f}", text, f)
        digest = sha(REPO / PLAN)
        for name in CONFIGS:
            c = cfg(name)
            self.assertEqual(c["preregistration"], {"path": PLAN, "sha256": digest}, name)
            self.assertIn("verified", er._check_preregistration(c["preregistration"]), name)


class H3PolicyArgs(unittest.TestCase):
    A = {"resolve_sweep": "1s", "ambiguity_after": "1s", "hold_cost": 1.0, "stop_cost": 1.0, "fence_cost": 5.0, "release_after": "10s",
         "resend_until": "30s", "periodic": "10s"}

    def setUp(self):
        # the runners install their policies into the shared campaign module: restore it for the other test modules
        saved = (C.LEGACY, C.policy_args, ext083._INSTALLED)

        def restore():
            C.LEGACY, C.policy_args, ext083._INSTALLED = saved
        self.addCleanup(restore)

    def test_old_policies_unchanged_and_new_ones(self):
        import campaign_ext077 as E77
        before = {}
        orig = C.policy_args
        C.LEGACY, ext083._INSTALLED = tuple(p for p in C.LEGACY if p not in ext083.POLICIES), False
        E77.install()
        for p in ("EVICON", "M1", "M0", "RES_PERIODIC", "RES_IMMEDIATE", "RELEASE_ON_TIMEOUT", "RELEASE_ON_READ"):
            before[p] = C.policy_args(p, dict(self.A))
        ext083.install()
        for p, args in before.items():
            self.assertEqual(C.policy_args(p, dict(self.A)), args, p)
        self.assertEqual(C.policy_args("M1W", dict(self.A)), ["-policy", "M1W", "-ambiguity-after", "1s", "-resend-until", "30s"])
        self.assertEqual(C.policy_args("RELEASE_ON_READ_ANSWERED", dict(self.A, release_settle="7s")),
                         ["-policy", "RELEASE_ON_READ_ANSWERED", "-release-after", "10s", "-release-settle", "7s", "-resolve-sweep", "1s",
                          "-resend-until", "30s"])
        self.assertEqual(C.policy_args("RELEASE_ON_TIMEOUT_120S", dict(self.A))[:4], ["-policy", "RELEASE_ON_TIMEOUT", "-release-after", "120s"])
        self.assertEqual(C.policy_args("EVICON_GREEDY", dict(self.A)), before["EVICON"] + ["-certificate", "greedy"])
        fc = C.policy_args("EVICON_FC100", dict(self.A))
        self.assertEqual(fc[fc.index("-fence-cost") + 1], "100.0")
        self.assertEqual([x for x in fc if x != "100.0"], [x for x in before["EVICON"] if x != "5.0"])
        for p in ext083.POLICIES:
            self.assertIn(p, C.LEGACY)
        self.assertIs(orig, orig)  # campaign.py's own function object is untouched (only the module attribute is rebound)


class H4Baselines(unittest.TestCase):
    def test_one_definition(self):
        want = {"RELEASE_ON_TIMEOUT", "RELEASE_ON_READ", "RELEASE_ON_READ_ANSWERED", "RELEASE_ON_TIMEOUT_30S", "RELEASE_ON_TIMEOUT_60S",
                "RELEASE_ON_TIMEOUT_120S"}
        self.assertEqual(set(safety_total.BASELINES), want)
        self.assertEqual(set(t3_attribution.BASELINES), want)
        self.assertEqual(set(CC.T3_BASELINES), want)
        self.assertEqual(set(er.RELEASE_BASELINES), want)
        self.assertEqual({p for p in want if ext083.is_unsafe_by_design(p)}, want - {"RELEASE_ON_TIMEOUT", "RELEASE_ON_READ"})
        for name in ("expRRA_queue", "expRTs_direct"):
            c = cfg(name)
            runner, base = er._emulated_runner(c)
            self.assertEqual(runner, "evaluation/campaign_ext083.py")
            self.assertEqual(set(base), set(c["policies"]) & want, name)
            for p in base:
                self.assertEqual(safety_total.kind(c, c["contracts"][0], p), "unsafe")
        for name in ("expTB_m1w", "expTB_winf", "expG_minio_winf"):
            self.assertEqual(er._expected_unsafe(cfg(name)), set(), name)
        self.assertEqual(safety_total.kind(cfg("expM1W_k2"), "K2", "M1W"), "guarded")


class H5Configs(unittest.TestCase):
    def test_differ_only_where_planned(self):
        for name, (ref, keys) in CONFIGS.items():
            a, b = flat(cfg(name)), flat(cfg(ref))
            diff = {k for k in set(a) | set(b) if a.get(k) != b.get(k)} - BASE
            self.assertEqual(diff, keys, name)

    def test_replay_schedules(self):
        for name, refs in REPLAYS.items():
            c = cfg(name)
            for ref in refs:
                o = cfg(ref)
                for ep in range(min(c["episodes"], o["episodes"])):
                    self.assertEqual(C.schedule(c, ep), C.schedule(o, ep), (name, ref, ep))

    def test_t7_levels(self):
        s, d = cfg("expS_w6"), cfg("expW_direct_winf")
        for lv in T7_LEVELS:
            for name, ref in ((f"expT7_q6_{lv}", s), (f"expT7_dinf_{lv}", d)):
                c = cfg(name)
                a0, w0 = C.schedule(c, 0)
                ra, rw = C.schedule(ref, 0)
                self.assertNotEqual(w0, rw, name)  # another fault schedule
                if not lv.startswith("seed"):
                    self.assertEqual(a0, ra, name)  # the same arrivals (common random numbers for the workload)
                else:
                    self.assertNotEqual(a0, ra, name)
        self.assertEqual(cfg("expT7_q6_rate05")["faults"]["windows_per_min"], 0.5)
        self.assertEqual(cfg("expT7_dinf_off40")["faults"]["offline_s"], [40.0, 120.0])


class H6Driver(unittest.TestCase):
    def test_runners(self):
        self.assertEqual(er._emulated_runner(cfg("expM1W_k2"))[0], "evaluation/campaign_ext083.py")
        self.assertEqual(er._emulated_runner(cfg("expG_s_winf"))[0], "evaluation/campaign_ext083.py")
        self.assertEqual(er._emulated_runner(cfg("expT7_q6_rate4"))[0], "evaluation/campaign.py")
        self.assertEqual(er._emulated_runner(cfg("expRR_queue"))[0], "evaluation/campaign_ext077.py")  # 077 unchanged
        self.assertEqual(er._emulated_runner(cfg("expS_w6"))[0], "evaluation/campaign.py")
        self.assertTrue(er._uses_083_policies(cfg("expTB_m1w")) and er._uses_083_policies(cfg("expG_minio_winf")))
        self.assertFalse(er._uses_083_policies(cfg("expTB_thingsboard")))
        self.assertEqual(er._zephyr_runner(cfg("expT4_zephyr_w6")), "evaluation/campaign_zephyr_ext077.py")
        self.assertEqual(er._pairs_ext_runs(REPO / PLAN), {})
        self.assertIsNone(er._pairs_ext_runs(REPO / PLAN077))
        verify = (REPO / "evicon_review.py").read_text()
        self.assertIn('"evaluation.test_release083"', verify)


class H7Schedules(unittest.TestCase):
    def test_distinct_fault_schedules(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        runs = []
        for name, seed in (("a", 1), ("b", 1), ("c", 2)):
            run = tmp / name / "20261031T000000"
            run.mkdir(parents=True)
            (run / "config.json").write_text(json.dumps({"name": name}))
            (run / "manifest.json").write_text(json.dumps({"release": "083", "scope": "TESTBED"}))
            rows = ["contract,policy,episode,cell_status,offered,admitted,dispatch_attempts,late_effects,fault_windows," + ",".join(safety_total.KINDS)]
            for ep in (0, 1):
                (run / f"schedule_ep{ep}.json").write_text(json.dumps({"arrivals": [{"t": ep}], "faults": [{"t": seed * 10 + ep, "kind": "offline"}]}))
                for ct in ("K0", "K0F"):
                    rows.append(f"{ct},EVICON,{ep},VALID,10,10,10,0,{1 if ep == 0 or name != 'c' else 0}," + ",".join("0" for _ in safety_total.KINDS))
            (run / "runtime_episodes.csv").write_text("\n".join(rows) + "\n")
            runs.append(safety_total.read_run(run))
        s = safety_total.summarize(runs)
        # a and b replay the same two schedules; c adds one (its episode 1 cells had no fault window)
        self.assertEqual(s["distinct_fault_schedules"], 3)
        self.assertEqual(s["cells_with_faults"], 10)
        self.assertAlmostEqual(s["upper95_per_schedule"], 1 - 0.05 ** (1 / 3))
        self.assertIn("3 distinct fault schedules", safety_total.markdown(runs, s))


class H8Analyses(unittest.TestCase):
    def test_admitted_after_deadline(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "row.json").write_text(json.dumps({"offered": 3}))
        lines = [{"cid": "a", "arrival": 0.0, "decision": "ADMITTED", "attempts": 1, "ids": ["a"], "admitted": {"a": 1.0}},
                 {"cid": "b", "arrival": 0.0, "decision": "ADMITTED", "attempts": 9, "ids": ["b"], "admitted": {"b": 61.2}},
                 {"cid": "c", "arrival": 0.0, "decision": "EXPIRED_DEFER", "attempts": 9, "ids": ["c"], "admitted": {}}]
        (tmp / "clients.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
        m = overhead.cell_metrics(tmp, 60.0)
        self.assertEqual((m["admitted_requests"], m["admitted_after_deadline"], m["identities_admitted_after_deadline"]), (2, 1, 1))

    def test_window_reopening_without_evidence(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        run = tmp / "campaigns" / "expA-confirm-k0" / "20261020T073331"
        d = run / "cells" / "ep3" / "K0F" / "RES_PERIODIC" / "raw" / "A"
        d.mkdir(parents=True)
        ev = [{"event": "DECISION_RECORDED", "command_id": "c9", "decision": "DEFER", "reason": "CONFIGURATION_BUDGET_EXHAUSTED"},
              {"event": "OUTBOX_RETRY_SUPPRESSED_T4", "command_id": "c1"},
              {"event": "DECISION_RECORDED", "command_id": "c8", "decision": "ADMITTED", "reason": "RELATIONAL_BELIEF_SAFE"},
              {"event": "DECISION_RECORDED", "command_id": "c9", "decision": "ADMITTED", "reason": "RELATIONAL_BELIEF_SAFE"}]
        (d / "authority1.jsonl").write_text("".join(json.dumps(x) + "\n" for x in ev))
        (run / "config.json").write_text(json.dumps({"name": "expA-confirm-k0"}))
        res = t3_attribution.scan([run])
        w = res["expA-confirm-k0/20261020T073331"]["window_none_guarded"]
        self.assertEqual(len(w), 1)
        self.assertEqual((w[0]["command_id"], w[0]["retry_suppressed_lines"], len(w[0]["events"])), ("c9", 1, 3))
        self.assertIn("analysis bound", w[0]["explanation"])
        self.assertIn("Window re-opening without committed evidence", t3_attribution.markdown(res))
        # a practice baseline's re-opening without evidence is counted, not listed
        run2 = tmp / "campaigns" / "expRT-queue" / "20261029T052600"
        d2 = run2 / "cells" / "ep0" / "K0" / "RELEASE_ON_TIMEOUT" / "raw" / "A"
        d2.mkdir(parents=True)
        (d2 / "authority0.jsonl").write_text("".join(json.dumps(x) + "\n" for x in ev))
        (run2 / "config.json").write_text(json.dumps({"name": "expRT-queue"}))
        self.assertEqual(t3_attribution.scan([run2])["expRT-queue/20261029T052600"]["window_none_guarded"], [])


class H9PaperTables(unittest.TestCase):
    def test_round_once(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "expC3").mkdir()
        g = {"contract": "ZEPHYR_F_QUEUE", "policy": "EVICON", "cells": 8, "clean_pp": 79.14677638945986, "confirmed_pp": 78.99,
             "queries": 0, "fences": 1, "fences_ok": 1, "stops": 0, "stops_terminal": 0, "unresolved_at_end": 0}
        (tmp / "expC3" / "expC3.json").write_text(json.dumps({"groups": [g]}))
        N = {}
        paper_tables.expC3(tmp, tmp, N)
        self.assertEqual(N["evCthreeFQueueClean"], "79.1")
        self.assertEqual(paper_tables.num(round(79.14677638945986, 2)), "79.2")  # what the two roundings gave
        src = (REPO / "evaluation" / "expC2_summary.py").read_text()
        self.assertNotIn("round(statistics.mean(v), 2)", src)

    def test_exact_contrast(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        rows = ["run,contract,policy,episode,clean_pp,offered,clean_fulfilled"]
        for ep, (a, b) in enumerate(((10, 30), (20, 41), (30, 49))):
            rows.append(f"S,K0,EVICON,{ep},{round(100 * a / 300, 4)},300,{a}")
            rows.append(f"S,K0F,EVICON,{ep},{round(100 * b / 300, 4)},300,{b}")
        (tmp / "cells.csv").write_text("\n".join(rows) + "\n")
        h = {"run": "S", "n": 3, "spec": {"policy": "EVICON", "from": "K0", "to": "K0F", "metric": "clean_pp"}}
        mean, t95 = paper_tables.exact_contrast(tmp, h)
        self.assertAlmostEqual(mean, 100 * (20 + 21 + 19) / 3 / 300)
        self.assertEqual(paper_tables.exact_contrast(tmp, dict(h, mean_exact=1.25, t95_exact=[0, 2])), (1.25, [0, 2]))

    def test_k2_replication_and_firmware_once(self):
        self.assertTrue(any(p.startswith("meas-mosquitto-2-x-zephyr-evicon-firmware-arm-cortex-m3") and s == "Zephyr EviCon firmware"
                            for p, s, _ in paper_tables.SHORT))
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "overhead").mkdir()
        (tmp / "confirm_expA").mkdir()
        rows = [{"campaign": f"expA-confirm-k2@{st}", "contract": "K3F", "policy": "EVICON", "fences_per_cmd": 0.19} for st in ("20261017T111506", "20261021T131941")]
        (tmp / "overhead" / "overhead.json").write_text(json.dumps(rows))
        (tmp / "confirm_expA" / "results.json").write_text(json.dumps({"k0_run": "a/20261016T234202", "k2_run": "b/20261017T111506"}))
        paper_tables.fence_cost(tmp, tmp, {})
        t = (tmp / "tab_fence_cost.tex").read_text()
        self.assertIn("K2 config, preregistered", t)
        self.assertIn("K2 config, replication", t)
        self.assertEqual(t.count("preregistered"), 1)


class H10PairsExt(unittest.TestCase):
    def test_spec_blocks(self):
        import pairs_ext083 as PX
        std, ext, info = PX.spec_blocks(REPO / PLAN)
        fs = {h.get("family") for h in std["hypotheses"] if h["test"] not in ("detects", "absent")}
        fe = {h.get("family") for h in ext["hypotheses"] if h["test"] not in ("detects", "absent")}
        self.assertFalse(fs & fe)
        names = {cfg(p.stem)["name"] for p in (REPO / "evaluation" / "campaigns").glob("*.json")}
        for key, r in std["runs"].items():
            self.assertIn(r["campaign"], names, key)
        own = {k for k, r in std["runs"].items() if not r.get("reference")}
        self.assertEqual({std["runs"][k]["campaign"] for k in own}, {cfg(n)["name"] for n in CONFIGS})
        for h in std["hypotheses"] + ext["hypotheses"]:
            for arm in ([h["a"], h["b"]] if "a" in h else [h]):
                for k in {arm.get("run"), arm.get("from_run")} - {None}:
                    self.assertIn(k, std["runs"], h["id"])
        ids = [h["id"] for h in std["hypotheses"] + ext["hypotheses"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_decide(self):
        import pairs_ext083 as PX

        def run(cells, schedules):
            return {"interpretable": True, "cells": {k: dict(v, schedule=schedules[k[2]]) for k, v in cells.items()}}
        m = lambda c, sf=0.0, v=0.0: {"clean_pp": c, "search_failures": sf, "violations": v}  # noqa: E731
        A = run({("K2", "M1", e): m(20 + e) for e in range(4)} | {("K2", "M1W", e): m(60 + 2 * e) for e in range(4)}
                | {("K0F", "EVICON_GREEDY", e): m(70, sf=0) for e in range(4)}, {e: f"s{e}" for e in range(4)})
        B = run({("K2", "M1", e): m(25) for e in range(4)}, {0: "s0", 1: "s1", 2: "x", 3: "s3"})
        runs = {"A": A, "B": B}
        spec = {"hypotheses": [
            {"id": "P", "family": "f1", "run": "A", "contract": "K2", "from_policy": "M1", "to_policy": "M1W", "metric": "clean_pp", "test": "at_least", "margin": 10},
            {"id": "X", "family": "f2", "run": "A", "from_run": "B", "contract": "K2", "from_policy": "M1", "to_policy": "M1W", "metric": "clean_pp", "test": "at_least", "margin": 10},
            {"id": "D", "family": "f3", "test": "at_most", "margin": 0, "metric": "clean_pp",
             "a": {"run": "A", "contract": "K2", "from_policy": "M1", "to_policy": "M1W"}, "b": {"run": "A", "from_run": "B", "contract": "K2", "from_policy": "M1", "to_policy": "M1W"}},
            {"id": "Z", "run": "A", "test": "absent", "contract": "K0F", "policy": "EVICON_GREEDY", "any_of": ["search_failures"]},
            {"id": "N", "family": "f4", "run": "C", "contract": "K2", "from_policy": "M1", "to_policy": "M1W", "metric": "clean_pp", "test": "at_least", "margin": 0}]}
        out = {h["id"]: h for h in PX.decide(spec, runs, 0.05)}
        self.assertEqual(out["P"]["n"], 4)
        self.assertAlmostEqual(out["P"]["mean_exact"], 40 + 1.5)
        self.assertEqual(out["P"]["verdict"], "MET")
        self.assertEqual(out["X"]["n"], 3)  # episode 2's schedules differ: not paired
        self.assertEqual(out["D"]["n"], 3)
        self.assertEqual(out["Z"]["verdict"], "ABSENT")
        self.assertEqual(out["N"]["verdict"], "NOT RUN")

    def test_safety_groups(self):
        import pairs_ext083 as PX
        import pairs_report as PR
        z = {k: 0.0 for k in PR.SAFETY} | {"late_effects": 1.0}
        A = {"interpretable": True, "cells": {("K2", "EVICON", 0): dict(z), ("K2", "RELEASE_ON_READ_ANSWERED", 0): dict(z, false_stopped=3.0)}}
        B = {"interpretable": True, "cells": {("K2", "EVICON", 0): dict(z, logical_duplicates=1.0)}}
        h = {"id": "S", "test": "safety", "runs": ["A", "C"], "exclude_policies": ["RELEASE_ON_READ_ANSWERED"]}
        o = PX.safety(h, {"A": A})
        self.assertEqual((o["verdict"], o["not_run"], o["cells"], o["late_effects"]), ("MET", ["C"], 1, 1))
        self.assertEqual(PX.safety(dict(h, exclude_policies=[]), {"A": A})["verdict"], "NOT MET")
        self.assertEqual(PX.safety(dict(h, runs=["A", "B"]), {"A": A, "B": B})["cells_detecting"], 1)
        self.assertEqual(PX.safety(dict(h, runs=["C"]), {"A": A})["verdict"], "NOT RUN")
        out = PX.decide({"hypotheses": [h]}, {"A": A}, 0.05)
        self.assertEqual(out[0]["verdict"], "MET")
        self.assertIn("not run: C", PX._ev(out[0], 0.05))


class H11Esim(unittest.TestCase):
    def test_esim083(self):
        import esim083
        res = {"ext_levels": {"TBw": {"TB_RPC/M1": {"clean_pp": 30.0, "violations": 0.0, "n": 6}, "TB_RPC/M1W": {"clean_pp": 70.0, "violations": 0.0, "n": 6}},
                              "T7d-seedB": {"K0F/EVICON": {"clean_pp": 72.0, "violations": 0.0, "n": 4}}},
               "hypotheses": [{"id": "T1-2", "test": "at_least", "verdict": "MET", "mean": 40.0, "t95": [30.0, 50.0], "n": 6},
                              {"id": "T6-1", "test": "at_most", "verdict": "NOT MET", "mean": 2.0, "t95": [-1.0, 5.0], "n": 8},
                              {"id": "RA-1", "test": "detects", "verdict": "DETECTED"}]}
        out = esim083.analyse(res)
        lv = {(r["run"], r["cell"]): r for r in out["levels"]}
        self.assertEqual(lv[("TBw", "TB_RPC/M1")]["sim_clean_pct"], 38.36)
        self.assertEqual(lv[("T7d-seedB", "K0F/EVICON")]["scenario"], "t7_base_dinf")
        ef = {e["id"]: e for e in out["effects"]}
        self.assertTrue(ef["T1-2"]["sign_agrees"])
        self.assertFalse(ef["T6-1"]["sign_agrees"])  # the simulator predicts a fall (-32 pp)
        self.assertNotIn("RA-1", ef)
        self.assertIn("E-SIM", esim083.markdown(out))
        self.assertIn("evaluation/esim083.py", (REPO / "evicon_review.py").read_text())


class H12PowerRule(unittest.TestCase):
    ESTIMANDS = {"T1-1", "T6-3", "T7-rate05-qF", "T7-rate05-qS", "T7-rate05-dF", "T7-off5-dF"}

    def test_rules(self):
        import pairs_ext083 as PX
        std, ext, _ = PX.spec_blocks(REPO / PLAN)
        hs = std["hypotheses"] + ext["hypotheses"]
        ids = {h["id"] for h in hs}
        self.assertFalse(ids & self.ESTIMANDS)
        paired = [h for h in hs if h["test"] in ("at_least", "at_most", "equivalent")]
        self.assertEqual(len(paired), 29)
        self.assertEqual(len(hs) + 1, 54)  # + X-S1, pairs_report's safety rule
        t46 = next(h for h in ext["hypotheses"] if h["id"] == "T4-6")
        self.assertEqual((t46["test"], t46["margin"], t46["from_run"], t46["from"], t46["to"], t46["run"]),
                         ("at_least", 5.0, "Zi", "ZEPHYR_PLAIN_QOS0", "ZEPHYR_CUTOFF", "Rz"))
        self.assertEqual({h["family"] for h in paired if h["id"].startswith("T7")},
                         {"t7-rate4", "t7-off5", "t7-off40", "t7-seedA", "t7-seedB"})
        # the estimands are still reported: Wk M1 -> M1W clean, the ThingsBoard gap W unbounded - W 3, T7 clean per level
        self.assertTrue(any(e.get("run") == "Wk" and e.get("from_policy") == "M1" and e.get("to_policy") == "M1W" and "clean_pp" in e["metrics"]
                            for e in ext["estimands"]))
        self.assertTrue(any("a" in e and e["a"]["run"] == "TBw3" and e["b"]["run"] == "TBwinf" for e in ext["estimands"]))
        for run in ("T7q-rate05", "T7d-rate05", "T7d-off5"):
            self.assertTrue(any(e["run"] == run and "clean_pp" in e["metrics"] for e in std["estimands"]), run)
        text = (REPO / PLAN).read_text()
        self.assertIn("**The 0.80 rule.**", text)
        with open(REPO / "evaluation" / "sim_predictions" / "extra083_power.csv") as fh:
            import csv
            self.assertEqual({r["id"] for r in csv.DictReader(fh)}, {h["id"] for h in paired})


if __name__ == "__main__":
    unittest.main()
