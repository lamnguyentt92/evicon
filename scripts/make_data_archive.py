#!/usr/bin/env python3
"""Data archive of the EviCon testbed runs (release 075), for deposit with the paper, and the offline
reproduction of the report from it.

  pack       On VM-C: one zip per review run the newest report read (its "sources:" list, as
             `evicon_review.py report --sources-of` reads it) or per --run, holding that run directory
             under its release directory (EVICON_TOIT_0xx/results/review-.../), plus MANIFEST.sha256,
             SOURCES.txt and README.md. Before writing, every text file is scanned for credentials
             (password=, secret=, token=, private keys, AWS-style keys); a hit stops the pack and lists the
             files (nothing is written). EviCon passes secrets only through environment variables, so a
             hit means something unexpected reached a run directory: inspect it, do not --force blindly.

  reproduce  Anywhere (no testbed): extracts every zip of an archive into one work directory, merging
             runs that were split over several zips, checks MANIFEST.sha256 first, and runs
             `python3 evicon_review.py report --local --from <run> ...` over the runs in the order of
             SOURCES.txt. The report (report/REPORT.md, the paper tables, the claims check) is then
             regenerated from the deposited data alone.

    python3 scripts/make_data_archive.py pack --sources-of /root/EVICON_TOIT_075 --out /root/EVICON_DATA_075
    python3 scripts/make_data_archive.py pack --run /root/EVICON_TOIT_074/results/review-20261024T031941 --out <dir>
    python3 scripts/make_data_archive.py reproduce --archive <dir> --work <empty dir> [--dry-run]
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SECRET_PATTERNS = [re.compile(p, re.I) for p in (
    r"(password|passwd|secret|api[_-]?key|access[_-]?key|token)\s*[=:]\s*['\"]?[A-Za-z0-9/+_\-]{8,}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"\bAKIA[0-9A-Z]{16}\b",
)]
# words that look like a credential assignment in EviCon's own outputs but are not one
ALLOW = re.compile(r"(token\s*[=:]\s*['\"]?(rb-[0-9]+|fence-|f-[0-9a-f]+|[0-9a-f]{8}-))|(_ENV|\$\{[A-Z_]+\})", re.I)
TEXT_SUFFIXES = {".json", ".jsonl", ".log", ".txt", ".md", ".csv", ".yml", ".yaml", ".sh", ".env", ".conf", ".ini", ".cfg", ".py", ".out", ""}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def report_sources(where: Path) -> list:
    reps = sorted(where.glob("report/REPORT.md")) or sorted(where.glob("results/review-*/report/REPORT.md"), key=lambda p: p.parent.parent.name)
    if not reps:
        raise SystemExit(f"--sources-of {where}: no report/REPORT.md")
    out = []
    for line in reps[-1].read_text().splitlines():
        m = re.match(r"^- `(/[^`]+)`$", line.strip())
        if m:
            out.append(Path(m.group(1)))
        elif out and line.startswith("- ["):
            break
    run = reps[-1].parent.parent
    if run not in out:
        out.append(run)
    return out


def scan(run: Path, max_mb: float) -> list:
    hits = []
    for p in sorted(run.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in TEXT_SUFFIXES or p.stat().st_size > max_mb * 1e6:
            continue
        try:
            text = p.read_text(errors="ignore")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if any(rx.search(line) for rx in SECRET_PATTERNS) and not ALLOW.search(line):
                hits.append(f"{p}:{i}")
                break
    return hits


def archive_name(run: Path) -> str:
    rel = run.parent.parent.name  # EVICON_TOIT_0xx
    return f"{rel}__{run.name}.zip"


def pack(a) -> int:
    runs = [Path(r).resolve() for r in a.run] if a.run else report_sources(Path(a.sources_of).resolve())
    missing = [str(r) for r in runs if not r.is_dir()]
    if missing:
        raise SystemExit(f"missing run directories: {missing}")
    hits = [h for r in runs for h in scan(r, a.scan_max_mb)]
    if hits and not a.force:
        print("possible credentials found; nothing written. Inspect these lines:", *hits[:50], sep="\n  ", file=sys.stderr)
        return 2
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=False)
    manifest, sources = [], []
    for r in runs:
        z = out / archive_name(r)
        base = r.parent.parent.parent  # the directory holding EVICON_TOIT_0xx
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            for p in sorted(r.rglob("*")):
                if p.is_file() and not p.is_symlink():
                    zf.write(p, str(p.relative_to(base)))
        manifest.append(f"{sha256(z)}  {z.name}")
        sources.append(f"{r.parent.parent.name}/results/{r.name}")
        print(f"packed {r} -> {z.name} ({z.stat().st_size / 1e6:.1f} MB)")
    (out / "MANIFEST.sha256").write_text("\n".join(manifest) + "\n")
    (out / "SOURCES.txt").write_text("\n".join(sources) + "\n")
    (out / "README.md").write_text(README.format(release=(REPO / "VERSION").read_text().strip(), n=len(runs),
                                                  runs="\n".join(f"- `{s}`" for s in sources)))
    print(f"data archive: {len(runs)} runs -> {out} (check: cd {out} && sha256sum -c MANIFEST.sha256)")
    return 0


def reproduce(a) -> int:
    arc, work = Path(a.archive).resolve(), Path(a.work).resolve()
    if work.exists() and any(work.iterdir()):
        raise SystemExit(f"--work {work} must be empty or absent")
    bad = []
    for line in (arc / "MANIFEST.sha256").read_text().splitlines():
        digest, name = line.split(None, 1)
        if sha256(arc / name.strip()) != digest:
            bad.append(name)
    if bad:
        raise SystemExit(f"MANIFEST.sha256 does not match: {bad}")
    work.mkdir(parents=True, exist_ok=True)
    for z in sorted(arc.glob("*.zip")):
        with zipfile.ZipFile(z) as zf:
            for n in zf.namelist():
                dest = (work / n).resolve()
                if not str(dest).startswith(str(work) + os.sep):
                    raise SystemExit(f"{z.name}: unsafe path {n}")
            zf.extractall(work)
    runs = [work / s for s in (arc / "SOURCES.txt").read_text().split()]
    cmd = [sys.executable, str(REPO / "evicon_review.py"), "report", "--local"] + sum((["--from", str(r)] for r in runs), [])
    print("reproduce:", " ".join(cmd))
    if a.dry_run:
        return 0
    return subprocess.call(cmd, cwd=str(REPO))


README = """# EviCon testbed data (release {release})

