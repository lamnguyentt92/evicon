"""ThingsBoard CE server-side RPC (release 068: run locally against ThingsBoard CE 4.3.1.6).

Data path: probe -> [fault proxy] -> ThingsBoard REST; ThingsBoard -> device over MQTT
(reference/mqtt_device.py --mode thingsboard, on VM-E), which writes effects to evicon-plant.
The command ID travels in params.cmd; ThingsBoard assigns its own rpcId.

Two modes (adapter_config.mode):
  * ``persistent`` (default): POST /api/rpc/twoway|oneway/{device} with persistent=true. The REST
    call answers at once with {"rpcId"}; the outcome is read from /api/rpc/persistent/{rpcId}
    (QUEUED, SENT, DELIVERED, SUCCESSFUL, TIMEOUT, EXPIRED, FAILED, DELETED). The client polls
    until a final status or its timeout. A persistent RPC carries expirationTime: ThingsBoard
    does not deliver it to the device after that time (measured by P7/P9T).
  * ``plain``: POST /api/rpc/twoway/{device} without persistence: the REST call waits for the
    device's reply (up to ``timeout``) and answers with it, or 504.

Query: by rpcId when the client knows it; otherwise (the reply was lost, so the rpcId is
unknown) by listing the device's persistent RPCs and matching params.cmd, which ThingsBoard
allows to any tenant user. Stop: DELETE /api/rpc/persistent/{rpcId}, which deletes the record;
whether the device can still execute is what P9 measures (CANCELED, never STOPPED). Statuses
TIMEOUT, EXPIRED and FAILED are reported as such, never as STOPPED (HM1).
"""
from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from typing import Optional

from ..model import APPLIED, CANCELED, ERROR, NOT_FOUND, OK, TIMEOUT, UNKNOWN, ClientObs, EvidenceObs, Unsupported
from ..plant import PlantClient
from .base import STATUS_TESTED_LOCAL, Adapter

TB_STATUS = {"SUCCESSFUL": APPLIED, "TIMEOUT": "TIMED_OUT", "EXPIRED": "EXPIRED", "FAILED": "FAILED",
             "QUEUED": "PENDING", "SENT": "PENDING", "DELIVERED": "DELIVERED", "DELETED": CANCELED}
FINAL = {"SUCCESSFUL", "TIMEOUT", "EXPIRED", "FAILED", "DELETED"}


