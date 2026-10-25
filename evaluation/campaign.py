#!/usr/bin/env python3
"""Runtime campaign (C3) for EviCon, release 065 (format of release 062).

EviCon authorities (VM-A) drive evicon-device owners (VM-E) whose evidence
contracts range over K0..K4/CUTOFF, through per-device fault proxies that run
inside this process (VM-C); truth comes from evicon-plant (VM-E).

For every (episode, contract, policy) cell the driver
  1. starts a fresh plant, devices and authorities (pid files, one state
     directory per cell, never shared or reused),
  2. replays the same seeded workload and the same non-overlapping fault
     schedule (common random numbers across policies and contracts),
  3. reads truth and authority outcomes over HTTP, checking every status,
  4. stops its own processes (pid files only), fetches the complete raw cell
     directory (plant journal, device journals/logs, authority DBs/traces/logs),
  5. appends one row to runtime_episodes.csv and checkpoints the cell.
A cell whose fault injection failed, whose truth or authority answers are
missing, or whose processes died is written with cell_status=INVALID:<why>;
it is never turned into a "0 violations" row. --resume continues a campaign.

Policies (identical contract, APIs, retry guard and reissue rule for all
EVICON-family policies; only when/what to resolve differs):
  EVICON          ski-rental threshold, certificates, candidate + sweep triggers
  RES_IMMEDIATE   stop/fence immediately (no query first)
  RES_QUERY_FIRST query first, then stop/fence immediately
  RES_FIXED       query first, stop/fence after authority.fixed_threshold
  RES_PERIODIC    no candidate trigger; every authority.periodic, resolve all aged
  M1, M0          legacy runtime selectors (no stop/fence)

  python3 evaluation/campaign.py --config evaluation/campaigns/local_smoke.json --out results/runtime
  python3 evaluation/campaign.py --resume results/runtime/<name>/<stamp>

Local mode runs every role on 127.0.0.1 (development; numbers are not
measurements). Remote mode is the 5-VM testbed (driver on VM-C, SSH as root).
"sim_twin" (optional) names the simulator config matched to the campaign's
regime (experiment A sim-to-real, evaluation/sim_to_real.py).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import re
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RELEASE = (REPO / "VERSION").read_text().strip() if (REPO / "VERSION").exists() else "unknown"
sys.path.insert(0, str(REPO / "conformance"))
from evicon_probe.faults import DOWN, UP, FaultProxy  # noqa: E402

CONFIG_KEYS = {"schema_version", "name", "regime", "mode", "seed", "episodes", "devices", "contracts", "policies", "duration_s",
               "tail_s", "workload", "faults", "authority", "activation_gate", "remote", "ports", "notes", "sim_twin",
               "preregistration"}
RESOLVERS = ("EVICON", "RES_IMMEDIATE", "RES_QUERY_FIRST", "RES_FIXED", "RES_PERIODIC")
LEGACY = ("M0", "M1")
FAULT_KINDS = ("drop_up", "drop_down", "delay_up", "offline", "crash_after_effect")
DEFAULT_PORTS = {"plant": 18090, "device": 18100, "authority": 19000, "proxy": 28200}


# ------------------------------------------------------------------ helpers
def http(method, url, body=None, timeout=5.0):
    """Returns (status or None, parsed body or text/error)."""
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", "Connection": "close"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            try:
                return r.status, (json.loads(raw) if raw else None)
            except ValueError:
                return r.status, raw.decode(errors="replace")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw) if raw else None
        except ValueError:
            return e.code, raw.decode(errors="replace")
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as e:
        return None, str(e)


def wait_http(url, timeout=20.0):
    t0 = time.monotonic()
    last = None
    while time.monotonic() - t0 < timeout:
        st, out = http("GET", url, timeout=2)
        if st == 200:
            return
        last = (st, out)
        time.sleep(0.2)
    raise RuntimeError(f"not ready: {url} ({last})")


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def load_config(path):
    cfg = json.loads(Path(path).read_text())
    unknown = set(cfg) - CONFIG_KEYS
    if unknown:
        raise ValueError(f"unknown config keys {sorted(unknown)}")
    if cfg.get("schema_version") != 2:
        raise ValueError("schema_version must be 2 (release 062 campaign format)")
    if cfg.get("mode", "local") not in ("local", "remote"):
        raise ValueError("mode must be local or remote")
    w = cfg["workload"]
    for k in ("rate_per_s", "value_min", "value_max", "bound", "deadline_s", "retry_s"):
        if k not in w:
            raise ValueError(f"workload.{k} required")
    if w["value_min"] < 1 or w["value_max"] < w["value_min"] or w["bound"] < w["value_max"]:
        raise ValueError("workload values/bound inconsistent")
    for p in cfg["policies"]:
        if p not in RESOLVERS + LEGACY:
            raise ValueError(f"unknown policy {p!r} (want one of {RESOLVERS + LEGACY})")
    mix = cfg.get("faults", {}).get("mix", {})
    for k in mix:
        if k not in FAULT_KINDS:
            raise ValueError(f"unknown fault kind {k!r}")
    if cfg.get("mode") == "remote":
        for k in ("vm_a", "vm_c_ip", "vm_e", "vm_a_ip", "vm_e_ip"):
            if k not in cfg.get("remote", {}):
                raise ValueError(f"remote.{k} required")
    return cfg


# ------------------------------------------------------------------ schedules (CRN)
@dataclass
class Arrival:
    t: float
    device: int
    seq: int
    value: int

    @property
    def cid(self):
        return f"d{self.device}-c{self.seq}"


@dataclass
class FaultWindow:
    t: float
    device: int
    kind: str
    dur: float


def schedule(cfg, episode):
    """Seeded arrivals and fault windows. Windows of one device never overlap
    (min_gap_s apart), so one fault runner per device applies them in order."""
    w, f = cfg["workload"], cfg.get("faults", {})
    arrivals, windows = [], []
    for d in range(cfg["devices"]):
        rng = random.Random(f"{cfg['seed']}/{episode}/arrivals/{d}")
        t, seq = 0.0, 0
        while True:
            t += rng.expovariate(w["rate_per_s"])
            if t >= cfg["duration_s"]:
                break
            seq += 1
            arrivals.append(Arrival(t, d, seq, rng.randint(w["value_min"], w["value_max"])))
        frng = random.Random(f"{cfg['seed']}/{episode}/faults/{d}")
        rate = f.get("windows_per_min", 0) / 60.0
        mix = f.get("mix", {"drop_up": 0.4, "drop_down": 0.4, "delay_up": 0.2})
        kinds, weights = list(mix), list(mix.values())
        gap = float(f.get("min_gap_s", 1.0))
        t, busy_until = 0.0, 0.0
        while rate > 0:
            t += frng.expovariate(rate)
            if t >= cfg["duration_s"]:
                break
            kind = frng.choices(kinds, weights)[0]
            lo, hi = f.get("offline_s" if kind == "offline" else "window_s", [5.0, 20.0] if kind == "offline" else [1.0, 4.0])
            dur = frng.uniform(lo, hi)
            start = max(t, busy_until + gap)
            if start >= cfg["duration_s"]:
                break
            windows.append(FaultWindow(round(start, 3), d, kind, round(dur, 3)))
            busy_until = start + dur
            t = start
    arrivals.sort(key=lambda a: (a.t, a.device))
    windows.sort(key=lambda x: (x.t, x.device))
    return arrivals, windows


# ------------------------------------------------------------------ hosts
class Host:
    """A role host. ssh=None runs locally; otherwise commands go over SSH
    (BatchMode, root). Processes are tracked by pid files only."""

    def __init__(self, name, addr, ssh=None):
        self.name, self.addr, self.ssh = name, addr, ssh

    def run(self, cmd, check=True, timeout=120):
        argv = ["bash", "-c", cmd] if self.ssh is None else \
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", self.ssh, cmd]
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        if check and p.returncode != 0:
            raise RuntimeError(f"{self.name}: {cmd[:160]} -> rc={p.returncode}: {p.stderr.strip()[:400]}")
        return p

    def spawn(self, name, argv, workdir):
        q = " ".join(shlex.quote(str(x)) for x in argv)
        w = shlex.quote(str(workdir))
        self.run(f"mkdir -p {w} && cd {w} && (nohup {q} > {shlex.quote(name)}.log 2>&1 & echo $! > {shlex.quote(name)}.pid)")
        return self.pid(name, workdir)

    def pid(self, name, workdir):
        out = self.run(f"cat {shlex.quote(str(workdir))}/{shlex.quote(name)}.pid", check=False).stdout.strip()
        return int(out) if out.isdigit() else None

    def alive(self, pid):
        """Running and not a zombie (an exited child that nobody reaped yet)."""
        if pid is None:
            return False
        return self.run(f's=$(ps -o stat= -p {int(pid)} 2>/dev/null); [ -n "$s" ] && [ "${{s#Z}}" = "$s" ]',
                        check=False).returncode == 0

    def stop_all(self, workdir, grace=5):
        """SIGTERM every process with a pid file in workdir, then SIGKILL, and removes the pid
        files. A pid counts only while that process still runs in workdir (spawn() starts
        every process there): a pid file left by an interrupted run (power loss, reboot)
        never makes EviCon signal another program that got the same pid (fix of 15/10)."""
        w = shlex.quote(str(workdir))
        self.run(f"d={w}; [ -d \"$d\" ] || exit 0; real=$(cd \"$d\" && pwd -P) || exit 0; pids=''; "
                 f"for f in \"$d\"/*.pid; do [ -f \"$f\" ] || continue; p=$(cat \"$f\" 2>/dev/null); "
                 f"case \"$p\" in ''|*[!0-9]*) continue;; esac; "
                 f"[ \"$(readlink /proc/$p/cwd 2>/dev/null)\" = \"$real\" ] && pids=\"$pids $p\"; done; "
                 f"[ -n \"$pids\" ] && kill -TERM $pids 2>/dev/null; "
                 f"for i in $(seq {grace * 10}); do alive=0; for p in $pids; do kill -0 $p 2>/dev/null && alive=1; done; "
                 f"[ $alive = 0 ] && break; sleep 0.1; done; "
                 f"for p in $pids; do [ \"$(readlink /proc/$p/cwd 2>/dev/null)\" = \"$real\" ] && kill -KILL $p 2>/dev/null; done; "
                 f"rm -f \"$d\"/*.pid; true", check=False, timeout=60)

    def stop_leftovers(self, state_dir):
        """Stops what an interrupted run (power loss, kill -9 of the driver) left running under
        state_dir, with the rule of stop_all (pid file, and the process still runs in that
        directory). The driver lock (one evicon_review.py at a time) makes sure no other run is
        using them. Returns one 'pid directory' line per stopped process."""
        sd = shlex.quote(str(state_dir))
        p = self.run(f"[ -d {sd} ] || exit 0; find {sd} -maxdepth 6 -name '*.pid' -printf '%h\\n' 2>/dev/null | sort -u | "
                     f"while read -r d; do real=$(cd \"$d\" && pwd -P) || continue; for f in \"$d\"/*.pid; do "
                     f"q=$(cat \"$f\" 2>/dev/null); case \"$q\" in ''|*[!0-9]*) continue;; esac; "
                     f"[ \"$(readlink /proc/$q/cwd 2>/dev/null)\" = \"$real\" ] && echo \"$q $d\"; done; done", check=False, timeout=60)
        found = [line for line in p.stdout.splitlines() if line.strip()]
        for d in sorted({line.split(" ", 1)[1] for line in found}):
            self.stop_all(d)
        return found

    def port_owner(self, port):
        """Which program listens on a TCP port (ss -ltnp; root shows the process)."""
        out = self.run(f"ss -ltnpH 'sport = :{int(port)}' 2>/dev/null", check=False).stdout.strip()
        if not out:
            return "unknown (no listener seen, or ss is missing)"
        users = [tok for line in out.splitlines() for tok in line.split() if tok.startswith("users:")]
        return " ".join(users) if users else out.splitlines()[0][:200]

    def ports_in_use(self, ports):
        out = self.run("ss -ltnH 2>/dev/null || netstat -ltn 2>/dev/null", check=False).stdout
        used = set()
        for line in out.splitlines():
            for tok in line.split():
                if ":" in tok:
                    tail = tok.rsplit(":", 1)[1]
                    if tail.isdigit():
                        used.add(int(tail))
        return sorted(set(ports) & used)

    def fetch_dir(self, remote_dir, local_dir):
        local_dir = Path(local_dir)
        local_dir.mkdir(parents=True, exist_ok=True)
        if self.ssh is None:
            if Path(remote_dir).resolve() != local_dir.resolve():
                shutil.copytree(remote_dir, local_dir, dirs_exist_ok=True)
            return
        with subprocess.Popen(["ssh", "-o", "BatchMode=yes", self.ssh, f"tar czf - -C {shlex.quote(str(remote_dir))} ."],
                              stdout=subprocess.PIPE) as src:
            subprocess.run(["tar", "xzf", "-", "-C", str(local_dir)], stdin=src.stdout, check=True, timeout=600)
            if src.wait(timeout=600) != 0:
                raise RuntimeError(f"{self.name}: fetch of {remote_dir} failed")

    def sha256(self, paths):
        out = self.run("sha256sum " + " ".join(shlex.quote(str(p)) for p in paths), check=False).stdout
        return {Path(line.split()[1]).name: line.split()[0] for line in out.splitlines() if len(line.split()) == 2}

    def clock_offset_s(self):
        """Wall-clock offset of this host relative to the driver (rough: half RTT error)."""
        t0 = time.time()
        out = self.run("date +%s.%N", check=False).stdout.strip()
        t1 = time.time()
        try:
            return round(float(out) - (t0 + t1) / 2, 4)
        except ValueError:
            return None


# ------------------------------------------------------------------ authority arguments
def policy_args(policy, a):
    sweep = a.get("resolve_sweep", "1s")
    if policy == "M0" or policy == "M1":
        return ["-policy", policy]
    base = ["-policy", "EVICON", "-ambiguity-after", a.get("ambiguity_after", "1s"),
            "-hold-cost", str(a.get("hold_cost", 1.0)), "-stop-cost", str(a.get("stop_cost", 1.0)),
            "-fence-cost", str(a.get("fence_cost", 5.0)), "-resolve-sweep", sweep]
    return base + {
        "EVICON": ["-resolve-mode", a.get("resolve_mode", "SKI_RENTAL"), "-resolve-query-first=" + str(a.get("query_first", True)).lower(),
                   "-resolve-trigger", "BLOCK_AND_SWEEP"],
        "RES_IMMEDIATE": ["-resolve-mode", "IMMEDIATE", "-resolve-query-first=false", "-resolve-trigger", "BLOCK_AND_SWEEP"],
        "RES_QUERY_FIRST": ["-resolve-mode", "IMMEDIATE", "-resolve-query-first=true", "-resolve-trigger", "BLOCK_AND_SWEEP"],
        "RES_FIXED": ["-resolve-mode", "FIXED", "-resolve-fixed", a.get("fixed_threshold", "5s"), "-resolve-query-first=true",
                      "-resolve-trigger", "BLOCK_AND_SWEEP"],
        "RES_PERIODIC": ["-resolve-mode", "IMMEDIATE", "-resolve-query-first=false", "-resolve-trigger", "SWEEP_ONLY",
                         "-resolve-sweep", a.get("periodic", "10s")],
    }[policy]


def authority_args(cfg, contract, policy, exe, dev_id, endpoint_url, db, listen, trace, cost_file):
    """Identical authority configuration for local and testbed runs."""
    a, w = cfg.get("authority", {}), cfg["workload"]
    args = [exe, "-profile", "DEVICE", "-domain", dev_id,
            "-component-min", "0", "-component-max", str(w["bound"]), "-anchor", "0",
            "-endpoint-url", endpoint_url, "-db", db, "-listen", listen, "-trace", trace,
            "-run-id", f"{cfg['name']}-{contract}-{policy}", "-dispatch-poll", a.get("dispatch_poll", "100ms"),
            "-probe-budget", str(a.get("probe_budget", 4)), "-max-outstanding", str(a.get("max_outstanding", 6)),
            "-query-cost-file", cost_file]
    args += policy_args(policy, a)
    if a.get("retry_guard", True):
        args += ["-retry-guard", "-max-network-delay", a.get("max_network_delay", "-1ns")]
    return args


# ------------------------------------------------------------------ one cell
@dataclass
class Cell:
    episode: int
    contract: str
    policy: str

    @property
    def key(self):
        return f"ep{self.episode}/{self.contract}/{self.policy}"


@dataclass
class Stack:
    plant_url: str
    authorities: list = field(default_factory=list)
    device_ids: list = field(default_factory=list)
    device_admin: list = field(default_factory=list)
    device_argv: list = field(default_factory=list)
    device_pid: list = field(default_factory=list)
    proxies: list = field(default_factory=list)
    auth_pids: list = field(default_factory=list)
    plant_pid: int = None


class Testbed:
    """Role hosts and paths for local or remote mode."""

    def __init__(self, cfg, out: Path, bin_dir: Path):
        self.cfg = cfg
        self.ports = dict(DEFAULT_PORTS, **cfg.get("ports", {}))
        if cfg.get("mode", "local") == "remote":
            r = cfg["remote"]
            self.e = Host("VM-E", r["vm_e_ip"], r["vm_e"])
            self.a = Host("VM-A", r["vm_a_ip"], r["vm_a"])
            self.c_ip = r["vm_c_ip"]
            self.bin_e = self.bin_a = r.get("bin_dir", "/root/evicon/bin")
            self.cost_file = r.get("cost_file", "/root/evicon/experiments/configs/query_costs.json")
            self.state_root = Path(r.get("state_dir", "/var/lib/evicon/campaigns")) / out.parent.name / out.name
            self.sock_dir = "/run/evicon"
            self.listen = "0.0.0.0"
        else:
            self.e = Host("local-E", "127.0.0.1")
            self.a = Host("local-A", "127.0.0.1")
            self.c_ip = "127.0.0.1"
            self.bin_e = self.bin_a = str(bin_dir)
            self.cost_file = str(REPO / "experiments" / "configs" / "query_costs.json")
            self.state_root = out / "state"
            self.sock_dir = str(Path(os.environ.get("TMPDIR", "/tmp")))
            self.listen = "127.0.0.1"

    def all_ports(self):
        n, p = self.cfg["devices"], self.ports
        return ([p["plant"]] + [p["device"] + 2 * d for d in range(n)] + [p["device"] + 2 * d + 1 for d in range(n)],
                [p["authority"] + d for d in range(n)], [p["proxy"] + d for d in range(n)])

    def preflight(self):
        e_ports, a_ports, c_ports = self.all_ports()
        hc = Host("VM-C", "127.0.0.1")
        busy = {}
        for h, ports in ((self.e, e_ports), (self.a, a_ports), (hc, c_ports)):
            used = h.ports_in_use(ports)
            if used:
                busy[h.name] = {p: h.port_owner(p) for p in used}
        if busy:
            raise RuntimeError(f"ports already in use: {busy}. Stop those programs (a previous EviCon run: its pid "
                               f"files; anything else: its owner) or set config.ports; EviCon never stops other programs")
        info = {"E_clock_offset_s": self.e.clock_offset_s(), "A_clock_offset_s": self.a.clock_offset_s()}
        for h, bdir, names in ((self.e, self.bin_e, ("evicon-plant", "evicon-device")), (self.a, self.bin_a, ("evicon-authority",))):
            for b in names:
                if h.run(f"test -x {shlex.quote(bdir)}/{b}", check=False).returncode != 0:
                    raise RuntimeError(f"{h.name}: {bdir}/{b} missing (distribute the binaries first)")
        if self.cfg.get("mode") == "remote" and self.a.run(f"test -r {shlex.quote(self.cost_file)}", check=False).returncode != 0:
            raise RuntimeError(f"VM-A: {self.cost_file} missing (distribute experiments/configs/query_costs.json)")
        info["binaries_E"] = self.e.sha256([f"{self.bin_e}/evicon-plant", f"{self.bin_e}/evicon-device"])
        info["binaries_A"] = self.a.sha256([f"{self.bin_a}/evicon-authority"])
        return info

    def start(self, cell: Cell, wd: str) -> Stack:
        cfg, p = self.cfg, self.ports
        tag = hashlib.sha1(wd.encode()).hexdigest()[:10]
        sock = f"{self.sock_dir}/evc-{tag}.sock"
        e_ip = self.e.addr
        plant_url = f"http://{e_ip}:{p['plant']}"
        st = Stack(plant_url=plant_url)
        self.e.run(f"mkdir -p {shlex.quote(self.sock_dir)} {shlex.quote(wd)}")
        st.plant_pid = self.e.spawn("plant", [f"{self.bin_e}/evicon-plant", "-log", f"{wd}/plant.jsonl", "-http", f"{self.listen}:{p['plant']}",
                                              "-unix", sock] + (["-fsync=false"] if cfg.get("mode", "local") == "local" else []), wd)
        self.ready(self.e, plant_url + "/healthz", "plant", st.plant_pid, wd)
        for d in range(cfg["devices"]):
            dev_id, dport, aport = f"pump{d}-1", p["device"] + 2 * d, p["device"] + 2 * d + 1
            argv = [f"{self.bin_e}/evicon-device", "-count", "1", "-id-prefix", f"pump{d}-", "-contract", cell.contract,
                    "-listen", f"{self.listen}:{dport}", "-admin-listen", f"{self.listen}:{aport}", "-state-dir", f"{wd}/dev{d}",
                    "-plant", f"unix:{sock}"] + [str(x) for x in cfg.get("faults", {}).get("device_args", [])]
            st.device_argv.append(argv)
            st.device_pid.append(self.e.spawn(f"device{d}", argv, wd))
            self.ready(self.e, f"http://{e_ip}:{dport}/healthz", f"device{d}", st.device_pid[-1], wd)
            proxy = FaultProxy(e_ip, dport, proto="tcp", listen_host=self.listen if cfg.get("mode", "local") == "local" else "0.0.0.0",
                               listen_port=p["proxy"] + d, linger_s=float(cfg.get("faults", {}).get("linger_s", 3.0)))
            proxy.start()
            st.proxies.append(proxy)
            auth_port = p["authority"] + d
            args = authority_args(cfg, cell.contract, cell.policy, f"{self.bin_a}/evicon-authority", dev_id,
                                  f"http://{self.c_ip}:{p['proxy'] + d}/d/{dev_id}", f"{wd}/authority{d}.db",
                                  f"{self.listen}:{auth_port}", f"{wd}/authority{d}.jsonl", self.cost_file)
            st.auth_pids.append(self.a.spawn(f"authority{d}", args, wd))
            self.ready(self.a, f"http://{self.a.addr}:{auth_port}/healthz", f"authority{d}", st.auth_pids[-1], wd)
            code, out = http("POST", f"{plant_url}/v1/limits?device={dev_id}&limit={cfg['workload']['bound']}")
            if code != 200:
                raise RuntimeError(f"plant limit for {dev_id}: {code} {out}")
            st.authorities.append(f"http://{self.a.addr}:{auth_port}")
            st.device_ids.append(dev_id)
            st.device_admin.append(f"http://{e_ip}:{aport}/d/{dev_id}")
        return st

    def restart_device(self, st: Stack, d: int, wd: str):
        st.device_pid[d] = self.e.spawn(f"device{d}", st.device_argv[d], wd)
        self.ready(self.e, f"http://{self.e.addr}:{self.ports['device'] + 2 * d}/healthz", f"device{d}", st.device_pid[d], wd)

    @staticmethod
    def ready(host, url, name, pid, wd, timeout=20.0):
        """Waits until the process *just started* answers /healthz.

        The answer must name the expected role (plant, device, authority,
        endpoint), this release and the started pid. An answer from any other
        program bound to the port (for example a service of an older deployment)
        fails at once instead of being measured (release 064). Fails fast, too,
        when the process exits; errors carry its log tail and the port's owner."""
        role = re.sub(r"\d+$", "", name)
        port = urllib.parse.urlsplit(url).port
        t0, last, next_check = time.monotonic(), None, time.monotonic() + 1.0
        foreign = None
        while time.monotonic() - t0 < timeout:
            code, out = http("GET", url, timeout=2)
            if code == 200:
                ident = out if isinstance(out, dict) else {}
                same_program = ident.get("role") == role and str(ident.get("release")) == RELEASE
                if same_program and (pid is None or ident.get("pid") == pid):
                    return
                foreign = ident or str(out)[:200]
                if not same_program or not host.alive(pid):
                    break  # another program answers: never wait for it to go away
            last = (code, out)
            if time.monotonic() >= next_check:
                next_check = time.monotonic() + 1.0
                if not host.alive(pid):
                    break
            time.sleep(0.2)
        state = "running" if host.alive(pid) else "exited"
        tail = host.run(f"tail -n 15 {shlex.quote(str(wd))}/{shlex.quote(name)}.log 2>/dev/null", check=False).stdout.strip()
        owner = host.port_owner(port) if port else "?"
        if foreign is not None:
            raise RuntimeError(f"{name}: port {port} on {host.name} is answered by another program, not by the {role} just "
                               f"started (pid {pid}, {state}). Answer: {json.dumps(foreign)[:300]}; port owner: {owner}. "
                               f"EviCon never stops other programs: stop it, or free port {port}, then re-run. "
                               f"{wd}/{name}.log: {tail[-400:] or '(empty)'}")
        raise RuntimeError(f"{name} not ready at {url} on {host.name} (pid {pid}, {state}); last answer {last}; "
                           f"port owner: {owner}; {wd}/{name}.log: {tail[-800:] or '(empty)'}")

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
        if self.cfg.get("mode", "local") == "local":
            return
        self.e.fetch_dir(wd, local / "raw" / "E")
        self.a.fetch_dir(wd, local / "raw" / "A")


