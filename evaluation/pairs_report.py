#!/usr/bin/env python3
"""Generic paired analysis of testbed campaigns (release 072).

One script for the release-072 experiments (W: window and channel, S: stop vs fence, L: sleepy
devices, NEG: negative controls) and, later, C (MinIO). The analysis is declared in the plan, not in
this file: the plan carries one ```pairs-spec``` block (JSON, schema below) and is frozen together with
this script by scripts/freeze_prereg.py (frozen-extra block). Without --plan, --spec reads the same JSON
from a file (exploratory use; the report says so).

Unit of analysis: one episode. Every contract and policy of a run replays the same seeded arrival and
fault schedule per episode (common random numbers), so a comparison is the per-episode difference
between two VALID cells of the same run, episode and policy.

Per-cell metrics
  clean_pp       campaign.py clean_fulfilled in pp of offered: exactly one effect over every identity
                 of the request, the first one at most deadline_s after arrival
  confirmed_pp   clean AND the client saw terminal APPLIED for the identity that took effect, at most
                 deadline_s after arrival (clients.jsonl terminal_seen / final_status). Recomputed from
                 the cell's clients.jsonl and plant.jsonl (raw/E/plant.jsonl, or raw/plant.jsonl for
                 local runs); the recomputed clean count is checked against clean_fulfilled
                 (mismatches are listed). Mirrors the simulator's clean_fulfilled_confirmed, observed
                 by the client instead of the authority (so it includes the client's polling delay)
  admitted_pp, stranded_resolved_unit_sec, stranded_censored_unit_sec, mean_time_to_terminal_s,
  fences, fences_ok, stops, stops_terminal, queries, late_effects, unresolved_at_end, reissues:
                 from runtime_episodes.csv
  fences_reached, fences_reached_ok, stops_reached, stops_reached_terminal:
                 fence and stop attempts that got an answer from the owner, and those that succeeded,
                 from the cell's authority traces (raw/**/authority*.jsonl): a fence answered 2xx (OK) or
                 409 (aborted by the owner, e.g. an uncertain receipt below the frontier); a stop answered
                 STOPPED or APPLIED (terminal) or STOP_NOT_TERMINAL (the owner cannot decide, e.g. a claim
                 without a receipt). DEVICE_OFFLINE and transport errors did not reach the owner
  window_defers  DEFER decisions caused by the window of unresolved commands: reasons
                 OUTSTANDING_BUDGET_EXHAUSTED (072; EVICON reports "OUTSTANDING_BUDGET_EXHAUSTED;<its
                 reason>") and CONFIGURATION_BUDGET_EXHAUSTED (<= 071, the enumeration cap); from the
                 top-8 defer_reasons of each cell, so a lower bound when a cell has more than 8 reasons

Spec (JSON)
  {"name": "...",
   "runs": {"<key>": {"campaign": "<config name>"}, ...},        # --run <key>=<run dir>
                                                                 # "reference": true for an earlier run used only
                                                                 # in estimands (its config names another plan)
   "alpha": 0.05,
   "hypotheses": [                                               # confirmatory, Holm per family
     {"id": "H-W1", "family": "primary", "run": "<key>", "policy": "EVICON",
      "from": "K0", "to": "K0F", "metric": "clean_pp",
      "test": "at_least" | "at_most" | "equivalent", "margin": 10.0},
     {"id": "H-S2", "family": "primary", "run": "<key>", "test": "rate_greater",
      "a": {"contract": "K0S", "policy": "EVICON", "num": "stops_terminal", "den": "stops"},
      "b": {"contract": "K0F", "policy": "EVICON", "num": "fences_ok", "den": "fences"}},
     {"id": "NEG-SEAL", "run": "<key>", "test": "detects" | "absent", "contract": "NEG_SEAL",
      "any_of": ["false_stopped", "logical_duplicates"]}],
   "safety": {"id": "H-S", "runs": ["<key>", ...], "exempt_contracts": ["NEG_SEAL", ...]},
   "estimands": [{"run": "<key>", "policy": "EVICON", "from": "K0", "to": "K0F",
                  "metrics": ["confirmed_pp", ...]},
                 {"run": "<key>", "from_run": "<key>", "policy": "EVICON", "from": "K0", "to": "K0",
                  "metrics": ["clean_pp"]}],                    # across runs of the same seed
   "gap": [{"run": "<key>", "policy": "EVICON", "low": "K0", "mid": "K0F", "high": "K2",
            "high_run": "<key>"}],                               # high_run optional (default: run)
   "max_invalid_fraction": 0.10}

Decision rules (t on the per-episode differences d = to - from, df = n - 1)
  at_least m    H0: mean(d) <= m; p = 1 - T((mean - m) / se). MET when the Holm-adjusted p < alpha
                (equivalently, for one hypothesis, the one-sided lower (1 - alpha) bound exceeds m)
  at_most m     H0: mean(d) >= m; p = T((mean - m) / se)
  equivalent m  TOST: p = max(p(mean <= -m), p(mean >= m)); MET when the Holm-adjusted p < alpha
                (for one hypothesis: the (1 - 2 alpha) t-interval lies inside (-m, +m))
  rate_greater  pooled success rate of a minus that of b, episodes resampled jointly (bootstrap 4000,
                seed 72); p = share of resamples with a difference <= 0; Holm as above
  detects       at least one VALID cell of that contract (and policy, if given) has a nonzero count in
                one of any_of; no p-value, not part of a Holm family. A negative control that detects
                nothing means the harness is not sensitive and every "0 violations" result must be
                re-examined
  absent        the converse: no VALID cell of that contract has a nonzero count in any of any_of
  H-S           the safety counts (bound_violations, duplicate_effects, logical_duplicates,
                false_stopped, false_applied) are 0 in every VALID cell of the listed runs, except the
                exempt (negative-control) contracts; late effects are reported separately
A run with more than max_invalid_fraction INVALID cells is NOT INTERPRETABLE: its hypotheses are not
decided. Hypotheses whose metric cannot be computed (missing cell files) are NOT COMPUTABLE.

Estimands are reported with 95 % t and bootstrap intervals and no claim. An estimand with from_run
compares two runs of the same seed episode by episode, using only episodes whose schedule files
(schedule_ep<e>.json) are byte-identical in both runs. gap = share of the distance
from low to high that mid recovers, (mean(mid) - mean(low)) / (mean(high) - mean(low)), with a
bootstrap interval over episodes. With all three arms in one run the episodes are resampled jointly;
with high_run (the high arm from another run, other seed) the two runs are resampled independently.

Reads only. Writes RESULTS.md, results.json and cells.csv into --out.

    python3 evaluation/pairs_report.py --plan evaluation/prereg/PREREG_expW_072.md \\
        --run q6=<expW-queue-w6 run dir> --run qinf=<expW-queue-winf run dir> --out <dir>
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cadence_report import boot_ci, t_cdf, t_interval  # noqa: E402  (frozen by the 071 plan; reused, not edited)

REPO = Path(__file__).resolve().parents[1]
SAFETY = ("bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied")
TRACE_METRICS = ("fences_reached", "fences_reached_ok", "stops_reached", "stops_reached_terminal")
ROW_METRICS = ("stranded_resolved_unit_sec", "stranded_censored_unit_sec", "mean_time_to_terminal_s", "fences", "fences_ok",
               "stops", "stops_terminal", "queries", "queries_terminal", "late_effects", "unresolved_at_end", "reissues",
               "offered", "admitted", "clean_fulfilled") + SAFETY
WINDOW_REASONS = ("OUTSTANDING_BUDGET_EXHAUSTED", "CONFIGURATION_BUDGET_EXHAUSTED")
METRIC_LABELS = {"clean_pp": "clean fulfilment (pp of offered)", "confirmed_pp": "confirmed clean fulfilment (pp of offered)",
                 "admitted_pp": "admitted (pp of offered)", "stranded_resolved_unit_sec": "stranded capacity-time, resolved (unit*s)",
                 "stranded_censored_unit_sec": "stranded capacity-time, censored (unit*s)",
                 "mean_time_to_terminal_s": "mean time to a terminal outcome (s)", "fences": "fences per episode",
                 "stops": "stops per episode", "window_defers": "window-bound DEFER decisions", "late_effects": "late effects"}


# ------------------------------------------------------------------ statistics
def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return math.nan


def holm(ps: dict) -> dict:
    """Holm step-down adjusted p-values."""
    items = sorted((p, k) for k, p in ps.items() if p is not None and not math.isnan(p))
    out, run, m = {}, 0.0, len(items)
    for i, (p, k) in enumerate(items):
        run = max(run, min(1.0, (m - i) * p))
        out[k] = run
    return out


def one_sided(d: list, m: float, upper: bool) -> float:
    """p of H0: mean <= m (upper=False, i.e. testing 'greater than m') or mean >= m (upper=True)."""
    n = len(d)
    if n < 2:
        return math.nan
    mean, sd = statistics.mean(d), statistics.stdev(d)
    if sd == 0:
        return 0.0 if (mean > m if not upper else mean < m) else 1.0
    t = (mean - m) / (sd / math.sqrt(n))
    return t_cdf(t, n - 1) if upper else 1.0 - t_cdf(t, n - 1)


def summary(x: list) -> dict:
    x = [v for v in x if not math.isnan(v)]
    if not x:
        return {"n": 0}
    lo, hi = boot_ci(x)
    t95 = t_interval(x, 0.95) or (math.nan, math.nan)
    return {"n": len(x), "mean": round(statistics.mean(x), 3), "t95": [round(v, 3) for v in t95], "boot95": [round(lo, 3), round(hi, 3)]}


def boot_rate_diff(pairs: list, n: int = 4000, seed: int = 72):
    """pairs: per episode (num_a, den_a, num_b, den_b). Returns (diff, ci95, p of diff <= 0)."""
    def rate(idx):
        na, da, nb, db = (sum(pairs[i][j] for i in idx) for j in range(4))
        return (na / da if da else math.nan) - (nb / db if db else math.nan)
    full = rate(range(len(pairs)))
    rng = random.Random(seed)
    m = len(pairs)
    reps = sorted(v for v in (rate([rng.randrange(m) for _ in range(m)]) for _ in range(n)) if not math.isnan(v))
    if not reps:
        return full, (math.nan, math.nan), math.nan
    return full, (reps[int(0.025 * len(reps))], reps[int(0.975 * len(reps)) - 1]), sum(1 for v in reps if v <= 0) / len(reps)


# ------------------------------------------------------------------ cells
def plant_log(celldir: Path):
    for p in (celldir / "raw" / "E" / "plant.jsonl", celldir / "raw" / "plant.jsonl"):
        if p.is_file():
            return p
    return None


def confirmed(celldir: Path, deadline_s: float):
    """(clean recomputed, confirmed) from the cell's clients.jsonl and plant.jsonl; None if absent."""
    cl, pl = celldir / "clients.jsonl", plant_log(celldir)
    if not cl.is_file() or pl is None:
        return None
    first, count = {}, Counter()
    with open(pl) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            cid = e.get("cmd")
            if cid is None:
                continue
            count[cid] += 1
            t = int(e.get("t_ns", 0)) / 1e9
            first[cid] = min(first.get(cid, t), t)
    clean = conf = 0
    with open(cl) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            lg = json.loads(line)
            ids = lg.get("ids") or []
            eff = sum(count.get(c, 0) for c in ids)
            if eff != 1:
                continue
            cid = next(c for c in ids if count.get(c, 0))
            dl = float(lg["arrival"]) + deadline_s
            if first[cid] > dl:
                continue
            clean += 1
            seen = (lg.get("terminal_seen") or {}).get(cid)
            if (lg.get("final_status") or {}).get(cid) == "APPLIED" and seen is not None and float(seen) <= dl:
                conf += 1
    return clean, conf


