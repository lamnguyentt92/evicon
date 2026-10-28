#!/bin/bash
# EviCon release 078: (re)initialises the testbed from VM-C, as root, after the VMs were installed again (new
# addresses, new host keys, no SSH keys). Nothing here changes a VM beyond root's SSH authorized_keys.
#
#   bash deploy/setup/testbed_init.sh detect          # this machine (VM-C): address, interface, subnet
#   bash deploy/setup/testbed_init.sh scan [CIDR]     # hosts answering SSH on VM-C's subnet (default: its /24)
#   bash deploy/setup/testbed_init.sh write --vm-a IP --vm-b IP --vm-d IP --vm-e IP [--vm-c IP] [--ssh-user root]
#                                                     # testbed.json; VM-C's address is detected when not given
#   bash deploy/setup/testbed_init.sh keys [--login USER] [--reset-host-keys]
#                                                     # VM-C's root key -> root on VM-A/B/D/E (asks the passwords
#                                                     # in this terminal: ssh-copy-id, then sudo once per VM)
#   bash deploy/setup/testbed_init.sh check           # SSH, OS, CPU, RAM, disk, clock, Internet of every VM
#
# The address of each VM is the one it has now: `hostname -I` on its console, or `scan` from VM-C. testbed.json
# is then the only place the addresses are kept; every EviCon script reads it (and refuses to run without it).
set -uo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO" || exit 2
OUT="${TESTBED_JSON:-$REPO/testbed.json}"
KEY=/root/.ssh/id_ed25519
[ "$(id -u)" = 0 ] || { echo "run as root on VM-C (sudo -i)"; exit 2; }

own_ip() {  # the address of the interface that carries the default route
  ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i <= NF; i++) if ($i == "src") {print $(i + 1); exit}}'
}
own_if() {
  ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i <= NF; i++) if ($i == "dev") {print $(i + 1); exit}}'
}

cmd_detect() {
  local ip ifc cidr
  ip=$(own_ip); ifc=$(own_if)
  if [ -z "$ip" ]; then
    ip=$(hostname -I 2>/dev/null | awk '{print $1}')
  fi
  [ -n "$ip" ] || { echo "no IPv4 address found on this machine"; return 1; }
  cidr=$(ip -o -4 addr show ${ifc:+dev "$ifc"} 2>/dev/null | awk -v a="$ip" '{split($4, x, "/"); if (x[1] == a) print $4}' | head -1)
  local gw dns
  gw=$(ip -4 route show default 2>/dev/null | awk '{print $3; exit}')
  dns=$(resolvectl dns ${ifc:+"$ifc"} 2>/dev/null | sed 's/^[^:]*: *//' | tr '\n' ' ')
  echo "VM-C (this machine): $ip   interface ${ifc:-?}   network ${cidr:-?}   hostname $(hostname)"
  echo "gateway ${gw:-?}   DNS ${dns:-?}"
  echo "all IPv4 addresses here: $(hostname -I 2>/dev/null)"
  echo "Keep the five addresses fixed until the last report (a DHCP reservation, or a static address in netplan):"
  echo "the firmware is built for VM-B's address, and testbed.json names all five."
}

