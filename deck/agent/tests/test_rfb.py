"""A2: all connections are to loopback fakes; no device or real desktop access."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

AGENT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT.parent))
from agent.tests.fakes.fake_vnc import FakeVNC as Server, gradient  # noqa: E402

SOCKETPAIR = os.environ.get("MK20_TEST_SOCKETPAIR") == "1"


def FakeVNC(*args, **kwargs):
    return Server(*args, socketpair=SOCKETPAIR, messages=True, **kwargs)


@pytest.fixture(scope="module")
def binaries(tmp_path_factory):
    build = tmp_path_factory.mktemp("rfb-build")
    flags = ["CFLAGS=-std=c11 -Wall -Wextra -Werror -O1 -g -DMK20_RFB_TEST"] if SOCKETPAIR else []
    subprocess.run(["make", "-C", str(AGENT), f"BUILD={build}", *flags, str(build / "mk20-agent"),
                    str(build / "mk20-agent-nosan"), str(build / "test_rfb")], check=True)
    subprocess.run([str(build / "test_rfb")], check=True)
    return build


def invoke(binary, *args, ok=True, password="secret12", server=None):
    env = dict(os.environ, MK20_VNC_PASSWORD=password)
    fds = ()
    if server and server.client:
        fds = (server.client.fileno(),); env["MK20_RFB_TEST_FD"] = str(fds[0])
    p = subprocess.run([str(binary), *map(str, args)], capture_output=True, text=True, env=env, pass_fds=fds, timeout=30)
    assert p.stderr == "", p.stderr
    assert (p.returncode == 0) == ok, p.stdout
    assert len(p.stdout.splitlines()) == 1
    return json.loads(p.stdout)


def snap(binaries, tmp_path, v, *args, ok=True, nosan=False, password="secret12"):
    return invoke(binaries / ("mk20-agent-nosan" if nosan else "mk20-agent"), "--snap", tmp_path / "snap.jpg",
                  "--host", "127.0.0.1", "--port", v.port, *args, ok=ok, password=password, server=v)


def probe(binaries, tmp_path, v, mw=1280, mh=800, frames=1, text=None):
    path = tmp_path / "pixels.rgb"
    args = [v.port, path, mw, mh, frames] + ([text] if text is not None else [])
    result = invoke(binaries / "test_rfb", *args, server=v)
    return result, path.read_bytes()


def box_reference(rgb, sw, sh, dw, dh):
    """Exact integer overlaps in a common coordinate grid, rounded to nearest."""
    out = bytearray()
    for ty in range(dh):
        ya, yb = ty * sh, (ty + 1) * sh
        for tx in range(dw):
            xa, xb = tx * sw, (tx + 1) * sw
            sums = [0, 0, 0]
            for y in range(ya // dh, (yb - 1) // dh + 1):
                wy = min(yb, (y + 1) * dh) - max(ya, y * dh)
                for x in range(xa // dw, (xb - 1) // dw + 1):
                    wx = min(xb, (x + 1) * dw) - max(xa, x * dw)
                    for c in range(3): sums[c] += rgb[(y * sw + x) * 3 + c] * wx * wy
            out.extend((s + sw * sh // 2) // (sw * sh) for s in sums)
    return bytes(out)


@pytest.mark.parametrize("encoding", [0, 5, 16])
@pytest.mark.parametrize("size,max_size", [((130, 70), (1280, 800)), ((137, 83), (51, 37)),
                                          ((131, 129), (1, 1)), ((65, 17), (34, 2))])
@pytest.mark.parametrize("split", [False, True])
def test_pixels(binaries, tmp_path, encoding, size, max_size, split):
    with FakeVNC(*size, encoding=encoding, split=split) as v:
        info, pixels = probe(binaries, tmp_path, v, *max_size)
        want = box_reference(v.rgb, *size, info["w"], info["h"])
        assert len(pixels) == len(want) and max(abs(a - b) for a, b in zip(pixels, want)) <= 2
        if size == (130, 70): assert pixels == v.rgb
        assert v.requests[0] == (0, 0, 0, *size)
        assert v.encodings_seen[0][:6] == [16, 5, 0, 1, -223, -239]
        assert v.encodings_seen[0][-1] & 0xffffffff == 0xc0a1e5ce


@pytest.mark.parametrize("mode", [0, 1, 2, 3, 4, 5, 16, 128, 130, 144, 255])
def test_zrle_subencodings(binaries, tmp_path, mode):
    w, h = 69, 67
    # Two colours exercise row padding and indices, long runs use solid separately.
    rgb = bytes(b for y in range(h) for x in range(w) for b in ((17, 91, 233) if mode == 1 or (x + y) % 2 else (123, 45, 67)))
    with FakeVNC(w, h, image=rgb, encoding=16, zrle_mode=mode) as v:
        _, pixels = probe(binaries, tmp_path, v)
        assert pixels == rgb


@pytest.mark.parametrize("mode", [128, 130, 255])
def test_zrle_long_runs(binaries, tmp_path, mode):
    rgb = bytes([0, 128, 255]) * (69 * 67)
    with FakeVNC(69, 67, image=rgb, encoding=16, zrle_mode=mode) as v:
        _, pixels = probe(binaries, tmp_path, v)
        assert pixels == rgb


@pytest.mark.parametrize("mode", ["raw", "solid", "foreground", "colored"])
def test_hextile_subencodings(binaries, tmp_path, mode):
    rgb = gradient(35, 33) if mode in ("colored", "raw") else bytes([5, 50, 250]) * (35 * 33)
    with FakeVNC(35, 33, image=rgb, encoding=5, hextile_mode=mode) as v:
        _, pixels = probe(binaries, tmp_path, v)
        assert pixels == rgb


@pytest.mark.parametrize("version", [3, 7, 8])
@pytest.mark.parametrize("sec_type", [1, 2])
def test_auth_versions(binaries, tmp_path, version, sec_type):
    with FakeVNC(17, 13, version=version, sec_type=sec_type) as v:
        info = snap(binaries, tmp_path, v)
        assert info["ok"] and (info["src_w"], info["src_h"]) == (17, 13)
        assert 0 < info["jpeg_bytes"] <= 128 * 1024 and info["peak_rss_kb"] > 0
        jpeg = (tmp_path / "snap.jpg").read_bytes()
        assert len(jpeg) == info["jpeg_bytes"] and jpeg.startswith(b"\xff\xd8") and jpeg.endswith(b"\xff\xd9")
        from PIL import Image
        with Image.open(tmp_path / "snap.jpg") as image:
            assert image.size == (17, 13)
            image.load()


@pytest.mark.parametrize("password", [b"", b"a", b"12345678", b"12345678ignored", b"\x80\xffAB"])
def test_des_password_file(binaries, tmp_path, password):
    path = tmp_path / "password"; path.write_bytes(password + b"\n"); path.chmod(0o600)
    with FakeVNC(9, 7, password=password) as v:
        assert snap(binaries, tmp_path, v, "--password-file", path)["ok"]


def test_auth_failure_no_password_in_output(binaries, tmp_path):
    with FakeVNC(password=b"right", security_reason=b"server echoed wrong-password") as v:
        info = snap(binaries, tmp_path, v, password="wrong-password", ok=False)
        assert info == {"ok": False, "error": "auth_failed"} and v.auth_fail == 1
        assert not (tmp_path / "snap.jpg").exists()


@pytest.mark.parametrize("sec_type,want", [(30, "unsupported_security"), (19, "unsupported_security"), (0, "security_refused")])
def test_unsupported_security(binaries, tmp_path, sec_type, want):
    with FakeVNC(sec_type=sec_type) as v:
        assert snap(binaries, tmp_path, v, ok=False)["error"] == want


def test_persistent_zlib_resize_copy_cursor(binaries, tmp_path):
    with FakeVNC(129, 79, cursor=True, resize=(131, 81), resize_at=1, extended=True) as v:
        info, pixels = probe(binaries, tmp_path, v, frames=2)
        assert info["extended_clipboard"] and pixels == gradient(131, 81)
        assert len(v.requests) == 3 and all(r[0] == 0 for r in v.requests)
    with FakeVNC(129, 79, copyrect=True) as v:
        info, pixels = probe(binaries, tmp_path, v, 51, 37)
        assert pixels == box_reference(v.rgb, v.w, v.h, info["w"], info["h"])
        assert len(v.requests) == 2 and all(r[0] == 0 for r in v.requests)


@pytest.mark.parametrize("extended", [False, True, "caps"])
@pytest.mark.parametrize("text,want", [("hello café ÿ", b"hello caf\xe9 \xff"), ("中文", None), ("emoji 🎹", None)])
def test_clipboard(binaries, tmp_path, extended, text, want):
    with FakeVNC(17, 13, extended=extended, cut_length=1024 * 1024 + 257) as v:
        info, pixels = probe(binaries, tmp_path, v, text=text)
        assert info["extended_clipboard"] == bool(extended) and pixels == v.rgb
        assert info["clip_error"] == ("unsupported" if want is None else "")
        assert v.cut_text == ([want] if want is not None else [])


@pytest.mark.parametrize("fields,error", [({"malformed": "bounds"}, "rect_bounds"),
                                          ({"encoding": 16, "malformed": "zrle_length"}, "length_limit"),
                                          ({"encoding": 16, "malformed": "zrle_bad"}, "zrle"),
                                          ({"encoding": 16, "malformed": "zrle_palette"}, "zrle_palette"),
                                          ({"encoding": 16, "malformed": "zrle_run"}, "zrle_run"),
                                          ({"encoding": 16, "malformed": "zrle_mode"}, "zrle_mode"),
                                          ({"encoding": 16, "malformed": "zrle_extra"}, "zrle_trailing"),
                                          ({"encoding": 5, "malformed": "hextile_bounds"}, "hextile_bounds"),
                                          ({"cut_wire_length": 0x7fffffff}, "length_limit"),
                                          ({"cut_wire_length": 0x80000000}, "length_limit"),
                                          ({"name_length": 0xffffffff}, "length_limit"),
                                          ({"truncate": True}, "truncated_stream")])
def test_malformed(binaries, tmp_path, fields, error):
    with FakeVNC(9, 7, **fields) as v:
        assert snap(binaries, tmp_path, v, ok=False)["error"] == error


def test_framebuffer_limit_and_timeout(binaries, tmp_path):
    with FakeVNC(8193, 1) as v:
        assert snap(binaries, tmp_path, v, ok=False)["error"] == "framebuffer_size"
    with FakeVNC(9, 7, delay=0.8) as v:
        t = time.monotonic()
        assert snap(binaries, tmp_path, v, "--timeout-ms", 100, ok=False)["error"] == "timeout"
        assert time.monotonic() - t < 1
    with FakeVNC(9, 7, resize=(8193, 1)) as v:
        assert snap(binaries, tmp_path, v, ok=False)["error"] == "framebuffer_size"


def test_reason_length_limit(binaries, tmp_path):
    with FakeVNC(9, 7, sec_type=0, security_reason=b"x" * (1024 * 1024 + 1)) as v:
        assert snap(binaries, tmp_path, v, ok=False)["error"] == "length_limit"


def test_cli_rejects_password_argv_and_bad_options(binaries, tmp_path):
    for args in [("--password", "secret"), ("--quality", 101), ("--max-w", 0), ("--port", 65536), ("--timeout-ms", -1)]:
        assert invoke(binaries / "mk20-agent", "--snap", tmp_path / "snap.jpg", *args, ok=False)["error"] == "bad_args"


def test_jpeg_hard_cap(binaries, tmp_path):
    import random
    noise = random.Random(42).randbytes(1280 * 800 * 3)
    with FakeVNC(1280, 800, image=noise, encoding=0) as v:
        assert snap(binaries, tmp_path, v, "--quality", 100, ok=False, nosan=True)["error"] == "image_limit"
    assert not (tmp_path / "snap.jpg").exists()


@pytest.mark.parametrize("size", [(1280, 800), (2880, 1800)])
def test_nosan_peak_rss(binaries, tmp_path, size):
    with FakeVNC(*size, encoding=16) as v:
        info = snap(binaries, tmp_path, v, "--timeout-ms", 15000, nosan=True)
        assert info["peak_rss_kb"] <= 16 * 1024, info
        assert info["heap_hw_bytes"] <= 14 * 1024 * 1024, info
        assert (info["w"], info["h"]) == (1280, 800)
        print(json.dumps({"source": size, **info}))
