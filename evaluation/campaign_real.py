#!/usr/bin/env python3
"""Runtime campaign on a real owner: experiment C, EviCon authorities against MinIO (release 073).

The cell procedure, the seeded schedules (common random numbers), the clients, the fault runner,
the row and the activation gate are those of evaluation/campaign.py, which the 067-072 plans freeze:
this file imports them and never edits them. What differs:

  owner    one S3 owner gateway per device (conformance/reference/s3_owner_gateway.py) on VM-C, in
           front of MinIO on VM-B, a fresh versioned bucket per cell. The gateway advertises the
           contract release 070 MEASURED for its call shape (experiments/owners/minio/), so the
           authority runs on measured evidence only
  faults   as in campaign.py: drop_up / drop_down / delay_up on the authority -> gateway path (the
           per-device fault proxy), crash_after_effect (the gateway exits after MinIO answered 200,
           before it replies; restarted after crash_recovery_s), offline (the gateway -> MinIO path
           drops every byte in both directions). All inside the fault model of the 070 measurement
           ("network loss/delay, client abandon; owner up")
  truth    the bucket's object versions, listed directly from MinIO by this process (never through a
           gateway or a fault proxy). An effect is a version with metadata evicon-kind=effect (HEAD of
           that version); a tombstone (a stop) is not an effect. Duplicate effects: identities with
           more than one effect version; bound violation: the effects of a device add up to more than
           the bound. Time of an effect: the version's LastModified, moved to VM-C's clock with VM-B's
           measured offset. Saved per cell in raw/truth/ (versions.json, truth.json) and as an effect
           log in the format of evicon-plant (raw/E/plant.jsonl: "cmd", "t_ns"), so the frozen
           pairs_report.py computes confirmed fulfilment as for every other campaign
  policies besides the policies of campaign.py, a config may define pseudo-policies in
           real_owner.policies: {"NAIVE": {"base": "M1", "authority": {"retry_guard": false}}} runs the
           legacy M1 authority without the T4 retry guard (the dispatcher re-sends the same identity
           until it gets a receipt: a blind retry)

Config: a campaign.py config (schema_version 2) plus
  "real_owner": {"system": "minio", "s3_url": "http://<VM-B>:9000" (the driver fills it in),
                 "access_key_env": "MINIO_ROOT_USER", "secret_key_env": "MINIO_ROOT_PASSWORD",
                 "contracts": {"<name>": {"mode": "COND"|"PLAIN", "contract_file": "<repo path>"}, ...},
                 "policies": {"<name>": {"base": "<policy>", "authority": {<overrides>}}, ...},
                 "gateway_budget_s": 1.8}
and, in remote mode, remote.vm_b / remote.vm_b_ip (VM-B's clock offset). Credentials: only in this
process's environment (the driver sets them), inherited by the gateways; never in a file or a log.

  python3 evaluation/campaign_real.py --config <derived config> --out <dir>
  python3 evaluation/campaign_real.py --resume <run dir>

evicon_review.py campaign expC_minio brings MinIO up on VM-B (EviCon's compose project only), runs
this script and brings MinIO down again.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "evaluation"))
sys.path.insert(0, str(REPO / "conformance"))
import campaign as C  # noqa: E402  (frozen; imported, never edited)
from campaign import Cell, Host, Stack  # noqa: E402
from evicon_probe.faults import FaultProxy  # noqa: E402
from reference.s3_client import S3  # noqa: E402

RELEASE = C.RELEASE
GATEWAY = REPO / "conformance" / "reference" / "s3_owner_gateway.py"
REAL_KEYS = {"system", "s3_url", "access_key_env", "secret_key_env", "contracts", "policies", "gateway_budget_s"}
LOG = C.RunLog()


# ------------------------------------------------------------------ config
def load_config(path) -> dict:
    """campaign.load_config's checks, plus the real_owner section; pseudo-policies are allowed."""
    cfg = json.loads(Path(path).read_text())
    ro = cfg.get("real_owner")
    if not isinstance(ro, dict):
        raise ValueError("real_owner section required (use evaluation/campaign.py for evicon-device campaigns)")
    unknown = set(ro) - REAL_KEYS
    if unknown:
        raise ValueError(f"unknown real_owner keys {sorted(unknown)}")
    if ro.get("system") != "minio":
        raise ValueError("real_owner.system: only minio (release 073)")
    for k in ("s3_url", "contracts"):
        if not ro.get(k):
            raise ValueError(f"real_owner.{k} required")
    for name in cfg.get("contracts", []):
        spec = ro["contracts"].get(name)
        if not spec or spec.get("mode") not in ("COND", "PLAIN") or not (REPO / str(spec.get("contract_file", ""))).is_file():
            raise ValueError(f"contract {name}: real_owner.contracts.{name} needs mode COND|PLAIN and an existing contract_file")
    pseudo = ro.get("policies", {})
    for name, p in pseudo.items():
        if p.get("base") not in C.RESOLVERS + C.LEGACY:
            raise ValueError(f"pseudo-policy {name}: base {p.get('base')!r} is not a campaign.py policy")
        bad = set(p.get("authority", {})) - {"retry_guard", "max_network_delay", "max_outstanding", "resolve_sweep", "periodic"}
        if bad:
            raise ValueError(f"pseudo-policy {name}: authority overrides {sorted(bad)} not allowed")
    base = {k: v for k, v in cfg.items() if k != "real_owner"}
    base["policies"] = [pseudo[p]["base"] if p in pseudo else p for p in cfg["policies"]]
    tmp = Path(os.environ.get("TMPDIR", "/tmp")) / f"campaign_real_check_{os.getpid()}.json"
    try:  # every other check is campaign.load_config's own (one definition of the format)
        tmp.write_text(json.dumps(base))
        C.load_config(tmp)
    finally:
        tmp.unlink(missing_ok=True)
    if cfg.get("mode") == "remote" and "vm_b_ip" not in cfg.get("remote", {}):
        raise ValueError("remote.vm_b_ip required (VM-B's clock offset)")
    return cfg


