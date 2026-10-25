import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
_BIN_DIR = Path(tempfile.mkdtemp(prefix="evicon-probe-bin-"))


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def go_tool():
    """The Go toolchain: `go` on PATH, else the pinned one in .tools/go (VM-C)."""
    found = shutil.which("go")
    if found:
        return found
    pinned = REPO / ".tools" / "go" / "bin" / "go"
    return str(pinned) if pinned.exists() else None


def go_binary(pkg: str) -> Path:
    """Builds ./cmd/<pkg> once per test process; returns the path, or None without Go.
    run_tests.py --require-go turns the resulting skips into a failure."""
    out = _BIN_DIR / pkg
    if out.exists():
        return out
    go = go_tool()
    if go is None:
        return None
    env = dict(os.environ, GOTOOLCHAIN="local", CGO_ENABLED="1")
    subprocess.run([go, "build", "-o", str(out), f"./cmd/{pkg}"], cwd=REPO, check=True, timeout=600, env=env)
    return out


def wait_http(url: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                if r.status == 200:
                    return
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(0.1)
    raise RuntimeError(f"{url} not ready: {last}")


class Proc:
    def __init__(self, args, cwd=None, env=None):
        self.log = tempfile.NamedTemporaryFile(prefix="evicon-probe-proc-", suffix=".log", delete=False)
        self.p = subprocess.Popen(args, cwd=cwd, stdout=self.log, stderr=subprocess.STDOUT, env=env or os.environ.copy())

    def stop(self):
        if self.p.poll() is None:
            self.p.terminate()
            try:
                self.p.wait(5)
            except subprocess.TimeoutExpired:
                self.p.kill()
        self.log.close()

    def output(self) -> str:
        return Path(self.log.name).read_text(errors="replace")
