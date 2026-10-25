#!/usr/bin/env python3
"""Pinned Temporal CLI (its `server start-dev` is the Temporal server `measure temporal`
starts on VM-D). Release 065, fix of 15/10.

  python3 scripts/ensure_temporal_cli.py                    # -> .tools/temporal (VM-C's architecture)
  python3 scripts/ensure_temporal_cli.py --arch arm64       # -> .tools/temporal-linux-arm64
  python3 scripts/ensure_temporal_cli.py --archive temporal_cli_1.3.0_linux_amd64.tar.gz   # no download

The archive is checked against the SHA-256 published with the release
(github.com/temporalio/cli/releases/tag/v1.3.0, checksums.txt) before anything is
extracted, and the installed binary must report version 1.3.0. Verified archives are
cached in ~/.cache/evicon-toolchains (EVICON_TOOLCHAIN_CACHE overrides it).
Exit codes: 0 ready, 2 failed.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

VERSION = "1.3.0"           # Temporal CLI 1.3.0 = Temporal Server 1.27.1, UI 2.36.0
SERVER_VERSION = "1.27.1"
SHA256 = {
    "amd64": "ca03976fb948b7084f4075dab55afb65915713498577ea68b483a14e3dfcd74e",
    "arm64": "eaa8cf16c5ea5551cd1ee83dd7aa24ce6e9789bfed113a7a2a2dcd7be2a99767",
}
ROOT = Path(__file__).resolve().parents[1]
ARCH = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}


def log(msg):
    print(f"[temporal-cli] {msg}", flush=True)


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def target_for(arch, root=ROOT):
    native = ARCH.get(platform.machine())
    return root / ".tools" / ("temporal" if arch == native else f"temporal-linux-{arch}")


def version_ok(exe):
    try:
        p = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    out = p.stdout.strip()
    return out if p.returncode == 0 and out.startswith(f"temporal version {VERSION} ") else ""


def obtain(arch, explicit=None):
    asset = f"temporal_cli_{VERSION}_linux_{arch}.tar.gz"
    cache = Path(os.environ.get("EVICON_TOOLCHAIN_CACHE") or Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
                 / "evicon-toolchains").expanduser()
    cached = cache / asset
    if explicit:
        p = Path(explicit).expanduser().resolve()
        if not p.is_file() or digest(p) != SHA256[arch]:
            raise RuntimeError(f"{p}: missing, or not the official {asset} (SHA-256 differs)")
        return p
    if cached.is_file() and digest(cached) == SHA256[arch]:
        log(f"reusing verified archive {cached}")
        return cached
    cache.mkdir(parents=True, exist_ok=True)
    url = f"https://github.com/temporalio/cli/releases/download/v{VERSION}/{asset}"
    last = ""
    for attempt in range(1, 5):
        fd, name = tempfile.mkstemp(prefix=asset + ".", suffix=".part", dir=cache)
        os.close(fd)
        tmp = Path(name)
        log(f"download attempt {attempt}/4: {url}")
        try:
            p = subprocess.run(["curl", "--fail", "--location", "--show-error", "--silent", "--proto", "=https",
                                "--connect-timeout", "15", "--max-time", "300", "--output", str(tmp), url],
                               capture_output=True, text=True, timeout=310)
            if p.returncode == 0 and digest(tmp) == SHA256[arch]:
                os.replace(tmp, cached)
                return cached
            last = p.stderr.strip()[-300:] if p.returncode else "downloaded file has the wrong SHA-256"
        except (OSError, subprocess.SubprocessError) as e:
            last = str(e)[-300:]
        finally:
            tmp.unlink(missing_ok=True)
        log(f"attempt {attempt}/4 failed: {last}")
        if attempt < 4:
            time.sleep(3 * attempt)
    raise RuntimeError(f"could not download {url} ({last}). Without Internet on VM-C: download that file on another "
                       f"machine, copy it to VM-C, then run: python3 scripts/ensure_temporal_cli.py --archive <file>")


def install(arch, explicit=None, root=ROOT):
    dest = target_for(arch, root)
    native = arch == ARCH.get(platform.machine())
    if dest.is_file() and not explicit:
        if (native and version_ok(dest)) or (not native and os.access(dest, os.X_OK)):
            return dest
    archive = obtain(arch, explicit)
    if digest(archive) != SHA256[arch]:
        raise RuntimeError("archive changed after verification")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tf:
        member = next((m for m in tf.getmembers() if m.name == "temporal" and m.isfile()), None)
        if member is None:
            raise RuntimeError(f"{archive}: no 'temporal' binary inside")
        fd, name = tempfile.mkstemp(prefix=".temporal-", dir=dest.parent)
        os.close(fd)
        tmp = Path(name)
        try:
            with tf.extractfile(member) as src, open(tmp, "wb") as out:
                shutil.copyfileobj(src, out)
            tmp.chmod(0o755)
            if native and not version_ok(tmp):
                raise RuntimeError("the extracted binary does not run or does not report version " + VERSION)
            os.replace(tmp, dest)
        finally:
            tmp.unlink(missing_ok=True)
    return dest


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--arch", choices=sorted(SHA256), default=None, help="architecture of the host that runs it (default: this host's)")
    ap.add_argument("--archive", help=f"local temporal_cli_{VERSION}_linux_<arch>.tar.gz; still checked against the pinned SHA-256")
    a = ap.parse_args(argv)
    try:
        if platform.system() != "Linux":
            raise RuntimeError("Linux only")
        arch = a.arch or ARCH.get(platform.machine())
        if arch not in SHA256:
            raise RuntimeError(f"unsupported architecture {platform.machine()}")
        dest = install(arch, a.archive)
        v = version_ok(dest) if arch == ARCH.get(platform.machine()) else f"temporal version {VERSION} (linux/{arch}, not run here)"
        log(f"[PASS] {dest} ({v}; sha256 {digest(dest)[:16]})")
        return 0
    except (OSError, RuntimeError, tarfile.TarError) as e:
        log(f"[FAIL] {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
