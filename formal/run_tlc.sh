#!/usr/bin/env bash
# Run TLC on every EviconFence configuration. Requires Java 11+.
# Safe models must report "No error"; naive, blindretry and sealorphans are
# negative controls and MUST report an invariant violation.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# Pinned to the stable release v1.7.4 (TLC 2.19): its asset is immutable. The
# v1.8.0 tag is a rolling pre-release whose jar changes (release 062 note).
# TLA2TOOLS_JAR=/path/to/tla2tools.jar uses a local copy (its hash is printed).
JAR="${TLA2TOOLS_JAR:-$HERE/.tools/tla2tools-1.7.4.jar}"
URL="https://github.com/tlaplus/tlaplus/releases/download/v1.7.4/tla2tools.jar"
SHA="936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88"
if [ -n "${TLA2TOOLS_JAR:-}" ]; then
  [ -f "$JAR" ] || { echo "TLA2TOOLS_JAR=$JAR not found" >&2; exit 2; }
  echo "using $JAR (sha256 $(sha256sum "$JAR" | cut -d' ' -f1))"
else
  if [ ! -f "$JAR" ]; then
    mkdir -p "$(dirname "$JAR")"
    curl -fsSL --retry 3 -o "$JAR.part" "$URL" || { echo "download of $URL failed; set TLA2TOOLS_JAR" >&2; exit 2; }
    if ! echo "$SHA  $JAR.part" | sha256sum -c - >/dev/null 2>&1; then
      echo "tla2tools.jar checksum mismatch (got $(sha256sum "$JAR.part" | cut -d' ' -f1)); set TLA2TOOLS_JAR to a trusted copy" >&2
      rm -f "$JAR.part"; exit 2
    fi
    mv "$JAR.part" "$JAR"
  fi
  echo "$SHA  $JAR" | sha256sum -c - >/dev/null || { echo "cached $JAR fails its pinned checksum" >&2; exit 2; }
fi
# Output (logs, summary) and TLC's state store live OUTSIDE the source tree by
# default: the state store of the safe model alone exceeds 1 GB.
OUT="${1:-${TMPDIR:-/tmp}/evicon-tlc}"
mkdir -p "$OUT"
rm -f "$OUT/summary.txt"
status=0
for cfg in safe reconcile nodedup stoponly fenceonly naive blindretry sealorphans; do
  log="$OUT/EviconFence_$cfg.log"
  meta="$(mktemp -d "${TMPDIR:-/tmp}/evicon-tlc-states.XXXXXX")"
  (cd "$OUT" && java -XX:+UseParallelGC -cp "$JAR" tlc2.TLC -workers auto -deadlock \
      -metadir "$meta" -config "$HERE/EviconFence_$cfg.cfg" "$HERE/MCEviconFence.tla" >"$log" 2>&1) || true
  rm -rf "$meta"
  if grep -q "No error has been found" "$log"; then res=PASS_NO_ERROR
  elif grep -q "is violated" "$log"; then res="VIOLATION:$(grep -o 'Invariant [A-Za-z]* is violated' "$log" | head -1)"
  else res=TLC_FAILED; fi
  case "$cfg" in
    naive|blindretry|sealorphans) [[ "$res" == VIOLATION* ]] && verdict=EXPECTED || { verdict=UNEXPECTED; status=1; } ;;
    *) [[ "$res" == PASS_NO_ERROR ]] && verdict=EXPECTED || { verdict=UNEXPECTED; status=1; } ;;
  esac
  states=$(grep -E 'distinct states found' "$log" | tail -1 || true)
  printf '%-10s %-55s %-10s %s\n' "$cfg" "$res" "$verdict" "$states" | tee -a "$OUT/summary.txt"
done
exit $status
