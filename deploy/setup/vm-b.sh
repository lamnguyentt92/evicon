#!/bin/bash
# VM-B (EviCon endpoint for the positive controls; owner for `measure mosquitto|emqx|nats|minio|
# dbos|k3s|thingsboard|zephyr`). Run as root on VM-B, or from VM-C with setup_all.sh (which also
# copies conformance/compose/vm-b here).
. "$(dirname "$0")/common.sh"
need_root
log "== VM-B setup (EviCon 083)"
apt_install libsqlite3-0 iproute2 tar ca-certificates
time_sync
evicon_dirs
docker_install
# pinned owner images (docker compose pulls them too; pulling now shows network problems early
# and keeps the first measurement short). 068: PostgreSQL (DBOS, ThingsBoard), k3s, ThingsBoard.
for IMG in "${MOSQUITTO_IMAGE:-eclipse-mosquitto:2.0.20}" "${EMQX_IMAGE:-emqx/emqx:5.8.6}" "${NATS_IMAGE:-nats:2.10.22}" \
           "${DBOS_PG_IMAGE:-postgres:16.10}" "${K3S_IMAGE:-rancher/k3s:v1.36.4-k3s1}" "${TB_IMAGE:-thingsboard/tb-node:4.3.1.6}"; do
  if docker image inspect "$IMG" >/dev/null 2>&1; then
    log "image present: $IMG"
  elif docker pull -q "$IMG"; then
    log "pulled $IMG: $(docker image inspect --format '{{.RepoDigests}}' "$IMG")"
  else
    warn "could not pull $IMG (no Internet on VM-B?). On a host with Internet: docker pull $IMG && docker save $IMG | gzip > img.tar.gz; copy it to VM-B; docker load < img.tar.gz"
  fi
done
# MinIO (068): no published images any more (Docker Hub removed minio/minio in September 2026,
# quay.io answers 401, dl.min.io 410). Built here from the official source of the release EviCon
# pins (conformance/compose/vm-b/minio/Dockerfile: tag and commit checked). 10-20 min the first
# time; `measure minio` builds it too when it is missing. SKIP_MINIO_BUILD=1 skips it here.
MINIO_REF="${MINIO_IMAGE:-evicon/minio-src:RELEASE.2025-04-22T22-12-26Z}"
MINIO_CTX="$(cd "$(dirname "$0")/../.." && pwd)/conformance/compose/vm-b/minio"
if docker image inspect "$MINIO_REF" >/dev/null 2>&1; then
  log "image present: $MINIO_REF"
elif [ -n "${MINIO_IMAGE:-}" ]; then
  docker pull -q "$MINIO_REF" >/dev/null && log "pulled $MINIO_REF" || warn "could not pull MINIO_IMAGE=$MINIO_REF"
elif [ "${SKIP_MINIO_BUILD:-0}" = 1 ]; then
  log "MinIO image not built (SKIP_MINIO_BUILD=1); 'measure minio' builds it"
elif [ -f "$MINIO_CTX/Dockerfile" ]; then
  mkdir -p /var/lib/evicon/builds
  log "building $MINIO_REF from the MinIO source (10-20 min; log /var/lib/evicon/builds/minio-image.log)"
  # 069: the build runs on the host network: on the testbed, DNS answers on VM-B itself but not
  # inside Docker containers (068: "Could not resolve host: github.com"); a proxy set on VM-B is
  # passed on (docker build does not forward it by itself)
  PROXY_ARGS=()
  for v in HTTP_PROXY HTTPS_PROXY NO_PROXY http_proxy https_proxy no_proxy; do
    [ -n "${!v:-}" ] && PROXY_ARGS+=(--build-arg "$v=${!v}")
  done
  if docker build --network=host "${PROXY_ARGS[@]}" -t "$MINIO_REF" "$MINIO_CTX" > /var/lib/evicon/builds/minio-image.log 2>&1; then
    log "built $MINIO_REF: $(docker run --rm "$MINIO_REF" --version 2>/dev/null | head -1)"
  else
    tail -n 15 /var/lib/evicon/builds/minio-image.log | sed 's/^/   | /' >&2
    warn "building $MINIO_REF failed (needs github.com, proxy.golang.org and Docker Hub from VM-B); 'measure minio' and the MinIO campaigns (expR_minio_winf, expC16_minio) need it and build it again when it is missing"
  fi
else
  warn "$MINIO_CTX/Dockerfile missing (run this script through setup_all.sh, which copies it); 'measure minio' builds the image"
fi
ufw_allow "${VM_C:?VM_C unset: run this script through deploy/setup/setup_all.sh, which sets it from testbed.json}" 18181 tcp
ufw_allow "${VM_C:?VM_C unset: run this script through deploy/setup/setup_all.sh, which sets it from testbed.json}" 1883 tcp
ufw_allow "${VM_E:?VM_E unset: run this script through deploy/setup/setup_all.sh, which sets it from testbed.json}" 1883 tcp
ufw_allow "${VM_C:?VM_C unset: run this script through deploy/setup/setup_all.sh, which sets it from testbed.json}" 1884 tcp     # 067: EMQX
ufw_allow "${VM_E:?VM_E unset: run this script through deploy/setup/setup_all.sh, which sets it from testbed.json}" 1884 tcp
ufw_allow "${VM_C:?VM_C unset: run this script through deploy/setup/setup_all.sh, which sets it from testbed.json}" 4222 tcp     # 067: NATS
ufw_allow "${VM_C:?VM_C unset: run this script through deploy/setup/setup_all.sh, which sets it from testbed.json}" 9000 tcp     # 067: MinIO
ufw_allow "${VM_A:?VM_A unset: run this script through deploy/setup/setup_all.sh, which sets it from testbed.json}" 5433 tcp     # 068: PostgreSQL of the DBOS app (VM-A)
ufw_allow "${VM_C:?VM_C unset: run this script through deploy/setup/setup_all.sh, which sets it from testbed.json}" 5433 tcp     # 068: the driver checks that PostgreSQL serves
ufw_allow "${VM_C:?VM_C unset: run this script through deploy/setup/setup_all.sh, which sets it from testbed.json}" 16443 tcp    # 068: k3s API server
ufw_allow "${VM_C:?VM_C unset: run this script through deploy/setup/setup_all.sh, which sets it from testbed.json}" 18088 tcp    # 068: ThingsBoard REST
ufw_allow "${VM_E:?VM_E unset: run this script through deploy/setup/setup_all.sh, which sets it from testbed.json}" 1885 tcp     # 068: ThingsBoard MQTT (reference device on VM-E)
o=$(port_owner 1883); [ -z "$o" ] || warn "port 1883 is in use: $o (measure mosquitto|zephyr needs it free; EviCon never stops other programs)"
for pp in 1884 18083 4222 8222 9000 9001 5433 16443 18088 1885; do
  o=$(port_owner $pp); [ -z "$o" ] || warn "port $pp is in use: $o (measure emqx|nats|minio|dbos|k3s|thingsboard needs it free)"
done
o=$(port_owner 18181); [ -z "$o" ] || warn "port 18181 is in use: $o (the EviCon endpoint of the controls needs it)"
log "disk on VM-B: $(df -h /var/lib/docker 2>/dev/null | tail -1 | awk '{print $4}') free for docker (ThingsBoard + MinIO build: about 6 GB)"
log "VM-B ready"
