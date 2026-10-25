#!/usr/bin/env python3
"""Runner of experiment Q (release 077, PREREG_review_077.md): the delivery binding of MQTT commands on the
Zephyr EviCon firmware, on the C3 runner.

evaluation/campaign_zephyr_ext.py (frozen by the C3 plan, on the frozen C2 runner campaign_zephyr.py) runs
EviCon authorities against the firmware behind Mosquitto through one MQTT owner gateway per device. This
runner imports it unchanged and adds, per contract of the config:

  "binding": "auto"|"qos1"|"qos0"   the QoS of the gateway's command messages
                                    (conformance/reference/mqtt_owner_gateway_ext077.py: auto is QoS 0 for a
                                    contract without D_h, QoS 1 with D_h); written to gateway.binding in the
                                    cell's directory, where the 077 gateway reads it

and, for the whole config:

  real_owner.offline_cut: true      every offline window of the queue channel starts with a cut: the link stays
                                    up until the gateway sends a command, lets it reach the broker, loses the
                                    broker's acknowledgement and replies, and resets 50 ms later (gateway.cut)

The gateway is conformance/reference/mqtt_owner_gateway_ext077.py (on the 075 gateway, on the frozen C2 gateway).
Everything else is the C3 runner's (and so the C2 runner's): this module replaces their load_config, testbed and
GATEWAY globals and calls their main.

    python3 evaluation/campaign_zephyr_ext077.py --config evaluation/campaigns/expQ_zephyr.json --out <dir>
"""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "evaluation"))
sys.path.insert(0, str(REPO / "conformance"))
import campaign_zephyr_ext as ZX  # noqa: E402  (frozen by the C3 plan; imported, never edited)

Z = ZX.Z
GATEWAY_EXT077 = REPO / "conformance" / "reference" / "mqtt_owner_gateway_ext077.py"
BINDINGS = ("auto", "qos1", "qos0")
FROZEN_LOAD_CONFIG = ZX.load_config


def load_config(path) -> dict:
    """The C3 runner's checks on a copy without the 077 keys, then the 077 rules."""
    cfg = json.loads(Path(path).read_text())
    ro = cfg.get("real_owner") or {}
    cut = ro.get("offline_cut", False)
    if not isinstance(cut, bool):
        raise ValueError("real_owner.offline_cut: true or false")
    sub = copy.deepcopy(cfg)
    sub["real_owner"].pop("offline_cut", None)
    for name in cfg.get("contracts", []):
        spec = (ro.get("contracts") or {}).get(name)
        if not isinstance(spec, dict):
            raise ValueError(f"contract {name}: real_owner.contracts.{name} required")
        b = spec.get("binding", "auto")
        if b not in BINDINGS:
            raise ValueError(f"contract {name}: binding {b!r}, expected auto, qos1 or qos0")
        if cut and spec.get("channel", "queue") != "queue":
            raise ValueError(f"contract {name}: offline_cut is a fault of the queue channel")
        sub["real_owner"]["contracts"][name].pop("binding", None)
    tmp = Path(os.environ.get("TMPDIR", "/tmp")) / f"campaign_zephyr_ext077_check_{os.getpid()}.json"
    try:
        tmp.write_text(json.dumps(sub))
        FROZEN_LOAD_CONFIG(tmp)  # every other rule is the C3 runner's (and the C2 runner's)
    finally:
        tmp.unlink(missing_ok=True)
    return cfg


class Ext077Testbed(ZX.ExtTestbed):
    def preflight(self):
        info = super().preflight()
        info["gateway_ext077_sha256"] = Z.C.sha256_file(GATEWAY_EXT077)
        info["runner_ext077"] = "evaluation/campaign_zephyr_ext077.py"
        info["bindings"] = {k: v.get("binding", "auto") for k, v in self.ro["contracts"].items()}
        info["offline_cut"] = bool(self.ro.get("offline_cut", False))
        return info

    def start(self, cell, wd: str):
        spec = self.ro["contracts"][cell.contract]
        Path(wd).mkdir(parents=True, exist_ok=True)  # the gateways run on this host (VM-C)
        Path(wd, "gateway.binding").write_text(spec.get("binding", "auto") + "\n")
        Path(wd, "gateway.cut").write_text(("1" if self.ro.get("offline_cut") else "0") + "\n")
        return super().start(cell, wd)


def _mark_manifest(out: Path):
    man = out / "manifest.json"
    if man.exists():
        m = json.loads(man.read_text())
        m["runner"] = "evaluation/campaign_zephyr_ext077.py (on evaluation/campaign_zephyr_ext.py, on evaluation/campaign_zephyr.py)"
        man.write_text(json.dumps(m, indent=2) + "\n")


def main(argv=None):
    Z.load_config = load_config
    Z.ZephyrTestbed = Ext077Testbed
    Z.GATEWAY = GATEWAY_EXT077
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
