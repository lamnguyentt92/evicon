/*
 * Boot report of the EviCon cutoff firmware (release 087; see evc_boot_report.h and ../CMakeLists.txt). Compiled into
 * firmware/zephyr-boot only; the measured firmware (../zephyr-cutoff) does not contain it.
 */
#include "evc_boot_report.h"

#include <stdio.h>
#include <string.h>

#include "evc_core.h"

static uint64_t boot_floor;

uint64_t evc_boot_floor(void)
{
	return boot_floor;
}

int evc_boot_append_floor(const char *in, size_t len, char *buf, size_t n, uint64_t floor)
{
	if (in == NULL || len < 2 || in[0] != '{' || in[len - 1] != '}') {
		return -1;
	}
	int m;
	if (len == 2) {
		m = snprintf(buf, n, "{\"floor\":%llu}", (unsigned long long)floor);
	} else {
		m = snprintf(buf, n, "%.*s,\"floor\":%llu}", (int)(len - 1), in, (unsigned long long)floor);
	}
	return (m <= 0 || (size_t)m >= n) ? -1 : m;
}

int evc_boot_hello_topic(const char *cmd, size_t len, char *buf, size_t n, char *dev, size_t n_dev)
{
	static const char suffix[] = "/cmd";
	size_t ls = sizeof(suffix) - 1;
	if (cmd == NULL || len <= ls || memcmp(cmd + len - ls, suffix, ls) != 0) {
		return -1;
	}
	size_t base = len - ls; /* "<ns>/<dev>" */
	size_t slash = base;
	while (slash > 0 && cmd[slash - 1] != '/') {
		slash--;
	}
	if (slash == 0 || base - slash == 0 || base - slash >= n_dev) {
		return -1;
	}
	memcpy(dev, cmd + slash, base - slash);
	dev[base - slash] = '\0';
	int m = snprintf(buf, n, "%.*s/hello", (int)base, cmd);
	return (m <= 0 || (size_t)m >= n) ? -1 : m;
}

void evc_boot_core_init(struct evc_core *c, const struct evc_persisted *p, int64_t horizon_ms, uint32_t bound, unsigned flags);

void evc_boot_core_init(struct evc_core *c, const struct evc_persisted *p, int64_t horizon_ms, uint32_t bound, unsigned flags)
{
	boot_floor = p != NULL ? p->hw_limit : 0;
	evc_core_init(c, p, horizon_ms, bound, flags);
}

#ifndef EVC_BOOT_HOST_CORE_ONLY
#include <zephyr/net/mqtt.h>

int evc_boot_mqtt_publish(struct mqtt_client *client, const struct mqtt_publish_param *param);
int evc_boot_mqtt_subscribe(struct mqtt_client *client, const struct mqtt_subscription_list *list);

static char out[640];

int evc_boot_mqtt_publish(struct mqtt_client *client, const struct mqtt_publish_param *param)
{
	struct mqtt_publish_param p = *param;
	int m = evc_boot_append_floor((const char *)param->message.payload.data, param->message.payload.len, out, sizeof(out),
				      boot_floor);
	if (m > 0) {
		p.message.payload.data = (uint8_t *)out;
		p.message.payload.len = (uint32_t)m;
	}
	return mqtt_publish(client, &p);
}

int evc_boot_mqtt_subscribe(struct mqtt_client *client, const struct mqtt_subscription_list *list)
{
	static uint16_t hello_mid = 0xF000;
	static char topic[128];
	static char body[256];
	char dev[64];
	int rc = mqtt_subscribe(client, list);
	if (rc != 0 || list == NULL || list->list_count == 0) {
		return rc;
	}
	int lt = evc_boot_hello_topic((const char *)list->list[0].topic.utf8, list->list[0].topic.size, topic, sizeof(topic), dev,
				      sizeof(dev));
	if (lt < 0) {
		return rc;
	}
	int lb = snprintf(body, sizeof(body), "{\"device\":\"%s\",\"hello\":true,\"floor\":%llu}", dev, (unsigned long long)boot_floor);
	if (lb <= 0 || (size_t)lb >= sizeof(body)) {
		return rc;
	}
	struct mqtt_publish_param p;
	memset(&p, 0, sizeof(p));
	p.message.topic.qos = MQTT_QOS_1_AT_LEAST_ONCE;
	p.message.topic.topic.utf8 = (const uint8_t *)topic;
	p.message.topic.topic.size = (uint32_t)lt;
	p.message.payload.data = (uint8_t *)body;
	p.message.payload.len = (uint32_t)lb;
	p.message_id = hello_mid++;
	if (hello_mid < 0xF000) {
		hello_mid = 0xF000;
	}
	(void)mqtt_publish(client, &p); /* best effort: the floor also travels with every reply */
	return rc;
}
#endif