class ThingsBoardRpcAdapter(Adapter):
    system = "ThingsBoard CE"
    operation = "server-side RPC 'actuate'"
    implementation_status = STATUS_TESTED_LOCAL
    references = ["https://thingsboard.io/docs/user-guide/rpc/", "https://thingsboard.io/docs/reference/rest-api/"]

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.data_url = cfg["data_url"].rstrip("/")
        self.control_url = cfg.get("control_url", cfg["data_url"]).rstrip("/")
        self.device = cfg["device_uuid"]
        self.device_id = cfg.get("plant_device_id", "tb-1")
        self.mode = cfg.get("mode", "persistent")
        self.direction = cfg.get("direction", "twoway")
        if self.mode not in ("persistent", "plain") or self.direction not in ("twoway", "oneway"):
            raise ValueError("mode must be persistent|plain, direction twoway|oneway")
        self.operation = f"server-side RPC 'actuate' ({self.mode}, {self.direction})"
        self.rpc_timeout_ms = int(cfg.get("rpc_timeout_ms", 5000))
        self.retries = int(cfg.get("retries", 0))
        self.expiration_s = cfg.get("expiration_s")
        self.poll_s = float(cfg.get("poll_s", 0.2))
        self.device_admin = cfg.get("device_admin_url", "").rstrip("/")
        self.plant = PlantClient(url=cfg["plant_url"])
        self.declared = cfg.get("declared", {})
        self._token = ""
        self._rpc_ids: dict = {}

    def _call(self, method, base, path, body=None, timeout=5.0, auth=True):
        data = None if body is None else json.dumps(body).encode()
        h = {"Content-Type": "application/json", "Connection": "close"}
        if auth:
            h["X-Authorization"] = "Bearer " + self._token
        req = urllib.request.Request(base + path, data=data, method=method, headers=h)
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None), (time.monotonic() - t0) * 1000
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                p = json.loads(raw) if raw else None
            except ValueError:
                p = raw.decode(errors="replace")
            return e.code, p, (time.monotonic() - t0) * 1000
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as e:
            return None, str(e), (time.monotonic() - t0) * 1000

    def setup(self) -> None:
        st, p, _ = self._call("POST", self.control_url, "/api/auth/login",
                              {"username": self.cfg["username"], "password": self.cfg["password"]}, auth=False)
        if st != 200:
            raise RuntimeError(f"ThingsBoard login failed: HTTP {st}")
        self._token = p["token"]

    # ---- data path ----------------------------------------------------------
    def send(self, command_id: str, timeout_s: Optional[float] = None) -> ClientObs:
        t = timeout_s or self.client_timeout_s
        t_end = time.monotonic() + t
        body = {"method": "actuate", "params": {"cmd": command_id, "value": int(self.value)}, "timeout": self.rpc_timeout_ms}
        if self.mode == "persistent":
            body.update(persistent=True, retries=self.retries)
            if self.expiration_s:
                body["expirationTime"] = int((time.time() + float(self.expiration_s)) * 1000)
        st, p, ms = self._call("POST", self.data_url, f"/api/rpc/{self.direction}/{self.device}", body, timeout=t)
        if st is None:
            return ClientObs(TIMEOUT, UNKNOWN, ms, detail=str(p)[:200])
        if self.mode == "plain":
            if st == 200:
                return ClientObs(OK, (p or {}).get("outcome", APPLIED) if isinstance(p, dict) else APPLIED, ms, raw=p)
            if st in (408, 504):
                return ClientObs(TIMEOUT, "TIMED_OUT", ms, detail=f"HTTP {st}", raw=p)
            return ClientObs(ERROR, UNKNOWN, ms, detail=f"HTTP {st}", raw=p)
        if st != 200 or not isinstance(p, dict) or not p.get("rpcId"):
            return ClientObs(ERROR, UNKNOWN, ms, detail=f"HTTP {st}", raw=p)
        rid = p["rpcId"]
        self._rpc_ids[command_id] = rid
        # the outcome of a persistent RPC: poll its record until final or the client gives up
        status = ""
        while time.monotonic() < t_end:
            s2, rec, _ = self._call("GET", self.data_url, f"/api/rpc/persistent/{rid}", timeout=max(0.5, t_end - time.monotonic()))
            if s2 is None:
                break  # the data path failed while polling: the client does not know
            if s2 == 200 and isinstance(rec, dict):
                status = str(rec.get("status", ""))
                if status in FINAL or (self.direction == "oneway" and status == "DELIVERED"):
                    o = TB_STATUS.get(status, UNKNOWN)
                    kind = OK if o in (APPLIED, "DELIVERED") else ERROR
                    return ClientObs(kind, o, (time.monotonic() - (t_end - t)) * 1000, detail=status, raw=rec.get("response"))
            time.sleep(self.poll_s)
        return ClientObs(TIMEOUT, TB_STATUS.get(status, UNKNOWN) if status else UNKNOWN, (time.monotonic() - (t_end - t)) * 1000,
                         detail=f"last status {status or '-'}")

    # ---- control path -------------------------------------------------------
    def _find_rpc(self, command_id: str):
        """rpcId of the persistent RPC whose params.cmd is the command (device RPC list, newest first)."""
        for page in range(5):
            st, p, _ = self._call("GET", self.control_url,
                                  f"/api/rpc/persistent/device/{self.device}?pageSize=100&page={page}&sortProperty=createdTime&sortOrder=DESC")
            if st != 200 or not isinstance(p, dict):
                return None
            for rec in p.get("data", []):
                req = rec.get("request") or {}
                body = req.get("body") or {}
                params = body.get("params") if isinstance(body, dict) else None
                if isinstance(params, str):
                    try:
                        params = json.loads(params)
                    except ValueError:
                        params = None
                if isinstance(params, dict) and params.get("cmd") == command_id:
                    return rec["id"]["id"]
            if not p.get("hasNext"):
                return None
        return None

    def query(self, command_id: str) -> EvidenceObs:
        if self.mode != "persistent":
            raise Unsupported("a plain RPC leaves no record to query")
        t0 = time.monotonic()
        rid = self._rpc_ids.get(command_id)
        how = "rpcId"
        if not rid:
            rid, how = self._find_rpc(command_id), "listing"
        if not rid:
            return EvidenceObs(True, NOT_FOUND, False, (time.monotonic() - t0) * 1000, detail="no persistent RPC with this params.cmd")
        st, p, _ = self._call("GET", self.control_url, f"/api/rpc/persistent/{rid}")
        ms = (time.monotonic() - t0) * 1000
        if st == 404:
            return EvidenceObs(True, NOT_FOUND, False, ms, detail=f"rpc {how}: deleted")
        if st != 200 or not isinstance(p, dict):
            return EvidenceObs(True, UNKNOWN, False, ms, detail=f"HTTP {st}")
        status = str(p.get("status"))
        o = TB_STATUS.get(status, UNKNOWN)
        return EvidenceObs(True, o, o == APPLIED, ms, detail=f"{status} (found by {how})", raw=p.get("response"))

    def stop(self, command_id: str) -> EvidenceObs:
        if self.mode != "persistent":
            raise Unsupported("a plain RPC cannot be deleted")
        rid = self._rpc_ids.get(command_id) or self._find_rpc(command_id)
        if not rid:
            raise Unsupported("no persistent RPC found for this command (nothing to delete)")
        st, p, ms = self._call("DELETE", self.control_url, f"/api/rpc/persistent/{rid}")
        # Deleting the RPC record is not owner evidence that the device will not execute.
        return EvidenceObs(True, CANCELED if st in (200, 204) else UNKNOWN, False, ms, detail=f"HTTP {st}", raw=p)

    def effects(self, command_id: str, baseline) -> int:
        return self.plant.effects(command_id, self.device_id)

    def set_device_online(self, online: bool) -> None:
        if not self.device_admin:
            raise Unsupported("device_admin_url not configured")
        req = urllib.request.Request(f"{self.device_admin}/_test/online?v={'true' if online else 'false'}", data=b"", method="POST")
        urllib.request.urlopen(req, timeout=5).read()

    def expiry_s(self):
        return float(self.expiration_s) if (self.mode == "persistent" and self.expiration_s) else None
