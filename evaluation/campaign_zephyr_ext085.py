#!/usr/bin/env python3
"""Runner of the release-085 firmware campaigns (PREREG_extra_085.md: EVICON and PERIODIC_FULL in the same campaign on the
cutoff firmware, the matched comparison of review M5a).

evaluation/campaign_zephyr_ext084.py (frozen by PREREG_extra_084.md: the 077, 083 and 084 policies, the delivery binding
rule checked before any cell, on the frozen campaign_zephyr_ext077.py) with the config key policy_order of
evaluation/ext085.py: the C2 runner's frozen run_campaign (campaign_zephyr.py) with its policy loop in the order of
ext085.policy_order, compiled into its own module before the 084 runner starts it. Everything else is the 084 runner's.

    python3 evaluation/campaign_zephyr_ext085.py --config <derived config> --out <dir>   (as campaign_zephyr_ext084.py)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "evaluation"))
sys.path.insert(0, str(REPO / "conformance"))
import ext085  # noqa: E402

RUNNER = "evaluation/campaign_zephyr_ext085.py (on campaign_zephyr_ext084.py, campaign_zephyr_ext077.py, campaign_zephyr_ext.py, campaign_zephyr.py)"


def _config(args):
    if "--config" in args:
        return json.loads(Path(args[args.index("--config") + 1]).read_text())
    if "--resume" in args:
        return json.loads((Path(args[args.index("--resume") + 1]) / "config.json").read_text())
    return {}


def main(argv=None):
    ext085.install()
    import campaign_zephyr as Z  # noqa: E402  (frozen by the C2 plan; imported, never edited)
    import campaign_zephyr_ext077 as Z77  # noqa: E402  (frozen by PREREG_review_077.md; imported, never edited)
    import campaign_zephyr_ext084 as Z84  # noqa: E402  (frozen by PREREG_extra_084.md; imported, never edited)
    args = list(sys.argv[1:] if argv is None else argv)
    cfg = _config(args)
    ext085.check_config(cfg)
    ext085.install_order(Z)
    mark077 = Z77._mark_manifest

    def mark(out):  # the 077 runner marks the manifest last (its wrapper is the outermost): then this runner's record
        mark077(out)
        ext085.mark_manifest(Path(out), RUNNER, cfg)
    Z77._mark_manifest = mark
    return Z84.main(args)


if __name__ == "__main__":
    sys.exit(main())
