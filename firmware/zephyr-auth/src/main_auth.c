/*
 * EviCon AUTHENTICATED firmware (release 077): firmware/zephyr-cutoff/src/main.c (frozen by the C2/C3 plans)
 * with three additions, each marked "AUTH 077" below; evc_core.c and evc_proto.c are the cutoff
 * firmware's, unchanged:
 *   1. commands, stops and fences must carry "mac": HMAC-SHA256 (evc_auth.c) under the device key
 *      (CONFIG_EVC_AUTH_KEY, 32 bytes in hex, provisioned at build time) of the canonical string
 *        "<kind>\n<ns>\n<device>\n<cmd>\n<seq>\n<value>\n<cutoff>\n<token>\n<expires_at_ms>"
 *      (decimal numbers, empty strings for absent fields); a request without a valid MAC changes nothing
 *      and is answered {"outcome":"REJECTED","reason":"UNAUTHENTICATED"}, which is never evidence;
 *      queries and pings are read-only and need none. A replayed authentic request is harmless: a
 *      command is deduplicated, a stop is idempotent, a fence's cutoff only grows.
 *   2. a fence whose cutoff is more than CONFIG_EVC_AUTH_CUTOFF_AHEAD above the highest sequence the
 *      firmware has seen (max of its claim high-water mark and its cutoff) is refused
 *      (CUTOFF_TOO_FAR_AHEAD): even an authentic but wrong cutoff seals at most that many future identities;
 *   3. every reply carries "mac": HMAC-SHA256 under the same key of "reply\n<ns>\n<device>\n" followed
 *      by the reply's bytes up to (without) its "mac" field, so the gateway accepts evidence only from the
 *      device (a forged receipt from another client of the broker fails the check).
 *
 * The original header of firmware/zephyr-cutoff/src/main.c follows.
 *
 * EviCon cutoff firmware - Zephyr application (MQTT 3.1.1 device, release 062).
 *
 * Runs as a Linux process on VM-E with board native_sim and offloaded host
 * sockets. The actuator effect is written to evicon-plant over TCP (line
 * protocol, `evicon-plant -tcp 127.0.0.1:18092`), which is the truth channel;
 * replies go to the topic named in each request ("reply"). The durable state
 * (cutoff, claim high-water mark, tombstones; see evc_core.h) lives in the
 * settings subsystem (NVS on the native_sim flash, which is backed by a file
 * so it survives process restarts - check CONFIG_FLASH_SIMULATOR / native_sim
 * flash file options of your Zephyr).
 *
 * STATUS (release 068): built with Zephyr v4.2.0 for native_sim and native_sim/native/64 (host
 * toolchain) and run end to end against Mosquitto and evicon-plant in the build environment:
 * EviCon-Probe measured K3 (D_h, Q_A, F observed; receipts not retained past the RAM ring).
 * evc_core.c / evc_proto.c are also host-tested with ASan/UBSan (tests/host).
 *
 * Release 074 (experiment C2): one native_sim binary serves several devices and three evidence
 * modes, chosen on the command line (real boards keep the Kconfig values; the decision core
 * evc_core.c and the message format evc_proto.c are unchanged):
 *   -evc_device=<id>        device ID: topic segment, plant device (default CONFIG_EVC_DEVICE_ID)
 *   -evc_ns=<prefix>        topic prefix (default "evicon"); a campaign uses one per cell, so
 *                           messages a broker kept for an earlier cell never reach this one
 *   -evc_broker=<ipv4> -evc_broker_port=<n>   (defaults CONFIG_EVC_BROKER_ADDR / _PORT)
 *   -evc_plant=<ipv4>  -evc_plant_port=<n>    (defaults CONFIG_EVC_PLANT_ADDR / _PORT)
 *   -evc_mode=cutoff|stop|plain
 *       cutoff  as measured in releases 068-070: dedup ring, query, fence (default; "stop" when
 *               CONFIG_EVC_STOP=y)
 *       stop    cutoff plus the per-identity stop (durable tombstones, EVC_F_STOP)
 *       plain   no evidence at all: every delivered command is executed and answered APPLIED
 *               (no dedup, no claim, no query, no fence, no stop: the topics are not subscribed),
 *               like an MQTT command handler without an EviCon core
 * Every mode answers <ns>/<dev>/ping (health, never evidence) with its device, mode and level.
 */
