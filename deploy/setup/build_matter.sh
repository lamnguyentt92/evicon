#!/usr/bin/env bash
# Builds the Matter reference apps on VM-E (release 067), unattended, for the Matter measurement
# (068/069): chip-all-clusters-app (the owner, a Linux Matter device) and chip-tool (controller).
#   1. apt packages the Matter SDK needs;  2. connectedhomeip at a pinned tag, Linux submodules only;
#   3. bootstrap (Pigweed environment, downloads several GB);  4. build both apps without BLE.
# Started by deploy/setup/build_iot.sh from VM-C. Idempotent. Needs Internet on VM-E, about 30 GB of
# disk and 8-16 GB RAM; takes 1-3 hours the first time.
# Writes only under $BUILD_ROOT (default /root/evicon-build); never touches other programs.
set -uo pipefail
MATTER_TAG="${MATTER_TAG:-v1.4.2.0}"
BUILD_ROOT="${BUILD_ROOT:-/root/evicon-build}"
STATUS="${STATUS_FILE:-/var/lib/evicon/builds/matter.status}"
SRC="$BUILD_ROOT/connectedhomeip"
OUT="$BUILD_ROOT/matter-out"
mkdir -p "$BUILD_ROOT" "$OUT" "$(dirname "$STATUS")"

step() { echo "$(date -u +%FT%TZ) STEP $*" | tee -a "$STATUS"; }
fail() { echo "$(date -u +%FT%TZ) BUILD_FAIL $*" | tee -a "$STATUS"; exit 1; }
: > "$STATUS"
step "start matter=$MATTER_TAG root=$BUILD_ROOT"
df -h "$BUILD_ROOT" | tail -1 | sed 's/^/disk: /' | tee -a "$STATUS"
free -g | sed -n 2p | sed 's/^/memory: /' | tee -a "$STATUS"

step "apt packages"
export DEBIAN_FRONTEND=noninteractive
APT=(apt-get -o DPkg::Lock::Timeout=1200)   # waits for unattended-upgrades instead of failing
"${APT[@]}" update -q || fail "apt-get update"
"${APT[@]}" install -y -q --no-install-recommends git gcc g++ pkg-config libssl-dev libdbus-1-dev libglib2.0-dev \
  libavahi-client-dev ninja-build python3-venv python3-dev python3-pip unzip libgirepository1.0-dev libcairo2-dev \
  libreadline-dev curl ca-certificates || fail "apt-get install"

step "source $MATTER_TAG"
if [ ! -d "$SRC/.git" ]; then
  git clone --depth 1 -b "$MATTER_TAG" https://github.com/project-chip/connectedhomeip.git "$SRC" \
    || fail "git clone (does tag $MATTER_TAG exist? set MATTER_TAG)"
fi
cd "$SRC" || fail "cd $SRC"
python3 scripts/checkout_submodules.py --shallow --platform linux || fail "submodules"
git describe --tags --always | sed 's/^/matter revision: /' | tee -a "$STATUS"

step "bootstrap (Pigweed environment)"
# activate.sh runs bootstrap the first time; it is not 'set -u' clean
set +u
# shellcheck disable=SC1091
source scripts/activate.sh > "$OUT/bootstrap.log" 2>&1 || { tail -40 "$OUT/bootstrap.log" | tee -a "$STATUS"; fail "bootstrap (log $OUT/bootstrap.log)"; }
set -u

for T in linux-x64-all-clusters-no-ble linux-x64-chip-tool-no-ble; do
  step "build $T"
  ./scripts/build/build_examples.py --target "$T" build > "$OUT/$T.log" 2>&1 \
    || { tail -40 "$OUT/$T.log" | tee -a "$STATUS"; fail "build $T (log $OUT/$T.log)"; }
done
cp -f out/linux-x64-all-clusters-no-ble/chip-all-clusters-app out/linux-x64-chip-tool-no-ble/chip-tool "$OUT/" || fail "copy binaries"
"$OUT/chip-tool" 2>&1 | head -3 | sed 's/^/chip-tool: /' | tee -a "$STATUS"
ls -la "$OUT/chip-all-clusters-app" "$OUT/chip-tool" | tee -a "$STATUS"
echo "$(date -u +%FT%TZ) BUILD_OK matter=$MATTER_TAG out=$OUT" | tee -a "$STATUS"
