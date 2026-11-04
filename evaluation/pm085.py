#!/usr/bin/env python3
"""Estimands E-PM of PREREG_extra_085.md (release 085; review M5a, M5b): EVICON and PERIODIC_FULL in the same campaign.

For every matched run (PMz6, PMzi: the cutoff firmware at W = 6 and unbounded; PMm6, PMmi: MinIO's conditional PUT) and
policy, from the cells (the per-episode metrics pairs_ext083.py wrote, report/pairs/PREREG_extra_085/cells.csv, when it is
given; else the runs' runtime_episodes.csv for the owner calls and clean fulfilment) and the authority traces:

  owner calls     logical owner calls per episode: queries, stops, fences and their sum (the unit of the count is one call
                  of the owner's evidence API, not a network message: a fence answer composed by the gateway is one call)
  fulfilment      clean and confirmed fulfilment (pp of offered), admitted, mean time to a terminal outcome
  fence pause     pauses per run and policy (n), p50 / p95 / max (ms) and the paused fraction, with the end events of
                  release 085 (evaluation/overhead.py: anchor, abort, per-identity commit; rounded-index percentiles)
  break-even      the cost ratio at which the two policies' modelled costs are equal, with logical owner calls as the unit
                  and a linear cost model (cost = queries * C_q + fences * C_f, or stops * C_s on MinIO): with
                  dq = queries(EVICON) - queries(PERIODIC_FULL) and dx = fences (or stops) (EVICON) - (PERIODIC_FULL),
                  per episode, r* = -mean(dq) / mean(dx) when the signs differ; EVICON has the lower modelled cost iff
                  C_x / C_q > r* (dq > 0 > dx). 95 % interval: paired bootstrap over episodes (10,000 resamples, seed 85,
                  percentiles); a resample in which the signs do not differ has no break-even and is counted apart.
                  Arithmetic on logical call counts: not a measured energy, latency or wear result
  same contract   per firmware run: every cell of both policies started its gateways with the same measured contract
                  (contract_sha256), the same binding and the same command QoS (the cells' gateway logs), and the
                  measurement the run used (config real_owner.contracts.<c>.measured_in)
  policy order    the order in which each episode's cells ran (done.jsonl), against the run's policy_order
  replications    with the 084 runs given: the same quantities for the 084 PF run and its reference run's EVICON cells (the
                  084 cross-run comparison) next to the matched one

Decides nothing (estimands). Writes pm085.json and PM085.md into --out.

    python3 evaluation/pm085.py --run PMz6=<dir> --run PMzi=<dir> --run PMm6=<dir> --run PMmi=<dir> \\
        [--run C2=<dir> --run PFz6=<dir> ...] [--cells <report>/pairs/PREREG_extra_085/cells.csv] --out <dir>
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import overhead as OV  # noqa: E402  (release 085: the fence pause with the per-identity end event)

MATCHED = {"PMz6": ("ZEPHYR_CUTOFF", "fences"), "PMzi": ("ZEPHYR_CUTOFF", "fences"),
           "PMm6": ("MINIO_COND", "stops"), "PMmi": ("MINIO_COND", "stops")}
CROSS084 = {"PMz6": ("PFz6", "C2"), "PMzi": ("PFzi", "Rz"), "PMm6": ("PFm6", "C"), "PMmi": ("PFmi", "Rm")}
POLICIES = ("EVICON", "PERIODIC_FULL")
METRICS = ("clean_pp", "confirmed_pp", "admitted_pp", "mean_time_to_terminal_s", "queries", "stops", "fences", "unresolved_at_end")
BOOT, SEED = 10000, 85


def _f(x):
    try:
        v = float(x)
        return v if not math.isnan(v) else None
    except (TypeError, ValueError):
        return None


def cells_from_csv(path: Path) -> dict:
    """{(run key, contract, policy): {episode: {metric: value}}} from pairs_ext083's cells.csv."""
    out = {}
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            out.setdefault((r["run"], r["contract"], r["policy"]), {})[int(r["episode"])] = {m: _f(r.get(m)) for m in METRICS}
    return out


