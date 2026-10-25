"""Rubric: probe results -> observed evidence contract (release 062).

Every capability gets one of five statuses, with the probes that support it:

  OBSERVED             a positive control showed the capability and no
                       applicable probe in the same premise contradicted it;
  CONTRADICTED         at least one applicable probe contradicted it
                       (duplicate, false terminal evidence, retention lost
                       under pressure);
  ABSENT               the system has no such interface;
  UNKNOWN              the probes could not decide (no positive control,
                       zero-effect run, probe not applicable or errored);
  DECLARED_UNVERIFIED  documented by the system but not observable black-box
                       (X; an unbounded dedup horizon).

Only OBSERVED capabilities enter the MEASURED contract, each with provenance.
Rules that answer review RV02/RV03:

  * D_h needs a positive control: P4 must show exactly one effect after a
    processed retry. A zero-effect P4 is UNKNOWN, never evidence of dedup.
  * Any duplicate in P2, P4, P6 or P8 (same identity, inside the horizon) and
    any duplicate in P5N (retention under pressure) vetoes D_h.
  * The horizon is the largest gap that did not duplicate: a lower bound under
    the probe's load (intervening identities are recorded). A measurement never
    yields an unbounded horizon; a declared unbounded horizon is reported as
    DECLARED_UNVERIFIED.
  * X is never measured black-box; it is DECLARED_UNVERIFIED when declared.
  * Q_S / F need terminal STOPPED evidence that the truth channel confirms
    (no effect afterwards); P9N (tombstone under pressure) can veto Q_S.

The emitted contract uses the JSON schema of internal/contract
(EvidenceContract, source MEASURED); `evicon-contract validate` checks it.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

from .model import APPLIED, STOPPED

CAPS = ("D_h", "Q_A", "Q_S", "F", "X", "T")
UNBOUNDED = -1

OBSERVED = "OBSERVED"
CONTRADICTED = "CONTRADICTED"
ABSENT = "ABSENT"
UNKNOWN = "UNKNOWN"
DECLARED_UNVERIFIED = "DECLARED_UNVERIFIED"
STATUSES = (OBSERVED, CONTRADICTED, ABSENT, UNKNOWN, DECLARED_UNVERIFIED)

DEFAULT_FAULT_MODEL = "network loss/delay, client abandon; owner up (no owner crash or reboot)"


def level(caps: set, horizon_ms: int) -> str:
    """Mirror of contract.EvidenceContract.Level (internal/contract/lattice.go)."""
    expiry_reveals = "T" in caps and ("X" in caps or ("Q_A" in caps and "D_h" in caps and horizon_ms == UNBOUNDED))
    if "X" in caps:
        return "K4"
    if "Q_S" in caps or "F" in caps or expiry_reveals:
        return "K3"
    if "Q_A" in caps:
        return "K2"
    if "D_h" in caps:
        return "K1"
    return "K0"


@dataclass
class Rubric:
    system: str
    operation: str
    implementation_status: str = ""
    status: dict = field(default_factory=dict)        # cap -> one of STATUSES
    observed: dict = field(default_factory=dict)      # cap -> True (OBSERVED) / False (CONTRADICTED, ABSENT) / None
    evidence: dict = field(default_factory=dict)      # cap -> probe ids behind the status
    provenance: dict = field(default_factory=dict)    # cap (and D_h_horizon, T_expiry) -> text for the contract
    reasons: dict = field(default_factory=dict)       # cap -> why it is not OBSERVED
    dedup_horizon_ms: int = 0                         # observed lower bound (ms), never unbounded
    horizon_interval_s: list = field(default_factory=list)
    retention: dict = field(default_factory=dict)     # receipts / dedup / tombstones under pressure
    expiry_ms: int = 0
    late_effect: Optional[bool] = None
    duplicates: list = field(default_factory=list)
    false_evidence: list = field(default_factory=list)
    deviations: list = field(default_factory=list)
    declared: dict = field(default_factory=dict)
    declared_unverified: list = field(default_factory=list)
    level: str = "K0"
    declared_level: str = ""
    evidence_latency_ms: dict = field(default_factory=dict)
    not_applicable: dict = field(default_factory=dict)
    errors: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    fault_model: str = DEFAULT_FAULT_MODEL

    def set(self, cap: str, status: str, probes: list, reason: str = "") -> None:
        assert status in STATUSES, status
        self.status[cap] = status
        self.observed[cap] = True if status == OBSERVED else (False if status in (CONTRADICTED, ABSENT) else None)
        self.evidence[cap] = list(dict.fromkeys(probes))
        if reason:
            self.reasons[cap] = reason
        else:
            self.reasons.pop(cap, None)

    def capabilities(self) -> list:
        return [c for c in CAPS if self.status.get(c) == OBSERVED]

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["capabilities"] = self.capabilities()
        return d


def _ok(r) -> bool:
    return r is not None and r.get("applicable", True) and not r.get("error")


def _status(obs) -> str:
    return (obs or {}).get("status", "")


def _processed(obs) -> bool:
    """The send reached the system and was answered (a timeout proves nothing)."""
    return _status(obs) in ("OK", "ERROR", "CONFLICT")


def _declared_caps(declared: dict) -> tuple:
    caps, h = set(), 0
    for k, v in (declared or {}).items():
        if k == "D_h":
            if v:
                caps.add("D_h")
                h = int(v * 1000) if v not in (UNBOUNDED, None) and v > 0 else UNBOUNDED
        elif v:
            caps.add(k)
    return caps, h


def _terminal(ev, outcome) -> bool:
    return bool(ev and ev.get("supported", True) and ev.get("terminal") and ev.get("outcome") == outcome)


def classify(results: list, system: str, operation: str, declared: dict, params: dict,
             implementation_status: str = "", fault_model: str = "") -> Rubric:
    """``results``: list of ProbeResult dicts (as written to results.jsonl)."""
    by = {}
    for r in results:
        by.setdefault(r["probe_id"], r)
    declared = declared or {}
    rb = Rubric(system=system, operation=operation, implementation_status=implementation_status, declared=declared,
                fault_model=fault_model or DEFAULT_FAULT_MODEL)
    for r in results:
        if r.get("error"):
            rb.errors[r["probe_id"]] = r["error"]
        if not r.get("applicable", True):
            rb.not_applicable[r["probe_id"]] = r.get("skip_reason", "")
        if r.get("duplicate"):
            rb.duplicates.append(r["probe_id"])
        for kind in ("false_stopped", "false_applied"):
            if r.get(kind):
                rb.false_evidence.append(f"{r['probe_id']}:{kind}")
        for ev in ("query", "stop", "fence", "query_after"):
            e = r.get(ev)
            if e and e.get("supported") and e.get("terminal"):
                rb.evidence_latency_ms.setdefault(ev, []).append(round(e.get("latency_ms", 0.0), 3))
    false_applied = [r["probe_id"] for r in results if _ok(r) and r.get("false_applied")]
    false_stopped = {ch: [r["probe_id"] for r in results if _ok(r) and r.get("false_stopped") and _terminal(r.get(ch), STOPPED)]
                     for ch in ("stop", "fence", "query", "query_after")}

    _classify_dedup(rb, by, declared, params)
    _classify_query(rb, by, false_applied)
    _classify_backward(rb, by, false_stopped)
    _classify_expiry(rb, by)
    if declared.get("X"):
        rb.set("X", DECLARED_UNVERIFIED, [], "X (atomic effect+receipt) needs crash-cut evidence; black-box probes cannot observe it")
        rb.declared_unverified.append("X")
    else:
        rb.set("X", UNKNOWN, [], "not declared and not observable black-box")

    lates = [p for p in ("P3", "P7", "P9", "P9F", "P9T", "P9N") if _ok(by.get(p)) and by[p].get("late_effect")]
    tested_late = [p for p in ("P3", "P7") if _ok(by.get(p))]
    rb.late_effect = bool(lates) if tested_late or lates else None
    if lates:
        rb.notes.append("late effect after a client-visible failure in " + ",".join(lates))

    dcaps, dh = _declared_caps(declared)
    for c in CAPS:
        st = rb.status.get(c)
        if c in dcaps and st in (CONTRADICTED, ABSENT):
            why = rb.reasons.get(c, "")  # 068: rstrip(" ()") cut the closing parenthesis of every reason
            rb.deviations.append(f"{c}: declared but {st.lower()}" + (f" ({why})" if why else ""))
        if c not in dcaps and st == OBSERVED:
            rb.deviations.append(f"{c}: observed but not declared")
        if c in dcaps and st == UNKNOWN:
            rb.declared_unverified.append(c)
    if "D_h" in dcaps and rb.status.get("D_h") == OBSERVED and dh not in (0, UNBOUNDED) and rb.horizon_interval_s:
        lo, hi = rb.horizon_interval_s[0], rb.horizon_interval_s[1]
        if hi is not None and dh / 1000.0 >= hi:
            rb.deviations.append(f"D_h: declared {dh/1000.0}s but duplicate observed at {hi}s")
        elif lo is not None and lo > 1.5 * dh / 1000.0:
            # not unsafe (the owner keeps its records longer than declared), but the declaration
            # is wrong: reported so the catalog is corrected (065 testbed run: CoAP 45 s vs >= 243.9 s)
            rb.deviations.append(f"D_h: declared {dh/1000.0}s but dedup still held at {lo}s (longer than declared)")
    for f in rb.false_evidence:
        rb.deviations.append("terminal evidence contradicted by truth: " + f)
    rb.declared_unverified = list(dict.fromkeys(rb.declared_unverified))
    rb.level = level(set(rb.capabilities()), rb.dedup_horizon_ms)
    rb.declared_level = level(dcaps, dh) if declared else ""
    return rb


def _classify_dedup(rb: Rubric, by: dict, declared: dict, params: dict) -> None:
    p4, p5, p5n, p5b = by.get("P4"), by.get("P5"), by.get("P5N"), by.get("P5B")
    within = float(params.get("within_gap_s", 1.0))
    veto = [p for p in ("P2", "P4", "P6", "P8") if _ok(by.get(p)) and by[p].get("duplicate")]
    if _ok(p5n) and p5n.get("duplicate"):
        n = p5n.get("parameters", {}).get("intervening")
        rb.retention["dedup"] = {"intervening": n, "retained": False}
        veto.append("P5N")
    elif _ok(p5n):
        rb.retention["dedup"] = {"intervening": p5n.get("parameters", {}).get("intervening"), "retained": True}
    if veto:
        why = "duplicate effect in " + ",".join(veto)
        if veto == ["P5N"]:
            why = f"dedup record lost after {rb.retention['dedup']['intervening']} intervening identities (P5N)"
        rb.set("D_h", CONTRADICTED, veto, why)
        return
    # an unbounded horizon can never be shown by a finite probe; reported only while D_h holds
    # (065 testbed run: listed for Temporal although D_h was contradicted)
    if declared.get("D_h") == UNBOUNDED:
        rb.declared_unverified.append("D_h_unbounded")
    if not _ok(p4):
        rb.set("D_h", UNKNOWN, [], "P4 not run, not applicable or errored")
        return
    eff = p4.get("effects_final")
    if eff is None or eff == 0:
        rb.set("D_h", UNKNOWN, ["P4"], "P4 produced no effect: no positive control for dedup")
        return
    if not _processed(p4.get("client")) or not _processed(p4.get("resend")):
        rb.set("D_h", UNKNOWN, ["P4"], "P4 send or retry was not shown to reach the system")
        return
    support = ["P4"] + [p for p in ("P2", "P6", "P8") if _ok(by.get(p))]
    lower, upper, pv = within, None, f"P4:gap={within}s"
    if _ok(p5):
        gap = float(p5.get("parameters", {}).get("gap_s", 0))
        if p5.get("duplicate"):
            upper = gap
            rb.notes.append(f"finite dedup horizon in [{within}s, {gap}s); contract uses the lower bound")
        elif (p5.get("effects_final") or 0) == 1 and _processed(p5.get("resend")):
            lower, pv = max(within, gap), f"P5:gap={gap}s"
            rb.notes.append(f"no duplicate after {gap}s: horizon >= {gap}s under the probe's load (lower bound)")
        support.append("P5")
    iv = (p5b or {}).get("parameters", {}).get("interval_s") if _ok(p5b) else None
    if iv and upper is not None and len(iv) == 2:
        # P5B narrowed [P4 gap, P5 gap) with fresh identities (release 065)
        blo, bhi = float(iv[0]), float(iv[1])
        if blo > lower:
            lower, pv = blo, f"P5B:no duplicate at {blo}s"
        upper = min(upper, bhi)
        support.append("P5B")
        rb.notes.append(f"P5B bracket: horizon in [{lower}s, {upper}s)")
    if _ok(p5n):
        n = p5n.get("parameters", {}).get("intervening")
        pv += f";P5N:intervening={n}:retained"
        support.append("P5N")
    else:
        pv += ";intervening=0"
    rb.dedup_horizon_ms = int(lower * 1000)
    rb.horizon_interval_s = [lower, upper]
    rb.set("D_h", OBSERVED, support)
    rb.provenance["D_h"] = ",".join(support)
    rb.provenance["D_h_horizon"] = f"lower bound {lower}s ({pv})"
    if declared.get("D_h") == UNBOUNDED:
        rb.notes.append(f"declared unbounded horizon not established; measured lower bound {lower}s")


def _classify_query(rb: Rubric, by: dict, false_applied: list) -> None:
    p2, p2n = by.get("P2"), by.get("P2N")
    if _ok(p2n):
        n = p2n.get("parameters", {}).get("intervening")
        kept = bool(p2n.get("parameters", {}).get("receipt_retained"))
        rb.retention["receipts"] = {"intervening": n, "retained": kept}
        if not kept and (p2n.get("effects") or 0) >= 1:
            rb.notes.append(f"APPLIED receipt no longer queryable after {n} intervening identities (P2N): NOT_FOUND is not evidence of absence")
    if false_applied:
        rb.set("Q_A", CONTRADICTED, false_applied, "APPLIED evidence for an identity without effect")
        return
    if not _ok(p2):
        rb.set("Q_A", UNKNOWN, [], "P2 not run, not applicable or errored")
        return
    q = p2.get("query")
    if q is None or not q.get("supported", False):
        rb.set("Q_A", ABSENT, ["P2"], "no identity-scoped query")
        return
    if (p2.get("effects") or 0) < 1:
        rb.set("Q_A", UNKNOWN, ["P2"], "P2 produced no effect: no positive control for the query")
        return
    if _terminal(q, APPLIED):
        support = ["P2"] + (["P2N"] if _ok(p2n) else [])
        rb.set("Q_A", OBSERVED, support)
        rb.provenance["Q_A"] = ",".join(support) + (f":receipts_retained={rb.retention['receipts']['retained']}@{rb.retention['receipts']['intervening']}"
                                                    if "receipts" in rb.retention else "")
        return
    if q.get("outcome") == APPLIED:
        # 069: e.g. a HEAD on an unconditionally written object: it shows the effect, but a later
        # copy can still apply, so it is not terminal evidence for the identity (068 wording read as
        # a contradiction of itself)
        rb.set("Q_A", CONTRADICTED, ["P2"], "query answered APPLIED for an applied identity, but not as terminal evidence "
                                            "(the identity can still be applied again)")
        return
    rb.set("Q_A", CONTRADICTED, ["P2"], f"query answered {q.get('outcome')} for an applied identity")


def _classify_backward(rb: Rubric, by: dict, false_stopped: dict) -> None:
    for cap, pid, ch in (("Q_S", "P9", "stop"), ("F", "P9F", "fence")):
        r = by.get(pid)
        if false_stopped.get(ch):
            rb.set(cap, CONTRADICTED, false_stopped[ch], f"STOPPED via {ch} but the identity took effect")
            continue
        if cap == "Q_S":
            p9n = by.get("P9N")
            if _ok(p9n) and _terminal(p9n.get("stop"), STOPPED):
                n = p9n.get("parameters", {}).get("intervening")
                kept = (p9n.get("effects_final") or 0) == 0
                rb.retention["tombstones"] = {"intervening": n, "retained": kept}
                if not kept:
                    rb.set(cap, CONTRADICTED, ["P9N"], f"tombstone lost after {n} intervening identities (P9N)")
                    continue
            p1 = by.get("P1")
            if _ok(p1) and _terminal(p1.get("stop"), STOPPED) and (p1.get("effects_final") or 0) > 0:
                rb.set(cap, CONTRADICTED, ["P1"], "P1: STOPPED but the resend took effect")
                continue
        if not _ok(r):
            unsupported = r is not None and not r.get("error") and "unsupported" in r.get("skip_reason", "")
            if unsupported:
                rb.set(cap, ABSENT, [pid], r.get("skip_reason", ""))
            else:
                rb.set(cap, UNKNOWN, [], f"{pid} not run, not applicable or errored")
            continue
        ev = r.get(ch)
        if ev is not None and not ev.get("supported", True):
            rb.set(cap, ABSENT, [pid], f"no {ch} interface")
            continue
        if _terminal(ev, STOPPED) and (r.get("effects_final") or 0) == 0:
            support = [pid] + [p for p in (("P1", "P9N") if cap == "Q_S" else ()) if _ok(by.get(p))]
            rb.set(cap, OBSERVED, support)
            rb.provenance[cap] = ",".join(support) + (f":tombstones_retained@{rb.retention['tombstones']['intervening']}"
                                                      if cap == "Q_S" and "tombstones" in rb.retention else "")
        elif ev is not None and ev.get("terminal") and ev.get("outcome") == STOPPED:
            rb.set(cap, CONTRADICTED, [pid], f"{ch} returned STOPPED but the late request took effect")
        else:
            rb.set(cap, UNKNOWN, [pid], f"{ch} answered {(ev or {}).get('outcome')} (not terminal STOPPED)")


def _classify_expiry(rb: Rubric, by: dict) -> None:
    r = by.get("P9T")
    if not _ok(r):
        if r is not None and "no owner expiry" in r.get("skip_reason", ""):
            rb.set("T", ABSENT, ["P9T"], "no owner expiry configured")
        else:
            rb.set("T", UNKNOWN, [], "P9T not run, not applicable or errored")
        return
    exp = float(r.get("parameters", {}).get("expiry_s") or 0)
    if r.get("late_effect") or (r.get("effects_final") or 0) > 0:
        rb.set("T", CONTRADICTED, ["P9T"], "a request delivered after the expiry took effect")
    elif exp <= 0:
        rb.set("T", UNKNOWN, ["P9T"], "expiry parameter unknown")
    else:
        rb.expiry_ms = int(exp * 1000)
        rb.set("T", OBSERVED, ["P9T"])
        delay = r.get("parameters", {}).get("request_delay_ms")
        rb.provenance["T"] = "P9T"
        rb.provenance["T_expiry"] = f"P9T:expiry={exp}s;request_delay_ms={delay}"


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def to_contract(rb: Rubric, references: list, contract_id: str = "") -> dict:
    caps = rb.capabilities()
    c = {
        "schema_version": 1,
        "id": contract_id or "meas-" + slug(rb.system + "-" + rb.operation)[:60],
        "system": rb.system,
        "operation": rb.operation,
        "source": "MEASURED",
        "capabilities": caps,
        "references": list(references or []),
        "verified_test_ids": sorted({p for cp in caps for p in rb.evidence.get(cp, []) if p.startswith("P")}),
        "provenance": {k: v for k, v in rb.provenance.items() if k in caps or k in ("D_h_horizon", "T_expiry")},
        "fault_model": rb.fault_model,
        "notes": "; ".join(rb.notes + rb.deviations)[:2000],
    }
    if rb.declared_unverified:
        c["declared_unverified"] = list(rb.declared_unverified)
    if "D_h" in caps:
        c["dedup_horizon_ms"] = max(1, rb.dedup_horizon_ms)
    if "T" in caps:
        c["expiry_ms"] = rb.expiry_ms
    return c


def write_outputs(rb: Rubric, contract: dict, out_dir) -> None:
    from pathlib import Path
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "rubric.json").write_text(json.dumps(rb.to_dict(), indent=2, sort_keys=True) + "\n")
    (out / "contract.json").write_text(json.dumps(contract, indent=2, sort_keys=True) + "\n")


def merge(rubrics: list) -> Rubric:
    """Aggregates repetitions: any contradiction wins; a capability is OBSERVED
    only if every repetition that could decide it observed it."""
    if not rubrics:
        raise ValueError("no rubrics")
    if len(rubrics) == 1:
        return rubrics[0]
    first = rubrics[0]
    m = Rubric(system=first.system, operation=first.operation, implementation_status=first.implementation_status,
               declared=first.declared, fault_model=first.fault_model)
    for c in CAPS:
        sts = [r.status.get(c, UNKNOWN) for r in rubrics]
        probes = sorted({p for r in rubrics for p in r.evidence.get(c, [])})
        reason = "; ".join(dict.fromkeys(r.reasons[c] for r in rubrics if c in r.reasons))
        if CONTRADICTED in sts:
            m.set(c, CONTRADICTED, probes, reason)
        elif all(s == OBSERVED for s in sts):
            m.set(c, OBSERVED, probes)
        elif ABSENT in sts and all(s in (ABSENT, UNKNOWN) for s in sts):
            m.set(c, ABSENT, probes, reason)
        elif DECLARED_UNVERIFIED in sts:
            m.set(c, DECLARED_UNVERIFIED, probes, reason)
        else:
            m.set(c, UNKNOWN, probes, reason or "not every repetition observed it")
        if m.status[c] == OBSERVED:
            m.provenance[c] = f"{first.provenance.get(c, ','.join(probes))} x{len(rubrics)} repetitions"
    hs = [r.dedup_horizon_ms for r in rubrics if r.status.get("D_h") == OBSERVED]
    if hs:
        m.dedup_horizon_ms = min(hs)
        m.provenance["D_h_horizon"] = f"min over repetitions of: {first.provenance.get('D_h_horizon', '')}"
    lo = [r.horizon_interval_s[0] for r in rubrics if r.horizon_interval_s]
    hi = [r.horizon_interval_s[1] for r in rubrics if r.horizon_interval_s and r.horizon_interval_s[1] is not None]
    if lo:
        m.horizon_interval_s = [min(lo), min(hi) if hi else None]
    ex = [r.expiry_ms for r in rubrics if r.status.get("T") == OBSERVED]
    m.expiry_ms = max(ex) if ex else 0
    if m.status.get("T") == OBSERVED:
        m.provenance["T_expiry"] = first.provenance.get("T_expiry", "P9T")
    lates = [r.late_effect for r in rubrics if r.late_effect is not None]
    m.late_effect = any(lates) if lates else None
    for r in rubrics:
        m.duplicates += r.duplicates
        m.false_evidence += r.false_evidence
        for k, v in r.retention.items():
            prev = m.retention.get(k)
            if prev is None or (prev.get("retained") and not v.get("retained")):
                m.retention[k] = v
        for d in r.deviations:
            if d not in m.deviations:
                m.deviations.append(d)
        for n in r.notes:
            if n not in m.notes:
                m.notes.append(n)
        for d in r.declared_unverified:
            if d not in m.declared_unverified:
                m.declared_unverified.append(d)
        m.not_applicable.update(r.not_applicable)
        m.errors.update(r.errors)
        for k, v in r.evidence_latency_ms.items():
            m.evidence_latency_ms.setdefault(k, []).extend(v)
    m.declared_unverified = [d for d in m.declared_unverified if d == "D_h_unbounded" or m.status.get(d) != OBSERVED]
    m.notes.append(f"aggregated over {len(rubrics)} repetitions")
    m.level = level(set(m.capabilities()), m.dedup_horizon_ms)
    m.declared_level = first.declared_level
    return m
