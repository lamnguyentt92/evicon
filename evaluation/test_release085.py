"""Release 085: the experiments of PREREG_extra_085.md (EVICON and PERIODIC_FULL in the same campaigns; the ThingsBoard RPC
checks), the fence pause under rule R5', -expires-after as transport metadata, the TLC boundary cases and the report analyses
for the reviewer of 4 November.

K1  the release is 085 everywhere (VERSION, buildinfo, CITATION.cff, the VM set-up banners, CHANGELOG)
K2  every earlier plan is byte-identical and still verifies (077 db67640d..., 083 6e3f24a9..., 084 b1135164...); the 085 plan is
    frozen with every file the 084 plan froze (same SHA-256), the 084 plan itself and its own runners, check module, check device
    and power files; every 085 config carries its SHA-256 and passes the driver's check
K3  the fence pause ends at BARRIER_PER_IDENTITY_COMMITTED too (synthetic trace): pauses, end events, open pauses, the exposure,
    trace lines that are not JSON, the rounded-index percentile
K4  policy_order: the frozen run_campaign of every runner changes in the policy loop only; without the key the config's order;
    alternate_by_episode reverses odd episodes; a bad value is refused
K5  each 085 config differs from the 084 PF config it copies only where the plan says, and replays the schedules of the PF run and
    of the reference run byte for byte through campaign.schedule
K6  the driver: the 085 runners for configs with policy_order (the 084 configs keep theirs), the verify list, the tbcheck command,
    the formal stage with the timing models, the report's E-PM, ThingsBoard checks, report analyses and earlier-verdict check
K7  the plan's blocks: families disjoint, every run named, 4 paired-difference hypotheses (the 0.80 rule: power file), 8 absence
    rules, the safety rule, 5 checks
K8  pm085: the break-even arithmetic and its bootstrap, the same-contract check from the cells' gateway logs, the order of the cells
K9  verdicts085: identical, a differing verdict and a missing hypothesis are found
K10 claims_check: TB-1 to TB-4 from tbcheck.json; a stand-in run is marked; NOT RUN when the plan has results but no check ran
K11 paper_tables: the macros of E-PM, the checks, the fence pause, the window interval, the offline bound and the safety split
K12 safety_total: the guarded cells with faults split by transport premise (Theorem 1 / dagger)
K13 review_l2_085: the window interval (paired on identical schedules, Welch otherwise), the offline bound (smallest first), t quantile
K14 TLA+: the timing models, their expected results and TLC_RESULTS_TIMING.txt agree; the model files' SHA-256 are recorded
K15 the ThingsBoard check module and device: the frozen adapter mapping and device are imported unchanged; the call shapes are the
    measured configs'; no credential in a config or on a command line
K16 -expires-after: the authority keeps the rule of releases 061-084 (moved into outbox.DispatchExpiry); no new flag
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
import ext085  # noqa: E402

PLAN = "evaluation/prereg/PREREG_extra_085.md"
PLAN084 = "evaluation/prereg/PREREG_extra_084.md"
SAFETY = ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]
CONFIGS = {"expPM_zephyr_w6": ("expPF_zephyr_w6", "expC2_zephyr"), "expPM_zephyr_winf": ("expPF_zephyr_winf", "expR_zephyr_winf"),
           "expPM_minio_w6": ("expPF_minio_w6", "expC_minio"), "expPM_minio_winf": ("expPF_minio_winf", "expR_minio_winf")}
BASE = {"name", "regime", "notes", "preregistration"}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def cfg(name: str) -> dict:
    return json.loads((REPO / "evaluation" / "campaigns" / f"{name}.json").read_text())


def tmpdir(tc) -> Path:
    t = Path(tempfile.mkdtemp())
    tc.addCleanup(shutil.rmtree, t, ignore_errors=True)
    return t


def sim_only() -> bool:
    return cfg("expPM_zephyr_w6")["notes"].startswith("SIM-ONLY")


class K1Version(unittest.TestCase):
    def test_version(self):
        v = (REPO / "VERSION").read_text().strip()
        self.assertGreaterEqual(v, "085")
        self.assertIn(f'const Release = "{v}"', (REPO / "internal" / "buildinfo" / "buildinfo.go").read_text())
        self.assertIn(f'version: "{v}"', (REPO / "CITATION.cff").read_text())
        for f in sorted((REPO / "deploy" / "setup").glob("vm-*.sh")):
            self.assertIn(f"(EviCon {v})", f.read_text(), f.name)
            self.assertNotIn("(EviCon 084)", f.read_text(), f.name)
        self.assertIn("## 085", (REPO / "CHANGELOG.md").read_text())


class K2Plans(unittest.TestCase):
    def test_earlier_plans(self):
        self.assertEqual(sha(REPO / "evaluation/prereg/PREREG_review_077.md"), "db67640d1709b06dfa5e61f77e3444c07e7c5828dfe24e0e85d163f06a711a29")
        self.assertEqual(sha(REPO / "evaluation/prereg/PREREG_extra_083.md"), "6e3f24a918de659c1feb8aaa886d9e9681195353528b5df10e615e8897ff3a06")
        self.assertEqual(sha(REPO / PLAN084), "b11351645c1e0f05f4a270412539a072f17148ea8607223dcb6182a171505345")
        for p in sorted((REPO / "evaluation" / "prereg").glob("*.md")):
            if "```frozen-files" not in p.read_text():
                continue
            out = subprocess.run([sys.executable, str(REPO / "scripts" / "freeze_prereg.py"), "--check", str(p.relative_to(REPO))],
                                 capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, p.name + out.stdout + out.stderr)

    def test_extra085_frozen(self):
        text = (REPO / PLAN).read_text()
        for marker in ("TO_BE_FROZEN", "PRE_RELEASE_CHECKS"):
            self.assertNotIn(marker, text)
        b084 = re.search(r"```frozen-files\n(.*?)```", (REPO / PLAN084).read_text(), re.S).group(1).strip().splitlines()
        for line in b084:
            self.assertIn(line, text, line)
        for f in (PLAN084, "evaluation/ext085.py", "evaluation/campaign_zephyr_ext085.py", "evaluation/campaign_real_ext085.py",
                  "conformance/tb_rpc_checks.py", "conformance/reference/mqtt_device_tbcheck.py", "evaluation/power_extra085_testbed.py",
                  "evaluation/sim_predictions/extra085_templates.csv", "evaluation/sim_predictions/extra085_power.csv"):
            self.assertIn(f"{sha(REPO / f)}  {f}", text, f)
        digest = sha(REPO / PLAN)
        for name in CONFIGS:
            c = cfg(name)
            self.assertEqual(c["preregistration"], {"path": PLAN, "sha256": digest}, name)
            self.assertIn("verified", er._check_preregistration(c["preregistration"]), name)


def _trace_cell(root: Path) -> Path:
    cell = root / "campaigns" / "expFI-s6" / "20261105T000000" / "cells" / "ep0" / "K0F" / "EVICON_FI"
    (cell / "raw" / "A").mkdir(parents=True)
    (cell / "row.json").write_text(json.dumps({"offered": 10, "fences": 4, "queries": 0, "stops": 0, "duration_s": 300}))
    ms = 1_000_000
    ev = [(0, "ADMISSION_PREPARE", None), (100, "BARRIER_PAUSE_COMMITTED", "t1"), (1100, "BARRIER_PER_IDENTITY_COMMITTED", "t1:OWNER_UNCERTAIN:d0-c3"),
          (2000, "BARRIER_PAUSE_COMMITTED", "t2"), (2500, "BARRIER_ANCHOR_COMMITTED", "t2"), (3000, "BARRIER_PAUSE_COMMITTED", "t3"),
          (3020, "BARRIER_ABORTED", "t3:BARRIER_UNCERTAIN_RECEIPTS"), (9000, "BARRIER_PAUSE_COMMITTED", "t4"), (10000, "DECISION_RECORDED", None)]
    lines = [json.dumps({"monotonic_ns": t * ms, "event": e, **({"reason": r} if r else {})}) for t, e, r in ev]
    lines.insert(3, "{not json")
    (cell / "raw" / "A" / "authority0.jsonl").write_text("\n".join(lines) + "\n")
    return cell


class K3FencePause(unittest.TestCase):
    def test_per_identity_end(self):
        import overhead as OV
        cell = _trace_cell(tmpdir(self))
        m = OV.cell_metrics(cell)
        self.assertEqual(sorted(m["fence_pause_ms"]), [20.0, 500.0, 1000.0])
        self.assertEqual(m["fence_pause_ends"], {"BARRIER_PER_IDENTITY_COMMITTED": 1, "BARRIER_ANCHOR_COMMITTED": 1, "BARRIER_ABORTED": 1})
        self.assertEqual(m["fence_pause_open"], 1)
        self.assertAlmostEqual(m["paused_ms"], 1520.0 + 1000.0)  # t4 open: to the last event (10000)
        self.assertAlmostEqual(m["cell_ms"], 10000.0)
        self.assertEqual(m["trace_parse_errors"], 1)
        rows = OV.summarize([cell.parents[3]], [])
        r = rows[0]
        self.assertEqual((r["fence_pause_n"], r["fence_pause_open"], r["cells_with_traces"], r["trace_parse_errors"]), (3, 1, 1, 1))
        self.assertEqual(r["fence_pause_ms_p50"], 500.0)
        self.assertAlmostEqual(r["paused_fraction"], 0.252)
        self.assertEqual(OV.pct([1, 2, 3, 4], 50), 3)  # rounded index: round(1.5) = 2 (half to even) -> the 3rd value, not the mean
        self.assertIn("BARRIER_PER_IDENTITY_COMMITTED", OV.FENCE_PAUSE_END)
        self.assertIn("Fence pause (release 085)", OV.markdown(rows))


class K4PolicyOrder(unittest.TestCase):
    def test_order(self):
        import inspect
        import campaign_real as CR
        import campaign_zephyr as Z
        for mod in (C, CR, Z):
            if getattr(mod, "_RUN_CAMPAIGN_085", False):
                continue
            orig = inspect.getsource(mod.run_campaign)
            new = ext085.ordered_source(mod)
            self.assertEqual(new.replace(ext085.LOOP_085, ext085.LOOP), orig, mod.__name__)
            self.assertEqual(sum(1 for a, b in zip(orig.splitlines(), new.splitlines()) if a != b), 1, mod.__name__)
        c = {"policies": ["EVICON", "PERIODIC_FULL"], "episodes": 4}
        self.assertEqual([ext085.policy_order(c, e) for e in range(3)], [["EVICON", "PERIODIC_FULL"]] * 3)
        c["policy_order"] = "alternate_by_episode"
        self.assertEqual([ext085.policy_order(c, e)[0] for e in range(4)], ["EVICON", "PERIODIC_FULL", "EVICON", "PERIODIC_FULL"])
        with self.assertRaises(ValueError):
            ext085.policy_order(dict(c, policy_order="random"), 0)
        t = tmpdir(self)
        (t / "manifest.json").write_text("{}")
        ext085.mark_manifest(t, "R", c)
        m = json.loads((t / "manifest.json").read_text())
        self.assertEqual(m["policy_order"]["episodes"]["1"], ["PERIODIC_FULL", "EVICON"])
        for f in ("campaign_zephyr_ext085.py", "campaign_real_ext085.py"):
            src = (REPO / "evaluation" / f).read_text()
            self.assertIn("ext085.install_order(", src)
            self.assertIn("_ext084", src)


class K5Configs(unittest.TestCase):
    def setUp(self):
        if sim_only():
            self.skipTest("SIM-ONLY regression copy: shortened configs")

    def test_differ_only_where_planned(self):
        from test_release084 import flat
        for name, (pf, _) in CONFIGS.items():
            a, b = flat(cfg(name)), flat(cfg(pf))
            diff = {k for k in set(a) | set(b) if a.get(k) != b.get(k)} - BASE
            want = {"policies", "policy_order"} | ({"activation_gate"} if "zephyr" in name else set())
            self.assertEqual(diff, want, name)
            c = cfg(name)
            self.assertEqual((c["policies"], c["policy_order"]), (["EVICON", "PERIODIC_FULL"], "alternate_by_episode"))
            if "zephyr" in name:
                self.assertEqual(c["activation_gate"]["policies"], ["EVICON", "PERIODIC_FULL"])
                z = c["real_owner"]["contracts"]["ZEPHYR_CUTOFF"]
                self.assertEqual((z["mode"], z["channel"], z["binding"]), ("CUTOFF", "queue", "auto"))
            else:
                self.assertNotIn("activation_gate", c)

    def test_replay_schedules(self):
        for name, (pf, ref) in CONFIGS.items():
            c = cfg(name)
            for other in (pf, ref):
                o = cfg(other)
                for ep in range(c["episodes"]):
                    self.assertEqual(C.schedule(c, ep), C.schedule(o, ep), (name, other, ep))


class K6Driver(unittest.TestCase):
    def test_runners_and_stages(self):
        self.assertEqual(er._zephyr_runner(cfg("expPM_zephyr_w6")), "evaluation/campaign_zephyr_ext085.py")
        self.assertEqual(er._zephyr_runner(cfg("expPF_zephyr_w6")), "evaluation/campaign_zephyr_ext084.py")  # 084 unchanged
        self.assertTrue(er._uses_085_options(cfg("expPM_minio_w6")))
        self.assertFalse(er._uses_085_options(cfg("expPF_minio_w6")))
        with self.assertRaises(er.StageFail):
            er._emulated_runner(dict(cfg("expFI_s6"), policy_order="alternate_by_episode"))
        src = (REPO / "evicon_review.py").read_text()
        for s in ('"evaluation.test_release085"', '"evaluation/campaign_real_ext085.py" if _uses_085_options(cfg)', "def cmd_tbcheck(",
                  '"tbcheck": cmd_tbcheck', "formal/run_tlc_timing.sh", "evaluation/pm085.py", "evaluation/review_l2_085.py",
                  "evaluation/verdicts085.py", 'glob("measure/tbcheck/tbcheck.json")', "conformance/tb_rpc_checks.py", "mqtt_device_tbcheck.py"):
            self.assertIn(s, src)

    def test_gateway_contract(self):
        t = tmpdir(self)
        a = type("A", (), {"from_run": [str(t / "old")], "archive": ""})()
        old = er.RUN
        try:
            er.RUN = type("R", (), {"path": t / "this"})()
            with self.assertRaises(er.StageFail):
                er._tb_gateway_contract(a)
            d = t / "old" / "measure" / "thingsboard" / "runs" / "thingsboard-rpc-r0" / "20261031T184402"
            d.mkdir(parents=True)
            (d / "contract.json").write_text(json.dumps({"source": "MEASURED", "capabilities": ["Q_A", "T"]}))
            self.assertEqual(er._tb_gateway_contract(a)[0], d / "contract.json")
        finally:
            er.RUN = old


class K7PlanBlocks(unittest.TestCase):
    def test_blocks(self):
        import csv
        import pairs_ext083 as PX
        std, ext, _ = PX.spec_blocks(REPO / PLAN)
        hs = std["hypotheses"] + ext["hypotheses"]
        fs = {h.get("family") for h in std["hypotheses"] if h["test"] not in ("detects", "absent")}
        fe = {h.get("family") for h in ext["hypotheses"] if h["test"] not in ("detects", "absent", "safety")}
        self.assertFalse(fs & fe)
        self.assertEqual(fe, {"pm-fw", "pm-minio"})
        names = {cfg(p.stem)["name"] for p in (REPO / "evaluation" / "campaigns").glob("*.json")}
        for key, r in std["runs"].items():
            self.assertIn(r["campaign"], names, key)
        own = {k for k, r in std["runs"].items() if not r.get("reference")}
        self.assertEqual({std["runs"][k]["campaign"] for k in own}, {cfg(n)["name"] for n in CONFIGS})
        for h in hs + ext["estimands"] + std["estimands"]:
            for k in {h.get("run"), h.get("from_run")} - {None}:
                self.assertIn(k, std["runs"], h.get("id", h.get("label")))
        paired = [h for h in hs if h["test"] == "equivalent"]
        self.assertEqual({h["id"] for h in paired}, {"PM-1", "PM-2", "PM-3", "PM-4"})
        for h in paired:
            self.assertEqual((h["from_policy"], h["to_policy"], h["margin"], h.get("from_run")), ("EVICON", "PERIODIC_FULL", 3.0, None))
        absent = [h for h in std["hypotheses"] if h["test"] == "absent"]
        self.assertEqual(len(absent), 8)
        for h in absent:
            self.assertEqual(h["any_of"], SAFETY)
        self.assertEqual(std["safety"]["id"], "PM-S")
        self.assertEqual(sorted(std["safety"]["runs"]), sorted(own))
        text = (REPO / PLAN).read_text()
        for tb in ("TB-1", "TB-2", "TB-3a", "TB-3b", "TB-4"):
            self.assertIn(f"**{tb}**", text)
        import tb_rpc_checks as K
        self.assertEqual(set(K.rules([], {"seq": 20, "cut": 10, "ack": 10, "ttl": 5})), {"TB-1", "TB-2", "TB-3a", "TB-3b", "TB-4"})
        with open(REPO / "evaluation" / "sim_predictions" / "extra085_power.csv") as fh:
            pw = [r for r in csv.DictReader(fh) if r["what"] == "primary at the prediction"]
        self.assertEqual({r["id"] for r in pw}, {"PM-1", "PM-2", "PM-3", "PM-4"})
        for r in pw:
            self.assertGreaterEqual(float(r["power"]), 0.80, r["id"])


def _run(root: Path, name: str, rows: list, cfg_: dict = None, gw: dict = None, order: list = None) -> Path:
    run = root / name / "20261105T000000"
    run.mkdir(parents=True)
    (run / "config.json").write_text(json.dumps(cfg_ or {"name": name}))
    (run / "manifest.json").write_text(json.dumps({"release": "085", "scope": "TESTBED", "finished": "x"}))
    import csv
    hdr = ["contract", "policy", "episode", "cell_status", "offered", "admitted", "clean_fulfilled", "queries", "stops", "fences",
           "mean_time_to_terminal_s", "unresolved_at_end", "late_effects", "fault_windows"] + SAFETY + ["dispatch_attempts"]
    with open(run / "runtime_episodes.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=hdr)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, 0) for k in hdr})
    for r in rows:
        cell = run / "cells" / f"ep{r['episode']}" / r["contract"] / r["policy"]
        (cell / "raw" / "C").mkdir(parents=True, exist_ok=True)
        (cell / "row.json").write_text("{}")
        if gw is not None:
            g = gw.get(r["policy"], gw.get("*"))
            (cell / "raw" / "C" / "gateway0.jsonl").write_text(json.dumps({"event": "connected"}) + "\n" + json.dumps(g) + "\n")
    if order:
        (run / "done.jsonl").write_text("".join(json.dumps({"cell": c, "status": "VALID"}) + "\n" for c in order))
    return run


class K8PM(unittest.TestCase):
    def test_pm085(self):
        import pm085
        t = tmpdir(self)
        rows = []
        for ep in range(4):
            rows.append(dict(contract="ZEPHYR_CUTOFF", policy="EVICON", episode=ep, cell_status="VALID", offered=100, admitted=80, clean_fulfilled=79,
                             queries=240 + ep, fences=16, stops=0))
            rows.append(dict(contract="ZEPHYR_CUTOFF", policy="PERIODIC_FULL", episode=ep, cell_status="VALID", offered=100, admitted=80, clean_fulfilled=79,
                             queries=40 + ep, fences=126, stops=0))
        gwrec = {"contract_sha256": "ab" * 32, "binding": "auto", "qos_cmd": 1}
        order = [f"ep{e}/ZEPHYR_CUTOFF/{p}" for e in range(4) for p in (["EVICON", "PERIODIC_FULL"] if e % 2 == 0 else ["PERIODIC_FULL", "EVICON"])]
        run = _run(t, "expPM-zephyr-w6", rows, {"name": "expPM-zephyr-w6", "policy_order": "alternate_by_episode",
                                               "real_owner": {"contracts": {"ZEPHYR_CUTOFF": {"measured_in": "measure/zephyr/x", "binding": "auto"}}}},
                   gw={"*": gwrec}, order=order)
        res = pm085.analyse({"PMz6": run}, {})
        r = res["matched"][0]
        self.assertAlmostEqual(r["break_even"]["ratio"], 200.0 / 110.0)
        self.assertEqual(r["break_even"]["evicon_cheaper_iff"], f"C_fence/C_query > {200 / 110:.3f}")
        self.assertEqual(r["break_even"]["boot_without_break_even"], 0)
        self.assertTrue(r["same_contract"]["same"])
        self.assertEqual(r["order"]["episodes"]["ep1"], ["PERIODIC_FULL", "EVICON"])
        self.assertAlmostEqual(r["levels"]["EVICON"]["owner_calls"], 241.5 + 16)
        run2 = _run(t / "b", "expPM-zephyr-w6", rows, gw={"EVICON": gwrec, "PERIODIC_FULL": dict(gwrec, qos_cmd=0)})
        self.assertFalse(pm085.same_contract(run2, "ZEPHYR_CUTOFF")["same"])
        self.assertIsNone(pm085.break_even({0: {"queries": 1, "fences": 1}, 1: {"queries": 2, "fences": 2}},
                                           {0: {"queries": 0, "fences": 0}, 1: {"queries": 0, "fences": 0}}, "fences")["ratio"])
        self.assertIn("E-PM", pm085.markdown(res))


class K9Verdicts(unittest.TestCase):
    def test_compare(self):
        import verdicts085 as V
        t = tmpdir(self)
        old = {"hypotheses": [{"plan": "P", "id": "H1", "verdict": "MET", "estimate": "+1"}, {"plan": "P", "id": "H2", "verdict": "ABSENT", "estimate": ""}]}
        (t / "old.json").write_text(json.dumps(old))
        (t / "same.json").write_text(json.dumps({"hypotheses": old["hypotheses"] + [{"plan": "PREREG_extra_085", "id": "PM-1", "verdict": "MET"}]}))
        self.assertTrue(V.compare(t / "same.json", t / "old.json")["identical"])
        (t / "diff.json").write_text(json.dumps({"hypotheses": [{"plan": "P", "id": "H1", "verdict": "NOT MET", "estimate": "+1"}]}))
        r = V.compare(t / "diff.json", t / "old.json")
        self.assertFalse(r["identical"])
        self.assertEqual(r["verdicts_differ"][0]["new"], "NOT MET")
        self.assertEqual(r["missing_in_new"], [["P", "H2"]])


class K10Claims(unittest.TestCase):
    def test_tb_rows(self):
        import claims_check as CC
        t = tmpdir(self)
        self.assertEqual(CC.checks_085(t), [])
        (t / "pairs" / "PREREG_extra_085").mkdir(parents=True)
        (t / "pairs" / "PREREG_extra_085" / "results.json").write_text("{}")
        self.assertEqual({r["verdict"] for r in CC.checks_085(t)}, {"NOT RUN"})
        (t / "tbcheck").mkdir()
        rules = {"TB-1": {"verdict": "MET", "evidence": "x"}, "TB-2": {"verdict": "ABSENT", "evidence": "y"}}
        (t / "tbcheck" / "tbcheck.json").write_text(json.dumps({"platform": "testbed", "rules": rules}))
        rows = {r["id"]: r["verdict"] for r in CC.checks_085(t)}
        self.assertEqual((rows["TB-1"], rows["TB-2"], rows["TB-4"]), ("MET", "ABSENT", "NOT RUN"))
        (t / "tbcheck" / "tbcheck.json").write_text(json.dumps({"platform": "stand-in", "rules": rules}))
        self.assertEqual({r["id"]: r["verdict"] for r in CC.checks_085(t)}["TB-1"], "STAND-IN MET")


class K11PaperTables(unittest.TestCase):
    def test_macros(self):
        import paper_tables as PT
        t = tmpdir(self)
        (t / "pm085").mkdir()
        lv = {"n": 8, "clean_pp": 79.3, "confirmed_pp": 79.1, "admitted_pp": 79.6, "mean_time_to_terminal_s": 1.9, "queries": 244.2, "stops": 0.0,
              "fences": 15.8, "unresolved_at_end": 0, "owner_calls": 260.0}
        fp = {"cells": 8, "n": 120, "p50_ms": 1180.0, "p95_ms": 33000.0, "max_ms": 36000.0, "paused_fraction": 0.14, "ends": {}}
        (t / "pm085" / "pm085.json").write_text(json.dumps({"matched": [{"run": "PMz6", "levels": {"EVICON": lv, "PERIODIC_FULL": lv},
                                                                         "fence_pause": {"EVICON": fp, "PERIODIC_FULL": fp},
                                                                         "break_even": {"ratio": 1.81, "boot95": [1.54, 2.30]},
                                                                         "same_contract": {"same": True}}], "cross084": []}))
        (t / "tbcheck").mkdir()
        (t / "tbcheck" / "tbcheck.json").write_text(json.dumps({"platform": "testbed", "system_info": {"version": "4.3.1.6", "type": "CE"},
                                                                "rules": {"TB-1": {"verdict": "MET", "evidence": "20 of 20"}, "TB-3a": {"verdict": "DETECTED", "evidence": "e"}}}))
        (t / "overhead").mkdir()
        (t / "overhead" / "overhead.json").write_text(json.dumps([{"campaign": "expFI-s6", "contract": "K0F", "policy": "EVICON_FI", "fence_pause_n": 187,
                                                                    "fence_pause_ms_p50": 1136.952, "fence_pause_ms_p95": 33067.8, "paused_fraction": 0.1248}]))
        (t / "l2_085" / "window_ci").mkdir(parents=True)
        (t / "l2_085" / "window_ci" / "window_ci.json").write_text(json.dumps({"pairs": [{"label": "fw-queue-plain-qos0", "mean": 34.8, "t95": [30.1, 39.5],
                                                                                         "boot95": [31.0, 38.6], "method": "paired", "from_mean": 24.7, "to_mean": 59.5}]}))
        (t / "l2_085" / "offline_bound").mkdir()
        (t / "l2_085" / "offline_bound" / "offline_bound.json").write_text(json.dumps({"rows": [{"campaign": "expC2-zephyr", "episodes": 8, "bound_pct": 86.8,
                                                                                                "best_clean_pct": 79.4, "best_group": "ZEPHYR_CUTOFF/EVICON"}], "regimes": []}))
        out = t / "paper"
        out.mkdir()
        N = {}
        PT.extra085(t, out, N)
        for k, v in (("evPMPMzSixEVICONOwnerCalls", "260.0"), ("evPMPMzSixBreakEven", "1.81"), ("evPMPMzSixBreakEvenLo", "1.54"),
                     ("evPMPMzSixSameContract", "yes"), ("evTBOneVerdict", "MET"), ("evTBThreeaVerdict", "DETECTED"), ("evTBVersion", "CE 4.3.1.6"),
                     ("evPauseFIsSixKZeroFEVICONFIMedMs", "1137.0"), ("evPauseFIsSixKZeroFEVICONFIN", "187"),
                     ("evPauseFIsSixKZeroFEVICONFIPausedPct", "12.48"), ("evLWinFwQueuePlainQosZeroMean", "+34.8"),
                     ("evLWinFwQueuePlainQosZeroMethod", "paired"), ("evLOfflineCTwoZephyrBound", "86.8")):
            self.assertEqual(N.get(k), v, k)
        for f in ("tab_pm085.tex", "tab_tbcheck.tex", "tab_l2_window_ci.tex", "tab_l2_offline_bound.tex"):
            self.assertTrue((out / f).exists(), f)
        self.assertTrue(all(re.fullmatch(r"[A-Za-z]+", k) for k in N))
        (t / "safety").mkdir()
        sp = {"theorem1": {"cells": 884, "campaigns": 40, "admitted": 400000, "offered": 500000, "violations": 0, "late_effects": 3,
                           "upper95_per_admitted_command": 7.5e-6}, "dagger": {"cells": 106, "campaigns": 9, "admitted": 40000, "violations": 0}}
        (t / "safety" / "safety.json").write_text(json.dumps({"summary": {"testbed_runs": 1, "guarded": {}, "guarded_violations": 0, "controls": {},
                                                                          "unguarded_dedup": {}, "verdict": "NO VIOLATION IN ANY GUARDED CELL",
                                                                          "premise_split": sp}}))
        N = {}
        PT.safety(t, out, N)
        self.assertEqual((N["evSafetySplitDaggerCells"], N["evSafetySplitTheoremOneCommands"], N["evSafetySplitTheoremOneLateEffects"]),
                         ("106", "400{,}000", "3"))


class K12SafetySplit(unittest.TestCase):
    def test_split(self):
        import safety_total as ST
        t = tmpdir(self)
        rows = [dict(contract="ZEPHYR_PLAIN", policy="EVICON", episode=0, cell_status="VALID", offered=100, admitted=30, clean_fulfilled=29,
                     fault_windows=5, late_effects=1),
                dict(contract="ZEPHYR_CUTOFF", policy="EVICON", episode=0, cell_status="VALID", offered=100, admitted=80, clean_fulfilled=79, fault_windows=5)]
        c = {"name": "expC2-zephyr", "real_owner": {"system": "zephyr", "qos_cmd": 1, "contracts": {"ZEPHYR_PLAIN": {"mode": "PLAIN"}, "ZEPHYR_CUTOFF": {"mode": "CUTOFF"}}}}
        run = _run(t, "expC2-zephyr", rows, c)
        (run / "schedule_ep0.json").write_text(json.dumps({"arrivals": [], "faults": [{"t": 1, "device": 0, "kind": "offline", "duration": 10}]}))
        s = ST.summarize([ST.read_run(run)])
        self.assertEqual(s["premise_split"]["dagger"]["cells"], 1)
        self.assertEqual(s["premise_split"]["dagger"]["admitted"], 30)
        self.assertEqual(s["premise_split"]["dagger"]["late_effects"], 1)
        self.assertEqual(s["premise_split"]["theorem1"]["cells"], 1)
        self.assertEqual(s["premise_split"]["theorem1"]["campaigns"], 1)
        self.assertEqual(s["phi1_fault_model"]["cells"] + s["theorem1_cells_with_faults"], s["cells_with_faults"])


class K13Report085(unittest.TestCase):
    def test_window_and_bound(self):
        import review_l2_085 as L
        self.assertAlmostEqual(L.t_q975(7), 2.3646, places=3)
        self.assertEqual(L.greedy_count([5, 1, 4, 2, 3], 7), 3)  # 1 + 2 + 3
        t = tmpdir(self)

        def rows(ct, base):
            return [dict(contract=ct, policy="EVICON", episode=e, cell_status="VALID", offered=100, clean_fulfilled=base + e) for e in range(4)]
        w = {"seed": 1, "devices": 2, "duration_s": 20, "workload": {"rate_per_s": 0.5, "value_min": 1, "value_max": 5, "bound": 6}}
        a = _run(t, "expT4-zephyr-w6", rows("ZEPHYR_PLAIN_QOS0", 25), dict(w, name="expT4-zephyr-w6"))
        b = _run(t, "expT4-zephyr-winf", rows("ZEPHYR_PLAIN_QOS0", 60), dict(w, name="expT4-zephyr-winf"))
        for e in range(4):
            for r in (a, b):
                (r / f"schedule_ep{e}.json").write_text(json.dumps({"arrivals": [{"device": 0, "value": v} for v in (5, 1, 4, 2)] +
                                                                    [{"device": 1, "value": 6}], "faults": []}))
        res = L.window_ci([a, b])["pairs"][0]
        self.assertEqual((res["method"], res["n"]), ("paired", 4))
        self.assertAlmostEqual(res["mean"], 35.0)
        (b / "schedule_ep2.json").write_text(json.dumps({"arrivals": [], "faults": [{"x": 1}]}))
        res = L.window_ci([a, b])["pairs"][0]
        self.assertEqual(res["method"], "welch")
        self.assertAlmostEqual(res["mean"], 35.0)
        ob = L.offline_bound([a])
        self.assertAlmostEqual(ob["rows"][0]["bound_pct"], 100.0 * 3 / 5)  # device 0: 1 + 2 (+4 > 6) -> 2; device 1: 6 -> 1; 3 of 5
        self.assertEqual(L.window_ci([a])["pairs"][0]["method"], "not available")


class K14TLA(unittest.TestCase):
    def test_timing_models(self):
        f = REPO / "formal"
        res = (f / "TLC_RESULTS_TIMING.txt").read_text()
        script = (f / "run_tlc_timing.sh").read_text()
        want = {"firstsend_after_expiry": "no error", "firstsend_before_expiry_witness": "Invariant NoHiddenEffect is violated",
                "firstsend_boundary_witness": "Invariant NoHiddenEffect is violated", "original_after_validity": "Invariant NoLateEffect is violated",
                "reissue_after_validity": "Invariant NoLateEffect is violated", "validity_every_attempt": "no error"}
        for cfg_, r in want.items():
            self.assertTrue((f / f"EviconTiming_{cfg_}.cfg").exists(), cfg_)
            line = next(x for x in res.splitlines() if x.startswith(cfg_ + " "))
            self.assertIn(r, line)
            self.assertTrue(line.rstrip().endswith("EXPECTED") and "UNEXPECTED" not in line, line)
            self.assertIn(cfg_, script)
            self.assertTrue((f / "tlc_logs_timing" / f"EviconTiming_{cfg_}.log").exists())
        for name in ["EviconTiming.tla", "run_tlc_timing.sh"] + [f"EviconTiming_{c}.cfg" for c in want]:
            self.assertIn(f"{sha(f / name)}  formal/{name}", res, name)
        for log in (f / "tlc_logs_timing").glob("*.log"):
            self.assertNotIn("/home/", log.read_text())


class K15TBCheck(unittest.TestCase):
    def test_module_and_device(self):
        import tb_rpc_checks as K
        from evicon_probe.adapters import thingsboard as TA
        self.assertIs(K.TB_STATUS, TA.TB_STATUS)
        for d, conf in (("oneway", "thingsboard-rpc-oneway"), ("twoway", "thingsboard-rpc-r0")):
            ac = json.loads((REPO / "conformance" / "configs" / f"{conf}.json").read_text())["adapter_config"]
            s = K.SHAPES[d]
            self.assertEqual((s["retries"], s["timeout"], s["expiration_s"], s["config"]), (ac["retries"], ac["rpc_timeout_ms"], ac["expiration_s"], conf))
        dev = (REPO / "conformance" / "reference" / "mqtt_device_tbcheck.py").read_text()
        self.assertIn("from reference import mqtt_device as MD", dev)
        self.assertIn("qos=1", (REPO / "conformance" / "reference" / "mqtt_device.py").read_text())
        src = (REPO / "evicon_review.py").read_text()
        i = src.index("def cmd_tbcheck(")
        body = src[i:src.index("\ndef ", i + 10)]
        self.assertIn('["TB_USERNAME", "TB_PASSWORD"]', body)
        self.assertIn('["TB_DEVICE_TOKEN"]', body)
        self.assertNotIn("password", "".join(re.findall(r"cfgd = \{.*?\}", body, re.S)).lower())


class K16ExpiresAfter(unittest.TestCase):
    def test_flag(self):
        main = (REPO / "cmd" / "evicon-authority" / "main.go").read_text()
        self.assertNotIn("transport-ttl", main)
        self.assertIn("disp.ExpiresAfter = outbox.DispatchExpiry(*expiresAfter, ec)", main)
        self.assertIn('flag.Duration("expires-after", -1,', main)
        go = (REPO / "internal" / "outbox" / "expires_after.go").read_text()
        self.assertIn("case expiresAfter >= 0:", go)
        self.assertIn("case ec.Has(contract.CapExpiry):", go)


class K17Provenance(unittest.TestCase):
    """PROVENANCE.md (coordinator's item): the plan of every campaign from its run config or the release tree's config,
    the newest plan's runs to be filled, every plan with its SHA-256, the publication entries consistent with the files."""

    def test_provenance(self):
        sys.path.insert(0, str(REPO / "scripts"))
        import provenance as PV
        t = tmpdir(self)
        run = t / "arch" / "runs" / "review-20261028T080322" / "campaigns" / "expK2-w6" / "20261030T092059"
        run.mkdir(parents=True)
        (run / "config.json").write_text(json.dumps({"name": "expK2-w6", "preregistration": {
            "path": "evaluation/prereg/PREREG_review_077.md", "sha256": sha(REPO / "evaluation" / "prereg" / "PREREG_review_077.md")}}))
        tree = t / "trees" / "EVICON_TOIT_072" / "evaluation" / "campaigns"
        tree.mkdir(parents=True)
        (tree / "expS_w6.json").write_text(json.dumps({"name": "expS-w6", "preregistration": {
            "path": "evaluation/prereg/PREREG_expS_072.md", "sha256": "0" * 64}}))
        rep = t / "report"
        (rep / "safety").mkdir(parents=True)
        (rep / "safety" / "safety.json").write_text(json.dumps({"summary": {"guarded": {"cells": 56}}, "runs": [
            {"run": "expK2-w6/20261030T092059", "dir": str(run), "release": "078", "scope": "TESTBED", "cells": 20, "invalid": 0, "experiment": "expK2-w6"},
            {"run": "expS-w6/20261022T161003", "dir": "/nowhere/review-20261021T173508/campaigns/expS-w6/20261022T161003", "release": "072",
             "scope": "TESTBED", "cells": 36, "invalid": 0, "experiment": "expS-w6"},
            {"run": "rq4-load-r1/20261020T044755", "dir": "/nowhere/review-20261020T011256/x", "release": "069", "scope": "TESTBED",
             "cells": 12, "invalid": 0, "experiment": "rq4-load-r1"}]}))
        self.assertEqual(PV.main(["--report", str(rep), "--out", str(t / "P.md"), "--trees", str(t / "trees")]), 0)
        md = (t / "P.md").read_text()
        self.assertIn("| `expK2-w6/20261030T092059` | 078 | `review-20261028T080322` | 20 | 0 | `PREREG_review_077.md` | `db67640d1709b06d...` | run config |", md)
        self.assertIn("| `expS-w6/20261022T161003` | 072 | `review-20261021T173508` | 36 | 0 | `PREREG_expS_072.md` | `0000000000000000...` | config of release 072 |", md)
        self.assertIn("none (exploratory) | - | - |", md)  # no config anywhere
        # release 086: the newest plan's campaigns are to be filled by the newest release's report
        newest = json.loads(re.search(r"```pairs-spec\n(.*?)```", (REPO / "evaluation" / "prereg" / PV.NEWEST_PLAN).read_text(), re.S).group(1))
        for c in [v["campaign"] for v in newest["runs"].values() if not v.get("reference")]:
            self.assertIn(f"| `{c}/<to be filled by the {PV.NEWEST_RELEASE} report>` | {PV.NEWEST_RELEASE} |", md)
        self.assertIn("DIFFERENT", md)  # the fake 072 config's SHA differs from the plan file
        for p in PV.plan_files():
            self.assertIn(f"| `{p.name}` | `{sha(p)}` |", md)
        # the publication entries: the frozen versions are the files of this release; 077's first version differs
        pub = PV.PUBLISHED
        self.assertEqual(pub["PREREG_review_077.md"][-1]["sha256"], sha(REPO / "evaluation" / "prereg" / "PREREG_review_077.md"))
        self.assertNotEqual(pub["PREREG_review_077.md"][0]["sha256"], pub["PREREG_review_077.md"][-1]["sha256"])
        for n in ("PREREG_extra_083.md", "PREREG_extra_084.md"):
            self.assertEqual(pub[n][0]["sha256"], sha(REPO / "evaluation" / "prereg" / n))
        self.assertEqual([x["commit"] for x in pub["PREREG_review_077.md"]] + [pub["PREREG_extra_083.md"][0]["commit"], pub["PREREG_extra_084.md"][0]["commit"]],
                         ["ed044bb", "d67fd27", "ad4c714", "ce7fd12"])
        if PV.NEWEST_PLAN == f"PREREG_extra_{PV.NEWEST_RELEASE}.md":  # release 088 runs plan 087, published by 087 (test_release088 L5)
            self.assertEqual(pub[PV.NEWEST_PLAN], [])  # release 086: plan 085 is published (40e451d), plan 086 not yet
            self.assertIn("to be filled when published", md)
        # the release's own file and the report's
        top = (REPO / "PROVENANCE.md").read_text()
        self.assertIn("review-20261102T174310", top)
        self.assertIn("expT7-dinf-off5", top)
        self.assertIn("TLC_RESULTS_TIMING.txt", top)
        self.assertIn('"scripts/provenance.py", "--report"', (REPO / "evicon_review.py").read_text())


class K18BundleNames(unittest.TestCase):
    """Two incremental bundles in one minute: the second gets _2 (the first is never replaced), and import-results reads it."""

    def test_unique(self):
        src = (REPO / "evicon_review.py").read_text()
        body = src[src.index("def make_bundle("):src.index("zpath = REPO / f\"{name}.zip\"", src.index("def make_bundle("))]
        self.assertIn('while a.incremental and (REPO / f"{name}.zip").exists():', body)
        self.assertIn('name = f"EVICON_{RELEASE}_result_{stamp}_{k}"', body)
        sys.path.insert(0, str(REPO / "scripts"))
        import import_results as IR
        for n in ("EVICON_085_result_20261104T1621.zip", "EVICON_085_result_20261104T1621_2.zip", "EVICON_085_result_20261104T1621_3.zip"):
            self.assertTrue(IR.BUNDLE_RE.match(n), n)
        sh_ = sorted(REPO.glob("run08*.sh"))[-1].read_text()  # the current operator script (release 086: run086.sh)
        self.assertNotIn("Send the newest", sh_)
        self.assertIn("not sent yet", sh_)


if __name__ == "__main__":
    unittest.main()
