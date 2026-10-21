"""Byte transports: real CDC-ACM serial port and an in-process mock device."""
from __future__ import annotations

import logging
import os
import re
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from typing import Protocol

from . import protocol as P
from .layout import DeviceLayout

# T113 USB gadget (CDC-ECM + CDC-ACM) and the alternate id accepted by the demo.
USB_IDS = ((0x1D6B, 0x0104), (0x1234, 0x5678))


class Transport(Protocol):
    def write(self, data: bytes) -> None: ...
    def read(self, timeout: float) -> bytes: ...
    def close(self) -> None: ...


# ECM+HID+ADB composite: KD is reachable through the on-device PTY<->TCP bridge (no ACM port then).
# MK20_TCP=host:port overrides, MK20_TCP=off disables. Firmware >= 1.1.0: device 172.30.250.2 / Mac 172.30.250.1;
# 1.0.x used 10.20.0.2 / 10.20.0.1; every release also answers on the link-local alias 169.254.7.2. Candidates are
# probed in order (all bound to the ECM interface), so the daemon follows an upgrade or a firmware rollback.
DEFAULT_TCP_CANDIDATES = ("172.30.250.2:17890", "10.20.0.2:17890", "169.254.7.2:17890")
DEFAULT_TCP = DEFAULT_TCP_CANDIDATES[0]
# Fixed host-side MAC of the device's ECM function (gadget host_addr) -> finds the ECM interface by MAC.
ECM_HOST_MAC = "02:11:22:33:44:56"
IP_BOUND_IF = 25  # macOS <netinet/in.h>; scoped routing: packets leave via this interface even if a TUN owns the route
log = logging.getLogger(__name__)
_route_warned: set[str] = set()


def ecm_interface() -> str | None:
    """Name of the interface carrying the MK20 ECM link (MK20_ECM_IF overrides; else matched by MAC)."""
    v = os.environ.get("MK20_ECM_IF")
    if v:
        return v
    mac = os.environ.get("MK20_ECM_MAC", ECM_HOST_MAC).lower()
    try:
        out = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=3).stdout
    except Exception:  # noqa: BLE001
        return None
    cur = None
    for line in out.splitlines():
        m = re.match(r"^([A-Za-z0-9]+):", line)
        if m:
            cur = m.group(1)
        elif cur and re.search(r"\bether " + re.escape(mac) + r"\b", line.lower()):
            return cur
    return None


def route_check(host: str) -> str | None:
    """Warn (once per host) if the OS route to the device goes through a TUN (utun*) instead of the ECM link.

    The daemon binds its socket to the ECM interface anyway, so it keeps working; other tools (ping, ssh, adb over
    TCP) would be captured. Never modifies proxy configs - it only prints the rule to add.
    """
    if sys.platform != "darwin":
        return None
    try:
        out = subprocess.run(["route", "-n", "get", host], capture_output=True, text=True, timeout=3).stdout
    except Exception:  # noqa: BLE001
        return None
    m = re.search(r"interface:\s*(\S+)", out)
    iface = m.group(1) if m else None
    if not iface or not iface.startswith("utun"):
        return None
    net = ".".join(host.split(".")[:3]) + ".0/24"
    msg = (f"route to MK20 {host} goes via {iface} (VPN/proxy TUN), not the ECM link. The deck daemon binds to the "
           f"ECM interface so it still works, but add a bypass for {net} to your TUN tool, e.g. "
           f"mihomo/Clash: tun.route-exclude-address: [{net}]; Surge: tun-excluded-routes = {net} (and skip-proxy); "
           f"sing-box: route_exclude_address: [\"{net}\"]; Tailscale/WireGuard: keep {net} out of AllowedIPs.")
    if host not in _route_warned:
        _route_warned.add(host)
        log.warning(msg)
    return msg


def _bound_socket(host: str, port: int, timeout: float) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    iface = ecm_interface() if sys.platform == "darwin" and not host.startswith("127.") and host != "localhost" else None
    if iface:
        try:
            s.setsockopt(socket.IPPROTO_IP, IP_BOUND_IF, socket.if_nametoindex(iface))
        except OSError:
            pass  # interface vanished (replug): fall back to the routing table
    try:
        s.connect((host, port))
    except BaseException:
        s.close()
        raise
    return s


def tcp_targets() -> list[str]:
    """MK20_TCP=host:port -> [that]; MK20_TCP=off -> []; default -> DEFAULT_TCP_CANDIDATES."""
    v = os.environ.get("MK20_TCP")
    if v is None:
        return list(DEFAULT_TCP_CANDIDATES)
    v = v.strip()
    return [] if v.lower() in ("", "off", "0", "no") else [v]


def tcp_target() -> str | None:
    """First configured candidate (compat); find_ports() probes all of them."""
    t = tcp_targets()
    return t[0] if t else None


def tcp_reachable(target: str, timeout: float = 0.5) -> bool:
    host, _, port = target.rpartition(":")
    try:
        _bound_socket(host, int(port), timeout).close()
    except OSError:
        return False
    route_check(host)
    return True