@dataclass
class CmdLog:
    cid: str
    device: int
    arrival: float
    value: int
    decision: str = ""
    attempts: int = 0
    last_reason: str = ""
    ids: list = field(default_factory=list)
    seqs: dict = field(default_factory=dict)
    admitted: dict = field(default_factory=dict)       # id -> wall time
    final_status: dict = field(default_factory=dict)  # id -> last status seen by the client
    terminal_seen: dict = field(default_factory=dict)


class FaultRunner(threading.Thread):
    """Applies one device's windows in order and logs what actually happened."""

    def __init__(self, tb: Testbed, st: Stack, d: int, windows, t0, wd, cfg, log):
        super().__init__(daemon=True)
        self.tb, self.st, self.d, self.windows, self.t0, self.wd, self.cfg, self.log = tb, st, d, windows, t0, wd, cfg, log
        self.errors = []
        self.stop_flag = threading.Event()

    def run(self):
        f = self.cfg.get("faults", {})
        for w in self.windows:
            delay = self.t0 + w.t - time.time()
            if delay > 0 and self.stop_flag.wait(delay):
                return
            rec = {"device": self.d, "kind": w.kind, "planned_start": self.t0 + w.t, "planned_dur": w.dur,
                   "actual_start": time.time(), "ok": True, "detail": ""}
            try:
                self._apply(w, f, rec)
            except Exception as e:  # recorded; the cell becomes INVALID
                rec["ok"], rec["detail"] = False, f"{type(e).__name__}: {e}"
                self.errors.append(rec["detail"])
            rec["actual_end"] = time.time()
            self.log(rec)

    def _apply(self, w, f, rec):
        px, adm = self.st.proxies[self.d], self.st.device_admin[self.d]
        if not self.tb.e.alive(self.st.device_pid[self.d]):
            raise RuntimeError("device process not running before the window")
        if w.kind == "offline":
            for v in ("false", "true"):
                code, out = http("POST", f"{adm}/_test/online?v={v}")
                if code != 200:
                    raise RuntimeError(f"online={v}: {code} {out}")
                if v == "false":
                    self.stop_flag.wait(w.dur)
            return
        if w.kind == "crash_after_effect":
            code, out = http("POST", f"{adm}/_test/crash?at=AFTER_EFFECT_BEFORE_RECEIPT")
            if code != 200:
                raise RuntimeError(f"arm crash: {code} {out}")
            end = time.time() + w.dur
            pid = self.st.device_pid[self.d]
            while time.time() < end and self.tb.e.alive(pid):
                self.stop_flag.wait(0.2)
            if self.tb.e.alive(pid):
                code, out = http("POST", f"{adm}/_test/crash?at=NONE")
                if code == 200:
                    rec["detail"] = "no command reached the device in the window: not crashed (disarmed)"
                    return
                # it may have crashed between the last liveness check and the disarm
                time.sleep(0.5)
                if self.tb.e.alive(pid):
                    raise RuntimeError(f"disarm crash: {code} {out}")
                rec["detail"] = "crashed at the end of the window"
            rec["crashed_at"] = time.time()
            self.stop_flag.wait(float(f.get("crash_recovery_s", 2.0)))
            self.tb.restart_device(self.st, self.d, self.wd)
            rec["restarted_at"] = time.time()
            return
        if w.kind == "drop_up":
            px.drop(UP)
        elif w.kind == "drop_down":
            px.drop(DOWN)
        elif w.kind == "delay_up":
            px.delay(UP, int(f.get("delay_ms", 4000)))
        self.stop_flag.wait(w.dur)
        px.clear()


