#!/usr/bin/env python3
"""Claims against evidence (release 075): every preregistered hypothesis and every cross-cutting claim of the
paper, checked mechanically against the files of one report (`evicon_review.py report`).

Part 1 lists the verdict of every hypothesis of every plan the report decided (pairs_report.py plans,
experiment A, experiment B2), with its estimate, so that the paper reports all of them, NOT MET included.
Part 2 checks the cross-cutting claims, each against the file it rests on:

  safety       no safety violation in any guarded testbed cell (safety/safety.json), with the exact 95 %
               upper bound; every negative-control cell detected a violation
  T3           no capacity re-opening after APPLIED-only evidence or no evidence, and no ADMITTED after a
               re-test on APPLIED-only evidence, in any run except the NEG_* negative controls and (release 081)
               the practice baselines RELEASE_ON_TIMEOUT, RELEASE_ON_READ, which release without evidence by
               design and are counted apart (t3/t3.json)
  contracts    the same system offers different measured contracts for different uses of its API
               (contract_map.csv: systems with more than one measured level)
  simulator    every scored effect has the sign the simulator predicted (predict/predict.json)
  fidelity     where the emulated owner's contract equals the real owner's, the emulated owner reproduces the
               real owner's clean fulfilment: equivalent within +-5 pp by the paired TOST (same seed, the same
               schedules), or within +-10 pp by Welch's TOST where the runs used different seeds (p < 0.05)
  reboot       across an owner reboot the firmware stays safe (reboot/reboot.json; outside the measured fault model)

A claim is SUPPORTED, NOT SUPPORTED, or NO DATA (its file is missing). The script states what the data
say; it does not choose what the paper claims.

    python3 evaluation/claims_check.py --report <report dir>
Writes CLAIMS.md and claims.json into <report dir>/claims.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

# fidelity pairs whose contracts are equal (the other pairs differ in capabilities or fault model)
MATCHED = {("MINIO_PLAIN", "K0"), ("ZEPHYR_PLAIN", "K0"), ("ZEPHYR_PLAIN_DIRECT", "K0")}
# release 081: the practice baselines of PREREG_review_077 (= safety_total.BASELINES, t3_attribution.BASELINES)
T3_BASELINES = ("RELEASE_ON_TIMEOUT", "RELEASE_ON_READ")


def _g(x) -> str:
    return f"{x:.2g}" if isinstance(x, (int, float)) else str(x)


def load(p: Path):
    return json.loads(p.read_text()) if p.exists() else None


def expA_verdict(h: dict) -> str:
    """Release 076: the verdict confirm_report.py gave (RESULTS_expA_confirm_067.md), not a pass flag the
    secondary hypotheses do not have: H-B has a conclusion ("inconclusive"), SR one verdict per config
    (K2 not applicable by design: only null effects)."""
    if "pass" in h:
        return "MET" if h["pass"] else "NOT MET"
    if h.get("conclusion"):
        return str(h["conclusion"]).upper()
    if isinstance(h.get("k0"), dict):
        k0 = "MET" if h["k0"].get("pass") else "NOT MET"
        k2 = "not applicable" if h.get("k2_not_applicable") else ("MET" if (h.get("k2") or {}).get("pass") else "NOT MET")
        return f"K0 {k0}; K2 {k2}"
    return "NOT DECIDED"


def hypotheses(report: Path) -> list:
    rows = []
    for f in sorted((report / "pairs").glob("*/results.json")):
        r = json.loads(f.read_text())
        for h in r.get("hypotheses", []):
            est = ""
            if h.get("mean") is not None:
                ci = h.get("t95") or []
                est = f"{h['mean']:+.2f}" + (f" [{ci[0]:+.2f}, {ci[1]:+.2f}]" if ci else "")
            elif h.get("cells") is not None:
                hit = h.get("cells_detecting", h.get("cells_hit", h.get("cells_with", "")))
                est = f"{hit} of {h['cells']} cells" + (f"; {h['counts']}" if h.get("counts") and any(h["counts"].values()) else "")
            rows.append({"plan": f.parent.name, "id": h["id"], "test": h.get("test"), "verdict": h.get("verdict"), "estimate": est,
                         "p_holm": h.get("p_holm")})
        if r.get("safety"):
            rows.append({"plan": f.parent.name, "id": r["safety"]["id"], "test": "safety", "verdict": r["safety"]["verdict"],
                         "estimate": f"{len(r['safety']['violations'])} cells with violations", "p_holm": None})
    a = load(report / "confirm_expA" / "results.json")
    if a:
        for hid, h in a.get("hypotheses", {}).items():
            est = ""
            if "policies" in h:
                est = "; ".join(f"{p}: {v.get('effect_pp', ''):+.2f}" for p, v in h["policies"].items() if isinstance(v.get("effect_pp"), (int, float)))
            elif isinstance(h.get("effect_pp"), (int, float)):
                est = f"{h['effect_pp']:+.2f}"
            elif isinstance(h.get("mean_diff_commands"), (int, float)):  # H-B: EVICON - RES_PERIODIC, commands per episode
                ci = h.get("ci95_commands") or []
                est = f"{h['mean_diff_commands']:+.2f} commands per episode" + (f" [{ci[0]:+.2f}, {ci[1]:+.2f}]" if len(ci) == 2 else "")
            rows.append({"plan": "PREREG_expA_confirm_067", "id": hid, "test": "", "verdict": expA_verdict(h),
                         "estimate": est, "p_holm": None})
    b = load(report / "cadence" / "results.json")
    if b:
        for hid in ("H-C", "H-S"):
            h = b.get(hid)
            if isinstance(h, dict):
                est = f"{h['mean_pp']:+.2f}" if isinstance(h.get("mean_pp"), (int, float)) else ""
                rows.append({"plan": "PREREG_expB2_cadence_071", "id": hid, "test": h.get("rule", "")[:60],
                             "verdict": h.get("verdict", "MET" if h.get("pass") else "NOT MET"), "estimate": est, "p_holm": None})
    return rows


def checks_077(report: Path) -> list:
    """Release 080: the hypotheses of PREREG_review_077.md that pairs_report.py does not decide, with the plan's rules:
    L2-1 from late_attribution.py (frozen) on run L2: no late effect from a resend sent after the intent's validity in
    any VALID K2/EVICON cell; X-Q0 (qos-dup: commands delivered twice or more > 0 at QoS 1 and = 0 at QoS 0); X-MCU (the
    emulated MCU's measured contract is {D_h, Q_A, F}, K3); X-AUTH (cases A1-A13 all PASS). A check without data in the
    report is left out."""
    rows = []
    la = load(report / "late" / "late_attribution.json")
    l2 = [r for r in (la or []) if isinstance(r, dict) and r.get("campaign") == "expL2-sleepy"]
    if l2:
        cells = [c for r in l2 for c in r.get("cells", []) if c.get("contract") == "K2" and c.get("policy") == "EVICON"]
        after = sum(int(c.get("late_resend", 0)) - int(c.get("late_resend_sent_in_validity", 0)) for c in cells)
        bad = sum(1 for c in cells if int(c.get("late_resend", 0)) - int(c.get("late_resend_sent_in_validity", 0)) > 0)
        late = sum(int(c.get("late_effects", 0)) for c in cells)
        first = sum(int(c.get("late_first_send", 0)) for c in cells)
        rows.append({"plan": "PREREG_review_077", "id": "L2-1", "test": "late_attribution",
                     "verdict": ("MET" if bad == 0 else "NOT MET") if cells else "NOT RUN",
                     "estimate": f"{bad} of {len(cells)} cells with a late effect from a resend sent after the validity ({after}); "
                                 f"late effects {late}: first send {first}, resend sent within the validity {late - first - after}",
                     "p_holm": None})
    q = load(report / "qos_dup" / "qos_dup_check.json")
    if q:
        by = {r.get("qos"): r for r in q.get("results", [])}
        d1 = (by.get(1) or {}).get("commands_delivered_twice_or_more")
        d0 = (by.get(0) or {}).get("commands_delivered_twice_or_more")
        ok = d1 is not None and d0 is not None and d1 > 0 and d0 == 0
        rows.append({"plan": "PREREG_review_077", "id": "X-Q0", "test": "check", "verdict": "PASS" if ok else "FAIL",
                     "estimate": f"QoS 1: {d1} of {(by.get(1) or {}).get('commands')} commands delivered twice or more; QoS 0: {d0}",
                     "p_holm": None})
    m = load(report / "mcu" / "measure.json")
    if m:
        res = [r for r in m.get("results", []) if r.get("config") == "zephyr-mqtt-mcu"]
        caps = sorted(res[0].get("capabilities", [])) if res else []
        ok = bool(res) and caps == sorted(["D_h", "Q_A", "F"]) and res[0].get("level") == "K3"
        rows.append({"plan": "PREREG_review_077", "id": "X-MCU", "test": "check", "verdict": "PASS" if ok else "FAIL",
                     "estimate": f"zephyr-mqtt-mcu: {caps} ({res[0].get('level') if res else '-'})", "p_holm": None})
    a = load(report / "auth" / "auth.json")
    if a:
        cases = a.get("cases", [])
        npass = sum(1 for c in cases if c.get("result") == "PASS")
        ok = a.get("verdict") == "PASS" and len(cases) == 13 and npass == 13
        rows.append({"plan": "PREREG_review_077", "id": "X-AUTH", "test": "check", "verdict": "PASS" if ok else "FAIL",
                     "estimate": f"{npass} of {len(cases)} cases PASS", "p_holm": None})
    return rows


def claim_safety(report: Path) -> dict:
    s = load(report / "safety" / "safety.json")
    if not s:
        return {"id": "safety", "status": "NO DATA"}
    s = s["summary"]
    g, ctl = s["guarded"], s["controls"]
    ok = s["guarded_violations"] == 0 and ctl.get("cells", 0) > 0 and ctl.get("detecting_cells") == ctl.get("cells")
    return {"id": "safety", "status": "SUPPORTED" if ok else "NOT SUPPORTED",
            "evidence": f"{g.get('cells', 0)} guarded cells, {g.get('admitted', 0)} admitted commands, {s['guarded_violations']} violations "
                        f"(exact one-sided 95 % upper bound per admitted command {_g(s.get('upper95_per_admitted_command'))}); negative controls "
                        f"{ctl.get('detecting_cells', 0)}/{ctl.get('cells', 0)} detected", "file": "safety/safety.json"}


def claim_t3(report: Path) -> dict:
    t = load(report / "t3" / "t3.json")
    if not t:
        return {"id": "T3", "status": "NO DATA"}
    bad, retested, base_runs, base_open = [], 0, set(), 0
    for run, r in t.items():
        for grp, g in (r.get("groups") or {}).items():
            if grp.startswith("NEG_"):
                continue
            cap = g.get("capacity") or {}
            if grp.split("/")[-1] in T3_BASELINES:  # 081: released without evidence by design (rules RT, RR of plan 077)
                base_runs.add(run)
                base_open += cap.get("APPLIED_ONLY", 0) + cap.get("NONE", 0)
                continue
            adm = (g.get("retested_after_applied") or {}).get("ADMITTED", 0)
            retested += sum((g.get("retested_after_applied") or {}).values())
            if cap.get("APPLIED_ONLY", 0) or cap.get("NONE", 0) or adm:
                bad.append(f"{run} {grp}: {cap} admitted after APPLIED {adm}")
    base = (f"; practice baselines left out ({', '.join(T3_BASELINES)}, unsafe by design: {base_open} re-openings without "
            f"evidence of non-occurrence in {len(base_runs)} runs)") if base_runs else ""
    return {"id": "T3", "status": "SUPPORTED" if not bad else "NOT SUPPORTED",
            "evidence": (f"{len(t)} runs, {retested} re-tests after APPLIED-only evidence, 0 re-openings without evidence of non-occurrence"
                         if not bad else "; ".join(bad[:5])) + base, "file": "t3/t3.json"}


def claim_contracts(report: Path) -> dict:
    p = report / "contract_map.csv"
    if not p.exists():
        return {"id": "contracts", "status": "NO DATA"}
    by = {}
    with open(p, newline="") as fh:
        for r in csv.DictReader(fh):
            if r["source"] == "MEASURED":
                sysname = r["system"].split(",")[0].split("(")[0].split("->")[-1].strip()
                by.setdefault(sysname, set()).add(r["level"])
    multi = {k: sorted(v) for k, v in by.items() if len(v) > 1}
    return {"id": "contracts", "status": "SUPPORTED" if multi else "NOT SUPPORTED",
            "evidence": "; ".join(f"{k}: {', '.join(v)}" for k, v in sorted(multi.items())) or "no system with two measured levels",
            "file": "contract_map.csv"}


def claim_simulator(report: Path) -> dict:
    p = load(report / "predict" / "predict.json")
    if not p:
        return {"id": "simulator", "status": "NO DATA"}
    s = p["summary"]
    if not s.get("effects_scored"):
        return {"id": "simulator", "status": "NO DATA", "evidence": "no effect with a testbed estimate and a prediction", "file": "predict/predict.json"}
    ok = s["same_sign"] == s["effects_scored"]
    miss = [f"{e['run'].split('/')[0]} {e['policy']} {e['from']} -> {e['to']} {e['metric']}: testbed {e['testbed']:+.2f}, simulator {e['sim']:+.2f}"
            for e in p.get("effects", []) if e.get("same_sign") is False and isinstance(e.get("testbed"), (int, float))
            and isinstance(e.get("sim"), (int, float))]
    return {"id": "simulator", "status": "SUPPORTED" if ok else "NOT SUPPORTED",
            "evidence": f"{s['same_sign']}/{s['effects_scored']} effects with the predicted sign, {s.get('direction_agrees')} with the predicted "
                        f"direction (null within 2 pp); Spearman {s['effect_spearman']}; effect MAE {s['effect_mae_pp']} pp; level bias "
                        f"{s['level_bias_clean_pp']} pp (testbed minus simulator)" + (f"; sign misses: {'; '.join(miss)}" if miss else ""),
            "file": "predict/predict.json"}


def claim_fidelity(report: Path) -> dict:
    f = load(report / "fidelity" / "fidelity.json")
    if f is None:
        return {"id": "fidelity", "status": "NO DATA"}
    rows, ok = [], True
    for x in f:
        if (x["real"]["contract"], x["emulated"]["contract"]) not in MATCHED:
            continue
        m = x["metrics"]["clean_pp"]
        test = m.get("paired") or m.get("unpaired") or {}
        margin = "5.0" if m.get("paired") else "10.0"
        tost = test.get("tost") or {}
        p = tost.get(margin)
        good = p is not None and p < 0.05
        ok &= good
        rows.append(f"{x['real']['run'].split('/')[0]} {x['real']['contract']} vs {x['emulated']['run'].split('/')[0]} {x['emulated']['contract']}: "
                    f"{test.get('diff', 0):+.2f} pp ({'paired' if m.get('paired') else 'Welch, other seed'}; TOST p +-5 {tost.get('5.0')}, "
                    f"+-10 {tost.get('10.0')})")
    if not rows:
        return {"id": "fidelity", "status": "NO DATA"}
    return {"id": "fidelity", "status": "SUPPORTED" if ok else "NOT SUPPORTED", "evidence": "; ".join(rows), "file": "fidelity/fidelity.json"}


def claim_reboot(report: Path) -> dict:
    r = load(report / "reboot" / "reboot.json")
    if not r:
        return {"id": "reboot", "status": "NO DATA"}
    return {"id": "reboot", "status": "SUPPORTED" if r.get("safe") else "NOT SUPPORTED",
            "evidence": f"duplicates {r['duplicates'] or 'none'}, false STOPPED {r['false_stopped'] or 'none'}, false APPLIED "
                        f"{r['false_applied'] or 'none'}; UNCERTAIN after the reboot {r['uncertain_after_reboot']} (safe, not live)",
            "file": "reboot/reboot.json"}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", required=True)
    a = ap.parse_args(argv)
    report = Path(a.report)
    hyp = hypotheses(report) + checks_077(report)
    claims = [claim_safety(report), claim_t3(report), claim_contracts(report), claim_simulator(report), claim_fidelity(report),
              claim_reboot(report)]
    out = report / "claims"
    out.mkdir(parents=True, exist_ok=True)
    (out / "claims.json").write_text(json.dumps({"hypotheses": hyp, "claims": claims}, indent=1) + "\n")
    counts = {}
    for h in hyp:
        counts[h["verdict"]] = counts.get(h["verdict"], 0) + 1
    L = ["# Claims against evidence", "", f"Part 1: {len(hyp)} preregistered hypotheses decided in this report: "
         + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())) + ". The paper reports every one of them.", "",
         "| plan | ID | test | verdict | estimate (pp) | Holm p |", "|---|---|---|---|---|---|"]
    for h in hyp:
        L.append(f"| {h['plan']} | {h['id']} | {h['test'] or ''} | **{h['verdict']}** | {h['estimate']} | {h['p_holm'] if h['p_holm'] is not None else ''} |")
    L += ["", "Part 2: cross-cutting claims.", "", "| claim | status | evidence | file |", "|---|---|---|---|"]
    for c in claims:
        L.append(f"| {c['id']} | **{c['status']}** | {c.get('evidence', '')} | {c.get('file', '')} |")
    (out / "CLAIMS.md").write_text("\n".join(L) + "\n")
    bad = [c["id"] for c in claims if c["status"] == "NOT SUPPORTED"]
    print(f"claims_check: {len(hyp)} hypotheses, {len(claims)} claims, not supported: {bad or 'none'} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
