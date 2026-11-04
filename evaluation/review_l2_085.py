#!/usr/bin/env python3
"""Report analyses of release 085 for the reviewer of 4 November (no new cells; run by `evicon_review.py report` over
every campaign it reads; "not available" where the runs are missing). Not hypotheses.

  window_ci      (review section 8, Table "the window and the budget"): the effect of removing the window on the
                 firmware without evidence under the delivery binding (the plain firmware at QoS 0, plan 083's T4 runs:
                 expT4-zephyr-w6 -> expT4-zephyr-winf, queue and direct channel), which the paper printed as a difference
                 of level means without an interval, and the same contrast with evidence (the cutoff firmware C2 -> Rz;
                 {F} on the direct channel C3 -> Rz) next to it. Per pair: the per-episode clean fulfilment (pp of
                 offered, VALID cells) of the two runs; when the two runs' schedule files are byte-identical for an
                 episode, the episodes are paired (mean of the per-episode differences, Student t 95 % interval and a
                 paired bootstrap percentile interval, 10,000 resamples, seed 85); for episodes whose schedules differ, the
                 runs are compared unpaired (Welch t interval and a two-sample bootstrap). The method used is recorded per
                 pair ("paired" or "welch").
  offline_bound  (review N7): an offline upper bound on clean fulfilment per campaign. Per domain (device) and
                 episode of the campaign's schedule (schedule_ep<e>.json: every offered request with its size), the largest
                 number of offered requests whose sizes fit the domain's bound (workload.bound, from the anchor 0: the
                 domain's component maximum), taking the smallest first, as a share of the offered requests; ignoring
                 faults and deadlines. No safe policy (one that never exceeds the bound) can clean-fulfil more requests of
                 that workload than this, because every fulfilled request adds its size once and the sum stays within the
                 bound. Per campaign: the mean bound over the episodes that ran, next to the best observed clean fulfilment
                 among its guarded (contract, policy) groups (safety_total.kind), and per regime (seed, rate, sizes, bound,
                 duration) the same over its campaigns.

    python3 evaluation/review_l2_085.py --out <dir> --campaign <run dir> ...
Writes L2_085.md, l2_085.json (summary numbers for the paper macros), window_ci/window_ci.json and
offline_bound/offline_bound.json.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import safety_total as ST  # noqa: E402

BOOT, SEED = 10000, 85
# (label, from: (campaign, contract, policy), to: (campaign, contract, policy), what)
WINDOW_PAIRS = [
    ("fw-queue-plain-qos0", ("expT4-zephyr-w6", "ZEPHYR_PLAIN_QOS0", "EVICON"), ("expT4-zephyr-winf", "ZEPHYR_PLAIN_QOS0", "EVICON"),
     "firmware, queue, plain at QoS 0: removing the window without evidence"),
    ("fw-direct-plain-qos0", ("expT4-zephyr-w6", "ZEPHYR_PLAIN_DIRECT_QOS0", "EVICON"), ("expT4-zephyr-winf", "ZEPHYR_PLAIN_DIRECT_QOS0", "EVICON"),
     "firmware, direct, plain at QoS 0: removing the window without evidence"),
    ("fw-queue-cutoff", ("expC2-zephyr", "ZEPHYR_CUTOFF", "EVICON"), ("expR-zephyr-winf", "ZEPHYR_CUTOFF", "EVICON"),
     "firmware, queue, cutoff: removing the window with evidence"),
    ("fw-direct-f", ("expC3-ablation", "ZEPHYR_F_DIRECT", "EVICON"), ("expR-zephyr-winf", "ZEPHYR_F_DIRECT", "EVICON"),
     "firmware, direct, {F}: removing the window with evidence"),
]


def t_q975(df: float) -> float:
    """The 0.975 quantile of Student's t (bisection on the regularized incomplete beta through math.lgamma)."""
    def cdf(t):
        x = df / (df + t * t)
        return 1.0 - 0.5 * betainc(df / 2.0, 0.5, x) if t > 0 else 0.5 * betainc(df / 2.0, 0.5, x)
    lo, hi = 0.0, 100.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if cdf(mid) < 0.975:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def betainc(a, b, x):
    """Regularized incomplete beta I_x(a, b) (continued fraction, Numerical Recipes)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x)
    if x < (a + 1) / (a + b + 2):
        return math.exp(lbeta) * _cf(a, b, x) / a
    return 1.0 - math.exp(lbeta) * _cf(b, a, 1 - x) / b


def _cf(a, b, x):
    qab, qap, qam = a + b, a + 1, a - 1
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > 1e-300 else 1e-300)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > 1e-300 else 1e-300)
        c = 1.0 + aa / c if abs(c) > 1e-300 else 1e-300
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > 1e-300 else 1e-300)
        c = 1.0 + aa / c if abs(c) > 1e-300 else 1e-300
        de = d * c
        h *= de
        if abs(de - 1.0) < 1e-12:
            break
    return h


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else ""


def episodes(run: Path, contract: str, policy: str) -> dict:
    """{episode: clean pp} of the VALID cells of one group."""
    out = {}
    f = run / "runtime_episodes.csv"
    if not f.exists():
        return out
    with open(f, newline="") as fh:
        for r in csv.DictReader(fh):
            if r["contract"] == contract and r["policy"] == policy and r["cell_status"].startswith("VALID") and float(r["offered"] or 0) > 0:
                out[int(r["episode"])] = 100.0 * float(r["clean_fulfilled"]) / float(r["offered"])
    return out


def window_pair(label, what, a_run: Path, a, b_run: Path, b) -> dict:
    A, B = episodes(a_run, a[1], a[2]), episodes(b_run, b[1], b[2])
    common = sorted(set(A) & set(B))
    same = [e for e in common if _sha(a_run / f"schedule_ep{e}.json") and _sha(a_run / f"schedule_ep{e}.json") == _sha(b_run / f"schedule_ep{e}.json")]
    rng = random.Random(SEED)
    res = {"label": label, "what": what, "from": f"{a_run.parent.name}/{a_run.name} {a[1]}/{a[2]}", "to": f"{b_run.parent.name}/{b_run.name} {b[1]}/{b[2]}",
           "from_mean": statistics.mean(A.values()) if A else None, "to_mean": statistics.mean(B.values()) if B else None,
           "n_from": len(A), "n_to": len(B)}
    if same and len(same) == len(common) and len(same) >= 2:
        d = [B[e] - A[e] for e in same]
        m, sd, n = statistics.mean(d), statistics.stdev(d), len(d)
        h = t_q975(n - 1) * sd / math.sqrt(n)
        boots = sorted(statistics.mean(rng.choice(d) for _ in d) for _ in range(BOOT))
        res.update(method="paired", n=n, mean=m, t95=[m - h, m + h], boot95=[boots[int(0.025 * (BOOT - 1))], boots[int(round(0.975 * (BOOT - 1)))]],
                   episodes=same)
    elif len(A) >= 2 and len(B) >= 2:
        xa, xb = list(A.values()), list(B.values())
        va, vb = statistics.variance(xa) / len(xa), statistics.variance(xb) / len(xb)
        m = statistics.mean(xb) - statistics.mean(xa)
        se = math.sqrt(va + vb)
        df = (va + vb) ** 2 / ((va ** 2 / (len(xa) - 1)) + (vb ** 2 / (len(xb) - 1))) if va + vb > 0 else len(xa) + len(xb) - 2
        h = t_q975(df) * se
        boots = sorted(statistics.mean(rng.choice(xb) for _ in xb) - statistics.mean(rng.choice(xa) for _ in xa) for _ in range(BOOT))
        res.update(method="welch", n=[len(xa), len(xb)], mean=m, t95=[m - h, m + h], welch_df=df,
                   boot95=[boots[int(0.025 * (BOOT - 1))], boots[int(round(0.975 * (BOOT - 1)))]],
                   why="the two runs' schedule files differ" if common else "no common episode")
    else:
        res.update(method="not available", why="fewer than two VALID episodes in one of the runs")
    return res


def earliest(runs: list, name: str):
    """The earliest complete run of a campaign (as the report selects the preregistered one)."""
    c = sorted((r for r in runs if r.parent.name == name and (r / "runtime_episodes.csv").exists()
                and ("finished" in _json(r / "manifest.json") or (r / "activation.json").exists())), key=lambda r: r.name)
    return c[0] if c else None


def _json(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def window_ci(runs: list) -> dict:
    rows = []
    for label, a, b, what in WINDOW_PAIRS:
        ra, rb = earliest(runs, a[0]), earliest(runs, b[0])
        if ra is None or rb is None:
            rows.append({"label": label, "what": what, "method": "not available",
                         "why": f"run(s) not among the sources: {', '.join(x for x, r in ((a[0], ra), (b[0], rb)) if r is None)}"})
            continue
        rows.append(window_pair(label, what, ra, a, rb, b))
    return {"pairs": rows}


def greedy_count(values: list, bound: int) -> int:
    tot, n = 0, 0
    for v in sorted(values):
        if tot + v > bound:
            break
        tot += v
        n += 1
    return n


def offline_bound(runs: list) -> dict:
    rows, regimes = [], {}
    for run in runs:
        cfg = _json(run / "config.json")
        w = cfg.get("workload") or {}
        if not w.get("bound") or not (run / "runtime_episodes.csv").exists():
            continue
        eps = {}
        for f in sorted(run.glob("schedule_ep*.json")):
            arr = _json(f).get("arrivals") or []
            if not arr:
                continue
            by = {}
            for x in arr:
                by.setdefault(x["device"], []).append(int(x["value"]))
            fit = sum(greedy_count(v, int(w["bound"])) for v in by.values())
            eps[int(f.stem.replace("schedule_ep", ""))] = {"offered": len(arr), "fit": fit, "share_pct": 100.0 * fit / len(arr)}
        groups, planned = {}, ST.planned_unsafe(cfg)
        with open(run / "runtime_episodes.csv", newline="") as fh:
            for r in csv.DictReader(fh):
                if not r["cell_status"].startswith("VALID") or float(r["offered"] or 0) <= 0:
                    continue
                if ST.kind(cfg, r["contract"], r["policy"], planned) != "guarded":
                    continue
                groups.setdefault(f"{r['contract']}/{r['policy']}", {})[int(r["episode"])] = 100.0 * float(r["clean_fulfilled"]) / float(r["offered"])
        ran = sorted({e for g in groups.values() for e in g} & set(eps))
        if not ran:
            continue
        best_g, best = None, None
        for g, v in groups.items():
            m = statistics.mean(v.values())
            if best is None or m > best:
                best_g, best = g, m
        bound = statistics.mean(eps[e]["share_pct"] for e in ran)
        key = (cfg.get("seed"), w.get("rate_per_s"), w.get("value_min"), w.get("value_max"), w.get("bound"), cfg.get("duration_s"), cfg.get("devices"))
        row = {"campaign": cfg.get("name", run.parent.name), "run": run.name, "regime": list(key), "episodes": len(ran), "bound_pct": bound,
               "bound_min_pct": min(eps[e]["share_pct"] for e in ran), "bound_max_pct": max(eps[e]["share_pct"] for e in ran),
               "best_group": best_g, "best_clean_pct": best, "gap_pp": bound - best}
        rows.append(row)
        regimes.setdefault(json.dumps(list(key)), []).append(row)
    reg = []
    for k, rs in sorted(regimes.items()):
        b = max(rs, key=lambda r: r["best_clean_pct"])
        reg.append({"regime": json.loads(k), "campaigns": len(rs), "bound_pct": statistics.mean(r["bound_pct"] for r in rs),
                    "best_clean_pct": b["best_clean_pct"], "best_campaign": b["campaign"], "best_group": b["best_group"]})
    return {"rows": rows, "regimes": reg}


def _n(x, nd=1):
    return "-" if x is None else f"{x:+.{nd}f}" if nd < 0 else f"{x:.{nd}f}"


def markdown(w: dict, ob: dict) -> str:
    L = ["# Report analyses of release 085 (reviewer of 4 November)", "", "## The effect of removing the window on the firmware (review section 8)", "",
         "Clean fulfilment, pp of offered, EVICON. Paired: per-episode differences on byte-identical schedule files (Student t and paired "
         "bootstrap); welch: unpaired (Welch t and two-sample bootstrap), used where the schedules differ.", "",
         "| pair | from -> to | levels | difference | 95 % t | 95 % bootstrap | method, n |", "|---|---|---|---|---|---|---|"]
    for r in w["pairs"]:
        if r.get("mean") is None:
            L.append(f"| {r['label']} | {r['what']} | - | - | - | - | {r['method']}: {r.get('why', '')} |")
            continue
        L.append(f"| {r['label']} | {r['from']} -> {r['to']} | {r['from_mean']:.2f} -> {r['to_mean']:.2f} | {r['mean']:+.2f} | "
                 f"[{r['t95'][0]:+.2f}, {r['t95'][1]:+.2f}] | [{r['boot95'][0]:+.2f}, {r['boot95'][1]:+.2f}] | {r['method']}, {r['n']} |")
    L += ["", "## Offline upper bound on clean fulfilment (review N7)", "",
          "Per domain and episode: the largest number of offered requests whose sizes fit the domain's bound (smallest first), as a share "
          "of the offered requests; faults and deadlines ignored. An upper bound on the request-count clean fulfilment of any safe policy "
          "for that workload (every fulfilled request adds its size once; the sum stays within the bound).", "",
          "| campaign | episodes | bound % (mean; min-max) | best observed clean % (guarded) | best group | gap (pp) |", "|---|---|---|---|---|---|"]
    for r in ob["rows"]:
        L.append(f"| {r['campaign']} | {r['episodes']} | {r['bound_pct']:.1f} ({r['bound_min_pct']:.1f}-{r['bound_max_pct']:.1f}) | {r['best_clean_pct']:.1f} | "
                 f"{r['best_group']} | {r['gap_pp']:.1f} |")
    L += ["", "Per regime (seed, rate per s, sizes, bound, duration, devices):", "", "| regime | campaigns | bound % | best clean % | in |", "|---|---|---|---|---|"]
    for r in ob["regimes"]:
        L.append(f"| {r['regime']} | {r['campaigns']} | {r['bound_pct']:.1f} | {r['best_clean_pct']:.1f} | {r['best_campaign']} {r['best_group']} |")
    return "\n".join(L) + "\n"


def summary(w: dict, ob: dict) -> dict:
    out = {"window_ci_available": sum(1 for r in w["pairs"] if r.get("mean") is not None), "offline_bound_campaigns": len(ob["rows"])}
    if ob["rows"]:
        out["offline_bound_min_pct"] = min(r["bound_pct"] for r in ob["rows"])
        out["offline_bound_max_pct"] = max(r["bound_pct"] for r in ob["rows"])
        out["offline_best_clean_max_pct"] = max(r["best_clean_pct"] for r in ob["rows"])
        out["offline_gap_min_pp"] = min(r["gap_pp"] for r in ob["rows"])
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--campaign", action="append", default=[])
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    runs = []
    for c in a.campaign:
        p = Path(c).resolve()
        if p not in runs and p.is_dir():
            runs.append(p)
    w, ob = window_ci(runs), offline_bound(runs)
    out = Path(a.out)
    for sub, d in (("window_ci", w), ("offline_bound", ob)):
        (out / sub).mkdir(parents=True, exist_ok=True)
        (out / sub / f"{sub}.json").write_text(json.dumps(d, indent=1, default=str) + "\n")
    (out / "l2_085.json").write_text(json.dumps(summary(w, ob), indent=1) + "\n")
    (out / "L2_085.md").write_text(markdown(w, ob))
    print(f"review_l2_085: window pairs {summary(w, ob)['window_ci_available']} of {len(w['pairs'])}, offline bound over {len(ob['rows'])} campaigns -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
