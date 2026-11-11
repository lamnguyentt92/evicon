#!/usr/bin/env python3
"""Runner of the release-087 MinIO campaign (PREREG_extra_087.md item A, run SHRm6: the documented shadow reconciliation
loop of release 086 on MinIO's conditional PUT, against EVICON in the same campaign).

evaluation/campaign_real.py (frozen by the 073 and 077 plans) with the policies of releases 077 to 087 installed in the
campaign module it imports (evaluation/ext087.py: SHADOW_RECONCILE and SHADOW_RECONCILE_1S read the owner's receipt, the
S3 gateway's receipt object, as their reported state, and re-publish under the same identity, which the conditional PUT
suppresses); everything else is campaign_real.py's.

    python3 evaluation/campaign_real_ext087.py --config <derived config> --out <dir>   (as campaign_real.py)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ext083  # noqa: E402
import ext087  # noqa: E402

RUNNER = "evaluation/campaign_real_ext087.py (on evaluation/campaign_real.py)"


def main(argv=None):
    ext087.install()
    import campaign_real as CR  # noqa: E402  (frozen; imported, never edited)
    args = list(sys.argv[1:] if argv is None else argv)
    cfg = {}
    if "--config" in args:
        cfg = json.loads(Path(args[args.index("--config") + 1]).read_text())
    if "--resume" in args:
        cfg = json.loads((Path(args[args.index("--resume") + 1]) / "config.json").read_text())
    ext083.add_policies(cfg)
    orig = CR.run_campaign

    def run_campaign(cfg, out, bin_dir, max_cells=0):
        try:
            return orig(cfg, out, bin_dir, max_cells)
        finally:
            man = Path(out) / "manifest.json"
            if man.exists():
                m = json.loads(man.read_text())
                if not str(m.get("runner", "")).startswith(RUNNER):
                    m["runner"] = RUNNER
                man.write_text(json.dumps(m, indent=2) + "\n")
    CR.run_campaign = run_campaign
    return CR.main(args)


if __name__ == "__main__":
    sys.exit(main())
