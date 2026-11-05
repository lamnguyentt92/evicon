#!/usr/bin/env python3
"""Runner of the release-086 emulated-owner campaigns (PREREG_extra_086.md, runs SHq and SHd: SHADOW_RECONCILE against
EVICON on the schedules of the state-read release baselines).

evaluation/campaign.py (frozen) runs EviCon authorities against evicon-device owners; campaign_ext077.py, ext083.py and
ext084.py (frozen) add the policies of releases 077, 083 and 084 and the exclude_cells key. This runner installs them and
the 086 policy (evaluation/ext086.py), then calls campaign.py's main: cells, schedules, faults, truth, rows and INVALID
rules are campaign.py's.

    python3 evaluation/campaign_ext086.py --config <config> --out <dir>      (as campaign.py)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ext083  # noqa: E402
import ext084  # noqa: E402
import ext086  # noqa: E402


def _config(args):
    if "--config" in args:
        return json.loads(Path(args[args.index("--config") + 1]).read_text())
    if "--resume" in args:
        return json.loads((Path(args[args.index("--resume") + 1]) / "config.json").read_text())
    return {}


def main(argv=None):
    C = ext086.install()
    args = list(sys.argv[1:] if argv is None else argv)
    cfg = _config(args)
    ext083.add_policies(cfg)
    ext084.install_exclusion(cfg)
    return C.main(args)


if __name__ == "__main__":
    sys.exit(main())
