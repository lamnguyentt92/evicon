#!/usr/bin/env python3
"""Runner of the release-077 emulated-owner campaigns that need it (PREREG_review_077.md): the baselines
expRT_queue, expRT_direct, expRR_queue, expRR_direct, and the configs with the validity cut of resends
(expK2_w6, expL2_sleepy).

evaluation/campaign.py (frozen by the plans of releases 067-075) runs EviCon authorities against
evicon-device owners. This runner imports it unchanged and adds two policies, both UNSAFE BY DESIGN:

  RELEASE_ON_TIMEOUT  (evicon-authority -policy RELEASE_ON_TIMEOUT -release-after <authority.release_after>)
                      a command unresolved release_after after its creation is committed STOPPED without any
                      owner evidence, and the client re-issues the intent, as a client that treats a timeout as
                      a failure would;
  RELEASE_ON_READ     (-policy RELEASE_ON_READ -release-after ...) state-read reconciliation: release_after after
                      its creation, a command without terminal evidence is looked up at the owner; an APPLIED or
                      STOPPED receipt is recorded, a command the owner does not report (NOT_FOUND, or the owner
                      cannot be reached) is committed STOPPED, as a client reading a device shadow would.

Both break rule R2 of the EviCon protocol on purpose: their safety violations are the measured quantity of
the baselines.

It also passes the release-077 validity cut of the T4 retry guard to every authority of a config that sets
authority.resend_until (a duration): a command is resent under its own identity only until that long after its
creation (evicon-authority -resend-until). The authority knows a command's creation, not its intent's arrival; the
client submits an intent no later than its deadline, so the 077 configs set validity_s - deadline_s, and no resend
is ever sent after the intent's validity.

Everything else (cells, schedules, faults, truth, rows, INVALID rules) is campaign.py's: this module only extends
its LEGACY policy list and its policy_args function, then calls its main.

    python3 evaluation/campaign_ext077.py --config <config> --out <dir>      (as campaign.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "evaluation"))
import campaign as C  # noqa: E402  (frozen by the 067-075 plans; imported, never edited)

RELEASE_ON_TIMEOUT = "RELEASE_ON_TIMEOUT"
RELEASE_ON_READ = "RELEASE_ON_READ"
BASELINES = (RELEASE_ON_TIMEOUT, RELEASE_ON_READ)
FROZEN_POLICY_ARGS = C.policy_args


def policy_args(policy, a):
    if policy in BASELINES:
        args = ["-policy", policy, "-release-after", a.get("release_after", "10s"),
                "-resolve-sweep", a.get("resolve_sweep", "1s")]
    else:
        args = FROZEN_POLICY_ARGS(policy, a)
    if a.get("resend_until"):
        args = args + ["-resend-until", str(a["resend_until"])]
    return args


def install():
    for p in BASELINES:
        if p not in C.LEGACY:
            C.LEGACY = tuple(C.LEGACY) + (p,)
    C.policy_args = policy_args


if __name__ == "__main__":
    install()
    sys.exit(C.main())