def trace_actions(celldir: Path) -> dict:
    """Fence and stop attempts that reached the owner, from the cell's authority traces. A fence reached
    the owner when it got an answer (2xx, or 409 = the owner aborted it, e.g. on an uncertain receipt
    below the frontier); a stop, when the owner answered STOPPED or APPLIED or that it cannot decide
    (STOP_NOT_TERMINAL, e.g. a claim without a receipt). DEVICE_OFFLINE, transport errors and other
    5xx are not owner answers."""
    out = {"fences_reached": 0.0, "fences_reached_ok": 0.0, "stops_reached": 0.0, "stops_reached_terminal": 0.0}
    traces = sorted(celldir.glob("raw/**/authority*.jsonl"))
    if not traces:
        return {k: math.nan for k in out}
    for tr in traces:
        with open(tr) as fh:
            for line in fh:
                if "_RESULT" not in line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                ev, oc, rs = e.get("event"), str(e.get("outcome", "")), str(e.get("reason", ""))
                if ev == "EVICON_FENCE_RESULT":
                    if oc.startswith("2"):
                        out["fences_reached"] += 1
                        out["fences_reached_ok"] += 1
                    elif oc == "409":
                        out["fences_reached"] += 1
                elif ev == "EVICON_STOP_RESULT":
                    if oc in ("STOPPED", "APPLIED"):
                        out["stops_reached"] += 1
                        out["stops_reached_terminal"] += 1
                    elif rs.startswith("STOP_NOT_TERMINAL"):
                        out["stops_reached"] += 1
    return out


