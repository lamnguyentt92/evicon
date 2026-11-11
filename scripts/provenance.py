#!/usr/bin/env python3
"""PROVENANCE.md (release 085; release 086: plan 085 published, its runs, plan 086; release 087: plan 086 published, its runs, plan 087): where every campaign the paper uses comes from, every preregistered plan with its SHA-256,
when it was frozen and published, the deviations of each plan's runs, and the numbers that the analysis does not
regenerate. The paper points here instead of carrying release and commit details.

    python3 scripts/provenance.py --report <report dir> --out <file> [--trees DIR] [--source-label TEXT]

Campaigns: every TESTBED campaign of the report's safety total (`<report>/safety/safety.json`: run, review run, release,
cells), and the plan it carries, from the campaign run's own `config.json` (the `preregistration` the runner checked)
when the run directory is there (as in `evicon_review.py report`, which writes `report/PROVENANCE.md`), else from the
config of the same name in the release tree that ran it (`<trees>/EVICON_TOIT_<release>/evaluation/campaigns/`; the
source of each row is shown). The runs of the newest plan that the report does not hold yet are listed as "to be filled
by the <newest release> report". Plans: every plan under evaluation/prereg/ with the SHA-256 of the file (checked against the SHA-256
the configs carry), the date it was frozen (the plan's header, else the date of the release it names), and, for the plans
published on GitHub before their runs, the commit and UTC time (verified read-only with `gh api` on
lamnguyentt92/evicon: the commit, its time, and the SHA-256 of the plan file at that commit). Deviations and exceptions:
written here, with their sources (the plans' results files, the paper's appendix, the build notes of each release)."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PREREG = REPO / "evaluation" / "prereg"
NEWEST_PLAN = "PREREG_extra_087.md"
NEWEST_RELEASE = "088"  # release 088 runs plan 087 (the same plan; see DEVIATIONS)

# Publication on GitHub (lamnguyentt92/evicon), verified with read-only `gh api` on 2026-11-04 (077-084), 2026-11-05 (085),
# 2026-11-09 (086) and 2026-11-11 (087):
# commit time (UTC) and the SHA-256 of the plan file at that commit (contents API, raw).
PUBLISHED = {
    "PREREG_review_077.md": [
        {"commit": "ed044bb", "utc": "2026-10-25 16:12", "sha256": "d0f28c5a691fd06a1c645612acc9ca95bd195abc3457284b2b2fe744b0a58138",
         "note": "release 077: first publication; differs from the frozen version only in one passage of section 7 (build "
                 "provenance: the runs are built from release 077 or 078)"},
        {"commit": "d67fd27", "utc": "2026-10-28 05:25", "sha256": "db67640d1709b06dfa5e61f77e3444c07e7c5828dfe24e0e85d163f06a711a29",
         "note": "release 078: the frozen version, re-frozen before any cell ran; it reached GitHub after the first 077 campaign "
                 "(expR-zephyr-winf/20261028T084744, VM-C time UTC+7) began at 01:47 UTC"}],
    "PREREG_extra_083.md": [{"commit": "ad4c714", "utc": "2026-10-31 01:13", "sha256": "6e3f24a918de659c1feb8aaa886d9e9681195353528b5df10e615e8897ff3a06", "note": "release 083"}],
    "PREREG_extra_084.md": [{"commit": "ce7fd12", "utc": "2026-10-31 11:37", "sha256": "b11351645c1e0f05f4a270412539a072f17148ea8607223dcb6182a171505345", "note": "release 084, tag v084"}],
    "PREREG_extra_085.md": [{"commit": "40e451d", "utc": "2026-11-04 10:05", "sha256": "22586c782aa3ac704bcc56376324faeb7c4047da6e35c1668a65a34924bf4790",
                             "note": "release 085, pushed 10:07 UTC; the 085 review run (review-20261104T171522, VM-C time UTC+7) began at "
                                     "10:15 UTC and its first cell at 11:18 UTC"}],
    "PREREG_extra_086.md": [{"commit": "440ecf1", "utc": "2026-11-05 06:21", "sha256": "d28956e98e4fff3ba032f14e1a3a48c1e92d9e0ffa1b75e14d9a36ca3adba9c6",
                             "note": "release 086; the 086 review run (review-20261105T134319, VM-C time UTC+7) began at 06:43 UTC, its "
                                     "first measurement at 06:52 UTC and its first cell at 07:48 UTC"}],
    "PREREG_extra_087.md": [{"commit": "2e98edc", "utc": "2026-11-11 04:50", "sha256": "f42bafffc96187f04d93e39d8f9d51bebd3cbf2fc306cb55765515ae2b86f100",
                             "note": "release 087, before any measurement of the plan; release 088 (the plan and every frozen file unchanged) runs it"}],
}

# Plans frozen as a later version than the release in their header says (date of the frozen version).
FROZEN_NOTE = {
    "PREREG_review_077.md": "2026-10-25 (release 077); frozen version 2026-10-28 (release 078), before any of its cells",
    "REPLICATION_expA_k0_069.md": "2026-10-20 (post hoc replication rule, written before the run: project note 07:23 UTC, campaign start 07:33 UTC)",
}

# Deviations of each plan's runs (no plan's own Deviations section records one; sources given).
DEVIATIONS = {
    "PREREG_expA_confirm_067.md": [
        "Fence experiment (experiment A): the first run (release 067) ran on an emulated owner with a defect (a sealed late "
        "copy overwrote an APPLIED receipt: 6 receipt flips in 4 cells; the authority refused every such answer). A "
        "replication on release 069 (`expA-confirm-k0`, K0) was decided post hoc by the rule `REPLICATION_expA_k0_069.md`, "
        "written before that run: the primary result stays the first run and the replication is reported next to it; "
        "`expA-confirm-k2` was replicated on release 071 (sources: `RESULTS_expA_confirm_067.md`, "
        "`REPLICATION_expA_k0_069.md`, the paper's appendix).",
        "SR was applied per config and pooled (post hoc), since the plan did not say which config (`RESULTS_expA_confirm_067.md`)."],
    "PREREG_expS_072.md": ["H-S3 was dropped before the run (power 0.18) and reported as estimand E-S1 (the paper's appendix)."],
    "PREREG_review_077.md": [
        "The frozen version (`db67640d...`) differs from the first published one only in section 7 (build provenance) and was "
        "re-frozen before any cell; it reached GitHub after the first campaign of the plan began (section 2)."],
    "PREREG_extra_083.md": [
        "Sessions: the plan named seven sessions, N1 to N7; the runs were grouped into four pieces (part 1, then parts 2a, "
        "2b, 2c) in exactly the planned order of campaigns, each ending with an incremental bundle (the plan allows a "
        "campaign to be split over sessions).",
        "`expT7-dinf-off5`: 1 of 8 cells INVALID (a fault-injection race: the device crashed, as scheduled, before it "
        "answered the harness's arm request, so it was not restarted). The plan re-runs an INVALID cell once, but the "
        "runner's activation gate, stricter than the plan's text, closed the run, so the re-run could not be done; with "
        "12.5 % of its cells excluded (more than 10 %) the run is not interpretable. Only an estimand of E-T7 and the "
        "safety rule X-S5 (its 7 VALID cells) use it; no hypothesis is affected. It is the only INVALID cell of the 402.",
        "T5: with T5-1 to T5-3 met and T5-SF1 to T5-SF4 absent the plan has the paper describe the greedy certificate as "
        "the release's algorithm for ADD domains; the default was not switched (releases 083 and 084 select it per run "
        "with a flag; every experiment except the greedy runs used enumeration). Source: the paper's appendix."],
    "PREREG_extra_084.md": [
        "PF-1 to PF-4 are across runs, as the plan states (PERIODIC_FULL ran alone, paired with the EVICON cells of earlier "
        "runs on byte-identical schedules); plan 085 repeats them within runs.",
        "The 084 report's fence pause for rule R5' counted the pauses that R5' ends with BARRIER_PER_IDENTITY_COMMITTED to "
        "the end of the cell (an analysis defect, review N5); release 085's `evaluation/overhead.py` corrects it, and the "
        "corrected values come from the 085 report."],
    "PREREG_extra_085.md": [
        "None: the plan's runs (review run `review-20261104T171522`: `tbcheck`, then expPM-zephyr-w6, -zephyr-winf, -minio-w6, "
        "-minio-winf, 16 cells each) ran in the planned order in one session; 64 cells, 0 INVALID; `tbcheck` decided at the "
        "first run (95 trials: SEQ 20, CUT 10 and ACK 10 per direction, TTL 5 per kind; 0 invalid); the final report found the 150 earlier verdicts identical to the 084 final "
        "report's (`report/verdicts085/`)."],
    "PREREG_extra_086.md": [
        "None: the plan's runs (review run `review-20261105T134319`: measure zephyr-plain zephyr, measure mosquitto-qos2, then "
        "expSR-zephyr-w6, -zephyr-winf, -minio-w6, -minio-winf (16 cells each), expSH-queue and expSH-direct (10 cells each)) ran "
        "in the planned order in one session; 84 cells, 0 INVALID; the final report found the 168 earlier verdicts identical to "
        "the 085 final report's (`report/verdicts086/`)."],
    NEWEST_PLAN: [
        "The plan runs under release 088, not 087. The 087 review run on the testbed (`review-20261111T120106`) stopped at `verify`, before any measurement or cell: the unit test `TestShadowReconcileK2Safe086` (release 086, unchanged) failed twice (its 30 ms reconciliation window was shorter than the journal writes before the first sweep on VM-C's disk), and a third `verify` with `TMPDIR` on tmpfs failed on `TestRecWindow087` (it needs the 1 ms ambiguity delay to pass before its sweeps). Release 088 changes only unit tests (these two: a 1 s window, a 5 ms wait; and `TestF37EvidenceConflictAbortsBarrierAndEscalates`, with the same 1 ms assumption: a 5 ms wait) and the release number: no runtime code and no frozen file changed.",
        "The rest is to be filled after the runs."],
}

# Numbers the report does not regenerate.
EXCEPTIONS = [
    "TLC results: `formal/TLC_RESULTS.txt`, `formal/TLC_RESULTS_FI.txt` and `formal/TLC_RESULTS_TIMING.txt` (with "
    "`formal/tlc_logs*/`) come from the release tree: TLC was run when each model was added (releases 082, 084, 085), "
    "not by `report`; `python3 evicon_review.py formal` runs them again. Release 086 adds no TLA+ configuration (the fence "
    "models never anchor on the owner's state: the guard's reservations come from the authority's own statuses). Release 087 "
    "adds the reboot model `formal/EviconFenceReboot.tla` (`formal/TLC_RESULTS_REBOOT.txt`, `formal/tlc_logs_reboot/`).",
    "Simulator predictions and the power tables of the plans: `evaluation/sim_predictions/` (and sections 1 and 5 of each "
    "plan) were computed before the plan was frozen and are frozen with it; `report` reads them and does not recompute "
    "them (each file's command is in `evaluation/sim_predictions/README.md`).",
    "The firmware footprint (`footprint.txt`) comes from the firmware build on VM-D (`deploy/setup/build_iot.sh`); "
    "`report` copies it.",
    "The publication commits and times of section 2 come from GitHub, not from the report.",
]


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def changelog_dates() -> dict:
    out = {}
    for line in (REPO / "CHANGELOG.md").read_text().splitlines():
        m = re.match(r"^## (\d{3}) — (\d{4}-\d{2}-\d{2})", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def plan_files() -> list:
    return sorted(p for p in PREREG.glob("*.md") if re.match(r"(PREREG_|PLAN_|NEGCTL_|REPLICATION_)", p.name))


def frozen_date(p: Path, dates: dict) -> str:
    if p.name in FROZEN_NOTE:
        return FROZEN_NOTE[p.name]
    head = p.read_text()[:600]
    m = re.search(r"[Ff]rozen on (\d{4}-\d{2}-\d{2})", head)
    rel = re.search(r"Release (\d{3})\.", head)
    if m:
        return m.group(1) + (f" (release {rel.group(1)})" if rel else "")
    if rel:
        return f"{dates.get(rel.group(1), '?')} (release {rel.group(1)}, before its first cell)"
    return "-"


def tree_configs(trees: Path | None, release: str) -> dict:
    """{campaign name: preregistration} of a release tree's configs."""
    out = {}
    if trees is None:
        return out
    d = trees / f"EVICON_TOIT_{release}" / "evaluation" / "campaigns"
    for f in sorted(d.glob("*.json")) if d.is_dir() else []:
        try:
            c = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(c, dict) and "name" in c:
            out[c["name"]] = c.get("preregistration")
    return out