cmd_scan() {
  local cidr="${1:-}"
  if [ -z "$cidr" ]; then
    local ip
    ip=$(own_ip)
    [ -n "$ip" ] || { echo "give the network, e.g. scan 192.168.10.0/24"; return 2; }
    cidr="${ip%.*}.0/24"
  fi
  python3 - "$cidr" "$(own_ip)" <<'PY'
import ipaddress, socket, sys
from concurrent.futures import ThreadPoolExecutor
net = ipaddress.ip_network(sys.argv[1], strict=False)
if net.num_addresses > 4096:
    raise SystemExit(f"{net}: more than 4096 addresses; give a /20 or smaller")
me = sys.argv[2]

def probe(a):
    a = str(a)
    try:
        with socket.create_connection((a, 22), timeout=0.6) as s:
            s.settimeout(1.5)
            try:
                banner = s.recv(128).decode(errors="replace").strip()
            except OSError:
                banner = "?"
    except OSError:
        return None
    try:
        name = socket.gethostbyaddr(a)[0]
    except OSError:
        name = "-"
    return a, banner, name

with ThreadPoolExecutor(64) as ex:
    found = [r for r in ex.map(probe, net.hosts()) if r]
print(f"hosts with SSH (port 22) on {net}:")
for a, banner, name in found:
    print(f"  {a:16s} {name:28s} {banner[:60]}" + ("   <- this machine (VM-C)" if a == me else ""))
print("Map them to VM-A/B/D/E with `hostname` on each VM's console if the names above do not say it, then:")
print("  bash deploy/setup/testbed_init.sh write --vm-a <IP> --vm-b <IP> --vm-d <IP> --vm-e <IP>")
PY
}

cmd_write() {
  local a="" b="" c="" d="" e="" user=root
  while [ $# -gt 0 ]; do
    case "$1" in
      --vm-a) a=$2; shift 2;; --vm-b) b=$2; shift 2;; --vm-c) c=$2; shift 2;;
      --vm-d) d=$2; shift 2;; --vm-e) e=$2; shift 2;; --ssh-user) user=$2; shift 2;;
      *) echo "unknown option $1"; return 2;;
    esac
  done
  [ -n "$c" ] || c=$(own_ip)
  for v in a b d e; do [ -n "${!v}" ] || { echo "--vm-$v <IP> missing"; return 2; }; done
  python3 - "$OUT" "$a" "$b" "$c" "$d" "$e" "$user" <<'PY'
import datetime, json, os, shutil, sys
sys.path.insert(0, os.getcwd())
import evicon_review as ER
out, a, b, c, d, e, user = sys.argv[1:]
cfg = {"vm_a": a, "vm_b": b, "vm_c": c, "vm_d": d, "vm_e": e, "ssh_user": user}
ER.check_testbed_cfg(dict(ER.DEFAULT_TESTBED, **cfg), "testbed_init write")
if os.path.exists(out):
    old = json.load(open(out))
    keep = {k: v for k, v in old.items() if k in ("remote_root", "state_dir", "temporal_address")}
    cfg.update(keep)
    bak = out + ".bak-" + datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    shutil.copy2(out, bak)
    print(f"previous {out} kept as {bak}")
open(out, "w").write(json.dumps(cfg, indent=2) + "\n")
print(f"wrote {out}:")
print(open(out).read().rstrip())
PY
}

ssh_root() {  # ssh_root <ip> <command...>: root, key only, new host keys accepted once
  ssh -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new "root@$1" "${@:2}"
}

