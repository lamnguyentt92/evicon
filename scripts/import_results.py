#!/usr/bin/env python3
"""Imports EviCon result bundles (EVICON_<release>_result[_<stamp>].zip, written by `evicon_review.py
bundle`) into one archive directory, so that a re-installed VM-C can read the runs of earlier releases
again (release 078: `evicon_review.py report --archive DIR`).

    python3 scripts/import_results.py /root/evicon-backup/*.zip --archive /root/evicon-archive [--lean]

What it does, per bundle (oldest first; a later bundle's copy of a file replaces an earlier one, as the
incremental bundles of one review run add to each other):
  - checks the bundle against <bundle>.sha256 when that file lies next to it (a mismatch skips the bundle);
  - extracts results/review-<stamp>/... into <archive>/runs/review-<stamp>/...: the merged review runs,
    with every campaign, measurement, control and overhead run they hold; the bundle's own files
    (environment snapshot, VM state, driver log) go to <archive>/bundles/<bundle name>/;
  - refuses members with an absolute path, a '..' component or a symbolic link (nothing is written
    outside the archive), and skips a file already present with the same size and CRC;
  - keeps, in <archive>/provenance.json, which bundle wrote each file, so that importing an earlier
    bundle again later never replaces a file a later bundle wrote (it is counted as superseded).
Then, for every campaign run in the archive:
  - every schedule file schedule_ep<e>.json the run lacks is written from the run's own config.json
    with evaluation/campaign.py's schedule (the function every runner uses) and listed as regenerated;
    for runs that hold their schedule files, episode 0 is regenerated too and compared byte for byte
    (a check that the archived schedules are the ones the frozen code produces).
Finally it writes <archive>/ARCHIVE.md and archive.json: the bundles (checksum verified or not), the
review runs, every campaign run (cells, VALID cells, complete or not, schedules), and which reference
runs of the 077 plan (evaluation/prereg/PREREG_review_077.md: pairs-spec and policy-compare) are there.

--lean leaves out the authority traces (raw/**/authority<d>.jsonl, about 90 % of the bytes) of the
campaigns that are not reference runs of the 077 plan and not RQ4 load runs; everything else is kept.
Without --lean the archive needs about 18 GB for the bundles of releases 065-076; the script checks the
free space first. Nothing outside the archive is changed; running it again only adds what is missing.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import zipfile
import zlib
from dataclasses import asdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "evaluation"))

BUNDLE_RE = re.compile(r"^EVICON_(\d{3})_result(?:_(\d{8}T\d{4}))?(?:_\d+)?\.zip$")
TRACE_RE = re.compile(r"/raw/(?:[^/]+/)*authority\d+\.jsonl$")
PLAN = REPO / "evaluation" / "prereg" / "PREREG_review_077.md"


class ImportError_(Exception):
    pass


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def bundle_key(p: Path):
    """Oldest first: release, then the bundle's stamp (incremental bundles), then the newest member date."""
    m = BUNDLE_RE.match(p.name)
    rel, stamp = (m.group(1), m.group(2) or "") if m else ("999", "")
    try:
        with zipfile.ZipFile(p) as z:
            newest = max((i.date_time for i in z.infolist()), default=(1980, 1, 1, 0, 0, 0))
    except zipfile.BadZipFile:
        newest = (1980, 1, 1, 0, 0, 0)
    return (rel, stamp or "%04d%02d%02dT%02d%02d" % newest[:5], p.name)


IGNORED = []  # zips in a given directory whose name is not a bundle's (e.g. "... (1).zip" from a browser)


def expand(sources) -> list:
    out = []
    for s in sources:
        p = Path(s)
        if p.is_dir():
            out += sorted(x for x in p.iterdir() if x.is_file() and BUNDLE_RE.match(x.name))
            IGNORED.extend(str(x) for x in sorted(p.iterdir()) if x.is_file() and x.suffix.lower() == ".zip" and not BUNDLE_RE.match(x.name))
        elif p.is_file():
            out.append(p)
        else:
            raise ImportError_(f"{s}: no such file or directory")
    seen, uniq = set(), []
    for p in out:
        r = p.resolve()
        if r not in seen:  # the same bundle given twice (or through a link): once
            seen.add(r)
            uniq.append(p.absolute())  # its own name and directory: the .sha256 next to it
    return sorted(uniq, key=bundle_key)


