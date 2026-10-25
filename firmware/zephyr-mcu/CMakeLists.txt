# EviCon firmware on MCU boards (release 077): the sources of ../zephyr-cutoff/src, unchanged (frozen by the
# C2/C3 plans), built for
#   mps2/an385            Arm Cortex-M3 MCU emulated by QEMU (machine mps2-an385, LAN9118 Ethernet), Zephyr's own
#                         TCP/IP stack and Ethernet driver, NVS settings on a simulated flash (RAM): the MQTT
#                         device that EviCon-Probe measures (`measure zephyr-mcu`)
#   nrf52840dk/nrf52840   Nordic nRF52840 (Cortex-M4F), IEEE 802.15.4 radio with OpenThread, NVS settings on the
#                         internal flash: the same MQTT device, built for its flash/RAM footprint (no board here)
cmake_minimum_required(VERSION 3.20.0)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(evc_mcu_firmware)

set(EVC_SRC ${CMAKE_CURRENT_SOURCE_DIR}/../zephyr-cutoff/src)
target_include_directories(app PRIVATE ${EVC_SRC})
target_sources(app PRIVATE ${EVC_SRC}/evc_core.c ${EVC_SRC}/main.c ${EVC_SRC}/evc_proto.c)
target_compile_definitions(app PRIVATE EVC_RING_SLOTS=${CONFIG_EVC_RING_SLOTS} EVC_TOMB_SLOTS=${CONFIG_EVC_TOMB_SLOTS})