def load_run(key: str, run: Path) -> dict:
    cfg = json.loads((run / "config.json").read_text()) if (run / "config.json").is_file() else {}
    deadline = float(cfg.get("workload", {}).get("deadline_s", 60))
    cells, invalid, mismatch, missing, schedules = {}, [], [], [], {}
    with open(run / "runtime_episodes.csv", newline="") as fh:
        for r in csv.DictReader(fh):
            ck = (r["contract"], r["policy"], int(r["episode"]))
            if not str(r.get("cell_status", "")).startswith("VALID"):
                invalid.append(f"ep{ck[2]}/{ck[0]}/{ck[1]}")
                continue
            m = {k: _float(r.get(k)) for k in ROW_METRICS}
            off = m["offered"]
            m["clean_pp"] = 100.0 * m["clean_fulfilled"] / off if off else math.nan
            m["admitted_pp"] = 100.0 * m["admitted"] / off if off else math.nan
            try:
                reasons = json.loads(r.get("defer_reasons") or "{}")
            except ValueError:
                reasons = {}
            m["defer_reasons"] = reasons
            m["window_defers"] = float(sum(int(v) for k, v in reasons.items() if k.split(";")[0] in WINDOW_REASONS))
            m["schedule"] = schedules.setdefault(ck[2], schedule_digest(run, ck[2]))
            celldir = run / "cells" / f"ep{ck[2]}" / ck[0] / ck[1]
            m.update(trace_actions(celldir))
            got = confirmed(celldir, deadline)
            if got is None:
                m["confirmed_pp"] = math.nan
                missing.append(f"ep{ck[2]}/{ck[0]}/{ck[1]}")
            else:
                clean, conf = got
                if clean != int(m["clean_fulfilled"]):
                    mismatch.append(f"ep{ck[2]}/{ck[0]}/{ck[1]}: recomputed {clean}, row {int(m['clean_fulfilled'])}")
                m["confirmed_pp"] = 100.0 * conf / off if off else math.nan
            cells[ck] = m
    total = len(cells) + len(invalid)
    return {"key": key, "dir": str(run), "name": cfg.get("name", ""), "config": cfg, "cells": cells, "invalid": invalid,
            "clean_mismatch": mismatch, "missing_cell_files": missing,
            "interpretable": total > 0 and len(invalid) <= 0.10 * total, "total_cells": total}


