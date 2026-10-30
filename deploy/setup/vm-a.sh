#!/bin/bash
# VM-A (authorities; Temporal activity worker; DBOS app). Run as root on VM-A, or from VM-C with setup_all.sh.
. "$(dirname "$0")/common.sh"
need_root
log "== VM-A setup (EviCon 082)"
apt_install python3 python3-pip libsqlite3-0 iproute2 tar ca-certificates
time_sync
evicon_dirs
# measure temporal: the reference worker (conformance/reference/temporal_worker.py)
python3 -c 'import temporalio' 2>/dev/null || pip_install "temporalio==1.34.0"
py_check temporalio
# measure dbos (068): the reference DBOS app (conformance/reference/dbos_app.py). DBOS Transact
# 3.x needs Python >= 3.10 (Ubuntu 22.04: 3.10, 24.04: 3.12). Only `measure dbos` needs it, so an
# older Python is a warning here and a FAIL of that measurement, not of the setup.
if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  have=$(python3 -c 'import importlib.metadata as m; print(m.version("dbos"))' 2>/dev/null || true)
  [ "$have" = "3.3.0" ] || pip_install "dbos==3.3.0"
  py_check dbos
  log "dbos: $(python3 -c 'import importlib.metadata as m; print(m.version("dbos"))')"
else
  warn "python3 on VM-A is $(python3 -V 2>&1): DBOS Transact 3.3.0 needs >= 3.10, so 'measure dbos' will FAIL (all other steps are unaffected)"
fi
ufw_allow "${VM_C:?VM_C unset: run this script through deploy/setup/setup_all.sh, which sets it from testbed.json}" 19000:19031 tcp   # 077: up to 32 authorities (scale)
ufw_allow "${VM_C:?VM_C unset: run this script through deploy/setup/setup_all.sh, which sets it from testbed.json}" 19100 tcp
ufw_allow "${VM_C:?VM_C unset: run this script through deploy/setup/setup_all.sh, which sets it from testbed.json}" 18300 tcp      # 068: the DBOS app's HTTP API (measure dbos)
o=$(port_owner 18300); [ -z "$o" ] || warn "port 18300 is in use: $o (measure dbos needs it free)"
log "VM-A ready"