{n} review runs of the EviCon reviewer driver (`evicon_review.py`) on the 5-VM testbed, one zip per run.
Each zip holds the run directory under its release directory, `EVICON_TOIT_0xx/results/review-<UTC time>/`:
the stage logs, the EviCon-Probe measurements (`measure/`), the campaigns (`campaigns/<name>/<UTC time>/`
with `config.json`, `manifest.json`, `runtime_episodes.csv`, the schedules and every cell's raw traces),
and that run's report (`report/`). `MANIFEST.sha256` lists the SHA-256 of every zip; `SOURCES.txt` the
runs in the order the report reads them.

Runs:

{runs}

Regenerate the report (tables, figures, number macros, every preregistered analysis, the claims check)
from these files alone, with the EviCon source release of the paper:

    python3 scripts/make_data_archive.py reproduce --archive <this directory> --work <empty directory>

Labels: `TESTBED` (5-VM testbed), `DEV_LOCAL` (one machine), `SIMULATION_ONLY` (the simulator). Only
`TESTBED` numbers are measurements. Preregistrations, with the SHA-256 their runs verified, are in
`evaluation/prereg/` of the source release; their outcomes in `evaluation/prereg/RESULTS_*.md`.
"""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pack")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--sources-of", default="")
    g.add_argument("--run", action="append", default=[])
    p.add_argument("--out", required=True)
    p.add_argument("--scan-max-mb", type=float, default=20.0)
    p.add_argument("--force", action="store_true", help="pack despite scan hits (after inspecting them)")
    r = sub.add_parser("reproduce")
    r.add_argument("--archive", required=True)
    r.add_argument("--work", required=True)
    r.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    return pack(a) if a.cmd == "pack" else reproduce(a)


if __name__ == "__main__":
    sys.exit(main())
