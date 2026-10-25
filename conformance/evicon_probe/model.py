"""Observation records for EviCon-Probe (release 061).

The harness never trusts what the client is told: every probe records the
client-visible outcome *and* the effect count read from a separate truth
channel (owner state read out of band, or the plant emulator). Deviations
between the two are the measurement study's findings.
"""
from __future__ import annotations

import dataclasses
import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional

# Client-visible status of one send.
OK = "OK"            # the client got a definitive success reply
ERROR = "ERROR"      # the client got a definitive error reply
TIMEOUT = "TIMEOUT"  # the client gave up (timeout, reset, 5xx gateway timeout)
CONFLICT = "CONFLICT"  # concurrent same-identity request rejected (e.g. 409)

# Normalised evidence outcomes (mirrors internal/contract Outcome).
APPLIED = "APPLIED"
STOPPED = "STOPPED"
PENDING = "PENDING"
UNCERTAIN = "UNCERTAIN"
NOT_FOUND = "NOT_FOUND"
EXPIRED = "EXPIRED"
CANCELED = "CANCELED"
TIMED_OUT = "TIMED_OUT"
UNKNOWN = "UNKNOWN"
UNSUPPORTED = "UNSUPPORTED"

TERMINAL = {APPLIED, STOPPED}


class Unsupported(Exception):
    """The system/operation has no such interface (e.g. no stop API)."""


@dataclass
class ClientObs:
    status: str
    outcome: str = UNKNOWN
    latency_ms: float = 0.0
    detail: str = ""
    raw: Any = None

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        if not _jsonable(d["raw"]):
            d["raw"] = repr(d["raw"])
        return d


@dataclass
class EvidenceObs:
    """Answer of a query / stop / fence on the control path."""
    supported: bool
    outcome: str = UNKNOWN
    terminal: bool = False
    latency_ms: float = 0.0
    detail: str = ""
    raw: Any = None

    @staticmethod
    def unsupported(detail: str = "") -> "EvidenceObs":
        return EvidenceObs(supported=False, outcome=UNSUPPORTED, detail=detail)

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        if not _jsonable(d["raw"]):
            d["raw"] = repr(d["raw"])
        return d


@dataclass
class ProbeResult:
    probe_id: str
    system: str
    operation: str
    command_id: str
    applicable: bool = True
    skip_reason: str = ""
    started_unix: float = field(default_factory=time.time)
    elapsed_ms: float = 0.0
    # truth
    effects: Optional[int] = None          # effects of this identity after the fault cut
    effects_final: Optional[int] = None    # effects after the probe's follow-up actions
    # client view
    client: Optional[ClientObs] = None
    resend: Optional[ClientObs] = None
    concurrent: list = field(default_factory=list)
    # control path
    query: Optional[EvidenceObs] = None
    query_after: Optional[EvidenceObs] = None
    stop: Optional[EvidenceObs] = None
    fence: Optional[EvidenceObs] = None
    # derived findings (filled by the probe)
    late_effect: Optional[bool] = None       # effect observed after the client saw a failure
    duplicate: Optional[bool] = None         # more than one effect for one identity
    false_stopped: Optional[bool] = None     # STOPPED evidence contradicted by truth
    false_applied: Optional[bool] = None     # APPLIED evidence contradicted by truth
    parameters: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict:
        d = {}
        for f in dataclasses.fields(self):
            v = getattr(self, f.name)
            if isinstance(v, (ClientObs, EvidenceObs)):
                v = v.to_dict()
            elif isinstance(v, list):
                v = [x.to_dict() if isinstance(x, (ClientObs, EvidenceObs)) else x for x in v]
            d[f.name] = v
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)


def _jsonable(x: Any) -> bool:
    try:
        json.dumps(x)
        return True
    except (TypeError, ValueError):
        return False
