"""Tests of the release 068 report tools on a synthetic campaign cell: fence_audit.py (outcome
classes by error text, receipt flips in device journals, stuck domains) and timeline.py (episode
selection, folding of repeats, the untimed CUTOFF record); release 069: overhead.py's split of the
admission latency (in = client + lock + eval, decide, out); release 070: confirm_report.py's
replication section, run labels that keep two runs of one campaign apart, paper_tables.py."""
import json
import tempfile
import unittest
from pathlib import Path

from evaluation import cadence_report, confirm_report, fence_audit, overhead, paper_tables, timeline

T0 = 1792195857.0


def iso(t):
    import datetime as dt
    whole = int(t)
    frac = f"{t - whole:.9f}"[2:]
    return dt.datetime.fromtimestamp(whole, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + f".{frac}Z"


def write_cell(root: Path) -> Path:
    cell = root / "campaigns" / "expA-x" / "20261018T000000" / "cells" / "ep0" / "K0F" / "EVICON"
    (cell / "raw" / "A").mkdir(parents=True)
    (cell / "raw" / "E" / "dev2").mkdir(parents=True)
    (cell / "row.json").write_text("{}")
    a = []

    def ev(t, event, **kw):
        a.append(dict({"domain_id": "pump2-1", "event": event, "wall_time": iso(T0 + t), "monotonic_ns": int(t * 1e9)}, **kw))
    ev(0.49, "ADMISSION_RECEIVED", command_id="d2-c14", wait_ns=1_000_000)
    ev(0.5, "ADMISSION_PREPARE", command_id="d2-c14")
    ev(0.502, "DECISION_RECORDED", command_id="d2-c14", decision="ADMITTED")
    ev(0.503, "OUTBOX_DISPATCH_ATTEMPT", command_id="d2-c14")
    for i in range(5):
        ev(2.5 + 0.1 * i, "OUTBOX_RETRY_SUPPRESSED_T4", command_id="d2-c14")
    ev(2.4, "EVICON_DECISION", outcome="WAIT", reason="mode=SWEEP;FENCE_THRESHOLD_NOT_REACHED;targets=")
    ev(3.368, "EVICON_DECISION", outcome="FENCE", reason="mode=SWEEP;FENCE_THRESHOLD_REACHED;targets=d2-c14;threshold_ms=834")
    ev(3.371, "BARRIER_PAUSE_COMMITTED", reason="evicon-sweep--r40")
    ev(3.381, "EVIDENCE_COMMITTED", command_id="d2-c14", outcome="STOPPED")
    ev(3.381, "BARRIER_ANCHOR_COMMITTED", reason="evicon-sweep--r40")
    ev(3.382, "EVICON_FENCE_RESULT", outcome="200", reason="")
    for t in (10.0, 11.0, 12.0):  # a domain stuck on one contradiction (067 bug)
        ev(t, "EVICON_FENCE_RESULT", outcome="500", reason='{"error":"terminal outcome reversal for d2-c9"}')
    ev(13.0, "EVICON_FENCE_RESULT", outcome="503", reason='{"error":"DEVICE_OFFLINE"}')
    ev(3.53, "DECISION_RECORDED", command_id="d2-c14-r1", decision="ADMITTED")
    (cell / "raw" / "A" / "authority2.jsonl").write_text("\n".join(json.dumps(x) for x in sorted(a, key=lambda x: x["wall_time"])) + "\n")
    (cell / "clients.jsonl").write_text(json.dumps({"cid": "d2-c14", "device": 2, "arrival": T0 + 0.47, "value": 5, "decision": "ADMITTED",
                                                    "admitted": {"d2-c14": T0 + 0.502, "d2-c14-r1": T0 + 3.531},
                                                    "final_status": {"d2-c14": "STOPPED", "d2-c14-r1": "APPLIED"},
                                                    "terminal_seen": {"d2-c14": T0 + 3.523, "d2-c14-r1": T0 + 4.039}}) + "\n")
    (cell / "faults.jsonl").write_text(json.dumps({"device": 2, "kind": "drop_up", "actual_start": T0 + 0.2, "actual_end": T0 + 1.48}) + "\n"
                                       + json.dumps({"device": 1, "kind": "offline", "actual_start": T0 + 0.2, "actual_end": T0 + 9}) + "\n")
    (cell / "raw" / "E" / "plant.jsonl").write_text(json.dumps({"seq": 1, "t_ns": int((T0 + 3.539) * 1e9), "device": "pump2-1", "cmd": "d2-c14-r1", "value": 5}) + "\n")
    j = [{"k": "REC", "r": {"id": "d2-c9", "status": "APPLIED", "at_ms": int((T0 + 0.1) * 1000)}},
         {"k": "CUTOFF", "c": 14, "tok": "evicon-sweep--r40", "req": 14},
         {"k": "REC", "r": {"id": "d2-c9", "status": "STOPPED", "reason": "SEALED_BY_FENCE", "at_ms": int((T0 + 5) * 1000)}},
         {"k": "REC", "r": {"id": "d2-c14-r1", "status": "APPLIED", "at_ms": int((T0 + 3.54) * 1000)}}]
    (cell / "raw" / "E" / "dev2" / "pump2-1.journal").write_text("\n".join(json.dumps(x) for x in j) + "\n")
    return cell


class Reports(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cell = write_cell(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_fence_audit(self):
        rep = fence_audit.audit([self.cell.parents[3]])
        row = rep["rows"][0]
        self.assertEqual(row["fence_calls"], 5)
        self.assertEqual(row["by_outcome"].get("TERMINAL_OUTCOME_REVERSAL"), 3)  # lowercase text, no code: still counted
        self.assertEqual(row["by_outcome"].get("DEVICE_OFFLINE"), 1)
        self.assertEqual(len(row["stuck_cells"]), 1)
        self.assertEqual(row["device_receipt_flips"], 1)
        self.assertIn("receipt flip", fence_audit.markdown(rep))

    def test_timeline(self):
        tl = timeline.build(self.cell)
        self.assertEqual(tl["domain"], "pump2-1")
        self.assertEqual(tl["targets"], ["d2-c14"])
        ev = [(r["lane"], r["event"]) for r in tl["rows"]]
        for want in [("fault", "drop_up start"), ("client", "ARRIVAL"), ("authority", "BARRIER_ANCHOR_COMMITTED"),
                     ("owner", "CUTOFF"), ("truth", "EFFECT"), ("client", "TERMINAL_SEEN")]:
            self.assertIn(want, ev)
        self.assertNotIn(("fault", "offline start"), ev)  # another device's fault
        sup = [r for r in tl["rows"] if r["event"] == "OUTBOX_RETRY_SUPPRESSED_T4"]
        self.assertEqual(len(sup), 1)
        self.assertEqual(sup[0]["count"], 5)
        cut = [r for r in tl["rows"] if r["event"] == "CUTOFF"][0]
        self.assertAlmostEqual(cut["t_s"], tl["anchor_s"] - 0.009, places=2)
        self.assertIn("|", timeline.markdown(tl))

    def test_overhead_split(self):
        cl = json.loads((self.cell / "clients.jsonl").read_text())
        cl["attempts"] = 1
        (self.cell / "clients.jsonl").write_text(json.dumps(cl) + "\n")
        m = overhead.cell_metrics(self.cell)
        self.assertEqual(len(m["in_ms"]), 1)
        self.assertAlmostEqual(m["in_ms"][0], 30.0, places=2)      # arrival 0.47 -> prepare 0.5
        self.assertAlmostEqual(m["client_ms"][0], 19.0, places=2)  # arrival 0.47 -> handler start 0.489
        self.assertAlmostEqual(m["lock_ms"][0], 1.0, places=2)     # wait_ns
        self.assertAlmostEqual(m["eval_ms"][0], 10.0, places=2)    # received 0.49 -> prepare 0.5
        self.assertAlmostEqual(m["out_ms"][0], 0.0, places=2)      # decision 0.502 -> client 0.502
        decide = 2.0                                               # prepare 0.5 -> decision 0.502 (monotonic)
        self.assertAlmostEqual(m["client_ms"][0] + m["lock_ms"][0] + m["eval_ms"][0], m["in_ms"][0], places=2)
        self.assertAlmostEqual(m["in_ms"][0] + decide + m["out_ms"][0], m["admit_ms"][0], places=2)
        self.assertTrue(any(abs(x - decide) < 0.01 for x in m["decide_ms"]))
        rows = overhead.summarize([self.cell.parents[3]], [])
        self.assertIn("| in | client | lock | eval | decide | out |", overhead.markdown(rows))
        self.assertIsNone(overhead.wall(""))
        self.assertAlmostEqual(overhead.wall("2026-10-19T09:33:23.000000500Z") % 1, 5e-7, places=6)  # float epoch: microsecond resolution

    def test_timeline_cli(self):
        out = Path(self.tmp.name) / "tl"
        rc = timeline.main(["--campaign", str(self.cell.parents[3]), "--out", str(out)])
        self.assertEqual(rc, 0)
        self.assertTrue((out / "timeline.csv").exists() and (out / "timeline.md").exists())


def write_csv(path: Path, rows: list):
    import csv
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def k0_run(root: Path, stamp: str, effect: float) -> Path:
    """A minimal expA-confirm-k0 run: the frozen analysis files confirm_report.py reads."""
    run = root / "campaigns" / "expA-confirm-k0" / stamp
    A = run / "analysis"
    up = [{"policy": p, "contract": "K0", "upgraded": "K0F", "metric": "clean_fulfilled", "mean_diff": d, "p_holm": ph,
           "equivalent_at_0.05": eq, "p_tost_holm": ""} for p, d, ph, eq in
          (("EVICON", effect * 6, 1e-9, "False"), ("RES_PERIODIC", effect * 6, 1e-8, "False"), ("M1", 0.0, 1.0, "True"))]
    write_csv(A / "contract_upgrade.csv", up)
    write_csv(A / "paired.csv", [{"contract": "K0F", "policy": "EVICON", "reference": "RES_PERIODIC", "metric": "clean_fulfilled",
                                  "mean_diff": 3.0, "ci95_lo": -10, "ci95_hi": 16, "p_holm": 0.7, "equivalent_at_0.05": "False"}])
    write_csv(A / "summary.csv", [{"contract": "K0F", "policy": p, "fences_mean": f, "evicon_decisions_mean": f}
                                  for p, f in (("EVICON", 200), ("RES_PERIODIC", 50))])
    (A / "report.json").write_text(json.dumps({"groups_with_safety_violations": [], "late_effects_total": 0,
                                               "fulfilled_late_total": 3, "invalid_cells_excluded": []}))
    eff = [{"policy": p, "base": "K0", "upgraded": "K0F", "runtime": {"mean_pp": e, "ci95_lo": e - 6, "ci95_hi": e + 6, "pairs": 3}}
           for p, e in (("EVICON", effect), ("RES_PERIODIC", effect), ("M1", 0.0))]
    (A / "sim_to_real").mkdir(parents=True)
    (A / "sim_to_real" / "sim_to_real.json").write_text(json.dumps({"effects": eff, "check": {"pass": True, "tau_b_margin_pooled": 1.0}}))
    eps = []
    for ep in range(3):
        for c in ("K0", "K0F"):
            for p in ("EVICON", "RES_PERIODIC", "M1"):
                cf = 300 + (60 * effect / 10 if c == "K0F" and p != "M1" else 0) + ep
                eps.append({"cell_status": "VALID", "offered": 1000, "clean_fulfilled": cf, "contract": c, "policy": p, "episode": ep})
    write_csv(run / "runtime_episodes.csv", eps)
    return run


def k2_run(root: Path, stamp: str = "20261017T111506", equivalent: str = "True") -> Path:
    run = root / "campaigns" / "expA-confirm-k2" / stamp
    A = run / "analysis"
    write_csv(A / "contract_upgrade.csv", [{"policy": p, "contract": "K2", "upgraded": "K3F", "metric": "clean_fulfilled", "mean_diff": 0,
                                            "p_holm": 1, "equivalent_at_0.05": equivalent, "p_tost_holm": 0.001} for p in ("EVICON", "RES_PERIODIC")])
    (A / "report.json").write_text(json.dumps({"groups_with_safety_violations": [], "late_effects_total": 0, "invalid_cells_excluded": []}))
    (A / "sim_to_real").mkdir(parents=True)
    (A / "sim_to_real" / "sim_to_real.json").write_text(json.dumps({
        "effects": [{"policy": p, "base": "K2", "upgraded": "K3F", "runtime": {"mean_pp": 0.0, "ci95_lo": -0.1, "ci95_hi": 0.1, "pairs": 2}}
                    for p in ("EVICON", "RES_PERIODIC")], "check": {"pass": False, "no_sign_disagrees": True, "nonnull_agreeing": 0}}))
    return run


class Release070(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_confirm_replication(self):
        k0 = k0_run(self.root / "a", "20261016T234202", 29.9)
        good = k0_run(self.root / "b", "20261020T073331", 30.7)
        weak = k0_run(self.root / "c", "20261022T000000", 8.0)  # the CI lower bound is below the plan's 10 pp
        out = self.root / "rep"
        rc = confirm_report.main(["--k0", str(k0), "--k2", str(k2_run(self.root / "a")), "--k0-rep", str(good),
                                  "--k0-rep", str(weak), "--out", str(out)])
        self.assertEqual(rc, 0)
        res = json.loads((out / "results.json").read_text())
        self.assertTrue(res["primary_hypotheses_pass"])
        reps = res["post_hoc"]["replications"]["runs"]
        self.assertEqual([r["replicates"] for r in reps], [True, False])
        self.assertTrue(reps[1]["emulator_clean"])
        md = (out / "RESULTS.md").read_text()
        self.assertIn("## Replication of the K0 config", md)
        self.assertIn("**replicates**", md)
        self.assertIn("**does not replicate**", md)
        self.assertIn("expA-confirm-k0/20261016T234202` (primary)", md)

    def test_run_labels_keep_runs_apart(self):
        a = self.root / "campaigns" / "expA-confirm-k0" / "20261016T234202"
        b = self.root / "x" / "campaigns" / "expA-confirm-k0" / "20261020T073331"
        c = self.root / "campaigns" / "expA-confirm-k2" / "20261017T111506"
        lab = fence_audit.run_labels([a, b, c])
        self.assertEqual((lab[a], lab[b], lab[c]), ("expA-confirm-k0@20261016T234202", "expA-confirm-k0@20261020T073331",
                                                    "expA-confirm-k2"))

    def test_paper_tables(self):
        rep = self.root / "report"
        k0 = k0_run(self.root / "a", "20261016T234202", 29.9)
        confirm_report.main(["--k0", str(k0), "--k2", str(k2_run(self.root / "a")), "--k0-rep",
                             str(k0_run(self.root / "b", "20261020T073331", 30.7)), "--out", str(rep / "confirm_expA")])
        base = {"source": "MEASURED", "operation": "op", "D_h": "—", "Q_A": "—", "Q_S": "—", "F": "—", "X": "—", "T": "—",
                "declared_unverified": "—", "fault_model": "", "notes": "", "D_h_lower_s": "", "D_h_upper_s": "",
                "declared_level": "K1", "late_effect": "True",
                "measured_in": "release 070 review-20261021T101500 20261021T120000"}
        write_csv(rep / "contract_map.csv", [
            dict(base, id="meas-nats-jetstream-js-publish", system="NATS JetStream", level="K1", D_h="114s (≥)",
                 D_h_lower_s="114.0", D_h_upper_s="122.0"),
            dict(base, id="meas-thingsboard-ce-server-side-rpc-actuate-persistent-twoway", system="ThingsBoard CE", level="K2",
                 Q_A="✓", T="60s", declared_level=""),
            dict(base, id="meas-odd_system", system="Odd_System & Co", level="K3", D_h="600s (≥)", D_h_lower_s="600.0", Q_A="✓"),
            dict(base, id="hyp-x", system="X", source="HYPOTHESIS", level="K0")])
        rq4 = {"contract": "K0F", "policy": "EVICON", "cells": 3, "client_ms_p50": 50.0, "lock_ms_p50": 0.03, "eval_ms_p50": 2.5,
               "decide_ms_p50": 2.0, "authority_cpu_pct_mean": 0.7, "authority_rss_mb_max": 17.0, "db_bytes_per_cmd": 1671.0,
               "events_per_cmd": 7.0}
        (rep / "overhead").mkdir(parents=True)
        (rep / "overhead" / "overhead.json").write_text(json.dumps([
            dict(rq4, campaign="rq4-load-r1", offered_per_s=3.96),
            dict(rq4, campaign="rq4-load-r20", offered_per_s=79.8, client_ms_p50=1660.0, eval_ms_p50=4.0, decide_ms_p50=4.0,
                 authority_cpu_pct_mean=9.5),
            {"campaign": "expA-confirm-k0@20261020T073331", "contract": "K0F", "policy": "EVICON", "fences_per_cmd": 0.35,
             "fence_pause_ms_p50": 14.1, "fence_pause_ms_p95": 27053.0, "fence_pause_ms_max": 37053.0, "paused_fraction": 0.098}]))
        (rep / "footprint.txt").write_text("ring=1      30672\t    276\t   9483\t  40431\t   9def\tzephyr.elf\n"
                                           "ring=128    30684\t    276\t  19643\t  50603\t   c5ab\tzephyr.elf\n")
        self.assertEqual(paper_tables.main(["--report", str(rep)]), 0)
        P = rep / "paper"
        cm = (P / "tab_contract_map.tex").read_text()
        self.assertIn("[114, 122)\\,s", cm)
        self.assertIn("60\\,s", cm)
        self.assertIn("$\\geq$600\\,s", cm)
        self.assertIn("Odd\\_System \\& Co", cm)  # escaped
        self.assertNotIn("hyp-x", cm)
        ex = (P / "tab_expA.tex").read_text()
        self.assertIn("Replication", ex)
        fc = (P / "tab_fence_cost.tex").read_text()
        self.assertIn("K0 config, replication", fc)
        self.assertIn("27.1", fc)  # p95 in seconds
        n = json.loads((P / "numbers.json").read_text())
        self.assertEqual((n["evSystems"], n["evConfigs"], n["evMeasuredReleases"], n["evReplicates"]), ("3", "3", "070", "yes"))
        self.assertEqual((n["evAuthMinMs"], n["evAuthMaxMs"], n["evBytesPerSlot"]), ("4.5", "8.0", "80"))
        self.assertTrue(all(k.isalpha() for k in n))
        self.assertIn("\\newcommand{\\evAEviconPP}{29.9}", (P / "numbers.tex").read_text())
        for f in ("tab_rq4_overhead.tex", "tab_footprint.tex", "README.md"):
            self.assertTrue((P / f).exists(), f)
        self.assertEqual(paper_tables.num(-0.004, 2), "0.00")  # no negative zero
        self.assertEqual(paper_tables.num(-0.05, 2), "\\ensuremath{-}0.05")


def cadence_run(root: Path, name: str, stamp: str, eps: int, evicon_pp: float, fences: tuple, sched: str = "s") -> Path:
    """A minimal campaign run for cadence_report.py: K0F cells of EVICON and RES_PERIODIC."""
    run = root / "campaigns" / name / stamp
    rows = []
    for ep in range(eps):
        for pol, pp, fe, sc in (("EVICON", 60 + evicon_pp + (ep % 3), fences[0], 3700.0), ("RES_PERIODIC", 60 + (ep % 3) + (ep % 2) * 0.5,
                                                                                       fences[1], 4800.0)):
            rows.append({"regime": "r", "contract": "K0F", "policy": pol, "episode": ep, "cell_status": "VALID", "offered": 1000,
                         "clean_fulfilled": pp * 10, "stranded_resolved_unit_sec": 50, "stranded_censored_unit_sec": sc + ep,
                         "mean_time_to_terminal_s": 1.0, "fences": fe + ep % 4})
        (run / f"schedule_ep{ep}.json").parent.mkdir(parents=True, exist_ok=True)
        (run / f"schedule_ep{ep}.json").write_text(json.dumps({"ep": ep, "s": sched}))
    write_csv(run / "runtime_episodes.csv", rows)
    (run / "analysis").mkdir(parents=True, exist_ok=True)
    (run / "analysis" / "report.json").write_text(json.dumps({"groups_with_safety_violations": [], "late_effects_total": 0}))
    return run


class Release071(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_t_distribution(self):
        self.assertAlmostEqual(cadence_report.t_quantile(0.95, 23), 1.7139, places=3)
        self.assertAlmostEqual(cadence_report.t_quantile(0.975, 15), 2.1314, places=3)
        self.assertAlmostEqual(cadence_report.t_cdf(2.0, 10), 0.96331, places=4)

    def test_cadence_report(self):
        ref = cadence_run(self.root / "a", "expA-confirm-k0", "20261020T073331", 16, 1.0, (200, 50))
        b2 = cadence_run(self.root / "b", "expB2-cadence", "20261022T000000", 32, 0.5, (60, 50))
        res = cadence_report.analyse(b2, ref)
        self.assertEqual(res["H-C"]["verdict"], "equivalent")
        self.assertTrue(res["H-S"]["pass"])
        self.assertEqual(len(res["E3"]["episodes_same_schedule"]), 16)
        self.assertGreater(res["E1"]["ref"]["ratio"], res["E1"]["b2"]["ratio"])
        self.assertAlmostEqual(res["E3"]["estimands"]["clean_pp"]["mean"], 0.5, places=2)
        md = cadence_report.markdown(res)
        self.assertIn("**equivalent**", md)

    def test_cadence_report_rules(self):
        ref = cadence_run(self.root / "a", "expA-confirm-k0", "20261020T073331", 16, 1.0, (200, 50))
        far = cadence_run(self.root / "c", "expB2-cadence", "20261022T000000", 32, 8.0, (60, 50))
        self.assertTrue(cadence_report.analyse(far, ref)["H-C"]["verdict"].startswith("different"))
        other = cadence_run(self.root / "d", "expB2-cadence", "20261023T000000", 32, 0.5, (60, 50), sched="other")
        self.assertEqual(cadence_report.analyse(other, ref)["E3"]["episodes_same_schedule"], [])  # schedules differ: no pairing


if __name__ == "__main__":
    unittest.main()
