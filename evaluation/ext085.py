"""The runner option of release 085 (PREREG_extra_085.md): the order of the policies within an episode.

evaluation/campaign.py, campaign_real.py and campaign_zephyr.py (frozen) run every episode's cells in the order
contracts x policies of the config. The matched comparison of release 085 (EVICON and PERIODIC_FULL in one campaign)
alternates the order by episode, so that neither policy always runs first after the owner starts (warm caches, the
owner's state after the previous cell):

  policy_order   "config" (default, the order of releases <= 084) or "alternate_by_episode": episode 0, 2, 4, ... in the
                 config's order of "policies", episodes 1, 3, 5, ... in the reverse order

A runner's frozen run_campaign is used unchanged except for that one line: its source with the policy loop over
_policy_order_085(cfg, ep) instead of cfg["policies"], compiled into the runner's own module at install
(install_order). The cells, their keys, schedules, faults, truth, rows, the resume rule (a cell is skipped by its key)
and the INVALID rules stay the runner's. Without the key every runner behaves as before (test_release085 K4). The
policies are those of releases 077-084 (ext084.install).
"""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import campaign as C  # noqa: E402  (frozen; imported, never edited)
import ext084  # noqa: E402  (frozen by PREREG_extra_084.md; imported, never edited)

KEY = "policy_order"
CONFIG_ORDER = "config"
ALTERNATE = "alternate_by_episode"
ORDERS = (CONFIG_ORDER, ALTERNATE)
LOOP = '            for policy in cfg["policies"]:\n'
LOOP_085 = '            for policy in _policy_order_085(cfg, ep):  # release 085: policy_order (evaluation/ext085.py)\n'


def policy_order(cfg: dict, ep: int) -> list:
    """The policies of episode ep in the order the config asks for."""
    order = cfg.get(KEY, CONFIG_ORDER)
    if order not in ORDERS:
        raise ValueError(f"{KEY}: {order!r}, expected one of {ORDERS}")
    pols = list(cfg["policies"])
    return pols[::-1] if order == ALTERNATE and ep % 2 == 1 else pols


def ordered_source(module) -> str:
    """The runner's frozen run_campaign source with the policy loop of policy_order (the only change)."""
    src = inspect.getsource(module.run_campaign)
    if src.count(LOOP) != 1:
        raise RuntimeError(f"{module.__name__}.run_campaign changed: the policy_order line cannot be placed")
    return src.replace(LOOP, LOOP_085)


def install_order(module):
    """Compiles the ordered run_campaign into the runner module (idempotent)."""
    if getattr(module, "_RUN_CAMPAIGN_085", False):
        return module
    src = ordered_source(module)
    module._policy_order_085 = policy_order
    exec(compile(src, module.__file__, "exec"), module.__dict__)
    module._RUN_CAMPAIGN_085 = True
    return module


def check_config(cfg: dict) -> None:
    order = cfg.get(KEY, CONFIG_ORDER)
    if order not in ORDERS:
        raise ValueError(f"{KEY}: {order!r}, expected one of {ORDERS}")


def mark_manifest(out: Path, runner: str, cfg: dict) -> None:
    """Records the runner and the policy order of every episode in the campaign's manifest."""
    man = out / "manifest.json"
    if man.exists():
        m = json.loads(man.read_text())
        m["runner"] = runner
        m[KEY] = {"rule": cfg.get(KEY, CONFIG_ORDER),
                  "episodes": {str(ep): policy_order(cfg, ep) for ep in range(cfg.get("episodes", 1))}}
        man.write_text(json.dumps(m, indent=2) + "\n")


_INSTALLED = False


def install():
    """ext084.install() (the policies of 077-084), then the policy_order key of the campaign format (idempotent)."""
    global _INSTALLED
    ext084.install()
    if not _INSTALLED:
        C.CONFIG_KEYS = set(C.CONFIG_KEYS) | {KEY}
        _INSTALLED = True
    return C
