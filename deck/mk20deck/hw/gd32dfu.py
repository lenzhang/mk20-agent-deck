"""GD32 (stm32duino/Maple bootloader, DFU ``1eaf:0003``) re-flash wrapper.

Dry-run by default: validates the image, lists DFU devices (``dfu-util -l`` is
read-only) and prints the exact command.  Flashing runs only with
``execute=True`` (CLI flag ``--i-understand-this-reflashes-gd32``).
"""
from __future__ import annotations

import hashlib
import re
import struct
from dataclasses import dataclass, field

from ..adapters.base import SubprocessRunner

DFU_ID = "1eaf:0003"
APP_BASE = 0x08002000  # stm32duino bootloader occupies the first 8 KiB
# Vendor image from the MK20 research bundle (gd32/syk_keyboards_mk20_plus_via.bin).
KNOWN_IMAGES = {
    "54cc4910f038154a4a10db4b59973ded9294843e0dadccc37eb1f3d7229098d0":
        "syk_keyboards_mk20_plus_via.bin (vendor VIA firmware, 39552 B, linked @0x08002000)",
}


@dataclass
class FlashPlan:
    image: str
    sha256: str
    known: str | None
    size: int
    sp: int
    reset: int
    alt: int | None = None
    dfu_listing: str = ""
    problems: list[str] = field(default_factory=list)
    argv: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def inspect_image(path: str) -> tuple[str, int, int, int]:
    data = open(path, "rb").read()
    sp, reset = struct.unpack_from("<II", data, 0)
    return hashlib.sha256(data).hexdigest(), len(data), sp, reset


def parse_dfu_list(text: str) -> list[dict]:
    out = []
    for m in re.finditer(r"Found DFU: \[([0-9a-fA-F]{4}:[0-9a-fA-F]{4})\].*?alt=(\d+), name=\"([^\"]*)\"", text):
        out.append({"id": m.group(1).lower(), "alt": int(m.group(2)), "name": m.group(3)})
    return out


def plan_flash(image: str, runner=None, allow_unknown: bool = False) -> FlashPlan:
    runner = runner or SubprocessRunner()
    sha, size, sp, reset = inspect_image(image)
    p = FlashPlan(image, sha, KNOWN_IMAGES.get(sha), size, sp, reset)
    if not p.known and not allow_unknown:
        p.problems.append("image sha256 is not the known vendor image (use --allow-unknown-image to override)")
    if not (0x20000000 <= sp <= 0x20010000):
        p.problems.append(f"initial SP 0x{sp:08x} not in SRAM — not a Cortex-M image?")
    if not (APP_BASE <= reset < APP_BASE + 0x40000):
        p.problems.append(f"reset vector 0x{reset:08x} not in app region @0x{APP_BASE:08x} — wrong link address")
    if size > 0x40000 - 0x2000:
        p.problems.append("image larger than flash")
    if not runner.which("dfu-util"):
        p.problems.append("dfu-util not installed (brew install dfu-util)")
    else:
        r = runner.run(["dfu-util", "-l"], timeout=10)
        p.dfu_listing = r.stdout
        alts = [a for a in parse_dfu_list(r.stdout) if a["id"] == DFU_ID]
        flash_alt = [a for a in alts if "8002000" in a["name"]] or [a for a in alts if a["alt"] == 2]
        if not alts:
            p.problems.append("no DFU device 1eaf:0003 — put the GD32 into its bootloader first (RECOVERY.md)")
        elif not flash_alt:
            p.problems.append(f"bootloader present but no alt for 0x08002000: {alts}")
        else:
            p.alt = flash_alt[0]["alt"]
    p.argv = ["dfu-util", "-d", DFU_ID, "-a", str(p.alt if p.alt is not None else 2), "-D", image, "-R"]
    return p


def execute(plan: FlashPlan, runner=None) -> tuple[int, str]:
    if not plan.ok:
        raise RuntimeError("refusing to flash: " + "; ".join(plan.problems))
    runner = runner or SubprocessRunner()
    r = runner.run(plan.argv, timeout=120)
    return r.returncode, (r.stdout or "") + (r.stderr or "")
