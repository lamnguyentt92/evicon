"""DBOS Transact reference app for EviCon-Probe (release 068; VM-A, PostgreSQL in docker on VM-B).

  POST /run/<cmd>?value=&device=&wait_s=   start workflow id=<cmd> (DBOS deduplicates the ID),
                                            answer within wait_s: 200 {status} when the workflow
                                            ended, 202 {status} while it runs
  GET  /status/<cmd>                         DBOS workflow status (404 when unknown)
  GET  /healthz                              role dbos-app, release, pid, DBOS version (503 until launched)

The workflow has one step, ``actuate``, which POSTs the physical effect to evicon-plant through
--plant-url (the driver points it at the fault proxy on VM-C, so probes can drop or delay the
step's effect call). The step is configured like the Temporal activity of `measure temporal`:
an HTTP timeout per attempt (--http-timeout, 2 s) and automatic step retries
(--step-max-attempts, 3). DBOS checkpoints a step's *result* after it returns: an attempt whose
effect happened but whose reply was lost or late is retried, which can duplicate the effect, and
the workflow status says nothing about how many effects the step had.

The PostgreSQL URL (with its password) comes from the environment variable DBOS_SYSTEM_DATABASE_URL
and is never on the command line, in a file or in a log:

  DBOS_SYSTEM_DATABASE_URL=postgresql://evicon:<pw>@192.168.107.152:5433/evicon_dbos \
    python3 dbos_app.py --plant-url http://192.168.107.150:28211 --port 18300
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from reference._health import health_body  # noqa: E402

# 068: the effect goes to the plant through VM-C; never through http_proxy from the VM's environment
urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))

from dbos import DBOS, SetWorkflowID  # noqa: E402

ARGS = None
READY = threading.Event()
TERMINAL = ("SUCCESS", "ERROR", "CANCELLED", "MAX_RECOVERY_ATTEMPTS_EXCEEDED")


def _actuate(cmd: str, value: int, device: str) -> str:
    body = json.dumps({"op": "apply", "device": device, "cmd": cmd, "value": value}).encode()
    req = urllib.request.Request(ARGS.plant_url.rstrip("/") + "/v1/apply", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "Connection": "close"})
    with urllib.request.urlopen(req, timeout=ARGS.http_timeout) as r:
        return r.read().decode()


def build_workflow(max_attempts: int):
    """The step and the workflow, registered once DBOS is configured (the decorator options come
    from the command line)."""
    step = DBOS.step(name="actuate", retries_allowed=max_attempts > 1, max_attempts=max(1, max_attempts),
                     interval_seconds=0.2, backoff_rate=1.0)(_actuate)

    @DBOS.workflow(name="evicon_probe_workflow")
    def run(cmd: str, value: int, device: str) -> str:
        return step(cmd, value, device)
    return run


class H(BaseHTTPRequestHandler):
    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        u = urlparse(self.path)
        if not READY.is_set():
            return self._json(503, {"error": "DBOS not launched yet"})
        if not u.path.startswith("/run/"):
            return self._json(404, {"error": "not found"})
        cmd = u.path[len("/run/"):]
        q = parse_qs(u.query)
        value, device = int(q.get("value", ["1"])[0]), q.get("device", ["dbos-1"])[0]
        wait_s = float(q.get("wait_s", ["2"])[0])
        try:
            with SetWorkflowID(cmd):
                h = DBOS.start_workflow(WORKFLOW, cmd, value, device)
        except Exception as e:  # noqa: BLE001 - reported to the probe
            return self._json(500, {"error": f"{type(e).__name__}: {e}"[:300]})
        deadline = time.monotonic() + wait_s
        while True:
            st = h.get_status().status
            if st in TERMINAL:
                return self._json(200, {"status": st})
            if time.monotonic() >= deadline:
                return self._json(202, {"status": st})
            time.sleep(0.05)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/healthz":
            data = health_body("dbos-app", lambda: {"dbos": importlib.metadata.version("dbos"), "plant_url": ARGS.plant_url,
                                                    "step_max_attempts": ARGS.step_max_attempts, "http_timeout_s": ARGS.http_timeout})
            self.send_response(200 if READY.is_set() else 503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if not u.path.startswith("/status/"):
            return self._json(404, {"error": "not found"})
        st = DBOS.get_workflow_status(u.path[len("/status/"):])
        if st is None:
            return self._json(404, {"error": "unknown workflow"})
        return self._json(200, {"status": st.status, "recovery_attempts": getattr(st, "recovery_attempts", None)})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--plant-url", required=True)
    ap.add_argument("--port", type=int, default=18300)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--http-timeout", type=float, default=2.0, help="timeout of one step attempt's effect call (s)")
    ap.add_argument("--step-max-attempts", type=int, default=3, help="DBOS step attempts (1 = no automatic retry)")
    ap.add_argument("--app-name", default="evicon-probe")
    ARGS = ap.parse_args()
    url = os.environ.get("DBOS_SYSTEM_DATABASE_URL", "")
    if not url:
        sys.exit("DBOS_SYSTEM_DATABASE_URL is not set (postgresql://user:password@host:port/db)")
    srv = ThreadingHTTPServer((ARGS.bind, ARGS.port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()  # /healthz answers 503 while DBOS launches
    DBOS(config={"name": ARGS.app_name, "system_database_url": url})
    WORKFLOW = build_workflow(ARGS.step_max_attempts)
    DBOS.launch()
    READY.set()
    print(f"dbos_app :{ARGS.port} dbos {importlib.metadata.version('dbos')} plant={ARGS.plant_url} "
          f"step_max_attempts={ARGS.step_max_attempts} http_timeout={ARGS.http_timeout}s", flush=True)
    threading.Event().wait()
