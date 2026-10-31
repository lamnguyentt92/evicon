#!/usr/bin/env python3
"""Experiment C2 at a glance (release 074): one row per contract and policy of an expC2-zephyr run.

Clean and confirmed fulfilment come from the frozen pairs_report.py (per episode, % of offered; mean and
95 % t interval); safety counts, actions and late effects from runtime_episodes.csv; the commands whose
blind retry the T4 guard withheld (distinct identities, from the authority traces: the
retry_suppressed_t4 column counts dispatcher passes, not commands); what the gateways saw (barriers:
completed, aborted by an uncertain receipt, timed out; stops by firmware answer; link resets and
restarts, from raw/C/gateway*.jsonl); and the contracts the run used (contracts/*.json, measured in the
same review run). Descriptive only: the hypotheses are decided by pairs_report.py against
PREREG_expC2_074.md.

    python3 evaluation/expC2_summary.py --out <dir> <expC2-zephyr run dir>
    python3 evaluation/expC2_summary.py --experiment C3 --out <dir> <expC3-ablation run dir>    (release 075)
Writes EXPC2.md and expC2.json (EXPC3.md and expC3.json with --experiment C3) into --out. Release 075: the
channel and the restriction of each contract (experiment C3) are listed with the contracts.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from cadence_report import t_interval  # noqa: E402  (frozen; reused)
from pairs_report import load_run  # noqa: E402  (frozen; reused)

COUNTS = ("duplicate_effects", "bound_violations", "logical_duplicates", "false_stopped", "false_applied", "late_effects",
          "unresolved_at_end", "queries", "stops", "stops_terminal", "fences", "fences_ok", "reissues", "dispatch_attempts")
ORDER = {"ZEPHYR_PLAIN": 0, "ZEPHYR_CUTOFF": 1, "ZEPHYR_STOP": 2}
POLICIES = {"EVICON": 0, "M1": 1, "NAIVE": 2}


def t4_commands(cell: Path) -> int:
    """Distinct identities with at least one OUTBOX_RETRY_SUPPRESSED_T4 event in the cell's authority traces."""
    ids = set()
    for f in cell.glob("raw/**/authority*.jsonl"):
        with open(f) as fh:
            for line in fh:
                if "OUTBOX_RETRY_SUPPRESSED_T4" not in line:
                    continue
                try:
                    x = json.loads(line)
                except ValueError:
                    continue
                ids.add((x.get("domain_id"), x.get("command_id")))
    return len(ids)


def gateway_stats(cell: Path) -> Counter:
    c = Counter()
    # testbed: the gateways' files are in raw/C (VM-C); local mode: in raw/. Only that one copy is read
    # (on a single-host simulated testbed raw/A and raw/E hold the same files again)
    d = cell / "raw" / "C" if (cell / "raw" / "C").is_dir() else cell / "raw"
    for f in sorted(d.glob("gateway[0-9].jsonl")) + sorted(d.glob("gateway[0-9][0-9].jsonl")):
        full = False
        with open(f) as fh:
            for line in fh:
                try:
                    x = json.loads(line)
                except ValueError:
                    continue
                op = x.get("op")
                if op == "barrier":
                    res = x.get("result", "?")
                    if res == "OK" and x.get("uncertain"):
                        res = "OK_WITH_UNCERTAIN"
                    c[f"barrier_{res}"] += 1
                elif op == "stop" and not x.get("cached"):
                    out = x.get("outcome") or "NO_REPLY"
                    # release 076: an UNCERTAIN stop by its reason (TOMBSTONE_TABLE_FULL: the firmware's durable
                    # tombstone table is full; FUTURE_BLOCKED_PAST_UNKNOWN: the receipt may have left the RAM ring)
                    c[f"stop_{out}" + (f"_{x.get('reason')}" if out == "UNCERTAIN" and x.get("reason") else "")] += 1
                    full |= x.get("reason") == "TOMBSTONE_TABLE_FULL"
                elif op == "start":
                    c["gateway_starts"] += 1
                elif op == "crash":
                    c["gateway_crashes"] += 1
                elif op == "online" and x.get("v") is False:
                    c["link_down"] += 1
                elif op == "OWNER_CONTRADICTION":
                    c["owner_contradictions"] += 1
                elif op == "mqtt" and x.get("event") == "disconnected":
                    c["mqtt_disconnects"] += 1
                elif op == "dropped":  # release 075: the direct channel lost a message while offline
                    c[f"dropped_{x.get('dir', '?')}_{x.get('kind', '?')}"] += 1
        c["device_episodes_with_full_tombstone_table"] += full
    if not c["device_episodes_with_full_tombstone_table"]:
        del c["device_episodes_with_full_tombstone_table"]
    return c


