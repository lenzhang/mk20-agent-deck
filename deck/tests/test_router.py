"""mk20-router (C, on-board) host build: Protocol A parsing, PTT -> HID Right Alt, keytrace, robustness."""
import json
import os
import shutil
import subprocess

import pytest
from conftest import DECK

from mk20deck.keytrace import parse
from mk20deck.protocol import CMD_JSON, encode_frame

SRC = DECK / "router/mk20_router.c"
pytestmark = pytest.mark.skipif(not shutil.which("gcc"), reason="needs gcc")


@pytest.fixture(scope="module")
def router(tmp_path_factory):
    out = tmp_path_factory.mktemp("rt") / "mk20-router"
    subprocess.run(["gcc", "-Wall", "-Wextra", "-Werror", "-O2", "-o", str(out), str(SRC)], check=True)
    return out


def ksc(col, row, pressed, fid=1, wrap=True):
    p = {"col": col, "row": row, "pressed": pressed}
    obj = {"method": "keyStateChanged", "parameters": p} if wrap else {"method": "keyStateChanged", **p}
    return encode_frame(CMD_JSON, json.dumps(obj, separators=(",", ":")).encode(), fid)


def run(router, tmp_path, stream: bytes, *args, trace=True):
    hid = tmp_path / "hidg0"
    hid.write_bytes(b"")
    src = tmp_path / "in.bin"
    src.write_bytes(stream)
    env = {"PATH": os.environ["PATH"]}
    if trace:
        env |= {"MK20_KEYTRACE": "1", "MK20_KEYTRACE_PATH": str(tmp_path / "k.log")}
    r = subprocess.run([str(router), "--src", f"file:{src}", "--hid", str(hid), *args], env=env,
                       capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, r.stderr
    tl = (tmp_path / "k.log").read_text() if trace and (tmp_path / "k.log").exists() else ""
    return hid.read_bytes(), parse(tl), r.stderr


def reports(b):
    return [b[i:i + 9] for i in range(0, len(b), 9)]


def test_ptt_press_release_writes_ralt(router, tmp_path):
    hid, tr, _ = run(router, tmp_path, ksc(0, 3, True) + ksc(0, 3, False, 2))
    assert reports(hid) == [bytes([1, 0x40, 0, 0, 0, 0, 0, 0, 0]), bytes([1, 0, 0, 0, 0, 0, 0, 0, 0])]
    assert [t[1] for t in tr] == ["recv", "routed", "hid_written"] * 2
    assert tr[0][2] == "0,3" and all(a[0] <= b[0] for a, b in zip(tr, tr[1:]))


def test_other_keys_and_knobs_are_local(router, tmp_path):
    hid, tr, _ = run(router, tmp_path, ksc(0, 0, True) + ksc(102, 102, True, 2, wrap=False))
    assert hid == b"" and [t[1] for t in tr] == ["recv", "routed", "local"] * 2


def test_garbage_bad_crc_and_split_reads_resync(router, tmp_path):
    good = ksc(0, 3, True)
    bad = bytearray(ksc(0, 3, False, 5))
    bad[-1] ^= 0xFF  # corrupt data crc
    stream = b"\x00junk\xa1\xa5" + bytes(bad) + good + b"\xa1\xa5\x5a" + ksc(0, 3, False, 7)
    hid, _tr, err = run(router, tmp_path, stream)
    assert reports(hid) == [bytes([1, 0x40] + [0] * 7), bytes([1, 0] + [0] * 7)]
    assert "bad_crc=" in err


def test_stuck_modifier_released_on_eof(router, tmp_path):
    hid, _tr, _ = run(router, tmp_path, ksc(0, 3, True))
    assert reports(hid)[-1] == bytes([1, 0] + [0] * 7)


def test_ptt_and_fw_base_options(router, tmp_path):
    hid, _tr, _ = run(router, tmp_path, ksc(2, 2, True) + ksc(2, 2, False, 2), "--ptt", "2,2", "--fw-base", "1")
    assert len(reports(hid)) == 2


def test_trace_off_is_silent(router, tmp_path):
    hid, tr, _ = run(router, tmp_path, ksc(0, 3, True) + ksc(0, 3, False, 2), trace=False)
    assert len(reports(hid)) == 2 and not (tmp_path / "k.log").exists()


def test_trace_rotation(router, tmp_path):
    stream = b"".join(ksc(0, 0, i % 2 == 0, i + 1) for i in range(400))
    hid = tmp_path / "h"
    hid.write_bytes(b"")
    src = tmp_path / "s"
    src.write_bytes(stream)
    env = {"MK20_KEYTRACE": "1", "MK20_KEYTRACE_PATH": str(tmp_path / "k.log"), "MK20_KEYTRACE_MAX": "2000"}
    subprocess.run([str(router), "--src", f"file:{src}", "--hid", str(hid)], env=env, check=True, timeout=10)
    assert (tmp_path / "k.log").stat().st_size <= 2000 and (tmp_path / "k.log.1").exists()


@pytest.mark.parametrize("args", [[], ["--src", "-"], ["--src", "-", "--hid", "x", "--ptt", "9,9"],
                                  ["--src", "-", "--hid", "x", "--fw-base", "2"], ["--bogus"]])
def test_bad_args(router, args):
    assert subprocess.run([str(router), *args], capture_output=True, timeout=5).returncode == 2


@pytest.mark.skipif(not shutil.which("arm-linux-gnueabihf-gcc"), reason="needs armhf cross gcc")
def test_armhf_static_build(tmp_path):
    out = tmp_path / "mk20-router.armhf"
    subprocess.run(["arm-linux-gnueabihf-gcc", "-Wall", "-Wextra", "-Werror", "-O2", "-static", "-o", str(out),
                    str(SRC)], check=True)
    head = out.read_bytes()[:20]
    assert head[:4] == b"\x7fELF" and head[4] == 1 and head[18] == 40  # 32-bit, EM_ARM
