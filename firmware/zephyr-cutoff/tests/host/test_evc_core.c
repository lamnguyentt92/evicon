/* Host unit test of the firmware decision core (gcc, no Zephyr).
 * Release 062 adds the review RV04/RV05 counterexamples: receipt eviction,
 * tombstone eviction, reboot, and a crash between effect and receipt must
 * never produce a false STOPPED or a duplicate effect. */
#include <errno.h>
#include <stdio.h>
#include <string.h>

#include "../../src/evc_core.h"

/* in-memory "flash" + plant */
struct sim {
	struct evc_persisted p;
	int fail_persist;
	int fail_effect;     /* effect not performed, error returned */
	int effect_then_eio; /* effect performed, then the reply is lost: unknown to the core */
	int effects;
	char last[EVC_ID_MAX];
};

static int persist_cutoff(void *ctx, uint64_t c)
{
	struct sim *s = ctx;
	if (s->fail_persist) {
		return -EIO;
	}
	s->p.cutoff = c;
	return 0;
}

static int persist_hw(void *ctx, uint64_t hw)
{
	struct sim *s = ctx;
	if (s->fail_persist) {
		return -EIO;
	}
	s->p.hw_limit = hw;
	return 0;
}

static int persist_tombs(void *ctx, const struct evc_tomb *t, uint16_t n)
{
	struct sim *s = ctx;
	if (s->fail_persist) {
		return -EIO;
	}
	s->p.ntomb = n;
	memcpy(s->p.tomb, t, sizeof(*t) * n);
	return 0;
}

static int effect(void *ctx, const char *id, uint32_t v)
{
	struct sim *s = ctx;
	(void)v;
	if (s->fail_effect) {
		return -EIO;
	}
	s->effects++;
	strncpy(s->last, id, EVC_ID_MAX - 1);
	return s->effect_then_eio ? -EIO : 0;
}

#define CHECK(x)                                                                                   \
	do {                                                                                       \
		if (!(x)) {                                                                        \
			fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #x);      \
			return 1;                                                                  \
		}                                                                                  \
	} while (0)

#define IO(s) {persist_cutoff, persist_hw, persist_tombs, effect, &(s)}
#define ALL (EVC_F_STOP | EVC_F_EXPIRY | EVC_F_GUARD_EVICTED)

static int fill_ring(struct evc_core *c, const struct evc_io *io, uint64_t first_seq, int n)
{
	struct evc_receipt r;
	char id[24];
	for (int i = 0; i < n; i++) {
		snprintf(id, sizeof id, "p%llu", (unsigned long long)(first_seq + (uint64_t)i));
		if (evc_core_command(c, io, id, first_seq + (uint64_t)i, 1, i, 0, &r) != 0) {
			return -1;
		}
	}
	return 0;
}

static int dedup_and_horizon(void)
{
	struct sim s = {0};
	struct evc_io io = IO(s);
	struct evc_core c;
	struct evc_receipt r;
	evc_core_init(&c, NULL, 1000, 0, 0);
	CHECK(evc_core_command(&c, &io, "a", 1, 2, 0, 0, &r) == 0 && r.outcome == EVC_APPLIED);
	CHECK(evc_core_command(&c, &io, "a", 1, 2, 500, 0, &r) == 0 && r.reason == EVC_R_REPLAY && s.effects == 1);
	CHECK(evc_core_command(&c, &io, "a", 1, 2, 1501, 0, &r) == 0 && s.effects == 2); /* beyond the time horizon */
	CHECK(evc_core_command(&c, &io, "a", 9, 2, 1600, 0, &r) == -EEXIST);
	evc_core_init(&c, NULL, 0, 0, 0); /* no dedup at all */
	s.effects = 0;
	evc_core_command(&c, &io, "b", 1, 1, 0, 0, &r);
	evc_core_command(&c, &io, "b", 1, 1, 1, 0, &r);
	CHECK(s.effects == 2);
	return 0;
}

