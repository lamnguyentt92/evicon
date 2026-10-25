"""CoAP (RFC 7252) confirmable POST, stdlib-only raw client.

Identity levels (``identity_level``):
  * ``message``     - a same-identity resend is the *identical datagram*
                      (same source port, Message ID and token), i.e. a CON
                      retransmission. This measures the stack's message-layer
                      deduplication horizon (EXCHANGE_LIFETIME, 247 s default).
  * ``application`` - a same-identity resend is a new CoAP request (new MID and
                      token) carrying the same application command ID. This is
                      what an application retry after a timeout does.

The server under test is any CoAP stack (libcoap, Californium, aiocoap) with
an actuator resource that reports effects to evicon-plant; see
conformance/reference/coap_device.py. Truth is read from the plant.
"""
from __future__ import annotations

import json
import random
import socket
import struct
import threading
import time
import urllib.parse
from typing import Optional

from ..model import APPLIED, OK, ERROR, TIMEOUT, UNKNOWN, ClientObs, EvidenceObs, Unsupported
from ..plant import PlantClient
from .base import STATUS_TESTED_TESTBED, Adapter

CON, NON, ACK, RST = 0, 1, 2, 3
GET, POST = 0x01, 0x02
OPT_URI_PATH, OPT_CONTENT_FORMAT, OPT_URI_QUERY = 11, 12, 15


def _opt_nibble(v: int):
    if v < 13:
        return v, b""
    if v < 269:
        return 13, bytes([v - 13])
    return 14, struct.pack("!H", v - 269)


def encode(mtype: int, code: int, mid: int, token: bytes, options: list, payload: bytes = b"") -> bytes:
    out = bytearray([(1 << 6) | (mtype << 4) | len(token), code]) + struct.pack("!H", mid) + token
    last = 0
    for num, val in sorted(options, key=lambda o: o[0]):
        d, dext = _opt_nibble(num - last)
        l, lext = _opt_nibble(len(val))
        out += bytes([(d << 4) | l]) + dext + lext + val
        last = num
    if payload:
        out += b"\xff" + payload
    return bytes(out)


def decode(b: bytes) -> dict:
    if len(b) < 4 or b[0] >> 6 != 1:
        raise ValueError("not a CoAP v1 message")
    tkl = b[0] & 0x0F
    m = {"type": (b[0] >> 4) & 0x3, "code": b[1], "mid": struct.unpack("!H", b[2:4])[0], "token": b[4:4 + tkl], "payload": b""}
    i = 4 + tkl
    while i < len(b):
        if b[i] == 0xFF:
            m["payload"] = b[i + 1:]
            break
        d, l = b[i] >> 4, b[i] & 0x0F
        i += 1
        for which in ("d", "l"):
            v = d if which == "d" else l
            if v == 13:
                v = b[i] + 13
                i += 1
            elif v == 14:
                v = struct.unpack("!H", b[i:i + 2])[0] + 269
                i += 2
            if which == "d":
                d = v
            else:
                l = v
        i += l
    return m


def code_str(c: int) -> str:
    return f"{c >> 5}.{c & 0x1F:02d}"


