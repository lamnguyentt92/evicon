#!/usr/bin/env python3
"""Did the owner's reported state ever differ from the evidence? (release 086, PREREG_extra_086.md section 2; verifier
finding B2-02). A report analysis over every campaign run `evicon_review.py report` reads; no new cells, no hypothesis.

Releases 061-085 committed the owner's reported state as the authority's anchor after every complete fence (rule R5,
and R5' when no listed identity is UNCERTAIN). Release 086 computes the anchor from evidence instead and aborts a fence
whose reported state differs (`internal/runtime/fence_anchor.go`). This analysis recomputes, for every completed fence
of every run, the anchor the evidence gives and compares it with the anchor the run committed:

  * the authority journals of a cell (`raw/A/authority<d>.db`, one per device; opened immutable, so that nothing is
    written next to an archived journal, or as a temporary copy when its -wal file holds data): the fence lifecycles
    (`authority_barrier_history`, in the order they began) and the admitted commands with their final status. Terminal
    evidence is monotone and nothing at or below a completed fence's cutoff changes after it, so the final status of an
    identity at or below the cutoff is its status when the fence completed;
  * per domain, in order: the evidence anchor of a COMPLETE lifecycle = the anchor committed by the previous COMPLETE
    lifecycle (the domain's initial anchor, `-anchor 0` in every runner, before the first) plus the vectors of every
    admitted identity above the previous cutoff and at or below this cutoff whose status is APPLIED (every testbed domain
    is ADD-only; a batch with a SET is reported as not computable). A terminal-history compaction between two fences
    folds evidence into the anchor by the same rule, so it does not change the comparison. PARTIAL (rule R5',
    unanchored) and ABORTED lifecycles commit no anchor and are counted apart;
  * for runs of release 086 the authority traces carry FENCE_ANCHOR_CHECK (the computed and the reported vectors and the
    result); their results are counted as well (a release-086 authority never commits a mismatching anchor; a mismatch
    there is an aborted fence).

Output, per campaign and policy: cells, cells with journals (else "not available": a lean archive keeps no raw
journals), completed fences, anchors compared, mismatches; per-identity intakes and aborted fences; the FENCE_ANCHOR_CHECK
results; and every mismatch with its cell, device, token, cutoff and both vectors. Expected (the "owner up" regime of
every campaign): 0 mismatches, i.e. the results of the runs of releases 066-085 do not depend on the anchor rule.

    python3 evaluation/anchor086.py --out <dir> --campaign <run dir> [--campaign ...]
Writes ANCHOR086.md and anchor086.json.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from urllib.request import pathname2url


def _json(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def campaign_name(run: Path) -> str:
    cfg = _json(run / "config.json")
    return cfg.get("name") or run.parent.name


def run_release(run: Path) -> str:
    m = _json(run / "manifest.json")
    return str(m.get("release") or m.get("source", {}).get("release") or "")


def cells(run: Path):
    """(episode, contract, policy, cell dir) of every cell of a campaign run."""
    for ep in sorted((run / "cells").glob("ep*")):
        for c in sorted(p for p in ep.iterdir() if p.is_dir()):
            for pol in sorted(p for p in c.iterdir() if p.is_dir()):
                yield ep.name, c.name, pol.name, pol


def _vec(s: str):
    try:
        v = json.loads(s)
        return [int(x) for x in v] if isinstance(v, list) else None
    except (TypeError, ValueError):
        return None


def open_readonly(db: Path, tmp: list):
    """Opens an archived journal without writing anything next to it. A journal in WAL mode opened with mode=ro still
    gets -wal and -shm side files from SQLite; immutable=1 creates none (the journal is closed for good). A journal whose
    -wal file holds data (an authority that did not close it) is copied, with its -wal, to a temporary directory first,
    so that the committed transactions in the WAL are read too."""
    wal = Path(str(db) + "-wal")
    if wal.exists() and wal.stat().st_size > 0:
        d = Path(tempfile.mkdtemp(prefix="anchor086-"))
        tmp.append(d)
        shutil.copy2(db, d / db.name)
        shutil.copy2(wal, d / wal.name)
        return sqlite3.connect(f"file:{pathname2url(str(d / db.name))}?mode=ro", uri=True, timeout=5)
    return sqlite3.connect(f"file:{pathname2url(str(db))}?mode=ro&immutable=1", uri=True, timeout=5)


def journal_fences(db: Path) -> dict:
    """The fences of one authority journal with the evidence anchor of every COMPLETE one."""
    out = {"complete": 0, "compared": 0, "mismatch": [], "partial": 0, "aborted": 0, "not_computable": 0, "error": ""}
    tmp = []
    try:
        con = open_readonly(db, tmp)
    except (sqlite3.Error, OSError) as e:
        out["error"] = str(e)
        for d in tmp:
            shutil.rmtree(d, ignore_errors=True)
        return out
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "authority_barrier_history" not in tables or "commands" not in tables:
            out["error"] = "no fence history in this journal"
            return out
        cols = {r[1] for r in con.execute("PRAGMA table_info(authority_barrier_history)")}
        if "anchor_json" not in cols:
            out["error"] = "the fence history has no anchors"
            return out
        cmds = defaultdict(list)
        for dom, cid, seq, op, vj, st in con.execute(
                "SELECT domain_id,command_id,sequence,operation,value_json,status FROM commands WHERE decision='ADMITTED' ORDER BY sequence"):
            cmds[dom].append((int(seq), op, _vec(vj), st, cid))
        hist = defaultdict(list)
        for dom, tok, cut, st, aj in con.execute(
                "SELECT domain_id,token,cutoff_sequence,status,anchor_json FROM authority_barrier_history ORDER BY created_at,rowid"):
            hist[dom].append((tok, int(cut), st, _vec(aj)))
        dims = {r[0]: _vec(r[1]) for r in con.execute("SELECT domain_id,anchor_json FROM domains")}
    except sqlite3.Error as e:
        out["error"] = str(e)
        return out
    finally:
        con.close()
        for d in tmp:
            shutil.rmtree(d, ignore_errors=True)
    for dom, rows in hist.items():
        dim = len(dims.get(dom) or [0])
        prev, prev_cut = [0] * dim, 0
        for tok, cut, st, anchor in rows:
            if st == "PARTIAL":
                out["partial"] += 1
                continue
            if st == "ABORTED":
                out["aborted"] += 1
                continue
            if st != "COMPLETE":
                continue
            out["complete"] += 1
            batch = [c for c in cmds.get(dom, []) if prev_cut < c[0] <= cut]
            if anchor is None or any(c[1] != "ADD" or c[2] is None or len(c[2]) != dim for c in batch if c[3] == "APPLIED"):
                out["not_computable"] += 1
                if anchor is not None:
                    prev, prev_cut = anchor, cut
                continue
            ev = list(prev)
            for c in batch:
                if c[3] == "APPLIED":
                    ev = [a + b for a, b in zip(ev, c[2])]
            out["compared"] += 1
            if ev != anchor:
                out["mismatch"].append({"domain": dom, "token": tok, "cutoff": cut, "previous_cutoff": prev_cut, "committed": anchor,
                                        "evidence": ev, "batch": len(batch), "applied": sum(1 for c in batch if c[3] == "APPLIED")})
            prev, prev_cut = anchor, cut
    return out


def trace_checks(cell: Path) -> Counter:
    """FENCE_ANCHOR_CHECK results of the cell's authority traces (release 086 and later)."""
    c = Counter()
    for tr in sorted((cell / "raw" / "A").glob("authority*.jsonl")):
        try:
            with open(tr, errors="replace") as fh:
                for line in fh:
                    if '"FENCE_ANCHOR_CHECK"' not in line:
                        continue
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    c[e.get("outcome", "?")] += 1
        except OSError:
            continue
    return c


