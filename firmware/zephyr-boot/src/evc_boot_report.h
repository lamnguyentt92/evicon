/*
 * Boot report of the EviCon cutoff firmware (release 087; firmware/zephyr-boot, PREREG_extra_087.md item B).
 *
 * The claim floor F is the claim high-water mark that evc_core_init restored at this boot (0 on a fresh flash): no
 * identity with a sequence at or below F takes effect any more (the eviction guard), and every identity above F that
 * took effect did so in this boot (the claim mark is persisted before every effect and never falls). So the RAM level
 * of this boot is the sum of the values of the identities above F that took effect. The firmware reports F with every
 * answer and in a hello after every (re)connect; the gateway passes it on as "claim_floor" and the authority's reboot
 * recovery (evicon-authority -reboot-recovery) uses it.
 */
#ifndef EVC_BOOT_REPORT_H
#define EVC_BOOT_REPORT_H

#include <stddef.h>
#include <stdint.h>

/* in[0..len) is a JSON object; writes it with ,"floor":F (or {"floor":F} for {}) into buf. Returns the new length,
 * or -1 when in is not an object or buf is too small (the caller then publishes in unchanged). */
int evc_boot_append_floor(const char *in, size_t len, char *buf, size_t n, uint64_t floor);

/* The hello topic of a command topic "<ns>/<dev>/cmd": "<ns>/<dev>/hello". Returns its length, -1 if cmd is not of
 * that form or buf is too small. dev (n_dev bytes) receives the device segment. */
int evc_boot_hello_topic(const char *cmd, size_t len, char *buf, size_t n, char *dev, size_t n_dev);

/* The floor recorded at boot. */
uint64_t evc_boot_floor(void);

#endif
