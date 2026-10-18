"""tools/tf_gpt_check.py (used by tf-restore-boot.sh in both modes): exact MK20 TF layout, on synthetic sparse images
built to match the live #221 card (and on the real card head when ~/mk20-backup is present)."""
import os
import struct
import subprocess
import sys
import uuid
import zlib
from pathlib import Path

import pytest

HERE = Path(__file__).parent
CHECK = HERE / "tf_gpt_check.py"
sys.path.insert(0, str(HERE))
import tf_gpt_check as G  # noqa: E402

BASIC = uuid.UUID("ebd0a0a2-b9e5-4433-87c0-68b6b72699c7").bytes_le
LIVE = [  # slot, start, length, attr, name, unique guid  (from the live card's disk-head.img)
    (s, a, n, at, nm, uuid.UUID(f"a0085546-4166-744a-a353-fca9272b8e{0x44 + s:02x}").bytes_le)
    for s, (a, n, at, nm) in G.MK20.items()
]
REAL_HEAD = [Path(p).expanduser() / "disk-head.img" for p in
             ("/workspace/mk20-backup/tf", "~/mk20-backup/tf/live-221-parts")]


def entries(parts, n=8):
    t = bytearray(128 * n)
    for slot, a, cnt, attr, name, uq, *typ in parts:
        struct.pack_into("<16s16sQQQ", t, (slot - 1) * 128, typ[0] if typ else BASIC, uq, a, a + cnt - 1, attr)
        t[(slot - 1) * 128 + 56:(slot - 1) * 128 + 56 + 2 * len(name)] = name.encode("utf-16-le")
    return bytes(t)


def hdr(cur, alt, ent, ecrc, guid, nlba=G.NLBA, first=G.FIRST, last=None, n=8, rev=0x10000):
    h = bytearray(512)
    struct.pack_into("<8sIII", h, 0, b"EFI PART", rev, 92, 0)
    struct.pack_into("<QQQQ16sQIII", h, 24, cur, alt, first, last if last is not None else nlba - 4, guid, ent, n, 128, ecrc)
    struct.pack_into("<I", h, 16, zlib.crc32(bytes(h[:92])) & 0xFFFFFFFF)
    return bytes(h)


MBR = bytes(446) + G.MBR_TABLE + b"\x55\xaa"


def make(path, parts=LIVE, nlba=G.NLBA, backup_parts=None, mbr=MBR, ent_lba=G.ENT_LBA, backup_guid=None, **hk):
    disk = uuid.uuid4().bytes
    e = entries(parts); be = entries(backup_parts) if backup_parts else e
    with open(path, "wb") as f:
        f.truncate(nlba * 512)
        f.write(mbr)
        f.seek(512); f.write(hdr(1, nlba - 1, ent_lba, zlib.crc32(e), disk, nlba=nlba, **hk))
        f.seek(ent_lba * 512); f.write(e)
        f.seek((nlba - 3) * 512); f.write(be)
        f.seek((nlba - 1) * 512); f.write(hdr(nlba - 1, 1, nlba - 3, zlib.crc32(be), backup_guid or disk, nlba=nlba, **hk))
    return path


def run(*args):
    return subprocess.run([sys.executable, str(CHECK), *map(str, args)], capture_output=True, text=True)


def refused(p, word):
    r = run(p)
    assert r.returncode != 0 and word in r.stderr, r.stderr
    return r


def test_exact_layout_ok(tmp_path):
    r = run(make(tmp_path / "a.img"))
    assert r.returncode == 0, r.stderr


def test_card_dump_mode_ok_and_bad(tmp_path):
    img = make(tmp_path / "a.img")
    with open(img, "rb") as f:
        head = f.read(G.HEAD_LBAS * 512); f.seek((G.NLBA - 3) * 512); tail = f.read(3 * 512)
    (tmp_path / "h").write_bytes(head); (tmp_path / "t").write_bytes(tail)
    assert run("--card", tmp_path / "h", tmp_path / "t", G.NLBA).returncode == 0
    (tmp_path / "t").write_bytes(b"x" + tail[1:])
    assert run("--card", tmp_path / "h", tmp_path / "t", G.NLBA).returncode != 0
    (tmp_path / "h").write_bytes(head[:-512])
    assert "dump sizes" in run("--card", tmp_path / "h", tmp_path / "t", G.NLBA).stderr


