"""Adapter interface between the generic probes P1-P9 and one (system, operation).

An adapter has two paths:
  * the data path (``send``), which the fault controller sits on;
  * the control path (``query``/``stop``/``fence``/truth), which must bypass
    the faulted hop so a stop is not itself delayed behind the request.

Truth must come from a channel the client cannot influence (owner state read
out of band, or the plant emulator). ``truth_baseline``/``effects`` let
adapters whose truth is a global counter measure per-probe deltas; probes run
sequentially, one identity at a time.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Optional

from ..model import ClientObs, EvidenceObs, Unsupported

# Implementation status, reported in every result file so a reader can tell
# which adapters were exercised against the real system in this release.
STATUS_TESTED_TESTBED = "TESTED_TESTBED"    # measured against the real system on the 5-VM testbed
STATUS_TESTED_LOCAL = "TESTED_LOCAL"        # exercised end-to-end by conformance/tests or a local real system
STATUS_UNTESTED = "UNTESTED_NEEDS_TESTBED"  # written against the documented API, never run here


class Adapter:
    system = "abstract"
    operation = "abstract"
    implementation_status = STATUS_UNTESTED
    references: list = []
    # Capabilities the documentation claims (rubric compares them with observation).
    declared: dict = {}
    # True when effects(cmd, ...) counts this identity's effects; False when it
    # is a delta of a global counter since the baseline (pressure probes adapt).
    per_identity_truth = True

    def __init__(self, cfg: dict):
        self.cfg = cfg or {}
        self.client_timeout_s = float(self.cfg.get("client_timeout_s", 2.0))
        self.value = self.cfg.get("value", 1)
        self._seq = int(self.cfg.get("first_sequence", 0))
        self.run_tag = self.cfg.get("run_tag") or uuid.uuid4().hex[:8]

    # ---- identities -----------------------------------------------------
    def new_id(self, probe_id: str) -> str:
        self._seq += 1
        return f"{probe_id.lower()}-{self.run_tag}-{self._seq}"

    def sequence_of(self, command_id: str) -> int:
        try:
            return int(command_id.rsplit("-", 1)[1])
        except (IndexError, ValueError):
            return self._seq

    # ---- lifecycle ------------------------------------------------------
    def setup(self) -> None:
        pass

    def teardown(self) -> None:
        pass

    def fresh_client(self) -> "Adapter":
        """A new client instance with no in-memory state (P8 recovery)."""
        return self

    # ---- data path ------------------------------------------------------
    def send(self, command_id: str, timeout_s: Optional[float] = None) -> ClientObs:
        raise NotImplementedError

    # ---- control path (raise Unsupported when the system has none) -------
    def query(self, command_id: str) -> EvidenceObs:
        raise Unsupported("query")

    def stop(self, command_id: str) -> EvidenceObs:
        raise Unsupported("stop")

    def fence(self, command_ids: list) -> EvidenceObs:
        raise Unsupported("fence")

    # ---- truth ----------------------------------------------------------
    def truth_baseline(self) -> Any:
        return None

    def effects(self, command_id: str, baseline: Any) -> int:
        raise NotImplementedError

    # ---- optional device hooks (P7) ---------------------------------------
    def set_device_online(self, online: bool) -> None:
        raise Unsupported("device online/offline hook")

    # ---- documented parameters ------------------------------------------
    def dedup_horizon_hint_s(self) -> Optional[float]:
        """Documented dedup horizon in seconds (None = unknown, -1 = unbounded)."""
        return self.cfg.get("dedup_horizon_hint_s")

    def expiry_s(self) -> Optional[float]:
        return self.cfg.get("expiry_s")

    def describe(self) -> dict:
        return {"system": self.system, "operation": self.operation, "implementation_status": self.implementation_status,
                "declared": self.declared, "references": self.references,
                "config": {k: v for k, v in self.cfg.items() if not _credential_key(k)}}


def _credential_key(k: str) -> bool:
    """Keys whose values are never written to result files (067: the MinIO access key too)."""
    k = k.lower()
    return any(w in k for w in ("password", "secret", "token", "access_key", "credential", "auth"))


def timed(fn, *a, **kw):
    t0 = time.monotonic()
    out = fn(*a, **kw)
    return out, (time.monotonic() - t0) * 1000.0
