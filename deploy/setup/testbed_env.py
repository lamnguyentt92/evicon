#!/usr/bin/env python3
"""Release 078: testbed.json -> shell variables for the setup scripts (setup_all.sh, build_iot.sh,
testbed_init.sh), with the driver's own checks (evicon_review.check_testbed_cfg): the file must exist,
the five addresses must be IPv4 and distinct, and VM-C's must be one of this machine's own. A testbed.json
copied from an earlier installation therefore stops the setup instead of configuring firewalls and
firmware for addresses that are gone.

    eval "$(python3 deploy/setup/testbed_env.py)"     # VM_A=... VM_B=... VM_C=... VM_D=... VM_E=... SSH_USER=...
    python3 deploy/setup/testbed_env.py --get vm_b     # one value
"""
from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
import evicon_review as ER  # noqa: E402  (the driver: one definition of the checks)

KEYS = ("vm_a", "vm_b", "vm_c", "vm_d", "vm_e", "ssh_user", "state_dir", "temporal_address", "remote_root")


def load(path: Path, check_local: bool = True) -> dict:
    if not path.exists():
        raise SystemExit(f"{path} missing: write it from the VMs' current addresses first:\n"
                         f"  bash deploy/setup/testbed_init.sh detect\n"
                         f"  bash deploy/setup/testbed_init.sh write --vm-a <IP> --vm-b <IP> --vm-d <IP> --vm-e <IP>")
    try:
        cfg = json.loads(path.read_text())
    except ValueError as e:
        raise SystemExit(f"{path}: not JSON ({e})")
    unknown = set(cfg) - set(ER.DEFAULT_TESTBED)
    if unknown:
        raise SystemExit(f"{path}: unknown keys {sorted(unknown)} (allowed: {sorted(ER.DEFAULT_TESTBED)})")
    cfg = dict(ER.DEFAULT_TESTBED, **cfg)
    ER.check_testbed_cfg(cfg, str(path), None if check_local else {cfg["vm_c"]})
    return cfg


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", default=str(REPO / "testbed.json"))
    ap.add_argument("--get", choices=KEYS)
    ap.add_argument("--no-local-check", action="store_true", help="do not require vm_c to be an address of this machine")
    a = ap.parse_args(argv)
    cfg = load(Path(a.file), not a.no_local_check)
    if a.get:
        print(cfg[a.get])
        return 0
    for k in KEYS:
        print(f"{k.upper()}={shlex.quote(str(cfg.get(k) or ''))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
