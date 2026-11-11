#!/usr/bin/env python3
"""The estimands E-SHR of PREREG_extra_087.md item A (reviewer comment M2; no claims): the documented shadow reconciliation
loop of release 086 (SHADOW_RECONCILE at the documented 10 s timeout, SHADOW_RECONCILE_1S at EviCon's 1 s sweep cadence)
against EVICON on the cutoff firmware (run SHRz6) and on MinIO's conditional PUT (run SHRm6), within run, paired by
episode; and each arm against the replication's EVICON cells (SRz6 ZEPHYR_F_DIRECT, SRm6 MINIO_COND) on byte-identical
schedules (across runs, informational).

Per cell (VALID cells only; the cell's runtime row, its authority traces and its clients' log):
  clean_pp, admitted_pp            fulfilment in pp of offered
  owner_calls_per_offered          logical owner calls per offered request: dispatch attempts + queries + stops + fences +
                                   the loop's reads of the reported state (SHADOW_RECONCILE_RESULT, not in the row's queries)
  reads_per_offered, republishes_per_offered
                                   the loop's reads and re-publishes (action=REPUBLISH) per offered request
  reads_by_outcome                 APPLIED, STOPPED, NOT_FOUND, UNREACHABLE, UNCERTAIN (the stranded-budget question: an
                                   UNCERTAIN read is read again forever)
  unresolved_at_end, budget_held_end
                                   admitted identities not terminal at the end and the sum of their values (units): from the
                                   authorities' journals (the clients stop watching at their deadline); from the clients'
                                   last seen status only when a lean archive keeps no journal
  stranded_resolved_unit_sec, stranded_censored_unit_sec, mean_time_to_terminal_s, late_effects,
  late_effects_after_client_gave_up, window_defers (the row's DEFER reasons of the window, as pairs_report.py counts them)
  broker_packets_per_offered, flash_writes_per_offered
                                   the physical units of E-COST on the firmware (the 086 counters of the 087 runner: every
                                   MQTT packet through the broker, the firmware's flash value writes); none on MinIO
Contrasts: EVICON -> SHADOW_RECONCILE, EVICON -> SHADOW_RECONCILE_1S, SHADOW_RECONCILE -> SHADOW_RECONCILE_1S (the cadence
contrast); mean of the per-episode differences with a Student t 95 % interval (pairs_report.summary).

    python3 evaluation/sh087.py --run SHRz6=<run> --run SHRm6=<run> [--ref SRz6=<run> --ref SRm6=<run>] --out <dir>
Writes SH087.md and sh087.json.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pairs_report as PR  # noqa: E402  (frozen since release 072; imported, never edited)

POLICIES = ("EVICON", "SHADOW_RECONCILE", "SHADOW_RECONCILE_1S")
CONTRASTS = (("EVICON", "SHADOW_RECONCILE"), ("EVICON", "SHADOW_RECONCILE_1S"), ("SHADOW_RECONCILE", "SHADOW_RECONCILE_1S"))
RUN_CONTRACT = {"SHRz6": "ZEPHYR_CUTOFF_DIRECT", "SHRm6": "MINIO_COND"}
REF = {"SHRz6": ("SRz6", "ZEPHYR_F_DIRECT"), "SHRm6": ("SRm6", "MINIO_COND")}
METRICS = ("clean_pp", "admitted_pp", "owner_calls_per_offered", "reads_per_offered", "republishes_per_offered", "unresolved_at_end",
           "budget_held_end", "stranded_censored_unit_sec", "mean_time_to_terminal_s", "late_effects", "window_defers",
           "broker_packets_per_offered", "flash_writes_per_offered")
WINDOW_REASONS = PR.WINDOW_REASONS


ROLE_DIR = {"authority": "A", "gateway": "C", "plant": "E"}


def one_copy(celldir: Path, pattern: str) -> list:
    """The cell's files raw/**/<pattern>, one per file name. On the testbed each file is fetched from the one VM that writes
    it; on a simulated testbed (one machine as every VM) the same file is fetched once per VM directory, and only the copy
    in the writing VM's directory (A: authority, C: gateway, E: plant) is read, otherwise the first in path order."""
    found = defaultdict(list)
    for p in sorted(celldir.glob(f"raw/**/{pattern}")):
        found[p.name].append(p)
    out = []
    for name, ps in sorted(found.items()):
        role = next((r for r in ROLE_DIR if name.startswith(r)), None)
        pref = [p for p in ps if role is not None and p.parent.name == ROLE_DIR[role]]
        out.append((pref or ps)[0])
    return out


def traces(celldir: Path) -> list:
    return one_copy(celldir, "authority*.jsonl")


def trace_counts(celldir: Path) -> dict:
    reads, rep = Counter(), 0
    for tr in traces(celldir):
        with open(tr, errors="replace") as fh:
            for line in fh:
                if "SHADOW_RECONCILE_RESULT" not in line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("event") == "SHADOW_RECONCILE_RESULT":
                    reads[str(e.get("outcome", ""))] += 1
                    if "action=REPUBLISH" in str(e.get("reason", "")):
                        rep += 1
    return {"reads": dict(reads), "republishes": rep}


