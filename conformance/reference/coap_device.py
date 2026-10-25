"""Reference CoAP actuator (aiocoap) that reports effects to evicon-plant.

  POST /actuate   {"cmd": <id>, "value": <int>} -> 2.04 {"outcome":"APPLIED"}
  GET  /status?cmd=<id>   (only with --status) application-level receipt query

The resource deliberately has NO application-level deduplication unless
--app-dedup is given: EviCon-Probe measures what the CoAP stack's message layer
provides. --exchange-lifetime shortens EXCHANGE_LIFETIME for local tests only
(the real campaign keeps the RFC 7252 default of 247 s).

Run:  python3 coap_device.py --bind 0.0.0.0 --port 5683 --plant http://127.0.0.1:18090 --health-port 18211
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import aiocoap  # noqa: E402
import aiocoap.resource as resource  # noqa: E402
from aiocoap.numbers import constants  # noqa: E402

from evicon_probe.plant import PlantClient  # noqa: E402
from reference._health import serve_health  # noqa: E402


class Actuate(resource.Resource):
    def __init__(self, plant: PlantClient, device: str, app_dedup: bool, delay: float):
        super().__init__()
        self.plant, self.device, self.app_dedup, self.delay = plant, device, app_dedup, delay
        self.done: dict = {}

    async def render_post(self, request):
        try:
            body = json.loads(request.payload or b"{}")
            cmd, value = str(body["cmd"]), int(body.get("value", 1))
        except (ValueError, KeyError):
            return aiocoap.Message(code=aiocoap.BAD_REQUEST)
        if self.app_dedup and cmd in self.done:
            return aiocoap.Message(code=aiocoap.CHANGED, payload=json.dumps({"outcome": "APPLIED", "replay": True}).encode())
        if self.delay:
            await asyncio.sleep(self.delay)
        await asyncio.get_running_loop().run_in_executor(None, self.plant.apply, self.device, cmd, value)
        self.done[cmd] = True
        return aiocoap.Message(code=aiocoap.CHANGED, payload=json.dumps({"outcome": "APPLIED"}).encode())


class Status(resource.Resource):
    def __init__(self, act: Actuate):
        super().__init__()
        self.act = act

    async def render_get(self, request):
        q = dict(x.split("=", 1) for x in request.opt.uri_query if "=" in x)
        o = "APPLIED" if q.get("cmd") in self.act.done else "NOT_FOUND"
        return aiocoap.Message(code=aiocoap.CONTENT, payload=json.dumps({"outcome": o}).encode())


async def main(a):
    if a.exchange_lifetime:
        constants.TransportTuning.EXCHANGE_LIFETIME = property(lambda self: float(a.exchange_lifetime))
    plant = PlantClient(url=a.plant)
    root = resource.Site()
    act = Actuate(plant, a.device, a.app_dedup, a.effect_delay)
    root.add_resource(["actuate"], act)
    if a.status:
        root.add_resource(["status"], Status(act))
    transports = [t for t in a.transports.split(",") if t] or None
    await aiocoap.Context.create_server_context(root, bind=(a.bind, a.port), transports=transports)
    if a.health_port:
        serve_health(a.health_port, "coap-device", lambda: {"device": a.device, "coap_port": a.port, "app_dedup": a.app_dedup,
                                                            "status": a.status}, a.health_bind)
    print(f"coap_device listening on {a.bind}:{a.port} device={a.device}", flush=True)
    await asyncio.get_running_loop().create_future()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5683)
    ap.add_argument("--plant", required=True)
    ap.add_argument("--device", default="coap-1")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--app-dedup", action="store_true")
    ap.add_argument("--effect-delay", type=float, default=0.0)
    ap.add_argument("--transports", default="", help="aiocoap server transports, e.g. udp6 or simplesocketserver (IPv4-only hosts)")
    ap.add_argument("--exchange-lifetime", type=float, default=0.0, help="test-only override of EXCHANGE_LIFETIME (s)")
    ap.add_argument("--health-port", type=int, default=0, help="HTTP GET /healthz (role, release, pid) on this port")
    ap.add_argument("--health-bind", default="0.0.0.0")
    asyncio.run(main(ap.parse_args()))
