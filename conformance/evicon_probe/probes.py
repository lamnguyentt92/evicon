"""Fault-cut probes P1-P9 (plan v2.1, section 5.3), generic over adapters.

Every probe uses a fresh identity, applies one fault cut on the data path,
observes the client, then reads the truth channel and (when the system offers
them) query / stop / fence on the control path. Probes never infer truth from
what the client was told.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from .adapters.base import Adapter
from .faults import DOWN, UP, FaultController, NoFaults
from .model import APPLIED, OK, STOPPED, ProbeResult, Unsupported


@dataclass
class ProbeParams:
    settle_s: float = 1.0            # wait for in-flight work before reading truth
    late_factor: float = 2.5         # P3/P9 request delay = late_factor * client timeout
    within_gap_s: float = 1.0        # P4 retry gap (must be < documented horizon)
    beyond_gap_s: float = 0.0        # P5 retry gap (0 = horizon + margin, needs a finite hint)
    beyond_margin_s: float = 5.0
    max_beyond_gap_s: float = 900.0  # never wait longer than this in P5
    concurrency: int = 2             # P6
    offline_s: float = 10.0          # P7
    p8_abandon_s: float = 0.05       # P8: the crashing client waits this long
    pressure_n: int = 0              # P2N/P5N/P9N: intervening identities (0 = skip)
    bisect_resolution_s: float = 5.0  # P5B: stop when the horizon bracket is this narrow
    bisect_max_steps: int = 5         # P5B: at most this many retry gaps

    @staticmethod
    def from_dict(d: dict) -> "ProbeParams":
        p = ProbeParams()
        for k, v in (d or {}).items():
            if not hasattr(p, k):
                raise ValueError(f"unknown probe parameter {k!r}")
            setattr(p, k, type(getattr(p, k))(v))
        return p


@dataclass
class Context:
    adapter: Adapter
    faults: FaultController
    params: ProbeParams = field(default_factory=ProbeParams)
    log: Callable[[str], None] = lambda s: None
    # results of the probes already run in this repetition (P5B reads P4/P5)
    previous: dict = field(default_factory=dict)


def _try(fn, *a):
    try:
        return fn(*a)
    except Unsupported:
        return None


def _new(ctx: Context, pid: str) -> ProbeResult:
    a = ctx.adapter
    return ProbeResult(probe_id=pid, system=a.system, operation=a.operation, command_id=a.new_id(pid))


def _late_ms(ctx: Context) -> int:
    return int(ctx.params.late_factor * ctx.adapter.client_timeout_s * 1000)


def _needs_net(ctx: Context, r: ProbeResult) -> bool:
    if isinstance(ctx.faults, NoFaults):
        r.applicable, r.skip_reason = False, "no network fault controller for this system"
        return False
    return True


def _falsity(r: ProbeResult) -> None:
    """Contradictions between terminal evidence and truth (documentation deviations)."""
    eff = r.effects_final if r.effects_final is not None else r.effects
    for ev in (r.query, r.query_after, r.stop, r.fence):
        if ev is None or not ev.terminal or eff is None:
            continue
        if ev.outcome == STOPPED and eff > 0:
            r.false_stopped = True
        if ev.outcome == APPLIED and eff == 0:
            r.false_applied = True
    if r.false_stopped is None:
        r.false_stopped = False
    if r.false_applied is None:
        r.false_applied = False


def p1_lose_request(ctx: Context) -> ProbeResult:
    """Request lost on the way: what does the client see; can anything prove STOPPED?"""
    a, p, r = ctx.adapter, ctx.params, _new(ctx, "P1")
    if not _needs_net(ctx, r):
        return r
    base = a.truth_baseline()
    ctx.faults.drop(UP)
    try:
        r.client = a.send(r.command_id)
    finally:
        ctx.faults.clear()
    time.sleep(p.settle_s)
    r.effects = a.effects(r.command_id, base)
    r.query = _try(a.query, r.command_id)
    r.stop = _try(a.stop, r.command_id)
    # Is the stop terminal at the owner? A same-identity resend must not apply.
    r.resend = a.send(r.command_id)
    time.sleep(p.settle_s)
    r.effects_final = a.effects(r.command_id, base)
    r.query_after = _try(a.query, r.command_id)
    _falsity(r)
    r.duplicate = (r.effects_final or 0) > 1
    return r


def p2_lose_response(ctx: Context) -> ProbeResult:
    """Effect happens, reply lost: is there a query that reveals APPLIED?"""
    a, p, r = ctx.adapter, ctx.params, _new(ctx, "P2")
    if not _needs_net(ctx, r):
        return r
    base = a.truth_baseline()
    ctx.faults.drop(DOWN)
    try:
        r.client = a.send(r.command_id)
    finally:
        ctx.faults.clear()
    time.sleep(p.settle_s)
    r.effects = a.effects(r.command_id, base)
    r.query = _try(a.query, r.command_id)
    r.resend = a.send(r.command_id)
    time.sleep(p.settle_s)
    r.effects_final = a.effects(r.command_id, base)
    r.duplicate = (r.effects_final or 0) > 1
    _falsity(r)
    return r


def p3_late_request(ctx: Context) -> ProbeResult:
    """Request delayed beyond the client timeout: late effect after a client-visible failure?"""
    a, p, r = ctx.adapter, ctx.params, _new(ctx, "P3")
    if not _needs_net(ctx, r):
        return r
    late = _late_ms(ctx)
    r.parameters = {"request_delay_ms": late, "client_timeout_s": a.client_timeout_s}
    base = a.truth_baseline()
    ctx.faults.delay(UP, late)
    t0 = time.monotonic()
    r.client = a.send(r.command_id)
    r.query = _try(a.query, r.command_id)  # ask while the request is still in flight
    r.effects = a.effects(r.command_id, base)
    # keep the toxic until the delayed bytes were delivered (Toxiproxy flushes on removal)
    time.sleep(max(0.0, late / 1000.0 - (time.monotonic() - t0)) + p.settle_s)
    ctx.faults.clear()
    time.sleep(p.settle_s)
    r.effects_final = a.effects(r.command_id, base)
    r.query_after = _try(a.query, r.command_id)
    r.late_effect = r.client.status != OK and (r.effects_final or 0) > (r.effects or 0)
    r.duplicate = (r.effects_final or 0) > 1
    _falsity(r)
    return r


def p4_retry_within_horizon(ctx: Context) -> ProbeResult:
    a, p, r = ctx.adapter, ctx.params, _new(ctx, "P4")
    base = a.truth_baseline()
    r.parameters = {"gap_s": p.within_gap_s}
    r.client = a.send(r.command_id)
    time.sleep(p.within_gap_s)
    r.resend = a.send(r.command_id)
    time.sleep(p.settle_s)
    r.effects_final = a.effects(r.command_id, base)
    r.duplicate = (r.effects_final or 0) > 1
    if r.client.status == OK and r.resend.status == OK:
        r.notes.append("replay_same_outcome=" + str(r.client.outcome == r.resend.outcome))
    _falsity(r)
    return r


def p5_retry_beyond_horizon(ctx: Context) -> ProbeResult:
    a, p, r = ctx.adapter, ctx.params, _new(ctx, "P5")
    gap = p.beyond_gap_s
    hint = a.dedup_horizon_hint_s()
    if gap <= 0:
        if hint is None or hint < 0:
            r.applicable, r.skip_reason = False, "no finite documented horizon and no beyond_gap_s configured"
            return r
        gap = float(hint) + p.beyond_margin_s
    if gap > p.max_beyond_gap_s:
        r.applicable, r.skip_reason = False, f"gap {gap}s exceeds max_beyond_gap_s"
        return r
    r.parameters = {"gap_s": gap, "horizon_hint_s": hint}
    base = a.truth_baseline()
    r.client = a.send(r.command_id)
    time.sleep(gap)
    r.resend = a.send(r.command_id)
    time.sleep(p.settle_s)
    r.effects_final = a.effects(r.command_id, base)
    r.duplicate = (r.effects_final or 0) > 1
    _falsity(r)
    return r


def _retry_once(a, cid: str, gap: float, settle: float) -> dict:
    """Send, wait ``gap`` seconds, resend the same identity; read truth."""
    base = a.truth_baseline()
    first = a.send(cid)
    time.sleep(gap)
    again = a.send(cid)
    time.sleep(settle)
    eff = a.effects(cid, base)
    return {"gap_s": round(gap, 3), "client": first.status, "resend": again.status, "effects": eff}


def p5b_bisect_horizon(ctx: Context) -> ProbeResult:
    """Narrows the dedup horizon between the largest gap without a duplicate
    (P4, or P5 when it did not duplicate) and the smallest gap with one (P5):
    each step retries a FRESH identity after the midpoint gap (release 065).

    A step decides only when both sends were answered and the truth channel
    shows 1 (no duplicate: the lower end moves up) or more (duplicate: the
    upper end moves down) effects; any other step ends the search, so the
    bracket is never narrowed on a guess. The result is still a lower bound
    under the probe's load (no intervening identities)."""
    a, p, r = ctx.adapter, ctx.params, _new(ctx, "P5B")
    if not getattr(a, "per_identity_truth", True):
        r.applicable, r.skip_reason = False, "needs per-identity truth (the plant or an owner record)"
        return r
    p4, p5 = ctx.previous.get("P4"), ctx.previous.get("P5")
    if p5 is None or not p5.applicable or p5.error:
        r.applicable, r.skip_reason = False, "P5 must run (and apply) before P5B in the same repetition"
        return r
    if not p5.duplicate:
        r.applicable, r.skip_reason = False, "P5 showed no duplicate: no upper end to bisect"
        return r
    if p4 is None or p4.error or p4.duplicate or (p4.effects_final or 0) != 1:
        r.applicable, r.skip_reason = False, "P4 must show dedup (one effect) before P5B"
        return r
    lo, hi = float(p4.parameters.get("gap_s", p.within_gap_s)), float(p5.parameters.get("gap_s", 0))
    if hi <= lo:
        r.applicable, r.skip_reason = False, f"P5 gap {hi}s is not above the P4 gap {lo}s"
        return r
    steps = []
    r.parameters = {"start_interval_s": [lo, hi], "resolution_s": p.bisect_resolution_s}
    while hi - lo > p.bisect_resolution_s and len(steps) < p.bisect_max_steps:
        mid = round((lo + hi) / 2.0, 3)
        cid = a.new_id("P5B")
        step = _retry_once(a, cid, mid, p.settle_s)
        step["command_id"] = cid
        steps.append(step)
        processed = step["client"] in (OK,) and step["resend"] in (OK,)
        if step["effects"] is None or not processed or step["effects"] < 1:
            step["decided"] = False
            r.notes.append(f"step at {mid}s undecided (client={step['client']}, resend={step['resend']}, effects={step['effects']}): search stopped")
            break
        step["decided"] = True
        step["duplicate"] = step["effects"] > 1
        if step["duplicate"]:
            hi = mid
        else:
            lo = mid
        ctx.log(f"P5B step {len(steps)}: gap={mid}s effects={step['effects']} -> [{lo}, {hi})")
    r.parameters.update({"steps": steps, "interval_s": [lo, hi], "width_s": round(hi - lo, 3)})
    r.effects_final = 1  # the probe's own identities are recorded per step
    r.duplicate = False  # duplicates beyond the horizon are the point of the search, not a veto
    r.notes.append(f"horizon in [{lo}s, {hi}s) after {len(steps)} step(s)")
    return r


