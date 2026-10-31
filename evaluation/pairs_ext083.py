#!/usr/bin/env python3
"""Paired analysis of PREREG_extra_083.md (release 083): pairs_report.py plus policy contrasts, cross-run
contrasts between contracts or policies, differences of contrasts, and trace counts.

evaluation/pairs_report.py (frozen since release 072) decides the hypotheses of a plan's ```pairs-spec```
block: contrasts between two contracts of one policy within a run, detection and absence rules, the safety rule,
estimands (also across runs of the same seed). The 083 plan also needs contrasts it cannot express. They are
declared in a second block of the plan, ```pairs-ext```, and decided here with pairs_report's own functions
(load_run, paired schedules, one_sided, holm, summary; imported, never edited):

  contrast   {"id", "family", "run", "from_run"?, "from"/"contract", "to"/"contract", "policy" or "from_policy" and
              "to_policy", "metric", "test": at_least | at_most | equivalent, "margin"}: per-episode difference of
              arm b = (run, to, to_policy) minus arm a = (from_run or run, from, from_policy). Across runs an
              episode is used only when its schedule file (schedule_ep<e>.json) is byte-identical in both runs
              (common random numbers), as pairs_report's estimands do
  did        {"id", "family", "test", "margin", "metric", "a": <contrast>, "b": <contrast>}: per-episode difference
              of two contrasts (b minus a), episodes whose schedules agree in every run involved (the window sweep's
              direction rules)
  detects / absent  as pairs_report, with the extra cell metrics below in any_of
  safety     {"id", "test": "safety", "runs": [...], "exclude_policies"?, "exempt_contracts"?}: pairs_report's safety
             rule (the five safety counts 0 in every VALID cell of the runs) over the runs that were run; a run not
             given is listed as not run (the verdict is NOT RUN only when none was run), and the practice baselines
             of the runs (unsafe by design) are left out through exclude_policies
  estimands  the same contrast forms, reported with intervals and no claim

Extra cell metrics (besides pairs_report's): search_failures (DEFER decisions EVICON_CERTIFICATE_SEARCH_FAILED,
counted in the cell's authority traces; the top-8 defer reasons when the traces are absent), violations (the
sum of the five safety counts), releases_without_evidence (the baselines' RELEASE_ON_TIMEOUT / RELEASE_ON_READ
trace events), queries_per_episode.

Holm is applied per family; the ```pairs-ext``` families are disjoint from the ```pairs-spec``` families (the plan
keeps them apart), so pairs_report.py alone decides its block exactly as here.

    python3 evaluation/pairs_ext083.py --plan evaluation/prereg/PREREG_extra_083.md --run KEY=DIR ... --out DIR
Writes RESULTS.md, results.json and cells.csv (pairs_report's format, extended) into --out.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pairs_report as PR  # noqa: E402  (frozen since release 072; imported, never edited)

EXTRA_METRICS = ("search_failures", "violations", "releases_without_evidence")


def trace_counts(celldir: Path) -> dict:
    traces = sorted(celldir.glob("raw/**/authority*.jsonl"))
    if not traces:
        return {}
    sf = rel = 0
    for tr in traces:
        with open(tr, errors="replace") as fh:
            for line in fh:
                if "CERTIFICATE_SEARCH_FAILED" in line and '"DECISION_RECORDED"' in line and '"DEFER"' in line:
                    sf += 1
                elif '"event":"RELEASE_ON_TIMEOUT"' in line.replace(" ", "") or '"event":"RELEASE_ON_READ"' in line.replace(" ", ""):
                    rel += 1
    return {"search_failures": float(sf), "releases_without_evidence": float(rel)}


def load_run(key: str, run: Path) -> dict:
    r = PR.load_run(key, run)
    for (ct, pol, ep), m in r["cells"].items():
        celldir = run / "cells" / f"ep{ep}" / ct / pol
        tc = trace_counts(celldir)
        if "search_failures" not in tc:
            tc["search_failures"] = float(sum(int(v) for k, v in m["defer_reasons"].items() if "CERTIFICATE_SEARCH_FAILED" in k))
            tc["releases_without_evidence"] = math.nan
        m.update(tc)
        m["violations"] = float(sum(m[k] for k in PR.SAFETY if not math.isnan(m[k])))
    return r


def spec_blocks(plan: Path):
    text = plan.read_text()
    std, info = PR.plan_info(plan)
    blk = re.search(r"```pairs-ext\n(.*?)```", text, re.S)
    ext = json.loads(blk.group(1)) if blk else {"hypotheses": [], "estimands": []}
    return std, ext, info


def arms(h: dict):
    run = h["run"]
    fr = h.get("from_run", run)
    ca, cb = h.get("from", h.get("contract")), h.get("to", h.get("contract"))
    pa, pb = h.get("from_policy", h.get("policy")), h.get("to_policy", h.get("policy"))
    return (fr, ca, pa), (run, cb, pb)


def label(h: dict) -> str:
    (fr, ca, pa), (r, cb, pb) = arms(h)
    a = f"{fr}:{ca}/{pa}" if fr != r else f"{ca}/{pa}"
    return f"{r}: {a} -> {cb}/{pb}"


def contrast(runs: dict, h: dict, metric: str) -> dict:
    """{episode: (b - a, schedules)} for the episodes where both cells are VALID (and, across runs, share a schedule)."""
    (fr, ca, pa), (r, cb, pb) = arms(h)
    RA, RB = runs.get(fr), runs.get(r)
    if RA is None or RB is None:
        return {}
    out = {}
    for (ct, pol, e), mb in RB["cells"].items():
        if ct != cb or pol != pb:
            continue
        ma = RA["cells"].get((ca, pa, e))
        if ma is None:
            continue
        if fr != r and (not mb["schedule"] or mb["schedule"] != ma["schedule"]):
            continue
        out[e] = (mb[metric] - ma[metric], mb["schedule"])
    return out


def values(runs: dict, h: dict) -> list:
    if h["test"] in ("at_least", "at_most", "equivalent") and "a" in h:  # did
        A, B = contrast(runs, h["a"], h["metric"]), contrast(runs, h["b"], h["metric"])
        return [B[e][0] - A[e][0] for e in sorted(set(A) & set(B)) if B[e][1] == A[e][1] or not (A[e][1] and B[e][1])]
    return [v for _, (v, _) in sorted(contrast(runs, h, h["metric"]).items())]


def decide(spec: dict, runs: dict, alpha: float) -> list:
    out, fam_p = [], defaultdict(dict)
    for h in spec.get("hypotheses", []):
        if h["test"] == "safety":
            out.append(safety(h, runs))
            continue
        o = {"id": h["id"], "family": h.get("family", "ext"), "test": h["test"], "spec": h, "form": "did" if "a" in h else "contrast"}
        keys = ({h["a"]["run"], h["a"].get("from_run", h["a"]["run"]), h["b"]["run"], h["b"].get("from_run", h["b"]["run"])} if "a" in h
                else {h["run"], h.get("from_run", h["run"])})
        o["run"] = h.get("run") or "+".join(sorted(keys))
        if h["test"] in ("at_least", "at_most", "equivalent"):
            o["label"] = (f"({label(h['b'])}) - ({label(h['a'])}), {h['metric']}" if "a" in h else f"{label(h)}, {h['metric']}")
        missing = [k for k in keys if k not in runs]
        if missing:
            o["verdict"] = "NOT RUN"
            o["missing_runs"] = missing
        elif any(not runs[k]["interpretable"] for k in keys):
            o["verdict"] = "NOT INTERPRETABLE"
        elif h["test"] in ("detects", "absent"):
            r = runs[h["run"]]
            ms = [m for (ct, pol, _), m in r["cells"].items() if ct == h["contract"] and pol == h.get("policy", pol)]
            counts = {k: int(sum(m[k] for m in ms if not math.isnan(m[k]))) for k in h["any_of"]}
            hit = sum(1 for m in ms if any((not math.isnan(m[k])) and m[k] > 0 for k in h["any_of"]))
            o.update({"cells": len(ms), "cells_detecting": hit, "counts": counts})
            if not ms:
                o["verdict"] = "NOT COMPUTABLE"
            elif h["test"] == "detects":
                o["verdict"] = "DETECTED" if hit else "NOT DETECTED"
            else:
                o["verdict"] = "ABSENT" if hit == 0 else "PRESENT"
        else:
            d = [v for v in values(runs, h) if not math.isnan(v)]
            o.update(PR.summary(d))
            if len(d) >= 2:  # unrounded, so that paper_tables.py rounds once (OPEN_ITEMS (a) 6)
                o["mean_exact"] = statistics.mean(d)
                o["t95_exact"] = list(PR.t_interval(d, 0.95) or (math.nan, math.nan))
            m = float(h.get("margin", 0.0))
            if len(d) < 2:
                o["verdict"] = "NOT COMPUTABLE"
            else:
                if h["test"] == "at_least":
                    p = PR.one_sided(d, m, upper=False)
                elif h["test"] == "at_most":
                    p = PR.one_sided(d, m, upper=True)
                else:
                    p = max(PR.one_sided(d, -m, upper=False), PR.one_sided(d, m, upper=True))
                    o["t_tost"] = [round(v, 3) for v in (PR.t_interval(d, 1 - 2 * alpha) or (math.nan, math.nan))]
                o["p"] = p
                fam_p[o["family"]][h["id"]] = p
        out.append(o)
    adj = {}
    for fam, ps in fam_p.items():
        adj.update(PR.holm(ps))
    for o in out:
        if o["id"] in adj:
            o["p_holm"] = round(adj[o["id"]], 6)
            o["p"] = round(o["p"], 6)
            o["verdict"] = "MET" if adj[o["id"]] < alpha else "NOT MET"
    return out


def safety(h: dict, runs: dict) -> dict:
    """The safety rule over the runs of a group that were run (pairs_report's rule: every VALID cell, the five
    safety counts; late effects reported separately), without the excluded policies and contracts."""
    excl, exempt = set(h.get("exclude_policies", [])), set(h.get("exempt_contracts", []))
    present = [k for k in h["runs"] if k in runs]
    o = {"id": h["id"], "family": "safety", "test": "safety", "spec": h, "form": "safety", "run": "+".join(h["runs"]),
         "not_run": [k for k in h["runs"] if k not in runs], "label": f"safety, runs {', '.join(h['runs'])}"}
    viol, cells, late, counts = [], 0, 0, {k: 0 for k in PR.SAFETY}
    for key in present:
        for (ct, pol, ep), m in sorted(runs[key]["cells"].items()):
            if pol in excl or ct in exempt:
                continue
            cells += 1
            late += int(m["late_effects"]) if not math.isnan(m["late_effects"]) else 0
            bad = {k: int(m[k]) for k in PR.SAFETY if not math.isnan(m[k]) and m[k] > 0}
            for k, v in bad.items():
                counts[k] += v
            if bad:
                viol.append({"run": key, "cell": f"ep{ep}/{ct}/{pol}", **bad})
    o.update({"cells": cells, "cells_detecting": len(viol), "counts": counts, "violations": viol, "late_effects": late})
    if not present:
        o["verdict"] = "NOT RUN"
    elif cells == 0:
        o["verdict"] = "NOT COMPUTABLE"
    else:
        o["verdict"] = "MET" if not viol else "NOT MET"
    return o


def estimands(spec: dict, runs: dict) -> list:
    out = []
    for e in spec.get("estimands", []):
        keys = ({e["a"]["run"], e["a"].get("from_run", e["a"]["run"]), e["b"]["run"], e["b"].get("from_run", e["b"]["run"])} if "a" in e
                else {e["run"], e.get("from_run", e["run"])})
        if any(k not in runs for k in keys):
            continue
        res = {"label": e.get("label") or (f"({label(e['b'])}) - ({label(e['a'])})" if "a" in e else label(e)), "metrics": {}}
        for m in e["metrics"]:
            res["metrics"][m] = PR.summary([v for v in values(runs, dict(e, metric=m, test="at_least")) if not math.isnan(v)])
        out.append(res)
    return out


def levels(runs: dict) -> dict:
    out = {}
    cols = ("clean_pp", "confirmed_pp", "admitted_pp", "violations", "search_failures", "queries", "releases_without_evidence",
            "late_effects", "unresolved_at_end", "window_defers")
    for key, r in runs.items():
        g = defaultdict(list)
        for (ct, pol, _), m in r["cells"].items():
            g[f"{ct}/{pol}"].append(m)
        out[key] = {k: dict({c: (round(statistics.mean(x for x in (mm[c] for mm in ms) if not math.isnan(x)), 3)
                                 if any(not math.isnan(mm[c]) for mm in ms) else None) for c in cols}, n=len(ms)) for k, ms in sorted(g.items())}
    return out


def _ev(h: dict, alpha: float) -> str:
    if h.get("n"):
        op = {"at_least": ">=", "at_most": "<=", "equivalent": "within ±"}.get(h["test"], h["test"])
        return (f"mean {h['mean']:+.2f}, 95 % t {PR._ci(h.get('t95'))}" + (f", {100 - 200 * alpha:.0f} % t {PR._ci(h.get('t_tost'))}" if h.get("t_tost") else "")
                + f"; {op} {h['spec'].get('margin', 0):g}; p {h.get('p', '-')}, Holm {h.get('p_holm', '-')}; n={h['n']}")
    if h.get("form") == "safety":
        return (f"{h['cells_detecting']} of {h['cells']} cells with violations; counts {h['counts']}; late effects {h['late_effects']}"
                + (f"; not run: {', '.join(h['not_run'])}" if h["not_run"] else ""))
    if h.get("cells") is not None:
        return f"{h.get('cells_detecting', 0)} of {h['cells']} cells; counts {h.get('counts')}"
    return "-" if not h.get("missing_runs") else f"runs not given: {', '.join(h['missing_runs'])}"


def markdown(std_res: dict, ext: list, est: list, lv: dict, alpha: float) -> str:
    L = [PR.markdown(std_res).rstrip(), "", "## Hypotheses of the ```pairs-ext``` block (policy, cross-run and difference contrasts)", "",
         "| ID | family | test | comparison | result | evidence |", "|---|---|---|---|---|---|"]
    for h in ext:
        s = h["spec"]
        if h["form"] == "safety":
            cmp_ = h["label"] + (f" (without {', '.join(s['exclude_policies'])})" if s.get("exclude_policies") else "")
        elif h["form"] == "did":
            cmp_ = f"({label(s['b'])}) - ({label(s['a'])}), {s['metric']}"
        elif h["test"] in ("detects", "absent"):
            cmp_ = f"{s['run']}: {s['contract']}{', ' + s['policy'] if s.get('policy') else ''}, any of {', '.join(s['any_of'])}"
        else:
            cmp_ = f"{label(s)}, {s['metric']}"
        L.append(f"| {h['id']} | {h['family']} | {h['test']} | {cmp_} | **{h['verdict']}** | {_ev(h, alpha)} |")
    if est:
        L += ["", "## Estimands of the ```pairs-ext``` block (no claim; per-episode differences)", "",
              "| comparison | metric | mean | 95 % t | 95 % bootstrap | n |", "|---|---|---|---|---|---|"]
        for e in est:
            for m, s in e["metrics"].items():
                if s.get("n"):
                    L.append(f"| {e['label']} | {PR.METRIC_LABELS.get(m, m)} | {s['mean']:+.3f} | {PR._ci(s['t95'])} | {PR._ci(s['boot95'])} | {s['n']} |")
    L += ["", "## Levels with the extra metrics (means per episode)", "",
          "| run | contract/policy | n | clean pp | confirmed pp | admitted pp | violations | search failures | queries | releases without evidence | late effects | unresolved at end | window defers |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for key, g in lv.items():
        for k, v in g.items():
            L.append(f"| {key} | {k} | {v['n']} | " + " | ".join("-" if v[c] is None else f"{v[c]:g}" for c in
                     ("clean_pp", "confirmed_pp", "admitted_pp", "violations", "search_failures", "queries", "releases_without_evidence",
                      "late_effects", "unresolved_at_end", "window_defers")) + " |")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--run", action="append", default=[], metavar="KEY=DIR")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    plan = Path(a.plan)
    if not plan.is_file() and (PR.REPO / a.plan).is_file():
        plan = PR.REPO / a.plan
    std, ext, info = spec_blocks(plan)
    runs = {}
    for kv in a.run:
        key, _, d = kv.partition("=")
        if not d:
            raise SystemExit(f"--run {kv}: want KEY=DIR")
        runs[key] = load_run(key, Path(d))
    alpha = float(std.get("alpha", 0.05))
    res = PR.analyse(std, runs, info)
    for k in runs:  # pairs_report sets "interpretable" per its own rule; keep it for the ext block
        runs[k]["interpretable"] = res["runs"].get(k, {}).get("interpretable", runs[k]["interpretable"])
    fams_std = {h.get("family", "primary") for h in std.get("hypotheses", []) if h.get("test") not in ("detects", "absent")}
    fams_ext = {h.get("family", "ext") for h in ext.get("hypotheses", []) if h.get("test") not in ("detects", "absent", "safety")}
    if fams_std & fams_ext:
        raise SystemExit(f"families shared by pairs-spec and pairs-ext: {sorted(fams_std & fams_ext)} (Holm would differ from pairs_report.py)")
    ext_res = decide(ext, runs, alpha)
    est = estimands(ext, runs)
    lv = levels(runs)
    merged = dict(res, hypotheses=res["hypotheses"] + ext_res, ext_estimands=est, ext_levels=lv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(merged, indent=2, default=str) + "\n")
    (out / "RESULTS.md").write_text(markdown(res, ext_res, est, lv, alpha))
    PR.write_cells(runs, out / "cells.csv")
    with open(out / "cells_extra.csv", "w") as fh:
        fh.write("run,contract,policy,episode," + ",".join(EXTRA_METRICS) + "\n")
        for key, r in runs.items():
            for (ct, pol, ep), m in sorted(r["cells"].items()):
                fh.write(f"{key},{ct},{pol},{ep}," + ",".join("" if math.isnan(m[c]) else f"{m[c]:g}" for c in EXTRA_METRICS) + "\n")
    v = ", ".join(f"{h['id']} {h['verdict']}" for h in merged["hypotheses"])
    if "safety" in res:
        v += f", {res['safety']['id']} {res['safety']['verdict']}"
    print(f"{res['name']}: {v or 'no hypotheses'} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
