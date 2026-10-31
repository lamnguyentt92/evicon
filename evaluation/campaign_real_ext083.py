#!/usr/bin/env python3
"""Runner of the release-083 MinIO campaign (PREREG_extra_083.md T5: EVICON_GREEDY at an unbounded window).

evaluation/campaign_real.py (frozen by the 073 and 077 plans) with the 083 policies of evaluation/ext083.py
installed in the campaign module it imports; everything else is campaign_real.py's.

    python3 evaluation/campaign_real_ext083.py --config <derived config> --out <dir>   (as campaign_real.py)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ext083  # noqa: E402


def main(argv=None):
    ext083.install()
    import campaign_real as CR  # noqa: E402  (frozen; imported, never edited)
    args = list(sys.argv[1:] if argv is None else argv)
    if "--config" in args:
        ext083.add_policies(json.loads(Path(args[args.index("--config") + 1]).read_text()))
    if "--resume" in args:
        ext083.add_policies(json.loads((Path(args[args.index("--resume") + 1]) / "config.json").read_text()))
    return CR.main(args)


if __name__ == "__main__":
    sys.exit(main())
