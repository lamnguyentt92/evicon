#include "evc_proto.h"

#include <stdio.h>
#include <string.h>

static const char *skip_ws(const char *p, const char *end)
{
	while (p < end && (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r')) {
		p++;
	}
	return p;
}

static int copy_str(const char **pp, const char *end, char *dst, size_t cap)
{
	const char *p = *pp;
	if (p >= end || *p != '"') {
		return -1;
	}
	p++;
	size_t n = 0;
	while (p < end && *p != '"') {
		if (*p == '\\' || (unsigned char)*p < 0x20) {
			return -1; /* identities and topics never need escapes */
		}
		if (dst != NULL) {
			if (n + 1 >= cap) {
				return -1;
			}
			dst[n] = *p;
		}
		n++;
		p++;
	}
	if (p >= end) {
		return -1;
	}
	if (dst != NULL) {
		dst[n] = '\0';
	}
	*pp = p + 1;
	return 0;
}

static int read_int(const char **pp, const char *end, int64_t *out)
{
	const char *p = *pp;
	int neg = 0;
	if (p < end && *p == '-') {
		neg = 1;
		p++;
	}
	if (p >= end || *p < '0' || *p > '9') {
		return -1;
	}
	uint64_t v = 0;
	while (p < end && *p >= '0' && *p <= '9') {
		uint64_t d = (uint64_t)(*p - '0');
		if (v > (UINT64_MAX - d) / 10) {
			return -1;
		}
		v = v * 10 + d;
		p++;
	}
	if (v > (uint64_t)INT64_MAX) {
		return -1;
	}
	*out = neg ? -(int64_t)v : (int64_t)v;
	*pp = p;
	return 0;
}

int evc_parse(const char *json, size_t len, struct evc_msg *m)
{
	const char *p = json, *end = json + len;
	memset(m, 0, sizeof(*m));
	p = skip_ws(p, end);
	if (p >= end || *p != '{') {
		return -1;
	}
	p++;
	p = skip_ws(p, end);
	if (p < end && *p == '}') {
		return skip_ws(p + 1, end) == end ? 0 : -1; /* empty object */
	}
	for (;;) {
		p = skip_ws(p, end);
		char key[24];
		if (copy_str(&p, end, key, sizeof key) != 0) {
			return -1;
		}
		p = skip_ws(p, end);
		if (p >= end || *p != ':') {
			return -1;
		}
		p = skip_ws(p + 1, end);
		int64_t iv = 0;
		if (!strcmp(key, "cmd")) {
			if (copy_str(&p, end, m->cmd, sizeof m->cmd)) return -1;
		} else if (!strcmp(key, "reply")) {
			if (copy_str(&p, end, m->reply, sizeof m->reply)) return -1;
		} else if (!strcmp(key, "token")) {
			if (copy_str(&p, end, m->token, sizeof m->token)) return -1;
		} else if (!strcmp(key, "seq") || !strcmp(key, "cutoff") || !strcmp(key, "value") || !strcmp(key, "expires_at_ms")) {
			if (read_int(&p, end, &iv)) return -1;
			if (!strcmp(key, "seq")) {
				if (iv < 0) return -1;
				m->seq = (uint64_t)iv;
			} else if (!strcmp(key, "cutoff")) {
				if (iv < 0) return -1;
				m->cutoff = (uint64_t)iv;
			} else if (!strcmp(key, "value")) {
				if (iv < 0 || iv > UINT32_MAX) return -1;
				m->value = (uint32_t)iv;
			} else {
				m->expires_at_ms = iv;
			}
		} else if (p < end && *p == '"') {
			if (copy_str(&p, end, NULL, 0)) return -1; /* ignore unknown string */
		} else {
			if (read_int(&p, end, &iv)) return -1; /* ignore unknown integer */
		}
		p = skip_ws(p, end);
		if (p < end && *p == ',') {
			p++;
			continue;
		}
		if (p < end && *p == '}') {
			return skip_ws(p + 1, end) == end ? 0 : -1; /* no trailing garbage */
		}
		return -1;
	}
}

static int fit(int n, size_t cap)
{
	return (n < 0 || (size_t)n >= cap) ? -1 : n;
}

int evc_format_receipt(char *buf, size_t n, const struct evc_receipt *r, const char *device)
{
	return fit(snprintf(buf, n,
			    "{\"device\":\"%s\",\"cmd\":\"%s\",\"seq\":%llu,\"outcome\":\"%s\",\"reason\":\"%s\","
			    "\"level\":%lu,\"owner_version\":%lu}",
			    device, r->id, (unsigned long long)r->seq, evc_outcome_str(r->outcome), evc_reason_str(r->reason),
			    (unsigned long)r->level_after, (unsigned long)r->owner_version),
		   n);
}

int evc_format_fence(char *buf, size_t n, uint64_t cutoff, uint32_t owner_version, uint32_t level, const char *token, int ok)
{
	return fit(snprintf(buf, n, "{\"cutoff\":%llu,\"token\":\"%s\",\"ok\":%s,\"owner_version\":%lu,\"level\":%lu}",
			    (unsigned long long)cutoff, token, ok ? "true" : "false", (unsigned long)owner_version,
			    (unsigned long)level),
		   n);
}

int evc_format_error(char *buf, size_t n, const char *id, const char *outcome, const char *reason, int rc)
{
	return fit(snprintf(buf, n, "{\"cmd\":\"%s\",\"outcome\":\"%s\",\"reason\":\"%s\",\"error\":%d}", id, outcome,
			    reason, rc),
		   n);
}
