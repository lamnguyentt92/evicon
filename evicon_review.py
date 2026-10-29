#!/usr/bin/env python3
"""EviCon reviewer driver: one command, run on VM-C as root.

    python3 evicon_review.py all              # every stage in order; stops at the first FAIL
    python3 evicon_review.py <stage> ...      # one or more stages
    python3 evicon_review.py status           # stage table of the current run
    python3 evicon_review.py plan             # what each stage does and how long it takes
    python3 evicon_review.py bundle           # everything I need for analysis -> EVICON_<release>_result.zip
    python3 evicon_review.py bundle --incremental   # only what is new since the last bundle (072)
    python3 evicon_review.py measure <stack> ...    # C2: measure a real system (stacks below)
    python3 evicon_review.py campaign <config.json> [--episodes N] [--max-cells N]   # long runtime campaign (release 065)
    python3 evicon_review.py overhead               # RQ4 load campaigns + resource sampling (release 068)
    python3 evicon_review.py report [--from RUN ...]   # paper tables over this run and older ones (release 068; 070)
    python3 evicon_review.py report --archive DIR      # ... and the runs the newest report in an archive of result bundles read (078)
    python3 evicon_review.py import-results BUNDLE.zip|DIR ... [--archive DIR] [--lean]   # 078: result bundles of
                                                       # earlier releases -> an archive the report reads
    python3 evicon_review.py twin2 [--from RUN ...]    # exploratory simulator twin v2 vs testbed (release 068)

Stages (in order):
    preflight   tools on VM-C, SSH to A/B/D/E, ports, clocks, VM-A -> VM-C reachability
    build       Go toolchain (pinned) and bin/evicon-* (cgo + SQLite)
    verify      go vet + go test, EviCon-Probe tests, firmware host tests, analysis tests
    formal      TLC on 5 safe models and 3 negative controls
    sim         evicon-des exploration (SIMULATION_ONLY) + analysis
    distribute  binaries and cost file to /root/evicon on VM-A, VM-B, VM-E
    controls    EviCon-Probe positive controls (evicon-endpoint on VM-B) and negative
                controls (evicon-device presets on VM-E, K0F/K1F included); every control
                has an expected contract
    pilot       C3 runtime pilots on the testbed: pending-binding and capacity-binding,
                with the activation gate (review RV01)
    analyze     analysis + sample-size planning of the pilot (power.py)
    collect     one zip of the run directory with a SHA-256 manifest

Commands (release 065-068; run after `all` has passed build, distribute and controls):
    measure     stacks mosquitto, coap, temporal, emqx, nats, minio, dbos, k3s, thingsboard,
                zephyr. Starts the truth plant and a reference device/worker/app when the stack
                needs them, brings the system up (Mosquitto, EMQX, NATS JetStream, MinIO,
                PostgreSQL for DBOS, k3s, ThingsBoard through docker compose on VM-B; its own
                Temporal dev server on VM-D:7243, or testbed key temporal_address; the Zephyr
                firmware from deploy/setup/build_iot.sh on VM-E), probes it from VM-C through an
                in-process fault proxy, stops what it started and writes
                measure/<stack>/measure.md and measure/contract_map.md. Several stacks: one
                after the other; a FAIL does not stop the following ones (release 068)
    campaign    runs or resumes a runtime campaign (e.g. experiment A, about 7 h for
                5 episodes); --max-cells splits it over several sessions (run it again
                to resume); when every cell is done: analysis, sample-size planning and
                the simulator-vs-testbed comparison (sim_twin). A config with a
                "preregistration" (expA_confirm_k0/k2, release 067) runs only while its
                plan and the analysis files the plan froze are unchanged
    overhead    rq4_load_r1/r5/r20 without faults, with evaluation/rq4_sampler.py on VM-A and
                VM-E (reads /proc of EviCon's own processes), then evaluation/overhead.py
    latency-control  (077) the same three loads from VM-C against evicon-noop on VM-A (admits
                everything at once): the load generator's and the network's share of the
                admission latency (evaluation/latency_control.py), about 25 min
    report      fence audit, confirmatory experiment A against its plan (070: and its
                replications; 071: experiment B2), overhead, contract map (newest measurement per contract, with
                the release and run that measured it) and firmware footprint, from this run
                and the runs given with --from (read only) -> report/REPORT.md; 070: LaTeX
                tables, PDF figures and number macros for the paper -> report/paper/
    twin2       experiments/sim/iot_dosing_testbed_v2.json against the experiment A data of
                this run and the --from runs (SIMULATION_ONLY, not preregistered)

Options:
    --testbed FILE  VM addresses (default: testbed.json; required on the testbed since release 078:
                    `bash deploy/setup/testbed_init.sh write ...` writes it from the VMs' current addresses)
    --run DIR       run directory (default: a new results/review-<stamp>; 'all' and single
                    stages continue the latest run unless --new is given)
    --new           start a new run directory
    --quick         1 probe repetition, 1 pilot episode, 5 simulator episodes
    --force         re-run stages that already passed in this run
    --local         self-test: every role on this host (development only, not a measurement)
    --episodes N, --max-cells N, --new-campaign   campaign options
    --from RUN      report/twin2: also read this run directory (repeatable)
    --archive DIR   report: also read the runs the newest report under DIR/runs read, and that report's own run
                    (the archive import-results writes; 078); import-results: the archive to write (default
                    /root/evicon-archive). --archive-missing-ok: go on when the archive lacks some of them
    --lean          import-results: leave out the authority traces of runs the 077 plan does not reference

Every stage writes logs/<stage>.log and a summary in stages.json; REVIEW_LOG.md is the
human-readable record; errors.log collects every FAIL, WARN and driver error (with traceback).
Each invocation is recorded in results/logs/driver.log. `bundle` packs results/ (review runs,
make logs, local campaigns) with an environment snapshot of VM-C (and of the VMs, over SSH)
into one zip. Nothing is deleted on the VMs except this driver's own state
directories (/var/lib/evicon/{controls,campaigns,measure,overhead}) and the containers and
volumes of its own compose project (evicon-probe-owner); processes are stopped by pid file only, and only
while they still run in the directory of that pid file. One evicon_review.py runs at a time
(/run/lock/evicon-review.lock); status and bundle are always allowed.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent
RELEASE = (REPO / "VERSION").read_text().strip() if (REPO / "VERSION").exists() else "062"
sys.path.insert(0, str(REPO / "evaluation"))
sys.path.insert(0, str(REPO / "conformance"))
# Release 068: the driver's own HTTP(S) requests go only to testbed peers, so they never use
# http_proxy/https_proxy (as evicon_probe already did). urllib matches no_proxy by host suffix
# and ignores CIDR entries such as 192.168.0.0/16, so a lab proxy setting would capture them.
# Downloads (Go, Temporal CLI, TLA+) run as separate processes and keep their proxy settings.
urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))


def _urlopen(req, timeout, context=None):
    """urlopen without proxies, also with a TLS context (urllib.request.urlopen(context=...)
    builds a new opener that reads the proxy environment again)."""
    if context is None:
        return urllib.request.urlopen(req, timeout=timeout)
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context)).open(req, timeout=timeout)

# Ports of the EviCon processes. VM-B's endpoint moved from 18081 to 18181 in release
# 064: 18081 (and 18080-18083, 19080-19083) belong to an older deployment on the testbed, whose
# processes may still run on the VMs.
ENDPOINT_PORT = 18181
PLANT_PORT, DEVICE_PORT, DEVICE_ADMIN_PORT = 18090, 18100, 18101
NEEDED_PORTS = {"B": [ENDPOINT_PORT], "E": [PLANT_PORT] + list(range(18100, 18110)), "A": list(range(19000, 19008))}

STAGES = ["preflight", "build", "verify", "formal", "sim", "distribute", "controls", "pilot", "analyze", "collect"]
DEFAULT_TESTBED = {
    "vm_a": "192.168.107.151", "vm_b": "192.168.107.152", "vm_c": "192.168.107.150",
    "vm_d": "192.168.107.153", "vm_e": "192.168.107.154", "ssh_user": "root",
    "remote_root": "/root/evicon", "state_dir": "/var/lib/evicon",
    # "" (default): `measure temporal` starts its own Temporal dev server on VM-D:7243 and
    # stops it afterwards. "host:port": measure an existing Temporal frontend instead.
    "temporal_address": "",
}
# The IPs written in the shipped configs; replaced by the testbed's at run time.
CONFIG_IPS = {"192.168.107.151": "vm_a", "192.168.107.152": "vm_b", "192.168.107.150": "vm_c",
              "192.168.107.153": "vm_d", "192.168.107.154": "vm_e"}
POSITIVE = [("evicon-e1", ["-profile", "E1"]), ("evicon-e2", ["-profile", "E2"]),
            ("evicon-e2-stop", ["-profile", "E2", "-per-identity-stop"]), ("evicon-e3", ["-profile", "E3"])]
# K0F and K1F (release 065) are the fence upgrades used by experiment A on the testbed.
NEGATIVE = [("device-k0", "K0"), ("device-k0f", "K0F"), ("device-k1", "K1"), ("device-k1f", "K1F"), ("device-k3s", "K3S"),
            ("device-cutoff", "CUTOFF"), ("device-k3t", "K3T")]
PILOTS = ["pilot_pending_binding", "pilot_capacity_binding"]
DURATION = {"preflight": "1 min", "build": "2-5 min (first Go download longer)", "verify": "5-10 min",
            "formal": "2-5 min", "sim": "10-15 min (--quick: 5-8 min)", "distribute": "1 min",
            "controls": "50-75 min (--quick: 20-25 min)", "pilot": "about 3 h (--quick: about 1 h)",
            "analyze": "1 min", "collect": "1 min"}


# ------------------------------------------------------------------ small utilities
def now():
    return dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")


class StageFail(Exception):
    pass


class Run:
    def __init__(self, path: Path):
        self.path = path
        (path / "logs").mkdir(parents=True, exist_ok=True)
        self.state_file = path / "stages.json"
        self.state = json.loads(self.state_file.read_text()) if self.state_file.exists() else {}
        self.log_fh = None
        self.stage = ""

    def save(self):
        self.state_file.write_text(json.dumps(self.state, indent=2, sort_keys=True) + "\n")

    def begin(self, stage):
        self.stage = stage
        self.log_fh = open(self.path / "logs" / f"{stage.replace(':', '-')}.log", "a")
        self.log(f"===== {stage} started {now()}")
        self.state[stage] = {"status": "RUNNING", "started": now(), "source": source_fingerprint()}
        self.save()

    def end(self, status, summary):
        self.state[self.stage].update({"status": status, "ended": now(), "summary": summary})
        self.save()
        self.log(f"===== {self.stage} {status} {now()}: {summary}")
        with open(self.path / "REVIEW_LOG.md", "a") as fh:
            fh.write(f"| {self.stage} | {status} | {self.state[self.stage]['started']} | {now()} | {str(summary).replace('|', '/')} |\n")
        if self.log_fh:
            self.log_fh.close()
            self.log_fh = None

    def log(self, msg):
        line = msg.rstrip("\n")
        try:
            print(line, flush=True)
        except OSError:  # the terminal is gone (SSH dropped): keep logging to the files
            pass
        if self.log_fh:
            self.log_fh.write(line + "\n")
            self.log_fh.flush()

    def problem(self, kind, msg, exc=None):
        """FAIL / WARN / ERROR: shown, written to the stage log, and appended to errors.log."""
        text = f"{now()} {kind} [{self.stage or '-'}] {msg}"
        if exc is not None:
            text += "\n" + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).rstrip()
        self.log(text)
        with open(self.path / "errors.log", "a") as fh:
            fh.write(text + "\n\n")


RUN: Run = None


def sh(cmd, cwd=REPO, env=None, timeout=3600, check=True, quiet=False):
    """Runs a local command, tees output into the stage log, returns (rc, output)."""
    RUN.log(f"$ {cmd if isinstance(cmd, str) else ' '.join(shlex.quote(str(c)) for c in cmd)}")
    p = subprocess.Popen(cmd, cwd=str(cwd), env=env, shell=isinstance(cmd, str), stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, executable="/bin/bash" if isinstance(cmd, str) else None)
    out = []
    deadline = time.monotonic() + timeout

    def reader():
        for line in p.stdout:
            out.append(line)
            if not quiet:
                RUN.log("  " + line.rstrip("\n"))
            elif RUN.log_fh:
                RUN.log_fh.write("  " + line)
    t = threading.Thread(target=reader, daemon=True)
    t.start()
    try:
        while p.poll() is None:
            if time.monotonic() > deadline:
                p.kill()
                raise StageFail(f"timeout after {timeout}s: {cmd}")
            time.sleep(0.2)
    except BaseException:
        # interrupted (Ctrl-C, SIGTERM): the child must not outlive the driver
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
        raise
    t.join(5)
    text = "".join(out)
    if check and p.returncode != 0:
        raise StageFail(f"command failed (rc={p.returncode}): {cmd if isinstance(cmd, str) else ' '.join(map(str, cmd))}")
    return p.returncode, text


def go_env():
    env = dict(os.environ)
    local_go = REPO / ".tools" / "go" / "bin"
    if (local_go / "go").exists():
        env["PATH"] = f"{local_go}:{env.get('PATH', '')}"
    env.setdefault("GOTOOLCHAIN", "local")
    env["CGO_ENABLED"] = "1"
    return env


def have(tool, env=None):
    return shutil.which(tool, path=(env or os.environ).get("PATH")) is not None


class Testbed:
    def __init__(self, cfg: dict, local: bool):
        self.cfg = dict(DEFAULT_TESTBED, **cfg)
        self.local = local
        if local:
            for k in ("vm_a", "vm_b", "vm_c", "vm_d", "vm_e"):
                self.cfg[k] = "127.0.0.1"
            self.cfg["remote_root"] = str(RUN.path / "local-hosts" / "evicon")
            self.cfg["state_dir"] = str(RUN.path / "local-hosts" / "state")

    def ip(self, role):
        return self.cfg[f"vm_{role.lower()}"]

    def ssh_target(self, role):
        return None if self.local else f"{self.cfg['ssh_user']}@{self.ip(role)}"

    def host(self, role):
        from campaign import Host
        return Host(f"VM-{role.upper()}", self.ip(role), self.ssh_target(role))

    def run(self, role, cmd, check=True, timeout=300):
        h = self.host(role)
        RUN.log(f"[{h.name}] $ {cmd}")
        p = h.run(cmd, check=False, timeout=timeout)
        for line in (p.stdout + p.stderr).splitlines()[-40:]:
            RUN.log("  " + line)
        if check and p.returncode != 0:
            # release 068: the error itself, not only the command (067: "docker compose up -d"
            # failed and the reason, a removed image, was only in the run log)
            why = " | ".join(l.strip() for l in (p.stderr or p.stdout).strip().splitlines()[-3:])[-400:]
            raise StageFail(f"{h.name}: command failed (rc={p.returncode}): {cmd}" + (f" -> {why}" if why else ""))
        return p

    def copy(self, role, files, dest):
        if self.local:
            Path(dest).mkdir(parents=True, exist_ok=True)
            for f in files:
                shutil.copy2(f, Path(dest) / Path(f).name)
            return
        sh(["scp", "-q", "-o", "BatchMode=yes", *map(str, files), f"{self.ssh_target(role)}:{dest}/"])

    def substitute(self, text: str) -> str:
        """The shipped configs' addresses -> this testbed's, in one pass (release 078: a new address may equal
        another role's shipped one) and only as whole addresses (not inside a longer number)."""
        pat = re.compile(r"(?<![\d.])(" + "|".join(re.escape(ip) for ip in CONFIG_IPS) + r")(?![\d])")
        return pat.sub(lambda m: self.cfg[CONFIG_IPS[m.group(1)]], text)


def local_ipv4() -> set:
    """Every IPv4 address of this host (release 078: testbed.json must name VM-C by one of them)."""
    out = {"127.0.0.1"}
    try:
        txt = subprocess.run(["ip", "-o", "-4", "addr", "show"], capture_output=True, text=True, timeout=10).stdout
        out |= set(re.findall(r"\binet (\d+\.\d+\.\d+\.\d+)/", txt))
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        out |= set(subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=10).stdout.split())
    except (OSError, subprocess.SubprocessError):
        pass
    return {a for a in out if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", a)}


def check_testbed_cfg(cfg: dict, where: str, own=None) -> None:
    """Release 078: the five addresses are IPv4, distinct, and VM-C's is one of this host's own (a testbed.json
    copied from an earlier installation names addresses that are gone)."""
    ips = {}
    for k in ("vm_a", "vm_b", "vm_c", "vm_d", "vm_e"):
        v = str(cfg.get(k, ""))
        if not re.fullmatch(r"(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})", v) or any(int(x) > 255 for x in v.split(".")):
            raise SystemExit(f"{where}: {k} must be an IPv4 address, not {v!r} (bash deploy/setup/testbed_init.sh write ...)")
        if v in ips:
            raise SystemExit(f"{where}: {k} and {ips[v]} have the same address {v}")
        ips[v] = k
    own = local_ipv4() if own is None else own
    if cfg["vm_c"] not in own:
        raise SystemExit(f"{where}: vm_c is {cfg['vm_c']}, which is not an address of this machine ({', '.join(sorted(own - {'127.0.0.1'})) or 'none'}). "
                         f"testbed.json names the VMs of another installation: write it again from the current addresses: "
                         f"bash deploy/setup/testbed_init.sh write --vm-a <IP> --vm-b <IP> --vm-d <IP> --vm-e <IP>")


def load_testbed(path, local):
    cfg = {}
    p = Path(path) if path else REPO / "testbed.json"
    if p.exists():
        cfg = json.loads(p.read_text())
        unknown = set(cfg) - set(DEFAULT_TESTBED)
        if unknown:
            raise SystemExit(f"{p}: unknown keys {sorted(unknown)} (allowed: {sorted(DEFAULT_TESTBED)})")
        ta = str(cfg.get("temporal_address") or "")
        if ta and not re.fullmatch(r"[A-Za-z0-9.\-]+:\d{1,5}", ta):
            raise SystemExit(f"{p}: temporal_address must be host:port (e.g. 192.168.107.153:7233) or empty, not {ta!r}")
        if not local:
            check_testbed_cfg(dict(DEFAULT_TESTBED, **cfg), str(p))
    elif path:
        raise SystemExit(f"testbed file {path} not found")
    elif not local:
        # release 078: never the shipped default addresses on a testbed (they belong to an earlier installation)
        raise SystemExit(f"{p} missing: write it from the VMs' current addresses first:\n"
                         f"  bash deploy/setup/testbed_init.sh detect        # this machine (VM-C)\n"
                         f"  bash deploy/setup/testbed_init.sh write --vm-a <IP> --vm-b <IP> --vm-d <IP> --vm-e <IP>")
    return Testbed(cfg, local)


def http_ok(url, timeout=3):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def wait_url(url, timeout=30):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if http_ok(url):
            return
        time.sleep(0.3)
    raise StageFail(f"not reachable: {url}")


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


SOURCE_DIRS = ("cmd", "internal", "conformance", "evaluation", "experiments", "firmware", "formal", "scripts", "deploy")
SOURCE_FILES = ("go.mod", "go.sum", "Makefile", "VERSION", "evicon_review.py")
GENERATED = {"__pycache__", ".tools", "test_evc_core", "test_evc_proto"}


def source_fingerprint() -> str:
    """Content hash of the code (never documentation, results/, bin/, caches or test
    binaries). Every stage records it, so a resumed run whose stages saw different code is
    visible."""
    files = [REPO / f for f in SOURCE_FILES if (REPO / f).is_file()]
    for d in SOURCE_DIRS:
        files += [p for p in (REPO / d).rglob("*") if p.is_file()]
    h = hashlib.sha256()
    for p in sorted(files):
        rel = p.relative_to(REPO)
        if GENERATED.intersection(rel.parts) or p.suffix in (".pyc", ".md"):
            continue
        h.update(f"{rel}\0{sha256(p)}\n".encode())
    return h.hexdigest()[:16]


def manifest_drift() -> list:
    """Files that differ from (or are missing against) the release MANIFEST.sha256."""
    m = REPO / "MANIFEST.sha256"
    if not m.exists():
        return []
    drift = []
    for line in m.read_text().splitlines():
        digest, _, rel = line.partition("  ")
        p = REPO / rel
        if not p.is_file():
            drift.append(f"missing {rel}")
        elif sha256(p) != digest:
            drift.append(f"modified {rel}")
    return drift


# ------------------------------------------------------------------ stages
def st_preflight(tb: Testbed, a):
    problems, warnings, info = [], [], {"release": RELEASE, "host": socket.gethostname(), "python": sys.version.split()[0],
                                         "platform": platform.platform(), "local_mode": tb.local}
    if sys.version_info < (3, 10):
        problems.append("Python >= 3.10 required on VM-C")
    drift = manifest_drift()
    info["manifest_drift"] = drift
    if drift:
        warnings.append(f"{len(drift)} file(s) differ from the release MANIFEST.sha256 (listed in preflight.json); "
                        "results describe the modified tree")
    if not tb.local and os.geteuid() != 0:
        warnings.append("not running as root (the runbook assumes root on VM-C)")
    env = go_env()
    for tool, need in (("gcc", True), ("pkg-config", True), ("tar", True), ("curl", True), ("java", False), ("make", True)):
        if not have(tool, env):
            (problems if need else warnings).append(f"{tool} not found on VM-C" + ("" if need else " (formal stage needs Java 11+)"))
    if have("pkg-config") and subprocess.run(["pkg-config", "--exists", "sqlite3"]).returncode != 0:
        problems.append("SQLite development files missing on VM-C (apt-get install -y libsqlite3-dev)")
    info["go"] = subprocess.run(["go", "version"], capture_output=True, text=True, env=env).stdout.strip() if have("go", env) else "absent (build installs the pinned toolchain)"
    free = shutil.disk_usage(REPO).free / 2**30
    info["free_disk_gib"] = round(free, 1)
    if free < 5:
        warnings.append(f"only {free:.1f} GiB free on VM-C")
    roles = ("A", "B", "E") if tb.local else ("A", "B", "D", "E")
    for role in roles:
        if tb.local:
            continue
        p = tb.host(role).run("true", check=False, timeout=20)
        if p.returncode != 0:
            (warnings if role == "D" else problems).append(f"SSH root@{tb.ip(role)} (VM-{role}) failed: {p.stderr.strip()[:160]}")
            continue
        lib = tb.host(role).run("ldconfig -p | grep -q libsqlite3.so && echo ok", check=False).stdout.strip()
        if role in ("A", "B", "E") and lib != "ok":
            problems.append(f"VM-{role}: libsqlite3 runtime missing (apt-get install -y libsqlite3-0)")
        off = tb.host(role).clock_offset_s()
        info[f"clock_offset_{role}_s"] = off
        if off is not None and abs(off) > 0.5:
            warnings.append(f"VM-{role} clock differs from VM-C by {off:+.2f} s (enable NTP/chrony on all VMs)")
    # what an interrupted earlier run left running (EviCon's own processes only)
    _sweep_leftovers(tb, [r for r in ("A", "B", "D", "E") if f"(VM-{r})" not in " ".join(problems + warnings)])
    busy = {}
    for role, ports in NEEDED_PORTS.items():
        h = tb.host(role)
        used = h.ports_in_use(ports)
        if used:
            busy[f"VM-{role}"] = {p: h.port_owner(p) for p in used}
    from campaign import Host
    hc = Host("VM-C", "127.0.0.1")
    used_c = hc.ports_in_use(list(range(28200, 28208)))
    if used_c:
        busy["VM-C"] = {p: hc.port_owner(p) for p in used_c}
    info["ports_busy"] = busy
    if busy:
        # A port held by another program would be measured instead of EviCon (063 on VM-B:
        # an older deployment's endpoint on 18081). EviCon never stops other programs: the operator does.
        problems.append("ports needed by EviCon are in use: " + "; ".join(
            f"{vm} port {p}: {owner}" for vm, d in busy.items() for p, owner in d.items())
            + ". Stop those programs (EviCon never stops them itself), then re-run")
    if not tb.local and "VM-A" not in " ".join(problems):
        # The campaign's fault proxies run on VM-C; the authorities on VM-A reach them.
        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind(("0.0.0.0", 28207))
            srv.listen(1)
            p = tb.host("A").run(f"timeout 4 bash -c '</dev/tcp/{tb.ip('C')}/28207' && echo ok", check=False, timeout=20)
            if p.stdout.strip() != "ok":
                problems.append(f"VM-A cannot reach VM-C:28207 (open TCP 28200-28215 on VM-C for {tb.ip('A')}, e.g. ufw allow from {tb.ip('A')} to any port 28200:28215 proto tcp)")
        except OSError as e:
            warnings.append(f"could not test VM-A -> VM-C reachability: {e}")
        finally:
            srv.close()
    info.update({"problems": problems, "warnings": warnings})
    (RUN.path / "preflight.json").write_text(json.dumps(info, indent=2) + "\n")
    for w in warnings:
        RUN.problem("WARN", w)
    for p in problems:
        RUN.log("FAIL " + p)  # errors.log gets them once, through the stage FAIL
    if problems:
        raise StageFail("; ".join(problems))
    return f"OK ({len(warnings)} warning(s))"


def st_build(tb, a):
    env = go_env()
    if not have("go", env):
        sh([sys.executable, "scripts/ensure_go_toolchain.py"], env=env, timeout=1800)
        env = go_env()
    if not have("go", env):
        raise StageFail("Go toolchain not available after scripts/ensure_go_toolchain.py")
    _, ver = sh(["go", "version"], env=env)
    (REPO / "bin").mkdir(exist_ok=True)
    sh(["go", "build", "-trimpath", "-o", "bin/", "./cmd/..."], env=env, timeout=1800)
    bins = {p.name: sha256(p) for p in sorted((REPO / "bin").glob("evicon-*"))}
    if len(bins) < 6:
        raise StageFail(f"expected 6 binaries, got {sorted(bins)}")
    (RUN.path / "build.json").write_text(json.dumps({"go": ver.strip(), "binaries_sha256": bins}, indent=2) + "\n")
    return f"{len(bins)} binaries ({ver.strip()})"


def st_verify(tb, a):
    env = go_env()
    results = {}
    sh(["go", "vet", "./..."], env=env, timeout=1800)
    rc, out = sh(["go", "test", "-count=1", "./..."], env=env, timeout=3600, check=False)
    results["go_test"] = "PASS" if rc == 0 else "FAIL"
    summary_file = RUN.path / "conformance-tests.json"
    rc, out = sh([sys.executable, "conformance/run_tests.py", "--require-go", "--json", str(summary_file)], env=env,
                 timeout=3600, check=False)
    results["conformance"] = "PASS" if rc == 0 else ("FAIL_GO_TESTS_SKIPPED" if rc == 3 else "FAIL")
    if summary_file.exists():
        ct = json.loads(summary_file.read_text())
        results["conformance_detail"] = {k: ct[k] for k in ("tests_run", "failures", "errors", "skipped", "go_skips")}
        for reason in sorted({x["reason"] for x in ct["skipped_detail"]}):
            RUN.problem("WARN", f"EviCon-Probe tests skipped: {reason}")
    hostdir = REPO / "firmware" / "zephyr-cutoff" / "tests" / "host"
    sh(["make", "-s", "-C", str(hostdir), "clean"], check=False)
    rc, out = sh(["make", "-s", "-C", str(hostdir)], check=False, timeout=900)
    if rc != 0 and ("asan" in out.lower() or "sanitizer" in out.lower()):
        RUN.log("sanitizer runtime unavailable: re-running the host tests without ASan/UBSan")
        sh(["make", "-s", "-C", str(hostdir), "clean"], check=False)
        rc, out = sh(["make", "-s", "-C", str(hostdir), "CFLAGS=-std=c11 -Wall -Wextra -Werror -O1 -g"], check=False, timeout=900)
        results["firmware_host"] = "PASS_NO_SANITIZER" if rc == 0 else "FAIL"
    else:
        results["firmware_host"] = "PASS" if rc == 0 else "FAIL"
    sh(["make", "-s", "-C", str(hostdir), "clean"], check=False)
    rc, _ = sh([sys.executable, "-m", "unittest", "evaluation.test_analysis", "evaluation.test_sim_to_real", "evaluation.test_campaign", "evaluation.test_prereg", "evaluation.test_reports", "evaluation.test_pairs_report", "evaluation.test_campaign_real", "evaluation.test_predict", "evaluation.test_campaign_zephyr", "evaluation.test_campaign_zephyr_ext", "evaluation.test_release075", "evaluation.test_release077", "evaluation.test_release078", "evaluation.test_release079"],
               env=env, check=False, timeout=900)
    results["analysis"] = "PASS" if rc == 0 else "FAIL"
    (RUN.path / "verify.json").write_text(json.dumps(results, indent=2) + "\n")
    failed = [k for k, v in results.items() if str(v).startswith("FAIL")]
    if failed:
        raise StageFail(f"failed: {failed}")
    return results


def st_formal(tb, a):
    if not have("java"):
        raise StageFail("java not found (apt-get install -y openjdk-17-jre-headless)")
    out = RUN.path / "formal"
    rc, _ = sh(["bash", "formal/run_tlc.sh", str(out)], timeout=7200, check=False)
    summary = (out / "summary.txt").read_text() if (out / "summary.txt").exists() else ""
    if rc != 0:
        raise StageFail("TLC: unexpected verdict (see formal/summary.txt)")
    return f"{summary.count('EXPECTED')}/8 EXPECTED (5 safe models PASS, 3 negative controls VIOLATION)"


def st_sim(tb, a):
    episodes = 5 if a.quick else 10
    out = RUN.path / "sim"
    runs = [("iot_dosing", "sim-k0"), ("iot_dosing", "sim-k2"), ("iot_dosing", "sim-k3-stop"), ("iot_dosing", "sim-device-cutoff"),
            ("cloud_quota", "E1"), ("cloud_quota", "E2")]
    csvs = []
    for cfg, only in runs:
        d = out / f"{cfg}-{only}"
        sh([str(REPO / "bin" / "evicon-des"), "-config", f"experiments/sim/{cfg}.json", "-episodes", str(episodes), "-only", only,
            "-out", str(d), "-references", "B2_LEDGER,RES_IMMEDIATE,RES_PERIODIC", "-bootstrap", "500"], timeout=5400)
        csvs.append(d / "episodes.csv")
    args = [sys.executable, "evaluation/analyze.py", "--out", str(out / "analysis"), "--bootstrap", "1000",
            "--reference", "B2_LEDGER", "--reference", "RES_IMMEDIATE", "--reference", "RES_PERIODIC"]
    for c in csvs:
        args += ["--csv", str(c)]
    _, rep = sh(args, timeout=1800)
    report = json.loads((out / "analysis" / "report.json").read_text())
    unsafe = [g for g in report["groups_with_safety_violations"] if g[3] not in ("B0_NAIVE_NEW_ID", "B1_DURABLE_RETRY", "B3_TIMEOUT_CANCEL", "B4_UNSAFE_FRONTIER")]
    if unsafe:
        raise StageFail(f"safety violation by a safe policy in simulation: {unsafe}")
    return f"SIMULATION_ONLY, {episodes} episodes/regime; negative controls unsafe as expected; safe policies 0 safety violations"


def st_distribute(tb, a):
    root = tb.cfg["remote_root"]
    bins = sorted((REPO / "bin").glob("evicon-*"))
    if len(bins) < 6:
        raise StageFail("run 'build' first")
    want = {p.name: sha256(p) for p in bins}
    for role in ("A", "B", "E"):
        tb.run(role, f"mkdir -p {root}/bin {root}/experiments/configs {tb.cfg['state_dir']} /run/evicon" if not tb.local
               else f"mkdir -p {root}/bin {root}/experiments/configs {tb.cfg['state_dir']}")
        tb.copy(role, bins, f"{root}/bin")
        tb.copy(role, [REPO / "experiments" / "configs" / "query_costs.json"], f"{root}/experiments/configs")
        got = tb.host(role).sha256([f"{root}/bin/{n}" for n in want])
        bad = [n for n, h in want.items() if got.get(n) != h]
        if bad:
            raise StageFail(f"VM-{role}: checksum mismatch for {bad}")
        if tb.host(role).run(f"{root}/bin/evicon-plant -h >/dev/null 2>&1; echo $?", check=False).stdout.strip() not in ("0", "2"):
            raise StageFail(f"VM-{role}: binaries do not start (missing libsqlite3? glibc older than VM-C's?)")
    return f"{len(bins)} binaries to VM-A/B/E:{root}/bin (sha256 verified)"


def _ready(host, url, role, pid, wd):
    """The process just started must answer (role, release, pid), not another program."""
    from campaign import Testbed
    try:
        Testbed.ready(host, url, role, pid, wd)
    except RuntimeError as e:
        raise StageFail(str(e)) from e


SWEPT_STATE_DIRS = ("controls", "campaigns", "measure", "overhead")  # never "builds" (release 069)


def _sweep_leftovers(tb, roles=("A", "B", "D", "E")):
    """Stops what an interrupted earlier run (power loss of VM-C, kill -9 of the driver) left
    running on the VMs: only EviCon's own processes, i.e. a pid file under the state directory
    whose process still runs in that directory. Safe because one evicon_review.py runs at a time
    (driver lock). Without it, a resumed campaign or a repeated stage found its ports taken by
    its own leftovers (fix of 15/10)."""
    if tb.local:
        return
    for role in roles:
        try:
            # 069: only the driver's own state directories; /var/lib/evicon/builds holds the IoT
            # builds the operator starts with build_iot.sh (hours long), which 068 stopped by mistake
            gone = []
            for sub in SWEPT_STATE_DIRS:
                gone += tb.host(role).stop_leftovers(f"{tb.cfg['state_dir']}/{sub}")
        except Exception as e:  # noqa: BLE001 - an unreachable VM is reported by the checks that follow
            RUN.log(f"(VM-{role}: leftover check failed: {e})")
            continue
        if gone:
            RUN.problem("WARN", f"VM-{role}: stopped {len(gone)} process(es) an interrupted EviCon run left running: "
                                + "; ".join(gone[:6]))


def _probe(cfg_name, tb, a, out):
    src = REPO / "conformance" / "configs" / f"{cfg_name}.json"
    cfg = json.loads(tb.substitute(src.read_text()))
    if a.quick:
        cfg["repetitions"] = 1
    derived = out / "configs" / f"{cfg_name}.json"
    derived.parent.mkdir(parents=True, exist_ok=True)
    derived.write_text(json.dumps(cfg, indent=2) + "\n")
    rc, text = sh([sys.executable, "conformance/run_probe.py", "run", "--config", str(derived), "--out", str(out / "runs")],
                  timeout=5400, check=False, quiet=True)
    start = text.find("{\n")
    summary = json.loads(text[start:]) if start >= 0 else {"error": text[-500:]}
    exp = summary.get("expect", {})
    return {"control": cfg_name, "rc": rc, "pass": rc == 0 and exp.get("pass", False), "capabilities": summary.get("capabilities"),
            "expected": exp.get("expected_capabilities"), "problems": exp.get("problems", summary.get("error")),
            "retention": summary.get("retention"), "declared_unverified": summary.get("declared_unverified"), "out": summary.get("out")}


def st_controls(tb, a):
    out = RUN.path / "controls"
    sd = f"{tb.cfg['state_dir']}/controls"
    root = tb.cfg["remote_root"]
    bin_ = f"{root}/bin" if not tb.local else str(REPO / "bin")
    B, E = tb.host("B"), tb.host("E")
    results = []
    _sweep_leftovers(tb, ("B", "E"))
    try:
        for name, args in POSITIVE:
            wd = f"{sd}/{name}"
            B.stop_all(wd)
            tb.run("B", f"rm -rf {shlex.quote(wd)} && mkdir -p {shlex.quote(wd)}")
            pid = B.spawn("endpoint", [f"{bin_}/evicon-endpoint", *args, "-db", f"{wd}/ep.db",
                                       "-listen", f"{'127.0.0.1' if tb.local else '0.0.0.0'}:{ENDPOINT_PORT}", "-component-max", "1000000"], wd)
            try:
                _ready(B, f"http://{tb.ip('B')}:{ENDPOINT_PORT}/healthz", "endpoint", pid, wd)
                r = _probe(name, tb, a, out)
            finally:
                B.stop_all(wd)
            results.append(r)
            RUN.log(f"CONTROL {name}: {'PASS' if r['pass'] else 'FAIL'} measured={r['capabilities']} expected={r['expected']} {r['problems'] or ''}")
        pwd = f"{sd}/plant"
        E.stop_all(pwd)
        tb.run("E", f"rm -rf {shlex.quote(pwd)} && mkdir -p {shlex.quote(pwd)}")
        # unix socket paths are limited to ~108 bytes: keep them short
        sock = f"/tmp/evc-ctl-{hashlib.sha1(str(RUN.path).encode()).hexdigest()[:8]}.sock" if tb.local else "/run/evicon/controls-plant.sock"
        listen = "127.0.0.1" if tb.local else "0.0.0.0"
        ppid = E.spawn("plant", [f"{bin_}/evicon-plant", "-log", f"{pwd}/plant.jsonl", "-http", f"{listen}:{PLANT_PORT}", "-unix", sock], pwd)
        _ready(E, f"http://{tb.ip('E')}:{PLANT_PORT}/healthz", "plant", ppid, pwd)
        try:
            for name, preset in NEGATIVE:
                wd = f"{sd}/{name}"
                E.stop_all(wd)
                tb.run("E", f"rm -rf {shlex.quote(wd)} && mkdir -p {shlex.quote(wd)}")
                dpid = E.spawn("device", [f"{bin_}/evicon-device", "-count", "1", "-id-prefix", "pump-", "-contract", preset,
                                          "-listen", f"{listen}:{DEVICE_PORT}", "-admin-listen", f"{listen}:{DEVICE_ADMIN_PORT}",
                                          "-state-dir", f"{wd}/dev", "-plant", f"unix:{sock}"], wd)
                try:
                    _ready(E, f"http://{tb.ip('E')}:{DEVICE_PORT}/healthz", "device", dpid, wd)
                    r = _probe(name, tb, a, out)
                finally:
                    E.stop_all(wd)
                results.append(r)
                RUN.log(f"CONTROL {name}: {'PASS' if r['pass'] else 'FAIL'} measured={r['capabilities']} expected={r['expected']} {r['problems'] or ''}")
        finally:
            E.stop_all(pwd)
    finally:
        (out / "controls.json").parent.mkdir(parents=True, exist_ok=True)
        (out / "controls.json").write_text(json.dumps(results, indent=2) + "\n")
        _controls_table(results, out / "controls.md")
    failed = [r["control"] for r in results if not r["pass"]]
    if failed:
        raise StageFail(f"controls failed: {failed}. Stop here: do not measure external systems with a harness that "
                        f"does not reproduce its controls (see controls/controls.md)")
    return f"{len(results)}/{len(results)} controls reproduce their expected contract"


def _controls_table(results, path):
    lines = ["| control | verdict | measured capabilities | expected | retention under pressure | declared, unverified |", "|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['control']} | {'PASS' if r['pass'] else 'FAIL'} | {r['capabilities']} | {r['expected']} | "
                     f"{json.dumps(r.get('retention'))} | {r.get('declared_unverified')} |")
    path.write_text("\n".join(lines) + "\n")


def _localize(cfg: dict) -> dict:
    """--local self-test: every role on this host, 2 devices, short cells. Long
    regimes (experiment A) are scaled down so a cell takes about a minute; the
    bound keeps binding."""
    cfg = json.loads(json.dumps(cfg))
    dur = cfg["duration_s"]
    cfg.update(mode="local", duration_s=40, devices=2, ports={"plant": 38090, "device": 38100, "authority": 39000, "proxy": 38200})
    cfg.pop("remote", None)
    w = cfg["workload"]
    if w["deadline_s"] > 20:
        scale = 40.0 / dur
        w["bound"] = max(w["value_max"], int(round(w["bound"] * scale)))
        w["deadline_s"], w["validity_s"] = 10, 15
        cfg["tail_s"] = 5
        f = cfg.setdefault("faults", {})
        f.update(windows_per_min=6, offline_s=[3.0, 6.0], window_s=[1.0, 2.0], min_gap_s=1.0, delay_ms=3000)
    return cfg


def _remote(cfg: dict, tb: Testbed) -> dict:
    r = cfg["remote"]
    r.update(vm_a=f"{tb.cfg['ssh_user']}@{tb.ip('A')}", vm_e=f"{tb.cfg['ssh_user']}@{tb.ip('E')}", vm_a_ip=tb.ip("A"),
             vm_e_ip=tb.ip("E"), vm_c_ip=tb.ip("C"), bin_dir=f"{tb.cfg['remote_root']}/bin",
             cost_file=f"{tb.cfg['remote_root']}/experiments/configs/query_costs.json",
             state_dir=f"{tb.cfg['state_dir']}/campaigns")
    return cfg


def _cells(cfg):
    """Cells of a campaign; release 074: minus the [contract, policy] pairs in exclude_cells (experiment C2)."""
    excl = {tuple(x) for x in cfg.get("exclude_cells", [])}
    pairs = [(c, p) for c in cfg["contracts"] for p in cfg["policies"] if (c, p) not in excl]
    return cfg["episodes"] * len(pairs)


def _cell_seconds(cfg):
    return cfg["duration_s"] + cfg["workload"]["deadline_s"] + cfg.get("tail_s", 10) + 10


def _run_campaign(cfg: dict, out: Path, tb: Testbed, new: bool = False, max_cells: int = 0,
                  runner: str = "evaluation/campaign.py") -> dict:
    """Runs (or resumes) one campaign under out/<name>/<stamp>; returns its state.

    An unfinished run of the same name is resumed when its config equals the
    derived one; a different config is never mixed into it (FAIL, unless
    new=True starts another run). runner (release 073): evaluation/campaign_real.py
    for a campaign on a real owner (experiment C)."""
    derived = out / "configs" / f"{cfg['name']}.json"
    derived.parent.mkdir(parents=True, exist_ok=True)
    derived.write_text(json.dumps(cfg, indent=2) + "\n")
    total = _cells(cfg)
    RUN.log(f"{cfg['name']}: {total} cells x ~{_cell_seconds(cfg)}s = about {total * _cell_seconds(cfg) / 3600:.1f} h")
    prev = sorted((out / cfg["name"]).glob("2*")) if (out / cfg["name"]).exists() else []
    unfinished = [p for p in prev if (p / "done.jsonl").exists() and not (p / "activation.json").exists()
                  and "finished" not in json.loads((p / "manifest.json").read_text() if (p / "manifest.json").exists() else "{}")]
    cmd = [sys.executable, runner]
    if unfinished and not new:
        old = json.loads((unfinished[-1] / "config.json").read_text())
        if old != cfg:
            raise StageFail(f"an unfinished campaign {unfinished[-1]} has a different config (episodes, contracts, ...); "
                            f"repeat it with the same options to resume, or add --new-campaign to start another one")
        RUN.log(f"resuming {unfinished[-1]}")
        cmd += ["--resume", str(unfinished[-1])]
    else:
        cmd += ["--config", str(derived), "--out", str(out)]
    if tb.local:
        cmd += ["--bin-dir", str(REPO / "bin")]
    if max_cells:
        cmd += ["--max-cells", str(max_cells)]
    rc, _ = sh(cmd, timeout=48 * 3600, check=False)
    runs = sorted((out / cfg["name"]).glob("2*"))
    if not runs:
        raise StageFail(f"{cfg['name']}: no run directory produced (see the log above)")
    run = runs[-1] if not (unfinished and not new) else unfinished[-1]
    done = [json.loads(l) for l in (run / "done.jsonl").read_text().splitlines() if l.strip()] if (run / "done.jsonl").exists() else []
    gate = json.loads((run / "activation.json").read_text()) if (run / "activation.json").exists() else None
    invalid = [d["cell"] for d in done if not str(d.get("status", "")).startswith("VALID")]
    return {"name": cfg["name"], "run": str(run), "rc": rc, "cells_done": len(done), "cells_total": total,
            "complete": len(done) >= total, "invalid": invalid, "gate": gate}


def st_pilot(tb, a):
    out = RUN.path / "runtime"
    gates = []
    _sweep_leftovers(tb, ("A", "E"))
    for name in PILOTS:
        cfg = json.loads(tb.substitute((REPO / "evaluation" / "campaigns" / f"{name}.json").read_text()))
        if a.quick:
            cfg["episodes"] = 1
        if tb.local:
            cfg = _localize(cfg)
            cfg["contracts"] = ["K3S", "K3F"]
        else:
            cfg = _remote(cfg, tb)
        st = _run_campaign(cfg, out, tb, new=a.new_campaign)
        gates.append({"pilot": name, "run": st["run"], "rc": st["rc"], "gate": st["gate"]})
        RUN.log(f"{name}: activation gate {'PASS' if st['gate'] and st['gate']['pass'] else 'FAIL'}")
    (out / "pilots.json").write_text(json.dumps(gates, indent=2) + "\n")
    bad = [g["pilot"] for g in gates if not (g["gate"] and g["gate"]["pass"])]
    if bad:
        raise StageFail(f"activation gate failed for {bad}: EviCon did not act in every gated cell or cells are INVALID. "
                        f"Do not start a confirmatory campaign; inspect runtime/*/activation.json")
    return "activation gate PASS in both pilots (DEV numbers are not results; see analyze)"


# power.py statuses that answer the planning question without a problem (release 068): the
# contract allows no action (nothing to size) or the design would need n > 5000 (equivalence
# cannot be shown; reported as inconclusive). 067 logged them as WARN.
POWER_EXPECTED = {"SIZED", "NO_ACTION_IN_CONTRACT", "INFEASIBLE"}


def _power(csvs, out: Path, label: str, args: list) -> dict:
    """power.py with --json; a result that sized nothing is a WARN in errors.log
    (release 064 wrote an empty table without saying so). Rows that are only
    NO_ACTION_IN_CONTRACT/INFEASIBLE are logged, not warned (release 068)."""
    js = out / f"power_{label}.json"
    cmd = [sys.executable, "evaluation/power.py", "--json", str(js)] + args
    for c in csvs:
        cmd += ["--csv", str(c)]
    rc, text = sh(cmd, check=False, quiet=True)
    (out / f"power_{label}.txt").write_text(text)
    res = json.loads(js.read_text()) if js.exists() else {"status": f"ERROR rc={rc}", "rows": []}
    counts = {}
    for r in res.get("rows", []):
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    status = res.get("status")
    if rc == 0 and status != "SIZED" and counts and set(counts) <= POWER_EXPECTED:
        status = "INCONCLUSIVE" if "INFEASIBLE" in counts else "NOTHING_TO_SIZE"
        RUN.log(f"sample-size planning {label}: {status} {counts} (expected; see {out.name}/power_{label}.txt)")
    elif rc != 0 or status != "SIZED":
        RUN.problem("WARN", f"sample-size planning {label}: {status} {counts or ''} (see {out.name}/power_{label}.txt)")
    return {"status": status, "counts": counts}


def _analyze_runtime(csvs, out: Path, references, margin_pp=2.0, negative_control=False, expected_unsafe=None) -> dict:
    """negative_control (release 072): a campaign whose contracts are all NEG_* runs devices that break
    their own contract on purpose (cmd/evicon-device presets NEG_SEAL, NEG_DEDUP); safety violations are
    then the expected result, and their absence is the failure (the harness would not be sensitive).
    expected_unsafe (release 073, experiment C): the (contract, policy) groups planned as the real-system
    negative control (blind retry on an owner without dedup); a violation anywhere else is a FAIL, no
    violation in a planned group is reported (the plan's C1 decides it)."""
    out.mkdir(parents=True, exist_ok=True)
    args = [sys.executable, "evaluation/analyze.py", "--out", str(out), "--bootstrap", "2000", "--margin-pp", str(margin_pp)]
    for r in references:
        args += ["--reference", r]
    for c in csvs:
        args += ["--csv", str(c)]
    sh(args, timeout=1800, quiet=True)
    rep = json.loads((out / "report.json").read_text())
    if expected_unsafe is not None:
        unsafe = {(g[2], g[3]) for g in rep["groups_with_safety_violations"]}
        other = sorted(unsafe - set(expected_unsafe))
        if other:
            raise StageFail(f"safety violations outside the planned negative control {sorted(expected_unsafe)}: {other}")
        quiet = sorted(set(expected_unsafe) - unsafe)
        if quiet:
            RUN.problem("WARN", f"the real-system negative control {quiet} showed no violation in this run (the plan's C1 "
                                f"decides what that means; see report/pairs/)")
        return rep
    if negative_control:
        if not rep["groups_with_safety_violations"]:
            raise StageFail("negative control: the checker reported NO safety violation for devices that break their contract "
                            "on purpose; the harness is not sensitive and every '0 violations' result must be re-examined")
        return rep
    if rep["groups_with_safety_violations"]:
        raise StageFail(f"safety violations: {rep['groups_with_safety_violations']}")
    return rep


def st_analyze(tb, a):
    out = RUN.path / "analysis"
    csvs = sorted((RUN.path / "runtime").glob("*/2*/runtime_episodes.csv"))
    if not csvs:
        raise StageFail("no pilot CSV: run 'pilot' first")
    rep = _analyze_runtime(csvs, out, ["M1", "RES_IMMEDIATE", "RES_PERIODIC"])
    power = {}
    for c in csvs:
        power[c.parent.parent.name] = _power([c], out, c.parent.parent.name,
                                             ["--policy", "EVICON", "--reference", "RES_IMMEDIATE", "--metric", "clean_fulfilled",
                                              "--delta", "0.05", "--relative", "--comparisons", "6"])
        power[c.parent.parent.name + "-A"] = _power([c], out, c.parent.parent.name + "-A",
                                                    ["--upgrade", "K2:K3F", "--policy", "EVICON", "--delta-pp", "10", "--comparisons", "3"])
    (out / "power_status.json").write_text(json.dumps(power, indent=2) + "\n")
    return (f"{rep['groups']} groups; invalid cells excluded: {len(rep['invalid_cells_excluded'])}; not activated: "
            f"{len(rep['resolver_groups_not_activated'])} (no action in contract, expected: "
            f"{len(rep.get('resolver_groups_no_action_in_contract', []))}); late effects (validity) {rep['late_effects_total']}, "
            f"fulfilled late (deadline) {rep['fulfilled_late_total']}; power: "
            + ", ".join(f"{k}={v['status']}" for k, v in power.items()))


# ------------------------------------------------------------------ campaign command (release 065)
def _sim_twin(cfg_path: str, episodes: int) -> Path:
    """Runs the simulator twin of a campaign once per (config, episodes); reused afterwards."""
    src = (REPO / cfg_path).resolve()
    if not src.is_file():
        raise StageFail(f"sim_twin {cfg_path} not found")
    key = hashlib.sha256(src.read_bytes() + f"/{episodes}".encode()).hexdigest()[:12]
    out = RUN.path / "sim-twin" / f"{src.stem}-{episodes}ep-{key}"
    if (out / "episodes.csv").exists():
        RUN.log(f"simulator twin already run: {out}")
        return out / "episodes.csv"
    sh([str(REPO / "bin" / "evicon-des"), "-config", str(src), "-episodes", str(episodes), "-out", str(out),
        "-references", "M1,B2_LEDGER", "-bootstrap", "500"], timeout=4 * 3600, quiet=True)
    return out / "episodes.csv"


def _check_preregistration(pre: dict) -> str:
    """A confirmatory campaign runs only on its frozen plan (release 067): the preregistration file
    must have the SHA-256 the campaign config names, and every file listed in its frozen-files
    block (analysis scripts, campaign runner, simulator twin) must be unchanged."""
    path = REPO / str(pre.get("path", ""))
    if not path.is_file():
        raise StageFail(f"preregistration {pre.get('path')} not found")
    got = sha256(path)
    if got != pre.get("sha256"):
        raise StageFail(f"preregistration {pre['path']} has SHA-256 {got[:16]}..., the campaign config froze "
                        f"{str(pre.get('sha256'))[:16]}...: a confirmatory campaign runs only on its frozen plan "
                        f"(re-freeze with scripts/freeze_prereg.py and record the deviation)")
    block = re.search(r"```frozen-files\n(.*?)```", path.read_text(), re.S)
    if not block:
        raise StageFail(f"preregistration {pre['path']}: no frozen-files block")
    changed, n = [], 0
    for line in block.group(1).splitlines():
        if not line.strip():
            continue
        h, rel = line.split(None, 1)
        n += 1
        f = REPO / rel.strip()
        if not f.is_file() or sha256(f) != h:
            changed.append(rel.strip())
    if changed:
        raise StageFail(f"files frozen by the preregistration {pre['path']} changed: {changed}")
    return f"preregistration {pre['path']} verified (SHA-256 {got[:16]}..., {n} frozen files unchanged)"


def cmd_campaign(tb, a, path):
    """python3 evicon_review.py campaign <config> [--episodes N] [--max-cells N] [--new-campaign]"""
    src = Path(path)
    if not src.is_file():
        for cand in (REPO / path, REPO / "evaluation" / "campaigns" / path, REPO / "evaluation" / "campaigns" / f"{path}.json"):
            if cand.is_file():
                src = cand
                break
    if not src.is_file():
        raise StageFail(f"campaign config {path} not found (e.g. evaluation/campaigns/expA_dosing.json)")
    cfg = json.loads(tb.substitute(src.read_text()))
    if cfg.get("preregistration"):
        if a.episodes or a.quick:
            raise StageFail(f"{src.name} is a preregistered confirmatory campaign: its episodes are fixed by the plan "
                            f"(do not pass --episodes or --quick)")
        RUN.log(_check_preregistration(cfg["preregistration"]))
    if a.episodes:
        cfg["episodes"] = a.episodes
    elif a.quick:
        cfg["episodes"] = 1
    if cfg.get("real_owner"):
        if cfg["real_owner"].get("system") == "zephyr":
            return _zephyr_owner_campaign(tb, a, src, cfg)
        if cfg["real_owner"].get("system") == "thingsboard":
            return _tb_owner_campaign(tb, a, src, cfg)
        return _real_owner_campaign(tb, a, src, cfg)
    if tb.local:
        cfg = _localize(cfg)
    else:
        if "remote" not in cfg:
            raise StageFail(f"{src}: no 'remote' section (a local-only config cannot run on the testbed)")
        cfg = _remote(cfg, tb)
    bins = {p.name: sha256(p) for p in sorted((REPO / "bin").glob("evicon-*"))}
    if len(bins) < 6:
        raise StageFail("no binaries: run 'python3 evicon_review.py build distribute' first")
    if not tb.local:
        for role in ("A", "E"):
            got = tb.host(role).sha256([f"{tb.cfg['remote_root']}/bin/{n}" for n in bins])
            stale = [n for n, h in bins.items() if got.get(n) != h]
            if stale:
                raise StageFail(f"VM-{role} runs other binaries than bin/ ({stale}): run 'python3 evicon_review.py distribute' first")
    _sweep_leftovers(tb, ("A", "E"))
    runner, baselines = _emulated_runner(cfg)
    baseline = bool(baselines)
    st = _run_campaign(cfg, RUN.path / "campaigns", tb, new=a.new_campaign, max_cells=a.max_cells, runner=runner)
    run = Path(st["run"])
    (run / "driver_status.json").write_text(json.dumps(st, indent=2) + "\n")
    if st["invalid"]:
        RUN.problem("WARN", f"{len(st['invalid'])} INVALID cell(s) in {run.name}: {st['invalid'][:8]} (see cells/*/problems.txt)")
    if not st["complete"]:
        return "PARTIAL", (f"{st['cells_done']}/{st['cells_total']} cells in {run}; continue later with the same command "
                           f"(it resumes); nothing is analysed until every cell is done")
    an = run / "analysis"
    csv_path = run / "runtime_episodes.csv"
    negctl = bool(cfg["contracts"]) and all(str(c).startswith("NEG_") for c in cfg["contracts"])
    # release 077: the baseline's violations are planned (it breaks rule R2 on purpose); a violation of any
    # other policy in the same campaign would still FAIL
    expected = {(c, b) for c in cfg["contracts"] for b in baselines} if baseline else None
    rep = _analyze_runtime([csv_path], an, ["M1", "RES_PERIODIC", "RES_IMMEDIATE"], negative_control=negctl,
                           expected_unsafe=expected)
    sys.path.insert(0, str(REPO / "evaluation"))
    import analyze as _an  # noqa: E402
    pairs = [f"{b}:{u}" for u, b in _an.RUNTIME_UPGRADES.items() if u in cfg["contracts"] and b in cfg["contracts"]]
    power = {}
    if pairs:
        args = []
        for pr in pairs:
            args += ["--upgrade", pr]
        resolvers = [p for p in cfg["policies"] if p in ("EVICON", "RES_PERIODIC", "RES_IMMEDIATE", "RES_QUERY_FIRST", "RES_FIXED")]
        power["A_superiority"] = _power([csv_path], an, "A", args + sum((["--policy", p] for p in resolvers), []) +
                                        ["--delta-pp", "10", "--comparisons", str(max(1, len(pairs) * len(resolvers)))])
        # equivalence is a hypothesis only where the contract already has a forward path (K2 ->
        # K3F, H-A2); 067 also sized it for K0 -> K0F, a pair with a +30 pp effect (WARN noise)
        eq_pairs = [pr for pr in pairs if pr.split(":")[0] in EQUIVALENCE_BASES]
        if eq_pairs:
            power["A_equivalence"] = _power([csv_path], an, "A-equivalence", sum((["--upgrade", pr] for pr in eq_pairs), [])
                                            + ["--policy", "EVICON", "--delta-pp", "2", "--equivalence", "--comparisons", str(len(eq_pairs))])
    if "EVICON" in cfg["policies"] and "RES_PERIODIC" in cfg["policies"]:
        power["B_equivalence"] = _power([csv_path], an, "B-equivalence", ["--policy", "EVICON", "--reference", "RES_PERIODIC",
                                                                         "--delta-pp", "2", "--equivalence", "--comparisons", str(len(cfg["contracts"]))])
    sr = None
    if cfg.get("sim_twin"):
        twin = _sim_twin(cfg["sim_twin"], 20 if (tb.local or a.quick) else 200)
        sh([sys.executable, "evaluation/sim_to_real.py", "--runtime", str(csv_path), "--sim", str(twin), "--out", str(an / "sim_to_real")],
           quiet=True)
        sr = json.loads((an / "sim_to_real" / "sim_to_real.json").read_text())["check"]
        if not sr["pass"] and sr_not_applicable(sr):
            # every effect of this config is null and every sign agrees: the 066 criterion needs a
            # non-null effect, so it cannot pass here by construction (067: expA_confirm_k2)
            sr = dict(sr, applicable=False)
            RUN.log("simulator vs testbed: not applicable to this config alone (all effects null, signs agree); "
                    "see the pooled check")
        elif not sr["pass"]:
            RUN.problem("WARN", f"simulator vs testbed check (SR) not met: {sr} (see analysis/sim_to_real/sim_to_real.md)")
        pooled = _pooled_sim_to_real(cfg["sim_twin"], twin)
        if pooled:
            sr = dict(sr, pooled=pooled)
    (an / "driver_summary.json").write_text(json.dumps({"campaign": st, "power": power, "sim_to_real": sr}, indent=2) + "\n")
    gate = st["gate"]
    if gate is not None and not gate["pass"]:
        raise StageFail(f"activation gate FAIL in {run.name}: failing {gate['failing'][:6]}, invalid {gate['invalid_cells'][:6]} "
                        f"(see activation.json); analysis was written to {an} for diagnosis")
    if baseline:
        found = sorted({(g[2], g[3]) for g in rep["groups_with_safety_violations"]})
        return "PASS", (f"{'/'.join(baselines)} baseline (unsafe by design): {st['cells_done']} cells, {len(st['invalid'])} INVALID; "
                        f"planned violations detected in {found or 'NO group (see the plan: RT-1..3, RR-1, RR-2 decide)'}; {an}")
    if negctl:
        return "PASS", (f"negative control: {st['cells_done']} cells, {len(st['invalid'])} INVALID; the checker DETECTED the planted "
                        f"violations in {rep['groups_with_safety_violations']} (expected); {an}")
    return "PASS", (f"{st['cells_done']} cells, {len(st['invalid'])} INVALID; gate {'PASS' if gate and gate['pass'] else '-'}; "
                    f"safety violations 0; late effects {rep['late_effects_total']}; power "
                    + ", ".join(f"{k}={v['status']}" for k, v in power.items())
                    + (f"; sim-to-real {_sr_word(sr)}" if sr is not None else "") + f"; {an}")


EQUIVALENCE_BASES = ("K2",)


def _expected_unsafe(cfg: dict) -> set:
    """Release 073: (contract, policy) groups planned to violate safety on a real owner (a pseudo-policy with
    the T4 retry guard off on an owner call shape without dedup; 077: ThingsBoard's RPC, and the plain
    firmware with an at-least-once binding under the offline cut). Release 079: the definition is
    evaluation/safety_total.py's planned_unsafe, so the report's safety total classifies the same cells."""
    from safety_total import planned_unsafe
    return planned_unsafe(cfg)


def _real_owner_campaign(tb, a, src: Path, cfg: dict):
    """Release 073, experiment C: EviCon authorities (VM-A) against MinIO (VM-B) through one S3 owner
    gateway per device (VM-C), run by evaluation/campaign_real.py. MinIO runs for the campaign in
    EviCon's own compose project, as for `measure minio`, and is brought down afterwards (also on
    failure). Its credentials come from VM-C's environment (MINIO_ROOT_USER, MINIO_ROOT_PASSWORD) or
    are random for this campaign; they reach VM-B on ssh's stdin and the runner and its gateways
    through their environment, never a file or a log."""
    if tb.local:
        raise StageFail(f"{src.name} runs on the testbed only (MinIO runs in docker on VM-B); there is no --local variant. "
                        f"evaluation/test_campaign_real.py runs it locally against a fake S3 (make verify)")
    if "remote" not in cfg:
        raise StageFail(f"{src}: no 'remote' section (a local-only config cannot run on the testbed)")
    cfg = _remote(cfg, tb)
    cfg["remote"].update(vm_b=f"{tb.cfg['ssh_user']}@{tb.ip('B')}", vm_b_ip=tb.ip("B"))
    exe = REPO / "bin" / "evicon-authority"
    if not exe.exists():
        raise StageFail("no binaries: run 'python3 evicon_review.py build distribute' first")
    got = tb.host("A").sha256([f"{tb.cfg['remote_root']}/bin/evicon-authority"])
    if got.get("evicon-authority") != sha256(exe):
        raise StageFail("VM-A runs another evicon-authority than bin/: run 'python3 evicon_review.py distribute' first")
    from campaign import Host
    gone = Host("VM-C", "127.0.0.1").stop_leftovers(f"{tb.cfg['state_dir']}/campaigns")  # gateways of an interrupted run
    if gone:
        RUN.problem("WARN", f"VM-C: stopped {len(gone)} gateway process(es) an interrupted EviCon run left running: " + "; ".join(gone[:6]))
    m = _Measure(tb, a, "minio")
    m.out = RUN.path / "campaigns" / "owner-minio"
    m.sd = f"{tb.cfg['state_dir']}/campaigns/owner-minio"
    m.out.mkdir(parents=True, exist_ok=True)
    m.checks()
    try:
        m.deploy()
        m.start_broker()
        cfg["real_owner"]["s3_url"] = f"http://{tb.ip('B')}:{m.P['minio']}"
        for k in (cfg["real_owner"].get("access_key_env", "MINIO_ROOT_USER"), cfg["real_owner"].get("secret_key_env", "MINIO_ROOT_PASSWORD")):
            if not os.environ.get(k):
                raise StageFail(f"{k} is not set after MinIO started (the runner reads the credentials from the environment)")
        st = _run_campaign(cfg, RUN.path / "campaigns", tb, new=a.new_campaign, max_cells=a.max_cells, runner="evaluation/campaign_real.py")
    finally:
        m.cleanup()
        (m.out / "owner.json").write_text(json.dumps(m.facts, indent=2, default=str) + "\n")
    run = Path(st["run"])
    shutil.copy2(m.out / "owner.json", run / "owner.json")
    (run / "driver_status.json").write_text(json.dumps(st, indent=2) + "\n")
    if st["invalid"]:
        RUN.problem("WARN", f"{len(st['invalid'])} INVALID cell(s) in {run.name}: {st['invalid'][:8]} (see cells/*/problems.txt)")
    if not st["complete"]:
        return "PARTIAL", (f"{st['cells_done']}/{st['cells_total']} cells in {run}; continue later with the same command "
                           f"(it resumes; MinIO is started again); nothing is analysed until every cell is done")
    an = run / "analysis"
    expected = _expected_unsafe(cfg)
    rep = _analyze_runtime([run / "runtime_episodes.csv"], an, ["M1"], expected_unsafe=expected)
    late = rep.get("late_effects_total", 0)
    with open(run / "runtime_episodes.csv", newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if r["cell_status"].startswith("VALID")]
    groups = {}
    for r in rows:
        g = groups.setdefault(f"{r['contract']}/{r['policy']}", {"cells": 0, "clean_pct": 0.0, "duplicate_effects": 0, "bound_violations": 0,
                                                                 "stops": 0, "queries": 0})
        g["cells"] += 1
        g["clean_pct"] += 100.0 * int(r["clean_fulfilled"]) / max(1, int(r["offered"]))
        for k in ("duplicate_effects", "bound_violations", "stops", "queries"):
            g[k] += int(r[k] or 0)
    for g in groups.values():
        g["clean_pct"] = round(g["clean_pct"] / max(1, g["cells"]), 1)
    (an / "driver_summary.json").write_text(json.dumps({"campaign": st, "groups": groups, "expected_unsafe": sorted(expected)}, indent=2) + "\n")
    return "PASS", (f"{st['cells_done']} cells on MinIO, {len(st['invalid'])} INVALID; safety violations only where planned "
                    f"({', '.join('/'.join(x) for x in sorted(expected)) or 'none'}); "
                    + "; ".join(f"{k}: clean {v['clean_pct']} %, dup {v['duplicate_effects']}" for k, v in sorted(groups.items()))
                    + f"; late effects {late}; {an}")


ZEPHYR_MODE_STACK = {"PLAIN": ("zephyr-plain", "zephyr-mqtt-plain", set()), "CUTOFF": ("zephyr", "zephyr-mqtt", {"D_h", "Q_A", "F"}),
                     "STOP": ("zephyr-stop", "zephyr-mqtt-stop", {"D_h", "Q_A", "Q_S", "F"})}


def _measured_zephyr_contracts(cfg: dict, fw_sha: str) -> dict:
    """Release 074, experiment C2: for each contract of the config, the contract this review run measured
    for the firmware mode (newest `measure <stack>` run), checked against the plan before any cell runs:
    MEASURED, the planned capabilities, the measured fault model, and the very firmware binary the
    campaign will run. Returns {contract name: (contract.json path, contract)}."""
    found, bad = {}, []
    for name in cfg["contracts"]:
        mode = cfg["real_owner"]["contracts"][name]["mode"]
        stack, conf, caps = ZEPHYR_MODE_STACK[mode]
        runs = sorted((RUN.path / "measure" / stack / "runs" / conf).glob("2*/contract.json"))
        if not runs:
            bad.append(f"{name}: no measurement of {stack} in this review run (python3 evicon_review.py measure {stack})")
            continue
        c = json.loads(runs[-1].read_text())
        m = json.loads((RUN.path / "measure" / stack / "measure.json").read_text()) if (RUN.path / "measure" / stack / "measure.json").exists() else {}
        msha = ((m.get("facts") or m).get("firmware") or {}).get("sha256")
        if c.get("source") != "MEASURED" or set(c.get("capabilities", [])) != caps:
            bad.append(f"{name}: {stack} measured {c.get('source')} {sorted(c.get('capabilities', []))}, the plan expects MEASURED {sorted(caps)}")
        elif "owner up" not in str(c.get("fault_model", "")):
            bad.append(f"{name}: measured fault model {c.get('fault_model')!r} is not the planned one")
        elif msha and msha != fw_sha:
            bad.append(f"{name}: {stack} was measured on firmware {msha[:16]}, the build to run is {fw_sha[:16]} (measure again)")
        else:
            found[name] = (runs[-1], c)
    if bad:
        raise StageFail("experiment C2 runs only on the contracts its plan expects, measured on the same firmware in this review run: "
                        + "; ".join(bad))
    return found


RELEASE_BASELINES = ("RELEASE_ON_TIMEOUT", "RELEASE_ON_READ")  # release 077, unsafe by design


def _emulated_runner(cfg: dict):
    """Release 077: the RELEASE_ON_TIMEOUT and RELEASE_ON_READ baselines and the validity cut of the retry
    guard (authority.resend_until) (PREREG_review_077.md) run on evaluation/campaign_ext077.py, which imports
    the frozen campaign.py unchanged and adds them; every other emulated-owner campaign runs on campaign.py.
    Returns (runner, baselines)."""
    baselines = sorted(set(cfg["policies"]) & set(RELEASE_BASELINES))
    ext = bool(baselines) or bool((cfg.get("authority") or {}).get("resend_until"))
    return ("evaluation/campaign_ext077.py" if ext else "evaluation/campaign.py"), baselines


def _zephyr_runner(cfg: dict) -> str:
    """Release 075: experiment C3 (restricted contracts, the direct channel) runs on
    evaluation/campaign_zephyr_ext.py, which imports the frozen C2 runner; C2 runs on that runner.
    Release 077: experiment Q (the delivery binding of the commands, the offline cut) runs on
    evaluation/campaign_zephyr_ext077.py, which imports the C3 runner."""
    specs = cfg["real_owner"]["contracts"].values()
    if cfg["real_owner"].get("offline_cut") or any("binding" in s for s in specs):
        return "evaluation/campaign_zephyr_ext077.py"
    ext = any(s.get("restrict_to") or s.get("channel") for s in specs)
    return "evaluation/campaign_zephyr_ext.py" if ext else "evaluation/campaign_zephyr.py"


TB_CAMPAIGN_CONF = ("thingsboard", "thingsboard-rpc-r0", {"Q_A", "T"})  # release 077, experiment TB
MCU_FWD_BROKER = "10.0.2.100"  # release 077: the broker's guest address when QEMU forwards it (a loopback VM-B)


def _tb_owner_campaign(tb, a, src: Path, cfg: dict):
    """Release 077, experiment TB: EviCon authorities (VM-A) against ThingsBoard CE's persistent server-side RPC
    (VM-B, EviCon's compose project, installed and provisioned as for `measure thingsboard`: demo passwords
    replaced by random ones kept in this process's environment) through one ThingsBoard owner gateway per device
    (VM-C) and one reference MQTT device per domain (VM-E), run by evaluation/campaign_tb.py. The contract is the
    one `measure thingsboard` produced in this review run for the gateway's call shape (thingsboard-rpc-r0)."""
    if tb.local:
        raise StageFail(f"{src.name} runs on the testbed only (ThingsBoard in docker on VM-B); conformance/tests/test_tb_owner_gateway.py "
                        f"tests the gateway locally (make verify)")
    if "remote" not in cfg:
        raise StageFail(f"{src}: no 'remote' section (a local-only config cannot run on the testbed)")
    cfg = _remote(cfg, tb)
    for role, name in (("A", "evicon-authority"), ("E", "evicon-plant")):
        got = tb.host(role).sha256([f"{tb.cfg['remote_root']}/bin/{name}"])
        if got.get(name) != sha256(REPO / "bin" / name):
            raise StageFail(f"VM-{role} runs another {name} than bin/: run 'python3 evicon_review.py distribute' first")
    stack, conf, caps = TB_CAMPAIGN_CONF
    runs = sorted((RUN.path / "measure" / stack / "runs" / conf).glob("2*/contract.json"))
    if not runs:
        raise StageFail(f"experiment TB needs the contract of its call shape measured in this review run: python3 evicon_review.py measure {stack}")
    c = json.loads(runs[-1].read_text())
    if c.get("source") != "MEASURED" or set(c.get("capabilities", [])) != caps or "owner up" not in str(c.get("fault_model", "")):
        raise StageFail(f"experiment TB runs only on the contract its plan expects: {conf} measured {c.get('source')} "
                        f"{sorted(c.get('capabilities', []))} ({c.get('fault_model')}), the plan expects MEASURED {sorted(caps)}, owner up")
    from campaign import Host
    gone = Host("VM-C", "127.0.0.1").stop_leftovers(f"{tb.cfg['state_dir']}/campaigns")
    if gone:
        RUN.problem("WARN", f"VM-C: stopped {len(gone)} gateway process(es) an interrupted EviCon run left running: " + "; ".join(gone[:6]))
    _sweep_leftovers(tb, ("A", "E"))
    m = _Measure(tb, a, "thingsboard")
    m.out = RUN.path / "campaigns" / "owner-thingsboard"
    m.sd = f"{tb.cfg['state_dir']}/campaigns/owner-thingsboard"
    m.out.mkdir(parents=True, exist_ok=True)
    cdir = m.out / "contracts"
    cdir.mkdir(parents=True, exist_ok=True)
    for name in cfg["contracts"]:
        dst = cdir / f"{name}.json"
        shutil.copy2(runs[-1], dst)
        cfg["real_owner"]["contracts"][name].update(contract_file=str(dst), measured_in=str(runs[-1].relative_to(RUN.path)))
    try:
        m.deploy()
        m.start_broker()  # compose up, install with demo data, then the demo passwords replaced (_tb_provision)
        cfg["real_owner"]["tb_url"] = f"http://{tb.ip('B')}:{m.P['tb_http']}"
        cfg["real_owner"]["tb_mqtt"] = f"{tb.ip('B')}:{m.P['tb_mqtt']}"
        m.facts["contract"] = {"file": str(runs[-1].relative_to(RUN.path)), "sha256": sha256(runs[-1]), "capabilities": c.get("capabilities")}
        st = _run_campaign(cfg, RUN.path / "campaigns", tb, new=a.new_campaign, max_cells=a.max_cells, runner="evaluation/campaign_tb.py")
    finally:
        m.cleanup()
        (m.out / "owner.json").write_text(json.dumps(m.facts, indent=2, default=str) + "\n")
    run = Path(st["run"])
    shutil.copy2(m.out / "owner.json", run / "owner.json")
    shutil.copytree(cdir, run / "contracts", dirs_exist_ok=True)
    (run / "driver_status.json").write_text(json.dumps(st, indent=2) + "\n")
    if st["invalid"]:
        RUN.problem("WARN", f"{len(st['invalid'])} INVALID cell(s) in {run.name}: {st['invalid'][:8]} (see cells/*/problems.txt)")
    if not st["complete"]:
        return "PARTIAL", (f"{st['cells_done']}/{st['cells_total']} cells in {run}; continue later with the same command "
                           f"(it resumes; ThingsBoard is installed again); nothing is analysed until every cell is done")
    an = run / "analysis"
    expected = _expected_unsafe(cfg)
    rep = _analyze_runtime([run / "runtime_episodes.csv"], an, ["M1"], expected_unsafe=expected)
    with open(run / "runtime_episodes.csv", newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if r["cell_status"].startswith("VALID")]
    if not rows:
        raise StageFail(f"no VALID cell on ThingsBoard in {run} ({len(st['invalid'])} INVALID: see cells/*/problems.txt); "
                        f"send the bundle (python3 evicon_review.py bundle)")
    groups = {}
    for r in rows:
        g = groups.setdefault(f"{r['contract']}/{r['policy']}", {"cells": 0, "clean_pct": 0.0, "duplicate_effects": 0, "late_effects": 0,
                                                                 "queries": 0, "unresolved_at_end": 0})
        g["cells"] += 1
        g["clean_pct"] += 100.0 * int(r["clean_fulfilled"]) / max(1, int(r["offered"]))
        for k in ("duplicate_effects", "late_effects", "queries", "unresolved_at_end"):
            g[k] += int(r[k] or 0)
    for g in groups.values():
        g["clean_pct"] = round(g["clean_pct"] / max(1, g["cells"]), 1)
    (an / "driver_summary.json").write_text(json.dumps({"campaign": st, "groups": groups, "expected_unsafe": sorted(expected)}, indent=2) + "\n")
    return "PASS", (f"{st['cells_done']} cells on ThingsBoard, {len(st['invalid'])} INVALID; safety violations only where planned "
                    f"({', '.join('/'.join(x) for x in sorted(expected)) or 'none'}); "
                    + "; ".join(f"{k}: clean {v['clean_pct']} %, dup {v['duplicate_effects']}, late {v['late_effects']}, "
                                f"unresolved {v['unresolved_at_end']}" for k, v in sorted(groups.items())) + f"; {an}")


def _place_zephyr_contracts(cfg: dict, contracts: dict, cdir: Path) -> dict:
    """Copies each contract measured in this review run into cdir (the campaign's contracts) and points
    the config at it. Release 075: a contract with restrict_to gets the restriction of the measured
    contract of its mode (campaign_zephyr_ext.restrict_contract: a weaker contract, every kept capability
    with its measured provenance, provenance.restricted_from naming the measured file's SHA-256); the
    measured file it restricts is copied as measured-<mode>.json. Returns the restrictions made."""
    from campaign_zephyr_ext import restrict_contract
    made = {}
    for name, (path, c) in contracts.items():
        spec = cfg["real_owner"]["contracts"][name]
        spec["measured_in"] = str(path.relative_to(RUN.path))
        if spec.get("restrict_to"):
            parent = cdir / f"measured-{spec['mode'].lower()}.json"
            shutil.copy2(path, parent)
            r = restrict_contract(c, sha256(parent), spec["restrict_to"])
            dst = cdir / f"{name}.json"
            dst.write_text(json.dumps(r, indent=2, sort_keys=True) + "\n")
            spec["contract_file"], spec["restricted_from"] = str(dst), str(parent)
            made[name] = {"capabilities": r["capabilities"], "restricted_from": str(parent.relative_to(RUN.path)), "sha256": sha256(dst)}
            RUN.log(f"{name}: {r['id']} {r['capabilities']}, the restriction of {c.get('id')} {sorted(c.get('capabilities', []))} "
                    f"measured in {path.relative_to(RUN.path)}")
        else:
            dst = cdir / f"{name}.json"
            shutil.copy2(path, dst)
            spec["contract_file"] = str(dst)
            RUN.log(f"{name}: measured contract {c.get('id')} {sorted(c.get('capabilities', []))} from {path.relative_to(RUN.path)}")
    return made


def _zephyr_owner_campaign(tb, a, src: Path, cfg: dict):
    """Release 074, experiment C2: EviCon authorities (VM-A) against the Zephyr EviCon firmware (VM-E, one
    native_sim process per device, in the evidence mode of the cell's contract) behind Mosquitto (VM-B),
    through one MQTT owner gateway per device (VM-C), run by evaluation/campaign_zephyr.py. The contracts
    are those `measure zephyr | zephyr-stop | zephyr-plain` produced in this review run for the same
    firmware binary; Mosquitto runs for the campaign in EviCon's own compose project, as for the
    measurement, and is brought down afterwards (also on failure)."""
    if tb.local:
        raise StageFail(f"{src.name} runs on the testbed only (Mosquitto in docker on VM-B, the firmware built by build_iot.sh); "
                        f"conformance/tests/test_mqtt_owner_gateway.py and evaluation/test_campaign_zephyr.py test the gateway and the runner locally (make verify)")
    if "remote" not in cfg:
        raise StageFail(f"{src}: no 'remote' section (a local-only config cannot run on the testbed)")
    cfg = _remote(cfg, tb)
    cfg["remote"].update(vm_b=f"{tb.cfg['ssh_user']}@{tb.ip('B')}", vm_b_ip=tb.ip("B"))
    exe = REPO / "bin" / "evicon-authority"
    if not exe.exists():
        raise StageFail("no binaries: run 'python3 evicon_review.py build distribute' first")
    for role, name in (("A", "evicon-authority"), ("E", "evicon-plant")):
        got = tb.host(role).sha256([f"{tb.cfg['remote_root']}/bin/{name}"])
        if got.get(name) != sha256(REPO / "bin" / name):
            raise StageFail(f"VM-{role} runs another {name} than bin/: run 'python3 evicon_review.py distribute' first")
    from campaign import Host
    gone = Host("VM-C", "127.0.0.1").stop_leftovers(f"{tb.cfg['state_dir']}/campaigns")  # gateways of an interrupted run
    if gone:
        RUN.problem("WARN", f"VM-C: stopped {len(gone)} gateway process(es) an interrupted EviCon run left running: " + "; ".join(gone[:6]))
    _sweep_leftovers(tb, ("A", "E"))
    m = _Measure(tb, a, "zephyr")
    m.out = RUN.path / "campaigns" / "owner-zephyr"
    m.sd = f"{tb.cfg['state_dir']}/campaigns/owner-zephyr"
    m.out.mkdir(parents=True, exist_ok=True)
    m.checks()
    try:
        m.deploy()
        fw = m.firmware_to_e(m.out / "firmware")
        contracts = _measured_zephyr_contracts(cfg, m.facts["firmware"]["sha256"])
        cdir = m.out / "contracts"
        cdir.mkdir(parents=True, exist_ok=True)
        restricted = _place_zephyr_contracts(cfg, contracts, cdir)
        m.facts["contracts"] = {n: {"file": str(p.relative_to(RUN.path)), "sha256": sha256(p), "capabilities": c.get("capabilities")}
                                for n, (p, c) in contracts.items()}
        if restricted:
            m.facts["restricted_contracts"] = restricted
        m.start_broker()
        cfg["real_owner"]["broker"] = f"{tb.ip('B')}:{m.P['broker']}"
        cfg["real_owner"]["firmware"] = fw
        st = _run_campaign(cfg, RUN.path / "campaigns", tb, new=a.new_campaign, max_cells=a.max_cells, runner=_zephyr_runner(cfg))
    finally:
        m.cleanup()
        (m.out / "owner.json").write_text(json.dumps(m.facts, indent=2, default=str) + "\n")
    run = Path(st["run"])
    shutil.copy2(m.out / "owner.json", run / "owner.json")
    shutil.copytree(m.out / "contracts", run / "contracts", dirs_exist_ok=True)
    (run / "driver_status.json").write_text(json.dumps(st, indent=2) + "\n")
    if st["invalid"]:
        RUN.problem("WARN", f"{len(st['invalid'])} INVALID cell(s) in {run.name}: {st['invalid'][:8]} (see cells/*/problems.txt)")
    if not st["complete"]:
        return "PARTIAL", (f"{st['cells_done']}/{st['cells_total']} cells in {run}; continue later with the same command "
                           f"(it resumes; Mosquitto is started again); nothing is analysed until every cell is done")
    an = run / "analysis"
    expected = _expected_unsafe(cfg)
    rep = _analyze_runtime([run / "runtime_episodes.csv"], an, ["M1"], expected_unsafe=expected)
    late = rep.get("late_effects_total", 0)
    with open(run / "runtime_episodes.csv", newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if r["cell_status"].startswith("VALID")]
    groups = {}
    for r in rows:
        g = groups.setdefault(f"{r['contract']}/{r['policy']}", {"cells": 0, "clean_pct": 0.0, "duplicate_effects": 0, "bound_violations": 0,
                                                                 "stops": 0, "fences": 0, "queries": 0})
        g["cells"] += 1
        g["clean_pct"] += 100.0 * int(r["clean_fulfilled"]) / max(1, int(r["offered"]))
        for k in ("duplicate_effects", "bound_violations", "stops", "fences", "queries"):
            g[k] += int(r[k] or 0)
    for g in groups.values():
        g["clean_pct"] = round(g["clean_pct"] / max(1, g["cells"]), 1)
    (an / "driver_summary.json").write_text(json.dumps({"campaign": st, "groups": groups, "expected_unsafe": sorted(expected)}, indent=2) + "\n")
    return "PASS", (f"{st['cells_done']} cells on Zephyr/Mosquitto, {len(st['invalid'])} INVALID; safety violations only where planned "
                    f"({', '.join('/'.join(x) for x in sorted(expected)) or 'none'}); "
                    + "; ".join(f"{k}: clean {v['clean_pct']} %, dup {v['duplicate_effects']}, fences {v['fences']}, stops {v['stops']}"
                                for k, v in sorted(groups.items()))
                    + f"; late effects {late}; {an}")


def sr_not_applicable(sr: dict) -> bool:
    """The 066 SR criterion needs >= 1 non-null effect; a config whose effects are all null (and
    agree in sign) cannot pass it alone (release 068)."""
    return bool(sr.get("no_sign_disagrees") and sr.get("nonnull_agreeing", 0) == 0 and not sr.get("undetermined_testbed"))


def _sr_word(sr: dict) -> str:
    word = "PASS" if sr.get("pass") else ("n/a (all effects null)" if sr.get("applicable") is False else "not met")
    if sr.get("pooled"):
        word += f", pooled over {sr['pooled']['configs']} configs {'PASS' if sr['pooled']['pass'] else 'not met'}"
    return word


def _pooled_sim_to_real(twin_cfg: str, twin_csv: Path):
    """SR over every complete campaign of this review run that uses the same simulator twin (e.g.
    expA_confirm_k0 + expA_confirm_k2: contract ranking over K0, K0F, K2, K3F). Written to
    campaigns/sim_to_real_pooled; reported separately from the per-config check (release 068)."""
    csvs, names = [], []
    for c in sorted((RUN.path / "campaigns").glob("*/2*/runtime_episodes.csv")):
        try:
            cfg = json.loads((c.parent / "config.json").read_text())
        except (OSError, ValueError):
            continue
        if cfg.get("sim_twin") == twin_cfg and (c.parent / "activation.json").exists():
            csvs.append(c)
            names.append(cfg.get("name", c.parent.parent.name))
    if len(set(names)) < 2:
        return None
    out = RUN.path / "campaigns" / "sim_to_real_pooled"
    args = [sys.executable, "evaluation/sim_to_real.py", "--sim", str(twin_csv), "--out", str(out)]
    for c in csvs:
        args += ["--runtime", str(c)]
    rc, _ = sh(args, quiet=True, check=False)
    if rc != 0 or not (out / "sim_to_real.json").exists():
        RUN.problem("WARN", f"pooled simulator vs testbed check failed to run (rc={rc})")
        return None
    chk = json.loads((out / "sim_to_real.json").read_text())["check"]
    (out / "README.txt").write_text("Pooled over campaigns: " + ", ".join(sorted(set(names))) + "\n"
                                    "Each campaign's own check is in its analysis/sim_to_real/. The pooled check is "
                                    "reported in addition (it was not part of the 067 preregistration).\n")
    RUN.log(f"simulator vs testbed, pooled over {sorted(set(names))}: {'PASS' if chk['pass'] else 'not met'} -> {out}")
    return {"configs": len(set(names)), "names": sorted(set(names)), "pass": chk["pass"], "dir": str(out)}


# ------------------------------------------------------------------ measure command (release 065)
# C2: measured evidence contracts of real systems. Each stack starts its own
# truth plant and reference device/worker, brings the system up (Mosquitto on
# VM-B through docker compose; a Temporal dev server on VM-D, fix of 15/10),
# runs EviCon-Probe from VM-C through an in-process fault proxy (no Toxiproxy),
# stops everything it started (pid files, its own compose project) and fetches
# the logs.
# 7243, not 7233: 7233 on VM-D belongs to an older deployment's Temporal (docker),
# which did not answer from VM-C in the 065 setup (15/10). EviCon starts its own server.
MEASURE_PORTS = {"plant": PLANT_PORT, "mqtt_admin": 18201, "coap": 5683, "coap_health": 18211, "worker_health": 19100,
                 "broker": 1883, "temporal": 7243, "proxy_mqtt": 21883, "proxy_coap": 25683, "proxy_temporal": 28210,
                 # release 067: EMQX, NATS JetStream and MinIO on VM-B (proxies listen on VM-C's loopback)
                 "emqx": 1884, "emqx_dashboard": 18083, "nats": 4222, "nats_monitor": 8222, "minio": 9000, "minio_console": 9001,
                 "proxy_emqx": 21884, "proxy_nats": 24222, "proxy_minio": 29000,
                 # release 068: DBOS (PostgreSQL on VM-B, app on VM-A), k3s API server and ThingsBoard on VM-B.
                 # Host ports chosen away from the defaults (6443, 8080, 1883) that other programs on VM-B
                 # may hold; 28211 is inside the 28200-28215 range VM-A already reaches on VM-C.
                 "dbos_pg": 5433, "dbos_app": 18300, "proxy_dbos": 28211,
                 "k3s_api": 16443, "proxy_k3s": 26443,
                 "tb_http": 18088, "tb_mqtt": 1885, "tb_admin": 18202, "proxy_tb": 28088,
                 # the Zephyr firmware writes its effects to the plant's line protocol on VM-E's loopback
                 "zephyr_plant_tcp": 18092}
# --local self-test: other ports, so a real broker/Temporal on this host is never touched
LOCAL_MEASURE_PORTS = {"plant": 38091, "mqtt_admin": 38201, "coap": 35683, "coap_health": 38211, "worker_health": 39100,
                       "broker": 31883, "temporal": 37233, "proxy_mqtt": 31884, "proxy_coap": 35684, "proxy_temporal": 38210}
# Stacks whose owner runs in EviCon's compose project on VM-B. "ready": how the driver knows the
# system serves (an open port is not enough: docker-proxy accepts while the service starts).
COMPOSE_OWNERS = {
    # images: the image variable(s) of the compose file and their pinned default; a value set in
    # VM-C's environment overrides the default and is passed explicitly to VM-B (release 068;
    # before, an override only worked in VM-B's own environment).
    "mosquitto": {"file": "docker-compose.yml", "profile": "mosquitto", "ports": ["broker"], "ready": "mqtt", "port": "broker",
                  "images": {"MOSQUITTO_IMAGE": "eclipse-mosquitto:2.0.20"}},
    "emqx": {"file": "docker-compose.yml", "profile": "emqx", "ports": ["emqx", "emqx_dashboard"], "ready": "mqtt", "port": "emqx",
             "images": {"EMQX_IMAGE": "emqx/emqx:5.8.6"}, "ready_s": 180},
    "nats": {"file": "docker-compose.yml", "profile": "nats", "ports": ["nats", "nats_monitor"], "ready": "nats", "port": "nats",
             "images": {"NATS_IMAGE": "nats:2.10.22"}},
    # release 068: MinIO publishes no images any more (removed from Docker Hub in September 2026);
    # the default image is built on VM-B from the official source of the same release
    # (conformance/compose/vm-b/minio/Dockerfile) when it is missing.
    "minio": {"file": "docker-compose.minio.yml", "profile": None, "ports": ["minio", "minio_console"], "ready": "http:/minio/health/live",
              "port": "minio", "images": {"MINIO_IMAGE": "evicon/minio-src:RELEASE.2025-04-22T22-12-26Z"},
              "build": {"MINIO_IMAGE": "minio"}, "secrets": ["MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD"]},
    "dbos": {"file": "docker-compose.dbos.yml", "profile": None, "ports": ["dbos_pg"], "ready": "pg", "port": "dbos_pg",
             "images": {"DBOS_PG_IMAGE": "postgres:16.10"}, "secrets": ["DBOS_PG_PASSWORD"], "ready_s": 120},
    "k3s": {"file": "docker-compose.k3s.yml", "profile": None, "ports": ["k3s_api"], "ready": "k8s", "port": "k3s_api",
            "images": {"K3S_IMAGE": "rancher/k3s:v1.36.4-k3s1"}, "secrets": ["K3S_PROBE_TOKEN"], "env": {"K3S_TLS_SAN": "{B}"},
            "ready_s": 240},
    "thingsboard": {"file": "docker-compose.thingsboard.yml", "profile": None, "ports": ["tb_http", "tb_mqtt"], "ready": "tb", "port": "tb_http",
                    "images": {"TB_IMAGE": "thingsboard/tb-node:4.3.1.6", "TB_PG_IMAGE": "postgres:16.10"}, "secrets": ["TB_PG_PASSWORD"],
                    # schema + demo tenant, once per measurement (the volumes are removed afterwards)
                    "init": [("run --rm -e INSTALL_TB=true -e LOAD_DEMO=true thingsboard-ce", 2400)], "ready_s": 900},
}
COMPOSE_PROJECT = "evicon-probe-owner"
STACKS = {
    "mosquitto": {"configs": ["mosquitto-rpc"], "py": {"C": ["paho.mqtt.client"], "E": ["paho.mqtt.client"]},
                  "what": "Eclipse Mosquitto 2.0.20 (docker, VM-B) + reference MQTT 5 device (VM-E)"},
    "coap": {"configs": ["coap-message", "coap-application"], "py": {"E": ["aiocoap"]},
             "what": "aiocoap 0.4.17 reference CoAP actuator (VM-E), message-level and application-level identity"},
    "temporal": {"configs": ["temporal-activity"], "py": {"C": ["temporalio"], "A": ["temporalio"]},
                 "what": "Temporal server ({server}) + activity worker (VM-A) whose effect goes through VM-C to the plant (VM-E)"},
    # release 067
    "emqx": {"configs": ["emqx-rpc"], "py": {"C": ["paho.mqtt.client"], "E": ["paho.mqtt.client"]},
             "what": "EMQX 5.8.6 (docker, VM-B) + reference MQTT 5 device (VM-E)"},
    "nats": {"configs": ["nats-jetstream"], "py": {"C": ["nats"]}, "plant": False,
             "what": "NATS JetStream 2.10.22 (docker, VM-B): publish with Nats-Msg-Id; the stream is the owner"},
    "minio": {"configs": ["minio-conditional", "minio-unconditional"], "py": {}, "plant": False,
              "what": "MinIO RELEASE.2025-04-22T22-12-26Z built from source (docker, VM-B): S3 PUT with and without "
                      "If-None-Match; the bucket (versioned) is the owner"},
    # release 068
    "dbos": {"configs": ["dbos-step"], "py": {"A": ["dbos"]},
             "what": "DBOS Transact (Python, VM-A) on PostgreSQL 16 (docker, VM-B): workflow id = command, one step whose "
                     "effect goes through VM-C to the plant (VM-E)"},
    "k3s": {"configs": ["k8s-name", "k8s-generatename"], "py": {}, "plant": False,
            "what": "Kubernetes API server (k3s v1.36.4+k3s1, docker, VM-B): create ConfigMap by fixed name and by "
                    "generateName; the API server is the owner"},
    "thingsboard": {"configs": ["thingsboard-rpc", "thingsboard-rpc-oneway", "thingsboard-rpc-r0"], "py": {"E": ["paho.mqtt.client"]},
                    "what": "ThingsBoard CE 4.3.1.6 (docker, VM-B) + reference MQTT device (VM-E): server-side RPC, "
                            "persistent two-way and one-way"},
    "zephyr": {"configs": ["zephyr-mqtt"], "py": {"C": ["paho.mqtt.client"]}, "owner": "mosquitto", "fw_mode": "cutoff",
               "what": "EviCon firmware on Zephyr {zephyr}, cutoff mode (native_sim/native/64, VM-E; built by deploy/setup/build_iot.sh) "
                       "behind Eclipse Mosquitto 2.0.20 (docker, VM-B)"},
    # release 074 (experiment C2): the same firmware binary in its other two evidence modes
    "zephyr-stop": {"configs": ["zephyr-mqtt-stop"], "py": {"C": ["paho.mqtt.client"]}, "owner": "mosquitto", "fw_mode": "stop",
                    "what": "EviCon firmware on Zephyr {zephyr}, stop mode (cutoff + per-identity stop; native_sim/native/64, VM-E) "
                            "behind Eclipse Mosquitto 2.0.20 (docker, VM-B)"},
    "zephyr-plain": {"configs": ["zephyr-mqtt-plain"], "py": {"C": ["paho.mqtt.client"]}, "owner": "mosquitto", "fw_mode": "plain",
                     "what": "EviCon firmware on Zephyr {zephyr}, plain mode (no evidence; native_sim/native/64, VM-E) "
                             "behind Eclipse Mosquitto 2.0.20 (docker, VM-B)"},
    # release 077: the same firmware sources (unchanged) built for an emulated Arm Cortex-M3 MCU: QEMU machine
    # mps2-an385 on VM-E, Zephyr's own TCP/IP stack and LAN9118 Ethernet driver (no host sockets), NVS on a
    # simulated flash; built by build_iot.sh (firmware/zephyr-mcu) with the broker and plant addresses as Kconfig values
    "zephyr-mcu": {"configs": ["zephyr-mqtt-mcu"], "py": {"C": ["paho.mqtt.client"]}, "owner": "mosquitto", "fw_mode": "cutoff",
                   "fw_target": "mcu",
                   "what": "EviCon firmware on Zephyr {zephyr}, cutoff mode, on an emulated Arm Cortex-M3 MCU (QEMU mps2-an385, VM-E; "
                           "Zephyr's own TCP/IP stack and Ethernet driver; built by deploy/setup/build_iot.sh) behind Eclipse "
                           "Mosquitto 2.0.20 (docker, VM-B)"},
    # release 077: the authenticated variant (firmware/zephyr-auth: request and reply MACs, cutoff bound) against
    # an attacker on the broker without the device key; a scripted check, not a contract measurement
    "zephyr-auth": {"configs": [], "auth_check": True, "py": {"C": ["paho.mqtt.client"]}, "owner": "mosquitto", "fw_mode": "stop",
                    "fw_target": "auth",
                    "what": "Authenticated EviCon firmware on Zephyr {zephyr} (firmware/zephyr-auth), stop mode (native_sim/native/64, VM-E) "
                            "behind Eclipse Mosquitto 2.0.20 (docker, VM-B), against forged, altered and replayed messages"},
    # release 075: what the firmware certifies across an owner reboot (outside the measured fault model)
    "zephyr-reboot": {"configs": [], "reboot_check": True, "py": {"C": ["paho.mqtt.client"]}, "owner": "mosquitto", "fw_mode": "stop",
                      "what": "EviCon firmware on Zephyr {zephyr}, stop mode, across an owner reboot (process stopped and started again "
                              "with its flash file; native_sim/native/64, VM-E) behind Eclipse Mosquitto 2.0.20 (docker, VM-B)"},
}
# processes each stack starts (role, workdir name), the ports that must be free before it starts
# (VM-C: the in-process fault proxy; others: the reference processes) and the VMs that need the
# conformance tree (release 068: one table instead of per-stack branches)
STACK_PROCS = {"mosquitto": [("E", "mqtt-device"), ("B", "broker")], "emqx": [("E", "mqtt-device"), ("B", "broker")],
               "nats": [("B", "broker")], "minio": [("B", "broker")], "coap": [("E", "coap-device")],
               "dbos": [("A", "dbos-app"), ("B", "broker")], "k3s": [("B", "broker")],
               "thingsboard": [("E", "tb-device"), ("B", "broker")], "zephyr": [("E", "zephyr-fw"), ("B", "broker")],
               "zephyr-stop": [("E", "zephyr-fw"), ("B", "broker")], "zephyr-plain": [("E", "zephyr-fw"), ("B", "broker")],
               "zephyr-reboot": [("E", "zephyr-fw"), ("B", "broker")], "zephyr-mcu": [("E", "zephyr-fw"), ("B", "broker")],
               "zephyr-auth": [("E", "zephyr-fw"), ("B", "broker")]}
STACK_FREE = {"mosquitto": {"C": ["proxy_mqtt"], "E": ["mqtt_admin"]}, "emqx": {"C": ["proxy_emqx"], "E": ["mqtt_admin"]},
              "nats": {"C": ["proxy_nats"]}, "minio": {"C": ["proxy_minio"]}, "temporal": {"C": ["proxy_temporal"]},
              "coap": {"E": ["coap_health"]}, "dbos": {"C": ["proxy_dbos"], "A": ["dbos_app"]}, "k3s": {"C": ["proxy_k3s"]},
              "thingsboard": {"C": ["proxy_tb"], "E": ["tb_admin"]}, "zephyr": {"C": ["proxy_mqtt"], "E": ["zephyr_plant_tcp"]},
              "zephyr-stop": {"C": ["proxy_mqtt"], "E": ["zephyr_plant_tcp"]}, "zephyr-plain": {"C": ["proxy_mqtt"], "E": ["zephyr_plant_tcp"]},
              "zephyr-reboot": {"E": ["zephyr_plant_tcp"]}, "zephyr-mcu": {"C": ["proxy_mqtt"], "E": ["zephyr_plant_tcp"]},
              "zephyr-auth": {"E": ["zephyr_plant_tcp"]}}
STACK_DEPLOY = {"mosquitto": ["E"], "emqx": ["E"], "coap": ["E"], "temporal": ["E", "A"], "dbos": ["A"], "thingsboard": ["E"]}
MEASURE_DURATION = {"mosquitto": "10-15 min (--quick: 3-5 min)", "coap": "60-75 min (--quick: 20-25 min)",
                    "temporal": "5-10 min (--quick: 2 min)", "emqx": "10-15 min (--quick: 4-6 min)",
                    "nats": "25-35 min (--quick: 10 min)", "minio": "30-40 min (--quick: 12-15 min; 070: P5 waits 600 s per repetition); +10-20 min the first time (MinIO image built from source)",
                    "dbos": "10-15 min (--quick: 4 min)", "k3s": "30-40 min (--quick: 12-15 min; 070: P5 waits 600 s per repetition)",
                    "thingsboard": "35-55 min (--quick: 15 min); ThingsBoard install about 5 min; 077: + thingsboard-rpc-r0",
                    "zephyr": "35-45 min (--quick: 15 min; 070: P5 waits 600 s per repetition); needs the firmware from build_iot.sh",
                    "zephyr-stop": "35-45 min (--quick: 15 min; P5 waits 600 s per repetition); needs the 074 firmware from build_iot.sh",
                    "zephyr-plain": "5-10 min (no P5); needs the 074 firmware from build_iot.sh",
                    "zephyr-reboot": "2-4 min (one scripted check across a firmware restart; release 075)",
                    "zephyr-mcu": "40-55 min (--quick: 15-20 min; P5 waits 600 s per repetition); needs the 077 MCU build from build_iot.sh",
                    "zephyr-auth": "2-4 min (one scripted check; release 077); needs the 077 build from build_iot.sh"}
# stacks that need the testbed's docker on VM-B (no --local self-test)
TESTBED_ONLY_STACKS = ("emqx", "nats", "minio", "dbos", "k3s", "thingsboard", "zephyr", "zephyr-stop", "zephyr-plain", "zephyr-reboot",
                       "zephyr-mcu", "zephyr-auth")


def _udp_in_use(host, port) -> str:
    out = host.run(f"ss -lunpH 'sport = :{int(port)}' 2>/dev/null", check=False).stdout.strip()
    return out.splitlines()[0][:200] if out else ""


def _py_missing(host, modules) -> list:
    missing = []
    for m in modules:
        if host.run(f"python3 -c 'import {m}' >/dev/null 2>&1", check=False).returncode != 0:
            missing.append(m)
    return missing


def _push_tree(tb, role, rels, dest):
    """Copies repository paths (files or directories) to dest on a VM, without caches."""
    files = [str(r) for r in rels]
    cmd = (f"tar czf - --exclude=__pycache__ --exclude='*.pyc' -C {shlex.quote(str(REPO))} {' '.join(map(shlex.quote, files))} | "
           f"ssh -o BatchMode=yes -o ConnectTimeout=10 {tb.ssh_target(role)} 'mkdir -p {shlex.quote(dest)} && tar xzf - -C {shlex.quote(dest)}'")
    sh(cmd, quiet=True, timeout=300)


def _push_file(tb, role, src, dest):
    """Copies one file to dest on a VM unless the same bytes are already there (SHA-256);
    atomic (copy to dest.part, then rename), checked after the copy."""
    want = sha256(src)
    if tb.host(role).sha256([dest]).get(Path(dest).name) == want:
        return
    q = shlex.quote(dest)
    tb.run(role, f"mkdir -p {shlex.quote(str(Path(dest).parent))}")
    sh(["scp", "-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", str(src), f"{tb.ssh_target(role)}:{dest}.part"], timeout=600)
    tb.run(role, f"chmod 755 {q}.part && mv -f {q}.part {q}")
    if tb.host(role).sha256([dest]).get(Path(dest).name) != want:
        raise StageFail(f"VM-{role}: {dest} differs from {src} after the copy")


def _tcp_listen_check(tb, port):
    """VM-A must reach VM-C:port (the Temporal activity's plant proxy)."""
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind(("0.0.0.0", port))
        srv.listen(1)
        p = tb.host("A").run(f"timeout 4 bash -c '</dev/tcp/{tb.ip('C')}/{port}' && echo ok", check=False, timeout=20)
        return p.stdout.strip() == "ok"
    finally:
        srv.close()


def _temporal_serving(addr, namespace="default", timeout=20.0, info=None):
    """None when Temporal at addr answers for the namespace, else why not. An open port is
    not enough: docker-proxy keeps a published port open while the server inside is down
    (065 setup on VM-D). info (a dict) receives the server version."""
    try:
        import asyncio
        from temporalio.api.workflowservice.v1 import DescribeNamespaceRequest, GetSystemInfoRequest
        from temporalio.client import Client
    except ImportError as e:
        return f"temporalio missing on VM-C ({e})"

    async def go():
        c = await Client.connect(addr, namespace=namespace)
        await c.workflow_service.describe_namespace(DescribeNamespaceRequest(namespace=namespace))
        if info is not None:
            info["server_version"] = (await c.workflow_service.get_system_info(GetSystemInfoRequest())).server_version
    try:
        asyncio.run(asyncio.wait_for(go(), timeout))
        return None
    except asyncio.TimeoutError:
        return f"no answer within {timeout:.0f} s"
    except Exception as e:  # noqa: BLE001 - reported to the operator
        return f"{type(e).__name__}: {' '.join(str(e).split())[:240]}"


TEMPORAL_CLI = "1.3.0"  # scripts/ensure_temporal_cli.py: Temporal CLI 1.3.0 = Temporal Server 1.27.1


def _temporal_cli(arch="amd64"):
    """The pinned Temporal CLI for a host architecture, on VM-C (downloaded and SHA-256
    checked by scripts/ensure_temporal_cli.py when missing)."""
    import platform as _pf
    native = {"x86_64": "amd64", "aarch64": "arm64"}.get(_pf.machine(), _pf.machine())
    exe = REPO / ".tools" / ("temporal" if arch == native else f"temporal-linux-{arch}")
    rc, out = sh([sys.executable, "scripts/ensure_temporal_cli.py", "--arch", arch], timeout=1500, check=False, quiet=True)
    if rc != 0 or not exe.is_file():
        raise StageFail(f"Temporal CLI {TEMPORAL_CLI} ({arch}) not available on VM-C: {out.strip()[-500:]}")
    return exe


def _coap_answers(host, port, timeout=12.0):
    """None when a confirmable CoAP GET /status?cmd=evicon-ready-check (a status query: no
    effect on the plant) sent from VM-C gets a 2.xx response, else why not. The device's HTTP
    /healthz says nothing about its UDP transport (fix of 15/10)."""
    import struct
    token, mid = os.urandom(4), int.from_bytes(os.urandom(2), "big")
    path, query = b"status", b"cmd=evicon-ready-check"
    msg = (bytes([0x40 | len(token), 0x01]) + struct.pack("!H", mid) + token
           + bytes([(11 << 4) | len(path)]) + path                    # Uri-Path (option 11)
           + bytes([(4 << 4) | 13, len(query) - 13]) + query)        # Uri-Query (option 15)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.settimeout(2.0)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            s.sendto(msg, (host, int(port)))  # a resend is a CON retransmission (same MID and token)
            try:
                while True:
                    data, _ = s.recvfrom(4096)
                    if len(data) < 4 or data[0] >> 6 != 1 or data[1] == 0:
                        continue  # not CoAP, or an empty ACK (a separate response follows)
                    if data[4:4 + (data[0] & 0x0F)] == token:
                        code = data[1]
                        return None if code >> 5 == 2 else f"CoAP answer {code >> 5}.{code & 31:02d}"
            except socket.timeout:
                continue
        return f"no CoAP answer within {timeout:.0f} s"
    except OSError as e:
        return f"{type(e).__name__}: {e}"
    finally:
        s.close()


def _nats_ok(host, port, timeout=2.0) -> bool:
    """A NATS server greets every client with INFO {...}."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as c:
            c.settimeout(timeout)
            return c.recv(5) == b"INFO "
    except OSError:
        return False


def _pg_ok(host, port, timeout=2.0) -> bool:
    """A PostgreSQL server answers an SSLRequest with one byte, S or N (release 068)."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as c:
            c.settimeout(timeout)
            c.sendall((8).to_bytes(4, "big") + (80877103).to_bytes(4, "big"))
            return c.recv(1) in (b"S", b"N")
    except OSError:
        return False


def _k8s_ready(host, port, timeout=3.0) -> bool:
    """The Kubernetes API answers GET /readyz with 200 for the probe's bearer token (release 068;
    the CA is fetched only once the server is up, so this check does not verify it)."""
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    req = urllib.request.Request(f"https://{host}:{port}/readyz", headers={"Authorization": "Bearer " + os.environ.get("K3S_PROBE_TOKEN", "")})
    try:
        with _urlopen(req, timeout, ctx) as r:
            return r.status == 200
    except Exception:
        return False


TB_DEMO = {"sysadmin": ("sysadmin@thingsboard.org", "sysadmin"), "tenant": ("tenant@thingsboard.org", "tenant")}


def _tb_login(base, user, password, timeout=5.0):
    """JWT of a ThingsBoard user, or None (wrong credentials or not up yet)."""
    req = urllib.request.Request(base + "/api/auth/login", data=json.dumps({"username": user, "password": password}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read()).get("token")
    except Exception:
        return None


def _tb_call(base, method, path, jwt, body=None, timeout=15.0):
    """ThingsBoard REST call with a JWT: (status, parsed body)."""
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json", "X-Authorization": "Bearer " + (jwt or "")})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")[:300]
    except Exception as e:  # noqa: BLE001
        return None, str(e)


def _k3s_version(host, port, ca) -> str:
    import ssl
    ctx = ssl.create_default_context(cafile=ca)
    req = urllib.request.Request(f"https://{host}:{port}/version", headers={"Authorization": "Bearer " + os.environ.get("K3S_PROBE_TOKEN", "")})
    try:
        with _urlopen(req, 10, ctx) as r:
            return json.loads(r.read()).get("gitVersion", "?")
    except Exception as e:  # noqa: BLE001
        raise StageFail(f"k3s: /version with the fetched CA failed ({e}); is VM-B's address in the certificate (K3S_TLS_SAN)?")


def _owner_ready(kind, host, port) -> bool:
    if kind == "mqtt":
        return _mqtt_connect_ok(host, port)
    if kind == "nats":
        return _nats_ok(host, port)
    if kind == "pg":
        return _pg_ok(host, port)
    if kind == "k8s":
        return _k8s_ready(host, port)
    if kind == "tb":  # the REST API logs a demo user in (the web UI answers earlier than the API)
        return _tb_login(f"http://{host}:{port}", *TB_DEMO["sysadmin"]) is not None
    if kind.startswith("http:"):
        return http_ok(f"http://{host}:{port}{kind[5:]}", timeout=3)
    raise ValueError(kind)


def _zephyr_answers(host, port, device, timeout=5.0, mode=None, ns="evicon"):
    """None when the firmware answers a ping (<ns>/<device>/ping: health, no effect, answered in every
    evidence mode; release 068-073: a query) through the broker in the expected mode, else why not."""
    try:
        import paho.mqtt.client as mqtt
    except ImportError as e:
        return f"paho-mqtt missing on VM-C ({e})"
    tag = os.urandom(4).hex()
    got, res = threading.Event(), {}

    def on_msg(_c, _u, m):
        try:
            res["body"] = json.loads(m.payload or b"{}")
        except ValueError:
            res["body"] = {}
        got.set()
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"evicon-ready-{tag}", protocol=mqtt.MQTTv311)
    c.on_message = on_msg
    try:
        c.connect(host, int(port), keepalive=10)
        c.loop_start()
        c.subscribe(f"evicon/ready-{tag}/#", qos=1)
        time.sleep(0.3)
        c.publish(f"{ns}/{device}/ping", json.dumps({"reply": f"evicon/ready-{tag}/ping"}), qos=1)
        if not got.wait(timeout):
            return "no reply"
        body = res.get("body") or {}
        if mode is not None and body.get("mode") != mode:
            return f"answers in mode {body.get('mode')!r}, expected {mode!r}"
        return None
    except OSError as e:
        return f"{type(e).__name__}: {e}"
    finally:
        try:
            c.disconnect()
            c.loop_stop()
        except Exception:  # noqa: BLE001
            pass


