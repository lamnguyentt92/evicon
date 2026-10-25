#pragma once
#include <stdint.h>
#include <stddef.h>
#include <sys/socket.h>
struct mqtt_utf8 { const uint8_t *utf8; uint32_t size; };
struct mqtt_binstr { uint8_t *data; uint32_t len; };
struct mqtt_topic { struct mqtt_utf8 topic; uint8_t qos; };
struct mqtt_publish_message { struct mqtt_topic topic; struct mqtt_binstr payload; };
struct mqtt_publish_param { struct mqtt_publish_message message; uint16_t message_id; };
struct mqtt_puback_param { uint16_t message_id; };
enum { MQTT_QOS_1_AT_LEAST_ONCE = 1 };
enum mqtt_evt_type { MQTT_EVT_CONNACK, MQTT_EVT_DISCONNECT, MQTT_EVT_PUBLISH };
struct mqtt_evt { enum mqtt_evt_type type; int result; union { struct mqtt_publish_param publish; } param; };
struct mqtt_client;
typedef void (*mqtt_evt_cb_t)(struct mqtt_client *c, const struct mqtt_evt *evt);
struct mqtt_transport { int type; struct { int sock; } tcp; };
enum { MQTT_TRANSPORT_NON_SECURE = 0, MQTT_VERSION_3_1_1 = 4 };
struct mqtt_client { void *broker; mqtt_evt_cb_t evt_cb; struct mqtt_utf8 client_id; int protocol_version; int clean_session;
  uint8_t *rx_buf; size_t rx_buf_size; uint8_t *tx_buf; size_t tx_buf_size; struct mqtt_transport transport; };
struct mqtt_subscription_list { struct mqtt_topic *list; uint16_t list_count; uint16_t message_id; };
void mqtt_client_init(struct mqtt_client *c);
int mqtt_connect(struct mqtt_client *c);
int mqtt_publish(struct mqtt_client *c, const struct mqtt_publish_param *p);
int mqtt_publish_qos1_ack(struct mqtt_client *c, const struct mqtt_puback_param *p);
int mqtt_read_publish_payload_blocking(struct mqtt_client *c, void *b, size_t n);
int mqtt_subscribe(struct mqtt_client *c, const struct mqtt_subscription_list *l);
int mqtt_input(struct mqtt_client *c);
int mqtt_live(struct mqtt_client *c);
int mqtt_abort(struct mqtt_client *c);
int mqtt_keepalive_time_left(const struct mqtt_client *c);
