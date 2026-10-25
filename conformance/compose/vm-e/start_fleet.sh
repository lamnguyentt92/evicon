#!/usr/bin/env bash
# VM-E (192.168.107.154): device side. Starts the plant (truth), then one
# device group. Usage: start_fleet.sh <preset> [count]   e.g. start_fleet.sh K3S 50
# Logs and journals go to /var/lib/evicon; stop with stop_fleet.sh.
set -euo pipefail
PRESET="${1:-K2}"; COUNT="${2:-1}"
BIN="${EVICON_BIN:-/root/evicon/bin}"
STATE=/var/lib/evicon; RUN=/run/evicon
mkdir -p "$STATE" "$RUN"
if ! curl -sf http://127.0.0.1:18090/healthz >/dev/null; then
  nohup "$BIN/evicon-plant" -log "$STATE/plant.jsonl" -http 0.0.0.0:18090 -unix "$RUN/plant.sock" > "$STATE/plant.log" 2>&1 &
  sleep 0.5
fi
nohup "$BIN/evicon-device" -count "$COUNT" -contract "$PRESET" -listen 0.0.0.0:18100 -admin-listen 0.0.0.0:18101 \
  -state-dir "$STATE/devices-$PRESET" -plant "unix:$RUN/plant.sock" > "$STATE/device-$PRESET.log" 2>&1 &
sleep 0.5
curl -sf http://127.0.0.1:18100/healthz && echo
echo "plant truth: curl http://192.168.107.154:18090/v1/truth/summary"