def cell_config(cfg: dict, policy: str):
    """(config the authority of this cell runs with, campaign.py policy)."""
    p = cfg["real_owner"].get("policies", {}).get(policy)
    if p is None:
        return cfg, policy
    c = copy.deepcopy(cfg)
    c.setdefault("authority", {}).update(p.get("authority", {}))
    return c, p["base"]


def bucket_name(out: Path, cell: Cell) -> str:
    """S3 bucket names: 3-63 characters, lower case, digits and hyphens."""
    h = hashlib.sha256(f"{out}/{cell.key}".encode()).hexdigest()[:20]
    return f"evc-{h}-ep{cell.episode}"


def credentials(cfg) -> tuple:
    ro = cfg["real_owner"]
    ka, ks = ro.get("access_key_env", "MINIO_ROOT_USER"), ro.get("secret_key_env", "MINIO_ROOT_PASSWORD")
    if not os.environ.get(ka) or not os.environ.get(ks):
        raise RuntimeError(f"MinIO credentials missing: {ka} and {ks} must be set in this process's environment "
                           f"(evicon_review.py campaign sets them; they are never written to a file)")
    return os.environ[ka], os.environ[ks]


def _iso_ns(s: str) -> int:
    """S3 LastModified (2026-10-24T21:03:04.123Z) -> epoch ns."""
    m = re.fullmatch(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:\d\d)?", s.strip())
    if not m:
        raise ValueError(f"not an S3 timestamp: {s!r}")
    head, frac, tz = m.group(1), m.group(2) or "", m.group(3) or "Z"
    base = datetime.fromisoformat(head + ("+00:00" if tz == "Z" else tz)).astimezone(timezone.utc).timestamp()
    return int(round(base)) * 1_000_000_000 + (int(frac.ljust(9, "0")[:9]) if frac else 0)


