#include "evc_core.h"

#include <errno.h>
#include <string.h>

void evc_core_init(struct evc_core *c, const struct evc_persisted *p, int64_t horizon_ms, uint32_t bound, unsigned flags)
{
	memset(c, 0, sizeof(*c));
	c->horizon_ms = horizon_ms;
	c->bound = bound;
	c->enable_stop = (uint8_t)((flags & EVC_F_STOP) != 0);
	c->enforce_expiry = (uint8_t)((flags & EVC_F_EXPIRY) != 0);
	c->guard_evicted = (uint8_t)((flags & EVC_F_GUARD_EVICTED) != 0);
	if (p != NULL) {
		c->cutoff = p->cutoff;
		c->hw_limit = p->hw_limit;
		/* the RAM ring is gone: any identity that may have started before
		 * the reboot has lost its receipt */
		c->evicted_max = p->hw_limit;
		c->ntomb = p->ntomb > EVC_TOMB_SLOTS ? EVC_TOMB_SLOTS : p->ntomb;
		memcpy(c->tomb, p->tomb, sizeof(c->tomb[0]) * c->ntomb);
	}
}

static int bad_id(const char *id, uint64_t seq)
{
	return id == NULL || id[0] == '\0' || strlen(id) >= EVC_ID_MAX || seq == 0;
}

static struct evc_receipt *find(struct evc_core *c, const char *id)
{
	for (uint16_t i = 0; i < c->count; i++) {
		if (strncmp(c->ring[i].id, id, EVC_ID_MAX) == 0) {
			return &c->ring[i];
		}
	}
	return NULL;
}

static const struct evc_tomb *find_tomb(const struct evc_core *c, const char *id)
{
	for (uint16_t i = 0; i < c->ntomb; i++) {
		if (strncmp(c->tomb[i].id, id, EVC_ID_MAX) == 0) {
			return &c->tomb[i];
		}
	}
	return NULL;
}

static int live(const struct evc_core *c, const struct evc_receipt *r, int64_t now_ms)
{
	if (c->horizon_ms == 0) {
		return 0;
	}
	if (c->horizon_ms < 0) {
		return 1;
	}
	return now_ms - r->at_ms <= c->horizon_ms;
}

static void raise_evicted(struct evc_core *c, uint64_t seq)
{
	if (seq > c->evicted_max) {
		c->evicted_max = seq;
	}
}

/* Store a receipt: overwrite the identity's slot if present, else take the
 * oldest slot. Evicting an APPLIED receipt raises evicted_max, so the core
 * never later claims that identity "did not start". */
static struct evc_receipt *store(struct evc_core *c, const struct evc_receipt *r)
{
	struct evc_receipt *slot = find(c, r->id);
	if (slot == NULL) {
		if (c->count < EVC_RING_SLOTS) {
			slot = &c->ring[c->count++];
		} else {
			slot = &c->ring[c->head];
			if (slot->outcome == EVC_APPLIED) {
				raise_evicted(c, slot->seq);
			}
			c->head = (uint16_t)((c->head + 1) % EVC_RING_SLOTS);
		}
	}
	*slot = *r;
	return slot;
}

static void fill(const struct evc_core *c, struct evc_receipt *r, const char *id, uint64_t seq, uint32_t value,
		 int64_t now_ms)
{
	memset(r, 0, sizeof(*r));
	if (id != NULL) {
		strncpy(r->id, id, EVC_ID_MAX - 1);
	}
	r->seq = seq;
	r->value = value;
	r->at_ms = now_ms;
	r->level_after = c->level;
	r->owner_version = c->owner_version;
}

static int terminal(struct evc_core *c, struct evc_receipt *r, uint8_t outcome, uint8_t reason, struct evc_receipt *out)
{
	c->owner_version++;
	r->outcome = outcome;
	r->reason = reason;
	r->level_after = c->level;
	r->owner_version = c->owner_version;
	*out = *store(c, r);
	return 0;
}

static void tomb_receipt(const struct evc_core *c, const struct evc_tomb *t, struct evc_receipt *out)
{
	fill(c, out, t->id, t->seq, 0, 0);
	out->outcome = EVC_STOPPED;
	out->reason = EVC_R_TOMBSTONE;
}

