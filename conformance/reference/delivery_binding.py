"""The delivery binding rule of the MQTT owner gateway, made explicit (release 084, review M1).

An MQTT session that resumes re-sends every QoS 1 PUBLISH it has not seen acknowledged (MQTT 3.1.1, sections 4.3.2
and 4.4), so a command sent at QoS 1 can reach the owner again at any later time, whether or not the authority ever
resends it. At the owner such a transport copy is indistinguishable from a resend under the same identity. QoS 1 is
therefore safe only when the owner's suppression covers every copy the transport can produce:

  (i)  structural suppression: the owner's design never executes a late copy of an identity it may have executed,
       whatever its age (an adapter or implementation premise, never a measurement). The cutoff firmware's eviction
       rule is the example, under two build premises that must BOTH hold: CONFIG_EVC_GUARD_EVICTED=y (Kconfig default)
       and CONFIG_EVC_DEDUP_HORIZON_MS=-1 (Kconfig default: a receipt stays valid for as long as it is in the ring).
       Then a command whose receipt may have left the 32-entry ring or been lost in a reboot is answered UNCERTAIN and
       never executed again (firmware/zephyr-cutoff/tests/host/test_evc_late_copy.c; the negative-control build
       executes it again). With a positive (or zero) horizon a receipt still in the ring but older than the horizon is
       not live, and src/evc_core.c (evc_core_command: the eviction guard applies only when no receipt is found)
       executes the copy again: the same host test shows it in both builds;
  (ii) owner expiry T with e + epsilon <= h: every copy that can still take effect arrives within h of the first
       arrival, inside the suppression horizon.

A measured finite D_h alone does not suffice: a horizon observed by probes (e.g. >= 600 s) does not cover a copy that
arrives after it. Rule: the gateway sends commands at QoS 1 only to an owner that meets (i) or (ii); QoS 0 (at most
once) otherwise. (A trusted bound on the redelivery delay below h, case (iii) of the paper, is not used by any gateway
of this release.)

The frozen 077 gateway (mqtt_owner_gateway_ext077.py, binding "auto") chooses QoS 1 for a contract with D_h and QoS 0
otherwise. This module states the rule and checks, before a firmware campaign, that the two agree for every contract
whose binding is "auto"; the driver (evicon_review.py) and the 084 firmware runner refuse a campaign where they differ.
For every configuration evaluated so far they agree (the cutoff firmware: D_h and structural suppression -> QoS 1; the
plain firmware: no D_h -> QoS 0), so the effective behaviour is unchanged.

The adapters' declarations are experiments/owners/bindings/delivery-binding-084.json.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DECLARATIONS = REPO / "experiments" / "owners" / "bindings" / "delivery-binding-084.json"


def frozen_auto_qos(caps) -> int:
    """The 077 gateway's binding "auto" (mqtt_owner_gateway_ext077.binding_qos("auto", caps)): QoS 1 iff D_h."""
    return 1 if "D_h" in set(caps) else 0


def rule_qos(contract: dict, structural: bool) -> tuple:
    """(QoS, reason) under the explicit rule. contract: an evidence contract (capabilities, dedup_horizon_ms,
    expiry_ms, clock_skew_ms); structural: the owner's adapter declares structural suppression (case i)."""
    caps = set(contract.get("capabilities") or [])
    if structural:
        return 1, "STRUCTURAL_SUPPRESSION"
    if "D_h" in caps and "T" in caps:
        h = contract.get("dedup_horizon_ms")
        e, eps = contract.get("expiry_ms") or 0, contract.get("clock_skew_ms") or 0
        if h is not None and (h < 0 or (e > 0 and e + eps <= h)):
            return 1, "EXPIRY_WITHIN_HORIZON"
        return 0, "EXPIRY_BEYOND_HORIZON"
    if "D_h" in caps:
        return 0, "FINITE_MEASURED_HORIZON_ONLY"
    return 0, "NO_DEDUPLICATION"


