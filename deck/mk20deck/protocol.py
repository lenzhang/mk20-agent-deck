"""Protocol A: PC <-> T113 over USB CDC-ACM.

Derived from the vendor reference ``OpenSourceLicenseDemo_V1.0/mainwindow.cpp``
(``packet_cmd_ack`` / ``parse_cmd``) and ``CRC32.cpp``.

Frame layout (all integers little-endian uint32)::

    A1 A5 5A 5E | id | cmd | size | crc32(size as 4 LE bytes) | data[size] | crc32(data)

CRC is the standard reflected CRC-32 (poly 0xEDB88320, init/xorout 0xFFFFFFFF),
i.e. identical to ``zlib.crc32``.

Commands: ``SHOW_JPG = 100`` (whole-canvas JPEG; the device answers with a frame
carrying the same cmd + id when the frame has been drawn), ``JSON = 101``
(``{"method": ..., "parameters": {...}}`` requests, ``{"ack_method": ...,
"success": bool, "result"|"errorString": ...}`` replies, and device events such as
``{"method": "keyStateChanged", "parameters": {"col", "row", "pressed"}}``).
"""
from __future__ import annotations

import json
import struct
import zlib
from dataclasses import dataclass, field
from typing import Any, Iterator

HEADER = bytes.fromhex("A1A55A5E")
HEAD_LEN = len(HEADER) + 4 * 4  # header + id + cmd + size + size_crc = 20

CMD_SHOW_JPG = 100
CMD_JSON = 101

# Upper bound for a single payload; guards against garbage sizes that happen to
# pass the size CRC.  A full 640x656 JPEG at q=100 is well below 1 MiB.
MAX_PAYLOAD = 8 * 1024 * 1024

# JSON methods seen in the device firmware string table (KeyboardDevice V2.31).
KNOWN_METHODS = (
    "getInfo", "setBacklight", "setVolume", "saveToFile", "setFileCRC",
    "deleteFiles", "getFilesBySuffix", "playAudio", "stopAudio", "keyboardInput",
)

# Methods the deck is allowed to send.  ``keyboardInput`` types text on the host
# through the device HID and therefore violates the P0 "no side-effect input"
# rule (PRD §4/§6.5, G20); file management methods are not needed in P0.
ALLOWED_METHODS = frozenset({"getInfo", "setBacklight", "setVolume", "playAudio", "stopAudio",
                             "getFilesBySuffix",  # read-only listing (devtest inventory)
                             "ui.hello", "ui.heartbeat",  # mk20-kbd (fw >= 1.3) host liveness, no content
                             # mk20-kbd (fw 1.5.1) L1 option mode: which slot keys type a digit when the USER presses
                             # them; the deck itself never types (unlike keyboardInput), kbd/UI.md "ui.hidKeys"
                             "ui.hidKeys"})
# Methods that change device state (need explicit opt-in in tools); getInfo/getFilesBySuffix are reads.
WRITE_METHODS = frozenset({"setBacklight", "setVolume", "playAudio", "stopAudio"})


def crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def encode_frame(cmd: int, data: bytes, frame_id: int) -> bytes:
    size = len(data)
    size_bytes = struct.pack("<I", size)
    return b"".join((
        HEADER,
        struct.pack("<III", frame_id & 0xFFFFFFFF, cmd & 0xFFFFFFFF, size),
        struct.pack("<I", crc32(size_bytes)),
        data,
        struct.pack("<I", crc32(data)),
    ))


