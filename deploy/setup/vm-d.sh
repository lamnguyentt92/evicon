#!/bin/bash
# VM-D (Temporal server for `measure temporal`). Run as root on VM-D, or from VM-C with
# setup_all.sh, which first copies the pinned Temporal CLI to /root/evicon/bin/temporal.
#
#   bash vm-d.sh              # prepare VM-D
#   bash vm-d.sh --diagnose   # only print the state of EviCon's Temporal on VM-D
#
# Fix of 15/10: EviCon no longer uses the Temporal that already runs on VM-D (port 7233,
# docker, from an older deployment: it did not answer from VM-C). `measure temporal`
# starts its own `temporal server start-dev` (Temporal CLI 1.3.0 = Temporal Server 1.27.1,
# in-memory, namespace "default" built in) on 0.0.0.0:7243 and stops it when the measurement
# ends. No Docker needed. The other Temporal is left as it is.
. "$(dirname "$0")/common.sh"
need_root
PORT="${TEMPORAL_PORT:-7243}"
EXE=/root/evicon/bin/temporal
SD=/var/lib/evicon/measure/temporal/temporal-server

diagnose() {
  log "Temporal CLI: $("$EXE" --version 2>/dev/null || echo "missing ($EXE; setup_all.sh copies it from VM-C)")"
  log "listener on $PORT: $(port_owner "$PORT" || true)"
  log "Temporal processes: $(pgrep -a -x temporal | head -3 | tr '\n' ';' || true)"
  log "firewall: $( (ufw status 2>/dev/null || echo 'ufw not installed') | head -1)"
  for f in "$SD/temporal-server.log"; do
    [ -f "$f" ] && { log "last lines of $f:"; tail -n 15 "$f" | cut -c1-300 | sed 's/^/   | /'; }
  done
  log "free memory: $(free -m | awk '/^Mem:/ {print $7 " MiB available of " $2}')"
  return 0
}
if [ "${1:-}" = "--diagnose" ]; then diagnose; exit 0; fi

log "== VM-D setup (EviCon 078)"
apt_install iproute2 tar ca-certificates
time_sync
evicon_dirs
mkdir -p /var/lib/evicon/measure
if [ "${SKIP_TEMPORAL:-0}" = 1 ]; then
  log "EviCon's Temporal not set up (SKIP_TEMPORAL=1; only 'measure temporal' needs it: bash deploy/setup/setup_all.sh d)"
  log "VM-D ready"; exit 0
fi
if [ -n "${TEMPORAL_ADDRESS:-}" ]; then
  log "testbed.json temporal_address=$TEMPORAL_ADDRESS: measure temporal uses that Temporal, not EviCon's own"
  log "VM-D ready"; exit 0
fi
if [ -x "$EXE" ] && v=$("$EXE" --version 2>/dev/null); then
  log "Temporal CLI: $v ($EXE)"
  case "$v" in "temporal version 1.3.0 "*) ;; *) die "$EXE is not Temporal CLI 1.3.0 ($v): run bash deploy/setup/setup_all.sh d on VM-C";; esac
else
  die "$EXE missing or does not run: run 'bash deploy/setup/setup_all.sh d' on VM-C (it copies the pinned Temporal CLI here)"
fi
# VM-C (probes) and VM-A (worker) connect to it
ufw_allow "${VM_C:?VM_C unset: run this script through deploy/setup/setup_all.sh, which sets it from testbed.json}" "$PORT" tcp
ufw_allow "${VM_A:?VM_A unset: run this script through deploy/setup/setup_all.sh, which sets it from testbed.json}" "$PORT" tcp
stop_own "$SD"   # EviCon's own server, left by an interrupted measurement or setup
o=$(port_owner "$PORT")
[ -z "$o" ] || warn "port $PORT is in use: $o (measure temporal needs it free; EviCon never stops other programs)"
o=$(port_owner 7233)
[ -z "$o" ] || log "port 7233 (the other Temporal, not used by EviCon): $o"
log "free memory: $(free -m | awk '/^Mem:/ {print $7 " MiB available of " $2}')"
log "VM-D ready (setup_all.sh then starts the server once on $PORT and checks it from VM-C and VM-A)"
