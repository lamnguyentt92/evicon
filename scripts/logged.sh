#!/bin/bash
# scripts/logged.sh NAME CMD [ARGS...]
# Runs CMD, shows its output, and appends that output, a header and the exit
# status to results/logs/NAME-<timestamp>.log (EVICON_LOG_DIR overrides the
# directory). `python3 evicon_review.py bundle` collects these logs.
set -uo pipefail
name=$1; shift
dir=${EVICON_LOG_DIR:-results/logs}
mkdir -p "$dir"
log="$dir/$name-$(date +%Y%m%dT%H%M%S).log"
{
  echo "### $(date '+%F %T %z') host=$(hostname) cwd=$(pwd) release=$(cat VERSION 2>/dev/null)"
  printf '### $'; printf ' %q' "$@"; echo
} >> "$log"
"$@" 2>&1 | tee -a "$log"
rc=${PIPESTATUS[0]}
echo "### exit $rc at $(date '+%F %T %z')" >> "$log"
if [ "$rc" -ne 0 ]; then
  echo "[evicon] '$name' failed (exit $rc); full output: $log" >&2
fi
exit "$rc"
