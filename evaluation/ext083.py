"""The authority policies of release 083 (PREREG_extra_083.md), for every runner.

evaluation/campaign.py (frozen by the 067-077 plans) maps a campaign policy to evicon-authority arguments in
its policy_args function and validates policies against its RESOLVERS + LEGACY lists; the real-owner runners
(campaign_real.py, campaign_tb.py, campaign_zephyr*.py, frozen) call the same module's authority_args. This
module adds the 083 policies to that one place, on top of release 077's (campaign_ext077.install: the two
practice baselines and the validity cut of resends), without editing any frozen file:

  M1W                         QueryOnly with the window-full trigger (T1): -policy M1W -ambiguity-after
  RELEASE_ON_READ_ANSWERED    releases only on an answered NOT_FOUND (T2): -policy RELEASE_ON_READ_ANSWERED
                              -release-after <authority.release_after, 10s> -release-settle
                              <authority.release_settle, 7s> -resolve-sweep; UNSAFE BY DESIGN
  RELEASE_ON_TIMEOUT_<n>S     RELEASE_ON_TIMEOUT with -release-after <n>s (T3); UNSAFE BY DESIGN
  EVICON_GREEDY               EVICON with the greedy certificate (T5): EVICON's arguments + -certificate greedy
  EVICON_FC<x>                EVICON with the fence cost x instead of authority.fence_cost (T5, the theta point:
                              theta = kappa_F / (lambda sum |v|))

Every other policy keeps the arguments of campaign.py / campaign_ext077.py byte for byte. authority.resend_until
(release 077) is passed to every policy, as campaign_ext077 does.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import campaign as C  # noqa: E402  (frozen; imported, never edited)
import campaign_ext077 as E77  # noqa: E402  (frozen by PREREG_review_077.md; imported, never edited)

M1W = "M1W"
RRA = "RELEASE_ON_READ_ANSWERED"
EVICON_GREEDY = "EVICON_GREEDY"
RT_VARIANT = re.compile(r"^RELEASE_ON_TIMEOUT_(\d+)S$")
FC_VARIANT = re.compile(r"^EVICON_FC(\d+(?:\.\d+)?)$")
# the policies of the 083 configs (the RT and FC families are open: any release time / fence cost)
POLICIES = (M1W, RRA, EVICON_GREEDY, "RELEASE_ON_TIMEOUT_30S", "RELEASE_ON_TIMEOUT_60S", "RELEASE_ON_TIMEOUT_120S", "EVICON_FC100")
# the unsafe-by-design ones (counted apart in the safety total; evaluation/safety_total.py BASELINES)
UNSAFE = (RRA, "RELEASE_ON_TIMEOUT_30S", "RELEASE_ON_TIMEOUT_60S", "RELEASE_ON_TIMEOUT_120S")


def is_083_policy(p: str) -> bool:
    return p in (M1W, RRA, EVICON_GREEDY) or bool(RT_VARIANT.match(p)) or bool(FC_VARIANT.match(p))


def is_unsafe_by_design(p: str) -> bool:
    return p == RRA or bool(RT_VARIANT.match(p))


def policy_args_083(policy: str, a: dict, prev):
    sweep = a.get("resolve_sweep", "1s")
    m = RT_VARIANT.match(policy)
    f = FC_VARIANT.match(policy)
    if policy == M1W:
        args = ["-policy", M1W, "-ambiguity-after", a.get("ambiguity_after", "1s")]
    elif policy == RRA:
        args = ["-policy", RRA, "-release-after", a.get("release_after", "10s"), "-release-settle", a.get("release_settle", "7s"),
                "-resolve-sweep", sweep]
    elif m:
        args = ["-policy", "RELEASE_ON_TIMEOUT", "-release-after", f"{int(m.group(1))}s", "-resolve-sweep", sweep]
    elif policy == EVICON_GREEDY:
        return prev("EVICON", a) + ["-certificate", "greedy"]  # prev appends resend_until itself
    elif f:
        return prev("EVICON", dict(a, fence_cost=float(f.group(1))))
    else:
        return prev(policy, a)
    if a.get("resend_until"):
        args = args + ["-resend-until", str(a["resend_until"])]
    return args


_INSTALLED = False


def install():
    """campaign_ext077.install(), then the 083 policies (idempotent). Returns the campaign module."""
    global _INSTALLED
    if _INSTALLED:
        return C
    E77.install()
    prev = C.policy_args

    def policy_args(policy, a):
        return policy_args_083(policy, a, prev)
    for p in POLICIES:
        if p not in C.LEGACY:
            C.LEGACY = tuple(C.LEGACY) + (p,)
    C.policy_args = policy_args
    _INSTALLED = True
    return C


def add_policies(cfg: dict):
    """Lets an open-family policy of a config (RELEASE_ON_TIMEOUT_<n>S, EVICON_FC<x>) pass campaign.load_config."""
    for p in cfg.get("policies", []):
        if is_083_policy(p) and p not in C.LEGACY:
            C.LEGACY = tuple(C.LEGACY) + (p,)
