#!/usr/bin/env python3
"""Checks, from VM-C, that the Temporal server on VM-D serves and has the namespace the
probes use, and creates the namespace when it is missing (EviCon 065, setup fix of 15/10).

The check runs from VM-C with the Python SDK, the same path `evicon_review.py measure
temporal` uses, so it does not depend on Docker networking on VM-D. (065 setup: the server
on VM-D was healthy, but a `--network host` admin-tools container could not reach it.)

  python3 deploy/setup/temporal_check.py --address 192.168.107.153:7233 [--namespace default] [--create]

Exit codes: 0 serving and the namespace exists (or was created); 2 not serving;
3 namespace missing (without --create); 4 creating the namespace failed.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time


def _say(msg: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


async def _check(addr: str, ns: str, create: bool, wait_s: float, retention_h: int) -> int:
    from temporalio.api.workflowservice.v1 import (DescribeNamespaceRequest, GetSystemInfoRequest, ListNamespacesRequest,
                                                   RegisterNamespaceRequest)
    from temporalio.client import Client
    from google.protobuf.duration_pb2 import Duration

    deadline = time.monotonic() + wait_s
    last = ""
    while True:
        try:
            c = await asyncio.wait_for(Client.connect(addr), 15)
            info = await asyncio.wait_for(c.workflow_service.get_system_info(GetSystemInfoRequest()), 15)
            listed = await asyncio.wait_for(c.workflow_service.list_namespaces(ListNamespacesRequest(page_size=100)), 15)
            names = sorted(n.namespace_info.name for n in listed.namespaces)
            break
        except Exception as e:  # noqa: BLE001 - reported below
            last = f"{type(e).__name__}: {' '.join(str(e).split())[:300]}"
            if time.monotonic() >= deadline:
                _say(f"FAIL: Temporal at {addr} does not answer from this host ({last})")
                return 2
            _say(f"waiting for Temporal at {addr} ({last[:120]})")
            await asyncio.sleep(5)
    _say(f"Temporal at {addr} SERVING (server {info.server_version or '?'}); namespaces: {', '.join(names) or '-'}")
    if ns in names:
        _say(f"namespace {ns} exists")
        return 0
    if not create:
        _say(f"FAIL: namespace {ns} missing (run again with --create)")
        return 3
    try:
        await c.workflow_service.register_namespace(RegisterNamespaceRequest(
            namespace=ns, workflow_execution_retention_period=Duration(seconds=retention_h * 3600)))
    except Exception as e:  # noqa: BLE001
        if "already" not in str(e).lower():
            _say(f"FAIL: creating namespace {ns}: {type(e).__name__}: {' '.join(str(e).split())[:300]}")
            return 4
    for _ in range(24):  # a new namespace needs a few seconds before it is visible everywhere
        try:
            await c.workflow_service.describe_namespace(DescribeNamespaceRequest(namespace=ns))
            _say(f"namespace {ns} created")
            return 0
        except Exception:  # noqa: BLE001
            await asyncio.sleep(5)
    _say(f"FAIL: namespace {ns} was registered but cannot be described after 2 min")
    return 4


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--address", required=True, help="host:port of the Temporal frontend, e.g. 192.168.107.153:7233")
    ap.add_argument("--namespace", default="default")
    ap.add_argument("--create", action="store_true", help="create the namespace when it is missing")
    ap.add_argument("--wait", type=float, default=120.0, help="seconds to wait for the server to answer")
    ap.add_argument("--retention-hours", type=int, default=72)
    a = ap.parse_args(argv)
    try:
        import temporalio  # noqa: F401
    except ImportError:
        _say("FAIL: Python module temporalio missing on this host (bash deploy/setup/vm-c.sh)")
        return 2
    return asyncio.run(_check(a.address, a.namespace, a.create, a.wait, a.retention_hours))


if __name__ == "__main__":
    sys.exit(main())
