# EviCon firmware on MCU boards (release 077)

`firmware/zephyr-mcu` builds the EviCon firmware sources of `firmware/zephyr-cutoff/src` (`main.c`,
`evc_core.c`, `evc_proto.c`, unchanged) for microcontroller boards. Nothing in the decision core or
the message format differs from the `native_sim` build that the experiments run; what differs is the
platform under it.

| Board | What it is | Network | Settings (cutoff, tombstones) | Used for |
|---|---|---|---|---|
| `mps2/an385` | Arm Cortex-M3 MCU emulated by QEMU (`qemu-system-arm -machine mps2-an385`) | Zephyr's own TCP/IP stack and LAN9118 Ethernet driver; QEMU user-mode networking (guest 10.0.2.15, router 10.0.2.2) | NVS on a simulated flash (RAM) | `measure zephyr-mcu`: EviCon-Probe measures the evidence contract of this image |
| `nrf52840dk/nrf52840` | Nordic nRF52840 (Cortex-M4F, 1 MB flash, 256 KB RAM) | OpenThread (minimal thread device) over the 802.15.4 radio, IPv6 and IPv4, TCP, MQTT | NVS on the internal flash | flash/RAM footprint of the networked device (build only) |

An MCU image has no command line: the device ID (`zephyr-1`), the evidence mode (cutoff), the broker
and the plant are Kconfig values. `deploy/setup/build_zephyr.sh` (started by `deploy/setup/build_iot.sh`)
builds both boards on the build VM with the broker of `testbed.json` and the plant at `10.0.2.2:18092`
(VM-E's `evicon-plant -tcp 127.0.0.1:18092`, seen through QEMU's NAT). The driver checks these values
against the testbed before it runs the image.

Run by hand (VM-E):

    qemu-system-arm -machine mps2-an385 -cpu cortex-m3 -nographic -vga none -monitor none \
        -serial file:console.log -kernel zephyr-mcu-qemu.elf -nic user,model=lan9118

Build by hand (a Zephyr v4.2.0 workspace with the modules cmsis_6, hal_nordic, openthread and mbedtls):

    west build -b mps2/an385 firmware/zephyr-mcu -- -DCONFIG_EVC_BROKER_ADDR=\"192.168.107.152\" \
        -DCONFIG_EVC_PLANT_ADDR=\"10.0.2.2\" -DCONFIG_EVC_PLANT_PORT=18092
    west build -b nrf52840dk/nrf52840 firmware/zephyr-mcu

Limits. QEMU emulates the CPU, the Ethernet controller and the timers, not the timing of a real MCU, and
the simulated flash keeps the settings only while the image runs (the measured fault model is "owner
up"). The nRF52840 image was built, not run: no board was available. It keeps the firmware's IPv4
addressing of the broker and the plant; on a Thread network these would be reached through a border
router with NAT64.