#include <errno.h>
#include <stdio.h>
#include <string.h>

#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/net/mqtt.h>
#include <zephyr/net/socket.h>
#include <zephyr/settings/settings.h>

#include "evc_core.h"
#include "evc_proto.h"
#include "evc_auth.h" /* AUTH 077 */

#if defined(CONFIG_ARCH_POSIX)
#include "cmdline.h" /* native_sim command line options */
#include "soc.h"
#endif

LOG_MODULE_REGISTER(evc_fw, LOG_LEVEL_INF);

#define TOPIC_FMT "%s/%s/%s"
#define N_TOPICS 5 /* cmd, fence, query, stop, ping */

enum evc_mode { EVC_MODE_CUTOFF = 0, EVC_MODE_STOP = 1, EVC_MODE_PLAIN = 2 };
static const char *const mode_names[] = {"cutoff", "stop", "plain"};

/* runtime configuration (release 074): native_sim's command line, else the Kconfig defaults. The
 * option parser resets every destination it knows before parsing (strings to NULL, unsigned numbers
 * to UINT32_MAX), so the defaults are applied in apply_defaults(), after parsing, not here. */
static char *opt_device;
static char *opt_ns;
static char *opt_broker;
static uint32_t opt_broker_port;
static char *opt_plant;
static uint32_t opt_plant_port;
static char *opt_mode;
static enum evc_mode fw_mode = IS_ENABLED(CONFIG_EVC_STOP) ? EVC_MODE_STOP : EVC_MODE_CUTOFF;
static char client_id[EVC_TOPIC_MAX];

/* ---- AUTH 077: device key, request MACs, reply MACs, cutoff bound -------- */
static uint8_t auth_key[32];

static void auth_init(void)
{
	if (evc_unhex(CONFIG_EVC_AUTH_KEY, auth_key, sizeof(auth_key)) != 0) {
		LOG_ERR("CONFIG_EVC_AUTH_KEY: 64 hex characters required (the authenticated firmware never runs without a key)");
		k_sleep(K_MSEC(100));
		k_panic();
	}
}

/* the request's "mac" (64 hex characters) into out; 0 when present */
static int auth_field(const char *body, size_t len, char out[EVC_MAC_HEX_LEN + 1])
{
	static const char key[] = "\"mac\":\"";
	for (size_t i = 0; i + sizeof(key) - 1 + EVC_MAC_HEX_LEN < len; i++) {
		if (memcmp(body + i, key, sizeof(key) - 1) == 0) {
			memcpy(out, body + i + sizeof(key) - 1, EVC_MAC_HEX_LEN);
			out[EVC_MAC_HEX_LEN] = '\0';
			return body[i + sizeof(key) - 1 + EVC_MAC_HEX_LEN] == '"' ? 0 : -1;
		}
	}
	return -1;
}

static int auth_request_ok(const char *kind, const struct evc_msg *m, const char *body, size_t len)
{
	char got[EVC_MAC_HEX_LEN + 1], want[EVC_MAC_HEX_LEN + 1], canon[320];
	uint8_t mac[EVC_SHA256_LEN];
	if (auth_field(body, len, got) != 0) {
		return 0;
	}
	int n = snprintf(canon, sizeof(canon), "%s\n%s\n%s\n%s\n%llu\n%lu\n%llu\n%s\n%lld", kind, opt_ns, opt_device, m->cmd,
			 (unsigned long long)m->seq, (unsigned long)m->value, (unsigned long long)m->cutoff, m->token,
			 (long long)m->expires_at_ms);
	if (n <= 0 || (size_t)n >= sizeof(canon)) {
		return 0;
	}
	evc_hmac_sha256(auth_key, sizeof(auth_key), canon, (size_t)n, mac);
	evc_hex(mac, want);
	return evc_mac_equal(got, want);
}

