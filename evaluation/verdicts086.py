#!/usr/bin/env python3
"""The verdicts of the earlier plans against the 085 final report (release 086, PREREG_extra_086.md section 6).

The final report of release 086 reads the runs of the 085 final report and the 086 run. Every hypothesis of every plan
up to PREREG_extra_085 (168 in the 085 final report) must keep the verdict the 085 final report gave it: this check compares Part 1 of the claims check
of the two reports (claims/claims.json: plan, ID, verdict, estimate) and lists every difference: a verdict that differs,
a hypothesis that is missing from either report, and (informational) an estimate that differs. The rows of
PREREG_extra_086 are new and left out.

    python3 evaluation/verdicts086.py --new <report dir> --old <085 report dir or its claims/claims.json> --out <dir>
Writes VERDICTS.md and verdicts.json; exit 0 also when verdicts differ (the report records it, the driver logs a FAIL).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

NEW_PLANS = ("PREREG_extra_086",)


def rows(claims: Path) -> dict:
    d = json.loads(claims.read_text())
    return {(h["plan"], h["id"]): h for h in d.get("hypotheses", []) if h.get("plan") not in NEW_PLANS}


def compare(new: Path, old: Path) -> dict:
    a, b = rows(old), rows(new)
    diff_v = [{"plan": k[0], "id": k[1], "old": a[k].get("verdict"), "new": b[k].get("verdict")}
              for k in sorted(set(a) & set(b)) if a[k].get("verdict") != b[k].get("verdict")]
    diff_e = [{"plan": k[0], "id": k[1], "old": a[k].get("estimate"), "new": b[k].get("estimate")}
              for k in sorted(set(a) & set(b)) if a[k].get("estimate") != b[k].get("estimate") and a[k].get("verdict") == b[k].get("verdict")]
    return {"old": str(old), "new": str(new), "compared": len(set(a) & set(b)), "verdicts_differ": diff_v,
            "missing_in_new": [list(k) for k in sorted(set(a) - set(b))], "new_in_new": [list(k) for k in sorted(set(b) - set(a))],
            "estimates_differ": diff_e, "identical": not diff_v and not (set(a) - set(b))}


def markdown(r: dict) -> str:
    L = ["# Verdicts of the earlier plans against the 085 final report", "",
         f"Compared {r['compared']} hypotheses (every plan up to PREREG_extra_085; `{r['old']}` against this report). "
         + ("**Identical**: every verdict as in the 085 final report." if r["identical"] else
            f"**DIFFERENT**: {len(r['verdicts_differ'])} verdict(s) differ, {len(r['missing_in_new'])} hypothesis(es) missing in this report."), ""]
    if r["verdicts_differ"]:
        L += ["| plan | ID | 085 final report | this report |", "|---|---|---|---|"] + [f"| {x['plan']} | {x['id']} | {x['old']} | {x['new']} |" for x in r["verdicts_differ"]] + [""]
    if r["missing_in_new"]:
        L += ["Missing in this report: " + ", ".join(f"{p} {i}" for p, i in r["missing_in_new"]), ""]
    if r["new_in_new"]:
        L += ["Not in the 085 final report (new rows of earlier plans): " + ", ".join(f"{p} {i}" for p, i in r["new_in_new"]), ""]
    if r["estimates_differ"]:
        L += [f"Estimates that differ with the same verdict ({len(r['estimates_differ'])}; informational):", "",
              "| plan | ID | 085 final report | this report |", "|---|---|---|---|"]
        L += [f"| {x['plan']} | {x['id']} | {x['old']} | {x['new']} |" for x in r["estimates_differ"]]
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--new", required=True)
    ap.add_argument("--old", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    new, old = Path(a.new), Path(a.old)
    new = new / "claims" / "claims.json" if new.is_dir() else new
    old = old / "claims" / "claims.json" if old.is_dir() else old
    r = compare(new, old)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "verdicts.json").write_text(json.dumps(r, indent=1) + "\n")
    (out / "VERDICTS.md").write_text(markdown(r))
    print(f"verdicts086: {r['compared']} compared, {len(r['verdicts_differ'])} verdict(s) differ, {len(r['missing_in_new'])} missing -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
