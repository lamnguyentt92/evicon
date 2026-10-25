"""Release 072 analysis tools on synthetic campaign runs: pairs_report.py (confirmed fulfilment from
clients.jsonl and plant.jsonl, the decision rules, Holm, cross-run estimands paired on identical
schedules, trace-derived stop/fence success, the spec read from a plan), evidence_latency.py,
t3_attribution.py (Theorem 3 counterexamples are reported), confirm_report.py --k2-rep and the new
paper tables."""
import csv
import json
import tempfile
import unittest
from pathlib import Path

from evaluation import confirm_report, evidence_latency, paper_tables, pairs_report, t3_attribution
from evaluation.test_reports import k0_run, k2_run

T0 = 1792195857.0


def write_rows(path: Path, rows: list):
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = list(rows[0].keys())
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def make_run(root: Path, name: str, effects: dict, eps: int = 6, sched: str = "s", violations=None, plan_sha="x") -> Path:
    """A campaign run with K0/K0F/K0S cells of EVICON: `effects[contract]` = clean fulfilment in pp
    (plus a small per-episode wobble); 100 requests per cell, every clean one confirmed except one per
    cell; traces with fence and stop results."""
    run = root / "campaigns" / name / "20261024T000000"
    (run).mkdir(parents=True)
    (run / "config.json").write_text(json.dumps({"name": name, "workload": {"deadline_s": 60},
                                                 "preregistration": {"path": "p.md", "sha256": plan_sha}}))
    rows = []
    for ep in range(eps):
        (run / f"schedule_ep{ep}.json").write_text(json.dumps({"ep": ep, "s": sched}))
        for ct, base in effects.items():
            clean = int(round(base + (ep % 3) - 1))
            cell = run / "cells" / f"ep{ep}" / ct / "EVICON"
            (cell / "raw" / "E").mkdir(parents=True)
            (cell / "raw" / "A").mkdir(parents=True)
            clients, plant = [], []
            for i in range(100):
                cid = f"d0-c{i}"
                rec = {"cid": cid, "device": 0, "arrival": T0 + i, "value": 1, "decision": "ADMITTED", "ids": [cid],
                       "admitted": {cid: T0 + i}, "final_status": {}, "terminal_seen": {}}
                if i < clean:
                    plant.append({"seq": len(plant) + 1, "t_ns": int((T0 + i + 1) * 1e9), "device": "pump0-1", "cmd": cid, "value": 1})
                    if i != 0:  # request 0 took effect but the client never saw APPLIED
                        rec["final_status"][cid], rec["terminal_seen"][cid] = "APPLIED", T0 + i + 2
                clients.append(rec)
            (cell / "clients.jsonl").write_text("".join(json.dumps(c) + "\n" for c in clients))
            (cell / "raw" / "E" / "plant.jsonl").write_text("".join(json.dumps(p) + "\n" for p in plant))
            tr = []
            if ct == "K0F":  # 4 fence answers from the owner (1 OK, 3 aborted) and 6 offline attempts
                tr += [{"event": "EVICON_FENCE_RESULT", "outcome": "200", "reason": "{}"}]
                tr += [{"event": "EVICON_FENCE_RESULT", "outcome": "409", "reason": '{"error":"BARRIER_UNCERTAIN_RECEIPTS"}'}] * 3
                tr += [{"event": "EVICON_FENCE_RESULT", "outcome": "503", "reason": '{"error":"DEVICE_OFFLINE"}'}] * 6
            if ct == "K0S":  # 4 stop answers (3 terminal, 1 not terminal) and 2 transport errors
                tr += [{"event": "EVICON_STOP_RESULT", "outcome": "STOPPED", "reason": "STOP_TOMBSTONE"}] * 2
                tr += [{"event": "EVICON_STOP_RESULT", "outcome": "APPLIED", "reason": "APPLIED"}]
                tr += [{"event": "EVICON_STOP_RESULT", "outcome": "UNCERTAIN", "reason": "STOP_NOT_TERMINAL:CLAIMED_WITHOUT_RECEIPT"}]
                tr += [{"event": "EVICON_STOP_RESULT", "outcome": "UNCERTAIN", "reason": "STOP_TRANSPORT_ERROR"}] * 2
            (cell / "raw" / "A" / "authority0.jsonl").write_text("".join(json.dumps(t) + "\n" for t in tr))
            row = {"regime": "r", "contract": ct, "policy": "EVICON", "episode": ep, "cell_status": "VALID", "offered": 100,
                   "admitted": 100, "clean_fulfilled": clean, "stranded_resolved_unit_sec": 0, "stranded_censored_unit_sec": 100 - clean,
                   "mean_time_to_terminal_s": 1.0, "fences": 10 if ct == "K0F" else 0, "fences_ok": 1 if ct == "K0F" else 0,
                   "stops": 6 if ct == "K0S" else 0, "stops_terminal": 3 if ct == "K0S" else 0, "queries": 0, "queries_terminal": 0,
                   "late_effects": 0, "unresolved_at_end": 0, "reissues": 0, "bound_violations": 0, "duplicate_effects": 0,
                   "logical_duplicates": 0, "false_stopped": 0, "false_applied": 0,
                   "defer_reasons": json.dumps({"OUTSTANDING_BUDGET_EXHAUSTED": 4 if ct == "K0" else 1, "BARRIER_IN_PROGRESS": 2,
                                                "OUTSTANDING_BUDGET_EXHAUSTED;EVICON_NO_BACKWARD_EVIDENCE": 3 if ct == "K0" else 0})}
            if violations and (ct, ep) in violations:
                row.update(violations[(ct, ep)])
            rows.append(row)
    write_rows(run / "runtime_episodes.csv", rows)
    return run


