#!/bin/bash
# VM-E (truth plant, evicon-device fleet, reference MQTT/CoAP/ThingsBoard devices, Zephyr firmware). Run as root on VM-E,
# or from VM-C with setup_all.sh.
. "$(dirname "$0")/common.sh"
need_root
log "== VM-E setup (EviCon 081)"
apt_install python3 python3-pip libsqlite3-0 iproute2 tar ca-certificates
# release 077: measure zephyr-mcu runs the firmware's Cortex-M3 image under QEMU (machine mps2-an385)
apt_install qemu-system-arm
command -v qemu-system-arm >/dev/null || warn "qemu-system-arm missing: measure zephyr-mcu cannot run"
time_sync
evicon_dirs
# measure mosquitto / coap: conformance/reference/mqtt_device.py and coap_device.py
python3 -c 'import paho.mqtt.client, aiocoap' 2>/dev/null || pip_install "paho-mqtt==2.1.0" "aiocoap==0.4.17"
py_check paho.mqtt.client aiocoap
for p in 18090 18100:18163 18201 18202 18211 18220:18235; do ufw_allow "${VM_C:?VM_C unset: run this script through deploy/setup/setup_all.sh, which sets it from testbed.json}" "$p" tcp; done   # 18202: ThingsBoard device (068); 18220-: experiment TB devices (077)
ufw_allow "${VM_C:?VM_C unset: run this script through deploy/setup/setup_all.sh, which sets it from testbed.json}" 5683 udp
for p in 18090 18092 18100 18101 18201 18202 18211; do o=$(port_owner $p); [ -z "$o" ] || warn "port $p is in use: $o"; done   # 18092: plant line protocol for the Zephyr firmware (loopback)
u=$(ss -lunpH 'sport = :5683' 2>/dev/null | head -1); [ -z "$u" ] || warn "udp/5683 is in use: $u (measure coap needs it)"
log "VM-E ready"
