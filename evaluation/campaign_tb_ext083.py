#!/usr/bin/env python3
"""Runner of the release-083 ThingsBoard campaigns (PREREG_extra_083.md T1: M1W next to EVICON and M1; T6: the
window sweep with EVICON and M1W).

evaluation/campaign_tb.py (frozen by PREREG_review_077.md) with the 083 policies of evaluation/ext083.py installed
in the campaign module it imports; everything else (ThingsBoard devices per cell, the gateways, the reference
MQTT devices, truth) is campaign_tb.py's.

    python3 evaluation/campaign_tb_ext083.py --config <derived config> --out <dir>     (as campaign_tb.py)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ext083  # noqa: E402


def main(argv=None):
    ext083.install()
    import campaign_tb as TBR  # noqa: E402  (frozen; imported, never edited)
    args = list(sys.argv[1:] if argv is None else argv)
    if "--config" in args:
        ext083.add_policies(json.loads(Path(args[args.index("--config") + 1]).read_text()))
    if "--resume" in args:
        ext083.add_policies(json.loads((Path(args[args.index("--resume") + 1]) / "config.json").read_text()))
    return TBR.main(args)


if __name__ == "__main__":
    sys.exit(main())
