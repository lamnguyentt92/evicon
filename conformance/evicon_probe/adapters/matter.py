"""Matter Invoke / Timed Invoke via chip-tool against all-clusters-app.

Effect: LevelControl Step (up, step size S) - not idempotent, so a duplicate
invoke is visible. Truth: CurrentLevel read on the control path after the
fault is cleared, divided by S (setup moves the level to 0 first).
Matter invokes carry no client-chosen identity; a resend is a new exchange
(MRP deduplicates retransmissions of one exchange only). With
``timed_ms`` the invoke is a Timed Invoke; P9T measures whether a delayed
exchange is rejected (it only is when the delay falls *between* the Timed
Request and the Invoke, which a uniformly delayed path does not produce).

Status: command lines per connectedhomeip chip-tool; output parsing must be
checked against the SDK version built on VM-A. Not run in this release.
"""
from __future__ import annotations

import re
import shlex
import subprocess
import time
from typing import Optional

from ..model import APPLIED, ERROR, OK, TIMEOUT, UNKNOWN, ClientObs
from .base import STATUS_UNTESTED, Adapter

LEVEL_RE = re.compile(r"(?:CurrentLevel|current-level|Data)\s*[:=]\s*(\d+)")
STATUS_RE = re.compile(r"[Ss]tatus\s*=\s*0x([0-9A-Fa-f]+)")


class MatterInvokeAdapter(Adapter):
    system = "Matter (all-clusters-app)"
    operation = "LevelControl Step invoke"
    implementation_status = STATUS_UNTESTED
    per_identity_truth = False
    references = ["https://github.com/project-chip/connectedhomeip/tree/master/examples/chip-tool",
                  "https://csa-iot.org/developer-resource/specifications-download-request/"]

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.chip_tool = shlex.split(cfg.get("chip_tool", "chip-tool"))
        self.node = str(cfg["node_id"])
        self.endpoint = str(cfg.get("endpoint_id", 1))
        self.step = int(cfg.get("step_size", 1))
        self.timed_ms = cfg.get("timed_ms")
        self.declared = cfg.get("declared", {"T": float(self.timed_ms) / 1000.0} if self.timed_ms else {})
        self.operation = "LevelControl Step " + ("timed invoke" if self.timed_ms else "invoke")
        self._base_level = 0
        self._by_cmd: dict = {}

    def _run(self, args: list, timeout: float):
        t0 = time.monotonic()
        try:
            p = subprocess.run(self.chip_tool + args, capture_output=True, text=True, timeout=timeout)
            return p.returncode, p.stdout + p.stderr, (time.monotonic() - t0) * 1000
        except subprocess.TimeoutExpired as e:
            return None, (e.stdout or "") if isinstance(e.stdout, str) else "", (time.monotonic() - t0) * 1000

    def _level(self) -> int:
        rc, out, _ = self._run(["levelcontrol", "read", "current-level", self.node, self.endpoint], 30)
        m = LEVEL_RE.findall(out or "")
        if rc != 0 or not m:
            raise RuntimeError(f"cannot read CurrentLevel: rc={rc}")
        return int(m[-1])

    def setup(self) -> None:
        self._run(["levelcontrol", "move-to-level", "0", "0", "0", "0", self.node, self.endpoint], 30)
        self._base_level = self._level()

    def send(self, command_id: str, timeout_s: Optional[float] = None) -> ClientObs:
        args = ["levelcontrol", "step", "0", str(self.step), "0", "0", "0", self.node, self.endpoint]
        if self.timed_ms:
            args += ["--timedInteractionTimeoutMs", str(int(self.timed_ms))]
        rc, out, ms = self._run(args, timeout_s or self.client_timeout_s)
        if rc is None:
            return ClientObs(TIMEOUT, UNKNOWN, ms, detail="chip-tool timeout")
        st = STATUS_RE.findall(out or "")
        if rc == 0 and st and int(st[-1], 16) == 0:
            return ClientObs(OK, APPLIED, ms)
        if st:
            return ClientObs(ERROR, UNKNOWN, ms, detail=f"IM status 0x{int(st[-1], 16):x}")
        return ClientObs(TIMEOUT if rc != 0 else ERROR, UNKNOWN, ms, detail=(out or "")[-300:])

    def truth_baseline(self):
        return self._level()

    def effects(self, command_id: str, baseline) -> int:
        # Matter has no identity in the effect: per-probe delta of CurrentLevel.
        return max(0, (self._level() - int(baseline)) // max(1, self.step))

    def expiry_s(self):
        return None if not self.timed_ms else float(self.timed_ms) / 1000.0