def counters(celldir: Path) -> tuple:
    """(broker packets, flash value writes) of a firmware cell from the 086 counters (NaN when absent, e.g. MinIO)."""
    pk = fw = math.nan
    b = celldir / "raw" / "C" / "broker_sys.json"
    if not b.exists():
        b = celldir / "raw" / "broker_sys.json"
    try:
        c = json.loads(b.read_text()).get("cell") or {}
        pk = float(c["$SYS/broker/messages/received"] + c["$SYS/broker/messages/sent"])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    f = celldir / "raw" / "E" / "flash_writes.json"
    if not f.exists():
        f = celldir / "raw" / "flash_writes.json"
    try:
        files = json.loads(f.read_text()).get("files") or []
        fw = float(sum(sum((x.get("value_writes") or {}).values()) for x in files))
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return pk, fw


def journal_held(celldir: Path, effects=None) -> dict:
    """Identities admitted and not terminal in the authorities' journals at the end of the cell (raw/**/authority<d>.db, opened
    without writing anything next to them, as anchor086.py opens them) and the budget they hold (the sum of the first value
    component); with effects (command id -> plant effects), also those of them that took effect. None when no journal is
    there (a lean archive): the caller then falls back to the clients' last seen status."""
    import shutil
    import sqlite3
    import anchor086
    dbs = one_copy(celldir, "authority*.db")
    if not dbs:
        return None
    out = {"unresolved": 0, "budget": 0, "unresolved_with_effect": 0, "budget_with_effect": 0}
    for db in dbs:
        tmp = []
        try:
            con = anchor086.open_readonly(db, tmp)
            for cid, st, vj in con.execute("SELECT command_id, status, value_json FROM commands"):
                if st in ("APPLIED", "STOPPED"):
                    continue
                try:
                    v = int((json.loads(vj) or [0])[0])
                except (ValueError, TypeError, IndexError):
                    v = 0
                out["unresolved"] += 1
                out["budget"] += v
                if effects is not None and effects.get(cid, 0) > 0:
                    out["unresolved_with_effect"] += 1
                    out["budget_with_effect"] += v
            con.close()
        except sqlite3.Error:
            return None
        finally:
            for d in tmp:
                shutil.rmtree(d, ignore_errors=True)
    return out


def budget_held(celldir: Path) -> tuple:
    n = held = 0
    p = celldir / "clients.jsonl"
    if not p.exists():
        return math.nan, math.nan
    for line in p.read_text().splitlines():
        if not line.strip():
            continue
        lg = json.loads(line)
        for cid in lg.get("admitted", {}):
            if lg.get("final_status", {}).get(cid) not in ("APPLIED", "STOPPED"):
                n += 1
                held += int(lg.get("value", 0))
    return n, held


def load(run: Path) -> dict:
    """{(contract, policy): {episode: metrics}} of the VALID cells."""
    out = defaultdict(dict)
    with open(run / "runtime_episodes.csv", newline="") as fh:
        for r in csv.DictReader(fh):
            if not r["cell_status"].startswith("VALID"):
                continue
            ep, ct, pol = int(r["episode"]), r["contract"], r["policy"]
            cd = run / "cells" / f"ep{ep}" / ct / pol
            off = max(1, int(r["offered"]))
            tc = trace_counts(cd)
            reads = sum(tc["reads"].values())
            calls = sum(int(float(r.get(k) or 0)) for k in ("dispatch_attempts", "queries", "stops", "fences")) + reads
            jh = journal_held(cd)
            held = jh["budget"] if jh is not None else budget_held(cd)[1]
            pk, fw = counters(cd)
            try:
                reasons = json.loads(r.get("defer_reasons") or "{}")
            except ValueError:
                reasons = {}
            m = {"clean_pp": 100.0 * int(r["clean_fulfilled"]) / off, "admitted_pp": 100.0 * int(r["admitted"]) / off,
                 "owner_calls_per_offered": calls / off, "reads_per_offered": reads / off, "republishes_per_offered": tc["republishes"] / off,
                 "unresolved_at_end": float(r["unresolved_at_end"] or 0), "budget_held_end": float(held),
                 "stranded_censored_unit_sec": float(r["stranded_censored_unit_sec"] or 0),
                 "mean_time_to_terminal_s": PR._float(r["mean_time_to_terminal_s"]), "late_effects": float(r["late_effects"] or 0),
                 "window_defers": float(sum(int(v) for k, v in reasons.items() if k.split(";")[0] in WINDOW_REASONS)),
                 "broker_packets_per_offered": pk / off, "flash_writes_per_offered": fw / off,
                 "reads_by_outcome": tc["reads"],
                 "late_effects_after_client_gave_up": float(r.get("late_effects_after_client_gave_up") or 0)}
            out[(ct, pol)][ep] = m
    return out


def contrast(a: dict, b: dict, metric: str) -> dict:
    d = [b[e][metric] - a[e][metric] for e in sorted(set(a) & set(b))
         if not math.isnan(a[e][metric]) and not math.isnan(b[e][metric])]
    return PR.summary(d) if d else {"n": 0}