def p6_concurrent_duplicate(ctx: Context) -> ProbeResult:
    a, p, r = ctx.adapter, ctx.params, _new(ctx, "P6")
    base = a.truth_baseline()
    out: list = [None] * p.concurrency
    barrier = threading.Barrier(p.concurrency)

    def worker(i):
        barrier.wait()
        out[i] = a.send(r.command_id)

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(p.concurrency)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    r.concurrent = out
    time.sleep(p.settle_s)
    r.effects_final = a.effects(r.command_id, base)
    r.duplicate = (r.effects_final or 0) > 1
    r.query_after = _try(a.query, r.command_id)
    _falsity(r)
    return r


def p7_device_offline(ctx: Context) -> ProbeResult:
    """Device offline at send time, back later: does the command still execute, and when?"""
    a, p, r = ctx.adapter, ctx.params, _new(ctx, "P7")
    try:
        a.set_device_online(False)
    except Unsupported as e:
        r.applicable, r.skip_reason = False, f"no device hook: {e}"
        return r
    r.parameters = {"offline_s": p.offline_s}
    base = a.truth_baseline()
    try:
        r.client = a.send(r.command_id)
        r.query = _try(a.query, r.command_id)
        r.effects = a.effects(r.command_id, base)
        time.sleep(max(0.0, p.offline_s - a.client_timeout_s))
    finally:
        a.set_device_online(True)
    t_online = time.monotonic()
    time.sleep(p.settle_s + 2.0)
    r.effects_final = a.effects(r.command_id, base)
    r.query_after = _try(a.query, r.command_id)
    r.late_effect = r.client.status != OK and (r.effects_final or 0) > (r.effects or 0)
    r.notes.append(f"truth_read_after_online_s={time.monotonic() - t_online:.2f}")
    _falsity(r)
    return r


