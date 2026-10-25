"""In-process fake of the S3 calls experiment C makes (release 073 tests only; no network, no auth check).

Versioned buckets, PUT with If-None-Match: * (412 when the key has a version), HEAD of the latest or
of one version (x-amz-meta-* back), ListObjectVersions with prefix and pagination (page size
configurable, to test the client's paging), bucket versioning on/off, GET / (ListBuckets).
Not MinIO: the real semantics were measured on the testbed in release 070.
"""
from __future__ import annotations

import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from xml.sax.saxutils import escape


class FakeS3:
    def __init__(self, page_size: int = 1000):
        self.page_size = page_size
        self.buckets = {}  # name -> {"versioning": bool, "objects": {key: [version, ...]}} (oldest first)
        self.lock = threading.Lock()
        self.requests = []
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.srv.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.srv.shutdown()
        self.srv.server_close()

    # ---- inspection --------------------------------------------------------
    def versions(self, bucket: str, key: str) -> list:
        with self.lock:
            return list(self.buckets.get(bucket, {}).get("objects", {}).get(key, []))

    def put_raw(self, bucket: str, key: str, meta: dict, body: bytes = b"{}", t: float = None):
        """Adds a version directly (test set-up), as a PUT would."""
        with self.lock:
            b = self.buckets.setdefault(bucket, {"versioning": True, "objects": {}})
            b["objects"].setdefault(key, []).append({"vid": uuid.uuid4().hex, "body": body, "t": t or time.time(),
                                                     "meta": {f"x-amz-meta-{k}".lower(): str(v) for k, v in meta.items()}})

    # ---- server ------------------------------------------------------------
    def _handler(self):
        fake = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def reply(self, code, body=b"", headers=None):
                self.send_response(code)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)
                self.close_connection = True

            def parts(self):
                u = urllib.parse.urlsplit(self.path)
                path = urllib.parse.unquote(u.path)
                bucket, _, key = path.lstrip("/").partition("/")
                return bucket, key, urllib.parse.parse_qs(u.query, keep_blank_values=True)

            def do_GET(self):
                bucket, key, q = self.parts()
                fake.requests.append(("GET", self.path))
                if not bucket:
                    names = "".join(f"<Bucket><Name>{escape(n)}</Name></Bucket>" for n in sorted(fake.buckets))
                    return self.reply(200, f"<ListAllMyBucketsResult><Buckets>{names}</Buckets></ListAllMyBucketsResult>".encode())
                with fake.lock:
                    b = fake.buckets.get(bucket)
                    if b is None:
                        return self.reply(404, b"<Error><Code>NoSuchBucket</Code></Error>")
                    if "versioning" in q:
                        st = "<Status>Enabled</Status>" if b["versioning"] else ""
                        return self.reply(200, f"<VersioningConfiguration>{st}</VersioningConfiguration>".encode())
                    if "versions" in q:
                        return self.reply(200, fake._list(b, q).encode(), {"Content-Type": "application/xml"})
                self.reply(400, b"<Error><Code>NotImplemented</Code></Error>")

            def do_HEAD(self):
                bucket, key, q = self.parts()
                fake.requests.append(("HEAD", self.path))
                with fake.lock:
                    vs = fake.buckets.get(bucket, {}).get("objects", {}).get(key, [])
                    vid = (q.get("versionId") or [""])[0]
                    v = next((x for x in vs if x["vid"] == vid), None) if vid else (vs[-1] if vs else None)
                if v is None:
                    return self.reply(404)
                self.send_response(200)
                for k, val in v["meta"].items():
                    self.send_header(k, val)
                self.send_header("x-amz-version-id", v["vid"])
                self.send_header("Content-Length", str(len(v["body"])))
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True

            def do_PUT(self):
                bucket, key, q = self.parts()
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                fake.requests.append(("PUT", self.path))
                with fake.lock:
                    if not key:
                        if "versioning" in q:
                            if bucket not in fake.buckets:
                                return self.reply(404, b"<Error><Code>NoSuchBucket</Code></Error>")
                            fake.buckets[bucket]["versioning"] = b"<Status>Enabled</Status>" in body
                            return self.reply(200)
                        if bucket in fake.buckets:
                            return self.reply(409, b"<Error><Code>BucketAlreadyOwnedByYou</Code></Error>")
                        fake.buckets[bucket] = {"versioning": False, "objects": {}}
                        return self.reply(200)
                    b = fake.buckets.get(bucket)
                    if b is None:
                        return self.reply(404, b"<Error><Code>NoSuchBucket</Code></Error>")
                    vs = b["objects"].setdefault(key, [])
                    if self.headers.get("If-None-Match") == "*" and vs:
                        return self.reply(412, b"<Error><Code>PreconditionFailed</Code></Error>")
                    meta = {k.lower(): v for k, v in self.headers.items() if k.lower().startswith("x-amz-meta-")}
                    v = {"vid": uuid.uuid4().hex if b["versioning"] else "null", "body": body, "t": time.time(), "meta": meta}
                    if b["versioning"]:
                        vs.append(v)
                    else:
                        vs[:] = [v]
                self.reply(200, b"", {"x-amz-version-id": v["vid"], "ETag": '"x"'})

        return H

    def _list(self, b, q) -> str:
        prefix = (q.get("prefix") or [""])[0]
        km, vm = (q.get("key-marker") or [""])[0], (q.get("version-id-marker") or [""])[0]
        page = min(int((q.get("max-keys") or ["1000"])[0]), self.page_size)
        seq = [(k, v) for k in sorted(b["objects"]) if k.startswith(prefix) for v in reversed(b["objects"][k])]  # newest first per key
        if km:
            idx = next((i for i, (k, v) in enumerate(seq) if k == km and v["vid"] == vm), None)
            seq = seq[idx + 1:] if idx is not None else [x for x in seq if x[0] > km]
        out, rest = seq[:page], seq[page:]
        items = []
        for k, v in out:
            lm = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(v["t"])) + f".{int((v['t'] % 1) * 1000):03d}Z"
            latest = b["objects"][k][-1] is v
            items.append(f"<Version><Key>{escape(k)}</Key><VersionId>{v['vid']}</VersionId><IsLatest>{str(latest).lower()}</IsLatest>"
                         f"<LastModified>{lm}</LastModified><Size>{len(v['body'])}</Size></Version>")
        tail = ""
        if rest:
            tail = f"<NextKeyMarker>{escape(out[-1][0])}</NextKeyMarker><NextVersionIdMarker>{out[-1][1]['vid']}</NextVersionIdMarker>"
        return (f"<?xml version=\"1.0\" encoding=\"UTF-8\"?><ListVersionsResult><Prefix>{escape(prefix)}</Prefix>"
                f"<IsTruncated>{'true' if rest else 'false'}</IsTruncated>{tail}{''.join(items)}</ListVersionsResult>")
