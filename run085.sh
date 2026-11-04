#!/bin/bash
# EviCon 085: plan PREREG_extra_085 in one review run of release 085: the firmware's contract, the ThingsBoard RPC checks
# (TB-1 to TB-4), EVICON and PERIODIC_FULL in the same campaigns on the cutoff firmware and on MinIO (PMz6, PMzi, PMm6,
# PMmi), then the final report of the paper (the data freeze) and the bundle. About 10.3 h; it can run in pieces.
#   tmux new -s evicon        (or tmux attach -t evicon)
#   bash /root/EVICON_TOIT_085/run085.sh [--until HH:MM] [--recheck] [--label NAME] 2>&1 | tee -a /root/run085.log
#   --until HH:MM  Vietnam time (UTC+7), the next such time (at most 20 h ahead). A step starts only if it is expected to
#                  end by then (a campaign: its cells x 7.3 min, or this run's own pace if slower, + 8 min; the checks
#                  60 min; the firmware measurement 50 min; the report 60 min). So turning the machine off after the piece
#                  cuts nothing. Run the same command again later: it goes on where it stopped.
#   --recheck      after the machine was off: testbed check, refuse a campaign directory cut at its very start,
#                  preflight --force.
# Order (plan section 6): preflight build verify distribute; measure zephyr-plain zephyr; tbcheck (once more if PARTIAL);
# campaigns expPM_zephyr_w6, expPM_zephyr_winf, expPM_minio_w6, expPM_minio_winf; report --archive ... --from 078
# --from 083 --from 084 (only when the four campaigns finished and tbcheck is decided); bundle.
# Steps that passed and campaigns that finished are skipped, an interrupted campaign is resumed; a bundle is made after
# new work only. An unplanned safety violation stops everything (exit 3). It refuses to start while another
# evicon_review.py runs.
set -uo pipefail
cd "${EVICON085_DIR:-/root/EVICON_TOIT_085}" || exit 1
UNTIL=""; RECHECK=0; LABEL="run085"
while [ $# -gt 0 ]; do
  case "$1" in
    --until) [ $# -ge 2 ] || { echo "--until needs HH:MM"; exit 2; }; UNTIL="$2"; shift 2;;
    --recheck) RECHECK=1; shift;;
    --label) [ $# -ge 2 ] || exit 2; LABEL="$2"; shift 2;;
    *) echo "unknown option: $1"; exit 2;;
  esac
done
RAN=0; WORK=0
R078=/root/EVICON_TOIT_078/results/review-20261028T080322
R083=${EVICON083_RUN:-$(readlink -f /root/EVICON_TOIT_083/results/LATEST 2>/dev/null)}
R084=${EVICON084_RUN:-$(readlink -f /root/EVICON_TOIT_084/results/LATEST 2>/dev/null)}
ARCHIVE=${EVICON_ARCHIVE:-/root/evicon-archive}
CAMPAIGNS="expPM_zephyr_w6 expPM_zephyr_winf expPM_minio_w6 expPM_minio_winf"

