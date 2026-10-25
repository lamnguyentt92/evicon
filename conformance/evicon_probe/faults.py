"""Directional network fault control for EviCon-Probe.

Three interchangeable controllers implement the same tiny interface
(``drop(direction)``, ``delay(direction, ms)``, ``clear()``):

* ``FaultProxy``   - in-process TCP/UDP proxy (no root, used by local tests and
                     usable on any VM);
* ``Toxiproxy``    - HTTP API of Shopify Toxiproxy (TCP only; runs on VM-C);
* ``Netem``        - ``tc netem`` on an interface (UDP/CoAP/Matter paths on
                     VM-E, needs root; supports dry-run for review).

Direction ``up`` is client -> system under test (the request path), ``down``
is system -> client (the reply path). A fault applies to bytes/datagrams that
arrive *after* the call; bytes already scheduled keep their schedule, so a
delayed request is still delivered late after ``clear()`` (this is what makes
the late-effect probes P3/P9 work). The control path (query/stop/fence) must
not traverse the faulted proxy: adapters keep a separate control URL.
"""
from __future__ import annotations

import asyncio
import json
import shlex
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

UP = "up"
DOWN = "down"
DIRECTIONS = (UP, DOWN)

PASS = "PASS"
DROP = "DROP"
DELAY = "DELAY"


class FaultController:
    def drop(self, direction: str) -> None:
        raise NotImplementedError

    def delay(self, direction: str, ms: int) -> None:
        raise NotImplementedError

    def clear(self) -> None:
        raise NotImplementedError

    def describe(self) -> dict:
        return {"kind": type(self).__name__}

    def quiesce(self, max_wait_s: float = 120.0) -> float:
        """Waits until bytes delayed by an earlier fault window were delivered;
        returns the seconds waited. Controllers that cannot tell return 0."""
        return 0.0


class NoFaults(FaultController):
    """For systems measured only through adapter hooks (e.g. device offline)."""

    def drop(self, direction: str) -> None:
        raise NotImplementedError("no network fault controller configured")

    def delay(self, direction: str, ms: int) -> None:
        raise NotImplementedError("no network fault controller configured")

    def clear(self) -> None:
        pass


def _check_dir(direction: str) -> None:
    if direction not in DIRECTIONS:
        raise ValueError(f"direction must be one of {DIRECTIONS}, got {direction!r}")


@dataclass
class _DirState:
    mode: str = PASS
    delay_ms: int = 0
    forwarded: int = 0
    dropped: int = 0
    delayed: int = 0


@dataclass
class ProxyStats:
    up: _DirState = field(default_factory=_DirState)
    down: _DirState = field(default_factory=_DirState)


