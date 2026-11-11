"""The authority policies of release 087 (PREREG_extra_087.md, items A and B), for every runner.

evaluation/campaign.py (frozen) maps a campaign policy to evicon-authority arguments in its policy_args function and
validates policies against its RESOLVERS + LEGACY lists; campaign_ext077.py, ext083.py, ext084.py and ext086.py (frozen)
add the policies of releases 077 to 086 to that one place. This module adds release 087's policies on top of them
(ext086.install), without editing any frozen file:

  SHADOW_RECONCILE_1S  item A (the cadence confound, reviewer finding F3-06): the documented shadow reconciliation loop of
                       release 086 (SHADOW_RECONCILE) with its reconciliation timeout at EviCon's sweep cadence: exactly
                       SHADOW_RECONCILE's arguments with -release-after 1s instead of authority.release_after (10 s)
  EVICON_FI_REC        item B, arm (iii): EVICON_FI (EVICON with the per-identity fence intake R5') plus the reboot
                       recovery of release 087 (-reboot-recovery: steps REC-1 to REC-4, internal/runtime/reboot_recovery.go)
  EVICON_FI_REC_OS     item B, arm (i), the negative control: EVICON_FI_REC with the anchor of releases 061-085
                       (-fence-anchor owner-state): after an owner reboot a complete fence adopts the reset level

SHADOW_RECONCILE_1S is a practice baseline as SHADOW_RECONCILE (it commits only the owner's reports and resends only the
same identity; registered with the legacy policies, as SHADOW_RECONCILE); EVICON_FI_REC and EVICON_FI_REC_OS are guarded
policies (the relational guard and owner evidence only). The negative control is planned unsafe through its contract
label (NEG_ZEPHYR_F_BOOT, safety_total.kind: control), not through the policy. Every other policy keeps its arguments byte
for byte (test_release087).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import campaign as C  # noqa: E402  (frozen; imported, never edited)
import ext086  # noqa: E402  (frozen by PREREG_extra_086.md; imported, never edited)

SHADOW_RECONCILE_1S = "SHADOW_RECONCILE_1S"
EVICON_FI_REC = "EVICON_FI_REC"
EVICON_FI_REC_OS = "EVICON_FI_REC_OS"
SHADOW_POLICIES = (SHADOW_RECONCILE_1S,)
GUARDED_POLICIES = (EVICON_FI_REC, EVICON_FI_REC_OS)
POLICIES = SHADOW_POLICIES + GUARDED_POLICIES
SHADOW_1S_RELEASE_AFTER = "1s"


def is_087_policy(p: str) -> bool:
    return p in POLICIES


def policy_args_087(policy: str, a: dict, prev):
    if policy == SHADOW_RECONCILE_1S:
        args = list(prev(ext086.SHADOW_RECONCILE, a))
        args[args.index("-release-after") + 1] = SHADOW_1S_RELEASE_AFTER
        return args
    if policy == EVICON_FI_REC:
        return list(prev("EVICON_FI", a)) + ["-reboot-recovery"]
    if policy == EVICON_FI_REC_OS:
        return list(prev("EVICON_FI", a)) + ["-reboot-recovery", "-fence-anchor", "owner-state"]
    return prev(policy, a)


_INSTALLED = False


def install():
    """ext086.install() (the 077, 083, 084 and 086 policies and exclude_cells), then the 087 policies (idempotent).
    Returns the campaign module."""
    global _INSTALLED
    if _INSTALLED:
        return C
    ext086.install()
    prev = C.policy_args

    def policy_args(policy, a):
        return policy_args_087(policy, a, prev)
    for p in SHADOW_POLICIES:
        if p not in C.LEGACY:
            C.LEGACY = tuple(C.LEGACY) + (p,)
    for p in GUARDED_POLICIES:
        if p not in C.RESOLVERS:
            C.RESOLVERS = tuple(C.RESOLVERS) + (p,)
    C.policy_args = policy_args
    _INSTALLED = True
    return C
