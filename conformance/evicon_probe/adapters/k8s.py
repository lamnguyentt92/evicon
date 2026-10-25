"""Kubernetes object creation: fixed ``metadata.name`` vs ``generateName`` (release 068).

Owner: kube-apiserver (k3s, API server only, in docker on VM-B). Effect: a ConfigMap labelled
evicon-probe-kind=effect. With ``identity: name`` the object name is the command ID: a second
create fails AlreadyExists (deduplication for as long as the object exists), GET by name is a
receipt query, and creating a tombstone object under the same name *first* is a per-identity
stop built from the create-if-absent precondition (a late create then fails). With
``identity: generateName`` none of this holds: the server names every object.

Transport: the Kubernetes REST API over HTTPS (no kubectl), bearer token from the environment
(adapter_config.token = "${K3S_PROBE_TOKEN}", random per measurement), server CA from the
cluster (public certificate). Data path through the fault proxy (adapter_config.data_server,
https://127.0.0.1:<proxy>; 127.0.0.1 is in the API server certificate), control path direct.
Run locally against k3s v1.36.4+k3s1 (`k3s server --disable-agent`) in release 068.
"""
from __future__ import annotations

import http.client
import json
import socket
import ssl
import time
import urllib.parse
from typing import Optional

from ..model import APPLIED, ERROR, NOT_FOUND, OK, STOPPED, TIMEOUT, UNKNOWN, ClientObs, EvidenceObs, Unsupported
from .base import STATUS_TESTED_LOCAL, Adapter


