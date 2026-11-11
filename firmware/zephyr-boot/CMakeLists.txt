# EviCon cutoff firmware with the boot report (release 087, PREREG_extra_087.md item B; reviewer comment M3).
#
# The measured firmware (../zephyr-cutoff) is frozen by the plans 074-086 and is not rebuilt. This application builds
# its sources unchanged (../zephyr-cutoff/src/main.c, evc_core.c, evc_proto.c; as ../zephyr-mcu and ../zephyr-auth do)
# plus src/evc_boot_report.c. Three calls of main.c are redirected to it when main.c is compiled here, so that no source
# line changes:
#   evc_core_init   -> evc_boot_core_init     records the claim floor F: the claim mark restored at this boot
#                                             (persisted hw_limit; 0 on a fresh flash), then calls evc_core_init
#   mqtt_publish    -> evc_boot_mqtt_publish  every reply (cmd, query, stop, fence, ping) carries "floor":F
#   mqtt_subscribe  -> evc_boot_mqtt_subscribe after every (re)connect's subscription, a hello {"device","hello","floor"}
#                                             on <ns>/<dev>/hello, so that the gateway learns F before the next command
# The decision core, the durable state and the flash writes are those of the measured firmware.
cmake_minimum_required(VERSION 3.20.0)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(evc_boot_firmware)

set(EVC_SRC ${CMAKE_CURRENT_SOURCE_DIR}/../zephyr-cutoff/src)
target_include_directories(app PRIVATE ${EVC_SRC} src)
target_sources(app PRIVATE ${EVC_SRC}/evc_core.c ${EVC_SRC}/main.c ${EVC_SRC}/evc_proto.c src/evc_boot_report.c)
target_compile_definitions(app PRIVATE EVC_RING_SLOTS=${CONFIG_EVC_RING_SLOTS} EVC_TOMB_SLOTS=${CONFIG_EVC_TOMB_SLOTS})
set_source_files_properties(${EVC_SRC}/main.c PROPERTIES COMPILE_DEFINITIONS
  "evc_core_init=evc_boot_core_init;mqtt_publish=evc_boot_mqtt_publish;mqtt_subscribe=evc_boot_mqtt_subscribe")
