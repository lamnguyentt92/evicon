"""The authority policy of release 086 (PREREG_extra_086.md, rules SH), for the emulated-owner runner.

evaluation/campaign.py (frozen) maps a campaign policy to evicon-authority arguments in its policy_args function and
validates policies against its RESOLVERS + LEGACY lists; campaign_ext077.py, ext083.py and ext084.py (frozen) add the
policies of releases 077, 083 and 084 to that one place. This module adds release 086's policy on top of them
(ext084.install), without editing any frozen file:

  SHADOW_RECONCILE  the documented device-shadow reconciliation loop (reviewer comment M3; quoted rules in
                    internal/runtime/shadow_reconcile.go): exactly the arguments of the RELEASE_ON_READ baseline of
                    release 077 (-release-after <authority.release_after>, -resolve-sweep, -resend-until when the
                    config sets authority.resend_until) with -policy SHADOW_RECONCILE: the reported state of every
                    unresolved command is read release_after after its creation and every release_after after that;
                    a command it does not show is published again under its own identity; nothing is released.

It is a practice baseline that commits only the owner's reports and resends only the same identity, so it is safe
where the owner deduplicates every identity without a horizon (the plan runs it on K2 only); it is not in the
unsafe-by-design lists. Every other policy keeps its arguments byte for byte (test_release086).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import campaign as C  # noqa: E402  (frozen; imported, never edited)
import ext084  # noqa: E402  (frozen by PREREG_extra_085.md; imported, never edited)

SHADOW_RECONCILE = "SHADOW_RECONCILE"
POLICIES = (SHADOW_RECONCILE,)


def is_086_policy(p: str) -> bool:
    return p in POLICIES


def policy_args_086(policy: str, a: dict, prev):
    if policy == SHADOW_RECONCILE:
        args = list(prev("RELEASE_ON_READ", a))
        args[args.index("-policy") + 1] = SHADOW_RECONCILE
        return args
    return prev(policy, a)


_INSTALLED = False


def install():
    """ext084.install() (the 077, 083 and 084 policies and exclude_cells), then the 086 policy (idempotent).
    Returns the campaign module."""
    global _INSTALLED
    if _INSTALLED:
        return C
    ext084.install()
    prev = C.policy_args

    def policy_args(policy, a):
        return policy_args_086(policy, a, prev)
    for p in POLICIES:
        if p not in C.LEGACY:
            C.LEGACY = tuple(C.LEGACY) + (p,)
    C.policy_args = policy_args
    _INSTALLED = True
    return C