def schedule_digest(run: Path, ep: int) -> str:
    p = run / f"schedule_ep{ep}.json"
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else ""


def paired(run: dict, policy: str, a: str, b: str, metric: str, from_run: dict | None = None) -> list:
    """Per-episode b - a. With from_run, arm a comes from that run (same seed): an episode is used only
    when its arrival and fault schedule files are byte-identical in both runs (common random numbers)."""
    c, ca = run["cells"], (from_run or run)["cells"]
    eps = sorted({k[2] for k in c})
    out = []
    for e in eps:
        if (a, policy, e) not in ca or (b, policy, e) not in c:
            continue
        if from_run is not None and (not c[(b, policy, e)]["schedule"] or c[(b, policy, e)]["schedule"] != ca[(a, policy, e)]["schedule"]):
            continue
        out.append(c[(b, policy, e)][metric] - ca[(a, policy, e)][metric])
    return out


# ------------------------------------------------------------------ analysis
def plan_info(plan: Path):
    text = plan.read_text()
    blk = re.search(r"```pairs-spec\n(.*?)```", text, re.S)
    if not blk:
        raise SystemExit(f"{plan}: no ```pairs-spec``` block")
    spec = json.loads(blk.group(1))
    frozen = re.search(r"```frozen-files\n(.*?)```", text, re.S)
    changed, n = [], 0
    for line in (frozen.group(1).splitlines() if frozen else []):
        if line.strip():
            h, rel = line.split(None, 1)
            n += 1
            f = REPO / rel.strip()
            if not f.is_file() or hashlib.sha256(f.read_bytes()).hexdigest() != h:
                changed.append(rel.strip())
    return spec, {"path": str(plan), "sha256": hashlib.sha256(plan.read_bytes()).hexdigest(), "frozen_files": n,
                  "frozen_changed": changed, "frozen_block": frozen is not None}


