"""The authority policies of release 084 (PREREG_extra_084.md), for every runner.

evaluation/campaign.py (frozen by the 067-083 plans) maps a campaign policy to evicon-authority arguments in its
policy_args function, validates policies against its RESOLVERS + LEGACY lists and runs every cell of
contracts x policies; the real-owner runners (campaign_real.py, campaign_zephyr*.py, frozen) call the same module.
This module adds release 084's policies to that one place, on top of releases 077 and 083 (ext083.install), without
editing any frozen file:

  EVICON_FI       EVICON with the per-identity intake of a complete fence answer (rule R5', review M3):
                  EVICON's arguments + -fence-intake per-identity
  PERIODIC_FULL   the full periodic reconciliation baseline (review M5): EVICON's arguments with
                  -resolve-trigger PERIODIC_FULL (every sweep, at EVICON's sweep cadence authority.resolve_sweep: a
                  query of each aged unresolved command, then a stop or a fence under EVICON's threshold rule;
                  no certificate search, no window or block trigger; internal/runtime/periodic_full.go)

and one config key the emulated-owner runner lacks:

  exclude_cells   [[contract, policy], ...]: cells of contracts x policies that are not run (as the firmware runner of
                  release 074 does), e.g. K0S/EVICON_FI, which would only repeat K0S/EVICON (a stop-only owner has no
                  fence). campaign.run_campaign is run unchanged except for that one skip: its frozen source with
                  one added line, compiled into the campaign module at install (install_exclusion).

Both policies are guarded (the same relational guard and owner evidence only) and safe by construction; neither is
a baseline that is unsafe by design. Every other policy keeps the arguments of campaign.py / campaign_ext077.py /
ext083.py byte for byte (test_release084 J3).
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import campaign as C  # noqa: E402  (frozen; imported, never edited)
import ext083  # noqa: E402  (frozen by PREREG_extra_083.md; imported, never edited)

EVICON_FI = "EVICON_FI"
PERIODIC_FULL = "PERIODIC_FULL"
POLICIES = (EVICON_FI, PERIODIC_FULL)


def is_084_policy(p: str) -> bool:
    return p in POLICIES


def policy_args_084(policy: str, a: dict, prev):
    if policy == EVICON_FI:
        return prev("EVICON", a) + ["-fence-intake", "per-identity"]
    if policy == PERIODIC_FULL:
        args = list(prev("EVICON", a))
        i = args.index("-resolve-trigger")
        args[i + 1] = "PERIODIC_FULL"
        return args
    return prev(policy, a)


EXCLUDE_ANCHOR = "                cell = Cell(ep, contract, policy)\n"
EXCLUDE_LINES = ("                if (contract, policy) in _EXCLUDED_084:  # release 084: exclude_cells (evaluation/ext084.py)\n"
                 "                    continue\n")


def excluded_run_campaign_source() -> str:
    """campaign.run_campaign's frozen source with the exclude_cells skip before each cell (the only change)."""
    src = inspect.getsource(C.run_campaign)
    if src.count(EXCLUDE_ANCHOR) != 1:
        raise RuntimeError("campaign.run_campaign changed: the exclude_cells line cannot be placed")
    return src.replace(EXCLUDE_ANCHOR, EXCLUDE_LINES + EXCLUDE_ANCHOR)


def install_exclusion(cfg: dict):
    """Sets the excluded cells of cfg and runs campaign.run_campaign with the skip (idempotent)."""
    excl = cfg.get("exclude_cells", [])
    for x in excl:
        if not (isinstance(x, list) and len(x) == 2 and x[0] in cfg.get("contracts", []) and x[1] in cfg.get("policies", [])):
            raise ValueError(f"exclude_cells: {x!r} is not a [contract, policy] pair of this config")
    C._EXCLUDED_084 = {tuple(x) for x in excl}
    if not getattr(C, "_RUN_CAMPAIGN_084", False):
        exec(compile(excluded_run_campaign_source(), C.__file__, "exec"), C.__dict__)
        C._RUN_CAMPAIGN_084 = True


_INSTALLED = False


def install():
    """ext083.install() (077 and 083 policies), then the 084 policies and the exclude_cells key (idempotent).
    Returns the campaign module."""
    global _INSTALLED
    if _INSTALLED:
        return C
    ext083.install()
    prev = C.policy_args

    def policy_args(policy, a):
        return policy_args_084(policy, a, prev)
    for p in POLICIES:
        if p not in C.RESOLVERS:
            C.RESOLVERS = tuple(C.RESOLVERS) + (p,)
    C.CONFIG_KEYS = set(C.CONFIG_KEYS) | {"exclude_cells"}
    C._EXCLUDED_084 = set()
    C.policy_args = policy_args
    _INSTALLED = True
    return C
