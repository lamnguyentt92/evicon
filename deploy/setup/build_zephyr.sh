#!/usr/bin/env bash
# Builds the EviCon cutoff firmware with Zephyr on the build VM (release 068), unattended:
#   1. apt packages Zephyr needs;   2. west in a venv;   3. Zephyr workspace (pinned tag), only the
#      modules the two builds need (cmsis_6, hal_nordic);   4. Zephyr SDK, ARM toolchain only;
#   5. firmware for native_sim/native/64 with the host toolchain (a 64-bit Linux process that runs on
#      VM-E; broker address from EVC_BROKER_ADDR, set by build_iot.sh from testbed.json);
#   6. footprint sweep for nrf52840dk (ROM/RAM for ring sizes 1, 8, 32, 128; build only, no flash).
#   Release 077, after BUILD_OK (a failure here does not touch the native build; status MCU_BUILD_OK/FAIL):
#   7. firmware/zephyr-mcu (the same sources, unchanged) for mps2/an385: an Arm Cortex-M3 image that VM-E runs
#      under qemu-system-arm with Zephyr's own TCP/IP stack (`measure zephyr-mcu`); broker VM-B, plant
#      10.0.2.2:18092 (VM-E through QEMU's user-mode NAT), as Kconfig values;
#   8. firmware/zephyr-mcu for nrf52840dk/nrf52840 with networking (OpenThread over the 802.15.4 radio, TCP,
#      MQTT, NVS): flash/RAM footprint of the networked device (build only);
#   9. firmware/zephyr-auth (request and reply MACs, cutoff bound) for native_sim/native/64 (`measure zephyr-auth`)
#      and for nrf52840dk/nrf52840 (footprint), with a random device key made for this build: it is kept in
#      $OUT/zephyr-auth.key (mode 0600, never copied into results) and reaches the build in a conf file of mode
#      0600 (never on a command line); the driver reads it over SSH for the check and passes it in the environment.
# Started by deploy/setup/build_iot.sh from VM-C. Idempotent: a finished step is skipped on a re-run.
# Needs Internet (GitHub, PyPI) and about 6 GB of disk. Takes 20-60 minutes the first time.
# Release 068: the same steps were run in the build environment (Zephyr v4.2.0, SDK 0.17.x): both
# builds succeed, and the native firmware was measured end to end (EviCon-Probe: K3).
# Writes only under $BUILD_ROOT (default /root/evicon-build); never touches other programs.
set -uo pipefail
ZEPHYR_TAG="${ZEPHYR_TAG:-v4.2.0}"
EVC_BROKER_ADDR="${EVC_BROKER_ADDR:?VM-B address missing: start the build with deploy/setup/build_iot.sh on VM-C (078: from testbed.json)}"
EVC_BROKER_PORT="${EVC_BROKER_PORT:-1883}"
[[ "$EVC_BROKER_ADDR" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "EVC_BROKER_ADDR must be an IPv4 address"; exit 2; }
BUILD_ROOT="${BUILD_ROOT:-/root/evicon-build}"
APP_SRC="${APP_SRC:-/root/evicon/firmware/zephyr-cutoff}"
STATUS="${STATUS_FILE:-/var/lib/evicon/builds/zephyr.status}"
WS="$BUILD_ROOT/zephyr-ws"
VENV="$BUILD_ROOT/zephyr-venv"
OUT="$BUILD_ROOT/zephyr-out"
mkdir -p "$BUILD_ROOT" "$OUT" "$(dirname "$STATUS")"

step() { echo "$(date -u +%FT%TZ) STEP $*" | tee -a "$STATUS"; }
fail() { echo "$(date -u +%FT%TZ) BUILD_FAIL $*" | tee -a "$STATUS"; exit 1; }
: > "$STATUS"
step "start zephyr=$ZEPHYR_TAG root=$BUILD_ROOT app=$APP_SRC"
[ -f "$APP_SRC/CMakeLists.txt" ] || fail "firmware source missing at $APP_SRC (run build_iot.sh from VM-C, it copies it)"
df -h "$BUILD_ROOT" | tail -1 | sed 's/^/disk: /' | tee -a "$STATUS"

step "apt packages"
export DEBIAN_FRONTEND=noninteractive
APT=(apt-get -o DPkg::Lock::Timeout=1200)   # waits for unattended-upgrades instead of failing
"${APT[@]}" update -q || fail "apt-get update"
"${APT[@]}" install -y -q --no-install-recommends git cmake ninja-build gperf ccache dfu-util device-tree-compiler wget \
  python3-dev python3-pip python3-venv python3-setuptools python3-wheel xz-utils file make gcc g++ \
  libmagic1 || fail "apt-get install"

step "west"
[ -x "$VENV/bin/west" ] || { python3 -m venv "$VENV" && "$VENV/bin/pip" install -q west; } || fail "west venv"
export PATH="$VENV/bin:$PATH"

step "workspace $ZEPHYR_TAG"
if [ ! -d "$WS/.west" ]; then
  west init -m https://github.com/zephyrproject-rtos/zephyr --mr "$ZEPHYR_TAG" "$WS" || fail "west init"
fi
cd "$WS" || fail "cd $WS"
# only the modules the two builds use (native_sim needs none; nrf52840dk needs CMSIS 6 and the Nordic HAL)
# release 077: + OpenThread and mbed TLS for the networked nrf52840dk build (step 8)
west config manifest.project-filter -- '-.*,+cmsis_6,+hal_nordic,+openthread,+mbedtls' || fail "west config project-filter"
west update --narrow -o=--depth=1 || west update || fail "west update"
west zephyr-export || fail "west zephyr-export"
"$VENV/bin/pip" install -q -r "$WS/zephyr/scripts/requirements.txt" || fail "zephyr python requirements"
git -C "$WS/zephyr" describe --tags --always | sed 's/^/zephyr revision: /' | tee -a "$STATUS"

step "Zephyr SDK"
if ! ls -d "$HOME"/zephyr-sdk-* >/dev/null 2>&1; then
  if ! west sdk install -t arm-zephyr-eabi; then
    # `west sdk install` asks the GitHub API for the release; without it (rate limit, proxy), the
    # same files straight from the release page, checked against the release's sha256.sum
    SDKV=$(cat "$WS/zephyr/SDK_VERSION") || fail "SDK_VERSION"
    U="https://github.com/zephyrproject-rtos/sdk-ng/releases/download/v$SDKV"
    T="$BUILD_ROOT/sdk-dl" && mkdir -p "$T" && cd "$T" || fail "sdk download dir"
    for f in "zephyr-sdk-${SDKV}_linux-x86_64_minimal.tar.xz" toolchain_linux-x86_64_arm-zephyr-eabi.tar.xz sha256.sum; do
      [ -s "$f" ] || wget -q -O "$f" "$U/$f" || fail "download $f"
    done
    grep -E " (zephyr-sdk-${SDKV}_linux-x86_64_minimal|toolchain_linux-x86_64_arm-zephyr-eabi)\.tar\.xz$" sha256.sum | sha256sum -c - \
      || fail "SDK checksum"
    tar xf "zephyr-sdk-${SDKV}_linux-x86_64_minimal.tar.xz" -C "$HOME" && tar xf toolchain_linux-x86_64_arm-zephyr-eabi.tar.xz -C "$HOME/zephyr-sdk-$SDKV" \
      && "$HOME/zephyr-sdk-$SDKV/setup.sh" -c || fail "SDK install"
    cd "$WS" || fail "cd $WS"
  fi
fi
ls -d "$HOME"/zephyr-sdk-* | sed 's/^/sdk: /' | tee -a "$STATUS"

export ZEPHYR_BASE="$WS/zephyr"
APP="$BUILD_ROOT/zephyr-cutoff"
rm -rf "$APP" && cp -a "$APP_SRC" "$APP"
# release 077: the MCU variant builds the sources of ../zephyr-cutoff (copied next to it)
APP_MCU_SRC="${APP_MCU_SRC:-$(dirname "$APP_SRC")/zephyr-mcu}"
APP_MCU="$BUILD_ROOT/zephyr-mcu"
rm -rf "$APP_MCU"
[ -d "$APP_MCU_SRC" ] && cp -a "$APP_MCU_SRC" "$APP_MCU"
APP_AUTH_SRC="${APP_AUTH_SRC:-$(dirname "$APP_SRC")/zephyr-auth}"
APP_AUTH="$BUILD_ROOT/zephyr-auth"
rm -rf "$APP_AUTH"
[ -d "$APP_AUTH_SRC" ] && cp -a "$APP_AUTH_SRC" "$APP_AUTH"

step "native_sim/native/64 build (broker $EVC_BROKER_ADDR:$EVC_BROKER_PORT)"
ZEPHYR_TOOLCHAIN_VARIANT=host west build -p always -b native_sim/native/64 "$APP" -d "$OUT/native" -- \
  -DCONFIG_EVC_BROKER_ADDR="\"$EVC_BROKER_ADDR\"" -DCONFIG_EVC_BROKER_PORT="$EVC_BROKER_PORT" > "$OUT/native-build.log" 2>&1 \
  || { tail -40 "$OUT/native-build.log" | tee -a "$STATUS"; fail "native_sim build (log $OUT/native-build.log)"; }
grep -q '^CONFIG_NET_NATIVE_OFFLOADED_SOCKETS=y' "$OUT/native/zephyr/.config" || fail "native_sim build without host sockets (NSOS)"
# stable names for `measure zephyr` (evicon_review.py copies them to VM-E and checks the broker address)
cp -f "$OUT/native/zephyr/zephyr.exe" "$OUT/zephyr-cutoff.exe" && cp -f "$OUT/native/zephyr/.config" "$OUT/zephyr-cutoff.config" \
  || fail "copying the native firmware"
sha256sum "$OUT/zephyr-cutoff.exe" | sed 's/^/native sha256: /' | tee -a "$STATUS"
grep -E '^CONFIG_EVC_(BROKER_ADDR|BROKER_PORT|DEVICE_ID|RING_SLOTS|TOMB_SLOTS|DEDUP_HORIZON_MS)=' "$OUT/zephyr-cutoff.config" | sed 's/^/native /' | tee -a "$STATUS"

step "footprint nrf52840dk"
: > "$OUT/footprint.txt"
for N in 1 8 32 128; do
  D="$OUT/fp-nrf52840dk-ring$N"
  if ZEPHYR_TOOLCHAIN_VARIANT=zephyr west build -p always -b nrf52840dk/nrf52840 "$APP" -d "$D" -- -DEXTRA_CONF_FILE=footprint.conf \
       -DCONFIG_EVC_RING_SLOTS=$N > "$D.log" 2>&1; then
    printf "ring=%-4s " "$N" >> "$OUT/footprint.txt"
    size "$D/zephyr/zephyr.elf" | tail -1 >> "$OUT/footprint.txt"
  else
    tail -30 "$D.log" | tee -a "$STATUS"
    fail "footprint build ring=$N (log $D.log)"
  fi
done
west build -d "$OUT/fp-nrf52840dk-ring32" -t rom_report > "$OUT/rom_report.txt" 2>&1 || true
west build -d "$OUT/fp-nrf52840dk-ring32" -t ram_report > "$OUT/ram_report.txt" 2>&1 || true
sed 's/^/footprint: /' "$OUT/footprint.txt" | tee -a "$STATUS"
echo "$(date -u +%FT%TZ) BUILD_OK zephyr=$ZEPHYR_TAG native=$OUT/zephyr-cutoff.exe footprint=$OUT/footprint.txt" | tee -a "$STATUS"

# ---- release 077: MCU builds (never change the native build above) ------------------------------------
mcu_fail() { echo "$(date -u +%FT%TZ) MCU_BUILD_FAIL $*" | tee -a "$STATUS"; exit 0; }
[ -d "$APP_MCU" ] || mcu_fail "firmware/zephyr-mcu missing (run build_iot.sh from VM-C of release 077, it copies it)"
step "MCU build mps2/an385 (QEMU Cortex-M3; broker $EVC_BROKER_ADDR:$EVC_BROKER_PORT, plant 10.0.2.2:18092)"
ZEPHYR_TOOLCHAIN_VARIANT=zephyr west build -p always -b mps2/an385 "$APP_MCU" -d "$OUT/mcu-qemu" -- \
  -DCONFIG_EVC_BROKER_ADDR="\"$EVC_BROKER_ADDR\"" -DCONFIG_EVC_BROKER_PORT="$EVC_BROKER_PORT" \
  -DCONFIG_EVC_PLANT_ADDR="\"10.0.2.2\"" -DCONFIG_EVC_PLANT_PORT=18092 > "$OUT/mcu-qemu-build.log" 2>&1 \
  || { tail -40 "$OUT/mcu-qemu-build.log" | tee -a "$STATUS"; mcu_fail "mps2/an385 build (log $OUT/mcu-qemu-build.log)"; }
cp -f "$OUT/mcu-qemu/zephyr/zephyr.elf" "$OUT/zephyr-mcu-qemu.elf" && cp -f "$OUT/mcu-qemu/zephyr/.config" "$OUT/zephyr-mcu-qemu.config" \
  || mcu_fail "copying the MCU image"
sha256sum "$OUT/zephyr-mcu-qemu.elf" | sed 's/^/mcu sha256: /' | tee -a "$STATUS"
size "$OUT/zephyr-mcu-qemu.elf" | tail -1 | sed 's/^/mcu qemu size (text data bss dec): /' | tee -a "$STATUS"
step "MCU build nrf52840dk/nrf52840 networked (OpenThread, TCP, MQTT, NVS; footprint only)"
ZEPHYR_TOOLCHAIN_VARIANT=zephyr west build -p always -b nrf52840dk/nrf52840 "$APP_MCU" -d "$OUT/mcu-nrf52840dk" \
  > "$OUT/mcu-nrf52840dk-build.log" 2>&1 \
  || { tail -40 "$OUT/mcu-nrf52840dk-build.log" | tee -a "$STATUS"; mcu_fail "nrf52840dk networked build (log $OUT/mcu-nrf52840dk-build.log)"; }
{ echo "nrf52840dk/nrf52840 networked (firmware/zephyr-mcu, OpenThread MTD + TCP + MQTT + NVS)"; size "$OUT/mcu-nrf52840dk/zephyr/zephyr.elf";
  grep -E "^ +(FLASH|RAM):" "$OUT/mcu-nrf52840dk-build.log" | tail -2; } > "$OUT/footprint-networked.txt"
west build -d "$OUT/mcu-nrf52840dk" -t rom_report > "$OUT/rom_report-networked.txt" 2>&1 || true
west build -d "$OUT/mcu-nrf52840dk" -t ram_report > "$OUT/ram_report-networked.txt" 2>&1 || true
sed 's/^/footprint networked: /' "$OUT/footprint-networked.txt" | tee -a "$STATUS"
[ -d "$APP_AUTH" ] || mcu_fail "firmware/zephyr-auth missing (run build_iot.sh from VM-C of release 077, it copies it)"
step "auth firmware native_sim/native/64 (broker $EVC_BROKER_ADDR:$EVC_BROKER_PORT) and nrf52840dk footprint"
( umask 077
  [ -s "$OUT/zephyr-auth.key" ] || python3 -c 'import secrets; print(secrets.token_hex(32), end="")' > "$OUT/zephyr-auth.key"
  printf 'CONFIG_EVC_AUTH_KEY="%s"\n' "$(cat "$OUT/zephyr-auth.key")" > "$OUT/zephyr-auth-key.conf" ) || mcu_fail "device key"
ZEPHYR_TOOLCHAIN_VARIANT=host west build -p always -b native_sim/native/64 "$APP_AUTH" -d "$OUT/auth-native" -- \
  -DEXTRA_CONF_FILE="$OUT/zephyr-auth-key.conf" -DCONFIG_EVC_BROKER_ADDR="\"$EVC_BROKER_ADDR\"" -DCONFIG_EVC_BROKER_PORT="$EVC_BROKER_PORT" \
  > "$OUT/auth-native-build.log" 2>&1 || { tail -40 "$OUT/auth-native-build.log" | tee -a "$STATUS"; mcu_fail "auth native build (log $OUT/auth-native-build.log)"; }
cp -f "$OUT/auth-native/zephyr/zephyr.exe" "$OUT/zephyr-auth.exe" || mcu_fail "copying the auth firmware"
chmod 700 "$OUT/auth-native" && chmod 600 "$OUT/auth-native/zephyr/.config" "$OUT/auth-native/CMakeCache.txt" 2>/dev/null
sha256sum "$OUT/zephyr-auth.exe" | sed 's/^/auth sha256: /' | tee -a "$STATUS"
ZEPHYR_TOOLCHAIN_VARIANT=zephyr west build -p always -b nrf52840dk/nrf52840 "$APP_AUTH" -d "$OUT/auth-nrf52840dk" -- \
  -DEXTRA_CONF_FILE="$OUT/zephyr-auth-key.conf" > "$OUT/auth-nrf52840dk-build.log" 2>&1 \
  || { tail -40 "$OUT/auth-nrf52840dk-build.log" | tee -a "$STATUS"; mcu_fail "auth nrf52840dk build (log $OUT/auth-nrf52840dk-build.log)"; }
chmod 700 "$OUT/auth-nrf52840dk"
{ echo "nrf52840dk/nrf52840 networked + authentication (firmware/zephyr-auth)"; size "$OUT/auth-nrf52840dk/zephyr/zephyr.elf";
  grep -E "^ +(FLASH|RAM):" "$OUT/auth-nrf52840dk-build.log" | tail -2; } > "$OUT/footprint-auth.txt"
sed 's/^/footprint auth: /' "$OUT/footprint-auth.txt" | tee -a "$STATUS"
echo "$(date -u +%FT%TZ) MCU_BUILD_OK qemu=$OUT/zephyr-mcu-qemu.elf footprint=$OUT/footprint-networked.txt auth=$OUT/zephyr-auth.exe" | tee -a "$STATUS"