def analyse(spec: dict, runs: dict, plan: dict | None) -> dict:
    alpha = float(spec.get("alpha", 0.05))
    maxinv = float(spec.get("max_invalid_fraction", 0.10))
    res = {"name": spec.get("name", ""), "plan": plan, "alpha": alpha, "runs": {}, "hypotheses": [], "estimands": [], "gap": [],
           "levels": {}, "defer_reasons": {}, "rates": {}, "deviations": []}
    for key, want in spec.get("runs", {}).items():
        if key not in runs:
            res["deviations"].append(f"run {key} ({want.get('campaign')}) not given")
            continue
        r = runs[key]
        total = r["total_cells"]
        r["interpretable"] = total > 0 and len(r["invalid"]) <= maxinv * total
        if want.get("campaign") and r["name"] != want["campaign"]:
            res["deviations"].append(f"run {key}: config name {r['name']!r}, plan expects {want['campaign']!r}")
        pre = r["config"].get("preregistration") or {}
        if plan and not want.get("reference") and pre.get("sha256") != plan["sha256"]:
            res["deviations"].append(f"run {key}: config froze plan SHA {str(pre.get('sha256'))[:16]}..., plan is {plan['sha256'][:16]}...")
        res["runs"][key] = {"dir": r["dir"], "campaign": r["name"], "cells_valid": len(r["cells"]), "invalid": r["invalid"],
                            "interpretable": r["interpretable"], "clean_mismatch": r["clean_mismatch"],
                            "missing_cell_files": len(r["missing_cell_files"])}
        lv, dr, rt = {}, {}, {}
        groups = defaultdict(list)
        for (ct, pol, _), m in r["cells"].items():
            groups[(ct, pol)].append(m)
        for (ct, pol), ms in sorted(groups.items()):
            g = f"{ct}/{pol}"
            lv[g] = {k: round(statistics.mean(v for v in (x[k] for x in ms) if not math.isnan(v)), 3)
                     if any(not math.isnan(x[k]) for x in ms) else None
                     for k in ("clean_pp", "confirmed_pp", "admitted_pp", "stranded_resolved_unit_sec", "stranded_censored_unit_sec",
                               "mean_time_to_terminal_s", "fences", "stops", "window_defers", "late_effects")}
            lv[g]["n"] = len(ms)
            cnt = Counter()
            for x in ms:
                cnt.update({k: int(v) for k, v in x["defer_reasons"].items()})
            dr[g] = dict(cnt.most_common(8))
            f, fo, s, so = (sum(x[k] for x in ms) for k in ("fences", "fences_ok", "stops", "stops_terminal"))
            fr, fro, sr, srt = (sum(x[k] for x in ms if not math.isnan(x[k])) for k in TRACE_METRICS)
            rt[g] = {"fences": int(f), "fence_success": round(fo / f, 3) if f else None,
                     "stops": int(s), "stop_success": round(so / s, 3) if s else None,
                     "fences_reached": int(fr), "fence_success_reached": round(fro / fr, 3) if fr else None,
                     "stops_reached": int(sr), "stop_success_reached": round(srt / sr, 3) if sr else None}
        res["levels"][key], res["defer_reasons"][key], res["rates"][key] = lv, dr, rt

    # hypotheses
    fam_p = defaultdict(dict)
    for h in spec.get("hypotheses", []):
        out = {"id": h["id"], "family": h.get("family", "primary"), "test": h["test"], "run": h["run"], "spec": h}
        r = runs.get(h["run"])
        if r is None:
            out["verdict"] = "NOT RUN"
        elif not r["interpretable"]:
            out["verdict"] = "NOT INTERPRETABLE"
        elif h["test"] in ("at_least", "at_most", "equivalent"):
            d = [v for v in paired(r, h["policy"], h["from"], h["to"], h["metric"]) if not math.isnan(v)]
            out.update(summary(d))
            m = float(h.get("margin", 0.0))
            if len(d) < 2:
                out["verdict"] = "NOT COMPUTABLE"
            else:
                if h["test"] == "at_least":
                    p = one_sided(d, m, upper=False)
                elif h["test"] == "at_most":
                    p = one_sided(d, m, upper=True)
                else:
                    p = max(one_sided(d, -m, upper=False), one_sided(d, m, upper=True))
                    ci = t_interval(d, 1 - 2 * alpha)
                    out["t_tost"] = [round(v, 3) for v in ci]
                out["p"] = p
                fam_p[out["family"]][h["id"]] = p
        elif h["test"] == "rate_greater":
            c = r["cells"]
            eps = sorted({k[2] for k in c})
            A, B = h["a"], h["b"]
            pairs = [(c[(A["contract"], A["policy"], e)][A["num"]], c[(A["contract"], A["policy"], e)][A["den"]],
                      c[(B["contract"], B["policy"], e)][B["num"]], c[(B["contract"], B["policy"], e)][B["den"]]) for e in eps
                     if (A["contract"], A["policy"], e) in c and (B["contract"], B["policy"], e) in c]
            if len(pairs) < 2:
                out["verdict"] = "NOT COMPUTABLE"
            else:
                diff, ci, p = boot_rate_diff(pairs)
                out.update({"n": len(pairs), "diff": round(diff, 4), "boot95": [round(v, 4) for v in ci], "p": p,
                            "rate_a": round(sum(x[0] for x in pairs) / sum(x[1] for x in pairs), 4) if sum(x[1] for x in pairs) else None,
                            "rate_b": round(sum(x[2] for x in pairs) / sum(x[3] for x in pairs), 4) if sum(x[3] for x in pairs) else None})
                if math.isnan(p):
                    out["verdict"] = "NOT COMPUTABLE"
                else:
                    fam_p[out["family"]][h["id"]] = p
        elif h["test"] in ("detects", "absent"):
            ms = [m for (ct, pol, _), m in r["cells"].items() if ct == h["contract"] and pol == h.get("policy", pol)]
            counts = {k: int(sum(m[k] for m in ms if not math.isnan(m[k]))) for k in h["any_of"]}
            cells_hit = sum(1 for m in ms if any(m[k] > 0 for k in h["any_of"]))
            out.update({"cells": len(ms), "cells_detecting": cells_hit, "counts": counts})
            if not ms:
                out["verdict"] = "NOT COMPUTABLE"
            elif h["test"] == "detects":
                out["verdict"] = "DETECTED" if cells_hit > 0 else "NOT DETECTED"
            else:
                out["verdict"] = "ABSENT" if cells_hit == 0 else "PRESENT"
        else:
            raise SystemExit(f"{h['id']}: unknown test {h['test']!r}")
        res["hypotheses"].append(out)
    adj = {}
    for fam, ps in fam_p.items():
        adj.update(holm(ps))
    for out in res["hypotheses"]:
        if out["id"] in adj:
            out["p_holm"] = round(adj[out["id"]], 6)
            out["p"] = round(out["p"], 6)
            out["verdict"] = "MET" if adj[out["id"]] < alpha else "NOT MET"

    # safety
    s = spec.get("safety")
    if s:
        viol, late, exempt = [], 0, set(s.get("exempt_contracts", []))
        ok = True
        for key in s.get("runs", []):
            r = runs.get(key)
            if r is None:
                ok = False
                continue
            for (ct, pol, ep), m in sorted(r["cells"].items()):
                if ct in exempt:
                    continue
                late += int(m["late_effects"]) if not math.isnan(m["late_effects"]) else 0
                bad = {k: int(m[k]) for k in SAFETY if not math.isnan(m[k]) and m[k] > 0}
                if bad:
                    viol.append({"run": key, "cell": f"ep{ep}/{ct}/{pol}", **bad})
        res["safety"] = {"id": s.get("id", "H-S"), "runs": list(s.get("runs", [])), "violations": viol, "late_effects": late,
                         "verdict": "MET" if ok and not viol else ("NOT RUN" if not ok else "NOT MET")}

    for e in spec.get("estimands", []):
        r = runs.get(e["run"])
        fr = runs.get(e["from_run"]) if e.get("from_run") else None
        if r is None or (e.get("from_run") and fr is None):
            continue
        res["estimands"].append({"run": e["run"], "from_run": e.get("from_run", e["run"]), "policy": e["policy"], "from": e["from"],
                                 "to": e["to"], "metrics": {m: summary(paired(r, e["policy"], e["from"], e["to"], m, fr)) for m in e["metrics"]}})
    for g in spec.get("gap", []):
        r = runs.get(g["run"])
        rh = runs.get(g.get("high_run", g["run"]))
        if r is None or rh is None:
            continue
        for metric in g.get("metrics", ["clean_pp", "confirmed_pp"]):
            c, ch = r["cells"], rh["cells"]
            if rh is r:
                eps = [e for e in sorted({k[2] for k in c}) if all((arm, g["policy"], e) in c for arm in (g["low"], g["mid"], g["high"]))]
                lows = [c[(g["low"], g["policy"], e)][metric] for e in eps]
                mids = [c[(g["mid"], g["policy"], e)][metric] for e in eps]
                highs = [c[(g["high"], g["policy"], e)][metric] for e in eps]
            else:  # high arm from another run (other seed): unpaired, resampled independently
                eps = [e for e in sorted({k[2] for k in c}) if all((arm, g["policy"], e) in c for arm in (g["low"], g["mid"]))]
                lows = [c[(g["low"], g["policy"], e)][metric] for e in eps]
                mids = [c[(g["mid"], g["policy"], e)][metric] for e in eps]
                highs = [m[metric] for (ct, pol, _), m in sorted(ch.items()) if ct == g["high"] and pol == g["policy"]]
            if any(math.isnan(v) for v in lows + mids + highs) or len(eps) < 2 or len(highs) < 2:
                continue

            def share(il, ih):
                lo = sum(lows[i] for i in il) / len(il)
                mid = sum(mids[i] for i in il) / len(il)
                hi = sum(highs[i] for i in ih) / len(ih)
                return (mid - lo) / (hi - lo) if hi != lo else math.nan
            rng = random.Random(73)
            n, nh = len(lows), len(highs)
            reps = []
            for _ in range(4000):
                il = [rng.randrange(n) for _ in range(n)]
                ih = il if rh is r else [rng.randrange(nh) for _ in range(nh)]
                v = share(il, ih)
                if not math.isnan(v):
                    reps.append(v)
            reps.sort()
            res["gap"].append({"run": g["run"], "high_run": g.get("high_run", g["run"]), "paired": rh is r, "policy": g["policy"],
                               "metric": metric, "arms": [g["low"], g["mid"], g["high"]], "n": n, "n_high": nh,
                               "share": round(share(range(n), range(nh)), 4),
                               "boot95": [round(reps[int(0.025 * len(reps))], 4), round(reps[int(0.975 * len(reps)) - 1], 4)] if reps else None})
    return res


