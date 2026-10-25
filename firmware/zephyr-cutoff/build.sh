#!/usr/bin/env bash
# Build and run helpers for the EviCon cutoff firmware (a Zephyr workspace: west init/update
# done, ZEPHYR_BASE set, SDK installed). On the testbed, deploy/setup/build_iot.sh does all of
# this unattended (release 068); these helpers are for building by hand.
#   ./build.sh native [broker]   build the MQTT device for native_sim/native/64 (broker IPv4, default 192.168.107.152)
#   ./build.sh run <id>          run it (flash file under /var/lib/evicon/fw-<id>.bin persists the cutoff)
#   ./build.sh footprint <board> e.g. nrf52840dk/nrf52840 or esp32c6_devkitc/esp32c6/hpcore; prints ROM/RAM
#   ./build.sh sweep <board>     footprint for ring sizes 1 8 32 128 (cutoff + minimal ring vs dedup table)
set -euo pipefail
APP="$(cd "$(dirname "$0")" && pwd)"
case "${1:-}" in
  native)
    # 068: 64-bit native_sim with the host toolchain (no 32-bit multilib needed); host sockets (NSOS)
    ZEPHYR_TOOLCHAIN_VARIANT=host west build -p auto -b native_sim/native/64 "$APP" -d "$APP/build-native" -- \
      -DCONFIG_EVC_BROKER_ADDR="\"${2:-192.168.107.152}\"" ;;
  run)
    ID="${2:-zephyr-1}"; mkdir -p /var/lib/evicon
    # Zephyr v4.2.0 native_sim: -flash=<file> (the flash simulator's backing file)
    "$APP/build-native/zephyr/zephyr.exe" -flash="/var/lib/evicon/fw-$ID.bin" ;;
  footprint)
    B="${2:?board}"; D="$APP/build-fp-${B//\//_}"
    west build -p auto -b "$B" "$APP" -d "$D" -- -DEXTRA_CONF_FILE=footprint.conf
    west build -d "$D" -t rom_report > "$D/rom_report.txt"; west build -d "$D" -t ram_report > "$D/ram_report.txt"
    grep -E "evc_core|core|evc_" "$D/rom_report.txt" "$D/ram_report.txt" | head -40 ;;
  sweep)
    B="${2:?board}"
    for N in 1 8 32 128; do
      D="$APP/build-fp-${B//\//_}-ring$N"
      west build -p always -b "$B" "$APP" -d "$D" -- -DEXTRA_CONF_FILE=footprint.conf -DCONFIG_EVC_RING_SLOTS=$N >/dev/null
      printf "ring=%-4s " "$N"; size "$D/zephyr/zephyr.elf" | tail -1
    done ;;
  *) sed -n 2,8p "$0"; exit 2 ;;
esac
