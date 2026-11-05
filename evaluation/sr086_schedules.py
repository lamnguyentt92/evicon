#!/usr/bin/env python3
"""The new seed of the second-seed replication (release 086, PREREG_extra_086.md section 2): its fault schedules and
arrivals differ from every earlier one.

Every campaign config of every release tree given (`--tree`, e.g. the release trees 062-085 and this one) is replayed
through the frozen `evaluation/campaign.schedule(cfg, episode)` for its episodes (at least 8), and each episode's
arrivals (time, device, sequence, size) and fault windows (time, device, kind, duration) are hashed. Configs whose
schedule-defining fields are equal (seed, devices, duration, workload rate and sizes, fault parameters) give the same
schedules and are listed once. The four SR configs of plan 086 (seed 20261105) are then compared with every earlier
schedule: an identical fault schedule or arrival sequence of any episode is reported (expected: none), and the SR
episodes among themselves.

    python3 evaluation/sr086_schedules.py --tree <release tree> ... --out evaluation/sim_predictions/extra086_schedules.csv
Prints the summary; exit 1 if an SR schedule equals an earlier one.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import campaign as C  # noqa: E402  (frozen: the schedule every runner replays)

SR = ("expSR-minio-w6", "expSR-minio-winf", "expSR-zephyr-w6", "expSR-zephyr-winf")


def signature(cfg: dict) -> str:
    w, f = cfg.get("workload", {}), cfg.get("faults", {})
    key = {"seed": cfg.get("seed"), "devices": cfg.get("devices"), "duration_s": cfg.get("duration_s"),
           "w": {k: w.get(k) for k in ("rate_per_s", "value_min", "value_max")}, "f": f}
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:16]


def digests(cfg: dict, ep: int):
    arr, win = C.schedule(cfg, ep)
    a = hashlib.sha256(json.dumps([(round(x.t, 6), x.device, x.seq, x.value) for x in arr]).encode()).hexdigest()[:20]
    f = hashlib.sha256(json.dumps([(x.t, x.device, x.kind, x.dur) for x in win]).encode()).hexdigest()[:20]
    return a, f, len(arr), len(win)


def scan(trees: list) -> dict:
    """signature -> {"configs": [...], "seed", "episodes": {ep: (arrivals digest, faults digest, n arrivals, n windows)}}"""
    sigs = {}
    for tree in trees:
        for p in sorted(Path(tree).glob("evaluation/campaigns/*.json")):
            try:
                cfg = json.loads(p.read_text())
                if "seed" not in cfg or "workload" not in cfg or "devices" not in cfg:
                    continue
                sig = signature(cfg)
                s = sigs.setdefault(sig, {"configs": [], "seed": cfg["seed"], "episodes": {}, "sr": cfg.get("name") in SR})
                s["configs"].append(f"{Path(tree).name}/{p.name}")
                for ep in range(max(8, int(cfg.get("episodes", 8)))):
                    if ep not in s["episodes"]:
                        s["episodes"][ep] = digests(cfg, ep)
            except Exception as e:  # noqa: BLE001  an old config this schedule() cannot replay is reported, not fatal
                print(f"skipped {p}: {e}", file=sys.stderr)
    return sigs


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tree", action="append", default=[])
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    sigs = scan(a.tree)
    sr = {k: v for k, v in sigs.items() if v["sr"]}
    old = {k: v for k, v in sigs.items() if not v["sr"]}
    old_a = {d[0] for v in old.values() for d in v["episodes"].values()}
    old_f = {d[1] for v in old.values() for d in v["episodes"].values() if d[3] > 0}
    same_a = [(c, ep) for v in sr.values() for ep, d in v["episodes"].items() if d[0] in old_a for c in v["configs"]]
    same_f = [(c, ep) for v in sr.values() for ep, d in v["episodes"].items() if d[1] in old_f for c in v["configs"]]
    sr_f = [d[1] for v in sr.values() for ep, d in v["episodes"].items() if ep < 8]
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow(["signature", "seed", "sr", "episode", "arrivals_sha", "faults_sha", "arrivals", "fault_windows", "configs"])
        for sig in sorted(sigs, key=lambda k: (not sigs[k]["sr"], str(sigs[k]["seed"]), k)):
            v = sigs[sig]
            for ep in sorted(v["episodes"]):
                d = v["episodes"][ep]
                wr.writerow([sig, v["seed"], int(v["sr"]), ep, d[0], d[1], d[2], d[3], ";".join(v["configs"]) if ep == 0 else ""])
    seeds = sorted({str(v["seed"]) for v in old.values()})
    print(f"sr086_schedules: {sum(len(v['configs']) for v in old.values())} earlier configs in {len(a.tree)} trees, {len(old)} distinct schedule "
          f"sets (seeds {', '.join(seeds)}), {sum(len(v['episodes']) for v in old.values())} earlier episode schedules; SR: {len(sr)} schedule "
          f"set(s) of seed {sorted({str(v['seed']) for v in sr.values()})}: identical arrivals {len(same_a)}, identical fault schedules {len(same_f)}, "
          f"distinct SR fault schedules {len(set(sr_f))} of {len(sr_f)} -> {out}")
    return 1 if same_a or same_f else 0


if __name__ == "__main__":
    sys.exit(main())
