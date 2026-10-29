"""Release 077 (PREREG_review_077.md): the baseline runner, the cross-policy comparisons, the safety total of
the baselines, the driver's runner choice, the latency control, the simulator sensitivity sweep and the testbed
power script.

  B1  campaign_ext077 adds RELEASE_ON_TIMEOUT and RELEASE_ON_READ to campaign.py and nothing else
  B2  the driver runs a config with a baseline on campaign_ext077.py, every other one on campaign.py, and
      plans the baseline's violations (and only those)
  B3  policy_compare pairs episodes only where the schedule files are byte-identical and counts violations
  B4  safety_total counts the baselines' cells as unsafe by design (079; 077: planned controls), EVICON's in the same run as guarded
  B5  latency_control splits the latency correctly and runs against evicon-noop end to end
  B6  sim_sensitivity077's regime patches
  B7  the power script's rules are the plan's
  B8  late_attribution splits the late effects by the attempt that took effect; the validity cut of resends
      (authority.resend_until) reaches every authority of the configs that set it
  B9  experiment Q: the 077 Zephyr runner's config rules, the driver's runner choice and planned violations
  B10 experiment TB (ThingsBoard runner config rules, planned violations), WAN, C16 and Scale configs, the MCU and
      the authenticated firmware stacks of the driver, the action-cost sweep's patches
  B11 the authenticated firmware's HMAC-SHA256 against the FIPS and RFC 4231 vectors (host build), and its derivation
      from the frozen main.c (the frozen sources unchanged)
"""
import csv
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "evaluation"))
sys.path.insert(0, str(REPO))

import campaign as C  # noqa: E402

FIELDS = ["regime", "contract", "policy", "episode", "devices", "cell_status", "scope", "offered", "admitted", "clean_fulfilled",
          "bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied", "late_effects", "reissues",
          "unresolved_at_end"]


def write_run(d: Path, rows: list, schedules: dict, cfg: dict | None = None) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "runtime_episodes.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, 0) for k in FIELDS})
    for ep, text in schedules.items():
        (d / f"schedule_ep{ep}.json").write_text(text)
    if cfg is not None:
        (d / "config.json").write_text(json.dumps(cfg))
    return d


def row(contract, policy, ep, offered=100, clean=50, admitted=60, status="VALID", **kw):
    r = {"regime": "x", "contract": contract, "policy": policy, "episode": ep, "devices": 4, "cell_status": status, "scope": "TESTBED",
         "offered": offered, "admitted": admitted, "clean_fulfilled": clean}
    r.update(kw)
    return r


class B1Runner(unittest.TestCase):
    def test_runner_adds_two_policies_only(self):
        import campaign_ext077 as X
        frozen_legacy, frozen_args = C.LEGACY, C.policy_args
        try:
            X.install()
            self.assertEqual(set(C.LEGACY) - set(frozen_legacy), {"RELEASE_ON_TIMEOUT", "RELEASE_ON_READ"})
            for p in X.BASELINES:
                self.assertEqual(C.policy_args(p, {"release_after": "10s", "resolve_sweep": "1s"}),
                                 ["-policy", p, "-release-after", "10s", "-resolve-sweep", "1s"])
            a = json.loads((REPO / "evaluation" / "campaigns" / "expW_queue_w6.json").read_text())["authority"]
            for p in ("EVICON", "RES_PERIODIC", "M1"):
                self.assertEqual(C.policy_args(p, a), frozen_args(p, a), p)
            # the validity cut of resends: appended to every policy's arguments when the config sets it
            cut = dict(a, resend_until="90s")
            for p in ("EVICON", "RES_PERIODIC"):
                self.assertEqual(C.policy_args(p, cut), frozen_args(p, a) + ["-resend-until", "90s"], p)
            self.assertEqual(C.policy_args("RELEASE_ON_READ", dict(cut, release_after="10s", resolve_sweep="1s")),
                             ["-policy", "RELEASE_ON_READ", "-release-after", "10s", "-resolve-sweep", "1s", "-resend-until", "90s"])
            X.install()  # idempotent
            self.assertEqual(len(C.LEGACY), len(frozen_legacy) + 2)
        finally:
            C.LEGACY, C.policy_args = frozen_legacy, frozen_args

    def test_campaign_py_unchanged(self):
        # frozen by the 067-075 plans (and again by the 077 plan)
        self.assertEqual(hashlib.sha256((REPO / "evaluation" / "campaign.py").read_bytes()).hexdigest(),
                         "7d928b8062787b6ba5985003f54fd1e1ae2398afb29af986c1205a8dffed0962")


