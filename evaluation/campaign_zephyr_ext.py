#!/usr/bin/env python3
"""Runner of experiment C3 (release 075): the C2 runner with restricted contracts and two owner channels.

evaluation/campaign_zephyr.py (frozen by the C2 plan) runs the EviCon authorities against the Zephyr EviCon
firmware behind Mosquitto through one MQTT owner gateway per device. This runner imports it unchanged and
adds two things, per contract of the config:

  "restricted_from": "<file>"   the contract_file is a restriction of that MEASURED contract of the
                                firmware mode to a subset of its capabilities (restrict_contract below:
                                every kept capability keeps its measured provenance; provenance
                                ["restricted_from"] names the measured contract and the SHA-256 of
                                its file). A restricted contract is weaker than the measured one, so it
                                is sound wherever the measured one is
  "channel": "queue"|"direct"   the owner channel of the offline windows: queue (C2: the gateway's MQTT
                                connections are reset and paho delivers what was published meanwhile
                                late) or direct (every message either way is lost while offline: a lost
                                command never takes effect). The runner writes it to gateway.channel in
                                the cell's directory, where the extended gateway reads it

The gateway is conformance/reference/mqtt_owner_gateway_ext.py (on the frozen C2 gateway). Everything
else (cells, faults, truth, rows, INVALID rules) is campaign_zephyr.py's: this module replaces its
load_config, ZephyrTestbed and GATEWAY globals and calls its main.

    python3 evaluation/campaign_zephyr_ext.py --config <config> --out <dir>      (as campaign_zephyr.py)
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "evaluation"))
sys.path.insert(0, str(REPO / "conformance"))
import campaign_zephyr as Z  # noqa: E402  (frozen by the C2 plan; imported, never edited)

FROZEN_LOAD_CONFIG = Z.load_config  # main() replaces Z.load_config with load_config below, which calls this one
GATEWAY_EXT = REPO / "conformance" / "reference" / "mqtt_owner_gateway_ext.py"
CHANNELS = ("queue", "direct")
SPEC_KEYS = {"mode", "contract_file", "restricted_from", "channel", "restrict_to", "measured_in"}


def sha256_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def restrict_contract(measured: dict, measured_sha256: str, keep, suffix: str = "") -> dict:
    """The restriction of a MEASURED contract to the capabilities in keep (a non-empty proper subset):
    a weaker contract, so sound wherever the measured one is. Only fields of contract.EvidenceContract
    (the authority decodes the advertised contract strictly)."""
    keep = set(keep)
    caps = set(measured.get("capabilities", []))
    if measured.get("source") != "MEASURED":
        raise ValueError(f"{measured.get('id')}: only a MEASURED contract can be restricted")
    if not keep or not keep < caps:
        raise ValueError(f"{measured.get('id')}: restriction {sorted(keep)} must be a non-empty proper subset of {sorted(caps)}")
    order = [c for c in measured["capabilities"] if c in keep]
    prov = {k: v for k, v in (measured.get("provenance") or {}).items() if k in keep or (k == "D_h_horizon" and "D_h" in keep)}
    prov["restricted_from"] = (f"{measured['id']} sha256:{measured_sha256} capabilities {','.join(measured['capabilities'])}; "
                               f"kept {','.join(order)}")
    c = {k: v for k, v in measured.items() if k not in ("capabilities", "provenance", "dedup_horizon_ms", "id", "notes")}
    c.update(id=f"{measured['id'][:40]}+only-{'-'.join(order)}{suffix}", capabilities=order, provenance=prov,
             notes=(f"Release 075, experiment C3: the contract {measured['id']} measured in this review run, restricted to "
                    f"{', '.join(order)} (every kept capability keeps its measured provenance). Weaker than the measured contract, "
                    f"so sound wherever it is."))
    if "D_h" in keep:
        c["dedup_horizon_ms"] = measured["dedup_horizon_ms"]
    if "verified_test_ids" in measured:
        c["verified_test_ids"] = measured["verified_test_ids"]
    return c


def check_restricted(name: str, mode: str, child: dict, parent: dict, parent_sha: str):
    full = Z.MODES[mode][1]
    if parent.get("source") != "MEASURED" or set(parent.get("capabilities", [])) != full:
        raise ValueError(f"contract {name}: restricted_from must be the MEASURED contract of mode {mode} ({sorted(full)}), "
                         f"got {parent.get('source')} {sorted(parent.get('capabilities', []))}")
    caps = set(child.get("capabilities", []))
    if child.get("source") != "MEASURED" or not caps or not caps < full:
        raise ValueError(f"contract {name}: a restriction of mode {mode} needs MEASURED and a non-empty proper subset of "
                         f"{sorted(full)}, got {child.get('source')} {sorted(caps)}")
    rf = (child.get("provenance") or {}).get("restricted_from", "")
    if f"{parent.get('id')} sha256:{parent_sha}" not in rf:
        raise ValueError(f"contract {name}: provenance.restricted_from {rf!r} does not name {parent.get('id')} sha256:{parent_sha}")
    for cap in caps:
        if (child.get("provenance") or {}).get(cap) != (parent.get("provenance") or {}).get(cap):
            raise ValueError(f"contract {name}: provenance of {cap} differs from the measured contract's")
    if child.get("fault_model") != parent.get("fault_model"):
        raise ValueError(f"contract {name}: fault model differs from the measured contract's")


def load_config(path) -> dict:
    """campaign_zephyr.load_config's checks on a copy whose restricted contracts are replaced by the
    measured contracts they restrict, then the restriction and channel rules of this module."""
    cfg = json.loads(Path(path).read_text())
    ro = cfg.get("real_owner") or {}
    sub = copy.deepcopy(cfg)
    for name in cfg.get("contracts", []):
        spec = (ro.get("contracts") or {}).get(name)
        if not isinstance(spec, dict):
            raise ValueError(f"contract {name}: real_owner.contracts.{name} required")
        bad = set(spec) - SPEC_KEYS
        if bad:
            raise ValueError(f"contract {name}: unknown keys {sorted(bad)}")
        if spec.get("channel", "queue") not in CHANNELS:
            raise ValueError(f"contract {name}: channel {spec.get('channel')!r}, expected queue or direct")
        if spec.get("restricted_from"):
            child_p, parent_p = Z.contract_path(str(spec.get("contract_file", ""))), Z.contract_path(str(spec["restricted_from"]))
            if spec.get("mode") not in Z.MODES or not child_p.is_file() or not parent_p.is_file():
                raise ValueError(f"contract {name}: mode PLAIN|CUTOFF|STOP and existing contract_file and restricted_from required")
            child = json.loads(child_p.read_text())
            check_restricted(name, spec["mode"], child, json.loads(parent_p.read_text()), sha256_file(parent_p))
            if spec.get("restrict_to") is not None and set(spec["restrict_to"]) != set(child.get("capabilities", [])):
                raise ValueError(f"contract {name}: restrict_to {sorted(spec['restrict_to'])} but {child_p.name} has {sorted(child.get('capabilities', []))}")
            sub["real_owner"]["contracts"][name] = {"mode": spec["mode"], "contract_file": spec["restricted_from"]}
        else:
            sub["real_owner"]["contracts"][name] = {"mode": spec.get("mode"), "contract_file": spec.get("contract_file", "")}
    tmp = Path(os.environ.get("TMPDIR", "/tmp")) / f"campaign_zephyr_ext_check_{os.getpid()}.json"
    try:
        tmp.write_text(json.dumps(sub))
        FROZEN_LOAD_CONFIG(tmp)  # every other rule is the frozen runner's
    finally:
        tmp.unlink(missing_ok=True)
    return cfg


class ExtTestbed(Z.ZephyrTestbed):
    def preflight(self):
        info = super().preflight()
        info["gateway_ext_sha256"] = Z.C.sha256_file(GATEWAY_EXT)
        info["gateway_frozen_sha256"] = Z.C.sha256_file(REPO / "conformance" / "reference" / "mqtt_owner_gateway.py")
        info["runner_ext"] = "evaluation/campaign_zephyr_ext.py"
        info["channels"] = {k: v.get("channel", "queue") for k, v in self.ro["contracts"].items()}
        info["restricted"] = {k: {"restricted_from": str(v["restricted_from"]), "sha256": sha256_file(Z.contract_path(v["restricted_from"]))}
                              for k, v in self.ro["contracts"].items() if v.get("restricted_from")}
        return info

    def start(self, cell, wd: str):
        ch = self.ro["contracts"][cell.contract].get("channel", "queue")
        self.e.run(f"mkdir -p {Z.shlex.quote(wd)}")
        Path(wd, "gateway.channel").write_text(ch + "\n")  # the gateways run on this host (VM-C)
        return super().start(cell, wd)


def _mark_manifest(out: Path):
    man = out / "manifest.json"
    if man.exists():
        m = json.loads(man.read_text())
        m["runner"] = "evaluation/campaign_zephyr_ext.py (on evaluation/campaign_zephyr.py)"
        man.write_text(json.dumps(m, indent=2) + "\n")


def main(argv=None):
    Z.load_config = load_config
    Z.ZephyrTestbed = ExtTestbed
    Z.GATEWAY = GATEWAY_EXT
    orig = Z.run_campaign

    def run_campaign(cfg, out, bin_dir, max_cells=0):
        try:
            return orig(cfg, out, bin_dir, max_cells)
        finally:
            _mark_manifest(out)
    Z.run_campaign = run_campaign
    return Z.main(argv)


if __name__ == "__main__":
    sys.exit(main())
