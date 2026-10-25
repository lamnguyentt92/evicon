"""DBOS Transact workflow step with an external effect (release 068: run locally against
DBOS 3.3.0 + PostgreSQL 16; on the testbed PostgreSQL runs in docker on VM-B, the app on VM-A).

The reference app (conformance/reference/dbos_app.py) runs a workflow with workflow ID = command
ID; its single step POSTs the effect to evicon-plant through the fault proxy, with a per-attempt
HTTP timeout and automatic step retries (like the Temporal activity). DBOS checkpoints a step's
*result* after it returns, so an attempt whose effect happened but whose reply was lost or late
is retried and can duplicate the effect (HM4). Identity: workflow ID (a second start with the
same ID returns the first execution). Query: workflow status, which is evidence about the
workflow, not about the effect. No stop, no fence.
"""
from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from typing import Optional

from ..model import APPLIED, CANCELED, ERROR, NOT_FOUND, OK, TIMEOUT, UNKNOWN, ClientObs, EvidenceObs
from ..plant import PlantClient
from .base import STATUS_TESTED_LOCAL, Adapter

# DBOS workflow status -> normalised outcome (never STOPPED: no DBOS state says the effect
# did not happen)
DBOS_STATUS = {"SUCCESS": APPLIED, "ERROR": "FAILED", "MAX_RECOVERY_ATTEMPTS_EXCEEDED": "FAILED",
               "CANCELLED": CANCELED, "PENDING": "PENDING", "ENQUEUED": "PENDING"}


class DbosStepAdapter(Adapter):
    system = "DBOS Transact"
    operation = "workflow(id=cmd) -> step actuate"
    implementation_status = STATUS_TESTED_LOCAL
    references = ["https://docs.dbos.dev/python/tutorials/workflow-tutorial",
                  "https://docs.dbos.dev/python/tutorials/step-tutorial",
                  "https://docs.dbos.dev/python/reference/contexts"]

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.app_url = cfg["app_url"].rstrip("/")
        self.device_id = cfg.get("plant_device_id", "dbos-1")
        self.plant = PlantClient(url=cfg["plant_url"])
        self.declared = cfg.get("declared", {"D_h": -1, "Q_A": True})

    def _call(self, method, path, timeout):
        req = urllib.request.Request(self.app_url + path, data=b"" if method == "POST" else None, method=method,
                                     headers={"Connection": "close"})
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.loads(r.read() or b"{}"), (time.monotonic() - t0) * 1000
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="replace")[:300]
            try:
                body = json.loads(raw)
            except ValueError:
                body = {"error": raw}
            return e.code, body, (time.monotonic() - t0) * 1000
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as e:
            return None, {"error": str(e)}, (time.monotonic() - t0) * 1000

    def setup(self) -> None:
        st, p, _ = self._call("GET", "/healthz", 5)
        if st != 200:
            raise RuntimeError(f"DBOS reference app at {self.app_url} not ready: {st} {p}")
        self.app_info = p

    def send(self, command_id: str, timeout_s: Optional[float] = None) -> ClientObs:
        t = timeout_s or self.client_timeout_s
        st, p, ms = self._call("POST", f"/run/{command_id}?value={int(self.value)}&device={self.device_id}&wait_s={t}", t + 2)
        if st is None:
            return ClientObs(TIMEOUT, UNKNOWN, ms, detail=p.get("error", ""))
        status = str(p.get("status", ""))
        if st == 200:
            o = DBOS_STATUS.get(status, UNKNOWN)
            return ClientObs(OK if o == APPLIED else ERROR, o, ms, detail=status, raw=p)
        if st == 202:
            return ClientObs(TIMEOUT, "PENDING", ms, detail=status, raw=p)
        return ClientObs(ERROR, UNKNOWN, ms, detail=f"HTTP {st}", raw=p)

    def query(self, command_id: str) -> EvidenceObs:
        st, p, ms = self._call("GET", f"/status/{command_id}", 5)
        if st == 404:
            return EvidenceObs(True, NOT_FOUND, False, ms)
        if st != 200:
            return EvidenceObs(True, UNKNOWN, False, ms, detail=f"HTTP {st}")
        s = str(p.get("status"))
        o = DBOS_STATUS.get(s, UNKNOWN)
        # SUCCESS is evidence that the workflow completed; whether the effect happened once is
        # for the truth channel to say (the rubric compares them)
        return EvidenceObs(True, o, o == APPLIED, ms, detail=s, raw=p)

    def effects(self, command_id: str, baseline) -> int:
        return self.plant.effects(command_id, self.device_id)