def _mqtt_connect_ok(host, port, timeout=2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout) as c:
            # MQTT 3.1.1 CONNECT, clean session, keepalive 10, client id "evicon-ready"
            cid = b"evicon-ready"
            var = b"\x00\x04MQTT\x04\x02\x00\x0a" + len(cid).to_bytes(2, "big") + cid
            c.sendall(b"\x10" + bytes([len(var)]) + var)
            c.settimeout(timeout)
            ack = c.recv(4)
            c.sendall(b"\xe0\x00")  # DISCONNECT
            return len(ack) == 4 and ack[0] == 0x20 and ack[3] == 0
    except OSError:
        return False


class _Measure:
    """State of one `measure <stack>` invocation."""

    def __init__(self, tb, a, stack):
        self.tb, self.a, self.stack, self.spec = tb, a, stack, STACKS[stack]
        self.P = dict(LOCAL_MEASURE_PORTS if tb.local else MEASURE_PORTS)
        self.out = RUN.path / "measure" / stack
        self.root = str(REPO) if tb.local else tb.cfg["remote_root"]
        base = (RUN.path / "local-hosts" / "state") if tb.local else Path(tb.cfg["state_dir"])
        self.sd = f"{base}/measure/{stack}"
        self.listen = "127.0.0.1" if tb.local else "0.0.0.0"
        self.E, self.A, self.B, self.D = tb.host("E"), tb.host("A"), tb.host("B"), tb.host("D")
        self.started = []  # (host, workdir) with pid files
        self.procs = []    # (host, name, pid, workdir) to check during the measurement
        self.compose_up = False
        self.owner = COMPOSE_OWNERS.get(self.spec.get("owner", stack))  # system run in EviCon's compose project on VM-B
        self.uses_plant = self.spec.get("plant", True)  # NATS/MinIO: the owner's own state is the truth
        # Temporal: "managed" (default) = EviCon starts a Temporal dev server on VM-D and stops it
        # afterwards; "external" = testbed key temporal_address; "local" = --local self-test
        ext = "" if tb.local else str(tb.cfg.get("temporal_address") or "")
        self.t_mode = "local" if tb.local else ("external" if ext else "managed")
        self.t_addr = ext or f"{'127.0.0.1' if tb.local else tb.ip('D')}:{self.P['temporal']}"
        server = {"managed": f"Temporal CLI {TEMPORAL_CLI} dev server started by EviCon on VM-D:{self.P['temporal']}, in-memory",
                  "external": f"existing Temporal at {self.t_addr}",
                  "local": f"Temporal CLI {TEMPORAL_CLI} dev server, local self-test"}[self.t_mode]
        self.facts = {"stack": stack, "what": self.spec["what"].replace("{server}", server).replace("{zephyr}", "v4.2.0"),
                      "release": RELEASE, "local": tb.local, "ports": self.P}
        if stack == "temporal":
            self.facts["temporal"] = {"mode": self.t_mode, "address": self.t_addr}

    # processes this stack starts: (host, workdir name)
    def _own(self):
        own = [(self.E, "plant")] if self.uses_plant else []
        if self.stack == "temporal":
            return own + [(self.A, "temporal-worker")] + ([(self.D, "temporal-server")] if self.t_mode != "external" else [])
        return own + [(self.tb.host(role), name) for role, name in STACK_PROCS[self.stack]]

    # ---- checks -------------------------------------------------------
    def checks(self):
        tb, P, problems = self.tb, self.P, []
        if tb.local and self.stack in TESTBED_ONLY_STACKS:
            raise StageFail(f"measure {self.stack} needs the testbed (its owner runs in docker on VM-B); "
                            f"--local supports {', '.join(s for s in STACKS if s not in TESTBED_ONLY_STACKS)}")
        # leftovers of an interrupted earlier run (power loss, kill -9 of the driver): only
        # processes started by EviCon in its own workdirs (pid file + cwd), never others
        if tb.local:
            for host, name in self._own():
                host.stop_all(self._wd(name))
        else:
            _sweep_leftovers(tb, ("A", "B", "E") + (("D",) if self.stack == "temporal" and self.t_mode == "managed" else ()))
        miss = [m for m in self.spec["py"].get("C", []) if importlib_find(m) is None]
        if miss:
            problems.append(f"VM-C: Python module(s) {miss} missing (pip3 install -r conformance/requirements-conformance.txt; "
                            f"deploy/setup/vm-c.sh does it)")
        for role in ("A", "E"):
            mods = self.spec["py"].get(role, [])
            if mods:
                m = _py_missing(tb.host(role), mods)
                if m:
                    problems.append(f"VM-{role}: Python module(s) {m} missing (run deploy/setup/vm-{role.lower()}.sh on VM-{role})")
        if not self.uses_plant:
            pass
        elif not tb.local:
            bins = {"evicon-plant": sha256(REPO / "bin" / "evicon-plant")} if (REPO / "bin" / "evicon-plant").exists() else {}
            if not bins:
                problems.append("bin/evicon-plant missing: run 'python3 evicon_review.py build distribute' first")
            else:
                got = self.E.sha256([f"{tb.cfg['remote_root']}/bin/evicon-plant"])
                if got.get("evicon-plant") != bins["evicon-plant"]:
                    problems.append("VM-E: /root/evicon/bin/evicon-plant differs from bin/ (run 'python3 evicon_review.py distribute')")
        elif not (REPO / "bin" / "evicon-plant").exists():
            problems.append("bin/evicon-plant missing: run 'python3 evicon_review.py build' first")
        busy = []
        free = STACK_FREE.get(self.stack, {})
        e_tcp = ([P["plant"]] if self.uses_plant else []) + [P[k] for k in free.get("E", [])]
        for p in self.E.ports_in_use(e_tcp):
            busy.append(f"VM-E tcp/{p}: {self.E.port_owner(p)}")
        for p in self.A.ports_in_use([P[k] for k in free.get("A", [])]):
            busy.append(f"VM-A tcp/{p}: {self.A.port_owner(p)}")
        if self.stack == "coap" and _udp_in_use(self.E, P["coap"]):
            busy.append(f"VM-E udp/{P['coap']}: {_udp_in_use(self.E, P['coap'])}")
        if self.stack == "temporal":
            for p in self.A.ports_in_use([P["worker_health"]]):
                busy.append(f"VM-A tcp/{p}: {self.A.port_owner(p)}")
            if self.t_mode != "external":
                for p in self.D.ports_in_use([P["temporal"]]):
                    busy.append(f"VM-D tcp/{p}: {self.D.port_owner(p)}")
        from campaign import Host
        hc = Host("VM-C", "127.0.0.1")
        c_tcp = [P[k] for k in free.get("C", [])]
        for p in hc.ports_in_use(c_tcp):
            busy.append(f"VM-C tcp/{p}: {hc.port_owner(p)}")
        if self.stack == "coap" and _udp_in_use(hc, P["proxy_coap"]):
            busy.append(f"VM-C udp/{P['proxy_coap']}: {_udp_in_use(hc, P['proxy_coap'])}")
        if self.owner:
            problems += self._broker_checks(busy)
        if self.stack == "temporal":
            problems += self._temporal_checks()
        if self.stack == "dbos" and not tb.local:
            v = self.A.run("python3 -c 'import sys; print(sys.version_info >= (3, 10))'", check=False).stdout.strip()
            if v != "True":
                problems.append("VM-A: DBOS Transact 3.x needs Python >= 3.10 (python3 on VM-A is older)")
            if not tb.local and not _tcp_listen_check(self.tb, P["proxy_dbos"]):
                problems.append(f"VM-A cannot reach VM-C:{P['proxy_dbos']} (ufw allow from {self.tb.ip('A')} to any port 28200:28215 proto tcp)")
        if self.stack == "k3s" and not shutil.which("openssl"):
            problems.append("VM-C: openssl missing (apt-get install -y openssl): the fault proxy needs a throwaway TLS certificate")
        if busy:
            problems.append("ports needed by this measurement are in use: " + "; ".join(busy)
                            + ". EviCon never stops other programs: stop them, then re-run")
        self.facts["checks"] = {"problems": problems}
        if problems:
            for x in problems:
                RUN.log("FAIL " + x)
            raise StageFail("; ".join(problems))

    def _broker_checks(self, busy):
        P, problems = self.P, []
        if self.tb.local:
            if not shutil.which("mosquitto"):
                problems.append("--local mosquitto needs the mosquitto binary on this host (apt-get install -y mosquitto)")
            if self.B.ports_in_use([P["broker"]]):
                busy.append(f"VM-B tcp/{P['broker']}: {self.B.port_owner(P['broker'])}")
            return problems
        if self.B.run("docker compose version >/dev/null 2>&1", check=False).returncode != 0:
            return ["VM-B: 'docker compose' not available (run deploy/setup/vm-b.sh on VM-B)"]
        if self.B.run("docker info >/dev/null 2>&1", check=False).returncode != 0:
            return ["VM-B: the Docker daemon is not running (systemctl enable --now docker)"]
        for port in [P[k] for k in self.owner["ports"]]:
            if self.B.ports_in_use([port]):
                ours = self.B.run(f"docker ps -q --filter label=com.docker.compose.project={COMPOSE_PROJECT} --filter publish={port}",
                                  check=False).stdout.strip()
                if not ours:
                    busy.append(f"VM-B tcp/{port}: {self.B.port_owner(port)} (not EviCon's compose project {COMPOSE_PROJECT})")
        return problems

    def _temporal_checks(self):
        P, problems = self.P, []
        if self.t_mode in ("local", "managed"):
            try:
                if self.t_mode == "managed":
                    p = self.D.run("uname -m", check=False, timeout=20)
                    if p.returncode != 0:
                        return [f"VM-D: SSH root@{self.tb.ip('D')} failed ({p.stderr.strip()[:200]}); the Temporal server runs there"]
                    arch = {"x86_64": "amd64", "aarch64": "arm64"}.get(p.stdout.strip(), p.stdout.strip())
                    self.t_arch = arch
                else:
                    arch = {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine(), platform.machine())
                self.t_cli = _temporal_cli(arch)
            except StageFail as e:
                problems.append(str(e))
        else:
            host, _, port = self.t_addr.rpartition(":")
            try:
                socket.create_connection((host, int(port)), timeout=5).close()
            except OSError as e:
                return [f"Temporal frontend {self.t_addr} (testbed.json temporal_address) not reachable from VM-C ({e}). "
                        f"Leave temporal_address empty to let EviCon start its own Temporal on VM-D"]
            info = {}
            why = _temporal_serving(self.t_addr, info=info)
            if why:
                return [f"Temporal at {self.t_addr} (testbed.json temporal_address) accepts connections but does not serve "
                        f"namespace 'default' ({why}). Check it with: python3 deploy/setup/temporal_check.py --address "
                        f"{self.t_addr} --create; or leave temporal_address empty to let EviCon start its own Temporal on VM-D"]
            self.facts["temporal"]["server_version"] = info.get("server_version", "?")
            if self.A.run(f"timeout 4 bash -c '</dev/tcp/{host}/{port}' && echo ok", check=False, timeout=20).stdout.strip() != "ok":
                problems.append(f"VM-A cannot reach Temporal at {self.t_addr} (firewall?)")
        if not self.tb.local:
            try:
                if not _tcp_listen_check(self.tb, P["proxy_temporal"]):
                    problems.append(f"VM-A cannot reach VM-C:{P['proxy_temporal']} (ufw allow from {self.tb.ip('A')} to any port 28200:28215 proto tcp)")
            except OSError as e:
                problems.append(f"VM-C port {P['proxy_temporal']}: {e}")
        return problems

    # ---- processes ------------------------------------------------------
    def _wd(self, name):
        return f"{self.sd}/{name}"

    def spawn(self, host, name, argv, wd, url):
        host.stop_all(wd)
        host.run(f"rm -rf {shlex.quote(wd)} && mkdir -p {shlex.quote(wd)}")
        pid = host.spawn(name, argv, wd)
        self.started.append((host, wd))
        self.procs.append((host, name, pid, wd))
        if url:
            try:
                _ready(host, url, name, pid, wd)
            except StageFail:
                self.procs.pop()  # never started: a retry (CoAP transport fallback) replaces it
                raise
        return pid

    def dead(self) -> list:
        """Processes this measurement started that are no longer running."""
        return [f"{name} on {host.name} (pid {pid}; log {wd}/{name}.log)" for host, name, pid, wd in self.procs if not host.alive(pid)]

    def deploy(self):
        if self.tb.local:
            return
        rels = ["VERSION", "conformance/reference", "conformance/evicon_probe"]
        for role in STACK_DEPLOY.get(self.stack, []):
            _push_tree(self.tb, role, rels, self.root)
        if self.owner:
            # the whole compose directory (compose files, mosquitto.conf, the MinIO Dockerfile)
            _push_tree(self.tb, "B", ["conformance/compose/vm-b"], self.root)
        if self.stack == "temporal" and self.t_mode == "managed":
            self.t_remote = f"{self.root}/bin/temporal"
            _push_file(self.tb, "D", self.t_cli, self.t_remote)

    def start_plant(self):
        if not self.uses_plant:
            return
        P, ip = self.P, self.tb.ip("E")
        wd = self._wd("plant")
        bin_ = str(REPO / "bin") if self.tb.local else f"{self.tb.cfg['remote_root']}/bin"
        self.spawn(self.E, "plant", [f"{bin_}/evicon-plant", "-log", f"{wd}/plant.jsonl", "-http", f"{self.listen}:{P['plant']}"]
                   + (["-tcp", f"127.0.0.1:{P['zephyr_plant_tcp']}"] if self.stack.startswith("zephyr") else [])
                   + (["-fsync=false"] if self.tb.local else []), wd, f"http://{ip}:{P['plant']}/healthz")
        self.plant_url = f"http://{ip}:{P['plant']}"

    def images(self) -> dict:
        """Image of every image variable of the owner: VM-C's environment overrides the pinned
        default (release 068: passed explicitly to VM-B, so the override is the one that runs)."""
        out = {}
        for var, default in self.owner.get("images", {}).items():
            v = os.environ.get(var, "").strip() or default
            if not re.fullmatch(r"[A-Za-z0-9._/:@+-]+", v):
                raise StageFail(f"{var}={v!r} is not an image reference")
            out[var] = v
        return out

    def _compose_cmd(self, args):
        f = f"{self.root}/conformance/compose/vm-b/{self.owner['file']}"
        prof = f" --profile {self.owner['profile']}" if self.owner.get("profile") else ""
        env = dict(self.images())
        for k, v in self.owner.get("env", {}).items():
            env[k] = v.format(B=self.tb.ip("B"))
        pre = " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items())
        return f"{pre} docker compose -p {COMPOSE_PROJECT} -f {shlex.quote(f)}{prof} {args}".strip()

    def _secrets(self) -> dict:
        """The owner's credentials (MinIO root user/password, PostgreSQL passwords, the k3s probe
        token): from VM-C's environment when set, else random ones for this measurement only.
        Never written to a file or a log: they reach VM-B on ssh's stdin and the probe runner
        through its environment (the derived configs keep ${VAR})."""
        import secrets as _secrets
        for k in self.owner.get("secrets", []):
            if not os.environ.get(k):
                os.environ[k] = ("evicon" + _secrets.token_hex(4)) if k.endswith("_USER") else _secrets.token_hex(24)
        return {k: os.environ[k] for k in self.owner.get("secrets", [])}

    def _with_secrets(self, host, cmd, names, timeout, check=True):
        """Runs cmd on host with the variables `names` exported from lines on stdin (values from
        VM-C's environment): never on a command line, in a file or in a log."""
        reads = "".join(f"IFS= read -r {n}; export {n}; " for n in names)
        RUN.log(f"[{host.name}] $ ({', '.join(names)} on stdin) {cmd}")
        argv = ["bash", "-c", reads + cmd] if host.ssh is None else \
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host.ssh, reads + cmd]
        p = subprocess.run(argv, input="".join(os.environ[n] + "\n" for n in names), capture_output=True, text=True, timeout=timeout)
        for line in (p.stdout + p.stderr).splitlines()[-40:]:
            RUN.log("  " + line)
        if check and p.returncode != 0:
            why = " | ".join(l.strip() for l in (p.stderr or p.stdout).strip().splitlines()[-3:])[-400:]
            raise StageFail(f"{host.name}: command failed (rc={p.returncode}): {cmd}" + (f" -> {why}" if why else ""))
        return p

    def compose(self, args, check=True, timeout=600):
        cmd = self._compose_cmd(args)
        if not self.owner.get("secrets"):
            return self.tb.run("B", cmd, check=check, timeout=timeout)
        return self._with_secrets(self.B, cmd, list(self._secrets()), timeout, check)

    def spawn_env(self, host, name, argv, wd, url, secrets, role=None):
        """spawn() whose process gets the variables `secrets` in its environment (from stdin).
        role: the role the process reports on /healthz when it differs from name."""
        host.stop_all(wd)
        host.run(f"rm -rf {shlex.quote(wd)} && mkdir -p {shlex.quote(wd)}")
        q = " ".join(shlex.quote(str(x)) for x in argv)
        self._with_secrets(host, f"cd {shlex.quote(wd)} && (nohup {q} > {shlex.quote(name)}.log 2>&1 < /dev/null & "
                                 f"echo $! > {shlex.quote(name)}.pid)", secrets, 60)
        pid = host.pid(name, wd)
        self.started.append((host, wd))
        self.procs.append((host, name, pid, wd))
        if url:
            _ready(host, url, role or name, pid, wd)
        return pid

    def _compose_reset(self):
        """Removes containers and volumes of EviCon's own compose project only (by label): a
        stack an interrupted run left up (other profile, other file) would hold RAM or ports."""
        lab = f"label=com.docker.compose.project={COMPOSE_PROJECT}"
        self.tb.run("B", f"ids=$(docker ps -aq --filter {lab}); [ -z \"$ids\" ] || docker rm -f $ids >/dev/null; "
                         f"vs=$(docker volume ls -q --filter {lab}); [ -z \"$vs\" ] || docker volume rm $vs >/dev/null; true",
                    check=False)

    def start_broker(self):
        P, ip, o = self.P, self.tb.ip("B"), self.owner
        port = P[o["port"]]
        if self.tb.local:
            wd = self._wd("broker")
            self.B.run(f"mkdir -p {shlex.quote(wd)}")
            conf = f"{wd}/mosquitto.conf"
            Path(conf).write_text(f"listener {P['broker']} 127.0.0.1\nallow_anonymous true\npersistence false\nmax_queued_messages 10000\n"
                                  "log_type error\nlog_type warning\nlog_type notice\n")
            self.B.stop_all(wd)
            self.B.spawn("broker", [shutil.which("mosquitto"), "-c", conf], wd)
            self.started.append((self.B, wd))
            self.facts["broker"] = {"kind": "local mosquitto binary", "version": _cmd(["mosquitto", "-h"]).splitlines()[0][:80]}
        else:
            self.compose("down -v", check=False)  # EviCon's own project only: a fresh owner state per measurement
            self._compose_reset()
            self.build_images()
            self.compose_up = True                 # from here on, cleanup() brings the project down
            for args, t in o.get("init", []):
                RUN.log(f"{self.stack}: one-off initialisation ({args.split()[-1]}), up to {t // 60} min")
                self.compose(args, timeout=t)
            self.compose("up -d", timeout=1800)    # the first run pulls the images
            imgs = {}
            for var, ref in self.images().items():
                # 069: the 068 NATS record was empty (the format with labels printed nothing); fall back
                # to id + digests, and record the error text rather than nothing
                r = shlex.quote(ref)
                imgs[var] = self.tb.run("B", f"out=$(docker image inspect --format '{{{{.Id}}}} {{{{.RepoDigests}}}} {{{{json .Config.Labels}}}}' {r} 2>&1) "
                                             f"&& [ -n \"$out\" ] || out=$(docker image inspect --format '{{{{.Id}}}} {{{{.RepoDigests}}}}' {r} 2>&1) "
                                             f"|| out=\"inspect failed: $out\"; printf '%s' \"$out\"", check=False).stdout.strip()[:600]
            # and the images the project's containers actually run (by id), whatever the reference
            run_ids = self.tb.run("B", f"docker ps -a --filter label=com.docker.compose.project={COMPOSE_PROJECT} "
                                       "--format '{{.Names}}' | while read -r n; do docker inspect --format '{{.Name}} {{.Image}} {{.Config.Image}}' \"$n\"; done",
                                  check=False).stdout.strip()[:1200]
            self.facts["broker"] = {"kind": "docker compose", "project": COMPOSE_PROJECT, "file": o["file"],
                                    "service": o.get("profile") or self.stack, "images": self.images(), "image_ids": imgs,
                                    "containers": run_ids.splitlines()}
        limit = o.get("ready_s", 90)
        end, polls = time.monotonic() + limit, 0
        while time.monotonic() < end:
            if _owner_ready(o["ready"], ip, port):
                RUN.log(f"{self.stack} ready at {ip}:{port}")
                self.after_up()
                return
            polls += 1
            if not self.tb.local and polls % 10 == 0:
                # 068: a container that exited will not serve; say so now instead of after ready_s
                # (ThingsBoard: 15 min)
                gone = self.compose("ps -a --status exited --status dead --services", check=False).stdout.split()
                if gone:
                    tail = self.compose(f"logs --no-color --tail 40 {' '.join(map(shlex.quote, gone))}", check=False).stdout.strip()[-1500:]
                    raise StageFail(f"{self.stack}: container(s) {gone} exited before {ip}:{port} served; log tail: {tail or '(empty)'}")
            time.sleep(2)
        tail = "" if self.tb.local else self.compose("logs --no-color --tail 30", check=False).stdout.strip()[-800:]
        raise StageFail(f"{self.stack} at {ip}:{port} does not serve within {limit} s ({o['ready']} check); "
                        f"container log tail: {tail or '(empty)'}")

    def build_images(self):
        """Builds an owner image that has no registry any more (release 068: MinIO, from its
        official source) when VM-B does not have it; an image set in VM-C's environment is used
        as given (pulled by compose)."""
        for var, ctx in self.owner.get("build", {}).items():
            if os.environ.get(var, "").strip():
                RUN.log(f"{var} from VM-C's environment: {os.environ[var].strip()} (no build)")
                continue
            ref = self.owner["images"][var]
            if self.tb.run("B", f"docker image inspect {shlex.quote(ref)} >/dev/null 2>&1", check=False).returncode == 0:
                RUN.log(f"image {ref} present on VM-B")
                continue
            wd = self._wd("broker")
            d = f"{self.root}/conformance/compose/vm-b/{ctx}"
            RUN.log(f"building {ref} on VM-B from {d} (first time only: 10-20 min; log {wd}/image-build.log)")
            # 069: host network (068 testbed: DNS works on VM-B but not inside containers) and VM-B's
            # own proxy variables, if any, passed as build arguments
            proxy = ("pa=(); for v in HTTP_PROXY HTTPS_PROXY NO_PROXY http_proxy https_proxy no_proxy; do "
                     "[ -n \"${!v:-}\" ] && pa+=(--build-arg \"$v=${!v}\"); done; ")
            p = self.tb.run("B", f"mkdir -p {shlex.quote(wd)} && {proxy}docker build --network=host \"${{pa[@]}}\" -t {shlex.quote(ref)} "
                                 f"{shlex.quote(d)} > {shlex.quote(wd)}/image-build.log 2>&1", check=False, timeout=5400)
            if p.returncode != 0:
                tail = self.tb.run("B", f"tail -n 25 {shlex.quote(wd)}/image-build.log", check=False).stdout.strip()[-1500:]
                raise StageFail(f"building {ref} on VM-B failed (needs Internet on VM-B: github.com, proxy.golang.org, "
                                f"Docker Hub). Log tail: {tail}. Alternative: set {var} on VM-C to another image that runs "
                                f"`minio server` (see conformance/compose/vm-b/minio/Dockerfile)")

    def after_up(self):
        """Per-owner work once the system serves (release 068)."""
        if self.stack == "k3s":
            self._k3s_tls()
        elif self.stack == "thingsboard":
            self._tb_provision()

    def _k3s_tls(self):
        """The API server's CA (a public certificate, kept with the results) and a throwaway
        certificate for the TLS-terminating fault proxy (VM-C state dir only)."""
        ca = self.compose("exec -T k3s cat /var/lib/rancher/k3s/server/tls/server-ca.crt").stdout
        if "BEGIN CERTIFICATE" not in ca:
            raise StageFail("k3s: server CA not readable from the container")
        self.k3s_dir = Path(self.sd) / "vm-c-tls" if self.tb.local else Path(self.tb.cfg["state_dir"]) / "measure" / "k3s" / "vm-c-tls"
        shutil.rmtree(self.k3s_dir, ignore_errors=True)
        self.k3s_dir.mkdir(parents=True, exist_ok=True)
        (self.k3s_dir / "server-ca.crt").write_text(ca)
        (self.out / "k3s-server-ca.crt").write_text(ca)
        sys.path.insert(0, str(REPO / "conformance"))
        from evicon_probe.faults import make_proxy_cert  # noqa: E402
        self.k3s_proxy_tls = make_proxy_cert(self.k3s_dir / "proxy", "127.0.0.1")
        v = _k3s_version(self.tb.ip("B"), self.P["k3s_api"], str(self.k3s_dir / "server-ca.crt"))
        self.facts["k3s"] = {"server_version": v}
        RUN.log(f"k3s API server {v}: CA fetched, fault-proxy certificate made")

    def _tb_provision(self):
        """Replaces the demo passwords (sysadmin, tenant) with random ones, creates the probe
        device and reads its access token. Credentials stay in this process's environment
        (TB_USERNAME/TB_PASSWORD for the adapter, TB_DEVICE_TOKEN for the device)."""
        import secrets as _secrets
        base = f"http://{self.tb.ip('B')}:{self.P['tb_http']}"
        for role in ("sysadmin", "tenant"):
            user, demo = TB_DEMO[role]
            new = _secrets.token_hex(16)
            jwt = _tb_login(base, user, demo)
            if not jwt:
                raise StageFail(f"ThingsBoard: demo {role} login failed (install without demo data?)")
            st, body = _tb_call(base, "POST", "/api/auth/changePassword", jwt, {"currentPassword": demo, "newPassword": new})
            if st != 200:
                raise StageFail(f"ThingsBoard: changing the {role} password failed: HTTP {st} {str(body)[:200]}")
            if role == "tenant":
                os.environ["TB_USERNAME"], os.environ["TB_PASSWORD"] = user, new
        jwt = _tb_login(base, os.environ["TB_USERNAME"], os.environ["TB_PASSWORD"])
        st, dev = _tb_call(base, "POST", "/api/device", jwt, {"name": "evicon-tb-1", "type": "default", "label": "EviCon probe device"})
        if st != 200:
            raise StageFail(f"ThingsBoard: creating the probe device failed: HTTP {st} {str(dev)[:200]}")
        dev_id = dev["id"]["id"]
        st, cred = _tb_call(base, "GET", f"/api/device/{dev_id}/credentials", jwt)
        if st != 200 or not cred.get("credentialsId"):
            raise StageFail(f"ThingsBoard: reading the device credentials failed: HTTP {st}")
        os.environ["TB_DEVICE_TOKEN"] = cred["credentialsId"]
        st, ver = _tb_call(base, "GET", "/api/system/info", jwt)
        self.facts["thingsboard"] = {"device_uuid": dev_id, "device_name": "evicon-tb-1",
                                     "system_info": ver if st == 200 else f"HTTP {st}",
                                     "passwords": "demo passwords replaced by random ones for this measurement"}
        RUN.log(f"ThingsBoard: demo passwords replaced, device evicon-tb-1 = {dev_id}")

    def stop_broker(self):
        if self.tb.local:
            return
        if self.compose_up:
            wd = self._wd("broker")
            self.tb.run("B", f"mkdir -p {shlex.quote(wd)}", check=False)
            self.compose(f"logs --no-color > {shlex.quote(wd)}/broker.log 2>&1", check=False)
            self.compose("down -v", check=False)
            self.compose_up = False

    def start_temporal(self):
        """EviCon's own Temporal: `temporal server start-dev` (in-memory SQLite, namespace
        "default" built in) on VM-D, bound to all addresses so VM-C and VM-A reach it; or on
        127.0.0.1 for --local. Stopped by cleanup() like every other process it starts."""
        if self.t_mode == "external":
            return
        wd, P = self._wd("temporal-server"), self.P
        exe = str(self.t_cli) if self.t_mode == "local" else self.t_remote
        ip = "127.0.0.1" if self.t_mode == "local" else "0.0.0.0"
        pid = self.spawn(self.D, "temporal-server", [exe, "server", "start-dev", "--ip", ip, "--port", str(P["temporal"]),
                                                     "--headless", "--log-level", "warn"], wd, None)
        info, why, end = {}, "not started", time.monotonic() + 120
        while time.monotonic() < end:
            if not self.D.alive(pid):
                tail = self.D.run(f"tail -n 20 {shlex.quote(wd)}/temporal-server.log", check=False).stdout.strip()
                raise StageFail(f"the Temporal dev server on {self.D.name} exited at start (pid {pid}); port owner: "
                                f"{self.D.port_owner(P['temporal'])}; {wd}/temporal-server.log: {tail[-800:] or '(empty)'}")
            why = _temporal_serving(self.t_addr, timeout=10, info=info)
            if why is None:
                break
            time.sleep(2)
        else:
            raise StageFail(f"the Temporal dev server on {self.D.name} (pid {pid}) does not answer at {self.t_addr} from VM-C "
                            f"within 120 s ({why}). Firewall on VM-D? (ufw allow from {self.tb.ip('C')} to any port "
                            f"{P['temporal']} proto tcp; same for {self.tb.ip('A')}); log: {wd}/temporal-server.log")
        version = self.D.run(f"{shlex.quote(exe)} --version", check=False).stdout.strip()
        self.facts["temporal"].update(server_version=info.get("server_version", "?"), cli=version, pid=pid,
                                      persistence="in-memory SQLite: a fresh server for every measurement")
        if self.t_mode == "managed":
            host, _, port = self.t_addr.rpartition(":")
            if self.A.run(f"timeout 4 bash -c '</dev/tcp/{host}/{port}' && echo ok", check=False, timeout=20).stdout.strip() != "ok":
                raise StageFail(f"VM-A cannot reach the Temporal dev server at {self.t_addr} (firewall on VM-D? "
                                f"ufw allow from {self.tb.ip('A')} to any port {port} proto tcp)")
        RUN.log(f"Temporal ready at {self.t_addr}: server {info.get('server_version', '?')} ({version})")

    def start_reference(self):
        P, e_ip, py = self.P, self.tb.ip("E"), "python3" if not self.tb.local else sys.executable
        ref = f"{self.root}/conformance/reference"
        if self.stack in ("mosquitto", "emqx"):
            wd = self._wd("mqtt-device")
            self.spawn(self.E, "mqtt-device", [py, f"{ref}/mqtt_device.py", "--broker", f"{self.tb.ip('B')}:{P[self.owner['port']]}",
                                               "--device", "pump-mqtt-1", "--plant", self.plant_url, "--admin-port", str(P["mqtt_admin"]),
                                               "--admin-bind", self.listen], wd, f"http://{e_ip}:{P['mqtt_admin']}/healthz")
        elif self.stack == "coap":
            wd = self._wd("coap-device")
            # aiocoap's default UDP transport needs IPv6 sockets; on an IPv4-only host the
            # simplesocketserver transport is used, which cannot bind the any-address: it
            # binds VM-E's own address (the one the proxy and the control path use)
            v6 = self.E.run("test -e /proc/net/if_inet6 && echo yes", check=False).stdout.strip() == "yes"
            simple = (not v6 or self.tb.local) and os.environ.get("EVICON_COAP_TRY_UDP6") != "1"  # env: self-test of the fallback

            def argv(simple):
                a = [py, f"{ref}/coap_device.py", "--bind", e_ip if simple else "::", "--port", str(P["coap"]),
                     "--plant", self.plant_url, "--status", "--health-port", str(P["coap_health"]), "--health-bind", self.listen]
                return a + ["--transports", "simplesocketserver" if simple else "udp6"]
            url = f"http://{e_ip}:{P['coap_health']}/healthz"

            def start(simple):
                """None when the device serves CoAP to VM-C, else why not (StageFail: not started)."""
                self.spawn(self.E, "coap-device", argv(simple), wd, url)
                why = _coap_answers(e_ip, P["coap"])
                if why:
                    self.procs.pop()  # replaced by the fallback, or stopped by cleanup()
                return why
            try:
                why = start(simple)
            except StageFail as e:
                if simple:
                    raise
                why = f"did not start: {str(e)[:240]}"
            if why and not simple:
                # aiocoap's udp6 transport ([::], IPv4 through mapped addresses) did not serve: fall back
                RUN.problem("WARN", f"coap-device with aiocoap's udp6 transport does not serve CoAP from VM-C ({why}); "
                                    f"retrying with simplesocketserver bound to {e_ip}")
                simple = True
                why = start(simple)
            if why:
                raise StageFail(f"coap-device on VM-E runs but does not answer CoAP from VM-C at {e_ip}:{P['coap']}/udp ({why}). "
                                f"Firewall on VM-E? (ufw allow from {self.tb.ip('C')} to any port {P['coap']} proto udp)")
            self.facts["coap_transport"] = f"simplesocketserver bound to {e_ip}" if simple else "aiocoap udp6 on [::] (IPv4 through mapped addresses)"
        elif self.stack == "dbos":
            wd = self._wd("dbos-app")
            os.environ["DBOS_SYSTEM_DATABASE_URL"] = (f"postgresql://evicon:{self._secrets()['DBOS_PG_PASSWORD']}@"
                                                      f"{self.tb.ip('B')}:{P['dbos_pg']}/evicon_dbos")
            self.spawn_env(self.A, "dbos-app", [py, f"{ref}/dbos_app.py", "--plant-url", f"http://{self.tb.ip('C')}:{P['proxy_dbos']}",
                                                "--port", str(P["dbos_app"]), "--bind", self.listen, "--http-timeout", "2",
                                                "--step-max-attempts", "3", "--app-name", f"evicon-probe-{RELEASE}"],
                           wd, f"http://{self.tb.ip('A')}:{P['dbos_app']}/healthz", ["DBOS_SYSTEM_DATABASE_URL"])
            self.facts["dbos"] = {"python": self._remote_version(self.A, "dbos"), "app": "conformance/reference/dbos_app.py",
                                  "step_max_attempts": 3, "http_timeout_s": 2}
        elif self.stack == "thingsboard":
            wd = self._wd("tb-device")
            self.spawn_env(self.E, "tb-device", [py, f"{ref}/mqtt_device.py", "--mode", "thingsboard", "--broker",
                                                 f"{self.tb.ip('B')}:{P['tb_mqtt']}", "--device", "tb-1", "--plant", self.plant_url,
                                                 "--admin-port", str(P["tb_admin"]), "--admin-bind", self.listen],
                           wd, f"http://{e_ip}:{P['tb_admin']}/healthz", ["TB_DEVICE_TOKEN"], role="mqtt-device")
        elif self.stack.startswith("zephyr"):
            self.start_firmware()
        elif self.stack == "temporal":
            wd = self._wd("temporal-worker")
            self.task_queue = f"evicon-probe-{RELEASE}-{RUN.path.name}-{int(time.time())}"[:200]
            self.spawn(self.A, "temporal-worker", [py, f"{ref}/temporal_worker.py", "--temporal", self.t_addr,
                                                   "--task-queue", self.task_queue, "--plant-url", f"http://{self.tb.ip('C')}:{P['proxy_temporal']}",
                                                   "--health-port", str(P["worker_health"]), "--health-bind", self.listen],
                       wd, f"http://{self.tb.ip('A')}:{P['worker_health']}/healthz")

    def firmware_to_e(self, out: Path) -> str:
        """The Zephyr EviCon firmware (releases 068-074): the native build from deploy/setup/build_iot.sh (on
        VM-D, VM-E or VM-B) is copied to VM-E; returns its path there. Release 074: the device ID, the
        broker and the evidence mode are command-line options (-evc_device, -evc_broker, -evc_mode), so a
        build without them (release <= 073) is refused here. The binary goes through a temporary
        directory (bundles carry no binaries; its SHA-256 is recorded), the build configuration is kept."""
        out.mkdir(parents=True, exist_ok=True)
        src = None
        order = [os.environ.get("ZEPHYR_VM", "d").lower()] + [v for v in ("d", "e", "b") if v != os.environ.get("ZEPHYR_VM", "d").lower()]
        for v in order:
            if v not in ("d", "e", "b"):
                continue
            h = self.tb.host(v.upper())
            # release 077: the MCU steps write their own MCU_BUILD_OK / MCU_BUILD_FAIL line after the native BUILD_OK
            st = h.run("grep -E ' BUILD_(OK|FAIL) ' /var/lib/evicon/builds/zephyr.status 2>/dev/null | tail -n 1", check=False).stdout.strip()
            if " BUILD_OK " in st and h.run("test -s /root/evicon-build/zephyr-out/zephyr-cutoff.exe", check=False).returncode == 0:
                src = h
                break
        if src is None:
            raise StageFail("no Zephyr firmware build: run 'bash deploy/setup/build_iot.sh start zephyr' on VM-C first "
                            "(20-60 min), then 'bash deploy/setup/build_iot.sh status' until BUILD_OK")
        tmp = Path(tempfile.mkdtemp(prefix="evicon-fw-"))
        for f, dst in (("zephyr-cutoff.exe", tmp), ("zephyr-cutoff.config", out)):
            sh(["scp", "-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", f"{src.ssh}:/root/evicon-build/zephyr-out/{f}", str(dst / f)],
               timeout=300)
        conf = dict(l.split("=", 1) for l in (out / "zephyr-cutoff.config").read_text().splitlines() if l.startswith("CONFIG_EVC_"))
        zst = src.run("grep -m1 'zephyr revision' /var/lib/evicon/builds/zephyr.status", check=False).stdout.strip()
        exe = f"{self.root}/bin/zephyr-cutoff.exe"
        fw_sha = sha256(tmp / "zephyr-cutoff.exe")
        try:
            _push_file(self.tb, "E", tmp / "zephyr-cutoff.exe", exe)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        helptext = self.E.run(f"{shlex.quote(exe)} --help 2>&1 | head -c 20000", check=False, timeout=30).stdout
        if "evc_mode" not in helptext:
            raise StageFail(f"the firmware built on {src.name} has no -evc_mode option (built from release <= 073): rebuild it from this "
                            f"release with 'bash deploy/setup/build_iot.sh start zephyr' (the workspace is kept: 5-15 min)")
        self.facts["firmware"] = {"built_on": src.name, "sha256": fw_sha, "zephyr": zst.split(":", 1)[-1].strip() or "?", "path_on_E": exe,
                                  "config": {k: v for k, v in conf.items() if k in ("CONFIG_EVC_DEVICE_ID", "CONFIG_EVC_RING_SLOTS",
                                                                                  "CONFIG_EVC_TOMB_SLOTS", "CONFIG_EVC_DEDUP_HORIZON_MS",
                                                                                  "CONFIG_EVC_STOP", "CONFIG_EVC_GUARD_EVICTED")}}
        return exe

    def auth_firmware_to_e(self, out: Path) -> str:
        """Release 077 (`measure zephyr-auth`): the authenticated native build of deploy/setup/build_iot.sh is copied to
        VM-E; its device key stays in memory (self.auth_key: read from the build VM over SSH, passed to the check in
        its environment, never written on VM-C). Returns the firmware's path on VM-E."""
        out.mkdir(parents=True, exist_ok=True)
        src = None
        for v in [os.environ.get("ZEPHYR_VM", "d").lower()] + [x for x in ("d", "e", "b") if x != os.environ.get("ZEPHYR_VM", "d").lower()]:
            if v not in ("d", "e", "b"):
                continue
            h = self.tb.host(v.upper())
            st = h.run("grep 'MCU_BUILD_OK.*auth=' /var/lib/evicon/builds/zephyr.status 2>/dev/null | tail -n 1", check=False).stdout.strip()
            if st and h.run("test -s /root/evicon-build/zephyr-out/zephyr-auth.exe", check=False).returncode == 0:
                src = h
                break
        if src is None:
            raise StageFail("no authenticated firmware build (release 077): run 'bash deploy/setup/build_iot.sh start zephyr' on VM-C "
                            "(the Zephyr workspace is kept: 10-20 min), then 'bash deploy/setup/build_iot.sh status' until MCU_BUILD_OK")
        key = src.run("cat /root/evicon-build/zephyr-out/zephyr-auth.key", check=False).stdout.strip()
        if len(key) != 64:
            raise StageFail(f"{src.name}: no device key next to the authenticated firmware (rebuild: build_iot.sh start zephyr)")
        self.auth_key = key
        tmp = Path(tempfile.mkdtemp(prefix="evicon-fw-"))
        sh(["scp", "-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", f"{src.ssh}:/root/evicon-build/zephyr-out/zephyr-auth.exe",
            str(tmp / "zephyr-auth.exe")], timeout=300)
        zst = src.run("grep -m1 'zephyr revision' /var/lib/evicon/builds/zephyr.status", check=False).stdout.strip()
        exe = f"{self.root}/bin/zephyr-auth.exe"
        fw_sha = sha256(tmp / "zephyr-auth.exe")
        try:
            _push_file(self.tb, "E", tmp / "zephyr-auth.exe", exe)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        fp = src.run("cat /root/evicon-build/zephyr-out/footprint-auth.txt /root/evicon-build/zephyr-out/footprint-networked.txt 2>/dev/null",
                     check=False).stdout
        (out / "footprint.txt").write_text(fp)
        self.facts["firmware"] = {"built_on": src.name, "sha256": fw_sha, "zephyr": zst.split(":", 1)[-1].strip() or "?", "path_on_E": exe,
                                  "variant": "firmware/zephyr-auth (request and reply MACs, cutoff bound)"}
        return exe

    def auth_check(self) -> dict:
        """Release 077 (`measure zephyr-auth`): conformance/reference/zephyr_auth_check.py against the authenticated firmware
        (fresh flash, stop mode) through VM-B's broker, with the plant's truth. Writes auth.json and auth.md."""
        P = self.P
        res = {"config": "zephyr-auth-check", "capabilities": "n/a (security check, not a contract)", "level": "-", "rc": 0}
        env = dict(os.environ, EVC_AUTH_KEY=self.auth_key)
        rc, text = sh([sys.executable, "conformance/reference/zephyr_auth_check.py", "--broker", f"{self.tb.ip('B')}:{P['broker']}",
                       "--device", "zephyr-1", "--plant", self.plant_url, "--out", str(self.out)], timeout=300, check=False, env=env)
        if not (self.out / "auth.json").exists():
            res.update(rc=rc or 1, error=f"zephyr_auth_check: {text[-400:]}")
            return res
        a = json.loads((self.out / "auth.json").read_text())
        res.update(verdict=a["verdict"], failed=[c["case"] for c in a["cases"] if c["result"] != "PASS"], out=str(self.out / "auth.md"))
        if a["verdict"] != "PASS":
            RUN.problem("WARN", f"zephyr-auth: cases {res['failed']} failed (see {self.out / 'auth.md'})")
        RUN.log(f"zephyr-auth: {a['verdict']} ({len(a['cases']) - len(res['failed'])}/{len(a['cases'])} cases)")
        return res

    def mcu_firmware_to_e(self, out: Path) -> str:
        """Release 077 (`measure zephyr-mcu`): the MCU build of deploy/setup/build_iot.sh (firmware/zephyr-mcu: the
        sources of firmware/zephyr-cutoff unchanged, board mps2/an385) is copied to VM-E; returns its path there.
        Its broker address must be VM-B's and its plant 10.0.2.2:<zephyr_plant_tcp> (VM-E through QEMU's NAT): they
        are Kconfig values of the build (an MCU image has no command line)."""
        out.mkdir(parents=True, exist_ok=True)
        src = None
        for v in [os.environ.get("ZEPHYR_VM", "d").lower()] + [x for x in ("d", "e", "b") if x != os.environ.get("ZEPHYR_VM", "d").lower()]:
            if v not in ("d", "e", "b"):
                continue
            h = self.tb.host(v.upper())
            st = h.run("grep -c MCU_BUILD_OK /var/lib/evicon/builds/zephyr.status 2>/dev/null", check=False).stdout.strip()
            if st not in ("", "0") and h.run("test -s /root/evicon-build/zephyr-out/zephyr-mcu-qemu.elf", check=False).returncode == 0:
                src = h
                break
        if src is None:
            raise StageFail("no MCU firmware build (release 077): run 'bash deploy/setup/build_iot.sh start zephyr' on VM-C "
                            "(the Zephyr workspace is kept: 10-20 min), then 'bash deploy/setup/build_iot.sh status' until MCU_BUILD_OK")
        if self.E.run("command -v qemu-system-arm", check=False).returncode != 0:
            raise StageFail("VM-E: qemu-system-arm missing (bash deploy/setup/vm-e.sh installs it, release 077)")
        tmp = Path(tempfile.mkdtemp(prefix="evicon-fw-"))
        for f, dst in (("zephyr-mcu-qemu.elf", tmp), ("zephyr-mcu-qemu.config", out)):
            sh(["scp", "-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", f"{src.ssh}:/root/evicon-build/zephyr-out/{f}", str(dst / f)],
               timeout=300)
        # the networked nRF52840 build's flash/RAM footprint (same sources), for the report
        sh(["scp", "-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", f"{src.ssh}:/root/evicon-build/zephyr-out/footprint-networked.txt",
            str(out / "footprint-networked.txt")], timeout=120, check=False, quiet=True)
        conf = dict(l.split("=", 1) for l in (out / "zephyr-mcu-qemu.config").read_text().splitlines() if l.startswith("CONFIG_EVC_"))
        b_addr = MCU_FWD_BROKER if self.tb.ip("B").startswith("127.") else self.tb.ip("B")
        want = {"CONFIG_EVC_BROKER_ADDR": f'"{b_addr}"', "CONFIG_EVC_BROKER_PORT": str(self.P["broker"]),
                "CONFIG_EVC_PLANT_ADDR": '"10.0.2.2"', "CONFIG_EVC_PLANT_PORT": str(self.P["zephyr_plant_tcp"])}
        bad = {k: conf.get(k) for k, v in want.items() if conf.get(k) != v}
        if bad:
            shutil.rmtree(tmp, ignore_errors=True)
            raise StageFail(f"the MCU firmware on {src.name} was built for {bad}, this testbed needs {want}: rebuild it "
                            f"('bash deploy/setup/build_iot.sh start zephyr')")
        zst = src.run("grep -m1 'zephyr revision' /var/lib/evicon/builds/zephyr.status", check=False).stdout.strip()
        elf = f"{self.root}/bin/zephyr-mcu-qemu.elf"
        fw_sha = sha256(tmp / "zephyr-mcu-qemu.elf")
        try:
            _push_file(self.tb, "E", tmp / "zephyr-mcu-qemu.elf", elf)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        qv = self.E.run("qemu-system-arm --version | head -1", check=False).stdout.strip()
        self.facts["firmware"] = {"built_on": src.name, "sha256": fw_sha, "zephyr": zst.split(":", 1)[-1].strip() or "?", "path_on_E": elf,
                                  "target": "mps2/an385 (Arm Cortex-M3) under " + (qv or "qemu-system-arm"),
                                  "config": {k: v for k, v in conf.items() if k in ("CONFIG_EVC_DEVICE_ID", "CONFIG_EVC_RING_SLOTS",
                                                                                  "CONFIG_EVC_TOMB_SLOTS", "CONFIG_EVC_DEDUP_HORIZON_MS",
                                                                                  "CONFIG_EVC_STOP", "CONFIG_EVC_GUARD_EVICTED")}}
        return elf

    def start_firmware(self):
        """The firmware for `measure zephyr | zephyr-stop | zephyr-plain`: copied to VM-E and run there as a
        Linux process in the stack's evidence mode, device zephyr-1, VM-B's broker (release 074: options;
        068-073: Kconfig values of the build)."""
        P = self.P
        mode = self.spec["fw_mode"]
        wd = self._wd("zephyr-fw")
        if self.spec.get("fw_target") == "auth":
            exe = self.auth_firmware_to_e(self.out / "firmware")
            self.fw_argv = [exe, f"-flash={wd}/fw.bin", "-evc_device=zephyr-1", f"-evc_broker={self.tb.ip('B')}",
                            f"-evc_broker_port={P['broker']}", "-evc_plant=127.0.0.1", f"-evc_plant_port={P['zephyr_plant_tcp']}", f"-evc_mode={mode}"]
        elif self.spec.get("fw_target") == "mcu":
            elf = self.mcu_firmware_to_e(self.out / "firmware")
            # QEMU user-mode networking: the guest is 10.0.2.15; 10.0.2.2 is VM-E itself (the plant), and the
            # broker on VM-B is reached through QEMU's NAT. A loopback VM-B (the simulated testbed) is a loopback
            # address inside the guest too, so there the broker is the guest address 10.0.2.100, forwarded by QEMU
            nic = "user,model=lan9118"
            if self.tb.ip("B").startswith("127."):
                nic += f",guestfwd=tcp:{MCU_FWD_BROKER}:{P['broker']}-cmd:nc {self.tb.ip('B')} {P['broker']}"
            self.fw_argv = ["qemu-system-arm", "-machine", "mps2-an385", "-cpu", "cortex-m3", "-nographic", "-vga", "none",
                            "-monitor", "none", "-serial", f"file:{wd}/console.log", "-kernel", elf, "-nic", nic]
        else:
            exe = self.firmware_to_e(self.out / "firmware")
            self.fw_argv = [exe, f"-flash={wd}/fw.bin", "-evc_device=zephyr-1", f"-evc_broker={self.tb.ip('B')}",
                            f"-evc_broker_port={P['broker']}", "-evc_plant=127.0.0.1", f"-evc_plant_port={P['zephyr_plant_tcp']}", f"-evc_mode={mode}"]
        self.spawn(self.E, "zephyr-fw", self.fw_argv, wd, None)
        self.facts["firmware"]["mode"] = mode
        end, why = time.monotonic() + 45, "no answer"
        while time.monotonic() < end:
            if self.dead():
                tail = self.E.run(f"tail -n 15 {shlex.quote(wd)}/zephyr-fw.log", check=False).stdout.strip()
                raise StageFail(f"the firmware exited on VM-E: {tail[-800:]}")
            why = _zephyr_answers(self.tb.ip("B"), P["broker"], "zephyr-1", mode=mode)
            if why is None:
                RUN.log(f"Zephyr firmware zephyr-1 ({mode} mode) answers through the broker (built on "
                        f"{self.facts['firmware']['built_on']}, {self.facts['firmware']['zephyr']})")
                return
            time.sleep(2)
        raise StageFail(f"the Zephyr firmware on VM-E does not answer a ping through {self.tb.ip('B')}:{P['broker']} within 45 s "
                        f"({why}); log {wd}/zephyr-fw.log")

    def _wait_firmware(self, mode: str, wd: str, what: str):
        P = self.P
        end, why = time.monotonic() + 45, "no answer"
        while time.monotonic() < end:
            if self.dead():
                tail = self.E.run(f"tail -n 15 {shlex.quote(wd)}/zephyr-fw.log", check=False).stdout.strip()
                raise StageFail(f"the firmware exited on VM-E ({what}): {tail[-800:]}")
            why = _zephyr_answers(self.tb.ip("B"), P["broker"], "zephyr-1", mode=mode)
            if why is None:
                return
            time.sleep(1)
        raise StageFail(f"the Zephyr firmware on VM-E does not answer a ping {what} within 45 s ({why})")

    def reboot_check(self) -> dict:
        """Release 075 (`measure zephyr-reboot`): conformance/reference/zephyr_reboot_check.py before an owner
        reboot, the firmware stopped by its pid file and started again with the same flash file (the
        durable cutoff, claim high-water mark and tombstones survive, the RAM receipt ring does not),
        the check after it, and the plant's effects: what the firmware certifies across a reboot, which
        the measured contracts (fault model "owner up") do not cover. Writes reboot.json and reboot.md."""
        P, wd = self.P, self._wd("zephyr-fw")
        state = self.out / "reboot_state.json"
        state.unlink(missing_ok=True)
        broker = f"{self.tb.ip('B')}:{P['broker']}"
        script = "conformance/reference/zephyr_reboot_check.py"
        res = {"config": "zephyr-reboot-check", "capabilities": "n/a (fault-model check, not a contract)", "level": "-", "rc": 0}
        rc, text = sh([sys.executable, script, "before", "--broker", broker, "--device", "zephyr-1", "--state", str(state)], timeout=300, check=False)
        if rc != 0:
            res.update(rc=rc, error=f"before the reboot: {text[-400:]}")
            return res
        self.E.stop_all(wd, grace=1)  # the owner reboot: stopped by its pid file, nothing else
        pid = self.E.spawn("zephyr-fw", self.fw_argv, wd)  # same flash file; Host.spawn keeps the directory
        self.procs = [x for x in self.procs if not (x[1] == "zephyr-fw" and x[3] == wd)] + [(self.E, "zephyr-fw", pid, wd)]
        self._wait_firmware(self.spec["fw_mode"], wd, "after the reboot")
        RUN.log(f"zephyr-reboot: firmware restarted with its flash file (pid {pid})")
        rc, text = sh([sys.executable, script, "after", "--broker", broker, "--device", "zephyr-1", "--state", str(state)], timeout=300, check=False)
        if rc != 0:
            res.update(rc=rc, error=f"after the reboot: {text[-400:]}")
            return res
        plant = self.out / "reboot_plant.jsonl"
        plant.write_text(self.E.run(f"cat {shlex.quote(self._wd('plant'))}/plant.jsonl", check=False).stdout)
        rc, text = sh([sys.executable, script, "summarize", "--state", str(state), "--plant-log", str(plant)], timeout=60, check=False, quiet=True)
        try:
            summ = json.loads(text[text.find("{"):])
        except ValueError:
            res.update(rc=rc or 1, error=f"summarize: {text[-400:]}")
            return res
        (self.out / "reboot.json").write_text(json.dumps(summ, indent=1) + "\n")
        L = [f"# The Zephyr EviCon firmware across an owner reboot ({self.spec['fw_mode']} mode)", "",
             "The contracts measured for the firmware hold under the fault model \"owner up\". Here the firmware process is stopped and "
             "started again with its flash file (durable: cutoff, claim high-water mark in blocks of 64, tombstones; RAM: the receipt "
             "ring). Release " + RELEASE + ".", "",
             f"Safety across the reboot: duplicate effects {summ['duplicates'] or 'none'}, false STOPPED {summ['false_stopped'] or 'none'}, "
             f"false APPLIED {summ['false_applied'] or 'none'}; durable cutoff after the reboot {summ['cutoff_after_reboot']}.", "",
             "| identity (class) | request after the reboot | answer | reason |", "|---|---|---|---|"]
        for r in summ["rows"]:
            L.append(f"| {r['identity'] or '-'} ({r['what']}) | {r['kind']} | {r['outcome']} | {r['reason'] or (('cutoff ' + str(r['cutoff'])) if r['cutoff'] else '-')} |")
        L += ["", f"UNCERTAIN after the reboot: {', '.join(summ['uncertain_after_reboot']) or 'none'} (safe: never executed again, never "
              "certified; not live: neither a query, a stop nor a fence resolves them). Effects per identity (plant): "
              f"{json.dumps(summ['effects'])}."]
        (self.out / "reboot.md").write_text("\n".join(L) + "\n")
        res.update(safe=summ["safe"], uncertain_after_reboot=summ["uncertain_after_reboot"], out=str(self.out / "reboot.md"))
        if not summ["safe"]:
            RUN.problem("WARN", f"zephyr-reboot: the firmware was not safe across the reboot: {summ['duplicates']} "
                                f"{summ['false_stopped']} {summ['false_applied']} (see {self.out / 'reboot.md'})")
        RUN.log(f"zephyr-reboot: safe={summ['safe']}, UNCERTAIN after the reboot: {summ['uncertain_after_reboot']}")
        return res

    # ---- probe configs ----------------------------------------------------
    def derive(self, name) -> dict:
        P, tb = self.P, self.tb
        cfg = json.loads(tb.substitute((REPO / "conformance" / "configs" / f"{name}.json").read_text()))
        ac = cfg["adapter_config"]
        if self.uses_plant:
            ac["plant_url"] = self.plant_url
        lh = "127.0.0.1"
        if self.stack in ("mosquitto", "emqx"):
            px = P["proxy_mqtt" if self.stack == "mosquitto" else "proxy_emqx"]
            ac.update(broker=f"127.0.0.1:{px}", device_admin_url=f"http://{tb.ip('E')}:{P['mqtt_admin']}")
            cfg["faults"] = {"kind": "proxy", "proto": "tcp", "listen_host": lh, "listen_port": px,
                             "target_host": tb.ip("B"), "target_port": P[self.owner["port"]], "linger_s": 3.0}
            cfg["system_version"] = json.dumps(self.facts.get("broker", {}))[:300]
        elif self.stack.startswith("zephyr"):
            # data path through the fault proxy; query/fence on a direct connection to the broker, so a
            # delayed command does not hold the control messages behind it in the same TCP stream
            ac.update(broker=f"127.0.0.1:{P['proxy_mqtt']}", control_broker=f"{tb.ip('B')}:{P['broker']}")
            cfg["faults"] = {"kind": "proxy", "proto": "tcp", "listen_host": lh, "listen_port": P["proxy_mqtt"],
                             "target_host": tb.ip("B"), "target_port": P["broker"], "linger_s": 3.0}
            fw = self.facts.get("firmware", {})
            cfg["system_version"] = (f"Zephyr {fw.get('zephyr', '?')} EviCon firmware -evc_mode={self.spec['fw_mode']} sha256 "
                                     f"{str(fw.get('sha256', '?'))[:16]} + "
                                     + json.dumps(self.facts.get("broker", {}).get("images", {})))[:300]
        elif self.stack == "nats":
            ac.update(data_server=f"nats://127.0.0.1:{P['proxy_nats']}", control_server=f"nats://{tb.ip('B')}:{P['nats']}")
            cfg["faults"] = {"kind": "proxy", "proto": "tcp", "listen_host": lh, "listen_port": P["proxy_nats"],
                             "target_host": tb.ip("B"), "target_port": P["nats"], "linger_s": 3.0}
            cfg["system_version"] = json.dumps(self.facts.get("broker", {}))[:300]
        elif self.stack == "minio":
            # access_key/secret_key stay ${MINIO_ROOT_USER}/${MINIO_ROOT_PASSWORD}: the probe runner reads its environment
            ac.update(data_url=f"http://127.0.0.1:{P['proxy_minio']}", control_url=f"http://{tb.ip('B')}:{P['minio']}")
            cfg["faults"] = {"kind": "proxy", "proto": "tcp", "listen_host": lh, "listen_port": P["proxy_minio"],
                             "target_host": tb.ip("B"), "target_port": P["minio"], "linger_s": 3.0}
            cfg["system_version"] = json.dumps(self.facts.get("broker", {}))[:300]
        elif self.stack == "dbos":
            ac.update(app_url=f"http://{tb.ip('A')}:{P['dbos_app']}")
            # faults between the DBOS step and the plant (like Temporal): the step reaches the plant through VM-C
            cfg["faults"] = {"kind": "proxy", "proto": "tcp", "listen_host": self.listen, "listen_port": P["proxy_dbos"],
                             "target_host": tb.ip("E"), "target_port": P["plant"], "linger_s": 3.0}
            cfg["system_version"] = (f"DBOS Transact {self.facts.get('dbos', {}).get('python', '?')} (python) on "
                                     + json.dumps(self.facts.get("broker", {}).get("images", {})))[:300]
        elif self.stack == "k3s":
            ac.update(data_server=f"https://127.0.0.1:{P['proxy_k3s']}", control_server=f"https://{tb.ip('B')}:{P['k3s_api']}",
                      ca_file=str(self.k3s_dir / "server-ca.crt"), data_ca_file=self.k3s_proxy_tls["cert_file"])
            # TLS ends at the fault proxy so that faults act on the HTTP request and reply, not the handshake
            cfg["faults"] = {"kind": "proxy", "proto": "tcp", "listen_host": lh, "listen_port": P["proxy_k3s"],
                             "target_host": tb.ip("B"), "target_port": P["k3s_api"], "linger_s": 3.0,
                             "tls": dict(self.k3s_proxy_tls, upstream_ca_file=str(self.k3s_dir / "server-ca.crt"),
                                         upstream_server_hostname=tb.ip("B"))}
            cfg["system_version"] = f"k3s {self.facts.get('k3s', {}).get('server_version', '?')} (API server only)"
        elif self.stack == "thingsboard":
            ac.update(data_url=f"http://127.0.0.1:{P['proxy_tb']}", control_url=f"http://{tb.ip('B')}:{P['tb_http']}",
                      device_uuid=self.facts["thingsboard"]["device_uuid"], device_admin_url=f"http://{tb.ip('E')}:{P['tb_admin']}")
            cfg["faults"] = {"kind": "proxy", "proto": "tcp", "listen_host": lh, "listen_port": P["proxy_tb"],
                             "target_host": tb.ip("B"), "target_port": P["tb_http"], "linger_s": 3.0}
            cfg["system_version"] = ("ThingsBoard CE " + json.dumps(self.facts.get("broker", {}).get("images", {})))[:300]
        elif self.stack == "coap":
            ac["control_uri"] = f"coap://{tb.ip('E')}:{P['coap']}/actuate"
            cfg["faults"] = {"kind": "proxy", "proto": "udp", "listen_host": lh, "listen_port": P["proxy_coap"],
                             "target_host": tb.ip("E"), "target_port": P["coap"]}
            cfg["system_version"] = "aiocoap " + self._remote_version(self.E, "aiocoap")
            if tb.local:
                # local self-test: EXCHANGE_LIFETIME is not shortened, so skip the 260 s P5 gap
                cfg["probes"] = [p for p in cfg["probes"] if p != "P5"]
        elif self.stack == "temporal":
            ac.update(temporal=self.t_addr, task_queue=self.task_queue)
            cfg["faults"] = {"kind": "proxy", "proto": "tcp", "listen_host": self.listen, "listen_port": P["proxy_temporal"],
                             "target_host": tb.ip("E"), "target_port": P["plant"], "linger_s": 3.0}
            t = self.facts["temporal"]
            cfg["system_version"] = (f"Temporal Server {t.get('server_version', '?')} ({t['mode']}"
                                     + (f", {t['cli']}" if t.get("cli") else "") + "); temporalio (python) "
                                     + self._remote_version(self.A, "temporalio"))
        if self.a.quick:
            cfg["repetitions"] = 1
        cfg.pop("expect", None)
        return cfg

    def _remote_version(self, host, dist):
        return host.run(f"python3 -c 'import importlib.metadata as m; print(m.version(\"{dist}\"))' 2>/dev/null", check=False).stdout.strip() or "?"

    def probe(self, name) -> dict:
        cfg = self.derive(name)
        derived = self.out / "configs" / f"{name}.json"
        derived.parent.mkdir(parents=True, exist_ok=True)
        derived.write_text(json.dumps(cfg, indent=2) + "\n")
        # not quiet: a CoAP measurement takes an hour, every probe line shows progress
        rc, text = sh([sys.executable, "conformance/run_probe.py", "run", "--config", str(derived), "--out", str(self.out / "runs")],
                      timeout=4 * 3600, check=False)
        start = text.find("{\n")
        try:
            summary = json.loads(text[start:]) if start >= 0 else {}
        except ValueError:
            summary = {}
        if not summary:
            summary = {"error": text[-800:]}
        summary.update(config=name, rc=rc, compare_with=cfg.get("compare_with", ""))
        gone = self.dead()
        if gone:
            # e.g. the Temporal worker lost its server: every later probe would only measure that
            summary["error"] = "process(es) exited during the measurement: " + "; ".join(gone)
        hyp = REPO / "experiments" / "contracts" / f"{cfg.get('compare_with', '')}.json"
        if hyp.is_file():
            summary["hypothesis_capabilities"] = json.loads(hyp.read_text()).get("capabilities", [])
        RUN.log(f"MEASURED {name}: capabilities={summary.get('capabilities')} level={summary.get('level')} "
                f"late_effect={summary.get('late_effect')} errors={summary.get('errors') or summary.get('error') or '-'}")
        return summary

    # ---- cleanup ----------------------------------------------------------
    def cleanup(self):
        for host, wd in reversed(self.started):
            try:
                host.stop_all(wd)
            except Exception as e:  # noqa: BLE001 - keep cleaning up
                RUN.problem("WARN", f"stopping {wd} on {host.name}: {e}")
        try:
            self.stop_broker()
        except Exception as e:  # noqa: BLE001
            RUN.problem("WARN", f"stopping the broker: {e}")
        if not self.tb.local:
            for role, host in (("E", self.E), ("A", self.A), ("B", self.B), ("D", self.D)):
                if host.run(f"test -d {shlex.quote(self.sd)}", check=False).returncode == 0:
                    try:
                        host.fetch_dir(self.sd, self.out / "raw" / f"VM-{role}")
                    except Exception as e:  # noqa: BLE001
                        RUN.problem("WARN", f"fetching {self.sd} from VM-{role}: {e}")


