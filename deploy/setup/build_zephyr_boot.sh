#!/usr/bin/env bash
# Builds the boot-report variant of the EviCon cutoff firmware (release 087, firmware/zephyr-boot; PREREG_extra_087.md
# item B) on the build VM, in the Zephyr workspace that deploy/setup/build_zephyr.sh made (run that first: BUILD_OK):
#   firmware/zephyr-boot compiles the frozen sources of firmware/zephyr-cutoff unchanged plus src/evc_boot_report.c
#   (the claim floor in every reply and a hello after every connect), for native_sim/native/64 with the host toolchain,
#   into $OUT/zephyr-boot.exe and $OUT/zephyr-boot.config. The measured build $OUT/zephyr-cutoff.exe is never rebuilt or
#   touched: its SHA-256 is written before and after, and the build fails if it changed.
# Started by deploy/setup/build_iot.sh from VM-C (`start zephyr_boot`). About 5-10 minutes. Writes only under
# $BUILD_ROOT/boot-app, $OUT/native-boot, $OUT/zephyr-boot.* and its status file; never touches other programs.
set -uo pipefail
EVC_BROKER_ADDR="${EVC_BROKER_ADDR:?VM-B address missing: start the build with deploy/setup/build_iot.sh on VM-C}"
EVC_BROKER_PORT="${EVC_BROKER_PORT:-1883}"
[[ "$EVC_BROKER_ADDR" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "EVC_BROKER_ADDR must be an IPv4 address"; exit 2; }
BUILD_ROOT="${BUILD_ROOT:-/root/evicon-build}"
FW_SRC="${FW_SRC:-/root/evicon/firmware}"
STATUS="${STATUS_FILE:-/var/lib/evicon/builds/zephyr_boot.status}"
WS="$BUILD_ROOT/zephyr-ws"
VENV="$BUILD_ROOT/zephyr-venv"
OUT="$BUILD_ROOT/zephyr-out"
APPS="$BUILD_ROOT/boot-app"
mkdir -p "$OUT" "$(dirname "$STATUS")"
step() { echo "$(date -u +%FT%TZ) STEP $*" | tee -a "$STATUS"; }
fail() { echo "$(date -u +%FT%TZ) BOOT_BUILD_FAIL $*" | tee -a "$STATUS"; exit 1; }
: > "$STATUS"
step "start (boot-report variant) root=$BUILD_ROOT src=$FW_SRC"
[ -d "$WS/zephyr" ] || fail "no Zephyr workspace at $WS: run 'bash deploy/setup/build_iot.sh start' (the Zephyr build) first"
[ -f "$FW_SRC/zephyr-boot/CMakeLists.txt" ] && [ -f "$FW_SRC/zephyr-cutoff/src/main.c" ] || fail "firmware sources missing under $FW_SRC (build_iot.sh copies them)"
before=""
[ -s "$OUT/zephyr-cutoff.exe" ] && before=$(sha256sum "$OUT/zephyr-cutoff.exe" | cut -d' ' -f1)
echo "default sha256 before: ${before:-none}" | tee -a "$STATUS"
rm -rf "$APPS" && mkdir -p "$APPS" && cp -a "$FW_SRC/zephyr-cutoff" "$FW_SRC/zephyr-boot" "$APPS/" || fail "copying the sources"
[ -d "$VENV/bin" ] && export PATH="$VENV/bin:$PATH"
export ZEPHYR_BASE="$WS/zephyr"
export ZEPHYR_TOOLCHAIN_VARIANT=host
cd "$WS" || fail "cd $WS"
step "native_sim/native/64 build of firmware/zephyr-boot (broker $EVC_BROKER_ADDR:$EVC_BROKER_PORT)"
if ! west build -p always -b native_sim/native/64 "$APPS/zephyr-boot" -d "$OUT/native-boot" -- \
     -DCONFIG_EVC_BROKER_ADDR="\"$EVC_BROKER_ADDR\"" -DCONFIG_EVC_BROKER_PORT="$EVC_BROKER_PORT" > "$OUT/boot-build.log" 2>&1; then
  # a workspace without west metadata (e.g. a copied one): the same build with CMake and Ninja
  rm -rf "$OUT/native-boot"
  { cmake -GNinja -B "$OUT/native-boot" -S "$APPS/zephyr-boot" -DBOARD=native_sim/native/64 \
      -DCONFIG_EVC_BROKER_ADDR="\"$EVC_BROKER_ADDR\"" -DCONFIG_EVC_BROKER_PORT="$EVC_BROKER_PORT" && ninja -C "$OUT/native-boot"; } \
      >> "$OUT/boot-build.log" 2>&1 || { tail -40 "$OUT/boot-build.log" | tee -a "$STATUS"; fail "build (log $OUT/boot-build.log)"; }
fi
grep -q '^CONFIG_NET_NATIVE_OFFLOADED_SOCKETS=y' "$OUT/native-boot/zephyr/.config" || fail "build without host sockets (NSOS)"
cp -f "$OUT/native-boot/zephyr/zephyr.exe" "$OUT/zephyr-boot.exe" && cp -f "$OUT/native-boot/zephyr/.config" "$OUT/zephyr-boot.config" || fail "copying the build"
sha256sum "$OUT/zephyr-boot.exe" | sed 's/^/boot sha256: /' | tee -a "$STATUS"
grep -E '^CONFIG_EVC_(BROKER_ADDR|BROKER_PORT|DEVICE_ID|RING_SLOTS|TOMB_SLOTS|DEDUP_HORIZON_MS|GUARD_EVICTED)=' "$OUT/zephyr-boot.config" | sed 's/^/boot /' | tee -a "$STATUS"
after=""
[ -s "$OUT/zephyr-cutoff.exe" ] && after=$(sha256sum "$OUT/zephyr-cutoff.exe" | cut -d' ' -f1)
echo "default sha256 after: ${after:-none}" | tee -a "$STATUS"
[ "$before" = "$after" ] || fail "the measured build zephyr-cutoff.exe changed during this build"
echo "$(date -u +%FT%TZ) BOOT_BUILD_OK boot=$OUT/zephyr-boot.exe default_sha256=${after:-none}" | tee -a "$STATUS"
