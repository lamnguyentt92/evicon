# EviCon cutoff firmware (release 062). Two variants:
#   default    native_sim MQTT device (src/main.c)        - VM-E experiments
#   footprint  core + settings only (src/footprint_main.c) - ROM/RAM report on real boards
cmake_minimum_required(VERSION 3.20.0)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(evc_cutoff_firmware)

target_sources(app PRIVATE src/evc_core.c)
target_compile_definitions(app PRIVATE EVC_RING_SLOTS=${CONFIG_EVC_RING_SLOTS} EVC_TOMB_SLOTS=${CONFIG_EVC_TOMB_SLOTS})
if(CONFIG_EVC_FOOTPRINT_ONLY)
  target_sources(app PRIVATE src/footprint_main.c)
else()
  target_sources(app PRIVATE src/main.c src/evc_proto.c)
endif()