SPEC = {
    "name": "test",
    "runs": {"a": {"campaign": "expT-a"}, "b": {"campaign": "expT-b"}},
    "hypotheses": [
        {"id": "H-1", "family": "F1", "run": "a", "policy": "EVICON", "from": "K0", "to": "K0F", "metric": "clean_pp", "test": "at_least", "margin": 10},
        {"id": "H-2", "family": "F2", "run": "b", "policy": "EVICON", "from": "K0", "to": "K0F", "metric": "clean_pp", "test": "equivalent", "margin": 3},
        {"id": "H-3", "family": "F3", "run": "a", "policy": "EVICON", "from": "K0", "to": "K0F", "metric": "clean_pp", "test": "at_least", "margin": 40},
        {"id": "H-4", "family": "F4", "run": "a", "test": "rate_greater",
         "a": {"contract": "K0S", "policy": "EVICON", "num": "stops_reached_terminal", "den": "stops_reached"},
         "b": {"contract": "K0F", "policy": "EVICON", "num": "fences_reached_ok", "den": "fences_reached"}},
        {"id": "N-1", "run": "a", "test": "detects", "contract": "K0", "any_of": ["false_stopped"]},
        {"id": "N-2", "run": "a", "test": "absent", "contract": "K0F", "any_of": ["false_stopped", "late_effects"]}],
    "safety": {"id": "H-S", "runs": ["a", "b"], "exempt_contracts": []},
    "estimands": [{"run": "a", "policy": "EVICON", "from": "K0", "to": "K0F", "metrics": ["confirmed_pp", "window_defers"]},
                  {"run": "b", "from_run": "a", "policy": "EVICON", "from": "K0", "to": "K0", "metrics": ["clean_pp"]}],
    "gap": [{"run": "a", "policy": "EVICON", "low": "K0", "mid": "K0F", "high": "K0S"}],
}


