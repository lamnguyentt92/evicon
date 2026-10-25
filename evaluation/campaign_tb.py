#!/usr/bin/env python3
"""Runtime campaign on a device-management platform: experiment TB, EviCon authorities against ThingsBoard CE's
persistent server-side RPC (release 077, PREREG_review_077.md).

The cell procedure, the seeded schedules (common random numbers), the clients, the fault runner, the truth from
evicon-plant, the row and the activation gate are those of evaluation/campaign.py, and the pseudo-policies those
of evaluation/campaign_real.py; both are frozen by earlier plans and imported, never edited. What differs from an
evicon-device campaign:

  owner    ThingsBoard CE 4.3.1.6 on VM-B (EviCon's compose project, started by the driver). Per cell and device,
           a ThingsBoard device made for the cell (named after the cell, deleted at its end, so no RPC of an earlier
           cell reaches a later one) and one reference MQTT device (conformance/reference/mqtt_device.py --mode
           thingsboard) on VM-E connected to ThingsBoard's MQTT API with that device's access token (on stdin,
           never on a command line), executing every "actuate" RPC it receives and writing the effect to
           evicon-plant on VM-E (the independent truth)
  client   per device, one ThingsBoard owner gateway (conformance/reference/tb_owner_gateway.py) on VM-C: the
           device-owner protocol of evicon-device towards the authority, ThingsBoard's REST API towards the
           platform (persistent two-way RPC without platform retries, expirationTime = the request's
           expires_at_ms), advertising the contract `measure thingsboard` MEASURED in the same review run for
           that call shape (thingsboard-rpc-r0: Q_A, T)
  faults   as in campaign.py: drop_up / drop_down / delay_up on the authority -> gateway path (the per-device
           fault proxy); crash_after_effect (the gateway exits after ThingsBoard reported the RPC SUCCESSFUL,
           before it replies; restarted after crash_recovery_s: a client abandon); offline (the device's MQTT
           connection to ThingsBoard is down: RPCs wait in ThingsBoard and reach the device when it reconnects,
           unless they expired). All inside the fault model of the measurement ("network loss/delay, client
           abandon; owner up")
  truth    evicon-plant on VM-E, read over its HTTP API while the stack is up (campaign.collect_live)

Config: a campaign.py config (schema_version 2) plus
  "real_owner": {"system": "thingsboard", "tb_url": "http://<VM-B>:18088", "tb_mqtt": "<VM-B>:1885" (the driver
                 fills both in), "contracts": {"<name>": {"contract_file": "<path>"}},
                 "policies": {"<name>": {"base": "<policy>", "authority": {<overrides>}}},
                 "gateway_budget_s": 1.8, "rpc_timeout_ms": 5000, "expiration_s": 60, "device_admin_port": 18220}
Credentials: the tenant user in this process's environment (TB_USERNAME, TB_PASSWORD; the driver sets them),
inherited by the gateways; device tokens read from ThingsBoard per cell and passed to the devices on stdin.

  python3 evaluation/campaign_tb.py --config <derived config> --out <dir>
  python3 evaluation/campaign_tb.py --resume <run dir>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "evaluation"))
sys.path.insert(0, str(REPO / "conformance"))
import campaign as C  # noqa: E402  (frozen; imported, never edited)
import campaign_real as CR  # noqa: E402  (frozen; imported, never edited)
import campaign_zephyr as Z  # noqa: E402  (frozen; its run_campaign and clock offset are reused)
from campaign import Cell, Host, Stack  # noqa: E402
from evicon_probe.faults import FaultProxy  # noqa: E402
from reference.tb_owner_gateway import CAPS, TB  # noqa: E402

RELEASE = C.RELEASE
GATEWAY = REPO / "conformance" / "reference" / "tb_owner_gateway.py"
DEVICE = REPO / "conformance" / "reference" / "mqtt_device.py"
REAL_KEYS = {"system", "tb_url", "tb_mqtt", "contracts", "policies", "gateway_budget_s", "rpc_timeout_ms", "expiration_s",
             "device_admin_port"}


def contract_path(p: str) -> Path:
    q = Path(p)
    return q if q.is_absolute() else REPO / q


def load_config(path) -> dict:
    """campaign.load_config's checks (through campaign_real's pseudo-policy rules), plus the real_owner section of
    experiment TB."""
    cfg = json.loads(Path(path).read_text())
    ro = cfg.get("real_owner")
    if not isinstance(ro, dict):
        raise ValueError("real_owner section required")
    unknown = set(ro) - REAL_KEYS
    if unknown:
        raise ValueError(f"unknown real_owner keys {sorted(unknown)}")
    if ro.get("system") != "thingsboard":
        raise ValueError("real_owner.system: thingsboard")
    for k in ("tb_url", "tb_mqtt", "contracts"):
        if not ro.get(k):
            raise ValueError(f"real_owner.{k} required")
    for name in cfg.get("contracts", []):
        spec = ro["contracts"].get(name)
        if not spec or not contract_path(str(spec.get("contract_file", ""))).is_file():
            raise ValueError(f"contract {name}: real_owner.contracts.{name} needs an existing contract_file")
        c = json.loads(contract_path(spec["contract_file"]).read_text())
        if c.get("source") != "MEASURED" or set(c.get("capabilities", [])) != CAPS:
            raise ValueError(f"contract {name}: {spec['contract_file']} is {c.get('source')} {sorted(c.get('capabilities', []))}, "
                             f"experiment TB needs MEASURED {sorted(CAPS)}")
    pseudo = ro.get("policies", {})
    for name, p in pseudo.items():
        if p.get("base") not in C.RESOLVERS + C.LEGACY:
            raise ValueError(f"pseudo-policy {name}: base {p.get('base')!r} is not a campaign.py policy")
        bad = set(p.get("authority", {})) - {"retry_guard", "max_network_delay", "max_outstanding", "resolve_sweep", "periodic"}
        if bad:
            raise ValueError(f"pseudo-policy {name}: authority overrides {sorted(bad)} not allowed")
    base = {k: v for k, v in cfg.items() if k not in ("real_owner", "exclude_cells")}
    base["policies"] = [pseudo[p]["base"] if p in pseudo else p for p in cfg["policies"]]
    tmp = Path(os.environ.get("TMPDIR", "/tmp")) / f"campaign_tb_check_{os.getpid()}.json"
    try:
        tmp.write_text(json.dumps(base))
        C.load_config(tmp)
    finally:
        tmp.unlink(missing_ok=True)
    return cfg


def cell_name(out: Path, cell: Cell) -> str:
    return "evc" + hashlib.sha256(f"{out}/{cell.key}".encode()).hexdigest()[:10]


def spawn_with_secret(host: Host, name: str, argv, workdir: str, var: str, value: str):
    """Host.spawn whose process gets `var` in its environment, read from stdin (never on a command line)."""
    q = " ".join(shlex.quote(str(x)) for x in argv)
    w = shlex.quote(str(workdir))
    cmd = (f"mkdir -p {w} && cd {w} && IFS= read -r {var} && export {var} && "
           f"(nohup {q} > {shlex.quote(name)}.log 2>&1 < /dev/null & echo $! > {shlex.quote(name)}.pid)")
    argv2 = ["bash", "-c", cmd] if host.ssh is None else ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host.ssh, cmd]
    p = subprocess.run(argv2, input=value + "\n", capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        raise RuntimeError(f"{host.name}: starting {name} failed: {p.stderr.strip()[:300]}")
    return host.pid(name, workdir)


class TBTestbed(C.Testbed):
    """Authorities on VM-A (as campaign.py); the MQTT devices and the plant on VM-E; one ThingsBoard gateway per
    device on VM-C. The fault runner checks and restarts tb.e's processes, so tb.e is VM-C (the gateways) and
    tb.dev is VM-E."""

    def __init__(self, cfg, out: Path, bin_dir: Path):
        super().__init__(cfg, out, bin_dir)
        self.out = out
        self.ro = cfg["real_owner"]
        self.dev = self.e
        self.fw = self.dev  # the C2 cell loop checks tb.fw (the plant, and st.fw_pid: here the MQTT devices)
        self.e = Host("VM-C", "127.0.0.1")
        self.gw_listen = "127.0.0.1"
        self.ports.setdefault("tb_admin", int(self.ro.get("device_admin_port", 18220)))
        self.root_e = str(REPO) if cfg.get("mode", "local") == "local" else str(Path(self.bin_e).parent)
        user, pw = os.environ.get("TB_USERNAME", ""), os.environ.get("TB_PASSWORD", "")
        if not user or not pw:
            raise RuntimeError("TB_USERNAME / TB_PASSWORD missing from the environment (the driver sets them)")
        self.api = TB(self.ro["tb_url"], user, pw)
        self.tb_devices = {}  # wd -> [uuid]
        self.names = {}
        self.ns = self.names  # the C2 cell loop records tb.ns[wd] in row.json

    def all_ports(self):
        n, p = self.cfg["devices"], self.ports
        return ([p["device"] + 2 * d for d in range(n)] + [p["device"] + 2 * d + 1 for d in range(n)] + [p["proxy"] + d for d in range(n)],
                [p["plant"]] + [p["tb_admin"] + d for d in range(n)], [p["authority"] + d for d in range(n)])

    def preflight(self):
        c_ports, e_ports, a_ports = self.all_ports()
        busy = {}
        for h, ports in ((self.e, c_ports), (self.dev, e_ports), (self.a, a_ports)):
            used = h.ports_in_use(ports)
            if used:
                busy[h.name] = {p: h.port_owner(p) for p in used}
        if busy:
            raise RuntimeError(f"ports already in use: {busy}. Stop those programs (a previous EviCon run: its pid "
                               f"files; anything else: its owner) or set config.ports; EviCon never stops other programs")
        if self.a.run(f"test -x {shlex.quote(self.bin_a)}/evicon-authority", check=False).returncode != 0:
            raise RuntimeError(f"{self.a.name}: {self.bin_a}/evicon-authority missing (distribute the binaries first)")
        if self.dev.run(f"test -x {shlex.quote(self.bin_e)}/evicon-plant", check=False).returncode != 0:
            raise RuntimeError(f"{self.dev.name}: {self.bin_e}/evicon-plant missing (distribute the binaries first)")
        dev_py = f"{self.root_e}/conformance/reference/mqtt_device.py"
        if self.dev.run(f"test -r {shlex.quote(dev_py)}", check=False).returncode != 0:
            raise RuntimeError(f"{self.dev.name}: {dev_py} missing (the driver deploys the conformance tree)")
        if self.cfg.get("mode") == "remote" and self.a.run(f"test -r {shlex.quote(self.cost_file)}", check=False).returncode != 0:
            raise RuntimeError(f"VM-A: {self.cost_file} missing (distribute experiments/configs/query_costs.json)")
        if not self.api.login(timeout=10):
            raise RuntimeError(f"ThingsBoard login at {self.ro['tb_url']} failed")
        st, info = self.api.call("GET", "/api/system/info", timeout=10)
        offs = {}
        for name, h in (("A", self.a), ("E", self.dev)):
            o, rtt = Z.open_session_offset(h)
            offs[f"{name}_clock_offset_s"], offs[f"{name}_clock_rtt_s"] = o, rtt
        return dict(offs, binaries_A=self.a.sha256([f"{self.bin_a}/evicon-authority"]), binaries_E=self.dev.sha256([f"{self.bin_e}/evicon-plant"]),
                    gateway_sha256=C.sha256_file(GATEWAY), device_sha256=C.sha256_file(DEVICE),
                    owner={"system": "thingsboard", "tb_url": self.ro["tb_url"], "tb_mqtt": self.ro["tb_mqtt"],
                           "system_info": info if st == 200 else f"HTTP {st}",
                           "contracts": {k: {"contract_file": str(v["contract_file"]), "sha256": C.sha256_file(contract_path(v["contract_file"]))}
                                         for k, v in self.ro["contracts"].items()},
                           "policies": self.ro.get("policies", {}), "rpc_timeout_ms": self.ro.get("rpc_timeout_ms", 5000),
                           "expiration_s": self.ro.get("expiration_s", 60)})

    def _tb_device(self, name: str):
        st, dev = self.api.call("POST", "/api/device", {"name": name, "type": "evicon", "label": "EviCon experiment TB"}, timeout=15)
        if st != 200 or not isinstance(dev, dict):
            raise RuntimeError(f"ThingsBoard: creating device {name} failed: HTTP {st} {str(dev)[:160]}")
        uid = dev["id"]["id"]
        st, cred = self.api.call("GET", f"/api/device/{uid}/credentials", timeout=15)
        if st != 200 or not isinstance(cred, dict) or not cred.get("credentialsId"):
            raise RuntimeError(f"ThingsBoard: reading the credentials of {name} failed: HTTP {st}")
        return uid, cred["credentialsId"]

    def start(self, cell: Cell, wd: str) -> Stack:
        st = Stack(plant_url="")
        st.dev_pid = []
        st.fw_pid = st.dev_pid  # checked alive at the end of the cell by the C2 cell loop (the owner side must stay up)
        try:
            return self._start(cell, wd, st)
        except BaseException:
            self.stop(st, wd)
            raise

    def _start(self, cell: Cell, wd: str, st: Stack) -> Stack:
        cfg, p = self.cfg, self.ports
        spec = self.ro["contracts"][cell.contract]
        acfg, apolicy = CR.cell_config(cfg, cell.policy)
        name = cell_name(self.out, cell)
        self.names[wd] = name
        plant_url = f"http://{self.dev.addr}:{p['plant']}"
        st.plant_url = plant_url
        for h in (self.e, self.dev, self.a):
            h.run(f"mkdir -p {shlex.quote(wd)}")
        st.plant_pid = self.dev.spawn("plant", [f"{self.bin_e}/evicon-plant", "-log", f"{wd}/plant.jsonl", "-http", f"{self.listen}:{p['plant']}"]
                                      + (["-fsync=false"] if cfg.get("mode", "local") == "local" else []), wd)
        self.ready(self.dev, plant_url + "/healthz", "plant", st.plant_pid, wd)
        uids = []
        self.tb_devices[wd] = uids
        for d in range(cfg["devices"]):
            dev_id = f"pump{d}-1"
            uid, token = self._tb_device(f"{name}-{dev_id}")
            uids.append(uid)
            argv = ["python3", f"{self.root_e}/conformance/reference/mqtt_device.py", "--mode", "thingsboard", "--broker", self.ro["tb_mqtt"],
                    "--device", dev_id, "--plant", f"http://127.0.0.1:{p['plant']}", "--admin-port", str(p["tb_admin"] + d),
                    "--admin-bind", self.listen]
            # the process name gives the role the readiness check expects (campaign.Testbed.ready: name without its
            # index); the reference device's /healthz names its role "mqtt-device"
            st.dev_pid.append(spawn_with_secret(self.dev, f"mqtt-device{d}", argv, wd, "TB_DEVICE_TOKEN", token))
            self.ready(self.dev, f"http://{self.dev.addr}:{p['tb_admin'] + d}/healthz", f"mqtt-device{d}", st.dev_pid[-1], wd)
            code, out = C.http("POST", f"{plant_url}/v1/limits?device={dev_id}&limit={cfg['workload']['bound']}")
            if code != 200:
                raise RuntimeError(f"plant limit for {dev_id}: {code} {out}")
        for d in range(cfg["devices"]):
            dev_id, gport, aport = f"pump{d}-1", p["device"] + 2 * d, p["device"] + 2 * d + 1
            argv = [sys.executable, str(GATEWAY), "--device-id", dev_id, "--tb-device", uids[d], "--listen", f"{self.gw_listen}:{gport}",
                    "--admin-listen", f"{self.gw_listen}:{aport}", "--tb-url", self.ro["tb_url"],
                    "--device-admin", f"http://{self.dev.addr}:{p['tb_admin'] + d}", "--contract-file", str(contract_path(spec["contract_file"])),
                    "--budget-s", str(self.ro.get("gateway_budget_s", 1.8)), "--rpc-timeout-ms", str(int(self.ro.get("rpc_timeout_ms", 5000))),
                    "--expiration-s", str(float(self.ro.get("expiration_s", 60))), "--log", f"{wd}/gateway{d}.jsonl"]
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
        if self.dev is not self.e:
            self.dev.stop_all(wd)
        for uid in self.tb_devices.pop(wd, []):  # the cell's ThingsBoard devices (and their RPCs) go with it
            self.api.call("DELETE", f"/api/device/{uid}", timeout=15)

    def fetch(self, wd: str, local: Path):
        """raw/E: VM-E's directory (plant, device logs); raw/C: the gateways' files; raw/A: VM-A's directory."""
        if self.cfg.get("mode", "local") == "local":
            return
        self.dev.fetch_dir(wd, local / "raw" / "E")
        dst = local / "raw" / "C"
        dst.mkdir(parents=True, exist_ok=True)
        for f in sorted(Path(wd).glob("gateway*")):
            if f.is_file() and not f.name.endswith(".pid"):
                dst.joinpath(f.name).write_bytes(f.read_bytes())
        self.a.fetch_dir(wd, local / "raw" / "A")


def main(argv=None):
    """campaign_zephyr.main and run_campaign with this module's config rules and testbed (both functions are
    the C2 runner's, frozen; they read the testbed class and load_config from their module)."""
    Z.load_config = load_config
    Z.ZephyrTestbed = TBTestbed
    orig = Z.run_campaign

    def run_campaign(cfg, out, bin_dir, max_cells=0):
        try:
            return orig(cfg, out, bin_dir, max_cells)
        finally:
            man = out / "manifest.json"
            if man.exists():
                m = json.loads(man.read_text())
                m.update(runner="evaluation/campaign_tb.py (on evaluation/campaign_zephyr.py's cell loop)", owner_system="thingsboard")
                man.write_text(json.dumps(m, indent=2) + "\n")
    Z.run_campaign = run_campaign
    return Z.main(argv)


if __name__ == "__main__":
    sys.exit(main())