def run_cell(cfg, tb: Testbed, st: Stack, arrivals, windows, wd, celldir: Path):
    w = cfg["workload"]
    t0 = time.time() + 0.5
    logs = {a.cid: CmdLog(a.cid, a.device, t0 + a.t, a.value) for a in arrivals}
    stop_flag = threading.Event()
    reissue = bool(w.get("reissue_after_stopped", True))
    flog = open(celldir / "faults.jsonl", "w")
    flock = threading.Lock()

    def log_fault(rec):
        with flock:
            flog.write(json.dumps(rec) + "\n")
            flog.flush()

    # One submitter per device (an ordered device command queue): sequences are
    # assigned at submission from a per-device counter, so every new identity
    # is above every admitted one (the authority rejects non-monotonic
    # sequences). The head request is retried until admitted, rejected or past
    # its deadline; admitted identities are followed by watcher threads. A
    # STOPPED identity whose intent is still wanted re-enters the queue at the
    # front with a new identity (stop/fence-then-retry).
    queues = [[] for _ in range(cfg["devices"])]
    conds = [threading.Condition() for _ in range(cfg["devices"])]
    counters = [0] * cfg["devices"]

    def enqueue(d, job, front=False):
        with conds[d]:
            if front:
                queues[d].insert(0, job)
            else:
                queues[d].append(job)
            conds[d].notify()

    def watch(lg, cid, deadline):
        status = ""
        while time.time() < deadline and not stop_flag.is_set():
            _, o = http("GET", f"{st.authorities[lg.device]}/v1/commands/{cid}")
            status = o.get("status", "") if isinstance(o, dict) else ""
            if status in ("APPLIED", "STOPPED"):
                lg.terminal_seen[cid] = time.time()
                break
            time.sleep(w["retry_s"])
        lg.final_status[cid] = status
        if status == "STOPPED" and reissue and time.time() < deadline and not stop_flag.is_set():
            enqueue(lg.device, (lg, len(lg.ids)), front=True)

    def submitter(d):
        while not stop_flag.is_set():
            with conds[d]:
                while not queues[d] and not stop_flag.is_set():
                    conds[d].wait(0.2)
                if stop_flag.is_set():
                    return
                lg, gen = queues[d].pop(0)
            deadline = lg.arrival + w["deadline_s"]
            if time.time() >= deadline:
                if not lg.admitted:
                    lg.decision = "EXPIRED_QUEUED"
                continue
            counters[d] += 1
            cid = lg.cid if gen == 0 else f"{lg.cid}-r{gen}"
            seq = counters[d]
            lg.ids.append(cid)
            lg.seqs[cid] = seq
            while time.time() < deadline and not stop_flag.is_set():
                body = {"schema_version": 1, "domain_id": st.device_ids[d], "generation": 1, "command_id": cid,
                        "operation": "ADD", "value": [lg.value], "epoch": 1, "sequence": seq}
                lg.attempts += 1
                code, out = http("POST", f"{st.authorities[d]}/v1/commands", body, timeout=max(1.0, w["retry_s"] * 3))
                dec = out.get("decision", "") if isinstance(out, dict) else ""
                lg.last_reason = out.get("reason", "") if isinstance(out, dict) else str(out)[:80]
                if dec == "REJECT":
                    lg.decision = lg.decision if lg.admitted else "REJECT"
                    break
                if dec == "ADMITTED":
                    lg.decision = "ADMITTED"
                    lg.admitted.setdefault(cid, time.time())
                    threading.Thread(target=watch, args=(lg, cid, deadline), daemon=True).start()
                    break
                lg.decision = lg.decision if lg.admitted else (dec or f"HTTP_{code}")
                time.sleep(w["retry_s"])
            else:
                if not lg.admitted:
                    lg.decision = "EXPIRED_" + (lg.decision or "NONE")

    subs = [threading.Thread(target=submitter, args=(d,), daemon=True) for d in range(cfg["devices"])]
    for t in subs:
        t.start()

    runners = []
    for d in range(cfg["devices"]):
        r = FaultRunner(tb, st, d, [x for x in windows if x.device == d], t0, wd, cfg, log_fault)
        r.start()
        runners.append(r)
    timers = [threading.Timer(max(0.0, t0 + a.t - time.time()), enqueue, args=(a.device, (logs[a.cid], 0))) for a in arrivals]
    for t in timers:
        t.daemon = True
        t.start()
    time.sleep(max(0.0, t0 + cfg["duration_s"] + w["deadline_s"] - time.time()))
    stop_flag.set()
    for t in subs:
        t.join(timeout=10)
    for r in runners:
        r.stop_flag.set()
    for r in runners:
        r.join(timeout=30)
    for px in st.proxies:
        px.clear()
    for adm in st.device_admin:
        http("POST", f"{adm}/_test/online?v=true")
    time.sleep(cfg.get("tail_s", 10))  # late deliveries / drains
    flog.close()
    errors = [e for r in runners for e in r.errors]
    return logs, t0, time.time(), errors