class PairsReport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.a = make_run(self.root / "a", "expT-a", {"K0": 30, "K0F": 60, "K0S": 70}, violations={("K0", 2): {"false_stopped": 1}})
        self.b = make_run(self.root / "b", "expT-b", {"K0": 75, "K0F": 76}, sched="s")

    def tearDown(self):
        self.tmp.cleanup()

    def analyse(self, spec=SPEC, plan=None):
        runs = {"a": pairs_report.load_run("a", self.a), "b": pairs_report.load_run("b", self.b)}
        return pairs_report.analyse(spec, runs, plan), runs

    def test_confirmed_recount_and_rules(self):
        res, runs = self.analyse()
        c = runs["a"]["cells"][("K0F", "EVICON", 0)]
        self.assertEqual(c["clean_pp"], 59.0)
        self.assertEqual(c["confirmed_pp"], 58.0)  # request 0 took effect, the client never saw APPLIED
        self.assertEqual(runs["a"]["clean_mismatch"], [])
        self.assertEqual((c["fences_reached"], c["fences_reached_ok"]), (4, 1))
        self.assertEqual(runs["a"]["cells"][("K0S", "EVICON", 0)]["stops_reached"], 4)
        v = {h["id"]: h["verdict"] for h in res["hypotheses"]}
        self.assertEqual(v, {"H-1": "MET", "H-2": "MET", "H-3": "NOT MET", "H-4": "MET", "N-1": "DETECTED", "N-2": "ABSENT"})
        self.assertEqual(res["safety"]["verdict"], "NOT MET")  # the planted false STOPPED
        self.assertEqual(res["safety"]["violations"][0]["cell"], "ep2/K0/EVICON")
        est = res["estimands"]
        self.assertAlmostEqual(est[0]["metrics"]["window_defers"]["mean"], -6.0)
        self.assertEqual(est[1]["metrics"]["clean_pp"]["n"], 6)  # identical schedules: every episode pairs
        self.assertAlmostEqual(est[1]["metrics"]["clean_pp"]["mean"], 45.0)
        g = res["gap"][0]
        self.assertAlmostEqual(g["share"], 0.75, places=3)
        md = pairs_report.markdown(res)
        self.assertIn("| H-1 | at_least |", md)
        self.assertIn("Exploratory", md)

    def test_cross_run_pairs_only_identical_schedules(self):
        self.b = make_run(self.root / "c", "expT-b", {"K0": 75, "K0F": 76}, sched="other")
        res, _ = self.analyse()
        self.assertEqual(res["estimands"][1]["metrics"]["clean_pp"], {"n": 0})

    def test_mismatch_and_invalid(self):
        with open(self.a / "runtime_episodes.csv", newline="") as fh:
            rows = list(csv.DictReader(fh))
        rows[0]["clean_fulfilled"] = "99"
        for r in rows[1:4]:
            r["cell_status"] = "INVALID: test"
        write_rows(self.a / "runtime_episodes.csv", rows)
        res, runs = self.analyse()
        self.assertEqual(len(runs["a"]["clean_mismatch"]), 1)
        self.assertFalse(res["runs"]["a"]["interpretable"])  # 3 of 18 cells INVALID > 10 %
        self.assertEqual({h["id"]: h["verdict"] for h in res["hypotheses"]}["H-1"], "NOT INTERPRETABLE")

    def test_holm(self):
        adj = pairs_report.holm({"x": 0.01, "y": 0.04, "z": 0.03})
        self.assertAlmostEqual(adj["x"], 0.03)
        self.assertAlmostEqual(adj["z"], 0.06)
        self.assertAlmostEqual(adj["y"], 0.06)  # monotone

    def test_plan_cli(self):
        plan = self.root / "PLAN.md"
        plan.write_text("# p\n\n```pairs-spec\n" + json.dumps(SPEC) + "\n```\n\n```frozen-files\n```\n")
        out = self.root / "out"
        rc = pairs_report.main(["--plan", str(plan), "--run", f"a={self.a}", "--run", f"b={self.b}", "--out", str(out)])
        self.assertEqual(rc, 0)
        res = json.loads((out / "results.json").read_text())
        self.assertTrue(any("froze plan SHA" in d for d in res["deviations"]))  # the configs name another plan
        self.assertTrue((out / "cells.csv").exists())
        # paper table
        rep = self.root / "report"
        (rep / "pairs" / "PREREG_expT_072").mkdir(parents=True)
        (rep / "pairs" / "PREREG_expT_072" / "results.json").write_text((out / "results.json").read_text())
        N = {}
        paper_tables.pairs(rep, rep, N)
        tab = (rep / "tab_pairs_PREREG_expT_072.tex").read_text()
        self.assertIn("H-1 & EVICON, K0 $\\to$ K0F, clean\\_pp", tab)
        self.assertEqual(N["evexpTHOneVerdict"], "MET")
        self.assertTrue(all(k.isalpha() for k in N))


