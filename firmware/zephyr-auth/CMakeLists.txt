# EviCon AUTHENTICATED firmware (release 077): src/main_auth.c (the frozen ../zephyr-cutoff/src/main.c with
# request MACs, reply MACs and a cutoff bound, marked "AUTH 077"), src/evc_auth.c (HMAC-SHA256), and the cutoff
# firmware's decision core and message format (../zephyr-cutoff/src/evc_core.c, evc_proto.c), unchanged.
# Boards: native_sim/native/64 (VM-E, `measure zephyr-auth`), mps2/an385 and nrf52840dk/nrf52840 (footprint).
cmake_minimum_required(VERSION 3.20.0)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(evc_auth_firmware)

set(EVC_SRC ${CMAKE_CURRENT_SOURCE_DIR}/../zephyr-cutoff/src)
target_include_directories(app PRIVATE ${EVC_SRC} src)
target_sources(app PRIVATE ${EVC_SRC}/evc_core.c ${EVC_SRC}/evc_proto.c src/evc_auth.c src/main_auth.c)
target_compile_definitions(app PRIVATE EVC_RING_SLOTS=${CONFIG_EVC_RING_SLOTS} EVC_TOMB_SLOTS=${CONFIG_EVC_TOMB_SLOTS})
