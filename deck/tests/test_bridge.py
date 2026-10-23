"""M2: ttygs_tcp_bridge v4 (host build of the same source): strict args, multi-listen, client replacement, --tap,
non-blocking pty master with stall flush (1.6.1)."""
import os, re, select, shutil, socket, subprocess, threading, time
import pytest
from conftest import DECK

SRC = DECK / "m2-scripts/bridge/ttygs_tcp_bridge.c"
import sys

pytestmark = pytest.mark.skipif(not shutil.which("gcc") or sys.platform != "linux",
                                reason="device-side bridge: needs gcc and Linux headers")


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


# ---- v3 --tap (firmware 1.2.0): KD->host copy for the on-board router; never stalls KD or the Mac link ----
def _start(br, tmp_path, port, tap=None):
    link = tmp_path / "link"
    args = [str(br), "--listen", f"127.0.0.1:{port}", "--link", str(link)] + (["--tap", str(tap)] if tap else [])
    p = subprocess.Popen(args, stderr=subprocess.PIPE, text=True)
    for _ in range(100):
        if link.exists() and (tap is None or tap.exists()):
            break
        time.sleep(0.05)
    return p, link


def _drain(s, n, timeout=10.0):
    s.settimeout(0.5); got = b""; end = time.time() + timeout
    while len(got) < n and time.time() < end:
        try:
            c = s.recv(65536)
        except socket.timeout:
            continue
        if not c:
            break
        got += c
    return got


@pytest.mark.parametrize("tap", ["rel/path", "/" + "x" * 120])
def test_tap_bad_args(br, tmp_path, tap):
    r = subprocess.run([str(br), "--listen", "127.0.0.1:18040", "--tap", tap, "--link", str(tmp_path / "l")],
                       capture_output=True, text=True, timeout=5)
    assert r.returncode == 2


def test_tap_copy_and_mode(br, tmp_path):
    tap = tmp_path / "kd-tap.sock"
    p, link = _start(br, tmp_path, 18041, tap)
    try:
        assert oct(tap.stat().st_mode & 0o777) == "0o600"
        mac = socket.create_connection(("127.0.0.1", 18041))
        t = socket.socket(socket.AF_UNIX); t.connect(str(tap)); time.sleep(0.1)
        fd = os.open(link, os.O_RDWR | os.O_NOCTTY)
        os.write(fd, b"frame-1"); time.sleep(0.1)
        assert mac.recv(100) == b"frame-1" and t.recv(100) == b"frame-1"
        t.sendall(b"ignored"); mac.sendall(b"to-kd"); time.sleep(0.1)
        assert os.read(fd, 100) == b"to-kd"       # tap bytes never reach KD
        t2 = socket.socket(socket.AF_UNIX); t2.connect(str(tap)); time.sleep(0.1)   # new tap client replaces old
        os.write(fd, b"frame-2"); time.sleep(0.1)
        assert t2.recv(100) == b"frame-2" and t.recv(100) == b"" and mac.recv(100) == b"frame-2"
    finally:
        p.terminate(); p.wait(5)
    assert not tap.exists() and not link.exists()


def test_tap_absent_client(br, tmp_path):
    """No tap client (and a tap client that disconnects): the Mac link is unaffected."""
    tap = tmp_path / "kd-tap.sock"
    p, link = _start(br, tmp_path, 18042, tap)
    try:
        mac = socket.create_connection(("127.0.0.1", 18042)); time.sleep(0.1)
        fd = os.open(link, os.O_RDWR | os.O_NOCTTY)
        os.write(fd, b"a" * 1000); time.sleep(0.1)
        assert _drain(mac, 1000) == b"a" * 1000
        t = socket.socket(socket.AF_UNIX); t.connect(str(tap)); time.sleep(0.1); t.close(); time.sleep(0.1)
        os.write(fd, b"b" * 1000); time.sleep(0.1)
        assert _drain(mac, 1000) == b"b" * 1000 and p.poll() is None
    finally:
        p.terminate(); p.wait(5)


