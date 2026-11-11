#!/bin/bash
# EviCon 088: everything of plan PREREG_extra_087 in ONE overnight session, in one review run of release 088: the firmware gate
# on both builds (the measured build and the boot-report build), the documented shadow loop on the real owners (SHRz6 on the
# cutoff firmware, SHRm6 on MinIO's conditional PUT: EVICON, SHADOW_RECONCILE at 10 s and at 1 s), owner reboots as a
# campaign fault with the reboot recovery (RBz6: the negative control, release 086, the recovery), then the final report of
# the paper (the data freeze, with the 185 earlier verdicts compared with the 086 final report) and the bundles.
# BEFORE the session (once, on VM-C, about 10 minutes): bash deploy/setup/build_iot.sh start zephyr_boot, then
# bash deploy/setup/build_iot.sh status until "BOOT_BUILD_OK" (the measured build is not rebuilt).
#   (no tmux needed: run it in the background with nohup, below)
#   nohup bash /root/EVICON_TOIT_088/run088.sh >> /root/run088.log 2>&1 < /dev/null &
# About 10.5 h in all (at most 12 h). Expected per step (from the 086 testbed run's own times):
#   preflight build verify distribute    0.2 h       measure zephyr zephyr-boot    1.15 h (the gate on both builds)
#   campaign expSHR_zephyr_w6            3.0 h (24 cells, with the counters)     campaign expSHR_minio_w6   2.9 h (24 cells)
#   campaign expRB_zephyr_w6             2.25 h (18 cells, 8 owner reboots per cell)
#   report (the data freeze)             0.95 h; bundles: minutes
# After an interruption (power off, ssh lost, Ctrl-C) run the SAME command again (with --recheck after the machine was
# off): steps that passed and campaigns that finished are skipped, an interrupted campaign is resumed.
# Options (not needed for the overnight run):
#   --until HH:MM  Vietnam time (UTC+7), the next such time (at most 20 h ahead): a step starts only if it is expected to
#                  end by then (a campaign: its cells x 7.3 min, or this run's own pace if slower, + 8 min; the firmware
#                  gate 75 min; the report 60 min); run the command again later (two nights: --until for the first).
#   --recheck      after the machine was off: testbed check, refuse a campaign directory cut at its very start,
#                  preflight --force.
# Order (plan section 6): preflight build verify distribute; measure zephyr zephyr-boot (the firmware gate on both builds);
# campaigns expSHR_zephyr_w6, expSHR_minio_w6 (item A first: an unplanned violation there stops the session before item B),
# expRB_zephyr_w6; report --archive ... --from 078 --from 083 --from 084 --from 085 --from 086 (only when the three
# campaigns finished); bundle after new work. An unplanned safety violation stops everything (exit 3); a violation of the
# negative control (NEG_ZEPHYR_F_BOOT) is planned. It refuses to start while another evicon_review.py runs.
set -uo pipefail
cd "${EVICON088_DIR:-/root/EVICON_TOIT_088}" || exit 1
UNTIL=""; RECHECK=0; LABEL="run088"
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
R085=${EVICON085_RUN:-$(readlink -f /root/EVICON_TOIT_085/results/LATEST 2>/dev/null)}
R086=${EVICON086_RUN:-$(readlink -f /root/EVICON_TOIT_086/results/LATEST 2>/dev/null)}
ARCHIVE=${EVICON_ARCHIVE:-/root/evicon-archive}
CAMPAIGNS="expSHR_zephyr_w6 expSHR_minio_w6 expRB_zephyr_w6"

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
report_current() {  # the report PASSed after every campaign and check of this run ended
python3 - <<'PY'
import json, pathlib, sys
f = pathlib.Path("results/LATEST/stages.json")
st = json.loads(f.read_text()) if f.exists() else {}
r = st.get("report", {})
if r.get("status") != "PASS":
    sys.exit(1)
others = [v.get("ended", "") for k, v in st.items() if k.startswith(("campaign:", "measure:"))]
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

echo "######## EviCon 088 $LABEL: started $(T now) (Vietnam time from this machine's clock: check it is right)"
if o=$(other_driver) || ! lock_free; then
  echo "######## REFUSED: another evicon_review.py is running (${o:-the driver lock /run/lock/evicon-review.lock is held}); wait until it ends"; exit 1
fi
for r in "$R078" "$R083" "$R084" "$R085" "$R086"; do
  [ -n "$r" ] && [ -d "$r" ] || { echo "######## REFUSED: a review run the final report reads is missing: '$r' (078: $R078; 083: /root/EVICON_TOIT_083/results/LATEST; 084: /root/EVICON_TOIT_084/results/LATEST; 085: /root/EVICON_TOIT_085/results/LATEST; 086: /root/EVICON_TOIT_086/results/LATEST)"; exit 1; }
done
[ -f "$R086/report/claims/claims.json" ] || { echo "######## REFUSED: the 086 final report is missing ($R086/report/claims/claims.json): the 185 earlier verdicts are compared with it"; exit 1; }
echo "######## the report will read: archive $ARCHIVE, 078 $R078, 083 $R083, 084 $R084, 085 $R085, 086 $R086"
DEADLINE=""; STOP_BEFORE=""
if [ -n "$UNTIL" ]; then
  DEADLINE=$(T epoch "$UNTIL"); rc=$?
  if [ $rc = 3 ]; then echo "######## $UNTIL is more than 20 h away (has it passed today?): nothing run"; exit 0; fi
  if [ $rc != 0 ] || [ -z "$DEADLINE" ]; then echo "######## --until wants HH:MM, e.g. 07:40"; exit 2; fi
  echo "######## steps must end by $(T fmt "$DEADLINE"); one that would not is left for the next piece"
fi
if bad=$(unsafe); then
  echo "######## STOP: unplanned safety violation in $bad. Send every EVICON_088_result_*.zip not sent yet; run nothing else."; exit 3
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
# a later piece: copy 088's binaries to the VMs again, so every campaign runs on exactly this release
[ -z "$NEW" ] && { E distribute --force || { echo "######## distribute FAIL: nothing run. Send: python3 evicon_review.py bundle"; exit 1; }; }
echo "######## review run: $(readlink -f results/LATEST)"

# 1. the firmware gate: both builds measured in this review run (the measured build for SHRz6, the boot-report build for RBz6;
#    the firmware campaigns refuse to start without their build's measured contract)
GATE=""; for st in zephyr zephyr-boot; do status_is "measure:$st" PASS || GATE="$GATE $st"; done
if [ -n "$GATE" ]; then
  if fits 75 "measure$GATE"; then
    RAN=1; WORK=1
    # shellcheck disable=SC2086
    E measure $GATE || echo "######## the firmware gate did not PASS (see above). If it says 'no boot-report firmware build': on VM-C run 'bash deploy/setup/build_iot.sh start zephyr_boot', wait for BOOT_BUILD_OK ('bash deploy/setup/build_iot.sh status'), then this script again. The firmware campaigns refuse to start until their build is measured"
  fi
fi
B
# 2. item A (the shadow loop on the firmware, then on MinIO), then item B (owner reboots)
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
echo
if [ -n "$left" ]; then
  [ -n "$STOP_BEFORE" ] && echo "######## $LABEL ENDED $(T now): the next step, $STOP_BEFORE, would not end by $(T fmt "$DEADLINE")"
  echo "######## not finished yet:$left"
  echo "######## no report: run this script again (it resumes), or send the bundle if a step keeps failing"
  echo "######## no campaign is running now. Bundles of this release (send those not sent yet):"
  ls -1t EVICON_088_result_*.zip 2>/dev/null | head -8 | sed 's/^/   /'
  exit 0
fi
if [ $WORK = 0 ] && report_current; then
  echo "######## nothing new in this piece and the final report passed earlier: not repeated. Its result: $(summary_of report | cut -c1-200)"
  echo "######## (to repeat it: python3 evicon_review.py report --archive $ARCHIVE --from $R078 --from $R083 --from $R084 --from $R085 --from $R086)"
  exit 0
fi
if ! fits 60 "the final report"; then
  echo "######## $LABEL ENDED $(T now): all campaigns finished; the report (about 60 min) would not end by $(T fmt "$DEADLINE")"
  echo "######## run this script again without --until (or with a later one) for the report"; exit 0
fi
if E report --archive "$ARCHIVE" --from "$R078" --from "$R083" --from "$R084" --from "$R085" --from "$R086"; then
  E bundle --incremental
  s=$(summary_of report)
  case "$s" in
    *"earlier verdicts against 086 DIFFER"*) echo; echo "######## REPORT DONE WITH A DIFFERENCE $(T now): an earlier plan's verdict differs from the 086 final report (report/verdicts087/VERDICTS.md). Send every EVICON_088_result_*.zip not sent yet (each holds only the files new since the one before)";;
    *"earlier verdicts against 086 identical"*) echo; echo "######## ALL DONE $(T now). The 185 earlier verdicts are those of the 086 final report. Send every EVICON_088_result_*.zip not sent yet (each holds only the files new since the one before)";;
    *) echo; echo "######## ALL DONE $(T now) (the earlier verdicts were not compared: is the 086 run's report among the sources?). Send every EVICON_088_result_*.zip not sent yet (each holds only the files new since the one before)";;
  esac
else
  E bundle --incremental
  echo; echo "######## REPORT FAIL $(T now): the campaigns are done. Send every EVICON_088_result_*.zip not sent yet (each holds only the files new since the one before) and the end of /root/run088.log"
  exit 1
fi
