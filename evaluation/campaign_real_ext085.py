#!/usr/bin/env python3
"""Runner of the release-085 MinIO campaigns (PREREG_extra_085.md: EVICON and PERIODIC_FULL in the same campaign on MinIO's
conditional PUT, the matched comparison of review M5a).

evaluation/campaign_real_ext084.py (frozen by PREREG_extra_084.md: the 077, 083 and 084 policies on the frozen
campaign_real.py) with the config key policy_order of evaluation/ext085.py: campaign_real.py's frozen run_campaign with its
policy loop in the order of ext085.policy_order, compiled into its own module before the 084 runner starts it. Everything
else is the 084 runner's.

    python3 evaluation/campaign_real_ext085.py --config <derived config> --out <dir>   (as campaign_real_ext084.py)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ext085  # noqa: E402

RUNNER = "evaluation/campaign_real_ext085.py (on campaign_real_ext084.py, campaign_real.py)"


def _config(args):
    if "--config" in args:
        return json.loads(Path(args[args.index("--config") + 1]).read_text())
    if "--resume" in args:
        return json.loads((Path(args[args.index("--resume") + 1]) / "config.json").read_text())
    return {}


def main(argv=None):
    ext085.install()
    import campaign_real as CR  # noqa: E402  (frozen; imported, never edited)
    import campaign_real_ext084 as R84  # noqa: E402  (frozen by PREREG_extra_084.md; imported, never edited)
    args = list(sys.argv[1:] if argv is None else argv)
    cfg = _config(args)
    ext085.check_config(cfg)
    ext085.install_order(CR)
    orig = CR.run_campaign

    def run_campaign(cfg, out, bin_dir, max_cells=0):
        try:
            return orig(cfg, out, bin_dir, max_cells)
        finally:
            ext085.mark_manifest(Path(out), RUNNER, cfg)
    CR.run_campaign = run_campaign
    return R84.main(args)


if __name__ == "__main__":
    sys.exit(main())