# ------------------------------------------------------------------ RQ4, reports, twin v2 (release 068)
RQ4_CONFIGS = ["rq4_load_r1", "rq4_load_r5", "rq4_load_r20"]


def _sampler(tb, role, names, out_name, start):
    """Starts (start=True) or stops EviCon's resource sampler on a VM: evaluation/rq4_sampler.py
    in /var/lib/evicon/overhead/sampler-<role>, pid file there (stopped only by that pid file)."""
    h = tb.host(role)
    wd = f"{Path(tb.cfg['state_dir']) if not tb.local else RUN.path / 'local-hosts' / 'state'}/overhead/sampler-{role}"
    if start:
        if not tb.local:
            _push_tree(tb, role, ["evaluation/rq4_sampler.py"], tb.cfg["remote_root"])
        root = str(REPO) if tb.local else tb.cfg["remote_root"]
        h.stop_all(wd)
        h.run(f"rm -rf {shlex.quote(wd)} && mkdir -p {shlex.quote(wd)}")
        pid = h.spawn("sampler", ["python3", f"{root}/evaluation/rq4_sampler.py", "--out", f"{wd}/{out_name}", "--interval", "1",
                                  "--names", names], wd)
        RUN.log(f"resource sampler on {h.name} (pid {pid}): {names}")
        return wd
    h.stop_all(wd)
    local = RUN.path / "overhead" / "samples"
    local.mkdir(parents=True, exist_ok=True)
    try:
        h.fetch_dir(wd, local / f"VM-{role}")
    except Exception as e:  # noqa: BLE001
        RUN.problem("WARN", f"fetching the samples from {h.name}: {e}")
    return wd