class Latency(unittest.TestCase):
    def test_latency(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t) / "review-20261024T000000"
            run = root / "measure" / "minio" / "runs" / "minio-conditional" / "20261024T010000"
            run.mkdir(parents=True)
            (run / "contract.json").write_text(json.dumps({"system": "MinIO (S3 API)", "capabilities": ["D_h", "Q_A", "Q_S"]}))
            rows = [{"probe_id": "P2", "applicable": True, "client": {"latency_ms": 2000.0, "status": "TIMEOUT", "outcome": "UNKNOWN"},
                     "query": {"latency_ms": float(q), "outcome": "APPLIED", "supported": True, "terminal": True},
                     "stop": {"latency_ms": 9.0, "outcome": "NOT_FOUND", "supported": True, "terminal": False}} for q in range(1, 21)]
            (run / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
            out = Path(t) / "out"
            self.assertEqual(evidence_latency.main(["--out", str(out), str(root)]), 0)
            got = {r["action"]: r for r in json.loads((out / "latency.json").read_text())}
            self.assertEqual((got["query"]["n_terminal"], got["query"]["p50_ms"], got["query"]["p95_ms"]), (20, 10.0, 19.0))
            self.assertEqual((got["client"]["n_timeout"], got["client"]["n_terminal"]), (20, 0))
            self.assertEqual(got["stop"]["n_terminal"], 0)
            self.assertEqual(got["query"]["group"], "measure/minio")
            rep = Path(t) / "rep"
            (rep / "latency").mkdir(parents=True)
            (rep / "latency" / "latency.json").write_text((out / "latency.json").read_text())
            N = {}
            paper_tables.latency(rep, rep, N)
            self.assertIn("minio-conditional", (rep / "tab_evidence_latency.tex").read_text().replace("\\_", "_"))
            self.assertEqual(N["evLatencyWorstPNinetyFiveMs"], "19")


class T3(unittest.TestCase):
    def trace(self, events):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        run = Path(t.name) / "campaigns" / "x" / "20261024T000000"
        cell = run / "cells" / "ep0" / "K0F" / "EVICON" / "raw" / "A"
        cell.mkdir(parents=True)
        (cell / "authority0.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
        return run

    def test_reopened_by_stopped(self):
        D = "DECISION_RECORDED"
        run = self.trace([
            {"event": D, "command_id": "a", "decision": "BLOCK", "reason": "RELATIONAL_ENVELOPE_COUNTEREXAMPLE"},
            {"event": "OUTBOX_RECEIPT_COMMITTED", "command_id": "x", "reason": "APPLIED"},
            {"event": D, "command_id": "a", "decision": "BLOCK", "reason": "RELATIONAL_ENVELOPE_COUNTEREXAMPLE"},
            {"event": "EVIDENCE_COMMITTED", "command_id": "y", "outcome": "STOPPED", "reason": "BARRIER_SEALED_UNCLAIMED:r1"},
            {"event": D, "command_id": "a", "decision": "ADMITTED", "reason": "RELATIONAL_BELIEF_SAFE"},
            {"event": D, "command_id": "b", "decision": "DEFER", "reason": "OUTSTANDING_BUDGET_EXHAUSTED;EVICON_WAIT:PENDING_NOT_AGED"},
            {"event": "OUTBOX_RECEIPT_COMMITTED", "command_id": "z", "reason": "APPLIED"},
            {"event": D, "command_id": "b", "decision": "ADMITTED", "reason": "RELATIONAL_BELIEF_SAFE"}])
        r = list(t3_attribution.scan([run]).values())[0]
        self.assertEqual(r["capacity_total"], {"STOPPED": 1})
        self.assertEqual(r["retested_total"], {"BLOCK": 1})
        self.assertEqual(r["groups"]["K0F/EVICON"]["window"], {"APPLIED_ONLY": 1})
        self.assertTrue(r["prediction_holds"])

    def test_counterexample_is_reported(self):
        D = "DECISION_RECORDED"
        run = self.trace([
            {"event": D, "command_id": "a", "decision": "BLOCK", "reason": "RELATIONAL_ENVELOPE_COUNTEREXAMPLE"},
            {"event": "EVIDENCE_COMMITTED", "command_id": "x", "outcome": "APPLIED", "reason": "POLICY_QUERY:APPLIED"},
            {"event": D, "command_id": "a", "decision": "ADMITTED", "reason": "RELATIONAL_BELIEF_SAFE"}])
        r = list(t3_attribution.scan([run]).values())[0]
        self.assertFalse(r["prediction_holds"])
        self.assertEqual(r["retested_total"], {"ADMITTED": 1})
        self.assertIn("APPLIED_ONLY", r["counterexamples"][0])


class K2Replication(unittest.TestCase):
    def test_k2_rep(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            k0 = k0_run(root / "a", "20261016T234202", 29.9)
            k2 = k2_run(root / "a")
            good = k2_run(root / "b", "20261022T000000")
            bad = k2_run(root / "c", "20261023T000000", equivalent="False")
            out = root / "rep"
            self.assertEqual(confirm_report.main(["--k0", str(k0), "--k2", str(k2), "--k2-rep", str(good), "--k2-rep", str(bad),
                                                  "--out", str(out)]), 0)
            res = json.loads((out / "results.json").read_text())
            self.assertEqual([r["replicates"] for r in res["post_hoc"]["replications_k2"]["runs"]], [True, False])
            md = (out / "RESULTS.md").read_text()
            self.assertIn("## Replication of the K2 config (R-K2", md)
            N = {}
            rep = root / "report"
            (rep / "confirm_expA").mkdir(parents=True)
            (rep / "confirm_expA" / "results.json").write_text((out / "results.json").read_text())
            paper_tables.expA(rep, rep, N)
            self.assertEqual(N["evKTwoReplicates"], "yes")
            self.assertIn("evATwoEviconRepPP", N)


if __name__ == "__main__":
    unittest.main()