def find_ports() -> list[str]:
    """Return candidate ports: ``tcp://`` bridge first when the ECM link answers, then CDC-ACM by VID/PID."""
    out: list[str] = []
    iface_known = sys.platform != "darwin" or ecm_interface() is not None
    for t in tcp_targets():
        host = t.rpartition(":")[0]
        if not iface_known and host.startswith("169.254."):
            continue  # link-local is only meaningful bound to the ECM interface
        if tcp_reachable(t):
            out.append(f"tcp://{t}")
            break
    try:
        from serial.tools import list_ports
    except ImportError:  # pragma: no cover
        return out
    acm = []
    for p in list_ports.comports():
        if (p.vid, p.pid) in USB_IDS:
            acm.append(p.device)
    # macOS exposes both /dev/tty.* and /dev/cu.*; prefer cu.* (no DCD wait).
    acm.sort(key=lambda d: (0 if "/cu." in d else 1, d))
    return out + acm


class TcpTransport:
    """Protocol A over the device's PTY<->TCP bridge (one client at a time; a new client replaces the old)."""

    def __init__(self, target: str) -> None:
        host, _, port = target.removeprefix("tcp://").rpartition(":")
        self._s = _bound_socket(host, int(port), 3)  # bound to the ECM interface: TUN-proof
        self._s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        self.port = f"tcp://{host}:{port}"

    def write(self, data: bytes) -> None:
        self._s.settimeout(5)
        self._s.sendall(data)

    def read(self, timeout: float) -> bytes:
        self._s.settimeout(max(timeout, 0.001))
        try:
            d = self._s.recv(65536)
        except (socket.timeout, TimeoutError):
            return b""
        if not d:
            raise OSError(57, "bridge closed the connection")
        return d

    def close(self) -> None:
        try:
            self._s.close()
        except Exception:
            pass


def open_transport(port: str):
    """``tcp://host:port`` -> TcpTransport, anything else -> SerialTransport."""
    return TcpTransport(port) if port.startswith("tcp://") else SerialTransport(port)


class SerialTransport:
    """115200 8N1, no flow control (nominal; CDC-ACM is USB bulk and not rate limited)."""

    def __init__(self, port: str) -> None:
        import serial  # local import keeps tests dependency-light

        self._s = serial.Serial(port, 115200, bytesize=8, parity="N", stopbits=1, timeout=0, write_timeout=5)
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
        try:
            self._s.close()
        except Exception:
            pass


