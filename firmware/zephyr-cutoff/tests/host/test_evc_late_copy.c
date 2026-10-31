/* Host test of late copies (release 084, review M1: the delivery binding).
 *
 * The MQTT gateway sends commands at QoS 1 to the cutoff firmware. MQTT 3.1.1 re-sends an unacknowledged
 * QoS 1 PUBLISH when a persistent session resumes, so a copy of a command can reach the firmware at any
 * later time, also after the receipt of its first execution has left the 32-entry ring or after a reboot
 * has emptied the ring. The binding is safe only if such a copy never takes effect again, whatever its
 * age: suppression by the owner's design (the eviction rule, CONFIG_EVC_GUARD_EVICTED), not a measured
 * finite horizon. This test feeds late copies through the firmware's own wire path: the message is parsed
 * by evc_parse, executed by evc_core_command and answered as src/main.c answers a command (an -EAGAIN of
 * the core is the reply {"outcome":"UNCERTAIN","reason":"RECEIPT_MAY_BE_EVICTED"}).
 *
 *   built normally:             a late copy after ring eviction, after a reboot, and after a time gap
 *                               beyond the measured horizon is answered UNCERTAIN and has no effect;
 *   built -DEVC_LATE_COPY_NO_GUARD (the negative control: the firmware built with
 *                               CONFIG_EVC_GUARD_EVICTED=n): the same late copies execute again.
 *
 * The suppression is structural only under both build premises: CONFIG_EVC_GUARD_EVICTED=y and
 * CONFIG_EVC_DEDUP_HORIZON_MS=-1 (both Kconfig defaults). The second negative case runs in both builds: with a
 * positive horizon (or 0) a receipt still in the ring but older than the horizon is not live, the eviction
 * guard (which applies only when no receipt is found) is bypassed, and a copy older than the horizon executes
 * again. conformance/reference/delivery_binding.py therefore allows QoS 1 only when both premises hold.
 */
#include <errno.h>
#include <stdio.h>
#include <string.h>

#include "../../src/evc_core.h"
#include "../../src/evc_proto.h"

#ifdef EVC_LATE_COPY_NO_GUARD
#define GUARD_FLAGS 0u
#define BUILD "negative control (CONFIG_EVC_GUARD_EVICTED=n)"
#else
#define GUARD_FLAGS EVC_F_GUARD_EVICTED
#define BUILD "cutoff firmware (CONFIG_EVC_GUARD_EVICTED=y, the default)"
#endif

struct sim {
	struct evc_persisted p;
	int effects;
};

static int persist_cutoff(void *ctx, uint64_t c)
{
	((struct sim *)ctx)->p.cutoff = c;
	return 0;
}

static int persist_hw(void *ctx, uint64_t hw)
{
	((struct sim *)ctx)->p.hw_limit = hw;
	return 0;
}

static int persist_tombs(void *ctx, const struct evc_tomb *t, uint16_t n)
{
	struct sim *s = ctx;
	s->p.ntomb = n;
	memcpy(s->p.tomb, t, sizeof(*t) * n);
	return 0;
}

static int effect(void *ctx, const char *id, uint32_t v)
{
	(void)id;
	(void)v;
	((struct sim *)ctx)->effects++;
	return 0;
}

#define CHECK(x)                                                                                   \
	do {                                                                                       \
		if (!(x)) {                                                                        \
			fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #x);      \
			return 1;                                                                  \
		}                                                                                  \
	} while (0)

/* One command message through the firmware's wire path, answered as src/main.c answers it. */
static int deliver(struct evc_core *c, const struct evc_io *io, const char *id, uint64_t seq, int64_t now_ms, char *reply,
		   size_t n)
{
	char msg[160];
	struct evc_msg m;
	struct evc_receipt r;
	snprintf(msg, sizeof msg, "{\"cmd\":\"%s\",\"seq\":%llu,\"value\":1,\"expires_at_ms\":-1,\"reply\":\"evicon/r/1\"}", id,
		 (unsigned long long)seq);
	if (evc_parse(msg, strlen(msg), &m) != 0) {
		return -1;
	}
	int rc = evc_core_command(c, io, m.cmd, m.seq, m.value, now_ms, m.expires_at_ms, &r);
	if (rc == 0) {
		return evc_format_receipt(reply, n, &r, "pump-1");
	}
	if (rc == -EEXIST) {
		return evc_format_error(reply, n, m.cmd, "IDENTITY_CONFLICT", "IDENTITY_CONFLICT", rc);
	}
	if (rc == -EAGAIN) {
		return evc_format_error(reply, n, m.cmd, "UNCERTAIN", "RECEIPT_MAY_BE_EVICTED", rc);
	}
	return evc_format_error(reply, n, m.cmd, "UNCERTAIN", "NO_TERMINAL_DECISION", rc);
}

static int answered_uncertain(const char *reply)
{
	return strstr(reply, "\"UNCERTAIN\"") != NULL && strstr(reply, "RECEIPT_MAY_BE_EVICTED") != NULL;
}