int evc_core_command(struct evc_core *c, const struct evc_io *io, const char *id, uint64_t seq, uint32_t value,
		     int64_t now_ms, int64_t expires_at_ms, struct evc_receipt *out)
{
	if (bad_id(id, seq)) {
		return -EINVAL;
	}
	const struct evc_tomb *t = find_tomb(c, id);
	if (t != NULL) {
		if (t->seq != seq) {
			return -EEXIST;
		}
		tomb_receipt(c, t, out);
		return 0;
	}
	struct evc_receipt *prev = find(c, id);
	if (prev != NULL && live(c, prev, now_ms)) {
		if (prev->seq != seq) {
			return -EEXIST; /* identity conflict: same ID, different intent */
		}
		*out = *prev;
		if (prev->outcome == EVC_APPLIED) {
			out->reason = EVC_R_REPLAY;
		}
		return 0;
	}
	if (prev == NULL && seq <= c->evicted_max && (c->guard_evicted || seq <= c->cutoff)) {
		/* It may already have taken effect (receipt evicted or lost on
		 * reboot): neither execute it again nor call it STOPPED. */
		return -EAGAIN;
	}
	struct evc_receipt r;
	fill(c, &r, id, seq, value, now_ms);
	if (seq <= c->cutoff) {
		return terminal(c, &r, EVC_STOPPED, EVC_R_SEALED_BY_FENCE, out);
	}
	if (c->enforce_expiry && expires_at_ms > 0 && now_ms > expires_at_ms) {
		return terminal(c, &r, EVC_STOPPED, EVC_R_EXPIRED, out);
	}
	if (c->bound > 0 && (uint64_t)c->level + value > c->bound) {
		return terminal(c, &r, EVC_STOPPED, EVC_R_BOUND, out);
	}
	if (seq > c->hw_limit) {
		/* claim high-water mark: durable before any effect */
		uint64_t next = seq + EVC_HW_BLOCK;
		int rc = io->persist_hw(io->ctx, next);
		if (rc != 0) {
			return -EIO; /* nothing started */
		}
		c->hw_limit = next;
	}
	if (io->apply_effect(io->ctx, id, value) != 0) {
		raise_evicted(c, seq); /* unknown whether the effect happened: no receipt */
		return -EIO;
	}
	c->level += value;
	return terminal(c, &r, EVC_APPLIED, EVC_R_APPLIED, out);
}

static int gc_tombs(struct evc_core *c)
{
	uint16_t w = 0;
	for (uint16_t i = 0; i < c->ntomb; i++) {
		if (c->tomb[i].seq > c->cutoff) {
			c->tomb[w++] = c->tomb[i];
		}
	}
	int freed = c->ntomb - w;
	c->ntomb = w;
	return freed;
}

int evc_core_fence(struct evc_core *c, const struct evc_io *io, uint64_t cutoff)
{
	if (cutoff <= c->cutoff) {
		return 0; /* monotonic: replays and older fences are no-ops */
	}
	int rc = io->persist_cutoff(io->ctx, cutoff);
	if (rc != 0) {
		return rc; /* not adopted unless durable */
	}
	c->cutoff = cutoff;
	c->owner_version++;
	if (gc_tombs(c) > 0) {
		/* best effort: a stale persisted tombstone below the cutoff is harmless */
		(void)io->persist_tombs(io->ctx, c->tomb, c->ntomb);
	}
	return 0;
}