# ------------------------------------------------------------------ collection
ROW_FIELDS = ["regime", "contract", "policy", "episode", "devices", "cell_status", "scope",
              "offered", "admitted", "clean_fulfilled", "fulfilled_late", "expired_blocked",
              "bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied",
              "late_effects", "late_effects_after_client_gave_up", "unresolved_at_end",
              "stranded_resolved_unit_sec", "stranded_censored_unit_sec", "mean_time_to_terminal_s",
              "reissues", "queries", "queries_terminal", "stops", "stops_terminal", "fences", "fences_ok",
              "evicon_decisions", "decisions_acting", "decisions_by_mode", "decisions_by_action", "defer_reasons",
              "escalations", "dispatch_attempts", "retry_suppressed_t4", "activated",
              "fault_windows", "fault_windows_ok", "crashes", "cell_dir"]


_FRAC = re.compile(r"^(.*T\d\d:\d\d:\d\d)(?:\.(\d+))?(.*)$")


def _ts(s):
    """RFC 3339 wall time -> epoch seconds. Go writes up to 9 fractional digits,
    which datetime.fromisoformat only accepts from Python 3.11 on; VM-C runs 3.10."""
    if not s:
        return None
    m = _FRAC.match(str(s).strip().replace("Z", "+00:00"))
    if not m:
        return None
    head, frac, tz = m.group(1), m.group(2) or "", m.group(3)
    try:
        base = datetime.fromisoformat(head + tz).timestamp()
    except ValueError:
        return None
    return base + (int(frac) / 10 ** len(frac) if frac else 0.0)


