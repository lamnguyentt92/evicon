"""/healthz for the reference apps (release 065).

The reviewer driver starts a reference app and then requires its /healthz to
name the expected role, this release and the pid it started, so a port held by
another program (an older deployment) is never measured instead.
"""
from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def release() -> str:
    for p in (Path(__file__).resolve().parents[2] / "VERSION", Path(__file__).resolve().parents[1] / "VERSION"):
        if p.is_file():
            return p.read_text().strip()
    return "unknown"


def health_body(role: str, extra=None) -> bytes:
    body = {"ok": True, "role": role, "pid": os.getpid(), "release": release()}
    if extra is not None:
        body.update(extra())
    return (json.dumps(body) + "\n").encode()


def serve_health(port: int, role: str, extra=None, bind: str = "0.0.0.0", ready=None) -> ThreadingHTTPServer:
    """GET /healthz on its own port, in a daemon thread; 503 until ready() is true."""
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.split("?", 1)[0] != "/healthz":
                self.send_error(404)
                return
            data = health_body(role, extra)
            self.send_response(200 if ready is None or ready() else 503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer((bind, port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
