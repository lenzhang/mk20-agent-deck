#!/usr/bin/env python3
"""Pack zImage into MK20-compatible Android boot.img (t113-pro BOOT_FEX)."""
import argparse, struct, hashlib
from pathlib import Path

STOCK_IMG = Path("/workspace/mk20-research/fw/mk20_dump/RFSFAT16_BOOT_FEX00000000")
STOCK_HDR = Path(__file__).resolve().parents[1] / "boot-extract" / "stock-boot-hdr-2k.bin"
RAMDISK = b"ramdisk.img\n"  # shipping sentinel (12 bytes)

def pack(kernel: str, out: str, page: int = 2048) -> None:
    k = Path(kernel).read_bytes()
    if STOCK_HDR.exists():
        header = bytearray(STOCK_HDR.read_bytes()[:page])
    else:
        header = bytearray(STOCK_IMG.read_bytes()[:page])
    struct.pack_into("<I", header, 8, len(k))
    struct.pack_into("<I", header, 16, len(RAMDISK))
    h = hashlib.sha1(k)
    h.update(RAMDISK)
    header[576:596] = h.digest()
    def pad(data: bytes, psz: int) -> bytes:
        n = (len(data) + psz - 1) // psz * psz
        return data + b"\0" * (n - len(data))
    blob = bytes(header) + pad(k, page) + pad(RAMDISK, page)
    Path(out).write_bytes(blob)
    print(f"wrote {out} ({len(blob)} bytes) sha256={hashlib.sha256(blob).hexdigest()}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("kernel")
    ap.add_argument("-o", required=True)
    a = ap.parse_args()
    pack(a.kernel, a.o)
