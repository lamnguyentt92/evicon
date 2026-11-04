#!/usr/bin/env python3
"""ThingsBoard RPC checks (release 085; reviewer of 4 November M3a, M3b; PREREG_extra_085.md TB-1 to TB-4).

Which event sets the status the probe adapter maps to APPLIED (`SUCCESSFUL`, evicon_probe/adapters/thingsboard.py
TB_STATUS) for persistent one-way and two-way server-side RPCs on the deployed ThingsBoard, tested with a fault cut
between the platform's hand-over and the device's effect, and what the ThingsBoard owner gateway sends as the RPC's
expirationTime (the delayed hand-off). Run on VM-C by `evicon_review.py tbcheck` against ThingsBoard (VM-B), the
reference device reference/mqtt_device_tbcheck.py (VM-E: test-only switches hold and ackmode), the plant (VM-E, the
truth) and one reference/tb_owner_gateway.py (VM-C). Credentials only from the environment: TB_USERNAME, TB_PASSWORD
(the tenant user; the device's token TB_DEVICE_TOKEN is the device's own).

Call shapes: the measured ones (conformance/configs/thingsboard-rpc-oneway.json and thingsboard-rpc-r0.json):
persistent, one-way retries 2 and two-way retries 0, timeout 5000 ms, expirationTime = send + 60 s; in CUT and ACK the
RPC timeout is 30000 ms (longer than the hold, so that the RPC is still pending when the handler is released).

  SEQ (n per direction, 20): send; poll the RPC record every <= 20 ms until a final status (SUCCESSFUL, TIMEOUT,
       EXPIRED, FAILED, DELETED) or 30 s; every status change with its time; the plant's effect time; the applied query
       (the adapter's mapping, by rpcId and by listing) after the final status.
  CUT (10): device hold on (ackmode after); send; wait until the platform shows the RPC handed over (status SENT or
       later) or 5 s; for 10 s poll the status, the applied query and the plant: no SUCCESSFUL, no APPLIED, no effect
       while held; release; the sequence until final.
  ACK (10, negative control): device ackmode before and hold on; as CUT.
  TTL (5 per kind): requests to the gateway (POST /v1/effects) with expires_at_ms already past (created 70 s ago,
       expiry 60 s), within the expiry (created 50 s ago) and without expires_at_ms: effect, platform status and the
       expirationTime the gateway sent (the RPC record's).

Times are VM-C wall-clock milliseconds after the trial's send; the plant's effect time (VM-E clock) is converted with
the clock offset VM-E - VM-C that the check measures itself before the trials, over HTTP to the check device on VM-E
(`/_test/clock`, the host of the plant): of 20 round trips the shortest, offset = device time - midpoint, uncertainty =
half that round trip (a bound on the error whatever the asymmetry); again after the trials (drift). The driver's
ssh-based offset (config clock_offset_ms) is only recorded, and used only if the device cannot answer: it is biased by
about half the ssh round trip (the remote `date` runs late in the session). An APPLIED status counts as after the
effect only if it was first seen later than the converted effect time plus the uncertainty. Every RPC the
checks create is deleted at the end (and any left over by an earlier interrupted run, by its command prefix); the
device is left in its default modes. Writes <out>/TBCHECK.md and <out>/tbcheck.json (per trial: sequence, times,
effect, verdict; the rules TB-1 to TB-4).

    TB_USERNAME=... TB_PASSWORD=... python3 conformance/tb_rpc_checks.py --config tbcheck_config.json --out <dir>
Config (JSON, no secrets): tb_url, device_uuid, device_admin_url, plant_url, plant_device_id, gateway_url,
gateway_device_id, n_seq, n_cut, n_ack, n_ttl, clock_offset_ms, clock_uncertainty_ms, platform ("testbed" or
"stand-in": only a testbed run on the real platform counts for the plan).
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # conformance/: evicon_probe
from evicon_probe.adapters.thingsboard import TB_STATUS  # noqa: E402  (the adapter's mapping; frozen, imported)

FINAL = ("SUCCESSFUL", "TIMEOUT", "EXPIRED", "FAILED", "DELETED")
APPLIED = "APPLIED"
PREFIX = "tbc085-"
SHAPES = {"oneway": {"retries": 2, "timeout": 5000, "expiration_s": 60, "config": "thingsboard-rpc-oneway"},
          "twoway": {"retries": 0, "timeout": 5000, "expiration_s": 60, "config": "thingsboard-rpc-r0"}}
CUT_TIMEOUT_MS = 30000
POLL_S = 0.02
FINAL_WAIT_S = 30.0
HANDOVER_WAIT_S = 5.0
HOLD_WINDOW_S = 10.0
CLOCK_SAMPLES = 20
SETTLE_S = 2.0
TTL_EXPIRY_S = 60.0
MAX_INVALID = 0.10


def _http(method, url, body=None, headers=None, timeout=5.0):
    data = None if body is None else json.dumps(body).encode()
    h = {"Content-Type": "application/json", "Connection": "close"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            try:
                return r.status, (json.loads(raw) if raw else None)
            except ValueError:
                return r.status, raw.decode(errors="replace")[:200]
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, (json.loads(raw) if raw else None)
        except ValueError:
            return e.code, raw.decode(errors="replace")[:200]
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as e:
        return None, str(e)[:200]


class TB:
    """ThingsBoard REST as the tenant user (JWT, logged in again on 401)."""

    def __init__(self, url, user, password):
        self.url, self.user, self.password, self.jwt = url.rstrip("/"), user, password, ""

    def login(self):
        st, p = _http("POST", self.url + "/api/auth/login", {"username": self.user, "password": self.password}, timeout=10)
        if st != 200 or not isinstance(p, dict) or not p.get("token"):
            raise RuntimeError(f"ThingsBoard login failed: HTTP {st}")
        self.jwt = p["token"]

    def call(self, method, path, body=None, timeout=5.0):
        st, p = _http(method, self.url + path, body, {"X-Authorization": "Bearer " + self.jwt}, timeout)
        if st == 401:
            self.login()
            st, p = _http(method, self.url + path, body, {"X-Authorization": "Bearer " + self.jwt}, timeout)
        return st, p

    def rpcs(self, device):
        out = []
        for page in range(50):
            st, p = self.call("GET", f"/api/rpc/persistent/device/{device}?pageSize=100&page={page}&sortProperty=createdTime&sortOrder=DESC")
            if st != 200 or not isinstance(p, dict):
                break
            out += p.get("data", [])
            if not p.get("hasNext"):
                break
        return out


def params_of(rec: dict) -> dict:
    body = (rec.get("request") or {}).get("body") or {}
    params = body.get("params") if isinstance(body, dict) else None
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except ValueError:
            params = None
    return params if isinstance(params, dict) else {}


class Rig:
    def __init__(self, cfg: dict, tb: TB):
        self.cfg, self.tb = cfg, tb
        self.device = cfg["device_uuid"]
        self.admin = cfg["device_admin_url"].rstrip("/")
        self.plant = cfg["plant_url"].rstrip("/")
        self.plant_dev = cfg.get("plant_device_id", "tb-1")
        self.gw = (cfg.get("gateway_url") or "").rstrip("/")
        self.off_ms = float(cfg.get("clock_offset_ms") or 0.0)  # replaced by clock() in main when the device answers
        self.u_ms = float(cfg.get("clock_uncertainty_ms") or 0.0)
        self.created = []  # rpcIds this run created
        self.seq = int(time.time())  # gateway request sequence numbers (> 0, increasing)

    # ---- the device's switches and log --------------------------------------------
    def dev(self, path, v):
        st, p = _http("POST", f"{self.admin}{path}?v={v}", timeout=5)
        if st != 200:
            raise RuntimeError(f"device admin {path}?v={v}: HTTP {st} {p}")
        return p

    def dev_state(self):
        st, p = _http("GET", f"{self.admin}/_test/state", timeout=5)
        return p if st == 200 and isinstance(p, dict) else {}

    def dev_events(self, cmd):
        st, p = _http("GET", f"{self.admin}/_test/events?{urllib.parse.urlencode({'cmd': cmd})}", timeout=5)
        return (p or {}).get("events", []) if st == 200 and isinstance(p, dict) else []

    def clock(self, n=CLOCK_SAMPLES):
        """The clock offset VM-E - VM-C (ms) from n HTTP round trips to the check device's /_test/clock: the sample with
        the shortest round trip; offset = device time - midpoint; uncertainty = half that round trip. None if no answer."""
        best = None
        for _ in range(n):
            t0 = time.time_ns()
            st, p = _http("GET", f"{self.admin}/_test/clock", timeout=5)
            t1 = time.time_ns()
            if st != 200 or not isinstance(p, dict) or "t_ns" not in p:
                continue
            if best is None or t1 - t0 < best[0]:
                best = (t1 - t0, int(p["t_ns"]) - (t0 + t1) / 2)
        if best is None:
            return None
        return {"offset_ms": round(best[1] / 1e6, 3), "uncertainty_ms": round(best[0] / 2e6, 3), "samples": n,
                "method": "HTTP round trips to the check device (/_test/clock), shortest of n"}

    def defaults(self):
        self.dev("/_test/hold", "false")
        self.dev("/_test/ackmode", "after")

    # ---- truth ----------------------------------------------------------------------
    def truth(self, cmd):
        st, p = _http("GET", f"{self.plant}/v1/truth?{urllib.parse.urlencode({'cmd': cmd, 'device': self.plant_dev})}", timeout=5)
        if st != 200 or not isinstance(p, dict):
            return None, None
        return int(p.get("effects", 0)), (int(p["first_effect_ns"]) if p.get("first_effect_ns") else None)

    # ---- one RPC ----------------------------------------------------------------------
    def send(self, direction, cmd, timeout_ms):
        shape = SHAPES[direction]
        now = time.time()
        body = {"method": "actuate", "params": {"cmd": cmd, "value": 1}, "timeout": timeout_ms, "persistent": True,
                "retries": shape["retries"], "expirationTime": int((now + shape["expiration_s"]) * 1000)}
        t0 = time.time()
        st, p = self.tb.call("POST", f"/api/rpc/{direction}/{self.device}", body, timeout=10)
        t1 = time.time()
        rid = p.get("rpcId") if st == 200 and isinstance(p, dict) else None
        if rid:
            self.created.append(rid)
        return rid, t0, t1, st, body

    def query(self, cmd, rid):
        """The applied query, as the adapter answers it: by rpcId, and by listing the device's RPCs (params.cmd)."""
        out = {}
        st, rec = self.tb.call("GET", f"/api/rpc/persistent/{rid}") if rid else (None, None)
        out["by_rpc_id"] = (TB_STATUS.get(str(rec.get("status")), "UNKNOWN") if st == 200 and isinstance(rec, dict)
                            else ("NOT_FOUND" if st == 404 else f"HTTP {st}"))
        found = [r for r in self.tb.rpcs(self.device) if str(params_of(r).get("cmd")) == cmd]
        out["by_listing"] = TB_STATUS.get(str(found[0].get("status")), "UNKNOWN") if found else "NOT_FOUND"
        return out

    def watch(self, rid, cmd, t_send, until, stop=lambda status: status in FINAL):
        """Polls the RPC record (every <= POLL_S) and the plant until `until` (VM-C time) or stop(status). Returns the
        status sequence [{status, seen_ms, prev_poll_ms}] (ms after t_send: the poll that first returned the status
        ended at seen_ms, the poll before it started at prev_poll_ms), the effect as the plant reports it (its own
        clock, converted), the effect as first observed from VM-C, the number of polls and the largest gap between
        status polls."""
        seq, last, prev_start = [], None, None
        eff_seen_ms, eff_prev_ms, effects, first_ns, eff_poll_start = None, None, 0, None, None
        polls, max_gap, prev_t, rec = 0, 0.0, None, None
        while True:
            t_a = time.time()
            st, rec2 = self.tb.call("GET", f"/api/rpc/persistent/{rid}", timeout=5)
            t_b = time.time()
            polls += 1
            if prev_t is not None:
                max_gap = max(max_gap, t_b - prev_t)
            prev_t = t_b
            status = str(rec2.get("status")) if st == 200 and isinstance(rec2, dict) else (f"HTTP_{st}" if st else "UNREACHABLE")
            if st == 200 and isinstance(rec2, dict):
                rec = rec2
            if status != last:
                seq.append({"status": status, "seen_ms": round((t_b - t_send) * 1000, 1),
                            "prev_poll_ms": round((prev_start - t_send) * 1000, 1) if prev_start is not None else None})
                last = status
            prev_start = t_a
            if eff_seen_ms is None:
                p_a = time.time()
                n, ns = self.truth(cmd)
                p_b = time.time()
                if n:
                    effects, first_ns = n, ns
                    eff_seen_ms = round((p_b - t_send) * 1000, 1)
                    eff_prev_ms = round((eff_poll_start - t_send) * 1000, 1) if eff_poll_start is not None else None
                eff_poll_start = p_a
            if stop(status) or time.time() >= until:
                break
            time.sleep(max(0.0, POLL_S - (time.time() - t_a)))
        eff_ms = round(first_ns / 1e6 - self.off_ms - t_send * 1000, 1) if first_ns else None
        return {"sequence": seq, "final": last, "record": rec, "effect_plant_ms": eff_ms, "effect_seen_ms": eff_seen_ms,
                "effect_prev_poll_ms": eff_prev_ms, "effects": effects, "polls": polls, "max_poll_gap_ms": round(max_gap * 1000, 1)}

    def settle_effects(self, cmd):
        time.sleep(SETTLE_S)
        n, ns = self.truth(cmd)
        return n, ns

    # ---- the checks ---------------------------------------------------------------------
    def seq_trial(self, direction, i):
        cmd = f"{PREFIX}seq-{direction}-{i}-{uuid.uuid4().hex[:8]}"
        rid, t0, t1, st, body = self.send(direction, cmd, SHAPES[direction]["timeout"])
        tr = {"check": "SEQ", "direction": direction, "trial": i, "cmd": cmd, "rpc_id": rid, "post_http": st,
              "post_ms": round((t1 - t0) * 1000, 1), "rpc_timeout_ms": body["timeout"], "retries": body["retries"]}
        if not rid:
            return dict(tr, valid=False, why=f"POST HTTP {st}")
        w = self.watch(rid, cmd, t0, t0 + FINAL_WAIT_S)
        n, ns = self.settle_effects(cmd)
        q = self.query(cmd, rid)
        seen = next((x["seen_ms"] for x in w["sequence"] if TB_STATUS.get(x["status"]) == APPLIED), None)
        tr.update(sequence=w["sequence"], final=w["final"], effect_plant_ms=w["effect_plant_ms"], effect_seen_ms=w["effect_seen_ms"],
                  effects=n, polls=w["polls"], max_poll_gap_ms=w["max_poll_gap_ms"], query_after_final=q, applied_seen_ms=seen,
                  device=self.dev_events(cmd), valid=w["final"] in FINAL or w["final"] in ("DELIVERED", "SENT", "QUEUED"))
        tr["applied_after_effect"] = (seen is not None and n == 1 and w["effect_plant_ms"] is not None
                                      and seen > w["effect_plant_ms"] + self.u_ms)
        tr["margin_ms"] = round(seen - w["effect_plant_ms"], 1) if seen is not None and w["effect_plant_ms"] is not None else None
        tr["verdict"] = ("APPLIED after the effect" if tr["applied_after_effect"] else
                         "no APPLIED" if seen is None else "APPLIED not after the effect")
        return tr

    def held_trial(self, check, direction, i):
        """CUT (ackmode after) or ACK (ackmode before, the negative control): the device holds the next handler."""
        cmd = f"{PREFIX}{check.lower()}-{direction}-{i}-{uuid.uuid4().hex[:8]}"
        self.defaults()
        if check == "ACK":
            self.dev("/_test/ackmode", "before")
        self.dev("/_test/hold", "true")
        rid, t0, t1, st, body = self.send(direction, cmd, CUT_TIMEOUT_MS)
        tr = {"check": check, "direction": direction, "trial": i, "cmd": cmd, "rpc_id": rid, "post_http": st,
              "ackmode": "before" if check == "ACK" else "after", "rpc_timeout_ms": body["timeout"], "retries": body["retries"]}
        try:
            if not rid:
                return dict(tr, valid=False, why=f"POST HTTP {st}")
            # the hand-over: the platform shows SENT or a later status (or 5 s pass)
            hand = self.watch(rid, cmd, t0, t0 + HANDOVER_WAIT_S, stop=lambda s: s not in ("QUEUED",) and not s.startswith(("HTTP_", "UNREACH")))
            handed = [x for x in hand["sequence"] if x["status"] not in ("QUEUED",) and not x["status"].startswith(("HTTP_", "UNREACH"))]
            t_hand = handed[0]["seen_ms"] if handed else None
            t_w0 = time.time()
            win = self.watch(rid, cmd, t0, t_w0 + HOLD_WINDOW_S, stop=lambda s: False)
            ds2 = self.dev_state()
            ev = self.dev_events(cmd)
            held = any(e.get("ev") == "hold_start" for e in ev)
            still_held = ds2.get("held_cmd") == cmd
            during = hand["sequence"] + win["sequence"]
            q_held = self.query(cmd, rid)
            applied_held = any(TB_STATUS.get(x["status"]) == APPLIED for x in during) or APPLIED in q_held.values()
            eff_held = bool(win["effects"] or hand["effects"])
            t_rel = time.time()
            self.dev("/_test/hold", "false")
            after = self.watch(rid, cmd, t0, t_rel + FINAL_WAIT_S)
            n, ns = self.settle_effects(cmd)
            q = self.query(cmd, rid)
            tr.update(handed_over_ms=t_hand, device_held=held, held_through_window=still_held,
                      statuses_while_held=sorted({x["status"] for x in during}), sequence_while_held=during,
                      query_while_held=q_held, applied_while_held=applied_held, effect_while_held=eff_held,
                      delivered_while_held=any(x["status"] == "DELIVERED" for x in during),
                      released_ms=round((t_rel - t0) * 1000, 1), sequence_after_release=after["sequence"], final=after["final"],
                      effect_plant_ms=after["effect_plant_ms"], effects=n, query_after_final=q, device=self.dev_events(cmd),
                      exercised=bool(t_hand is not None and held and still_held), valid=True)
            tr["verdict"] = ("not exercised (not handed over or not held)" if not tr["exercised"] else
                             "APPLIED while held" if applied_held else "effect while held" if eff_held else "clean while held")
            return tr
        finally:
            self.defaults()

    def ttl_trial(self, kind, i):
        cmd = f"{PREFIX}ttl-{kind}-{i}-{uuid.uuid4().hex[:8]}"
        self.seq += 1
        now = time.time()
        body = {"schema_version": 1, "domain_id": self.cfg["gateway_device_id"], "generation": 1, "command_id": cmd, "operation": "ADD",
                "value": [1], "sequence": self.seq}
        created_ago = {"past": 70.0, "within": 50.0}.get(kind)
        if created_ago is not None:
            body["expires_at_ms"] = int((now - created_ago + TTL_EXPIRY_S) * 1000)
        t0 = time.time()
        st, p = _http("POST", f"{self.gw}/v1/effects", body, timeout=10)
        t1 = time.time()
        tr = {"check": "TTL", "kind": kind, "trial": i, "cmd": cmd, "expires_at_ms": body.get("expires_at_ms"), "gateway_http": st,
              "gateway_outcome": (p or {}).get("outcome") or (p or {}).get("error") if isinstance(p, dict) else str(p)[:80],
              "request_wall_ms": [int(t0 * 1000), int(t1 * 1000) + 1]}
        rec = None
        for _ in range(50):
            found = [r for r in self.tb.rpcs(self.device) if str(params_of(r).get("cmd")) == cmd]
            if found:
                rec = found[0]
                break
            time.sleep(0.1)
        if rec is None:
            n, _ = self.settle_effects(cmd)
            return dict(tr, valid=st is not None, rpc_found=False, effects=n, verdict="no RPC created")
        rid = rec["id"]["id"]
        self.created.append(rid)
        w = self.watch(rid, cmd, t0, t0 + FINAL_WAIT_S)
        n, _ = self.settle_effects(cmd)
        rec = w["record"] or rec
        exp_sent = rec.get("expirationTime")
        if created_ago is not None:
            exp_ok = exp_sent == body["expires_at_ms"]
            want = body["expires_at_ms"]
        else:
            lo, hi = tr["request_wall_ms"][0] + int(TTL_EXPIRY_S * 1000) - 1, tr["request_wall_ms"][1] + int(TTL_EXPIRY_S * 1000) + 1
            exp_ok = isinstance(exp_sent, int) and lo <= exp_sent <= hi
            want = [lo, hi]
        eff_ok = (n == 0) if kind == "past" else (n == 1)
        tr.update(rpc_found=True, rpc_id=rid, expiration_time_sent=exp_sent, expiration_expected=want, expiration_ok=exp_ok,
                  created_time=rec.get("createdTime"), sequence=w["sequence"], final=w["final"], effects=n, effect_ok=eff_ok,
                  valid=True, verdict="as the rule expects" if (exp_ok and eff_ok) else "differs from the rule")
        return tr

    def cleanup(self, all_prefix=True):
        """Deletes every RPC this run created and every RPC of the device whose params.cmd has the check prefix."""
        ids = set(self.created)
        if all_prefix:
            ids |= {r["id"]["id"] for r in self.tb.rpcs(self.device) if str(params_of(r).get("cmd", "")).startswith(PREFIX)}
        bad = []
        for rid in sorted(ids):
            st, _ = self.tb.call("DELETE", f"/api/rpc/persistent/{rid}")
            if st not in (200, 204, 404):
                bad.append(f"{rid}: HTTP {st}")
        left = [r["id"]["id"] for r in self.tb.rpcs(self.device) if str(params_of(r).get("cmd", "")).startswith(PREFIX)]
        return {"deleted": len(ids) - len(bad), "failed": bad, "left": left}


