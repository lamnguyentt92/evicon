#!/usr/bin/env bash
# Release 087 (PREREG_extra_087.md item B): TLC on EviconFenceReboot.tla (the {F} firmware's fence across owner reboots, the
# authority's anchor, the reboot recovery REC-1 to REC-3 and the gateway seal GW-3), as run_tlc_fi.sh runs EviconFenceFI.tla
# (same pinned tla2tools, same options). Expected:
#   recovery, recovery_nocheck, r086        No error (BoundSafety, AtMostOncePerIntent, NoFalseStopped, NoFalseApplied,
#                                           FloorPartition; recovery also NeverConflicted: the floor-relative check never
#                                           fails under the eviction guard, the formal side of rule RB-C1)
#   ownerstate                              BoundSafety violated (the negative control: the reset level adopted)
#   noguard                                 AtMostOncePerIntent violated (the eviction guard is a premise: FB)
#   recovery_witness                        NoAnchorAfterReboot violated (the recovery commits an anchor after a reboot)
#   r086_conflict_witness                   NeverConflicted violated (release 086 conflicts after a reboot)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
JAR="${TLA2TOOLS_JAR:-$HERE/.tools/tla2tools-1.7.4.jar}"
URL="https://github.com/tlaplus/tlaplus/releases/download/v1.7.4/tla2tools.jar"
SHA="936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88"
if [ ! -f "$JAR" ] && [ -z "${TLA2TOOLS_JAR:-}" ]; then
  mkdir -p "$(dirname "$JAR")"
  curl -fsSL --retry 3 -o "$JAR.part" "$URL" || { echo "download of $URL failed; set TLA2TOOLS_JAR" >&2; exit 2; }
  mv "$JAR.part" "$JAR"
fi
[ -f "$JAR" ] || { echo "TLA2TOOLS_JAR=$JAR not found" >&2; exit 2; }
echo "$SHA  $JAR" | sha256sum -c - >/dev/null || { echo "$JAR fails its pinned checksum" >&2; exit 2; }
OUT="${1:-${TMPDIR:-/tmp}/evicon-tlc-reboot}"
mkdir -p "$OUT"
rm -f "$OUT/summary.txt"
status=0
for cfg in recovery recovery_nocheck r086 ownerstate noguard recovery_witness r086_conflict_witness; do
  log="$OUT/EviconFenceReboot_$cfg.log"
  meta="$(mktemp -d "${TMPDIR:-/tmp}/evicon-tlc-states.XXXXXX")"
  (cd "$OUT" && java -XX:+UseParallelGC -cp "$JAR" tlc2.TLC -workers auto -deadlock \
      -metadir "$meta" -config "$HERE/EviconFenceReboot_$cfg.cfg" "$HERE/EviconFenceReboot.tla" >"$log" 2>&1) || true
  rm -rf "$meta"
  if grep -q "No error has been found" "$log"; then res=PASS_NO_ERROR
  elif grep -q "is violated" "$log"; then res="VIOLATION:$(grep -o 'Invariant [A-Za-z]* is violated' "$log" | head -1)"
  else res=TLC_FAILED; fi
  case "$cfg" in
    ownerstate) want="VIOLATION:Invariant BoundSafety is violated";;
    noguard) want="VIOLATION:Invariant AtMostOncePerIntent is violated";;
    recovery_witness) want="VIOLATION:Invariant NoAnchorAfterReboot is violated";;
    r086_conflict_witness) want="VIOLATION:Invariant NeverConflicted is violated";;
    *) want=PASS_NO_ERROR;;
  esac
  [ "$res" = "$want" ] && verdict=EXPECTED || { verdict=UNEXPECTED; status=1; }
  states=$(grep -E 'distinct states found' "$log" | tail -1 || true)
  printf '%-22s %-55s %-10s %s\n' "$cfg" "$res" "$verdict" "$states" | tee -a "$OUT/summary.txt"
done
exit $status