OTHER_LOAD_PCT = 10.0  # release 077: CPU share (ps %CPU) above which another program is reported


def _warn_other_load(tb):
    """RQ4 and the latency control measure the CPU and latency of EviCon's processes; other programs on
    VM-A/B/E share the same cores. Release 069: listed as a WARN, never stopped (the operator decides).
    Release 077: every process outside EviCon whose CPU share exceeds OTHER_LOAD_PCT (ps %CPU over its
    lifetime), not only the programs of one earlier deployment."""
    pat = ("ps -eo pid=,pcpu=,args= --sort=-pcpu | awk -v t=%s '$2 > t && $3 !~ /(^|\\/)(evicon-[a-z0-9-]+|ps|awk|sshd)$/' "
           "| head -n 8" % OTHER_LOAD_PCT)
    for role in ("A", "B", "E"):
        try:
            found = tb.run(role, pat, check=False, timeout=30).stdout.strip()
        except Exception as e:  # noqa: BLE001
            RUN.log(f"overhead: could not list processes on VM-{role}: {e}")
            continue
        if found:
            RUN.problem("WARN", f"overhead: other program(s) use more than {OTHER_LOAD_PCT:g} % CPU on VM-{role} and share "
                                f"its cores with the measured processes: {' | '.join(found.splitlines())[:400]} "
                                f"(EviCon does not stop them; the operator decides)")


