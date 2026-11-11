/* Host test of the boot report (release 087; firmware/zephyr-boot/src/evc_boot_report.c), against the frozen decision
 * core and reply formats of ../../../zephyr-cutoff/src and stub MQTT calls that record what is published:
 *   - evc_boot_core_init records the floor: 0 on a fresh flash, the persisted claim mark after a reboot;
 *   - every reply format of the firmware (receipt, error, fence, ping) gets "floor":F and stays one JSON object;
 *   - after a subscription a hello {"device","hello","floor"} goes to <ns>/<dev>/hello;
 *   - the claim floor partitions the level: after a reboot the level counts exactly the identities above F applied
 *     in this boot, and nothing at or below F executes. */
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>

#include <zephyr/net/mqtt.h>

#include "evc_boot_report.h"
#include "evc_core.h"
#include "evc_proto.h"

void evc_boot_core_init(struct evc_core *c, const struct evc_persisted *p, int64_t horizon_ms, uint32_t bound, unsigned flags);
int evc_boot_mqtt_publish(struct mqtt_client *client, const struct mqtt_publish_param *param);
int evc_boot_mqtt_subscribe(struct mqtt_client *client, const struct mqtt_subscription_list *list);

static char last_topic[160], last_body[700];
static int publishes;

int mqtt_publish(struct mqtt_client *c, const struct mqtt_publish_param *p)
{
	(void)c;
	snprintf(last_topic, sizeof last_topic, "%.*s", (int)p->message.topic.topic.size, (const char *)p->message.topic.topic.utf8);
	snprintf(last_body, sizeof last_body, "%.*s", (int)p->message.payload.len, (const char *)p->message.payload.data);
	publishes++;
	return 0;
}

int mqtt_subscribe(struct mqtt_client *c, const struct mqtt_subscription_list *l)
{
	(void)c;
	(void)l;
	return 0;
}

static struct evc_persisted flash;
static long plant;
static int p_cut(void *x, uint64_t v) { (void)x; flash.cutoff = v; return 0; }
static int p_hw(void *x, uint64_t v) { (void)x; flash.hw_limit = v; return 0; }
static int p_tomb(void *x, const struct evc_tomb *t, uint16_t n) { (void)x; (void)t; flash.ntomb = n; return 0; }
static int effect(void *x, const char *id, uint32_t v) { (void)x; (void)id; plant += v; return 0; }
static const struct evc_io io = {p_cut, p_hw, p_tomb, effect, NULL};

static void publish(const char *body)
{
	struct mqtt_publish_param p;
	memset(&p, 0, sizeof p);
	p.message.topic.topic.utf8 = (const uint8_t *)"ns/dev/resp/x/cmd";
	p.message.topic.topic.size = (uint32_t)strlen("ns/dev/resp/x/cmd");
	p.message.payload.data = (uint8_t *)body;
	p.message.payload.len = (uint32_t)strlen(body);
	assert(evc_boot_mqtt_publish(NULL, &p) == 0);
}

static void expect_floor(unsigned long long f)
{
	char want[64];
	snprintf(want, sizeof want, ",\"floor\":%llu}", f);
	size_t lb = strlen(last_body), lw = strlen(want);
	assert(lb > lw && strcmp(last_body + lb - lw, want) == 0);
	assert(last_body[0] == '{');
}

int main(void)
{
	struct evc_core core;
	struct evc_receipt r;
	char buf[512];
	/* first boot: floor 0 */
	evc_boot_core_init(&core, &flash, -1, 0, EVC_F_GUARD_EVICTED);
	assert(evc_boot_floor() == 0);
	assert(evc_core_command(&core, &io, "c1", 1, 3, 0, 0, &r) == 0 && r.outcome == EVC_APPLIED);
	assert(evc_core_command(&core, &io, "c2", 2, 4, 0, 0, &r) == 0 && r.outcome == EVC_APPLIED);
	assert(flash.hw_limit == 1 + EVC_HW_BLOCK);
	int n = evc_format_receipt(buf, sizeof buf, &r, "dev");
	assert(n > 0);
	publish(buf);
	expect_floor(0);
	/* reboot: the floor is the persisted claim mark; the level restarts at 0 */
	evc_boot_core_init(&core, &flash, -1, 0, EVC_F_GUARD_EVICTED);
	unsigned long long f = evc_boot_floor();
	assert(f == 1 + EVC_HW_BLOCK && core.level == 0);
	/* nothing at or below the floor executes: a replay and a new identity inside the claimed range are refused */
	assert(evc_core_command(&core, &io, "c2", 2, 4, 0, 0, &r) == -EAGAIN);
	assert(evc_core_command(&core, &io, "c3", 3, 1, 0, 0, &r) == -EAGAIN);
	/* above the floor: executed, and the level counts exactly these */
	assert(evc_core_command(&core, &io, "c4", f + 1, 5, 0, 0, &r) == 0 && r.outcome == EVC_APPLIED);
	assert(core.level == 5 && plant == 12);
	/* every reply format carries the floor and stays one object */
	n = evc_format_receipt(buf, sizeof buf, &r, "dev");
	publish(buf);
	expect_floor(f);
	n = evc_format_error(buf, sizeof buf, "c3", "UNCERTAIN", "RECEIPT_MAY_BE_EVICTED", -EAGAIN);
	publish(buf);
	expect_floor(f);
	assert(strstr(last_body, "\"outcome\":\"UNCERTAIN\"") != NULL);
	assert(evc_core_fence(&core, &io, f + 1) == 0);
	n = evc_format_fence(buf, sizeof buf, core.cutoff, core.owner_version, core.level, "t1", 1);
	publish(buf);
	expect_floor(f);
	assert(strstr(last_body, "\"level\":5") != NULL);
	publish("{\"device\":\"dev\",\"mode\":\"cutoff\",\"ok\":true,\"level\":5,\"cutoff\":74}");
	expect_floor(f);
	publish("{}");
	assert(strcmp(last_body, "{\"floor\":65}") == 0 || f != 65);
	/* not an object: published unchanged */
	publish("plain text");
	assert(strcmp(last_body, "plain text") == 0);
	/* the hello after a subscription */
	struct mqtt_topic t = {{(const uint8_t *)"e1a2b3/pump0-1/cmd", (uint32_t)strlen("e1a2b3/pump0-1/cmd")}, 1};
	struct mqtt_subscription_list l = {&t, 1, 7};
	int before = publishes;
	assert(evc_boot_mqtt_subscribe(NULL, &l) == 0);
	assert(publishes == before + 1);
	assert(strcmp(last_topic, "e1a2b3/pump0-1/hello") == 0);
	char want[96];
	snprintf(want, sizeof want, "{\"device\":\"pump0-1\",\"hello\":true,\"floor\":%llu}", f);
	assert(strcmp(last_body, want) == 0);
	/* hello topic edge cases */
	char top[64], dev[16];
	assert(evc_boot_hello_topic("cmd", 3, top, sizeof top, dev, sizeof dev) == -1);
	assert(evc_boot_hello_topic("a/cmd", 5, top, sizeof top, dev, sizeof dev) == -1);
	assert(evc_boot_hello_topic("ns/d/query", 10, top, sizeof top, dev, sizeof dev) == -1);
	puts("test_evc_boot_report: OK (floor 0 then the claim mark after a reboot; every reply format with \"floor\"; the hello;"
	     " nothing at or below the floor executes; the level counts the identities above it)");
	return 0;
}
