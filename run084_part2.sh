#!/bin/bash
# EviCon 084, session 2 of 2: plan PREREG_extra_084, (c) PERIODIC_FULL on MinIO (expPF_minio_w6, expPF_minio_winf) and then
# the final report of the paper (the data freeze), in the same review run as session 1. About 3.6 h.
#   tmux new -s evicon        (or tmux attach -t evicon)
#   bash /root/EVICON_TOIT_084/run084_part2.sh 2>&1 | tee -a /root/run084.log
# It may be stopped (Ctrl-C) and started again with the same command: finished campaigns are skipped and an interrupted
# one is resumed. The report runs only when all 6 campaigns of the plan finished; it reads the archive, the 078 run, the
# 083 run and this run. It refuses to start while release 083's plan is unfinished.
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
[ -e results/LATEST ] || { echo "######## no review run of 084 yet: run session 1 first (bash /root/EVICON_TOIT_084/run084_part1.sh)"; exit 1; }
E preflight build verify distribute --quick || { echo "######## build/verify FAIL: nothing run. Send: python3 evicon_review.py bundle"; exit 1; }
# copy 084's binaries to the VMs again, so session 2 runs on exactly the release of session 1 whatever ran in between
E distribute --force || { echo "######## distribute FAIL: nothing run. Send: python3 evicon_review.py bundle"; exit 1; }
echo "######## review run: $(readlink -f results/LATEST)"
# (c) PERIODIC_FULL on MinIO's conditional PUT, W = 6 then W unbounded
C expPF_minio_w6 expPF_minio_winf; B

left=""; for f in evaluation/campaigns/*.json; do
  python3 -c 'import json,sys; sys.exit(0 if (json.load(open(sys.argv[1])).get("preregistration") or {}).get("path") == "evaluation/prereg/PREREG_extra_084.md" else 1)' "$f" || continue  # the plan's 6 configs
  c=$(basename "$f" .json); finished "$c" || left="$left $c"; done
echo
if [ -n "$left" ]; then
  echo "######## not finished yet:$left"
  echo "######## no report: run session 1 / session 2 again (they resume), or send the bundle if a campaign keeps failing"
  B
else
  if E report --archive /root/evicon-archive --from /root/EVICON_TOIT_078/results/review-20261028T080322 --from "$R083"; then
    E bundle --incremental
    echo; echo "######## ALL DONE $(date '+%F %T'). Send the newest EVICON_084_result_*.zip"
  else
    E bundle --incremental
    echo; echo "######## REPORT FAIL $(date '+%F %T'): the campaigns are done. Send the newest EVICON_084_result_*.zip and the end of /root/run084.log"
  fi
fi
