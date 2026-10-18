"""tf-restore-boot.sh --whole-image GPT check (the embedded python) on synthetic sparse images."""
import re
import struct
import subprocess
import sys
import uuid
import zlib
from pathlib import Path

SCRIPT = Path(__file__).with_name("tf-restore-boot.sh").read_text()
CHECK = re.search(r"python3 - \"\$SRC\" <<'PY'.*?\n(.*?)\nPY\n", SCRIPT, re.S).group(1)
MK20 = [(41464, 65536), (107000, 504), (107504, 504), (108008, 12600), (120608, 2097152), (2217760, 5644512)]
NLBA = 2217760 + 5644512 + 34


def make(path, parts, nlba=NLBA, corrupt_backup=False):
    es, n = 128, 128
    ents = bytearray(es * n)
    for i, (s, c) in enumerate(parts):
        struct.pack_into("<16s16sQQ", ents, i * es, uuid.uuid4().bytes, uuid.uuid4().bytes, s, s + c - 1)
    ecrc = zlib.crc32(bytes(ents)) & 0xFFFFFFFF

    def hdr(cur, alt, ent):
        h = bytearray(92)
        struct.pack_into("<8sIII", h, 0, b"EFI PART", 0x10000, 92, 0)
        struct.pack_into("<QQQQ16sQIII", h, 24, cur, alt, 34, nlba - 34, uuid.uuid4().bytes, ent, n, es, ecrc)
        struct.pack_into("<I", h, 16, zlib.crc32(bytes(h)) & 0xFFFFFFFF)
        return bytes(h)
    with open(path, "wb") as f:
        f.truncate(nlba * 512)
        f.seek(510); f.write(b"\x55\xaa")
        f.seek(512); f.write(hdr(1, nlba - 1, 2))
        f.seek(1024); f.write(ents)
        f.seek((nlba - 33) * 512); f.write(ents if not corrupt_backup else b"x" + bytes(ents[1:]))
        f.seek((nlba - 1) * 512); f.write(hdr(nlba - 1, 1, nlba - 33))


def check(p):
    return subprocess.run([sys.executable, "-", str(p)], input=CHECK, capture_output=True, text=True)


def test_mk20_layout_ok(tmp_path):
    make(tmp_path / "a.img", MK20)
    r = check(tmp_path / "a.img")
    assert r.returncode == 0, r.stderr


def test_five_partitions_refused(tmp_path):
    make(tmp_path / "a.img", MK20[:5])
    assert check(tmp_path / "a.img").returncode != 0


def test_shifted_partition_refused(tmp_path):
    bad = list(MK20); bad[3] = (108016, 12600)
    make(tmp_path / "a.img", bad)
    assert "p4" in check(tmp_path / "a.img").stderr


def test_bad_backup_refused(tmp_path):
    make(tmp_path / "a.img", MK20, corrupt_backup=True)
    assert check(tmp_path / "a.img").returncode != 0


def test_header_crc_refused(tmp_path):
    make(tmp_path / "a.img", MK20)
    with open(tmp_path / "a.img", "r+b") as f:
        f.seek(512 + 60); f.write(b"\xff")
    assert "CRC" in check(tmp_path / "a.img").stderr


def test_partition_image_refused(tmp_path):
    (tmp_path / "p4.img").write_bytes(b"\0" * 6451200)
    assert check(tmp_path / "p4.img").returncode != 0