def cells_from_run(key: str, run: Path) -> dict:
    """The same from the run's runtime_episodes.csv (VALID cells): owner calls, clean, admitted, time to terminal."""
    out = {}
    f = run / "runtime_episodes.csv"
    if not f.exists():
        return out
    with open(f, newline="") as fh:
        for r in csv.DictReader(fh):
            if not r["cell_status"].startswith("VALID"):
                continue
            off = max(1.0, float(r["offered"] or 0))
            out.setdefault((key, r["contract"], r["policy"]), {})[int(r["episode"])] = {
                "clean_pp": 100.0 * float(r["clean_fulfilled"] or 0) / off, "confirmed_pp": None,
                "admitted_pp": 100.0 * float(r["admitted"] or 0) / off, "mean_time_to_terminal_s": _f(r["mean_time_to_terminal_s"]),
                "queries": _f(r["queries"]), "stops": _f(r["stops"]), "fences": _f(r["fences"]), "unresolved_at_end": _f(r["unresolved_at_end"])}
    return out


def mean(v):
    v = [x for x in v if x is not None]
    return statistics.mean(v) if v else None


def level(eps: dict) -> dict:
    out = {"n": len(eps)}
    for m in METRICS:
        out[m] = mean([e.get(m) for e in eps.values()])
    calls = [sum(e.get(k) or 0.0 for k in ("queries", "stops", "fences")) for e in eps.values()]
    out["owner_calls"] = mean(calls)
    return out


def break_even(a: dict, b: dict, other: str) -> dict:
    """a = EVICON's episodes, b = PERIODIC_FULL's: r* = -mean(dq) / mean(dx) and its paired bootstrap interval."""
    eps = sorted(set(a) & set(b))
    dq = [(a[e]["queries"] or 0.0) - (b[e]["queries"] or 0.0) for e in eps]
    dx = [(a[e][other] or 0.0) - (b[e][other] or 0.0) for e in eps]
    out = {"n": len(eps), "other": other, "dq_mean": mean(dq), "dx_mean": mean(dx)}
    if len(eps) < 2:
        return dict(out, ratio=None, why="fewer than 2 paired episodes")

    def ratio(q, x):
        mq, mx = statistics.mean(q), statistics.mean(x)
        return -mq / mx if mx != 0 and (mq > 0) != (mx > 0) and mq != 0 else None
    r = ratio(dq, dx)
    out["ratio"] = r
    out["evicon_cheaper_iff"] = (f"C_{other[:-1]}/C_query > {r:.3f}" if r is not None and out["dq_mean"] > 0 else
                                 f"C_{other[:-1]}/C_query < {r:.3f}" if r is not None else "no break-even (one policy has fewer calls of both kinds)")
    rng = random.Random(SEED)
    rs, none = [], 0
    for _ in range(BOOT):
        idx = [rng.randrange(len(eps)) for _ in eps]
        x = ratio([dq[i] for i in idx], [dx[i] for i in idx])
        if x is None:
            none += 1
        else:
            rs.append(x)
    rs.sort()
    if rs:
        out["boot95"] = [rs[int(0.025 * (len(rs) - 1))], rs[int(round(0.975 * (len(rs) - 1)))]]
    out["boot_without_break_even"] = none
    out["boot_resamples"] = BOOT
    return out


def fence_pause(run: Path, contract: str, policy: str) -> dict:
    pauses, paused, cell_ms, ends, cells = [], 0.0, 0.0, {}, 0
    for cell in sorted(run.glob(f"cells/ep*/{contract}/{policy}")):
        if not (cell / "row.json").exists():
            continue
        m = OV.cell_metrics(cell)
        cells += 1
        pauses += m["fence_pause_ms"]
        paused += m["paused_ms"]
        cell_ms += m["cell_ms"]
        for k, v in m["fence_pause_ends"].items():
            ends[k] = ends.get(k, 0) + v
    return {"cells": cells, "n": len(pauses), "p50_ms": OV.pct(pauses, 50), "p95_ms": OV.pct(pauses, 95),
            "max_ms": round(max(pauses), 1) if pauses else None, "paused_fraction": paused / cell_ms if cell_ms else None, "ends": ends}


