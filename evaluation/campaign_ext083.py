#!/usr/bin/env python3
"""Runner of the release-083 emulated-owner campaigns that need a policy of release 083 (PREREG_extra_083.md):
M1W (T1), RELEASE_ON_READ_ANSWERED (T2), RELEASE_ON_TIMEOUT_<n>S (T3), EVICON_GREEDY and EVICON_FC<x> (T5).

evaluation/campaign.py (frozen) runs EviCon authorities against evicon-device owners; campaign_ext077.py (frozen by
PREREG_review_077.md) adds release 077's baselines and the validity cut of resends. This runner installs both and
the 083 policies (evaluation/ext083.py), then calls campaign.py's main: cells, schedules, faults, truth, rows and
INVALID rules are campaign.py's.

    python3 evaluation/campaign_ext083.py --config <config> --out <dir>      (as campaign.py)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ext083  # noqa: E402


def main(argv=None):
    C = ext083.install()
    args = list(sys.argv[1:] if argv is None else argv)
    if "--config" in args:
        ext083.add_policies(json.loads(Path(args[args.index("--config") + 1]).read_text()))
    if "--resume" in args:
        ext083.add_policies(json.loads((Path(args[args.index("--resume") + 1]) / "config.json").read_text()))
    return C.main(args)


if __name__ == "__main__":
    sys.exit(main())
