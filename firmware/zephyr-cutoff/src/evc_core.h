/*
 * evc_core: portable decision core of the EviCon device firmware (release 062).
 *
 * No Zephyr dependency: compiled into the Zephyr application (src/main.c,
 * src/footprint_main.c) and into the host unit test (tests/host). All
 * durable writes go through evc_io callbacks, which the firmware maps to the
 * settings subsystem (NVS on flash, a file-backed flash on native_sim).
 *
 * Durable state (struct evc_persisted, written BEFORE it is relied on):
 *   cutoff    monotonic fence frontier F
 *   hw_limit  upper bound of every sequence that may have started an effect
 *             (persisted in blocks of EVC_HW_BLOCK before the effect)
 *   tomb[]    per-identity stop tombstones (Q_S); a separate table that the
 *             receipt ring can never evict; full table => -ENOSPC, never a
 *             silent drop; tombstones at or below the cutoff are collected
 * RAM only:
 *   ring[]    receipts of the last EVC_RING_SLOTS identities
 *   evicted_max  every APPLIED identity with seq <= evicted_max may have lost
 *             its receipt (ring eviction, effect with unknown result, or a
 *             reboot, which sets evicted_max = hw_limit)
 *
 * What the owner can certify (review RV04/RV05):
 *   - an identity found in the ring or the tombstone table: its outcome;
 *   - an identity NOT found with seq > evicted_max: it has not started; if
 *     also seq <= cutoff it never will (STOPPED, "SEALED_NOT_STARTED");
 *   - an identity NOT found with seq <= evicted_max: UNKNOWN. A fence or a
 *     tombstone blocks its future, but its past cannot be recovered, so the
 *     firmware never answers STOPPED for it.
 * With EVC_GUARD_EVICTED (default), such an identity is never executed blindly
 * either: a command with seq <= evicted_max that is not in the ring is
 * answered UNCERTAIN (no duplicate effect after eviction or reboot; clients
 * reissue with a fresh sequence above hw_limit).
 */
#ifndef EVC_CORE_H
#define EVC_CORE_H

#include <stddef.h>
#include <stdint.h>

#ifndef EVC_ID_MAX
#define EVC_ID_MAX 48
#endif
#ifndef EVC_RING_SLOTS
#define EVC_RING_SLOTS 32
#endif
#ifndef EVC_TOMB_SLOTS
#define EVC_TOMB_SLOTS 16
#endif
#ifndef EVC_HW_BLOCK
#define EVC_HW_BLOCK 64
#endif
#if EVC_RING_SLOTS < 1 || EVC_TOMB_SLOTS < 1 || EVC_HW_BLOCK < 1
#error "EVC_RING_SLOTS, EVC_TOMB_SLOTS and EVC_HW_BLOCK must be >= 1"
#endif

enum evc_outcome { EVC_NONE = 0, EVC_APPLIED = 1, EVC_STOPPED = 2, EVC_UNCERTAIN = 3 };

enum evc_reason {
	EVC_R_APPLIED = 0,
	EVC_R_REPLAY = 1,
	EVC_R_SEALED_BY_FENCE = 2,
	EVC_R_EXPIRED = 3,
	EVC_R_BOUND = 4,
	EVC_R_TOMBSTONE = 5,
	EVC_R_EFFECT_FAILED = 6,
	EVC_R_IDENTITY_CONFLICT = 7,
	EVC_R_SEALED_NOT_STARTED = 8,   /* not found, certainly not started, seq <= cutoff */
	EVC_R_RECEIPT_MAY_BE_EVICTED = 9, /* not found, seq <= evicted_max: outcome unknown */
	EVC_R_TOMBSTONE_PAST_UNKNOWN = 10, /* future blocked by a tombstone/cutoff, past unknown */
	EVC_R_NOT_STARTED = 11,          /* not found, certainly not started, not blocked */
};

struct evc_receipt {
	char id[EVC_ID_MAX];
	uint64_t seq;
	int64_t at_ms;
	uint32_t value;
	uint32_t level_after;
	uint32_t owner_version;
	uint8_t outcome; /* enum evc_outcome */
	uint8_t reason;  /* enum evc_reason */
};

