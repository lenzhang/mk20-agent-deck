#!/usr/bin/env python3
"""Tests for TcpTransport — run: python3 test_tcp_transport.py"""
from __future__ import annotations

import socket
import threading
import time
import unittest

from tcp_transport import TcpTransport, open_transport


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


if __name__ == "__main__":
    unittest.main()
