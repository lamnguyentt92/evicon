"""EviCon-Probe: evidence-contract measurement harness."""
import urllib.request as _urllib_request
from pathlib import Path as _Path

_version_file = _Path(__file__).resolve().parents[2] / "VERSION"
__version__ = _version_file.read_text().strip() if _version_file.exists() else "unknown"


def use_direct_http() -> None:
    """Send every urllib request directly, ignoring http_proxy/https_proxy.

    All EviCon traffic goes to testbed peers (127.0.0.1, the five VMs). Lab VMs
    often set a proxy for package downloads; urllib would then route requests
    for 192.168.x.y through it, and the failures would look like owner faults.
    Tools that must download something from the Internet run as separate
    processes and keep their proxy settings."""
    _urllib_request.install_opener(_urllib_request.build_opener(_urllib_request.ProxyHandler({})))


use_direct_http()