def summarize(run: Path) -> dict:
    r = load_run("C2", run)
    rows = {}
    with open(run / "runtime_episodes.csv", newline="") as fh:
        for x in csv.DictReader(fh):
            if x["cell_status"].startswith("VALID"):
                rows.setdefault((x["contract"], x["policy"]), []).append(x)
    groups = []
    for (ct, pol), xs in sorted(rows.items(), key=lambda k: (ORDER.get(k[0][0], 9), k[0][0], POLICIES.get(k[0][1], 9), k[0][1])):
        g = {"contract": ct, "policy": pol, "cells": len(xs), "devices": max(int(float(x.get("devices") or 0)) for x in xs)}
        for k in COUNTS:
            g[k] = sum(int(float(x.get(k) or 0)) for x in xs)
        g["cells_with_duplicates"] = sum(1 for x in xs if int(x["duplicate_effects"] or 0) > 0)
        for m in ("clean_pp", "confirmed_pp"):
            v = [c[m] for (a, b, _), c in r["cells"].items() if a == ct and b == pol and c[m] == c[m]]
            # release 083: unrounded (paper_tables.py rounds once; a 2-decimal value rounded again to 1 decimal printed
            # 79.147 as 79.2, OPEN_ITEMS (a) 6)
            g[m] = statistics.mean(v) if v else None
            ci = t_interval(v, 0.95) if len(v) > 1 else None
            g[m + "_t95"] = [ci[0], ci[1]] if ci else None
        t4, gw = 0, Counter()
        for cell in (run / "cells").glob(f"ep*/{ct}/{pol}"):
            t4 += t4_commands(cell)
            gw += gateway_stats(cell)
        g["t4_withheld_commands"] = t4
        g["gateway"] = dict(sorted(gw.items()))
        groups.append(g)
    contracts = {}
    specs = {}
    if (run / "config.json").is_file():
        specs = (json.loads((run / "config.json").read_text()).get("real_owner") or {}).get("contracts") or {}
    for f in sorted((run / "contracts").glob("*.json")):
        c = json.loads(f.read_text())
        sp = specs.get(f.stem, {})
        contracts[f.stem] = {"id": c.get("id"), "capabilities": c.get("capabilities"), "dedup_horizon_ms": c.get("dedup_horizon_ms"),
                             "source": c.get("source"), "channel": sp.get("channel", ""),
                             "restricted_from": Path(sp["restricted_from"]).name if sp.get("restricted_from") else ""}
    owner = {}
    if (run / "owner.json").is_file():
        o = json.loads((run / "owner.json").read_text())
        owner = {"broker": o.get("broker", {}), "firmware": o.get("firmware", {}), "what": o.get("what", "")}
    return {"run": str(run), "invalid": r["invalid"], "groups": groups, "contracts": contracts, "owner": owner}


TITLES = {"C2": "Experiment C2: EviCon on the Zephyr firmware behind Mosquitto, at a glance",
          "C3": "Experiment C3: the firmware's evidence decomposed on the real IoT stack, at a glance"}


def markdown(s: dict, experiment: str = "C2") -> str:
    L = [f"# {TITLES[experiment]}", "",
         f"Run `{s['run']}`; INVALID cells: {len(s['invalid'])} {s['invalid'][:8]}", ""]
    if s["contracts"]:
        L += ["Contracts (measured in the same review run):", ""]
        for k, c in s["contracts"].items():
            extra = "".join(f"; {x} {c[x]}" for x in ("channel", "restricted_from") if c.get(x))
            L.append(f"- {k}: `{c['id']}` {c['source']} {c['capabilities']} (D_h horizon {c['dedup_horizon_ms']} ms{extra})")
        L.append("")
    L += ["| contract | policy | cells | clean % [95 %] | confirmed % [95 %] | duplicate effects (cells) | bound violations | other safety | "
          "late effects | unresolved at end | queries | fences (ok) | stops (terminal) | re-issues | T4: commands withheld |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for g in s["groups"]:
        def c(m):
            return "--" if g[m] is None else f"{g[m]:.1f} [{g[m + '_t95'][0]:.1f}, {g[m + '_t95'][1]:.1f}]" if g[m + "_t95"] else f"{g[m]:.1f}"
        other = g["logical_duplicates"] + g["false_stopped"] + g["false_applied"]
        L.append(f"| {g['contract']} | {g['policy']} | {g['cells']} | {c('clean_pp')} | {c('confirmed_pp')} | {g['duplicate_effects']} "
                 f"({g['cells_with_duplicates']}) | {g['bound_violations']} | {other} | {g['late_effects']} | {g['unresolved_at_end']} | "
                 f"{g['queries']} | {g['fences']} ({g['fences_ok']}) | {g['stops']} ({g['stops_terminal']}) | {g['reissues']} | "
                 f"{g['t4_withheld_commands']} |")
    L += ["", "Gateways (what the client side saw, summed over cells):", ""]
    for g in s["groups"]:
        if g["gateway"]:
            L.append(f"- {g['contract']}/{g['policy']}: " + ", ".join(f"{k} {v}" for k, v in g["gateway"].items()))
    if s["owner"]:
        L += ["", f"Owner as run: `{json.dumps(s['owner'])[:700]}`"]
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--experiment", choices=sorted(TITLES), default="C2")
    ap.add_argument("run")
    a = ap.parse_args(argv)
    s = summarize(Path(a.run))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"exp{a.experiment}.json").write_text(json.dumps(s, indent=1) + "\n")
    (out / f"EXP{a.experiment}.md").write_text(markdown(s, a.experiment))
    print(f"expC2 summary: {len(s['groups'])} groups -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