# ------------------------------------------------------------------ output
def _ci(v):
    return f"({v[0]:+.2f}, {v[1]:+.2f})" if v and not any(isinstance(x, float) and math.isnan(x) for x in v) else "-"


def _r(v):
    return "-" if v is None else f"{v:g}"


def markdown(res: dict) -> str:
    p = res["plan"]
    L = [f"# Paired analysis: {res['name']}", ""]
    if p:
        L.append(f"Plan `{p['path']}` (SHA-256 `{p['sha256'][:16]}...`); {p['frozen_files']} frozen files"
                 + (f", **changed since freezing: {p['frozen_changed']}**" if p["frozen_changed"] else ", all unchanged") + ".")
    else:
        L.append("Exploratory: no frozen plan (spec read from a file). Nothing below is confirmatory.")
    L += ["", "## Runs", "", "| key | campaign | VALID cells | INVALID | interpretable | clean recount mismatches | cells without client/plant files |",
          "|---|---|---|---|---|---|---|"]
    for k, r in res["runs"].items():
        L.append(f"| {k} | `{r['campaign']}` | {r['cells_valid']} | {len(r['invalid'])} | {'yes' if r['interpretable'] else '**NO**'} | "
                 f"{len(r['clean_mismatch'])} | {r['missing_cell_files']} |")
    if res["deviations"]:
        L += ["", "Deviations detected:", ""] + [f"- {d}" for d in res["deviations"]]
    L += ["", "## Hypotheses", "", "| ID | test | comparison | result | evidence |", "|---|---|---|---|---|"]
    for h in res["hypotheses"]:
        s = h["spec"]
        if h["test"] in ("at_least", "at_most", "equivalent"):
            cmp_ = f"{h['run']}: {s['policy']}, {s['from']} -> {s['to']}, {s['metric']}"
            op = {"at_least": ">=", "at_most": "<=", "equivalent": "within ±"}[h["test"]]
            ev = (f"mean {h['mean']:+.2f}, 95 % t {_ci(h.get('t95'))}" + (f", {100 - 200 * res['alpha']:.0f} % t {_ci(h.get('t_tost'))}" if h.get("t_tost") else "")
                  + f"; {op} {s.get('margin', 0):g}; p {h.get('p', '-')}, Holm {h.get('p_holm', '-')}; n={h['n']}") if h.get("n") else "-"
        elif h["test"] == "rate_greater":
            cmp_ = f"{h['run']}: {s['a']['contract']} {s['a']['num']}/{s['a']['den']} > {s['b']['contract']} {s['b']['num']}/{s['b']['den']}"
            ev = (f"{h.get('rate_a')} vs {h.get('rate_b')}; difference {h.get('diff')}, 95 % bootstrap {h.get('boot95')}; "
                  f"p {h.get('p', '-')}, Holm {h.get('p_holm', '-')}; n={h.get('n')}") if h.get("n") else "-"
        else:
            cmp_ = f"{h['run']}: {s['contract']}{', ' + s['policy'] if s.get('policy') else ''}, any of {', '.join(s['any_of'])}"
            ev = f"{h.get('cells_detecting', 0)} of {h.get('cells', 0)} cells; counts {h.get('counts')}"
        L.append(f"| {h['id']} | {h['test']} | {cmp_} | **{h['verdict']}** | {ev} |")
    if "safety" in res:
        s = res["safety"]
        L.append(f"| {s['id']} | safety | {', '.join(s['runs'])} | **{s['verdict']}** | {len(s['violations'])} cells with violations; "
                 f"late effects {s['late_effects']} |")
        for v in s["violations"][:20]:
            L.append(f"|  |  | {v['run']} {v['cell']} |  | {', '.join(f'{k}={x}' for k, x in v.items() if k not in ('run', 'cell'))} |")
    if res["estimands"]:
        L += ["", "## Estimands (no claim; per-episode differences)", "", "| run | policy | from -> to | metric | mean | 95 % t | 95 % bootstrap | n |",
              "|---|---|---|---|---|---|---|---|"]
        for e in res["estimands"]:
            for m, s in e["metrics"].items():
                if s.get("n"):
                    runlab = e["run"] if e["from_run"] == e["run"] else f"{e['from_run']} -> {e['run']}"
                    L.append(f"| {runlab} | {e['policy']} | {e['from']} -> {e['to']} | {METRIC_LABELS.get(m, m)} | {s['mean']:+.3f} | "
                             f"{_ci(s['t95'])} | {_ci(s['boot95'])} | {s['n']} |")
    if res["gap"]:
        L += ["", "## Share of the gap recovered", "", "| run | policy | metric | low -> mid -> high | share | 95 % bootstrap | n |", "|---|---|---|---|---|---|---|"]
        for g in res["gap"]:
            L.append(f"| {g['run']}{'' if g['paired'] else ' (high: ' + g['high_run'] + ', unpaired)'} | {g['policy']} | {g['metric']} | {' -> '.join(g['arms'])} | {g['share']} | {g['boot95']} | {g['n']} |")
    L += ["", "## Levels (means per episode)", ""]
    cols = ("clean_pp", "confirmed_pp", "admitted_pp", "stranded_resolved_unit_sec", "stranded_censored_unit_sec", "mean_time_to_terminal_s",
            "fences", "stops", "window_defers", "late_effects")
    L += ["| run | contract/policy | n | " + " | ".join(cols) + " | fence success (all; reached owner) | stop success (all; reached owner) |",
          "|---|---|---|" + "---|" * (len(cols) + 2)]
    for key, lv in res["levels"].items():
        for g, v in lv.items():
            rt = res["rates"][key][g]
            L.append(f"| {key} | {g} | {v['n']} | " + " | ".join("-" if v[c] is None else f"{v[c]:g}" for c in cols)
                     + f" | {_r(rt['fence_success'])} ({rt['fences']}); {_r(rt['fence_success_reached'])} ({rt['fences_reached']}) | "
                     f"{_r(rt['stop_success'])} ({rt['stops']}); {_r(rt['stop_success_reached'])} ({rt['stops_reached']}) |")
    L += ["", "## DEFER reasons (top 8 per cell, summed)", ""]
    for key, dr in res["defer_reasons"].items():
        for g, cnt in dr.items():
            L.append(f"- {key} {g}: " + (", ".join(f"{k} {v}" for k, v in cnt.items()) or "none"))
    return "\n".join(L) + "\n"


