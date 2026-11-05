#!/usr/bin/env python3
"""Runner of the release-086 firmware campaigns (PREREG_extra_086.md, runs SRz6 and SRzi): the 077 runner with two
counters for the physical-unit cost table (estimand E-COST; reviewer comment M3(1)). Nothing a cell does changes.

evaluation/campaign_zephyr_ext077.py (frozen by PREREG_review_077.md; on the frozen C3 and C2 runners) runs EviCon
authorities against the cutoff firmware behind Mosquitto with the delivery binding of each contract. This runner imports
it unchanged, replaces its testbed class by a subclass that adds, per cell:

  broker counter   the broker's own counters of every MQTT packet it received and sent ($SYS/broker/messages/received,
                   .../sent, $SYS/broker/publish/messages/received, .../sent: Mosquitto publishes them, retained, every
                   sys_interval = 10 s by default). Read once after the testbed's preflight and once after each cell's
                   processes are stopped, each read SYS_WAIT_S = 11 s after the last activity so that the value comes
                   from a tick after it; a cell's packets are the difference of the reads around it minus the packets
                   of one read (measured in the preflight from two reads with nothing in between). All packets of the
                   cell's four gateways and four firmware processes through the broker: publishes, acknowledgements,
                   connects, subscriptions, keepalives. Written to raw/C/broker_sys.json
  flash counter    the firmware's flash writes, counted from each firmware process's flash file on VM-E
                   (evaluation/nvs_flash.py, piped to VM-E's python3) before the frozen runner's fetch deletes the
                   files: value writes per settings name (evicon/cutoff, evicon/hw, evicon/tomb). Written to
                   raw/E/flash_writes.json

A counter that fails is recorded in its file ("error") and never makes a cell INVALID. The 085 runs have neither
counter; the report says so (evaluation/cost086.py).

    python3 evaluation/campaign_zephyr_ext086.py --config <derived config> --out <dir>    (as campaign_zephyr_ext077.py)
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "evaluation"))
sys.path.insert(0, str(REPO / "conformance"))
import campaign_zephyr_ext077 as Z77  # noqa: E402  (frozen by PREREG_review_077.md; imported, never edited)

RUNNER = "evaluation/campaign_zephyr_ext086.py (on evaluation/campaign_zephyr_ext077.py)"
SYS_TOPICS = ("$SYS/broker/messages/received", "$SYS/broker/messages/sent", "$SYS/broker/publish/messages/received",
              "$SYS/broker/publish/messages/sent")
SYS_WAIT_S = 11.0
NVS_SCRIPT = REPO / "evaluation" / "nvs_flash.py"
BASE = Z77.Ext077Testbed


def sys_read(broker: str, timeout: float = 5.0) -> dict:
    """One read of the broker's retained $SYS counters: {topic: int}."""
    import paho.mqtt.client as mqtt
    host, port = broker.rsplit(":", 1)
    got, ev = {}, threading.Event()
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"evicon-sys-{time.time_ns()}", protocol=mqtt.MQTTv311)

    def on_message(_c, _u, m):
        try:
            got[m.topic] = int(m.payload)
        except ValueError:
            return
        if len(got) == len(SYS_TOPICS):
            ev.set()
    c.on_message = on_message
    c.on_connect = lambda cl, *a, **k: [cl.subscribe(t, 0) for t in SYS_TOPICS]
    c.connect(host, int(port), keepalive=30)
    c.loop_start()
    try:
        if not ev.wait(timeout):
            raise RuntimeError(f"$SYS counters not received from {broker} within {timeout} s (got {sorted(got)})")
    finally:
        c.loop_stop()
        c.disconnect()
    return dict(got)


def diff(a: dict, b: dict) -> dict:
    return {t: b[t] - a[t] for t in SYS_TOPICS}