# ------------------------------------------------------------------ testbed
class RealTestbed(C.Testbed):
    """Authorities on VM-A (as campaign.py); one S3 gateway per device on VM-C, in front of MinIO."""

    def __init__(self, cfg, out: Path, bin_dir: Path):
        super().__init__(cfg, out, bin_dir)
        self.out = out
        self.ro = cfg["real_owner"]
        # the gateways run next to the driver: FaultRunner and run_cell check them through tb.e
        self.e = Host("VM-C", "127.0.0.1")
        self.gw_listen = "127.0.0.1"
        r = cfg.get("remote", {})
        self.b = Host("VM-B", r["vm_b_ip"], r.get("vm_b")) if cfg.get("mode") == "remote" and r.get("vm_b") else None
        self.s3 = S3(self.ro["s3_url"], *credentials(cfg))
        self.bucket = {}

    def all_ports(self):
        n, p = self.cfg["devices"], self.ports
        return ([p["device"] + 2 * d for d in range(n)] + [p["device"] + 2 * d + 1 for d in range(n)],
                [p["authority"] + d for d in range(n)], [p["proxy"] + d for d in range(n)])

    def preflight(self):
        g_ports, a_ports, c_ports = self.all_ports()
        busy = {}
        for h, ports in ((self.e, g_ports + c_ports), (self.a, a_ports)):
            used = h.ports_in_use(ports)
            if used:
                busy[h.name] = {p: h.port_owner(p) for p in used}
        if busy:
            raise RuntimeError(f"ports already in use: {busy}. Stop those programs (a previous EviCon run: its pid "
                               f"files; anything else: its owner) or set config.ports; EviCon never stops other programs")
        if self.a.run(f"test -x {shlex.quote(self.bin_a)}/evicon-authority", check=False).returncode != 0:
            raise RuntimeError(f"{self.a.name}: {self.bin_a}/evicon-authority missing (distribute the binaries first)")
        if self.cfg.get("mode") == "remote" and self.a.run(f"test -r {shlex.quote(self.cost_file)}", check=False).returncode != 0:
            raise RuntimeError(f"VM-A: {self.cost_file} missing (distribute experiments/configs/query_costs.json)")
        r = self.s3.request("GET", "/", timeout=10)  # ListBuckets: reachability and credentials
        if r.status != 200:
            raise RuntimeError(f"MinIO at {self.ro['s3_url']} not usable from VM-C: HTTP {r.status} {r.error} {r.body[:160]!r}")
        info = {"A_clock_offset_s": self.a.clock_offset_s(), "B_clock_offset_s": self.b_offset(),
                "binaries_A": self.a.sha256([f"{self.bin_a}/evicon-authority"]),
                "gateway_sha256": C.sha256_file(GATEWAY), "s3_client_sha256": C.sha256_file(GATEWAY.parent / "s3_client.py"),
                "owner": {"system": self.ro["system"], "s3_url": self.ro["s3_url"], "server": r.headers.get("Server", "?"),
                          "contracts": {k: {"mode": v["mode"], "contract_file": v["contract_file"],
                                            "sha256": C.sha256_file(REPO / v["contract_file"])}
                                        for k, v in self.ro["contracts"].items()},
                          "policies": self.ro.get("policies", {})}}
        return info

    def b_offset(self):
        """VM-B's clock minus VM-C's, in seconds, from an open SSH session: VM-B prints its time when VM-C
        sends a line, so the round trip is one LAN round trip (about a millisecond), not an SSH handshake
        (Host.clock_offset_s takes the midpoint of the whole ssh call, about 0.1-0.2 s too late on the
        testbed). Best of three samples; 0 in local mode. Recorded per cell in raw/truth/."""
        if self.b is None:
            return 0.0
        best = None
        for _ in range(3):
            p = subprocess.Popen(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", self.b.ssh,
                                  'echo ready; read -r x; t=$EPOCHREALTIME; [ -n "$t" ] || t=$(date +%s.%N); echo "$t"'],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            try:
                if p.stdout.readline().strip() != "ready":
                    continue
                t0 = time.time()
                p.stdin.write("\n")
                p.stdin.flush()
                out = p.stdout.readline().strip().replace(",", ".")
                t1 = time.time()
                off = float(out) - (t0 + t1) / 2
                if best is None or t1 - t0 < best[1]:
                    best = (off, t1 - t0)
            except (ValueError, OSError):
                continue
            finally:
                for f in (p.stdin, p.stdout):
                    try:
                        f.close()
                    except OSError:
                        pass
                p.wait(10)
        if best is None:
            raise RuntimeError(f"cannot read VM-B's clock over ssh ({self.b.ssh})")
        self.b_rtt_s = round(best[1], 4)
        return round(best[0], 4)

    def start(self, cell: Cell, wd: str) -> Stack:
        st = Stack(plant_url="")
        try:
            return self._start(cell, wd, st)
        except BaseException:
            # what was started before the failure (gateways, proxies, authorities) never outlives the cell
            self.stop(st, wd)
            raise

    def _start(self, cell: Cell, wd: str, st: Stack) -> Stack:
        cfg, p = self.cfg, self.ports
        spec = self.ro["contracts"][cell.contract]
        acfg, apolicy = cell_config(cfg, cell.policy)
        bucket = bucket_name(self.out, cell)
        self.s3.create_versioned_bucket(bucket)
        self.bucket[wd] = bucket
        self.e.run(f"mkdir -p {shlex.quote(wd)}")
        self.a.run(f"mkdir -p {shlex.quote(wd)}")
        for d in range(cfg["devices"]):
            dev_id, gport, aport = f"pump{d}-1", p["device"] + 2 * d, p["device"] + 2 * d + 1
            argv = [sys.executable, str(GATEWAY), "--mode", spec["mode"], "--device-id", dev_id,
                    "--listen", f"{self.gw_listen}:{gport}", "--admin-listen", f"{self.gw_listen}:{aport}",
                    "--s3-url", self.ro["s3_url"], "--bucket", bucket, "--prefix", dev_id,
                    "--contract-file", str(REPO / spec["contract_file"]), "--budget-s", str(self.ro.get("gateway_budget_s", 1.8)),
                    "--access-key-env", self.ro.get("access_key_env", "MINIO_ROOT_USER"),
                    "--secret-key-env", self.ro.get("secret_key_env", "MINIO_ROOT_PASSWORD"), "--log", f"{wd}/gateway{d}.jsonl"]
            st.device_argv.append(argv)
            st.device_pid.append(self.e.spawn(f"gateway{d}", argv, wd))
            self.ready(self.e, f"http://{self.gw_listen}:{gport}/healthz", f"gateway{d}", st.device_pid[-1], wd)
            proxy = FaultProxy(self.gw_listen, gport, proto="tcp", listen_host=self.listen if cfg.get("mode", "local") == "local" else "0.0.0.0",
                               listen_port=p["proxy"] + d, linger_s=float(cfg.get("faults", {}).get("linger_s", 3.0)))
            proxy.start()
            st.proxies.append(proxy)
            auth_port = p["authority"] + d
            args = C.authority_args(acfg, cell.contract, apolicy, f"{self.bin_a}/evicon-authority", dev_id,
                                    f"http://{self.c_ip}:{p['proxy'] + d}/d/{dev_id}", f"{wd}/authority{d}.db",
                                    f"{self.listen}:{auth_port}", f"{wd}/authority{d}.jsonl", self.cost_file)
            st.auth_pids.append(self.a.spawn(f"authority{d}", args, wd))
            self.ready(self.a, f"http://{self.a.addr}:{auth_port}/healthz", f"authority{d}", st.auth_pids[-1], wd)
            st.authorities.append(f"http://{self.a.addr}:{auth_port}")
            st.device_ids.append(dev_id)
            st.device_admin.append(f"http://{self.gw_listen}:{aport}/d/{dev_id}")
        return st

    def restart_device(self, st: Stack, d: int, wd: str):
        st.device_pid[d] = self.e.spawn(f"gateway{d}", st.device_argv[d], wd)
        self.ready(self.e, f"http://{self.gw_listen}:{self.ports['device'] + 2 * d}/healthz", f"gateway{d}", st.device_pid[d], wd)

    def stop(self, st: Stack, wd: str):
        for pr in st.proxies:
            try:
                pr.clear()
            except Exception:
                pass
            pr.stop()
        self.a.stop_all(wd)
        self.e.stop_all(wd)

    def fetch(self, wd: str, local: Path):
        """raw/E: the gateways' files (VM-C is this host: copied, only gateway*); raw/A: VM-A's directory."""
        if self.cfg.get("mode", "local") == "local":
            return
        dst = local / "raw" / "E"
        dst.mkdir(parents=True, exist_ok=True)
        for f in sorted(Path(wd).glob("gateway*")):
            if f.is_file():
                shutil.copy2(f, dst / f.name)
        self.a.fetch_dir(wd, local / "raw" / "A")