def campaigns(report: Path, trees: Path | None) -> tuple:
    s = json.loads((report / "safety" / "safety.json").read_text())
    rows, cache = [], {}
    for r in s.get("runs", []):
        m = re.search(r"review-\d{8}T\d{6}", r.get("dir", ""))
        prereg, src = None, "-"
        cfg = Path(r.get("dir", "")) / "config.json"
        if cfg.is_file():
            try:
                prereg, src = json.loads(cfg.read_text()).get("preregistration"), "run config"
            except (OSError, ValueError):
                pass
        if src == "-":
            rel = str(r.get("release", ""))
            if rel not in cache:
                cache[rel] = tree_configs(trees, rel)
            if r.get("experiment") in cache[rel]:
                prereg, src = cache[rel][r["experiment"]], f"config of release {rel}"
        rows.append({"run": r["run"], "review_run": m.group(0) if m else "-", "release": str(r.get("release", "")),
                     "scope": r.get("scope", ""), "cells": r.get("cells"), "invalid": r.get("invalid"),
                     "plan": Path(prereg["path"]).name if isinstance(prereg, dict) and prereg.get("path") else None,
                     "plan_sha256": prereg.get("sha256") if isinstance(prereg, dict) else None, "source": src})
    # the newest plan's runs that the report does not hold yet
    spec = re.search(r"```pairs-spec\n(.*?)```", (PREREG / NEWEST_PLAN).read_text(), re.S)
    have = {r["run"].split("/")[0] for r in rows}
    pending = []
    if spec:
        for key, want in json.loads(spec.group(1)).get("runs", {}).items():
            if not want.get("reference") and want["campaign"] not in have:
                pending.append({"key": key, "campaign": want["campaign"]})
    return rows, pending, s.get("summary", {})