def same_contract(run: Path, contract: str) -> dict:
    """The gateways' start records of every cell: contract SHA-256, binding, command QoS (firmware runs)."""
    seen, cells = {}, 0
    for pol in POLICIES:
        for cell in sorted(run.glob(f"cells/ep*/{contract}/{pol}")):
            for g in sorted(cell.glob("raw/C/gateway*.jsonl")):
                if g.name.endswith(".state.jsonl"):
                    continue
                for line in g.read_text(errors="replace").splitlines():
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    if "contract_sha256" in e:
                        seen.setdefault((pol, e.get("contract_sha256"), e.get("binding"), e.get("qos_cmd")), set()).add(cell.parts[-3])
                        break
            cells += 1
    combos = sorted({k[1:] for k in seen})
    try:
        cfg = json.loads((run / "config.json").read_text())
        spec = cfg["real_owner"]["contracts"][contract]
    except (OSError, ValueError, KeyError):
        spec = {}
    return {"cells": cells, "combinations": [list(c) for c in combos], "same": (len(combos) == 1) if combos else None,
            "policies_seen": sorted({k[0] for k in seen}), "measured_in": spec.get("measured_in"), "binding_config": spec.get("binding"),
            "contract_file": spec.get("contract_file")}


def order(run: Path) -> dict:
    """The order of each episode's cells (done.jsonl) and the run's rule."""
    rule = None
    try:
        rule = json.loads((run / "config.json").read_text()).get("policy_order", "config")
    except (OSError, ValueError):
        pass
    first = {}
    f = run / "done.jsonl"
    for line in (f.read_text().splitlines() if f.exists() else []):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        ep, ct, pol = d["cell"].split("/")
        first.setdefault(ep, []).append(pol)
    return {"rule": rule, "episodes": {ep: v for ep, v in sorted(first.items())}}


def analyse(runs: dict, cells: dict) -> dict:
    res = {"matched": [], "cross084": []}
    for key, (contract, other) in MATCHED.items():
        pf, ref = CROSS084[key]
        if pf in runs and ref in runs:
            a = cells.get((ref, contract, "EVICON")) or cells_from_run(ref, runs[ref]).get((ref, contract, "EVICON"), {})
            b = cells.get((pf, contract, "PERIODIC_FULL")) or cells_from_run(pf, runs[pf]).get((pf, contract, "PERIODIC_FULL"), {})
            res["cross084"].append({"matched_run": key, "pf_run": pf, "ref_run": ref, "levels": {"EVICON": level(a), "PERIODIC_FULL": level(b)},
                                    "break_even": break_even(a, b, other)})
        if key not in runs:
            continue
        run = runs[key]
        c = {pol: cells.get((key, contract, pol)) or cells_from_run(key, run).get((key, contract, pol), {}) for pol in POLICIES}
        row = {"run": key, "campaign": run.parent.name, "dir": str(run), "contract": contract, "levels": {p: level(c[p]) for p in POLICIES},
               "fence_pause": {p: fence_pause(run, contract, p) for p in POLICIES}, "break_even": break_even(c["EVICON"], c["PERIODIC_FULL"], other),
               "order": order(run)}
        if contract.startswith("ZEPHYR"):
            row["same_contract"] = same_contract(run, contract)
        eps = sorted(set(c["EVICON"]) & set(c["PERIODIC_FULL"]))
        row["clean_diff"] = {"n": len(eps), "mean": mean([c["PERIODIC_FULL"][e]["clean_pp"] - c["EVICON"][e]["clean_pp"] for e in eps])}
        res["matched"].append(row)
    return res


def _n(x, nd=1):
    return "-" if x is None else f"{x:.{nd}f}"