def cmd_overhead(tb, a):
    """python3 evicon_review.py overhead: RQ4 load campaigns (no faults; 1, 5, 20 commands/s per
    device) with CPU/RSS sampling of the authorities (VM-A) and devices (VM-E), then
    evaluation/overhead.py over them and over the experiment A campaigns of this run."""
    out = RUN.path / "overhead"
    out.mkdir(parents=True, exist_ok=True)
    if not tb.local:
        _warn_other_load(tb)
    states = []
    for name in RQ4_CONFIGS:
        cfg = json.loads(tb.substitute((REPO / "evaluation" / "campaigns" / f"{name}.json").read_text()))
        if a.quick:
            cfg.update(episodes=1, duration_s=60)
        cfg = _localize(cfg) if tb.local else _remote(cfg, tb)
        _sweep_leftovers(tb, ("A", "E"))
        # one samples file per configuration and start: fetch_dir copies by name, so a resumed or
        # later configuration must not overwrite the samples of an earlier one
        stamp = time.strftime("%Y%m%dT%H%M%S")
        _sampler(tb, "A", "evicon-authority", f"samples-A-{name}-{stamp}.jsonl", True)
        _sampler(tb, "E", "evicon-device,evicon-plant", f"samples-E-{name}-{stamp}.jsonl", True)
        try:
            st = _run_campaign(cfg, RUN.path / "campaigns", tb, new=a.new_campaign, max_cells=a.max_cells)
        finally:
            _sampler(tb, "A", "", "", False)
            _sampler(tb, "E", "", "", False)
        states.append(st)
        if not st["complete"]:
            return "PARTIAL", f"{name}: {st['cells_done']}/{st['cells_total']} cells; run the same command again to resume"
        if st["invalid"]:
            RUN.problem("WARN", f"{name}: {len(st['invalid'])} INVALID cell(s): {st['invalid'][:6]}")
    runs = [str(Path(s["run"])) for s in states] + [str(p) for p in sorted((RUN.path / "campaigns").glob("expA*/2*"))
                                                    if (p / "activation.json").exists()]
    args = [sys.executable, "evaluation/overhead.py", "--out", str(out)]
    for r in runs:
        args += ["--campaign", r]
    for smp in sorted((out / "samples").rglob("samples-*.jsonl")):
        args += ["--samples", str(smp)]
    sh(args, timeout=3600, quiet=True)
    rows = json.loads((out / "overhead.json").read_text())
    rq = [r for r in rows if r["campaign"].startswith("rq4")]
    hi = max(rq, key=lambda r: r["offered_per_s"] or 0) if rq else None
    summ = (f"{len(rq)} load groups; at {hi['offered_per_s']} cmd/s ({hi['contract']}, {hi['policy']}): admit p50/p95 "
            f"{hi['admit_ms_p50']}/{hi['admit_ms_p95']} ms, decide p50 {hi['decide_ms_p50']} ms, authority CPU "
            f"{hi['authority_cpu_pct_mean']} %, RSS {hi['authority_rss_mb_max']} MB" if hi else "no RQ4 rows")
    return "PASS", summ + f" -> {out / 'overhead.md'}"