/* RV04 counterexample: A applied, 32 identities evict its receipt. */
static int eviction_never_yields_not_started_or_duplicate(void)
{
	struct sim s = {0};
	struct evc_io io = IO(s);
	struct evc_core c;
	struct evc_receipt r;
	evc_core_init(&c, NULL, -1, 0, EVC_F_GUARD_EVICTED);
	CHECK(evc_core_command(&c, &io, "A", 1, 1, 0, 0, &r) == 0 && r.outcome == EVC_APPLIED);
	CHECK(fill_ring(&c, &io, 2, EVC_RING_SLOTS) == 0);
	CHECK(c.evicted_max >= 1);
	CHECK(evc_core_query(&c, "A", 1, &r) == 0 && r.outcome == EVC_UNCERTAIN && r.reason == EVC_R_RECEIPT_MAY_BE_EVICTED);
	/* fence over A: the future is blocked, the past is still unknown -> never STOPPED */
	CHECK(evc_core_fence(&c, &io, 1) == 0);
	CHECK(evc_core_query(&c, "A", 1, &r) == 0 && r.outcome == EVC_UNCERTAIN);
	/* redelivery of A must neither re-execute nor claim STOPPED */
	int before = s.effects;
	CHECK(evc_core_command(&c, &io, "A", 1, 1, 100, 0, &r) == -EAGAIN && s.effects == before);
	/* a never-sent identity above evicted_max under the cutoff is certainly not started */
	CHECK(evc_core_fence(&c, &io, 1000) == 0);
	CHECK(evc_core_query(&c, "B", 999, &r) == 0 && r.outcome == EVC_STOPPED && r.reason == EVC_R_SEALED_NOT_STARTED);
	CHECK(evc_core_command(&c, &io, "B", 999, 1, 100, 0, &r) == 0 && r.outcome == EVC_STOPPED && s.effects == before);
	CHECK(evc_core_query(&c, "C", 2000, &r) == 0 && r.outcome == EVC_NONE && r.reason == EVC_R_NOT_STARTED);
	return 0;
}

/* Negative control: without the eviction guard the ring bounds dedup. */
static int unguarded_ring_duplicates_after_eviction(void)
{
	struct sim s = {0};
	struct evc_io io = IO(s);
	struct evc_core c;
	struct evc_receipt r;
	evc_core_init(&c, NULL, -1, 0, 0);
	CHECK(evc_core_command(&c, &io, "A", 1, 1, 0, 0, &r) == 0);
	CHECK(fill_ring(&c, &io, 2, EVC_RING_SLOTS) == 0);
	CHECK(evc_core_command(&c, &io, "A", 1, 1, 100, 0, &r) == 0 && r.outcome == EVC_APPLIED);
	CHECK(s.effects == EVC_RING_SLOTS + 2); /* duplicate: what P5N measures */
	return 0;
}

/* RV05 counterexample: a tombstone must survive ring pressure and reboot. */
static int tombstones_are_durable_and_not_evicted(void)
{
	struct sim s = {0};
	struct evc_io io = IO(s);
	struct evc_core c;
	struct evc_receipt r;
	evc_core_init(&c, NULL, -1, 0, ALL);
	CHECK(evc_core_stop(&c, &io, "A", 1, 0, &r) == 0 && r.outcome == EVC_STOPPED && r.reason == EVC_R_TOMBSTONE);
	CHECK(s.p.ntomb == 1);
	CHECK(fill_ring(&c, &io, 2, 2 * EVC_RING_SLOTS) == 0);
	int before = s.effects;
	CHECK(evc_core_command(&c, &io, "A", 1, 1, 100, 0, &r) == 0 && r.outcome == EVC_STOPPED && s.effects == before);
	/* reboot: tombstone reloaded from flash */
	evc_core_init(&c, &s.p, -1, 0, ALL);
	CHECK(evc_core_command(&c, &io, "A", 1, 1, 100, 0, &r) == 0 && r.outcome == EVC_STOPPED && s.effects == before);
	CHECK(evc_core_query(&c, "A", 1, &r) == 1 && r.outcome == EVC_STOPPED);
	/* stop of an identity whose receipt was lost: future blocked, past unknown */
	CHECK(evc_core_stop(&c, &io, "p5", 5, 0, &r) == 1 && r.outcome == EVC_UNCERTAIN);
	return 0;
}

