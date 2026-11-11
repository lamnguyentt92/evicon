#!/usr/bin/env python3
"""Runner of the release-087 firmware campaigns (PREREG_extra_087.md: runs SHRz6 (item A) and RBz6 (item B)).

evaluation/campaign_zephyr_ext086.py (frozen by PREREG_extra_086.md: the 077 runner with the broker and flash counters of
the cost table) runs EviCon authorities against the cutoff firmware behind Mosquitto through one MQTT owner gateway per
device. This runner imports it (and through it campaign_zephyr_ext077.py, campaign_zephyr_ext.py, campaign_zephyr.py and
campaign.py, all frozen) unchanged and adds:

  the 087 policies      evaluation/ext087.py (SHADOW_RECONCILE_1S, EVICON_FI_REC, EVICON_FI_REC_OS), on top of every
                        earlier policy
  the 087 gateway       conformance/reference/mqtt_owner_gateway_ext087.py (on the 077 gateway): the firmware's claim
                        floor (GW-1), claim_floor in every answer (GW-2), and the seal GW-3 when the cell's gateway.recovery is
                        "1"; with the measured firmware (no floor in its replies) and gateway.recovery "0" it answers as
                        the 077 gateway
  real_owner.gateway_recovery_policies: [<policy>, ...]
                        the policies whose cells run the gateway with GW-3 (gateway.recovery = 1 in the cell's directory);
                        every other cell 0
  real_owner.firmware_variant: "default" | "boot"
                        which build the driver copies to VM-E (evicon_review.py: the measured build zephyr-cutoff.exe, or
                        the boot-report build zephyr-boot.exe of firmware/zephyr-boot, with the contract `measure
                        zephyr-boot` measured for it); the runner records it
  real_owner.owner_reboot: {"per_device": [{"kind": "quiet", "range_s": [lo, hi], "after_arrival_s": a},
                                           {"kind": "random", "range_s": [lo, hi]}],
                            "down_s": d}
                        the campaign fault owner_reboot (item B): per device and episode, each listed reboot stops the
                        device's firmware process by its pid file on VM-E (SIGTERM, after 1 s SIGKILL; only while that
                        process still runs in the cell's directory: the pid-file rule of campaign.Host.stop_all), waits
                        down_s, and starts it again with the same arguments and the same flash file (the 083 reboot
                        check's mechanism, conformance/reference/zephyr_reboot_check.py with `measure zephyr-reboot`):
                        the durable cutoff, claim mark and tombstones survive, the RAM ring and level do not. Times:
                          quiet   the longest gap between two scheduled arrivals of the device that starts at or after lo
                                  and ends by hi; the firmware is stopped a seconds after the arrival that opens it (no
                                  scheduled command of that device in flight)
                          random  uniform in [lo, hi] from the separate stream "<seed>/<episode>/reboots/<device>", so
                                  arrivals and fault windows stay byte-identical to the schedules without the fault
                        The pid the runner checks at the end of the cell is the restarted process's. Every reboot is
                        written to the cell's reboots.jsonl (planned time, the pid stopped, how, the restart, the first
                        answer and the floor it reports); the schedule to reboots_ep<e>.json. A reboot that cannot be
                        applied makes the cell INVALID, as any fault that cannot be applied

The reboot fault is outside the fault model under which the firmware's contracts were measured ("owner up"); the plan
says so. Cells, schedules, faults, truth, rows and INVALID rules are otherwise the frozen runners'.

    python3 evaluation/campaign_zephyr_ext087.py --config <derived config> --out <dir>   (as campaign_zephyr_ext086.py)
"""
from __future__ import annotations

import copy
import json
import os
import random
import shlex
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "evaluation"))
sys.path.insert(0, str(REPO / "conformance"))
import ext087  # noqa: E402
import campaign_zephyr_ext086 as Z86  # noqa: E402  (frozen by PREREG_extra_086.md; imported, never edited)

Z77 = Z86.Z77
Z = Z77.Z
C = ext087.install()
RUNNER = "evaluation/campaign_zephyr_ext087.py (on evaluation/campaign_zephyr_ext086.py)"
GATEWAY_EXT087 = REPO / "conformance" / "reference" / "mqtt_owner_gateway_ext087.py"
KEYS_087 = ("owner_reboot", "firmware_variant", "gateway_recovery_policies")
VARIANTS = ("default", "boot")
FROZEN_LOAD_CONFIG_077 = Z77.load_config
STOP_GRACE_S = 1.0
ANSWER_WAIT_S = 20.0