SCALE_CONFIGS = ["rq4_scale_d8", "rq4_scale_d16", "rq4_scale_d32"]  # release 077


def cmd_scale(tb, a):
    """Release 077 (PREREG_review_077.md, run Scale): the RQ4 load (no faults, 5 commands/s per device) with 8, 16 and
    32 domains (one authority on VM-A and one device on VM-E per domain), with CPU/RSS sampling as `overhead`, then
    evaluation/overhead.py over these runs and the 4-domain run rq4-load-r5 of this review run (if `overhead` ran).
    About 1.2 h (24 cells)."""
    out = RUN.path / "overhead" / "scale"
    out.mkdir(parents=True, exist_ok=True)
    if not tb.local:
        _warn_other_load(tb)
    states = []
    for name in SCALE_CONFIGS:
        cfg = json.loads(tb.substitute((REPO / "evaluation" / "campaigns" / f"{name}.json").read_text()))
        if a.quick:
            cfg.update(episodes=1, duration_s=60)
        cfg = _localize(cfg) if tb.local else _remote(cfg, tb)
        _sweep_leftovers(tb, ("A", "E"))
        stamp = time.strftime("%Y%m%dT%H%M%S")
        _sampler(tb, "A", "evicon-authority", f"samples-A-{name}-{stamp}.jsonl", True)
        _sampler(tb, "E", "evicon-device,evicon-plant", f"samples-E-{name}-{stamp}.jsonl", True)
        try:
            st = _run_campaign(cfg, RUN.path / "campaigns", tb, new=a.new_campaign, max_cells=a.max_cells)
        finally:
            _sampler(tb, "A", "", "", False)
            _sampler(tb, "E", "", "", False)
        states.append(st)
        if not st["complete"]:
            return "PARTIAL", f"{name}: {st['cells_done']}/{st['cells_total']} cells; run the same command again to resume"
        if st["invalid"]:
            RUN.problem("WARN", f"{name}: {len(st['invalid'])} INVALID cell(s): {st['invalid'][:6]}")
    runs = [str(Path(s["run"])) for s in states] + [str(p) for p in sorted((RUN.path / "campaigns").glob("rq4-load-r5/2*"))]
    args = [sys.executable, "evaluation/overhead.py", "--out", str(out)]
    for r in runs:
        args += ["--campaign", r]
    for smp in sorted((RUN.path / "overhead" / "samples").rglob("samples-*.jsonl")):
        if "rq4_scale" in smp.name or "rq4_load_r5" in smp.name:
            args += ["--samples", str(smp)]
    sh(args, timeout=3600, quiet=True)
    rows = [r for r in json.loads((out / "overhead.json").read_text()) if r["campaign"].startswith("rq4")]
    rows.sort(key=lambda r: (r["offered_per_s"] or 0))
    summ = "; ".join(f"{r['campaign']} {r['contract']}/{r['policy']}: {r['offered_per_s']} cmd/s, admit p50/p95 {r['admit_ms_p50']}/"
                     f"{r['admit_ms_p95']} ms, CPU {r['authority_cpu_pct_mean']} %" for r in rows if r["policy"] == "EVICON" and r["contract"] == "K2")
    return "PASS", (summ or "no rows") + f" -> {out / 'overhead.md'}"


def cmd_qos_dup(tb, a):
    """Release 077 (PREREG_review_077.md, run Q0): conformance/reference/qos_dup_check.py against Eclipse
    Mosquitto 2.0.20 on VM-B (EviCon's compose project evicon-probe-owner, as for `measure mosquitto`, brought
    down afterwards). Commands are published at QoS 1 (the C2/C3 gateway's binding) and at QoS 0 through a link
    on VM-C that loses the broker's PUBACK of every 5th command and resets; a subscriber in the firmware's
    configuration counts the deliveries of every command. About 10 minutes."""
    if importlib_find("paho.mqtt.client") is None:
        raise StageFail("VM-C: Python module paho.mqtt.client missing (deploy/setup/vm-c.sh installs it)")
    if tb.local and not shutil.which("mosquitto"):
        raise StageFail("--local: needs the mosquitto binary on this host")
    m = _Measure(tb, a, "mosquitto")
    m.out = RUN.path / "measure" / "qos-dup"
    m.sd = m.sd.rsplit("/", 1)[0] + "/qos-dup"
    m.out.mkdir(parents=True, exist_ok=True)
    if not tb.local:
        _sweep_leftovers(tb, ("B",))
    n = 60 if a.quick else 300
    try:
        m.start_broker()
        broker = f"{'127.0.0.1' if tb.local else tb.ip('B')}:{m.P['broker']}"
        m.facts["qos_dup"] = {"broker": broker, "commands": n, "every": 5}
        rc, text = sh([sys.executable, "conformance/reference/qos_dup_check.py", "--broker", broker, "--out", str(m.out),
                       "-n", str(n), "--every", "5"], timeout=2400, quiet=True, check=False)
    finally:
        m.cleanup()
        (m.out / "owner.json").write_text(json.dumps(m.facts, indent=2, default=str) + "\n")
    if rc != 0:
        raise StageFail(f"qos_dup_check failed: {text[-600:]}")
    res = {r["binding"]: r for r in json.loads((m.out / "qos_dup_check.json").read_text())["results"]}
    return "PASS", ("; ".join(f"{b}: {r['commands']} commands, {r['resets']} resets, delivered twice or more {r['commands_delivered_twice_or_more']}, "
                              f"lost {r['lost_commands']}" for b, r in res.items()) + f" -> {m.out / 'QOS_DUP_CHECK.md'}")


def cmd_latency_control(tb, a):
    """python3 evicon_review.py latency-control (release 077, PREREG_review_077.md): the RQ4 load
    (rq4_load_r1/r5/r20: the same seeded arrivals and generator design as campaign.py) from VM-C against
    evicon-noop on VM-A, an endpoint that admits every command at once, on the authorities' ports; then
    evaluation/latency_control.py splits the client-observed admission latency into the load generator,
    the network and the endpoint. evicon-noop runs in /var/lib/evicon/overhead/noop and is stopped by its
    pid file (also on failure)."""
    sys.path.insert(0, str(REPO / "evaluation"))
    import latency_control as LC  # noqa: E402
    from campaign import DEFAULT_PORTS, wait_http
    out = RUN.path / "overhead" / "latency_control"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)
    if not (REPO / "bin" / "evicon-noop").exists():
        raise StageFail("no bin/evicon-noop: run 'python3 evicon_review.py build distribute' first")
    if tb.local:
        res = LC.local(out, 1 if a.quick else 3, 30 if a.quick else None)
        parts = ["%g/s: client p50 %s ms" % (r["rate_per_device_s"], r["client_ms"]["p50"]) for r in res["loads"]]
        return "PASS", "local: " + "; ".join(parts) + f" -> {out}"
    root, h = tb.cfg["remote_root"], tb.host("A")
    if h.sha256([f"{root}/bin/evicon-noop"]).get("evicon-noop") != sha256(REPO / "bin" / "evicon-noop"):
        raise StageFail("VM-A runs another evicon-noop than bin/: run 'python3 evicon_review.py distribute' first")
    _warn_other_load(tb)
    _sweep_leftovers(tb, ("A",))
    wd = f"{tb.cfg['state_dir']}/overhead/noop"
    clients, noops = [], []
    try:
        for name in RQ4_CONFIGS:
            cfg = json.loads(tb.substitute((REPO / "evaluation" / "campaigns" / f"{name}.json").read_text()))
            ports = [DEFAULT_PORTS["authority"] + d for d in range(cfg["devices"])]
            busy = h.ports_in_use(ports)
            if busy:
                raise StageFail(f"VM-A: ports {busy} are in use (an authority still running?); nothing was started")
            h.stop_all(wd)
            h.run(f"mkdir -p {shlex.quote(wd)}")
            log = f"{wd}/noop-{name}.jsonl"
            pid = h.spawn("noop", [f"{root}/bin/evicon-noop", "-listen", ",".join(f"{tb.ip('A')}:{p}" for p in ports), "-log", log], wd)
            RUN.log(f"evicon-noop on VM-A (pid {pid}) on ports {ports} for {name}")
            urls = [f"http://{tb.ip('A')}:{p}" for p in ports]
            for u in urls:
                try:
                    wait_http(f"{u}/healthz", timeout=20)
                except RuntimeError as e:
                    raise StageFail(f"evicon-noop on VM-A does not answer at {u}: {e}") from e
            for ep in range(1 if a.quick else cfg["episodes"]):
                n = LC.generate(cfg, ep, urls, out / f"clients-{name}.jsonl", duration_s=30 if a.quick else None)
                RUN.log(f"{name} episode {ep}: {n} commands")
            h.stop_all(wd)
            h.fetch_dir(wd, out / "raw" / "VM-A")
            clients.append(out / f"clients-{name}.jsonl")
            noops.append(out / "raw" / "VM-A" / f"noop-{name}.jsonl")
    finally:
        h.stop_all(wd)
    ovh = RUN.path / "overhead" / "overhead.json"
    res = LC.analyse(clients, [x for x in noops if x.exists()], str(ovh) if ovh.exists() else None)
    (out / "latency_control.json").write_text(json.dumps(res, indent=2) + "\n")
    (out / "LATENCY_CONTROL.md").write_text(LC.markdown(res))
    return "PASS", ("; ".join(f"{r['rate_per_device_s']:g}/s: gen p50/p95 {r['gen_ms']['p50']}/{r['gen_ms']['p95']} ms, "
                              f"client {r['client_ms']['p50']}/{r['client_ms']['p95']} ms" for r in res["loads"]) + f" -> {out}")


