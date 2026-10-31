#!/usr/bin/env python3
"""Runner of the release-084 firmware campaigns (PREREG_extra_084.md (c): PERIODIC_FULL on the cutoff firmware).

evaluation/campaign_zephyr_ext077.py (frozen by PREREG_review_077.md; on the C3 and C2 runners, frozen) with the 077, 083
and 084 policies installed in the campaign module those runners import (evaluation/ext084.py), and the delivery binding
rule of release 084 checked before any cell (conformance/reference/delivery_binding.py): every contract whose binding is
"auto" must get from the frozen 077 gateway exactly the QoS the explicit rule gives (QoS 1 only to an owner whose adapter
declares structural suppression or offers T with e + epsilon <= h; QoS 0 otherwise); a contract for which they differ is
refused before the first cell. Everything else is campaign_zephyr_ext077.py's.

    python3 evaluation/campaign_zephyr_ext084.py --config <derived config> --out <dir>   (as campaign_zephyr_ext077.py)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "evaluation"))
sys.path.insert(0, str(REPO / "conformance"))
import ext083  # noqa: E402
import ext084  # noqa: E402
from reference import delivery_binding as DB  # noqa: E402


def check_binding(cfg: dict) -> list:
    """The binding rule against the 077 gateway's auto choice, per contract of a firmware config (problems; [] = OK)."""
    problems = []
    for name, spec in ((cfg.get("real_owner") or {}).get("contracts") or {}).items():
        if spec.get("binding", "auto") != "auto":
            continue  # an explicit qos0/qos1 is a call shape the plan chose (e.g. run Q's negative control)
        path = Path(spec.get("contract_file", ""))
        if not path.is_absolute():
            path = REPO / path
        if not path.is_file():
            problems.append(f"{name}: contract file {spec.get('contract_file')} not found")
            continue
        problems += DB.check_contract(name, spec.get("mode", ""), json.loads(path.read_text()), repo=REPO)
    return problems


def main(argv=None):
    ext084.install()
    import campaign_zephyr_ext077 as Z77  # noqa: E402  (frozen; imported, never edited)
    args = list(sys.argv[1:] if argv is None else argv)
    cfg = {}
    if "--config" in args:
        cfg = json.loads(Path(args[args.index("--config") + 1]).read_text())
    if "--resume" in args:
        cfg = json.loads((Path(args[args.index("--resume") + 1]) / "config.json").read_text())
    ext083.add_policies(cfg)
    bad = check_binding(cfg)
    if bad:
        raise SystemExit("delivery binding rule (release 084) refuses this campaign: " + "; ".join(bad))
    return Z77.main(args)


if __name__ == "__main__":
    sys.exit(main())
