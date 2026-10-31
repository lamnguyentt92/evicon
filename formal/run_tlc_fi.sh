#!/usr/bin/env bash
# Release 084: TLC on the per-identity fence intake (rule R5') of EviconFenceFI.tla, as run_tlc.sh runs the
# release-062 model (same pinned tla2tools, same options). perid and perid_fenceonly must report "No error";
# nofc (the owner's answer omits UNCERTAIN identities: no FC) is a negative control and MUST report a violation.
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
OUT="${1:-${TMPDIR:-/tmp}/evicon-tlc-fi}"
mkdir -p "$OUT"
rm -f "$OUT/summary.txt"
status=0
for cfg in perid perid_fenceonly nofc; do
  log="$OUT/EviconFenceFI_$cfg.log"
  meta="$(mktemp -d "${TMPDIR:-/tmp}/evicon-tlc-states.XXXXXX")"
  (cd "$OUT" && java -XX:+UseParallelGC -cp "$JAR" tlc2.TLC -workers auto -deadlock \
      -metadir "$meta" -config "$HERE/EviconFenceFI_$cfg.cfg" "$HERE/MCEviconFenceFI.tla" >"$log" 2>&1) || true
  rm -rf "$meta"
  if grep -q "No error has been found" "$log"; then res=PASS_NO_ERROR
  elif grep -q "is violated" "$log"; then res="VIOLATION:$(grep -o 'Invariant [A-Za-z]* is violated' "$log" | head -1)"
  else res=TLC_FAILED; fi
  case "$cfg" in
    nofc) [[ "$res" == VIOLATION* ]] && verdict=EXPECTED || { verdict=UNEXPECTED; status=1; } ;;
    *) [[ "$res" == PASS_NO_ERROR ]] && verdict=EXPECTED || { verdict=UNEXPECTED; status=1; } ;;
  esac
  states=$(grep -E 'distinct states found' "$log" | tail -1 || true)
  printf '%-16s %-55s %-10s %s\n' "$cfg" "$res" "$verdict" "$states" | tee -a "$OUT/summary.txt"
done
exit $status
