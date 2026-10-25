#!/bin/bash
# Starts the long IoT builds from VM-C, in the background on a build VM (release 068):
#
#   bash deploy/setup/build_iot.sh start          # Zephyr firmware (native_sim/native/64, nrf52840dk footprint; 077: MCU image for QEMU, networked nrf52840dk), on VM-D
#   bash deploy/setup/build_iot.sh start matter   # optional: Matter apps (chip-all-clusters-app, chip-tool), on VM-D
#   bash deploy/setup/build_iot.sh status         # progress: last steps, BUILD_OK / BUILD_FAIL
#   bash deploy/setup/build_iot.sh fetch          # status files, build logs, footprint -> results/logs/iot-build/
#
# `measure zephyr` needs the Zephyr build; release 068 measures no Matter device, so the Matter
# build is optional and not started by default (067 started both). Build VMs: VM-D for both
# (ZEPHYR_VM=e|b, MATTER_VM=e|b to change; BUILD_VM=<d|e|b> sends both to one VM). Do not build on
# VM-E while a campaign runs: a compile there disturbs the devices' timing. The firmware's broker
# address is VM-B's (testbed.json vm_b). The builds run under nohup (closing SSH or VS Code does not
# stop them, no tmux needed) and are idempotent: `start` again resumes after a failure. Workspaces
# go to /root/evicon-build, status to /var/lib/evicon/builds. Needs Internet on the build VM
# (GitHub, PyPI). Zephyr: about 6 GB of disk, 20-60 min; Matter: about 40 GB, 2-4 h. Nothing else on
# the VMs is stopped or changed (apt only installs the build packages).
set -uo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO" || exit 2
WHAT="${2:-zephyr}"
for w in $WHAT; do case "$w" in zephyr|matter) ;; *) echo "unknown build '$w' (zephyr | matter)"; exit 2;; esac; done
ZEPHYR_VM="${BUILD_VM:-${ZEPHYR_VM:-d}}"
MATTER_VM="${BUILD_VM:-${MATTER_VM:-d}}"
for v in "$ZEPHYR_VM" "$MATTER_VM"; do case "$v" in d|e|b) ;; *) echo "build VMs must be d, e or b"; exit 2;; esac; done
ipof() {  # testbed.json address of VM-<x>, defaults 192.168.107.15x
  python3 -c "import json,os,sys; c=json.load(open('testbed.json')) if os.path.exists('testbed.json') else {}; d={'a':'192.168.107.151','b':'192.168.107.152','c':'192.168.107.150','d':'192.168.107.153','e':'192.168.107.154'}; print(c.get('vm_'+sys.argv[1], d[sys.argv[1]]))" "$1"
}
VM_B_IP=$(ipof b) || { echo "testbed.json: cannot read it"; exit 2; }
BD=/var/lib/evicon/builds
ssh_to() { ssh -o BatchMode=yes -o ConnectTimeout=10 "root@$(ipof "$1")" "${@:2}"; }
vm_of() { if [ "$1" = zephyr ]; then echo "$ZEPHYR_VM"; else echo "$MATTER_VM"; fi; }