struct evc_tomb {
	char id[EVC_ID_MAX];
	uint64_t seq;
};

/* Everything the io layer keeps durably; restored by evc_core_init. */
struct evc_persisted {
	uint64_t cutoff;
	uint64_t hw_limit;
	uint16_t ntomb;
	struct evc_tomb tomb[EVC_TOMB_SLOTS];
};

struct evc_core {
	uint64_t cutoff;
	uint64_t hw_limit;
	uint64_t evicted_max;
	uint32_t level;         /* cumulative actuator level (RAM; the plant is the truth) */
	uint32_t owner_version; /* increments on every terminal decision */
	uint32_t bound;         /* 0 = none */
	int64_t horizon_ms;     /* 0 = no dedup, < 0 = ring-bounded only, > 0 = time + ring */
	uint8_t enable_stop;
	uint8_t enforce_expiry;
	uint8_t guard_evicted;
	uint16_t head;
	uint16_t count;
	uint16_t ntomb;
	struct evc_tomb tomb[EVC_TOMB_SLOTS];
	struct evc_receipt ring[EVC_RING_SLOTS];
};

struct evc_io {
	/* Each must store durably before returning 0. */
	int (*persist_cutoff)(void *ctx, uint64_t cutoff);
	int (*persist_hw)(void *ctx, uint64_t hw_limit);
	int (*persist_tombs)(void *ctx, const struct evc_tomb *tomb, uint16_t n);
	/* Physical effect (plant). Return 0 only if the effect happened; any
	 * other value means "unknown" (the core then treats the receipt as lost). */
	int (*apply_effect)(void *ctx, const char *id, uint32_t value);
	void *ctx;
};

#define EVC_F_STOP 0x1
#define EVC_F_EXPIRY 0x2
#define EVC_F_GUARD_EVICTED 0x4

/* p may be NULL (first boot). After a reboot evicted_max = hw_limit. */
void evc_core_init(struct evc_core *c, const struct evc_persisted *p, int64_t horizon_ms, uint32_t bound, unsigned flags);

/* Executes one command. Returns 0 and fills *out (APPLIED or STOPPED), or:
 *   -EINVAL  malformed; -EEXIST identity conflict;
 *   -EAGAIN  refused: seq <= evicted_max and no receipt (EVC_GUARD_EVICTED);
 *   -EIO     no terminal decision (persist or effect failure): answer UNCERTAIN. */
int evc_core_command(struct evc_core *c, const struct evc_io *io, const char *id, uint64_t seq, uint32_t value,
		     int64_t now_ms, int64_t expires_at_ms, struct evc_receipt *out);

/* Installs cutoff (monotonic; persisted first) and collects tombstones at or
 * below it. Returns 0 on success. */
int evc_core_fence(struct evc_core *c, const struct evc_io *io, uint64_t cutoff);

/* Per-identity stop. Always fills *out; returns
 *   0        terminal: out->outcome is APPLIED or STOPPED;
 *   1        future blocked but past unknown (out->outcome == EVC_UNCERTAIN);
 *   -ENOTSUP / -EINVAL / -EEXIST / -ENOSPC (table full) / -EIO (persist failed). */
int evc_core_stop(struct evc_core *c, const struct evc_io *io, const char *id, uint64_t seq, int64_t now_ms,
		  struct evc_receipt *out);

/* Receipt query (Q_A) by identity and sequence. Always fills *out:
 *   found in ring or tombstone table  -> its receipt;
 *   not found, seq > evicted_max      -> STOPPED/SEALED_NOT_STARTED if seq <= cutoff,
 *                                        else EVC_NONE/NOT_STARTED (certain, not terminal);
 *   not found, seq <= evicted_max (or seq == 0) -> EVC_UNCERTAIN/RECEIPT_MAY_BE_EVICTED.
 * Returns 1 if the identity was found, 0 otherwise. */
int evc_core_query(const struct evc_core *c, const char *id, uint64_t seq, struct evc_receipt *out);

const char *evc_reason_str(uint8_t r);
const char *evc_outcome_str(uint8_t o);

#endif /* EVC_CORE_H */