def p8_client_crash(ctx: Context) -> ProbeResult:
    """The sender crashes right after sending; a fresh client must recover from durable owner state."""
    a, p, r = ctx.adapter, ctx.params, _new(ctx, "P8")
    base = a.truth_baseline()
    r.client = a.send(r.command_id, timeout_s=p.p8_abandon_s)  # crash = abandon the reply
    time.sleep(p.settle_s)
    fresh = a.fresh_client()
    r.effects = a.effects(r.command_id, base)
    r.query = _try(fresh.query, r.command_id)
    r.resend = fresh.send(r.command_id)
    time.sleep(p.settle_s)
    r.effects_final = a.effects(r.command_id, base)
    r.duplicate = (r.effects_final or 0) > 1
    _falsity(r)
    return r


def _late_then_backward(ctx: Context, pid: str, action: str) -> ProbeResult:
    a, p, r = ctx.adapter, ctx.params, _new(ctx, pid)
    if not _needs_net(ctx, r):
        return r
    late = _late_ms(ctx)
    if action == "expiry":
        exp = a.expiry_s()
        if exp is None:
            r.applicable, r.skip_reason = False, "no owner expiry configured"
            return r
        late = max(late, int((float(exp) + p.settle_s) * 1000) + 500)
    r.parameters = {"request_delay_ms": late, "action": action}
    base = a.truth_baseline()
    ctx.faults.delay(UP, late)
    t0 = time.monotonic()
    r.client = a.send(r.command_id)
    try:
        if action == "stop":
            r.stop = a.stop(r.command_id)
        elif action == "fence":
            r.fence = a.fence([r.command_id])
        elif action == "expiry":
            r.parameters["expiry_s"] = a.expiry_s()
    except Unsupported as e:
        r.applicable, r.skip_reason = False, f"{action} unsupported: {e}"
    r.effects = a.effects(r.command_id, base)
    time.sleep(max(0.0, late / 1000.0 - (time.monotonic() - t0)) + p.settle_s)
    ctx.faults.clear()
    time.sleep(p.settle_s)
    r.effects_final = a.effects(r.command_id, base)
    r.query_after = _try(a.query, r.command_id)
    r.late_effect = r.client.status != OK and (r.effects_final or 0) > (r.effects or 0)
    _falsity(r)
    return r