def analyse(runs: list) -> dict:
    rows, mism = {}, []
    for run in runs:
        name, rel = campaign_name(run), run_release(run)
        for ep, contract, policy, cell in cells(run):
            key = (name, run.name, policy)
            r = rows.setdefault(key, {"campaign": name, "run": run.name, "release": rel, "policy": policy, "cells": 0, "cells_with_journals": 0,
                                      "journals": 0, "fences_completed": 0, "anchors_compared": 0, "mismatches": 0, "per_identity": 0,
                                      "aborted": 0, "not_computable": 0, "journal_errors": 0, "trace_checks": Counter()})
            r["cells"] += 1
            dbs = sorted((cell / "raw" / "A").glob("authority*.db"))
            if dbs:
                r["cells_with_journals"] += 1
            for db in dbs:
                j = journal_fences(db)
                r["journals"] += 1
                if j["error"]:
                    r["journal_errors"] += 1
                r["fences_completed"] += j["complete"]
                r["anchors_compared"] += j["compared"]
                r["mismatches"] += len(j["mismatch"])
                r["per_identity"] += j["partial"]
                r["aborted"] += j["aborted"]
                r["not_computable"] += j["not_computable"]
                for m in j["mismatch"]:
                    mism.append(dict(m, campaign=name, run=run.name, cell=f"{ep}/{contract}/{policy}", journal=db.name))
            r["trace_checks"].update(trace_checks(cell))
    out = []
    for key in sorted(rows):
        r = rows[key]
        r["trace_checks"] = dict(sorted(r["trace_checks"].items()))
        r["available"] = r["cells_with_journals"] > 0
        out.append(r)
    tot = {k: sum(r[k] for r in out) for k in ("cells", "cells_with_journals", "fences_completed", "anchors_compared", "mismatches",
                                                 "per_identity", "aborted", "not_computable")}
    tot["campaign_runs"] = len({(r["campaign"], r["run"]) for r in out})
    tot["campaign_runs_available"] = len({(r["campaign"], r["run"]) for r in out if r["available"]})
    tc = Counter()
    for r in out:
        tc.update(r["trace_checks"])
    tot["trace_checks"] = dict(sorted(tc.items()))
    return {"rows": out, "mismatches": mism, "total": tot}