class MockDevice:
    """In-process MK20 stand-in speaking Protocol A.

    * answers ``getInfo`` with a layout (default guessed MK20 canvas),
    * answers other allowed JSON methods with ``success: true``,
    * acks each ``SHOW_JPG`` with an empty frame carrying the same cmd/id after
      ``draw_delay`` seconds, rejecting frames whose JPEG size != canvas,
    * lets tests/simulator inject ``keyStateChanged`` events.

    ``ui=True`` adds the mk20-kbd (fw >= 1.3) host-liveness methods of ``kbd/UI.md``: ``ui.hello`` answers
    ``version``/``heartbeat_ms``/``capabilities``/``generation``; ``ui.heartbeat`` is accepted only after a hello
    and until :meth:`lose`, its arrival times are recorded, and the device sample rules (gap < 1 period: not
    counted, > 1.5 periods: count restarts, 3 samples spanning >= 2 periods: ONLINE) run scaled to
    ``heartbeat_ms``.  Without ``ui`` the ``ui.*`` methods get "Unknown method" like stock KD.
    """

    UI_REJECT = "unavailable or invalid UI request"

    def __init__(self, layout: DeviceLayout | None = None, draw_delay: float = 0.0, ui: bool = False,
                 heartbeat_ms: int = 1000) -> None:
        self.layout = layout or DeviceLayout.default()
        self.draw_delay = draw_delay
        self.ui = ui
        self.heartbeat_ms = heartbeat_ms
        self.ui_generation = 0
        self.ui_negotiated = False
        self.ui_lost = False  # host declared lost: heartbeats rejected until the next hello
        self.ui_online = False
        self.hellos: list[float] = []  # accepted ui.hello arrival times (monotonic)
        self.heartbeats: list[float] = []  # accepted ui.heartbeat arrival times (monotonic)
        self.heartbeats_rejected = 0
        self.beat_restarts = 0  # probing count restarted by a gap > 1.5 periods
        self.beats_too_close = 0  # samples < 1 period after the last counted one (not counted)
        self._beats, self._first, self._last_beat = 0, 0.0, 0.0
        self._parser = P.FrameParser()
        self._out: deque[bytes] = deque()
        self._cv = threading.Condition()
        self.frames: list[bytes] = []  # received JPEGs
        self.json_requests: list[dict] = []
        self.rejected: list[str] = []
        self.closed = False
        self.unplugged = False  # read/write raise OSError like a vanished tty
        self.hung = False  # swallow everything (T113 stuck)
        self._ids = P.FrameIdCounter(1000)

    # --- Transport side -------------------------------------------------
    def write(self, data: bytes) -> None:
        if self.unplugged:
            raise OSError(6, "Device not configured")
        if self.hung:
            return
        for fr in self._parser.feed(data):
            self._handle(fr)

    def read(self, timeout: float) -> bytes:
        if self.unplugged:
            raise OSError(6, "Device not configured")
        with self._cv:
            if not self._out:
                self._cv.wait(timeout)
            if not self._out:
                return b""
            chunks = b"".join(self._out)
            self._out.clear()
            return chunks

    def close(self) -> None:
        self.closed = True

    # --- Device behaviour ------------------------------------------------
    def _emit(self, data: bytes) -> None:
        with self._cv:
            self._out.append(data)
            self._cv.notify_all()

    def _handle(self, fr: P.Frame) -> None:
        if fr.cmd == P.CMD_SHOW_JPG:
            from io import BytesIO

            from PIL import Image

            try:
                im = Image.open(BytesIO(fr.data))
                size = im.size
            except Exception:
                size = None
            if size != (self.layout.width, self.layout.height):
                self.rejected.append(f"Incorrect resolution {size}")
                self._emit(P.encode_frame(P.CMD_JSON, P.json_bytes(
                    {"errorString": f"Incorrect resolution, the device requires the resolution to be "
                                    f"{self.layout.width}x{self.layout.height}"}), 0xFFFFFFFF))
                return
            self.frames.append(fr.data)
            if self.draw_delay:
                time.sleep(self.draw_delay)
            self._emit(P.encode_frame(P.CMD_SHOW_JPG, b"", fr.frame_id))
            return
        obj = fr.json()
        if obj is None:
            return
        self.json_requests.append(obj)
        method = obj.get("method")
        if self.ui and isinstance(method, str) and method.startswith("ui."):
            res = self._ui(method, obj)
            ack = ({"ack_method": method, "success": False, "errorString": self.UI_REJECT} if res is None else
                   {"ack_method": method, "success": True, "result": res})
        elif method == "getInfo":
            if self.ui and self.ui_lost:  # legacy fresh handshake after loss: un-negotiates
                self.ui_negotiated = self.ui_lost = False
            ack = {"ack_method": "getInfo", "success": True, "result": self.layout.to_get_info()}
        elif method == "getFilesBySuffix":
            ack = {"ack_method": method, "success": True, "result": {"filePaths": ["/data/theme/a.jpg"]}}
        elif method in P.KNOWN_METHODS:
            ack = {"ack_method": method, "success": True, "result": {}}
        else:
            ack = {"ack_method": method, "success": False,
                   "errorString": f"Unknown method: {method}. Available methods: [{', '.join(P.KNOWN_METHODS)}]"}
        self._emit(P.encode_frame(P.CMD_JSON, P.json_bytes(ack), fr.frame_id))

    def _ui(self, method: str, obj: dict) -> dict | None:
        now = time.monotonic()
        if method == "ui.hello":
            v = obj.get("version")
            if type(v) is not int or v != 1:
                return None
            self.hellos.append(now)
            self.ui_negotiated, self.ui_lost, self.ui_online, self._beats = True, False, False, 0
            return {"version": 1, "heartbeat_ms": self.heartbeat_ms, "capabilities": ["ui.page", "ui.heartbeat"],
                    "generation": self.ui_generation}
        if method == "ui.heartbeat":
            if not self.ui_negotiated or self.ui_lost:
                self.heartbeats_rejected += 1
                return None
            self.heartbeats.append(now)
            self._sample(now)
            return {}
        return None  # ui.page etc.: not modelled

    def _sample(self, now: float) -> None:
        period = self.heartbeat_ms / 1000
        if not self._beats or now - self._last_beat > 1.5 * period:
            if self._beats:
                self.beat_restarts += 1
            self._beats, self._first = 1, now
        elif now - self._last_beat >= period:
            self._beats += 1
        else:
            self.beats_too_close += 1
            return
        self._last_beat = now
        if self._beats >= 3 and now - self._first >= 2 * period:
            self.ui_online = True

    def lose(self) -> None:
        """Device-side host loss (5 s silence / SUSPECT): reject heartbeats until the next ui.hello."""
        self.ui_lost, self.ui_online, self._beats = True, False, 0

    EVENT_ID = 0xFFFFFFFF  # KD / mk20-kbd: device-originated events carry this frame id

    def emit_json(self, obj: dict, frame_id: int | None = None) -> None:
        """Inject an arbitrary device JSON event (unknown knob formats etc.); ``frame_id=EVENT_ID`` as on hardware."""
        self._emit(P.encode_frame(P.CMD_JSON, P.json_bytes(obj), self._ids.next() if frame_id is None else frame_id))

    def press(self, col: int, row: int, pressed: bool) -> None:
        """Inject a firmware-coordinate key event."""
        ev = {"method": "keyStateChanged", "parameters": {"col": col, "row": row, "pressed": pressed}}
        self._emit(P.encode_frame(P.CMD_JSON, P.json_bytes(ev), self._ids.next()))
