/*
 * evc_proto: minimal, allocation-free parsing of the flat JSON messages the
 * firmware receives, and formatting of its replies. Portable (host-tested).
 *
 *   evicon/<dev>/cmd    {"cmd":"<id>","seq":N,"value":N,"expires_at_ms":N,"reply":"<topic>"}
 *   evicon/<dev>/fence  {"cutoff":N,"token":"<t>","reply":"<topic>"}
 *   evicon/<dev>/query  {"cmd":"<id>","seq":N,"reply":"<topic>"}   (seq required: see evc_core_query)
 *   evicon/<dev>/stop   {"cmd":"<id>","seq":N,"reply":"<topic>"}
 */
#ifndef EVC_PROTO_H
#define EVC_PROTO_H

#include <stddef.h>
#include <stdint.h>

#include "evc_core.h"

#define EVC_TOPIC_MAX 96

struct evc_msg {
	char cmd[EVC_ID_MAX];
	char reply[EVC_TOPIC_MAX];
	char token[EVC_ID_MAX];
	uint64_t seq;
	uint64_t cutoff;
	int64_t expires_at_ms;
	uint32_t value;
};

/* Returns 0 on success; fields not present stay zero/empty. Rejects
 * malformed input, oversized strings and escaped quotes in strings. */
int evc_parse(const char *json, size_t len, struct evc_msg *m);

/* Formats a receipt reply; returns the length or -1 if buf is too small. */
int evc_format_receipt(char *buf, size_t n, const struct evc_receipt *r, const char *device);
int evc_format_fence(char *buf, size_t n, uint64_t cutoff, uint32_t owner_version, uint32_t level, const char *token, int ok);
/* Non-receipt replies, e.g. {"cmd":"x","outcome":"UNCERTAIN","reason":"RECEIPT_MAY_BE_EVICTED","error":-11}. */
int evc_format_error(char *buf, size_t n, const char *id, const char *outcome, const char *reason, int rc);

#endif
