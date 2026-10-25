#!/usr/bin/env python3
"""Freezes a preregistration (release 067; plan-specific files: release 071).

1. Writes the SHA-256 of every file the plan depends on into the plan's
   ```frozen-files``` block (analysis scripts, campaign runner, simulator twin), plus
   the files the plan lists in an optional ```frozen-extra``` block (one path per line;
   release 071, e.g. the analysis script of experiment B2). A plan without that block
   freezes exactly the six files below, as in release 067.
2. Writes the SHA-256 of the plan itself into every campaign config that
   names it under "preregistration".

evicon_review.py campaign refuses to run a config whose plan or frozen files
changed afterwards. Re-freezing after a run started is a deviation: record it in
the plan's deviation section and in the result report.

  python3 scripts/freeze_prereg.py evaluation/prereg/PREREG_expA_confirm_067.md
  python3 scripts/freeze_prereg.py --check <plan>   # exit 1 if freezing would change anything (no writes)
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

FROZEN = [
    "evaluation/analyze.py",
    "evaluation/power.py",
    "evaluation/sim_to_real.py",
    "evaluation/campaign.py",
    "experiments/sim/iot_dosing_testbed.json",
    "evaluation/campaigns/expA_dosing.json",
]
# Not frozen: the Go code. The runtime and the simulator twin are built from the release that runs the
# campaign (its source fingerprint is in the run's source.json); a code change after freezing needs a
# new release and is reported as a deviation.


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main(argv):
    check = "--check" in argv
    argv = [a for a in argv if a != "--check"]
    if len(argv) != 1:
        raise SystemExit(__doc__)
    stale = []
    plan = (REPO / argv[0]).resolve()
    rel = plan.relative_to(REPO).as_posix()
    text = plan.read_text()
    extra = re.search(r"```frozen-extra\n(.*?)```", text, flags=re.S)
    files = FROZEN + ([ln.strip() for ln in extra.group(1).splitlines() if ln.strip()] if extra else [])
    missing = [f for f in files if not (REPO / f).is_file()]
    if missing:
        raise SystemExit(f"{rel}: files to freeze not found: {missing}")
    block = "\n".join(f"{sha256(REPO / f)}  {f}" for f in files) + "\n"
    new, n = re.subn(r"```frozen-files\n.*?```", "```frozen-files\n" + block + "```", text, flags=re.S)
    if n != 1:
        raise SystemExit(f"{rel}: exactly one ```frozen-files``` block expected, found {n}")
    if new != text:  # unchanged files are not rewritten
        stale.append(rel)
        if not check:
            plan.write_text(new)
    digest = sha256(plan) if not check else hashlib.sha256(new.encode()).hexdigest()
    configs = []
    for cfg_path in sorted((REPO / "evaluation" / "campaigns").glob("*.json")):
        cfg = json.loads(cfg_path.read_text())
        pre = cfg.get("preregistration")
        if isinstance(pre, dict) and pre.get("path") == rel:
            pre["sha256"] = digest
            out = json.dumps(cfg, indent=2) + "\n"
            if out != cfg_path.read_text():
                stale.append(cfg_path.name)
                if not check:
                    cfg_path.write_text(out)
            configs.append(cfg_path.name)
    if not configs:
        raise SystemExit(f"no campaign config names {rel} under 'preregistration'")
    if check and stale:
        raise SystemExit(f"not frozen: {', '.join(stale)} would change (run scripts/freeze_prereg.py {rel})")
    print(f"{rel}: SHA-256 {digest}; {len(files)} frozen files; configs: {', '.join(configs)}"
          + (" (check: frozen)" if check else ""))


if __name__ == "__main__":
    main(sys.argv[1:])
