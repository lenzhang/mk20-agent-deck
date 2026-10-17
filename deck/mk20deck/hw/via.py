"""VIA raw-HID client for the MK20's GD32 (QMK ``4250:426f``).

Read-only by default.  Wire format: 32-byte reports on usage page 0xFF60 / usage
0x61; byte 0 = command id, the keyboard echoes the request with data filled in,
or answers ``0xFF`` (id_unhandled).

Backups dump the *raw* dynamic-keymap EEPROM buffer (``get_buffer``) for a
generous size plus per-cell keycodes, encoders and the macro buffer.  QMK ignores
out-of-range offsets on read (zeros) and on write, so a raw buffer restore is
safe even if the matrix size guess is wrong; restore nevertheless writes only
cells that differ, via ``set_keycode`` / ``set_encoder``, and verifies by read-back.

Destructive commands (dynamic_keymap_reset 0x06, eeprom_reset 0x0A,
bootloader_jump 0x0B, macro_reset 0x10, custom_save 0x09) are never sent.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import struct
import time
from dataclasses import dataclass
from typing import Protocol

VIA_VID, VIA_PID = 0x4250, 0x426F
USAGE_PAGE, USAGE = 0xFF60, 0x61
REPORT = 32

GET_PROTOCOL_VERSION = 0x01
GET_KEYBOARD_VALUE = 0x02
GET_KEYCODE = 0x04
SET_KEYCODE = 0x05
MACRO_GET_COUNT = 0x0C
MACRO_GET_BUFFER_SIZE = 0x0D
MACRO_GET_BUFFER = 0x0E
MACRO_SET_BUFFER = 0x0F
GET_LAYER_COUNT = 0x11
GET_BUFFER = 0x12
GET_ENCODER = 0x14
SET_ENCODER = 0x15
UNHANDLED = 0xFF
KV_UPTIME, KV_LAYOUT_OPTIONS, KV_FIRMWARE_VERSION = 0x01, 0x02, 0x04

READ_ONLY_CMDS = frozenset({GET_PROTOCOL_VERSION, GET_KEYBOARD_VALUE, GET_KEYCODE, MACRO_GET_COUNT,
                            MACRO_GET_BUFFER_SIZE, MACRO_GET_BUFFER, GET_LAYER_COUNT, GET_BUFFER, GET_ENCODER})
WRITE_CMDS = frozenset({SET_KEYCODE, SET_ENCODER, MACRO_SET_BUFFER})
NEVER_CMDS = frozenset({0x06, 0x09, 0x0A, 0x0B, 0x10})  # reset / save-all / eeprom reset / bootloader / macro reset
CHUNK = 28  # max payload bytes per get_buffer/set_buffer report

KC_NO, KC_TRNS, KC_RALT = 0x0000, 0x0001, 0x00E6

_BASIC = {0: "KC_NO", 1: "KC_TRNS", 0x28: "KC_ENT", 0x29: "KC_ESC", 0x2A: "KC_BSPC", 0x2B: "KC_TAB",
          0x2C: "KC_SPC", 0x7F: "KC_MUTE", 0x80: "KC_VOLU", 0x81: "KC_VOLD",
          0xA8: "KC_MUTE", 0xA9: "KC_VOLU", 0xAA: "KC_VOLD", 0xAE: "KC_MPLY",
          0xE0: "KC_LCTL", 0xE1: "KC_LSFT", 0xE2: "KC_LALT", 0xE3: "KC_LGUI",
          0xE4: "KC_RCTL", 0xE5: "KC_RSFT", 0xE6: "KC_RALT", 0xE7: "KC_RGUI"}
for _i in range(26):
    _BASIC[0x04 + _i] = "KC_" + chr(ord("A") + _i)
for _i, _d in enumerate("1234567890"):
    _BASIC[0x1E + _i] = "KC_" + _d
for _i in range(12):
    _BASIC[0x3A + _i] = f"KC_F{_i + 1}"
for _i in range(12):
    _BASIC[0x68 + _i] = f"KC_F{_i + 13}"


def keycode_name(kc: int) -> str:
    """Best-effort QMK name (basic range; others as hex — VIA ids differ by protocol version)."""
    return _BASIC.get(kc, f"0x{kc:04X}")


def has_side_effect(kc: int) -> bool:
    """True unless the code is KC_NO / KC_TRNS (used for the P0 HID-sanitisation audit)."""
    return kc not in (KC_NO, KC_TRNS)


class ViaError(RuntimeError):
    pass


class HidLink(Protocol):
    def write(self, report: bytes) -> None: ...
    def read(self, timeout_ms: int) -> bytes: ...
    def close(self) -> None: ...


def find_via_devices(hid_module=None) -> list[dict]:
    """Enumerate VIA raw-HID interfaces (needs the ``hid`` module from ``pip install hidapi``)."""
    if hid_module is None:
        try:
            import hid as hid_module  # type: ignore
        except ImportError as e:
            raise ViaError("python module 'hid' missing — pip install hidapi") from e
    out = []
    for d in hid_module.enumerate(VIA_VID, VIA_PID):
        if d.get("usage_page") == USAGE_PAGE and d.get("usage") == USAGE:
            out.append(d)
    return out


class HidapiLink:
    """hidapi transport.  Vendor usage pages do not need macOS Input Monitoring."""

    def __init__(self, path: bytes, hid_module=None) -> None:
        if hid_module is None:
            import hid as hid_module  # type: ignore
        self._d = hid_module.device()
        self._d.open_path(path)

    def write(self, report: bytes) -> None:
        self._d.write(b"\x00" + report)  # report id 0

    def read(self, timeout_ms: int) -> bytes:
        return bytes(self._d.read(REPORT, timeout_ms))

    def close(self) -> None:
        try:
            self._d.close()
        except Exception:  # noqa: BLE001
            pass


class ViaClient:
    def __init__(self, link: HidLink, allow_writes: bool = False, timeout_ms: int = 500) -> None:
        self.link = link
        self.allow_writes = allow_writes
        self.timeout_ms = timeout_ms
        self.calls = 0

    def _cmd(self, cmd: int, *payload: int) -> bytes:
        if cmd in NEVER_CMDS:
            raise PermissionError(f"VIA command 0x{cmd:02X} is destructive and never sent by mk20deck")
        if cmd in WRITE_CMDS and not self.allow_writes:
            raise PermissionError(f"VIA write 0x{cmd:02X} needs explicit --yes-write-keymap")
        if cmd not in READ_ONLY_CMDS | WRITE_CMDS:
            raise PermissionError(f"VIA command 0x{cmd:02X} not in allow-list")
        req = bytes([cmd, *payload]).ljust(REPORT, b"\x00")
        self.link.write(req)
        self.calls += 1
        deadline = time.monotonic() + self.timeout_ms / 1000
        while True:
            left = int((deadline - time.monotonic()) * 1000)
            if left <= 0:
                raise ViaError(f"timeout waiting for VIA reply to 0x{cmd:02X}")
            resp = self.link.read(left)
            if not resp:
                continue
            if resp[0] == UNHANDLED:
                raise ViaError(f"VIA command 0x{cmd:02X} unhandled by firmware")
            if resp[0] == cmd:
                return resp

    # ----------------------------------------------------------- reads
    def protocol_version(self) -> int:
        r = self._cmd(GET_PROTOCOL_VERSION)
        return (r[1] << 8) | r[2]

    def firmware_version(self) -> int | None:
        try:
            r = self._cmd(GET_KEYBOARD_VALUE, KV_FIRMWARE_VERSION)
        except ViaError:
            return None
        return struct.unpack(">I", r[2:6])[0]

    def layout_options(self) -> int | None:
        try:
            r = self._cmd(GET_KEYBOARD_VALUE, KV_LAYOUT_OPTIONS)
        except ViaError:
            return None
        return struct.unpack(">I", r[2:6])[0]

    def layer_count(self) -> int:
        return self._cmd(GET_LAYER_COUNT)[1]

    def keycode(self, layer: int, row: int, col: int) -> int:
        r = self._cmd(GET_KEYCODE, layer, row, col)
        return (r[4] << 8) | r[5]

    def encoder(self, layer: int, idx: int, clockwise: bool) -> int:
        r = self._cmd(GET_ENCODER, layer, idx, 1 if clockwise else 0)
        return (r[4] << 8) | r[5]

    def get_buffer(self, offset: int, size: int) -> bytes:
        out = bytearray()
        while size > 0:
            n = min(CHUNK, size)
            r = self._cmd(GET_BUFFER, (offset >> 8) & 0xFF, offset & 0xFF, n)
            out += r[4:4 + n]
            offset += n
            size -= n
        return bytes(out)

    def macro_info(self) -> tuple[int, int]:
        cnt = self._cmd(MACRO_GET_COUNT)[1]
        r = self._cmd(MACRO_GET_BUFFER_SIZE)
        return cnt, (r[1] << 8) | r[2]

    def macro_buffer(self, size: int) -> bytes:
        out = bytearray()
        off = 0
        while off < size:
            n = min(CHUNK, size - off)
            r = self._cmd(MACRO_GET_BUFFER, (off >> 8) & 0xFF, off & 0xFF, n)
            out += r[4:4 + n]
            off += n
        return bytes(out)

    # ----------------------------------------------------------- writes
    def set_keycode(self, layer: int, row: int, col: int, kc: int) -> None:
        self._cmd(SET_KEYCODE, layer, row, col, (kc >> 8) & 0xFF, kc & 0xFF)

    def set_encoder(self, layer: int, idx: int, clockwise: bool, kc: int) -> None:
        self._cmd(SET_ENCODER, layer, idx, 1 if clockwise else 0, (kc >> 8) & 0xFF, kc & 0xFF)

    def set_macro_buffer(self, data: bytes) -> None:
        off = 0
        while off < len(data):
            chunk = data[off:off + CHUNK]
            self._cmd(MACRO_SET_BUFFER, (off >> 8) & 0xFF, off & 0xFF, len(chunk), *chunk)
            off += len(chunk)


# ------------------------------------------------------------------ matrix detection
def detect_matrix(client: ViaClient, raw: bytes, max_rows: int = 8, max_cols: int = 24,
                  min_keys: int = 20, max_keys: int = 64) -> tuple[int, int] | None:
    """Find (rows, cols) such that get_keycode(0, r, c) == raw[(r*cols+c)*2] for every cell.

    QMK's get_keycode returns KC_NO outside the matrix while the raw buffer
    continues with the next row, so wrong sizes disagree unless the keymap is
    mostly KC_NO.  Returns None when ambiguous (caller then needs --rows/--cols).
    """
    cache: dict[tuple[int, int], int] = {}

    def kc(r: int, c: int) -> int:
        if (r, c) not in cache:
            cache[(r, c)] = client.keycode(0, r, c)
        return cache[(r, c)]

    def raw_kc(i: int) -> int:
        return (raw[2 * i] << 8) | raw[2 * i + 1] if 2 * i + 1 < len(raw) else 0

    hits = []
    for rows in range(1, max_rows + 1):
        for cols in range(1, max_cols + 1):
            n = rows * cols
            if not (min_keys <= n <= max_keys):
                continue
            ok = True
            for r in range(rows):
                for c in range(cols):
                    if kc(r, c) != raw_kc(r * cols + c):
                        ok = False
                        break
                if not ok:
                    break
            # outside the matrix must read KC_NO
            if ok and cols < max_cols and kc(0, cols) != KC_NO:
                ok = False
            if ok and rows < max_rows and kc(rows, 0) != KC_NO:
                ok = False
            if ok:
                hits.append((rows, cols))
    # prefer the smallest matrix that is consistent and has a nonzero cell in its last row
    hits = [h for h in hits if any(kc(h[0] - 1, c) for c in range(h[1]))
            and any(kc(r, h[1] - 1) for r in range(h[0]))]
    return hits[0] if len(hits) == 1 else None


# ------------------------------------------------------------------ backup / restore
@dataclass
class Backup:
    data: dict

    @property
    def rows(self) -> int:
        return self.data["matrix"]["rows"]

    @property
    def cols(self) -> int:
        return self.data["matrix"]["cols"]

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=1)

    @classmethod
    def load(cls, path: str) -> "Backup":
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        if d.get("format") != "mk20deck-via-backup/1":
            raise ViaError("not an mk20deck VIA backup")
        raw = bytes.fromhex(d["raw_keymap_hex"])
        if hashlib.sha256(raw).hexdigest() != d["raw_keymap_sha256"]:
            raise ViaError("backup raw keymap checksum mismatch")
        return cls(d)


def backup(client: ViaClient, rows: int | None = None, cols: int | None = None, encoders: int = 2,
           raw_bytes_per_layer: int = 256, device: dict | None = None) -> Backup:
    proto = client.protocol_version()
    layers = client.layer_count()
    raw = client.get_buffer(0, layers * raw_bytes_per_layer)
    detected = None
    if rows is None or cols is None:
        detected = detect_matrix(client, raw)
        if detected is None:
            raise ViaError("cannot detect matrix size; pass --rows/--cols")
        rows, cols = detected
    keymap = [[[client.keycode(l, r, c) for c in range(cols)] for r in range(rows)] for l in range(layers)]
    mismatch = [(l, r, c) for l in range(layers) for r in range(rows) for c in range(cols)
                if keymap[l][r][c] != int.from_bytes(raw[((l * rows + r) * cols + c) * 2:][:2], "big")]
    enc = []
    for l in range(layers):
        row = []
        for i in range(encoders):
            try:
                row.append({"ccw": client.encoder(l, i, False), "cw": client.encoder(l, i, True)})
            except ViaError as e:  # encoder map unsupported (VIA < 10)
                row.append({"error": str(e)})
        enc.append(row)
    try:
        mcount, msize = client.macro_info()
        macros = client.macro_buffer(msize)
    except ViaError:
        mcount, msize, macros = 0, 0, b""
    d = {
        "format": "mk20deck-via-backup/1",
        "created": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "device": device or {"vid": f"0x{VIA_VID:04x}", "pid": f"0x{VIA_PID:04x}"},
        "protocol_version": proto,
        "firmware_version": client.firmware_version(),
        "layout_options": client.layout_options(),
        "layers": layers,
        "matrix": {"rows": rows, "cols": cols, "detected": detected is not None},
        "keymap": keymap,
        "keymap_names": [[[keycode_name(k) for k in r] for r in lay] for lay in keymap],
        "keymap_vs_raw_mismatch": mismatch,
        "encoders": enc,
        "macros": {"count": mcount, "size": msize, "hex": macros.hex()},
        "raw_keymap_hex": raw.hex(),
        "raw_keymap_sha256": hashlib.sha256(raw).hexdigest(),
        "via_calls": client.calls,
    }
    return Backup(d)


def plan_restore(client: ViaClient, b: Backup, include_macros: bool = False) -> list[tuple]:
    """Read current state and list the writes needed to match the backup (read-only)."""
    if client.protocol_version() != b.data["protocol_version"]:
        raise ViaError("VIA protocol version differs from backup; refusing (keycode ids may differ)")
    if client.layer_count() != b.data["layers"]:
        raise ViaError("layer count differs from backup; refusing")
    ops: list[tuple] = []
    for l, lay in enumerate(b.data["keymap"]):
        for r, row in enumerate(lay):
            for c, want in enumerate(row):
                if client.keycode(l, r, c) != want:
                    ops.append(("key", l, r, c, want))
    for l, row in enumerate(b.data["encoders"]):
        for i, e in enumerate(row):
            if "error" in e:
                continue
            for cw, key in ((False, "ccw"), (True, "cw")):
                if client.encoder(l, i, cw) != e[key]:
                    ops.append(("enc", l, i, cw, e[key]))
    if include_macros and b.data["macros"]["size"]:
        want = bytes.fromhex(b.data["macros"]["hex"])
        if client.macro_buffer(len(want)) != want:
            ops.append(("macros", want))
    return ops


def apply_restore(client: ViaClient, ops: list[tuple]) -> list[str]:
    """Execute a plan (needs ``allow_writes``) and verify by read-back."""
    errors = []
    for op in ops:
        if op[0] == "key":
            _, l, r, c, kc = op
            client.set_keycode(l, r, c, kc)
            if client.keycode(l, r, c) != kc:
                errors.append(f"verify failed key L{l} r{r} c{c}")
        elif op[0] == "enc":
            _, l, i, cw, kc = op
            client.set_encoder(l, i, cw, kc)
            if client.encoder(l, i, cw) != kc:
                errors.append(f"verify failed encoder L{l} #{i} {'cw' if cw else 'ccw'}")
        elif op[0] == "macros":
            client.set_macro_buffer(op[1])
            if client.macro_buffer(len(op[1])) != op[1]:
                errors.append("verify failed macros")
    return errors


# ------------------------------------------------------------------ mock
class MockViaKeyboard:
    """QMK dynamic-keymap semantics, enough for backup/restore tests."""

    def __init__(self, rows: int = 4, cols: int = 6, layers: int = 4, encoders: int = 2,
                 protocol: int = 12, macro_size: int = 64, encoder_support: bool = True) -> None:
        self.rows, self.cols, self.layers, self.n_enc = rows, cols, layers, encoders
        self.protocol = protocol
        self.encoder_support = encoder_support
        self.eeprom = bytearray(layers * rows * cols * 2)
        self.enc = {(l, i, cw): 0 for l in range(layers) for i in range(encoders) for cw in (0, 1)}
        self.macros = bytearray(macro_size)
        self.writes: list[bytes] = []
        self.received: list[int] = []
        self._out: list[bytes] = []
        for l in range(layers):
            for r in range(rows):
                for c in range(cols):
                    self._set(l, r, c, (0x04 + r * cols + c) if l == 0 else (KC_TRNS if (r + c) % 2 else KC_NO))
        if layers:
            self._set(0, rows - 1, cols - 1, KC_RALT)
        self.enc[(0, 0, 1)], self.enc[(0, 0, 0)] = 0x80, 0x81

    def _idx(self, l, r, c):
        return ((l * self.rows + r) * self.cols + c) * 2

    def _set(self, l, r, c, kc):
        i = self._idx(l, r, c)
        self.eeprom[i:i + 2] = kc.to_bytes(2, "big")

    def _get(self, l, r, c):
        if l >= self.layers or r >= self.rows or c >= self.cols:
            return 0
        i = self._idx(l, r, c)
        return int.from_bytes(self.eeprom[i:i + 2], "big")

    # HidLink
    def write(self, report: bytes) -> None:
        assert len(report) == REPORT
        cmd = report[0]
        self.received.append(cmd)
        r = bytearray(report)
        if cmd == GET_PROTOCOL_VERSION:
            r[1:3] = self.protocol.to_bytes(2, "big")
        elif cmd == GET_KEYBOARD_VALUE:
            if report[1] == KV_FIRMWARE_VERSION:
                r[2:6] = (0x00010203).to_bytes(4, "big")
            elif report[1] == KV_LAYOUT_OPTIONS:
                r[2:6] = (0).to_bytes(4, "big")
            else:
                r[0] = UNHANDLED
        elif cmd == GET_LAYER_COUNT:
            r[1] = self.layers
        elif cmd == GET_KEYCODE:
            r[4:6] = self._get(report[1], report[2], report[3]).to_bytes(2, "big")
        elif cmd == SET_KEYCODE:
            self.writes.append(bytes(report[:6]))
            l, row, c = report[1], report[2], report[3]
            if l < self.layers and row < self.rows and c < self.cols:
                self._set(l, row, c, (report[4] << 8) | report[5])
        elif cmd == GET_BUFFER:
            off, n = (report[1] << 8) | report[2], report[3]
            for k in range(n):
                r[4 + k] = self.eeprom[off + k] if off + k < len(self.eeprom) else 0
        elif cmd in (GET_ENCODER, SET_ENCODER):
            if not self.encoder_support:
                r[0] = UNHANDLED
            else:
                key = (report[1], report[2], report[3])
                if cmd == SET_ENCODER:
                    self.writes.append(bytes(report[:6]))
                    if key in self.enc:
                        self.enc[key] = (report[4] << 8) | report[5]
                r[4:6] = self.enc.get(key, 0).to_bytes(2, "big")
        elif cmd == MACRO_GET_COUNT:
            r[1] = 16
        elif cmd == MACRO_GET_BUFFER_SIZE:
            r[1:3] = len(self.macros).to_bytes(2, "big")
        elif cmd in (MACRO_GET_BUFFER, MACRO_SET_BUFFER):
            off, n = (report[1] << 8) | report[2], report[3]
            for k in range(n):
                if off + k < len(self.macros):
                    if cmd == MACRO_SET_BUFFER:
                        self.macros[off + k] = report[4 + k]
                    r[4 + k] = self.macros[off + k]
            if cmd == MACRO_SET_BUFFER:
                self.writes.append(bytes(report[:4]))
        else:
            r[0] = UNHANDLED
        self._out.append(bytes(r))

    def read(self, timeout_ms: int) -> bytes:
        return self._out.pop(0) if self._out else b""

    def close(self) -> None:
        pass