class CoapAdapter(Adapter):
    system = "CoAP"
    operation = "CON POST /actuate"
    implementation_status = STATUS_TESTED_TESTBED  # testbed: aiocoap 0.4.17 (065-067)
    references = ["https://www.rfc-editor.org/rfc/rfc7252", "https://www.rfc-editor.org/rfc/rfc9175"]

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        u = urllib.parse.urlparse(cfg["data_uri"])
        self.target = (u.hostname, u.port or 5683)
        self.path = [p for p in u.path.split("/") if p]
        cu = urllib.parse.urlparse(cfg.get("control_uri", cfg["data_uri"]))
        self.control = (cu.hostname, cu.port or 5683)
        self.status_path = [p for p in cfg.get("status_path", "").split("/") if p]
        self.identity_level = cfg.get("identity_level", "message")
        if self.identity_level not in ("message", "application"):
            raise ValueError("identity_level must be message or application")
        self.system = f"CoAP ({cfg.get('stack', 'server stack')}, identity={self.identity_level})"
        self.ack_timeout = float(cfg.get("ack_timeout_s", 2.0))
        self.device_id = cfg.get("device_id", "coap-1")
        self.plant = PlantClient(url=cfg["plant_url"])
        lifetime = cfg.get("exchange_lifetime_s", 247.0)
        self.declared = cfg.get("declared") or ({"D_h": float(lifetime)} if self.identity_level == "message" else {})
        self._mid = random.randint(0, 0xFFFF)
        self._tok = random.getrandbits(32)
        self._sent: dict = {}  # command_id -> datagram (message-level identity)
        self._lock = threading.Lock()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0" if cfg.get("bind_any") else "127.0.0.1", 0))

    def teardown(self) -> None:
        self.sock.close()

    def _next(self):
        self._mid = (self._mid + 1) & 0xFFFF
        self._tok = (self._tok + 1) & 0xFFFFFFFF
        return self._mid, struct.pack("!I", self._tok)

    def _datagram(self, command_id: str) -> tuple:
        if self.identity_level == "message" and command_id in self._sent:
            return self._sent[command_id]
        mid, tok = self._next()
        body = json.dumps({"cmd": command_id, "value": int(self.value)}).encode()
        opts = [(OPT_URI_PATH, p.encode()) for p in self.path] + [(OPT_CONTENT_FORMAT, bytes([50]))]
        dg = (encode(CON, POST, mid, tok, opts, body), mid, tok)
        self._sent[command_id] = dg
        return dg

    def _exchange(self, dg: bytes, mid: int, tok: bytes, target, timeout: float) -> Optional[dict]:
        """CON exchange with retransmission of the identical datagram until a
        response with our token arrives or the client timeout expires."""
        deadline = time.monotonic() + timeout
        next_tx = time.monotonic()
        wait = self.ack_timeout * random.uniform(1.0, 1.5)
        acked = False
        while time.monotonic() < deadline:
            if not acked and time.monotonic() >= next_tx:
                self.sock.sendto(dg, target)
                next_tx = time.monotonic() + wait
                wait *= 2
            self.sock.settimeout(max(0.01, min(deadline, next_tx if not acked else deadline) - time.monotonic()))
            try:
                data, _ = self.sock.recvfrom(4096)
            except (socket.timeout, TimeoutError):
                continue
            except OSError:
                return None
            try:
                m = decode(data)
            except ValueError:
                continue
            if m["type"] == ACK and m["mid"] == mid and m["code"] == 0:
                acked = True  # separate response follows
                continue
            if m["token"] != tok:
                continue
            if m["type"] == CON:  # separate response: acknowledge it
                self.sock.sendto(encode(ACK, 0, m["mid"], b"", []), target)
            return m
        return None

    def send(self, command_id: str, timeout_s: Optional[float] = None) -> ClientObs:
        with self._lock:
            dg, mid, tok = self._datagram(command_id)
            t0 = time.monotonic()
            m = self._exchange(dg, mid, tok, self.target, timeout_s or self.client_timeout_s)
            ms = (time.monotonic() - t0) * 1000
        if m is None:
            return ClientObs(TIMEOUT, UNKNOWN, ms, detail="no response")
        cls = m["code"] >> 5
        payload = {}
        try:
            payload = json.loads(m["payload"] or b"{}")
        except ValueError:
            pass
        if cls == 2:
            return ClientObs(OK, payload.get("outcome", APPLIED), ms, detail=code_str(m["code"]), raw=payload)
        return ClientObs(ERROR if cls == 4 else TIMEOUT, UNKNOWN, ms, detail=code_str(m["code"]), raw=payload)

    def query(self, command_id: str) -> EvidenceObs:
        if not self.status_path:
            raise Unsupported("no application status resource configured")
        with self._lock:
            mid, tok = self._next()
            opts = [(OPT_URI_PATH, p.encode()) for p in self.status_path] + [(OPT_URI_QUERY, f"cmd={command_id}".encode())]
            t0 = time.monotonic()
            m = self._exchange(encode(CON, GET, mid, tok, opts), mid, tok, self.control, 5.0)
            ms = (time.monotonic() - t0) * 1000
        if m is None:
            return EvidenceObs(True, UNKNOWN, False, ms, detail="no response")
        try:
            o = json.loads(m["payload"] or b"{}").get("outcome", UNKNOWN)
        except ValueError:
            o = UNKNOWN
        return EvidenceObs(True, o, o == APPLIED, ms, detail=code_str(m["code"]))

    def effects(self, command_id: str, baseline) -> int:
        return self.plant.effects(command_id, self.device_id)

    def dedup_horizon_hint_s(self):
        if "dedup_horizon_hint_s" in self.cfg:
            return self.cfg["dedup_horizon_hint_s"]
        return self.declared.get("D_h")