cmd_keys() {
  local login="" reset=0
  while [ $# -gt 0 ]; do
    case "$1" in --login) login=$2; shift 2;; --reset-host-keys) reset=1; shift;; *) echo "unknown option $1"; return 2;; esac
  done
  eval "$(python3 deploy/setup/testbed_env.py --file "$OUT")" || return 2
  if [ ! -s "$KEY" ]; then
    mkdir -p /root/.ssh && chmod 700 /root/.ssh
    ssh-keygen -q -t ed25519 -N "" -C "evicon-vm-c-$(hostname)" -f "$KEY" || return 1
    echo "created VM-C's root key $KEY"
  fi
  local pub rc=0
  pub=$(cat "$KEY.pub")
  for role in A B D E; do
    local v="VM_$role" ipx err
    ipx=${!v}
    if [ "$reset" = 1 ]; then ssh-keygen -q -R "$ipx" >/dev/null 2>&1; fi
    err=$(ssh_root "$ipx" true 2>&1)
    if [ $? -eq 0 ]; then
      echo "VM-$role $ipx: root login with VM-C's key OK"
      continue
    fi
    if echo "$err" | grep -q "REMOTE HOST IDENTIFICATION HAS CHANGED"; then
      echo "VM-$role $ipx: the host key changed (the VM was installed again). If you re-installed it yourself:"
      echo "   bash deploy/setup/testbed_init.sh keys --reset-host-keys${login:+ --login $login}"
      rc=1; continue
    fi
    if [ -z "$login" ]; then
      echo "VM-$role $ipx: root login with the key fails ($(echo "$err" | tail -1)). Copying the key:"
      echo "   as root (needs root password login on that VM):"
      ssh-copy-id -i "$KEY.pub" -o StrictHostKeyChecking=accept-new "root@$ipx" || {
        echo "   failed: give the login of the user you created at installation (it has sudo):"
        echo "   bash deploy/setup/testbed_init.sh keys --login <user>"; rc=1; continue; }
    else
      echo "VM-$role $ipx: copying VM-C's key to $login, then to root (ssh asks $login's password, sudo asks it once more)"
      ssh-copy-id -i "$KEY.pub" -o StrictHostKeyChecking=accept-new "$login@$ipx" || { echo "   ssh-copy-id to $login@$ipx failed"; rc=1; continue; }
      ssh -t -o StrictHostKeyChecking=accept-new "$login@$ipx" \
        "sudo sh -c 'umask 077; mkdir -p /root/.ssh; touch /root/.ssh/authorized_keys; grep -qxF \"$pub\" /root/.ssh/authorized_keys || echo \"$pub\" >> /root/.ssh/authorized_keys; chmod 700 /root/.ssh; chmod 600 /root/.ssh/authorized_keys'" \
        || { echo "   copying the key to root on $ipx failed (is $login in the sudo group?)"; rc=1; continue; }
    fi
    if ssh_root "$ipx" true 2>/dev/null; then
      echo "VM-$role $ipx: root login with VM-C's key OK"
    else
      echo "VM-$role $ipx: root login still fails. On that VM: grep -i '^PermitRootLogin' /etc/ssh/sshd_config /etc/ssh/sshd_config.d/* "
      echo "   must not say 'no' (Ubuntu's default 'prohibit-password' allows keys); then: systemctl restart ssh"
      rc=1
    fi
  done
  [ "$rc" = 0 ] && echo "keys: VM-C reaches root on VM-A, VM-B, VM-D and VM-E without a password"
  return $rc
}

ver_lt() {  # ver_lt 2.35 2.39: true when the first version is older
  [ "$1" != "$2" ] && [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -1)" = "$1" ]
}