def p9_stop_then_late(ctx: Context) -> ProbeResult:
    """Per-identity stop while the request is delayed; the late request must not apply."""
    return _late_then_backward(ctx, "P9", "stop")


def p9f_fence_then_late(ctx: Context) -> ProbeResult:
    """Owner fence covering the delayed identity; the late request must not apply."""
    return _late_then_backward(ctx, "P9F", "fence")


def p9t_expiry_then_late(ctx: Context) -> ProbeResult:
    """Delay beyond the owner expiry: the late request must not apply (T)."""
    return _late_then_backward(ctx, "P9T", "expiry")


# ---- retention under pressure (release 062, review RV02-RV05) -------------
# Each probe sends ``pressure_n`` fresh identities between the observation of
# one identity and the follow-up, so bounded receipt / dedup / tombstone
# tables (e.g. a RAM ring) show their real retention instead of an optimistic
# "no violation observed".

def _pressure(ctx: Context, r: ProbeResult) -> int:
    a, n = ctx.adapter, ctx.params.pressure_n
    ok = 0
    for _ in range(n):
        c = a.send(a.new_id(r.probe_id + "x"))
        ok += c.status == OK
    r.parameters.update({"intervening": n, "intervening_ok": ok})
    if ok < n:
        r.notes.append(f"only {ok}/{n} intervening sends succeeded; pressure may be lower than configured")
    return ok


