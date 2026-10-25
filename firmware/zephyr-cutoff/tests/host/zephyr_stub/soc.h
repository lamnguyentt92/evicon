/* Declaration stub of native_sim's NATIVE_TASK (Zephyr: boards/native/common), for the host
 * syntax/type check of src/main.c with CONFIG_ARCH_POSIX. Not Zephyr. */
#pragma once
#define NATIVE_TASK(fn, level, prio) static void (*const fn##_native_task)(void) __attribute__((unused)) = fn;
