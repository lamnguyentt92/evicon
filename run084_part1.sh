#!/bin/bash
# EviCon 084, session 1 of 2: plan PREREG_extra_084, (a) per-identity fence intake (expFI_s6, expFI_sinf), then the
# firmware measurement and (c) PERIODIC_FULL on the cutoff firmware (expPF_zephyr_w6, expPF_zephyr_winf). About 7.3 h.
# Session 2 (run084_part2.sh) runs (c) on MinIO and the final report later, in the same review run.
#   tmux new -s evicon
#   bash /root/EVICON_TOIT_084/run084_part1.sh 2>&1 | tee -a /root/run084.log
# Interrupted (Ctrl-C, power loss): run the same command again. Stages and measurements that passed and campaigns that
# finished are skipped; an interrupted campaign is resumed. The first call makes a new review run of release 084.
# It refuses to start while release 083's plan is unfinished: 084's distribute replaces the binaries 083's campaigns use.
# A safety violation in any cell of this plan stops everything (send the bundle).
set -uo pipefail
cd /root/EVICON_TOIT_084 || exit 1
RAN=0
E() { echo; echo "######## $(date '+%F %T')  evicon_review.py $*"; python3 evicon_review.py "$@"; }
done083() {  # all 30 campaigns of PREREG_extra_083 finished in 083's review run (prints that run on success)
python3 - <<'PY'
import json, pathlib, sys
root = pathlib.Path("/root/EVICON_TOIT_083")
sha083 = "6e3f24a918de659c1feb8aaa886d9e9681195353528b5df10e615e8897ff3a06"  # PREREG_extra_083.md
latest = root / "results" / "LATEST"
if not latest.exists():
    print(f"no review run of 083 under {root}/results"); sys.exit(1)
cfgs = [p for p in sorted((root / "evaluation" / "campaigns").glob("*.json")) if sha083 in p.read_text()]
if len(cfgs) != 30:
    print(f"{len(cfgs)} configs of PREREG_extra_083 in {root}, expected 30"); sys.exit(1)
left = []
for p in cfgs:
    base = latest / "campaigns" / json.loads(p.read_text())["name"]
    ok = False
    for r in (sorted(base.glob("2*")) if base.exists() else []):
        m = r / "manifest.json"
        if (r / "activation.json").exists() or (m.exists() and "finished" in json.loads(m.read_text() or "{}")):
            ok = True
            break
    if not ok:
        left.append(p.stem)
if left:
    print(f"083 not finished in {latest.resolve()}: {len(left)} of 30 campaigns left: " + " ".join(left)); sys.exit(1)
print(latest.resolve())
PY
}
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
status_is() {  # status_is <stages.json key> <status> [<text in its summary>]
python3 - "$@" <<'PY'
import json, pathlib, sys
f = pathlib.Path("results/LATEST/stages.json")
st = json.loads(f.read_text()).get(sys.argv[1], {}) if f.exists() else {}
sys.exit(0 if st.get("status") == sys.argv[2] and (len(sys.argv) < 4 or sys.argv[3] in str(st.get("summary", ""))) else 1)
PY
}
M() {  # measure <stacks...> (the driver skips a stack that already passed in this run)
  local all=1; for s in "$@"; do status_is "measure:$s" PASS || all=0; done
  [ $all = 1 ] || RAN=1
  E measure "$@"
}
C() {  # campaigns in order; finished ones skipped, an interrupted one resumed
  for c in "$@"; do
    if finished "$c"; then echo "[campaign $c] finished earlier in this run: skipped"; continue; fi
    RAN=1; E campaign "$c" && continue
    if status_is "campaign:$c" FAIL "safety violations"; then
      echo; echo "######## STOP: unplanned safety violation in $c. Send the bundle below; run nothing else."
      E bundle --incremental; exit 3
    fi
  done
}
B() { [ $RAN = 1 ] && { E bundle --incremental; RAN=0; }; true; }  # only after new work

R083=$(done083) || { echo "######## REFUSED: $R083"; echo "######## finish release 083 first (bash /root/run083_part2.sh), then start this script"; exit 1; }
echo "######## release 083 finished: $R083"
NEW=""; [ -e results/LATEST ] || NEW=--new
E preflight build verify distribute $NEW --quick || { echo "######## build/verify FAIL: nothing run. Send: python3 evicon_review.py bundle"; exit 1; }
echo "######## review run: $(readlink -f results/LATEST)"
# (a) rule R5' against R5 and the stop on the orphan schedules, W = 6 then W unbounded
C expFI_s6 expFI_sinf; B
# (c) PERIODIC_FULL on the cutoff firmware (the firmware contract measured first, in this review run)
M zephyr-plain zephyr && C expPF_zephyr_w6 expPF_zephyr_winf
B

left=""; for c in expFI_s6 expFI_sinf expPF_zephyr_w6 expPF_zephyr_winf; do finished "$c" || left="$left $c"; done
echo
if [ -z "$left" ]; then echo "######## SESSION 1 DONE $(date '+%F %T'): 4 campaigns finished. Send the newest EVICON_084_result_*.zip; session 2 later: bash /root/EVICON_TOIT_084/run084_part2.sh"
else echo "######## SESSION 1 ENDED $(date '+%F %T') with campaigns not finished:$left"; echo "######## send the newest EVICON_084_result_*.zip (python3 evicon_review.py bundle if none was made)"; fi