static int tombstone_table_full_is_explicit_and_fence_collects(void)
{
	struct sim s = {0};
	struct evc_io io = IO(s);
	struct evc_core c;
	struct evc_receipt r;
	char id[24];
	evc_core_init(&c, NULL, -1, 0, ALL);
	for (int i = 0; i < EVC_TOMB_SLOTS; i++) {
		snprintf(id, sizeof id, "t%d", i);
		CHECK(evc_core_stop(&c, &io, id, (uint64_t)i + 10, 0, &r) == 0);
	}
	CHECK(evc_core_stop(&c, &io, "over", 100, 0, &r) == -ENOSPC); /* never a silent drop */
	CHECK(evc_core_fence(&c, &io, 10 + EVC_TOMB_SLOTS / 2) == 0); /* collects the covered half */
	CHECK(c.ntomb == EVC_TOMB_SLOTS - EVC_TOMB_SLOTS / 2 - 1);
	CHECK(evc_core_stop(&c, &io, "over", 100, 0, &r) == 0 && r.outcome == EVC_STOPPED);
	/* collected tombstones stay terminal through the cutoff */
	CHECK(evc_core_command(&c, &io, "t0", 10, 1, 0, 0, &r) == 0 && r.outcome == EVC_STOPPED && s.effects == 0);
	s.fail_persist = 1;
	CHECK(evc_core_stop(&c, &io, "np", 200, 0, &r) == -EIO); /* not durable: no stop */
	return 0;
}

static int reboot_loses_receipts_but_not_certainty(void)
{
	struct sim s = {0};
	struct evc_io io = IO(s);
	struct evc_core c;
	struct evc_receipt r;
	evc_core_init(&c, NULL, -1, 0, ALL);
	CHECK(evc_core_command(&c, &io, "A", 5, 1, 0, 0, &r) == 0 && r.outcome == EVC_APPLIED);
	CHECK(s.p.hw_limit == 5 + EVC_HW_BLOCK); /* persisted before the effect */
	evc_core_init(&c, &s.p, -1, 0, ALL);   /* reboot */
	CHECK(evc_core_query(&c, "A", 5, &r) == 0 && r.outcome == EVC_UNCERTAIN);
	CHECK(evc_core_command(&c, &io, "A", 5, 1, 0, 0, &r) == -EAGAIN && s.effects == 1);
	CHECK(evc_core_fence(&c, &io, 5) == 0 && evc_core_query(&c, "A", 5, &r) == 0 && r.outcome == EVC_UNCERTAIN);
	/* fresh sequences above hw_limit run normally */
	CHECK(evc_core_command(&c, &io, "N", s.p.hw_limit + 1, 1, 0, 0, &r) == 0 && r.outcome == EVC_APPLIED);
	return 0;
}

/* Crash/unknown result between effect and receipt (AFTER_EFFECT_BEFORE_RECEIPT). */
static int effect_with_unknown_result_is_never_stopped(void)
{
	struct sim s = {0};
	struct evc_io io = IO(s);
	struct evc_core c;
	struct evc_receipt r;
	evc_core_init(&c, NULL, -1, 0, ALL);
	s.effect_then_eio = 1;
	CHECK(evc_core_command(&c, &io, "V", 7, 1, 0, 0, &r) == -EIO && s.effects == 1);
	s.effect_then_eio = 0;
	CHECK(evc_core_query(&c, "V", 7, &r) == 0 && r.outcome == EVC_UNCERTAIN);
	CHECK(evc_core_stop(&c, &io, "V", 7, 0, &r) == 1 && r.outcome == EVC_UNCERTAIN);
	CHECK(evc_core_fence(&c, &io, 7) == 0 && evc_core_query(&c, "V", 7, &r) == 0 && r.outcome == EVC_UNCERTAIN);
	CHECK(evc_core_command(&c, &io, "V", 7, 1, 0, 0, &r) == -EAGAIN && s.effects == 1);
	/* an effect that certainly did not happen leaves no doubt */
	s.fail_effect = 1;
	CHECK(evc_core_command(&c, &io, "W", 8, 1, 0, 0, &r) == -EIO);
	return 0;
}

