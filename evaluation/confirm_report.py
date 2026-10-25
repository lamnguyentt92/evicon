#!/usr/bin/env python3
"""Results of the preregistered confirmatory experiment A (release 068; replications: release 070).

Applies the decision rules of section 3 of the frozen plan
(evaluation/prereg/PREREG_expA_confirm_067.md) to the analysis files the driver wrote for the two
confirmatory campaigns, mechanically, and adds - clearly labelled POST HOC - the analyses that
were not part of the plan:

  * the simulator-vs-testbed check pooled over both configs (the plan's SR rule needs a non-null
    effect, which the K2 config by design does not have);
  * a sensitivity analysis without the cells a testbed defect affected (fence audit: device
    receipt flips and domains stuck on the same failed fence; release 067 emulator bug, fixed in
    068), for H-A1 and H-B.

Release 070: --k0-rep <run dir> (repeatable) adds a replication of the K0 config under the same
frozen plan (decided after the 067 results, so labelled post hoc; its decision rule was written down
before the run: evaluation/prereg/REPLICATION_expA_k0_069.md). A replication "replicates" when
H-A1, H-A1c and H-S hold on it alone, and its fence audit shows 0 receipt flips and 0 stuck cells
(otherwise the emulator is still faulty and the run is not used as evidence). The primary result
stays the --k0 run.

Release 072: --k2-rep <run dir> (repeatable) adds a replication of the K2 config (R-K2, decided after the
067 results, so post hoc; decision rule written before the run in the release-071 plan of record): it
replicates when H-A2 (EVICON and RES_PERIODIC, K2 -> K3F equivalent, TOST +-2 pp, Holm) and H-S hold on
it alone and its fence audit shows 0 receipt flips and 0 stuck cells. Fence counts and the fence OK
rate are reported with it. The primary result stays the --k2 run.

    python3 evaluation/confirm_report.py --k0 <expA-confirm-k0 run dir> --k2 <expA-confirm-k2 run dir> \
        [--k0-rep <later expA-confirm-k0 run dir> ...] [--k2-rep <later expA-confirm-k2 run dir> ...] \
        --sim <simulator twin episodes.csv> --out <dir>

Writes RESULTS.md and results.json. Reads only; the frozen analysis files are not re-run (except
sim_to_real.py for the pooled check, which writes to <out>/sim_to_real_pooled).
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fence_audit  # noqa: E402

PLAN = "evaluation/prereg/PREREG_expA_confirm_067.md"


def rows(path: Path) -> list:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def pick(rs, **kw):
    out = [r for r in rs if all(str(r.get(k)) == str(v) for k, v in kw.items())]
    if len(out) != 1:
        raise ValueError(f"expected one row for {kw}, found {len(out)}")
    return out[0]


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return float("nan")


def boolean(x) -> bool:
    return str(x).strip().lower() == "true"


def boot_ci(d, B=4000, seed=20261017):
    rnd = random.Random(seed)
    m = sorted(sum(rnd.choice(d) for _ in d) / len(d) for _ in range(B))
    return m[int(0.025 * B)], m[int(0.975 * B) - 1]


def pp_by_cell(csv_path: Path) -> dict:
    out = {}
    for r in rows(csv_path):
        if not str(r.get("cell_status", "")).startswith("VALID"):
            continue
        off = f(r["offered"])
        out[(r["contract"], r["policy"], int(r["episode"]))] = 100.0 * f(r["clean_fulfilled"]) / off if off else float("nan")
    return out


def paired_pp(pp, a, b, episodes):
    """mean, CI of pp[a] - pp[b] over the episodes where both exist; a, b = (contract, policy)."""
    d = [pp[(a[0], a[1], e)] - pp[(b[0], b[1], e)] for e in episodes if (a[0], a[1], e) in pp and (b[0], b[1], e) in pp]
    if len(d) < 2:
        return {"n": len(d)}
    lo, hi = boot_ci(d)
    sd = statistics.stdev(d)
    t = statistics.mean(d) / (sd / math.sqrt(len(d))) if sd > 0 else float("inf")
    return {"n": len(d), "mean_pp": round(statistics.mean(d), 3), "ci95_lo": round(lo, 3), "ci95_hi": round(hi, 3), "t": round(t, 2)}


def analysis_dir(run: Path) -> Path:
    a = run / "analysis"
    if not (a / "report.json").exists():
        raise SystemExit(f"{a}: no analysis (the campaign is not complete?)")
    return a


def run_name(r: Path) -> str:
    return f"{r.parent.name}/{r.name}"  # campaign/stamp, not this host's path


def k0_part(k0: Path) -> dict:
    """The hypotheses of the plan that the K0 config decides on its own (H-A1, H-A1c, H-B), its
    safety record and its per-episode clean fulfilment, from the frozen analysis of one run."""
    A0 = analysis_dir(k0)
    cu0, pr0 = rows(A0 / "contract_upgrade.csv"), rows(A0 / "paired.csv")
    rep0 = json.loads((A0 / "report.json").read_text())
    sr0 = json.loads((A0 / "sim_to_real" / "sim_to_real.json").read_text())
    out = {"report": rep0, "sr": sr0}
    # H-A1: EVICON and RES_PERIODIC, K0 -> K0F: mean_diff > 0, p_holm < 0.05, testbed CI lower bound >= 10 pp
    eff = {(e["policy"], e["base"], e["upgraded"]): e for e in sr0["effects"]}
    ha1 = {}
    for pol in ("EVICON", "RES_PERIODIC"):
        r = pick(cu0, policy=pol, contract="K0", upgraded="K0F", metric="clean_fulfilled")
        e = eff[(pol, "K0", "K0F")]["runtime"]
        ok = f(r["mean_diff"]) > 0 and f(r["p_holm"]) < 0.05 and f(e["ci95_lo"]) >= 10.0
        ha1[pol] = {"mean_diff_commands": f(r["mean_diff"]), "p_holm": f(r["p_holm"]), "effect_pp": e["mean_pp"],
                    "ci95_pp": [e["ci95_lo"], e["ci95_hi"]], "pairs": e["pairs"], "pass": ok}
    out["H-A1"] = {"rule": "per policy: mean_diff > 0 and p_holm < 0.05 (contract_upgrade.csv), testbed CI lower bound >= 10 pp "
                           "(sim_to_real.md)", "policies": ha1, "pass": all(v["pass"] for v in ha1.values())}
    # H-A1c: M1 K0 -> K0F equivalent (TOST +-2 pp)
    r = pick(cu0, policy="M1", contract="K0", upgraded="K0F", metric="clean_fulfilled")
    e = eff[("M1", "K0", "K0F")]["runtime"]
    out["H-A1c"] = {"rule": "M1, K0 -> K0F: equivalent_at_0.05 (TOST, +-2 pp)", "equivalent": boolean(r["equivalent_at_0.05"]),
                    "effect_pp": e["mean_pp"], "ci95_pp": [e["ci95_lo"], e["ci95_hi"]], "pass": boolean(r["equivalent_at_0.05"])}
    # H-B (secondary)
    rb = pick(pr0, contract="K0F", policy="EVICON", reference="RES_PERIODIC", metric="clean_fulfilled")
    sm = {(x["contract"], x["policy"]): x for x in rows(A0 / "summary.csv")}
    pp = pp_by_cell(k0 / "runtime_episodes.csv")
    eps = sorted({k[2] for k in pp})
    hb = paired_pp(pp, ("K0F", "EVICON"), ("K0F", "RES_PERIODIC"), eps)
    out["H-B"] = {"rule": "no superiority claim unless p_holm < 0.05; no equivalence claim unless TOST passes",
                  "mean_diff_commands": f(rb["mean_diff"]), "ci95_commands": [f(rb["ci95_lo"]), f(rb["ci95_hi"])],
                  "p_holm": f(rb["p_holm"]), "equivalent": boolean(rb["equivalent_at_0.05"]),
                  "effect_pp": hb, "fences_per_episode": {p: f(sm[("K0F", p)]["fences_mean"]) for p in ("EVICON", "RES_PERIODIC")},
                  "acting_decisions_per_episode": {p: f(sm[("K0F", p)]["evicon_decisions_mean"]) for p in ("EVICON", "RES_PERIODIC")},
                  "conclusion": ("superior" if f(rb["p_holm"]) < 0.05 and f(rb["mean_diff"]) > 0 else
                                 "equivalent" if boolean(rb["equivalent_at_0.05"]) else "inconclusive")}
    out["pp"], out["episodes"] = pp, eps
    return out


def k2_part(k2: Path) -> dict:
    """H-A2 of the plan on one run of the K2 config, from its frozen analysis."""
    A2 = analysis_dir(k2)
    cu2 = rows(A2 / "contract_upgrade.csv")
    rep2 = json.loads((A2 / "report.json").read_text())
    sr2 = json.loads((A2 / "sim_to_real" / "sim_to_real.json").read_text())
    eff2 = {(x["policy"], x["base"], x["upgraded"]): x for x in sr2["effects"]}
    ha2 = {}
    for pol in ("EVICON", "RES_PERIODIC"):
        r = pick(cu2, policy=pol, contract="K2", upgraded="K3F", metric="clean_fulfilled")
        e = eff2[(pol, "K2", "K3F")]["runtime"]
        ha2[pol] = {"equivalent": boolean(r["equivalent_at_0.05"]), "p_tost_holm": f(r["p_tost_holm"]), "effect_pp": e["mean_pp"],
                    "ci95_pp": [e["ci95_lo"], e["ci95_hi"]], "pass": boolean(r["equivalent_at_0.05"])}
    return {"report": rep2, "sr": sr2, "H-A2": {"rule": "per policy: K2 -> K3F equivalent_at_0.05 (TOST, +-2 pp, Holm)",
                                               "policies": ha2, "pass": all(v["pass"] for v in ha2.values())}}


def replication_k2(k2: Path) -> dict:
    """A later run of the K2 config (release 072, R-K2): H-A2 and H-S on this run alone, plus its fence
    audit (0 receipt flips and 0 stuck cells for the run to count), fence counts and OK rates."""
    p = k2_part(k2)
    rep = p["report"]
    au = fence_audit.audit([k2])
    hs = {"violations": rep["groups_with_safety_violations"], "late_effects": rep["late_effects_total"],
          "fulfilled_late": rep.get("fulfilled_late_total", 0), "pass": not rep["groups_with_safety_violations"]}
    clean = au["flips_total"] == 0 and au["stuck_cells_total"] == 0
    return {"run": run_name(k2), "H-A2": p["H-A2"], "H-S": hs, "invalid_cells": rep["invalid_cells_excluded"],
            "fence_audit": {"receipt_flips": au["flips_total"], "stuck_cells": au["stuck_cells_total"],
                            "evidence_conflicts": sum(r["evidence_conflicts"] for r in au["rows"])},
            "fences": {r["policy"]: r["fence_calls"] for r in au["rows"] if r["contract"] == "K3F" and r["fence_calls"]},
            "fence_ok_rate": {r["policy"]: r["fence_ok_rate"] for r in au["rows"] if r["contract"] == "K3F" and r["fence_calls"]},
            "emulator_clean": clean, "replicates": bool(p["H-A2"]["pass"] and hs["pass"] and clean)}


def replication(k0: Path) -> dict:
    """A later run of the K0 config under the same plan: H-A1, H-A1c, H-S on this run alone, plus
    its fence audit (receipt flips and stuck cells must be 0 for the run to count)."""
    p = k0_part(k0)
    rep = p["report"]
    au = fence_audit.audit([k0])
    hs = {"violations": rep["groups_with_safety_violations"], "late_effects": rep["late_effects_total"],
          "fulfilled_late": rep.get("fulfilled_late_total", 0), "pass": not rep["groups_with_safety_violations"]}
    clean = au["flips_total"] == 0 and au["stuck_cells_total"] == 0
    return {"run": run_name(k0), "H-A1": p["H-A1"], "H-A1c": p["H-A1c"], "H-S": hs, "H-B": p["H-B"],
            "invalid_cells": rep["invalid_cells_excluded"],
            "fence_audit": {"receipt_flips": au["flips_total"], "stuck_cells": au["stuck_cells_total"],
                            "evidence_conflicts": sum(r["evidence_conflicts"] for r in au["rows"])},
            "fence_ok_rate": {r["policy"]: r["fence_ok_rate"] for r in au["rows"] if r["contract"] == "K0F" and r["fence_calls"]},
            "emulator_clean": clean,
            "replicates": bool(p["H-A1"]["pass"] and p["H-A1c"]["pass"] and hs["pass"] and clean)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--k0", required=True, help="run directory of expA-confirm-k0")
    ap.add_argument("--k2", required=True, help="run directory of expA-confirm-k2")
    ap.add_argument("--k0-rep", action="append", default=[], help="a later run of expA-confirm-k0: a replication (release 070)")
    ap.add_argument("--k2-rep", action="append", default=[], help="a later run of expA-confirm-k2: a replication (release 072, R-K2)")
    ap.add_argument("--sim", default="", help="simulator twin episodes.csv (pooled SR check)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    k0, k2, out = Path(a.k0), Path(a.k2), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    p2 = k2_part(k2)
    rep2, sr2 = p2["report"], p2["sr"]
    p0 = k0_part(k0)
    rep0, sr0, pp, eps = p0["report"], p0["sr"], p0["pp"], p0["episodes"]
    res = {"plan": PLAN, "k0_run": run_name(k0), "k2_run": run_name(k2), "hypotheses": {}, "post_hoc": {}}
    H = res["hypotheses"]
    H["H-A1"], H["H-A1c"] = p0["H-A1"], p0["H-A1c"]
    # H-A2: EVICON and RES_PERIODIC, K2 -> K3F equivalent
    H["H-A2"] = p2["H-A2"]
    # H-S
    viol = rep0["groups_with_safety_violations"] + rep2["groups_with_safety_violations"]
    H["H-S"] = {"rule": "report.json groups_with_safety_violations empty (both configs)", "violations": viol,
                "late_effects": rep0["late_effects_total"] + rep2["late_effects_total"],
                "fulfilled_late": rep0.get("fulfilled_late_total", 0) + rep2.get("fulfilled_late_total", 0), "pass": not viol}
    # H-B (secondary)
    H["H-B"] = p0["H-B"]
    # SR (per config, as preregistered)
    H["SR"] = {"rule": "sim_to_real.md, criterion 066 (margin 2 pp)", "k0": sr0["check"], "k2": sr2["check"],
               "k2_not_applicable": bool(sr2["check"].get("no_sign_disagrees") and sr2["check"].get("nonnull_agreeing", 0) == 0),
               "note": "the K2 config has only null effects by design (H-A2), so the criterion's 'at least one non-null effect "
                       "agrees' cannot hold for it alone; see the pooled check (post hoc)"}
    # exclusions
    inval = rep0["invalid_cells_excluded"] + rep2["invalid_cells_excluded"]
    res["exclusions"] = {"invalid_cells": inval, "interpretable": True}

    # POST HOC 1: pooled SR
    if a.sim:
        po = out / "sim_to_real_pooled"
        p = subprocess.run([sys.executable, str(HERE / "sim_to_real.py"), "--runtime", str(k0 / "runtime_episodes.csv"), "--runtime",
                            str(k2 / "runtime_episodes.csv"), "--sim", a.sim, "--out", str(po)], capture_output=True, text=True)
        if p.returncode == 0:
            pj = json.loads((po / "sim_to_real.json").read_text())
            res["post_hoc"]["SR_pooled"] = {"check": pj["check"], "ranking": pj["ranking"], "dir": str(po)}
    # POST HOC 2: sensitivity without the cells the emulator defect affected
    au = fence_audit.audit([k0])
    bad = set()
    for r in au["rows"]:
        for c in r["stuck_cells"] + r["flip_cells"]:
            bad.add(int(c["cell"].split("/")[0][2:]))
    stuck = set()
    for r in au["rows"]:
        for c in r["stuck_cells"]:
            stuck.add(int(c["cell"].split("/")[0][2:]))
    sens = {}
    for label, drop in (("all", set()), ("without_stuck_episodes", stuck), ("without_affected_episodes", bad)):
        keep = [e for e in eps if e not in drop]
        sens[label] = {"episodes": len(keep), "dropped": sorted(drop),
                       "A_EVICON": paired_pp(pp, ("K0F", "EVICON"), ("K0", "EVICON"), keep),
                       "A_RES_PERIODIC": paired_pp(pp, ("K0F", "RES_PERIODIC"), ("K0", "RES_PERIODIC"), keep),
                       "A_M1": paired_pp(pp, ("K0F", "M1"), ("K0", "M1"), keep),
                       "B_EVICON_minus_RES_PERIODIC": paired_pp(pp, ("K0F", "EVICON"), ("K0F", "RES_PERIODIC"), keep)}
    res["post_hoc"]["sensitivity"] = sens
    res["post_hoc"]["fence_audit"] = {"receipt_flips": au["flips_total"], "stuck_cells": au["stuck_cells_total"],
                                      "rows": [{k: r[k] for k in ("contract", "policy", "fence_calls", "fence_ok", "fence_ok_rate",
                                                                  "by_outcome", "device_receipt_flips")} for r in au["rows"] if r["fence_calls"]]}
    primary = all(H[k]["pass"] for k in ("H-A1", "H-A1c", "H-A2", "H-S"))
    res["primary_hypotheses_pass"] = primary
    reps = []
    for r in a.k0_rep:
        reps.append(replication(Path(r)))
    if reps:
        res["post_hoc"]["replications"] = {
            "note": "replication of the K0 config under the same frozen plan, decided after the 067 results (post hoc); "
                    "decision rule written before the run: evaluation/prereg/REPLICATION_expA_k0_069.md",
            "original": {"run": run_name(k0), "H-A1": H["H-A1"], "H-A1c": H["H-A1c"],
                         "H-S_k0": {"violations": rep0["groups_with_safety_violations"], "late_effects": rep0["late_effects_total"]},
                         "H-B": H["H-B"], "fence_audit": {"receipt_flips": au["flips_total"], "stuck_cells": au["stuck_cells_total"]},
                         "fence_ok_rate": {x["policy"]: x["fence_ok_rate"] for x in res["post_hoc"]["fence_audit"]["rows"]
                                           if x["contract"] == "K0F"}},
            "runs": reps}
    reps2 = [replication_k2(Path(r)) for r in a.k2_rep]
    if reps2:
        au2 = fence_audit.audit([k2])
        res["post_hoc"]["replications_k2"] = {
            "note": "replication of the K2 config under the same frozen plan (R-K2), decided after the 067 results (post hoc); "
                    "decision rule written before the run in the release-071 plan of record",
            "original": {"run": run_name(k2), "H-A2": H["H-A2"],
                         "H-S_k2": {"violations": rep2["groups_with_safety_violations"], "late_effects": rep2["late_effects_total"]},
                         "fence_audit": {"receipt_flips": au2["flips_total"], "stuck_cells": au2["stuck_cells_total"]},
                         "fences": {x["policy"]: x["fence_calls"] for x in au2["rows"] if x["contract"] == "K3F" and x["fence_calls"]},
                         "fence_ok_rate": {x["policy"]: x["fence_ok_rate"] for x in au2["rows"] if x["contract"] == "K3F" and x["fence_calls"]}},
            "runs": reps2}
    (out / "results.json").write_text(json.dumps(res, indent=2) + "\n")
    (out / "RESULTS.md").write_text(markdown(res))
    rtxt = "".join(f"; replication {x['run']}: {'replicates' if x['replicates'] else 'does NOT replicate'}" for x in reps + reps2)
    print(f"confirmatory experiment A: primary hypotheses {'PASS' if primary else 'NOT ALL MET'}; H-B {H['H-B']['conclusion']}{rtxt} -> {out}")
    return 0


def _pp(d):
    if not d or "mean_pp" not in d:
        return "-"
    return f"{d['mean_pp']:+.2f} ({d['ci95_lo']:+.2f}, {d['ci95_hi']:+.2f}), n={d['n']}"


def markdown(r: dict) -> str:
    H = r["hypotheses"]
    word = lambda ok: "**MET**" if ok else "**NOT MET**"  # noqa: E731
    L = ["# Confirmatory experiment A: results", "",
         f"Plan: `{r['plan']}` (frozen before the runs; its SHA-256 and the frozen analysis files were verified by the "
         "driver before each campaign).", f"Runs: `{r['k0_run']}`, `{r['k2_run']}`.", "",
         f"Excluded (INVALID) cells: {len(r['exclusions']['invalid_cells'])}.", "",
         "## Preregistered hypotheses (decision rules of section 3)", "",
         "| ID | Result | Evidence |", "|---|---|---|"]
    ha1 = H["H-A1"]["policies"]
    L.append(f"| H-A1 | {word(H['H-A1']['pass'])} | " + "; ".join(
        f"{p}: {v['effect_pp']:+.2f} pp, CI ({v['ci95_pp'][0]:+.2f}, {v['ci95_pp'][1]:+.2f}), p_holm {v['p_holm']:.2g}" for p, v in ha1.items()) + " |")
    c = H["H-A1c"]
    L.append(f"| H-A1c | {word(c['pass'])} | M1: {c['effect_pp']:+.2f} pp, CI ({c['ci95_pp'][0]:+.2f}, {c['ci95_pp'][1]:+.2f}), "
             f"equivalent {c['equivalent']} |")
    L.append(f"| H-A2 | {word(H['H-A2']['pass'])} | " + "; ".join(
        f"{p}: {v['effect_pp']:+.2f} pp, CI ({v['ci95_pp'][0]:+.2f}, {v['ci95_pp'][1]:+.2f}), equivalent {v['equivalent']}"
        for p, v in H["H-A2"]["policies"].items()) + " |")
    s = H["H-S"]
    L.append(f"| H-S | {word(s['pass'])} | violations {len(s['violations'])}; late effects {s['late_effects']}; "
             f"fulfilled after the deadline (reported separately) {s['fulfilled_late']} |")
    b = H["H-B"]
    L.append(f"| H-B (secondary) | {b['conclusion']} | EVICON - RES_PERIODIC on K0F: {b['mean_diff_commands']:+.2f} commands per "
             f"episode, CI ({b['ci95_commands'][0]:+.2f}, {b['ci95_commands'][1]:+.2f}); {_pp(b['effect_pp'])} (bootstrap); p_holm {b['p_holm']:.2g}; "
             f"equivalent {b['equivalent']}; fences per episode {b['fences_per_episode']} |")
    sr = H["SR"]
    L.append(f"| SR (secondary) | K0: {'MET' if sr['k0'].get('pass') else 'NOT MET'}; K2: "
             f"{'not applicable (all effects null, signs agree)' if sr['k2_not_applicable'] else ('MET' if sr['k2'].get('pass') else 'NOT MET')} | "
             f"tau-b (ties < margin) K0 {sr['k0'].get('tau_b_margin_pooled')} |")
    L += ["", f"Primary hypotheses (H-A1, H-A1c, H-A2, H-S): **{'all met' if r['primary_hypotheses_pass'] else 'not all met'}**.", "",
          "## Post hoc (not part of the plan)", ""]
    ph = r["post_hoc"]
    if ph.get("SR_pooled"):
        chk = ph["SR_pooled"]["check"]
        L += [f"- **SR pooled over both configs** (contract ranking over K0, K0F, K2, K3F): {'MET' if chk['pass'] else 'NOT MET'} "
              f"(tau-b with ties < margin {chk.get('tau_b_margin_pooled')}; signs agree {chk.get('all_signs_agree')}).", ""]
    fa = ph["fence_audit"]
    L += [f"- **Testbed defect** (release 067 device emulator, fixed in 068 by device test D09): {fa['receipt_flips']} device receipt "
          f"flips (APPLIED overwritten by STOPPED after a fence), {fa['stuck_cells']} cells with a domain whose later fences all "
          "failed on the contradiction. The authority refused every contradicting receipt (no false outcome was recorded).", "",
          "| Episodes | n | A: EVICON K0->K0F | A: RES_PERIODIC | A: M1 | B: EVICON - RES_PERIODIC (K0F) |", "|---|---|---|---|---|---|"]
    for label, v in ph["sensitivity"].items():
        L.append(f"| {label.replace('_', ' ')} (dropped {v['dropped'] or '-'}) | {v['episodes']} | {_pp(v['A_EVICON'])} | "
                 f"{_pp(v['A_RES_PERIODIC'])} | {_pp(v['A_M1'])} | {_pp(v['B_EVICON_minus_RES_PERIODIC'])} |")
    L += ["", "Fence outcomes (K0F/K3F):", "", "| contract | policy | fence calls | OK | OK rate | outcomes |", "|---|---|---|---|---|---|"]
    for x in fa["rows"]:
        L.append(f"| {x['contract']} | {x['policy']} | {x['fence_calls']} | {x['fence_ok']} | {x['fence_ok_rate']} | "
                 + ", ".join(f"{k} {v}" for k, v in x["by_outcome"].items() if k != "OK") + " |")
    rp = ph.get("replications")
    if rp:
        o, runs = rp["original"], rp["runs"]

        def ci(v):
            return f"{v['effect_pp']:+.2f} ({v['ci95_pp'][0]:+.2f}, {v['ci95_pp'][1]:+.2f})"

        def col(x, key):
            if key == "A_EVICON":
                return f"{ci(x['H-A1']['policies']['EVICON'])} {word(x['H-A1']['policies']['EVICON']['pass'])}"
            if key == "A_RES":
                return f"{ci(x['H-A1']['policies']['RES_PERIODIC'])} {word(x['H-A1']['policies']['RES_PERIODIC']['pass'])}"
            if key == "A_M1":
                return f"{ci(x['H-A1c'])} {word(x['H-A1c']['pass'])}"
            if key == "S":
                s = x.get("H-S") or x.get("H-S_k0")
                return f"violations {len(s['violations'])}, late effects {s['late_effects']}"
            if key == "B":
                b = x["H-B"]
                return f"{_pp(b['effect_pp'])}; {b['conclusion']}"
            if key == "F":
                fe = x["H-B"]["fences_per_episode"]
                return f"EVICON {fe['EVICON']:g}, RES_PERIODIC {fe['RES_PERIODIC']:g}"
            if key == "OK":
                return ", ".join(f"{k} {v}" for k, v in sorted(x["fence_ok_rate"].items()))
            if key == "AU":
                return f"receipt flips {x['fence_audit']['receipt_flips']}, stuck cells {x['fence_audit']['stuck_cells']}"
            return ""
        heads = [f"original `{o['run']}` (primary)"] + [f"replication `{x['run']}`" for x in runs]
        L += ["", "## Replication of the K0 config (post hoc decision, same frozen plan)", "",
              rp["note"] + ". A replication replicates when H-A1, H-A1c and H-S hold on it alone and its fence audit shows 0 "
              "receipt flips and 0 stuck cells. The primary result stays the original run.", "",
              "| | " + " | ".join(heads) + " |", "|---|" + "---|" * len(heads)]
        for key, label in (("A_EVICON", "H-A1 EVICON K0->K0F (pp)"), ("A_RES", "H-A1 RES_PERIODIC K0->K0F (pp)"),
                           ("A_M1", "H-A1c M1 K0->K0F (pp)"), ("S", "H-S (K0 config)"), ("B", "H-B EVICON - RES_PERIODIC (pp)"),
                           ("F", "fences per episode (K0F)"), ("OK", "fence OK rate (K0F)"), ("AU", "fence audit")):
            L.append(f"| {label} | " + " | ".join(col(x, key) for x in [o] + runs) + " |")
        L.append("| verdict | primary | " + " | ".join("**replicates**" if x["replicates"] else
                                                      ("**does not replicate**" if x["emulator_clean"] else
                                                       "not used (emulator defect)") for x in runs) + " |")
    rp2 = ph.get("replications_k2")
    if rp2:
        o, runs = rp2["original"], rp2["runs"]

        def ci2(v):
            return f"{v['effect_pp']:+.2f} ({v['ci95_pp'][0]:+.2f}, {v['ci95_pp'][1]:+.2f})"

        def col2(x, key):
            if key in ("EVICON", "RES_PERIODIC"):
                v = x["H-A2"]["policies"][key]
                return f"{ci2(v)} {word(v['pass'])}"
            if key == "S":
                s = x.get("H-S") or x.get("H-S_k2")
                return f"violations {len(s['violations'])}, late effects {s['late_effects']}"
            if key == "F":
                return ", ".join(f"{k} {v}" for k, v in sorted(x["fences"].items())) or "-"
            if key == "OK":
                return ", ".join(f"{k} {v}" for k, v in sorted(x["fence_ok_rate"].items())) or "-"
            if key == "AU":
                return f"receipt flips {x['fence_audit']['receipt_flips']}, stuck cells {x['fence_audit']['stuck_cells']}"
            return ""
        heads = [f"original `{o['run']}` (primary)"] + [f"replication `{x['run']}`" for x in runs]
        L += ["", "## Replication of the K2 config (R-K2; post hoc decision, same frozen plan)", "",
              rp2["note"] + ". A replication replicates when H-A2 and H-S hold on it alone and its fence audit shows 0 receipt flips "
              "and 0 stuck cells. The primary result stays the original run.", "",
              "| | " + " | ".join(heads) + " |", "|---|" + "---|" * len(heads)]
        for key, label in (("EVICON", "H-A2 EVICON K2->K3F (pp)"), ("RES_PERIODIC", "H-A2 RES_PERIODIC K2->K3F (pp)"),
                           ("S", "H-S (K2 config)"), ("F", "fence calls (K3F)"), ("OK", "fence OK rate (K3F)"), ("AU", "fence audit")):
            L.append(f"| {label} | " + " | ".join(col2(x, key) for x in [o] + runs) + " |")
        L.append("| verdict | primary | " + " | ".join("**replicates**" if x["replicates"] else
                                                      ("**does not replicate**" if x["emulator_clean"] else
                                                       "not used (emulator defect)") for x in runs) + " |")
    L += ["", "Effects in percentage points of clean fulfilment; CIs are percentile bootstrap (4000) over episodes for the post hoc "
              "table and the frozen analysis' intervals for the hypotheses."]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    sys.exit(main())
