"""Contract Map: every system next to its capability set.

Measured horizons are lower bounds under the probe's load ("≥"); declared
capabilities the probes could not establish are listed separately.

Combines the catalog (DOCUMENTED / HYPOTHESIS / simulator contracts in
experiments/contracts) with MEASURED contracts produced by EviCon-Probe, and
lists, per system, the declared/hypothesised level next to the measured one.
"""
from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path

from .rubric import level

SOURCE_ORDER = {"MEASURED": 0, "RUNTIME_VERIFIED": 1, "DOCUMENTED": 2, "HYPOTHESIS": 3}


def _load(paths) -> list:
    out = []
    for p in paths:
        p = Path(p)
        files = sorted(p.glob("*.json")) if p.is_dir() else [p]
        for f in files:
            c = json.loads(f.read_text())
            c["_file"] = str(f)
            out.append(c)
    return out


def measured_in(path: str) -> str:
    """Release, run and measurement stamp of a measured contract, from where the driver wrote it:
    .../EVICON_TOIT_<release>/results/review-<stamp>/measure/<stack>/runs/<config>/<stamp>/contract.json
    (or EVICON_<release>_result/... in a bundle)
    (release 070; '-' for a contract file elsewhere)."""
    rel = re.search(r"EVICON_(?:TOIT_)?(\d{3})(?!\d)", path)  # source tree, or EVICON_<release>_result in a bundle
    run = re.search(r"(review-\d{8}T\d{6}(?:-local)?)", path)
    stamp = Path(path).parent.name if re.fullmatch(r"\d{8}T\d{6}", Path(path).parent.name) else ""
    parts = [f"release {rel.group(1)}" if rel else "", run.group(1) if run else "", stamp]
    return " ".join(p for p in parts if p) or "-"


def _rubric_fields(c: dict) -> dict:
    """From the rubric.json written next to a measured contract (release 070): the horizon
    interval [lower, upper) in seconds (upper empty when no duplicate was seen), the level of the
    declared capabilities and whether a late effect was observed."""
    out = {"D_h_lower_s": "", "D_h_upper_s": "", "declared_level": "", "late_effect": ""}
    f = c.get("_file", "")
    if c.get("source") != "MEASURED" or not f:
        return out
    try:
        rb = json.loads(Path(f).with_name("rubric.json").read_text())
    except (OSError, ValueError):
        return out
    iv = rb.get("horizon_interval_s") or []
    if "D_h" in c.get("capabilities", []) and iv:
        out["D_h_lower_s"] = iv[0]
        out["D_h_upper_s"] = iv[1] if len(iv) > 1 and iv[1] is not None else ""
    out["declared_level"] = rb.get("declared_level", "") or ""
    out["late_effect"] = rb.get("late_effect", "")
    return out


def rows(catalog_dirs, measured_files, include_sim: bool = False) -> list:
    contracts = _load(catalog_dirs) + _load(measured_files)
    res = []
    for c in contracts:
        if not include_sim and str(c.get("id", "")).startswith("sim-"):
            continue
        caps = set(c.get("capabilities", []))
        h = int(c.get("dedup_horizon_ms", 0))
        res.append({
            "id": c.get("id"), "system": c.get("system", ""), "operation": c.get("operation", ""),
            "source": c.get("source", ""), "level": level(caps, h),
            "D_h": (("∞" if h == -1 else f"{h/1000:g}s") + ("" if c.get("source") != "MEASURED" else " (≥)")) if "D_h" in caps else "—",
            "Q_A": "✓" if "Q_A" in caps else "—", "Q_S": "✓" if "Q_S" in caps else "—",
            "F": "✓" if "F" in caps else "—", "X": "✓" if "X" in caps else "—",
            "T": (f"{int(c.get('expiry_ms', 0))/1000:g}s" if "T" in caps else "—"),
            "declared_unverified": ",".join(c.get("declared_unverified", [])) or "—",
            "fault_model": c.get("fault_model", "") or "—",
            "notes": c.get("notes", "")[:160],
            "measured_in": measured_in(c.get("_file", "")) if c.get("source") == "MEASURED" else "",
            **_rubric_fields(c),
        })
    res.sort(key=lambda r: (r["system"].lower(), SOURCE_ORDER.get(r["source"], 9), r["id"]))
    return res


def markdown(rs: list) -> str:
    cols = ["system", "operation", "source", "level", "D_h", "Q_A", "Q_S", "F", "X", "T", "declared_unverified", "id", "measured_in"]
    b = io.StringIO()
    b.write("| " + " | ".join(cols) + " |\n")
    b.write("|" + "---|" * len(cols) + "\n")
    for r in rs:
        b.write("| " + " | ".join(str(r[c]).replace("|", "/") for c in cols) + " |\n")
    return b.getvalue()


def write(rs: list, out_prefix) -> None:
    p = Path(out_prefix)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.with_suffix(".md").write_text("# Contract Map\n\n" + markdown(rs))
    with open(p.with_suffix(".csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rs[0].keys()) if rs else ["id"])
        w.writeheader()
        for r in rs:
            w.writerow(r)