def _find_runs(base: Path, name: str):
    return [p for p in sorted(base.glob(f"campaigns/{name}/2*")) if (p / "activation.json").exists() and (p / "analysis" / "report.json").exists()]


def _find_complete_runs(base: Path, name: str):
    """Release 072: complete runs of a campaign with its analysis, gated or not (a config without an
    activation gate, such as the negative controls or experiment L, writes no activation.json)."""
    out = []
    for p in sorted(base.glob(f"campaigns/{name}/2*")):
        if not (p / "analysis" / "report.json").exists():
            continue
        try:
            if json.loads((p / "driver_status.json").read_text()).get("complete"):
                out.append(p)
        except (OSError, ValueError):
            continue
    return out


def _report_sources(where: str) -> list:
    """Release 074 (`report --sources-of DIR`): the runs the newest report under DIR (a release directory
    such as /root/EVICON_TOIT_073, or one review run) was written from, then that review run itself. The
    sources are the `- ` lines under "sources:" in its REPORT.md (every release since 070 writes them)."""
    d = Path(where).resolve()
    reps = sorted(d.glob("report/REPORT.md")) or sorted(d.glob("results/review-*/report/REPORT.md"), key=lambda p: p.parent.parent.name)
    if not reps:
        raise StageFail(f"--sources-of {where}: no report/REPORT.md there (give a release directory, e.g. /root/EVICON_TOIT_073)")
    rep = reps[-1]
    out = []
    for line in rep.read_text().splitlines():
        m = re.match(r"^- `(/[^`]+)`$", line.strip())
        if m:
            out.append(m.group(1))
        elif out and line.startswith("- ["):
            break
    out.append(str(rep.parent.parent))
    missing = [x for x in out if not Path(x).is_dir()]
    if missing:
        raise StageFail(f"--sources-of {where}: {rep} names runs that are gone: {missing[:4]}")
    RUN.log(f"report sources from {rep}: {len(out)} run(s)")
    return out


def _archive_sources(where: str, missing_ok: bool = False) -> list:
    """Release 078 (`report --archive DIR`): the runs the newest report in the archive was written from,
    then that review run itself, as `--sources-of` reads them from a release directory, but in the archive
    scripts/import_results.py wrote from the result bundles (DIR/runs/review-*). A run the report names
    that the archive lacks stops the report (its bundle was not imported), unless --archive-missing-ok; 079: so does a
    campaign run that report names."""
    sys.path.insert(0, str(REPO / "scripts"))
    from import_results import newest_report_campaigns, newest_report_sources
    d = Path(where).resolve()
    if not [p for p in (d / "runs").glob("review-*") if p.is_dir()]:
        raise StageFail(f"--archive {where}: no review run under {d / 'runs'} (python3 evicon_review.py import-results "
                        f"<bundles> --archive {where} first)")
    rep, srcs = newest_report_sources(d)
    if rep is None:
        raise StageFail(f"--archive {where}: no review run there holds a report (report/REPORT.md); import the bundle of "
                        f"the latest release that wrote one, or give the runs with --from")
    gone = [f"{n} (import EVICON_{r or '<release>'}_result*.zip)" for n, r, ok in srcs if not ok]
    # release 079: a review run can be there while some of its campaign runs are not (only some incremental bundles)
    gone += [f"campaign run {n} (import the other incremental bundles of its release)" for n in newest_report_campaigns(d)[1]]
    if gone and not missing_ok:
        raise StageFail(f"--archive {where}: {rep} read {len(gone)} run(s) the archive lacks: {gone}; import them "
                        f"(python3 evicon_review.py import-results <zip> --archive {where}), or give --archive-missing-ok")
    if gone:
        RUN.log(f"WARN: report without {len(gone)} run(s) the earlier report read: {gone}")
    runs = [str(d / "runs" / n) for n, _, ok in srcs if ok]
    RUN.log(f"report sources from the archive {d} (those of {rep.relative_to(d)}): {len(runs)} run(s)")
    return runs


def cmd_import_results(a) -> int:
    """Release 078: python3 evicon_review.py import-results <bundle.zip | dir> ... [--archive DIR] [--lean]: the
    result bundles of earlier releases (EVICON_<release>_result*.zip) -> one archive that `report --archive DIR`
    reads (scripts/import_results.py). Needs no VM and no review run."""
    if len(a.stages) < 2:
        raise SystemExit("usage: python3 evicon_review.py import-results <bundle.zip | directory with bundles> ... "
                         "[--archive /root/evicon-archive] [--lean]")
    args = [sys.executable, str(REPO / "scripts" / "import_results.py"), *a.stages[1:], "--archive", a.archive or "/root/evicon-archive"]
    if a.lean:
        args.append("--lean")
    if a.force_space:
        args.append("--force-space")
    return subprocess.run(args).returncode


def _sources(a) -> list:
    """Run directories a report reads: those of --archive (078), of --sources-of (074), the --from ones (in the
    order given), then this run."""
    out = []
    for s in ((_archive_sources(a.archive, getattr(a, "archive_missing_ok", False)) if getattr(a, "archive", "") else [])
              + (_report_sources(a.sources_of) if getattr(a, "sources_of", "") else []) + list(a.from_run or [])):
        p = Path(s).resolve()
        if not p.is_dir():
            raise StageFail(f"--from {s}: not a directory (give a run directory, e.g. /root/EVICON_TOIT_067/results/review-...)")
        if p not in out:
            out.append(p)
    if RUN.path.resolve() not in out:
        out.append(RUN.path.resolve())
    return out


def cmd_report(tb, a):
    """python3 evicon_review.py report [--from DIR ...]: paper-ready tables from this run and the
    runs given with --from (e.g. the 066 and 067 runs): confirmatory experiment A against its plan,
    fence/evidence audit, RQ4 overhead, measured contract map (newest measurement of each
    contract), IoT build footprint."""
    srcs = _sources(a)
    out = RUN.path / "report"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)
    done, index = [], ["# EviCon report", "", f"Release {RELEASE}; written {now()}; sources:", ""] + [f"- `{s}`" for s in srcs] + [""]
    camps = [p for s in srcs for p in sorted(s.glob("campaigns/*/2*")) if (p / "cells").exists()]
    if camps:
        sh([sys.executable, "evaluation/fence_audit.py", "--out", str(out / "fence_audit")] + sum((["--campaign", str(c)] for c in camps), []),
           timeout=1800, quiet=True)
        done.append("fence audit")
        index.append("- [Fence and evidence audit](fence_audit/fence_audit.md)")
    # release 070: the earliest run of each config is the preregistered one (primary); later K0 runs
    # are replications under the same plan (069: the 067 run on the fixed emulator)
    k0 = sorted({r for s in srcs for r in _find_runs(s, "expA-confirm-k0")}, key=lambda r: r.name)
    k2 = sorted({r for s in srcs for r in _find_runs(s, "expA-confirm-k2")}, key=lambda r: r.name)
    if k0 and k2:
        twins = sorted(t for s in srcs for t in s.glob("sim-twin/iot_dosing_testbed-200ep-*/episodes.csv"))
        args = [sys.executable, "evaluation/confirm_report.py", "--k0", str(k0[0]), "--k2", str(k2[0]), "--out", str(out / "confirm_expA")]
        for r in k0[1:]:
            args += ["--k0-rep", str(r)]
        for r in k2[1:]:  # release 072: R-K2
            args += ["--k2-rep", str(r)]
        if twins:
            args += ["--sim", str(twins[-1])]
        rc, text = sh(args, timeout=1800, quiet=True, check=False)
        if rc == 0:
            done.append("confirmatory experiment A")
            index.append("- [Confirmatory experiment A, against its preregistration](confirm_expA/RESULTS.md)")
        else:
            RUN.problem("WARN", f"confirm_report failed: {text[-400:]}")
    # release 071: experiment B2 (resolution cadence) against the newest K0 run (EVICON at 1 s, same seed)
    b2 = sorted({r for s in srcs for r in _find_runs(s, "expB2-cadence")}, key=lambda r: r.name)
    if b2 and k0:
        rc, text = sh([sys.executable, "evaluation/cadence_report.py", "--b2", str(b2[-1]), "--ref", str(k0[-1]),
                       "--out", str(out / "cadence")], timeout=900, quiet=True, check=False)
        if rc == 0:
            done.append("experiment B2")
            index.append("- [Experiment B2: resolution cadence, against its preregistration](cadence/RESULTS.md)")
        else:
            RUN.problem("WARN", f"cadence_report failed: {text[-400:]}")
    # release 072: experiments whose plan carries a pairs-spec (W, S, L, NEG), against that plan; the
    # earliest complete run of each campaign is the preregistered one
    for plan in sorted((REPO / "evaluation" / "prereg").glob("*.md")):
        m = re.search(r"```pairs-spec\n(.*?)```", plan.read_text(), re.S)
        if not m:
            continue
        try:
            spec = json.loads(m.group(1))
        except ValueError as e:
            RUN.problem("WARN", f"{plan.name}: pairs-spec is not JSON ({e})")
            continue
        runargs = []
        for key, want in spec.get("runs", {}).items():
            found = sorted({r for s in srcs for r in _find_complete_runs(s, want.get("campaign", ""))}, key=lambda r: r.name)
            if found:
                runargs += ["--run", f"{key}={found[0]}"]
        if not runargs:
            continue
        rc, text = sh([sys.executable, "evaluation/pairs_report.py", "--plan", str(plan.relative_to(REPO)), *runargs,
                       "--out", str(out / "pairs" / plan.stem)], timeout=1800, quiet=True, check=False)
        if rc == 0:
            done.append(plan.stem)
            index.append(f"- [{spec.get('name', plan.stem)}, against its plan](pairs/{plan.stem}/RESULTS.md)")
        else:
            RUN.problem("WARN", f"pairs_report {plan.name} failed: {text[-400:]}")
    # release 077: cross-policy comparisons declared in a plan's ```policy-compare``` block (the
    # RELEASE_ON_TIMEOUT baseline against EVICON on the same schedules)
    for plan in sorted((REPO / "evaluation" / "prereg").glob("*.md")):
        m = re.search(r"```policy-compare\n(.*?)```", plan.read_text(), re.S)
        if not m:
            continue
        try:
            spec = json.loads(m.group(1))
        except ValueError as e:
            RUN.problem("WARN", f"{plan.name}: policy-compare is not JSON ({e})")
            continue
        runargs = []
        for key, want in spec.get("runs", {}).items():
            found = sorted({r for s in srcs for r in _find_complete_runs(s, want.get("campaign", ""))}, key=lambda r: r.name)
            if found:
                runargs += ["--run", f"{key}={found[0]}"]
        if not runargs:
            continue
        rc, text = sh([sys.executable, "evaluation/policy_compare.py", "--plan", str(plan.relative_to(REPO)), *runargs,
                       "--out", str(out / "policy_compare" / plan.stem)], timeout=900, quiet=True, check=False)
        if rc == 0:
            done.append(f"policy comparisons {plan.stem}")
            index.append(f"- [{spec.get('name', plan.stem)}](policy_compare/{plan.stem}/RESULTS.md)")
        else:
            RUN.problem("WARN", f"policy_compare {plan.name} failed: {text[-400:]}")
    if camps:  # release 072: Theorem 3 on the traces (what evidence re-opens a blocked request)
        rc, text = sh([sys.executable, "evaluation/t3_attribution.py", "--out", str(out / "t3")] + [str(c) for c in camps],
                      timeout=3600, quiet=True, check=False)
        if rc == 0:
            done.append("T3 attribution")
            index.append("- [Theorem 3 on the testbed traces](t3/T3.md)")
        else:
            RUN.problem("WARN", f"t3_attribution failed: {text[-400:]}")
    # release 077: the late effects split by the dispatch attempt that took effect (emulated owners;
    # evicon-device records the attempt from release 077 on, earlier runs show "unknown")
    late_runs = []
    for c in camps:
        try:
            cfg = json.loads((c / "config.json").read_text())
        except (OSError, ValueError):
            continue
        if "real_owner" not in cfg and (c / "runtime_episodes.csv").exists():
            late_runs.append(c)
    if late_runs:
        rc, text = sh([sys.executable, "evaluation/late_attribution.py", "--out", str(out / "late")]
                      + [x for c in late_runs for x in ("--campaign", str(c))], timeout=1800, quiet=True, check=False)
        if rc == 0:
            done.append("late-effect attribution")
            index.append("- [Late effects by the attempt that caused them](late/LATE_ATTRIBUTION.md)")
        else:
            RUN.problem("WARN", f"late_attribution failed: {text[-400:]}")
    probe_roots =[s for s in srcs if (s / "controls").is_dir() or (s / "measure").is_dir()]
    if probe_roots:  # release 072: evidence latency per system from the raw probe results
        rc, text = sh([sys.executable, "evaluation/evidence_latency.py", "--out", str(out / "latency")] + [str(s) for s in probe_roots],
                      timeout=900, quiet=True, check=False)
        if rc == 0:
            done.append("evidence latency")
            index.append("- [Evidence latency per system](latency/LATENCY.md)")
        else:
            RUN.problem("WARN", f"evidence_latency failed: {text[-400:]}")
    # release 073: the simulator's predictions (frozen in the plans) against the earliest complete run of
    # each planned experiment; experiment C at a glance
    pred_runs = [k0[0]] if k0 else []
    pred_runs += [k2[0]] if k2 else []
    for name in ("expW-queue-w6", "expW-queue-winf", "expW-direct-w6", "expW-direct-winf", "expS-w6", "expS-winf", "expL-sleepy", "expC-minio",
                 "expC2-zephyr", "expC3-ablation"):
        found = sorted({r for s in srcs for r in _find_complete_runs(s, name)}, key=lambda r: r.name)
        if found:
            pred_runs.append(found[0])
    if pred_runs:
        rc, text = sh([sys.executable, "evaluation/predict_check.py", "--out", str(out / "predict")] + [str(r) for r in pred_runs],
                      timeout=1800, quiet=True, check=False)
        if rc == 0:
            done.append("simulator predictions")
            index.append("- [Simulator predictions against the testbed](predict/PREDICT.md)")
        else:
            RUN.problem("WARN", f"predict_check failed: {text[-400:]}")
    expc = sorted({r for s in srcs for r in _find_complete_runs(s, "expC-minio")}, key=lambda r: r.name)
    if expc:
        rc, text = sh([sys.executable, "evaluation/expC_summary.py", "--out", str(out / "expC"), str(expc[0])], timeout=900, quiet=True, check=False)
        if rc == 0:
            done.append("experiment C")
            index.append("- [Experiment C on MinIO, at a glance](expC/EXPC.md)")
        else:
            RUN.problem("WARN", f"expC_summary failed: {text[-400:]}")
    expc2 = sorted({r for s in srcs for r in _find_complete_runs(s, "expC2-zephyr")}, key=lambda r: r.name)
    if expc2:  # release 074: experiment C2 at a glance
        rc, text = sh([sys.executable, "evaluation/expC2_summary.py", "--out", str(out / "expC2"), str(expc2[0])], timeout=900, quiet=True,
                      check=False)
        if rc == 0:
            done.append("experiment C2")
            index.append("- [Experiment C2 on the Zephyr firmware behind Mosquitto, at a glance](expC2/EXPC2.md)")
        else:
            RUN.problem("WARN", f"expC2_summary failed: {text[-400:]}")
    expc3 = sorted({r for s in srcs for r in _find_complete_runs(s, "expC3-ablation")}, key=lambda r: r.name)
    if expc3:  # release 075: experiment C3 at a glance
        rc, text = sh([sys.executable, "evaluation/expC2_summary.py", "--experiment", "C3", "--out", str(out / "expC3"), str(expc3[0])],
                      timeout=900, quiet=True, check=False)
        if rc == 0:
            done.append("experiment C3")
            index.append("- [Experiment C3: the firmware's evidence decomposed on the real IoT stack, at a glance](expC3/EXPC3.md)")
        else:
            RUN.problem("WARN", f"expC2_summary --experiment C3 failed: {text[-400:]}")
    # release 074: emulated owner against real owner at the same configuration (C: exploratory, after the
    # run; C2: the estimands of its plan, the same seed as expW-queue-w6 and expS-w6)
    first = {}
    for name in ("expC-minio", "expC2-zephyr", "expC3-ablation", "expW-direct-w6", "expW-queue-w6", "expS-w6"):
        found = sorted({r for s in srcs for r in _find_complete_runs(s, name)}, key=lambda r: r.name)
        if found:
            first[name] = found[0]
    fid = []
    if "expC-minio" in first and "expW-direct-w6" in first:
        fid += [f"{first['expC-minio']}:MINIO_PLAIN:EVICON={first['expW-direct-w6']}:K0:EVICON",
                f"{first['expC-minio']}:MINIO_COND:EVICON={first['expW-direct-w6']}:K0F:EVICON"]
    if "expC2-zephyr" in first and "expW-queue-w6" in first:
        fid += [f"{first['expC2-zephyr']}:ZEPHYR_PLAIN:EVICON={first['expW-queue-w6']}:K0:EVICON",
                f"{first['expC2-zephyr']}:ZEPHYR_CUTOFF:EVICON={first['expW-queue-w6']}:K0F:EVICON"]
    if "expC2-zephyr" in first and "expS-w6" in first:
        fid += [f"{first['expC2-zephyr']}:ZEPHYR_STOP:EVICON={first['expS-w6']}:K0S:EVICON"]
    # release 075, experiment C3 (same seed; the emulated owner crashes, the firmware does not: E-C3-6 of its plan)
    if "expC3-ablation" in first and "expW-direct-w6" in first:
        fid += [f"{first['expC3-ablation']}:ZEPHYR_PLAIN_DIRECT:EVICON={first['expW-direct-w6']}:K0:EVICON",
                f"{first['expC3-ablation']}:ZEPHYR_F_DIRECT:EVICON={first['expW-direct-w6']}:K0F:EVICON"]
    if "expC3-ablation" in first and "expW-queue-w6" in first:
        fid += [f"{first['expC3-ablation']}:ZEPHYR_F_QUEUE:EVICON={first['expW-queue-w6']}:K0F:EVICON"]
    if "expC3-ablation" in first and "expS-w6" in first:
        fid += [f"{first['expC3-ablation']}:ZEPHYR_QS_QUEUE:EVICON={first['expS-w6']}:K0S:EVICON"]
    if fid:
        rc, text = sh([sys.executable, "evaluation/fidelity_check.py", "--out", str(out / "fidelity")] + sum((["--pair", x] for x in fid), []),
                      timeout=1800, quiet=True, check=False)
        if rc == 0:
            done.append("fidelity")
            index.append("- [Emulated owner against real owner at the same configuration](fidelity/FIDELITY.md)")
        else:
            RUN.problem("WARN", f"fidelity_check failed: {text[-400:]}")
    # release 075: safety over every testbed campaign (campaigns and the runtime pilots), against the negative controls
    safety_runs = sorted({p for s in srcs for p in list(s.glob("campaigns/*/2*")) + list(s.glob("runtime/*/2*"))
                          if (p / "runtime_episodes.csv").exists()})
    if safety_runs:
        rc, text = sh([sys.executable, "evaluation/safety_total.py", "--out", str(out / "safety")] + [str(p) for p in safety_runs],
                      timeout=900, quiet=True, check=False)
        if (out / "safety" / "SAFETY.md").exists():
            done.append("safety total")
            index.append("- [Safety over every testbed campaign](safety/SAFETY.md)")
            if rc != 0:
                RUN.problem("FAIL", "safety_total: a safety violation in a guarded cell (see report/safety/SAFETY.md)")
        else:
            RUN.problem("WARN", f"safety_total failed: {text[-400:]}")
    # release 075: the firmware across an owner reboot (measure zephyr-reboot), newest among the sources
    rb = sorted((p for s in srcs for p in s.glob("measure/zephyr-reboot/reboot.md")), key=lambda p: p.stat().st_mtime)
    if rb:
        (out / "reboot").mkdir(parents=True, exist_ok=True)
        shutil.copy2(rb[-1], out / "reboot" / "REBOOT.md")
        if (rb[-1].parent / "reboot.json").exists():
            shutil.copy2(rb[-1].parent / "reboot.json", out / "reboot" / "reboot.json")
        done.append("owner reboot")
        index.append("- [The Zephyr firmware across an owner reboot (outside the measured fault model)](reboot/REBOOT.md)")
    # release 077: the authenticated firmware against forged, altered and replayed messages (measure zephyr-auth),
    # with the footprints of the networked and the authenticated nRF52840 builds
    au = sorted((p for s in srcs for p in s.glob("measure/zephyr-auth/auth.md")), key=lambda p: p.stat().st_mtime)
    if au:
        (out / "auth").mkdir(parents=True, exist_ok=True)
        shutil.copy2(au[-1], out / "auth" / "AUTH.md")
        for f in ("auth.json", "firmware/footprint.txt"):
            if (au[-1].parent / f).exists():
                shutil.copy2(au[-1].parent / f, out / "auth" / Path(f).name)
        done.append("authenticated firmware")
        index.append("- [The authenticated firmware against forged, altered and replayed messages](auth/AUTH.md)")
    fence_runs = k0 or [c for c in camps if any(c.glob("cells/ep*/K0F/EVICON"))]
    if fence_runs:  # 068: figure "timeout -> wait -> fence -> certified outcome" from real traces
        rc, text = sh([sys.executable, "evaluation/timeline.py", "--campaign", str(fence_runs[-1]), "--contract", "K0F",
                       "--policy", "EVICON", "--out", str(out / "timeline")], timeout=600, quiet=True, check=False)
        if rc == 0:
            done.append("timeline")
            index.append("- [Timeline of one ambiguity episode (K0F, EVICON)](timeline/timeline.md) ([png](timeline/timeline.png))")
        else:
            RUN.problem("WARN", f"timeline failed: {text[-300:]}")
    if camps:
        smp = sorted(x for s in srcs for x in s.glob("overhead/samples/**/samples-*.jsonl"))
        sh([sys.executable, "evaluation/overhead.py", "--out", str(out / "overhead")] + sum((["--campaign", str(c)] for c in camps), [])
           + sum((["--samples", str(x)] for x in smp), []), timeout=3600, quiet=True)
        done.append("overhead")
        index.append("- [RQ4 overhead](overhead/overhead.md)")
    # release 077: the latency control (newest latency-control run among the sources), next to the RQ4 rows
    lcs = sorted((x for s in srcs for x in s.glob("overhead/latency_control/latency_control.json")), key=lambda x: x.stat().st_mtime)
    if lcs:
        sys.path.insert(0, str(REPO / "evaluation"))
        import latency_control as LC  # noqa: E402
        d = lcs[-1].parent
        res = LC.analyse(sorted(d.glob("clients-*.jsonl")), sorted(d.glob("raw/VM-A/noop-*.jsonl")) or sorted(d.glob("noop-*.jsonl")),
                         str(out / "overhead" / "overhead.json") if (out / "overhead" / "overhead.json").exists() else None)
        (out / "latency_control").mkdir(parents=True, exist_ok=True)
        (out / "latency_control" / "latency_control.json").write_text(json.dumps(res, indent=2) + "\n")
        (out / "latency_control" / "LATENCY_CONTROL.md").write_text(LC.markdown(res) + f"\nSource: `{d}`\n")
        done.append("latency control")
        index.append("- [RQ4 latency control: the same loads against a no-op endpoint](latency_control/LATENCY_CONTROL.md)")
    # release 077: MQTT command duplicates under PUBACK loss, per delivery binding (newest qos-dup run)
    qds = sorted((x for s in srcs for x in s.glob("measure/qos-dup/QOS_DUP_CHECK.md")), key=lambda x: x.stat().st_mtime)
    if qds:
        (out / "qos_dup").mkdir(parents=True, exist_ok=True)
        for f in ("QOS_DUP_CHECK.md", "qos_dup_check.json"):
            if (qds[-1].parent / f).exists():
                shutil.copy2(qds[-1].parent / f, out / "qos_dup" / f)
        done.append("qos-dup")
        index.append("- [MQTT command duplicates when a reply is lost, per delivery binding](qos_dup/QOS_DUP_CHECK.md)")
    # release 077: the firmware on the emulated Cortex-M3 (measure zephyr-mcu) and the authority with 8-32 domains (scale)
    mcu = sorted((p for s in srcs for p in s.glob("measure/zephyr-mcu/measure.md")), key=lambda p: p.stat().st_mtime)
    if mcu:
        (out / "mcu").mkdir(parents=True, exist_ok=True)
        shutil.copy2(mcu[-1], out / "mcu" / "MCU.md")
        for f in sorted(mcu[-1].parent.rglob("footprint*.txt")):
            shutil.copy2(f, out / "mcu" / f.name)
        done.append("firmware on the emulated MCU")
        index.append("- [The firmware on an emulated Cortex-M3 (QEMU mps2-an385): measured contract](mcu/MCU.md)")
    sc = sorted((p for s in srcs for p in s.glob("overhead/scale/overhead.md")), key=lambda p: p.stat().st_mtime)
    if sc:
        (out / "scale").mkdir(parents=True, exist_ok=True)
        shutil.copy2(sc[-1], out / "scale" / "SCALE.md")
        if (sc[-1].parent / "overhead.json").exists():
            shutil.copy2(sc[-1].parent / "overhead.json", out / "scale" / "scale.json")
        done.append("scale")
        index.append("- [The authority with 4, 8, 16 and 32 domains (RQ4 load)](scale/SCALE.md)")
    measured =[s / "measure" for s in srcs if (s / "measure").is_dir()]
    if measured:
        rc, _ = sh([sys.executable, "conformance/run_probe.py", "map", "--catalog", "experiments/contracts", "--out", str(out / "contract_map")]
                   + sum((["--measured", str(m)] for m in measured), []), check=False, quiet=True)
        if rc == 0:
            done.append("contract map")
            index.append("- [Measured contract map](contract_map.md) ([csv](contract_map.csv))")
    for s in [REPO] + srcs[::-1]:
        fp = (s / "results" / "logs" / "iot-build" / "footprint.txt") if s == REPO else (s.parent / "logs" / "iot-build" / "footprint.txt")
        if fp.exists():
            shutil.copy2(fp, out / "footprint.txt")
            done.append("footprint")
            index.append("- [Firmware footprint (nrf52840dk)](footprint.txt)")
            break
    if done:  # release 070: LaTeX tables, PDF figures and number macros for the paper, from the files above
        rc, text = sh([sys.executable, "evaluation/paper_tables.py", "--report", str(out)], timeout=900, check=False, quiet=True)
        if rc == 0:
            done.append("paper tables")
            index.append("- [Paper tables, figures and numbers](paper/README.md)")
        else:
            RUN.problem("WARN", f"paper_tables failed: {text[-400:]}")
    if done:  # release 075: every preregistered hypothesis and every cross-cutting claim against its file
        rc, text = sh([sys.executable, "evaluation/claims_check.py", "--report", str(out)], timeout=300, check=False, quiet=True)
        if rc == 0:
            done.append("claims")
            index.append("- [Claims against evidence (every preregistered hypothesis, every cross-cutting claim)](claims/CLAIMS.md)")
        else:
            RUN.problem("WARN", f"claims_check failed: {text[-400:]}")
    (out / "REPORT.md").write_text("\n".join(index) + "\n")
    if not done:
        raise StageFail(f"nothing to report in {', '.join(map(str, srcs))} (no campaigns, no measurements)")
    return "PASS", f"{', '.join(done)} -> {out / 'REPORT.md'}"


def cmd_twin2(tb, a):
    """python3 evicon_review.py twin2 [--from DIR]: the exploratory simulator twin v2
    (experiments/sim/iot_dosing_testbed_v2.json: offline periods uniform in [10, 40] s) against
    the testbed experiment A data of this run or DIR. SIMULATION_ONLY, not preregistered."""
    srcs = _sources(a)
    csvs = [p / "runtime_episodes.csv" for s in srcs for n in ("expA-confirm-k0", "expA-confirm-k2", "expA-dosing") for p in _find_runs(s, n)]
    if not csvs:
        raise StageFail(f"no complete experiment A campaign in {', '.join(map(str, srcs))}")
    twin = _sim_twin("experiments/sim/iot_dosing_testbed_v2.json", 20 if (tb.local or a.quick) else 200)
    out = RUN.path / "twin2"
    sh([sys.executable, "evaluation/sim_to_real.py", "--sim", str(twin), "--out", str(out)] + sum((["--runtime", str(c)] for c in csvs), []),
       quiet=True)
    chk = json.loads((out / "sim_to_real.json").read_text())
    (out / "README.txt").write_text("EXPLORATORY (SIMULATION_ONLY): twin v2 = frozen twin + uniform offline periods [10 s, 40 s].\n"
                                    "Testbed data: " + ", ".join(str(c.parent) for c in csvs) + "\n")
    return "PASS", f"twin v2 vs testbed: SR {'PASS' if chk['check']['pass'] else 'not met'} ({len(csvs)} campaign(s)) -> {out / 'sim_to_real.md'}"


def importlib_find(mod):
    import importlib.util
    try:
        return importlib.util.find_spec(mod)
    except (ImportError, ValueError):
        return None


def cmd_measure(tb, a, stack):
    """python3 evicon_review.py measure <stack> (see STACKS)"""
    if stack not in STACKS:
        raise StageFail(f"unknown stack {stack!r}; choose from {sorted(STACKS)}")
    m = _Measure(tb, a, stack)
    m.out.mkdir(parents=True, exist_ok=True)
    m.checks()
    results = []
    try:
        m.deploy()
        m.start_plant()
        if m.owner:
            m.start_broker()
        if stack == "temporal":
            m.start_temporal()
        m.start_reference()
        for name in m.spec["configs"]:
            results.append(m.probe(name))
        if m.spec.get("reboot_check"):
            results.append(m.reboot_check())
        if m.spec.get("auth_check"):
            results.append(m.auth_check())
    finally:
        m.cleanup()
        m.facts["results"] = results
        (m.out / "measure.json").write_text(json.dumps(m.facts, indent=2, default=str) + "\n")
        _measure_table(results, m.out / "measure.md", m.facts)
    sh([sys.executable, "conformance/run_probe.py", "map", "--catalog", "experiments/contracts", "--measured", str(RUN.path / "measure"),
        "--out", str(RUN.path / "measure" / "contract_map")], check=False, quiet=True)
    bad = [r["config"] for r in results if r.get("rc") not in (0, None) or r.get("errors") or r.get("error")]
    if bad:
        raise StageFail(f"probe errors in {bad}: the adapter or the stack misbehaved, so these results are not a measurement. "
                        f"Send the bundle (python3 evicon_review.py bundle); see measure/{stack}/measure.md and runs/*/errors.log")
    return "PASS", "; ".join((f"{r['config']}: {r['verdict']}" + (f" (failed {r.get('failed')})" if r.get("failed") else ""))
                             if "verdict" in r else f"{r['config']}: {r.get('capabilities')} ({r.get('level')}), late effect {r.get('late_effect')}"
                             for r in results) + f" -> {m.out / 'measure.md'}"


