"""Temporal workflow whose activity performs one physical effect.

Owner of the effect: evicon-plant, reached by the activity worker
(conformance/reference/temporal_worker.py) through the fault proxy, so the
fault sits between the activity and the effect - where an activity
start-to-close timeout and a late completion interact (HM4). Identity:
workflow ID = command ID with REJECT_DUPLICATE (deduplicated while the
execution is retained). ``describe`` is a query on the *workflow*, not on the
effect: COMPLETED is APPLIED evidence only if truth agrees; a retried
activity after a timeout typically duplicates the effect.

Status: temporalio Python SDK API; not run in this release (Temporal runs on
VM-D in the testbed).
"""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Optional

from ..model import APPLIED, CANCELED, ERROR, NOT_FOUND, OK, TIMEOUT, UNKNOWN, ClientObs, EvidenceObs
from ..plant import PlantClient
from .base import STATUS_TESTED_TESTBED, Adapter


class TemporalActivityAdapter(Adapter):
    system = "Temporal"
    operation = "workflow(id=cmd) -> activity actuate"
    implementation_status = STATUS_TESTED_TESTBED  # testbed: Temporal 1.27.1 (065-067)
    references = ["https://docs.temporal.io/activities#activity-timeouts", "https://docs.temporal.io/workflows#workflow-id-reuse-policy"]

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        from temporalio.client import Client  # optional dependency
        self._Client = Client
        self.target = cfg.get("temporal", "192.168.107.153:7243")
        self.namespace = cfg.get("namespace", "default")
        self.task_queue = cfg.get("task_queue", "evicon-probe")
        self.device_id = cfg.get("plant_device_id", "temporal-1")
        self.plant = PlantClient(url=cfg["plant_url"])
        self.declared = cfg.get("declared", {"D_h": -1, "Q_A": True})
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True).start()
        self.client = None

    def _run(self, coro, timeout=None):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    def setup(self) -> None:
        self.client = self._run(self._Client.connect(self.target, namespace=self.namespace), 30)

    def send(self, command_id: str, timeout_s: Optional[float] = None) -> ClientObs:
        from temporalio.common import WorkflowIDReusePolicy
        from temporalio.exceptions import WorkflowAlreadyStartedError
        t = timeout_s or self.client_timeout_s

        async def go():
            try:
                h = await self.client.start_workflow("EviconProbeWorkflow", args=[command_id, int(self.value), self.device_id,
                                                                                    float(self.cfg.get("start_to_close_s", 2.0)), int(self.cfg.get("max_attempts", 3))],
                                                     id=command_id, task_queue=self.task_queue,
                                                     id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE)
            except WorkflowAlreadyStartedError:
                h = self.client.get_workflow_handle(command_id)
            return await asyncio.wait_for(h.result(), t)
        t0 = time.monotonic()
        try:
            out = self._run(go(), t + 2)
            return ClientObs(OK, APPLIED, (time.monotonic() - t0) * 1000, raw=out)
        except (asyncio.TimeoutError, TimeoutError):
            return ClientObs(TIMEOUT, UNKNOWN, (time.monotonic() - t0) * 1000, detail="workflow result not ready")
        except Exception as e:  # WorkflowFailureError etc.
            return ClientObs(ERROR, UNKNOWN, (time.monotonic() - t0) * 1000, detail=f"{type(e).__name__}: {e}"[:300])

    def query(self, command_id: str) -> EvidenceObs:
        async def go():
            return await self.client.get_workflow_handle(command_id).describe()
        t0 = time.monotonic()
        try:
            d = self._run(go(), 10)
        except Exception as e:
            return EvidenceObs(True, NOT_FOUND if "not found" in str(e).lower() else UNKNOWN, False, (time.monotonic() - t0) * 1000, detail=type(e).__name__)
        name = getattr(d.status, "name", str(d.status))
        o = {"COMPLETED": APPLIED, "CANCELED": CANCELED, "TERMINATED": CANCELED}.get(name, "PENDING" if name == "RUNNING" else UNKNOWN)
        return EvidenceObs(True, o, o == APPLIED, (time.monotonic() - t0) * 1000, detail=name)

    def stop(self, command_id: str) -> EvidenceObs:
        async def go():
            await self.client.get_workflow_handle(command_id).cancel()
        t0 = time.monotonic()
        try:
            self._run(go(), 10)
        except Exception as e:
            return EvidenceObs(True, UNKNOWN, False, (time.monotonic() - t0) * 1000, detail=type(e).__name__)
        # Cancellation is a request to the workflow, not owner evidence about the effect.
        return EvidenceObs(True, CANCELED, False, (time.monotonic() - t0) * 1000)

    def effects(self, command_id: str, baseline) -> int:
        return self.plant.effects(command_id, self.device_id)