class FaultProxy(FaultController):
    """Asyncio TCP or UDP proxy running in a background thread.

    ``linger_s``: after the client closes a TCP connection, the upstream side
    stays open this long after the last queued chunk, so a delayed request is
    processed by the server even though the client already gave up.
    """

    def __init__(self, target_host: str, target_port: int, proto: str = "tcp",
                 listen_host: str = "127.0.0.1", listen_port: int = 0, linger_s: float = 3.0,
                 tls: Optional[dict] = None):
        if proto not in ("tcp", "udp"):
            raise ValueError("proto must be tcp or udp")
        if tls and proto != "tcp":
            raise ValueError("tls needs proto tcp")
        # Release 068: an HTTPS owner (the Kubernetes API) behind a TCP-level fault would see the
        # TLS handshake dropped or delayed, never the HTTP request: the client gives up before it
        # sends anything, so P1-P3 would not measure the request or the reply at all. With
        # ``tls`` the proxy terminates TLS (its own throwaway certificate, which the probe trusts)
        # and opens TLS to the owner (verified against the owner's CA), so faults act on the
        # plaintext request and reply bytes, as on a plaintext owner.
        self.tls = dict(tls or {})
        self.target = (target_host, target_port)
        self.proto = proto
        self.listen = (listen_host, listen_port)
        self.linger_s = linger_s
        self.stats = ProxyStats()
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._server = None
        self._udp_transport = None
        self._sessions: dict = {}
        self._writers: set = set()  # open TCP streams, closed on stop()
        self.address: tuple = ("", 0)
        self._startup_error: Optional[BaseException] = None
        self._last_due = 0.0  # time.monotonic() when the last delayed chunk is due

    # ---- controller interface -------------------------------------------
    def _set(self, direction: str, mode: str, ms: int = 0) -> None:
        _check_dir(direction)
        with self._lock:
            st = getattr(self.stats, direction)
            st.mode, st.delay_ms = mode, int(ms)

    def drop(self, direction: str) -> None:
        self._set(direction, DROP)

    def delay(self, direction: str, ms: int) -> None:
        if ms < 0:
            raise ValueError("delay must be >= 0")
        self._set(direction, DELAY, ms)

    def clear(self) -> None:
        for d in DIRECTIONS:
            self._set(d, PASS)

    def describe(self) -> dict:
        d = {"kind": "FaultProxy", "proto": self.proto, "listen": list(self.address), "target": list(self.target)}
        if self.tls:
            d["tls"] = "terminated at the proxy, re-originated to the owner (faults act on the plaintext request/reply)"
        return d

    def _server_ssl(self):
        if not self.tls:
            return None
        import ssl
        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ctx.load_cert_chain(self.tls["cert_file"], self.tls["key_file"])
        return ctx

    def _client_ssl(self):
        import ssl
        ca = self.tls.get("upstream_ca_file")
        ctx = ssl.create_default_context(cafile=ca) if ca else ssl.create_default_context()
        if self.tls.get("upstream_insecure"):
            ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
        return ctx

    def _due(self, delay_s: float) -> None:
        with self._lock:
            self._last_due = max(self._last_due, time.monotonic() + delay_s)

    def quiesce(self, max_wait_s: float = 120.0) -> float:
        """A TCP stream is ordered: a chunk delayed during a window (e.g. the
        client's PUBACK) holds every later byte of that connection until it is
        due. Probes wait here so one probe's delay never leaks into the next
        (release 065, found with MQTT: P6 after P3)."""
        with self._lock:
            wait = self._last_due - time.monotonic()
        wait = min(max(0.0, wait), max_wait_s)
        if wait > 0:
            time.sleep(wait + 0.05)
        return wait

    def _classify(self, direction: str):
        """Returns (deliver, delay_s) for a chunk arriving now."""
        with self._lock:
            st = getattr(self.stats, direction)
            if st.mode == DROP:
                st.dropped += 1
                return False, 0.0
            if st.mode == DELAY:
                st.delayed += 1
                st.forwarded += 1
                return True, st.delay_ms / 1000.0
            st.forwarded += 1
            return True, 0.0

    # ---- lifecycle ------------------------------------------------------
    def start(self) -> tuple:
        self._thread = threading.Thread(target=self._run, name="fault-proxy", daemon=True)
        self._thread.start()
        if not self._ready.wait(10):
            raise RuntimeError("fault proxy did not start")
        if self._startup_error:
            raise self._startup_error
        return self.address

    def stop(self) -> None:
        if self._loop is None:
            return
        loop = self._loop

        async def _shutdown():
            if self._server is not None:
                self._server.close()
            if self._udp_transport is not None:
                self._udp_transport.close()
            for s in list(self._sessions.values()):
                s.close()
            for w in list(self._writers):  # no transport may outlive the proxy (ResourceWarning on 3.10)
                try:
                    w.close()
                except Exception:
                    pass
            tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            for t in tasks:
                t.cancel()
            if tasks:  # let cancelled pumps/senders unwind before the loop stops
                await asyncio.wait(tasks, timeout=2)
            loop.set_exception_handler(lambda _loop, _ctx: None)  # shutdown: no "destroyed task" noise

        fut = asyncio.run_coroutine_threadsafe(_shutdown(), loop)
        try:
            fut.result(5)
        except Exception:
            pass
        loop.call_soon_threadsafe(loop.stop)
        if self._thread:
            self._thread.join(5)
        self._loop = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            if self.proto == "tcp":
                self._server = loop.run_until_complete(asyncio.start_server(self._on_tcp, *self.listen, ssl=self._server_ssl()))
                self.address = self._server.sockets[0].getsockname()[:2]
            else:
                transport, _ = loop.run_until_complete(loop.create_datagram_endpoint(
                    lambda: _UDPFront(self), local_addr=self.listen))
                self._udp_transport = transport
                self.address = transport.get_extra_info("sockname")[:2]
        except BaseException as e:  # pragma: no cover - surfaced to start()
            self._startup_error = e
            self._ready.set()
            return
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            loop.close()

    # ---- TCP ------------------------------------------------------------
    async def _on_tcp(self, creader, cwriter):
        self._writers.add(cwriter)
        upq: asyncio.Queue = asyncio.Queue()
        downq: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        client_closed = asyncio.Event()
        reply_dropped = []

        async def pump(reader, q, direction):
            while True:
                try:
                    data = await reader.read(65536)
                except (ConnectionError, OSError):
                    data = b""
                if not data:
                    if direction == UP:
                        client_closed.set()
                    elif reply_dropped:
                        # The reply was lost: the client must not learn it from the server's close
                        # either (065 testbed run: HTTP clients saw "closed without response" at
                        # once instead of waiting for their timeout). Close when the client gives up.
                        try:
                            await asyncio.wait_for(client_closed.wait(), 300)
                        except asyncio.TimeoutError:
                            pass
                    await q.put((loop.time(), None))
                    return
                deliver, d = self._classify(direction)
                if deliver:
                    if d:
                        self._due(d)
                    await q.put((loop.time() + d, data))
                elif direction == DOWN:
                    reply_dropped.append(True)

        async def sender(q, writer, linger, first=None):
            while True:
                at, data = first if first is not None else await q.get()
                first = None
                wait = at - loop.time()
                if wait > 0:
                    await asyncio.sleep(wait)
                if data is None:
                    if linger:
                        await asyncio.sleep(self.linger_s)
                    try:
                        writer.close()
                    except Exception:
                        pass
                    return
                try:
                    writer.write(data)
                    await writer.drain()
                except (ConnectionError, OSError):
                    pass  # peer gone: delivery attempted, like a real network

        # The client's bytes are read (and time-stamped) from the start.
        up_pump = asyncio.create_task(pump(creader, upq, UP))
        # A connection opened while the request path is delayed reaches the server late, as on a
        # real path where the SYN is delayed too (fix of 15/10). Connecting at once let a server
        # with a header timeout (Go ReadHeaderTimeout 2 s in evicon-plant and evicon-device) close
        # the idle connection before the delayed request arrived: a "delay" became a drop, and
        # the client saw the connection closed after 2 s (065 testbed run: Temporal P3/P9).
        # Likewise, a connection opened while the request path drops reaches the server only with
        # the first bytes that get through: a lost request never reaches the server (the client
        # waits for its own timeout instead of seeing the server's header timeout close it).
        with self._lock:
            mode, connect_delay = self.stats.up.mode, self.stats.up.delay_ms / 1000.0
        first = None
        if mode == DELAY and connect_delay:
            self._due(connect_delay)
            await asyncio.sleep(connect_delay)
        elif mode == DROP:
            first = await upq.get()
            if first[1] is None:  # the client gave up before anything got through
                cwriter.close()
                self._writers.discard(cwriter)
                return
            wait = first[0] - loop.time()
            if wait > 0:
                await asyncio.sleep(wait)
        try:
            if self.tls:
                ureader, uwriter = await asyncio.open_connection(*self.target, ssl=self._client_ssl(),
                                                                 server_hostname=self.tls.get("upstream_server_hostname") or self.target[0])
            else:
                ureader, uwriter = await asyncio.open_connection(*self.target)
        except OSError:
            up_pump.cancel()
            cwriter.close()
            self._writers.discard(cwriter)
            return
        self._writers.add(uwriter)
        tasks = [up_pump, asyncio.create_task(pump(ureader, downq, DOWN)),
                 asyncio.create_task(sender(upq, uwriter, True, first)), asyncio.create_task(sender(downq, cwriter, False))]
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            for w in (cwriter, uwriter):
                try:
                    w.close()
                except Exception:
                    pass
                self._writers.discard(w)