/* appends ,"mac":"<hex>" to the reply in buf (length n, ending in '}'); returns the new length or -1 */
static int auth_sign_reply(char *buf, size_t cap, int n)
{
	char head[EVC_TOPIC_MAX * 2 + 16], hex[EVC_MAC_HEX_LEN + 1];
	uint8_t mac[EVC_SHA256_LEN];
	if (n < 2 || buf[n - 1] != '}' || (size_t)n + 9 + EVC_MAC_HEX_LEN >= cap) {
		return -1;
	}
	int h = snprintf(head, sizeof(head), "reply\n%s\n%s\n", opt_ns, opt_device);
	if (h <= 0 || (size_t)h >= sizeof(head)) {
		return -1;
	}
	static char msg[sizeof(head) + 512];
	if ((size_t)h + (size_t)(n - 1) > sizeof(msg)) {
		return -1;
	}
	memcpy(msg, head, (size_t)h);
	memcpy(msg + h, buf, (size_t)(n - 1)); /* the reply without its closing brace */
	evc_hmac_sha256(auth_key, sizeof(auth_key), msg, (size_t)h + (size_t)(n - 1), mac);
	evc_hex(mac, hex);
	return snprintf(buf + n - 1, cap - (size_t)(n - 1), ",\"mac\":\"%s\"}", hex) + n - 1;
}

#if defined(CONFIG_ARCH_POSIX)
static void evc_add_options(void)
{
	static struct args_struct_t opts[] = {
		{.option = "evc_device", .name = "id", .type = 's', .dest = (void *)&opt_device,
		 .descript = "device ID (topic segment, plant device)"},
		{.option = "evc_ns", .name = "prefix", .type = 's', .dest = (void *)&opt_ns,
		 .descript = "topic prefix (default evicon)"},
		{.option = "evc_broker", .name = "ipv4", .type = 's', .dest = (void *)&opt_broker,
		 .descript = "MQTT broker IPv4 address"},
		{.option = "evc_broker_port", .name = "port", .type = 'u', .dest = (void *)&opt_broker_port,
		 .descript = "MQTT broker port"},
		{.option = "evc_plant", .name = "ipv4", .type = 's', .dest = (void *)&opt_plant,
		 .descript = "evicon-plant line-protocol IPv4 address"},
		{.option = "evc_plant_port", .name = "port", .type = 'u', .dest = (void *)&opt_plant_port,
		 .descript = "evicon-plant line-protocol port"},
		{.option = "evc_mode", .name = "cutoff|stop|plain", .type = 's', .dest = (void *)&opt_mode,
		 .descript = "evidence mode"},
		ARG_TABLE_ENDMARKER};
	native_add_command_line_opts(opts);
}
NATIVE_TASK(evc_add_options, PRE_BOOT_1, 10);
#endif

static struct evc_core core;
static struct evc_persisted persisted;
static struct mqtt_client client;
static struct sockaddr_storage broker;
static uint8_t rx_buf[1024];
static uint8_t tx_buf[1024];
static char payload[512];
static char reply[512];
static char topics[N_TOPICS][EVC_TOPIC_MAX];
static uint32_t plain_level;   /* plain mode: cumulative level (RAM) */
static uint32_t plain_version; /* plain mode: increments per effect */
static uint16_t next_mid = 1;
static int plant_sock = -1;

/* ---- settings: durable cutoff, claim high-water mark, tombstones ---------- */
struct tomb_blob {
	uint16_t n;
	struct evc_tomb t[EVC_TOMB_SLOTS];
};

static int evc_settings_set(const char *name, size_t len, settings_read_cb read_cb, void *cb_arg)
{
	if (strcmp(name, "cutoff") == 0 && len == sizeof(persisted.cutoff)) {
		return read_cb(cb_arg, &persisted.cutoff, sizeof(persisted.cutoff)) < 0 ? -EIO : 0;
	}
	if (strcmp(name, "hw") == 0 && len == sizeof(persisted.hw_limit)) {
		return read_cb(cb_arg, &persisted.hw_limit, sizeof(persisted.hw_limit)) < 0 ? -EIO : 0;
	}
	if (strcmp(name, "tomb") == 0 && len == sizeof(struct tomb_blob)) {
		static struct tomb_blob b;
		if (read_cb(cb_arg, &b, sizeof(b)) < 0) {
			return -EIO;
		}
		persisted.ntomb = b.n > EVC_TOMB_SLOTS ? EVC_TOMB_SLOTS : b.n;
		memcpy(persisted.tomb, b.t, sizeof(b.t[0]) * persisted.ntomb);
	}
	return 0;
}
SETTINGS_STATIC_HANDLER_DEFINE(evicon, "evicon", NULL, evc_settings_set, NULL, NULL);

