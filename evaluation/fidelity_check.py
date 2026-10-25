#!/usr/bin/env python3
"""Emulated owner against real owner at the same configuration (release 074).

Every runtime experiment before C drove evicon-device, an emulated owner with a contract chosen by
preset. Experiments C (MinIO) and C2 (Zephyr firmware behind Mosquitto) run the same authority, workload,
faults, window and channel against real owners. Where a real owner's measured contract equals an
emulated preset (MinIO PUT = K0; the Zephyr firmware's plain mode = K0), the two runs ask the same
question of different owners, and their agreement measures how faithful the emulated testbed is. Where
the real contract only adds capabilities that the runtime cannot use under an unbounded network delay
(D_h; release 073 plan, section 1), the comparison is reported too, marked "contract differs".

For each pair (real run, contract, policy) ~ (emulated run, contract, policy), with clean and confirmed
fulfilment per episode from the frozen pairs_report.py:
  unpaired  difference of means, Welch 95 % interval, TOST at +-5 and +-10 pp (the runs used different
            seeds, e.g. C against expW-direct-w6)
  paired    when both runs replayed the same schedule in an episode (same seed and fault parameters,
            schedule_ep<k>.json byte-identical: C2 against expW-queue-w6 and expS-w6), the per-episode
            differences, t interval and TOST
Exploratory for C (decided after the C run); for C2 the same comparisons are estimands of its plan.

    python3 evaluation/fidelity_check.py --out <dir> --pair <real run>:<contract>:<policy>=<emulated run>:<contract>:<policy> ...
Writes FIDELITY.md and fidelity.json.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from cadence_report import t_interval  # noqa: E402  (frozen; reused)
from pairs_report import load_run  # noqa: E402  (frozen; reused)

METRICS = ("clean_pp", "confirmed_pp")
MARGINS = (5.0, 10.0)


def _tcdf(t: float, df: float) -> float:
    """Student t CDF (regularized incomplete beta, continued fraction)."""
    x = df / (df + t * t)
    a, b = df / 2.0, 0.5

    def betacf(a, b, x):
        qab, qap, qam = a + b, a + 1.0, a - 1.0
        c, d = 1.0, 1.0 - qab * x / qap
        d = 1.0 / (d if abs(d) > 1e-300 else 1e-300)
        h = d
        for m in range(1, 300):
            m2 = 2 * m
            aa = m * (b - m) * x / ((qam + m2) * (a + m2))
            d = 1.0 + aa * d
            d = 1.0 / (d if abs(d) > 1e-300 else 1e-300)
            c = 1.0 + aa / c if abs(c) > 1e-300 else 1e300
            h *= d * c
            aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
            d = 1.0 + aa * d
            d = 1.0 / (d if abs(d) > 1e-300 else 1e-300)
            c = 1.0 + aa / c if abs(c) > 1e-300 else 1e300
            de = d * c
            h *= de
            if abs(de - 1.0) < 1e-12:
                break
        return h
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(lbeta + a * math.log(x) + b * math.log(1.0 - x)) / a if 0 < x < 1 else 0.0
    ib = front * betacf(a, b, x) if x < (a + 1) / (a + b + 2) else 1.0 - (math.exp(lbeta + b * math.log(1 - x) + a * math.log(x)) / b) * betacf(b, a, 1 - x)
    p = 0.5 * ib
    return 1.0 - p if t > 0 else p


def _tq(p: float, df: float) -> float:
    lo, hi = 0.0, 100.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if _tcdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def welch(a: list, b: list) -> dict:
    """b - a, unpaired."""
    if len(a) < 2 or len(b) < 2:
        return {}
    va, vb = statistics.variance(a) / len(a), statistics.variance(b) / len(b)
    d = statistics.mean(b) - statistics.mean(a)
    se = math.sqrt(va + vb)
    if se == 0:
        return {"diff": round(d, 3), "t95": [round(d, 3), round(d, 3)], "tost": {str(m): 0.0 if abs(d) < m else 1.0 for m in MARGINS}}
    df = (va + vb) ** 2 / (va ** 2 / (len(a) - 1) + vb ** 2 / (len(b) - 1))
    q = _tq(0.975, df)
    tost = {str(m): round(max(1 - _tcdf((d + m) / se, df), _tcdf((d - m) / se, df)), 4) for m in MARGINS}
    return {"diff": round(d, 3), "t95": [round(d - q * se, 3), round(d + q * se, 3)], "df": round(df, 1), "tost": tost}


def paired(d: list) -> dict:
    if len(d) < 2:
        return {}
    m, se = statistics.mean(d), statistics.stdev(d) / math.sqrt(len(d))
    ci = t_interval(d, 0.95)
    df = len(d) - 1
    tost = {str(x): (round(max(1 - _tcdf((m + x) / se, df), _tcdf((m - x) / se, df)), 4) if se > 0 else (0.0 if abs(m) < x else 1.0))
            for x in MARGINS}
    return {"diff": round(m, 3), "t95": [round(ci[0], 3), round(ci[1], 3)], "n": len(d), "tost": tost}


def same_schedule(ra: Path, rb: Path, ep: int) -> bool:
    fa, fb = ra / f"schedule_ep{ep}.json", rb / f"schedule_ep{ep}.json"
    return fa.is_file() and fb.is_file() and fa.read_bytes() == fb.read_bytes()


def parse(spec: str):
    real, emu = spec.split("=", 1)
    r = real.rsplit(":", 2)
    e = emu.rsplit(":", 2)
    if len(r) != 3 or len(e) != 3:
        raise SystemExit(f"--pair {spec}: expected <run>:<contract>:<policy>=<run>:<contract>:<policy>")
    return (Path(r[0]), r[1], r[2]), (Path(e[0]), e[1], e[2])


def compare(pair, cache: dict) -> dict:
    (rr, rc, rp), (er, ec, ep) = pair
    for p in (rr, er):
        if p not in cache:
            cache[p] = load_run(p.name, p)
    R, E = cache[rr], cache[er]
    out = {"real": {"run": f"{R['name']}/{rr.name}", "contract": rc, "policy": rp},
           "emulated": {"run": f"{E['name']}/{er.name}", "contract": ec, "policy": ep}, "metrics": {}}
    for m in METRICS:
        a = {e: c[m] for (ct, pol, e), c in E["cells"].items() if ct == ec and pol == ep and c[m] == c[m]}
        b = {e: c[m] for (ct, pol, e), c in R["cells"].items() if ct == rc and pol == rp and c[m] == c[m]}
        res = {"real_mean": round(statistics.mean(b.values()), 3) if b else None, "real_n": len(b),
               "emulated_mean": round(statistics.mean(a.values()), 3) if a else None, "emulated_n": len(a),
               "unpaired": welch(list(a.values()), list(b.values()))}
        common = sorted(e for e in set(a) & set(b) if same_schedule(rr, er, e))
        if common:
            res["paired"] = paired([b[e] - a[e] for e in common])
            res["paired"]["episodes"] = common
        out["metrics"][m] = res
    return out


def markdown(res: list) -> str:
    L = ["# Emulated owner against real owner, same configuration", "",
         "Difference = real minus emulated, in pp of offered. TOST p < 0.05: equivalent within the margin. Paired: only episodes "
         "whose fault and arrival schedule were byte-identical in both runs.", "",
         "| real | emulated | metric | real mean (n) | emulated mean (n) | unpaired diff [95 %] | TOST +-5 / +-10 | paired diff [95 %] (n) | TOST +-5 / +-10 |",
         "|---|---|---|---|---|---|---|---|---|"]
    for r in res:
        for m, x in r["metrics"].items():
            u, p = x.get("unpaired") or {}, x.get("paired") or {}

            def fmt(v):
                return f"{v['diff']:+.1f} [{v['t95'][0]:+.1f}, {v['t95'][1]:+.1f}]" if v else "--"

            def tost(v):
                return f"{v['tost']['5.0']} / {v['tost']['10.0']}" if v else "--"
            L.append(f"| {r['real']['run']} {r['real']['contract']}/{r['real']['policy']} | {r['emulated']['run']} "
                     f"{r['emulated']['contract']}/{r['emulated']['policy']} | {m} | {x['real_mean']} ({x['real_n']}) | "
                     f"{x['emulated_mean']} ({x['emulated_n']}) | {fmt(u)} | {tost(u)} | {fmt(p)}{' (' + str(p['n']) + ')' if p else ''} | {tost(p)} |")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--pair", action="append", required=True)
    a = ap.parse_args(argv)
    cache = {}
    res = [compare(parse(p), cache) for p in a.pair]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "fidelity.json").write_text(json.dumps(res, indent=1) + "\n")
    (out / "FIDELITY.md").write_text(markdown(res))
    print(f"fidelity_check: {len(res)} pairs -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
