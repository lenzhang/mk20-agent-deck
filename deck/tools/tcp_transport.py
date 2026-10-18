"""TCP transport for Protocol A over ECM (or adb-forward / pty bridge).

Drop into mk20deck/transport.py (or import alongside SerialTransport).
Device side (firmware >= 1.1.0): ttygs_tcp_bridge --dev usb0 --listen 172.30.250.2:17890 --listen 169.254.7.2:17890
Mac: connect_device() -> 172.30.250.2:17890, falling back to the link-local rescue alias 169.254.7.2 forced out the
MK20 interface (found by its host MAC). Override with MK20_HOST=<ip> (and MK20_BRIDGE_PORT).
"""
from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
from typing import Optional, Protocol


class Transport(Protocol):
    def write(self, data: bytes) -> None: ...
    def read(self, timeout: float) -> bytes: ...
    def close(self) -> None: ...


DEFAULT_ECM_HOST = "172.30.250.2"   # firmware 1.1.0+ (was 10.20.0.2 in 1.0.x)
LINK_LOCAL_HOST = "169.254.7.2"     # rescue/discovery alias on usb0, all releases
DEFAULT_PORT = 17890
MK20_HOST_MAC = "02:11:22:33:44:56"
IP_BOUND_IF = 25                    # macOS <netinet/in.h>


def mk20_interface(mac: str = MK20_HOST_MAC) -> Optional[str]:
    """Name of the host interface whose hardware address is the MK20 host MAC (macOS/Linux ifconfig)."""
    try:
        out = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    cur = None
    for line in out.splitlines():
        m = re.match(r"^([A-Za-z0-9]+):", line)
        if m:
            cur = m.group(1)
        elif cur and re.search(r"\b(ether|HWaddr)\s+" + re.escape(mac.lower()), line.lower()):
            return cur
    return None


class TcpTransport:
    """Length-agnostic byte pipe over TCP (same framing as SerialTransport)."""

    def __init__(self, host: str = DEFAULT_ECM_HOST, port: int = DEFAULT_PORT, connect_timeout: float = 5.0,
                 bound_if: Optional[str] = None) -> None:
        self.host = host
        self.port = port
        if bound_if:
            # link-local: macOS routes 169.254/16 via the primary interface only, so pin the socket to the MK20 one
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if sys.platform == "darwin":
                s.setsockopt(socket.IPPROTO_IP, IP_BOUND_IF, socket.if_nametoindex(bound_if))
            elif hasattr(socket, "SO_BINDTODEVICE"):
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, bound_if.encode())
            s.settimeout(connect_timeout)
            try:
                s.connect((host, port))
            except OSError:
                s.close()
                raise
            self._s = s
        else:
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


def connect_device(port: Optional[int] = None, hosts: Optional[list] = None, connect_timeout: float = 3.0,
                   iface: Optional[str] = None) -> TcpTransport:
    """Primary ECM address first, then the link-local alias; every automatic candidate is pinned to the MK20
    interface (found by host MAC) so a TUN/overlapping route can never carry it to an unrelated host.
    The link-local path needs the Mac to hold an address the device routes back to (same ECM subnet); after a
    subnet mismatch run tools/mk20_host_net.py sync (adb over USB is unaffected)."""
    port = port or int(os.environ.get("MK20_BRIDGE_PORT", DEFAULT_PORT))
    if hosts is None:
        bif = iface or mk20_interface() or ""
        env = os.environ.get("MK20_HOST")
        if env:
            ll = env.startswith("169.254.")
            hosts = [(env, bif if ll else (bif or None))]   # link-local override must be bound
        else:
            hosts = [(DEFAULT_ECM_HOST, bif), (LINK_LOCAL_HOST, bif)]
    errs = []
    for host, bif in hosts:
        if bif == "":
            errs.append(f"{host}: MK20 interface (MAC {MK20_HOST_MAC}) not found")
            continue
        try:
            return TcpTransport(host, port, connect_timeout, bound_if=bif)
        except OSError as e:
            errs.append(f"{host}:{port}: {e}")
    raise ConnectionError("MK20 bridge unreachable: " + "; ".join(errs))


def open_transport(url: str):
    """Factory: mk20: (auto: primary, then link-local) | serial:/dev/cu.xxx | tcp:172.30.250.2:17890 | tcp://host:port"""
    if url in ("mk20", "mk20:", "auto"):
        return connect_device()
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
