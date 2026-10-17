"""Read-only USB enumeration (macOS ``system_profiler``/``ioreg``; Linux sysfs) and MK20 role tagging."""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass

from ..adapters.base import SubprocessRunner

KNOWN = {
    (0x1D6B, 0x0104): "T113 gadget (CDC-ECM + CDC-ACM, Protocol A)",
    (0x1234, 0x5678): "T113 gadget (alternate id)",
    (0x4250, 0x426F): "GD32 QMK/VIA keyboard (HID)",
    (0x1EAF, 0x0003): "GD32 in DFU bootloader (stm32duino/Maple) — keyboard NOT running",
    (0x05E3, 0x0610): "GL852G hub",
    (0x05E3, 0x0608): "GL852G/GL850G hub",
    (0x1F3A, 0xEFE8): "Allwinner FEL (T113 in USB boot mode) — system NOT running",
}


@dataclass
class UsbDev:
    vid: int
    pid: int
    name: str
    serial: str = ""
    location: str = ""
    role: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["vid"], d["pid"] = f"0x{self.vid:04x}", f"0x{self.pid:04x}"
        return d


def _hex(v) -> int | None:
    if v is None:
        return None
    if isinstance(v, int):
        return v
    m = re.search(r"0x([0-9a-fA-F]{1,4})", str(v))
    return int(m.group(1), 16) if m else None


def parse_system_profiler(js: str) -> list[UsbDev]:
    out: list[UsbDev] = []

    def walk(items):
        for it in items or []:
            vid, pid = _hex(it.get("vendor_id")), _hex(it.get("product_id"))
            if vid is not None and pid is not None:
                out.append(UsbDev(vid, pid, it.get("_name", ""), it.get("serial_num", ""),
                                  it.get("location_id", "")))
            walk(it.get("_items"))

    d = json.loads(js)
    for bus in d.get("SPUSBDataType", []) + d.get("SPUSBHostDataType", []):
        walk(bus.get("_items"))
    return out


def parse_ioreg(text: str) -> list[UsbDev]:
    """``ioreg -p IOUSB -l -w0`` fallback: blocks with idVendor/idProduct."""
    out: list[UsbDev] = []
    for block in re.split(r"\+-o ", text)[1:]:
        name = block.split("<", 1)[0].strip()
        v = re.search(r'"idVendor" = (\d+)', block)
        p = re.search(r'"idProduct" = (\d+)', block)
        if v and p:
            s = re.search(r'"USB Serial Number" = "([^"]*)"', block)
            loc = re.search(r'"locationID" = (\d+)', block)
            out.append(UsbDev(int(v.group(1)), int(p.group(1)), name, s.group(1) if s else "",
                              hex(int(loc.group(1))) if loc else ""))
    return out


def parse_sysfs(root: str = "/sys/bus/usb/devices") -> list[UsbDev]:
    out = []
    if not os.path.isdir(root):
        return out
    for n in sorted(os.listdir(root)):
        p = os.path.join(root, n)
        try:
            vid = int(open(os.path.join(p, "idVendor")).read().strip(), 16)
            pid = int(open(os.path.join(p, "idProduct")).read().strip(), 16)
        except (OSError, ValueError):
            continue
        prod = ""
        try:
            prod = open(os.path.join(p, "product")).read().strip()
        except OSError:
            pass
        out.append(UsbDev(vid, pid, prod, location=n))
    return out


def enumerate_usb(runner=None, platform: str | None = None) -> tuple[list[UsbDev], str]:
    runner = runner or SubprocessRunner()
    platform = platform or os.uname().sysname
    devs: list[UsbDev] = []
    source = ""
    if platform == "Darwin":
        r = runner.run(["system_profiler", "-json", "SPUSBDataType"], timeout=30)
        if r.returncode == 0 and r.stdout.strip():
            try:
                devs, source = parse_system_profiler(r.stdout), "system_profiler"
            except (ValueError, KeyError):
                devs = []
        if not devs:
            r = runner.run(["ioreg", "-p", "IOUSB", "-l", "-w0"], timeout=30)
            devs, source = parse_ioreg(r.stdout or ""), "ioreg"
    else:
        devs, source = parse_sysfs(), "sysfs"
    for d in devs:
        d.role = KNOWN.get((d.vid, d.pid), "")
    return devs, source


def mk20_summary(devs: list[UsbDev]) -> dict:
    ids = {(d.vid, d.pid) for d in devs}
    return {
        "t113_cdc": (0x1D6B, 0x0104) in ids or (0x1234, 0x5678) in ids,
        "gd32_via": (0x4250, 0x426F) in ids,
        "gd32_dfu": (0x1EAF, 0x0003) in ids,
        "t113_fel": (0x1F3A, 0xEFE8) in ids,
        "devices": [d.to_dict() for d in devs if d.role],
    }
