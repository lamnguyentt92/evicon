"""Release 069: the reference Temporal worker's workflow must pass the Temporal workflow sandbox.

068 built a urllib opener at import time; the sandbox re-imports the module to validate the
workflow, refused `urllib.request.ProxyHandler()` there, and the worker exited at start
(`measure temporal` FAIL on the testbed). This validates the workflow exactly as
temporalio.worker.Worker does, without a Temporal server.
"""
import asyncio
import importlib
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner
    from temporalio.workflow import _Definition
except ImportError:  # temporalio is installed on VM-C (vm-c.sh); skip elsewhere
    SandboxedWorkflowRunner = None


@unittest.skipIf(SandboxedWorkflowRunner is None, "temporalio not installed")
class TemporalWorkerSandbox(unittest.TestCase):
    def test_workflow_validates_in_sandbox(self):
        mod = importlib.import_module("reference.temporal_worker")
        defn = _Definition.must_from_class(mod.EviconProbeWorkflow)

        async def prepare():  # the sandbox needs a running event loop, as inside Worker
            SandboxedWorkflowRunner().prepare_workflow(defn)  # raises if the module is not sandbox-safe
        asyncio.run(prepare())

    def test_activity_opener_ignores_proxy_environment(self):
        mod = importlib.import_module("reference.temporal_worker")
        with mock.patch.dict(os.environ, {"http_proxy": "http://192.0.2.1:3128", "https_proxy": "http://192.0.2.1:3128"}):
            mod.OPENER = None
            handlers = mod._opener().handlers
        self.assertFalse([h for h in handlers if getattr(h, "proxies", None)], [type(h).__name__ for h in handlers])


if __name__ == "__main__":
    unittest.main()
