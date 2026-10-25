"""Release 075 extensions of the MQTT owner gateway (experiment C3), against the firmware model of
test_mqtt_owner_gateway.py (no broker).

X1 restricted contracts: a non-empty proper subset of the mode's measured capabilities, MEASURED, every kept
   capability with provenance, provenance.restricted_from naming the measured contract; anything else is refused
X2 the gateway refuses what its advertised contract lacks (query needs Q_A, stop Q_S, barrier F); a
   barrier of the {F} restriction still reports a receipt for every identity at or below its cutoff
X3 direct channel (offline mode drop): a command sent while offline never reaches the firmware (504), also
   not after the window; a reply the firmware sends while offline is lost; the next barrier seals the lost
   command (STOPPED, never started); queue channel (reset, default): the command arrives late
X4 the mode comes from --offline-mode, else gateway.channel next to the state file
"""
import argparse
import json
import tempfile
import time
import unittest
from pathlib import Path

from reference import mqtt_owner_gateway as G
from reference import mqtt_owner_gateway_ext as X
from tests.test_mqtt_owner_gateway import DEV, FakeConn, FakeFirmware, FakeLink, barrier, code, contract, req, stop_req

OPEN = []


def tearDownModule():
    for gw in OPEN:
        gw.close()


def restricted(mode: str, tmp: Path, keep, provenance=True, source="MEASURED", horizon=False) -> str:
    parent = json.loads(Path(contract(mode, tmp)).read_text())
    parent["provenance"] = {c: "P-test" for c in parent["capabilities"]}
    c = dict(parent, id=f"{parent['id']}+only", capabilities=sorted(keep), source=source,
             provenance={k: v for k, v in parent["provenance"].items() if k in keep})
    if provenance:
        c["provenance"]["restricted_from"] = f"{parent['id']} sha256:{'0' * 64} capabilities {','.join(parent['capabilities'])}"
    if not horizon and "D_h" not in keep:
        c.pop("dedup_horizon_ms", None)
    p = Path(tempfile.mkstemp(suffix=".json", dir=tmp)[1])
    p.write_text(json.dumps(c))
    return str(p)


def args(mode, tmp: Path, cfile, offline_mode="", state=None, budget=0.3):
    return argparse.Namespace(mode=mode, device_id=DEV, ns="t1", generation=1, epoch=1, listen="", admin_listen="", broker="127.0.0.1:1",
                              contract_file=cfile, state=str(state or tmp / "gw.state.jsonl"), budget_s=budget, qos_cmd=1, keepalive_s=60,
                              offline_mode=offline_mode, log=str(tmp / "gw.jsonl"))


def make(mode, tmp: Path, cfile=None, fw=None, **kw):
    fw = fw or FakeFirmware(G.FW_MODE[mode])
    link = FakeLink()
    holder = {}

    def net(gw):
        holder["data"], holder["ctl"] = FakeConn(gw, fw, link, "data"), FakeConn(gw, fw, link, "ctl")
        return link, holder["data"], holder["ctl"]
    gw = X.ExtGateway(args(mode, tmp, cfile or contract(mode, tmp), **kw), net=net)
    OPEN.append(gw)
    return gw, fw, link, holder


class ExtGatewayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_x1_restricted_contracts(self):
        for mode, keep in (("CUTOFF", {"Q_A"}), ("CUTOFF", {"F"}), ("STOP", {"Q_S"}), ("STOP", {"Q_A", "Q_S"})):
            gw, _, _, _ = make(mode, self.tmp, restricted(mode, self.tmp, keep))
            self.assertEqual(set(gw.capabilities()["evidence_contract"]["capabilities"]), keep)
        bad = [restricted("CUTOFF", self.tmp, {"Q_A"}, provenance=False),  # no restricted_from
               restricted("CUTOFF", self.tmp, {"Q_S"}),                     # not a subset of the mode's
               restricted("CUTOFF", self.tmp, {"F"}, source="DECLARED"),
               restricted("CUTOFF", self.tmp, {"F"}, horizon=True)]         # dedup_horizon_ms without D_h
        for f in bad:
            with self.assertRaises(SystemExit, msg=f):
                make("CUTOFF", self.tmp, f)
        gw, _, _, _ = make("CUTOFF", self.tmp)  # the measured contract itself, as in C2
        self.assertEqual(set(gw.caps), {"D_h", "Q_A", "F"})

    def test_x2_refuses_missing_capabilities(self):
        gw, fw, _, h = make("CUTOFF", self.tmp, restricted("CUTOFF", self.tmp, {"Q_A"}))
        gw.effect(req("c1", 1), time.monotonic())
        self.assertEqual(code(gw.query, "c1", time.monotonic())[1]["outcome"], "APPLIED")
        self.assertEqual(code(gw.barrier, barrier(3), time.monotonic())[0], 409)
        gw, fw, _, h = make("CUTOFF", Path(tempfile.mkdtemp()), restricted("CUTOFF", self.tmp, {"F"}))
        gw.effect(req("c1", 1), time.monotonic())
        h["data"].lose_replies = 1
        self.assertEqual(code(gw.effect, req("c2", 2), time.monotonic())[0], 504)  # applied, reply lost
        self.assertEqual(code(gw.query, "c1", time.monotonic())[0], 409)
        c, b = code(gw.barrier, barrier(3), time.monotonic())
        self.assertEqual(c, 200)
        self.assertEqual({r["command_id"]: r["outcome"] for r in b["receipts"]}, {"c1": "APPLIED", "c2": "APPLIED"})
        gw, fw, _, _ = make("STOP", Path(tempfile.mkdtemp()), restricted("STOP", self.tmp, {"Q_S"}))
        self.assertEqual(code(gw.stop, stop_req("c5", 5), time.monotonic())[1]["outcome"], "STOPPED")
        self.assertEqual(code(gw.query, "c5", time.monotonic())[0], 409)
        self.assertEqual(code(gw.barrier, barrier(5), time.monotonic())[0], 409)

    def test_x3_direct_and_queue_channels(self):
        gw, fw, link, h = make("CUTOFF", self.tmp, offline_mode="drop")
        gw.effect(req("c1", 1), time.monotonic())
        gw.set_online(False)
        self.assertTrue(link.up)  # the connections stay up: messages are lost, not held
        self.assertEqual(code(gw.effect, req("c2", 2), time.monotonic())[0], 504)
        gw.on_reply("cmd", fw.handle("cmd", {"cmd": "c9", "seq": 9, "value": 1}))  # a reply while offline: lost
        gw.set_online(True)
        self.assertEqual(fw.effects, ["c1", "c9"])  # c2 never reached the firmware
        self.assertNotIn("c9", gw.rcpt)
        c, b = code(gw.barrier, barrier(2), time.monotonic())
        self.assertEqual(c, 200)
        self.assertEqual({r["command_id"]: r["outcome"] for r in b["receipts"]}, {"c1": "APPLIED", "c2": "STOPPED"})
        self.assertEqual(fw.effects, ["c1", "c9"])
        self.assertGreaterEqual(gw.dropped["out"], 1)
        self.assertEqual(gw.dropped["in"], 1)
        q, fq, lq, _ = make("CUTOFF", Path(tempfile.mkdtemp()))  # queue channel: the frozen behaviour
        q.set_online(False)
        self.assertFalse(lq.up)
        self.assertEqual(code(q.effect, req("c1", 1), time.monotonic())[0], 504)
        q.set_online(True)
        self.assertEqual(fq.effects, ["c1"])  # delivered late

    def test_x4_mode_from_channel_file(self):
        (self.tmp / "gateway.channel").write_text("direct\n")
        gw, _, _, _ = make("CUTOFF", self.tmp, state=self.tmp / "gateway0.state.jsonl")
        self.assertEqual(gw.offline_mode, "drop")
        (self.tmp / "gateway.channel").write_text("queue\n")
        gw, _, _, _ = make("CUTOFF", self.tmp, state=self.tmp / "gateway1.state.jsonl")
        self.assertEqual(gw.offline_mode, "reset")
        gw, _, _, _ = make("CUTOFF", self.tmp, state=self.tmp / "gateway2.state.jsonl", offline_mode="drop")
        self.assertEqual(gw.offline_mode, "drop")  # the option wins
        (self.tmp / "gateway.channel").write_text("lossy\n")
        with self.assertRaises(SystemExit):
            make("CUTOFF", self.tmp, state=self.tmp / "gateway3.state.jsonl")


if __name__ == "__main__":
    unittest.main()
