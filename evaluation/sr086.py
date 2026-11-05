#!/usr/bin/env python3
"""Estimands E-SR of the second-seed replication (release 086, PREREG_extra_086.md section 4). Decides nothing: the
rules SR-1 to SR-4 are decided by pairs_report.py from the plan's pairs-spec.

Per contrast (clean fulfilment, pp of offered, EVICON, VALID cells):
  replication  the within-run per-episode differences of the SR run (seed 20261105): mean, Student t 95 % interval and a
               paired bootstrap percentile interval (10,000 resamples, seed 86);
  original     the same for the original contrast (SR-1: C, MINIO_PLAIN -> MINIO_COND; SR-2: Rm; SR-3: T4-3, Z6's plain at
               QoS 0 -> C3's {F}, episodes paired by their byte-identical schedule files; SR-4: T4-4, Zi -> Rz), and the
               within-run original of the firmware contrasts next to it (C3 and Rz: plain at QoS 1 -> {F}; U-2, C3-1);
  seeds        replication minus original: the difference of the two means with a Welch t 95 % interval (the two
               seeds' schedules differ, so the episodes are not paired) and a two-sample bootstrap of the per-episode
               differences (10,000, seed 86);
  ratio        the replication's estimate as a share of the original's (the margin of SR-1 to SR-4 is half of it);
  levels       the per-episode levels of the two contracts in the SR run and in the original runs.

    python3 evaluation/sr086.py --run SRm6=<dir> --run C=<dir> ... --out <dir>
Writes SR086.md and sr086.json. Runs not given are "not available".
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from review_l2_085 import episodes, t_q975  # noqa: E402

BOOT, SEED = 10000, 86
# id, label, replication (run key, from contract, to contract), original (from run, from contract, to run, to contract),
# within-run original of the firmware contrasts (run, from, to) or None
CONTRASTS = [
    ("SR-1", "MinIO, W = 6: conditional PUT with stop vs plain PUT", ("SRm6", "MINIO_PLAIN", "MINIO_COND"), ("C", "MINIO_PLAIN", "C", "MINIO_COND"), None),
    ("SR-2", "MinIO, W unbounded", ("SRmi", "MINIO_PLAIN", "MINIO_COND"), ("Rm", "MINIO_PLAIN", "Rm", "MINIO_COND"), None),
    ("SR-3", "firmware, W = 6: {F} vs plain at QoS 0, direct", ("SRz6", "ZEPHYR_PLAIN_DIRECT_QOS0", "ZEPHYR_F_DIRECT"),
     ("Z6", "ZEPHYR_PLAIN_DIRECT_QOS0", "C3", "ZEPHYR_F_DIRECT"), ("C3", "ZEPHYR_PLAIN_DIRECT", "ZEPHYR_F_DIRECT")),
    ("SR-4", "firmware, W unbounded", ("SRzi", "ZEPHYR_PLAIN_DIRECT_QOS0", "ZEPHYR_F_DIRECT"),
     ("Zi", "ZEPHYR_PLAIN_DIRECT_QOS0", "Rz", "ZEPHYR_F_DIRECT"), ("Rz", "ZEPHYR_PLAIN_DIRECT", "ZEPHYR_F_DIRECT")),
]


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else ""


def paired(a_run: Path, a: str, b_run: Path, b: str, policy: str = "EVICON") -> dict:
    A, B = episodes(a_run, a, policy), episodes(b_run, b, policy)
    common = sorted(set(A) & set(B))
    if a_run != b_run:  # across runs: only episodes with byte-identical schedule files are pairs
        common = [e for e in common if _sha(a_run / f"schedule_ep{e}.json") and _sha(a_run / f"schedule_ep{e}.json") == _sha(b_run / f"schedule_ep{e}.json")]
    d = [B[e] - A[e] for e in common]
    res = {"from": f"{a_run.parent.name}/{a_run.name} {a}", "to": f"{b_run.parent.name}/{b_run.name} {b}", "n": len(d), "episodes": common,
           "diffs": [round(x, 4) for x in d], "from_levels": [round(A[e], 4) for e in common], "to_levels": [round(B[e], 4) for e in common]}
    if len(d) < 2:
        res["mean"] = statistics.mean(d) if d else None
        return res
    m, sd, n = statistics.mean(d), statistics.stdev(d), len(d)
    h = t_q975(n - 1) * sd / math.sqrt(n)
    rng = random.Random(f"{SEED}/{res['from']}/{res['to']}")
    boots = sorted(statistics.mean(rng.choice(d) for _ in d) for _ in range(BOOT))
    res.update(mean=m, sd=sd, t95=[m - h, m + h], boot95=[boots[int(0.025 * (BOOT - 1))], boots[int(round(0.975 * (BOOT - 1)))]])
    return res


def seeds(rep: dict, orig: dict, key: str) -> dict:
    xa, xb = orig.get("diffs") or [], rep.get("diffs") or []
    if len(xa) < 2 or len(xb) < 2:
        return {"method": "not available"}
    va, vb = statistics.variance(xa) / len(xa), statistics.variance(xb) / len(xb)
    m = statistics.mean(xb) - statistics.mean(xa)
    se = math.sqrt(va + vb)
    df = (va + vb) ** 2 / ((va ** 2 / (len(xa) - 1)) + (vb ** 2 / (len(xb) - 1))) if va + vb > 0 else len(xa) + len(xb) - 2
    h = t_q975(df) * se
    rng = random.Random(f"{SEED}/{key}/seeds")
    boots = sorted(statistics.mean(rng.choice(xb) for _ in xb) - statistics.mean(rng.choice(xa) for _ in xa) for _ in range(BOOT))
    return {"method": "welch", "mean": m, "t95": [m - h, m + h], "welch_df": df, "boot95": [boots[int(0.025 * (BOOT - 1))], boots[int(round(0.975 * (BOOT - 1)))]]}


def analyse(runs: dict) -> dict:
    out = []
    for cid, label, (rk, rf, rt), (ofr, of, otr, ot), within in CONTRASTS:
        row = {"id": cid, "label": label}
        if rk in runs:
            row["replication"] = paired(runs[rk], rf, runs[rk], rt)
        else:
            row["replication"] = {"n": 0, "why": f"run {rk} not available"}
        if ofr in runs and otr in runs:
            row["original"] = paired(runs[ofr], of, runs[otr], ot)
        else:
            row["original"] = {"n": 0, "why": f"runs {ofr}/{otr} not available"}
        if within and within[0] in runs:
            row["original_within_run"] = paired(runs[within[0]], within[1], runs[within[0]], within[2])
        row["seeds"] = seeds(row["replication"], row["original"], cid)
        r, o = row["replication"].get("mean"), row["original"].get("mean")
        row["ratio"] = (r / o) if (r is not None and o) else None
        out.append(row)
    return {"contrasts": out, "runs": {k: f"{v.parent.name}/{v.name}" for k, v in sorted(runs.items())}}


def _ci(v):
    return f"[{v[0]:+.2f}, {v[1]:+.2f}]" if v else "-"


def _m(x):
    return f"{x:+.2f}" if isinstance(x, (int, float)) else "-"


def markdown(res: dict) -> str:
    L = ["# Second-seed replication: estimands E-SR (PREREG_extra_086.md section 4)", "",
         "Clean fulfilment, pp of offered, EVICON. Replication: the SR run (seed 20261105), within run, episodes paired. Original: "
         "the headline contrast of the paper (MinIO within run; firmware across runs on byte-identical schedules). Seeds: replication "
         "minus original, Welch t and a two-sample bootstrap (the schedules differ). The rules SR-1 to SR-4 are decided by "
         "pairs_report.py (pairs/PREREG_extra_086/RESULTS.md).", "",
         "| ID | contrast | replication (n) | t 95 % | bootstrap 95 % | original (n) | t 95 % | seeds: replication - original | Welch t 95 % | ratio |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for c in res["contrasts"]:
        r, o, s = c["replication"], c["original"], c["seeds"]
        ratio = f"{c['ratio']:.2f}" if c["ratio"] is not None else "-"
        L.append(f"| {c['id']} | {c['label']} | {_m(r.get('mean'))} ({r.get('n', 0)}) | {_ci(r.get('t95'))} | {_ci(r.get('boot95'))} | "
                 f"{_m(o.get('mean'))} ({o.get('n', 0)}) | {_ci(o.get('t95'))} | {_m(s.get('mean'))} | {_ci(s.get('t95'))} | {ratio} |")
    L.append("")
    for c in res["contrasts"]:
        w = c.get("original_within_run")
        if w:
            L.append(f"- {c['id']}: the within-run original (plain firmware at QoS 1 -> {{F}}, {w['to']}): {_m(w.get('mean'))} {_ci(w.get('t95'))}, n = {w.get('n', 0)}")
    L += ["", "Runs: " + ", ".join(f"{k} `{v}`" for k, v in res["runs"].items()), ""]
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", action="append", default=[], help="KEY=run dir")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    runs = {}
    for x in a.run:
        k, _, v = x.partition("=")
        if v and Path(v).is_dir():
            runs[k] = Path(v)
    res = analyse(runs)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "sr086.json").write_text(json.dumps(res, indent=1) + "\n")
    (out / "SR086.md").write_text(markdown(res))
    print(f"sr086: {sum(1 for c in res['contrasts'] if c['replication'].get('n'))} replication contrast(s), "
          f"{sum(1 for c in res['contrasts'] if c['original'].get('n'))} original(s) -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