class _UDPSession(asyncio.DatagramProtocol):
    def __init__(self, proxy: FaultProxy, front, client_addr):
        self.proxy, self.front, self.client_addr = proxy, front, client_addr
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        deliver, d = self.proxy._classify(DOWN)
        if deliver:
            if d:
                self.proxy._due(d)
            loop = asyncio.get_running_loop()
            loop.call_later(d, lambda: self.front.transport.sendto(data, self.client_addr))

    def send(self, data):
        deliver, d = self.proxy._classify(UP)
        if deliver and d:
            self.proxy._due(d)
        if deliver and self.transport is not None:
            asyncio.get_running_loop().call_later(d, lambda: self.transport.sendto(data))

    def close(self):
        if self.transport is not None:
            self.transport.close()


class _UDPFront(asyncio.DatagramProtocol):
    def __init__(self, proxy: FaultProxy):
        self.proxy = proxy
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        sess = self.proxy._sessions.get(addr)
        if sess is None:
            asyncio.ensure_future(self._open(addr, data))
        else:
            sess.send(data)

    async def _open(self, addr, first):
        loop = asyncio.get_running_loop()
        if addr not in self.proxy._sessions:
            _, sess = await loop.create_datagram_endpoint(lambda: _UDPSession(self.proxy, self, addr),
                                                         remote_addr=self.proxy.target)
            self.proxy._sessions[addr] = sess
        self.proxy._sessions[addr].send(first)