@dataclass(frozen=True)
class Frame:
    frame_id: int
    cmd: int
    data: bytes

    def json(self) -> dict[str, Any] | None:
        if self.cmd != CMD_JSON:
            return None
        try:
            obj = json.loads(self.data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return obj if isinstance(obj, dict) else None


@dataclass
class FrameParser:
    """Incremental stream parser with resynchronisation.

    Mirrors ``MainWindow::parse_cmd``: scan for the header, drop garbage before it,
    validate the size CRC (on mismatch skip past the bogus header and rescan),
    wait for the full payload, then deliver the frame only if the data CRC matches.
    """

    buffer: bytearray = field(default_factory=bytearray)
    dropped_bytes: int = 0
    bad_size_crc: int = 0
    bad_data_crc: int = 0

    def feed(self, chunk: bytes) -> list[Frame]:
        self.buffer.extend(chunk)
        return list(self._drain())

    def _drain(self) -> Iterator[Frame]:
        buf = self.buffer
        while len(buf) >= HEAD_LEN:
            idx = buf.find(HEADER)
            if idx < 0:
                # Keep a possible partial header at the tail.
                keep = len(HEADER) - 1
                self.dropped_bytes += len(buf) - keep
                del buf[: len(buf) - keep]
                return
            if idx > 0:
                self.dropped_bytes += idx
                del buf[:idx]
                if len(buf) < HEAD_LEN:
                    return
            frame_id, cmd, size, size_crc = struct.unpack_from("<IIII", buf, len(HEADER))
            if size_crc != crc32(struct.pack("<I", size)) or size > MAX_PAYLOAD:
                self.bad_size_crc += 1
                del buf[:1]  # skip this header byte and rescan
                continue
            total = HEAD_LEN + size + 4
            if len(buf) < total:
                return
            data = bytes(buf[HEAD_LEN: HEAD_LEN + size])
            (data_crc,) = struct.unpack_from("<I", buf, HEAD_LEN + size)
            del buf[:total]
            if data_crc != crc32(data):
                self.bad_data_crc += 1
                continue
            yield Frame(frame_id, cmd, data)


class FrameIdCounter:
    """Monotonic uint32 id generator (the reference uses a static counter starting at 1)."""

    def __init__(self, start: int = 1) -> None:
        self._next = start

    def next(self) -> int:
        v = self._next
        self._next = (self._next + 1) & 0xFFFFFFFF or 1
        return v


def json_bytes(obj: dict[str, Any]) -> bytes:
    """Compact UTF-8 JSON (QJsonDocument::Compact equivalent)."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def request(method: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
    if method not in ALLOWED_METHODS:
        raise PermissionError(f"method {method!r} is not allowed by the deck (side effects / not P0)")
    obj: dict[str, Any] = {"method": method}
    if parameters is not None:
        obj["parameters"] = parameters
    return obj


def get_info() -> dict[str, Any]:
    return request("getInfo")


def set_backlight(level: int) -> dict[str, Any]:
    return request("setBacklight", {"level": int(max(0, min(100, level)))})


def set_volume(level: int) -> dict[str, Any]:
    # KeyboardDevice V2.31/V2.32: "Volume level must be between 0 and 7"
    return request("setVolume", {"level": int(max(0, min(7, level)))})


def get_files_by_suffix(suffixes: list[str]) -> dict[str, Any]:
    return request("getFilesBySuffix", {"suffixs": list(suffixes)})  # sic: firmware spelling


def unknown_method_probe() -> dict[str, Any]:
    """Deliberately unknown method: firmware answers 'Unknown method: … Available methods: […]'.
    Built by hand (bypasses the allow-list) because it cannot have side effects."""
    return {"method": "mk20deckProbe"}


def ui_hello() -> dict[str, Any]:
    """mk20-kbd host handshake (kbd/UI.md): flat JSON, no "parameters" object."""
    return {**request("ui.hello"), "version": 1}


def ui_heartbeat() -> dict[str, Any]:
    """mk20-kbd host liveness sample; keeps the host ONLINE on every device page."""
    return request("ui.heartbeat")


# HID usages (page 0x07) the firmware reports in an option-mode press ("hid" in keyStateChanged parameters).
HID_DIGIT = {d: 0x1D + d for d in range(1, 10)}  # 1 -> 0x1E ... 9 -> 0x26
HID_RETURN, HID_ESCAPE, HID_BACKSPACE = 0x28, 0x29, 0x2A
HID_KEYS_MAX = 9  # firmware limit: at most nine of fb1..fb15


def ui_hid_keys(keys: dict[int, int], generation: int) -> dict[str, Any]:
    """mk20-kbd L1 option mode (kbd/UI.md): ``keys`` maps logical fb 1..15 -> digit 1..9 (``{}`` clears).
    Validated like the firmware so a bad map never leaves the host: <= 9 entries, no digit twice."""
    if not 1 <= int(generation) <= 999_999_999:
        raise ValueError("generation out of range")
    if len(keys) > HID_KEYS_MAX or len(set(keys.values())) != len(keys):
        raise ValueError("at most nine keys, each digit once")
    out: dict[str, str] = {}
    for fb, d in sorted(keys.items()):
        if isinstance(fb, bool) or isinstance(d, bool) or not 1 <= int(fb) <= 15 or not 1 <= int(d) <= 9:
            raise ValueError(f"bad option key {fb!r}: {d!r}")
        out[str(int(fb))] = str(int(d))
    return {**request("ui.hidKeys"), "generation": int(generation), "keys": out}


def play_audio(file_path: str) -> dict[str, Any]:
    return request("playAudio", {"filePath": file_path})


@dataclass(frozen=True)
class KeyEvent:
    """Raw ``keyStateChanged`` event; ``col``/``row`` are as reported by firmware.

    Firmware strings also show a keycode form (``keyCodeH``/``keyCodeL``, plus a
    ``requestId``); then col/row are -1 and ``keycode`` is set.  Mapped through
    the ``[keycodes]`` config table measured by devtest.
    """

    col: int
    row: int
    pressed: bool
    keycode: int | None = None
    request_id: Any = None
    hid: int | None = None  # option mode: HID usage the keyboard itself put down for this press (kbd/UI.md)


@dataclass(frozen=True)
class Ack:
    method: str
    success: bool
    result: dict[str, Any]
    error: str | None


def classify_json(obj: dict[str, Any]) -> KeyEvent | Ack | dict[str, Any]:
    if "ack_method" in obj:
        res = obj.get("result")
        return Ack(
            method=str(obj.get("ack_method")),
            success=bool(obj.get("success")),
            result=res if isinstance(res, dict) else {},
            error=obj.get("errorString"),
        )
    if obj.get("method") == "keyStateChanged":
        p = obj.get("parameters") or obj
        rid = p.get("requestId", obj.get("requestId"))
        try:
            if "col" in p and "row" in p:
                kc = None
                if "keyCodeH" in p:
                    kc = (int(p["keyCodeH"]) << 8) | int(p.get("keyCodeL", 0))
                hid = p.get("hid")
                hid = hid if type(hid) is int and 0x04 <= hid <= 0x65 else None  # anything else: not trusted
                return KeyEvent(int(p["col"]), int(p["row"]), bool(p.get("pressed")), kc, rid, hid)
            if "keyCodeH" in p or "keyCodeL" in p:
                kc = (int(p.get("keyCodeH", 0)) << 8) | int(p.get("keyCodeL", 0))
                return KeyEvent(-1, -1, bool(p.get("pressed")), kc, rid)
        except (TypeError, ValueError):
            return obj
        return obj
    return obj
