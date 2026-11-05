/* Host shim of the cutoff firmware's wire path (release 086, verifier finding B2-02).
 *
 * A shared library for host tests (conformance/tests/test_fence_anchor_reboot.py): one simulated device that
 * answers every message exactly as src/main.c answers it in the cutoff and stop modes (evc_parse, the decision
 * core evc_core.c, the reply formats of evc_proto.c), with the durable state (cutoff, claim mark hw_limit,
 * tombstones) in a struct that survives evc_shim_boot(1) and the RAM state (receipt ring, level, owner
 * version, evicted_max) lost there, as a reboot of the firmware loses it (src/main.c keeps the level in RAM and
 * evc_core_init sets evicted_max = hw_limit after a reboot). The plant is a counter of the applied values:
 * the truth, which a reboot does not reset. No Zephyr, no MQTT: the test connects it to the real MQTT owner
 * gateway through an in-process link. Not part of the firmware build.
 *
 *   cc -shared -fPIC -o libevcshim.so evc_wire_shim.c ../../src/evc_core.c ../../src/evc_proto.c
 */
#include <errno.h>
#include <stdio.h>
#include <string.h>

#include "../../src/evc_core.h"
#include "../../src/evc_proto.h"

static struct evc_persisted flash;
static struct evc_core core;
static long plant_total;
static long plant_effects;
static int64_t clock_ms;
static int stop_mode;

static int persist_cutoff(void *ctx, uint64_t c)
{
	(void)ctx;
	flash.cutoff = c;
	return 0;
}

static int persist_hw(void *ctx, uint64_t hw)
{
	(void)ctx;
	flash.hw_limit = hw;
	return 0;
}

static int persist_tombs(void *ctx, const struct evc_tomb *t, uint16_t n)
{
	(void)ctx;
	flash.ntomb = n;
	memcpy(flash.tomb, t, sizeof(*t) * n);
	return 0;
}

static int apply_effect(void *ctx, const char *id, uint32_t value)
{
	(void)ctx;
	(void)id;
	plant_total += (long)value;
	plant_effects++;
	return 0;
}

static const struct evc_io io = {persist_cutoff, persist_hw, persist_tombs, apply_effect, NULL};

/* First boot (reboot = 0: empty flash, plant 0) or a reboot (reboot = 1: the flash and the plant survive, the
 * RAM does not). mode_stop selects the stop mode (EVC_F_STOP). The flags are those of src/main.c with the
 * Kconfig defaults (CONFIG_EVC_GUARD_EVICTED=y, CONFIG_EVC_DEDUP_HORIZON_MS=-1, no expiry, no bound). */
void evc_shim_boot(int reboot, int mode_stop)
{
	if (!reboot) {
		memset(&flash, 0, sizeof flash);
		plant_total = 0;
		plant_effects = 0;
		clock_ms = 0;
	}
	stop_mode = mode_stop;
	memset(&core, 0, sizeof core);
	evc_core_init(&core, reboot ? &flash : NULL, -1, 0, (mode_stop ? EVC_F_STOP : 0u) | EVC_F_GUARD_EVICTED);
}

long evc_shim_plant_total(void)
{
	return plant_total;
}

long evc_shim_plant_effects(void)
{
	return plant_effects;
}

unsigned long long evc_shim_hw_limit(void)
{
	return (unsigned long long)core.hw_limit;
}

unsigned long long evc_shim_evicted_max(void)
{
	return (unsigned long long)core.evicted_max;
}

unsigned long evc_shim_level(void)
{
	return (unsigned long)core.level;
}

/* One message on topic kind (0 cmd, 1 fence, 2 query, 3 stop, 4 ping) answered as src/main.c's handle()
 * answers it; returns the reply's length (0: no reply, as main.c publishes none). */
int evc_shim_handle(int kind, const char *body, char *reply, int n)
{
	struct evc_msg m;
	struct evc_receipt r;
	int len = -1;
	clock_ms += 10;
	if (evc_parse(body, strlen(body), &m) != 0) {
		return 0;
	}
	switch (kind) {
	case 4:
		len = snprintf(reply, (size_t)n, "{\"device\":\"%s\",\"mode\":\"%s\",\"ok\":true,\"level\":%lu,\"cutoff\":%llu}", "shim",
			       stop_mode ? "stop" : "cutoff", (unsigned long)core.level, (unsigned long long)core.cutoff);
		break;
	case 0: {
		int rc = evc_core_command(&core, &io, m.cmd, m.seq, m.value, clock_ms, m.expires_at_ms, &r);
		if (rc == 0) {
			len = evc_format_receipt(reply, (size_t)n, &r, "shim");
		} else if (rc == -EEXIST) {
			len = evc_format_error(reply, (size_t)n, m.cmd, "IDENTITY_CONFLICT", "IDENTITY_CONFLICT", rc);
		} else if (rc == -EAGAIN) {
			len = evc_format_error(reply, (size_t)n, m.cmd, "UNCERTAIN", "RECEIPT_MAY_BE_EVICTED", rc);
		} else {
			len = evc_format_error(reply, (size_t)n, m.cmd, "UNCERTAIN", "NO_TERMINAL_DECISION", rc);
		}
		break;
	}
	case 1: {
		int rc = evc_core_fence(&core, &io, m.cutoff);
		len = evc_format_fence(reply, (size_t)n, core.cutoff, core.owner_version, core.level, m.token, rc == 0);
		break;
	}
	case 2:
		(void)evc_core_query(&core, m.cmd, m.seq, &r);
		len = evc_format_receipt(reply, (size_t)n, &r, "shim");
		break;
	case 3: {
		int rc = evc_core_stop(&core, &io, m.cmd, m.seq, clock_ms, &r);
		len = rc >= 0 ? evc_format_receipt(reply, (size_t)n, &r, "shim")
			      : evc_format_error(reply, (size_t)n, m.cmd, "UNCERTAIN", rc == -ENOSPC ? "TOMBSTONE_TABLE_FULL" : "STOP_FAILED", rc);
		break;
	}
	default:
		return 0;
	}
	return len > 0 && len < n ? len : 0;
}
