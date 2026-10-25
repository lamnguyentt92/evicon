import socket
import socketserver
import threading
import time
import unittest

from evicon_probe.faults import DOWN, UP, FaultProxy, Netem


class _Echo(socketserver.BaseRequestHandler):
    def handle(self):
        while True:
            data = self.request.recv(4096)
            if not data:
                return
            _Echo.seen.append((time.monotonic(), data))
            self.request.sendall(data.upper())


_Echo.seen = []


class _UDPEcho(socketserver.BaseRequestHandler):
    def handle(self):
        data, sock = self.request
        _UDPEcho.seen.append(data)
        sock.sendto(data.upper(), self.client_address)


_UDPEcho.seen = []


class FaultProxyTCPTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        socketserver.ThreadingTCPServer.allow_reuse_address = True
        cls.srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Echo)
        cls.srv.daemon_threads = True
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def roundtrip(self, addr, payload=b"ping", timeout=1.0):
        s = socket.create_connection(addr, timeout=timeout)
        try:
            s.sendall(payload)
            return s.recv(100)
        finally:
            s.close()

    def test_pass_drop_delay(self):
        with FaultProxy(*self.srv.server_address, proto="tcp", linger_s=0.5) as p:
            self.assertEqual(self.roundtrip(p.address), b"PING")
            p.drop(DOWN)
            _Echo.seen.clear()
            with self.assertRaises(socket.timeout):
                self.roundtrip(p.address, b"lost-reply", 0.5)
            time.sleep(0.1)
            self.assertTrue(any(d == b"lost-reply" for _, d in _Echo.seen), "request must reach server when only the reply is dropped")
            p.clear()
            p.drop(UP)
            _Echo.seen.clear()
            with self.assertRaises(socket.timeout):
                self.roundtrip(p.address, b"lost-req", 0.5)
            time.sleep(0.2)
            self.assertFalse(_Echo.seen, "dropped request must not reach server")
            p.clear()
            p.delay(UP, 800)
            _Echo.seen.clear()
            t0 = time.monotonic()
            with self.assertRaises(socket.timeout):
                self.roundtrip(p.address, b"late", 0.3)  # client gives up
            p.clear()  # already-scheduled bytes keep their schedule
            time.sleep(1.0)
            self.assertTrue(_Echo.seen, "delayed request must still be delivered after the client gave up")
            self.assertGreaterEqual(_Echo.seen[0][0] - t0, 0.7)
            self.assertEqual(p.stats.up.dropped, 1)
            self.assertGreaterEqual(p.stats.up.delayed, 1)

    def test_quiesce_waits_for_delayed_bytes(self):
        # release 065: a chunk delayed in a window holds the ordered stream after
        # clear(); quiesce() waits until it is due, so the next probe starts clean
        with FaultProxy(*self.srv.server_address, proto="tcp", linger_s=0.5) as p:
            self.assertEqual(p.quiesce(), 0.0)
            p.delay(UP, 700)
            s = socket.create_connection(p.address, timeout=2)
            try:
                s.sendall(b"held")
                time.sleep(0.1)
                p.clear()
                t0 = time.monotonic()
                waited = p.quiesce()
                self.assertGreater(waited, 0.4)
                self.assertGreaterEqual(time.monotonic() - t0, 0.5)
                self.assertEqual(s.recv(100), b"HELD")
                self.assertEqual(p.quiesce(), 0.0)
            finally:
                s.close()


class FaultProxyUDPTest(unittest.TestCase):
    def test_udp_drop_and_pass(self):
        srv = socketserver.ThreadingUDPServer(("127.0.0.1", 0), _UDPEcho)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with FaultProxy(*srv.server_address, proto="udp") as p:
                c = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                c.settimeout(0.5)
                c.sendto(b"a", p.address)
                self.assertEqual(c.recvfrom(10)[0], b"A")
                p.drop(UP)
                _UDPEcho.seen.clear()
                c.sendto(b"b", p.address)
                with self.assertRaises(socket.timeout):
                    c.recvfrom(10)
                self.assertFalse(_UDPEcho.seen)
                p.clear()
                p.drop(DOWN)
                c.sendto(b"c", p.address)
                with self.assertRaises(socket.timeout):
                    c.recvfrom(10)
                self.assertEqual(_UDPEcho.seen, [b"c"])
                c.close()
        finally:
            srv.shutdown()
            srv.server_close()


class NetemDryRunTest(unittest.TestCase):
    def test_commands(self):
        n = Netem("veth-up", "veth-down", ssh="root@192.168.107.154", netns_up="evicon-edge-ns", dry_run=True)
        n.drop(UP)
        n.delay(DOWN, 1500)
        n.clear()
        flat = [" ".join(c) for c in n.history]
        self.assertIn("ip netns exec evicon-edge-ns tc qdisc replace dev veth-up root netem loss 100%", flat[0])
        self.assertTrue(flat[0].startswith("ssh -o BatchMode=yes root@192.168.107.154"))
        self.assertIn("delay 1500ms", flat[1])
        self.assertEqual(len(n.history), 4)


if __name__ == "__main__":
    unittest.main()
