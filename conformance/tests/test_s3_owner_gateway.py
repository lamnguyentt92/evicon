"""S3 owner gateway of experiment C (release 073) against an in-process fake S3.

The gateway runs as a separate process, as in a campaign (credentials only in its environment).
G1 intent hash = Go's protocol.CanonicalHash (vectors printed by the Go code of this release)
G2 COND: effect, replay, conflict, query, stop, effect after a stop, stop after an effect; /d/<id> prefix
G3 PLAIN: a repeated PUT is a second effect version; no query, no stop
G4 offline: the path to S3 drops everything -> 504 within the budget, no version; online again -> served
G5 crash between effect and reply: exit 17, the version exists, a restarted gateway answers the replay
G6 a contract file that does not match the mode is refused
G7 ListObjectVersions paging of the client
"""
import json
import os
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from reference.s3_client import S3
from reference.s3_owner_gateway import canonical_hash
from tests.fake_s3 import FakeS3
from tests.helpers import free_port

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
GATEWAY = REPO / "conformance" / "reference" / "s3_owner_gateway.py"
CONTRACT = {"COND": REPO / "experiments" / "owners" / "minio" / "meas-minio-cond-070.json",
            "PLAIN": REPO / "experiments" / "owners" / "minio" / "meas-minio-plain-070.json"}
ENV = dict(os.environ, EVC_TEST_KEY="test-access", EVC_TEST_SECRET="test-secret")


def call(method, url, body=None, timeout=5.0):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, json.loads(raw) if raw else None
    except (urllib.error.URLError, ConnectionError, OSError) as e:
        return None, str(e)


def cmd(cid, value, seq, dev="pump0-1"):
    return {"schema_version": 1, "domain_id": dev, "generation": 1, "command_id": cid, "operation": "ADD", "value": [value],
            "epoch": 1, "sequence": seq}


def stop(cid, value, seq, dev="pump0-1"):
    return {"schema_version": 1, "domain_id": dev, "generation": 1, "command_id": cid, "intent_hash": canonical_hash(cmd(cid, value, seq, dev)),
            "sequence": seq}