def collect_live(cfg, st: Stack, logs):
    """Truth and authority state while the stack is still up. Every HTTP
    answer is checked; anything missing makes the cell INVALID."""
    problems = []
    code, summ = http("GET", f"{st.plant_url}/v1/truth/summary", timeout=10)
    if code != 200 or not isinstance(summ, dict):
        problems.append(f"plant summary HTTP {code}")
        summ = {}
    truth, status = {}, {}
    for lg in logs.values():
        dev = st.device_ids[lg.device]
        for cid in lg.ids:
            c, tr = http("GET", f"{st.plant_url}/v1/truth?cmd={cid}&device={dev}", timeout=10)
            if c != 200 or not isinstance(tr, dict):
                problems.append(f"truth {cid}: HTTP {c}")
                continue
            truth[cid] = tr
            if cid in lg.admitted:
                c, o = http("GET", f"{st.authorities[lg.device]}/v1/commands/{cid}", timeout=10)
                if c != 200 or not isinstance(o, dict):
                    problems.append(f"authority status {cid}: HTTP {c}")
                    continue
                status[cid] = o.get("status", "")
    return summ, truth, status, problems


def parse_traces(paths):
    ev = Counter()
    by_mode, by_action, defers = Counter(), Counter(), Counter()
    evidence_at = {}
    acting = 0
    for p in paths:
        if not Path(p).exists():
            continue
        for line in Path(p).read_text(errors="replace").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            name = e.get("event", "")
            ev[name] += 1
            reason, outcome = e.get("reason") or "", e.get("outcome") or ""
            if name == "EVICON_DECISION":
                mode = reason.split(";", 1)[0].removeprefix("mode=")
                by_mode[mode] += 1
                by_action[outcome] += 1
                acting += outcome in ("QUERY", "STOP", "FENCE")
            elif name == "EVICON_STOP_RESULT":
                ev["stops_terminal"] += outcome in ("STOPPED", "APPLIED")
            elif name == "EVICON_FENCE_RESULT":
                ev["fences_ok"] += outcome.startswith("2")
            elif name == "EVICON_QUERY_RESULT":
                ev["queries_terminal"] += outcome in ("STOPPED", "APPLIED")
            elif name == "DECISION_RECORDED" and e.get("decision") == "DEFER":
                defers[reason.split(":", 1)[0][:60]] += 1
            if name in ("EVIDENCE_COMMITTED", "OUTBOX_RECEIPT_COMMITTED") and e.get("command_id"):
                if "APPLIED" in outcome + reason or "STOPPED" in outcome + reason:
                    t = _ts(e.get("wall_time"))
                    cid = e["command_id"]
                    if t is not None and (cid not in evidence_at or t < evidence_at[cid]):
                        evidence_at[cid] = t
    return ev, by_mode, by_action, defers, evidence_at, acting