class K8sCreateAdapter(Adapter):
    system = "Kubernetes API (k3s)"
    operation = "create ConfigMap"
    implementation_status = STATUS_TESTED_LOCAL
    references = ["https://kubernetes.io/docs/reference/using-api/api-concepts/#generated-values",
                  "https://kubernetes.io/docs/reference/kubernetes-api/common-definitions/object-meta/",
                  "https://kubernetes.io/docs/reference/using-api/api-concepts/#create"]

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.ns = cfg.get("namespace", "evicon-probe")
        self.identity = cfg.get("identity", "name")
        if self.identity not in ("name", "generateName"):
            raise ValueError("identity must be name or generateName")
        self.operation = f"create ConfigMap ({self.identity})"
        self.data = urllib.parse.urlsplit(cfg["data_server"])
        self.control = urllib.parse.urlsplit(cfg.get("control_server") or cfg["data_server"])
        self.token = cfg.get("token", "")
        # control path: the API server's CA; data path: the fault proxy's own certificate when the
        # proxy terminates TLS (faults then act on the HTTP request/reply, not on the handshake)
        self.ctx = self._ctx(cfg.get("ca_file"))
        self.data_ctx = self._ctx(cfg.get("data_ca_file") or cfg.get("ca_file"))
        self.declared = cfg.get("declared", {"D_h": -1, "Q_A": True, "Q_S": True} if self.identity == "name" else {})
        self.server_version = ""

    # ---- transport ----------------------------------------------------------
    def _ctx(self, ca):
        ctx = ssl.create_default_context(cafile=ca) if ca else ssl.create_default_context()
        if self.cfg.get("insecure_skip_verify"):
            ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
        return ctx

    def _req(self, method, path, body=None, control=True, timeout=10.0):
        u = self.control if control else self.data
        conn = http.client.HTTPSConnection(u.hostname, u.port or 443, context=self.ctx if control else self.data_ctx, timeout=timeout)
        h = {"Accept": "application/json", "Connection": "close"}
        if self.token:
            h["Authorization"] = "Bearer " + self.token
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        t0 = time.monotonic()
        try:
            conn.request(method, path, body=data, headers=h)
            r = conn.getresponse()
            raw = r.read()
            try:
                p = json.loads(raw) if raw else {}
            except ValueError:
                p = {"raw": raw[:300].decode(errors="replace")}
            return r.status, p, (time.monotonic() - t0) * 1000
        except (socket.timeout, TimeoutError) as e:
            return None, {"error": f"timeout: {e}"}, (time.monotonic() - t0) * 1000
        except (OSError, http.client.HTTPException, ssl.SSLError) as e:
            return None, {"error": f"{type(e).__name__}: {e}"}, (time.monotonic() - t0) * 1000
        finally:
            conn.close()

    def _manifest(self, command_id: str, kind: str) -> dict:
        meta = {"labels": {"evicon-probe-cmd": command_id, "evicon-probe-kind": kind}}
        if self.identity == "name" or kind == "tombstone":
            meta["name"] = command_id
        else:
            meta["generateName"] = command_id + "-"
        return {"apiVersion": "v1", "kind": "ConfigMap", "metadata": meta, "data": {"value": str(self.value)}}

    @property
    def _cm(self) -> str:
        return f"/api/v1/namespaces/{self.ns}/configmaps"

    # ---- lifecycle ----------------------------------------------------------
    def setup(self) -> None:
        st, p, _ = self._req("GET", "/version")
        if st != 200:
            raise RuntimeError(f"Kubernetes API at {self.control.geturl()} not usable: {st} {str(p)[:200]}")
        self.server_version = str(p.get("gitVersion", ""))
        st, p, _ = self._req("POST", "/api/v1/namespaces", {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": self.ns}})
        if st not in (201, 409):
            raise RuntimeError(f"cannot create namespace {self.ns}: {st} {str(p)[:200]}")

    def describe(self) -> dict:
        d = super().describe()
        d["server_version"] = self.server_version
        return d

    # ---- data path ----------------------------------------------------------
    def send(self, command_id: str, timeout_s: Optional[float] = None) -> ClientObs:
        t = timeout_s or self.client_timeout_s
        st, p, ms = self._req("POST", self._cm + f"?timeout={max(1, int(t))}s", self._manifest(command_id, "effect"), control=False, timeout=t)
        if st is None:
            return ClientObs(TIMEOUT, UNKNOWN, ms, detail=p.get("error", "")[:200])
        if st == 201:
            return ClientObs(OK, APPLIED, ms, detail=p.get("metadata", {}).get("name", ""))
        if st == 409:
            return ClientObs(OK, "ALREADY_EXISTS", ms, detail=str(p.get("reason", ""))[:200])
        if st in (408, 504):
            return ClientObs(TIMEOUT, UNKNOWN, ms, detail=f"HTTP {st} {str(p.get('message', ''))[:160]}")
        return ClientObs(ERROR, UNKNOWN, ms, detail=f"HTTP {st} {str(p.get('message', ''))[:160]}")

    # ---- control path -------------------------------------------------------
    def _objects(self, command_id: str) -> list:
        sel = urllib.parse.quote(f"evicon-probe-cmd={command_id}")
        st, p, _ = self._req("GET", self._cm + f"?labelSelector={sel}")
        if st != 200:
            raise RuntimeError(f"list configmaps failed: {st} {str(p)[:200]}")
        return p.get("items", [])

    def query(self, command_id: str) -> EvidenceObs:
        if self.identity != "name":
            raise Unsupported("generateName objects cannot be looked up by the client identity")
        st, p, ms = self._req("GET", f"{self._cm}/{command_id}")
        if st == 404:
            return EvidenceObs(True, NOT_FOUND, False, ms)
        if st != 200:
            return EvidenceObs(True, UNKNOWN, False, ms, detail=f"HTTP {st}")
        kind = p.get("metadata", {}).get("labels", {}).get("evicon-probe-kind")
        o = APPLIED if kind == "effect" else STOPPED
        return EvidenceObs(True, o, True, ms, detail=f"kind={kind}")

    def stop(self, command_id: str) -> EvidenceObs:
        if self.identity != "name":
            raise Unsupported("no per-identity precondition with generateName")
        st, p, ms = self._req("POST", self._cm, self._manifest(command_id, "tombstone"))
        if st == 201:
            return EvidenceObs(True, STOPPED, True, ms, detail="tombstone created (create-if-absent)")
        q = self.query(command_id)
        q.latency_ms += ms
        return q

    def effects(self, command_id: str, baseline) -> int:
        return sum(1 for o in self._objects(command_id) if o["metadata"].get("labels", {}).get("evicon-probe-kind") == "effect")