class Gw:
    def __init__(self, s3url, bucket, mode, tmp, budget=1.8, contract=None):
        self.port, self.aport = free_port(), free_port()
        self.argv = [sys.executable, str(GATEWAY), "--mode", mode, "--device-id", "pump0-1", "--listen", f"127.0.0.1:{self.port}",
                     "--admin-listen", f"127.0.0.1:{self.aport}", "--s3-url", s3url, "--bucket", bucket,
                     "--contract-file", str(contract or CONTRACT[mode]), "--budget-s", str(budget), "--access-key-env", "EVC_TEST_KEY",
                     "--secret-key-env", "EVC_TEST_SECRET", "--log", str(Path(tmp) / f"gw-{mode}.jsonl")]
        self.url = f"http://127.0.0.1:{self.port}"
        self.adm = f"http://127.0.0.1:{self.aport}"
        self.p = None

    def start(self):
        self.p = subprocess.Popen(self.argv, env=ENV, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        t0 = time.monotonic()
        while time.monotonic() - t0 < 15:
            if self.p.poll() is not None:
                raise RuntimeError(f"gateway exited: {self.p.stdout.read().decode(errors='replace')}")
            st, out = call("GET", self.url + "/healthz", timeout=1)
            if st == 200:
                return out
            time.sleep(0.1)
        raise RuntimeError("gateway not ready")

    def close(self):
        if self.p is not None and self.p.poll() is None:
            self.p.terminate()
            self.p.wait(5)
        if self.p is not None:
            self.p.stdout.close()


class GatewayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(__import__("tempfile").mkdtemp())
        self.fake = FakeS3().__enter__()
        self.s3 = S3(self.fake.url, "test-access", "test-secret")
        self.bucket = "evc-test-bucket"
        self.s3.create_versioned_bucket(self.bucket)
        self.gws = []

    def tearDown(self):
        for g in self.gws:
            g.close()
        self.fake.__exit__()

    def gw(self, mode, **kw):
        g = Gw(self.fake.url, self.bucket, mode, self.tmp, **kw)
        self.gws.append(g)
        return g

    def kinds(self, key):
        return [v["meta"].get("x-amz-meta-evicon-kind") for v in self.fake.versions(self.bucket, key)]

    def test_g1_intent_hash_matches_go(self):
        # go: protocol.CommandRequest{SchemaVersion: 1, DomainID: "pump0-1", Generation: 1, ..., Epoch: 1}.CanonicalHash()
        self.assertEqual(canonical_hash(cmd("d0-c1", 3, 1)), "d8ce58ad4ac45b017ac23725bb4e8d7a925c9cc191536f3016084c3eb81bc2ff")
        self.assertEqual(canonical_hash(cmd("d3-c117-r2", 5, 140)), "e07ba23a776c6ec61194a95215d72d703667ce3128021fc082588162e3a702d6")
        self.assertEqual(canonical_hash(cmd("x<&>", 1, 9)), "d06519114ef2a826906c1c62a9527052ae0f7604e0fa11120c02ea153603b71c")

    def test_g2_conditional(self):
        g = self.gw("COND")
        h = g.start()
        self.assertEqual((h["role"], h["release"], h["pid"]), ("gateway", (REPO / "VERSION").read_text().strip(), g.p.pid))
        st, caps = call("GET", g.url + "/v1/capabilities")
        self.assertEqual(caps["evidence_contract"], json.loads(CONTRACT["COND"].read_text()))
        self.assertEqual((caps["profile"], caps["dimension"], caps["supported_operations"]), ("DEVICE", 1, ["ADD"]))
        base = g.url + "/d/pump0-1"  # the endpoint URL a campaign hands to the authority
        st, rc = call("POST", base + "/v1/effects", cmd("d0-c1", 3, 1))
        self.assertEqual((st, rc["outcome"], rc["reason"], rc["state"]), (200, "APPLIED", "S3_PUT_200", [3]))
        self.assertEqual(rc["intent_hash"], canonical_hash(cmd("d0-c1", 3, 1)))
        self.assertEqual((rc["owner_version"], rc["receipt_version"]), (1, 1))
        st, rc = call("POST", base + "/v1/effects", cmd("d0-c1", 3, 1))
        self.assertEqual((st, rc["outcome"], rc["reason"]), (200, "APPLIED", "S3_EXISTING_EFFECT"))
        self.assertEqual(self.kinds("pump0-1/d0-c1"), ["effect"])  # a replay is not a second effect
        self.assertEqual(call("POST", base + "/v1/effects", cmd("d0-c1", 4, 1))[0], 409)
        bad = dict(cmd("d0-c1", 3, 1), intent_hash="0" * 64)
        self.assertEqual(call("POST", base + "/v1/effects", bad)[0], 400)
        st, rc = call("GET", base + "/v1/receipts/d0-c1")
        self.assertEqual((st, rc["outcome"]), (200, "APPLIED"))
        self.assertEqual(call("GET", base + "/v1/receipts/d0-c9")[0], 404)
        st, rc = call("POST", base + "/v1/stop", stop("d0-c2", 2, 2))
        self.assertEqual((st, rc["outcome"], rc["reason"]), (200, "STOPPED", "S3_TOMBSTONE_WRITTEN"))
        st, rc = call("POST", base + "/v1/effects", cmd("d0-c2", 2, 2))  # a late copy after the stop
        self.assertEqual((st, rc["outcome"]), (200, "STOPPED"))
        self.assertEqual(self.kinds("pump0-1/d0-c2"), ["tombstone"])
        st, rc = call("POST", base + "/v1/stop", stop("d0-c1", 3, 1))  # the stop lost the race: APPLIED
        self.assertEqual((st, rc["outcome"]), (200, "APPLIED"))
        self.assertEqual(call("POST", base + "/v1/barriers", {"schema_version": 1})[0], 409)
        self.assertEqual(call("POST", g.url + "/d/pump9-1/v1/effects", cmd("d0-c3", 1, 3, "pump9-1"))[0], 404)

    def test_g3_plain(self):
        g = self.gw("PLAIN")
        g.start()
        for _ in range(2):
            st, rc = call("POST", g.url + "/v1/effects", cmd("d0-c1", 3, 1))
            self.assertEqual((st, rc["outcome"]), (200, "APPLIED"))
        self.assertEqual(self.kinds("pump0-1/d0-c1"), ["effect", "effect"])  # an unconditional PUT applies twice
        self.assertEqual(call("GET", g.url + "/v1/receipts/d0-c1"), (409, {"error": "UNSUPPORTED"}))
        self.assertEqual(call("POST", g.url + "/v1/stop", stop("d0-c1", 3, 1)), (409, {"error": "STOP_UNSUPPORTED"}))
        self.assertEqual(call("GET", g.url + "/v1/capabilities")[1]["evidence_contract"]["capabilities"], [])

    def test_g4_offline(self):
        g = self.gw("COND", budget=0.6)
        g.start()
        self.assertEqual(call("POST", g.adm + "/d/pump0-1/_test/online?v=false")[0], 200)
        t0 = time.monotonic()
        st, out = call("POST", g.url + "/v1/effects", cmd("d0-c1", 3, 1))
        self.assertEqual((st, out["error"]), (504, "OWNER_TIMEOUT"))
        self.assertLess(time.monotonic() - t0, 1.5)
        self.assertEqual(call("GET", g.url + "/v1/receipts/d0-c1")[0], 504)
        self.assertEqual(self.fake.versions(self.bucket, "pump0-1/d0-c1"), [])
        self.assertEqual(call("POST", g.adm + "/_test/online?v=true")[0], 200)
        st, rc = call("POST", g.url + "/v1/effects", cmd("d0-c1", 3, 1))
        self.assertEqual((st, rc["outcome"]), (200, "APPLIED"))
        self.assertEqual(call("POST", g.adm + "/_test/online?v=maybe")[0], 400)

    def test_g5_crash_between_effect_and_reply(self):
        g = self.gw("COND")
        g.start()
        self.assertEqual(call("POST", g.adm + "/_test/crash?at=AFTER_CLAIM_BEFORE_EFFECT")[0], 400)
        self.assertEqual(call("POST", g.adm + "/_test/crash?at=AFTER_EFFECT_BEFORE_RECEIPT"), (200, {"armed": "AFTER_EFFECT_BEFORE_RECEIPT"}))
        st, _ = call("POST", g.url + "/v1/effects", cmd("d0-c1", 3, 1))
        self.assertIsNone(st)
        self.assertEqual(g.p.wait(5), 17)
        self.assertEqual(self.kinds("pump0-1/d0-c1"), ["effect"])  # the orphan: effect without receipt
        g.p.stdout.close()
        g.start()  # stateless: the restarted gateway answers from the object store
        st, rc = call("POST", g.url + "/v1/effects", cmd("d0-c1", 3, 1))
        self.assertEqual((st, rc["outcome"], rc["reason"]), (200, "APPLIED", "S3_EXISTING_EFFECT"))
        self.assertEqual(call("POST", g.adm + "/_test/crash?at=AFTER_EFFECT_BEFORE_RECEIPT")[0], 200)
        self.assertEqual(call("POST", g.adm + "/_test/crash?at=NONE"), (200, {"armed": ""}))
        st, rc = call("POST", g.url + "/v1/effects", cmd("d0-c2", 1, 2))
        self.assertEqual((st, rc["outcome"]), (200, "APPLIED"))
        log = [json.loads(x) for x in (self.tmp / "gw-COND.jsonl").read_text().splitlines()]
        self.assertIn("crash", [x["op"] for x in log])
        self.assertNotIn("test-secret", (self.tmp / "gw-COND.jsonl").read_text())

    def test_g6_contract_must_match_mode(self):
        g = self.gw("COND", contract=CONTRACT["PLAIN"])
        with self.assertRaises(RuntimeError) as cm:
            g.start()
        self.assertIn("mode COND serves", str(cm.exception))

    def test_g7_list_versions_pages(self):
        self.fake.page_size = 2
        for i in range(3):
            self.fake.put_raw(self.bucket, "pump0-1/a", {"evicon-kind": "effect"})
        self.fake.put_raw(self.bucket, "pump0-1/b", {"evicon-kind": "tombstone"}, b"")
        self.fake.put_raw(self.bucket, "pump1-1/c", {"evicon-kind": "effect"})
        vs = self.s3.list_versions(self.bucket)
        self.assertEqual(sorted(v["key"] for v in vs), ["pump0-1/a"] * 3 + ["pump0-1/b", "pump1-1/c"])
        self.assertEqual(len({v["version_id"] for v in vs}), 5)
        self.assertEqual(len(self.s3.list_versions(self.bucket, prefix="pump0-1/")), 4)


if __name__ == "__main__":
    unittest.main()