start_on() {  # start_on <vm letter> <builds...>
  local vm=$1; shift
  local name ip
  name="VM-$(echo "$vm" | tr a-z A-Z)"
  ip=$(ipof "$vm")
  ssh_to "$vm" true || { echo "FAIL: cannot SSH to root@$ip without a password (ssh-copy-id root@$ip)"; return 1; }
  # 069: also a build script whose wrapper shell is gone (068's preflight stopped the wrapper by mistake)
  if ssh_to "$vm" "{ [ -f $BD/build.pid ] && kill -0 \$(cat $BD/build.pid) 2>/dev/null && grep -q build_ /proc/\$(cat $BD/build.pid)/cmdline; } || pgrep -f 'deploy/setup/build_(zephyr|matter)[.]sh' >/dev/null"; then
    echo "a build is already running on $name (pid $(ssh_to "$vm" cat $BD/build.pid)); see: bash deploy/setup/build_iot.sh status"
    return 1
  fi
  ssh_to "$vm" "mkdir -p /root/evicon/firmware /root/evicon/deploy/setup $BD" || return 1
  tar czf - firmware/zephyr-cutoff firmware/zephyr-mcu firmware/zephyr-auth deploy/setup/build_zephyr.sh deploy/setup/build_matter.sh \
    | ssh_to "$vm" "tar xzf - -C /root/evicon" || { echo "FAIL: copying the build scripts to $name"; return 1; }
  local envs="EVC_BROKER_ADDR=$VM_B_IP "   # pinned versions can be overridden from VM-C: ZEPHYR_TAG=... MATTER_TAG=...
  for v in ZEPHYR_TAG MATTER_TAG; do
    local val="${!v:-}"
    if [ -n "$val" ]; then
      [[ "$val" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "$v must be a tag name"; return 2; }
      envs+="$v=$val "
    fi
  done
  local cmds=""
  for w in "$@"; do cmds+="${envs}bash /root/evicon/deploy/setup/build_$w.sh > $BD/$w.log 2>&1; "; done
  # 068: `cd && nohup ... &` put the whole list in a background subshell that kept ssh's output
  # open, so `start` waited for the build (hours) instead of returning; only nohup goes to the background
  ssh_to "$vm" "cd $BD || exit 1; nohup bash -c '$cmds' > $BD/build.out 2>&1 < /dev/null & echo \$! > $BD/build.pid" || return 1
  echo "started on $name ($ip): $* (pid $(ssh_to "$vm" cat $BD/build.pid))"
}

case "${1:-}" in
  start)
    rc=0
    declare -A ON=()
    for w in $WHAT; do ON[$(vm_of "$w")]+="$w "; done
    for vm in "${!ON[@]}"; do
      # shellcheck disable=SC2086
      start_on "$vm" ${ON[$vm]} || rc=1
    done
    echo "progress: bash deploy/setup/build_iot.sh status"
    exit $rc
    ;;
  status)
    for w in zephyr matter; do
      vm=$(vm_of "$w")
      echo "== $w on VM-$(echo "$vm" | tr a-z A-Z)"
      ssh_to "$vm" "pid=\$(cat $BD/build.pid 2>/dev/null); bp=\$(pgrep -f 'deploy/setup/build_$w[.]sh' | head -1); if [ -n \"\$pid\" ] && kill -0 \$pid 2>/dev/null; then echo \"build running (pid \$pid)\"; elif [ -n \"\$bp\" ]; then echo \"build running (pid \$bp)\"; else echo 'no build running'; fi;
                    df -h /root | tail -1 | sed 's/^/disk: /'; free -g | sed -n 2p | sed 's/^/memory: /';
                    [ -f $BD/$w.status ] && tail -n 8 $BD/$w.status || echo '(not started)'" || echo "(VM not reachable)"
    done
    ;;
  fetch)
    mkdir -p results/logs/iot-build
    for w in zephyr matter; do
      vm=$(vm_of "$w"); ip=$(ipof "$vm")
      for f in "$w.status" "$w.log" build.out; do
        scp -q -o BatchMode=yes "root@$ip:$BD/$f" "results/logs/iot-build/${w}-${f}" 2>/dev/null || true
      done
    done
    ip=$(ipof "$ZEPHYR_VM")
    for f in footprint.txt rom_report.txt ram_report.txt native-build.log zephyr-cutoff.config footprint-networked.txt \
             rom_report-networked.txt ram_report-networked.txt mcu-qemu-build.log zephyr-mcu-qemu.config footprint-auth.txt; do
      scp -q -o BatchMode=yes "root@$ip:/root/evicon-build/zephyr-out/$f" results/logs/iot-build/ 2>/dev/null || true
    done
    ls -la results/logs/iot-build
    ;;
  *) sed -n 2,18p "$0"; exit 2 ;;
esac