def _measure_table(results, path, facts):
    seen = {k: facts[k] for k in ("broker", "coap_transport", "temporal", "dbos", "k3s", "thingsboard") if facts.get(k)}
    L = [f"# Measured evidence contract: {facts['stack']}", "", f"{facts['what']}. Release {facts['release']}"
         + (" (LOCAL SELF-TEST, not a testbed measurement)" if facts["local"] else "") + ".", ""]
    if seen:
        L += [f"As run: `{json.dumps(seen)[:600]}`", ""]
    L += ["| config | measured capabilities | level | hypothesis (catalog) | horizon interval (s) | late effect | retention under pressure | deviations | errors |",
         "|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        L.append(f"| {r['config']} | {r.get('capabilities')} | {r.get('level')} | {r.get('compare_with')}: {r.get('hypothesis_capabilities')} | "
                 f"{r.get('horizon_interval_s')} | {r.get('late_effect')} | {json.dumps(r.get('retention'))} | "
                 f"{'; '.join(r.get('deviations') or []) or '-'} | {r.get('errors') or r.get('error') or '-'} |")
    L += ["", "Per-capability status (OBSERVED / CONTRADICTED / ABSENT / UNKNOWN / DECLARED_UNVERIFIED):", ""]
    for r in results:
        L.append(f"- {r['config']}: {json.dumps(r.get('status'))}")
    L += ["", f"Raw results: {[r.get('out') for r in results]}"]
    path.write_text("\n".join(L) + "\n")


def st_collect(tb, a):
    stamp = RUN.path.name
    seen = {s: RUN.state.get(s, {}).get("source") for s in STAGES if s in RUN.state}
    mixed = len({v for v in seen.values() if v}) > 1
    (RUN.path / "source.json").write_text(json.dumps({"per_stage": seen, "mixed": mixed,
                                                      "manifest_drift": manifest_drift()}, indent=2) + "\n")
    if mixed:
        RUN.problem("WARN", "stages of this run saw different source trees (source.json); "
                "re-run with --new for single-source evidence")
    zpath = RUN.path.parent / f"{stamp}.zip"
    manifest = []
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(RUN.path.rglob("*")):
            if p.is_file() and "local-hosts" not in p.parts and p.suffix not in (".db-wal", ".db-shm"):
                rel = p.relative_to(RUN.path.parent)
                z.write(p, str(rel))
                manifest.append(f"{sha256(p)}  {rel}")
        z.writestr(f"{stamp}/MANIFEST.sha256", "\n".join(manifest) + "\n")
    digest = sha256(zpath)
    (RUN.path.parent / f"{stamp}.zip.sha256").write_text(f"{digest}  {zpath.name}\n")
    return f"{zpath} ({zpath.stat().st_size // 1024} KiB) sha256 {digest}" + (" [MIXED SOURCE: see source.json]" if mixed else "")


FUNCS = {s: globals()[f"st_{s}"] for s in STAGES}


def _command(a, path) -> int:
    """campaign <config> | measure <stack> [<stack> ...]: recorded in stages.json
    as campaign:<name> / measure:<stack>, logged like a stage."""
    kind, args = a.stages[0], a.stages[1:]
    if not args and kind in ("campaign", "measure"):
        raise SystemExit("usage: evicon_review.py campaign <config.json> [--episodes N] [--max-cells N] | "
                         f"measure <{'|'.join(STACKS)}> ...")
    tb = load_testbed(a.testbed, a.local)
    (path / "testbed.json").write_text(json.dumps(tb.cfg, indent=2) + "\n")
    print(f"EviCon {RELEASE} reviewer driver -> {path}")
    if kind in ("overhead", "report", "twin2", "latency-control", "qos-dup", "scale"):
        cmd = {"overhead": cmd_overhead, "report": cmd_report, "twin2": cmd_twin2, "latency-control": cmd_latency_control,
               "qos-dup": cmd_qos_dup, "scale": cmd_scale}[kind]
        jobs = [(kind, (lambda cmd=cmd: cmd(tb, a)))]  # bound now: the loop below rebinds `fn`
    elif kind == "campaign":
        jobs = [(f"campaign:{Path(args[0]).stem}", lambda: cmd_campaign(tb, a, args[0]))]
    else:
        jobs = [(f"measure:{x}", (lambda x=x: cmd_measure(tb, a, x))) for x in args]
    if kind == "campaign" and len(args) > 1:
        raise SystemExit("campaign takes one config")
    rc, failed = 0, []
    for key, fn in jobs:
        if not a.force and RUN.state.get(key, {}).get("status") == "PASS" and kind in ("measure", "overhead", "latency-control", "qos-dup", "scale"):
            print(f"[{key}] already PASS in this run (use --force to repeat, or --new for a new run)")
            continue
        RUN.begin(key)
        t0 = time.monotonic()
        try:
            status, summary = fn()
            RUN.end(status, summary)
        except StageFail as e:
            RUN.problem("FAIL", str(e))
            RUN.end("FAIL", str(e))
            print(f"\n[{key}] FAIL: {e}\nLog: {path / 'logs' / (key.replace(':', '-') + '.log')}  Errors: {path / 'errors.log'}\n"
                  f"To send everything for analysis: python3 evicon_review.py bundle")
            rc, failed = max(rc, 1), failed + [key]
            if kind == "measure":
                continue  # 068: the other stacks are independent; measure them anyway
            return 1
        except KeyboardInterrupt:
            RUN.end("INTERRUPTED", "interrupted by the operator")
            return 130
        except Exception as e:  # a driver bug must not look like a PASS
            RUN.problem("ERROR", f"driver error in {key}: {type(e).__name__}: {e}", e)
            RUN.end("ERROR", f"{type(e).__name__}: {e}")
            print(f"\n[{key}] DRIVER ERROR: {type(e).__name__}: {e}\nTraceback: {path / 'errors.log'}\n"
                  f"Please send: python3 evicon_review.py bundle")
            rc, failed = 2, failed + [key]
            if kind == "measure":
                continue
            return 2
        print(f"[{key}] {status} in {time.monotonic() - t0:.0f}s: {summary}")
    if failed and len(jobs) > 1:
        print(f"\nnot PASS: {', '.join(failed)}; the others ran. Re-run the same command: stacks that PASSed are skipped.")
    return rc


def print_status(run: Run):
    print(f"run: {run.path}")
    print(f"{'stage':11s} {'status':10s} {'ended':26s} summary")
    for s in STAGES:
        x = run.state.get(s, {})
        print(f"{s:11s} {x.get('status', '-'):10s} {x.get('ended', ''):26s} {str(x.get('summary', ''))[:110]}")
    extra = [k for k in sorted(run.state) if k not in STAGES]
    if extra:
        print("commands (campaign / measure):")
        for s in extra:
            x = run.state[s]
            print(f"{s:28s} {x.get('status', '-'):10s} {x.get('ended', ''):26s} {str(x.get('summary', ''))[:90]}")


# ------------------------------------------------------------------ bundle
BUNDLE_MAX_FILE = 200 * 2**20


def _cmd(argv, timeout=20, env=None):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env)
        return (p.stdout + p.stderr).strip()
    except Exception as e:  # noqa: BLE001 - best effort snapshot
        return f"<{type(e).__name__}: {e}>"


def _redact_proxy(v):
    """Keep scheme://host:port of a proxy URL, drop any user:password."""
    import urllib.parse
    try:
        u = urllib.parse.urlsplit(v)
        return f"{u.scheme}://{u.hostname}:{u.port}" if u.hostname else "<set>"
    except ValueError:
        return "<set>"


def environment_snapshot():
    env = go_env()
    info = {"release": RELEASE, "created": now(), "host": socket.gethostname(), "euid": os.geteuid(),
            "source_fingerprint": source_fingerprint(), "manifest_drift": manifest_drift(),
            "platform": platform.platform(), "python": sys.version, "python_executable": sys.executable,
            "go": _cmd(["go", "version"], env=env), "java": [l for l in _cmd(["java", "-version"]).splitlines() if "version" in l][:1],
            "gcc": _cmd(["gcc", "--version"]).splitlines()[:1], "cpu_count": os.cpu_count(),
            "temporal_cli": _cmd([str(REPO / ".tools" / "temporal"), "--version"]) if (REPO / ".tools" / "temporal").exists() else "absent",
            "disk_free_gib": round(shutil.disk_usage(REPO).free / 2**30, 1),
            "aiocoap": __import__("importlib.util").util.find_spec("aiocoap") is not None,
            "python_modules": {m: importlib_find(m) is not None for m in ("aiocoap", "paho", "temporalio", "nats", "matplotlib")},
            "time_local": dt.datetime.now().astimezone().isoformat(), "time_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "timedatectl": _cmd(["timedatectl", "show", "-p", "Timezone", "-p", "NTPSynchronized", "-p", "NTP"]),
            "proxy_env": {k: _redact_proxy(v) for k, v in os.environ.items()
                          if k.lower() in ("http_proxy", "https_proxy", "all_proxy")},
            "no_proxy": os.environ.get("no_proxy", os.environ.get("NO_PROXY", "")),
            "ip_addr": _cmd(["ip", "-brief", "addr"]), "listening": _cmd(["bash", "-c", "ss -ltnH 2>/dev/null | awk '{print $4}' | sort | head -200"])}
    try:
        info["os_release"] = dict(l.split("=", 1) for l in Path("/etc/os-release").read_text().splitlines() if "=" in l).get("PRETTY_NAME", "").strip('"')
        mem = dict(l.split(":", 1) for l in Path("/proc/meminfo").read_text().splitlines() if ":" in l)
        info["mem_total"], info["mem_available"] = mem.get("MemTotal", "").strip(), mem.get("MemAvailable", "").strip()
        info["loadavg"] = Path("/proc/loadavg").read_text().strip()
    except OSError:
        pass
    runs = []
    for d in sorted((REPO / "results").glob("review-*")):
        if d.is_dir() and (d / "stages.json").exists():
            st = json.loads((d / "stages.json").read_text())
            runs.append({"run": d.name, "stages": {k: v.get("status") for k, v in st.items()}})
    info["review_runs"] = runs
    tbf = REPO / "testbed.json"
    info["testbed_json"] = json.loads(tbf.read_text()) if tbf.exists() else None
    return info


def remote_snapshot(tb_cfg: dict, out: Path):
    """Best effort, over SSH (BatchMode, 10 s): state of each VM and the files the
    controls left on VM-B/VM-E (campaign cells are already fetched by campaign.py)."""
    notes = {}
    state_dir = tb_cfg.get("state_dir", "/var/lib/evicon")
    remote_root = tb_cfg.get("remote_root", "/root/evicon")
    for role in ("A", "B", "D", "E"):
        ip = tb_cfg.get(f"vm_{role.lower()}")
        target = f"{tb_cfg.get('ssh_user', 'root')}@{ip}"
        ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", target]
        script = (f"echo '## date'; date '+%F %T %z'; echo '## host'; hostname; uname -a; uptime; "
                  f"echo '## os'; grep PRETTY_NAME /etc/os-release; echo '## disk'; df -h / {shlex.quote(state_dir)} 2>/dev/null; "
                  f"echo '## mem'; free -m; echo '## evicon processes'; ps -eo pid,etime,args | grep -E 'evicon-(endpoint|device|plant|authority)|mqtt_device|coap_device|temporal_worker|temporal server start-dev' | grep -v grep; "
                  f"echo '## busiest processes'; ps -eo pid,pcpu,etime,args --sort=-pcpu 2>/dev/null | head -n 15; "
                  f"echo '## docker'; docker ps --format '{{{{.Names}}}} {{{{.Image}}}} {{{{.Status}}}} {{{{.Ports}}}}' 2>/dev/null; "
                  f"echo '## python modules'; python3 -c 'import importlib.util as u; print({{m: bool(u.find_spec(m)) for m in (\"paho\", \"aiocoap\", \"temporalio\", \"nats\")}})' 2>/dev/null; "
                  f"echo '## listening (all)'; ss -ltnpH 2>/dev/null; "
                  f"echo '## binaries'; sha256sum {shlex.quote(remote_root)}/bin/evicon-* 2>/dev/null; "
                  f"echo '## state'; find {shlex.quote(state_dir)} -maxdepth 3 2>/dev/null | head -100; "
                  f"echo '## timedatectl'; timedatectl show -p NTPSynchronized -p Timezone 2>/dev/null; "
                  f"echo '## firewall'; (ufw status 2>/dev/null || echo 'ufw: not installed') | head -30; "
                  f"echo '## temporal cli'; {shlex.quote(remote_root)}/bin/temporal --version 2>/dev/null; "
                  f"echo '## iot builds'; tail -n 5 {shlex.quote(state_dir)}/builds/*.status 2>/dev/null; "
                  f"echo '## docker images'; docker images --format '{{{{.Repository}}}}:{{{{.Tag}}}} {{{{.ID}}}} {{{{.Size}}}}' 2>/dev/null | head -20")
        text = _cmd(ssh + [script], timeout=40)
        (out / f"VM-{role}.txt").write_text(text + "\n")
        notes[f"VM-{role}"] = "ok" if "## date" in text else text.splitlines()[-1][:200] if text else "no answer"
        if "## date" in text:
            tgz = out / f"VM-{role}-controls.tgz"  # logs, journals and DBs the controls and measure left on the VM
            # 067: also the IoT build status/logs (deploy/setup/build_iot.sh) under builds/
            find = (f"cd {shlex.quote(state_dir)} 2>/dev/null && find controls measure builds -type f -size -20M -print0 2>/dev/null "
                    f"| xargs -0 -r tar czf - 2>/dev/null")
            try:
                with open(tgz, "wb") as fh:
                    subprocess.run(ssh + [find], stdout=fh, stderr=subprocess.DEVNULL, timeout=120)
                if tgz.stat().st_size == 0:
                    tgz.unlink()
            except Exception as e:  # noqa: BLE001
                notes[f"VM-{role}-logs"] = f"{type(e).__name__}: {e}"
    return notes


BUNDLE_INDEX = ".bundle_index.json"


def make_bundle(a) -> int:
    """One zip with everything needed to analyse this VM-C's runs. With --incremental (release 072),
    only the files of results/ that are new or changed since the last bundle (results/.bundle_index.json:
    size and mtime of every file bundled so far), so a nightly bundle does not resend earlier campaigns;
    the zip is then named EVICON_<release>_result_<YYYYmmddTHHMM>.zip."""
    stamp = dt.datetime.now().strftime("%Y%m%dT%H%M")
    name = f"EVICON_{RELEASE}_result" + (f"_{stamp}" if a.incremental else "")
    zpath = REPO / f"{name}.zip"
    results = REPO / "results"
    index_path = results / BUNDLE_INDEX
    try:
        index = json.loads(index_path.read_text()) if index_path.exists() else {}
    except ValueError:
        index = {}
    unchanged = 0
    work = Path(tempfile.mkdtemp(prefix="evicon-bundle-"))
    print(f"collecting results/ and an environment snapshot -> {zpath}")
    env = environment_snapshot()
    (work / "environment.json").write_text(json.dumps(env, indent=2) + "\n")
    remote_cfg = None
    latest = results / "LATEST"
    if latest.exists() and (latest / "testbed.json").exists():
        remote_cfg = json.loads((latest / "testbed.json").read_text())
    elif not latest.exists() and (REPO / "testbed.json").exists():
        # no review run yet (e.g. setup failed): still ask the VMs, at the configured addresses (078: only those of
        # testbed.json, never the shipped defaults)
        try:
            remote_cfg = dict(DEFAULT_TESTBED, **json.loads((REPO / "testbed.json").read_text()))
        except ValueError:
            remote_cfg = None
    if remote_cfg and remote_cfg.get("vm_a") == "127.0.0.1":
        remote_cfg = None  # local self-test: there are no VMs to ask
    if remote_cfg and not a.no_remote:
        print("asking VM-A/B/D/E for their state over SSH (skip with --no-remote) ...")
        (work / "remote").mkdir()
        env["remote"] = remote_snapshot(remote_cfg, work / "remote")
        (work / "environment.json").write_text(json.dumps(env, indent=2) + "\n")
    files, skipped = [], []
    if results.exists():
        for p in sorted(results.rglob("*")):
            if not p.is_file() or p.is_symlink():
                continue
            rel = p.relative_to(REPO)
            if p.suffix == ".zip" and p.with_suffix("").is_dir():
                continue  # a review run zip whose directory is included anyway
            if p.suffix in (".db-wal", ".db-shm") or ("local-hosts" in rel.parts and "bin" in rel.parts):
                continue
            if p.name == BUNDLE_INDEX:
                continue
            st = p.stat()
            if st.st_size > BUNDLE_MAX_FILE:
                skipped.append(f"{rel} ({st.st_size // 2**20} MiB > {BUNDLE_MAX_FILE // 2**20} MiB)")
                continue
            if a.incremental and index.get(str(rel)) == [st.st_size, st.st_mtime_ns]:
                unchanged += 1
                continue
            files.append((p, f"{name}/{rel}"))
    for extra in ("testbed.json",):
        if (REPO / extra).exists():
            files.append((REPO / extra, f"{name}/{extra}"))
    for p in sorted(work.rglob("*")):
        if p.is_file():
            files.append((p, f"{name}/bundle/{p.relative_to(work)}"))
    readme = (f"EviCon {RELEASE} result bundle from {env['host']} at {env['created']}.\n"
              + (f"INCREMENTAL: only files new or changed since the last bundle ({unchanged} unchanged files left out; "
                 f"they are in the earlier bundles)\n" if a.incremental else "")
              + f"results/            everything under results/ (review runs, make logs, local campaigns)\n"
              f"bundle/environment.json  VM-C snapshot (tools, clock, proxy settings without credentials, runs)\n"
              f"bundle/remote/      VM-A/B/D/E state over SSH and the control files of VM-B/VM-E (testbed only)\n"
              f"bundle/skipped.txt  files left out because of their size\n")
    manifest = []
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for p, arc in files:
            z.write(p, arc)
            manifest.append(f"{sha256(p)}  {arc}")
        z.writestr(f"{name}/bundle/README.txt", readme)
        z.writestr(f"{name}/bundle/skipped.txt", "\n".join(skipped) + ("\n" if skipped else ""))
        z.writestr(f"{name}/bundle/MANIFEST.sha256", "\n".join(manifest) + "\n")
    shutil.rmtree(work, ignore_errors=True)
    for p, arc in files:  # remember what was sent (both modes), for the next --incremental bundle
        if arc.startswith(f"{name}/results/"):
            st = p.stat()
            index[str(p.relative_to(REPO))] = [st.st_size, st.st_mtime_ns]
    if results.exists():
        index_path.write_text(json.dumps(index) + "\n")
    digest = sha256(zpath)
    (REPO / f"{name}.zip.sha256").write_text(f"{digest}  {zpath.name}\n")
    runs = ", ".join(f"{r['run']}" for r in env["review_runs"]) or "none"
    print(f"wrote {zpath} ({zpath.stat().st_size // 1024} KiB, {len(files)} files; review runs: {runs})\n"
          f"sha256 {digest}\nSend this one file: {zpath.name}" + (f"\nleft out (size): {len(skipped)} file(s), see bundle/skipped.txt" if skipped else "")
          + (f"\nincremental: {unchanged} unchanged file(s) already sent in an earlier bundle" if a.incremental else ""))
    return 0


def _sigterm(signum, frame):
    raise KeyboardInterrupt(f"signal {signum}")


_LOCK = None


def _driver_lock(local: bool):
    """One evicon_review.py at a time on the testbed (fix of 15/10): two runs (two tmux windows,
    an old and a new release) would fight over the same ports and processes. --local self-tests
    lock only their own release directory."""
    global _LOCK
    import fcntl
    path = REPO / "results" / ".driver-local.lock"
    if not local:
        for cand in (Path("/run/lock"), Path("/var/lock"), REPO / "results"):
            if cand.is_dir() and os.access(cand, os.W_OK):
                path = cand / "evicon-review.lock"
                break
    try:
        fh = open(path, "a+")
    except OSError:  # e.g. created by root, now run as another user
        path = REPO / "results" / ".driver.lock"
        fh = open(path, "a+")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.seek(0)
        who = fh.read().strip() or "?"
        fh.close()
        raise SystemExit(f"another evicon_review.py is running on this host ({who}; lock {path}). Wait for it to end "
                         f"(tmux attach -t evicon) or stop it with Ctrl-C, then re-run. Two runs at once would use the same "
                         f"ports and processes on the VMs.")
    fh.seek(0)
    fh.truncate()
    fh.write(f"pid {os.getpid()}, {now()}, {REPO}: evicon_review.py {' '.join(sys.argv[1:])}\n")
    fh.flush()
    _LOCK = fh


def main(argv=None):
    """Records every invocation (and any crash, with traceback) in results/logs/driver.log."""
    import signal
    signal.signal(signal.SIGTERM, _sigterm)  # cleanup (pid files, compose project) also runs on kill
    signal.signal(signal.SIGHUP, _sigterm)   # ... and when the SSH session of a run outside tmux drops
    argv = sys.argv[1:] if argv is None else argv
    logdir = REPO / "results" / "logs"
    logdir.mkdir(parents=True, exist_ok=True)

    def record(text):
        with open(logdir / "driver.log", "a") as fh:
            fh.write(text.rstrip("\n") + "\n")
    record(f"{now()} start: evicon_review.py {' '.join(argv)} (release {RELEASE}, host {socket.gethostname()}, python {sys.version.split()[0]})")
    try:
        rc = _main(argv)
    except SystemExit as e:
        record(f"{now()} exit: {e.code}")
        raise
    except BaseException as e:
        record(f"{now()} crash: {type(e).__name__}: {e}\n{traceback.format_exc()}")
        raise
    record(f"{now()} end: rc={rc}")
    return rc


def _main(argv):
    global RUN
    ap = argparse.ArgumentParser(description="EviCon reviewer driver (run on VM-C as root)", formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__)
    ap.add_argument("stages", nargs="+", help="all | status | plan | bundle | measure <stack> | campaign <config> | overhead | report | "
                                               "twin2 | " + " | ".join(STAGES))
    ap.add_argument("--testbed")
    ap.add_argument("--run")
    ap.add_argument("--new", action="store_true")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--local", action="store_true")
    ap.add_argument("--no-remote", action="store_true", help="bundle: do not ask the VMs over SSH")
    ap.add_argument("--incremental", action="store_true", help="bundle: only files new or changed since the last bundle (072)")
    ap.add_argument("--episodes", type=int, default=0, help="campaign: episodes (default: the config's)")
    ap.add_argument("--max-cells", type=int, default=0, help="campaign: stop after this many new cells (resume later)")
    ap.add_argument("--new-campaign", action="store_true", help="campaign/pilot: start a new run instead of resuming")
    ap.add_argument("--from", dest="from_run", action="append", default=[],
                    help="report/twin2: also read the campaigns and measurements of this run directory (repeatable, "
                         "e.g. the 066 and 067 runs); the current run is always included")
    ap.add_argument("--sources-of", dest="sources_of", default="",
                    help="report (074): also read every run the newest report of this release directory (e.g. "
                         "/root/EVICON_TOIT_073) read, and that report's own run: one option instead of a --from per release")
    ap.add_argument("--archive", default="",
                    help="report (078): also read the runs the newest report in the archive read, and that report's "
                         "own run (--sources-of on the archive import-results writes); import-results: the archive to "
                         "write (default /root/evicon-archive)")
    ap.add_argument("--archive-missing-ok", action="store_true",
                    help="report --archive (078): go on when the archive lacks some of those runs (listed as WARN)")
    ap.add_argument("--lean", action="store_true", help="import-results (078): leave out the authority traces of the runs "
                                                        "the 077 plan does not reference")
    ap.add_argument("--force-space", action="store_true", help="import-results (078): skip the free-space check")
    a = ap.parse_args(argv)
    if a.stages[0] == "import-results":
        return cmd_import_results(a)
    if a.stages == ["plan"]:
        for s in STAGES:
            print(f"{s:11s} {DURATION[s]:40s} {FUNCS[s].__name__}")
        print(f"{'campaign':11s} {'expA_confirm_k0: about 12 h (96 cells)':40s} python3 evicon_review.py campaign expA_confirm_k0")
        print(f"{'campaign':11s} {'expA_confirm_k2: about 4 h (30 cells)':40s} python3 evicon_review.py campaign expA_confirm_k2")
        print(f"{'campaign':11s} {'expA_dosing (pilot): about 7.5 h':40s} python3 evicon_review.py campaign expA_dosing --episodes 5")
        for cfg_name, what in (("negctl_072", "negative controls: about 1 h (8 cells)"), ("expW_queue_w6", "W, queue, W=6: about 5.8 h (48 cells)"),
                               ("expW_queue_winf", "W, queue, W=inf: about 5.8 h (48 cells)"), ("expW_direct_w6", "W, direct, W=6: about 2.9 h (24 cells)"),
                               ("expW_direct_winf", "W, direct, W=inf: about 2.9 h (24 cells)"), ("expS_w6", "S, W=6: about 4.3 h (36 cells)"),
                               ("expS_winf", "S, W=inf: about 4.3 h (36 cells)"), ("expL_sleepy", "L (exploratory): about 2 h (16 cells)"),
                               ("expC_minio", "C, MinIO (073): about 5.8 h (48 cells)"),
                               ("expC2_zephyr", "C2, Zephyr (074): about 6.7 h (56 cells)"),
                               ("expC3_ablation", "C3, Zephyr evidence ablation (075): about 6.7 h (56 cells)"),
                               ("expR_zephyr_winf", "077 U, Zephyr at W=inf: about 4 h (32 cells)"),
                               ("expRT_queue", "077 RT baseline, queue: about 2 h (16 cells)"),
                               ("expR_minio_winf", "077 U, MinIO at W=inf: about 2 h (16 cells)"),
                               ("expO_noorphan", "077 NoOrphan: about 2.9 h (24 cells)"),
                               ("expK2_w6", "077 K2 replication: about 2.4 h (20 cells)"),
                               ("expV_mild", "077 Mild regime: about 2.9 h (24 cells)"),
                               ("expRT_direct", "077 RT baseline, direct: about 1 h (8 cells)"),
                               ("expRR_queue", "077 RR baseline, queue: about 1.2 h (10 cells)"),
                               ("expRR_direct", "077 RR baseline, direct: about 1.2 h (10 cells)"),
                               ("expL2_sleepy", "077 L2, sleepy devices, resend cut: about 2 h (16 cells)"),
                               ("expQ_zephyr", "077 Q, MQTT delivery binding (Zephyr): about 2.1 h (18 cells)"),
                               ("expWAN_s6", "077 WAN, S at W=6 with a 200 ms RTT: about 2.9 h (24 cells)"),
                               ("expC16_minio", "077 C16, MinIO at W=16: about 1.6 h (16 cells)"),
                               ("expTB_thingsboard", "077 TB, ThingsBoard persistent RPC: about 2.2 h (18 cells)")):
            print(f"{'campaign':11s} {what:40s} python3 evicon_review.py campaign {cfg_name}")
        for k in STACKS:
            print(f"{'measure':11s} {MEASURE_DURATION[k]:40s} python3 evicon_review.py measure {k}")
        print(f"{'overhead':11s} {'RQ4 load campaigns: about 2 h (36 cells)':40s} python3 evicon_review.py overhead")
        print(f"{'latency-control':11s} {'077, RQ4 loads against a no-op: about 25 min':40s} python3 evicon_review.py latency-control")
        print(f"{'qos-dup':11s} {'077, MQTT QoS 1 vs 0 under PUBACK loss: 10 min':40s} python3 evicon_review.py qos-dup")
        print(f"{'scale':11s} {'077, RQ4 load with 8/16/32 domains: 1.2 h':40s} python3 evicon_review.py scale")
        print(f"{'report':11s} {'2-5 min (078: 15-30 min with an archive)':40s} python3 evicon_review.py report [--archive <dir>] [--from <run dir> ...]")
        print(f"{'import':11s} {'078, result bundles -> archive: 10-30 min':40s} python3 evicon_review.py import-results <bundles> --archive /root/evicon-archive")
        print(f"{'twin2':11s} {'about 5 min':40s} python3 evicon_review.py twin2 [--from <run dir>]")
        return 0
    if a.stages == ["bundle"]:
        return make_bundle(a)
    base = REPO / "results"
    base.mkdir(exist_ok=True)
    if a.stages != ["status"]:
        _driver_lock(a.local)
    latest = base / "LATEST"
    if a.run:
        path = Path(a.run).resolve()
    elif latest.exists() and not a.new:
        path = latest.resolve()
    else:
        path = base / ("review-" + dt.datetime.now().strftime("%Y%m%dT%H%M%S") + ("-local" if a.local else ""))
    RUN = Run(path)
    if not (path / "REVIEW_LOG.md").exists():
        (path / "REVIEW_LOG.md").write_text(f"# EviCon {RELEASE} review run {path.name}\n\nStarted {now()} on {socket.gethostname()}"
                                            f"{' (LOCAL SELF-TEST: not a testbed run)' if a.local else ''}.\n\n"
                                            "| stage | status | started | ended | summary |\n|---|---|---|---|---|\n")
    if latest.is_symlink() or not latest.exists():
        if latest.is_symlink():
            latest.unlink()
        latest.symlink_to(path)
    if a.stages == ["status"]:
        print_status(RUN)
        return 0
    if a.stages[0] in ("campaign", "measure", "overhead", "report", "twin2", "latency-control", "qos-dup", "scale"):
        return _command(a, path)
    stages = STAGES if a.stages == ["all"] else a.stages
    for s in stages:
        if s not in FUNCS:
            raise SystemExit(f"unknown stage {s!r}; choose from: all status plan {' '.join(STAGES)}")
    tb = load_testbed(a.testbed, a.local)
    (path / "testbed.json").write_text(json.dumps(tb.cfg, indent=2) + "\n")
    print(f"EviCon {RELEASE} reviewer driver -> {path}")
    current = source_fingerprint()
    for s in stages:
        if not a.force and RUN.state.get(s, {}).get("status") == "PASS":
            seen = RUN.state[s].get("source")
            note = "" if seen in (None, current) else f"; NOTE: it ran on source {seen}, the tree is now {current}"
            print(f"[{s}] already PASS in this run (use --force to repeat){note}")
            continue
        RUN.begin(s)
        t0 = time.monotonic()
        try:
            summary = FUNCS[s](tb, a)
            RUN.end("PASS", summary if isinstance(summary, str) else json.dumps(summary))
        except StageFail as e:
            RUN.problem("FAIL", str(e))
            RUN.end("FAIL", str(e))
            print(f"\n[{s}] FAIL: {e}\nLog: {path / 'logs' / (s + '.log')}  Errors: {path / 'errors.log'}\n"
                  f"Fix the cause and re-run: python3 evicon_review.py {' '.join(stages[stages.index(s):]) if a.stages != ['all'] else 'all'}\n"
                  f"To send everything for analysis: python3 evicon_review.py bundle")
            return 1
        except KeyboardInterrupt:
            RUN.end("INTERRUPTED", "interrupted by the operator")
            return 130
        except Exception as e:  # a driver bug must not look like a PASS
            RUN.problem("ERROR", f"driver error in stage {s}: {type(e).__name__}: {e}", e)
            RUN.end("ERROR", f"{type(e).__name__}: {e}")
            print(f"\n[{s}] DRIVER ERROR: {type(e).__name__}: {e}\nTraceback: {path / 'errors.log'}\n"
                  f"Please send: python3 evicon_review.py bundle")
            return 2
        print(f"[{s}] PASS in {time.monotonic() - t0:.0f}s")
    print_status(RUN)
    return 0


if __name__ == "__main__":
    sys.exit(main())