# ------------------------------------------------------------------ truth
def collect_truth(cfg, tb: RealTestbed, st: Stack, logs, wd: str, celldir: Path):
    """(summ, truth, status, problems) in the shapes campaign.build_row reads, from MinIO's object versions."""
    problems = []
    bucket = tb.bucket.get(wd, "")
    raw = celldir / "raw" / "truth"
    raw.mkdir(parents=True, exist_ok=True)
    offset = tb.b_offset()
    try:
        versions = tb.s3.list_versions(bucket)
    except Exception as e:  # noqa: BLE001
        return {}, {}, {}, [f"truth: {e}"]
    want = {cid: lg for lg in logs.values() for cid in lg.ids}
    eff, tomb = {}, {}
    for v in versions:
        if v["delete_marker"]:
            problems.append(f"truth: delete marker on {v['key']} (nothing in experiment C deletes)")
            continue
        h = tb.s3.head(bucket, v["key"], timeout=10, version_id=v["version_id"])
        if h.status != 200:
            problems.append(f"truth: HEAD {v['key']}@{v['version_id']}: HTTP {h.status} {h.error}")
            continue
        v["kind"], v["intent"], v["value"] = h.meta("evicon-kind"), h.meta("evicon-intent"), h.meta("evicon-value")
        v["t_ns"] = _iso_ns(v["last_modified"]) - int(round(offset * 1e9))
        dev, _, cid = v["key"].partition("/")
        v["device"], v["cmd"] = dev, cid
        if v["kind"] not in ("effect", "tombstone"):
            problems.append(f"truth: {v['key']} has evicon-kind {v['kind']!r}")
            continue
        if cid not in want or st.device_ids[want[cid].device] != dev:
            problems.append(f"truth: object {v['key']} matches no identity the clients sent")
            continue
        if v["kind"] == "effect":
            if str(want[cid].value) != v["value"]:
                problems.append(f"truth: {v['key']} carries value {v['value']}, the client sent {want[cid].value}")
            eff.setdefault(cid, []).append(v)
        else:
            tomb.setdefault(cid, []).append(v)
    bound = cfg["workload"]["bound"]
    devices = []
    for d, dev in enumerate(st.device_ids):
        vals = sorted((x["t_ns"], int(x["value"] or 0)) for cid, xs in eff.items() for x in xs if x["device"] == dev)
        total, peak = 0, 0
        for _, val in vals:
            total += val
            peak = max(peak, total)
        devices.append({"device": dev, "effects": len(vals), "total": total, "limit": bound, "limit_exceeded": peak > bound})
    summ = {"source": f"MinIO object versions of bucket {bucket}", "identities_with_duplicate_effects": sum(1 for xs in eff.values() if len(xs) > 1),
            "identities_with_effect_and_tombstone": sum(1 for c in eff if c in tomb), "tombstones": sum(len(x) for x in tomb.values()),
            "effect_versions": sum(len(x) for x in eff.values()), "devices": devices, "b_clock_offset_s": offset,
            "b_clock_rtt_s": getattr(tb, "b_rtt_s", None)}
    if summ["identities_with_effect_and_tombstone"]:
        # two successful conditional creates of one key: impossible for a correct owner; reported, and
        # campaign.build_row counts a STOPPED identity with an effect as false_stopped
        problems.append(f"truth: {summ['identities_with_effect_and_tombstone']} identities have an effect and a tombstone")
    truth = {cid: {"effects": len(eff.get(cid, [])), "first_effect_ns": min((x["t_ns"] for x in eff.get(cid, [])), default=0),
                   "tombstones": len(tomb.get(cid, []))} for cid in want}
    (raw / "versions.json").write_text(json.dumps({"bucket": bucket, "b_clock_offset_s": offset, "versions": versions}, indent=1) + "\n")
    (raw / "truth.json").write_text(json.dumps({"summary": summ, "truth": truth}, indent=1, sort_keys=True) + "\n")
    plant = celldir / "raw" / ("plant.jsonl" if cfg.get("mode", "local") == "local" else "E/plant.jsonl")
    plant.parent.mkdir(parents=True, exist_ok=True)
    with open(plant, "w") as fh:  # the evicon-plant line format, so pairs_report.py reads it unchanged
        for x in sorted((x for xs in eff.values() for x in xs), key=lambda x: x["t_ns"]):
            fh.write(json.dumps({"cmd": x["cmd"], "device": x["device"], "value": int(x["value"] or 0), "t_ns": x["t_ns"],
                                 "source": "minio-object-version", "version_id": x["version_id"]}) + "\n")
    (plant.parent / "TRUTH_README.txt").write_text(
        "plant.jsonl here is not written by evicon-plant: campaign_real.py derived it from the MinIO object versions of\n"
        f"bucket {bucket} (one line per effect version, time = LastModified on VM-C's clock). Raw listing: ../truth/.\n")
    status = {}
    for lg in logs.values():
        for cid in lg.ids:
            if cid in lg.admitted:
                c, o = C.http("GET", f"{st.authorities[lg.device]}/v1/commands/{cid}", timeout=10)
                if c != 200 or not isinstance(o, dict):
                    problems.append(f"authority status {cid}: HTTP {c}")
                    continue
                status[cid] = o.get("status", "")
    return summ, truth, status, problems


