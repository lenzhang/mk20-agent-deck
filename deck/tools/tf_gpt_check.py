#!/usr/bin/env python3
"""Read-only check that a disk image (or a TF card dump) has EXACTLY the MK20 #221 TF layout.

Reference: the live card's GPT (~/mk20-backup/tf/live-221-parts/disk-head.img, layout.txt). Anything else is refused:
protective MBR, GPT revision/geometry, entry array location/count, both headers + CRCs, primary == backup,
and per-slot type GUID, start LBA, length, attributes and name for exactly slots 1-6 (slots 7-8 empty).

  tf_gpt_check.py IMAGE                      whole-card raw image (nlba = file size / 512)
  tf_gpt_check.py --card HEAD TAIL NLBA      card dump: HEAD = LBAs [0, 41464), TAIL = LBAs [NLBA-3, NLBA)
Exit 0 = MK20 layout; non-zero with a reason on stderr otherwise.
"""
import struct
import sys
import zlib

NLBA = 7864320                     # the MK20 TF card: 4026531840 B
FIRST, LAST = 41464, NLBA - 4      # usable LBAs
ENT_LBA, N_ENT, ENT_SIZE = 41462, 8, 128
TBL = (N_ENT * ENT_SIZE + 511) // 512
HEAD_LBAS = ENT_LBA + TBL          # 41464: MBR + primary header + primary entries
TAIL_LBAS = TBL + 1                # backup entries + backup header
REVISION = 0x00010000
BASIC_DATA = bytes.fromhex("a2a0d0ebe5b9334487c068b6b72699c7")   # EBD0A0A2-B9E5-4433-87C0-68B6B72699C7 (on-disk)
# slot: (start LBA, length in LBAs, attributes, name)
MK20 = {
    1: (41464, 65536, 0x0000200000000000, "boot-resource"),
    2: (107000, 504, 0xC000200000000000, "env"),
    3: (107504, 504, 0xC000200000000000, "env-redund"),
    4: (108008, 12600, 0xC000200000000000, "boot"),
    5: (120608, 2097152, 0xC000200000000000, "rootfs"),
    6: (2217760, 5644512, 0xC000000000000000, "UDISK"),
}
# protective MBR partition table (bytes 446..509): one 0xEE entry LBA 1, length 0xFFFFFFFF; rest empty
MBR_TABLE = bytes.fromhex("00000200eeffffff01000000ffffffff") + b"\0" * 48


class Reader:
    def __init__(self, image=None, head=None, tail=None, nlba=None):
        if image is not None:
            self.f = open(image, "rb")
            self.f.seek(0, 2)
            size = self.f.tell()
            if size % 512:
                sys.exit(f"image size {size} is not a multiple of 512")
            self.nlba = size // 512
            self.head = self.tail = None
        else:
            self.f = None
            self.nlba = nlba
            self.head = open(head, "rb").read()
            self.tail = open(tail, "rb").read()
            if len(self.head) != HEAD_LBAS * 512 or len(self.tail) != TAIL_LBAS * 512:
                sys.exit(f"card dump sizes head={len(self.head)} tail={len(self.tail)} != "
                         f"{HEAD_LBAS * 512}/{TAIL_LBAS * 512}")

    def read(self, lba, count):
        if lba < 0 or lba + count > self.nlba:
            sys.exit(f"read LBA {lba}+{count} outside the disk")
        if self.f is not None:
            self.f.seek(lba * 512)
            return self.f.read(count * 512)
        if lba + count <= HEAD_LBAS:
            return self.head[lba * 512:(lba + count) * 512]
        t0 = self.nlba - TAIL_LBAS
        if lba >= t0:
            return self.tail[(lba - t0) * 512:(lba - t0 + count) * 512]
        sys.exit(f"LBA {lba}+{count} not in the card dump")