static int fence_is_durable_first_and_monotonic(void)
{
	struct sim s = {0};
	struct evc_io io = IO(s);
	struct evc_core c;
	struct evc_receipt r;
	evc_core_init(&c, NULL, -1, 0, ALL);
	s.fail_persist = 1;
	CHECK(evc_core_fence(&c, &io, 5) == -EIO && c.cutoff == 0); /* not adopted unless durable */
	s.fail_persist = 0;
	CHECK(evc_core_fence(&c, &io, 5) == 0 && c.cutoff == 5 && s.p.cutoff == 5);
	CHECK(evc_core_fence(&c, &io, 3) == 0 && c.cutoff == 5);
	CHECK(evc_core_command(&c, &io, "late", 4, 1, 0, 0, &r) == 0 && r.outcome == EVC_STOPPED && r.reason == EVC_R_SEALED_BY_FENCE);
	CHECK(s.effects == 0);
	evc_core_init(&c, &s.p, -1, 0, ALL); /* reboot: cutoff restored, nothing ever started */
	CHECK(evc_core_command(&c, &io, "late", 4, 1, 0, 0, &r) == 0 && r.outcome == EVC_STOPPED && s.effects == 0);
	CHECK(evc_core_command(&c, &io, "new", 6, 1, 0, 0, &r) == 0 && r.outcome == EVC_APPLIED);
	s.fail_persist = 1; /* claim high-water cannot be persisted: nothing starts */
	CHECK(evc_core_command(&c, &io, "big", 6 + EVC_HW_BLOCK + 1, 1, 0, 0, &r) == -EIO && s.effects == 1);
	return 0;
}

static int stop_expiry_bound(void)
{
	struct sim s = {0};
	struct evc_io io = IO(s);
	struct evc_core c;
	struct evc_receipt r;
	evc_core_init(&c, NULL, 1000, 5, ALL);
	CHECK(evc_core_stop(&c, &io, "x", 1, 0, &r) == 0 && r.outcome == EVC_STOPPED);
	CHECK(evc_core_command(&c, &io, "x", 1, 1, 999999, 0, &r) == 0 && r.outcome == EVC_STOPPED && s.effects == 0);
	CHECK(evc_core_command(&c, &io, "y", 2, 1, 100, 50, &r) == 0 && r.reason == EVC_R_EXPIRED && s.effects == 0);
	CHECK(evc_core_command(&c, &io, "z", 3, 4, 100, 0, &r) == 0 && r.outcome == EVC_APPLIED);
	CHECK(evc_core_command(&c, &io, "w", 4, 2, 100, 0, &r) == 0 && r.reason == EVC_R_BOUND);
	CHECK(evc_core_stop(&c, &io, "z", 3, 200, &r) == 0 && r.outcome == EVC_APPLIED);
	CHECK(evc_core_stop(&c, &io, "z", 4, 200, &r) == -EEXIST);
	evc_core_init(&c, NULL, 1000, 0, 0);
	CHECK(evc_core_stop(&c, &io, "x", 1, 0, &r) == -ENOTSUP);
	return 0;
}

int main(void)
{
	int fails = dedup_and_horizon() + eviction_never_yields_not_started_or_duplicate() +
		    unguarded_ring_duplicates_after_eviction() + tombstones_are_durable_and_not_evicted() +
		    tombstone_table_full_is_explicit_and_fence_collects() + reboot_loses_receipts_but_not_certainty() +
		    effect_with_unknown_result_is_never_stopped() + fence_is_durable_first_and_monotonic() + stop_expiry_bound();
	printf("evc_core host tests: %s (ring=%d slots, tombstones=%d, %zu bytes RAM core state, %zu bytes durable)\n",
	       fails ? "FAIL" : "OK", EVC_RING_SLOTS, EVC_TOMB_SLOTS, sizeof(struct evc_core), sizeof(struct evc_persisted));
	return fails != 0;
}