def _needs_pressure(ctx: Context, r: ProbeResult) -> bool:
    if ctx.params.pressure_n <= 0:
        r.applicable, r.skip_reason = False, "pressure_n not configured"
        return False
    return True


def p2n_receipt_after_pressure(ctx: Context) -> ProbeResult:
    """Applied identity, then pressure_n other identities: is APPLIED still queryable?"""
    a, p, r = ctx.adapter, ctx.params, _new(ctx, "P2N")
    if not _needs_pressure(ctx, r):
        return r
    base = a.truth_baseline()
    r.client = a.send(r.command_id)
    time.sleep(p.settle_s)
    r.effects = a.effects(r.command_id, base)
    _pressure(ctx, r)
    time.sleep(p.settle_s)
    r.query = _try(a.query, r.command_id)
    r.effects_final = r.effects
    r.parameters["receipt_retained"] = bool(r.query and r.query.terminal and r.query.outcome == APPLIED)
    _falsity(r)
    return r


def p5n_retry_after_pressure(ctx: Context) -> ProbeResult:
    """Same-identity retry after pressure_n other identities: is the dedup record retained?"""
    a, p, r = ctx.adapter, ctx.params, _new(ctx, "P5N")
    if not _needs_pressure(ctx, r):
        return r
    base = a.truth_baseline()
    r.client = a.send(r.command_id)
    time.sleep(p.settle_s)
    r.effects = a.effects(r.command_id, base)
    _pressure(ctx, r)
    time.sleep(p.settle_s)
    base2 = a.truth_baseline()
    r.resend = a.send(r.command_id)
    time.sleep(p.settle_s)
    if getattr(a, "per_identity_truth", True):
        r.effects_final = a.effects(r.command_id, base)
    else:  # global-delta truth: only the resend ran since base2
        r.effects_final = (r.effects or 0) + a.effects(r.command_id, base2)
    r.duplicate = (r.effects_final or 0) > 1
    _falsity(r)
    return r


def p9n_stop_then_pressure(ctx: Context) -> ProbeResult:
    """Stop an identity that never arrived, push pressure_n identities, then
    deliver it: a terminal stop must still win (tombstone retention)."""
    a, p, r = ctx.adapter, ctx.params, _new(ctx, "P9N")
    if not _needs_pressure(ctx, r):
        return r
    try:
        r.stop = a.stop(r.command_id)
    except Unsupported as e:
        r.applicable, r.skip_reason = False, f"stop unsupported: {e}"
        return r
    r.effects = 0
    _pressure(ctx, r)
    time.sleep(p.settle_s)
    base = a.truth_baseline()
    r.resend = a.send(r.command_id)
    time.sleep(p.settle_s)
    r.effects_final = a.effects(r.command_id, base)
    r.query_after = _try(a.query, r.command_id)
    r.late_effect = (r.effects_final or 0) > 0
    _falsity(r)
    return r


PROBES = {
    "P1": p1_lose_request,
    "P2": p2_lose_response,
    "P3": p3_late_request,
    "P4": p4_retry_within_horizon,
    "P5": p5_retry_beyond_horizon,
    "P5B": p5b_bisect_horizon,
    "P6": p6_concurrent_duplicate,
    "P7": p7_device_offline,
    "P8": p8_client_crash,
    "P9": p9_stop_then_late,
    "P9F": p9f_fence_then_late,
    "P9T": p9t_expiry_then_late,
    "P2N": p2n_receipt_after_pressure,
    "P5N": p5n_retry_after_pressure,
    "P9N": p9n_stop_then_pressure,
}


def run_probe(ctx: Context, pid: str) -> ProbeResult:
    fn = PROBES[pid]
    t0 = time.monotonic()
    try:
        r = fn(ctx)
    except Exception as e:  # recorded, never hidden
        try:
            ctx.faults.clear()
        except Exception:
            pass
        r = ProbeResult(probe_id=pid, system=ctx.adapter.system, operation=ctx.adapter.operation, command_id="", error=f"{type(e).__name__}: {e}")
    r.elapsed_ms = (time.monotonic() - t0) * 1000
    # bytes delayed by this probe's window are delivered before the next probe starts
    q = getattr(ctx.faults, "quiesce", None)
    if q is not None:
        try:
            waited = q()
            if waited > 0.5:
                r.notes.append(f"waited {waited:.1f}s for delayed bytes before the next probe")
        except Exception:
            pass
    return r
