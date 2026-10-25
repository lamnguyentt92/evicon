/*
 * Footprint build (no networking): links evc_core with the settings
 * subsystem on real boards (e.g. nrf52840dk/nrf52840, esp32c6_devkitc) so
 * `west build -t rom_report / ram_report` attributes ROM/RAM to the durable
 * state (cutoff + claim high-water mark + tombstone table) versus the RAM
 * receipt ring (EVC_RING_SLOTS entries).
 * Build only; nothing here is meant to be flashed for the experiments.
 * STATUS: not compiled in this release.
 */
#include <errno.h>
#include <string.h>

#include <zephyr/kernel.h>
#include <zephyr/settings/settings.h>

#include "evc_core.h"

static struct evc_core core;
static struct evc_persisted persisted;
static volatile uint32_t sink;

static int set(const char *name, size_t len, settings_read_cb read_cb, void *cb_arg)
{
	if (strcmp(name, "cutoff") == 0 && len == sizeof(persisted.cutoff)) {
		return read_cb(cb_arg, &persisted.cutoff, sizeof(persisted.cutoff)) < 0 ? -EIO : 0;
	}
	if (strcmp(name, "hw") == 0 && len == sizeof(persisted.hw_limit)) {
		return read_cb(cb_arg, &persisted.hw_limit, sizeof(persisted.hw_limit)) < 0 ? -EIO : 0;
	}
	return 0;
}
SETTINGS_STATIC_HANDLER_DEFINE(evicon, "evicon", NULL, set, NULL, NULL);

static int persist(void *ctx, uint64_t c)
{
	ARG_UNUSED(ctx);
	return settings_save_one("evicon/cutoff", &c, sizeof(c));
}

static int persist_hw(void *ctx, uint64_t hw)
{
	ARG_UNUSED(ctx);
	return settings_save_one("evicon/hw", &hw, sizeof(hw));
}

static int persist_tombs(void *ctx, const struct evc_tomb *t, uint16_t n)
{
	ARG_UNUSED(ctx);
	return settings_save_one("evicon/tomb", t, sizeof(*t) * n);
}

static int effect(void *ctx, const char *id, uint32_t v)
{
	ARG_UNUSED(ctx);
	ARG_UNUSED(id);
	sink += v; /* stands in for the actuator driver */
	return 0;
}

int main(void)
{
	static const struct evc_io io = {.persist_cutoff = persist, .persist_hw = persist_hw, .persist_tombs = persist_tombs,
					 .apply_effect = effect, .ctx = NULL};
	struct evc_receipt r;
	settings_subsys_init();
	settings_load();
	evc_core_init(&core, &persisted, CONFIG_EVC_DEDUP_HORIZON_MS, CONFIG_EVC_BOUND,
		      EVC_F_GUARD_EVICTED | (IS_ENABLED(CONFIG_EVC_STOP) ? EVC_F_STOP : 0));
	(void)evc_core_command(&core, &io, "boot", persisted.hw_limit + 1, 1, k_uptime_get(), 0, &r);
	(void)evc_core_stop(&core, &io, "stop", persisted.hw_limit + 2, k_uptime_get(), &r);
	(void)evc_core_fence(&core, &io, persisted.hw_limit + 1);
	(void)evc_core_query(&core, "boot", persisted.hw_limit + 1, &r);
	return 0;
}