def build_row(cfg, cell: Cell, st: Stack, logs, t_end, summ, truth, status, trace_paths, problems, fault_log, celldir):
    w = cfg["workload"]
    validity = float(w.get("validity_s", w["deadline_s"]))
    row = {k: 0 for k in ROW_FIELDS}
    row.update({"regime": cfg.get("regime", cfg["name"]), "contract": cell.contract, "policy": cell.policy, "episode": cell.episode,
                "devices": cfg["devices"], "offered": len(logs), "scope": "DEV_LOCAL" if cfg.get("mode", "local") == "local" else "TESTBED",
                "cell_dir": str(celldir)})
    row["duplicate_effects"] = int(summ.get("identities_with_duplicate_effects", 0)) if summ else 0
    row["bound_violations"] = sum(1 for d in (summ or {}).get("devices", []) if d.get("limit_exceeded") and d["device"] in st.device_ids)
    ev, by_mode, by_action, defers, evidence_at, acting = parse_traces(trace_paths)
    ttt = []
    for lg in logs.values():
        if lg.decision == "ADMITTED" or lg.admitted:
            row["admitted"] += 1
        else:
            row["expired_blocked"] += 1
        row["reissues"] += max(0, len(lg.ids) - 1)
        effects_total, ids_with_effect, first = 0, 0, None
        for cid in lg.ids:
            tr = truth.get(cid)
            if tr is None:
                continue
            eff = int(tr.get("effects", 0))
            fns = int(tr.get("first_effect_ns", 0) or 0)
            if eff > 0:
                effects_total += eff
                ids_with_effect += 1
                t_eff = fns / 1e9
                first = t_eff if first is None else min(first, t_eff)
            if cid in lg.admitted:
                s = status.get(cid, "")
                if s not in ("APPLIED", "STOPPED"):
                    row["unresolved_at_end"] += 1
                else:
                    t_ev = evidence_at.get(cid)
                    if t_ev is not None:
                        ttt.append(max(0.0, t_ev - lg.admitted[cid]))
                if s == "STOPPED" and eff > 0:
                    row["false_stopped"] += 1
                if s == "APPLIED" and eff == 0:
                    row["false_applied"] += 1
                if eff == 0:
                    # capacity reserved for nothing, from admission to terminal
                    # evidence (resolved) or to the end of the cell (censored)
                    t_ev = evidence_at.get(cid) if s in ("APPLIED", "STOPPED") else None
                    if t_ev is not None:
                        row["stranded_resolved_unit_sec"] += lg.value * max(0.0, t_ev - lg.admitted[cid])
                    else:
                        row["stranded_censored_unit_sec"] += lg.value * max(0.0, t_end - lg.admitted[cid])
        if ids_with_effect > 1:
            row["logical_duplicates"] += 1  # one intent took effect under two identities: unsafe reissue
        if effects_total == 1:
            if first <= lg.arrival + w["deadline_s"]:
                row["clean_fulfilled"] += 1
            else:
                row["fulfilled_late"] += 1
        if first is not None:
            if first > lg.arrival + validity:
                row["late_effects"] += 1
            if lg.decision != "ADMITTED":
                row["late_effects_after_client_gave_up"] += 1
    row["stranded_resolved_unit_sec"] = round(row["stranded_resolved_unit_sec"], 3)
    row["stranded_censored_unit_sec"] = round(row["stranded_censored_unit_sec"], 3)
    row["mean_time_to_terminal_s"] = round(sum(ttt) / len(ttt), 3) if ttt else ""
    row["queries"] = ev["EVICON_QUERY_RESULT"] + ev["PROBE_ATTEMPT"]
    row["queries_terminal"] = ev["queries_terminal"]
    row["stops"], row["stops_terminal"] = ev["EVICON_STOP_RESULT"], ev["stops_terminal"]
    row["fences"], row["fences_ok"] = ev["EVICON_FENCE_RESULT"], ev["fences_ok"]
    row["evicon_decisions"], row["decisions_acting"] = ev["EVICON_DECISION"], acting
    row["decisions_by_mode"] = json.dumps(dict(by_mode), sort_keys=True)
    row["decisions_by_action"] = json.dumps(dict(by_action), sort_keys=True)
    row["defer_reasons"] = json.dumps(dict(defers.most_common(8)), sort_keys=True)
    row["escalations"] = by_action.get("ESCALATE", 0)
    row["dispatch_attempts"] = ev["OUTBOX_DISPATCH_ATTEMPT"]
    row["retry_suppressed_t4"] = ev["OUTBOX_RETRY_SUPPRESSED_T4"]
    row["activated"] = bool(acting) if cell.policy in RESOLVERS else ""
    row["fault_windows"] = len(fault_log)
    row["fault_windows_ok"] = sum(1 for r in fault_log if r.get("ok"))
    row["crashes"] = sum(1 for r in fault_log if r.get("crashed_at"))
    if not any(Path(p).exists() for p in trace_paths):
        problems.append("authority traces missing")
    row["cell_status"] = "VALID" if not problems else "INVALID:" + "; ".join(sorted(set(problems)))[:300]
    return row