def markdown(res: dict) -> str:
    L = ["# E-PM: EVICON and PERIODIC_FULL in the same campaign (PREREG_extra_085.md)", "",
         "Estimands, no claim. Owner calls are logical calls of the owner's evidence API per episode (a fence answer the gateway "
         "composes is one call). The break-even is arithmetic on these counts under a linear cost model, not a measured energy, "
         "latency or wear result.", "",
         "| run | policy | n | clean % | confirmed % | queries | stops | fences | owner calls | time to terminal (s) | fence pauses (n, p50/p95 ms, paused %) |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in res["matched"]:
        for p in POLICIES:
            lv, fp = r["levels"][p], r["fence_pause"][p]
            L.append(f"| {r['run']} | {p} | {lv['n']} | {_n(lv['clean_pp'], 2)} | {_n(lv['confirmed_pp'], 2)} | {_n(lv['queries'])} | {_n(lv['stops'])} | "
                     f"{_n(lv['fences'])} | {_n(lv['owner_calls'])} | {_n(lv['mean_time_to_terminal_s'], 3)} | "
                     f"{fp['n']}, {_n(fp['p50_ms'])}/{_n(fp['p95_ms'])}, {_n(100 * fp['paused_fraction'] if fp['paused_fraction'] is not None else None, 2)} |")
    L += ["", "| run | PERIODIC_FULL - EVICON, clean (pp, n) | EVICON - PERIODIC_FULL: queries / fences or stops | break-even r* (95 % bootstrap) | EVICON cheaper iff | same contract and binding | order of the cells |",
          "|---|---|---|---|---|---|---|"]
    for r in res["matched"]:
        b = r["break_even"]
        sc = r.get("same_contract")
        same = ("-" if sc is None else "not available (no gateway logs in the cells)" if sc["same"] is None else
                (("yes: " if sc["same"] else "NO: ") + "; ".join(f"sha {str(c[0])[:12]}, binding {c[1]}, QoS {c[2]}" for c in sc["combinations"])
                 + f" ({sc['measured_in']})"))
        ordr = " ".join(f"{ep}:{'>'.join(x[:2] for x in v)}" for ep, v in r["order"]["episodes"].items())
        L.append(f"| {r['run']} | {_n(r['clean_diff']['mean'], 3)} ({r['clean_diff']['n']}) | {_n(b['dq_mean'])} / {_n(b['dx_mean'])} | "
                 f"{_n(b.get('ratio'), 3)} ({', '.join(_n(x, 3) for x in b.get('boot95') or []) or '-'}; without break-even {b.get('boot_without_break_even', '-')}) | "
                 f"{b.get('evicon_cheaper_iff', '-')} | {same} | {r['order']['rule']}: {ordr} |")
    if res["cross084"]:
        L += ["", "The 084 cross-run comparison (the 084 PF run against its reference run's EVICON cells, paired by schedule):", "",
              "| matched run | 084 runs | EVICON queries / fences or stops | PERIODIC_FULL queries / fences or stops | break-even r* (95 % bootstrap) |", "|---|---|---|---|---|"]
        for r in res["cross084"]:
            b, e, p = r["break_even"], r["levels"]["EVICON"], r["levels"]["PERIODIC_FULL"]
            o = b["other"]
            L.append(f"| {r['matched_run']} | {r['pf_run']} vs {r['ref_run']} | {_n(e['queries'])} / {_n(e[o])} | {_n(p['queries'])} / {_n(p[o])} | "
                     f"{_n(b.get('ratio'), 3)} ({', '.join(_n(x, 3) for x in b.get('boot95') or []) or '-'}) |")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", action="append", default=[], metavar="KEY=DIR")
    ap.add_argument("--cells", default="")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    runs = {}
    for kv in a.run:
        k, _, d = kv.partition("=")
        runs[k] = Path(d)
    cells = cells_from_csv(Path(a.cells)) if a.cells and Path(a.cells).exists() else {}
    res = analyse(runs, cells)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "pm085.json").write_text(json.dumps(res, indent=1, default=str) + "\n")
    (out / "PM085.md").write_text(markdown(res))
    print(f"pm085: {len(res['matched'])} matched run(s), {len(res['cross084'])} 084 cross-run comparison(s) -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
