"""Release 068: the files `measure` hands to docker compose on VM-B, checked before they reach VM-B.

The 068 trim of docker-compose.yml left a second copy of the old services after `volumes:`
(found by the simulated-testbed run of `measure zephyr`); every Mosquitto, EMQX, NATS and
Zephyr measurement would have failed at `docker compose up`. These tests parse every compose
file (PyYAML when installed, else a column-0 structure check), and check that the driver's
owner table (COMPOSE_OWNERS, MEASURE_PORTS) matches the files: service, profile, host ports,
image variables and required secrets.
"""
import json
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
COMPOSE = REPO / "conformance" / "compose" / "vm-b"
sys.path.insert(0, str(REPO))

try:
    import yaml  # noqa: F401
except ImportError:  # VM-C may not have PyYAML; the structure check below still runs
    yaml = None


def _driver():
    import importlib
    return importlib.import_module("evicon_review")


class ComposeFiles(unittest.TestCase):
    def files(self):
        fs = sorted(COMPOSE.glob("docker-compose*.yml"))
        self.assertTrue(fs)
        return fs

    def test_top_level_structure(self):
        for f in self.files():
            keys = [l.split(":")[0] for l in f.read_text().splitlines() if l and not l.startswith(("#", " "))]
            self.assertEqual(len(keys), len(set(keys)), f"{f.name}: a top-level key appears twice: {keys}")
            self.assertTrue(set(keys) <= {"name", "services", "volumes"}, f"{f.name}: {keys}")
            self.assertIn("services", keys, f.name)

    @unittest.skipIf(yaml is None, "PyYAML not installed")
    def test_parse_and_volumes(self):
        for f in self.files():
            d = yaml.safe_load(f.read_text())
            self.assertIsInstance(d, dict, f.name)
            declared = set((d.get("volumes") or {}).keys())
            for name, svc in d["services"].items():
                self.assertIn("image", svc, f"{f.name}: {name}")
                for v in svc.get("volumes", []) or []:
                    src = str(v).split(":")[0]
                    if not src.startswith((".", "/")):
                        self.assertIn(src, declared, f"{f.name}: {name} uses undeclared volume {src}")

    @unittest.skipIf(yaml is None, "PyYAML not installed")
    def test_driver_owner_table_matches_files(self):
        drv = _driver()
        for stack, o in drv.COMPOSE_OWNERS.items():
            f = COMPOSE / o["file"]
            self.assertTrue(f.is_file(), f"{stack}: {o['file']} missing")
            text = f.read_text()
            d = yaml.safe_load(text)
            services = d["services"]
            if o.get("profile"):
                self.assertTrue(any(o["profile"] in (s.get("profiles") or []) for s in services.values()),
                                f"{stack}: no service with profile {o['profile']}")
            published = set()
            for s in services.values():
                if o.get("profile") and o["profile"] not in (s.get("profiles") or []):
                    continue
                for p in s.get("ports", []) or []:
                    published.add(int(str(p).split(":")[0]))
            for k in o["ports"]:
                self.assertIn(drv.MEASURE_PORTS[k], published, f"{stack}: port {k}={drv.MEASURE_PORTS[k]} not published by {o['file']}")
            for var in o.get("images", {}):
                self.assertIn("${" + var, text, f"{stack}: image variable {var} not used in {o['file']}")
            for var in o.get("secrets", []):
                self.assertIn("${" + var + ":?", text, f"{stack}: secret {var} must be required (:?) in {o['file']}")
            for init, _t in o.get("init", []):
                self.assertIn(init.split()[-1], services, f"{stack}: init service {init.split()[-1]} missing")

    def test_no_secret_values_in_files(self):
        for f in self.files():
            for line in f.read_text().splitlines():
                if re.search(r"(PASSWORD|TOKEN|SECRET)\w*:\s*[^$\s{]", line) and "${" not in line:
                    self.fail(f"{f.name}: literal credential: {line.strip()}")


class StackConfigs(unittest.TestCase):
    def test_every_stack_config_exists_and_parses(self):
        drv = _driver()
        for stack, spec in drv.STACKS.items():
            for name in spec["configs"]:
                p = REPO / "conformance" / "configs" / f"{name}.json"
                self.assertTrue(p.is_file(), f"{stack}: {p} missing")
                cfg = json.loads(p.read_text())
                self.assertIn("adapter", cfg, name)
            self.assertIn(stack, drv.STACK_PROCS if stack != "temporal" else {"temporal": 1}, stack)
            self.assertIn(stack, drv.MEASURE_DURATION, stack)


if __name__ == "__main__":
    unittest.main()
