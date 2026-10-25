"""LwM2M Execute through the Eclipse Leshan server demo REST API.

Data path: probe -> [fault proxy] -> Leshan REST -> CoAP -> LwM2M client
(Anjay demo, Wakaama, or the Zephyr LwM2M client on native_sim in
firmware/zephyr-cutoff) whose executable resource reports to evicon-plant.
The command ID is passed as Execute argument 0 (LwM2M 1.1 syntax 0='<id>').
LwM2M Execute has no receipt query by identity, no stop and no fence; the
probe measures CoAP-level deduplication, queue-mode delivery to sleeping
clients (P7) and late execution (P3).

Status: REST path per Leshan 2.x demo server (POST /api/clients/{ep}/{path}
with ?timeout=); not run against Leshan in this release.
"""
from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from ..model import APPLIED, ERROR, OK, TIMEOUT, UNKNOWN, ClientObs
from ..plant import PlantClient
from .base import STATUS_UNTESTED, Adapter


class LeshanExecuteAdapter(Adapter):
    system = "Eclipse Leshan + LwM2M client"
    operation = "Execute /33000/0/1 (dose)"
    implementation_status = STATUS_UNTESTED
    references = ["https://github.com/eclipse-leshan/leshan", "https://www.openmobilealliance.org/release/LightweightM2M/"]

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.data_url = cfg["data_url"].rstrip("/")
        self.endpoint = cfg["endpoint"]
        self.path = cfg.get("resource_path", "33000/0/1").strip("/")
        self.server_timeout_s = int(cfg.get("server_timeout_s", 5))
        self.device_id = cfg.get("plant_device_id", self.endpoint)
        self.plant = PlantClient(url=cfg["plant_url"])
        self.declared = cfg.get("declared", {})
        self.system = cfg.get("client_name", self.system)

    def send(self, command_id: str, timeout_s: Optional[float] = None) -> ClientObs:
        url = f"{self.data_url}/api/clients/{urllib.parse.quote(self.endpoint)}/{self.path}?timeout={self.server_timeout_s}"
        body = f"0='{command_id}'".encode()
        req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "text/plain", "Connection": "close"})
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout_s or self.client_timeout_s) as r:
                p = json.loads(r.read() or b"{}")
                ms = (time.monotonic() - t0) * 1000
                if p.get("success") or str(p.get("status", "")).upper() == "CHANGED":
                    return ClientObs(OK, APPLIED, ms, detail=str(p.get("status")), raw=p)
                return ClientObs(ERROR, UNKNOWN, ms, detail=str(p.get("status")), raw=p)
        except urllib.error.HTTPError as e:
            ms = (time.monotonic() - t0) * 1000
            # Leshan reports a CoAP timeout to a sleeping/unreachable client as a gateway error.
            st = TIMEOUT if e.code in (502, 503, 504) else ERROR
            return ClientObs(st, UNKNOWN, ms, detail=f"HTTP {e.code}", raw=e.read().decode(errors="replace")[:300])
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as e:
            return ClientObs(TIMEOUT, UNKNOWN, (time.monotonic() - t0) * 1000, detail=str(e))

    def effects(self, command_id: str, baseline) -> int:
        return self.plant.effects(command_id, self.device_id)