@pytest.mark.parametrize("head", [p for p in REAL_HEAD if p.is_file()][:1] or [None])
def test_real_live_card_head_ok(tmp_path, head):
    if head is None:
        pytest.skip("no ~/mk20-backup disk-head.img here")
    data = head.read_bytes()
    h = bytearray(data[512:1024])
    ent = data[G.ENT_LBA * 512:(G.ENT_LBA + 2) * 512]
    struct.pack_into("<QQ", h, 24, G.NLBA - 1, 1); struct.pack_into("<Q", h, 72, G.NLBA - 3)
    struct.pack_into("<I", h, 16, 0); struct.pack_into("<I", h, 16, zlib.crc32(bytes(h[:92])) & 0xFFFFFFFF)
    (tmp_path / "t").write_bytes(ent + bytes(h))   # backup = primary mirrored (what a healthy card has)
    r = run("--card", head, tmp_path / "t", G.NLBA)
    assert r.returncode == 0, r.stderr


def test_wrong_card_size_refused(tmp_path):
    refused(make(tmp_path / "a.img", nlba=G.NLBA + 2048), "LBAs")


def test_partition_image_refused(tmp_path):
    (tmp_path / "p4.img").write_bytes(b"\0" * 6451200)
    assert run(tmp_path / "p4.img").returncode != 0


def test_mbr_must_match(tmp_path):
    bad = bytes(446) + bytes.fromhex("0000020007ffffff01000000ffffffff") + bytes(48) + b"\x55\xaa"
    refused(make(tmp_path / "a.img", mbr=bad), "MBR")


def test_gpt_revision_must_be_1_0(tmp_path):
    refused(make(tmp_path / "a.img", rev=0x20000), "revision")


def test_entry_array_location_must_match(tmp_path):
    refused(make(tmp_path / "a.img", ent_lba=2), "geometry")


def test_first_usable_must_match(tmp_path):
    refused(make(tmp_path / "a.img", first=34), "geometry")


def test_entry_count_must_match(tmp_path):
    refused(make(tmp_path / "a.img", n=128), "geometry")


def test_five_partitions_refused(tmp_path):
    refused(make(tmp_path / "a.img", parts=LIVE[:5]), "slot 6")


def test_seventh_partition_refused(tmp_path):
    extra = (7, 7862272, 1000, 0, "x", b"\x07" * 16)
    refused(make(tmp_path / "a.img", parts=LIVE + [extra]), "slot 7")


@pytest.mark.parametrize("field,value,word", [
    (1, 108016, "p4"), (2, 1000, "p4"), (3, 0xC000000000000000, "attributes"), (4, "BOOT", "name"),
])
def test_p4_field_mismatch_refused(tmp_path, field, value, word):
    bad = [list(p) for p in LIVE]; bad[3][field] = value
    refused(make(tmp_path / "a.img", parts=[tuple(p) for p in bad]), word)


def test_type_guid_must_be_basic_data(tmp_path):
    bad = list(LIVE); bad[0] = LIVE[0] + (b"T" * 16,)
    refused(make(tmp_path / "a.img", parts=bad), "type GUID")


def test_duplicate_unique_guid_refused(tmp_path):
    bad = list(LIVE); bad[1] = LIVE[1][:5] + (LIVE[0][5],)
    refused(make(tmp_path / "a.img", parts=bad), "unique GUID")


def test_reordered_names_refused(tmp_path):
    bad = list(LIVE); bad[1] = LIVE[1][:4] + ("env-redund",) + LIVE[1][5:]; bad[2] = LIVE[2][:4] + ("env",) + LIVE[2][5:]
    refused(make(tmp_path / "a.img", parts=bad), "name")


def test_backup_table_differs_refused(tmp_path):
    bad = list(LIVE); bad[5] = LIVE[5][:2] + (5644000,) + LIVE[5][3:]
    refused(make(tmp_path / "a.img", backup_parts=bad), "differ")


def test_backup_guid_differs_refused(tmp_path):
    refused(make(tmp_path / "a.img", backup_guid=b"G" * 16), "guid")


def test_header_crc_refused(tmp_path):
    p = make(tmp_path / "a.img")
    with open(p, "r+b") as f:
        f.seek(512 + 60); f.write(b"\xff")
    refused(p, "CRC")


def test_entries_crc_refused(tmp_path):
    p = make(tmp_path / "a.img")
    with open(p, "r+b") as f:
        f.seek(G.ENT_LBA * 512 + 40); f.write(b"\x01")
    refused(p, "CRC")


def test_script_uses_checker_in_both_modes():
    s = (HERE / "tf-restore-boot.sh").read_text()
    assert 'python3 "$GPT_CHECK" "$SRC"' in s and 'python3 "$GPT_CHECK" --card' in s
    # the card check runs before anything is unmounted or written
    assert s.index('--card "$TMPD/head"') < s.index("diskutil unmountDisk") < s.index("sudo dd if=\"$SRC\"")


@pytest.mark.skipif(os.name != "posix", reason="posix")
def test_checker_is_read_only(tmp_path):
    p = make(tmp_path / "a.img"); before = p.stat().st_mtime_ns
    run(p)
    assert p.stat().st_mtime_ns == before