class Toxiproxy(FaultController):
    """Toxiproxy HTTP API (https://github.com/Shopify/toxiproxy), TCP only.

    ``drop`` uses a ``timeout`` toxic with timeout=0 (data dropped until the
    toxic is removed); ``delay`` uses ``latency``. ``clear`` removes the
    probe's toxics only.
    """

    def __init__(self, api: str, proxy: str, listen: str = "", upstream: str = "", create: bool = False):
        self.api = api.rstrip("/")
        self.proxy = proxy
        if create:
            self._req("POST", "/proxies", {"name": proxy, "listen": listen, "upstream": upstream, "enabled": True}, ok=(200, 201, 409))

    def _req(self, method, path, body=None, ok=(200, 201, 204)):
        data = None if body is None else json.dumps(body).encode()
        r = urllib.request.Request(self.api + path, data=data, method=method, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(r, timeout=5) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            if e.code in ok:
                return None
            raise

    @staticmethod
    def _stream(direction: str) -> str:
        _check_dir(direction)
        return "upstream" if direction == UP else "downstream"

    def drop(self, direction: str) -> None:
        name = f"evicon_probe_drop_{direction}"
        self._req("POST", f"/proxies/{self.proxy}/toxics",
                  {"name": name, "type": "timeout", "stream": self._stream(direction), "toxicity": 1.0, "attributes": {"timeout": 0}})

    def delay(self, direction: str, ms: int) -> None:
        name = f"evicon_probe_delay_{direction}"
        self._req("POST", f"/proxies/{self.proxy}/toxics",
                  {"name": name, "type": "latency", "stream": self._stream(direction), "toxicity": 1.0, "attributes": {"latency": int(ms), "jitter": 0}})

    def clear(self) -> None:
        # Removing a latency toxic may flush data that is still waiting; the
        # late-delivery probes therefore keep the delay toxic until the
        # scheduled delivery time has passed and only then call clear().
        toxics = self._req("GET", f"/proxies/{self.proxy}/toxics") or []
        for t in toxics:
            if str(t.get("name", "")).startswith("evicon_probe_"):
                self._req("DELETE", f"/proxies/{self.proxy}/toxics/{t['name']}", ok=(204, 404))

    def describe(self) -> dict:
        return {"kind": "Toxiproxy", "api": self.api, "proxy": self.proxy}


class Netem(FaultController):
    """tc/netem on egress interfaces (root). ``up_iface`` carries the request
    path egress, ``down_iface`` the reply path egress (e.g. the veth inside
    ``evicon-edge-ns`` on VM-E). ``ssh`` runs the commands remotely.
    ``dry_run`` records commands without executing them.
    """

    def __init__(self, up_iface: str, down_iface: str, ssh: str = "", sudo: bool = True,
                 netns_up: str = "", netns_down: str = "", dry_run: bool = False):
        self.ifaces = {UP: up_iface, DOWN: down_iface}
        self.netns = {UP: netns_up, DOWN: netns_down}
        self.ssh, self.sudo, self.dry_run = ssh, sudo, dry_run
        self.history: list = []
        self._active = set()

    def _cmd(self, direction: str, args: list) -> list:
        base = ["tc"] + args
        if self.netns[direction]:
            base = ["ip", "netns", "exec", self.netns[direction]] + base
        if self.sudo:
            base = ["sudo", "-n"] + base
        if self.ssh:
            base = ["ssh", "-o", "BatchMode=yes", self.ssh, " ".join(shlex.quote(x) for x in base)]
        return base

    def _run(self, cmd: list, check: bool = True) -> None:
        self.history.append(cmd)
        if self.dry_run:
            return
        subprocess.run(cmd, check=check, capture_output=True, timeout=15)

    def drop(self, direction: str) -> None:
        _check_dir(direction)
        self._run(self._cmd(direction, ["qdisc", "replace", "dev", self.ifaces[direction], "root", "netem", "loss", "100%"]))
        self._active.add(direction)

    def delay(self, direction: str, ms: int) -> None:
        _check_dir(direction)
        # A large netem limit keeps delayed packets instead of tail-dropping.
        self._run(self._cmd(direction, ["qdisc", "replace", "dev", self.ifaces[direction], "root", "netem",
                                         "delay", f"{int(ms)}ms", "limit", "100000"]))
        self._active.add(direction)

    def clear(self) -> None:
        for d in DIRECTIONS:
            self._run(self._cmd(d, ["qdisc", "del", "dev", self.ifaces[d], "root"]), check=False)
        self._active.clear()

    def describe(self) -> dict:
        return {"kind": "Netem", "ifaces": self.ifaces, "netns": self.netns, "ssh": self.ssh}



def make_proxy_cert(directory, host: str = "127.0.0.1") -> dict:
    """A throwaway self-signed certificate for a TLS-terminating FaultProxy (release 068), made
    with the openssl CLI (present on every testbed VM). Returns {"cert_file", "key_file"}. The
    key protects nothing but the probe's own loopback hop; it is never copied into results."""
    import os
    import subprocess
    from pathlib import Path
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    cert, key = d / "proxy.crt", d / "proxy.key"
    if not (cert.is_file() and key.is_file()):
        san = f"IP:{host}" if host.replace(".", "").isdigit() else f"DNS:{host}"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes", "-days", "2",
                        "-subj", "/CN=evicon-fault-proxy", "-addext", f"subjectAltName={san}", "-keyout", str(key), "-out", str(cert)],
                       check=True, capture_output=True, timeout=60)
        os.chmod(key, 0o600)
    return {"cert_file": str(cert), "key_file": str(key)}


