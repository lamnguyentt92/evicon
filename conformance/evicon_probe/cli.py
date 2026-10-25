"""EviCon-Probe command line.

  python -m evicon_probe run --config configs/evicon-e2-stop.json --out out/
  python -m evicon_probe classify --results out/<name>/<stamp>/results.jsonl --config configs/x.json
  python -m evicon_probe map --catalog ../experiments/contracts --measured out --out out/contract_map
  python -m evicon_probe list
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import adapters as registry
from . import contract_map
from .runner import load_config, reclassify, run_campaign
from .rubric import to_contract


def check_expect(expect, rb) -> dict | None:
    """Positive/negative controls carry the contract they must reproduce."""
    if not expect:
        return None
    problems = []
    want = sorted(expect.get("capabilities", []))
    if sorted(rb.capabilities()) != want:
        problems.append(f"capabilities {sorted(rb.capabilities())} != expected {want}")
    if expect.get("no_false_evidence") and rb.false_evidence:
        problems.append(f"false terminal evidence: {rb.false_evidence}")
    if rb.errors:
        problems.append(f"probe errors: {sorted(rb.errors)}")
    for d in expect.get("declared_unverified", []):
        if d not in rb.declared_unverified:
            problems.append(f"{d} expected in declared_unverified")
    return {"pass": not problems, "problems": problems, "expected_capabilities": want}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="evicon_probe", description="EviCon-Probe evidence-contract measurement harness")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--config", required=True)
    r.add_argument("--out", default=str(Path(__file__).resolve().parents[2] / "results" / "conformance"),
                   help="default: results/conformance (collected by `evicon_review.py bundle`)")
    r.add_argument("--probes", default="", help="comma-separated subset, e.g. P1,P2,P4")
    r.add_argument("--reps", type=int, default=0)
    c = sub.add_parser("classify")
    c.add_argument("--results", required=True)
    c.add_argument("--config", required=True)
    m = sub.add_parser("map")
    m.add_argument("--catalog", action="append", default=[])
    m.add_argument("--measured", action="append", default=[],
                   help="directory searched recursively for contract.json (repeatable; release 068: several runs, "
                        "the newest measurement of each contract id is kept)")
    m.add_argument("--out", default="out/contract_map")
    m.add_argument("--include-sim", action="store_true")
    sub.add_parser("list")
    a = ap.parse_args(argv)

    if a.cmd == "list":
        for name in sorted(registry.REGISTRY):
            try:
                cls = registry.load(name)
                print(f"{name:22s} {cls.implementation_status:24s} {cls.system} / {cls.operation}")
            except ImportError as e:  # missing optional dependency
                print(f"{name:22s} {'IMPORT_ERROR':24s} {e}")
        return 0
    if a.cmd == "run":
        cfg = load_config(a.config)
        probes = [p.strip().upper() for p in a.probes.split(",") if p.strip()] or None
        rb, contract, out = run_campaign(cfg, a.out, probes, a.reps or None)
        summary = {"out": str(out), "level": rb.level, "declared_level": rb.declared_level,
                   "capabilities": rb.capabilities(), "status": rb.status, "dedup_horizon_ms": rb.dedup_horizon_ms,
                   "horizon_interval_s": rb.horizon_interval_s,
                   "declared_unverified": rb.declared_unverified, "retention": rb.retention,
                   "late_effect": rb.late_effect, "false_evidence": rb.false_evidence, "errors": rb.errors,
                   "deviations": rb.deviations}
        verdict = check_expect(cfg.get("expect"), rb)
        if verdict is not None:
            summary["expect"] = verdict
        (Path(out) / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
        print(json.dumps(summary, indent=2))
        return 0 if verdict is None or verdict["pass"] else 3
    if a.cmd == "classify":
        cfg = load_config(a.config)
        cls = registry.load(cfg["adapter"])
        declared = getattr(cls, "declared", {}) or {}
        from .probes import ProbeParams
        rb = reclassify(a.results, declared, ProbeParams.from_dict(cfg.get("params", {})).__dict__, cfg.get("fault_model", ""))
        print(json.dumps({"rubric": rb.to_dict(), "contract": to_contract(rb, cfg.get("references", []))}, indent=2, sort_keys=True))
        return 0
    if a.cmd == "map":
        latest = {}
        for d in a.measured:
            for f in Path(d).rglob("contract.json"):
                try:
                    cid = json.loads(f.read_text()).get("id") or str(f)
                except (OSError, ValueError):
                    continue
                # runs/<config>/<YYYYmmddTHHMMSS>/contract.json: the newest repetition of a measurement wins
                if cid not in latest or f.parent.name > latest[cid].parent.name:
                    latest[cid] = f
        measured = sorted(latest.values())
        rs = contract_map.rows(a.catalog, measured, a.include_sim)
        contract_map.write(rs, a.out)
        print(contract_map.markdown(rs))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
