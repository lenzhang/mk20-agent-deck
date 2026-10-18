"""TCP transport for Protocol A over ECM (or adb-forward / pty bridge).

Drop into mk20deck/transport.py (or import alongside SerialTransport).
Device side: ttygs_tcp_bridge --listen 0.0.0.0:17890 --link /dev/ttyGS0
Mac: TcpTransport("192.168.7.2", 17890)  # after ECM link-up
"""
from __future__ import annotations

import socket
import time
from typing import Protocol


class Transport(Protocol):
    def write(self, data: bytes) -> None: ...
    def read(self, timeout: float) -> bytes: ...
    def close(self) -> None: ...


DEFAULT_ECM_HOST = "192.168.7.2"
DEFAULT_PORT = 17890


class TcpTransport:
    """Length-agnostic byte pipe over TCP (same framing as SerialTransport)."""

    def __init__(self, host: str = DEFAULT_ECM_HOST, port: int = DEFAULT_PORT, connect_timeout: float = 5.0) -> None:
        self.host = host
        self.port = port
        self._s = socket.create_connection((host, port), timeout=connect_timeout)
        self._s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._s.settimeout(0.0)  # non-blocking; read() uses select-style wait
        self._buf = bytearray()

    def write(self, data: bytes) -> None:
        self._s.settimeout(5.0)
        try:
            self._s.sendall(data)
        finally:
            self._s.settimeout(0.0)

    def read(self, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        out = bytearray()
        while True:
            try:
                chunk = self._s.recv(65536)
                if not chunk:
                    break
                out.extend(chunk)
                # drain quickly
                self._s.settimeout(0.0)
                while True:
                    try:
                        more = self._s.recv(65536)
                        if not more:
                            break
                        out.extend(more)
                    except (BlockingIOError, TimeoutError):
                        break
                if out:
                    return bytes(out)
            except (BlockingIOError, TimeoutError):
                pass
            if time.monotonic() >= deadline:
                return bytes(out)
            time.sleep(0.002)
        return bytes(out)

    def close(self) -> None:
        try:
            self._s.close()
        except Exception:
            pass


def open_transport(url: str):
    """Factory: serial:/dev/cu.xxx | tcp:192.168.7.2:17890 | tcp://host:port"""
    if url.startswith("tcp:"):
        rest = url[4:].lstrip("/")
        host, _, port_s = rest.partition(":")
        return TcpTransport(host or DEFAULT_ECM_HOST, int(port_s or DEFAULT_PORT))
    if url.startswith("serial:"):
        from serial import Serial  # type: ignore
        # thin wrapper matching SerialTransport ctor shape
        class _S:
            def __init__(self, port):
                self._s = Serial(port, 115200, timeout=0, write_timeout=5)
                self.port = port
            def write(self, data: bytes) -> None:
                self._s.write(data)
            def read(self, timeout: float) -> bytes:
                deadline = time.monotonic() + timeout
                while True:
                    n = self._s.in_waiting
                    if n:
                        return self._s.read(n)
                    if time.monotonic() >= deadline:
                        return b""
                    time.sleep(0.002)
            def close(self) -> None:
                self._s.close()
        return _S(url[7:])
    raise ValueError(f"unsupported transport url: {url}")
