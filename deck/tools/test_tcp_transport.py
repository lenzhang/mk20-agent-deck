#!/usr/bin/env python3
"""Tests for TcpTransport — run: python3 test_tcp_transport.py"""
from __future__ import annotations

import socket
import threading
import time
import unittest

from tcp_transport import TcpTransport, open_transport, connect_device, DEFAULT_ECM_HOST, mk20_interface


def _echo_server(port_holder: list):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port_holder.append(s.getsockname()[1])
    port_holder.append(s)
    conn, _ = s.accept()
    while True:
        data = conn.recv(65536)
        if not data:
            break
        conn.sendall(data)
    conn.close()
    s.close()


class TestTcp(unittest.TestCase):
    def test_echo_roundtrip(self):
        ports: list = []
        t = threading.Thread(target=_echo_server, args=(ports,), daemon=True)
        t.start()
        for _ in range(50):
            if ports:
                break
            time.sleep(0.01)
        port = ports[0]
        tr = TcpTransport("127.0.0.1", port)
        payload = b"\x01\x02hello-protocol-a" * 100
        tr.write(payload)
        got = b""
        deadline = time.monotonic() + 2
        while len(got) < len(payload) and time.monotonic() < deadline:
            got += tr.read(0.1)
        self.assertEqual(got, payload)
        tr.close()

    def test_open_transport_url(self):
        ports: list = []
        t = threading.Thread(target=_echo_server, args=(ports,), daemon=True)
        t.start()
        for _ in range(50):
            if ports:
                break
            time.sleep(0.01)
        tr = open_transport(f"tcp:127.0.0.1:{ports[0]}")
        tr.write(b"abc")
        self.assertEqual(tr.read(1.0), b"abc")
        tr.close()



class TestConnectDevice(unittest.TestCase):
    def test_default_is_new_subnet(self):
        self.assertEqual(DEFAULT_ECM_HOST, "172.30.250.2")

    def test_fallback_to_second_host(self):
        ports: list = []
        threading.Thread(target=_echo_server, args=(ports,), daemon=True).start()
        for _ in range(50):
            if ports:
                break
            time.sleep(0.01)
        # first candidate refuses (closed port), second answers
        dead = socket.socket(); dead.bind(("127.0.0.1", 0)); dport = dead.getsockname()[1]; dead.close()
        tr = connect_device(port=ports[0], hosts=[("127.0.0.2", None), ("127.0.0.1", None)], connect_timeout=1)
        tr.write(b"hi"); self.assertEqual(tr.read(1.0), b"hi"); tr.close()

    def test_missing_interface_reported(self):
        with self.assertRaises(ConnectionError) as cm:
            connect_device(port=1, hosts=[("169.254.7.2", "")], connect_timeout=0.2)
        self.assertIn("not found", str(cm.exception))


class TestBinding(unittest.TestCase):
    def setUp(self):
        import tcp_transport as t
        self.t = t
        self.seen = []
        self._orig = (t.TcpTransport, t.mk20_interface)
        test = self
        class Rec:
            def __init__(self, host, port, timeout, bound_if=None):
                test.seen.append((host, bound_if)); raise OSError("refused")
        t.TcpTransport = Rec

    def tearDown(self):
        self.t.TcpTransport, self.t.mk20_interface = self._orig
        import os
        os.environ.pop("MK20_HOST", None)

    def test_all_candidates_bound(self):
        self.t.mk20_interface = lambda mac=None: "en13"
        with self.assertRaises(ConnectionError):
            self.t.connect_device(port=1)
        self.assertEqual(self.seen, [("172.30.250.2", "en13"), ("169.254.7.2", "en13")])

    def test_no_interface_no_unbound_attempt(self):
        self.t.mk20_interface = lambda mac=None: None
        with self.assertRaises(ConnectionError) as cm:
            self.t.connect_device(port=1)
        self.assertEqual(self.seen, [])
        self.assertIn("not found", str(cm.exception))

    def test_link_local_override_bound(self):
        import os
        self.t.mk20_interface = lambda mac=None: None
        os.environ["MK20_HOST"] = "169.254.7.2"
        with self.assertRaises(ConnectionError):
            self.t.connect_device(port=1)
        self.assertEqual(self.seen, [])
        os.environ["MK20_HOST"] = "10.1.2.3"   # explicit routed override may go unbound
        with self.assertRaises(ConnectionError):
            self.t.connect_device(port=1)
        self.assertEqual(self.seen, [("10.1.2.3", None)])

if __name__ == "__main__":
    unittest.main()