# ---- rules (PREREG_extra_085.md section 3: TB-1 to TB-4, fixed in advance) ------------------------------------------
def _valid(trials):
    return [t for t in trials if t.get("valid")]


def interpretable(trials, planned) -> bool:
    return bool(planned) and len(trials) >= planned and len(trials) - len(_valid(trials)) <= MAX_INVALID * planned


def rules(trials: list, planned: dict) -> dict:
    out = {}
    one = [t for t in trials if t["check"] == "SEQ" and t["direction"] == "oneway"]
    v = _valid(one)
    if not interpretable(one, planned.get("seq", 0)):
        out["TB-1"] = {"verdict": "NOT INTERPRETABLE", "evidence": f"{len(v)} valid of {len(one)} trials ({planned.get('seq')} planned)"}
    else:
        seen = [t for t in v if t.get("applied_seen_ms") is not None]
        after = [t for t in v if t.get("applied_after_effect")]
        if not seen:
            verdict = "NOT REACHED"
        elif len(after) == len(v):
            verdict = "MET"
        else:
            verdict = "NOT MET"
        m = [t["margin_ms"] for t in v if t.get("margin_ms") is not None]
        out["TB-1"] = {"verdict": verdict, "evidence": f"{len(after)} of {len(v)} one-way trials: SUCCESSFUL first seen after the plant effect; "
                       f"{len(seen)} reached SUCCESSFUL; margin min {min(m) if m else '-'} ms",
                       "finals": sorted({str(t.get('final')) for t in v})}
    cut = [t for t in trials if t["check"] == "CUT"]
    ex = [t for t in _valid(cut) if t.get("exercised")]
    if not interpretable(cut, 2 * planned.get("cut", 0)) or len(ex) < (1 - MAX_INVALID) * 2 * planned.get("cut", 0):
        out["TB-2"] = {"verdict": "NOT INTERPRETABLE", "evidence": f"{len(ex)} exercised of {len(cut)} trials"}
    else:
        bad = [t for t in ex if t.get("applied_while_held") or t.get("effect_while_held")]
        per = {d: f"{sum(1 for t in ex if t['direction'] == d and (t.get('applied_while_held') or t.get('effect_while_held')))} of "
                  f"{sum(1 for t in ex if t['direction'] == d)}" for d in ("oneway", "twoway")}
        out["TB-2"] = {"verdict": "DETECTED" if bad else "ABSENT",
                       "evidence": f"APPLIED or effect while held in {len(bad)} of {len(ex)} exercised trials (one-way {per['oneway']}, two-way {per['twoway']})"}
    for d, rid in (("oneway", "TB-3a"), ("twoway", "TB-3b")):
        ack = [t for t in trials if t["check"] == "ACK" and t["direction"] == d]
        ex = [t for t in _valid(ack) if t.get("exercised")]
        if not interpretable(ack, planned.get("ack", 0)) or len(ex) < (1 - MAX_INVALID) * planned.get("ack", 0):
            out[rid] = {"verdict": "NOT INTERPRETABLE", "evidence": f"{len(ex)} exercised of {len(ack)} trials"}
            continue
        det = [t for t in ex if t.get("applied_while_held")]
        dl = sum(1 for t in ex if t.get("delivered_while_held"))
        out[rid] = {"verdict": "DETECTED" if det else "ABSENT",
                    "evidence": f"{d}: SUCCESSFUL/APPLIED before the effect (device acknowledged before the plant write, handler held) in "
                                f"{len(det)} of {len(ex)} exercised trials; DELIVERED while held in {dl}"}
    ttl = [t for t in trials if t["check"] == "TTL"]
    if not interpretable(ttl, 3 * planned.get("ttl", 0)):
        out["TB-4"] = {"verdict": "NOT INTERPRETABLE", "evidence": f"{len(_valid(ttl))} valid of {len(ttl)} trials"}
    else:
        v = _valid(ttl)
        ok = [t for t in v if t.get("expiration_ok") and t.get("effect_ok")]
        per = {k: f"{sum(1 for t in v if t['kind'] == k and t.get('expiration_ok') and t.get('effect_ok'))} of {sum(1 for t in v if t['kind'] == k)}"
               for k in ("past", "within", "none")}
        out["TB-4"] = {"verdict": "MET" if len(ok) == len(v) else "NOT MET",
                       "evidence": f"as the rule expects: past-expiry {per['past']} (no effect, expirationTime = expires_at_ms), within {per['within']} "
                                   f"(one effect, expirationTime = expires_at_ms), without {per['none']} (one effect, expirationTime = gateway receipt + 60 s)"}
    return out


