"""Release 074, experiment C2: the runner's config rules (evaluation/campaign_zephyr.py) and the driver's
gate on the measured contracts (evicon_review._measured_zephyr_contracts). The firmware, Mosquitto and
the gateway's MQTT path are exercised by the measurement and the campaign on the testbed and by the
pre-release run (PREREG_expC2_074.md, section 9); conformance/tests/test_mqtt_owner_gateway.py covers
the gateway's protocol against a model of the firmware.

Z-R1 load_config: system, modes, measured contracts with the mode's capabilities, exclude_cells
Z-R2 one topic prefix per cell; ports; the cell count without the excluded pairs (driver)
Z-R3 the driver runs C2 only on contracts measured in this review run, with the expected capabilities,
     the measured fault model and the firmware binary the campaign runs
"""
import copy
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "evaluation"))
sys.path.insert(0, str(REPO))
import campaign_zephyr as Z  # noqa: E402
import evicon_review as er  # noqa: E402
from campaign import Cell  # noqa: E402

FAULT_MODEL = "network loss/delay, client abandon; owner up (no owner crash or reboot)"


def measured(tmp: Path, mode: str, caps=None, source="MEASURED", fault_model=FAULT_MODEL) -> Path:
    c = {"schema_version": 1, "id": f"meas-test-{mode.lower()}", "source": source, "system": "test", "operation": "test",
         "capabilities": sorted(Z.MODES[mode][1] if caps is None else caps), "fault_model": fault_model}
    p = Path(tempfile.mkstemp(prefix=f"{mode}-", suffix=".json", dir=tmp)[1])
    p.write_text(json.dumps(c))
    return p


def config(tmp: Path) -> dict:
    c = json.loads((REPO / "evaluation" / "campaigns" / "expC2_zephyr.json").read_text())
    c["mode"] = "local"
    c.pop("remote")
    c.pop("preregistration")
    c["real_owner"]["broker"] = "127.0.0.1:1883"
    c["real_owner"]["firmware"] = "/x/zephyr-cutoff.exe"
    for name, spec in c["real_owner"]["contracts"].items():
        spec["contract_file"] = str(measured(tmp, spec["mode"]))
    return c


def write(tmp: Path, c: dict) -> Path:
    p = tmp / "cfg.json"
    p.write_text(json.dumps(c))
    return p


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_zr1_load_config(self):
        c = config(self.tmp)
        got = Z.load_config(write(self.tmp, c))
        self.assertEqual(got["real_owner"]["system"], "zephyr")
        for bad, msg in ((("system",), "zephyr"), (("contracts", "ZEPHYR_STOP", "mode"), "PLAIN|CUTOFF|STOP")):
            d = copy.deepcopy(c)
            tgt = d["real_owner"]
            for k in bad[:-1]:
                tgt = tgt[k]
            tgt[bad[-1]] = "minio" if bad == ("system",) else "COND"
            with self.assertRaisesRegex(ValueError, msg):
                Z.load_config(write(self.tmp, d))
        d = copy.deepcopy(c)  # a contract file with the wrong capabilities for its mode
        d["real_owner"]["contracts"]["ZEPHYR_CUTOFF"]["contract_file"] = str(measured(self.tmp, "CUTOFF", caps={"D_h", "F"}))
        with self.assertRaisesRegex(ValueError, "needs MEASURED"):
            Z.load_config(write(self.tmp, d))
        d = copy.deepcopy(c)
        d["real_owner"]["contracts"]["ZEPHYR_PLAIN"]["contract_file"] = str(measured(self.tmp, "PLAIN", source="DECLARED"))
        with self.assertRaisesRegex(ValueError, "needs MEASURED"):
            Z.load_config(write(self.tmp, d))
        d = copy.deepcopy(c)
        d["exclude_cells"] = [["ZEPHYR_STOP", "RES_PERIODIC"]]
        with self.assertRaisesRegex(ValueError, "exclude_cells"):
            Z.load_config(write(self.tmp, d))
        d = copy.deepcopy(c)
        d["real_owner"]["policies"]["NAIVE"]["authority"]["dispatch_poll"] = "1s"
        with self.assertRaisesRegex(ValueError, "not allowed"):
            Z.load_config(write(self.tmp, d))

    def test_zr2_prefix_ports_cells(self):
        out = self.tmp / "run"
        a, b = Z.cell_ns(out, Cell(0, "ZEPHYR_PLAIN", "EVICON")), Z.cell_ns(out, Cell(0, "ZEPHYR_PLAIN", "M1"))
        self.assertNotEqual(a, b)
        self.assertEqual(a, Z.cell_ns(out, Cell(0, "ZEPHYR_PLAIN", "EVICON")))
        self.assertRegex(a, r"^e[0-9a-f]{12}$")
        c = json.loads((REPO / "evaluation" / "campaigns" / "expC2_zephyr.json").read_text())
        self.assertEqual(er._cells(c), 8 * 7)
        self.assertEqual(er._cells(dict(c, exclude_cells=[])), 8 * 9)
        self.assertEqual(er._expected_unsafe(c), {("ZEPHYR_PLAIN", "NAIVE")})


class DriverGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.old = er.RUN
        er.RUN = types.SimpleNamespace(path=self.tmp, log=lambda *a, **k: None, problem=lambda *a, **k: None)

    def tearDown(self):
        er.RUN = self.old

    def measure(self, stack, conf, caps, fw_sha="f" * 64, fault_model=FAULT_MODEL):
        d = self.tmp / "measure" / stack / "runs" / conf / "20261024T000000"
        d.mkdir(parents=True, exist_ok=True)
        (d / "contract.json").write_text(json.dumps({"id": f"m-{stack}", "source": "MEASURED", "capabilities": sorted(caps),
                                                     "fault_model": fault_model}))
        (self.tmp / "measure" / stack / "measure.json").write_text(json.dumps({"stack": stack, "firmware": {"sha256": fw_sha}}))

    def test_zr3_measured_contract_gate(self):
        c = json.loads((REPO / "evaluation" / "campaigns" / "expC2_zephyr.json").read_text())
        with self.assertRaisesRegex(er.StageFail, "no measurement of zephyr-plain"):
            er._measured_zephyr_contracts(c, "f" * 64)
        for mode, (stack, conf, caps) in er.ZEPHYR_MODE_STACK.items():
            self.measure(stack, conf, caps)
        got = er._measured_zephyr_contracts(c, "f" * 64)
        self.assertEqual(set(got), {"ZEPHYR_PLAIN", "ZEPHYR_CUTOFF", "ZEPHYR_STOP"})
        with self.assertRaisesRegex(er.StageFail, "measure again"):
            er._measured_zephyr_contracts(c, "e" * 64)  # another firmware binary than the measured one
        self.measure("zephyr", "zephyr-mqtt", {"D_h", "F"})  # Q_A not observed: not the planned contract
        with self.assertRaisesRegex(er.StageFail, "the plan expects MEASURED"):
            er._measured_zephyr_contracts(c, "f" * 64)
        self.measure("zephyr", "zephyr-mqtt", {"D_h", "Q_A", "F"}, fault_model="network loss/delay")
        with self.assertRaisesRegex(er.StageFail, "fault model"):
            er._measured_zephyr_contracts(c, "f" * 64)


if __name__ == "__main__":
    unittest.main()