T() {  # T now | T in <min> | T fmt <epoch> | T epoch <HH:MM> | T fits <deadline epoch> <min>   (Vietnam time)
python3 - "$@" <<'PY'
import datetime as dt, sys, time
VN = dt.timezone(dt.timedelta(hours=7))
now = time.time()
fmt = lambda t: dt.datetime.fromtimestamp(t, VN).strftime("%a %d/%m %H:%M")
cmd = sys.argv[1]
if cmd == "now":
    print(fmt(now))
elif cmd == "in":
    print(fmt(now + float(sys.argv[2]) * 60))
elif cmd == "fmt":
    print(fmt(float(sys.argv[2])))
elif cmd == "epoch":
    h, m = (int(x) for x in sys.argv[2].split(":"))
    n = dt.datetime.fromtimestamp(now, VN)
    d = n.replace(hour=h, minute=m, second=0, microsecond=0)
    if d.timestamp() <= now:
        d += dt.timedelta(days=1)
    if d.timestamp() - now > 20 * 3600:
        sys.exit(3)
    print(int(d.timestamp()))
elif cmd == "fits":
    sys.exit(0 if now + float(sys.argv[3]) * 60 <= float(sys.argv[2]) else 1)
PY
}
E() { echo; echo "######## $(T now)  evicon_review.py $*"; python3 evicon_review.py "$@"; }
finished() {  # a run of this campaign in this review run that the driver would not resume
python3 - "$1" <<'PY'
import json, pathlib, sys
name = json.loads(pathlib.Path(f"evaluation/campaigns/{sys.argv[1]}.json").read_text())["name"]
base = pathlib.Path("results/LATEST/campaigns") / name
for p in (sorted(base.glob("2*")) if base.exists() else []):
    m = p / "manifest.json"
    if (p / "activation.json").exists() or (m.exists() and "finished" in json.loads(m.read_text() or "{}")):
        sys.exit(0)
sys.exit(1)
PY
}
status_is() {  # status_is <stages.json key> <status>
python3 - "$@" <<'PY'
import json, pathlib, sys
f = pathlib.Path("results/LATEST/stages.json")
st = json.loads(f.read_text()).get(sys.argv[1], {}) if f.exists() else {}
sys.exit(0 if st.get("status") == sys.argv[2] else 1)
PY
}
summary_of() {  # the summary of a stages.json key
python3 - "$1" <<'PY'
import json, pathlib, sys
f = pathlib.Path("results/LATEST/stages.json")
print((json.loads(f.read_text()).get(sys.argv[1], {}) if f.exists() else {}).get("summary", ""))
PY
}
unsafe() {  # campaigns whose last result is an unplanned safety violation (the driver's wordings)
python3 - <<'PY'
import json, pathlib, sys
f = pathlib.Path("results/LATEST/stages.json")
st = json.loads(f.read_text()) if f.exists() else {}
bad = [k.split(":", 1)[1] for k, v in st.items() if k.startswith("campaign:") and v.get("status") == "FAIL"
       and any(t in str(v.get("summary", "")) for t in ("safety violations outside", "safety violations: "))]
print(" ".join(bad))
sys.exit(0 if bad else 1)
PY
}
est_min() {  # expected minutes of the whole campaign (conservative for a resumed one)
python3 - "$1" <<'PY'
import datetime as dt, json, pathlib, statistics, sys
c = json.loads(pathlib.Path(f"evaluation/campaigns/{sys.argv[1]}.json").read_text())
cells = (c.get("episodes", 1) * len(c.get("contracts") or [0]) * len(c.get("policies") or [0])
         - len(c.get("exclude_cells") or []))
ep = c.get("duration_s", 300) + c.get("tail_s", 60)
pace = 7.3 * max(ep, 360) / 360
seen = []
for m in pathlib.Path("results/LATEST/campaigns").glob("*/2*/manifest.json"):
    try:
        d = json.loads(m.read_text())
        cf = json.loads((m.parent / "config.json").read_text())
        if "finished" not in d or not d.get("cells") or len(d.get("sessions") or []) != 1:
            continue
        if cf.get("duration_s", 0) + cf.get("tail_s", 0) != ep:
            continue
        f = "%Y-%m-%dT%H:%M:%S%z"
        mins = (dt.datetime.strptime(d["finished"], f) - dt.datetime.strptime(d["started"], f)).total_seconds() / 60
        seen.append(mins / d["cells"])
    except Exception:
        continue
if seen:
    pace = max(pace, statistics.median(seen))
print(round(cells * pace + 8))
PY
}
cut_at_start() {  # campaign directories cut at their very start (machine off): empty config.json, cannot resume
python3 - <<'PY'
import json, pathlib, sys
bad = []
for p in sorted(pathlib.Path("results/LATEST/campaigns").glob("*/2*")):
    if not p.is_dir():
        continue
    m = p / "manifest.json"
    try:
        done = (p / "activation.json").exists() or (m.exists() and "finished" in json.loads(m.read_text() or "{}"))
    except Exception:
        done = False
    c = p / "config.json"
    if not done and (not c.exists() or c.stat().st_size == 0):
        bad.append(str(p.resolve()))
print("\n".join(bad))
sys.exit(0 if bad else 1)
PY
}
other_driver() {  # another evicon_review.py running (any directory), or the driver's lock held
python3 - <<'PY'
import os, pathlib, sys
me = os.getpid()
for p in pathlib.Path("/proc").glob("[0-9]*"):
    try:
        argv = [a.decode(errors="replace") for a in (p / "cmdline").read_bytes().split(b"\0") if a]
    except OSError:
        continue
    if int(p.name) != me and len(argv) >= 2 and os.path.basename(argv[0]).startswith("python") \
            and any(os.path.basename(a) == "evicon_review.py" for a in argv[1:3]):
        print(f"pid {p.name}: {' '.join(argv)[:160]}")
        sys.exit(0)
sys.exit(1)
PY
}
tb_decided() {  # tbcheck PASS, or PARTIAL in two runs (plan section 3: the second run decides)
  status_is tbcheck PASS && return 0
  status_is tbcheck PARTIAL || return 1
  [ "$(grep -c '^===== tbcheck PARTIAL' results/LATEST/logs/tbcheck.log 2>/dev/null)" -ge 2 ]
}
report_current() {  # the report PASSed after every campaign and check of this run ended
python3 - <<'PY'
import json, pathlib, sys
f = pathlib.Path("results/LATEST/stages.json")
st = json.loads(f.read_text()) if f.exists() else {}
r = st.get("report", {})
if r.get("status") != "PASS":
    sys.exit(1)
others = [v.get("ended", "") for k, v in st.items() if k.startswith(("campaign:", "measure:")) or k == "tbcheck"]
sys.exit(0 if all(str(r.get("ended", "")) >= str(e) for e in others) else 1)
PY
}
lock_free() { ( flock -n 9 ) 9>>/run/lock/evicon-review.lock; }
B() { [ $RAN = 1 ] && { E bundle --incremental; RAN=0; }; true; }  # only after new work
fits() {  # fits <minutes> <what>: with --until, is there time for the step? (else remember it and say no)
  [ -z "$DEADLINE" ] && return 0
  T fits "$DEADLINE" "$1" && return 0
  STOP_BEFORE="$2 (about $1 min)"; return 1
}

