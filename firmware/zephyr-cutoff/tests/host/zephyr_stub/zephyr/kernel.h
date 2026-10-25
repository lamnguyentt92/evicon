/* Minimal declaration stubs for a syntax/type check of src/main.c on a host
 * without the Zephyr SDK (tests/host/Makefile target "glue"). Not Zephyr. */
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <time.h>
#define ARG_UNUSED(x) (void)(x)
#define IS_ENABLED(x) 0
#define MIN(a,b) ((a)<(b)?(a):(b))
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
typedef struct { int64_t t; } k_timeout_t;
#define K_SECONDS(s) ((k_timeout_t){(s)})
#define K_MSEC(ms) ((k_timeout_t){(ms)})
int k_sleep(k_timeout_t t);
int64_t k_uptime_get(void);
void k_panic(void) __attribute__((noreturn));
#define CONFIG_EVC_DEVICE_ID "zephyr-1"
#define CONFIG_EVC_PLANT_PORT 18092
#define CONFIG_EVC_PLANT_ADDR "127.0.0.1"
#define CONFIG_EVC_BROKER_PORT 1883
#define CONFIG_EVC_BROKER_ADDR "127.0.0.1"
#define CONFIG_EVC_DEDUP_HORIZON_MS -1
#define CONFIG_EVC_BOUND 0