# ------------------------------------------------------------------ activation gate
def activation_gate(cfg, rows):
    g = cfg.get("activation_gate")
    if not g:
        return None
    policies = g.get("policies", ["EVICON"])
    contracts = g.get("contracts", cfg["contracts"])
    need = int(g.get("min_acting_decisions_per_cell", 1))
    cells, failing = [], []
    for r in rows:
        if r["policy"] in policies and r["contract"] in contracts:
            ok = str(r["cell_status"]).startswith("VALID") and int(r["decisions_acting"] or 0) >= need
            cells.append({"cell": f"ep{r['episode']}/{r['contract']}/{r['policy']}", "acting": r["decisions_acting"],
                          "by_mode": r["decisions_by_mode"], "status": r["cell_status"], "ok": ok})
            if not ok:
                failing.append(cells[-1]["cell"])
    invalid = [f"ep{r['episode']}/{r['contract']}/{r['policy']}" for r in rows if not str(r["cell_status"]).startswith("VALID")]
    return {"pass": bool(cells) and not failing and not invalid, "gated_cells": len(cells), "failing": failing,
            "invalid_cells": invalid, "rule": f">= {need} acting EVICON decision(s) in every VALID gated cell and no INVALID cell",
            "cells": cells}


# ------------------------------------------------------------------ main
class RunLog:
    """Everything the campaign prints also goes to <run>/campaign.log; every
    failure (with its traceback, when there is one) goes to <run>/errors.log."""

    def __init__(self):
        self.dir = None

    def attach(self, out: Path):
        self.dir = out

    def _write(self, name, text):
        if self.dir is not None:
            with open(self.dir / name, "a") as fh:
                fh.write(text.rstrip("\n") + "\n")

    def info(self, msg):
        print(msg, flush=True)
        self._write("campaign.log", f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}")

    def error(self, where, detail="", exc=None):
        text = f"{time.strftime('%Y-%m-%d %H:%M:%S%z')} [{where}] {detail}".rstrip()
        if exc is not None:
            text += "\n" + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).rstrip()
        self._write("errors.log", text + "\n")
        self._write("campaign.log", f"{time.strftime('%Y-%m-%d %H:%M:%S')} ERROR [{where}] {detail}")


