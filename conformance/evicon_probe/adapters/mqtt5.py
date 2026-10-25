"""MQTT 5 request/response to a device (Mosquitto 2.x, EMQX 5.x).

Data path: probe client -> [fault proxy] -> broker -> device.
The device is conformance/reference/mqtt_device.py (or Zephyr native_sim
firmware) which reports effects to evicon-plant; truth is read from the plant.

Measured semantics: QoS 1/2 redelivery after reconnect, Message Expiry
Interval (does broker expiry bound device execution?), persistent sessions
for offline devices (P7), and the absence of any receipt query or stop.

``reply_mode: payload`` targets the Zephyr firmware (firmware/zephyr-cutoff,
MQTT 3.1.1): reply topic and identity travel in the JSON payload, and the
firmware also answers query/stop/fence on evicon/<dev>/{query,stop,fence}.

Status: written against paho-mqtt 2.1 and the MQTT 5.0 specification; release
065 ran it against Mosquitto 2.0.18 on one host (local self-test of `measure
mosquitto`), which exposed the lost-subscription bug fixed in setup(). The
testbed run (Mosquitto 2.0.20 in docker on VM-B) is the measurement.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
import uuid
from typing import Optional

from ..model import APPLIED, OK, STOPPED, TIMEOUT, UNKNOWN, ClientObs, EvidenceObs, Unsupported
from ..plant import PlantClient
from .base import STATUS_TESTED_TESTBED, Adapter


class Mqtt5RpcAdapter(Adapter):
    system = "MQTT 5 broker"
    operation = "request/response PUBLISH (correlation data)"
    implementation_status = STATUS_TESTED_TESTBED  # testbed: Mosquitto 2.0.20 (065-067) and EMQX 5.8.6 (067)
    references = ["https://docs.oasis-open.org/mqtt/mqtt/v5.0/mqtt-v5.0.html", "https://mosquitto.org/man/mosquitto-conf-5.html"]

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        import paho.mqtt.client as mqtt  # optional dependency
        from paho.mqtt.packettypes import PacketTypes
        from paho.mqtt.properties import Properties
        self._mqtt, self._Props, self._PT = mqtt, Properties, PacketTypes
        self.system = cfg.get("broker_name", "MQTT 5 broker")
        host, port = cfg["broker"].rsplit(":", 1)
        self.broker = (host, int(port))
        self.device_id = cfg.get("device_id", "mqtt-1")
        self.prefix = cfg.get("topic_prefix", "evicon")
        self.qos = int(cfg.get("qos", 1))
        self.expiry = cfg.get("message_expiry_s")
        self.device_admin = cfg.get("device_admin_url", "").rstrip("/")
        self.status_query = bool(cfg.get("status_query", False))
        self.reply_mode = cfg.get("reply_mode", "properties")
        if self.reply_mode not in ("properties", "payload"):
            raise ValueError("reply_mode must be properties or payload")
        self.protocol = int(cfg.get("protocol", 5 if self.reply_mode == "properties" else 4))
        if self.reply_mode == "payload" and "first_sequence" not in cfg:
            self._seq = int(time.time()) * 1000  # the firmware cutoff persists across runs
        self.plant = PlantClient(url=cfg["plant_url"])
        self.declared = cfg.get("declared", {"T": float(self.expiry)} if self.expiry else {})
        self.client_id = f"evicon-probe-{uuid.uuid4().hex[:8]}"
        self.resp_topic = f"{self.prefix}/{self.device_id}/resp/{self.client_id}"
        self._waiters: dict = {}
        self._lock = threading.Lock()
        self.c = None
        # Release 068: control path (query, stop, fence) on its own connection straight to the
        # broker, bypassing the fault proxy (the base class rule). On the one proxied connection a
        # delayed command held every later byte of the stream, so a fence sent after it reached
        # the device after it too and P9F could not measure F (found running the Zephyr firmware).
        cb = cfg.get("control_broker", "")
        self.control_broker = tuple((cb.rsplit(":", 1)[0], int(cb.rsplit(":", 1)[1]))) if cb else None
        self.cc = None

    def setup(self) -> None:
        m = self._mqtt
        proto = m.MQTTv5 if self.protocol == 5 else m.MQTTv311
        extra = {} if self.protocol == 5 else {"clean_session": False}  # 3.1.1: persistent session for the random client id
        self.c = m.Client(m.CallbackAPIVersion.VERSION2, client_id=self.client_id, protocol=proto, **extra)
        self.c.on_message = self._on_message
        self.c.on_connect = self._on_connect
        self.c.reconnect_delay_set(min_delay=1, max_delay=2)
        self._subscribed = threading.Event()
        # Release 065 (first run against a real broker): a fault window can make
        # the broker drop the probe's connection; paho reconnects, but with
        # MQTT 5's default session expiry (0) the response subscription was
        # gone and every later reply was lost. Keep the session for a while
        # and subscribe again on every (re)connect. keepalive_s must exceed the
        # longest injected delay, or the broker closes the connection and a
        # delayed request is lost instead of arriving late.
        kw = {}
        if self.protocol == 5:
            props = self._Props(self._PT.CONNECT)
            props.SessionExpiryInterval = int(self.cfg.get("session_expiry_s", 300))
            kw = {"clean_start": m.MQTT_CLEAN_START_FIRST_ONLY, "properties": props}
        self.c.connect(self.broker[0], self.broker[1], keepalive=int(self.cfg.get("keepalive_s", 60)), **kw)
        self.c.loop_start()
        if not self._subscribed.wait(10):
            raise RuntimeError(f"MQTT: no SUBACK from {self.broker[0]}:{self.broker[1]} within 10 s")
        if self.control_broker:
            ctl_sub = threading.Event()
            self.cc = m.Client(m.CallbackAPIVersion.VERSION2, client_id=self.client_id + "-ctl", protocol=proto, **extra)
            self.cc.on_message = self._on_message

            def on_ctl_connect(client, userdata, flags, reason_code, properties=None):
                client.on_subscribe = lambda *_a, **_k: ctl_sub.set()
                client.subscribe(self.resp_topic + "-ctl" if self.reply_mode == "properties" else self.resp_topic + "-ctl/#", qos=1)
            self.cc.on_connect = on_ctl_connect
            self.cc.reconnect_delay_set(min_delay=1, max_delay=2)
            self.cc.connect(self.control_broker[0], self.control_broker[1], keepalive=60, **kw)
            self.cc.loop_start()
            if not ctl_sub.wait(10):
                raise RuntimeError(f"MQTT: no SUBACK on the control connection {self.control_broker[0]}:{self.control_broker[1]} within 10 s")
        time.sleep(0.2)

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        def subscribed(*_a, **_k):
            self._subscribed.set()
        client.on_subscribe = subscribed
        client.subscribe(self.resp_topic if self.reply_mode == "properties" else self.resp_topic + "/#", qos=1)

    def teardown(self) -> None:
        # Disconnect first: paho's loop_stop() waits for every unacknowledged
        # QoS 1 message, and a request lost in a fault window (P1) never gets
        # its PUBACK, so loop_stop() alone never returned (release 065).
        for c in (self.c, self.cc):
            if c is None:
                continue
            try:
                c.disconnect()
            except Exception:  # noqa: BLE001 - already disconnected
                pass
            t = threading.Thread(target=c.loop_stop, daemon=True)
            t.start()
            t.join(5)

    def _on_message(self, client, userdata, msg):
        if self.reply_mode == "payload":
            try:
                body = json.loads(msg.payload or b"{}")
            except ValueError:
                return
            kind = msg.topic.rsplit("/", 1)[-1]
            key = f"{kind}:{body.get('token') or body.get('cmd', '')}"
        else:
            corr = getattr(getattr(msg, "properties", None), "CorrelationData", None)
            if corr is None:
                return
            key = corr.decode(errors="replace")
        with self._lock:
            w = self._waiters.get(key)
        if w is not None:
            try:
                w["payload"] = json.loads(msg.payload or b"{}")
            except ValueError:
                w["payload"] = {"raw": msg.payload.decode(errors="replace")}
            w["event"].set()

    def _rpc(self, topic: str, key: str, body: dict, timeout: float, control: bool = False):
        client = self.cc if (control and self.cc is not None) else self.c
        props = self._Props(self._PT.PUBLISH)
        props.ResponseTopic = self.resp_topic + ("-ctl" if client is self.cc and client is not None else "")
        props.CorrelationData = key.encode()
        if self.expiry:
            props.MessageExpiryInterval = int(self.expiry)
        w = {"event": threading.Event(), "payload": None}
        with self._lock:
            self._waiters[key] = w
        t0 = time.monotonic()
        client.publish(topic, json.dumps(body), qos=self.qos, properties=props)
        ok = w["event"].wait(timeout)
        with self._lock:
            self._waiters.pop(key, None)
        return ok, w["payload"], (time.monotonic() - t0) * 1000

    def _payload_rpc(self, kind: str, key: str, body: dict, timeout: float):
        ctl = kind != "cmd" and self.cc is not None
        client = self.cc if ctl else self.c
        body = dict(body, reply=f"{self.resp_topic}{'-ctl' if ctl else ''}/{kind}")
        w = {"event": threading.Event(), "payload": None}
        with self._lock:
            self._waiters[f"{kind}:{key}"] = w
        t0 = time.monotonic()
        client.publish(f"{self.prefix}/{self.device_id}/{kind}", json.dumps(body), qos=self.qos)
        ok = w["event"].wait(timeout)
        with self._lock:
            self._waiters.pop(f"{kind}:{key}", None)
        return ok, w["payload"], (time.monotonic() - t0) * 1000

    def send(self, command_id: str, timeout_s: Optional[float] = None) -> ClientObs:
        # a resend with the same identity re-publishes with the same correlation data
        t = timeout_s or self.client_timeout_s
        if self.reply_mode == "payload":
            ok, payload, ms = self._payload_rpc("cmd", command_id, {"cmd": command_id, "seq": self.sequence_of(command_id),
                                                                   "value": int(self.value)}, t)
        else:
            ok, payload, ms = self._rpc(f"{self.prefix}/{self.device_id}/cmd", command_id,
                                        {"cmd": command_id, "value": int(self.value)}, t)
        if not ok:
            return ClientObs(TIMEOUT, UNKNOWN, ms, detail="no response before client timeout")
        return ClientObs(OK, (payload or {}).get("outcome", APPLIED), ms, raw=payload)

    def query(self, command_id: str) -> EvidenceObs:
        if self.reply_mode == "payload":
            # The firmware certifies the outcome itself (evc_core_query): APPLIED
            # or STOPPED are terminal; NOT_FOUND ("not started yet") and
            # UNCERTAIN ("receipt may be evicted") are not. The query carries
            # the identity's sequence, which the certification depends on.
            ok, p, ms = self._payload_rpc("query", command_id, {"cmd": command_id, "seq": self.sequence_of(command_id)}, 5.0)
            o = (p or {}).get("outcome", UNKNOWN) if ok else UNKNOWN
            return EvidenceObs(True, o, o in (APPLIED, STOPPED), ms, detail=(p or {}).get("reason", ""), raw=p)
        if not self.status_query:
            raise Unsupported("MQTT has no receipt query; device status topic not enabled")
        ok, payload, ms = self._rpc(f"{self.prefix}/{self.device_id}/query", "q-" + command_id, {"cmd": command_id}, 5.0, control=True)
        if not ok:
            return EvidenceObs(True, UNKNOWN, False, ms, detail="no status reply")
        o = (payload or {}).get("outcome", UNKNOWN)
        return EvidenceObs(True, o, o == APPLIED, ms, raw=payload)

    def stop(self, command_id: str) -> EvidenceObs:
        if self.reply_mode != "payload" or not self.cfg.get("firmware_stop", False):
            raise Unsupported("MQTT offers no per-identity stop")
        ok, p, ms = self._payload_rpc("stop", command_id, {"cmd": command_id, "seq": self.sequence_of(command_id)}, 5.0)
        o = (p or {}).get("outcome", UNKNOWN) if ok else UNKNOWN
        # FUTURE_BLOCKED_PAST_UNKNOWN / TOMBSTONE_TABLE_FULL arrive as UNCERTAIN: not terminal
        return EvidenceObs(True, o, o in (APPLIED, STOPPED), ms, detail=(p or {}).get("reason", ""), raw=p)

    def fence(self, command_ids: list) -> EvidenceObs:
        if self.reply_mode != "payload":
            raise Unsupported("MQTT offers no fence")
        cutoff = max(self.sequence_of(c) for c in command_ids)
        token = f"f-{self.run_tag}-{cutoff}"
        ok, p, ms = self._payload_rpc("fence", token, {"cutoff": cutoff, "token": token}, 5.0)
        if not ok or not (p or {}).get("ok"):
            return EvidenceObs(True, UNKNOWN, False, ms, raw=p)
        # A cutoff blocks the future; it does not recover the past (review
        # RV04). The outcome is whatever the firmware certifies for the
        # identity after the fence; "fence OK + no receipt" is never STOPPED.
        q = self.query(command_ids[0])
        terminal = q.outcome in (APPLIED, STOPPED)
        return EvidenceObs(True, q.outcome, terminal, ms + q.latency_ms,
                           detail=f"cutoff={(p or {}).get('cutoff')};query_reason={q.detail}", raw={"fence": p, "query": q.raw})

    def effects(self, command_id: str, baseline) -> int:
        return self.plant.effects(command_id, self.device_id)

    def set_device_online(self, online: bool) -> None:
        if not self.device_admin:
            raise Unsupported("device_admin_url not configured")
        req = urllib.request.Request(f"{self.device_admin}/_test/online?v={'true' if online else 'false'}", data=b"", method="POST")
        urllib.request.urlopen(req, timeout=5).read()

    def expiry_s(self):
        return self.expiry
