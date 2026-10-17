import os
import struct
import zlib

import pytest

from mk20deck import protocol as P

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "golden_frames.txt")


def golden():
    out = {}
    with open(FIX) as f:
        for line in f:
            name, hexs = line.split()
            out[name] = bytes.fromhex(hexs)
    return out


VENDOR_TABLE_FIRST = [0x00000000, 0x77073096, 0xEE0E612C, 0x990951BA]


def test_crc_matches_vendor_table_semantics():
    # vendor CRC32.cpp = reflected 0xEDB88320, init/xorout 0xFFFFFFFF == zlib
    assert P.crc32(b"123456789") == 0xCBF43926
    assert P.crc32(b"") == 0


@pytest.mark.parametrize("name,fid,cmd,data", [
    ("getInfo", 1, 101, b'{"method":"getInfo"}'),
    ("setBacklight50", 2, 101, b'{"method":"setBacklight","parameters":{"level":50}}'),
    ("keyStateChanged", 7, 101, b'{"method":"keyStateChanged","parameters":{"col":0,"pressed":true,"row":0}}'),
    ("showJpgEmpty", 3, 100, b""),
    ("showJpgStub", 4, 100, bytes.fromhex("ffd8ffe0010100000100010000ffd9")),
])
def test_encode_matches_vendor_golden(name, fid, cmd, data):
    """Golden frames produced by tools/golden_gen.cpp (vendor packet_cmd_ack + CRC32.cpp)."""
    assert P.encode_frame(cmd, data, fid) == golden()[name]


def test_builders_produce_vendor_json():
    assert P.json_bytes(P.get_info()) == b'{"method":"getInfo"}'
    assert P.json_bytes(P.set_backlight(50)) == b'{"method":"setBacklight","parameters":{"level":50}}'
    assert P.encode_frame(P.CMD_JSON, P.json_bytes(P.get_info()), 1) == golden()["getInfo"]


def test_keyboard_input_is_forbidden():
    with pytest.raises(PermissionError):
        P.request("keyboardInput", {"inputString": "x"})


def test_parse_all_goldens_in_one_stream_with_garbage():
    g = golden()
    stream = b"0" * 1000 + g["getInfo"] + b"\x00\xa1\xa5" + g["keyStateChanged"] + g["showJpgEmpty"]
    p = P.FrameParser()
    frames = p.feed(stream)
    assert [(f.frame_id, f.cmd) for f in frames] == [(1, 101), (7, 101), (3, 100)]
    ev = P.classify_json(frames[1].json())
    assert ev == P.KeyEvent(0, 0, True)


def test_parse_byte_by_byte():
    g = golden()["setBacklight50"]
    p = P.FrameParser()
    got = []
    for b in g:
        got += p.feed(bytes([b]))
    assert len(got) == 1 and got[0].json()["parameters"]["level"] == 50


def test_bad_data_crc_dropped_and_resync():
    g = golden()
    bad = bytearray(g["getInfo"])
    bad[25] ^= 0xFF
    p = P.FrameParser()
    frames = p.feed(bytes(bad) + g["showJpgEmpty"])
    assert [f.frame_id for f in frames] == [3]
    assert p.bad_data_crc == 1


def test_bad_size_crc_skips_header():
    g = golden()["getInfo"]
    bad = bytearray(g)
    bad[16] ^= 0x01  # corrupt size_crc
    p = P.FrameParser()
    frames = p.feed(bytes(bad) + golden()["showJpgEmpty"])
    assert [f.frame_id for f in frames] == [3]
    assert p.bad_size_crc >= 1


def test_oversize_rejected():
    size = P.MAX_PAYLOAD + 1
    hdr = P.HEADER + struct.pack("<IIII", 1, 100, size, zlib.crc32(struct.pack("<I", size)))
    p = P.FrameParser()
    assert p.feed(hdr + golden()["showJpgEmpty"])[0].frame_id == 3


def test_ack_classification():
    a = P.classify_json({"ack_method": "getInfo", "success": True, "result": {"deviceModel": "MK20"}})
    assert isinstance(a, P.Ack) and a.success and a.result["deviceModel"] == "MK20"
    e = P.classify_json({"ack_method": "foo", "success": False, "errorString": "Unknown method"})
    assert e.error == "Unknown method"


def test_id_counter_wraps_skipping_zero():
    c = P.FrameIdCounter(0xFFFFFFFF)
    assert c.next() == 0xFFFFFFFF and c.next() == 1


# Golden vectors produced by compiling the vendor's CRC32.cpp verbatim with a
# byte-for-byte reimplementation of packet_cmd_ack (demo) / packet_cmd_new
# (device KeyboardDevice V2.32, Ghidra-decompiled).  See
# NOTES-protocol-re-2026-10-17.md.
def test_frame_matches_vendor_golden_vectors():
    from mk20deck import protocol as P
    assert P.encode_frame(P.CMD_JSON, P.json_bytes(P.get_info()), 1).hex() == (
        "a1a55a5e010000006500000014000000d41f3ffe"
        "7b226d6574686f64223a22676574496e666f227d8fa441b6")
    assert P.encode_frame(P.CMD_SHOW_JPG, b"abc", 7).hex() == (
        "a1a55a5e070000006400000003000000f270f133616263c2412435")
