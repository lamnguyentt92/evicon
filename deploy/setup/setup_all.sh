#!/bin/bash
# Prepares all five VMs from VM-C (EviCon, since release 065; 068: VM-B gets the compose files too). Run as root on VM-C,
# from the release:
#
#   bash deploy/setup/setup_all.sh                 # VM-C itself, then VM-A, VM-B, VM-D, VM-E over SSH
#   bash deploy/setup/setup_all.sh c e             # only some VMs
#
# Addresses come from testbed.json (vm_a ... vm_e, ssh_user) when present, else 192.168.107.150-154.
# Needs root SSH from VM-C to the other VMs without a password (ssh-copy-id root@<ip> once).
# Logs: results/logs/setup-<vm>-<stamp>.log. Each VM script is idempotent and can be run on its VM:
#   bash deploy/setup/vm-<a|b|c|d|e>.sh
# VM-D: copies the pinned Temporal CLI (scripts/ensure_temporal_cli.py) to /root/evicon/bin/temporal,
# runs vm-d.sh, then starts EviCon's Temporal dev server once on VM-D:7243, checks it from VM-C
# (Python SDK, namespace "default") and from VM-A (TCP), and stops it again. `measure temporal`
# starts and stops it the same way. With "temporal_address" in testbed.json, that Temporal is
# checked instead (deploy/setup/temporal_check.py --create).
set -uo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO" || exit 2
STAMP=$(date +%Y%m%dT%H%M%S)
mkdir -p results/logs
cfg_sh=$(python3 - <<'PY'
import json, os, shlex
cfg = {"vm_a": "192.168.107.151", "vm_b": "192.168.107.152", "vm_c": "192.168.107.150",
       "vm_d": "192.168.107.153", "vm_e": "192.168.107.154", "ssh_user": "root", "state_dir": "/var/lib/evicon",
       "temporal_address": ""}
if os.path.exists("testbed.json"):
    cfg.update({k: v for k, v in json.load(open("testbed.json")).items() if k in cfg})
for k, v in cfg.items():
    print(f"{k.upper()}={shlex.quote(str(v or ''))}")
PY
) || { echo "testbed.json: cannot read it (must be JSON, see ARTIFACT.md section 5)"; exit 2; }
eval "$cfg_sh"
VMS="${*:-c a b d e}"
for vm in $VMS; do case "$vm" in a|b|c|d|e) ;; *) echo "unknown VM '$vm' (use: a b c d e)"; exit 2;; esac; done
declare -A IP=([a]=$VM_A [b]=$VM_B [c]=$VM_C [d]=$VM_D [e]=$VM_E)
declare -A RESULT
WARNLOGS=()
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=10)
ENVS="VM_A=$VM_A VM_B=$VM_B VM_C=$VM_C VM_D=$VM_D VM_E=$VM_E TEMPORAL_ADDRESS=$TEMPORAL_ADDRESS"
TPORT=7243
TWD="$STATE_DIR/measure/temporal/temporal-server"   # the same workdir `measure temporal` uses
TEXE=/root/evicon/bin/temporal

arch_of() { case "$1" in x86_64|amd64) echo amd64;; aarch64|arm64) echo arm64;; *) echo "$1";; esac; }

temporal_push() {  # copies the pinned Temporal CLI to VM-D when VM-D does not have the same bytes
  local t=$1 arch exe want have
  arch=$(arch_of "$("${SSH[@]}" "$t" uname -m)") || return 1
  python3 scripts/ensure_temporal_cli.py --arch "$arch" || return 1
  if [ "$arch" = "$(arch_of "$(uname -m)")" ]; then exe=.tools/temporal; else exe=.tools/temporal-linux-$arch; fi
  want=$(sha256sum "$exe" | cut -d' ' -f1)
  have=$("${SSH[@]}" "$t" "sha256sum $TEXE 2>/dev/null | cut -d' ' -f1")
  if [ "$want" != "$have" ]; then
    echo "copying $exe ($(du -h "$exe" | cut -f1)) to $t:$TEXE"
    "${SSH[@]}" "$t" "mkdir -p $(dirname $TEXE)" \
      && scp -q -o BatchMode=yes -o ConnectTimeout=10 "$exe" "$t:$TEXE.part" \
      && "${SSH[@]}" "$t" "chmod 755 $TEXE.part && mv -f $TEXE.part $TEXE" || return 1
    have=$("${SSH[@]}" "$t" "sha256sum $TEXE | cut -d' ' -f1")
    [ "$want" = "$have" ] || { echo "FAIL: $TEXE on VM-D differs from $exe after the copy"; return 1; }
  fi
  echo "Temporal CLI on VM-D: $TEXE (sha256 ${want:0:16}..., same as VM-C's $exe)"
}

temporal_stop() {  # stops EviCon's own server on VM-D (pid file in $TWD, process still running there)
  "${SSH[@]}" "$1" ". /root/evicon/deploy/setup/common.sh && stop_own $TWD"
}