LOG = RunLog()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config")
    ap.add_argument("--out", default="results/runtime")
    ap.add_argument("--resume", help="continue the campaign in this run directory")
    ap.add_argument("--bin-dir", default=str(REPO / "bin"), help="local mode: directory with evicon-* binaries")
    ap.add_argument("--max-cells", type=int, default=0, help="stop after this many new cells (checkpointed)")
    a = ap.parse_args(argv)
    # Every process is started after "cd <cell dir>", so all paths handed to it
    # must be absolute (release 063: a relative --out made the plant fail).
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
    LOG.info(f"campaign {cfg['name']} (EviCon {RELEASE}) -> {out}")
    try:
        return run_campaign(cfg, out, Path(a.bin_dir).resolve(), a.max_cells)
    except Exception as e:
        LOG.error("campaign", f"{type(e).__name__}: {e}", e)
        LOG.info(f"campaign aborted: {type(e).__name__}: {e} (traceback in {out / 'errors.log'})")
        raise


def run_campaign(cfg, out: Path, bin_dir: Path, max_cells: int = 0):
    tb = Testbed(cfg, out, bin_dir)
    info = tb.preflight()
    done = set()
    if (out / "done.jsonl").exists():
        done = {json.loads(l)["cell"] for l in (out / "done.jsonl").read_text().splitlines() if l.strip()}
    man = out / "manifest.json"
    manifest = json.loads(man.read_text()) if man.exists() else {
        "schema_version": 2, "release": RELEASE, "scope": "DEV_LOCAL" if cfg.get("mode", "local") == "local" else "TESTBED",
        "config_sha256": hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest(), "driver_host": socket.gethostname(),
        "python": sys.version.split()[0], "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "sessions": []}
    manifest["sessions"].append({"started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "preflight": info})
    man.write_text(json.dumps(manifest, indent=2) + "\n")
    csv_path = out / "runtime_episodes.csv"
    if not csv_path.exists():
        with open(csv_path, "w", newline="") as fh:
            csv.DictWriter(fh, fieldnames=ROW_FIELDS).writeheader()
    new_cells = 0
    for ep in range(cfg.get("episodes", 1)):
        arrivals, windows = schedule(cfg, ep)
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
                try:
                    st = tb.start(cell, wd)
                    logs, t0, t_end, ferr = run_cell(cfg, tb, st, arrivals, windows, wd, celldir)
                    problems += [f"fault: {e}" for e in ferr][:5]
                    for i, pid in enumerate(st.auth_pids):
                        if not tb.a.alive(pid):
                            problems.append(f"authority{i} died")
                    if not tb.e.alive(st.plant_pid):
                        problems.append("plant died")
                    summ, truth, status, p2 = collect_live(cfg, st, logs)
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
                    flog = [json.loads(l) for l in (celldir / "faults.jsonl").read_text().splitlines() if l.strip()]
                (celldir / "clients.jsonl").write_text("".join(json.dumps(asdict(lg)) + "\n" for lg in logs.values()))
                row = build_row(cfg, cell, st or Stack(plant_url=""), logs, t_end, summ, truth, status, traces, problems, flog, celldir)
                (celldir / "row.json").write_text(json.dumps(dict(row, problems=sorted(set(problems))), indent=2) + "\n")
                if problems:
                    (celldir / "problems.txt").write_text("\n".join(sorted(set(problems))) + "\n")
                    LOG.error(cell.key, "INVALID: " + " | ".join(sorted(set(problems))))
                with open(csv_path, "a", newline="") as fh:
                    csv.DictWriter(fh, fieldnames=ROW_FIELDS).writerow(row)
                with open(out / "done.jsonl", "a") as fh:
                    fh.write(json.dumps({"cell": cell.key, "status": row["cell_status"], "at": time.time()}) + "\n")
                new_cells += 1
                LOG.info(f"[{cell.key}] {row['cell_status'][:160]} " + json.dumps({k: row[k] for k in (
                    "offered", "admitted", "clean_fulfilled", "unresolved_at_end", "false_stopped", "duplicate_effects",
                    "bound_violations", "decisions_acting", "stops", "fences", "queries")}))
    rows = list(csv.DictReader(open(csv_path, newline="")))
    gate = activation_gate(cfg, rows)
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
