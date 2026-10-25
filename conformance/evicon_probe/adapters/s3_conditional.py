"""S3 conditional writes (MinIO on VM-B): PUT with If-None-Match: *.

Owner: the object store. Identity: object key = command ID. Effect: an
object *version* written by the command (bucket versioning on, metadata
x-amz-meta-evicon-kind=effect). With ``conditional: true`` a second PUT fails
412, HEAD is a receipt query, and writing a tombstone under the key first is
a per-identity stop (the late PUT then fails 412). With ``conditional:
false`` every PUT creates a new version (duplicate effect).

Dependency-free AWS Signature V4 (tested against the AWS documentation
example in tests/test_adapters_offline.py). Status against MinIO: not run in
this release.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from ..model import APPLIED, ERROR, NOT_FOUND, OK, STOPPED, TIMEOUT, UNKNOWN, ClientObs, EvidenceObs, Unsupported
from .base import STATUS_UNTESTED, Adapter

EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def _q(s: str, safe: str = "-_.~") -> str:
    return urllib.parse.quote(s, safe=safe)


def sigv4_headers(method: str, url: str, headers: dict, payload_sha256: str, access_key: str, secret_key: str,
                  region: str = "us-east-1", service: str = "s3", now: Optional[dt.datetime] = None) -> dict:
    """Returns the headers (including Authorization) for an S3 SigV4 request."""
    now = now or dt.datetime.now(dt.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date = now.strftime("%Y%m%d")
    u = urllib.parse.urlsplit(url)
    host = u.netloc
    h = {k.lower(): str(v).strip() for k, v in headers.items()}
    h["host"] = host
    h["x-amz-date"] = amz_date
    h["x-amz-content-sha256"] = payload_sha256
    canon_uri = _q(u.path or "/", safe="/-_.~")
    pairs = urllib.parse.parse_qsl(u.query, keep_blank_values=True)
    canon_query = "&".join(f"{_q(k)}={_q(v)}" for k, v in sorted(pairs))
    signed = sorted(h)
    canon_headers = "".join(f"{k}:{re.sub(r' +', ' ', h[k])}\n" for k in signed)
    signed_headers = ";".join(signed)
    creq = "\n".join([method, canon_uri, canon_query, canon_headers, signed_headers, payload_sha256])
    scope = f"{date}/{region}/{service}/aws4_request"
    sts = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(creq.encode()).hexdigest()])
    k = hmac.new(("AWS4" + secret_key).encode(), date.encode(), hashlib.sha256).digest()
    for part in (region, service, "aws4_request"):
        k = hmac.new(k, part.encode(), hashlib.sha256).digest()
    sig = hmac.new(k, sts.encode(), hashlib.sha256).hexdigest()
    out = {kk: vv for kk, vv in h.items() if kk != "host"}
    out["Authorization"] = f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={signed_headers}, Signature={sig}"
    return out


class S3ConditionalPutAdapter(Adapter):
    system = "MinIO (S3 API)"
    operation = "PUT object"
    implementation_status = STATUS_UNTESTED
    references = ["https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html",
                  "https://docs.aws.amazon.com/AmazonS3/latest/API/sig-v4-header-based-auth.html"]

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.data_url = cfg["data_url"].rstrip("/")
        self.control_url = cfg.get("control_url", cfg["data_url"]).rstrip("/")
        self.bucket = cfg.get("bucket", "evicon-probe")
        self.access, self.secret = cfg["access_key"], cfg["secret_key"]
        self.region = cfg.get("region", "us-east-1")
        self.conditional = bool(cfg.get("conditional", True))
        self.operation = "PUT object " + ("If-None-Match: *" if self.conditional else "(unconditional)")
        self.declared = cfg.get("declared", {"D_h": -1, "Q_A": True} if self.conditional else {"Q_A": True})

    def _req(self, method, base, path, body=b"", headers=None, timeout=5.0):
        url = f"{base}{path}"
        headers = dict(headers or {})
        if method in ("PUT", "POST"):
            # urllib would add an unsigned form Content-Type to every request with a body (067: some
            # S3 servers then misroute the request); send an explicit, signed one
            headers.setdefault("Content-Type", "application/octet-stream")
        h = sigv4_headers(method, url, headers, hashlib.sha256(body).hexdigest(), self.access, self.secret, self.region)
        h["Connection"] = "close"
        req = urllib.request.Request(url, data=body if method in ("PUT", "POST") else None, method=method, headers=h)
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, dict(r.headers), r.read(), (time.monotonic() - t0) * 1000
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read(), (time.monotonic() - t0) * 1000
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as e:
            return None, {}, str(e).encode(), (time.monotonic() - t0) * 1000

    def setup(self) -> None:
        self._req("PUT", self.control_url, f"/{self.bucket}")
        xml = b'<VersioningConfiguration xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><Status>Enabled</Status></VersioningConfiguration>'
        md5 = base64.b64encode(hashlib.md5(xml).digest()).decode()  # some S3 servers require it on bucket configuration
        st, _, body, _ = self._req("PUT", self.control_url, f"/{self.bucket}?versioning", xml,
                                   {"Content-Type": "application/xml", "Content-MD5": md5})
        if st != 200:
            raise RuntimeError(f"cannot enable versioning: {st} {body[:200]!r}")

    def _put(self, base, key, kind, timeout):
        h = {"x-amz-meta-evicon-kind": kind, "Content-Type": "application/json"}
        if self.conditional or kind == "tombstone":
            h["If-None-Match"] = "*"
        return self._req("PUT", base, f"/{self.bucket}/{_q(key)}", b'{"value":%d}' % int(self.value), h, timeout)

    def send(self, command_id: str, timeout_s: Optional[float] = None) -> ClientObs:
        st, _, body, ms = self._put(self.data_url, command_id, "effect", timeout_s or self.client_timeout_s)
        if st is None:
            return ClientObs(TIMEOUT, UNKNOWN, ms, detail=body.decode(errors="replace")[:200])
        if st == 200:
            return ClientObs(OK, APPLIED, ms)
        if st == 412:
            return ClientObs(OK, "PRECONDITION_FAILED", ms, detail="object exists")
        return ClientObs(ERROR if st < 500 else TIMEOUT, UNKNOWN, ms, detail=f"HTTP {st}")

    def query(self, command_id: str) -> EvidenceObs:
        st, h, _, ms = self._req("HEAD", self.control_url, f"/{self.bucket}/{_q(command_id)}")
        if st == 404:
            return EvidenceObs(True, NOT_FOUND, False, ms)
        if st != 200:
            return EvidenceObs(True, UNKNOWN, False, ms, detail=f"HTTP {st}")
        kind = {k.lower(): v for k, v in h.items()}.get("x-amz-meta-evicon-kind", "")
        o = APPLIED if kind == "effect" else STOPPED if kind == "tombstone" else UNKNOWN
        return EvidenceObs(True, o, o in (APPLIED, STOPPED) and self.conditional, ms, detail=f"kind={kind}")

    def stop(self, command_id: str) -> EvidenceObs:
        if not self.conditional:
            raise Unsupported("unconditional PUT cannot be fenced per key")
        st, _, _, ms = self._put(self.control_url, command_id, "tombstone", 5.0)
        if st == 200:
            return EvidenceObs(True, STOPPED, True, ms, detail="tombstone written (If-None-Match)")
        q = self.query(command_id)
        q.latency_ms += ms
        return q

    def effects(self, command_id: str, baseline) -> int:
        st, _, body, _ = self._req("GET", self.control_url, f"/{self.bucket}?prefix={_q(command_id)}&versions=")
        if st != 200:
            raise RuntimeError(f"list versions failed: {st}")
        text = body.decode(errors="replace")
        n = 0
        for block in re.findall(r"<Version>(.*?)</Version>", text, re.S):
            key = re.search(r"<Key>(.*?)</Key>", block).group(1)
            vid = re.search(r"<VersionId>(.*?)</VersionId>", block).group(1)
            if key != command_id:
                continue
            hs, h, _, _ = self._req("HEAD", self.control_url, f"/{self.bucket}/{_q(key)}?versionId={_q(vid)}")
            if hs == 200 and {k.lower(): v for k, v in h.items()}.get("x-amz-meta-evicon-kind") == "effect":
                n += 1
        return n
