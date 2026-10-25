#include <stdio.h>
#include <string.h>

#include "../../src/evc_proto.h"

#define CHECK(x)                                                                                   \
	do {                                                                                       \
		if (!(x)) {                                                                        \
			fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #x);      \
			return 1;                                                                  \
		}                                                                                  \
	} while (0)

int main(void)
{
	struct evc_msg m;
	const char *ok = " {\"cmd\":\"dose-7\", \"seq\": 18446744073709551, \"value\":3,\"expires_at_ms\":-1,\"reply\":\"evicon/r/1\",\"x\":\"y\",\"n\":5}";
	CHECK(evc_parse(ok, strlen(ok), &m) == 0);
	CHECK(!strcmp(m.cmd, "dose-7") && m.seq == 18446744073709551ULL && m.value == 3 && m.expires_at_ms == -1 && !strcmp(m.reply, "evicon/r/1"));
	const char *fence = "{\"cutoff\":42,\"token\":\"t1\"}";
	CHECK(evc_parse(fence, strlen(fence), &m) == 0 && m.cutoff == 42 && !strcmp(m.token, "t1"));
	const char *bad[] = {"", "[]", "{\"cmd\":\"a\\\"b\"}", "{\"seq\":-1}", "{\"value\":4294967296}", "{\"cmd\":\"a\"",
			     "{\"seq\":99999999999999999999}", "{\"cmd\" \"a\"}", "{\"cmd\":\"a\",}x"};
	for (size_t i = 0; i < sizeof bad / sizeof bad[0]; i++) {
		if (evc_parse(bad[i], strlen(bad[i]), &m) == 0) {
			fprintf(stderr, "accepted bad input %zu: %s\n", i, bad[i]);
			return 1;
		}
	}
	char longid[200];
	memset(longid, 'a', sizeof longid);
	snprintf(longid, sizeof longid, "{\"cmd\":\"%0120d\"}", 1);
	CHECK(evc_parse(longid, strlen(longid), &m) != 0); /* longer than EVC_ID_MAX */
	struct evc_receipt r = {0};
	strcpy(r.id, "dose-7");
	r.seq = 9;
	r.outcome = EVC_STOPPED;
	r.reason = EVC_R_SEALED_BY_FENCE;
	char buf[256];
	CHECK(evc_format_receipt(buf, sizeof buf, &r, "pump-1") > 0);
	CHECK(strstr(buf, "\"outcome\":\"STOPPED\"") && strstr(buf, "SEALED_BY_FENCE"));
	CHECK(evc_format_receipt(buf, 10, &r, "pump-1") == -1);
	CHECK(evc_format_fence(buf, sizeof buf, 42, 7, 3, "t1", 1) > 0 && strstr(buf, "\"ok\":true"));
	r.outcome = EVC_UNCERTAIN;
	r.reason = EVC_R_RECEIPT_MAY_BE_EVICTED;
	CHECK(evc_format_receipt(buf, sizeof buf, &r, "pump-1") > 0 && strstr(buf, "\"outcome\":\"UNCERTAIN\"") &&
	      strstr(buf, "RECEIPT_MAY_BE_EVICTED"));
	r.outcome = EVC_NONE;
	r.reason = EVC_R_NOT_STARTED;
	CHECK(evc_format_receipt(buf, sizeof buf, &r, "pump-1") > 0 && strstr(buf, "\"outcome\":\"NOT_FOUND\""));
	CHECK(evc_format_error(buf, sizeof buf, "x", "UNCERTAIN", "TOMBSTONE_TABLE_FULL", -28) > 0 && strstr(buf, "\"error\":-28"));
	const char *q = "{\"cmd\":\"dose-7\",\"seq\":9,\"reply\":\"evicon/r/1\"}";
	CHECK(evc_parse(q, strlen(q), &m) == 0 && m.seq == 9);
	printf("evc_proto host tests: OK\n");
	return 0;
}