temporal_smoke() {  # starts EviCon's Temporal dev server on VM-D once, checks it from VM-C and VM-A, stops it
  local t=$1 rc=0 o
  temporal_stop "$t"  # a leftover of an interrupted measurement or setup
  o=$("${SSH[@]}" "$t" "ss -ltnpH 'sport = :$TPORT'")
  if [ -n "$o" ]; then
    echo "FAIL: port $TPORT on VM-D is in use by another program: $o (EviCon never stops other programs: stop it, then re-run)"
    return 1
  fi
  echo "starting EviCon's Temporal dev server on VM-D 0.0.0.0:$TPORT (in-memory; stopped again after the check)"
  trap 'temporal_stop "'"$t"'"; exit 130' INT TERM
  "${SSH[@]}" "$t" "rm -rf $TWD && mkdir -p $TWD && cd $TWD && (nohup $TEXE server start-dev --ip 0.0.0.0 --port $TPORT --headless --log-level warn > temporal-server.log 2>&1 & echo \$! > temporal-server.pid)" || rc=1
  [ "$rc" = 0 ] && { python3 deploy/setup/temporal_check.py --address "$VM_D:$TPORT" --namespace default --wait 90 || rc=1; }
  if [ "$rc" = 0 ]; then
    if "${SSH[@]}" "$SSH_USER@$VM_A" "timeout 4 bash -c '</dev/tcp/$VM_D/$TPORT'" 2>/dev/null; then
      echo "VM-A reaches $VM_D:$TPORT"
    else
      echo "FAIL: VM-A cannot reach $VM_D:$TPORT (firewall on VM-D? ufw allow from $VM_A to any port $TPORT proto tcp)"; rc=1
    fi
  fi
  if [ "$rc" != 0 ]; then
    echo "-- $TWD/temporal-server.log on VM-D:"
    "${SSH[@]}" "$t" "tail -n 20 $TWD/temporal-server.log 2>/dev/null" | cut -c1-300 | sed 's/^/   | /'
  fi
  temporal_stop "$t"
  trap - INT TERM
  [ "$rc" = 0 ] && echo "Temporal check PASS: VM-D:$TPORT served namespace default to VM-C, VM-A reached it; server stopped again"
  return $rc
}

for vm in $VMS; do
  log="results/logs/setup-vm-$vm-$STAMP.log"
  echo "== VM-${vm^^} (${IP[$vm]}) -> $log"
  if [ "$vm" = c ]; then
    env VM_A="$VM_A" VM_B="$VM_B" VM_C="$VM_C" VM_D="$VM_D" VM_E="$VM_E" bash deploy/setup/vm-c.sh 2>&1 | tee "$log"; rc=${PIPESTATUS[0]}
  else
    t="$SSH_USER@${IP[$vm]}"
    if ! "${SSH[@]}" "$t" true 2>>"$log"; then
      echo "   cannot SSH to $t without a password: run 'ssh-copy-id $t' first" | tee -a "$log"
      RESULT[$vm]="FAIL (ssh)"; continue
    fi
    extra=""
    [ "$vm" = b ] && extra="conformance/compose/vm-b"   # 068: vm-b.sh builds the MinIO image from minio/Dockerfile
    # shellcheck disable=SC2086
    tar czf - --exclude=__pycache__ deploy/setup/common.sh "deploy/setup/vm-$vm.sh" $extra | "${SSH[@]}" "$t" 'mkdir -p /root/evicon && tar xzf - -C /root/evicon' 2>>"$log"
    rc=0
    if [ "$vm" = d ] && [ -z "$TEMPORAL_ADDRESS" ]; then
      temporal_push "$t" 2>&1 | tee -a "$log"; rc=${PIPESTATUS[0]}
    fi
    if [ "$rc" = 0 ]; then
      "${SSH[@]}" "$t" "$ENVS bash /root/evicon/deploy/setup/vm-$vm.sh" 2>&1 | tee -a "$log"; rc=${PIPESTATUS[0]}
    fi
    if [ "$vm" = d ] && [ "$rc" = 0 ]; then
      if [ -z "$TEMPORAL_ADDRESS" ]; then
        echo "-- Temporal check (start on VM-D, check from VM-C and VM-A, stop)" | tee -a "$log"
        temporal_smoke "$t" 2>&1 | tee -a "$log"; rc=${PIPESTATUS[0]}
      else
        echo "-- existing Temporal at $TEMPORAL_ADDRESS (testbed.json temporal_address)" | tee -a "$log"
        python3 deploy/setup/temporal_check.py --address "$TEMPORAL_ADDRESS" --namespace default --create 2>&1 | tee -a "$log"; rc=${PIPESTATUS[0]}
      fi
      if [ "$rc" != 0 ]; then
        "${SSH[@]}" "$t" "bash /root/evicon/deploy/setup/vm-d.sh --diagnose" 2>&1 | tee -a "$log"
      fi
    fi
  fi
  RESULT[$vm]=$([ "$rc" = 0 ] && echo OK || echo "FAIL (rc=$rc, see $log)")
  # 069: a warning (e.g. the MinIO image build failed) is not a FAIL, but it must not hide behind "OK"
  nw=$(grep -c "WARN:" "$log" 2>/dev/null || true)
  if [ "$rc" = 0 ] && [ "${nw:-0}" -gt 0 ]; then RESULT[$vm]="OK, $nw warning(s) (listed below)"; WARNLOGS+=("$log"); fi
done
echo
echo "== setup summary"
for vm in $VMS; do printf '   VM-%s %-16s %s\n' "${vm^^}" "${IP[$vm]}" "${RESULT[$vm]:-?}"; done
if [ "${#WARNLOGS[@]}" -gt 0 ]; then
  echo "== warnings (setup continues; each says which measurement it affects)"
  for l in "${WARNLOGS[@]}"; do grep -h "WARN:" "$l" | sed 's/^/   /'; done
fi
echo "next: python3 evicon_review.py preflight --new"
for vm in $VMS; do case "${RESULT[$vm]:-}" in OK) ;; *) exit 1;; esac; done
