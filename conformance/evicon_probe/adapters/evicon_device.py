"""evicon-device emulator (cmd/evicon-device) with truth from evicon-plant.

Used for (a) the harness's negative controls (K0, finite-horizon K1, device
cutoff without dedup, owner expiry) and (b) the VM-E fleet campaigns.
"""
from __future__ import annotations

import json
import time
import urllib.request
from typing import Optional

from ..plant import PlantClient
from .base import STATUS_TESTED_LOCAL
from .evicon_endpoint import EviconEndpointAdapter

PRESET_DECLARED = {
    "K0": {},
    "K0F": {"F": True},
    "K1": {"D_h": 45.0},
    "K1F": {"D_h": 45.0, "F": True},
    "K2": {"D_h": -1, "Q_A": True},
    "K3S": {"D_h": -1, "Q_A": True, "Q_S": True},
    "K3F": {"D_h": -1, "Q_A": True, "F": True},
    "K3T": {"D_h": -1, "Q_A": True, "T": 30.0},
    "K4": {"D_h": -1, "Q_A": True, "X": True},
    "CUTOFF": {"Q_A": True, "F": True},
}


class EviconDeviceAdapter(EviconEndpointAdapter):
    system = "evicon-device emulator"
    operation = "ADD (dosing pulse) via POST /d/<id>/v1/effects"
    implementation_status = STATUS_TESTED_LOCAL
    # effects() reads the plant's per-identity record, unlike the endpoint
    # adapter it inherits from (global component delta).
    per_identity_truth = True
    references = ["internal/device/device.go", "cmd/evicon-device/main.go", "internal/plant/plant.go"]

    def __init__(self, cfg: dict):
        cfg = dict(cfg)
        dev = cfg.get("device_id", "pump-1")
        cfg.setdefault("domain", dev)
        super().__init__(cfg)
        self.device_id = dev
        self.preset = cfg.get("contract", "K2").upper()
        self.declared = dict(cfg.get("declared") or PRESET_DECLARED.get(self.preset, {}))
        self.system = f"evicon-device {self.preset}"
        self.admin_url = cfg.get("admin_url", "").rstrip("/")
        self.plant = PlantClient(url=cfg["plant_url"])
        self._expires: dict = {}

    def _request(self, command_id: str) -> dict:
        r = super()._request(command_id)
        exp = self.expiry_s()
        if exp:
            # the deadline is fixed at first send; retries carry the same one
            r["expires_at_ms"] = self._expires.setdefault(command_id, int((time.time() + float(exp)) * 1000))
        return r

    def truth_baseline(self):
        return None

    def effects(self, command_id: str, baseline) -> int:
        return self.plant.effects(command_id, self.device_id)

    def set_device_online(self, online: bool) -> None:
        if not self.admin_url:
            from ..model import Unsupported
            raise Unsupported("no admin_url for the device")
        req = urllib.request.Request(f"{self.admin_url}/_test/online?v={'true' if online else 'false'}", data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            json.loads(r.read())

    def dedup_horizon_hint_s(self) -> Optional[float]:
        if "dedup_horizon_hint_s" in self.cfg:
            return self.cfg["dedup_horizon_hint_s"]
        return self.declared.get("D_h")

    def expiry_s(self) -> Optional[float]:
        return self.cfg.get("expiry_s", self.declared.get("T"))
