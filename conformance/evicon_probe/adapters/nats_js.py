"""NATS JetStream publish with Nats-Msg-Id (a pure K1 example).

The stream is the owner: a stored message is the effect. Each command uses
its own subject <prefix>.<command_id>, so the per-subject message count from
stream info is the per-identity truth. JetStream deduplicates by Nats-Msg-Id
inside the stream's duplicate window (default 2 min); there is no lookup by
message ID, no stop and no fence.

Status: written against nats-py 2.9 and nats-server 2.10 docs; not run
against a server in this release.
"""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Optional

from ..model import APPLIED, ERROR, OK, TIMEOUT, UNKNOWN, ClientObs
from .base import STATUS_TESTED_TESTBED, Adapter


class NatsJetStreamAdapter(Adapter):
    system = "NATS JetStream"
    operation = "js.publish with Nats-Msg-Id"
    implementation_status = STATUS_TESTED_TESTBED  # testbed: NATS JetStream 2.10.22 (067)
    references = ["https://docs.nats.io/nats-concepts/jetstream/streams", "https://docs.nats.io/using-nats/developer/develop_jetstream/model_deep_dive"]

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        import nats  # optional dependency
        self._nats = nats
        self.data_server = cfg["data_server"]          # nats://{proxy}
        self.control_server = cfg.get("control_server", cfg["data_server"])
        self.stream = cfg.get("stream", "EVICONPROBE")
        self.prefix = cfg.get("subject_prefix", "eviconprobe")
        self.window_s = float(cfg.get("duplicate_window_s", 120))
        self.declared = cfg.get("declared", {"D_h": self.window_s})
        self._loop = asyncio.new_event_loop()
        self._t = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._t.start()
        self.nc = self.js = self.cnc = self.cjs = None

    def _run(self, coro, timeout=None):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    def setup(self) -> None:
        async def go():
            from nats.js.api import StreamConfig
            self.nc = await self._nats.connect(servers=[self.data_server], max_reconnect_attempts=-1, reconnect_time_wait=0.5)
            self.js = self.nc.jetstream()
            self.cnc = await self._nats.connect(servers=[self.control_server])
            self.cjs = self.cnc.jetstream()
            cfg = StreamConfig(name=self.stream, subjects=[f"{self.prefix}.>"], duplicate_window=self.window_s)
            try:
                await self.cjs.add_stream(cfg)
            except Exception:
                await self.cjs.update_stream(cfg)
        self._run(go(), 30)

    def teardown(self) -> None:
        async def go():
            for c in (self.nc, self.cnc):
                if c is not None:
                    await c.close()
        try:
            self._run(go(), 10)
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)

    def send(self, command_id: str, timeout_s: Optional[float] = None) -> ClientObs:
        t = timeout_s or self.client_timeout_s

        async def go():
            return await self.js.publish(f"{self.prefix}.{command_id}", b'{"value":%d}' % int(self.value),
                                         headers={"Nats-Msg-Id": command_id}, timeout=t)
        t0 = time.monotonic()
        try:
            ack = self._run(go(), t + 1)
        except Exception as e:  # nats.errors.TimeoutError, NoRespondersError, connection errors
            name = type(e).__name__
            st = TIMEOUT if "Timeout" in name or "Connection" in name or "NoResponders" in name else ERROR
            return ClientObs(st, UNKNOWN, (time.monotonic() - t0) * 1000, detail=name)
        return ClientObs(OK, APPLIED, (time.monotonic() - t0) * 1000, detail=f"seq={ack.seq} duplicate={ack.duplicate}",
                         raw={"seq": ack.seq, "duplicate": bool(ack.duplicate)})

    def effects(self, command_id: str, baseline) -> int:
        async def go():
            info = await self.cjs.stream_info(self.stream, subjects_filter=f"{self.prefix}.{command_id}")
            return int((info.state.subjects or {}).get(f"{self.prefix}.{command_id}", 0))
        return self._run(go(), 10)

    def dedup_horizon_hint_s(self):
        return self.window_s
