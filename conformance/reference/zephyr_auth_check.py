#!/usr/bin/env python3
"""What the authenticated EviCon firmware accepts and certifies (release 077, `measure zephyr-auth`).

firmware/zephyr-auth (the cutoff firmware with request MACs, reply MACs and a cutoff bound; see its
src/main_auth.c) runs in stop mode with a fresh flash. This check talks to it through the broker as the
gateway and as an attacker that can publish on the broker but does not know the device key, and reads the
plant (the truth) for every effect:

  A1  ping: the reply carries a valid device MAC
  A2  an authentic command takes effect (APPLIED, one effect)
  A3  a command without a MAC is refused (REJECTED UNAUTHENTICATED) and has no effect
  A4  a command with a wrong MAC is refused, no effect
  A5  a command authenticated for another device is refused, no effect (the MAC binds the device)
  A6  a replayed authentic command is answered from the dedup ring (REPLAY), no second effect
  A7  a stop without a MAC changes nothing: the command it names still takes effect when it arrives
  A8  an authentic fence with a cutoff far above every sequence seen is refused (CUTOFF_TOO_FAR_AHEAD), cutoff unchanged
  A9  a fence without a MAC is refused, cutoff unchanged
  A10 an authentic fence within the bound is installed; a later command at or below it is sealed STOPPED, no effect
  A11 an authentic stop seals the command it names (STOPPED), no effect when it arrives
  A12 a forged receipt (no valid device MAC) and a genuine reply altered by one byte both fail the reply check
  A13 the plant saw exactly the effects of A2 and A7's authentic command

The device key comes from the environment (EVC_AUTH_KEY, 64 hex characters), never from a file or the
command line.

    EVC_AUTH_KEY=... python3 conformance/reference/zephyr_auth_check.py --broker <host:port> --device zephyr-1 \\
        --ns evicon --plant http://<VM-E>:18090 --out <dir>
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import threading
import time
import urllib.request
import uuid
from pathlib import Path

FAR = 10 ** 9


def canon(kind: str, ns: str, device: str, body: dict) -> bytes:
    return "\n".join([kind, ns, device, str(body.get("cmd", "")), str(int(body.get("seq", 0))), str(int(body.get("value", 0))),
                      str(int(body.get("cutoff", 0))), str(body.get("token", "")), str(int(body.get("expires_at_ms", 0)))]).encode()


def request_mac(key: bytes, kind: str, ns: str, device: str, body: dict) -> str:
    return hmac.new(key, canon(kind, ns, device, body), hashlib.sha256).hexdigest()


def reply_ok(key: bytes, ns: str, device: str, raw: bytes) -> bool:
    """The firmware's reply MAC: HMAC(key, "reply\\n<ns>\\n<device>\\n" + the reply's bytes before ,"mac")."""
    i = raw.rfind(b',"mac":"')
    if i < 0 or not raw.endswith(b'"}') or len(raw) - i != len(b',"mac":"') + 64 + 2:
        return False
    got = raw[i + 8:i + 8 + 64].decode(errors="replace")
    want = hmac.new(key, f"reply\n{ns}\n{device}\n".encode() + raw[:i], hashlib.sha256).hexdigest()
    return hmac.compare_digest(got, want)


class Client:
    def __init__(self, broker: str, ns: str, device: str, key: bytes):
        import paho.mqtt.client as mqtt
        self.ns, self.device, self.key = ns, device, key
        self.tag = uuid.uuid4().hex[:8]
        self.resp = f"{ns}/{device}/resp/authcheck-{self.tag}"
        self.got, self.ev = {}, threading.Event()
        host, _, port = broker.rpartition(":")
        self.c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"authcheck-{self.tag}", protocol=mqtt.MQTTv311)
        ready = threading.Event()
        self.c.on_connect = lambda c, *a, **k: c.subscribe(self.resp + "/#", qos=1)
        self.c.on_subscribe = lambda *a, **k: ready.set()
        self.c.on_message = self._msg
        self.c.connect(host, int(port), keepalive=30)
        self.c.loop_start()
        if not ready.wait(10):
            raise SystemExit(f"no SUBACK from {broker}")

    def _msg(self, c, u, m):
        self.got[m.topic.rsplit("/", 1)[-1]] = m.payload
        self.ev.set()

    def rpc(self, kind: str, body: dict, mac: str = "valid", timeout: float = 5.0):
        """mac: valid | none | bad | otherdev. Returns (reply dict or None, raw bytes, reply MAC valid)."""
        n = uuid.uuid4().hex[:6]
        b = dict(body, reply=f"{self.resp}/{n}")
        if mac == "valid":
            b["mac"] = request_mac(self.key, kind, self.ns, self.device, body)
        elif mac == "bad":
            b["mac"] = hashlib.sha256(os.urandom(16)).hexdigest()
        elif mac == "otherdev":
            b["mac"] = request_mac(self.key, kind, self.ns, self.device + "-other", body)
        self.ev.clear()
        self.c.publish(f"{self.ns}/{self.device}/{kind}", json.dumps(b, separators=(",", ":")), qos=1)
        end = time.time() + timeout
        while time.time() < end and n not in self.got:
            self.ev.wait(0.2)
            self.ev.clear()
        raw = self.got.pop(n, None)
        if raw is None:
            return None, b"", False
        return json.loads(raw), raw, reply_ok(self.key, self.ns, self.device, raw)

    def close(self):
        self.c.loop_stop()
        self.c.disconnect()


def effects(plant: str, cmd: str, device: str) -> int:
    with urllib.request.urlopen(f"{plant}/v1/truth?cmd={cmd}&device={device}", timeout=10) as r:
        return int(json.loads(r.read() or b"{}").get("effects", 0))


def run(a, key: bytes) -> list:
    cl = Client(a.broker, a.ns, a.device, key)
    rows = []
    run_id = uuid.uuid4().hex[:4]
    cid = lambda x: f"auth-{run_id}-{x}"  # noqa: E731

    def row(case, what, ok, detail):
        rows.append({"case": case, "check": what, "result": "PASS" if ok else "FAIL", "detail": detail})

    def eff(x):
        return effects(a.plant, cid(x), a.device)

    try:
        r, raw, rok = cl.rpc("ping", {})
        row("A1", "ping reply carries a valid device MAC", bool(r) and rok, raw.decode(errors="replace")[:160])
        cut0 = int((r or {}).get("cutoff", -1))
        r, raw, rok = cl.rpc("cmd", {"cmd": cid("a2"), "seq": 2, "value": 1})
        row("A2", "authentic command: APPLIED, one effect", bool(r) and r.get("outcome") == "APPLIED" and rok and eff("a2") == 1,
            f"{r} effects={eff('a2')}")
        r, raw, rok = cl.rpc("cmd", {"cmd": cid("a3"), "seq": 3, "value": 1}, mac="none")
        row("A3", "command without MAC: REJECTED, no effect", bool(r) and r.get("reason") == "UNAUTHENTICATED" and rok and eff("a3") == 0,
            f"{r} effects={eff('a3')}")
        r, raw, rok = cl.rpc("cmd", {"cmd": cid("a4"), "seq": 4, "value": 1}, mac="bad")
        row("A4", "command with a wrong MAC: REJECTED, no effect", bool(r) and r.get("reason") == "UNAUTHENTICATED" and eff("a4") == 0,
            f"{r} effects={eff('a4')}")
        r, raw, rok = cl.rpc("cmd", {"cmd": cid("a5"), "seq": 5, "value": 1}, mac="otherdev")
        row("A5", "command authenticated for another device: REJECTED, no effect",
            bool(r) and r.get("reason") == "UNAUTHENTICATED" and eff("a5") == 0, f"{r} effects={eff('a5')}")
        r, raw, rok = cl.rpc("cmd", {"cmd": cid("a2"), "seq": 2, "value": 1})
        row("A6", "replayed authentic command: answered from the ring, no second effect",
            bool(r) and r.get("outcome") == "APPLIED" and r.get("reason") == "REPLAY" and eff("a2") == 1, f"{r} effects={eff('a2')}")
        r, raw, rok = cl.rpc("stop", {"cmd": cid("a7"), "seq": 7}, mac="none")
        r2, _, _ = cl.rpc("cmd", {"cmd": cid("a7"), "seq": 7, "value": 1})
        row("A7", "stop without MAC changes nothing: the command takes effect later",
            bool(r) and r.get("reason") == "UNAUTHENTICATED" and bool(r2) and r2.get("outcome") == "APPLIED" and eff("a7") == 1,
            f"stop {r}; cmd {r2}; effects={eff('a7')}")
        r, raw, rok = cl.rpc("fence", {"cutoff": FAR, "token": f"far-{run_id}"})
        p, _, _ = cl.rpc("ping", {})
        row("A8", "authentic fence far ahead: refused, cutoff unchanged",
            bool(r) and r.get("ok") is False and r.get("reason") == "CUTOFF_TOO_FAR_AHEAD" and rok and int((p or {}).get("cutoff", -2)) == cut0,
            f"{r}; cutoff {cut0} -> {(p or {}).get('cutoff')}")
        r, raw, rok = cl.rpc("fence", {"cutoff": 9, "token": f"forged-{run_id}"}, mac="none")
        p, _, _ = cl.rpc("ping", {})
        row("A9", "fence without MAC: REJECTED, cutoff unchanged",
            bool(r) and r.get("reason") == "UNAUTHENTICATED" and int((p or {}).get("cutoff", -2)) == cut0, f"{r}; cutoff {(p or {}).get('cutoff')}")
        r, raw, rok = cl.rpc("fence", {"cutoff": 9, "token": f"ok-{run_id}"})
        r2, _, _ = cl.rpc("cmd", {"cmd": cid("a10"), "seq": 9, "value": 1})
        row("A10", "authentic fence within the bound installed; a later command below it sealed, no effect",
            bool(r) and r.get("ok") is True and rok and bool(r2) and r2.get("outcome") == "STOPPED" and eff("a10") == 0,
            f"fence {r}; cmd {r2}; effects={eff('a10')}")
        r, raw, rok = cl.rpc("stop", {"cmd": cid("a11"), "seq": 11})
        r2, _, _ = cl.rpc("cmd", {"cmd": cid("a11"), "seq": 11, "value": 1})
        row("A11", "authentic stop seals the command it names, no effect",
            bool(r) and r.get("outcome") == "STOPPED" and rok and bool(r2) and r2.get("outcome") == "STOPPED" and eff("a11") == 0,
            f"stop {r}; cmd {r2}; effects={eff('a11')}")
        forged = json.dumps({"device": a.device, "cmd": cid("a2"), "seq": 2, "outcome": "STOPPED", "reason": "SEALED_BY_FENCE",
                             "level": 0, "owner_version": 99, "mac": "0" * 64}, separators=(",", ":")).encode()
        _, good, _ = cl.rpc("query", {"cmd": cid("a2"), "seq": 2})
        altered = good.replace(b'"APPLIED"', b'"STOPPED"', 1) if good else b""
        row("A12", "forged and altered receipts fail the reply check",
            not reply_ok(key, a.ns, a.device, forged) and bool(good) and reply_ok(key, a.ns, a.device, good)
            and altered != good and not reply_ok(key, a.ns, a.device, altered),
            f"genuine query reply verifies: {reply_ok(key, a.ns, a.device, good)}")
        total = {x: eff(x) for x in ("a2", "a3", "a4", "a5", "a7", "a10", "a11")}
        row("A13", "the plant saw only the authentic, unsealed commands (a2, a7)",
            total == {"a2": 1, "a3": 0, "a4": 0, "a5": 0, "a7": 1, "a10": 0, "a11": 0}, json.dumps(total))
    finally:
        cl.close()
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--broker", required=True)
    ap.add_argument("--device", default="zephyr-1")
    ap.add_argument("--ns", default="evicon")
    ap.add_argument("--plant", required=True, help="evicon-plant truth API, e.g. http://127.0.0.1:18090")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    try:
        key = bytes.fromhex(os.environ.get("EVC_AUTH_KEY", ""))
    except ValueError:
        key = b""
    if len(key) != 32:
        raise SystemExit("EVC_AUTH_KEY: the device key, 64 hex characters, in the environment")
    rows = run(a, key)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    ok = all(r["result"] == "PASS" for r in rows)
    (out / "auth.json").write_text(json.dumps({"device": a.device, "ns": a.ns, "broker": a.broker, "verdict": "PASS" if ok else "FAIL",
                                               "cases": rows}, indent=2) + "\n")
    md = ["# Authenticated firmware: what it accepts and certifies (release 077)", "",
          f"Device `{a.device}`, topic prefix `{a.ns}`, broker `{a.broker}`. Verdict: **{'PASS' if ok else 'FAIL'}**.", "",
          "| case | check | result | detail |", "|---|---|---|---|"]
    md += [f"| {r['case']} | {r['check']} | {r['result']} | {r['detail'][:200].replace('|', '/')} |" for r in rows]
    (out / "auth.md").write_text("\n".join(md) + "\n")
    print(f"zephyr_auth_check: {sum(r['result'] == 'PASS' for r in rows)}/{len(rows)} PASS -> {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
