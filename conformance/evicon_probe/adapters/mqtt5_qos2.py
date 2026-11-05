"""MQTT 5 request/response at QoS 2 end to end (release 086, PREREG_extra_086.md rule set QB; reviewer comment M3(2),
question Q6).

The frozen adapter (adapters/mqtt5.py, unchanged and imported) publishes requests at the configured QoS but subscribes
its response topic at QoS 1. This adapter is the same with the response subscription at QoS 2 and its own operation
name, so that the measured contract is a row of its own in the contract map. With adapter_config "qos": 2 every hop of
the request path (probe -> broker -> device, reference/mqtt_device_qos2.py subscribing at QoS 2) and of the reply path
(device -> broker -> probe) uses the QoS 2 exchange. What that certifies after a client-visible timeout is measured
by the same probes as the QoS 1 rows, not assumed.
"""
from __future__ import annotations

from .mqtt5 import Mqtt5RpcAdapter


class Mqtt5RpcQos2Adapter(Mqtt5RpcAdapter):
    operation = "request/response PUBLISH at QoS 2 (correlation data)"

    def __init__(self, cfg: dict):
        if int(cfg.get("qos", 2)) != 2:
            raise ValueError("mqtt5_rpc_qos2: adapter_config qos must be 2")
        super().__init__(dict(cfg, qos=2))

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        def subscribed(*_a, **_k):
            self._subscribed.set()
        client.on_subscribe = subscribed
        client.subscribe(self.resp_topic if self.reply_mode == "properties" else self.resp_topic + "/#", qos=2)
