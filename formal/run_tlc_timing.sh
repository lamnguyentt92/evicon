#!/usr/bin/env bash
# Release 085 (review M1): TLC on the timing boundary cases of EviconTiming.tla, with the pinned tla2tools of
# run_tlc.sh and run_tlc_fi.sh and the same options. The expected result of every configuration was written before
# the first run (PREREG_extra_085.md, section 9):
#   firstsend_after_expiry           no error   (a first send after t0 + e + eps never takes effect: NoEffect)
#   firstsend_before_expiry_witness  VIOLATION  of NoHiddenEffect (the witness of Theorem 2(b) for a first send <= t0 + e)
#   firstsend_boundary_witness       VIOLATION  of NoHiddenEffect (first send = t0 + e + eps: the boundary is tight)
#   original_after_validity          VIOLATION  of NoLateEffect (the dispatcher's rule: an original sent after V takes effect late)
#   reissue_after_validity           VIOLATION  of NoLateEffect (a queued re-issue that leaves after V - D takes effect late)
#   validity_every_attempt           no error   (validity checked for every attempt, re-issue included: NoLateEffect)
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
OUT="${1:-${TMPDIR:-/tmp}/evicon-tlc-timing}"
mkdir -p "$OUT"
rm -f "$OUT/summary.txt"
status=0
for cfg in firstsend_after_expiry firstsend_before_expiry_witness firstsend_boundary_witness original_after_validity \
           reissue_after_validity validity_every_attempt; do
  log="$OUT/EviconTiming_$cfg.log"
  meta="$(mktemp -d "${TMPDIR:-/tmp}/evicon-tlc-states.XXXXXX")"
  (cd "$OUT" && java -XX:+UseParallelGC -cp "$JAR" tlc2.TLC -workers auto -deadlock \
      -metadir "$meta" -config "$HERE/EviconTiming_$cfg.cfg" "$HERE/EviconTiming.tla" >"$log" 2>&1) || true
  rm -rf "$meta"
  if grep -q "No error has been found" "$log"; then res=PASS_NO_ERROR
  elif grep -q "is violated" "$log"; then res="VIOLATION:$(grep -o 'Invariant [A-Za-z]* is violated' "$log" | head -1)"
  else res=TLC_FAILED; fi
  case "$cfg" in
    firstsend_before_expiry_witness|firstsend_boundary_witness)
      [[ "$res" == "VIOLATION:Invariant NoHiddenEffect is violated" ]] && verdict=EXPECTED || { verdict=UNEXPECTED; status=1; } ;;
    original_after_validity|reissue_after_validity)
      [[ "$res" == "VIOLATION:Invariant NoLateEffect is violated" ]] && verdict=EXPECTED || { verdict=UNEXPECTED; status=1; } ;;
    *) [[ "$res" == PASS_NO_ERROR ]] && verdict=EXPECTED || { verdict=UNEXPECTED; status=1; } ;;
  esac
  states=$(grep -E 'distinct states found' "$log" | tail -1 || true)
  printf '%-32s %-50s %-10s %s\n' "$cfg" "$res" "$verdict" "$states" | tee -a "$OUT/summary.txt"
done
exit $status
