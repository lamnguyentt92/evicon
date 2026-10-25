#!/usr/bin/env python3
"""Timeline of one ambiguity episode in a runtime campaign cell (release 068; figure for RQ3/RQ4).

From the raw traces a campaign fetched into a cell (raw/A/authority*.jsonl, raw/E/plant.jsonl,
raw/E/dev*/*.journal, clients.jsonl, faults.jsonl), picks one fence that the owner certified
(BARRIER_ANCHOR_COMMITTED) and lays out, on one time axis, what led to it and what followed:

  fault     the fault windows of that device (drop/delay/offline) overlapping the episode
  client    command arrival, admission, the terminal outcome the client saw
  authority admission, dispatch, waits, the fence decision, barrier pause/anchor, evidence
  owner     the device journal (CLAIMED/APPLIED/STOPPED, cutoff)
  truth     effects recorded by the plant (the independent channel)

Repeated identical rows (the policy's WAIT every second, suppressed retries) are folded into one
row with a count. Time is in seconds from the start of the episode window.

    python3 evaluation/timeline.py --cell <campaign>/cells/ep0/K0F/EVICON --out <dir> [--domain pump0-1]
    python3 evaluation/timeline.py --campaign <campaign run dir> --contract K0F --policy EVICON --out <dir>

Writes timeline.csv, timeline.md and, when matplotlib is installed, timeline.png and timeline.pdf (070). Read only.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import sys
from pathlib import Path

NOISY = {"OUTBOX_RETRY_SUPPRESSED_T4", "TERMINAL_HISTORY_COMPACTED", "REVISION_RETRY", "REVISION_RECOMPUTE"}
# events labelled in the figure (the table has them all)
LABELS = {"ARRIVAL": "arrival", "TERMINAL_SEEN": "client sees {detail}", "OUTBOX_DISPATCH_ATTEMPT": "dispatch",
          "EVICON_DECISION": "{detail}", "BARRIER_ANCHOR_COMMITTED": "fence certified", "CUTOFF": "cutoff",
          "APPLIED": "APPLIED", "STOPPED": "STOPPED", "EFFECT": "effect"}


def wall(s: str) -> float:
    """RFC 3339 with nanoseconds ('2026-10-17T00:11:36.239401151Z') -> epoch seconds."""
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?Z", s or "")
    if not m:
        return 0.0
    base = dt.datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc).timestamp()
    return base + (float("0." + m.group(2)) if m.group(2) else 0.0)


def jsonl(path: Path):
    if not path.exists():
        return
    for line in path.read_text(errors="replace").splitlines():
        try:
            yield json.loads(line)
        except ValueError:
            continue


def authority_events(cell: Path) -> list:
    out = []
    for f in sorted(cell.glob("raw/A/authority*.jsonl")):
        for e in jsonl(f):
            e["_t"] = wall(e.get("wall_time", ""))
            out.append(e)
    return sorted(out, key=lambda e: e["_t"])


def pick_episode(events: list, domain: str = ""):
    """(domain, anchor event, fence decision, target command ids) of the first certified fence."""
    last_fence = {}
    for e in events:
        d = e.get("domain_id", "")
        if domain and d != domain:
            continue
        if e.get("event") in ("EVICON_DECISION", "POLICY_DECISION") and str(e.get("outcome", "")).upper() == "FENCE":
            last_fence[d] = e
        if e.get("event") == "BARRIER_ANCHOR_COMMITTED" and d in last_fence:
            m = re.search(r"targets=([^;]*)", last_fence[d].get("reason", ""))
            targets = [x for x in (m.group(1).split(",") if m else []) if x]
            if targets:
                return d, e, last_fence[d], targets
    return None


def build(cell: Path, domain: str = "", after_s: float = 4.0) -> dict:
    ev = authority_events(cell)
    ep = pick_episode(ev, domain)
    if not ep:
        raise SystemExit(f"{cell}: no certified fence (BARRIER_ANCHOR_COMMITTED after a FENCE decision) found")
    dom, anchor, fence, targets = ep
    dev = re.match(r"d(\d+)-", targets[0])
    dev_idx = int(dev.group(1)) if dev else None
    prefix = f"d{dev_idx}-" if dev_idx is not None else ""
    t_first = min([e["_t"] for e in ev if e.get("command_id") in targets] or [fence["_t"]])
    t0 = t_first - 0.5
    t_end = anchor["_t"] + after_s
    for e in ev:  # extend to the next admission on that domain after the anchor
        if e["_t"] > anchor["_t"] and e.get("domain_id") == dom and e.get("event") == "DECISION_RECORDED" \
                and e.get("decision") == "ADMITTED" and e.get("command_id") not in targets:
            t_end = max(t_end, min(e["_t"] + 0.5, anchor["_t"] + 30))
            break
    rows = []

    def add(t, lane, event, cmd="", detail=""):
        if t0 <= t <= t_end:
            rows.append({"t": t, "lane": lane, "event": event, "command": cmd, "detail": str(detail)[:140]})

    for e in ev:
        if e.get("domain_id") != dom or e.get("event") == "PLANNER_CONFIGURATION":
            continue
        cmd = e.get("command_id", "")
        if cmd and not cmd.startswith(prefix):
            continue
        detail = e.get("decision") or e.get("outcome") or e.get("reason") or ""
        if e.get("event") == "EVICON_FENCE_RESULT":
            detail = f"{e.get('outcome', '')} {e.get('reason', '')}".strip()
        add(e["_t"], "authority", e.get("event", ""), cmd, detail)
    for c in jsonl(cell / "clients.jsonl"):
        if dev_idx is not None and c.get("device") != dev_idx:
            continue
        add(float(c.get("arrival", 0)), "client", "ARRIVAL", c.get("cid", ""), f"value {c.get('value')}")
        for i, t in (c.get("admitted") or {}).items():
            add(float(t), "client", "ADMITTED", i, "")
        for i, t in (c.get("terminal_seen") or {}).items():
            add(float(t), "client", "TERMINAL_SEEN", i, (c.get("final_status") or {}).get(i, ""))
        if c.get("decision") and c.get("decision") != "ADMITTED":
            add(float(c.get("arrival", 0)), "client", c["decision"], c.get("cid", ""), c.get("last_reason", ""))
    for f in jsonl(cell / "faults.jsonl"):
        if dev_idx is not None and f.get("device") != dev_idx:
            continue
        s, e = float(f.get("actual_start") or f.get("planned_start") or 0), float(f.get("actual_end") or 0)
        if e >= t0 and s <= t_end:
            rows.append({"t": max(s, t0), "lane": "fault", "event": f"{f.get('kind', '')} start", "command": "",
                         "detail": f"{e - s:.1f} s" + (" (began before the window)" if s < t0 else ""), "end": min(e, t_end)})
            add(e, "fault", f"{f.get('kind', '')} end", "", "")
    for p in jsonl(cell / "raw" / "E" / "plant.jsonl"):
        if str(p.get("cmd", "")).startswith(prefix):
            add(int(p.get("t_ns", 0)) / 1e9, "truth", "EFFECT", p.get("cmd", ""), f"value {p.get('value')}")
    # the device journal's CUTOFF records carry no time: placed at the authority's pause of the
    # same barrier token (the device writes the cutoff while that barrier is open), and said so
    pauses = {e.get("reason", ""): e["_t"] for e in ev if e.get("event") == "BARRIER_PAUSE_COMMITTED" and e.get("domain_id") == dom}
    for j in sorted(cell.glob("raw/E/*/*.journal")):
        if dom and not j.name.startswith(dom):
            continue
        for e in jsonl(j):
            r = e.get("r") or {}
            if e.get("k") == "REC":
                add((r.get("at_ms") or 0) / 1000.0, "owner", r.get("status", ""), r.get("id", ""), r.get("reason", ""))
            elif e.get("k") == "CUTOFF" and e.get("tok") in pauses:
                add(pauses[e["tok"]] + 0.001, "owner", "CUTOFF", "", f"cutoff {e.get('c')} (journal has no time; placed at the barrier's pause)")
    rows.sort(key=lambda r: (r["t"], r["lane"]))
    folded = []
    for r in rows:  # fold repeats: same lane, event, command and detail in a row
        if folded and all(folded[-1][k] == r[k] for k in ("lane", "event", "command", "detail")):
            folded[-1]["count"] += 1
            folded[-1]["t_last"] = r["t"]
            continue
        folded.append(dict(r, count=1, t_last=r["t"]))
    for r in folded:
        r["t_s"] = round(r["t"] - t0, 3)
        r["t_last_s"] = round(r["t_last"] - t0, 3)
    return {"cell": str(cell), "domain": dom, "device": dev_idx, "targets": targets, "t0_epoch": t0,
            "fence_decision_s": round(fence["_t"] - t0, 3), "anchor_s": round(anchor["_t"] - t0, 3),
            "window_s": round(t_end - t0, 3), "rows": folded}


def markdown(tl: dict) -> str:
    L = ["# Timeline of one ambiguity episode", "",
         f"Cell `{tl['cell']}`, domain `{tl['domain']}`, fence targets {', '.join(tl['targets'])}. "
         f"Fence decided at {tl['fence_decision_s']} s, certified (anchor) at {tl['anchor_s']} s; window {tl['window_s']} s.", "",
         "| t (s) | lane | event | command | detail | repeats |", "|---|---|---|---|---|---|"]
    for r in tl["rows"]:
        rep = f"{r['count']}× until {r['t_last_s']} s" if r["count"] > 1 else ""
        L.append(f"| {r['t_s']} | {r['lane']} | {r['event']} | {r['command']} | {r['detail'].replace('|', '/')} | {rep} |")
    return "\n".join(L) + "\n"


def plot(tl: dict, path: Path) -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return False
    lanes = ["fault", "client", "authority", "owner", "truth"]
    fig, ax = plt.subplots(figsize=(11, 3.8))
    labelled = {}
    for r in tl["rows"]:
        y = lanes.index(r["lane"])
        if r["lane"] == "fault" and r["event"].endswith("start") and "end" in r:
            ax.barh(y, r["end"] - (r["t"]) if r.get("end") else 0, left=r["t_s"], height=0.4, color="#d9a441", alpha=0.6)
            ax.text(r["t_s"], y + 0.28, r["event"].replace(" start", ""), fontsize=6)
            continue
        if r["event"] in NOISY:
            continue
        ax.plot([r["t_s"]], [y], "o", ms=3, color="#33658a")
        label = LABELS.get(r["event"])
        if label is not None:
            text = (label.format(detail=r["detail"].split()[0] if r["detail"] else "") + " " + r["command"]).strip()
            n = labelled.setdefault(y, 0)
            labelled[y] += 1
            ax.annotate(text[:30], (r["t_s"], y), fontsize=6, rotation=25, xytext=(2, 4 + 9 * (n % 3)), textcoords="offset points")
    ax.axvline(tl["fence_decision_s"], color="#999", lw=0.8, ls="--")
    ax.axvline(tl["anchor_s"], color="#2f7d32", lw=0.8, ls="--")
    ax.set_yticks(range(len(lanes)), lanes)
    ax.set_ylim(-0.6, len(lanes) - 0.2)
    ax.set_xlabel("seconds from the start of the episode")
    ax.set_title(f"{tl['domain']}: timeout -> wait -> fence -> certified outcome", fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    matplotlib.rcParams["pdf.fonttype"] = 42  # release 070: a vector copy for the paper, with embedded TrueType fonts
    fig.savefig(path.with_suffix(".pdf"))
    plt.close(fig)
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cell", help="one campaign cell directory")
    ap.add_argument("--campaign", help="a campaign run directory: the first cell of --contract/--policy with a certified fence")
    ap.add_argument("--contract", default="K0F")
    ap.add_argument("--policy", default="EVICON")
    ap.add_argument("--domain", default="")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    cells = [Path(a.cell)] if a.cell else sorted(Path(a.campaign).glob(f"cells/ep*/{a.contract}/{a.policy}"),
                                                 key=lambda p: int(re.sub(r"\D", "", p.parts[-3]) or 0))
    tl = None
    for c in cells:
        if pick_episode(authority_events(c), a.domain):
            tl = build(c, a.domain)
            break
    if tl is None:
        print(f"no cell with a certified fence among {len(cells)}", file=sys.stderr)
        return 1
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "timeline.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["t_s", "t_last_s", "count", "lane", "event", "command", "detail"], extrasaction="ignore")
        w.writeheader()
        w.writerows(tl["rows"])
    (out / "timeline.md").write_text(markdown(tl))
    (out / "timeline.json").write_text(json.dumps({k: v for k, v in tl.items() if k != "rows"}, indent=2) + "\n")
    png = plot(tl, out / "timeline.png")
    print(f"timeline: {tl['cell']} {tl['domain']}, {len(tl['rows'])} rows, fence at {tl['fence_decision_s']} s, anchor at "
          f"{tl['anchor_s']} s{'; png' if png else ''} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
