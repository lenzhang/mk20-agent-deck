"""Byte transports: real CDC-ACM serial port and an in-process mock device."""
from __future__ import annotations

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


def find_ports() -> list[str]:
    """Return candidate CDC-ACM device paths by VID/PID (needs pyserial)."""
    try:
        from serial.tools import list_ports
    except ImportError:  # pragma: no cover
        return []
    out = []
    for p in list_ports.comports():
        if (p.vid, p.pid) in USB_IDS:
            out.append(p.device)
    # macOS exposes both /dev/tty.* and /dev/cu.*; prefer cu.* (no DCD wait).
    out.sort(key=lambda d: (0 if "/cu." in d else 1, d))
    return out


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
    """

    def __init__(self, layout: DeviceLayout | None = None, draw_delay: float = 0.0) -> None:
        self.layout = layout or DeviceLayout.default()
        self.draw_delay = draw_delay
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
        if method == "getInfo":
            ack = {"ack_method": "getInfo", "success": True, "result": self.layout.to_get_info()}
        elif method == "getFilesBySuffix":
            ack = {"ack_method": method, "success": True, "result": {"filePaths": ["/data/theme/a.jpg"]}}
        elif method in P.KNOWN_METHODS:
            ack = {"ack_method": method, "success": True, "result": {}}
        else:
            ack = {"ack_method": method, "success": False,
                   "errorString": f"Unknown method: {method}. Available methods: [{', '.join(P.KNOWN_METHODS)}]"}
        self._emit(P.encode_frame(P.CMD_JSON, P.json_bytes(ack), fr.frame_id))

    def emit_json(self, obj: dict) -> None:
        """Inject an arbitrary device JSON event (unknown knob formats etc.)."""
        self._emit(P.encode_frame(P.CMD_JSON, P.json_bytes(obj), self._ids.next()))

    def press(self, col: int, row: int, pressed: bool) -> None:
        """Inject a firmware-coordinate key event."""
        ev = {"method": "keyStateChanged", "parameters": {"col": col, "row": row, "pressed": pressed}}
        self._emit(P.encode_frame(P.CMD_JSON, P.json_bytes(ev), self._ids.next()))
