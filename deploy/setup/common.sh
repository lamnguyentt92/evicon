# shellcheck shell=bash
# Shared helpers for deploy/setup/vm-*.sh (EviCon release 065, setup fix of 15/10). Sourced, not run.
# Every script is idempotent: running it again only installs what is missing.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

log()  { printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }
warn() { printf '[%s] WARN: %s\n' "$(date '+%F %T')" "$*" >&2; }
die()  { printf '[%s] FAIL: %s\n' "$(date '+%F %T')" "$*" >&2; exit 1; }

need_root() { [ "$(id -u)" = 0 ] || die "run as root (sudo -i)"; }

apt_wait() {
  # Another apt/dpkg (often unattended-upgrades or apt-daily right after boot) holds the
  # lock: wait for it instead of failing (065 setup on VM-A: "Could not get lock").
  local waited=0
  while fuser /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/lib/apt/lists/lock >/dev/null 2>&1 \
        || pgrep -x 'apt|apt-get|dpkg' >/dev/null 2>&1; do
    if [ "$waited" -ge "${APT_WAIT_MAX:-1200}" ]; then
      die "apt/dpkg is still busy after ${waited} s ($(pgrep -a -x 'apt|apt-get|dpkg' | head -3 | tr '\n' ';')); wait for it to finish, then run this script again"
    fi
    [ $((waited % 60)) = 0 ] && log "waiting for another apt/dpkg to finish: $(pgrep -a -x 'apt|apt-get|dpkg' | head -2 | tr '\n' ';')"
    sleep 5; waited=$((waited + 5))
  done
}

APT_OPTS=(-q -o DPkg::Lock::Timeout=600)

apt_install() {
  # installs only the packages that are missing; one apt-get update per run
  local missing=()
  for p in "$@"; do dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p"); done
  if [ "${#missing[@]}" -gt 0 ]; then
    apt_wait
    if [ -z "${APT_UPDATED:-}" ]; then apt-get "${APT_OPTS[@]}" update; APT_UPDATED=1; fi
    log "apt-get install ${missing[*]}"
    apt_wait
    apt-get "${APT_OPTS[@]}" install -y --no-install-recommends "${missing[@]}"
  else
    log "apt packages present: $*"
  fi
}

pip_install() {
  # system-wide pip of the python3 that runs EviCon (python3 -m pip, not a pip3 of another
  # Python); --break-system-packages only where pip knows it (Ubuntu 24.04)
  local flags=(--disable-pip-version-check -q)
  python3 -m pip --version >/dev/null 2>&1 || apt_install python3-pip
  if python3 -m pip install --help 2>/dev/null | grep -q -- --break-system-packages; then flags+=(--break-system-packages); fi
  log "python3 -m pip install $*"
  python3 -m pip install "${flags[@]}" "$@"
}

py_check() {
  # py_check module ... : prints each module's version; fails when one is missing
  local ok=1 v
  for m in "$@"; do
    if v=$(python3 - "$m" 2>/dev/null <<'PY'
import importlib, importlib.metadata as md, sys
m = sys.argv[1]
importlib.import_module(m)
dist = {"paho.mqtt.client": "paho-mqtt", "nats": "nats-py"}.get(m, m)
try:
    print(md.version(dist))
except Exception:
    print("?")
PY
    ); then
      log "python3 $(python3 -V 2>&1 | cut -d' ' -f2): $m $v"
    else
      warn "python3: module $m missing"; ok=0
    fi
  done
  [ "$ok" = 1 ]
}

time_sync() {
  apt_install chrony
  systemctl enable --now chrony >/dev/null 2>&1 || systemctl enable --now chronyd >/dev/null 2>&1 || warn "could not enable chrony"
  timedatectl set-ntp true >/dev/null 2>&1 || true
  log "time: $(timedatectl show -p NTPSynchronized --value 2>/dev/null || echo '?') (NTPSynchronized), $(date '+%F %T %z')"
}

evicon_dirs() {
  mkdir -p /root/evicon/bin /root/evicon/experiments/configs /var/lib/evicon /run/evicon
  log "directories: /root/evicon /var/lib/evicon /run/evicon"
}

docker_install() {
  if ! command -v docker >/dev/null 2>&1; then
    apt_install docker.io
  fi
  if ! docker compose version >/dev/null 2>&1; then
    # Ubuntu 22.04/24.04 ship Compose v2 as docker-compose-v2; Docker CE repositories call it docker-compose-plugin
    if apt-cache show docker-compose-v2 >/dev/null 2>&1; then apt_install docker-compose-v2
    elif apt-cache show docker-compose-plugin >/dev/null 2>&1; then apt_install docker-compose-plugin
    else die "no Docker Compose v2 package found (enable the 'universe' repository: add-apt-repository universe)"; fi
  fi
  systemctl enable --now docker >/dev/null 2>&1 || true
  docker info >/dev/null 2>&1 || die "the Docker daemon does not run (journalctl -u docker)"
  log "docker: $(docker --version); $(docker compose version)"
}

port_owner() { ss -ltnpH "sport = :$1" 2>/dev/null | head -1; }

stop_own() {
  # stop_own <workdir>: stops the processes EviCon started in <workdir> (its pid files; a pid
  # counts only while that process still runs in <workdir>) and removes the pid files.
  # Never signals another program, even one that reused a pid after a reboot.
  local d=$1 real p f
  [ -d "$d" ] || return 0
  real=$(cd "$d" && pwd -P) || return 0
  for f in "$d"/*.pid; do
    [ -f "$f" ] || continue
    p=$(cat "$f" 2>/dev/null || true)
    case "$p" in ''|*[!0-9]*) rm -f "$f"; continue;; esac
    if [ "$(readlink "/proc/$p/cwd" 2>/dev/null || true)" = "$real" ]; then
      kill -TERM "$p" 2>/dev/null || true
      for _ in $(seq 50); do kill -0 "$p" 2>/dev/null || break; sleep 0.2; done
      if [ "$(readlink "/proc/$p/cwd" 2>/dev/null || true)" = "$real" ]; then kill -KILL "$p" 2>/dev/null || true; fi
      log "stopped EviCon's own process $p ($d)"
    fi
    rm -f "$f"
  done
  return 0
}

ufw_allow() {
  # ufw_allow <from-ip> <port|first:last> <tcp|udp>: only when ufw is active; adds an allow
  # rule for one connection direction EviCon needs (never removes or changes other rules)
  if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    ufw allow from "$1" to any port "$2" proto "$3" >/dev/null && log "ufw: $1 -> $2/$3 allowed"
  fi
  return 0
}