/* synchronous flash writes: each returns only after the value is durable */
static int persist_cutoff(void *ctx, uint64_t c)
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
	static struct tomb_blob b;
	memset(&b, 0, sizeof(b));
	b.n = n;
	memcpy(b.t, t, sizeof(*t) * n);
	return settings_save_one("evicon/tomb", &b, sizeof(b));
}

/* ---- plant: the physical effect ------------------------------------------ */
static int plant_connect(void)
{
	struct sockaddr_in a = {.sin_family = AF_INET, .sin_port = htons((uint16_t)opt_plant_port)};
	zsock_inet_pton(AF_INET, opt_plant, &a.sin_addr);
	int s = zsock_socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
	if (s < 0) {
		return -errno;
	}
	if (zsock_connect(s, (struct sockaddr *)&a, sizeof(a)) < 0) {
		zsock_close(s);
		return -errno;
	}
	plant_sock = s;
	return 0;
}

static int apply_effect(void *ctx, const char *id, uint32_t value)
{
	ARG_UNUSED(ctx);
	char line[160];
	int n = snprintf(line, sizeof(line), "{\"op\":\"apply\",\"device\":\"%s\",\"cmd\":\"%s\",\"value\":%lu}\n",
			 opt_device, id, (unsigned long)value);
	if (n <= 0 || (size_t)n >= sizeof(line)) {
		return -EINVAL;
	}
	if (plant_sock < 0 && plant_connect() != 0) {
		return -EIO; /* effect certainly not performed */
	}
	if (zsock_send(plant_sock, line, n, 0) != n) {
		zsock_close(plant_sock);
		plant_sock = -1;
		return -EIO; /* may or may not have reached the plant: caller answers UNCERTAIN */
	}
	char resp[160];
	size_t got = 0;
	while (got < sizeof(resp) - 1) {
		ssize_t r = zsock_recv(plant_sock, resp + got, 1, 0);
		if (r <= 0) {
			zsock_close(plant_sock);
			plant_sock = -1;
			return -EIO;
		}
		if (resp[got++] == '\n') {
			break;
		}
	}
	resp[got] = '\0';
	return strstr(resp, "\"ok\":true") ? 0 : -EIO;
}

static const struct evc_io io = {.persist_cutoff = persist_cutoff, .persist_hw = persist_hw, .persist_tombs = persist_tombs,
				 .apply_effect = apply_effect, .ctx = NULL};

/* ---- MQTT ---------------------------------------------------------------- */
static void publish(const char *topic, const char *body, size_t len)
{
	struct mqtt_publish_param p = {0};
	p.message.topic.qos = MQTT_QOS_1_AT_LEAST_ONCE;
	p.message.topic.topic.utf8 = (const uint8_t *)topic;
	p.message.topic.topic.size = strlen(topic);
	p.message.payload.data = (uint8_t *)body;
	p.message.payload.len = len;
	p.message_id = next_mid++;
	if (mqtt_publish(&client, &p) != 0) {
		LOG_WRN("reply publish failed");
	}
}