# ------------------------------------------------------------------ config
def check_087(cfg: dict):
    ro = cfg.get("real_owner") or {}
    v = ro.get("firmware_variant", "default")
    if v not in VARIANTS:
        raise ValueError(f"real_owner.firmware_variant: {v!r}, expected default or boot")
    pols = ro.get("gateway_recovery_policies", [])
    if not isinstance(pols, list) or any(p not in cfg.get("policies", []) for p in pols):
        raise ValueError("real_owner.gateway_recovery_policies: a list of policies of this config")
    rb = ro.get("owner_reboot")
    if rb is None:
        return
    if not isinstance(rb, dict) or set(rb) - {"per_device", "down_s"} or not isinstance(rb.get("per_device"), list) or not rb["per_device"]:
        raise ValueError("real_owner.owner_reboot: {\"per_device\": [...], \"down_s\": <seconds>}")
    if not 0.5 <= float(rb.get("down_s", 2.0)) <= 30:
        raise ValueError("real_owner.owner_reboot.down_s: 0.5 to 30 seconds")
    for x in rb["per_device"]:
        lo, hi = (x.get("range_s") or [None, None])[:2]
        if x.get("kind") not in ("quiet", "random") or not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)) \
                or not 0 <= lo < hi <= cfg.get("duration_s", 0):
            raise ValueError(f"real_owner.owner_reboot.per_device: {x!r} (kind quiet|random, range_s [lo, hi] within the cell)")
        if x["kind"] == "quiet" and not 0 < float(x.get("after_arrival_s", 1.5)) < hi - lo:
            raise ValueError(f"real_owner.owner_reboot: after_arrival_s of {x!r}")
    if v != "boot":
        raise ValueError("real_owner.owner_reboot needs real_owner.firmware_variant boot (the floor of the boot-report firmware)")


def load_config(path) -> dict:
    """The 077 runner's checks (and through it the C3 and C2 runners') on a copy without the 087 keys, then the 087 rules."""
    cfg = json.loads(Path(path).read_text())
    check_087(cfg)
    sub = copy.deepcopy(cfg)
    for k in KEYS_087:
        sub.get("real_owner", {}).pop(k, None)
    tmp = Path(os.environ.get("TMPDIR", "/tmp")) / f"campaign_zephyr_ext087_check_{os.getpid()}.json"
    try:
        tmp.write_text(json.dumps(sub))
        FROZEN_LOAD_CONFIG_077(tmp)
    finally:
        tmp.unlink(missing_ok=True)
    return cfg


# ------------------------------------------------------------------ the owner_reboot schedule
def reboot_schedule(cfg: dict, episode: int) -> list:
    """[{device, kind, t, ...}] for one episode: deterministic, from the config and the episode's arrivals only."""
    rb = (cfg.get("real_owner") or {}).get("owner_reboot")
    if not rb:
        return []
    arrivals, _ = C.schedule(cfg, episode)
    out = []
    for d in range(cfg["devices"]):
        rng = random.Random(f"{cfg['seed']}/{episode}/reboots/{d}")
        ts = sorted(a.t for a in arrivals if a.device == d)
        for x in rb["per_device"]:
            lo, hi = float(x["range_s"][0]), float(x["range_s"][1])
            if x["kind"] == "random":
                out.append({"device": d, "kind": "random", "t": round(rng.uniform(lo, hi), 3)})
                continue
            after = float(x.get("after_arrival_s", 1.5))
            best = None
            for a, b in zip(ts, ts[1:]):
                if a >= lo and b <= hi and (best is None or b - a > best[1] - best[0]):
                    best = (a, b)
            if best is None:  # no two arrivals in the range: its start
                out.append({"device": d, "kind": "quiet", "t": round(lo + after, 3), "gap_s": None})
            else:
                out.append({"device": d, "kind": "quiet", "t": round(best[0] + after, 3), "gap_s": round(best[1] - best[0], 3),
                            "gap_start_s": round(best[0], 3)})
    return sorted(out, key=lambda r: (r["t"], r["device"]))