cmd_check() {
  eval "$(python3 deploy/setup/testbed_env.py --file "$OUT")" || return 2
  local rc=0
  declare -gA GLIBC=()
  printf '%-5s %-16s %-14s %-24s %4s %7s %8s %9s %8s %s\n' VM address hostname OS CPUs RAM_GiB disk_GiB clock_s Internet notes
  for role in C A B D E; do
    local v="VM_$role" ipx out
    ipx=${!v}
    local probe='h=$(hostname); . /etc/os-release 2>/dev/null; os="${ID:-?}-${VERSION_ID:-?}"; c=$(nproc);
      m=$(awk "/MemTotal/ {printf \"%.1f\", \$2/1048576}" /proc/meminfo); dsk=$(df -BG --output=avail / | tail -1 | tr -dc 0-9);
      n=$(curl -sS -m 8 -o /dev/null -w "%{http_code}" https://github.com 2>/dev/null || echo no);
      sync=$(timedatectl show -p NTPSynchronized --value 2>/dev/null || echo ?);
      dk=$(command -v docker >/dev/null && echo docker || echo -);
      g=$(ldd --version 2>&1 | head -1 | grep -o "[0-9]*\.[0-9]*$");
      echo "$h|$os|$c|$m|$dsk|$n|$sync|$dk|$g"'
    local tb ta t
    if [ "$role" = C ]; then
      out=$(bash -c "$probe" 2>/dev/null)
      tb=$(date +%s.%N); t=$(date +%s.%N); ta=$(date +%s.%N)
    else
      out=$(ssh_root "$ipx" "$probe" 2>/dev/null)
      tb=$(date +%s.%N); t=$(ssh_root "$ipx" date +%s.%N 2>/dev/null); ta=$(date +%s.%N)
    fi
    if [ -z "$out" ]; then
      printf '%-5s %-16s %s\n' "VM-$role" "$ipx" "NO ROOT SSH (bash deploy/setup/testbed_init.sh keys)"; rc=1; continue
    fi
    IFS='|' read -r h os c m dsk n sync dk g <<< "$out"
    GLIBC[$role]=${g:-?}
    local off note=""
    # the remote `date` runs at the end of the SSH round trip (after connection set-up): compare it with the return
    off=$(python3 -c "import sys; t, b, a = map(float, sys.argv[1:]); print(f'{t - a:+.2f}')" "$t" "$tb" "$ta" 2>/dev/null || echo ?)
    [ "$sync" = yes ] || note+="clock not NTP-synchronised; "
    python3 -c "import sys; sys.exit(0 if abs(float(sys.argv[1])) <= 1.0 else 1)" "$off" 2>/dev/null || note+="clock offset > 1 s; "
    case "$role" in
      B) awk "BEGIN {exit !($m < 6)}" && note+="RAM < 6 GiB (ThingsBoard + PostgreSQL); "
         [ "${dsk:-0}" -lt 20 ] && note+="disk < 20 GiB (images, MinIO build); ";;
      D) [ "${dsk:-0}" -lt 15 ] && note+="disk < 15 GiB (Zephyr workspace, SDK, builds); "
         awk "BEGIN {exit !($m < 4)}" && note+="RAM < 4 GiB (Zephyr builds); ";;
      E) [ "${c:-1}" -lt 2 ] && note+="1 CPU (devices, firmware, QEMU); ";;
      C) [ "${dsk:-0}" -lt 30 ] && note+="disk < 30 GiB (results; import-results needs up to 18 GiB, --lean about 10); ";;
    esac
    [ "$n" = 200 ] || [ "$n" = 301 ] || [ "$n" = 302 ] || note+="no HTTPS to github.com ($n); "
    printf '%-5s %-16s %-14s %-24s %4s %7s %8s %9s %8s %s\n' "VM-$role" "$ipx" "${h:0:14}" "${os:0:24}" "$c" "$m" "${dsk:-?}" "$off" "$n" "${note:-ok}"
  done
  # the binaries are built on VM-C (Go) and VM-D (the native_sim firmware, run on VM-E) and linked against
  # their C library: a VM with an older one cannot run them (e.g. built on Ubuntu 24.04, run on 22.04)
  local older=() r
  for r in A B D E; do
    [ -n "${GLIBC[$r]:-}" ] && [ -n "${GLIBC[C]:-}" ] && ver_lt "${GLIBC[$r]}" "${GLIBC[C]}" && older+=("VM-$r ${GLIBC[$r]}")
  done
  if [ "${#older[@]}" -gt 0 ]; then
    echo "WARN: glibc on VM-C is ${GLIBC[C]}, older on ${older[*]}: the binaries built on VM-C may not run there; install the same Ubuntu release on all five VMs"; rc=1
  fi
  if [ -n "${GLIBC[D]:-}" ] && [ -n "${GLIBC[E]:-}" ] && ver_lt "${GLIBC[E]}" "${GLIBC[D]}"; then
    echo "WARN: glibc on VM-D is ${GLIBC[D]}, on VM-E ${GLIBC[E]}: the firmware built on VM-D (native_sim) may not run on VM-E; use the same Ubuntu release"; rc=1
  fi
  return $rc
}

case "${1:-}" in
  detect) shift; cmd_detect "$@";;
  scan) shift; cmd_scan "$@";;
  write) shift; cmd_write "$@";;
  keys) shift; cmd_keys "$@";;
  check) shift; cmd_check "$@";;
  *) sed -n 2,16p "$0"; exit 2;;
esac