def markdown(res: dict) -> str:
    t = res["total"]
    L = ["# Fence anchors: the owner's reported state against the evidence (PREREG_extra_086.md section 2)", "",
         "For every completed fence of every campaign run read: the anchor the run committed (releases 061-085: the owner's "
         "reported state) against the anchor the evidence gives (the previous committed anchor plus every APPLIED identity "
         "above the previous cutoff and at or below this cutoff). No hypothesis; expected 0 mismatches.", "",
         f"**{t['mismatches']} mismatch(es)** in {t['anchors_compared']} compared anchors of {t['fences_completed']} completed fences "
         f"({t['campaign_runs_available']} of {t['campaign_runs']} campaign runs with journals; {t['cells_with_journals']} of {t['cells']} "
         f"cells); per-identity intakes {t['per_identity']}, aborted fences {t['aborted']}, not computable {t['not_computable']}; "
         f"FENCE_ANCHOR_CHECK (release 086 traces): {t['trace_checks'] or 'none'}.", "",
         "| campaign | run | release | policy | cells | with journals | fences completed | anchors compared | mismatches | per-identity | aborted | FENCE_ANCHOR_CHECK |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in res["rows"]:
        avail = "" if r["available"] else " (not available)"
        L.append(f"| {r['campaign']} | {r['run']} | {r['release'] or '-'} | {r['policy']} | {r['cells']} | {r['cells_with_journals']}{avail} | "
                 f"{r['fences_completed']} | {r['anchors_compared']} | {r['mismatches']} | {r['per_identity']} | {r['aborted']} | "
                 f"{', '.join(f'{k} {v}' for k, v in r['trace_checks'].items()) or '-'} |")
    L.append("")
    if res["mismatches"]:
        L += ["## Mismatches", "", "| campaign | run | cell | journal | domain | token | cutoff | committed | evidence |", "|---|---|---|---|---|---|---|---|---|"]
        L += [f"| {m['campaign']} | {m['run']} | {m['cell']} | {m['journal']} | {m['domain']} | {m['token']} | {m['cutoff']} | {m['committed']} | {m['evidence']} |"
              for m in res["mismatches"][:200]]
        L.append("")
    else:
        L += ["No completed fence committed an anchor that differs from the evidence: the runs read are unaffected by the "
              "evidence anchor of release 086 (\"owner up\" regime).", ""]
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--campaign", action="append", default=[])
    a = ap.parse_args(argv)
    runs = [Path(c) for c in a.campaign if (Path(c) / "cells").is_dir()]
    res = analyse(runs)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "anchor086.json").write_text(json.dumps(res, indent=1, default=str) + "\n")
    (out / "ANCHOR086.md").write_text(markdown(res))
    t = res["total"]
    print(f"anchor086: {t['campaign_runs']} campaign runs ({t['campaign_runs_available']} with journals), {t['fences_completed']} completed fences, "
          f"{t['anchors_compared']} compared, {t['mismatches']} mismatches -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
