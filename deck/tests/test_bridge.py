"""M2: ttygs_tcp_bridge v2 (host build of the same source): strict args, multi-listen, client replacement."""
import os, shutil, socket, subprocess, time
import pytest
from conftest import DECK

SRC = DECK / "m2-scripts/bridge/ttygs_tcp_bridge.c"
pytestmark = pytest.mark.skipif(not shutil.which("gcc"), reason="needs gcc")


@pytest.fixture(scope="module")
def br(tmp_path_factory):
    out = tmp_path_factory.mktemp("br") / "bridge"
    subprocess.run(["gcc", "-Wall", "-Wextra", "-Werror", "-O2", "-o", str(out), str(SRC)], check=True)
    return out


@pytest.mark.parametrize("args", [[], ["--listen", "127.0.0.1:0"], ["--listen", "127.0.0.1:+80"], ["--listen", "127.0.0.1: 80"],
                                  ["--listen", "1.2.3:80"], ["--listen", "127.0.0.1:65536"], ["--listen", ":80"],
                                  ["--listen", "127.0.0.1:80", "--bogus"], ["--listen"] * 1, ["--listen", "0.0.0.0:18020"],
                                  ["--dev", "x" * 20, "--listen", "127.0.0.1:18021"],
                                  sum([["--listen", f"127.0.0.1:{p}"] for p in range(18000, 18005)], [])])
def test_bad_args(br, tmp_path, args):
    link = tmp_path / "link"
    r = subprocess.run([str(br), *args, "--link", str(link)], capture_output=True, text=True, timeout=5)
    assert r.returncode == 2, r.stderr
    assert not link.exists()


def test_bind_failure_leaves_no_link(br, tmp_path):
    link = tmp_path / "link"
    r = subprocess.run([str(br), "--listen", "192.0.2.77:18010", "--link", str(link)], capture_output=True, text=True, timeout=5)
    assert r.returncode == 1 and not link.exists()


def test_multi_listen_and_replace(br, tmp_path):
    link = tmp_path / "link"
    p = subprocess.Popen([str(br), "--listen", "127.0.0.1:18011", "--listen", "127.0.0.2:18011", "--link", str(link)],
                         stderr=subprocess.PIPE, text=True)
    try:
        for _ in range(50):
            if link.exists():
                break
            time.sleep(0.05)
        s1 = socket.create_connection(("127.0.0.1", 18011)); time.sleep(0.1)
        fd = os.open(link, os.O_RDWR | os.O_NOCTTY)
        os.write(fd, b"to-client"); time.sleep(0.1)
        assert s1.recv(100) == b"to-client"
        s1.sendall(b"to-kd"); time.sleep(0.1)
        assert os.read(fd, 100) == b"to-kd"
        s2 = socket.create_connection(("127.0.0.2", 18011)); time.sleep(0.1)
        os.write(fd, b"second"); time.sleep(0.1)
        assert s2.recv(100) == b"second" and s1.recv(100) == b""
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.3", 18011), timeout=1)
    finally:
        p.terminate(); p.wait(5)
    assert not link.exists()   # SIGTERM removes the link


def test_bind_to_device(br, tmp_path):
    """--dev lo: reachable on lo; a non-existent device fails (rc 1) before the link is created."""
    link = tmp_path / "link"
    r = subprocess.run([str(br), "--dev", "nonexist0", "--listen", "127.0.0.1:18030", "--link", str(link)],
                       capture_output=True, text=True, timeout=5)
    assert r.returncode == 1 and not link.exists()
