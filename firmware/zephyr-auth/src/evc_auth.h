/*
 * evc_auth (release 077): HMAC-SHA256 (FIPS 180-4, RFC 2104) for the authenticated firmware variant.
 * Portable C, no allocation, host-tested against the FIPS and RFC 4231 test vectors (tests/test_auth.c).
 */
#ifndef EVC_AUTH_H
#define EVC_AUTH_H

#include <stddef.h>
#include <stdint.h>

#define EVC_SHA256_LEN 32
#define EVC_MAC_HEX_LEN 64

struct evc_sha256 {
	uint32_t h[8];
	uint64_t len;
	uint8_t buf[64];
	size_t n;
};

void evc_sha256_init(struct evc_sha256 *s);
void evc_sha256_update(struct evc_sha256 *s, const void *data, size_t len);
void evc_sha256_final(struct evc_sha256 *s, uint8_t out[EVC_SHA256_LEN]);

void evc_hmac_sha256(const uint8_t *key, size_t key_len, const void *msg, size_t msg_len, uint8_t out[EVC_SHA256_LEN]);

/* lower-case hex of a MAC into out (EVC_MAC_HEX_LEN + 1 bytes, NUL-terminated) */
void evc_hex(const uint8_t mac[EVC_SHA256_LEN], char out[EVC_MAC_HEX_LEN + 1]);

/* hex string (2*n chars, upper or lower case) -> n bytes; 0 on success */
int evc_unhex(const char *hex, uint8_t *out, size_t n);

/* constant-time comparison of two hex MACs (EVC_MAC_HEX_LEN chars each); 1 when equal */
int evc_mac_equal(const char *a, const char *b);

#endif