echo "######## EviCon 085 $LABEL: started $(T now) (Vietnam time from this machine's clock: check it is right)"
if o=$(other_driver) || ! lock_free; then
  echo "######## REFUSED: another evicon_review.py is running (${o:-the driver lock /run/lock/evicon-review.lock is held}); wait until it ends"; exit 1
fi
for r in "$R078" "$R083" "$R084"; do
  [ -n "$r" ] && [ -d "$r" ] || { echo "######## REFUSED: a review run the final report reads is missing: '$r' (078: $R078; 083: /root/EVICON_TOIT_083/results/LATEST; 084: /root/EVICON_TOIT_084/results/LATEST)"; exit 1; }
done
echo "######## the report will read: archive $ARCHIVE, 078 $R078, 083 $R083, 084 $R084"
DEADLINE=""; STOP_BEFORE=""
if [ -n "$UNTIL" ]; then
  DEADLINE=$(T epoch "$UNTIL"); rc=$?
  if [ $rc = 3 ]; then echo "######## $UNTIL is more than 20 h away (has it passed today?): nothing run"; exit 0; fi
  if [ $rc != 0 ] || [ -z "$DEADLINE" ]; then echo "######## --until wants HH:MM, e.g. 07:40"; exit 2; fi
  echo "######## steps must end by $(T fmt "$DEADLINE"); one that would not is left for the next piece"
fi
if bad=$(unsafe); then
  echo "######## STOP: unplanned safety violation in $bad. Send every EVICON_085_result_*.zip not sent yet; run nothing else."; exit 3
fi
if [ $RECHECK = 1 ] && [ -e results/LATEST ]; then
  echo; echo "######## testbed check (column notes: ok, or only 'clock not NTP-synchronised')"
  bash deploy/setup/testbed_init.sh check || true
  if cut=$(cut_at_start); then
    echo "######## a campaign was cut at its very start (empty config.json). Move it aside, then run this again:"
    echo "$cut" | while IFS= read -r d; do echo "   mkdir -p /root/evicon-quarantine && mv '$d' /root/evicon-quarantine/"; done
    exit 1
  fi
  E preflight --force || { echo "######## preflight FAIL: start the 5 VMs, wait 2-3 minutes, run this again"; exit 1; }
fi
NEW=""; [ -e results/LATEST ] || NEW=--new
E preflight build verify distribute $NEW --quick || { echo "######## build/verify FAIL: nothing run. Send: python3 evicon_review.py bundle"; exit 1; }
# a later piece: copy 085's binaries to the VMs again, so every campaign runs on exactly this release
[ -z "$NEW" ] && { E distribute --force || { echo "######## distribute FAIL: nothing run. Send: python3 evicon_review.py bundle"; exit 1; }; }
echo "######## review run: $(readlink -f results/LATEST)"