def level(x: dict, metric: str) -> dict:
    return PR.summary([v[metric] for v in x.values() if not math.isnan(v[metric])]) if x else {"n": 0}


def same_schedules(r1: Path, r2: Path) -> set:
    return {e for e in range(64) if (r1 / f"schedule_ep{e}.json").exists() and (r2 / f"schedule_ep{e}.json").exists()
            and PR.schedule_digest(r1, e) == PR.schedule_digest(r2, e)}


def analyse(runs: dict, refs: dict) -> dict:
    res = {"runs": {k: str(v) for k, v in runs.items()}, "refs": {k: str(v) for k, v in refs.items()}, "owners": {}}
    for key, run in runs.items():
        data = load(run)
        cts = sorted({c for c, _ in data})
        ct = RUN_CONTRACT.get(key) if RUN_CONTRACT.get(key) in cts or len(cts) != 1 else cts[0]
        arms = {p: data.get((ct, p), {}) for p in POLICIES}
        o = {"contract": ct, "levels": {}, "contrasts": {}, "reads_by_outcome": {}, "cross_run": {}}
        for p, x in arms.items():
            o["levels"][p] = {m: level(x, m) for m in METRICS}
            tot = Counter()
            for v in x.values():
                tot.update(v["reads_by_outcome"])
            o["reads_by_outcome"][p] = dict(tot)
        for a, b in CONTRASTS:
            o["contrasts"][f"{a} -> {b}"] = {m: contrast(arms[a], arms[b], m) for m in METRICS}
        rk, rct = REF.get(key, ("", ""))
        if rk in refs:
            ref = load(refs[rk]).get((rct, "EVICON"), {})
            eps = same_schedules(run, refs[rk])
            for p, x in arms.items():
                o["cross_run"][f"{rk} {rct}/EVICON -> {key} {p}"] = {
                    "episodes_same_schedule": sorted(eps & set(ref) & set(x)),
                    "clean_pp": contrast({e: ref[e] for e in eps if e in ref}, {e: x[e] for e in eps if e in x}, "clean_pp")}
        res["owners"][key] = o
    return res


def _c(s: dict) -> str:
    if not s or s.get("n", 0) == 0:
        return "-"
    t = s.get("t95") or [math.nan, math.nan]
    return f"{s['mean']:+.3f} [{t[0]:+.3f}, {t[1]:+.3f}] (n={s['n']})"


def _l(s: dict) -> str:
    return "-" if not s or s.get("n", 0) == 0 else f"{s['mean']:.3f} (n={s['n']})"


def markdown(r: dict) -> str:
    L = ["# E-SHR: the documented shadow loop on the real owners (PREREG_extra_087.md item A)", "",
         "Estimands only (the rules SHR-1 to SHR-4, SHR-A1 to SHR-A6 and SHR-S are decided in `pairs/PREREG_extra_087/`). Runs: "
         + ", ".join(f"{k} `{v}`" for k, v in r["runs"].items()) + (("; references " + ", ".join(f"{k} `{v}`" for k, v in r["refs"].items()))
                                                                     if r["refs"] else "; no SR reference run among the sources") + ".", ""]
    for key, o in r["owners"].items():
        L += [f"## {key} ({o['contract']})", "", "| metric | " + " | ".join(POLICIES) + " |", "|---|" + "---|" * len(POLICIES)]
        for m in METRICS:
            L.append(f"| {m} | " + " | ".join(_l(o["levels"][p].get(m)) for p in POLICIES) + " |")
        L += ["", "Reads of the reported state by outcome (all cells): " + "; ".join(f"{p}: {json.dumps(o['reads_by_outcome'][p], sort_keys=True)}"
                                                                         for p in POLICIES[1:]), "",
              "| metric | " + " | ".join(o["contrasts"]) + " |", "|---|" + "---|" * len(o["contrasts"])]
        for m in METRICS:
            L.append(f"| {m} | " + " | ".join(_c(c.get(m)) for c in o["contrasts"].values()) + " |")
        if o["cross_run"]:
            L += ["", "Across runs (clean pp, episodes with byte-identical schedules; informational):", ""]
            L += [f"- {k}: {_c(v['clean_pp'])} on episodes {v['episodes_same_schedule']}" for k, v in o["cross_run"].items()]
        L.append("")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", action="append", default=[], help="KEY=DIR (SHRz6, SHRm6)")
    ap.add_argument("--ref", action="append", default=[], help="KEY=DIR (SRz6, SRm6)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    runs = {k: Path(v) for k, v in (x.split("=", 1) for x in a.run)}
    refs = {k: Path(v) for k, v in (x.split("=", 1) for x in a.ref)}
    if not runs:
        raise SystemExit("--run SHRz6=<dir> and/or --run SHRm6=<dir> required")
    r = analyse(runs, refs)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "sh087.json").write_text(json.dumps(r, indent=1, default=str) + "\n")
    (out / "SH087.md").write_text(markdown(r))
    print(f"sh087: {', '.join(runs)} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
