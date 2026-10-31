#!/usr/bin/env python3
"""Runner of the release-084 emulated-owner campaigns (PREREG_extra_084.md (a): EVICON_FI against EVICON and the stop on
the stop experiment's orphan schedules).

evaluation/campaign.py (frozen) runs EviCon authorities against evicon-device owners; campaign_ext077.py and ext083.py
(frozen) add the policies of releases 077 and 083. This runner installs them and the 084 policies and the exclude_cells key
(evaluation/ext084.py), then calls campaign.py's main: cells, schedules, faults, truth, rows and INVALID rules are
campaign.py's.

    python3 evaluation/campaign_ext084.py --config <config> --out <dir>      (as campaign.py)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ext083  # noqa: E402
import ext084  # noqa: E402


def _config(args):
    if "--config" in args:
        return json.loads(Path(args[args.index("--config") + 1]).read_text())
    if "--resume" in args:
        return json.loads((Path(args[args.index("--resume") + 1]) / "config.json").read_text())
    return {}


def main(argv=None):
    C = ext084.install()
    args = list(sys.argv[1:] if argv is None else argv)
    cfg = _config(args)
    ext083.add_policies(cfg)
    ext084.install_exclusion(cfg)
    return C.main(args)


if __name__ == "__main__":
    sys.exit(main())