# ------------------------------------------------------------------ main
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config")
    ap.add_argument("--out", default="results/runtime")
    ap.add_argument("--resume", help="continue the campaign in this run directory")
    ap.add_argument("--bin-dir", default=str(REPO / "bin"), help="local mode: directory with evicon-* binaries")
    ap.add_argument("--max-cells", type=int, default=0, help="stop after this many new cells (checkpointed)")
    a = ap.parse_args(argv)
    if a.resume:
        out = Path(a.resume).resolve()
        cfg = json.loads((out / "config.json").read_text())
        if a.config and load_config(a.config) != cfg:
            raise SystemExit("--config differs from the campaign being resumed")
    else:
        if not a.config:
            raise SystemExit("--config or --resume required")
        cfg = load_config(a.config)
        out = Path(a.out).resolve() / cfg["name"] / time.strftime("%Y%m%dT%H%M%S")
        out.mkdir(parents=True, exist_ok=False)
        (out / "config.json").write_text(json.dumps(cfg, indent=2) + "\n")
    LOG.attach(out)
    LOG.info(f"campaign {cfg['name']} on {cfg['real_owner']['system']} (EviCon {RELEASE}) -> {out}")
    try:
        return run_campaign(cfg, out, Path(a.bin_dir).resolve(), a.max_cells)
    except Exception as e:
        LOG.error("campaign", f"{type(e).__name__}: {e}", e)
        LOG.info(f"campaign aborted: {type(e).__name__}: {e} (traceback in {out / 'errors.log'})")
        raise