# 1. the firmware's contract in this review run (the firmware campaigns need it), then 2. the ThingsBoard checks
if ! { status_is measure:zephyr-plain PASS && status_is measure:zephyr PASS; }; then
  if fits 50 "measure zephyr-plain zephyr"; then RAN=1; WORK=1; E measure zephyr-plain zephyr || echo "######## measure did not PASS (see above): the firmware campaigns will refuse to start"; fi
fi
# PARTIAL = a check NOT INTERPRETABLE (plan section 3): run once more, the second run decides
for attempt in 1 2; do
  { [ -n "$STOP_BEFORE" ] || tb_decided; } && break
  fits 60 "tbcheck" || break
  RAN=1; WORK=1
  E tbcheck --from "$R083"   # exit 0 for PASS and for PARTIAL: the status decides
  status_is tbcheck PASS && break
  if status_is tbcheck PARTIAL; then
    if tb_decided; then echo "######## tbcheck PARTIAL twice: the second run decides (NOT INTERPRETABLE stays in the report)"; break; fi
    echo "######## tbcheck PARTIAL (a check not interpretable): it runs once more, as the plan says"
  else
    echo "######## tbcheck did not PASS (see above); going on with the campaigns. Run this script again later: it repeats tbcheck"; break
  fi
done
B
# 3. EVICON and PERIODIC_FULL in the same campaigns
for c in $CAMPAIGNS; do
  [ -n "$STOP_BEFORE" ] && break
  finished "$c" && { echo "[campaign $c] finished earlier in this run: skipped"; continue; }
  m=$(est_min "$c") || m=130
  fits "$m" "$c" || break
  echo; echo "######## $(T now) campaign $c: about $m min, expected end about $(T in "$m")"
  RAN=1; WORK=1
  E campaign "$c" && continue
  if bad=$(unsafe); then
    echo; echo "######## STOP: unplanned safety violation in $bad. Send the bundle below; run nothing else."
    E bundle --incremental; exit 3
  fi
  echo "######## campaign $c did not finish (see above); going on with the next one"
done
B

left=""; for c in $CAMPAIGNS; do finished "$c" || left="$left $c"; done
tb_decided || left="$left tbcheck"
echo
if [ -n "$left" ]; then
  [ -n "$STOP_BEFORE" ] && echo "######## $LABEL ENDED $(T now): the next step, $STOP_BEFORE, would not end by $(T fmt "$DEADLINE")"
  echo "######## not finished yet:$left"
  echo "######## no report: run this script again (it resumes), or send the bundle if a step keeps failing"
  echo "######## no campaign is running now. Bundles of this release (send those not sent yet):"
  ls -1t EVICON_085_result_*.zip 2>/dev/null | head -8 | sed 's/^/   /'
  exit 0
fi
if [ $WORK = 0 ] && report_current; then
  echo "######## nothing new in this piece and the final report passed earlier: not repeated. Its result: $(summary_of report | cut -c1-200)"
  echo "######## (to repeat it: python3 evicon_review.py report --archive $ARCHIVE --from $R078 --from $R083 --from $R084)"
  exit 0
fi
if ! fits 60 "the final report"; then
  echo "######## $LABEL ENDED $(T now): all campaigns and the checks finished; the report (about 60 min) would not end by $(T fmt "$DEADLINE")"
  echo "######## run this script again without --until (or with a later one) for the report"; exit 0
fi
if E report --archive "$ARCHIVE" --from "$R078" --from "$R083" --from "$R084"; then
  E bundle --incremental
  s=$(summary_of report)
  case "$s" in
    *"earlier verdicts identical"*) echo; echo "######## ALL DONE $(T now). The earlier plans' verdicts are those of the 084 final report. Send every EVICON_085_result_*.zip not sent yet (each holds only the files new since the one before)";;
    *"earlier verdicts DIFFER"*) echo; echo "######## REPORT DONE WITH A DIFFERENCE $(T now): an earlier plan's verdict differs from the 084 final report (report/verdicts085/VERDICTS.md). Send every EVICON_085_result_*.zip not sent yet (each holds only the files new since the one before)";;
    *) echo; echo "######## ALL DONE $(T now) (the earlier verdicts were not compared: is the 084 run's report among the sources?). Send every EVICON_085_result_*.zip not sent yet (each holds only the files new since the one before)";;
  esac
else
  E bundle --incremental
  echo; echo "######## REPORT FAIL $(T now): the campaigns are done. Send every EVICON_085_result_*.zip not sent yet (each holds only the files new since the one before) and the end of /root/run085.log"
  exit 1
fi
