#!/usr/bin/env python3
"""Release 087 checks (PREREG_extra_087.md: the documented shadow loop on the real owners; owner reboots as a campaign fault
with the reboot recovery).

K1  every ID of plan 087 (rules, runs, campaigns, estimands, the recovery steps REC-1 to REC-4, the gateway steps GW-1 to
    GW-3) collides with no ID of the plans 066-086 nor of the 086 final report's claims list; no step is named A1 to A4
K2  the release is 087 everywhere (VERSION, buildinfo, CITATION.cff version and date, the VM set-up banners, CHANGELOG,
    the release notes, evaluation/README, ARTIFACT)
K3  the earlier plans are byte-identical (077, 083, 084, 085, 086), every file 086 froze is frozen again with the same
    SHA-256, plan 087 is frozen and its three configs carry its SHA-256
K4  the configs are copies of the SR configs and differ only where the plan says
K5  the schedules: the 087 configs replay the SR schedules byte for byte; the reboot schedule is deterministic, from a
    stream of its own, the quiet reboot in an arrival gap
K6  the plan: 17 rules (families, margins, tests, exempt contracts), the regime statement and the REC-4 statement
K7  power: every paired rule >= 0.80 at its episode count under its primary template; the templates name their source
K8  the authority: -reboot-recovery off by default, its Go tests, the journal tables created on first use only
K9  the 087 gateway: no floor -> the 077 answers; the floor from replies and the hello; claim_floor in receipts, refusals
    and the barrier answer (the fence reply's own floor); the seal: STOPPED, durable, never forwarded, conflicts refused
K10 the 087 firmware runner: its config rules, the pid-file stop (cwd check, no pkill), the fault runner and the
    gateway.recovery switch per policy, the testbed class on the 086 runner
K11 the driver: verify list, runners, the planned negative control, the measured build's SHA-256, measure zephyr-boot,
    the report steps of release 087
K12 run087.sh: the order of the steps, the five sources of the report, the refusal, the bundle wording; the guide
K13 sh087 on a synthetic run: owner calls with the loop's reads, re-publishes, budget held at the end, paired contrasts
K14 provenance: plan 086 published (440ecf1, its frozen SHA-256), its runs without deviation, plan 087 the newest
K15 hygiene of the new files (English, no forbidden word, no guide name in tests or sources)
K16 the boot-report firmware: the frozen sources compiled unchanged, the redirections, the host tests, the build script
    that never rebuilds the measured build
K17 rb087 on a synthetic run: admissions after each reboot, the check RB-C1, stranded budget
K18 verdicts087: identical, a differing verdict and a missing hypothesis are found
K19 the credential scan of make_data_archive: per match; the authority's fence tokens pass, secrets do not
K20 no test file at the tree root (the reboot test lives in conformance/tests/ only)
K21 TLA+: the reboot model, its seven configurations and the recorded TLC results
K22 the 087 policies: SHADOW_RECONCILE_1S, EVICON_FI_REC, EVICON_FI_REC_OS; every earlier policy's arguments unchanged
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "evaluation"))
sys.path.insert(0, str(REPO / "conformance"))
import evicon_review as er  # noqa: E402
import campaign as C  # noqa: E402

PLAN = "evaluation/prereg/PREREG_extra_087.md"
SAFETY = ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]
CONFIGS = {"expSHR_zephyr_w6": "expSR_zephyr_w6", "expSHR_minio_w6": "expSR_minio_w6", "expRB_zephyr_w6": "expSR_zephyr_w6"}
EARLIER = {"evaluation/prereg/PREREG_review_077.md": "db67640d1709b06dfa5e61f77e3444c07e7c5828dfe24e0e85d163f06a711a29",
           "evaluation/prereg/PREREG_extra_083.md": "6e3f24a918de659c1feb8aaa886d9e9681195353528b5df10e615e8897ff3a06",
           "evaluation/prereg/PREREG_extra_084.md": "b11351645c1e0f05f4a270412539a072f17148ea8607223dcb6182a171505345",
           "evaluation/prereg/PREREG_extra_085.md": "22586c782aa3ac704bcc56376324faeb7c4047da6e35c1668a65a34924bf4790",
           "evaluation/prereg/PREREG_extra_086.md": "d28956e98e4fff3ba032f14e1a3a48c1e92d9e0ffa1b75e14d9a36ca3adba9c6"}
FIRMWARE_086 = "907b2ccdc11ed3d32c6d311e6870741fd256a23885a8db8a75732beb73896f9d"
VERSION = (REPO / "VERSION").read_text().strip()
# release 088 runs this plan: the operator script and the guide are the newest release's (run088.sh, its guide)
GUIDE = f"EVICON_{VERSION}_" + "RUN_GUIDE" + "_VI.md"
SCRIPT = f"run{VERSION}.sh"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def cfg(name: str) -> dict:
    return json.loads((REPO / "evaluation" / "campaigns" / f"{name}.json").read_text())


def tmpdir(tc) -> Path:
    t = Path(tempfile.mkdtemp())
    tc.addCleanup(shutil.rmtree, t, ignore_errors=True)
    return t


def sim_only() -> bool:
    return cfg("expSHR_minio_w6")["notes"].startswith("SIM-ONLY")


def block(name: str, plan: str = PLAN) -> dict:
    return json.loads(re.search(rf"```{name}\n(.*?)```", (REPO / plan).read_text(), re.S).group(1))


# ------------------------------------------------------------------ K1
ID_TOKEN = re.compile(r"\b(?:E-)?[A-Z][A-Za-z0-9]*(?:-[A-Z0-9][A-Za-z0-9]*)+\b")


def plan_ids(path: Path) -> set:
    """Every rule, estimand, run and campaign ID of a plan: the JSON blocks' ids, run keys and campaigns, and the IDs in
    bold and in the first column of its tables (ranges "X-A1 to X-A8" expanded by their ends)."""
    text = path.read_text()
    out = set()
    for name in ("pairs-spec", "pairs-ext", "policy-compare"):
        for m in re.finditer(rf"```{name}\n(.*?)```", text, re.S):
            try:
                spec = json.loads(m.group(1))
            except ValueError:
                continue
            for h in spec.get("hypotheses", []) + spec.get("comparisons", []) + ([spec["safety"]] if isinstance(spec.get("safety"), dict) else []):
                if h.get("id"):
                    out.add(h["id"])
            for k, v in (spec.get("runs") or {}).items():
                out.add(k)
                if v.get("campaign"):
                    out.add(v["campaign"])
    for m in re.finditer(r"\*\*([^*]{1,60})\*\*", text):
        out |= set(ID_TOKEN.findall(m.group(1)))
    for line in text.splitlines():
        if line.startswith("| ") and not line.startswith("|---"):
            out |= set(ID_TOKEN.findall(line.split("|")[1]))
    return {x for x in out if not x.startswith(("PREREG", "RESULTS"))}


NEW_IDS = ({f"SHR-{i}" for i in range(1, 5)} | {f"SHR-A{i}" for i in range(1, 7)} | {"SHR-S", "RB-1", "RB-A1", "RB-A2", "RB-D1", "RB-S", "RB-C1"}
           | {"E-SHR", "E-SHR-X", "E-RB", "E-GATE", "SHRz6", "SHRm6", "RBz6", "expSHR-zephyr-w6", "expSHR-minio-w6", "expRB-zephyr-w6"}
           | {f"REC-{i}" for i in range(1, 5)} | {"GW-1", "GW-2", "GW-3"})


class K1Ids(unittest.TestCase):
    def test_unique(self):
        earlier = set()
        for p in sorted((REPO / "evaluation" / "prereg").glob("*.md")):
            if p.name != "PREREG_extra_087.md":
                earlier |= plan_ids(p)
        self.assertIn("SH-A1", earlier)  # the extraction finds the 086 rules ...
        self.assertIn("SR-3", earlier)
        self.assertIn("SHq", earlier)
        self.assertIn("expSH-queue", earlier)
        self.assertIn("E-SH", earlier)
        with open(REPO / "evaluation" / "sim_predictions" / "extra087_claim_ids.csv", newline="") as fh:
            claims = {r["id"] for r in csv.DictReader(fh)}
        self.assertEqual(len(claims), 184)  # 178 distinct hypothesis IDs of the 185 rows, and the 6 cross-cutting claims
        self.assertIn("reboot", claims)
        mine = plan_ids(REPO / PLAN)
        new = {x for x in mine if x not in earlier} | NEW_IDS
        self.assertTrue(NEW_IDS <= new)
        # ... and none of plan 087's own IDs collides with them (case-insensitive for the claims)
        self.assertEqual(NEW_IDS & earlier, set())
        self.assertEqual({x.lower() for x in NEW_IDS} & {c.lower() for c in claims}, set())
        for x in mine:
            if x.startswith(("SHR", "RB", "E-SHR", "E-RB", "E-GATE", "REC-", "GW-", "expSHR", "expRB")):
                self.assertIn(x, NEW_IDS, x)
        # the 087 rule IDs of the analysis blocks are exactly the planned ones
        ids = {h["id"] for h in block("pairs-spec")["hypotheses"]} | {block("pairs-spec")["safety"]["id"]} | {h["id"] for h in block("pairs-ext")["hypotheses"]}
        self.assertEqual(ids, (NEW_IDS & {x for x in NEW_IDS if x.startswith(("SHR-", "RB-"))}) - {"RB-C1"})

    def test_no_a_steps(self):
        step = re.compile(r"(?<![-\w])A[1-4](?![\w-])")
        for f in ("internal/runtime/reboot_recovery.go", "internal/runtime/reboot_recovery_test.go", "internal/journal/owner_floor.go",
                  "internal/runtime/authority.go", "internal/runtime/fence_anchor.go", PLAN, GUIDE, "CHANGELOG.md",
                  "conformance/reference/mqtt_owner_gateway_ext087.py", "evaluation/campaign_zephyr_ext087.py", "evaluation/rb087.py",
                  "formal/EviconFenceReboot.tla", "formal/TLC_RESULTS_REBOOT.txt"):
            s = (REPO / f).read_text()
            if f == "CHANGELOG.md":
                s = s[:s.index("## 086")]
            self.assertFalse(step.findall(s), f)
        go = (REPO / "internal" / "runtime" / "reboot_recovery.go").read_text()
        for i in range(1, 5):
            self.assertIn(f"REC-{i}", go)


# ------------------------------------------------------------------ K2
class K2Version(unittest.TestCase):
    def test_version(self):
        v = (REPO / "VERSION").read_text().strip()
        self.assertGreaterEqual(v, "087")
        self.assertIn(f'const Release = "{v}"', (REPO / "internal" / "buildinfo" / "buildinfo.go").read_text())
        cit = (REPO / "CITATION.cff").read_text()
        self.assertIn(f'version: "{v}"', cit)
        for f in sorted((REPO / "deploy" / "setup").glob("vm-*.sh")):
            self.assertIn(f"(EviCon {v})", f.read_text(), f.name)
        if v == "087":
            self.assertIn('date-released: "2026-11-11"', cit)
            self.assertTrue((REPO / "CHANGELOG.md").read_text().split("\n## ")[1].startswith("087"))
            first = (REPO / "RELEASE_NOTES_VI.md").read_text().splitlines()[0]
            self.assertTrue(first.startswith("# EviCon") and first.endswith("087 (11/11/2026)"), first)
            self.assertIn("Release **087**", (REPO / "README.md").read_text())
            self.assertIn("Release **087**", (REPO / "ARTIFACT.md").read_text())
        self.assertIn("## Release 087", (REPO / "evaluation" / "README.md").read_text())
        self.assertIn("| 18 | Release 087", (REPO / "ARTIFACT.md").read_text())


# ------------------------------------------------------------------ K3
def frozen(plan: str) -> dict:
    m = re.search(r"```frozen-files\n(.*?)```", (REPO / plan).read_text(), re.S)
    return dict(reversed(ln.split("  ", 1)) for ln in m.group(1).splitlines() if ln.strip())


class K3Frozen(unittest.TestCase):
    def test_earlier_plans(self):
        for p, h in EARLIER.items():
            self.assertEqual(sha(REPO / p), h, p)

    def test_086_frozen_again(self):
        f086, f087 = frozen("evaluation/prereg/PREREG_extra_086.md"), frozen(PLAN)
        for path, h in f086.items():
            self.assertEqual(f087.get(path), h, path)
        self.assertEqual(f087["evaluation/prereg/PREREG_extra_086.md"], EARLIER["evaluation/prereg/PREREG_extra_086.md"])
        for path in ("evaluation/ext087.py", "evaluation/campaign_zephyr_ext087.py", "evaluation/campaign_real_ext087.py",
                     "conformance/reference/mqtt_owner_gateway_ext087.py", "firmware/zephyr-boot/src/evc_boot_report.c",
                     "firmware/zephyr-boot/CMakeLists.txt", "deploy/setup/build_zephyr_boot.sh", "evaluation/power_extra087_testbed.py",
                     "evaluation/sim_predictions/extra087_power.csv", "evaluation/sim_predictions/extra087_templates.csv",
                     "evaluation/sim_predictions/extra087_claim_ids.csv", "evaluation/sh087.py", "evaluation/rb087.py"):
            self.assertIn(path, f087)
        for path, h in f087.items():
            self.assertEqual(sha(REPO / path), h, path)

    def test_087_frozen(self):
        r = subprocess.run([sys.executable, "scripts/freeze_prereg.py", "--check", PLAN], cwd=REPO, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        h = sha(REPO / PLAN)
        for name in CONFIGS:
            self.assertEqual(cfg(name)["preregistration"], {"path": PLAN, "sha256": h}, name)


# ------------------------------------------------------------------ K4
class K4Configs(unittest.TestCase):
    def test_copies(self):
        for name, base in CONFIGS.items():
            c, o = cfg(name), cfg(base)
            self.assertEqual(c["seed"], 20261105)
            self.assertEqual(c["name"], name.replace("_", "-"))
            self.assertEqual(c["regime"], c["name"])
            same = set(o) - {"name", "regime", "notes", "preregistration", "policies", "contracts", "real_owner", "episodes", "duration_s",
                             "tail_s", "authority", "exclude_cells"}
            for k in same:
                self.assertEqual(c[k], o[k], (name, k))
            a = dict(c["authority"])
            if "SHR" in name:
                self.assertEqual(a.pop("release_after"), "10s")
                self.assertEqual(c["policies"], ["EVICON", "SHADOW_RECONCILE", "SHADOW_RECONCILE_1S"])
            self.assertEqual(a, o["authority"], name)
            if not sim_only():
                self.assertEqual((c["episodes"], c["duration_s"], c["tail_s"]), (6 if "RB" in name else 8, o["duration_s"], o["tail_s"]))
            ro, oro = dict(c["real_owner"]), dict(o["real_owner"])
            if "minio" in name:
                self.assertEqual(c["contracts"], ["MINIO_COND"])
                self.assertEqual(ro.pop("contracts"), {"MINIO_COND": oro.pop("contracts")["MINIO_COND"]})
                self.assertEqual(ro, oro)
                continue
            for k in set(oro) - {"contracts"}:
                self.assertEqual(ro[k], oro[k], (name, k))
            fdir = dict(oro["contracts"]["ZEPHYR_F_DIRECT"])
            if name == "expSHR_zephyr_w6":
                self.assertEqual(c["contracts"], ["ZEPHYR_CUTOFF_DIRECT"])
                fdir.pop("restrict_to")
                self.assertEqual(ro["contracts"], {"ZEPHYR_CUTOFF_DIRECT": fdir})  # D1: the unrestricted measured contract
                self.assertEqual(ro["firmware_variant"], "default")
                self.assertNotIn("owner_reboot", ro)
            else:
                self.assertEqual(c["contracts"], ["NEG_ZEPHYR_F_BOOT", "ZEPHYR_F_BOOT"])
                self.assertEqual(c["policies"], ["EVICON_FI_REC_OS", "EVICON_FI", "EVICON_FI_REC"])
                self.assertEqual(ro["contracts"], {"NEG_ZEPHYR_F_BOOT": fdir, "ZEPHYR_F_BOOT": fdir})
                self.assertEqual(sorted(map(tuple, c["exclude_cells"])), [("NEG_ZEPHYR_F_BOOT", "EVICON_FI"), ("NEG_ZEPHYR_F_BOOT", "EVICON_FI_REC"),
                                                                         ("ZEPHYR_F_BOOT", "EVICON_FI_REC_OS")])
                self.assertEqual(ro["firmware_variant"], "boot")
                self.assertEqual(ro["gateway_recovery_policies"], ["EVICON_FI_REC_OS", "EVICON_FI_REC"])
                rb = ro["owner_reboot"]
                self.assertEqual(rb["down_s"], 2.0)
                if not sim_only():
                    self.assertEqual(rb["per_device"], [{"kind": "quiet", "range_s": [60, 145], "after_arrival_s": 1.5},
                                                        {"kind": "random", "range_s": [150, 240]}])


# ------------------------------------------------------------------ K5
class K5Schedules(unittest.TestCase):
    def test_same_schedules(self):
        import campaign_zephyr_ext087 as Z
        for name, base in CONFIGS.items():
            c, o = cfg(name), cfg(base)
            for ep in range(c["episodes"]):
                if sim_only():  # a SIM-ONLY copy keeps the seed with a shorter cell: its arrivals are a prefix of the SR ones
                    a1, a2 = C.schedule(c, ep)[0], C.schedule(o, ep)[0]
                    self.assertEqual(a1, [x for x in a2 if x.t < c["duration_s"]][:len(a1)], (name, ep))
                    continue
                self.assertEqual(C.schedule(c, ep), C.schedule(o, ep), (name, ep))
        rb = cfg("expRB_zephyr_w6")
        for ep in range(2):
            s1, s2 = Z.reboot_schedule(rb, ep), Z.reboot_schedule(rb, ep)
            self.assertEqual(s1, s2)
            self.assertEqual(len(s1), rb["devices"] * len(rb["real_owner"]["owner_reboot"]["per_device"]))
            arr, _ = C.schedule(rb, ep)
            for r in s1:
                if r["kind"] == "quiet" and r.get("gap_s"):
                    ts = [a.t for a in arr if a.device == r["device"]]
                    self.assertFalse([t for t in ts if r["gap_start_s"] + 0.001 < t < r["gap_start_s"] + r["gap_s"] - 0.001], r)
                    self.assertLess(r["gap_start_s"], r["t"])
        self.assertEqual(Z.reboot_schedule(cfg("expSHR_zephyr_w6"), 0), [])


# ------------------------------------------------------------------ K6
class K6Plan(unittest.TestCase):
    def test_rules(self):
        spec, ext = block("pairs-spec"), block("pairs-ext")
        hs = {h["id"]: h for h in spec["hypotheses"]}
        xs = {h["id"]: h for h in ext["hypotheses"]}
        self.assertEqual(len(hs) + 1 + len(xs) + 1, 17)  # with SHR-S and the check RB-C1
        for i, (run, ct, pol) in enumerate([("SHRz6", "ZEPHYR_CUTOFF_DIRECT", p) for p in ("EVICON", "SHADOW_RECONCILE", "SHADOW_RECONCILE_1S")]
                                           + [("SHRm6", "MINIO_COND", p) for p in ("EVICON", "SHADOW_RECONCILE", "SHADOW_RECONCILE_1S")], 1):
            h = hs[f"SHR-A{i}"]
            self.assertEqual((h["run"], h["contract"], h["policy"], h["test"], h["any_of"]), (run, ct, pol, "absent", SAFETY))
        self.assertEqual((hs["RB-A1"]["policy"], hs["RB-A2"]["policy"]), ("EVICON_FI_REC", "EVICON_FI"))
        self.assertEqual((hs["RB-D1"]["test"], hs["RB-D1"]["contract"], hs["RB-D1"]["any_of"]), ("detects", "NEG_ZEPHYR_F_BOOT", ["bound_violations"]))
        self.assertEqual(spec["safety"], {"id": "SHR-S", "runs": ["SHRz6", "SHRm6"], "exempt_contracts": []})
        self.assertEqual(xs["RB-S"], {"id": "RB-S", "test": "safety", "runs": ["RBz6"], "exempt_contracts": ["NEG_ZEPHYR_F_BOOT"]})
        for i in range(1, 5):
            h = xs[f"SHR-{i}"]
            self.assertEqual((h["test"], h["margin"], h["metric"], h["from_policy"]), ("equivalent", 3.0, "clean_pp", "EVICON"))
            self.assertEqual(h["family"], "shr-fw" if i <= 2 else "shr-minio")
            self.assertEqual(h["to_policy"], "SHADOW_RECONCILE" if i % 2 else "SHADOW_RECONCILE_1S")
        self.assertEqual((xs["RB-1"]["test"], xs["RB-1"]["margin"], xs["RB-1"]["from_policy"], xs["RB-1"]["to_policy"], xs["RB-1"]["family"]),
                         ("at_least", 15.0, "EVICON_FI", "EVICON_FI_REC", "rb"))
        text = " ".join((REPO / PLAN).read_text().split())
        for s in ("**outside** it", "owner up (no owner crash or reboot)", "REC-4 changes only the window accounting of identities at or below a durably installed cutoff",
                  "the bound argument does not use the window", "coordinator decision D1", "coordinator decision D2", "coordinator decision D4",
                  "coordinator decision D5", "Interpretation fixed in advance", "185 verdicts"):
            self.assertIn(s, text)


# ------------------------------------------------------------------ K7
class K7Power(unittest.TestCase):
    def test_power(self):
        import power_extra087_testbed as PW
        rows = list(csv.DictReader(open(REPO / "evaluation" / "sim_predictions" / "extra087_power.csv")))
        for rule, (fam, primary, pred, test, margin) in PW.RULES.items():
            n = 6 if rule == "RB-1" else 8
            r = [x for x in rows if x["rule"] == rule and x["template"] == primary and float(x["true_mean"]) == pred
                 and float(x["spread_factor"]) == 1.0 and int(x["n"]) == n]
            self.assertEqual(len(r), 1, rule)
            self.assertGreaterEqual(float(r[0]["power"]), 0.80, rule)
        tpl = list(csv.DictReader(open(REPO / "evaluation" / "sim_predictions" / "extra087_templates.csv")))
        self.assertEqual({r["template"] for r in tpl}, set(PW.SOURCES))
        for r in tpl:
            self.assertRegex(r["source_sha256_16"], r"^[0-9a-f]{16}$")
            self.assertTrue(r["source"].startswith("report/pairs/PREREG_extra_08"))


# ------------------------------------------------------------------ K8
class K8Authority(unittest.TestCase):
    def test_flag(self):
        main = (REPO / "cmd" / "evicon-authority" / "main.go").read_text()
        self.assertRegex(main, r'flag\.Bool\("reboot-recovery", false,')
        src = "".join(p.read_text() for p in (REPO / "internal").rglob("*_test.go"))
        for t in ("TestRebootRecoveryDefaultOff087", "TestRecSequenceAdvance087", "TestRecFloorCheck087", "TestRecWindow087",
                  "TestRebootRecoveryProperty087"):
            self.assertIn(f"func {t}(", src)
        jf = (REPO / "internal" / "journal" / "owner_floor.go").read_text()
        self.assertIn("CREATE TABLE IF NOT EXISTS owner_floor", jf)
        schema = "".join(p.read_text() for p in (REPO / "internal" / "journal").glob("*.go") if p.name != "owner_floor.go" and not p.name.endswith("_test.go"))
        self.assertNotIn("owner_floor", schema)  # created on first use only: an authority without the flag keeps 086's schema
        rr = (REPO / "internal" / "runtime" / "reboot_recovery.go").read_text()
        self.assertIn("if !a.cfg.RebootRecovery", rr)


# ------------------------------------------------------------------ K9
class _Link:
    def __init__(self):
        self.up, self.port, self.pending = True, 0, []

    def set_up(self, v):
        self.up = v

    def describe(self):
        return {"up": self.up}


class _Conn:
    def __init__(self, gw, fw, role):
        self.gw, self.fw, self.role = gw, fw, role
        self.subscribed = __import__("threading").Event()
        self.subscribed.set()
        self.published = []

    def publish(self, kind, body, qos):
        self.published.append((kind, dict(body)))
        rep = self.fw(kind, body)
        if rep is not None:
            self.gw.on_reply(kind, rep)

    def close(self):
        pass


def _fw(floor=None):
    """A firmware model: applies every command (level += value), answers fences with the cutoff; floor in every reply if given."""
    st = {"level": 0, "cutoff": 0, "ov": 0, "floor": floor}

    def handle(kind, b):
        if kind == "ping":
            rep = {"device": "pump0-1", "mode": "cutoff", "ok": True, "level": st["level"]}
        elif kind == "cmd":
            st["level"] += int(b["value"])
            st["ov"] += 1
            rep = {"cmd": b["cmd"], "seq": b["seq"], "outcome": "APPLIED", "reason": "APPLIED", "level": st["level"], "owner_version": st["ov"]}
        elif kind == "fence":
            st["cutoff"] = max(st["cutoff"], int(b["cutoff"]))
            rep = {"ok": True, "cutoff": st["cutoff"], "token": b["token"], "level": st["level"], "owner_version": max(1, st["ov"])}
        elif kind == "query":
            rep = {"cmd": b["cmd"], "seq": b["seq"], "outcome": "STOPPED", "reason": "SEALED_NOT_STARTED", "level": st["level"], "owner_version": 1}
        else:
            return None
        if st["floor"] is not None:
            rep = dict(rep, floor=st["floor"])
        return rep
    handle.st = st
    return handle


def _contract(tmp: Path) -> Path:
    from tests.test_fence_anchor_reboot import MEASURED_CUTOFF
    p = tmp / "contract.json"
    p.write_text(json.dumps(MEASURED_CUTOFF))
    return p


def _gw(tmp: Path, floor=None, recovery=False):
    from reference import mqtt_owner_gateway_ext087 as G87
    fw = _fw(floor)
    a = argparse.Namespace(mode="CUTOFF", device_id="pump0-1", ns="t", generation=1, epoch=1, listen="", admin_listen="", broker="127.0.0.1:1",
                           contract_file=str(_contract(tmp)), state=str(tmp / "gw.state.jsonl"), budget_s=0.3, qos_cmd=1, keepalive_s=60,
                           offline_mode="drop", binding="qos1", cut=False, recovery=recovery, log=str(tmp / "gw.jsonl"))
    conns = {"fw": fw}

    def net(gw):
        conns["data"], conns["ctl"] = _Conn(gw, fw, "data"), _Conn(gw, fw, "ctl")
        return _Link(), conns["data"], conns["ctl"]
    return G87.Ext087Gateway(a, net=net), conns


def _effect(cid, seq, value=2):
    from reference.s3_owner_gateway import canonical_hash
    req = {"schema_version": 1, "domain_id": "pump0-1", "generation": 1, "command_id": cid, "operation": "ADD", "value": [value], "epoch": 1,
           "sequence": seq}
    req["intent_hash"] = canonical_hash(req)
    return req


class K9Gateway(unittest.TestCase):
    def test_no_floor_is_the_077_gateway(self):
        import time
        g, _ = _gw(tmpdir(self), floor=None, recovery=False)
        r = g.effect(_effect("c1", 1), time.monotonic())
        self.assertNotIn("claim_floor", r)
        self.assertEqual(r["outcome"], "APPLIED")
        b = g.barrier({"schema_version": 1, "domain_id": "pump0-1", "generation": 1, "token": "t1", "cutoff_sequence": 3}, time.monotonic())
        self.assertNotIn("claim_floor", b)
        self.assertIsNone(g.floor)
        g.close()

    def test_floor_and_seal(self):
        import time
        from reference.s3_owner_gateway import Reject
        t = tmpdir(self)
        g, conns = _gw(t, floor=0, recovery=True)
        self.assertEqual(g.floor, 0)  # learned from the ping at start
        r = g.effect(_effect("c1", 1), time.monotonic())
        self.assertEqual(r["claim_floor"], 0)
        conns["fw"].st["floor"] = 65  # the firmware rebooted: its replies carry the new floor
        g.on_reply("hello", {"device": "pump0-1", "hello": True, "floor": 65})
        self.assertEqual(g.floor, 65)
        g.on_reply("hello", {"device": "pump0-1", "hello": True, "floor": 3})  # a lower report is never adopted
        self.assertEqual(g.floor, 65)
        n = len(conns["data"].published)
        r = g.effect(_effect("c2", 2), time.monotonic())
        self.assertEqual((r["outcome"], r["reason"], r["claim_floor"]), ("STOPPED", "GATEWAY_SEALED_BELOW_CLAIM_FLOOR", 65))
        self.assertEqual(len(conns["data"].published), n, "a sealed identity is never published")
        r2 = g.effect(_effect("c2", 2), time.monotonic())  # the same request again: the same answer
        self.assertEqual(r2["outcome"], "STOPPED")
        with self.assertRaises(Reject) as e:
            g.effect(_effect("c2", 2, value=3), time.monotonic())
        self.assertEqual(e.exception.code, 409)
        r3 = g.effect(_effect("c1", 1), time.monotonic())  # forwarded before the floor: not sealed (the 077 path)
        self.assertNotEqual(r3.get("reason"), "GATEWAY_SEALED_BELOW_CLAIM_FLOOR")
        self.assertEqual(g.effect(_effect("c66", 66), time.monotonic())["outcome"], "APPLIED")
        b = g.barrier({"schema_version": 1, "domain_id": "pump0-1", "generation": 1, "token": "tk", "cutoff_sequence": 66}, time.monotonic())
        self.assertEqual(b["claim_floor"], 65)  # the fence reply's own floor
        self.assertNotIn("c2", {x["command_id"] for x in b["receipts"]})  # never forwarded: not listed
        g.close()
        # durable: a restarted gateway keeps the floor and the seal
        g2, conns2 = _gw(t, floor=65, recovery=True)
        self.assertIn("c2", g2.sealed)
        with self.assertRaises(Reject):
            g2.forward("c2", 2, "x", 2, "effect")
        g2.close()
        recs = [json.loads(x) for x in (t / "gw.state.jsonl").read_text().splitlines()]
        self.assertTrue(any(x.get("k") == "seal" and x["cid"] == "c2" for x in recs))
        self.assertTrue(any(x.get("k") == "floor" and x["floor"] == 65 for x in recs))

    def test_switch_off_does_not_seal(self):
        import time
        g, _ = _gw(tmpdir(self), floor=65, recovery=False)
        r = g.effect(_effect("c2", 2), time.monotonic())
        self.assertEqual(r["outcome"], "APPLIED")  # the model firmware applies it; the real one refuses it (the guard)
        self.assertEqual(r["claim_floor"], 65)
        g.close()


# ------------------------------------------------------------------ K10
class K10Runner(unittest.TestCase):
    def test_runner(self):
        import campaign_zephyr_ext087 as Z
        src = (REPO / "evaluation" / "campaign_zephyr_ext087.py").read_text()
        self.assertNotIn("pkill", src)
        self.assertNotIn("killall", src)
        stop = Z._stop_script("/w d", "firmware0", 1.0)
        for s in ("firmware0.pid", "readlink /proc/$p/cwd", "kill -TERM $p", "kill -KILL $p", "notrunning"):
            self.assertIn(s, stop)
        self.assertTrue(issubclass(Z.RebootFaultRunner, C.FaultRunner))
        self.assertTrue(issubclass(Z.Ext087Testbed, Z.Z86.Ext086Testbed))
        self.assertIn('Path(wd, "gateway.recovery").write_text(("1" if rec else "0") + "\\n")', src)
        bad = cfg("expRB_zephyr_w6")
        bad["real_owner"]["firmware_variant"] = "default"
        with self.assertRaises(ValueError):
            Z.check_087(bad)
        bad = cfg("expRB_zephyr_w6")
        bad["real_owner"]["gateway_recovery_policies"] = ["EVICON_GREEDY"]
        with self.assertRaises(ValueError):
            Z.check_087(bad)
        Z.check_087(cfg("expRB_zephyr_w6"))
        Z.check_087(cfg("expSHR_zephyr_w6"))
        # the frozen loaders accept the config once the 087 keys are taken out (contract files as the driver places them)
        t = tmpdir(self)
        c = cfg("expRB_zephyr_w6")
        from campaign_zephyr_ext import restrict_contract
        from tests.test_fence_anchor_reboot import MEASURED_CUTOFF
        (t / "measured.json").write_text(json.dumps(MEASURED_CUTOFF))
        (t / "f.json").write_text(json.dumps(restrict_contract(MEASURED_CUTOFF, sha(t / "measured.json"), {"F"})))
        for s in c["real_owner"]["contracts"].values():
            s.update(contract_file=str(t / "f.json"), restricted_from=str(t / "measured.json"))
        (t / "c.json").write_text(json.dumps(c))
        self.assertEqual(Z.load_config(t / "c.json")["real_owner"]["owner_reboot"], c["real_owner"]["owner_reboot"])


# ------------------------------------------------------------------ K11
class K11Driver(unittest.TestCase):
    def test_driver(self):
        src = (REPO / "evicon_review.py").read_text()
        for s in ('"evaluation.test_release087"', "evaluation/sh087.py", "evaluation/rb087.py", "evaluation/verdicts087.py",
                  '"Release 086;" in (s / "report" / "REPORT.md").read_text()[:400]', "earlier verdicts against 086", "firmware_boot_host",
                  "formal/run_tlc_reboot.sh"):
            self.assertIn(s, src)
        self.assertEqual(er.FIRMWARE_086_SHA256, FIRMWARE_086)
        self.assertIn("zephyr-boot", er.STACKS)
        self.assertEqual(er.STACKS["zephyr-boot"]["fw_target"], "boot")
        for n in ("expSHR_zephyr_w6", "expRB_zephyr_w6"):
            self.assertEqual(er._zephyr_runner(cfg(n)), "evaluation/campaign_zephyr_ext087.py")
        self.assertEqual(er._zephyr_runner(cfg("expSR_zephyr_w6")), "evaluation/campaign_zephyr_ext086.py")  # earlier configs unchanged
        self.assertEqual(er._zephyr_runner(cfg("expPM_zephyr_w6")), "evaluation/campaign_zephyr_ext085.py")
        self.assertTrue(er._uses_087(cfg("expSHR_minio_w6")))
        self.assertFalse(er._uses_087(cfg("expSR_minio_w6")))
        self.assertEqual(er._emulated_runner(cfg("expSH_queue")), ("evaluation/campaign_ext086.py", []))
        self.assertEqual(er._negative_controls(cfg("expRB_zephyr_w6")), {("NEG_ZEPHYR_F_BOOT", "EVICON_FI_REC_OS")})
        self.assertEqual(er._expected_unsafe(cfg("expRB_zephyr_w6")), set())  # the one rule of release 079, unchanged
        for n in ("expSHR_zephyr_w6", "expSHR_minio_w6", "expSR_zephyr_w6"):
            self.assertEqual(er._negative_controls(cfg(n)), set())
        self.assertEqual(er._negative_controls(cfg("negctl_072")), set())  # the emulated controls: negative_control, as before
        self.assertIn("expected = _expected_unsafe(cfg) | _negative_controls(cfg)", src)
        self.assertLess(src.index("evaluation/rb087.py"), src.index('"evaluation/paper_tables.py", "--report"'))
        self.assertEqual(er.ZEPHYR_MODE_STACK_BOOT["CUTOFF"][0], "zephyr-boot")


# ------------------------------------------------------------------ K12
class K12Script(unittest.TestCase):
    def test_run087(self):
        s = (REPO / SCRIPT).read_text()
        self.assertFalse((REPO / "run086.sh").exists())
        if VERSION != "087":
            self.assertFalse((REPO / "run087.sh").exists())  # release 088: run088.sh replaces it (test_release088)
        self.assertIn('CAMPAIGNS="expSHR_zephyr_w6 expSHR_minio_w6 expRB_zephyr_w6"', s)
        order = [s.index(x) for x in ("E preflight build verify distribute", "for st in zephyr zephyr-boot", 'E campaign "$c"',
                                       'E report --archive "$ARCHIVE" --from "$R078" --from "$R083" --from "$R084" --from "$R085" --from "$R086"')]
        self.assertEqual(order, sorted(order))
        for x in ("REFUSED: another evicon_review.py is running", "STOP: unplanned safety violation", "not sent yet", f"EVICON_{VERSION}_result_",
                  "The 185 earlier verdicts are those of the 086 final report", "build_iot.sh start zephyr_boot"):
            self.assertIn(x, s)
        self.assertNotIn("EVICON_086_result", s)
        self.assertNotIn("RUN_GUIDE" + "_VI", s)
        r = subprocess.run(["bash", "-n", str(REPO / SCRIPT)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((REPO / GUIDE).exists())
        self.assertFalse((REPO / ("EVICON_086_" + "RUN_GUIDE" + "_VI.md")).exists())
        g = (REPO / GUIDE).read_text()
        for x in (SCRIPT, "build_iot.sh start zephyr_boot", "BOOT_BUILD_OK", "907b2ccd", "expRB_zephyr_w6", "NEG_ZEPHYR_F_BOOT"):
            self.assertIn(x, g)


# ------------------------------------------------------------------ K13
def _synthetic_run(t: Path, name: str, contract: str, policies: list, eps: int, extra_rows=None) -> Path:
    run = t / name / "20261110T000000"
    rows = []
    for ep in range(eps):
        for i, pol in enumerate(policies):
            cd = run / "cells" / f"ep{ep}" / contract / pol
            (cd / "raw" / "A").mkdir(parents=True)
            ev = []
            if pol.startswith("SHADOW"):
                ev = [{"event": "SHADOW_RECONCILE_RESULT", "outcome": "NOT_FOUND", "reason": "action=REPUBLISH;x"},
                      {"event": "SHADOW_RECONCILE_RESULT", "outcome": "UNCERTAIN", "reason": "action=WAIT;x"}]
            (cd / "raw" / "A" / "authority0.jsonl").write_text("".join(json.dumps(e) + "\n" for e in ev))
            (cd / "clients.jsonl").write_text(json.dumps({"cid": "d0-c1", "device": 0, "arrival": 1.0, "value": 4, "admitted": {"d0-c1": 1.5},
                                                          "final_status": {"d0-c1": "PENDING" if i else "APPLIED"}, "ids": ["d0-c1"]}) + "\n")
            r = {k: 0 for k in C.ROW_FIELDS}
            r.update(regime=name, contract=contract, policy=pol, episode=ep, devices=4, cell_status="VALID", offered=100, admitted=90,
                     clean_fulfilled=80 - i - ep % 2, dispatch_attempts=100, queries=10, unresolved_at_end=i, mean_time_to_terminal_s=1.0 + i,
                     defer_reasons=json.dumps({"OUTSTANDING_BUDGET_EXHAUSTED": 3}))
            r.update((extra_rows or {}).get(pol, {}))
            rows.append(r)
    with open(run / "runtime_episodes.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=C.ROW_FIELDS)
        w.writeheader()
        w.writerows(rows)
    (run / "config.json").write_text(json.dumps({"name": name, "workload": {"deadline_s": 60}}))
    return run


class K13Sh087(unittest.TestCase):
    def test_estimands(self):
        import sh087
        t = tmpdir(self)
        run = _synthetic_run(t, "expSHR-zephyr-w6", "ZEPHYR_CUTOFF_DIRECT", ["EVICON", "SHADOW_RECONCILE", "SHADOW_RECONCILE_1S"], 3)
        r = sh087.analyse({"SHRz6": run}, {})
        o = r["owners"]["SHRz6"]
        self.assertEqual(o["contract"], "ZEPHYR_CUTOFF_DIRECT")
        self.assertAlmostEqual(o["levels"]["EVICON"]["owner_calls_per_offered"]["mean"], 1.1)
        self.assertAlmostEqual(o["levels"]["SHADOW_RECONCILE"]["owner_calls_per_offered"]["mean"], 1.12)
        self.assertAlmostEqual(o["levels"]["SHADOW_RECONCILE"]["republishes_per_offered"]["mean"], 0.01)
        self.assertEqual(o["levels"]["SHADOW_RECONCILE"]["budget_held_end"]["mean"], 4.0)
        self.assertEqual(o["levels"]["EVICON"]["budget_held_end"]["mean"], 0.0)
        self.assertEqual(o["contrasts"]["EVICON -> SHADOW_RECONCILE"]["clean_pp"]["mean"], -1.0)
        self.assertEqual(o["reads_by_outcome"]["SHADOW_RECONCILE"], {"NOT_FOUND": 3, "UNCERTAIN": 3})
        self.assertEqual(o["levels"]["EVICON"]["window_defers"]["mean"], 3.0)
        self.assertIn("E-SHR", sh087.markdown(r))


# ------------------------------------------------------------------ K14
class K14Provenance(unittest.TestCase):
    def test_published(self):
        sys.path.insert(0, str(REPO / "scripts"))
        import provenance as PV
        self.assertEqual((PV.NEWEST_PLAN, PV.NEWEST_RELEASE), ("PREREG_extra_087.md", VERSION))
        p = PV.PUBLISHED["PREREG_extra_086.md"]
        self.assertEqual((p[0]["commit"], p[0]["utc"], p[0]["sha256"]), ("440ecf1", "2026-11-05 06:21", EARLIER["evaluation/prereg/PREREG_extra_086.md"]))
        if VERSION == "087":
            self.assertEqual(PV.PUBLISHED["PREREG_extra_087.md"], [])
        else:  # release 088: plan 087 as published by release 087 (2e98edc), unchanged
            p = PV.PUBLISHED["PREREG_extra_087.md"]
            self.assertEqual((p[0]["commit"], p[0]["sha256"]), ("2e98edc", sha(REPO / PLAN)))
        self.assertIn("review-20261105T134319", " ".join(PV.DEVIATIONS["PREREG_extra_086.md"]))
        self.assertTrue(" ".join(PV.DEVIATIONS["PREREG_extra_086.md"]).startswith("None:"))
        top = (REPO / "PROVENANCE.md").read_text()
        for s in ("440ecf1", "expSR-zephyr-w6/20261105T144816", f"expRB-zephyr-w6/<to be filled by the {VERSION} report>", f"(release {VERSION})"):
            self.assertIn(s, top)


# ------------------------------------------------------------------ K15
NEW_FILES = ["internal/runtime/reboot_recovery.go", "internal/runtime/reboot_recovery_test.go", "internal/journal/owner_floor.go",
             "evaluation/ext087.py", "evaluation/campaign_zephyr_ext087.py", "evaluation/campaign_real_ext087.py", "evaluation/sh087.py",
             "evaluation/rb087.py", "evaluation/verdicts087.py", "evaluation/power_extra087_testbed.py", "evaluation/test_release087.py", PLAN,
             SCRIPT, "conformance/reference/mqtt_owner_gateway_ext087.py", "conformance/tests/test_reboot_recovery087.py",
             "firmware/zephyr-boot/CMakeLists.txt", "firmware/zephyr-boot/Kconfig", "firmware/zephyr-boot/prj.conf", "firmware/zephyr-boot/README.md",
             "firmware/zephyr-boot/src/evc_boot_report.c", "firmware/zephyr-boot/src/evc_boot_report.h", "firmware/zephyr-boot/tests/host/Makefile",
             "firmware/zephyr-boot/tests/host/evc_boot_shim.c", "firmware/zephyr-boot/tests/host/test_evc_boot_report.c",
             "deploy/setup/build_zephyr_boot.sh", "formal/EviconFenceReboot.tla", "formal/run_tlc_reboot.sh", "formal/TLC_RESULTS_REBOOT.txt",
             "evaluation/campaigns/expSHR_zephyr_w6.json", "evaluation/campaigns/expSHR_minio_w6.json", "evaluation/campaigns/expRB_zephyr_w6.json"]


class K15Hygiene(unittest.TestCase):
    def test_new_files(self):
        forbidden = "EC" + "HO"
        guide = "RUN_GUIDE" + "_VI"
        for f in NEW_FILES:
            s = (REPO / f).read_text()
            self.assertNotIn(forbidden, s, f)
            self.assertFalse(re.search("[" + chr(0xC0) + "-" + chr(0x1EF9) + "]", s), f"non-English characters in {f}")
            if f != SCRIPT:
                self.assertNotIn(guide, s, f)
        for p in list((REPO / "evaluation").glob("test_*.py")) + list((REPO / "conformance" / "tests").glob("test_*.py")):
            self.assertNotIn(guide, p.read_text(), p.name)


# ------------------------------------------------------------------ K16
class K16Firmware(unittest.TestCase):
    def test_boot_build(self):
        cm = (REPO / "firmware" / "zephyr-boot" / "CMakeLists.txt").read_text()
        self.assertIn("set(EVC_SRC ${CMAKE_CURRENT_SOURCE_DIR}/../zephyr-cutoff/src)", cm)
        self.assertIn('"evc_core_init=evc_boot_core_init;mqtt_publish=evc_boot_mqtt_publish;mqtt_subscribe=evc_boot_mqtt_subscribe"', cm)
        self.assertFalse(list((REPO / "firmware" / "zephyr-boot" / "src").glob("main.c")) + list((REPO / "firmware" / "zephyr-boot" / "src").glob("evc_core*")))
        f086 = frozen("evaluation/prereg/PREREG_extra_086.md")
        for path, h in f086.items():
            if path.startswith("firmware/zephyr-cutoff/"):
                self.assertEqual(sha(REPO / path), h, path)
        mk = (REPO / "firmware" / "zephyr-boot" / "tests" / "host" / "Makefile").read_text()
        for s in ("libevcbootshim.so", "-DEVC_BOOT_HOST_CORE_ONLY", "$(REDIRECT) $(CUT)/main.c"):
            self.assertIn(s, mk)
        b = (REPO / "deploy" / "setup" / "build_zephyr_boot.sh").read_text()
        self.assertIn('-d "$OUT/native-boot"', b)
        self.assertNotIn('-d "$OUT/native"', b)
        self.assertNotIn('cp -f "$OUT/native-boot/zephyr/zephyr.exe" "$OUT/zephyr-cutoff.exe"', b)
        self.assertIn('[ "$before" = "$after" ] || fail', b)
        self.assertIn("BOOT_BUILD_OK", b)
        bi = (REPO / "deploy" / "setup" / "build_iot.sh").read_text()
        self.assertIn("zephyr|matter|zephyr_boot", bi)
        self.assertIn("firmware/zephyr-boot", bi)
        r = subprocess.run(["bash", "-n", str(REPO / "deploy" / "setup" / "build_zephyr_boot.sh")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


# ------------------------------------------------------------------ K17
class K17Rb087(unittest.TestCase):
    def test_estimands(self):
        import rb087
        t = tmpdir(self)
        pols = ["EVICON_FI", "EVICON_FI_REC"]
        run = _synthetic_run(t, "expRB-zephyr-w6", "ZEPHYR_F_BOOT", pols, 2)
        for ep in range(2):
            for pol in pols:
                cd = run / "cells" / f"ep{ep}" / "ZEPHYR_F_BOOT" / pol
                (cd / "reboots.jsonl").write_text(json.dumps({"device": 0, "kind": "quiet", "ok": True, "t": 60, "killed_at": 100.0, "started_at": 102.0,
                                                              "first_answer_at": 103.0, "floor": 65}) + "\n")
                adm = {"d0-c5": 104.0, "d0-c6": 110.0} if pol == "EVICON_FI_REC" else {}
                (cd / "clients.jsonl").write_text("".join(json.dumps({"cid": c, "device": 0, "arrival": a - 0.5, "value": 2, "admitted": {c: a},
                                                                      "final_status": {c: "APPLIED"}, "ids": [c]}) + "\n" for c, a in adm.items())
                                                  + json.dumps({"cid": "d0-c9", "device": 0, "arrival": 105.0, "value": 3, "admitted": {"d0-c9": 95.0},
                                                                "final_status": {"d0-c9": "PENDING"}, "ids": ["d0-c9"]}) + "\n")
                (cd / "raw" / "E").mkdir()
                (cd / "raw" / "E" / "plant.jsonl").write_text("".join(json.dumps({"cmd": c, "t_ns": 1}) + "\n" for c in adm))
                if pol == "EVICON_FI_REC":
                    (cd / "raw" / "A" / "authority0.jsonl").write_text(json.dumps({"event": "FENCE_ANCHOR_CHECK", "outcome": "MATCH_FLOOR"}) + "\n"
                                                                       + json.dumps({"event": "OWNER_FLOOR", "reason": "floor=65"}) + "\n")
        r = rb087.analyse(run, None)
        self.assertEqual(r["RB-C1"]["verdict"], "MET")
        a = r["arms"]["ZEPHYR_F_BOOT/EVICON_FI_REC"]
        self.assertEqual(a["per_kind"]["quiet"]["admissions_mean"], 2.0)
        self.assertEqual(a["per_kind"]["quiet"]["admissions_effective_mean"], 2.0)
        self.assertEqual(a["per_kind"]["quiet"]["s_to_first_admission_median"], 1.0)
        self.assertEqual(a["unresolved_at_end"]["budget"], 6)
        self.assertEqual(a["fence_anchor_check"], {"MATCH_FLOOR": 2})
        self.assertEqual(r["arms"]["ZEPHYR_F_BOOT/EVICON_FI"]["per_kind"]["quiet"]["admissions_mean"], 0.0)
        cd = run / "cells" / "ep1" / "ZEPHYR_F_BOOT" / "EVICON_FI_REC" / "raw" / "A" / "authority0.jsonl"
        cd.write_text(cd.read_text() + json.dumps({"event": "FENCE_ANCHOR_CHECK", "outcome": "MISMATCH_FLOOR"}) + "\n")
        r = rb087.analyse(run, None)
        self.assertEqual((r["RB-C1"]["verdict"], r["RB-C1"]["count"], r["RB-C1"]["cells"]), ("NOT MET", 1, ["ep1"]))
        self.assertIn("RB-C1", rb087.markdown(r))

    def test_one_copy(self):
        """A simulated testbed fetches the same file once per VM directory: each trace is read once (the writer's copy)."""
        import rb087
        import sh087
        cd = tmpdir(self)
        seal = json.dumps({"op": "seal", "fresh": True}) + "\n"
        check = json.dumps({"event": "FENCE_ANCHOR_CHECK", "outcome": "MATCH_FLOOR"}) + "\n"
        read = json.dumps({"event": "SHADOW_RECONCILE_RESULT", "outcome": "NOT_FOUND", "reason": "action=REPUBLISH"}) + "\n"
        for vm in ("A", "C", "E"):
            (cd / "raw" / vm).mkdir(parents=True)
            (cd / "raw" / vm / "authority0.jsonl").write_text(check + read)
            (cd / "raw" / vm / "gateway0.jsonl").write_text(seal)
        self.assertEqual([p.parent.name for p in sh087.one_copy(cd, "authority*.jsonl")], ["A"])
        self.assertEqual([p.parent.name for p in sh087.one_copy(cd, "gateway*.jsonl")], ["C"])
        tc = rb087.trace_counts(cd)
        self.assertEqual((tc["fence_anchor_check"], tc["gateway_seals"]), ({"MATCH_FLOOR": 1}, 1))
        self.assertEqual(sh087.trace_counts(cd), {"reads": {"NOT_FOUND": 1}, "republishes": 1})


# ------------------------------------------------------------------ K18
class K18Verdicts(unittest.TestCase):
    def test_compare(self):
        import verdicts087 as V
        t = tmpdir(self)
        old = {"hypotheses": [{"plan": "PREREG_extra_086", "id": "SR-1", "verdict": "MET", "estimate": "1"},
                              {"plan": "PREREG_extra_086", "id": "SR-2", "verdict": "MET", "estimate": "2"}]}
        new = {"hypotheses": [{"plan": "PREREG_extra_086", "id": "SR-1", "verdict": "NOT MET", "estimate": "1"},
                              {"plan": "PREREG_extra_087", "id": "SHR-1", "verdict": "MET", "estimate": "0"}]}
        (t / "old.json").write_text(json.dumps(old))
        (t / "new.json").write_text(json.dumps(new))
        r = V.compare(t / "new.json", t / "old.json")
        self.assertFalse(r["identical"])
        self.assertEqual([x["id"] for x in r["verdicts_differ"]], ["SR-1"])
        self.assertEqual(r["missing_in_new"], [["PREREG_extra_086", "SR-2"]])
        self.assertEqual(r["new_in_new"], [])
        r = V.compare(t / "old.json", t / "old.json")
        self.assertTrue(r["identical"])


# ------------------------------------------------------------------ K19
class K19Archive(unittest.TestCase):
    def test_scan(self):
        sys.path.insert(0, str(REPO / "scripts"))
        import make_data_archive as M
        for ok in ('"reason": "token=evicon-sweep--r121;mode=evidence;computed=[19];reported=[19]"', "token=evicon-certificate-d1-c116-r45;mode=evidence",
                   "token=evicon-pending_bound-d2-c54-r77-a2", "token=evicon-periodic--r9", "token=rb-1", "token=fence-abc123", "token: f-0a1b2c3d",
                   "token=1234abcd-xyz", "password=MINIO_ROOT_PASSWORD_ENV"):
            self.assertFalse(M.line_hit(ok), ok)
        for bad in ("password=hunter2hunter2", "token=abcdefghijklmnop", "-----BEGIN RSA PRIVATE KEY-----", "AKIAABCDEFGHIJKLMNOP",
                    "token=evicon-sweep--r121 password=supersecret99", "api_key: zzzzzzzzzzzz", "token=evicon-bogus-d1-r3x"):
            self.assertTrue(M.line_hit(bad), bad)
        t = tmpdir(self)
        (t / "a.jsonl").write_text('{"event":"FENCE_ANCHOR_CHECK","reason":"token=evicon-sweep--r121;mode=evidence"}\n')
        self.assertEqual(M.scan(t, 5), [])
        (t / "b.log").write_text("token=evicon-sweep--r121 secret=abcdefgh12345\n")
        self.assertEqual(len(M.scan(t, 5)), 1)


# ------------------------------------------------------------------ K20
class K20Root(unittest.TestCase):
    def test_no_stray_tests(self):
        self.assertEqual(sorted(p.name for p in REPO.glob("test_*.py")), [])
        self.assertTrue((REPO / "conformance" / "tests" / "test_fence_anchor_reboot.py").exists())
        self.assertTrue((REPO / "conformance" / "tests" / "test_reboot_recovery087.py").exists())


# ------------------------------------------------------------------ K21
class K21Formal(unittest.TestCase):
    def test_tlc_results(self):
        res = (REPO / "formal" / "TLC_RESULTS_REBOOT.txt").read_text()
        want = {"recovery": "no error", "recovery_nocheck": "no error", "r086": "no error", "ownerstate": "Invariant BoundSafety is violated",
                "noguard": "Invariant AtMostOncePerIntent is violated", "recovery_witness": "Invariant NoAnchorAfterReboot is violated",
                "r086_conflict_witness": "Invariant NeverConflicted is violated"}
        for c, r in want.items():
            self.assertTrue((REPO / "formal" / f"EviconFenceReboot_{c}.cfg").exists(), c)
            self.assertRegex(res, rf"(?m)^{c}\s+{re.escape(r)}\s", c)
            self.assertTrue((REPO / "formal" / "tlc_logs_reboot" / f"EviconFenceReboot_{c}.log").exists(), c)
        for line in res.splitlines():
            m = re.match(r"^([0-9a-f]{64})  (formal/\S+)$", line)
            if m:
                self.assertEqual(sha(REPO / m.group(2)), m.group(1), m.group(2))
        self.assertIn("NeverConflicted", (REPO / "formal" / "EviconFenceReboot_recovery.cfg").read_text())
        self.assertIn("run_tlc_reboot.sh", (REPO / "evicon_review.py").read_text())


# ------------------------------------------------------------------ K22
class K22Policies(unittest.TestCase):
    def test_policies(self):
        code = ("import sys, json; sys.path.insert(0, 'evaluation'); import campaign as C; a = json.load(open('evaluation/campaigns/expSHR_zephyr_w6.json'))['authority']; "
                "pols = list(C.RESOLVERS) + list(C.LEGACY); before = {p: C.policy_args(p, a) for p in pols}; "
                "import ext086; ext086.install(); pols = list(C.RESOLVERS) + list(C.LEGACY); b86 = {p: C.policy_args(p, a) for p in pols}; "
                "import ext087; ext087.install(); after = {p: C.policy_args(p, a) for p in pols}; "
                "new = {p: C.policy_args(p, a) for p in ext087.POLICIES}; "
                "print(json.dumps({'same': all(after[p] == b86[p] for p in b86), 'new': new, 'fi': C.policy_args('EVICON_FI', a), "
                "'sh': C.policy_args('SHADOW_RECONCILE', a), 'legacy': list(C.LEGACY), 'resolvers': list(C.RESOLVERS)}))")
        r = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        d = json.loads(r.stdout)
        self.assertTrue(d["same"])
        sh, sh1 = d["sh"], d["new"]["SHADOW_RECONCILE_1S"]
        self.assertEqual(sh[sh.index("-release-after") + 1], "10s")
        i = sh1.index("-release-after")
        self.assertEqual(sh1[:i + 1] + sh1[i + 2:], sh[:i + 1] + sh[i + 2:])
        self.assertEqual(sh1[i + 1], "1s")
        self.assertEqual(d["new"]["EVICON_FI_REC"], d["fi"] + ["-reboot-recovery"])
        self.assertEqual(d["new"]["EVICON_FI_REC_OS"], d["fi"] + ["-reboot-recovery", "-fence-anchor", "owner-state"])
        self.assertIn("SHADOW_RECONCILE_1S", d["legacy"])
        self.assertIn("EVICON_FI_REC", d["resolvers"])
        import safety_total
        for p in ("SHADOW_RECONCILE_1S", "EVICON_FI_REC", "EVICON_FI_REC_OS"):
            self.assertNotIn(p, safety_total.BASELINES)
        self.assertEqual(safety_total.kind(cfg("expRB_zephyr_w6"), "NEG_ZEPHYR_F_BOOT", "EVICON_FI_REC_OS"), "control")
        self.assertEqual(safety_total.kind(cfg("expRB_zephyr_w6"), "ZEPHYR_F_BOOT", "EVICON_FI_REC"), "guarded")


if __name__ == "__main__":
    unittest.main()