# The build premises of structural suppression in the cutoff firmware (both must hold; the Kconfig defaults)
PREMISES = {"CONFIG_EVC_GUARD_EVICTED": "y", "CONFIG_EVC_DEDUP_HORIZON_MS": "-1"}


def _kconfig_default(kconfig: str, sym: str):
    m = re.search(rf"config {sym[len('CONFIG_'):]}\n(.*?)(?:\n\S|\Z)", kconfig, re.S)
    if not m:
        return None
    d = re.search(r"^\s*default\s+(\S+)\s*$", m.group(1), re.M)
    return d.group(1) if d else None


def source_premises(repo: Path = REPO) -> list:
    """Problems ([] = OK) with the premises in the firmware's source configuration: each Kconfig default is the premise's
    value and no project or board configuration sets another."""
    fw = repo / "firmware" / "zephyr-cutoff"
    kconfig = (fw / "Kconfig").read_text()
    bad = []
    for sym, want in PREMISES.items():
        got = _kconfig_default(kconfig, sym)
        if got != want:
            bad.append(f"Kconfig default {sym}={got} (structural suppression needs {sym}={want})")
        for conf in [fw / "prj.conf", *sorted((fw / "boards").glob("*.conf"))]:
            if not conf.exists():
                continue
            for m in re.finditer(rf"^\s*{sym}\s*=\s*(\S+)\s*$", conf.read_text(), re.M):
                if m.group(1) != want:
                    bad.append(f"{conf.relative_to(repo)} sets {sym}={m.group(1)} (structural suppression needs {sym}={want})")
    return bad


def build_premises(build_config: dict) -> list:
    """Problems ([] = OK) with the premises in a build's configuration ({CONFIG_EVC_*: value}, as the driver reads it from the
    build's zephyr-cutoff.config: a bool that is off is absent there)."""
    bad = []
    for sym, want in PREMISES.items():
        got = build_config.get(sym)
        if str(got).strip('"') != want:
            bad.append(f"the firmware build has {sym}={got if got is not None else 'n (not set)'} (structural suppression needs {sym}={want})")
    return bad


def guard_enabled(repo: Path = REPO) -> bool:
    """Both build premises hold in the source configuration (the name of release 084's first version of this check)."""
    return not source_premises(repo)


def declarations(path: Path = DECLARATIONS) -> dict:
    return json.loads(Path(path).read_text())["adapters"]


def _requires(d: dict) -> list:
    r = d.get("requires") or []
    return [r] if isinstance(r, str) else list(r)


def structural(mode: str, repo: Path = REPO, decl: dict | None = None, build_config: dict | None = None) -> bool:
    """The adapter of the firmware mode declares structural suppression and its build premises hold: in the source
    configuration and, when given, in the configuration of the build that runs."""
    d = (decl if decl is not None else declarations()).get(str(mode).upper(), {})
    if not d.get("structural_suppression"):
        return False
    req = _requires(d)
    if any(r.split("=", 1)[0] in PREMISES for r in req):
        if source_premises(repo):
            return False
        if build_config is not None and build_premises(build_config):
            return False
    return True


def check_contract(name: str, mode: str, contract: dict, repo: Path = REPO, decl: dict | None = None,
                   build_config: dict | None = None) -> list:
    """Problems ([] = OK) for one contract with binding "auto": the 077 gateway's QoS must be the rule's."""
    gw = frozen_auto_qos(contract.get("capabilities") or [])
    q, why = rule_qos(contract, structural(mode, repo, decl, build_config))
    if gw != q:
        extra = ""
        d = (decl if decl is not None else declarations()).get(str(mode).upper(), {})
        if d.get("structural_suppression"):
            extra = "; " + "; ".join(source_premises(repo) + (build_premises(build_config) if build_config is not None else []))
        return [f"{name} (mode {mode}): the gateway's binding auto sends QoS {gw}, the delivery binding rule allows QoS {q} ({why}){extra}"]
    return []
