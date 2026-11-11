/* Host shim of the boot-report firmware's wire path (release 087; firmware/zephyr-boot): the frozen host shim of the
 * cutoff firmware (../../../zephyr-cutoff/tests/host/evc_wire_shim.c, included unchanged) with its evc_core_init call
 * redirected to evc_boot_core_init, as firmware/zephyr-boot's CMakeLists redirects main.c's, and every reply passed
 * through evc_boot_append_floor, as evc_boot_mqtt_publish passes every published reply. Loaded by
 * conformance/tests/test_reboot_recovery087.py. Not part of any firmware build.
 *
 *   cc -shared -fPIC -o libevcbootshim.so evc_boot_shim.c ../../src/evc_boot_report.c <cutoff>/src/evc_core.c <cutoff>/src/evc_proto.c
 */
#define evc_core_init evc_boot_core_init
#include "../../../zephyr-cutoff/tests/host/evc_wire_shim.c"
#undef evc_core_init

#include "../../src/evc_boot_report.h"

void evc_boot_core_init(struct evc_core *c, const struct evc_persisted *p, int64_t horizon_ms, uint32_t bound, unsigned flags);

/* evc_shim_handle with the floor appended to the reply, as the boot-report firmware publishes it. */
int evc_boot_shim_handle(int kind, const char *body, char *reply, int n)
{
	char raw[512];
	int len = evc_shim_handle(kind, body, raw, (int)sizeof(raw));
	if (len <= 0) {
		return 0;
	}
	int m = evc_boot_append_floor(raw, (size_t)len, reply, (size_t)n, evc_boot_floor());
	if (m < 0) {
		if (len >= n) {
			return 0;
		}
		memcpy(reply, raw, (size_t)len + 1);
		return len;
	}
	return m;
}

unsigned long long evc_boot_shim_floor(void)
{
	return (unsigned long long)evc_boot_floor();
}
