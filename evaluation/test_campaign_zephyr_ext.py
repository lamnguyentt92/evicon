"""Release 075, experiment C3: restricted contracts and the owner channel of evaluation/campaign_zephyr_ext.py,
and the driver's placement of the contracts measured in the review run (evicon_review._place_zephyr_contracts).

E-R1 restrict_contract: a non-empty proper subset, measured provenance kept, provenance.restricted_from
     names the measured file's SHA-256, no D_h horizon without D_h; the Go validator (evicon-contract)
     accepts it and grades it (strict decoding: the authority decodes the advertised contract strictly)
E-R2 load_config: the frozen runner's checks on the measured contracts, then the restriction rules
     (parent of the mode, SHA-256, restrict_to, provenance, fault model) and the channel
E-R3 the driver: restricted files from the measured ones, the C3 runner, 56 cells, no planned violation;
     the C3 config differs from C2's only where the plan says
"""
import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "evaluation"))
sys.path.insert(0, str(REPO))
import campaign_zephyr_ext as X  # noqa: E402
import evicon_review as er  # noqa: E402

FAULT_MODEL = "network loss/delay, client abandon; owner up (no owner crash or reboot)"


def measured(mode: str) -> dict:
    caps = {"PLAIN": [], "CUTOFF": ["D_h", "Q_A", "F"], "STOP": ["D_h", "Q_A", "Q_S", "F"]}[mode]
    c = {"schema_version": 1, "id": f"meas-test-zephyr-{mode.lower()}", "source": "MEASURED", "system": "test", "operation": "test",
         "capabilities": caps, "fault_model": FAULT_MODEL, "provenance": {k: f"P-{k} x3 repetitions" for k in caps},
         "verified_test_ids": ["P2", "P9F"]}
    if "D_h" in caps:
        c["dedup_horizon_ms"] = 600000
        c["provenance"]["D_h_horizon"] = "lower bound 600.0s"
    return c


def write(tmp: Path, obj, name=None) -> Path:
    p = tmp / name if name else Path(tempfile.mkstemp(suffix=".json", dir=tmp)[1])
    p.write_text(json.dumps(obj))
    return p


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def c3_config(tmp: Path) -> dict:
    c = json.loads((REPO / "evaluation" / "campaigns" / "expC3_ablation.json").read_text())
    c["mode"] = "local"
    c.pop("remote")
    c.pop("preregistration")
    c["real_owner"]["broker"] = "127.0.0.1:1883"
    c["real_owner"]["firmware"] = "/x/zephyr-cutoff.exe"
    parents = {m: write(tmp, measured(m), f"measured-{m.lower()}.json") for m in ("PLAIN", "CUTOFF", "STOP")}
    for name, spec in c["real_owner"]["contracts"].items():
        p = parents[spec["mode"]]
        if spec.get("restrict_to"):
            spec["contract_file"] = str(write(tmp, X.restrict_contract(json.loads(p.read_text()), sha(p), spec["restrict_to"]), f"{name}.json"))
            spec["restricted_from"] = str(p)
        else:
            spec["contract_file"] = str(p)
    return c


class RestrictTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_er1_restrict_contract(self):
        m = measured("CUTOFF")
        r = X.restrict_contract(m, "a" * 64, ["F"])
        self.assertEqual(r["capabilities"], ["F"])
        self.assertEqual(r["source"], "MEASURED")
        self.assertEqual(r["provenance"]["F"], m["provenance"]["F"])
        self.assertNotIn("Q_A", r["provenance"])
        self.assertNotIn("dedup_horizon_ms", r)
        self.assertIn(f"{m['id']} sha256:{'a' * 64}", r["provenance"]["restricted_from"])
        self.assertEqual(r["fault_model"], m["fault_model"])
        r2 = X.restrict_contract(m, "a" * 64, ["D_h", "Q_A"])
        self.assertEqual((r2["dedup_horizon_ms"], "D_h_horizon" in r2["provenance"]), (600000, True))
        for keep in ([], ["D_h", "Q_A", "F"], ["Q_S"]):
            with self.assertRaises(ValueError):
                X.restrict_contract(m, "a" * 64, keep)
        with self.assertRaises(ValueError):
            X.restrict_contract(dict(m, source="DECLARED"), "a" * 64, ["F"])
        exe = REPO / "bin" / "evicon-contract"
        if exe.exists():
            files = [write(self.tmp, X.restrict_contract(measured(mode), "b" * 64, keep), f"r-{mode}-{'-'.join(keep)}.json")
                     for mode, keep in (("CUTOFF", ["Q_A"]), ("CUTOFF", ["F"]), ("STOP", ["Q_S"]))]
            out = subprocess.run([str(exe), "validate"] + [str(f) for f in files], capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            lv = subprocess.run([str(exe), "levels"] + [str(f) for f in files], capture_output=True, text=True).stdout.splitlines()
            self.assertEqual(sorted(json.loads(x)["capabilities"][0] for x in lv), ["F", "Q_A", "Q_S"])

    def test_er2_load_config(self):
        c = c3_config(self.tmp)
        got = X.load_config(write(self.tmp, c))
        self.assertEqual(got["contracts"], c["contracts"])
        old = X.Z.load_config
        X.Z.load_config = X.load_config  # as main() installs it: the frozen checks are still the frozen function's
        try:
            self.assertEqual(X.load_config(write(self.tmp, c))["name"], c["name"])
        finally:
            X.Z.load_config = old
        cases = []
        d = copy.deepcopy(c)
        d["real_owner"]["contracts"]["ZEPHYR_F_DIRECT"]["channel"] = "lossy"
        cases.append((d, "channel"))
        d = copy.deepcopy(c)
        d["real_owner"]["contracts"]["ZEPHYR_F_DIRECT"]["restrict_to"] = ["Q_A"]
        cases.append((d, "restrict_to"))
        d = copy.deepcopy(c)  # restricts the stop mode's contract but runs the cutoff mode
        d["real_owner"]["contracts"]["ZEPHYR_F_DIRECT"]["restricted_from"] = str(self.tmp / "measured-stop.json")
        cases.append((d, "restricted_from must be the MEASURED contract of mode CUTOFF"))
        d = copy.deepcopy(c)  # the measured file changed after the restriction was made
        p = Path(d["real_owner"]["contracts"]["ZEPHYR_QA_DIRECT"]["restricted_from"])
        alt = write(self.tmp, dict(measured("CUTOFF"), notes="edited"))
        d["real_owner"]["contracts"]["ZEPHYR_QA_DIRECT"]["restricted_from"] = str(alt)
        cases.append((d, "does not name"))
        d = copy.deepcopy(c)
        d["real_owner"]["contracts"]["ZEPHYR_QS_QUEUE"]["extra"] = 1
        cases.append((d, "unknown keys"))
        d = copy.deepcopy(c)  # a restriction of a contract the frozen runner refuses for its mode
        bad = write(self.tmp, dict(measured("CUTOFF"), capabilities=["D_h", "F"]))
        d["real_owner"]["contracts"]["ZEPHYR_PLAIN_DIRECT"]["contract_file"] = str(bad)
        cases.append((d, "needs MEASURED"))
        for d, msg in cases:
            with self.assertRaisesRegex(ValueError, msg):
                X.load_config(write(self.tmp, d))
        self.assertTrue(p.exists())


class DriverTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.old = er.RUN
        er.RUN = types.SimpleNamespace(path=self.tmp, log=lambda *a, **k: None, problem=lambda *a, **k: None)

    def tearDown(self):
        er.RUN = self.old

    def test_er3_driver(self):
        c = json.loads((REPO / "evaluation" / "campaigns" / "expC3_ablation.json").read_text())
        c2 = json.loads((REPO / "evaluation" / "campaigns" / "expC2_zephyr.json").read_text())
        self.assertEqual(er._zephyr_runner(c), "evaluation/campaign_zephyr_ext.py")
        self.assertEqual(er._zephyr_runner(c2), "evaluation/campaign_zephyr.py")
        self.assertEqual(er._cells(c), 56)
        self.assertEqual(er._expected_unsafe(c), set())
        for k in ("devices", "duration_s", "tail_s", "workload", "faults", "authority", "seed", "mode", "schema_version", "remote"):
            self.assertEqual(c[k], c2[k], k)
        mdir = self.tmp / "measure"
        contracts = {}
        for name, spec in c["real_owner"]["contracts"].items():
            stack = er.ZEPHYR_MODE_STACK[spec["mode"]][0]
            p = mdir / stack / "contract.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(measured(spec["mode"])))
            contracts[name] = (p, measured(spec["mode"]))
        cdir = self.tmp / "contracts"
        cdir.mkdir()
        made = er._place_zephyr_contracts(c, contracts, cdir)
        self.assertEqual(set(made), {n for n, s in c["real_owner"]["contracts"].items() if s.get("restrict_to")})
        for name, spec in c["real_owner"]["contracts"].items():
            f = json.loads(Path(spec["contract_file"]).read_text())
            self.assertEqual(f["capabilities"], spec.get("restrict_to", []), name)
        c["mode"] = "local"
        c.pop("remote")
        c.pop("preregistration")
        c["real_owner"].update(broker="127.0.0.1:1883", firmware="/x/fw.exe")
        self.assertEqual(X.load_config(write(self.tmp, c))["name"], "expC3-ablation")


if __name__ == "__main__":
    unittest.main()