static int64_t now_ms(void)
{
#ifdef CONFIG_EVC_EXPIRY
	struct timespec ts;
	clock_gettime(CLOCK_REALTIME, &ts); /* requires SNTP-synchronised realtime */
	return (int64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
#else
	return k_uptime_get();
#endif
}

static void handle(const char *topic, const char *body, size_t len)
{
	struct evc_msg m;
	struct evc_receipt r;
	int n = -1;
	if (evc_parse(body, len, &m) != 0 || m.reply[0] == '\0') {
		LOG_WRN("malformed request on %s", topic);
		return;
	}
	/* AUTH 077: commands, fences and stops need a valid MAC; nothing changes without one */
	{
		const char *kind = strcmp(topic, topics[0]) == 0 ? "cmd" : strcmp(topic, topics[1]) == 0 ? "fence"
								 : strcmp(topic, topics[3]) == 0 ? "stop" : NULL;
		if (kind != NULL && !auth_request_ok(kind, &m, body, len)) {
			LOG_WRN("unauthenticated %s on %s", kind, topic);
			n = evc_format_error(reply, sizeof(reply), m.cmd[0] ? m.cmd : m.token, "REJECTED", "UNAUTHENTICATED", -EACCES);
			n = n > 0 ? auth_sign_reply(reply, sizeof(reply), n) : -1;
			if (n > 0 && (size_t)n < sizeof(reply)) {
				publish(m.reply, reply, (size_t)n);
			}
			return;
		}
	}
	if (strcmp(topic, topics[4]) == 0) { /* ping: health, never evidence */
		n = snprintf(reply, sizeof(reply),
			     "{\"device\":\"%s\",\"mode\":\"%s\",\"ok\":true,\"level\":%lu,\"cutoff\":%llu}", opt_device,
			     mode_names[fw_mode],
			     (unsigned long)(fw_mode == EVC_MODE_PLAIN ? plain_level : core.level),
			     (unsigned long long)core.cutoff);
	} else if (fw_mode == EVC_MODE_PLAIN) {
		/* release 074: no evidence. Every delivered command is executed; the reply is APPLIED
		 * (or UNCERTAIN when the plant write failed). Only cmd and ping are subscribed. */
		if (strcmp(topic, topics[0]) != 0) {
			return;
		}
		memset(&r, 0, sizeof(r));
		strncpy(r.id, m.cmd, EVC_ID_MAX - 1);
		r.seq = m.seq;
		r.value = m.value;
		if (apply_effect(NULL, m.cmd, m.value) == 0) {
			plain_level += m.value;
			plain_version++;
			r.level_after = plain_level;
			r.owner_version = plain_version;
			r.outcome = EVC_APPLIED;
			r.reason = EVC_R_APPLIED;
			n = evc_format_receipt(reply, sizeof(reply), &r, opt_device);
		} else {
			n = evc_format_error(reply, sizeof(reply), m.cmd, "UNCERTAIN", "EFFECT_FAILED", -EIO);
		}
	} else if (strcmp(topic, topics[0]) == 0) { /* cmd */
		int rc = evc_core_command(&core, &io, m.cmd, m.seq, m.value, now_ms(), m.expires_at_ms, &r);
		if (rc == 0) {
			n = evc_format_receipt(reply, sizeof(reply), &r, opt_device);
		} else if (rc == -EEXIST) {
			n = evc_format_error(reply, sizeof(reply), m.cmd, "IDENTITY_CONFLICT", "IDENTITY_CONFLICT", rc);
		} else if (rc == -EAGAIN) {
			n = evc_format_error(reply, sizeof(reply), m.cmd, "UNCERTAIN", "RECEIPT_MAY_BE_EVICTED", rc);
		} else {
			n = evc_format_error(reply, sizeof(reply), m.cmd, "UNCERTAIN", "NO_TERMINAL_DECISION", rc);
		}
	} else if (strcmp(topic, topics[1]) == 0) { /* fence */
		/* AUTH 077: a cutoff far above every sequence seen is refused (it would seal future identities) */
		uint64_t seen = core.hw_limit > core.cutoff ? core.hw_limit : core.cutoff;
		if (m.cutoff > seen + (uint64_t)CONFIG_EVC_AUTH_CUTOFF_AHEAD) {
			n = snprintf(reply, sizeof(reply),
				     "{\"cutoff\":%llu,\"token\":\"%s\",\"ok\":false,\"reason\":\"CUTOFF_TOO_FAR_AHEAD\",\"owner_version\":%lu,"
				     "\"level\":%lu}",
				     (unsigned long long)core.cutoff, m.token, (unsigned long)core.owner_version, (unsigned long)core.level);
		} else {
			int rc = evc_core_fence(&core, &io, m.cutoff);
			n = evc_format_fence(reply, sizeof(reply), core.cutoff, core.owner_version, core.level, m.token, rc == 0);
		}
	} else if (strcmp(topic, topics[2]) == 0) { /* query: the reply says what the owner can certify */
		(void)evc_core_query(&core, m.cmd, m.seq, &r);
		n = evc_format_receipt(reply, sizeof(reply), &r, opt_device);
	} else if (strcmp(topic, topics[3]) == 0) { /* stop */
		int rc = evc_core_stop(&core, &io, m.cmd, m.seq, now_ms(), &r);
		n = rc >= 0 ? evc_format_receipt(reply, sizeof(reply), &r, opt_device)
			    : evc_format_error(reply, sizeof(reply), m.cmd, "UNCERTAIN",
					       rc == -ENOSPC ? "TOMBSTONE_TABLE_FULL" : "STOP_FAILED", rc);
	}
	n = n > 0 && (size_t)n < sizeof(reply) ? auth_sign_reply(reply, sizeof(reply), n) : -1; /* AUTH 077 */
	if (n > 0 && (size_t)n < sizeof(reply)) {
		publish(m.reply, reply, (size_t)n);
	}
}

static void mqtt_evt(struct mqtt_client *c, const struct mqtt_evt *evt)
{
	switch (evt->type) {
	case MQTT_EVT_CONNACK:
		LOG_INF("connected to broker (result %d)", evt->result);
		break;
	case MQTT_EVT_DISCONNECT:
		LOG_WRN("disconnected");
		break;
	case MQTT_EVT_PUBLISH: {
		const struct mqtt_publish_param *p = &evt->param.publish;
		size_t len = p->message.payload.len;
		char topic[EVC_TOPIC_MAX];
		size_t tl = MIN(p->message.topic.topic.size, sizeof(topic) - 1);
		memcpy(topic, p->message.topic.topic.utf8, tl);
		topic[tl] = '\0';
		if (len >= sizeof(payload)) {
			LOG_WRN("payload too large");
			break;
		}
		if (mqtt_read_publish_payload_blocking(c, payload, len) < 0) {
			break;
		}
		payload[len] = '\0';
		/* Execute first, acknowledge after: a crash in between makes the broker
		 * redeliver; the claim high-water mark makes the core answer UNCERTAIN
		 * for such a redelivery instead of executing it again. */
		handle(topic, payload, len);
		if (p->message.topic.qos == MQTT_QOS_1_AT_LEAST_ONCE) {
			struct mqtt_puback_param ack = {.message_id = p->message_id};
			mqtt_publish_qos1_ack(c, &ack);
		}
		break;
	}
	default:
		break;
	}
}

static int connect_broker(void)
{
	struct sockaddr_in *b = (struct sockaddr_in *)&broker;
	b->sin_family = AF_INET;
	b->sin_port = htons((uint16_t)opt_broker_port);
	zsock_inet_pton(AF_INET, opt_broker, &b->sin_addr);
	mqtt_client_init(&client);
	client.broker = &broker;
	client.evt_cb = mqtt_evt;
	client.client_id.utf8 = (const uint8_t *)client_id;
	client.client_id.size = strlen(client_id);
	client.protocol_version = MQTT_VERSION_3_1_1;
	client.clean_session = 0; /* persistent session: broker queues while offline */
	client.rx_buf = rx_buf;
	client.rx_buf_size = sizeof(rx_buf);
	client.tx_buf = tx_buf;
	client.tx_buf_size = sizeof(tx_buf);
	client.transport.type = MQTT_TRANSPORT_NON_SECURE;
	int rc = mqtt_connect(&client);
	if (rc != 0) {
		return rc;
	}
	struct mqtt_topic t[N_TOPICS];
	const char *kinds[N_TOPICS] = {"cmd", "fence", "query", "stop", "ping"};
	size_t nt = 0;
	for (int i = 0; i < N_TOPICS; i++) {
		snprintf(topics[i], sizeof(topics[i]), TOPIC_FMT, opt_ns, opt_device, kinds[i]);
		/* release 074: plain mode subscribes cmd and ping only (no evidence topics) */
		if (fw_mode == EVC_MODE_PLAIN && i != 0 && i != 4) {
			continue;
		}
		t[nt].topic.utf8 = (const uint8_t *)topics[i];
		t[nt].topic.size = strlen(topics[i]);
		t[nt].qos = MQTT_QOS_1_AT_LEAST_ONCE;
		nt++;
	}
	/* the CONNACK must be processed before subscribing */
	struct zsock_pollfd fd = {.fd = client.transport.tcp.sock, .events = ZSOCK_POLLIN};
	if (zsock_poll(&fd, 1, 5000) > 0) {
		mqtt_input(&client);
	}
	struct mqtt_subscription_list l = {.list = t, .list_count = nt, .message_id = next_mid++};
	return mqtt_subscribe(&client, &l);
}

/* a port option: unset (the parser's UINT32_MAX, or 0) -> the Kconfig default; out of range -> stop */
static uint32_t port_or_default(uint32_t v, uint32_t dflt, const char *name)
{
	if (v == 0 || v == UINT32_MAX) {
		return dflt;
	}
	if (v > 65535) {
		LOG_ERR("-%s=%u: not a TCP port", name, (unsigned)v);
		k_sleep(K_MSEC(100));
		k_panic();
	}
	return v;
}

static void apply_defaults(void)
{
	if (opt_device == NULL || opt_device[0] == '\0') {
		opt_device = CONFIG_EVC_DEVICE_ID;
	}
	if (opt_ns == NULL || opt_ns[0] == '\0') {
		opt_ns = "evicon";
	}
	if (opt_broker == NULL || opt_broker[0] == '\0') {
		opt_broker = CONFIG_EVC_BROKER_ADDR;
	}
	opt_broker_port = port_or_default(opt_broker_port, CONFIG_EVC_BROKER_PORT, "evc_broker_port");
	if (opt_plant == NULL || opt_plant[0] == '\0') {
		opt_plant = CONFIG_EVC_PLANT_ADDR;
	}
	opt_plant_port = port_or_default(opt_plant_port, CONFIG_EVC_PLANT_PORT, "evc_plant_port");
}

int main(void)
{
	apply_defaults();
	auth_init(); /* AUTH 077 */
	if (opt_mode != NULL && opt_mode[0] != '\0') {
		int found = -1;
		for (int i = 0; i < (int)ARRAY_SIZE(mode_names); i++) {
			if (strcmp(opt_mode, mode_names[i]) == 0) {
				found = i;
			}
		}
		if (found < 0) {
			LOG_ERR("-evc_mode=%s: expected cutoff, stop or plain", opt_mode);
			k_sleep(K_MSEC(100));
			k_panic(); /* never run with an evidence mode nobody asked for */
		}
		fw_mode = (enum evc_mode)found;
	}
	if (strcmp(opt_ns, "evicon") == 0) {
		snprintf(client_id, sizeof(client_id), "%s", opt_device);
	} else {
		snprintf(client_id, sizeof(client_id), "%s-%s", opt_ns, opt_device);
	}
	settings_subsys_init();
	settings_load();
	unsigned flags = (fw_mode == EVC_MODE_STOP ? EVC_F_STOP : 0) | (IS_ENABLED(CONFIG_EVC_EXPIRY) ? EVC_F_EXPIRY : 0) |
			 (IS_ENABLED(CONFIG_EVC_GUARD_EVICTED) ? EVC_F_GUARD_EVICTED : 0);
	evc_core_init(&core, &persisted, CONFIG_EVC_DEDUP_HORIZON_MS, CONFIG_EVC_BOUND, flags);
	LOG_INF("evicon AUTHENTICATED firmware (077, cutoff bound %d ahead)", CONFIG_EVC_AUTH_CUTOFF_AHEAD);
	LOG_INF("evicon firmware %s mode=%s ns=%s broker=%s:%u plant=%s:%u: cutoff=%llu hw_limit=%llu tombstones=%u ring=%d "
		"horizon_ms=%d",
		opt_device, mode_names[fw_mode], opt_ns, opt_broker, (unsigned)opt_broker_port, opt_plant, (unsigned)opt_plant_port,
		(unsigned long long)persisted.cutoff, (unsigned long long)persisted.hw_limit, (unsigned)persisted.ntomb,
		EVC_RING_SLOTS, CONFIG_EVC_DEDUP_HORIZON_MS);
	for (;;) {
		if (connect_broker() != 0) {
			k_sleep(K_SECONDS(1));
			continue;
		}
		for (;;) {
			struct zsock_pollfd fd = {.fd = client.transport.tcp.sock, .events = ZSOCK_POLLIN};
			int rc = zsock_poll(&fd, 1, mqtt_keepalive_time_left(&client));
			if (rc < 0) {
				break;
			}
			if (rc > 0 && mqtt_input(&client) != 0) {
				break;
			}
			rc = mqtt_live(&client);
			if (rc != 0 && rc != -EAGAIN) {
				break;
			}
		}
		mqtt_abort(&client);
		k_sleep(K_SECONDS(1));
	}
	return 0;
}
