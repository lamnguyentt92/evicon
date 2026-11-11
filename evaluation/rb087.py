#!/usr/bin/env python3
"""The estimands E-RB and the check RB-C1 of PREREG_extra_087.md item B (reviewer comment M3; no claims besides RB-C1's
count): owner reboots as a campaign fault on the boot-report firmware (run RBz6), three arms:
  (i)   NEG_ZEPHYR_F_BOOT / EVICON_FI_REC_OS   the recovery with the owner-state anchor (negative control)
  (ii)  ZEPHYR_F_BOOT / EVICON_FI              release 086 (the evidence anchor, no recovery, no gateway seal)
  (iii) ZEPHYR_F_BOOT / EVICON_FI_REC          the recovery REC-1 to REC-4 with the gateway seal GW-3

Per reboot (the cell's reboots.jsonl; times on the driver host's clock, as the clients' log): the device's admissions from
its restart (first answer) to its next reboot or the end of the cell, as a count and in pp of the device's arrivals in that
interval (it can exceed 100: the backlog queued during the downtime and the reissues of stopped intents are
admitted then too); those that took effect (the plant's log); the time from the restart to the first admission and to the first
admission that took effect. Per cell: identities left unresolved at the end and the budget they hold (sum of values), by
whether the plant shows an effect (from the authorities' journals; the clients' last seen status only when a lean
archive keeps no journal); clean fulfilment; the trace counts of the recovery (OWNER_FLOOR, SEQUENCE_ADVANCED, the
FENCE_ANCHOR_CHECK outcomes, EVIDENCE_CONFLICT, BARRIER_ANCHOR_COMMITTED) and the gateway's seals (GW-3).
RB-C1 (check, plan section 3): the number of FENCE_ANCHOR_CHECK outcomes MISMATCH_FLOOR in arm (iii); predicted 0.
Across runs (informational): arm (iii) against SRz6's ZEPHYR_F_DIRECT / EVICON cells on byte-identical schedules (the
cost of the reboots).

    python3 evaluation/rb087.py --run RBz6=<run> [--ref SRz6=<run>] --out <dir>
Writes RB087.md and rb087.json.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pairs_report as PR  # noqa: E402  (frozen since release 072; imported, never edited)

ARMS = (("NEG_ZEPHYR_F_BOOT", "EVICON_FI_REC_OS", "(i) negative control"), ("ZEPHYR_F_BOOT", "EVICON_FI", "(ii) release 086"),
        ("ZEPHYR_F_BOOT", "EVICON_FI_REC", "(iii) recovery"))
TRACE_EVENTS = ("OWNER_FLOOR", "SEQUENCE_ADVANCED", "EVIDENCE_CONFLICT", "BARRIER_ANCHOR_COMMITTED", "REBOOT_RECOVERY_CONFIGURATION")


def jl(p: Path) -> list:
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(errors="replace").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def plant_counts(celldir: Path) -> Counter:
    c = Counter()
    p = PR.plant_log(celldir)
    for e in jl(p) if p else []:
        if e.get("cmd") is not None:
            c[e["cmd"]] += 1
    return c


def trace_counts(celldir: Path) -> dict:
    ev, checks = Counter(), Counter()
    import sh087  # one copy of each file (a simulated testbed fetches the same file once per VM directory)
    for tr in sh087.one_copy(celldir, "authority*.jsonl"):
        with open(tr, errors="replace") as fh:
            for line in fh:
                if not any(k in line for k in TRACE_EVENTS + ("FENCE_ANCHOR_CHECK",)):
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                name = e.get("event")
                if name == "FENCE_ANCHOR_CHECK":
                    checks[str(e.get("outcome", ""))] += 1
                elif name in TRACE_EVENTS:
                    ev[name] += 1
    seals = 0
    for g in sh087.one_copy(celldir, "gateway*.jsonl"):
        seals += sum(1 for e in jl(g) if e.get("op") == "seal" and e.get("fresh"))
    return {"events": dict(ev), "fence_anchor_check": dict(checks), "gateway_seals": seals}


def per_reboot(celldir: Path, deadline_s: float) -> tuple:
    clients = jl(celldir / "clients.jsonl")
    reboots = [r for r in jl(celldir / "reboots.jsonl")]
    eff = plant_counts(celldir)
    end = max((float(c["arrival"]) + deadline_s for c in clients), default=0.0)
    out = []
    for r in sorted(reboots, key=lambda x: (x.get("device", 0), x.get("killed_at", 0))):
        d = r.get("device")
        if not r.get("ok") or r.get("first_answer_at") is None:
            out.append({"device": d, "kind": r.get("kind"), "ok": False, "detail": r.get("detail", "")})
            continue
        t0 = float(r["first_answer_at"])
        later = [float(x["killed_at"]) for x in reboots if x.get("device") == d and x.get("killed_at") and float(x["killed_at"]) > t0]
        t1 = min(later) if later else end
        arr = sum(1 for c in clients if c.get("device") == d and t0 <= float(c["arrival"]) < t1)
        adm = sorted((float(t), cid) for c in clients if c.get("device") == d for cid, t in (c.get("admitted") or {}).items() if t0 <= float(t) < t1)
        eff_adm = [(t, cid) for t, cid in adm if eff.get(cid, 0) > 0]
        out.append({"device": d, "kind": r.get("kind"), "ok": True, "planned_t": r.get("t"), "floor": r.get("floor"),
                    "down_s": round(float(r["started_at"]) - float(r["killed_at"]), 3) if r.get("started_at") else None,
                    "interval_s": round(t1 - t0, 3), "arrivals": arr, "admissions": len(adm), "admissions_effective": len(eff_adm),
                    "admitted_pp_of_arrivals": round(100.0 * len(adm) / arr, 2) if arr else None,
                    "s_to_first_admission": round(adm[0][0] - t0, 3) if adm else None,
                    "s_to_first_effective_admission": round(eff_adm[0][0] - t0, 3) if eff_adm else None})
    import sh087  # the journals' view at the end of the cell (the clients stop watching at their deadline)
    jh = sh087.journal_held(celldir, eff)
    if jh is not None:
        return out, jh
    held = {"unresolved": 0, "budget": 0, "unresolved_with_effect": 0, "budget_with_effect": 0}
    for c in clients:
        for cid in c.get("admitted") or {}:
            if (c.get("final_status") or {}).get(cid) not in ("APPLIED", "STOPPED"):
                held["unresolved"] += 1
                held["budget"] += int(c.get("value", 0))
                if eff.get(cid, 0) > 0:
                    held["unresolved_with_effect"] += 1
                    held["budget_with_effect"] += int(c.get("value", 0))
    return out, held


def _r3(x):
    return round(x, 3) if isinstance(x, (int, float)) else x


def analyse(run: Path, ref: Path | None) -> dict:
    cfg = json.loads((run / "config.json").read_text()) if (run / "config.json").exists() else {}
    deadline = float(cfg.get("workload", {}).get("deadline_s", 60))
    cells = defaultdict(dict)
    with open(run / "runtime_episodes.csv", newline="") as fh:
        rows = list(csv.DictReader(fh))
    invalid = []
    for r in rows:
        ep, ct, pol = int(r["episode"]), r["contract"], r["policy"]
        if not r["cell_status"].startswith("VALID"):
            invalid.append(f"ep{ep}/{ct}/{pol}")
            continue
        cd = run / "cells" / f"ep{ep}" / ct / pol
        reb, held = per_reboot(cd, deadline)
        off = max(1, int(r["offered"]))
        cells[(ct, pol)][ep] = {"clean_pp": 100.0 * int(r["clean_fulfilled"]) / off, "admitted_pp": 100.0 * int(r["admitted"]) / off,
                                "safety": {k: int(float(r[k] or 0)) for k in PR.SAFETY}, "late_effects": int(float(r["late_effects"] or 0)),
                                "reboots": reb, "held": held, "traces": trace_counts(cd), "fences": int(float(r["fences"] or 0))}
    res = {"run": str(run), "ref": str(ref) if ref else "", "invalid": invalid, "arms": {}}
    for ct, pol, label in ARMS:
        x = cells.get((ct, pol), {})
        rb = [b for v in x.values() for b in v["reboots"] if b.get("ok")]
        by_kind = {}
        for kind in ("quiet", "random"):
            k = [b for b in rb if b.get("kind") == kind]
            by_kind[kind] = {"reboots": len(k),
                             "admissions_mean": round(statistics.mean([b["admissions"] for b in k]), 2) if k else None,
                             "admissions_effective_mean": round(statistics.mean([b["admissions_effective"] for b in k]), 2) if k else None,
                             "admitted_pp_of_arrivals_mean": round(statistics.mean([b["admitted_pp_of_arrivals"] for b in k
                                                                                    if b["admitted_pp_of_arrivals"] is not None]), 2)
                             if any(b["admitted_pp_of_arrivals"] is not None for b in k) else None,
                             "with_effective_admission": sum(1 for b in k if b["admissions_effective"] > 0),
                             "s_to_first_admission_median": _r3(statistics.median([b["s_to_first_admission"] for b in k if b["s_to_first_admission"] is not None]))
                             if any(b["s_to_first_admission"] is not None for b in k) else None,
                             "s_to_first_effective_admission_median": _r3(statistics.median([b["s_to_first_effective_admission"] for b in k
                                                                                             if b["s_to_first_effective_admission"] is not None]))
                             if any(b["s_to_first_effective_admission"] is not None for b in k) else None}
        checks, ev = Counter(), Counter()
        seals = 0
        for v in x.values():
            checks.update(v["traces"]["fence_anchor_check"])
            ev.update(v["traces"]["events"])
            seals += v["traces"]["gateway_seals"]
        held = Counter()
        for v in x.values():
            held.update(v["held"])
        res["arms"][f"{ct}/{pol}"] = {
            "label": label, "cells": len(x), "clean_pp": PR.summary([v["clean_pp"] for v in x.values()]),
            "admitted_pp": PR.summary([v["admitted_pp"] for v in x.values()]),
            "violation_cells": sum(1 for v in x.values() if any(v["safety"].values())),
            "safety_totals": dict(sum((Counter(v["safety"]) for v in x.values()), Counter())),
            "late_effects": sum(v["late_effects"] for v in x.values()), "reboots_ok": len(rb),
            "reboots_failed": sum(1 for v in x.values() for b in v["reboots"] if not b.get("ok")), "per_kind": by_kind,
            "unresolved_at_end": dict(held), "fence_anchor_check": dict(checks), "events": dict(ev), "gateway_seals": seals,
            "per_cell": {f"ep{e}": {"clean_pp": round(v["clean_pp"], 3), "safety": v["safety"], "held": v["held"],
                                    "fence_anchor_check": v["traces"]["fence_anchor_check"], "reboots": v["reboots"]} for e, v in sorted(x.items())}}
    a, b = cells.get(("ZEPHYR_F_BOOT", "EVICON_FI"), {}), cells.get(("ZEPHYR_F_BOOT", "EVICON_FI_REC"), {})
    res["contrast_ii_iii_clean_pp"] = PR.summary([b[e]["clean_pp"] - a[e]["clean_pp"] for e in sorted(set(a) & set(b))])
    iii = res["arms"].get("ZEPHYR_F_BOOT/EVICON_FI_REC", {})
    mm = {k: v["fence_anchor_check"].get("MISMATCH_FLOOR", 0) for k, v in iii.get("per_cell", {}).items()}
    res["RB-C1"] = {"rule": "no FENCE_ANCHOR_CHECK MISMATCH_FLOOR in arm (iii)", "prediction": 0, "count": sum(mm.values()),
                    "cells": [k for k, n in mm.items() if n], "verdict": ("NOT RUN" if not iii.get("cells") else
                                                                          "MET" if sum(mm.values()) == 0 else "NOT MET")}
    if ref is not None and (ref / "runtime_episodes.csv").exists():
        refc = {}
        with open(ref / "runtime_episodes.csv", newline="") as fh:
            for r in csv.DictReader(fh):
                if r["cell_status"].startswith("VALID") and r["contract"] == "ZEPHYR_F_DIRECT" and r["policy"] == "EVICON":
                    refc[int(r["episode"])] = 100.0 * int(r["clean_fulfilled"]) / max(1, int(r["offered"]))
        eps = [e for e in sorted(set(refc) & set(b)) if PR.schedule_digest(run, e) and PR.schedule_digest(run, e) == PR.schedule_digest(ref, e)]
        res["cross_run_iii_vs_SRz6"] = {"episodes_same_schedule": eps,
                                        "clean_pp": PR.summary([b[e]["clean_pp"] - refc[e] for e in eps]) if eps else {"n": 0}}
    return res


def _s(s: dict, signed=False) -> str:
    if not s or s.get("n", 0) == 0:
        return "-"
    t = s.get("t95") or [math.nan, math.nan]
    f = "+.2f" if signed else ".2f"
    return f"{s['mean']:{f}} [{t[0]:{f}}, {t[1]:{f}}] (n={s['n']})"


def markdown(r: dict) -> str:
    L = ["# E-RB: owner reboots as a campaign fault (PREREG_extra_087.md item B)", "",
         f"Run `{r['run']}`" + (f"; reference SRz6 `{r['ref']}`" if r["ref"] else "") + (f"; INVALID cells: {r['invalid']}" if r["invalid"] else "") + ".",
         "Estimands only, except the check RB-C1; the rules RB-1, RB-A1, RB-A2, RB-D1 and RB-S are decided in `pairs/PREREG_extra_087/`.", "",
         f"**RB-C1** ({r['RB-C1']['rule']}; prediction 0): {r['RB-C1']['count']} -> **{r['RB-C1']['verdict']}**"
         + (f" (cells {r['RB-C1']['cells']})" if r["RB-C1"]["cells"] else ""), "",
         f"Arm (ii) -> (iii), clean fulfilment, per-episode difference: {_s(r['contrast_ii_iii_clean_pp'], True)}", ""]
    if "cross_run_iii_vs_SRz6" in r:
        c = r["cross_run_iii_vs_SRz6"]
        L += [f"Arm (iii) against SRz6 ZEPHYR_F_DIRECT/EVICON (no reboots), episodes {c['episodes_same_schedule']}: {_s(c['clean_pp'], True)}", ""]
    L += ["| arm | cells | clean pp | violation cells | reboots ok / failed | unresolved at end (budget) | of them with an effect | "
          "FENCE_ANCHOR_CHECK | recovery events | gateway seals |", "|---|---|---|---|---|---|---|---|---|---|"]
    for k, a in r["arms"].items():
        h = a["unresolved_at_end"]
        L.append(f"| {a['label']}: {k} | {a['cells']} | {_s(a['clean_pp'])} | {a['violation_cells']} {json.dumps(a['safety_totals'])} | "
                 f"{a['reboots_ok']} / {a['reboots_failed']} | {h.get('unresolved', 0)} ({h.get('budget', 0)} units) | "
                 f"{h.get('unresolved_with_effect', 0)} ({h.get('budget_with_effect', 0)} units) | {json.dumps(a['fence_anchor_check'], sort_keys=True)} | "
                 f"{json.dumps(a['events'], sort_keys=True)} | {a['gateway_seals']} |")
    L += ["", "Per reboot kind (means over reboots; times are medians, seconds from the restart's first answer):", "",
          "| arm | kind | reboots | admissions | of them effective | admitted pp of arrivals | with an effective admission | s to 1st admission | "
          "s to 1st effective |", "|---|---|---|---|---|---|---|---|---|"]
    for k, a in r["arms"].items():
        for kind, v in a["per_kind"].items():
            L.append(f"| {a['label']} | {kind} | {v['reboots']} | {v['admissions_mean']} | {v['admissions_effective_mean']} | "
                     f"{v['admitted_pp_of_arrivals_mean']} | {v['with_effective_admission']} | {v['s_to_first_admission_median']} | "
                     f"{v['s_to_first_effective_admission_median']} |")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True, help="RBz6=<run dir>")
    ap.add_argument("--ref", default="", help="SRz6=<run dir>")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    run = Path(a.run.split("=", 1)[-1])
    ref = Path(a.ref.split("=", 1)[-1]) if a.ref else None
    r = analyse(run, ref)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "rb087.json").write_text(json.dumps(r, indent=1, default=str) + "\n")
    (out / "RB087.md").write_text(markdown(r))
    print(f"rb087: RB-C1 {r['RB-C1']['verdict']} ({r['RB-C1']['count']}) -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