class Ext086Testbed(BASE):
    def preflight(self):
        info = super().preflight()
        self._sys_last, self._sys_reader, self._sys_note = None, None, ""
        try:
            time.sleep(SYS_WAIT_S)
            r0 = sys_read(self.ro["broker"])
            time.sleep(SYS_WAIT_S)
            r1 = sys_read(self.ro["broker"])
            self._sys_reader, self._sys_last = diff(r0, r1), r1  # the packets of one read, nothing else in between
        except Exception as e:  # noqa: BLE001  a counter never stops a campaign
            self._sys_note = f"{type(e).__name__}: {e}"
        self._sys_cell = {}
        return info

    def stop(self, st, wd: str):
        super().stop(st, wd)
        rec = {"before": getattr(self, "_sys_last", None), "reader_per_read": getattr(self, "_sys_reader", None), "wait_s": SYS_WAIT_S,
               "broker": self.ro.get("broker"), "topics": list(SYS_TOPICS)}
        try:
            time.sleep(SYS_WAIT_S)
            after = sys_read(self.ro["broker"])
            rec["after"], self._sys_last = after, after
            if rec["before"] is not None and rec["reader_per_read"] is not None:
                d = diff(rec["before"], after)
                rec["cell"] = {t: d[t] - rec["reader_per_read"][t] for t in SYS_TOPICS}
            else:
                rec["error"] = "no earlier read: " + (getattr(self, "_sys_note", "") or "the previous read failed")
        except Exception as e:  # noqa: BLE001
            rec["error"], self._sys_last = f"{type(e).__name__}: {e}", None
        if not hasattr(self, "_sys_cell"):
            self._sys_cell = {}
        self._sys_cell[wd] = rec

    def _flash_counts(self, wd: str):
        src = NVS_SCRIPT.read_text()
        if getattr(self.fw, "ssh", None) is None:
            argv = [sys.executable, "-", wd]
        else:
            argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", self.fw.ssh, f"python3 - {wd}"]
        p = subprocess.run(argv, input=src, capture_output=True, text=True, timeout=120)
        if p.returncode != 0:
            raise RuntimeError(f"nvs_flash.py on the firmware host: rc={p.returncode}: {p.stderr.strip()[:300]}")
        return json.loads(p.stdout)

    def fetch(self, wd: str, local: Path):
        flash = {"counter": "evaluation/nvs_flash.py on the firmware host, before the flash files are deleted"}
        try:
            flash["files"] = self._flash_counts(wd)
        except Exception as e:  # noqa: BLE001
            flash["error"] = f"{type(e).__name__}: {e}"
        super().fetch(wd, local)
        local = Path(local)
        remote = self.cfg.get("mode", "local") != "local"
        rc, re_ = (local / "raw" / "C", local / "raw" / "E") if remote else (local / "raw", local / "raw")
        for d in (rc, re_):
            d.mkdir(parents=True, exist_ok=True)
        (re_ / "flash_writes.json").write_text(json.dumps(flash, indent=1) + "\n")
        rec = getattr(self, "_sys_cell", {}).pop(wd, {"error": "the cell's processes were not stopped through stop()"})
        (rc / "broker_sys.json").write_text(json.dumps(rec, indent=1) + "\n")


def main(argv=None):
    Z77.Ext077Testbed = Ext086Testbed  # Z77.main installs it as the runner's testbed class
    mark077 = Z77._mark_manifest

    def mark(out):
        mark077(out)
        man = Path(out) / "manifest.json"
        if man.exists():
            m = json.loads(man.read_text())
            if not str(m.get("runner", "")).startswith(RUNNER):
                m["runner"] = RUNNER + " (" + m.get("runner", "") + ")"
            m["counters_086"] = {"broker": "raw/C/broker_sys.json", "flash": "raw/E/flash_writes.json"}
            man.write_text(json.dumps(m, indent=2) + "\n")
    Z77._mark_manifest = mark
    return Z77.main(argv)


if __name__ == "__main__":
    sys.exit(main())
