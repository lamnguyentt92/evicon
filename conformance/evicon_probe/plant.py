"""Client for the plant emulator (cmd/evicon-plant): the independent truth channel.

Devices, reference apps and workflow activities report physical effects to
the plant over a local socket or HTTP; the harness only *reads* the plant.
The plant never sees receipts, so a crash between effect and receipt is real.
"""
from __future__ import annotations

import json
import socket
import urllib.parse
import urllib.request


class PlantClient:
    def __init__(self, url: str = "", unix: str = "", timeout: float = 5.0):
        if not url and not unix:
            raise ValueError("plant url or unix socket path required")
        self.url = url.rstrip("/")
        self.unix = unix
        self.timeout = timeout

    # ---- writer side (devices, reference apps) -------------------------------
    def apply(self, device: str, cmd: str, value: int = 1, once: bool = False) -> dict:
        msg = {"op": "apply", "device": device, "cmd": cmd, "value": int(value), "once": bool(once)}
        if self.unix:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(self.timeout)
                s.connect(self.unix)
                s.sendall((json.dumps(msg) + "\n").encode())
                buf = b""
                while not buf.endswith(b"\n"):
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
            out = json.loads(buf)
        else:
            req = urllib.request.Request(self.url + "/v1/apply", data=json.dumps(msg).encode(), method="POST",
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                out = json.loads(r.read())
        if not out.get("ok"):
            raise RuntimeError(f"plant apply failed: {out}")
        return out

    # ---- reader side (harness truth) -------------------------------------------
    def effects(self, cmd: str, device: str = "") -> int:
        if not self.url:
            raise ValueError("truth reads need the plant HTTP url")
        q = {"cmd": cmd}
        if device:
            q["device"] = device
        with urllib.request.urlopen(f"{self.url}/v1/truth?{urllib.parse.urlencode(q)}", timeout=self.timeout) as r:
            return int(json.loads(r.read())["effects"])

    def summary(self) -> dict:
        with urllib.request.urlopen(self.url + "/v1/truth/summary", timeout=self.timeout) as r:
            return json.loads(r.read())