def test_tap_slow_client_never_stalls(br, tmp_path):
    """A tap client that never reads: KD writes and the Mac client still get every byte (tap drops instead)."""
    tap = tmp_path / "kd-tap.sock"
    p, link = _start(br, tmp_path, 18043, tap)
    try:
        mac = socket.create_connection(("127.0.0.1", 18043))
        t = socket.socket(socket.AF_UNIX); t.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096); t.connect(str(tap))
        time.sleep(0.1)
        fd = os.open(link, os.O_RDWR | os.O_NOCTTY)
        os.set_blocking(fd, False)
        chunk = bytes(range(256)) * 4; total = 0; sent = b""; end = time.time() + 20; got = b""
        mac.setblocking(False)
        while total < 2 * 1024 * 1024 and time.time() < end:
            try:
                w = os.write(fd, chunk); total += w; sent += chunk[:w]
            except BlockingIOError:
                time.sleep(0.001)
            try:
                got += mac.recv(1 << 20)
            except BlockingIOError:
                pass
        assert total >= 2 * 1024 * 1024, "KD side stalled behind the tap"
        mac.setblocking(True)
        got += _drain(mac, total - len(got))
        assert got == sent
        tg = _drain(t, 1 << 30, timeout=3.0)   # returns at EOF: the bridge closed the stuck tap client
        assert len(tg) < total and sent.startswith(tg)   # a gap-free prefix, then EOF (never a silent hole)
        t.settimeout(1); assert t.recv(10) == b""
        assert p.poll() is None
    finally:
        p.terminate(); err = p.communicate(timeout=5)[1]
    assert "tap client dropped" in err


def test_tap_unavailable_continues(br, tmp_path):
    """A tap path that cannot be bound (missing dir) is logged; the bridge still serves the Mac link."""
    tap = tmp_path / "nodir" / "kd-tap.sock"
    link = tmp_path / "link"
    p = subprocess.Popen([str(br), "--listen", "127.0.0.1:18044", "--link", str(link), "--tap", str(tap)],
                         stderr=subprocess.PIPE, text=True)
    try:
        for _ in range(100):
            if link.exists():
                break
            time.sleep(0.05)
        mac = socket.create_connection(("127.0.0.1", 18044)); time.sleep(0.1)
        fd = os.open(link, os.O_RDWR | os.O_NOCTTY); os.write(fd, b"ok"); time.sleep(0.1)
        assert mac.recv(10) == b"ok"
    finally:
        p.terminate(); err = p.communicate(timeout=5)[1]
    assert "continuing without tap" in err



# ---- v4 (firmware 1.6.1): never block the loop on the pty master; flush a stalled pty input path ----
class _Err:
    """Collects the bridge's stderr lines with their arrival time (monotonic)."""
    def __init__(self, p):
        self.lines: list = []
        self._t = threading.Thread(target=self._run, args=(p,), daemon=True)
        self._t.start()

    def _run(self, p):
        for line in p.stderr:
            self.lines.append((time.monotonic(), line.rstrip("\n")))

    def stalls(self):
        return [(t, l) for t, l in self.lines if "pty input stalled" in l]

    def wait_stall(self, n, timeout):
        end = time.monotonic() + timeout
        while time.monotonic() < end and len(self.stalls()) < n:
            time.sleep(0.02)
        return self.stalls()


def _recv_exact(s, n, timeout=3.0):
    s.settimeout(timeout); got = b""
    while len(got) < n:
        c = s.recv(n - len(got))
        if not c:
            break
        got += c
    return got


def _closed(s, timeout=2.0):
    """True once the peer closed s (EOF or RST); unread data is skipped."""
    s.settimeout(timeout); end = time.monotonic() + timeout
    try:
        while time.monotonic() < end:
            if s.recv(65536) == b"":
                return True
    except (ConnectionResetError, BrokenPipeError):
        return True
    except socket.timeout:
        return False
    return False


def _flood_until_blocked(c, quiet=0.3, limit=1.5):
    """Send on non-blocking c until sends have failed with EAGAIN for `quiet` s (the bridge stopped reading)."""
    chunk = bytes(range(256)) * 64; blocked = None; end = time.monotonic() + limit; sent = 0
    while time.monotonic() < end:
        try:
            sent += c.send(chunk); blocked = None
        except BlockingIOError:
            blocked = blocked or time.monotonic()
            if time.monotonic() - blocked >= quiet:
                return sent
            time.sleep(0.005)
    raise AssertionError(f"client never blocked ({sent} bytes sent): the bridge kept reading a full pty")