def header(r, lba, alt, ent_lba):
    h = r.read(lba, 1)
    if h[:8] != b"EFI PART":
        sys.exit(f"no GPT header at LBA {lba}")
    rev, hs = struct.unpack_from("<II", h, 8)
    if rev != REVISION or hs != 92:
        sys.exit(f"GPT revision {rev:#x} / header size {hs} at LBA {lba} != 1.0/92")
    if h[20:24] != b"\0\0\0\0" or any(h[92:]):
        sys.exit(f"GPT header reserved bytes non-zero at LBA {lba}")
    z = bytearray(h[:92]); z[16:20] = b"\0\0\0\0"
    if zlib.crc32(bytes(z)) & 0xFFFFFFFF != struct.unpack_from("<I", h, 16)[0]:
        sys.exit(f"GPT header CRC bad at LBA {lba}")
    cur, alt_, first, last = struct.unpack_from("<QQQQ", h, 24)
    ent, n, es, ecrc = struct.unpack_from("<QIII", h, 72)
    want = (lba, alt, FIRST, LAST, ent_lba, N_ENT, ENT_SIZE)
    got = (cur, alt_, first, last, ent, n, es)
    if got != want:
        sys.exit(f"GPT geometry at LBA {lba} (cur,alt,first,last,entries,n,size)={got} != MK20 {want}")
    e = r.read(ent, TBL)[:n * es]
    if zlib.crc32(e) & 0xFFFFFFFF != ecrc:
        sys.exit(f"GPT entries CRC bad (header LBA {lba})")
    return h[56:72], e


def check(r):
    if r.nlba != NLBA:
        sys.exit(f"disk has {r.nlba} LBAs, the MK20 card has {NLBA}")
    mbr = r.read(0, 1)
    if mbr[510:512] != b"\x55\xaa" or mbr[446:510] != MBR_TABLE:
        sys.exit("protective MBR does not match the MK20 card (single 0xEE entry at LBA 1)")
    guid, ents = header(r, 1, NLBA - 1, ENT_LBA)
    bguid, bents = header(r, NLBA - 1, 1, NLBA - 1 - TBL)
    if guid != bguid:
        sys.exit("primary/backup GPT differ in disk guid")
    if ents != bents:
        sys.exit("primary/backup GPT differ in partition entries")
    seen = set()
    for i in range(N_ENT):
        ent = ents[i * ENT_SIZE:(i + 1) * ENT_SIZE]
        slot = i + 1
        if slot not in MK20:
            if any(ent):
                sys.exit(f"slot {slot} is used; MK20 uses exactly slots 1-6")
            continue
        if ent[:16] == b"\0" * 16:
            sys.exit(f"slot {slot} is empty; MK20 uses exactly slots 1-6")
        s0, n0, a0, name0 = MK20[slot]
        a, b, attr = struct.unpack_from("<QQQ", ent, 32)
        try:
            name = ent[56:128].decode("utf-16-le").split("\0", 1)[0]
        except UnicodeDecodeError:
            sys.exit(f"p{slot} name is not UTF-16")
        if ent[:16] != BASIC_DATA:
            sys.exit(f"p{slot} type GUID {ent[:16].hex()} != MK20 basic-data")
        uniq = ent[16:32]
        if uniq == b"\0" * 16 or uniq in seen:
            sys.exit(f"p{slot} unique GUID zero or duplicated")
        seen.add(uniq)
        if (a, b - a + 1) != (s0, n0):
            sys.exit(f"p{slot} {a}+{b - a + 1} != MK20 {s0}+{n0}")
        if attr != a0:
            sys.exit(f"p{slot} attributes {attr:#x} != MK20 {a0:#x}")
        if name != name0:
            sys.exit(f"p{slot} name {name!r} != MK20 {name0!r}")
    print("GPT ok: exact MK20 layout (MBR, GPT 1.0 geometry, slots 1-6 type/start/size/attr/name; backup identical)")


def main(argv):
    if len(argv) == 2:
        check(Reader(image=argv[1]))
    elif len(argv) == 5 and argv[1] == "--card":
        if not argv[4].isdigit():
            sys.exit("NLBA must be an integer")
        check(Reader(head=argv[2], tail=argv[3], nlba=int(argv[4])))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