def from_config(cfg: dict, default_target: Optional[tuple] = None) -> FaultController:
    """Builds a controller from the ``faults`` block of a system config."""
    kind = (cfg or {}).get("kind", "none").lower()
    if kind == "none":
        return NoFaults()
    if kind == "proxy":
        host, port = cfg.get("target_host"), cfg.get("target_port")
        if (host is None or port is None) and default_target:
            host, port = default_target
        p = FaultProxy(host, int(port), proto=cfg.get("proto", "tcp"), listen_host=cfg.get("listen_host", "127.0.0.1"),
                       listen_port=int(cfg.get("listen_port", 0)), linger_s=float(cfg.get("linger_s", 3.0)), tls=cfg.get("tls"))
        p.start()
        return p
    if kind == "toxiproxy":
        return Toxiproxy(cfg["api"], cfg["proxy"], cfg.get("listen", ""), cfg.get("upstream", ""), bool(cfg.get("create", False)))
    if kind == "netem":
        return Netem(cfg["up_iface"], cfg["down_iface"], cfg.get("ssh", ""), bool(cfg.get("sudo", True)),
                     cfg.get("netns_up", ""), cfg.get("netns_down", ""), bool(cfg.get("dry_run", False)))
    raise ValueError(f"unknown fault controller kind {kind!r}")


def wait_until(deadline_monotonic: float) -> None:
    d = deadline_monotonic - time.monotonic()
    if d > 0:
        time.sleep(d)