def plan_references() -> dict:
    """{key: campaign name} of the 077 plan's reference runs (pairs-spec 'reference': true, and the
    policy-compare runs that no 077 config writes)."""
    refs = {}
    if not PLAN.exists():
        return refs
    text = PLAN.read_text()
    m = re.search(r"```pairs-spec\n(.*?)```", text, re.S)
    if m:
        for k, v in json.loads(m.group(1))["runs"].items():
            if v.get("reference"):
                refs[k] = v["campaign"]
    m = re.search(r"```policy-compare\n(.*?)```", text, re.S)
    if m:
        own = {json.loads(p.read_text()).get("name") for p in (REPO / "evaluation" / "campaigns").glob("*.json")
               if "PREREG_review_077" in p.read_text()}
        for k, v in json.loads(m.group(1))["runs"].items():
            if v["campaign"] not in own and v["campaign"] not in refs.values():
                refs[k] = v["campaign"]
    return refs


def _safe_rel(name: str) -> str:
    if name.startswith("/") or "\\" in name or re.match(r"^[A-Za-z]:", name):
        raise ImportError_(f"member {name!r}: absolute path")
    parts = [p for p in name.split("/") if p]
    if any(p in ("..", ".") for p in parts):
        raise ImportError_(f"member {name!r}: '..' or '.' component")
    return "/".join(parts)


def _target(archive: Path, bundle: str, rel: str):
    """Where a bundle member goes (None: not imported)."""
    top, _, rest = rel.partition("/")
    if not rest:
        return None
    if rest.startswith("results/review-"):
        sub = rest[len("results/"):]
        first = sub.split("/", 1)[0]
        if not re.fullmatch(r"review-\d{8}T\d{6}(-local)?", first) or "/" not in sub:
            return None  # e.g. a review run's own zip: its directory is in the bundle anyway
        return archive / "runs" / sub
    return archive / "bundles" / Path(bundle).stem / rest


def _campaign_of(target: Path, archive: Path):
    """Campaign name of a file under runs/review-*/campaigns/<name>/<stamp>/..., else None."""
    try:
        parts = target.relative_to(archive / "runs").parts
    except ValueError:
        return None
    return parts[2] if len(parts) > 3 and parts[1] == "campaigns" else None


def load_provenance(archive: Path) -> dict:
    """{"bundles": [[key...], ...], "files": {path relative to the archive: index into bundles}}."""
    p = archive / "provenance.json"
    try:
        prov = json.loads(p.read_text())
        prov["bundles"] = [tuple(b) for b in prov["bundles"]]
        return prov
    except (OSError, ValueError, KeyError, TypeError):
        return {"bundles": [], "files": {}}


def save_provenance(archive: Path, prov: dict):
    tmp = archive / "provenance.json.part"
    tmp.write_text(json.dumps({"bundles": [list(b) for b in prov["bundles"]], "files": prov["files"]}, separators=(",", ":")))
    os.replace(tmp, archive / "provenance.json")


