"""Minimal S3 client for experiment C (release 073): the owner gateway and the truth reader.

Signs with the dependency-free AWS Signature V4 of the measured adapter
(evicon_probe/adapters/s3_conditional.py, release 067, checked against the AWS documentation
example in tests/test_adapters_offline.py), so the gateway calls MinIO exactly the way the 070
measurement did. Credentials come from the caller (the gateway and the runner read them from
their environment); they are never logged.

Every call returns S3Reply(status, headers, body, ms, error): status None means the request got
no HTTP answer (timeout or connection error; error says which). Nothing here deletes anything.
"""
from __future__ import annotations

import base64
import hashlib
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

from evicon_probe.adapters.s3_conditional import sigv4_headers

TIMEOUT = "TIMEOUT"
UNREACHABLE = "UNREACHABLE"


@dataclass
class S3Reply:
    status: Optional[int]
    headers: dict = field(default_factory=dict)
    body: bytes = b""
    ms: float = 0.0
    error: str = ""  # TIMEOUT or UNREACHABLE when status is None

    def meta(self, name: str) -> str:
        """x-amz-meta-<name>, case-insensitive."""
        want = f"x-amz-meta-{name}".lower()
        return next((v for k, v in self.headers.items() if k.lower() == want), "")


def quote_key(key: str) -> str:
    return urllib.parse.quote(key, safe="/-_.~")


class S3:
    def __init__(self, base_url: str, access_key: str, secret_key: str, region: str = "us-east-1"):
        self.base = base_url.rstrip("/")
        self.access, self.secret, self.region = access_key, secret_key, region

    def request(self, method: str, path: str, body: bytes = b"", headers: Optional[dict] = None, timeout: float = 5.0) -> S3Reply:
        url = f"{self.base}{path}"
        headers = dict(headers or {})
        if method in ("PUT", "POST"):
            headers.setdefault("Content-Type", "application/octet-stream")
        h = sigv4_headers(method, url, headers, hashlib.sha256(body).hexdigest(), self.access, self.secret, self.region)
        h["Connection"] = "close"
        req = urllib.request.Request(url, data=body if method in ("PUT", "POST") else None, method=method, headers=h)
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=max(0.05, timeout)) as r:
                return S3Reply(r.status, dict(r.headers), r.read(), (time.monotonic() - t0) * 1000)
        except urllib.error.HTTPError as e:
            try:
                data = e.read()
            except (OSError, socket.timeout):
                data = b""
            return S3Reply(e.code, dict(e.headers or {}), data, (time.monotonic() - t0) * 1000)
        except (socket.timeout, TimeoutError) as e:
            return S3Reply(None, {}, str(e).encode(), (time.monotonic() - t0) * 1000, TIMEOUT)
        except urllib.error.URLError as e:
            kind = TIMEOUT if isinstance(e.reason, (socket.timeout, TimeoutError)) else UNREACHABLE
            return S3Reply(None, {}, str(e).encode(), (time.monotonic() - t0) * 1000, kind)
        except OSError as e:  # reset by peer, broken pipe, ...
            return S3Reply(None, {}, str(e).encode(), (time.monotonic() - t0) * 1000, UNREACHABLE)

    # ---- objects ---------------------------------------------------------
    def put(self, bucket: str, key: str, body: bytes, meta: dict, if_none_match: bool, timeout: float) -> S3Reply:
        h = {f"x-amz-meta-{k}": str(v) for k, v in meta.items()}
        h["Content-Type"] = "application/json"
        if if_none_match:
            h["If-None-Match"] = "*"
        return self.request("PUT", f"/{bucket}/{quote_key(key)}", body, h, timeout)

    def head(self, bucket: str, key: str, timeout: float, version_id: str = "") -> S3Reply:
        q = f"?versionId={urllib.parse.quote(version_id, safe='')}" if version_id else ""
        return self.request("HEAD", f"/{bucket}/{quote_key(key)}{q}", timeout=timeout)

    # ---- buckets ---------------------------------------------------------
    def create_versioned_bucket(self, bucket: str, timeout: float = 10.0) -> None:
        r = self.request("PUT", f"/{bucket}", timeout=timeout)
        if r.status not in (200, 409):  # 409: BucketAlreadyOwnedByYou
            raise RuntimeError(f"create bucket {bucket}: HTTP {r.status} {r.error} {r.body[:200]!r}")
        xml = b'<VersioningConfiguration xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><Status>Enabled</Status></VersioningConfiguration>'
        md5 = base64.b64encode(hashlib.md5(xml).digest()).decode()
        r = self.request("PUT", f"/{bucket}?versioning", xml, {"Content-Type": "application/xml", "Content-MD5": md5}, timeout)
        if r.status != 200:
            raise RuntimeError(f"enable versioning on {bucket}: HTTP {r.status} {r.error} {r.body[:200]!r}")
        r = self.request("GET", f"/{bucket}?versioning", timeout=timeout)
        if r.status != 200 or b"<Status>Enabled</Status>" not in r.body:
            raise RuntimeError(f"versioning of {bucket} not enabled: HTTP {r.status} {r.body[:200]!r}")

    def list_versions(self, bucket: str, prefix: str = "", timeout: float = 30.0) -> list:
        """Every object version and delete marker under prefix, all pages:
        [{"key", "version_id", "size", "last_modified", "is_latest", "delete_marker"}]."""
        out, key_marker, vid_marker = [], "", ""
        for _ in range(10000):
            q = {"versions": "", "prefix": prefix, "max-keys": "1000"}
            if key_marker:
                q["key-marker"] = key_marker
            if vid_marker:
                q["version-id-marker"] = vid_marker
            # every parameter as k=v, also the empty ones ("versions=", "prefix="), as the measured adapter sends them
            qs = "&".join(f"{k}={urllib.parse.quote(v, safe='')}" for k, v in sorted(q.items()))
            r = self.request("GET", f"/{bucket}?{qs}", timeout=timeout)
            if r.status != 200:
                raise RuntimeError(f"list versions of {bucket}: HTTP {r.status} {r.error} {r.body[:200]!r}")
            text = r.body.decode(errors="replace")
            for tag, dm in (("Version", False), ("DeleteMarker", True)):
                for block in re.findall(rf"<{tag}>(.*?)</{tag}>", text, re.S):
                    def g(name, block=block):
                        m = re.search(rf"<{name}>(.*?)</{name}>", block, re.S)
                        return m.group(1) if m else ""
                    out.append({"key": _unxml(g("Key")), "version_id": g("VersionId"), "size": int(g("Size") or 0),
                                "last_modified": g("LastModified"), "is_latest": g("IsLatest") == "true", "delete_marker": dm})
            if "<IsTruncated>true</IsTruncated>" not in text:
                return out
            key_marker = _unxml((re.search(r"<NextKeyMarker>(.*?)</NextKeyMarker>", text, re.S) or [None, ""])[1])
            vid_marker = (re.search(r"<NextVersionIdMarker>(.*?)</NextVersionIdMarker>", text, re.S) or [None, ""])[1]
            if not key_marker:
                raise RuntimeError(f"list versions of {bucket}: truncated page without NextKeyMarker")
        raise RuntimeError(f"list versions of {bucket}: too many pages")


def _unxml(s: str) -> str:
    return s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'").replace("&amp;", "&")