def _stop_script(wd: str, name: str, grace_s: float) -> str:
    """Stops the process of <wd>/<name>.pid only while it still runs in wd (SIGTERM, after grace_s SIGKILL); prints how."""
    w, n = shlex.quote(str(wd)), shlex.quote(name)
    steps = max(1, int(grace_s * 10))
    return (f"d={w}; real=$(cd \"$d\" && pwd -P) || exit 3; p=$(cat \"$d\"/{n}.pid 2>/dev/null); "
            f"case \"$p\" in ''|*[!0-9]*) echo nopid; exit 4;; esac; "
            f"[ \"$(readlink /proc/$p/cwd 2>/dev/null)\" = \"$real\" ] || {{ echo notrunning; exit 5; }}; "
            f"tr '\\0' '\\n' < /proc/$p/cmdline > \"$d\"/{n}.argv; kill -TERM $p 2>/dev/null; how=TERM; "
            f"for i in $(seq {steps}); do kill -0 $p 2>/dev/null || break; sleep 0.1; done; "
            f"if kill -0 $p 2>/dev/null && [ \"$(readlink /proc/$p/cwd 2>/dev/null)\" = \"$real\" ]; then kill -KILL $p; how=KILL; fi; "
            f"for i in $(seq 50); do kill -0 $p 2>/dev/null || {{ echo \"$how $p\"; exit 0; }}; sleep 0.1; done; echo alive; exit 6")


class RebootThread(threading.Thread):
    """Applies one device's reboots at their times (the owner_reboot fault)."""

    def __init__(self, tb, st, d: int, plan: list, t0: float, wd: str, celldir: Path, cfg: dict, stop_flag: threading.Event, errors: list):
        super().__init__(daemon=True)
        self.tb, self.st, self.d, self.plan, self.t0, self.wd, self.celldir = tb, st, d, plan, t0, wd, celldir
        self.cfg, self.stop_flag, self.errors = cfg, stop_flag, errors
        self.down_s = float(cfg["real_owner"]["owner_reboot"].get("down_s", 2.0))

    def run(self):
        for k, r in enumerate(self.plan):
            delay = self.t0 + r["t"] - time.time()
            if delay > 0 and self.stop_flag.wait(delay):
                return
            rec = dict(r, index=k, planned_at=round(self.t0 + r["t"], 3), down_s=self.down_s, ok=True, detail="")
            try:
                self._reboot(k, rec)
            except Exception as e:  # recorded; the cell becomes INVALID (a fault that could not be applied)
                rec["ok"], rec["detail"] = False, f"{type(e).__name__}: {e}"
                self.errors.append(f"owner_reboot device {self.d}: {rec['detail']}")
            with open(self.celldir / "reboots.jsonl", "a") as fh:
                fh.write(json.dumps(rec, sort_keys=True) + "\n")

    def _reboot(self, k: int, rec: dict):
        tb, d, wd = self.tb, self.d, self.wd
        name = f"firmware{d}"
        ns = tb.ns.get(wd, "")
        dev = f"pump{d}-1"
        rec["old_pid"] = self.st.fw_pid[d]
        rec["killed_at"] = round(time.time(), 6)
        p = tb.fw.run(_stop_script(wd, name, STOP_GRACE_S), check=False, timeout=60)
        out = (p.stdout or "").strip()
        if p.returncode != 0:
            raise RuntimeError(f"stop by pid file: rc={p.returncode} {out[-200:]} {p.stderr.strip()[-200:]}")
        rec["stopped"] = out
        rec["stopped_at"] = round(time.time(), 6)
        argv = [x for x in tb.fw.run(f"cat {shlex.quote(str(wd))}/{name}.argv", check=True).stdout.split("\n") if x]
        if not argv or argv[0] != tb.ro["firmware"] or f"-flash={wd}/fw{d}.bin" not in argv or f"-evc_ns={ns}" not in argv:
            raise RuntimeError(f"the stopped process's arguments are not this cell's firmware{d}: {argv[:3]}")
        tb.fw.run(f"cd {shlex.quote(str(wd))} && mv -f {name}.log {name}.boot{k}.log", check=False)
        self.stop_flag.wait(self.down_s)
        pid = tb.fw.spawn(name, argv, wd)
        if pid is None:
            raise RuntimeError("restart: no pid")
        self.st.fw_pid[d] = pid
        rec["new_pid"], rec["started_at"] = pid, round(time.time(), 6)
        end, rep = time.monotonic() + ANSWER_WAIT_S, None
        while time.monotonic() < end and rep is None:
            if not tb.fw.alive(pid):
                raise RuntimeError(f"firmware{d} exited after the restart")
            rep = Z.mqtt_rpc(tb.ro["broker"], f"{ns}/{dev}/ping", {}, timeout=2.0)
        if rep is None:
            raise RuntimeError(f"firmware{d} does not answer a ping within {ANSWER_WAIT_S:.0f} s after the restart")
        rec["first_answer_at"] = round(time.time(), 6)
        rec["floor"] = rep.get("floor")
        rec["cutoff_after"] = rep.get("cutoff")