def clock_text(res: dict) -> str:
    c = res.get("clock") or {}
    off, u = res.get("clock_offset_ms"), res.get("clock_uncertainty_ms")
    if c.get("used") == "http":
        return (f"clock offset VM-E - VM-C {off} ms (+- {u} ms; HTTP round trips to the check device before the trials; after "
                f"them {(c.get('after') or {}).get('offset_ms', '-')} ms; the driver's ssh estimate {c.get('driver_ssh_offset_ms')} ms "
                "is not used).")
    return f"clock offset VM-E - VM-C {off} ms (+- {u} ms; {c.get('used', 'the driver')})."


def markdown(res: dict) -> str:
    L = ["# ThingsBoard RPC checks (release 085, PREREG_extra_085.md TB-1 to TB-4)", "",
         f"Platform: `{json.dumps(res.get('system_info'))}` ({res.get('platform')}); device `{res.get('device_uuid')}`; "
         f"started {res.get('started')}, ended {res.get('ended')}; " + clock_text(res)]
    if res.get("platform") != "testbed":
        L += ["", "**Not a result**: this run is against the ThingsBoard stand-in of the simulated testbed; only a run on the testbed's "
              "deployed ThingsBoard counts for the plan."]
    L += ["", "Call shapes: " + "; ".join(f"{d}: persistent, retries {s['retries']}, timeout {s['timeout']} ms (CUT/ACK {CUT_TIMEOUT_MS} ms), "
                                        f"expirationTime = send + {s['expiration_s']} s ({s['config']})" for d, s in SHAPES.items()),
          "", "## Rules", "", "| rule | verdict | evidence |", "|---|---|---|"]
    for k, v in sorted(res.get("rules", {}).items()):
        L.append(f"| {k} | **{v['verdict']}** | {v['evidence']} |")
    L += ["", "## Trials", "", "| check | direction/kind | trial | final | sequence (status @ ms after send) | effect (plant, ms) | "
          "APPLIED seen (ms) | while held | verdict |", "|---|---|---|---|---|---|---|---|---|"]
    for t in res.get("trials", []):
        seq = t.get("sequence") or (t.get("sequence_while_held", []) + t.get("sequence_after_release", []))
        s = ", ".join(f"{x['status']}@{x['seen_ms']:g}" for x in seq)[:300]
        held = (f"{'/'.join(t.get('statuses_while_held', []))}; effect {t.get('effect_while_held')}" if t["check"] in ("CUT", "ACK") else "")
        L.append(f"| {t['check']} | {t.get('direction', t.get('kind'))} | {t['trial']} | {t.get('final')} | {s} | {t.get('effect_plant_ms')} | "
                 f"{t.get('applied_seen_ms', '')} | {held} | {t.get('verdict', t.get('why', ''))} |")
    c = res.get("cleanup") or {}
    L += ["", f"Clean-up: {c.get('deleted')} RPC(s) deleted, failed {c.get('failed')}, left {c.get('left')}; device modes at the end: "
          f"`{json.dumps(res.get('device_final_state'))}`.", ""]
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    cfg = json.loads(Path(a.config).read_text())
    user, password = os.environ.get("TB_USERNAME", ""), os.environ.get("TB_PASSWORD", "")
    if not user or not password:
        raise SystemExit("TB_USERNAME and TB_PASSWORD must be set in the environment")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    tb = TB(cfg["tb_url"], user, password)
    tb.login()
    rig = Rig(cfg, tb)
    clock0 = rig.clock()
    if clock0:
        rig.off_ms, rig.u_ms = clock0["offset_ms"], clock0["uncertainty_ms"]
    planned = {"seq": int(cfg.get("n_seq", 20)), "cut": int(cfg.get("n_cut", 10)), "ack": int(cfg.get("n_ack", 10)), "ttl": int(cfg.get("n_ttl", 5))}
    st, info = tb.call("GET", "/api/system/info")
    res = {"schema_version": 1, "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "platform": cfg.get("platform", "testbed"),
           "system_info": info if st == 200 else f"HTTP {st}", "device_uuid": rig.device, "planned": planned, "shapes": SHAPES,
           "cut_timeout_ms": CUT_TIMEOUT_MS, "poll_s": POLL_S, "hold_window_s": HOLD_WINDOW_S, "handover_wait_s": HANDOVER_WAIT_S,
           "final_wait_s": FINAL_WAIT_S, "clock_offset_ms": rig.off_ms, "clock_uncertainty_ms": rig.u_ms,
           "clock": {"before": clock0, "driver_ssh_offset_ms": cfg.get("clock_offset_ms"),
                     "used": "http" if clock0 else "driver_ssh (the device did not answer /_test/clock)"},
           "gateway_device_id": cfg.get("gateway_device_id"), "trials": []}
    trials = res["trials"]
    try:
        rig.cleanup()  # RPCs an interrupted earlier run left (by the prefix)
        rig.defaults()
        for d in ("oneway", "twoway"):
            for i in range(planned["seq"]):
                trials.append(rig.seq_trial(d, i))
                print(f"SEQ {d} {i}: {trials[-1].get('final')} {trials[-1].get('verdict', trials[-1].get('why'))}", flush=True)
        for check, n in (("CUT", planned["cut"]), ("ACK", planned["ack"])):
            for d in ("oneway", "twoway"):
                for i in range(n):
                    trials.append(rig.held_trial(check, d, i))
                    print(f"{check} {d} {i}: {trials[-1].get('final')} {trials[-1].get('verdict', trials[-1].get('why'))}", flush=True)
        if rig.gw:
            for k in ("past", "within", "none"):
                for i in range(planned["ttl"]):
                    trials.append(rig.ttl_trial(k, i))
                    print(f"TTL {k} {i}: {trials[-1].get('final')} {trials[-1].get('verdict')}", flush=True)
    finally:
        try:
            rig.defaults()
        except Exception as e:  # noqa: BLE001
            res["defaults_error"] = str(e)
        res["device_final_state"] = rig.dev_state()
        res["clock"]["after"] = rig.clock()
        if clock0 and res["clock"]["after"]:
            res["clock"]["drift_ms"] = round(res["clock"]["after"]["offset_ms"] - clock0["offset_ms"], 3)
        try:
            res["cleanup"] = rig.cleanup()
        except Exception as e:  # noqa: BLE001
            res["cleanup"] = {"error": str(e)}
        res["ended"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        res["rules"] = rules(trials, planned)
        (out / "tbcheck.json").write_text(json.dumps(res, indent=1, default=str) + "\n")
        (out / "TBCHECK.md").write_text(markdown(res))
    print("tbcheck: " + "; ".join(f"{k} {v['verdict']}" for k, v in sorted(res["rules"].items())) + f" -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
