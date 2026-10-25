"""EviCon's own endpoint (cmd/evicon-endpoint, profiles E1/E2/E3, E2 + -per-identity-stop).

This adapter is the harness's positive control: the evidence contract of each
profile is known from RUNTIME_VERIFIED tests, so EviCon-Probe must classify it
correctly before its classification of third-party systems is trusted.
"""
from __future__ import annotations

import hashlib
import json
import socket
import time
import urllib.error
import urllib.request
from typing import Optional

from ..model import (APPLIED, CONFLICT, ERROR, NOT_FOUND, OK, STOPPED, TIMEOUT, UNKNOWN, ClientObs, EvidenceObs,
                     Unsupported)
from .base import STATUS_TESTED_LOCAL, Adapter

DECLARED = {
    "E1": {"D_h": -1, "Q_A": True},
    "E2": {"D_h": -1, "Q_A": True, "F": True},
    "E2_STOP": {"D_h": -1, "Q_A": True, "F": True, "Q_S": True},
    "E3": {"D_h": -1, "Q_A": True, "X": True},
}


def _http(method: str, url: str, body: Optional[dict], timeout: float):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", "Connection": "close"})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None), (time.monotonic() - t0) * 1000
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            payload = json.loads(raw) if raw else None
        except ValueError:
            payload = raw.decode(errors="replace")
        return e.code, payload, (time.monotonic() - t0) * 1000
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as e:
        return None, str(e), (time.monotonic() - t0) * 1000


class EviconEndpointAdapter(Adapter):
    system = "EviCon endpoint"
    operation = "ADD via POST /v1/effects"
    implementation_status = STATUS_TESTED_LOCAL
    per_identity_truth = False
    references = ["internal/adapter/e12.go", "internal/adapter/e3.go", "cmd/evicon-endpoint/main.go"]

    def __init__(self, cfg: dict):
        cfg = dict(cfg)
        cfg.setdefault("first_sequence", int(time.time()) * 1000)
        super().__init__(cfg)
        self.profile = cfg.get("profile", "E2").upper()
        self.declared = DECLARED.get(self.profile, {})
        self.system = f"EviCon endpoint {self.profile}"
        self.data_url = cfg["data_url"].rstrip("/")
        self.control_url = cfg.get("control_url", cfg["data_url"]).rstrip("/")
        self.domain = cfg.get("domain", "atlas-001")
        self.generation = int(cfg.get("generation", 1))
        self.epoch = int(cfg.get("epoch", 1))
        self._fence_n = 0

    def _request(self, command_id: str) -> dict:
        return {"schema_version": 1, "domain_id": self.domain, "generation": self.generation, "command_id": command_id,
                "operation": "ADD", "value": [int(self.value)], "epoch": self.epoch, "sequence": self.sequence_of(command_id)}

    def intent_hash(self, command_id: str) -> str:
        # Mirrors protocol.CommandRequest.CanonicalHash (Go json.Marshal order, no spaces).
        r = self._request(command_id)
        canon = json.dumps({k: r[k] for k in ("schema_version", "domain_id", "generation", "command_id", "operation", "value", "epoch", "sequence")},
                           separators=(",", ":"))
        return hashlib.sha256(canon.encode()).hexdigest()

    def send(self, command_id: str, timeout_s: Optional[float] = None) -> ClientObs:
        status, payload, ms = _http("POST", self.data_url + "/v1/effects", self._request(command_id), timeout_s or self.client_timeout_s)
        if status is None:
            return ClientObs(TIMEOUT, UNKNOWN, ms, detail=str(payload))
        if status == 409:
            return ClientObs(CONFLICT, UNKNOWN, ms, raw=payload)
        if status // 100 != 2:
            return ClientObs(ERROR if status < 500 else TIMEOUT, UNKNOWN, ms, detail=f"HTTP {status}", raw=payload)
        return ClientObs(OK, payload.get("outcome", UNKNOWN), ms, detail=payload.get("reason", ""), raw=payload)

    def query(self, command_id: str) -> EvidenceObs:
        status, payload, ms = _http("GET", f"{self.control_url}/v1/receipts/{command_id}", None, 5)
        if status == 409 and isinstance(payload, dict) and "UNSUPPORTED" in str(payload.get("error", "")):
            raise Unsupported("QUERY_UNSUPPORTED")
        if status == 404:
            return EvidenceObs(True, NOT_FOUND, False, ms, raw=payload)
        if status is None or status // 100 != 2:
            return EvidenceObs(True, UNKNOWN, False, ms, detail=f"HTTP {status}", raw=payload)
        o = payload.get("outcome", UNKNOWN)
        return EvidenceObs(True, o, o in (APPLIED, STOPPED), ms, detail=payload.get("reason", ""), raw=payload)

    def stop(self, command_id: str) -> EvidenceObs:
        body = {"schema_version": 1, "domain_id": self.domain, "generation": self.generation, "command_id": command_id,
                "intent_hash": self.intent_hash(command_id), "sequence": self.sequence_of(command_id)}
        status, payload, ms = _http("POST", self.control_url + "/v1/stop", body, 5)
        if status == 409 and isinstance(payload, dict) and payload.get("error") == "STOP_UNSUPPORTED":
            raise Unsupported("STOP_UNSUPPORTED")
        if status is None or status // 100 != 2:
            return EvidenceObs(True, UNKNOWN, False, ms, detail=f"HTTP {status}", raw=payload)
        o = payload.get("outcome", UNKNOWN)
        return EvidenceObs(True, o, o in (APPLIED, STOPPED), ms, detail=payload.get("reason", ""), raw=payload)

    def fence(self, command_ids: list) -> EvidenceObs:
        self._fence_n += 1
        cutoff = max(self.sequence_of(c) for c in command_ids)
        body = {"schema_version": 1, "domain_id": self.domain, "generation": self.generation,
                "token": f"probe-{self.run_tag}-{self._fence_n}", "cutoff_sequence": cutoff}
        status, payload, ms = _http("POST", self.control_url + "/v1/barriers", body, 10)
        if status == 409 and isinstance(payload, dict) and payload.get("error") == "BARRIER_UNSUPPORTED":
            raise Unsupported("BARRIER_UNSUPPORTED")
        if status is None or status // 100 != 2:
            return EvidenceObs(True, UNKNOWN, False, ms, detail=f"HTTP {status}", raw=payload)
        # Owner fence semantics: every identity at or below the cutoff without
        # an APPLIED receipt is STOPPED (sealed) from now on.
        rec = {r["command_id"]: r["outcome"] for r in (payload.get("receipts") or [])}
        target = command_ids[0]
        o = rec.get(target, STOPPED)
        return EvidenceObs(True, o, True, ms, detail=f"cutoff={cutoff};reason={payload.get('reason','')}", raw=payload)

    def truth_baseline(self):
        status, payload, _ = _http("GET", self.control_url + "/_debug/state", None, 5)
        if status != 200:
            raise RuntimeError(f"truth read failed: {status} {payload}")
        return int(payload.get("effects", 0))

    def effects(self, command_id: str, baseline) -> int:
        return self.truth_baseline() - int(baseline)

    def dedup_horizon_hint_s(self):
        return -1
