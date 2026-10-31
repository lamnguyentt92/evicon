#!/usr/bin/env python3
"""Runner of the release-084 MinIO campaigns (PREREG_extra_084.md (c): PERIODIC_FULL on MinIO's conditional PUT).

evaluation/campaign_real.py (frozen by the 073 and 077 plans) with the 077, 083 and 084 policies installed in the
campaign module it imports (evaluation/ext084.py); everything else is campaign_real.py's.

    python3 evaluation/campaign_real_ext084.py --config <derived config> --out <dir>   (as campaign_real.py)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ext083  # noqa: E402
import ext084  # noqa: E402


def main(argv=None):
    ext084.install()
    import campaign_real as CR  # noqa: E402  (frozen; imported, never edited)
    args = list(sys.argv[1:] if argv is None else argv)
    if "--config" in args:
        ext083.add_policies(json.loads(Path(args[args.index("--config") + 1]).read_text()))
    if "--resume" in args:
        ext083.add_policies(json.loads((Path(args[args.index("--resume") + 1]) / "config.json").read_text()))
    return CR.main(args)


if __name__ == "__main__":
    sys.exit(main())