class RebootFaultRunner(C.FaultRunner):
    """campaign.FaultRunner (frozen) for the device's windows, plus the device's reboots in a thread of their own."""

    def run(self):
        plan = getattr(self.tb, "_reboot_plan", {}).get(self.wd, [])
        mine = [r for r in plan if r["device"] == self.d]
        th = None
        if mine:
            th = RebootThread(self.tb, self.st, self.d, mine, self.t0, self.wd, self.tb._celldir[self.wd], self.cfg, self.stop_flag, self.errors)
            th.start()
        super().run()
        if th is not None:
            while th.is_alive() and not self.stop_flag.is_set():
                th.join(0.5)
            th.join(timeout=ANSWER_WAIT_S + 15)


class Ext087Testbed(Z86.Ext086Testbed):
    def preflight(self):
        info = super().preflight()
        info["gateway_ext087_sha256"] = Z.C.sha256_file(GATEWAY_EXT087)
        info["runner_ext087"] = "evaluation/campaign_zephyr_ext087.py"
        info["firmware_variant"] = self.ro.get("firmware_variant", "default")
        info["gateway_recovery_policies"] = list(self.ro.get("gateway_recovery_policies", []))
        info["owner_reboot"] = self.ro.get("owner_reboot")
        return info

    def start(self, cell, wd: str):
        Path(wd).mkdir(parents=True, exist_ok=True)  # the gateways run on this host (VM-C)
        rec = cell.policy in self.ro.get("gateway_recovery_policies", [])
        Path(wd, "gateway.recovery").write_text(("1" if rec else "0") + "\n")
        if not hasattr(self, "_celldir"):
            self._celldir, self._reboot_plan = {}, {}
        celldir = self.out / "cells" / f"ep{cell.episode}" / cell.contract / cell.policy
        self._celldir[wd] = celldir
        self._reboot_plan[wd] = reboot_schedule(self.cfg, cell.episode)
        if self._reboot_plan[wd]:
            (self.out / f"reboots_ep{cell.episode}.json").write_text(json.dumps(self._reboot_plan[wd], indent=1) + "\n")
            (celldir / "reboots.jsonl").write_text("")
        return super().start(cell, wd)


def _mark(out: Path, cfg: dict):
    man = Path(out) / "manifest.json"
    if man.exists():
        m = json.loads(man.read_text())
        if not str(m.get("runner", "")).startswith(RUNNER):
            m["runner"] = RUNNER + " (" + m.get("runner", "") + ")"
        ro = cfg.get("real_owner") or {}
        m["release_087"] = {"gateway": "conformance/reference/mqtt_owner_gateway_ext087.py", "firmware_variant": ro.get("firmware_variant", "default"),
                            "gateway_recovery_policies": ro.get("gateway_recovery_policies", []), "owner_reboot": ro.get("owner_reboot"),
                            "reboots": "cells/*/*/*/reboots.jsonl" if ro.get("owner_reboot") else None}
        man.write_text(json.dumps(m, indent=2) + "\n")


def main(argv=None):
    Z77.Ext077Testbed = Ext087Testbed  # Z77.main installs it as the runner's testbed class
    Z77.GATEWAY_EXT077 = GATEWAY_EXT087  # Z77.main installs it as the runner's gateway
    Z77.load_config = load_config  # Z77.main installs it as the runner's config check
    C.FaultRunner = RebootFaultRunner  # campaign.run_cell starts one per device
    mark077 = Z77._mark_manifest
    args = list(sys.argv[1:] if argv is None else argv)
    if "--config" in args:
        cfg = json.loads(Path(args[args.index("--config") + 1]).read_text())
    elif "--resume" in args:
        cfg = json.loads((Path(args[args.index("--resume") + 1]) / "config.json").read_text())
    else:
        cfg = {}

    def mark(out):
        mark077(out)
        man = Path(out) / "manifest.json"
        if man.exists():
            m = json.loads(man.read_text())
            m.setdefault("counters_086", {"broker": "raw/C/broker_sys.json", "flash": "raw/E/flash_writes.json"})
            man.write_text(json.dumps(m, indent=2) + "\n")
        _mark(Path(out), cfg)
    Z77._mark_manifest = mark
    return Z77.main(args)


if __name__ == "__main__":
    sys.exit(main())