class B2Driver(unittest.TestCase):
    def test_runner_choice_and_planned_violations(self):
        import evicon_review as er
        for name, want in (("expRT_queue", ("evaluation/campaign_ext077.py", ["RELEASE_ON_TIMEOUT"])),
                           ("expRR_direct", ("evaluation/campaign_ext077.py", ["RELEASE_ON_READ"])),
                           ("expK2_w6", ("evaluation/campaign_ext077.py", [])), ("expL2_sleepy", ("evaluation/campaign_ext077.py", [])),
                           ("expO_noorphan", ("evaluation/campaign.py", [])), ("expW_queue_w6", ("evaluation/campaign.py", []))):
            cfg = json.loads((REPO / "evaluation" / "campaigns" / f"{name}.json").read_text())
            self.assertEqual(er._emulated_runner(cfg), want, name)
        self.assertEqual(er.RELEASE_BASELINES, ("RELEASE_ON_TIMEOUT", "RELEASE_ON_READ"))
        self.assertIn("policy_compare.py", (REPO / "evicon_review.py").read_text())

    def test_new_configs_load(self):
        for name in ("expR_minio_winf", "expR_zephyr_winf", "expO_noorphan", "expK2_w6", "expV_mild", "expRT_queue", "expRT_direct",
                     "expRR_queue", "expRR_direct", "expL2_sleepy"):
            path = REPO / "evaluation" / "campaigns" / f"{name}.json"
            cfg = json.loads(path.read_text())
            if "real_owner" not in cfg:  # the real-owner configs are checked by their runners (C, C3 tests)
                import campaign_ext077 as X
                legacy, args = C.LEGACY, C.policy_args
                try:
                    X.install()  # as the runner does before campaign.main
                    cfg = C.load_config(path)
                finally:
                    C.LEGACY, C.policy_args = legacy, args
            self.assertEqual(cfg["preregistration"]["path"], "evaluation/prereg/PREREG_review_077.md", name)