def write_cells(runs: dict, path: Path):
    cols = ["run", "contract", "policy", "episode", "clean_pp", "confirmed_pp", "admitted_pp", "window_defers"] + list(ROW_METRICS) + list(TRACE_METRICS)
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for key, r in runs.items():
            for (ct, pol, ep), m in sorted(r["cells"].items()):
                w.writerow([key, ct, pol, ep] + [("" if isinstance(m[c], float) and math.isnan(m[c]) else round(m[c], 4)) for c in cols[4:]])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--plan", help="frozen plan with a ```pairs-spec``` block")
    src.add_argument("--spec", help="spec JSON file (exploratory)")
    ap.add_argument("--run", action="append", default=[], metavar="KEY=DIR", help="run directory for a spec run key")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if a.plan:
        plan_path = Path(a.plan)
        if not plan_path.is_file() and (REPO / a.plan).is_file():
            plan_path = REPO / a.plan
        spec, plan = plan_info(plan_path)
    else:
        spec, plan = json.loads(Path(a.spec).read_text()), None
    runs = {}
    for kv in a.run:
        key, _, d = kv.partition("=")
        if not d:
            raise SystemExit(f"--run {kv}: want KEY=DIR")
        runs[key] = load_run(key, Path(d))
    res = analyse(spec, runs, plan)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(res, indent=2, default=str) + "\n")
    (out / "RESULTS.md").write_text(markdown(res))
    write_cells(runs, out / "cells.csv")
    verdicts = ", ".join(f"{h['id']} {h['verdict']}" for h in res["hypotheses"])
    if "safety" in res:
        verdicts += f", {res['safety']['id']} {res['safety']['verdict']}"
    print(f"{res['name']}: {verdicts or 'no hypotheses'} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