static int answered_applied(const char *reply)
{
	return strstr(reply, "\"APPLIED\"") != NULL;
}

/* The receipt of A leaves the 32-entry ring; a copy of A arrives 700 s later (beyond the measured 600 s). */
static int late_copy_after_ring_eviction(void)
{
	struct sim s = {0};
	struct evc_io io = {persist_cutoff, persist_hw, persist_tombs, effect, &s};
	struct evc_core c;
	char reply[256], id[16];
	evc_core_init(&c, NULL, -1, 0, GUARD_FLAGS); /* horizon -1: ring-bounded, as CONFIG_EVC_DEDUP_HORIZON_MS */
	CHECK(deliver(&c, &io, "A", 1, 0, reply, sizeof reply) > 0 && answered_applied(reply) && s.effects == 1);
	CHECK(deliver(&c, &io, "A", 1, 5000, reply, sizeof reply) > 0 && strstr(reply, "REPLAY") && s.effects == 1); /* in the ring: replay */
	for (int i = 0; i < EVC_RING_SLOTS; i++) {
		snprintf(id, sizeof id, "p%d", i);
		CHECK(deliver(&c, &io, id, (uint64_t)(2 + i), 10000 + i, reply, sizeof reply) > 0 && answered_applied(reply));
	}
	int before = s.effects;
	deliver(&c, &io, "A", 1, 700000, reply, sizeof reply);
#ifdef EVC_LATE_COPY_NO_GUARD
	CHECK(!answered_uncertain(reply) && answered_applied(reply) && s.effects == before + 1); /* the negative control executes it again */
#else
	CHECK(answered_uncertain(reply) && s.effects == before);
	struct evc_receipt q;
	CHECK(evc_core_query(&c, "A", 1, &q) == 0 && q.outcome == EVC_UNCERTAIN); /* and never claims STOPPED */
#endif
	return 0;
}

/* A reboot empties the ring; a copy of A arrives after the reboot. */
static int late_copy_after_reboot(void)
{
	struct sim s = {0};
	struct evc_io io = {persist_cutoff, persist_hw, persist_tombs, effect, &s};
	struct evc_core c;
	char reply[256];
	evc_core_init(&c, NULL, -1, 0, GUARD_FLAGS);
	CHECK(deliver(&c, &io, "A", 1, 0, reply, sizeof reply) > 0 && answered_applied(reply) && s.effects == 1);
	CHECK(deliver(&c, &io, "B", 2, 10, reply, sizeof reply) > 0 && answered_applied(reply) && s.effects == 2);
	evc_core_init(&c, &s.p, -1, 0, GUARD_FLAGS); /* reboot: durable cutoff and hw_limit, RAM ring lost */
	deliver(&c, &io, "A", 1, 120000, reply, sizeof reply);
#ifdef EVC_LATE_COPY_NO_GUARD
	CHECK(!answered_uncertain(reply) && answered_applied(reply) && s.effects == 3);
#else
	CHECK(answered_uncertain(reply) && s.effects == 2);
	/* a fresh identity (sequence above hw_limit) still runs: the guard blocks only possible duplicates */
	CHECK(deliver(&c, &io, "C", s.p.hw_limit + 1, 120001, reply, sizeof reply) > 0 && answered_applied(reply) && s.effects == 3);
#endif
	return 0;
}

/* The second premise's negative case (both builds): CONFIG_EVC_DEDUP_HORIZON_MS > 0 or = 0. A copy of A that arrives
 * after the horizon, while A's receipt is still in the ring, executes again: no structural suppression. */
static int late_copy_beyond_positive_horizon(void)
{
	static const int horizons[] = {600000, 0};
	for (unsigned k = 0; k < sizeof horizons / sizeof horizons[0]; k++) {
		struct sim s = {0};
		struct evc_io io = {persist_cutoff, persist_hw, persist_tombs, effect, &s};
		struct evc_core c;
		char reply[256];
		evc_core_init(&c, NULL, horizons[k], 0, GUARD_FLAGS);
		CHECK(deliver(&c, &io, "A", 1, 0, reply, sizeof reply) > 0 && answered_applied(reply) && s.effects == 1);
		if (horizons[k] > 0) { /* within the horizon: a replay of the receipt, no effect */
			CHECK(deliver(&c, &io, "A", 1, 5000, reply, sizeof reply) > 0 && strstr(reply, "REPLAY") && s.effects == 1);
		}
		deliver(&c, &io, "A", 1, 700000, reply, sizeof reply); /* beyond the horizon, receipt still in the ring */
		CHECK(!answered_uncertain(reply) && answered_applied(reply) && s.effects == 2); /* executed again */
	}
	return 0;
}

int main(void)
{
	int fails = late_copy_after_ring_eviction() + late_copy_after_reboot() + late_copy_beyond_positive_horizon();
	printf("evc late-copy host tests, %s: %s (ring=%d slots)\n", BUILD, fails ? "FAIL" : "OK", EVC_RING_SLOTS);
	return fails != 0;
}
