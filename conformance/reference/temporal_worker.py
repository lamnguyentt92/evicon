"""Temporal worker for the EviCon-Probe Temporal adapter (VM-A or VM-D).

The activity POSTs the effect to evicon-plant via --plant-url, which should
point at the fault proxy (inside the probe on VM-C) so probes can delay or drop
the activity's effect call. Activity timeouts/retries are configurable.

  python3 temporal_worker.py --temporal 192.168.107.153:7243 --plant-url http://192.168.107.150:28210
  (activity start-to-close timeout and retry attempts are workflow arguments
   chosen by the probe config: adapter_config.start_to_close_s / max_attempts)
--health-port serves GET /healthz (role temporal-worker, release, pid) for the
reviewer driver (release 065).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.request
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import RetryPolicy
from temporalio.worker import Worker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from reference._health import serve_health  # noqa: E402

ARGS = None
# The effect goes to the plant through VM-C, never through http_proxy from the VM's environment.
# 069: the opener is made in __main__ / lazily in the activity, not at import time: the Temporal
# workflow sandbox re-imports this module to validate the workflow and refuses
# urllib.request.ProxyHandler() there (068 made it at import time and the worker did not start).
OPENER = None


def _opener():
    global OPENER
    if OPENER is None:
        OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return OPENER
STATE = {"worker_running": False}


@activity.defn(name="actuate")
def actuate(cmd: str, value: int, device: str) -> str:
    body = json.dumps({"op": "apply", "device": device, "cmd": cmd, "value": value}).encode()
    req = urllib.request.Request(ARGS.plant_url.rstrip("/") + "/v1/apply", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    with _opener().open(req, timeout=ARGS.http_timeout) as r:
        return r.read().decode()


@workflow.defn(name="EviconProbeWorkflow")
class EviconProbeWorkflow:
    @workflow.run
    async def run(self, cmd: str, value: int, device: str, start_to_close_s: float, max_attempts: int) -> str:
        # timeouts come in as arguments: workflow code must stay deterministic
        return await workflow.execute_activity(
            "actuate", args=[cmd, value, device],
            start_to_close_timeout=timedelta(seconds=start_to_close_s),
            retry_policy=RetryPolicy(maximum_attempts=max_attempts))


async def main():
    from concurrent.futures import ThreadPoolExecutor
    client = await Client.connect(ARGS.temporal, namespace=ARGS.namespace)
    with ThreadPoolExecutor(max_workers=8) as pool:
        w = Worker(client, task_queue=ARGS.task_queue, workflows=[EviconProbeWorkflow], activities=[actuate], activity_executor=pool)
        print(f"temporal_worker task_queue={ARGS.task_queue} plant={ARGS.plant_url}", flush=True)
        STATE["worker_running"] = True
        try:
            await w.run()
        finally:
            STATE["worker_running"] = False


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--temporal", default="127.0.0.1:7233")
    ap.add_argument("--namespace", default="default")
    ap.add_argument("--task-queue", default="evicon-probe")
    ap.add_argument("--plant-url", required=True)
    ap.add_argument("--http-timeout", type=float, default=30.0)
    ap.add_argument("--health-port", type=int, default=0, help="HTTP GET /healthz (role, release, pid, worker state)")
    ap.add_argument("--health-bind", default="0.0.0.0")
    ARGS = ap.parse_args()
    if ARGS.health_port:
        # 200 only once the worker polls its task queue (the driver waits for it)
        serve_health(ARGS.health_port, "temporal-worker", lambda: {"ok": STATE["worker_running"], "worker_running": STATE["worker_running"],
                                                                     "task_queue": ARGS.task_queue, "temporal": ARGS.temporal,
                                                                     "plant_url": ARGS.plant_url}, ARGS.health_bind,
                     ready=lambda: STATE["worker_running"])
    asyncio.run(main())