def markdown(report_label: str, rows: list, pending: list, summary: dict, report: Path) -> str:
    dates = changelog_dates()
    plans = plan_files()
    carried = {}
    for r in rows:
        if r["plan"]:
            carried.setdefault(r["plan"], set()).add(r["plan_sha256"])
    L = ["# Provenance", "",
         f"Generated by `scripts/provenance.py` (release {(REPO / 'VERSION').read_text().strip()}) from {report_label}. "
         f"The final report of release {NEWEST_RELEASE} writes the same tables, with its own campaigns filled in, to "
         f"`report/PROVENANCE.md`; that file supersedes this one for the {NEWEST_RELEASE} rows.", "",
         "## 1. Campaigns", "",
         f"Every TESTBED campaign of the report's safety total ({len(rows)} campaigns, {summary.get('guarded', {}).get('cells', '?') if isinstance(summary.get('guarded'), dict) else '?'} "
         "guarded VALID cells): the release that ran it, the review run it belongs to (on VM-C, or in the archive imported "
         "from its bundles), and the plan it carries (the `preregistration` of its config, checked by the runner before "
         "the first cell). Source: the campaign run's own `config.json`, or the config of the same name in the release "
         "that ran it.", "",
         "| campaign run | release | review run | VALID cells | INVALID | plan | plan SHA-256 | source |", "|---|---|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda x: (x["release"], x["run"])):
        L.append(f"| `{r['run']}` | {r['release']} | `{r['review_run']}` | {r['cells']} | {r['invalid']} | "
                 f"{('`' + r['plan'] + '`') if r['plan'] else 'none (exploratory)'} | "
                 f"{('`' + r['plan_sha256'][:16] + '...`') if r['plan_sha256'] else '-'} | {r['source']} |")
    for p in pending:
        L.append(f"| `{p['campaign']}/<to be filled by the {NEWEST_RELEASE} report>` | {NEWEST_RELEASE} | to be filled by the {NEWEST_RELEASE} report | - | - | "
                 f"`{NEWEST_PLAN}` | `{sha256(PREREG / NEWEST_PLAN)[:16]}...` | plan {NEWEST_PLAN} ({p['key']}) |")
    tbc = report / "tbcheck" / "tbcheck.json"
    if tbc.is_file():
        t = json.loads(tbc.read_text())
        L += ["", f"The ThingsBoard checks TB-1 to TB-4 of `PREREG_extra_085.md` are not a campaign: stage `tbcheck`, copied to "
                  f"`report/tbcheck/` (platform {t.get('platform')}, started {t.get('started')})."]
    else:
        L += ["", "The ThingsBoard checks TB-1 to TB-4 of `PREREG_extra_085.md` are not a campaign: stage `tbcheck` of the 085 "
                  "review run (`measure/tbcheck/`, copied to `report/tbcheck/`); not among this report's sources."]
    L += ["", "## 2. Plans", "",
          "Every plan with the SHA-256 of the file in this release (the frozen version: nothing above a plan's Deviations "
          "section is edited after freezing), the date it was frozen, and where the configs carry it, whether they "
          "carry the same SHA-256. Publication on GitHub (`lamnguyentt92/evicon`) is given for the plans published "
          "before their runs.", "",
          "| plan | SHA-256 | frozen | configs carry it | published on GitHub (commit, UTC) |", "|---|---|---|---|---|"]
    for p in plans:
        h = sha256(p)
        c = carried.get(p.name)
        ok = "-" if not c else ("yes" if c == {h} else f"DIFFERENT: {sorted(x[:16] for x in c if x)}")
        pub = PUBLISHED.get(p.name)
        if pub is None:
            ptxt = "-"
        elif not pub:
            ptxt = "to be filled when published"
        else:
            ptxt = "; ".join(f"`{x['commit']}` {x['utc']} (SHA-256 `{x['sha256'][:16]}...`: {x['note']})" for x in pub)
        L.append(f"| `{p.name}` | `{h}` | {frozen_date(p, dates)} | {ok} | {ptxt} |")
    L += ["", "## 3. Deviations of each plan's runs", "",
          "No plan's Deviations section records an entry (several plans have none); the points below are recorded in the "
          "plans' results files (`evaluation/prereg/RESULTS_*.md`), the paper's appendix and the release notes. Plans not "
          "listed: no deviation of their runs is known.", ""]
    for name, items in DEVIATIONS.items():
        L.append(f"- `{name}`:")
        L += [f"  - {x}" for x in items]
    L += ["", "## 4. Numbers the analysis does not regenerate", "",
          "Every other number of the paper is regenerated by `evicon_review.py report` from the campaigns above "
          "(`report/paper/numbers.tex`).", ""]
    L += [f"- {x}" for x in EXCEPTIONS]
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--report", required=True, help="a report directory (with safety/safety.json)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--trees", default="", help="directory holding the release trees EVICON_TOIT_<release> (fallback for the plan)")
    ap.add_argument("--source-label", default="", help="how the report is named in the header (default: its path)")
    a = ap.parse_args(argv)
    report = Path(a.report)
    if not (report / "safety" / "safety.json").is_file():
        print(f"provenance: no {report / 'safety' / 'safety.json'}", file=sys.stderr)
        return 1
    rows, pending, summary = campaigns(report, Path(a.trees) if a.trees else None)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(markdown(a.source_label or f"`{report}`", rows, pending, summary, report))
    missing = [r["run"] for r in rows if r["source"] == "-"]
    print(f"provenance: {len(rows)} campaigns ({len(missing)} without a config), {len(pending)} to be filled -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