def test_pty_stall_keeps_the_bridge_responsive_then_flushes(br, tmp_path):
    """Incident 2026-10-23 (1.6.0-rc4): a pty slave whose input nobody consumes + a flooding client. The bridge must
    keep serving (master->client forwarding, a new client accepted) while the pty input is full, flush the slave's
    input after PTY_STALL_MS = 2 s without write progress (one log line, pending bytes dropped, current client closed),
    and carry normal traffic once a reader appears."""
    p, link = _start(br, tmp_path, 18050)
    err = _Err(p)
    try:
        slave = os.open(link, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)   # the "KD side": written, never read (yet)
        c1 = socket.create_connection(("127.0.0.1", 18050)); time.sleep(0.1)
        c1.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 16384); c1.setblocking(False)
        t0 = time.monotonic()
        _flood_until_blocked(c1)                       # pty input full, bridge pending full, TCP backpressure
        assert not err.stalls() and p.poll() is None
        # master -> client still forwarded while the client -> pty direction is blocked
        os.write(slave, b"kd-out-1")
        select.select([c1], [], [], 1.0)
        c1.setblocking(True)
        assert _recv_exact(c1, 8, 1.0) == b"kd-out-1"
        # a new client is accepted (replaces c1) and gets KD output, still before the stall flush
        c2 = socket.create_connection(("127.0.0.1", 18050), timeout=1.0); time.sleep(0.1)
        os.write(slave, b"kd-out-2")
        assert _recv_exact(c2, 8, 1.0) == b"kd-out-2"
        assert _closed(c1, 1.0)                        # replaced
        t_c2 = time.monotonic()
        st = err.wait_stall(1, 6.0)
        assert len(st) == 1, err.lines
        t_line, line = st[0]
        assert t_line > t_c2, "c2 was served only after the flush: the loop was blocked"
        m = re.search(r"pty input stalled (\d+) s: flushed \(#1\); (\d+) pending bytes dropped, client closed$", line)
        assert m and m.group(1) == "2" and 0 < int(m.group(2)) <= 4096, line
        assert 2.0 <= t_line - t0 <= 5.0, t_line - t0  # 2 s without write progress (PTY_STALL_MS)
        assert _closed(c2, 2.0)                        # the current client is closed: the host reconnects
        with pytest.raises(BlockingIOError):           # TCIFLUSH emptied the slave input (flip buffer + ldisc)
            os.read(slave, 65536)
        # a reader appears: normal traffic in both directions, no further stall
        got = bytearray(); stop = threading.Event()

        def reader():
            while not stop.is_set():
                r, _, _ = select.select([slave], [], [], 0.05)
                if r:
                    try:
                        got.extend(os.read(slave, 65536))
                    except BlockingIOError:
                        pass
        th = threading.Thread(target=reader, daemon=True); th.start()
        c3 = socket.create_connection(("127.0.0.1", 18050)); time.sleep(0.1)
        payload = os.urandom(256 * 1024)
        c3.sendall(payload)
        end = time.monotonic() + 10
        while len(got) < len(payload) and time.monotonic() < end:
            time.sleep(0.02)
        stop.set(); th.join(2)
        assert bytes(got) == payload
        os.write(slave, b"kd-out-3")
        assert _recv_exact(c3, 8, 1.0) == b"kd-out-3"
        assert len(err.stalls()) == 1 and p.poll() is None
    finally:
        p.terminate(); p.wait(5)


def test_slow_reader_never_triggers_the_stall_flush(br, tmp_path):
    """A reader that pauses 1.2 s at a time (< PTY_STALL_MS) while the client floods: no flush, every byte in order."""
    p, link = _start(br, tmp_path, 18051)
    err = _Err(p)
    try:
        slave = os.open(link, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        c = socket.create_connection(("127.0.0.1", 18051)); time.sleep(0.1)
        payload = os.urandom(256 * 1024)
        th = threading.Thread(target=c.sendall, args=(payload,), daemon=True); th.start()
        got = bytearray()
        for i in range(3):                             # pause with the pty full, then read ~64 KiB
            time.sleep(1.2)
            while len(got) < 64 * 1024 * (i + 1):
                r, _, _ = select.select([slave], [], [], 0.2)
                if not r:
                    break
                try:
                    got.extend(os.read(slave, 4096))
                except BlockingIOError:
                    pass
        end = time.monotonic() + 10
        while len(got) < len(payload) and time.monotonic() < end:
            r, _, _ = select.select([slave], [], [], 0.05)
            if r:
                try:
                    got.extend(os.read(slave, 65536))
                except BlockingIOError:
                    pass
        th.join(2)
        assert bytes(got) == payload and not err.stalls(), err.lines
        assert p.poll() is None
    finally:
        p.terminate(); p.wait(5)