def import_bundle(z: Path, archive: Path, lean: bool, keep: set, log, prov: dict = None) -> dict:
    res = {"bundle": z.name, "sha256": None, "verified": None, "files": 0, "written": 0, "unchanged": 0,
           "superseded": 0, "lean_skipped": 0, "lean_skipped_bytes": 0, "skipped": 0, "runs": []}
    runs = set()
    prov = prov if prov is not None else {"bundles": [], "files": {}}
    key = tuple(bundle_key(z))
    if key not in prov["bundles"]:
        prov["bundles"].append(key)
    me = prov["bundles"].index(key)
    side = z.with_name(z.name + ".sha256")
    digest = sha256_file(z)
    res["sha256"] = digest
    if side.exists():
        want = side.read_text().split()[0].strip().lower() if side.read_text().split() else ""
        res["verified"] = want == digest
        if not res["verified"]:
            raise ImportError_(f"{z.name}: SHA-256 {digest} differs from {side.name} ({want}); bundle skipped")
    with zipfile.ZipFile(z) as f:
        infos = f.infolist()
        tops = {i.filename.split("/", 1)[0] for i in infos if i.filename.strip("/")}
        if len(tops) != 1:
            raise ImportError_(f"{z.name}: not an EviCon result bundle (top-level entries {sorted(tops)[:5]})")
        for i in infos:  # every member checked before anything is written: a refused bundle writes nothing
            _safe_rel(i.filename)
            if (i.external_attr >> 16) & 0o170000 == stat.S_IFLNK:
                raise ImportError_(f"{z.name}: member {i.filename!r} is a symbolic link")
        for i in infos:
            rel = _safe_rel(i.filename)
            if i.is_dir():
                continue
            res["files"] += 1
            t = _target(archive, z.name, rel)
            if t is None:
                res["skipped"] += 1
                continue
            if t.is_relative_to(archive / "runs"):
                runs.add(t.relative_to(archive / "runs").parts[0])
            if lean and TRACE_RE.search("/" + rel) and _campaign_of(t, archive) not in keep:
                res["lean_skipped"] += 1
                res["lean_skipped_bytes"] += i.file_size
                continue
            if t.exists() and t.stat().st_size == i.file_size:
                crc = 0
                with open(t, "rb") as fh:
                    for b in iter(lambda: fh.read(1 << 20), b""):
                        crc = zlib.crc32(b, crc)
                if crc == i.CRC:
                    res["unchanged"] += 1
                    continue
            rel_t = str(t.relative_to(archive))
            by = prov["files"].get(rel_t)
            if t.exists() and by is not None and by != me and prov["bundles"][by] > key:
                res["superseded"] += 1  # a later bundle wrote this file: it stays
                continue
            t.parent.mkdir(parents=True, exist_ok=True)
            tmp = t.with_name(t.name + ".part")
            with f.open(i) as src, open(tmp, "wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
            ts = dt.datetime(*i.date_time).timestamp()
            os.utime(tmp, (ts, ts))
            os.replace(tmp, t)
            prov["files"][rel_t] = me
            res["written"] += 1
    res["runs"] = sorted(runs)
    log(f"{z.name}: {res['files']} files, {res['written']} written, {res['unchanged']} unchanged"
        + (f", {res['superseded']} kept from later bundles" if res["superseded"] else "")
        + (f", {res['lean_skipped']} traces left out (--lean)" if res["lean_skipped"] else "")
        + ("" if res["verified"] is None else ", checksum " + ("verified" if res["verified"] else "MISMATCH")))
    return res


def schedules(archive: Path, log) -> list:
    import campaign as C
    out = []
    for cfgp in sorted((archive / "runs").glob("review-*/campaigns/*/2*/config.json")):
        run = cfgp.parent
        try:
            cfg = json.loads(cfgp.read_text())
            eps = int(cfg.get("episodes", 1))
        except (ValueError, TypeError):
            out.append({"run": str(run.relative_to(archive)), "error": "config.json unreadable"})
            continue
        rec = {"run": str(run.relative_to(archive)), "episodes": eps, "present": 0, "regenerated": [], "check": None}
        for ep in range(eps):
            p = run / f"schedule_ep{ep}.json"
            try:
                arrivals, windows = C.schedule(cfg, ep)
            except Exception as e:  # noqa: BLE001 (a config of an older release the schedule cannot read)
                rec["error"] = f"schedule({ep}): {type(e).__name__}: {e}"
                break
            text = json.dumps({"arrivals": [asdict(x) for x in arrivals], "faults": [asdict(x) for x in windows]})
            if p.exists():
                rec["present"] += 1
                if ep == 0:
                    rec["check"] = "identical" if p.read_text() == text else "DIFFERENT"
            else:
                p.write_text(text)
                rec["regenerated"].append(ep)
        if rec["regenerated"]:
            (run / "schedule_regenerated.json").write_text(json.dumps({"episodes": rec["regenerated"], "by": "scripts/import_results.py",
                                                                        "function": "evaluation/campaign.py schedule"}) + "\n")
            log(f"{rec['run']}: schedules of episodes {rec['regenerated']} regenerated from config.json")
        if rec["check"] == "DIFFERENT":
            log(f"WARN {rec['run']}: the archived schedule_ep0.json differs from the one campaign.py computes from config.json")
        out.append(rec)
    return out


def campaigns(archive: Path) -> list:
    import csv
    rows = []
    for run in sorted((archive / "runs").glob("review-*/campaigns/*/2*")):
        if not (run / "config.json").exists():
            continue
        cfg = json.loads((run / "config.json").read_text())
        csvp = run / "runtime_episodes.csv"
        cells, valid = 0, 0
        if csvp.exists():
            with open(csvp, newline="") as fh:
                for r in csv.DictReader(fh):
                    cells += 1
                    valid += str(r.get("cell_status", "")).startswith("VALID")
        excl = {tuple(x) for x in cfg.get("exclude_cells", [])}
        total = int(cfg.get("episodes", 1)) * len([(c, p) for c in cfg.get("contracts", []) for p in cfg.get("policies", []) if (c, p) not in excl])
        traces = sum(1 for _ in run.glob("cells/**/authority*.jsonl"))
        rel = ""
        for f in (run / "manifest.json", run.parents[2] / "source.json"):
            try:
                rel = str(json.loads(f.read_text()).get("release") or "") or rel
            except (OSError, ValueError):
                pass
            if rel:
                break
        rows.append({"campaign": run.parent.name, "run": str(run.relative_to(archive)), "release": rel, "cells": cells,
                     "cells_total": total, "valid": valid, "complete": cells >= total > 0, "traces": traces})
    return rows


def newest_report_sources(archive: Path):
    """The runs the newest report in the archive was written from (the `- ` lines under "sources:" of
    runs/review-*/report/REPORT.md, as `report --sources-of` reads them), then that review run itself.
    Returns (report path or None, [(review run name, release or "", present)]). `report --archive` reads
    exactly these runs, so the archive gives the report the runs the earlier report read."""
    reps = sorted((archive / "runs").glob("review-*/report/REPORT.md"), key=lambda r: r.parent.parent.name)
    if not reps:
        return None, []
    rep, out = reps[-1], []
    for line in rep.read_text(errors="replace").splitlines():
        m = re.match(r"^- `(/[^`]+)`$", line.strip())
        if m:
            src = Path(m.group(1))
            rel = re.match(r"EVICON_TOIT_(\d{3})$", src.parent.parent.name) if src.parent.name == "results" else None
            out.append((src.name, rel.group(1) if rel else ""))
        elif out and line.startswith("- ["):
            break
    own = rep.parent.parent.name
    if own not in [n for n, _ in out]:
        out.append((own, ""))
    return rep, [(n, rel, (archive / "runs" / n).is_dir()) for n, rel in out]


def free_bytes(p: Path) -> int:
    q = p
    while not q.exists():
        q = q.parent
    return shutil.disk_usage(q).free


def union_size(zips) -> int:
    u = {}
    for z in zips:
        with zipfile.ZipFile(z) as f:
            for i in f.infolist():
                if not i.is_dir():
                    rel = i.filename.split("/", 1)[-1]
                    u[rel] = max(u.get(rel, 0), i.file_size)
    return sum(u.values())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sources", nargs="+", help="bundle zips, or directories holding them")
    ap.add_argument("--archive", default="/root/evicon-archive")
    ap.add_argument("--lean", action="store_true", help="leave out the authority traces of runs the 077 plan does not reference")
    ap.add_argument("--force-space", action="store_true", help="skip the free-space check")
    a = ap.parse_args(argv)
    archive = Path(a.archive).resolve()
    lines = []

    def log(s):
        print(s, flush=True)
        lines.append(s)
    try:
        zips = expand(a.sources)
    except ImportError_ as e:
        print(f"import-results: {e}", file=sys.stderr)
        return 2
    if not zips:
        print("import-results: no EVICON_<release>_result*.zip found in " + ", ".join(a.sources), file=sys.stderr)
        return 2
    for x in IGNORED:
        log(f"WARN: {x} ignored: not named EVICON_<release>_result[_<stamp>].zip (rename it, or give the file itself)")
    refs = plan_references()
    keep = set(refs.values()) | {"rq4-load-r1", "rq4-load-r5", "rq4-load-r20"}
    need = union_size(zips)
    free = free_bytes(archive)
    log(f"{len(zips)} bundle(s), up to {need / 2**30:.1f} GiB unpacked{' before --lean' if a.lean else ''}; "
        f"{free / 2**30:.1f} GiB free at {archive}")
    if not a.lean and not a.force_space and need + (2 << 30) > free:
        print(f"import-results: not enough space for every run with its traces ({need / 2**30:.1f} GiB + 2 GiB margin > "
              f"{free / 2**30:.1f} GiB free). Run it again with --lean (the plan's reference runs keep their traces), or "
              f"give --archive on a larger disk.", file=sys.stderr)
        return 3
    (archive / "runs").mkdir(parents=True, exist_ok=True)
    results, failed = [], []
    prov = load_provenance(archive)
    for z in zips:
        try:
            results.append(import_bundle(z, archive, a.lean, keep, log, prov))
        except (ImportError_, zipfile.BadZipFile, OSError) as e:
            failed.append({"bundle": z.name, "error": str(e)})
            log(f"FAIL {z.name}: {e}")
        save_provenance(archive, prov)
    sched = schedules(archive, log)
    camps = campaigns(archive)
    present = {}
    for key, name in sorted(refs.items()):
        runs = [c for c in camps if c["campaign"] == name and c["complete"]]
        present[key] = {"campaign": name, "run": runs[0]["run"] if runs else None, "traces": runs[0]["traces"] if runs else 0}
    state = {"written": dt.datetime.now().isoformat(timespec="seconds"), "archive": str(archive), "lean": a.lean,
             "bundles": results, "failed": failed, "schedules": sched, "campaigns": camps, "plan_references": present}
    old = {}
    if (archive / "archive.json").exists():
        try:
            old = json.loads((archive / "archive.json").read_text())
        except ValueError:
            old = {}
    seen = {b["bundle"] for b in results}
    state["bundles"] = [b for b in old.get("bundles", []) if b["bundle"] not in seen] + results
    # the release that wrote each review run (that of its bundles): make_data_archive.py packs the run under it
    rr = {}
    for b in state["bundles"]:
        m = BUNDLE_RE.match(b["bundle"])
        for r in b.get("runs", []):
            e = rr.setdefault(r, {"release": None, "bundles": []})
            e["bundles"].append(b["bundle"])
            if m and (e["release"] is None or m.group(1) < e["release"]):
                e["release"] = m.group(1)
    state["runs"] = dict(sorted(rr.items()))
    (archive / "archive.json").write_text(json.dumps(state, indent=2) + "\n")
    md = ["# EviCon result archive", "", f"Written {state['written']} by `scripts/import_results.py`" + (" (--lean)" if a.lean else ""), "",
          "## Bundles", "", "| bundle | SHA-256 | checksum file | files | written |", "|---|---|---|---|---|"]
    for b in state["bundles"]:
        md.append(f"| {b['bundle']} | `{(b['sha256'] or '')[:16]}...` | {'-' if b['verified'] is None else ('verified' if b['verified'] else 'MISMATCH')} "
                  f"| {b['files']} | {b['written']} |")
    for f in failed:
        md.append(f"| {f['bundle']} | - | FAILED: {f['error']} | - | - |")
    md += ["", "## Reference runs of PREREG_review_077.md", "", "| key | campaign | run | authority traces |", "|---|---|---|---|"]
    for k, v in present.items():
        md.append(f"| {k} | `{v['campaign']}` | {('`' + v['run'] + '`') if v['run'] else '**MISSING**'} | {v['traces']} |")
    rep, srcs = newest_report_sources(archive)
    state["report_sources"] = {"report": str(rep.relative_to(archive)) if rep else None,
                               "runs": [{"run": n, "release": r, "present": ok} for n, r, ok in srcs]}
    (archive / "archive.json").write_text(json.dumps(state, indent=2) + "\n")
    md += ["", "## Runs `report --archive` reads", ""]
    if rep:
        md += [f"The sources of the newest report in the archive (`{rep.relative_to(archive)}`):", "",
               "| review run | release | in the archive |", "|---|---|---|"]
        md += [f"| `{n}` | {r or '-'} | {'yes' if ok else '**MISSING** (import EVICON_' + (r or '<release>') + '_result*.zip)'} |" for n, r, ok in srcs]
    else:
        md.append("No review run of the archive holds a report (`report --archive` needs one).")
    md += ["", "## Campaign runs", "", "| campaign | run | release | cells | VALID | complete | traces | schedules |", "|---|---|---|---|---|---|---|---|"]
    by_run = {s["run"]: s for s in sched}
    for c in camps:
        s = by_run.get(c["run"], {})
        sch = s.get("error") or (f"{s.get('present', 0)} archived" + (f", {len(s['regenerated'])} regenerated" if s.get("regenerated") else "")
                                 + (f", ep0 {s['check']}" if s.get("check") else ""))
        md.append(f"| {c['campaign']} | `{c['run']}` | {c['release'] or '-'} | {c['cells']}/{c['cells_total']} | {c['valid']} | "
                  f"{'yes' if c['complete'] else 'no'} | {c['traces']} | {sch} |")
    (archive / "ARCHIVE.md").write_text("\n".join(md) + "\n")
    missing = [k for k, v in present.items() if not v["run"]]
    differ = [s["run"] for s in sched if s.get("check") == "DIFFERENT"]
    log(f"archive: {len(list((archive / 'runs').glob('review-*')))} review run(s), {len(camps)} campaign run(s) -> {archive / 'ARCHIVE.md'}")
    log("reference runs of the 077 plan: " + ("all present" if not missing else f"MISSING {missing} (import the bundles that hold them)"))
    gone = [f"{n} (EVICON_{r or '<release>'}_result*.zip)" for n, r, ok in srcs if not ok]
    if rep:
        log(f"runs of the newest report ({rep.parent.parent.name}), which `report --archive` reads: "
            + (f"all {len(srcs)} present" if not gone else f"MISSING {gone}"))
    if differ:
        log(f"WARN: archived schedules differ from the frozen code's in {len(differ)} run(s): {differ[:4]}")
    return 1 if failed or differ else 0


if __name__ == "__main__":
    raise SystemExit(main())
