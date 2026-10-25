#!/usr/bin/env python3
"""Runtime campaign on a real IoT stack: experiment C2, EviCon authorities against the Zephyr EviCon
firmware behind Mosquitto (release 074).

The cell procedure, the seeded schedules (common random numbers), the clients, the fault runner, the
truth from evicon-plant, the row and the activation gate are those of evaluation/campaign.py, and the
pseudo-policies those of evaluation/campaign_real.py; both are frozen by earlier plans and imported,
never edited. What differs from an evicon-device campaign:

  owner    per device, one process of the Zephyr EviCon firmware (native_sim/native/64, built by
           deploy/setup/build_iot.sh) on VM-E, connected to Mosquitto on VM-B, writing its effects to
           evicon-plant on VM-E (TCP line protocol: the independent truth, as for every campaign). The
           firmware runs in the evidence mode of the cell's contract (-evc_mode plain | cutoff | stop),
           with a fresh flash file and a topic prefix of its own per cell. The firmware is never
           crashed: the contracts were measured with the owner up
  client   per device, one MQTT owner gateway (conformance/reference/mqtt_owner_gateway.py) on VM-C:
           the device-owner protocol of evicon-device towards the authority, MQTT towards the broker,
           advertising the contract the same review run MEASURED for the firmware mode
  faults   as in campaign.py: drop_up / drop_down / delay_up on the authority -> gateway path (the
           per-device fault proxy); crash_after_effect (the gateway exits after the firmware answered
           APPLIED, before it replies; restarted after crash_recovery_s: a client abandon); offline (the
           gateway's link to the broker is down: its MQTT connections are reset and refused, paho keeps
           what is published meanwhile and sends it after reconnecting). All inside the fault model of
           the measurement ("network loss/delay, client abandon; owner up")
  truth    evicon-plant on VM-E, read over its HTTP API while the stack is up (campaign.collect_live)

Config: a campaign.py config (schema_version 2) plus
  "real_owner": {"system": "zephyr",
                 "broker": "<VM-B>:1883" (the driver fills it in), "firmware": "<path of the firmware on
                 VM-E>" (the driver fills it in), "plant_tcp_port": 18093,
                 "contracts": {"<name>": {"mode": "PLAIN"|"CUTOFF"|"STOP", "contract_file": "<path>"}, ...},
                 "policies": {"<name>": {"base": "<policy>", "authority": {<overrides>}}, ...},
                 "gateway_budget_s": 1.8, "qos_cmd": 1}
contract_file: a repository path or an absolute path (the driver copies the contracts measured in this
review run into the campaign directory and points there).

  python3 evaluation/campaign_zephyr.py --config <derived config> --out <dir>
  python3 evaluation/campaign_zephyr.py --resume <run dir>

evicon_review.py campaign expC2_zephyr brings Mosquitto up on VM-B (EviCon's compose project only),
copies the firmware to VM-E, runs this script and brings Mosquitto down again.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "evaluation"))
sys.path.insert(0, str(REPO / "conformance"))
import campaign as C  # noqa: E402  (frozen; imported, never edited)
import campaign_real as CR  # noqa: E402  (frozen; imported, never edited)
from campaign import Cell, Host, Stack  # noqa: E402
from evicon_probe.faults import FaultProxy  # noqa: E402

RELEASE = C.RELEASE
GATEWAY = REPO / "conformance" / "reference" / "mqtt_owner_gateway.py"
FIRMWARE_SRC = REPO / "firmware" / "zephyr-cutoff"
REAL_KEYS = {"system", "broker", "firmware", "plant_tcp_port", "contracts", "policies", "gateway_budget_s", "qos_cmd"}
MODES = {"PLAIN": ("plain", set()), "CUTOFF": ("cutoff", {"D_h", "Q_A", "F"}), "STOP": ("stop", {"D_h", "Q_A", "Q_S", "F"})}
LOG = C.RunLog()


# ------------------------------------------------------------------ config
def contract_path(p: str) -> Path:
    q = Path(p)
    return q if q.is_absolute() else REPO / q


def load_config(path) -> dict:
    """campaign.load_config's checks (through campaign_real's pseudo-policy rules), plus the real_owner
    section of experiment C2."""
    cfg = json.loads(Path(path).read_text())
    ro = cfg.get("real_owner")
    if not isinstance(ro, dict):
        raise ValueError("real_owner section required")
    unknown = set(ro) - REAL_KEYS
    if unknown:
        raise ValueError(f"unknown real_owner keys {sorted(unknown)}")
    if ro.get("system") != "zephyr":
        raise ValueError("real_owner.system: zephyr (MinIO campaigns: evaluation/campaign_real.py)")
    for k in ("broker", "firmware", "contracts"):
        if not ro.get(k):
            raise ValueError(f"real_owner.{k} required")
    if int(ro.get("qos_cmd", 1)) not in (1, 2):
        raise ValueError("real_owner.qos_cmd: 1 or 2")
    for name in cfg.get("contracts", []):
        spec = ro["contracts"].get(name)
        if not spec or spec.get("mode") not in MODES or not contract_path(str(spec.get("contract_file", ""))).is_file():
            raise ValueError(f"contract {name}: real_owner.contracts.{name} needs mode PLAIN|CUTOFF|STOP and an existing contract_file")
        c = json.loads(contract_path(spec["contract_file"]).read_text())
        if c.get("source") != "MEASURED" or set(c.get("capabilities", [])) != MODES[spec["mode"]][1]:
            raise ValueError(f"contract {name}: {spec['contract_file']} is {c.get('source')} {sorted(c.get('capabilities', []))}, "
                             f"mode {spec['mode']} needs MEASURED {sorted(MODES[spec['mode']][1])}")
    pseudo = ro.get("policies", {})
    for name, p in pseudo.items():
        if p.get("base") not in C.RESOLVERS + C.LEGACY:
            raise ValueError(f"pseudo-policy {name}: base {p.get('base')!r} is not a campaign.py policy")
        bad = set(p.get("authority", {})) - {"retry_guard", "max_network_delay", "max_outstanding", "resolve_sweep", "periodic"}
        if bad:
            raise ValueError(f"pseudo-policy {name}: authority overrides {sorted(bad)} not allowed")
    excl = cfg.get("exclude_cells", [])
    for x in excl:
        if not (isinstance(x, list) and len(x) == 2 and x[0] in cfg.get("contracts", []) and x[1] in cfg.get("policies", [])):
            raise ValueError(f"exclude_cells: {x!r} is not a [contract, policy] pair of this config")
    base = {k: v for k, v in cfg.items() if k not in ("real_owner", "exclude_cells")}
    base["policies"] = [pseudo[p]["base"] if p in pseudo else p for p in cfg["policies"]]
    tmp = Path(os.environ.get("TMPDIR", "/tmp")) / f"campaign_zephyr_check_{os.getpid()}.json"
    try:  # every other check is campaign.load_config's own (one definition of the format)
        tmp.write_text(json.dumps(base))
        C.load_config(tmp)
    finally:
        tmp.unlink(missing_ok=True)
    if cfg.get("mode") == "remote" and "vm_b_ip" not in cfg.get("remote", {}):
        raise ValueError("remote.vm_b_ip required")
    return cfg


def cell_ns(out: Path, cell: Cell) -> str:
    """Topic prefix of a cell: messages the broker kept for an earlier cell never reach this one."""
    return "e" + hashlib.sha256(f"{out}/{cell.key}".encode()).hexdigest()[:12]


def open_session_offset(host: Host):
    """(clock offset of host minus this host, round trip), in seconds, from an open SSH session (one LAN
    round trip, not an SSH handshake); (0, 0) for a local host. Best of three."""
    if host.ssh is None:
        return 0.0, 0.0
    best = None
    for _ in range(3):
        p = subprocess.Popen(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host.ssh,
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
        return None, None
    return round(best[0], 4), round(best[1], 4)


def mqtt_rpc(broker: str, topic: str, body: dict, timeout=5.0):
    """One request/reply through the broker (firmware ping): the reply, or None."""
    import paho.mqtt.client as mqtt
    host, port = broker.rsplit(":", 1)
    tag = os.urandom(4).hex()
    got, res = threading.Event(), {}
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"evicon-c2-ready-{tag}", protocol=mqtt.MQTTv311)

    def on_msg(_c, _u, m):
        try:
            res["body"] = json.loads(m.payload or b"{}")
        except ValueError:
            res["body"] = {}
        got.set()
    c.on_message = on_msg
    sub = threading.Event()
    c.on_subscribe = lambda *_a, **_k: sub.set()
    try:
        c.connect(host, int(port), keepalive=10)
        c.loop_start()
        c.subscribe(f"evicon-ready/{tag}/#", qos=1)
        sub.wait(3)
        c.publish(topic, json.dumps(dict(body, reply=f"evicon-ready/{tag}/r")), qos=1)
        return res.get("body") if got.wait(timeout) else None
    except OSError:
        return None
    finally:
        try:
            c.disconnect()
            c.loop_stop()
        except Exception:  # noqa: BLE001
            pass


# ------------------------------------------------------------------ testbed
class ZephyrTestbed(C.Testbed):
    """Authorities on VM-A (as campaign.py); firmware and plant on VM-E; one MQTT gateway per device on
    VM-C. The fault runner checks and restarts tb.e's processes, so tb.e is VM-C (the gateways) and
    tb.fw is VM-E (firmware, plant)."""

    def __init__(self, cfg, out: Path, bin_dir: Path):
        super().__init__(cfg, out, bin_dir)
        self.out = out
        self.ro = cfg["real_owner"]
        self.fw = self.e  # VM-E (remote) or this host (local)
        self.e = Host("VM-C", "127.0.0.1")
        self.gw_listen = "127.0.0.1"
        self.ports.setdefault("plant_tcp", int(self.ro.get("plant_tcp_port", 18093)))
        self.ns = {}

    def all_ports(self):
        n, p = self.cfg["devices"], self.ports
        return ([p["device"] + 2 * d for d in range(n)] + [p["device"] + 2 * d + 1 for d in range(n)] + [p["proxy"] + d for d in range(n)],
                [p["plant"], p["plant_tcp"]], [p["authority"] + d for d in range(n)])

    def preflight(self):
        c_ports, e_ports, a_ports = self.all_ports()
        busy = {}
        for h, ports in ((self.e, c_ports), (self.fw, e_ports), (self.a, a_ports)):
            used = h.ports_in_use(ports)
            if used:
                busy[h.name] = {p: h.port_owner(p) for p in used}
        if busy:
            raise RuntimeError(f"ports already in use: {busy}. Stop those programs (a previous EviCon run: its pid "
                               f"files; anything else: its owner) or set config.ports; EviCon never stops other programs")
        if self.a.run(f"test -x {shlex.quote(self.bin_a)}/evicon-authority", check=False).returncode != 0:
            raise RuntimeError(f"{self.a.name}: {self.bin_a}/evicon-authority missing (distribute the binaries first)")
        if self.fw.run(f"test -x {shlex.quote(self.bin_e)}/evicon-plant", check=False).returncode != 0:
            raise RuntimeError(f"{self.fw.name}: {self.bin_e}/evicon-plant missing (distribute the binaries first)")
        exe = self.ro["firmware"]
        if self.fw.run(f"test -x {shlex.quote(exe)}", check=False).returncode != 0:
            raise RuntimeError(f"{self.fw.name}: firmware {exe} missing (evicon_review.py campaign copies it from the build VM)")
        if self.cfg.get("mode") == "remote" and self.a.run(f"test -r {shlex.quote(self.cost_file)}", check=False).returncode != 0:
            raise RuntimeError(f"VM-A: {self.cost_file} missing (distribute experiments/configs/query_costs.json)")
        host, port = self.ro["broker"].rsplit(":", 1)
        try:
            socket.create_connection((host, int(port)), timeout=5).close()
        except OSError as e:
            raise RuntimeError(f"Mosquitto at {self.ro['broker']} not reachable from VM-C: {e}")
        offs = {}
        for name, h in (("A", self.a), ("E", self.fw)):
            o, rtt = open_session_offset(h)
            offs[f"{name}_clock_offset_s"], offs[f"{name}_clock_rtt_s"] = o, rtt
        info = dict(offs, binaries_A=self.a.sha256([f"{self.bin_a}/evicon-authority"]),
                    binaries_E=self.fw.sha256([f"{self.bin_e}/evicon-plant", exe]),
                    gateway_sha256=C.sha256_file(GATEWAY),
                    owner={"system": "zephyr", "broker": self.ro["broker"], "firmware": exe, "qos_cmd": int(self.ro.get("qos_cmd", 1)),
                           "contracts": {k: {"mode": v["mode"], "contract_file": str(v["contract_file"]),
                                             "sha256": C.sha256_file(contract_path(v["contract_file"]))}
                                         for k, v in self.ro["contracts"].items()},
                           "policies": self.ro.get("policies", {})})
        return info

    def start(self, cell: Cell, wd: str) -> Stack:
        st = Stack(plant_url="")
        st.fw_pid = []
        try:
            return self._start(cell, wd, st)
        except BaseException:
            self.stop(st, wd)  # nothing started for this cell outlives it
            raise

    def _start(self, cell: Cell, wd: str, st: Stack) -> Stack:
        cfg, p = self.cfg, self.ports
        spec = self.ro["contracts"][cell.contract]
        fw_mode = MODES[spec["mode"]][0]
        acfg, apolicy = CR.cell_config(cfg, cell.policy)
        ns = cell_ns(self.out, cell)
        self.ns[wd] = ns
        e_addr = self.fw.addr
        plant_url = f"http://{e_addr}:{p['plant']}"
        st.plant_url = plant_url
        for h in (self.e, self.fw, self.a):
            h.run(f"mkdir -p {shlex.quote(wd)}")
        st.plant_pid = self.fw.spawn("plant", [f"{self.bin_e}/evicon-plant", "-log", f"{wd}/plant.jsonl", "-http", f"{self.listen}:{p['plant']}",
                                               "-tcp", f"127.0.0.1:{p['plant_tcp']}"]
                                     + (["-fsync=false"] if cfg.get("mode", "local") == "local" else []), wd)
        self.ready(self.fw, plant_url + "/healthz", "plant", st.plant_pid, wd)
        bhost, bport = self.ro["broker"].rsplit(":", 1)
        for d in range(cfg["devices"]):
            dev_id = f"pump{d}-1"
            argv = [self.ro["firmware"], f"-flash={wd}/fw{d}.bin", f"-evc_device={dev_id}", f"-evc_ns={ns}", f"-evc_broker={bhost}",
                    f"-evc_broker_port={bport}", "-evc_plant=127.0.0.1", f"-evc_plant_port={p['plant_tcp']}", f"-evc_mode={fw_mode}"]
            st.fw_pid.append(self.fw.spawn(f"firmware{d}", argv, wd))
        for d in range(cfg["devices"]):
            dev_id = f"pump{d}-1"
            end, rep = time.monotonic() + 20, None
            while time.monotonic() < end and rep is None:
                if not self.fw.alive(st.fw_pid[d]):
                    tail = self.fw.run(f"tail -n 12 {shlex.quote(wd)}/firmware{d}.log", check=False).stdout.strip()
                    raise RuntimeError(f"firmware{d} exited at start: {tail[-600:]}")
                rep = mqtt_rpc(self.ro["broker"], f"{ns}/{dev_id}/ping", {}, timeout=2.0)
            if rep is None or rep.get("mode") != fw_mode or rep.get("device") != dev_id:
                raise RuntimeError(f"firmware{d} ({dev_id}, prefix {ns}) does not answer a ping as mode {fw_mode} within 20 s: {rep}")
            code, out = C.http("POST", f"{plant_url}/v1/limits?device={dev_id}&limit={cfg['workload']['bound']}")
            if code != 200:
                raise RuntimeError(f"plant limit for {dev_id}: {code} {out}")
        for d in range(cfg["devices"]):
            dev_id, gport, aport = f"pump{d}-1", p["device"] + 2 * d, p["device"] + 2 * d + 1
            argv = [sys.executable, str(GATEWAY), "--mode", spec["mode"], "--device-id", dev_id, "--ns", ns,
                    "--listen", f"{self.gw_listen}:{gport}", "--admin-listen", f"{self.gw_listen}:{aport}", "--broker", self.ro["broker"],
                    "--contract-file", str(contract_path(spec["contract_file"])), "--state", f"{wd}/gateway{d}.state.jsonl",
                    "--budget-s", str(self.ro.get("gateway_budget_s", 1.8)), "--qos-cmd", str(int(self.ro.get("qos_cmd", 1))),
                    "--log", f"{wd}/gateway{d}.jsonl"]
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
        if self.fw is not self.e:
            self.fw.stop_all(wd)

    def fetch(self, wd: str, local: Path):
        """raw/E: VM-E's directory (plant, firmware logs, flash files are left out); raw/C: the gateways'
        files on VM-C; raw/A: VM-A's directory. Local mode keeps everything in the cell's raw/."""
        if self.cfg.get("mode", "local") == "local":
            return
        self.fw.fetch_dir(wd, local / "raw" / "E")
        for f in (local / "raw" / "E").glob("fw*.bin"):
            f.unlink()  # simulated flash (2 MiB each): not data
        dst = local / "raw" / "C"
        dst.mkdir(parents=True, exist_ok=True)
        for f in sorted(Path(wd).glob("gateway*")):
            if f.is_file() and not f.name.endswith(".pid"):
                shutil.copy2(f, dst / f.name)
        self.a.fetch_dir(wd, local / "raw" / "A")


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
    """campaign.run_campaign with the owner of experiment C2 and the pseudo-policies of campaign_real.py."""
    tb = ZephyrTestbed(cfg, out, bin_dir)
    info = tb.preflight()
    done = set()
    if (out / "done.jsonl").exists():
        done = {json.loads(line)["cell"] for line in (out / "done.jsonl").read_text().splitlines() if line.strip()}
    man = out / "manifest.json"
    manifest = json.loads(man.read_text()) if man.exists() else {
        "schema_version": 2, "release": RELEASE, "scope": "DEV_LOCAL" if cfg.get("mode", "local") == "local" else "TESTBED",
        "runner": "evaluation/campaign_zephyr.py", "owner_system": "zephyr",
        "config_sha256": hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest(), "driver_host": socket.gethostname(),
        "python": sys.version.split()[0], "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "sessions": []}
    manifest["sessions"].append({"started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "preflight": info})
    man.write_text(json.dumps(manifest, indent=2) + "\n")
    csv_path = out / "runtime_episodes.csv"
    if not csv_path.exists():
        with open(csv_path, "w", newline="") as fh:
            csv.DictWriter(fh, fieldnames=C.ROW_FIELDS).writeheader()
    excluded = {tuple(x) for x in cfg.get("exclude_cells", [])}
    new_cells = 0
    for ep in range(cfg.get("episodes", 1)):
        arrivals, windows = C.schedule(cfg, ep)
        (out / f"schedule_ep{ep}.json").write_text(json.dumps({"arrivals": [asdict(x) for x in arrivals], "faults": [asdict(x) for x in windows]}))
        for contract in cfg["contracts"]:
            for policy in cfg["policies"]:
                if (contract, policy) in excluded:
                    continue
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
                ccfg, _ = CR.cell_config(cfg, policy)
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
                    for i, pid in enumerate(getattr(st, "fw_pid", [])):
                        if not tb.fw.alive(pid):
                            problems.append(f"firmware{i} exited (the owner must stay up: outside the measured fault model)")
                    if not tb.fw.alive(st.plant_pid):
                        problems.append("plant died")
                    summ, truth, status, p2 = C.collect_live(cfg, st, logs)
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
                (celldir / "row.json").write_text(json.dumps(dict(row, problems=sorted(set(problems)), ns=tb.ns.get(wd, "")), indent=2) + "\n")
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
