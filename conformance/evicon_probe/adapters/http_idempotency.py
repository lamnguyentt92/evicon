"""Generic HTTP API with an Idempotency-Key header (IETF draft style).

For services on the testbed that implement Idempotency-Key (or for the
reference server used in the tests). Configuration:
  data_url        POST target (through the fault proxy), e.g. http://{proxy}/v1/doses
  key_header      default "Idempotency-Key"
  body            JSON body template; "{cmd}" and "{value}" are substituted
  query_url       optional GET template for a receipt query, e.g. http://host/v1/doses/{cmd}
  truth           {"plant_url": ..., "device_id": ...}  or {"url": "http://host/truth?cmd={cmd}", "field": "effects"}
  declared        documented capabilities, e.g. {"D_h": 86400} for a 24 h key retention

Status: TESTED_LOCAL against the reference server in tests/test_adapters_offline.py.
"""
from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from typing import Optional

from ..model import APPLIED, CONFLICT, ERROR, NOT_FOUND, OK, TIMEOUT, UNKNOWN, ClientObs, EvidenceObs, Unsupported
from ..plant import PlantClient
from .base import STATUS_TESTED_LOCAL, Adapter


def _sub(obj, cmd, value):
    if isinstance(obj, str):
        return obj.replace("{cmd}", cmd).replace("{value}", str(value))
    if isinstance(obj, dict):
        return {k: _sub(v, cmd, value) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sub(v, cmd, value) for v in obj]
    return obj


class HttpIdempotencyAdapter(Adapter):
    system = "HTTP Idempotency-Key API"
    operation = "POST with Idempotency-Key"
    implementation_status = STATUS_TESTED_LOCAL
    references = ["https://datatracker.ietf.org/doc/draft-ietf-httpapi-idempotency-key-header/"]

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.system = cfg.get("system_name", self.system)
        self.data_url = cfg["data_url"]
        self.key_header = cfg.get("key_header", "Idempotency-Key")
        self.body = cfg.get("body", {"cmd": "{cmd}", "value": "{value}"})
        self.query_url = cfg.get("query_url", "")
        self.headers = cfg.get("headers", {})
        t = cfg["truth"]
        self.plant = PlantClient(url=t["plant_url"]) if "plant_url" in t else None
        self.truth = t
        self.declared = cfg.get("declared", {})

    def _do(self, method, url, body, headers, timeout):
        data = None if body is None else json.dumps(body).encode()
        h = {"Content-Type": "application/json", "Connection": "close", **headers}
        req = urllib.request.Request(url, data=data, method=method, headers=h)
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else {}), (time.monotonic() - t0) * 1000
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                p = json.loads(raw) if raw else {}
            except ValueError:
                p = {"raw": raw.decode(errors="replace")[:300]}
            return e.code, p, (time.monotonic() - t0) * 1000
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as e:
            return None, {"error": str(e)}, (time.monotonic() - t0) * 1000

    def send(self, command_id: str, timeout_s: Optional[float] = None) -> ClientObs:
        st, p, ms = self._do("POST", self.data_url, _sub(self.body, command_id, self.value), {**self.headers, self.key_header: command_id},
                             timeout_s or self.client_timeout_s)
        if st is None:
            return ClientObs(TIMEOUT, UNKNOWN, ms, detail=p.get("error", ""))
        if st == 409:
            return ClientObs(CONFLICT, UNKNOWN, ms, raw=p)  # concurrent request with the same key in progress
        if 200 <= st < 300:
            return ClientObs(OK, p.get("outcome", APPLIED), ms, raw=p)
        return ClientObs(ERROR if st < 500 else TIMEOUT, UNKNOWN, ms, detail=f"HTTP {st}", raw=p)

    def query(self, command_id: str) -> EvidenceObs:
        if not self.query_url:
            raise Unsupported("no query_url configured")
        st, p, ms = self._do("GET", self.query_url.replace("{cmd}", command_id), None, self.headers, 5)
        if st == 404:
            return EvidenceObs(True, NOT_FOUND, False, ms)
        if st != 200:
            return EvidenceObs(True, UNKNOWN, False, ms, detail=f"HTTP {st}")
        o = p.get("outcome", APPLIED)
        return EvidenceObs(True, o, o == APPLIED, ms, raw=p)

    def effects(self, command_id: str, baseline) -> int:
        if self.plant:
            return self.plant.effects(command_id, self.truth.get("device_id", ""))
        st, p, _ = self._do("GET", self.truth["url"].replace("{cmd}", command_id), None, {}, 5)
        if st != 200:
            raise RuntimeError(f"truth read failed: {st}")
        return int(p[self.truth.get("field", "effects")])