def run_campaign(cfg, out: Path, bin_dir: Path, max_cells: int = 0):
    """campaign.run_campaign with the owner, the truth and the pseudo-policies of experiment C."""
    tb = RealTestbed(cfg, out, bin_dir)
    info = tb.preflight()
    done = set()
    if (out / "done.jsonl").exists():
        done = {json.loads(line)["cell"] for line in (out / "done.jsonl").read_text().splitlines() if line.strip()}
    man = out / "manifest.json"
    manifest = json.loads(man.read_text()) if man.exists() else {
        "schema_version": 2, "release": RELEASE, "scope": "DEV_LOCAL" if cfg.get("mode", "local") == "local" else "TESTBED",
        "runner": "evaluation/campaign_real.py", "owner_system": cfg["real_owner"]["system"],
        "config_sha256": hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest(), "driver_host": socket.gethostname(),
        "python": sys.version.split()[0], "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "sessions": []}
    manifest["sessions"].append({"started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "preflight": info})
    man.write_text(json.dumps(manifest, indent=2) + "\n")
    csv_path = out / "runtime_episodes.csv"
    if not csv_path.exists():
        with open(csv_path, "w", newline="") as fh:
            csv.DictWriter(fh, fieldnames=C.ROW_FIELDS).writeheader()
    new_cells = 0
    for ep in range(cfg.get("episodes", 1)):
        arrivals, windows = C.schedule(cfg, ep)
        (out / f"schedule_ep{ep}.json").write_text(json.dumps({"arrivals": [asdict(x) for x in arrivals], "faults": [asdict(x) for x in windows]}))
        for contract in cfg["contracts"]:
            for policy in cfg["policies"]:
                cell = Cell(ep, contract, policy)
                if cell.key in done:
                    continue
                if max_cells and new_cells >= max_cells:
                    LOG.info(f"stopping after {new_cells} new cells (checkpointed); continue with --resume {out}")
                    return 0
                celldir = out / "cells" / f"ep{ep}" / contract / policy
                if celldir.exists():
                    shutil.rmtree(celldir)  # an interrupted, unrecorded attempt of this cell
                celldir.mkdir(parents=True)
                wd = str((tb.state_root / f"ep{ep}-{contract}-{policy}") if cfg.get("mode") == "remote" else (celldir / "raw"))
                problems, st, logs, t_end, flog = [], None, {}, time.time(), []
                summ, truth, status = {}, {}, {}
                ccfg, _ = cell_config(cfg, policy)
                try:
                    st = tb.start(cell, wd)
                    logs, t0, t_end, ferr = C.run_cell(ccfg, tb, st, arrivals, windows, wd, celldir)
                    problems += [f"fault: {e}" for e in ferr][:5]
                    for i, pid in enumerate(st.auth_pids):
                        if not tb.a.alive(pid):
                            problems.append(f"authority{i} died")
                    for i, pid in enumerate(st.device_pid):
                        if not tb.e.alive(pid):
                            problems.append(f"gateway{i} not running at the end")
                    summ, truth, status, p2 = collect_truth(cfg, tb, st, logs, wd, celldir)
                    problems += p2[:10]
                except Exception as e:
                    problems.append(f"{type(e).__name__}: {e}")
                    LOG.error(cell.key, f"{type(e).__name__}: {e}", e)
                finally:
                    if st is not None:
                        tb.stop(st, wd)
                    try:
                        tb.fetch(wd, celldir)
                    except Exception as e:
                        problems.append(f"fetch: {e}")
                        LOG.error(cell.key, f"fetch: {e}", e)
                raw = celldir / "raw" if cfg.get("mode", "local") == "local" else celldir / "raw" / "A"
                traces = sorted(raw.glob("authority*.jsonl"))
                if (celldir / "faults.jsonl").exists():
                    flog = [json.loads(line) for line in (celldir / "faults.jsonl").read_text().splitlines() if line.strip()]
                (celldir / "clients.jsonl").write_text("".join(json.dumps(asdict(lg)) + "\n" for lg in logs.values()))
                row = C.build_row(cfg, cell, st or Stack(plant_url=""), logs, t_end, summ, truth, status, traces, problems, flog, celldir)
                (celldir / "row.json").write_text(json.dumps(dict(row, problems=sorted(set(problems)), bucket=tb.bucket.get(wd, "")), indent=2) + "\n")
                if problems:
                    (celldir / "problems.txt").write_text("\n".join(sorted(set(problems))) + "\n")
                    LOG.error(cell.key, "INVALID: " + " | ".join(sorted(set(problems))))
                with open(csv_path, "a", newline="") as fh:
                    csv.DictWriter(fh, fieldnames=C.ROW_FIELDS).writerow(row)
                with open(out / "done.jsonl", "a") as fh:
                    fh.write(json.dumps({"cell": cell.key, "status": row["cell_status"], "at": time.time()}) + "\n")
                new_cells += 1
                LOG.info(f"[{cell.key}] {row['cell_status'][:160]} " + json.dumps({k: row[k] for k in (
                    "offered", "admitted", "clean_fulfilled", "unresolved_at_end", "false_stopped", "duplicate_effects",
                    "bound_violations", "decisions_acting", "stops", "fences", "queries")}))
    with open(csv_path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    gate = C.activation_gate(cfg, rows)
    manifest = json.loads(man.read_text())
    manifest.update({"finished": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "cells": len(rows),
                     "invalid_cells": sum(1 for r in rows if not r["cell_status"].startswith("VALID")), "activation_gate": gate})
    man.write_text(json.dumps(manifest, indent=2) + "\n")
    if gate is not None:
        (out / "activation.json").write_text(json.dumps(gate, indent=2) + "\n")
        LOG.info(f"activation gate: {'PASS' if gate['pass'] else 'FAIL'} ({gate['rule']}); failing={gate['failing'][:6]} invalid={gate['invalid_cells'][:6]}")
        if not gate["pass"]:
            LOG.error("activation-gate", json.dumps(gate))
    LOG.info(f"wrote {csv_path}; log {out / 'campaign.log'}" + (f"; errors {out / 'errors.log'}" if (out / "errors.log").exists() else ""))
    return 0 if gate is None or gate["pass"] else 4


if __name__ == "__main__":
    sys.exit(main())