class B3PolicyCompare(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_pairs_only_identical_schedules(self):
        import policy_compare as P
        same = {0: '{"a": 0}', 1: '{"a": 1}', 2: '{"a": 2}'}
        a = write_run(self.tmp / "a", [row("K0", "EVICON", e, clean=30 + e) for e in range(3)], same)
        b = write_run(self.tmp / "b", [row("K0", "RELEASE_ON_TIMEOUT", e, clean=70 + e, false_stopped=2, bound_violations=1) for e in range(3)],
                      {0: same[0], 1: same[1], 2: '{"a": "other"}'})
        spec = {"name": "t", "comparisons": [{"id": "X", "a": {"run": "A", "contract": "K0", "policy": "EVICON"},
                                              "b": {"run": "B", "contract": "K0", "policy": "RELEASE_ON_TIMEOUT"},
                                              "metrics": ["clean_pp", "violations"]},
                                             {"id": "Y", "a": {"run": "A", "contract": "K0", "policy": "EVICON"},
                                              "b": {"run": "Z", "contract": "K0", "policy": "EVICON"}, "metrics": ["clean_pp"]}]}
        res = P.analyse(spec, {"A": str(a), "B": str(b)})
        x = res["comparisons"][0]
        self.assertEqual(x["differences"]["clean_pp"]["n"], 2)  # episode 2 has another schedule
        self.assertAlmostEqual(x["differences"]["clean_pp"]["mean"], 40.0)
        self.assertEqual(x["differences"]["violations"]["mean"], 3.0)
        self.assertEqual((x["arm_b"]["violations"], x["arm_b"]["cells_with_violation"], x["arm_b"]["false_stopped"]), (9, 3, 6))
        self.assertEqual(res["comparisons"][1], {"id": "Y", "status": "NOT RUN"})
        md = P.markdown(res)
        self.assertIn("| X |", md)
        self.assertIn("NOT RUN", md)

    def test_release_counts_from_traces(self):
        import policy_compare as P
        run = write_run(self.tmp / "r", [row("K2", "RELEASE_ON_READ", 0), row("K2", "RELEASE_ON_READ", 1)], {0: "s", 1: "t"})
        ev = lambda c, why: json.dumps({"event": "RELEASE_ON_READ", "domain_id": "pump0-1", "command_id": c,  # noqa: E731
                                        "reason": f"STOPPED_WITHOUT_OWNER_EVIDENCE:RELEASE_ON_READ_NOT_REPORTED_{why}"})
        for ep, lines in ((0, [ev("c1", "UNREACHABLE"), ev("c2", "NOT_FOUND"), '{"event":"EVICON_QUERY_RESULT"}']), (1, [ev("c1", "UNREACHABLE")])):
            for host in ("A", "E"):  # a single-host run holds the same trace twice
                d = run / "cells" / f"ep{ep}" / "K2" / "RELEASE_ON_READ" / "raw" / host
                d.mkdir(parents=True)
                (d / "authority0.jsonl").write_text("\n".join(lines) + "\n")
        self.assertEqual(P.releases(run, {"contract": "K2", "policy": "RELEASE_ON_READ"}),
                         {"RELEASE_ON_READ_NOT_REPORTED_UNREACHABLE": 2, "RELEASE_ON_READ_NOT_REPORTED_NOT_FOUND": 1})
        self.assertEqual(P.releases(run, {"contract": "K2", "policy": "EVICON"}), {})

    def test_invalid_cells_ignored_and_spec_of_plan(self):
        import policy_compare as P
        a = write_run(self.tmp / "a", [row("K2", "EVICON", 0), row("K2", "RELEASE_ON_READ", 0, status="INVALID: died")], {0: "s"})
        cells = P.load_run(a)
        self.assertEqual(set(cells), {("K2", "EVICON", 0)})
        spec = P.spec_of(REPO / "evaluation" / "prereg" / "PREREG_review_077.md")
        self.assertEqual({c["id"] for c in spec["comparisons"]}, {"RT-E1", "RT-E2", "RT-E3", "RT-E4", "RR-E1", "RR-E2", "RR-E3", "TB-E1", "TB-E2"})
        names = {json.loads(p.read_text())["name"] for p in (REPO / "evaluation" / "campaigns").glob("*.json")}
        self.assertTrue({r["campaign"] for r in spec["runs"].values()} <= names)


class B4SafetyTotal(unittest.TestCase):
    def test_baseline_cells_are_planned_controls(self):
        import safety_total as T
        tmp = Path(tempfile.mkdtemp())
        try:
            cfg = json.loads((REPO / "evaluation" / "campaigns" / "expRR_queue.json").read_text())
            run = write_run(tmp / "expRR-queue" / "20261026T000000",
                            [row("K2", "RELEASE_ON_READ", 0, false_stopped=3, logical_duplicates=1), row("K2", "EVICON", 0)], {0: "s"}, cfg)
            (run / "manifest.json").write_text(json.dumps({"release": "077", "scope": "TESTBED"}))
            out = T.read_run(run)
            self.assertEqual(out["guarded"]["cells"], 1)
            self.assertEqual(out["guarded"].get("bound_violations", 0) + out["guarded"].get("false_stopped", 0), 0)
            # release 079: unsafe by design (a violation allowed, not required in every cell), not a negative control
            self.assertIn("K2/RELEASE_ON_READ", out["unsafe"])
            self.assertEqual(out["unsafe"]["K2/RELEASE_ON_READ"]["false_stopped"], 3)
            self.assertEqual(dict(out["control"]), {})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class B5LatencyControl(unittest.TestCase):
    def test_analyse_splits_latency(self):
        import latency_control as L
        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / "c.jsonl").write_text(json.dumps({"cid": "x1", "rate_per_s": 5.0, "arrival": 100.0, "sent": 100.010, "admitted": 100.030}) + "\n"
                                         + json.dumps({"cid": "x2", "rate_per_s": 5.0, "arrival": 101.0, "sent": 101.002, "admitted": None}) + "\n")
            (tmp / "n.jsonl").write_text(json.dumps({"method": "POST", "command_id": "x1", "handler_start_unix_ns": int(100.015e9),
                                                     "reply_unix_ns": int(100.016e9)}) + "\n")
            res = L.analyse([tmp / "c.jsonl"], [tmp / "n.jsonl"])
            r = res["loads"][0]
            self.assertEqual((r["offered"], r["admitted"], r["unmatched"]), (2, 1, 0))
            self.assertAlmostEqual(r["gen_ms"]["p50"], 10.0, places=1)
            self.assertAlmostEqual(r["net_ms"]["p50"], 5.0, places=1)
            self.assertAlmostEqual(r["client_ms"]["p50"], 15.0, places=1)
            self.assertAlmostEqual(r["admit_ms"]["p50"], 30.0, places=1)
            self.assertAlmostEqual(r["noop_ms"]["p50"], 1.0, places=1)
            self.assertIn("| 5 | 2 | 1 |", L.markdown(res))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_against_evicon_noop(self):
        import latency_control as L
        exe = REPO / "bin" / "evicon-noop"
        if not exe.exists():
            self.skipTest("bin/evicon-noop not built")
        tmp = Path(tempfile.mkdtemp())
        p = None
        try:
            cfg = json.loads((REPO / "evaluation" / "campaigns" / "rq4_load_r5.json").read_text())
            cfg.update(devices=2, tail_s=1)
            ports = L._free_ports(2)
            p = subprocess.Popen([str(exe), "-listen", ",".join(f"127.0.0.1:{x}" for x in ports), "-log", str(tmp / "noop.jsonl")],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            urls = [f"http://127.0.0.1:{x}" for x in ports]
            for u in urls:
                C.wait_http(f"{u}/healthz", timeout=10)
            n = L.generate(cfg, 0, urls, tmp / "clients.jsonl", duration_s=3)
            self.assertGreater(n, 5)
        finally:
            if p:
                p.terminate()
                p.wait(timeout=10)
        try:
            time.sleep(0.1)
            res = L.analyse([tmp / "clients.jsonl"], [tmp / "noop.jsonl"])
            r = res["loads"][0]
            self.assertEqual(r["admitted"], n)
            self.assertEqual(r["unmatched"], 0)
            self.assertLess(r["noop_ms"]["p50"], 50)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class B6Sensitivity(unittest.TestCase):
    def test_patches(self):
        import sim_sensitivity077 as S
        base = json.loads((REPO / "experiments" / "sim" / "iot_dosing_testbed.json").read_text())
        f0 = S._offline_share(base["owner"])
        self.assertAlmostEqual(f0, 0.205, places=2)
        c = S.patch(base, "scale", 0.5)
        self.assertAlmostEqual(c["network"]["req_loss_prob"], base["network"]["req_loss_prob"] / 2)
        self.assertAlmostEqual(c["owner"]["crash_mid_prob"], base["owner"]["crash_mid_prob"] / 2)
        self.assertAlmostEqual(S._offline_share(c["owner"]), f0 / 2, places=3)
        c = S.patch(base, "offline", 0.30)
        self.assertAlmostEqual(S._offline_share(c["owner"]), 0.30, places=3)
        self.assertEqual(c["owner"]["offline_mean_ms"], base["owner"]["offline_mean_ms"])
        c = S.patch(base, "outage", 60000)
        self.assertEqual(c["owner"]["offline_mean_ms"], 60000)
        self.assertAlmostEqual(S._offline_share(c["owner"]), f0, places=3)
        c = S.patch(base, "offline", 0.0)["owner"]
        self.assertEqual((c["offline_mean_ms"], c["online_mean_ms"]), (0, 0))  # the simulator's "always online"
        self.assertEqual(S._offline_share(c), 0.0)
        self.assertEqual(S.patch(base, "crash", 0.02)["owner"]["crash_mid_prob"], 0.02)
        self.assertEqual(base, json.loads((REPO / "experiments" / "sim" / "iot_dosing_testbed.json").read_text()))  # not mutated
        self.assertEqual(len(list(S.points())), sum(len(v) for v in S.FACTORS.values()) * 4)


class B7Power(unittest.TestCase):
    def test_rules_are_the_plans(self):
        import re
        import power_review077_testbed as W
        import sim_review077 as SR
        spec = json.loads(re.search(r"```pairs-spec\n(.*?)```", (REPO / "evaluation" / "prereg" / "PREREG_review_077.md").read_text(),
                                    re.S).group(1))
        plan = {(h["id"], h["family"], h["test"], float(h["margin"])) for h in spec["hypotheses"] if "family" in h}
        script = {(rid, fam, test, float(m)) for fam, rules in W.FAMILIES.items() for rid, test, m, n, tpl, sim in rules}
        sim = {(h[0], h[1], h[7], float(h[8])) for h in SR.HYPOTHESES}
        self.assertEqual(plan, script)
        self.assertEqual(plan, sim)
        for fam, rules in W.FAMILIES.items():
            for rid, *_ , tpl, _sim in rules:
                self.assertIn(tpl, W.TEMPLATES)



class B8LateAttribution(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, effects_files: bool = True, name: str = ""):
        run = self.tmp / (name or ("run" if effects_files else "old"))
        cell = run / "cells" / "ep0" / "K2" / "EVICON"
        # intents arrive at t = 100 s, validity 45 s: an effect after 145 s is late
        clients = [{"cid": "a", "arrival": 100.0, "ids": ["a#1"]},           # on time
                   {"cid": "b", "arrival": 100.0, "ids": ["b#1"]},           # late, first send delivered late
                   {"cid": "c", "arrival": 100.0, "ids": ["c#1"]},           # late, a resend took effect
                   {"cid": "d", "arrival": 100.0, "ids": ["d#1", "d#2"]},    # first effect (on time) under its second identity
                   {"cid": "e", "arrival": 100.0, "ids": ["e#1"]}]           # no effect
        cell.mkdir(parents=True)
        (cell / "clients.jsonl").write_text("".join(json.dumps(c) + "\n" for c in clients))
        plant = [("a#1", 120), ("b#1", 150), ("c#1", 160), ("d#2", 130), ("c#1", 170)]
        effects = [("a#1", 1, 120), ("b#1", 1, 150), ("c#1", 3, 160), ("d#2", 1, 130), ("c#1", 4, 170)]
        for host in ("A", "E"):  # a single-host run fetches the same directory twice
            raw = cell / "raw" / host
            (raw / "dev0").mkdir(parents=True)
            (raw / "plant.jsonl").write_text("".join(json.dumps({"seq": i, "t_ns": int(t * 1e9), "device": "D0", "cmd": c, "value": 1}) + "\n"
                                                     for i, (c, t) in enumerate(plant)))
            if effects_files and host == "A":
                # the authority's dispatch attempts: c#1 attempt 3 sent at 140 s (within the 145 s validity)
                from datetime import datetime, timezone
                wt = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "123Z"
                (raw / "authority0.jsonl").write_text("".join(json.dumps({"event": "OUTBOX_DISPATCH_ATTEMPT", "command_id": c, "attempt": a,
                                                                          "wall_time": wt(t)}) + "\n"
                                                              for c, a, t in (("b#1", 1, 101), ("c#1", 1, 101), ("c#1", 2, 120), ("c#1", 3, 140.5))))
            if effects_files:
                (raw / "dev0" / "D0.effects.jsonl").write_text("".join(json.dumps({"command_id": c, "attempt": a, "at_unix_ms": t * 1000}) + "\n"
                                                                       for c, a, t in effects))
        write_run(run, [row("K2", "EVICON", 0, late_effects=2)], {0: "{}"},
                  {"name": "x", "workload": {"deadline_s": 30, "validity_s": 45}})
        return run

    def test_split_by_attempt(self):
        import late_attribution as L
        res = L.run_attribution(self._run())
        g = res["groups"]["K2/EVICON"]
        self.assertEqual((g["late_effects"], g["late_first_send"], g["late_resend"], g.get("late_unknown", 0)), (2, 1, 1, 0))
        self.assertEqual((g["late_resend_sent_in_validity"], g.get("late_resend_sent_after_validity", 0)), (1, 0))
        self.assertEqual((g["effects_first_send"], g["effects_resend"]), (3, 2))  # copies counted once
        self.assertEqual(res["late_mismatch"], [])
        md = L.markdown([res])
        self.assertIn("| x | K2/EVICON | 1 | 2 | 1 | 1 (1 / 0) | 0 (0) |", md)

    def test_runs_without_effect_records(self):
        import late_attribution as L
        g = L.run_attribution(self._run(effects_files=False))["groups"]["K2/EVICON"]
        self.assertEqual((g["late_effects"], g.get("late_first_send", 0), g["late_unknown"]), (2, 0, 2))
        # a run before 077 (no attempt numbers): c#1 was dispatched at 150 s, after its validity (145 s) and before
        # its effect (160 s): the runtime may have caused that late effect; b#1 was not dispatched after its validity
        run = self._run(effects_files=False, name="old2")
        from datetime import datetime, timezone
        wt = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
        tr = run / "cells" / "ep0" / "K2" / "EVICON" / "raw" / "A" / "authority0.jsonl"
        tr.write_text("".join(json.dumps({"event": "OUTBOX_DISPATCH_ATTEMPT", "command_id": c, "wall_time": wt(t)}) + "\n"
                              for c, t in (("b#1", 101), ("c#1", 101), ("c#1", 150))))
        g = L.run_attribution(run)["groups"]["K2/EVICON"]
        self.assertEqual((g["late_unknown"], g["late_unknown_dispatched_after_validity"]), (2, 1))

    def test_resend_cut_in_the_planned_configs(self):
        for name in ("expK2_w6", "expRR_queue", "expRR_direct", "expL2_sleepy"):
            cfg = json.loads((REPO / "evaluation" / "campaigns" / f"{name}.json").read_text())
            # the client submits an intent no later than its deadline: the cut at validity - deadline after the
            # command's creation is never later than the intent's validity
            w = cfg["workload"]
            self.assertEqual(cfg["authority"]["resend_until"], "%ds" % (w["validity_s"] - w["deadline_s"]), name)



class B9ExperimentQ(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _cfg(self):
        from test_campaign_zephyr_ext import measured, write
        c = json.loads((REPO / "evaluation" / "campaigns" / "expQ_zephyr.json").read_text())
        c["mode"] = "local"
        c.pop("remote")
        c.pop("preregistration")
        c["real_owner"]["broker"] = "127.0.0.1:1883"
        c["real_owner"]["firmware"] = "/x/zephyr-cutoff.exe"
        for name, spec in c["real_owner"]["contracts"].items():
            spec["contract_file"] = str(write(self.tmp, measured(spec["mode"]), f"{name}.json"))
        return c

    def test_load_config(self):
        import campaign_zephyr_ext077 as Q
        c = self._cfg()
        p = self.tmp / "q.json"
        p.write_text(json.dumps(c))
        self.assertEqual(Q.load_config(p)["contracts"], ["ZEPHYR_PLAIN_QOS1", "ZEPHYR_PLAIN_QOS0", "ZEPHYR_CUTOFF_QOS1"])
        for mut in (lambda d: d["real_owner"]["contracts"]["ZEPHYR_PLAIN_QOS1"].update(binding="qos2"),
                    lambda d: d["real_owner"].update(offline_cut="yes"),
                    lambda d: d["real_owner"]["contracts"]["ZEPHYR_PLAIN_QOS0"].update(channel="direct"),  # the cut is a queue fault
                    lambda d: d["real_owner"]["contracts"]["ZEPHYR_CUTOFF_QOS1"].update(color="red")):     # the C3 runner's key check
            d = json.loads(json.dumps(c))
            mut(d)
            p.write_text(json.dumps(d))
            with self.assertRaises(ValueError):
                Q.load_config(p)

    def test_driver(self):
        import evicon_review as er
        c = json.loads((REPO / "evaluation" / "campaigns" / "expQ_zephyr.json").read_text())
        self.assertEqual(er._zephyr_runner(c), "evaluation/campaign_zephyr_ext077.py")
        self.assertEqual(er._expected_unsafe(c), {("ZEPHYR_PLAIN_QOS1", "EVICON")})
        for name in ("expC2_zephyr", "expC3_ablation"):  # the frozen runs: their runners
            c2 = json.loads((REPO / "evaluation" / "campaigns" / f"{name}.json").read_text())
            self.assertNotEqual(er._zephyr_runner(c2), "evaluation/campaign_zephyr_ext077.py", name)
            self.assertFalse(any(ct.endswith("_QOS1") for ct, _ in er._expected_unsafe(c2)), name)
        # U-Zephyr: the 077 runner and gateway with the C2/C3 binding (QoS 1) everywhere, no planned violation
        r = json.loads((REPO / "evaluation" / "campaigns" / "expR_zephyr_winf.json").read_text())
        self.assertEqual(er._zephyr_runner(r), "evaluation/campaign_zephyr_ext077.py")
        self.assertEqual({s.get("binding") for s in r["real_owner"]["contracts"].values()}, {"qos1"})
        self.assertEqual(er._expected_unsafe(r), set())

    def test_same_schedule_as_c2(self):
        c2 = json.loads((REPO / "evaluation" / "campaigns" / "expC2_zephyr.json").read_text())
        q = json.loads((REPO / "evaluation" / "campaigns" / "expQ_zephyr.json").read_text())
        for k in ("seed", "devices", "duration_s", "tail_s", "workload", "faults"):
            self.assertEqual(q[k], c2[k], k)
        self.assertEqual(q["authority"]["max_outstanding"], c2["authority"]["max_outstanding"])



class B10NewRuns(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_tb_config_and_driver(self):
        import campaign_tb as T
        import evicon_review as er
        c = json.loads((REPO / "evaluation" / "campaigns" / "expTB_thingsboard.json").read_text())
        cf = self.tmp / "tb.json"
        cf.write_text(json.dumps({"source": "MEASURED", "capabilities": ["Q_A", "T"], "fault_model": "network loss/delay, client abandon; owner up"}))
        c["real_owner"]["contracts"]["TB_RPC"]["contract_file"] = str(cf)
        c["real_owner"].update(tb_url="http://x:1", tb_mqtt="x:1")
        p = self.tmp / "c.json"
        p.write_text(json.dumps(c))
        self.assertEqual(T.load_config(p)["policies"], ["EVICON", "M1", "NAIVE"])
        cf.write_text(json.dumps({"source": "MEASURED", "capabilities": ["D_h", "Q_A", "T"]}))  # not the planned contract
        with self.assertRaises(ValueError):
            T.load_config(p)
        self.assertEqual(er._expected_unsafe(c), {("TB_RPC", "NAIVE")})
        self.assertEqual(er.TB_CAMPAIGN_CONF, ("thingsboard", "thingsboard-rpc-r0", {"Q_A", "T"}))
        self.assertIn("thingsboard-rpc-r0", er.STACKS["thingsboard"]["configs"])
        r0 = json.loads((REPO / "conformance" / "configs" / "thingsboard-rpc-r0.json").read_text())
        base = json.loads((REPO / "conformance" / "configs" / "thingsboard-rpc.json").read_text())
        self.assertEqual(r0["adapter_config"]["retries"], 0)
        self.assertEqual({k: v for k, v in r0["adapter_config"].items() if k != "retries"},
                         {k: v for k, v in base["adapter_config"].items() if k != "retries"})
        self.assertEqual((r0["probes"], r0["params"], r0["repetitions"]), (base["probes"], base["params"], base["repetitions"]))
        ref = json.loads((REPO / "evaluation" / "campaigns" / "expC_minio.json").read_text())
        for k in ("seed", "devices", "duration_s", "tail_s", "workload", "faults"):
            self.assertEqual(c[k], ref[k], k)
        # every process the TB testbed waits for is named after the role its /healthz reports (campaign.Testbed.ready
        # takes the role from the name without its index, and treats any other role as a foreign program)
        import re
        src = (REPO / "evaluation" / "campaign_tb.py").read_text()
        names = set(re.findall(r'self\.ready\(self\.\w+, [^,]+, f?"([a-z-]+)(?:\{d\})?"', src))
        self.assertEqual(names, {"plant", "mqtt-device", "gateway", "authority"})
        self.assertIn('health_body("mqtt-device"', (REPO / "conformance" / "reference" / "mqtt_device.py").read_text())
        from reference import tb_owner_gateway as TG
        self.assertEqual(TG.ROLE, "gateway")
        self.assertIn('spawn_with_secret(self.dev, f"mqtt-device{d}"', src)

    def test_wan_c16_scale_configs(self):
        legacy, args = C.LEGACY, C.policy_args
        w = C.load_config(REPO / "evaluation" / "campaigns" / "expWAN_s6.json")
        s6 = json.loads((REPO / "evaluation" / "campaigns" / "expS_w6.json").read_text())
        self.assertEqual(w["faults"]["device_args"], ["-wan-delay", "100ms", "-wan-jitter", "25ms"])
        for ep in range(w["episodes"]):
            self.assertEqual(C.schedule(w, ep), C.schedule(s6, ep))
        c16 = json.loads((REPO / "evaluation" / "campaigns" / "expC16_minio.json").read_text())
        self.assertEqual((c16["authority"]["max_outstanding"], c16["policies"]), (16, ["EVICON"]))
        for n in (8, 16, 32):
            sc = C.load_config(REPO / "evaluation" / "campaigns" / f"rq4_scale_d{n}.json")
            self.assertEqual((sc["devices"], sc["workload"]["rate_per_s"], sc["faults"]["windows_per_min"]), (n, 5.0, 0.0))
        import evicon_review as er
        self.assertEqual(er.SCALE_CONFIGS, ["rq4_scale_d8", "rq4_scale_d16", "rq4_scale_d32"])
        self.assertEqual((C.LEGACY, C.policy_args), (legacy, args))

    def test_firmware_stacks(self):
        import evicon_review as er
        self.assertEqual((er.STACKS["zephyr-mcu"]["fw_target"], er.STACKS["zephyr-mcu"]["configs"]), ("mcu", ["zephyr-mqtt-mcu"]))
        self.assertTrue(er.STACKS["zephyr-auth"]["auth_check"])
        for st in ("zephyr-mcu", "zephyr-auth"):
            self.assertIn(st, er.TESTBED_ONLY_STACKS)
        mcu = json.loads((REPO / "conformance" / "configs" / "zephyr-mqtt-mcu.json").read_text())
        nat = json.loads((REPO / "conformance" / "configs" / "zephyr-mqtt.json").read_text())
        self.assertEqual((mcu["probes"], mcu["params"], mcu["repetitions"], mcu["fault_model"]),
                         (nat["probes"], nat["params"], nat["repetitions"], nat["fault_model"]))
        self.assertEqual(er.MCU_FWD_BROKER, "10.0.2.100")

    def test_theta_patches(self):
        import sim_theta077 as TH
        base = json.loads((REPO / "experiments" / "sim" / "iot_dosing_testbed.json").read_text())
        self.assertEqual(TH.patch(base, "fence", 50)["authority"]["costs"]["fence_cost"], 50)
        self.assertEqual(TH.patch(base, "stop", 0.25)["authority"]["costs"]["stop_cost"], 0.25)
        self.assertEqual(TH.patch(base, "hold", 4)["authority"]["costs"]["hold_per_unit_per_sec"], 4)
        self.assertEqual(TH.patch(base, "immediate", 0)["authority"]["resolve"]["mode"], "IMMEDIATE")
        self.assertEqual(base["authority"]["costs"], {"hold_per_unit_per_sec": 1, "query_cost": 0.001, "stop_cost": 1, "fence_cost": 5})
        self.assertEqual({k: v for k, v in TH.TWIN.items()}, {"fence": 5.0, "stop": 1.0, "hold": 1.0})
        self.assertEqual(len(list(TH.points())), sum(len(v) for v in TH.FACTORS.values()) * 2)


class B11AuthFirmware(unittest.TestCase):
    def test_hmac_vectors(self):
        cc = shutil.which("cc") or shutil.which("gcc")
        if not cc:
            self.skipTest("no C compiler")
        fw = REPO / "firmware" / "zephyr-auth"
        exe = Path(tempfile.mkdtemp()) / "test_auth"
        r = subprocess.run([cc, "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(fw / "src"), str(fw / "tests" / "test_auth.c"),
                            str(fw / "src" / "evc_auth.c"), "-o", str(exe)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = subprocess.run([str(exe)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("ALL OK", r.stdout)

    def test_derived_from_frozen_main(self):
        main = (REPO / "firmware" / "zephyr-cutoff" / "src" / "main.c").read_text()
        auth = (REPO / "firmware" / "zephyr-auth" / "src" / "main_auth.c").read_text()
        self.assertEqual(auth.count("AUTH 077"), 7)
        # every function of the frozen main.c is in the derived file (the additions are marked, nothing removed)
        import re
        for fn in re.findall(r"^static [a-z_ 0-9*]+?(\w+)\(", main, re.M):
            self.assertIn(fn + "(", auth, fn)
        for f in ("CMakeLists.txt", "Kconfig"):
            self.assertIn("../zephyr-cutoff", (REPO / "firmware" / "zephyr-auth" / f).read_text(), f)
            self.assertIn("../zephyr-cutoff", (REPO / "firmware" / "zephyr-mcu" / f).read_text(), f)


if __name__ == "__main__":
    unittest.main()