int evc_core_stop(struct evc_core *c, const struct evc_io *io, const char *id, uint64_t seq, int64_t now_ms,
		  struct evc_receipt *out)
{
	if (!c->enable_stop) {
		return -ENOTSUP;
	}
	if (bad_id(id, seq)) {
		return -EINVAL;
	}
	const struct evc_tomb *t = find_tomb(c, id);
	if (t != NULL) {
		if (t->seq != seq) {
			return -EEXIST;
		}
		tomb_receipt(c, t, out);
		return 0;
	}
	struct evc_receipt *prev = find(c, id);
	if (prev != NULL && prev->outcome != EVC_NONE) {
		if (prev->seq != seq) {
			return -EEXIST;
		}
		*out = *prev; /* APPLIED or an earlier terminal STOPPED */
		return 0;
	}
	int certain = seq > c->evicted_max;
	fill(c, out, id, seq, 0, now_ms);
	if (seq <= c->cutoff || (!certain && c->guard_evicted)) {
		/* the future is already blocked (cutoff, or the eviction guard) */
		out->outcome = certain ? EVC_STOPPED : EVC_UNCERTAIN;
		out->reason = certain ? EVC_R_SEALED_NOT_STARTED : EVC_R_TOMBSTONE_PAST_UNKNOWN;
		return certain ? 0 : 1;
	}
	if (c->ntomb >= EVC_TOMB_SLOTS && gc_tombs(c) == 0) {
		return -ENOSPC; /* never drop a tombstone silently */
	}
	struct evc_tomb nt;
	memset(&nt, 0, sizeof(nt));
	strncpy(nt.id, id, EVC_ID_MAX - 1);
	nt.seq = seq;
	c->tomb[c->ntomb++] = nt;
	if (io->persist_tombs(io->ctx, c->tomb, c->ntomb) != 0) {
		c->ntomb--;
		return -EIO; /* not durable: no stop */
	}
	c->owner_version++;
	out->owner_version = c->owner_version;
	out->outcome = certain ? EVC_STOPPED : EVC_UNCERTAIN;
	out->reason = certain ? EVC_R_TOMBSTONE : EVC_R_TOMBSTONE_PAST_UNKNOWN;
	return certain ? 0 : 1;
}

int evc_core_query(const struct evc_core *c, const char *id, uint64_t seq, struct evc_receipt *out)
{
	const struct evc_tomb *t = find_tomb(c, id);
	if (t != NULL) {
		tomb_receipt(c, t, out);
		return 1;
	}
	for (uint16_t i = 0; i < c->count; i++) {
		if (strncmp(c->ring[i].id, id, EVC_ID_MAX) == 0) {
			*out = c->ring[i];
			return 1;
		}
	}
	fill(c, out, id, seq, 0, 0);
	if (seq == 0 || seq <= c->evicted_max) {
		out->outcome = EVC_UNCERTAIN;
		out->reason = EVC_R_RECEIPT_MAY_BE_EVICTED;
	} else if (seq <= c->cutoff) {
		out->outcome = EVC_STOPPED;
		out->reason = EVC_R_SEALED_NOT_STARTED;
	} else {
		out->outcome = EVC_NONE;
		out->reason = EVC_R_NOT_STARTED;
	}
	return 0;
}

const char *evc_outcome_str(uint8_t o)
{
	switch (o) {
	case EVC_APPLIED:
		return "APPLIED";
	case EVC_STOPPED:
		return "STOPPED";
	case EVC_NONE:
		return "NOT_FOUND";
	default:
		return "UNCERTAIN";
	}
}

const char *evc_reason_str(uint8_t r)
{
	switch (r) {
	case EVC_R_APPLIED:
		return "APPLIED";
	case EVC_R_REPLAY:
		return "REPLAY";
	case EVC_R_SEALED_BY_FENCE:
		return "SEALED_BY_FENCE";
	case EVC_R_EXPIRED:
		return "EXPIRED_AT_DEVICE";
	case EVC_R_BOUND:
		return "BOUND_REJECT";
	case EVC_R_TOMBSTONE:
		return "STOP_TOMBSTONE";
	case EVC_R_EFFECT_FAILED:
		return "EFFECT_FAILED";
	case EVC_R_IDENTITY_CONFLICT:
		return "IDENTITY_CONFLICT";
	case EVC_R_SEALED_NOT_STARTED:
		return "SEALED_NOT_STARTED";
	case EVC_R_RECEIPT_MAY_BE_EVICTED:
		return "RECEIPT_MAY_BE_EVICTED";
	case EVC_R_TOMBSTONE_PAST_UNKNOWN:
		return "FUTURE_BLOCKED_PAST_UNKNOWN";
	case EVC_R_NOT_STARTED:
		return "NOT_STARTED";
	default:
		return "UNKNOWN";
	}
}
