"""Adapter registry. Imports are lazy so that a missing optional client
library (paho-mqtt, aiocoap, nats-py, ...) only affects that adapter."""
from __future__ import annotations

import importlib

REGISTRY = {
    "evicon_endpoint": ("evicon_endpoint", "EviconEndpointAdapter"),
    "http_idempotency": ("http_idempotency", "HttpIdempotencyAdapter"),
    "evicon_device": ("evicon_device", "EviconDeviceAdapter"),
    "coap": ("coap", "CoapAdapter"),
    "mqtt5_rpc": ("mqtt5", "Mqtt5RpcAdapter"),
    "nats_jetstream": ("nats_js", "NatsJetStreamAdapter"),
    "thingsboard_rpc": ("thingsboard", "ThingsBoardRpcAdapter"),
    "leshan_execute": ("leshan", "LeshanExecuteAdapter"),
    "matter_invoke": ("matter", "MatterInvokeAdapter"),
    "k8s_create": ("k8s", "K8sCreateAdapter"),
    "s3_conditional_put": ("s3_conditional", "S3ConditionalPutAdapter"),
    "temporal_activity": ("temporal_activity", "TemporalActivityAdapter"),
    "dbos_step": ("dbos_step", "DbosStepAdapter"),
}


def load(name: str):
    if name not in REGISTRY:
        raise KeyError(f"unknown adapter {name!r}; known: {sorted(REGISTRY)}")
    mod, cls = REGISTRY[name]
    return getattr(importlib.import_module(f"{__name__}.{mod}"), cls)
