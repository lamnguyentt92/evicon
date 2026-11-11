#!/usr/bin/env python3
"""Release 086 checks (PREREG_extra_086.md; the fence anchor from evidence, verifier finding B2-02).

K1  the release is 086 everywhere (VERSION, buildinfo, CITATION.cff, the VM set-up banners, CHANGELOG)
K2  the earlier plans are byte-identical (077, 083, 084, 085) and every file 085 froze is frozen again; 086 frozen
K3  the authority: -fence-anchor (default evidence; owner-state restores 061-085), the commit path, the Go tests
K4  the SR configs are copies of the original configs and differ only where the plan says
K5  the new seed: replayed schedules equal the frozen table, and none equals an earlier schedule
K6  the plan's rules: margins = half the original estimates (as the power script), families, 13 rules, no exempt contract
K7  power: every SR rule >= 0.80 at n = 8 with the plan's margin; the lower-95 % margin would fail the 0.80 rule
K8  sr086: the replication next to the original, the difference between the seeds (Welch) on synthetic runs
K9  anchor086: a matching journal, a mismatching one and a lean cell (no journal: not available)
K10 verdicts086: identical, a differing verdict and a missing hypothesis are found
K11 the driver: verify list, the runners of the SR configs, the report steps of release 086
K12 run086.sh: the order of the steps, the four sources of the report, the refusal and the bundle wording
K13 paper tables: the E-SR and fence-anchor macros
K14 provenance: plan 085 published (40e451d, its frozen SHA-256), plan 086 the newest, to be filled
K15 hygiene of the new files (English, no forbidden word, no guide name in tests or sources)
K16 the firmware host library of the wire path is built by the host tests and loaded by the conformance test
Addendum (5 November):
K17 SHADOW_RECONCILE: the policy, its Go tests, the runner arguments (RELEASE_ON_READ's with -policy SHADOW_RECONCILE),
    the SH configs (copies of expRRA_queue / expRR_direct), not an unsafe-by-design baseline, the runner of the driver
K18 the QoS 2 row: device, adapter, config (the QoS 1 row's probes at QoS 2), the driver's stack, the paper short name
K19 cost086 on a synthetic firmware run: gateway requests, derived and counted flash writes, broker packets, paired
    intervals, the break-even units; "not available" fields for runs without counters
K20 nvs_flash: a synthetic NVS sector with two settings names and an incomplete (garbage-collected) image
K21 anchor086 opens an archived journal without writing anything next to it (WAL journal, side files)
K22 campaign_zephyr_ext086: the counters change nothing a cell does (the 077 testbed class with three wrapped methods)
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "evaluation"))
import evicon_review as er  # noqa: E402
import campaign as C  # noqa: E402

PLAN = "evaluation/prereg/PREREG_extra_086.md"
SAFETY = ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]
SR = {"expSR_minio_w6": ("expC_minio",), "expSR_minio_winf": ("expR_minio_winf",),
      "expSR_zephyr_w6": ("expT4_zephyr_w6", "expC3_ablation"), "expSR_zephyr_winf": ("expT4_zephyr_winf", "expR_zephyr_winf")}
EARLIER = {"evaluation/prereg/PREREG_review_077.md": "db67640d1709b06dfa5e61f77e3444c07e7c5828dfe24e0e85d163f06a711a29",
           "evaluation/prereg/PREREG_extra_083.md": "6e3f24a918de659c1feb8aaa886d9e9681195353528b5df10e615e8897ff3a06",
           "evaluation/prereg/PREREG_extra_084.md": "b11351645c1e0f05f4a270412539a072f17148ea8607223dcb6182a171505345",
           "evaluation/prereg/PREREG_extra_085.md": "22586c782aa3ac704bcc56376324faeb7c4047da6e35c1668a65a34924bf4790"}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def cfg(name: str) -> dict:
    return json.loads((REPO / "evaluation" / "campaigns" / f"{name}.json").read_text())


def tmpdir(tc) -> Path:
    t = Path(tempfile.mkdtemp())
    tc.addCleanup(shutil.rmtree, t, ignore_errors=True)
    return t


def sim_only() -> bool:
    return cfg("expSR_minio_w6")["notes"].startswith("SIM-ONLY")


def block(name: str) -> dict:
    return json.loads(re.search(rf"```{name}\n(.*?)```", (REPO / PLAN).read_text(), re.S).group(1))


class K1Version(unittest.TestCase):
    def test_version(self):
        v = (REPO / "VERSION").read_text().strip()  # release 087: this release or a later one
        self.assertGreaterEqual(v, "086")
        self.assertIn(f'const Release = "{v}"', (REPO / "internal" / "buildinfo" / "buildinfo.go").read_text())
        self.assertIn(f'version: "{v}"', (REPO / "CITATION.cff").read_text())
        for f in sorted((REPO / "deploy" / "setup").glob("vm-*.sh")):
            self.assertIn(f"(EviCon {v})", f.read_text(), f.name)
        self.assertIn("## 086", (REPO / "CHANGELOG.md").read_text())


class K2Frozen(unittest.TestCase):
    def test_earlier_plans(self):
        for p, h in EARLIER.items():
            self.assertEqual(sha(REPO / p), h, p)

    def test_085_frozen_again(self):
        def frozen(plan):
            m = re.search(r"```frozen-files\n(.*?)```", (REPO / plan).read_text(), re.S)
            return dict(reversed(ln.split("  ", 1)) for ln in m.group(1).splitlines() if ln.strip())
        f085, f086 = frozen("evaluation/prereg/PREREG_extra_085.md"), frozen(PLAN)
        for path, h in f085.items():
            self.assertEqual(f086.get(path), h, path)
        self.assertEqual(f086["evaluation/prereg/PREREG_extra_085.md"], EARLIER["evaluation/prereg/PREREG_extra_085.md"])
        for path in ("evaluation/power_extra086_testbed.py", "evaluation/sim_predictions/extra086_power.csv", "evaluation/sr086_schedules.py",
                     "evaluation/sim_predictions/extra086_schedules.csv", "evaluation/sim_predictions/extra086_templates.csv", "evaluation/sr086.py"):
            self.assertIn(path, f086)
        for path, h in f086.items():
            self.assertEqual(sha(REPO / path), h, path)

    def test_086_frozen(self):
        r = subprocess.run([sys.executable, "scripts/freeze_prereg.py", "--check", PLAN], cwd=REPO, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        h = sha(REPO / PLAN)
        for name in SR:
            self.assertEqual(cfg(name)["preregistration"], {"path": PLAN, "sha256": h}, name)


class K3Authority(unittest.TestCase):
    def test_flag_and_commit_path(self):
        main = (REPO / "cmd" / "evicon-authority" / "main.go").read_text()
        self.assertIn('flag.String("fence-anchor", eruntime.FenceAnchorEvidence,', main)
        self.assertIn("FenceAnchor: strings.ToLower(strings.TrimSpace(*fenceAnchor))", main)
        auth = (REPO / "internal" / "runtime" / "authority.go").read_text()
        self.assertIn('FenceAnchorEvidence   = "evidence"', auth)
        self.assertIn('FenceAnchorOwnerState = "owner-state"', auth)
        self.assertIn("cfg.FenceAnchor = FenceAnchorEvidence", auth)
        body = auth[auth.index("seal := a.cfg.Policy == probe.EVICON"):auth.index('Event: "BARRIER_ANCHOR_COMMITTED"')]
        for s in ("a.computeFenceAnchor(d, rc.CutoffSequence, recs)", "case !fa.Contains(rc.State):", "FENCE_ANCHOR_MISMATCH",
                  "a.commitUnanchoredLocked(", "anchor = []int64(fa.States[0])",
                  "CommitBarrierLifecycleSeal(br.DomainID, br.Token, d.Generation, rc.CutoffSequence, anchor, rc.OwnerVersion, recs, seal)"):
            self.assertIn(s, body)
        # the uncertain paths (R5 abort, R5' per identity) come first and are unchanged
        self.assertLess(auth.index("return a.commitPerIdentityLocked(br, d, rc, recs, uncertain)"), auth.index("a.computeFenceAnchor("))

    def test_go_tests_exist(self):
        src = "".join(p.read_text() for p in (REPO / "internal").rglob("*_test.go"))
        for t in ("TestFenceAnchorDefault086", "TestFenceAnchorFromEvidence086", "TestFenceAnchorMismatchAborts086", "TestFenceAnchorUndeterminedSET086",
                  "TestFenceAnchorPropertyE2086", "TestFenceAnchorPropertyDevice086", "TestFenceAnchorStatesUnit086", "TestFenceAnchorStatesProperty086"):
            self.assertIn(f"func {t}(", src)


class K4Configs(unittest.TestCase):
    def test_copies(self):
        for name, origs in SR.items():
            c, o = cfg(name), cfg(origs[0])
            self.assertEqual(c["seed"], 20261105)
            self.assertEqual(c["policies"], ["EVICON"])
            self.assertEqual(c["name"], name.replace("_", "-"))
            self.assertEqual(c["regime"], c["name"])
            same = set(o) - {"name", "regime", "notes", "preregistration", "seed", "policies", "contracts", "real_owner", "episodes", "duration_s", "tail_s"}
            for k in same:
                self.assertEqual(c[k], o[k], (name, k))
            if not sim_only():
                for k in ("episodes", "duration_s", "tail_s"):
                    self.assertEqual(c[k], o[k], (name, k))
            ro, oro = dict(c["real_owner"]), dict(o["real_owner"])
            if "minio" in name:
                self.assertEqual(c["contracts"], ["MINIO_PLAIN", "MINIO_COND"])
                oro.pop("policies", None)
                self.assertEqual(ro, oro)
            else:
                self.assertEqual(c["contracts"], ["ZEPHYR_PLAIN_DIRECT_QOS0", "ZEPHYR_F_DIRECT"])
                f = dict(cfg(origs[1])["real_owner"]["contracts"]["ZEPHYR_F_DIRECT"])
                f["binding"] = "qos1"
                self.assertEqual(ro["contracts"], {"ZEPHYR_PLAIN_DIRECT_QOS0": oro["contracts"]["ZEPHYR_PLAIN_DIRECT_QOS0"], "ZEPHYR_F_DIRECT": f})
                self.assertEqual(oro["contracts"]["ZEPHYR_PLAIN_DIRECT_QOS0"]["binding"], "auto")
                for k in set(oro) - {"contracts"}:
                    self.assertEqual(ro[k], oro[k], (name, k))
            self.assertEqual(c["authority"]["max_outstanding"], o["authority"]["max_outstanding"])


class K5Seed(unittest.TestCase):
    def test_schedules(self):
        import sr086_schedules as S
        rows = list(csv.DictReader(open(REPO / "evaluation" / "sim_predictions" / "extra086_schedules.csv")))
        sr = {int(r["episode"]): r for r in rows if r["sr"] == "1"}
        old = [r for r in rows if r["sr"] == "0"]
        self.assertGreaterEqual(len({r["seed"] for r in old}), 10)
        self.assertNotIn("20261105", {r["seed"] for r in old})
        old_a, old_f = {r["arrivals_sha"] for r in old}, {r["faults_sha"] for r in old}
        for ep in range(8):
            self.assertNotIn(sr[ep]["arrivals_sha"], old_a)
            self.assertNotIn(sr[ep]["faults_sha"], old_f)
        self.assertEqual(len({sr[ep]["faults_sha"] for ep in range(8)}), 8)
        for name in SR:  # the configs replay the frozen table (a SIM-ONLY copy keeps the seed: its schedules are prefixes)
            c = cfg(name)
            if sim_only():
                continue
            for ep in range(c["episodes"]):
                a, f, _, _ = S.digests(c, ep)
                self.assertEqual((a, f), (sr[ep]["arrivals_sha"], sr[ep]["faults_sha"]), (name, ep))


class K6Plan(unittest.TestCase):
    def test_rules(self):
        import power_extra086_testbed as PW
        spec = block("pairs-spec")
        hs = {h["id"]: h for h in spec["hypotheses"]}
        self.assertEqual(len(hs) + 1, 17)  # + SR-S
        want = {"SR-1": ("SRm6", "MINIO_PLAIN", "MINIO_COND", "sr-minio"), "SR-2": ("SRmi", "MINIO_PLAIN", "MINIO_COND", "sr-minio"),
                "SR-3": ("SRz6", "ZEPHYR_PLAIN_DIRECT_QOS0", "ZEPHYR_F_DIRECT", "sr-fw"), "SR-4": ("SRzi", "ZEPHYR_PLAIN_DIRECT_QOS0", "ZEPHYR_F_DIRECT", "sr-fw")}
        for k, (run, frm, to, fam) in want.items():
            h = hs[k]
            self.assertEqual((h["run"], h["from"], h["to"], h["family"], h["test"], h["metric"], h["policy"]), (run, frm, to, fam, "at_least", "clean_pp", "EVICON"))
            self.assertEqual(h["margin"], PW.MARGIN_HALF[k])
            self.assertGreaterEqual(h["margin"], PW.ORIGINAL[k][1] / 2)
            self.assertLess(h["margin"] - PW.ORIGINAL[k][1] / 2, 0.01)
        absent = [h for h in hs.values() if h["test"] == "absent" and h["id"].startswith("SR-")]
        self.assertEqual(len(absent), 8)
        self.assertEqual({(h["run"], h["contract"]) for h in absent}, {(r, c) for r, a, b, _ in want.values() for c in (a, b)})
        sh = {h["id"]: (h["run"], h["contract"], h["policy"], h["test"]) for h in hs.values() if h["id"].startswith("SH-")}
        self.assertEqual(sh, {"SH-A1": ("SHq", "K2", "SHADOW_RECONCILE", "absent"), "SH-A2": ("SHq", "K2", "EVICON", "absent"),
                              "SH-A3": ("SHd", "K2", "SHADOW_RECONCILE", "absent"), "SH-A4": ("SHd", "K2", "EVICON", "absent")})
        for h in absent:
            self.assertEqual(h["any_of"], SAFETY)
        self.assertEqual(spec["safety"], {"id": "SR-S", "runs": ["SRm6", "SRmi", "SRz6", "SRzi"], "exempt_contracts": []})
        self.assertEqual({v["campaign"] for v in spec["runs"].values()}, {cfg(n)["name"] for n in list(SR) + ["expSH_queue", "expSH_direct"]})
        pc = block("policy-compare")
        self.assertEqual([c["id"] for c in pc["comparisons"]], [f"SH-E{i}" for i in range(1, 7)])
        txt = (REPO / PLAN).read_text()
        for q in ("you might have to poll the shadow by making HTTP GET requests until the device", "can ignore all notifications with",
                  "Expectation, written before the measurement", "About 12.5 h in all"):
            self.assertIn(q, txt)


class K7Power(unittest.TestCase):
    def test_power(self):
        rows = list(csv.DictReader(open(REPO / "evaluation" / "sim_predictions" / "extra086_power.csv")))
        base = [r for r in rows if r["n"] == "8" and r["spread_factor"] == "1.0" and float(r["true_mean"]) == float(r["original_estimate"])]
        for rule in ("SR-1", "SR-2", "SR-3", "SR-4"):
            half = [float(r["power"]) for r in base if r["rule"] == rule and r["margin_rule"] == "half"]
            low = [float(r["power"]) for r in base if r["rule"] == rule and r["margin_rule"] == "lower95"]
            self.assertTrue(half and min(half) >= 0.80, (rule, half))
            self.assertTrue(low and max(low) < 0.80, (rule, low))


def fake_run(root: Path, name: str, stamp: str, levels: dict, schedule_tag: str) -> Path:
    """A campaign run with runtime_episodes.csv: levels {contract: [clean pp per episode]} (offered 100)."""
    run = root / "campaigns" / name / stamp
    run.mkdir(parents=True)
    with open(run / "runtime_episodes.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["contract", "policy", "episode", "cell_status", "offered", "clean_fulfilled"])
        for ct, xs in levels.items():
            for ep, x in enumerate(xs):
                w.writerow([ct, "EVICON", ep, "VALID", 100, x])
    for ep in range(len(next(iter(levels.values())))):
        (run / f"schedule_ep{ep}.json").write_text(json.dumps({"tag": schedule_tag, "ep": ep}))
    return run


class K8SR086(unittest.TestCase):
    def test_estimands(self):
        import sr086
        t = tmpdir(self)
        runs = {"SRm6": fake_run(t, "expSR-minio-w6", "20261106T010000", {"MINIO_PLAIN": [30, 40, 35, 45], "MINIO_COND": [80, 82, 79, 85]}, "new"),
                "C": fake_run(t, "expC-minio", "20261023T083658", {"MINIO_PLAIN": [25, 35, 30, 20], "MINIO_COND": [78, 80, 77, 79]}, "old"),
                "SRz6": fake_run(t, "expSR-zephyr-w6", "20261106T020000", {"ZEPHYR_PLAIN_DIRECT_QOS0": [20, 30, 25, 35], "ZEPHYR_F_DIRECT": [80, 81, 79, 84]}, "new"),
                "Z6": fake_run(t, "expT4-zephyr-w6", "20261031T113212", {"ZEPHYR_PLAIN_DIRECT_QOS0": [20, 36, 33, 9]}, "old"),
                "C3": fake_run(t, "expC3-ablation", "20261024T161324", {"ZEPHYR_PLAIN_DIRECT": [20, 37, 33, 9], "ZEPHYR_F_DIRECT": [85, 80, 83, 78]}, "old")}
        res = sr086.analyse(runs)
        c = {x["id"]: x for x in res["contrasts"]}
        self.assertAlmostEqual(c["SR-1"]["replication"]["mean"], 44.0)
        self.assertAlmostEqual(c["SR-1"]["original"]["mean"], 51.0)
        self.assertEqual(c["SR-1"]["seeds"]["method"], "welch")
        self.assertAlmostEqual(c["SR-1"]["seeds"]["mean"], -7.0)
        self.assertAlmostEqual(c["SR-1"]["ratio"], 44 / 51)
        self.assertEqual(c["SR-3"]["original"]["n"], 4)  # Z6 -> C3 paired on identical schedule files
        self.assertAlmostEqual(c["SR-3"]["original"]["mean"], statistics_mean([65, 44, 50, 69]))
        self.assertIn("original_within_run", c["SR-3"])
        self.assertEqual(c["SR-2"]["replication"]["n"], 0)  # not available
        md = sr086.markdown(res)
        self.assertIn("| SR-1 |", md)


def statistics_mean(x):
    return sum(x) / len(x)


def fake_journal(path: Path, anchors: list, cmds: list):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE domains(domain_id TEXT, anchor_json TEXT, cutoff_sequence INTEGER)")
    con.execute("CREATE TABLE commands(domain_id TEXT, command_id TEXT, sequence INTEGER, operation TEXT, value_json TEXT, status TEXT, decision TEXT)")
    con.execute("CREATE TABLE authority_barrier_history(domain_id TEXT, token TEXT, cutoff_sequence INTEGER, status TEXT, anchor_json TEXT, created_at TEXT)")
    con.execute("INSERT INTO domains VALUES('d','[0]',0)")
    for i, (seq, v, st) in enumerate(cmds):
        con.execute("INSERT INTO commands VALUES('d',?,?,?,?,?,'ADMITTED')", (f"c{seq}", seq, "ADD", json.dumps([v]), st))
    for i, (tok, cut, st, anc) in enumerate(anchors):
        con.execute("INSERT INTO authority_barrier_history VALUES('d',?,?,?,?,?)", (tok, cut, st, json.dumps(anc), f"2026-11-05T00:00:0{i}Z"))
    con.commit()
    con.close()


class K9Anchor086(unittest.TestCase):
    def test_journals(self):
        import anchor086 as A
        t = tmpdir(self)
        run = t / "campaigns" / "expX" / "20261105T000000"
        good, bad, lean = (run / "cells" / "ep0" / "K0F" / p for p in ("EVICON", "EVICON_FI", "M1"))
        for d in (good, bad, lean):
            d.mkdir(parents=True)
        (good / "raw" / "A").mkdir(parents=True)
        (bad / "raw" / "A").mkdir(parents=True)
        cmds = [(1, 3, "APPLIED"), (2, 4, "STOPPED"), (3, 5, "APPLIED"), (4, 1, "APPLIED")]
        fake_journal(good / "raw" / "A" / "authority0.db", [("t1", 2, "COMPLETE", [3]), ("t2", 3, "ABORTED", [8]), ("t3", 4, "COMPLETE", [9]),
                                                             ("t4", 4, "PARTIAL", [])], cmds)
        fake_journal(bad / "raw" / "A" / "authority0.db", [("t1", 3, "COMPLETE", [0])], cmds)  # a reset level adopted
        (run / "config.json").write_text(json.dumps({"name": "expX"}))
        res = A.analyse([run])
        rows = {r["policy"]: r for r in res["rows"]}
        self.assertEqual((rows["EVICON"]["fences_completed"], rows["EVICON"]["anchors_compared"], rows["EVICON"]["mismatches"]), (2, 2, 0))
        self.assertEqual((rows["EVICON"]["aborted"], rows["EVICON"]["per_identity"]), (1, 1))
        self.assertEqual(rows["EVICON_FI"]["mismatches"], 1)
        self.assertEqual(res["mismatches"][0]["evidence"], [8])
        self.assertFalse(rows["M1"]["available"])
        self.assertIn("(not available)", A.markdown(res))
        self.assertEqual(res["total"]["mismatches"], 1)


class K10Verdicts(unittest.TestCase):
    def test_compare(self):
        import verdicts086 as V
        t = tmpdir(self)
        old = {"hypotheses": [{"plan": "PREREG_extra_085", "id": "PM-1", "verdict": "MET", "estimate": "+0.1"},
                              {"plan": "PREREG_review_077", "id": "U-1", "verdict": "MET", "estimate": "+16.50"}]}
        (t / "old.json").write_text(json.dumps(old))
        (t / "same.json").write_text(json.dumps({"hypotheses": old["hypotheses"] + [{"plan": "PREREG_extra_086", "id": "SR-1", "verdict": "MET"}]}))
        self.assertTrue(V.compare(t / "same.json", t / "old.json")["identical"])
        (t / "diff.json").write_text(json.dumps({"hypotheses": [dict(old["hypotheses"][0], verdict="NOT MET")]}))
        r = V.compare(t / "diff.json", t / "old.json")
        self.assertFalse(r["identical"])
        self.assertEqual(r["verdicts_differ"][0]["id"], "PM-1")
        self.assertEqual(r["missing_in_new"], [["PREREG_review_077", "U-1"]])


class K11Driver(unittest.TestCase):
    def test_driver(self):
        src = (REPO / "evicon_review.py").read_text()
        for s in ('"evaluation.test_release086"', "evaluation/sr086.py", "evaluation/anchor086.py", "evaluation/verdicts086.py",
                  '"Release 085;" in (s / "report" / "REPORT.md").read_text()[:400]', "earlier verdicts against 085"):
            self.assertIn(s, src)
        self.assertEqual(er._zephyr_runner(cfg("expSR_zephyr_w6")), "evaluation/campaign_zephyr_ext086.py")
        self.assertEqual(er._zephyr_runner(cfg("expSR_zephyr_winf")), "evaluation/campaign_zephyr_ext086.py")
        self.assertEqual(er._zephyr_runner(cfg("expPM_zephyr_w6")), "evaluation/campaign_zephyr_ext085.py")  # earlier configs unchanged
        self.assertEqual(er._zephyr_runner(cfg("expT4_zephyr_w6")), "evaluation/campaign_zephyr_ext077.py")
        for n in ("expSH_queue", "expSH_direct"):
            self.assertEqual(er._emulated_runner(cfg(n)), ("evaluation/campaign_ext086.py", []))
        self.assertEqual(er._emulated_runner(cfg("expRR_queue"))[0], "evaluation/campaign_ext077.py")
        self.assertIn("evaluation/cost086.py", src)
        for n in ("expSR_minio_w6", "expSR_minio_winf"):
            c = cfg(n)
            self.assertFalse(er._uses_083_policies(c) or er._uses_084_policies(c) or er._uses_085_options(c))
        # the report step order: the anchor analysis and E-SR before the paper tables and the claims check
        self.assertLess(src.index("evaluation/anchor086.py"), src.index('"evaluation/paper_tables.py", "--report"'))


class K12Script(unittest.TestCase):
    def test_run086(self):
        if not (REPO / "run086.sh").exists():  # release 087: run087.sh replaces it (test_release087 K12)
            self.assertTrue(sorted(REPO.glob("run08*.sh")))
            return
        s = (REPO / "run086.sh").read_text()
        self.assertFalse((REPO / "run085.sh").exists())
        self.assertIn('CAMPAIGNS="expSR_zephyr_w6 expSR_zephyr_winf expSR_minio_w6 expSR_minio_winf expSH_queue expSH_direct"', s)
        order = [s.index(x) for x in ("E preflight build verify distribute", "E measure zephyr-plain zephyr", "E measure mosquitto-qos2",
                                       'E campaign "$c"', 'E report --archive "$ARCHIVE" --from "$R078" --from "$R083" --from "$R084" --from "$R085"')]
        self.assertEqual(order, sorted(order))
        self.assertIn("REFUSED: another evicon_review.py is running", s)
        self.assertIn("STOP: unplanned safety violation", s)
        self.assertIn("not sent yet", s)
        self.assertNotIn("tbcheck", s)
        self.assertNotIn("EVICON_085_result", s)
        self.assertNotIn("RUN_GUIDE" + "_VI", s)
        r = subprocess.run(["bash", "-n", str(REPO / "run086.sh")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


class K13Paper(unittest.TestCase):
    def test_macros(self):
        import paper_tables as PT
        t = tmpdir(self)
        (t / "sr086").mkdir()
        (t / "sr086" / "sr086.json").write_text(json.dumps({"contrasts": [
            {"id": "SR-1", "label": "MinIO, W = 6", "replication": {"mean": 50.1, "t95": [40.0, 60.2], "n": 8},
             "original": {"mean": 53.4, "t95": [43.45, 63.34], "n": 8}, "seeds": {"mean": -3.3, "t95": [-15.0, 8.4]}, "ratio": 0.938}]}))
        (t / "anchor086").mkdir()
        (t / "anchor086" / "anchor086.json").write_text(json.dumps({"total": {"fences_completed": 120, "anchors_compared": 118, "mismatches": 0,
                                                                              "campaign_runs": 9, "campaign_runs_available": 7}}))
        N = {}
        out = t / "paper"
        out.mkdir()
        PT.extra086(t, out, N)
        self.assertEqual((N["evSROneRep"], N["evSROneOrig"], N["evSROneSeeds"], N["evSROneRatio"]), ("50.1", "53.4", "\\ensuremath{-}3.3", "0.94"))
        self.assertEqual((N["evAnchorFences"], N["evAnchorMismatches"], N["evAnchorRunsAvailable"]), ("120", "0", "7"))
        self.assertTrue((out / "tab_sr086.tex").exists())


class K14Provenance(unittest.TestCase):
    def test_published(self):
        sys.path.insert(0, str(REPO / "scripts"))
        import provenance as PV
        self.assertGreaterEqual(PV.NEWEST_RELEASE, "086")  # release 087: a later plan is the newest (test_release087 K14)
        self.assertEqual(PV.NEWEST_PLAN, f"PREREG_extra_{PV.NEWEST_RELEASE}.md")
        p = PV.PUBLISHED["PREREG_extra_085.md"]
        self.assertEqual((p[0]["commit"], p[0]["sha256"]), ("40e451d", sha(REPO / "evaluation" / "prereg" / "PREREG_extra_085.md")))
        self.assertEqual(PV.PUBLISHED[PV.NEWEST_PLAN], [])
        self.assertIn("review-20261104T171522", " ".join(PV.DEVIATIONS["PREREG_extra_085.md"]))
        top = (REPO / "PROVENANCE.md").read_text()
        self.assertIn("40e451d", top)
        self.assertIn("expPM-zephyr-w6/20261104T181900", top)


NEW_FILES = ["internal/runtime/fence_anchor.go", "internal/runtime/fence_anchor_test.go", "internal/evidence/fence_anchor.go",
             "internal/evidence/fence_anchor_test.go", "evaluation/anchor086.py", "evaluation/sr086.py", "evaluation/sr086_schedules.py",
             "evaluation/power_extra086_testbed.py", "evaluation/verdicts086.py", "evaluation/test_release086.py", PLAN, "run086.sh",
             "conformance/tests/test_fence_anchor_reboot.py", "firmware/zephyr-cutoff/tests/host/evc_wire_shim.c",
             "internal/runtime/shadow_reconcile.go", "internal/runtime/shadow_reconcile_test.go", "evaluation/ext086.py", "evaluation/campaign_ext086.py",
             "evaluation/campaign_zephyr_ext086.py", "evaluation/nvs_flash.py", "evaluation/cost086.py", "conformance/reference/mqtt_device_qos2.py",
             "conformance/evicon_probe/adapters/mqtt5_qos2.py", "conformance/configs/mosquitto-rpc-qos2.json", "conformance/tests/test_qos2_device.py",
             "evaluation/campaigns/expSH_queue.json", "evaluation/campaigns/expSH_direct.json"]


class K15Hygiene(unittest.TestCase):
    def test_new_files(self):
        forbidden = "EC" + "HO"
        guide = "RUN_GUIDE" + "_VI"
        for f in NEW_FILES:
            if f == "run086.sh" and not (REPO / f).exists():
                continue  # release 087: run087.sh replaces it
            s = (REPO / f).read_text()
            self.assertNotIn(forbidden, s, f)
            self.assertFalse(re.search("[" + chr(0xC0) + "-" + chr(0x1EF9) + "]", s), f"non-English characters in {f}")
            self.assertNotIn(guide, s, f)


class K16Firmware(unittest.TestCase):
    def test_shim(self):
        mk = (REPO / "firmware" / "zephyr-cutoff" / "tests" / "host" / "Makefile").read_text()
        self.assertIn("libevcshim.so", mk[:mk.index("test_evc_core:")])
        t = (REPO / "conformance" / "tests" / "test_fence_anchor_reboot.py").read_text()
        for s in ("evc_wire_shim.c", '"-fence-anchor", anchor_mode', "MISMATCH_OWNER_STATE_ADOPTED", "self.assertGreater(r[\"total\"], BOUND"):
            self.assertIn(s, t)
        shim = (REPO / "firmware" / "zephyr-cutoff" / "tests" / "host" / "evc_wire_shim.c").read_text()
        self.assertIn("evc_core_init(&core, reboot ? &flash : NULL, -1, 0, (mode_stop ? EVC_F_STOP : 0u) | EVC_F_GUARD_EVICTED)", shim)


class K17Shadow(unittest.TestCase):
    def test_policy(self):
        src = "".join(p.read_text() for p in (REPO / "internal").rglob("*_test.go"))
        for t in ("TestShadowReconcilePolicy086", "TestShadowReconcileK2Safe086", "TestShadowReconcileK0Unsafe086"):
            self.assertIn(f"func {t}(", src)
        pol = (REPO / "internal" / "probe" / "policy.go").read_text()
        self.assertIn('ShadowReconcile Method = "SHADOW_RECONCILE"', pol)
        self.assertIn("M1W, ReleaseOnReadAnswered, ShadowReconcile:", pol)
        go = (REPO / "internal" / "runtime" / "shadow_reconcile.go").read_text()
        for q in ("you might have to poll the shadow by making HTTP GET requests until the device has updated", "Neither page documents a timeout"):
            self.assertIn(q, go.replace("\n//      ", " ").replace("\n//   ", " ").replace("\n// ", " "))
        r = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, 'evaluation'); import ext086, campaign as C, json; ext086.install(); "
                            "a = json.load(open('evaluation/campaigns/expSH_queue.json'))['authority']; "
                            "print(json.dumps([C.policy_args('SHADOW_RECONCILE', a), C.policy_args('RELEASE_ON_READ', a), C.policy_args('EVICON', a)]))"],
                           cwd=REPO, capture_output=True, text=True)
        sh, rr, ev = json.loads(r.stdout)
        self.assertEqual(sh, ["-policy", "SHADOW_RECONCILE"] + rr[2:])
        self.assertIn("-resend-until", sh)
        import safety_total
        self.assertNotIn("SHADOW_RECONCILE", safety_total.BASELINES)

    def test_configs(self):
        for name, base, drop in (("expSH_queue", "expRRA_queue", ("release_settle",)), ("expSH_direct", "expRR_direct", ())):
            c, o = cfg(name), cfg(base)
            self.assertEqual(c["policies"], ["EVICON", "SHADOW_RECONCILE"])
            self.assertEqual(c["preregistration"]["path"], PLAN)
            for k in set(o) - {"name", "regime", "notes", "preregistration", "policies", "authority", "episodes", "duration_s", "tail_s"}:
                self.assertEqual(c[k], o[k], (name, k))
            oa = {k: v for k, v in o["authority"].items() if k not in drop}
            self.assertEqual(c["authority"], oa)
            if not sim_only():
                self.assertEqual((c["episodes"], c["duration_s"], c["tail_s"]), (o["episodes"], o["duration_s"], o["tail_s"]))
        self.assertEqual(cfg("expSH_queue")["faults"]["device_args"], ["-shadow-reads"])


class K18Qos2(unittest.TestCase):
    def test_row(self):
        q1 = json.loads((REPO / "conformance" / "configs" / "mosquitto-rpc.json").read_text())
        q2 = json.loads((REPO / "conformance" / "configs" / "mosquitto-rpc-qos2.json").read_text())
        self.assertEqual((q2["adapter"], q2["adapter_config"]["qos"]), ("mqtt5_rpc_qos2", 2))
        for k in ("probes", "params", "repetitions", "faults", "fault_model", "system_version"):
            self.assertEqual(q2[k], q1[k], k)
        self.assertEqual({k: v for k, v in q2["adapter_config"].items() if k != "qos"}, {k: v for k, v in q1["adapter_config"].items() if k != "qos"})
        st = er.STACKS["mosquitto-qos2"]
        self.assertEqual((st["configs"], st["owner"], st["device_script"]), (["mosquitto-rpc-qos2"], "mosquitto", "mqtt_device_qos2.py"))
        dev = (REPO / "conformance" / "reference" / "mqtt_device_qos2.py").read_text()
        self.assertIn("import mqtt_device as base", dev)
        self.assertIn("QOS = 2", dev)
        import paper_tables as PT
        self.assertEqual(PT.short_name({"id": "meas-eclipse-mosquitto-2-x-request-response-publish-at-qos-2-corr", "system": "", "operation": ""}),
                         ("Mosquitto", "MQTT~5 request/response, QoS~2"))
        self.assertEqual(PT.short_name({"id": "meas-eclipse-mosquitto-2-x-request-response-publish-correlation-d", "system": "", "operation": ""}),
                         ("Mosquitto", "MQTT~5 request/response"))


def fake_fw_cell(run: Path, ep: int, contract: str, policy: str, offered: int, fences: list, effects: list, counted=None, broker=None):
    cell = run / "cells" / f"ep{ep}" / contract / policy
    (cell / "raw" / "C").mkdir(parents=True)
    (cell / "raw" / "E").mkdir(parents=True)
    (cell / "raw" / "A").mkdir(parents=True)
    (cell / "row.json").write_text(json.dumps({"cell_status": "VALID", "offered": offered, "queries": 3, "stops": 0, "fences": len(fences)}))
    ops = [{"op": "effect", "cid": c, "seq": q} for c, q in effects] + [{"op": "query", "cid": "x"}, {"op": "query", "cid": "y", "cached": True}]
    ops += [{"op": "barrier", "cutoff": cut, "result": res, "queried": 1} for cut, res in fences]
    (cell / "raw" / "C" / "gateway0.jsonl").write_text("".join(json.dumps(o) + "\n" for o in ops))
    (cell / "raw" / "C" / "gateway0.state.jsonl").write_text("".join(json.dumps({"k": "fwd", "cid": c, "seq": q}) + "\n" for c, q in effects))
    (cell / "raw" / "E" / "plant.jsonl").write_text("".join(json.dumps({"device": "pump0-1", "cmd": c}) + "\n" for c, _ in effects))
    if counted is not None:
        (cell / "raw" / "E" / "flash_writes.json").write_text(json.dumps({"files": [{"value_writes": counted, "complete": True}]}))
    if broker is not None:
        (cell / "raw" / "C" / "broker_sys.json").write_text(json.dumps({"cell": broker}))


class K19Cost(unittest.TestCase):
    def test_cost(self):
        import cost086 as K
        t = tmpdir(self)
        run = t / "campaigns" / "expSR-zephyr-w6" / "20261106T000000"
        run.mkdir(parents=True)
        (run / "config.json").write_text(json.dumps({"real_owner": {"system": "zephyr", "contracts": {
            "ZEPHYR_PLAIN_DIRECT_QOS0": {"mode": "PLAIN"}, "ZEPHYR_F_DIRECT": {"mode": "CUTOFF"}}}}))
        sys_ = {"$SYS/broker/messages/received": 100, "$SYS/broker/messages/sent": 120, "$SYS/broker/publish/messages/received": 40,
                "$SYS/broker/publish/messages/sent": 50}
        effs = [(f"c{i}", i) for i in range(1, 71)]  # claim mark: seq 1 and 66
        for ep in (0, 1):
            fake_fw_cell(run, ep, "ZEPHYR_PLAIN_DIRECT_QOS0", "EVICON", 100, [], effs[:50], counted={}, broker=sys_)
            fake_fw_cell(run, ep, "ZEPHYR_F_DIRECT", "EVICON", 100, [(10, "OK"), (10, "OK"), (30, "QUERY_TIMEOUT"), (40, "TIMEOUT" if ep else "OK")], effs,
                         counted={"evicon/hw": 2, "evicon/cutoff": 3}, broker=dict(sys_, **{"$SYS/broker/messages/sent": 150}))
        res = K.analyse({"SRz6": run})
        r = res["runs"]["SRz6"]
        self.assertTrue(r["available"])
        A, B = K.cell_costs(run, "ZEPHYR_PLAIN_DIRECT_QOS0", "EVICON"), K.cell_costs(run, "ZEPHYR_F_DIRECT", "EVICON")
        self.assertEqual(A[0]["flash_writes_derived"], 0)  # plain mode: no durable write
        self.assertEqual(B[0]["flash_derived"]["hw"], 2)
        self.assertEqual(B[0]["flash_derived"]["cutoff"], 3)  # 10, 30, 40: distinct installed cutoffs
        self.assertIsNone(B[1]["flash_derived"]["cutoff"])  # an unanswered fence: not recoverable exactly
        self.assertEqual(B[1]["flash_derived"]["cutoff_bounds"], [2, 3])
        self.assertEqual(B[0]["flash_writes_counted"], 5)
        self.assertEqual(B[0]["gateway_requests"], 70 + 1 + 4 + 4)  # effects, uncached query, fences, per-identity queries of answered fences
        self.assertEqual(B[1]["gateway_requests"], 70 + 1 + 4 + 3)
        self.assertEqual(B[0]["broker_packets"], 250)
        self.assertAlmostEqual(r["paired"]["broker_packets"]["mean"], 0.3)
        self.assertEqual(r["b"]["flash_check"], {"cells_both": 1, "cells_equal": 1})
        self.assertIn("SRz6", K.markdown(res))
        self.assertFalse(res["runs"]["PMz6"]["available"])

    def test_shadow_reads(self):
        """SH runs: the shadow loop's reads count as owner calls (the row's queries do not include them), with its re-publishes."""
        import cost086 as K
        t = tmpdir(self)
        run = t / "campaigns" / "expSH-queue" / "20261106T000000"
        run.mkdir(parents=True)
        (run / "config.json").write_text(json.dumps({"policies": ["EVICON", "SHADOW_RECONCILE"], "contracts": ["K2"]}))
        for pol, q, evs in (("EVICON", 30, []), ("SHADOW_RECONCILE", 0, ["action=REPUBLISH;NO_OWNER_RECORD", "action=WAIT", "action=RECORDED",
                                                                           "action=REPUBLISH;NO_ANSWER"])):
            cell = run / "cells" / "ep0" / "K2" / pol
            (cell / "raw" / "A").mkdir(parents=True)
            (cell / "row.json").write_text(json.dumps({"cell_status": "VALID", "offered": 100, "queries": q, "stops": 0, "fences": 0}))
            (cell / "raw" / "A" / "authority0.jsonl").write_text("".join(json.dumps({"event": "SHADOW_RECONCILE_RESULT", "reason": r}) + "\n" for r in evs)
                                                                   + json.dumps({"event": "OTHER"}) + "\n")
        a, b = K.cell_costs(run, "K2", "EVICON")[0], K.cell_costs(run, "K2", "SHADOW_RECONCILE")[0]
        self.assertEqual((a["owner_calls"], a["shadow_reads"], a["republishes"]), (30, 0, 0))
        self.assertEqual((b["owner_calls"], b["shadow_reads"], b["republishes"]), (4, 4, 2))
        self.assertNotIn("shadow_reads", K.cell_costs(Path(str(run).replace("expSH-queue", "none")), "K2", "EVICON").get(0, {}))


class K20Nvs(unittest.TestCase):
    def test_count(self):
        import nvs_flash as N
        t = tmpdir(self)
        sec = bytearray(b"\xff" * N.SECTOR)
        data, ates = bytearray(), []

        def put(ident, payload):
            off = len(data)
            data.extend(payload)
            e = ident.to_bytes(2, "little") + off.to_bytes(2, "little") + len(payload).to_bytes(2, "little") + b"\xff"
            ates.append(e + bytes([N.crc8_ccitt(e)]))
        gc = (0xFFFF).to_bytes(2, "little") + b"\x00\x00\x00\x00\xff"
        ates.append(gc + bytes([N.crc8_ccitt(gc)]))
        put(0x8000, b"\x01\x80")
        put(0xC001, (65).to_bytes(8, "little"))
        put(0x8001, b"evicon/hw")
        put(0xC001, (130).to_bytes(8, "little"))
        put(0x8000, b"\x02\x80")
        put(0xC002, (10).to_bytes(8, "little"))
        put(0x8002, b"evicon/cutoff")
        sec[:len(data)] = data
        for i, e in enumerate(ates):
            off = N.SECTOR - 2 * N.ATE - i * N.ATE
            sec[off:off + N.ATE] = e
        img = bytearray(b"\xff" * (16 * N.SECTOR))
        img[8 * N.SECTOR:9 * N.SECTOR] = sec
        (t / "fw0.bin").write_bytes(bytes(img))
        c = N.count(t / "fw0.bin")
        self.assertEqual(c["value_writes"], {"evicon/hw": 2, "evicon/cutoff": 1})
        self.assertTrue(c["complete"])
        img[10 * N.SECTOR:11 * N.SECTOR] = sec  # a second sector with entries: garbage collection may have dropped history
        (t / "fw1.bin").write_bytes(bytes(img))
        self.assertFalse(N.count(t / "fw1.bin")["complete"])


class K21AnchorReadOnly(unittest.TestCase):
    def test_no_side_files(self):
        import anchor086 as A
        t = tmpdir(self)
        db = t / "authority0.db"
        con = sqlite3.connect(db)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("CREATE TABLE domains(domain_id TEXT, anchor_json TEXT, cutoff_sequence INTEGER)")
        con.execute("CREATE TABLE commands(domain_id TEXT, command_id TEXT, sequence INTEGER, operation TEXT, value_json TEXT, status TEXT, decision TEXT)")
        con.execute("CREATE TABLE authority_barrier_history(domain_id TEXT, token TEXT, cutoff_sequence INTEGER, status TEXT, anchor_json TEXT, created_at TEXT)")
        con.execute("INSERT INTO domains VALUES('d','[0]',0)")
        con.execute("INSERT INTO commands VALUES('d','c1',1,'ADD','[3]','APPLIED','ADMITTED')")
        con.execute("INSERT INTO authority_barrier_history VALUES('d','t1',1,'COMPLETE','[3]','x')")
        con.commit()
        con.close()
        for f in (t / "authority0.db-wal", t / "authority0.db-shm"):
            if f.exists():
                f.unlink()
        before = sorted(p.name for p in t.iterdir())
        j = A.journal_fences(db)
        self.assertEqual((j["complete"], j["compared"], j["mismatch"], j["error"]), (1, 1, [], ""))
        self.assertEqual(sorted(p.name for p in t.iterdir()), before, "no -wal or -shm file next to an archived journal")


class K22ZephyrCounters(unittest.TestCase):
    def test_runner(self):
        src = (REPO / "evaluation" / "campaign_zephyr_ext086.py").read_text()
        self.assertIn("import campaign_zephyr_ext077 as Z77", src)
        self.assertIn("Z77.Ext077Testbed = Ext086Testbed", src)
        cls = src[src.index("class Ext086Testbed"):src.index("def main(")]
        self.assertEqual(re.findall(r"\n    def (\w+)\(", cls), ["preflight", "stop", "_flash_counts", "fetch"])
        for m in ("preflight", "stop", "fetch"):
            self.assertIn(f"super().{m}(", cls)
        self.assertIn("except Exception", cls)  # a counter never makes a cell INVALID


class K23Qos2Expiry(unittest.TestCase):
    """The directed expiry check of the QoS 2 row: P9T's delay, the summary per delay, the driver step, the report copy,
    the paper macros and the plan's frozen list."""

    def test_check(self):
        sys.path.insert(0, str(REPO / "conformance" / "reference"))
        import qos2_expiry_check as Q
        self.assertEqual(Q.p9t_delay_s(30.0, 1.0), 31.5)  # probes._late_then_backward: expiry + settle + 0.5 s
        rows = [{"delay_s": 22.5, "effects_final": 1, "first_effect_s": 45.2, "late_effect": True},
                {"delay_s": 22.5, "effects_final": 1, "first_effect_s": 45.6, "late_effect": True},
                {"delay_s": 31.5, "effects_final": 0, "first_effect_s": None, "late_effect": False}]
        res = Q.summarise(rows, 30.0, 31.5)
        short, long_ = res["cases"]
        self.assertEqual((short["trials"], short["with_effect"], short["late_effects"], short["first_effect_s_median"], short["p9t_case"]),
                         (2, 2, 2, 45.4, False))
        self.assertEqual((long_["trials"], long_["with_effect"], long_["late_effects"], long_["p9t_case"]), (1, 0, 0, True))
        self.assertFalse(res["bounds_execution_by_expiry_from_send"])
        res["trials"] = [dict(r, command_id="qx", client="TIMEOUT") for r in rows]
        md = Q.markdown(res)
        self.assertIn("| 22.5 | 0.75 | 2 | 2 | 2 | 0 | 45.4 (45.2-45.6) | no |", md)
        ok = Q.summarise([dict(rows[0], effects_final=0, first_effect_s=None, late_effect=False)], 30.0, 31.5)
        self.assertTrue(ok["bounds_execution_by_expiry_from_send"])
        # an effect seen first after the expiry is late; one seen within it is not
        src = (REPO / "conformance" / "reference" / "qos2_expiry_check.py").read_text()
        self.assertIn('"late_effect": bool(final > 0 and (first is None or first > expiry_s))', src)
        self.assertIn("faults.delay(UP, int(delay_s * 1000))", src)

    def test_wiring(self):
        self.assertTrue(er.STACKS["mosquitto-qos2"].get("expiry_check"))
        drv = (REPO / "evicon_review.py").read_text()
        self.assertIn('"conformance/reference/qos2_expiry_check.py", "--config", str(cfg)', drv)
        self.assertIn('if m.spec.get("expiry_check"):', drv)
        self.assertIn('glob("measure/mosquitto-qos2/qos2_expiry.json")', drv)
        plan = (REPO / PLAN).read_text()
        fe = re.search(r"\n```frozen-extra\n(.*?)```", plan, re.S).group(1).split()
        self.assertIn("conformance/reference/qos2_expiry_check.py", fe)
        self.assertIn("directed expiry check", plan[plan.index("- **QB (the QoS 2 row).**"):])
        import paper_tables as PT
        with tempfile.TemporaryDirectory() as td:
            rep = Path(td)
            (rep / "qb086").mkdir()
            (rep / "qb086" / "qos2_expiry.json").write_text(json.dumps({"expiry_s": 30.0, "cases": [
                {"delay_s": 31.5, "trials": 3, "with_effect": 0, "late_effects": 0, "first_effect_s_median": None},
                {"delay_s": 22.5, "trials": 3, "with_effect": 3, "late_effects": 3, "first_effect_s_median": 45.3}]}))
            src = (REPO / "evaluation" / "paper_tables.py").read_text()
            self.assertIn('N[f"evQosTwoExp{tag}Late"] = str(c["late_effects"])', src)
            self.assertIn('qx = report / "qb086" / "qos2_expiry.json"', src)


if __name__ == "__main__":
    unittest.main()
