#!/bin/bash
# VM-C (the reviewer driver evicon_review.py: build, probes, campaigns, analysis). Run as root on VM-C.
. "$(dirname "$0")/common.sh"
need_root
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
log "== VM-C setup (EviCon 073) in $REPO"
apt_install python3 python3-pip gcc make pkg-config libsqlite3-dev curl tar unzip zip git ca-certificates \
            openjdk-17-jre-headless iproute2 openssh-client python3-matplotlib python3-yaml tmux openssl
time_sync
python3 - <<'PY' || die "Python >= 3.10 required on VM-C"
import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)
PY
# EviCon-Probe: adapters for the measured systems (versions pinned in conformance/requirements-conformance.txt)
want="aiocoap==0.4.17 paho-mqtt==2.1.0 temporalio==1.34.0 nats-py==2.9.0"
python3 -c 'import aiocoap, paho.mqtt.client, temporalio, nats' 2>/dev/null || pip_install $want
py_check aiocoap paho.mqtt.client temporalio
# measure k3s: the fault proxy terminates TLS with a throwaway certificate made by openssl (068)
command -v openssl >/dev/null || die "openssl missing (apt-get install -y openssl)"
# Go is pinned and installed by the driver into .tools/go (python3 evicon_review.py build)
[ -x "$REPO/.tools/go/bin/go" ] && log "go: $("$REPO/.tools/go/bin/go" version)" || log "go: installed by 'python3 evicon_review.py build'"
# SSH key for the driver (root@VM-C -> root@VM-A/B/D/E, BatchMode)
if [ ! -f /root/.ssh/id_ed25519 ] && [ ! -f /root/.ssh/id_rsa ]; then
  ssh-keygen -q -t ed25519 -N '' -f /root/.ssh/id_ed25519
  log "created /root/.ssh/id_ed25519; copy it to every VM: ssh-copy-id root@<VM-A/B/D/E>"
fi
# VM-A reaches the campaign fault proxies and the Temporal plant proxy on VM-C:28200-28215
ufw_allow "${VM_A:-192.168.107.151}" 28200:28215 tcp
# measure temporal: the pinned Temporal CLI (its dev server runs on VM-D; setup_all.sh copies it there)
python3 "$REPO/scripts/ensure_temporal_cli.py" \
  || warn "Temporal CLI missing: only 'measure temporal' needs it (no Internet on VM-C? python3 scripts/ensure_temporal_cli.py --archive <file>)"
log "VM-C ready"
