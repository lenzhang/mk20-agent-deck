"""End-to-end page sync: the REAL mk20-kbd binary (``--run``, fake GD32 + host on ptys) against the real Python
DeviceLink / Daemon / Controller.  The keyboard owns K2 short (route toggle + synthetic K2 tap), the L4 回 L1 key
(fb5, decided on the device) and K1 short on L4 (its own LS page); the Mac changes its page itself on a MODE long
press.  After every step the keyboard route and the Mac page must agree.

The route is observed without touching the firmware: a non-empty ``ui.hidKeys`` map is accepted only on the live L1
route (cleared again at once), and K2 rotation is forwarded to the Mac only on a live (ONLINE + fresh) L1/L4 page.
Linux only (ptys + the host build of mk20-kbd; part of the Docker ``make test``).
"""
import os
import pty
import select
import signal
import subprocess
import sys
import threading
import time
import tty
from pathlib import Path

import pytest

from mk20deck.config import DEFAULT_KNOB_MAP, DeckConfig
from mk20deck.controller import Controller
from mk20deck.daemon import Daemon
from mk20deck.link import DeviceLink
from mk20deck.render.canvas import Renderer
from mk20deck.sim import demo_adapters

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="real mk20-kbd host build + ptys: Linux (Docker) only")

KBD = Path(__file__).resolve().parents[1] / "kbd"
K1, K2 = 100, 103  # GD32 knob press codes (row == col)
FB20 = (3, 4)      # (row, col) firmware coordinates: MODE key (logical 20)
FB5 = (0, 4)       # L4 回 L1 (logical 5) / S5 on L1
FB17 = (3, 1)      # LS 会话安排 (fw 1.6.0) / DELETE on L1/L4


def gd32_frame(pl):
    return bytes([0xAA, 0x55, sum(pl) & 0xFF, len(pl), (~len(pl)) & 0xFF]) + bytes(pl) + b"\xF5\x5F"


class PtyTransport:
    """DeviceLink transport on the host pty master (blocking writes, select-based reads)."""

    def __init__(self, fd):
        self.fd = fd

    def write(self, data):
        view = memoryview(data)
        while view:
            view = view[os.write(self.fd, view):]

    def read(self, timeout):
        r, _, _ = select.select([self.fd], [], [], timeout)
        if not r:
            return b""
        try:
            return os.read(self.fd, 65536)
        except OSError:
            return b""

    def close(self):
        pass


class RecordingTransport(PtyTransport):
    """Also keeps every ui.page frame the Mac writes (after the fact: was a request sent at all?)."""

    def __init__(self, fd, pages):
        super().__init__(fd)
        self.pages = pages

    def write(self, data):
        if b'"ui.page"' in bytes(data):
            self.pages.append(bytes(data))
        super().write(data)


def mkpty():
    m, s = pty.openpty()
    tty.setraw(m)
    tty.setraw(s)
    return m, os.ttyname(s), s


class Rig:
    def __init__(self, tmp):
        subprocess.run(["make", "-s", "-C", str(KBD), "mk20-kbd"], check=True)
        fb = tmp / "fb"
        fb.mkdir()
        for i in range(1, 22):
            (fb / f"fb{i}").write_bytes(b"")
        self.gm, gpath, self._gs = mkpty()
        self.hm, hpath, self._hs = mkpty()
        self.proc = subprocess.Popen([str(KBD / "mk20-kbd"), "--run", "--gd32", gpath, "--host", hpath, "--fb", str(fb),
                                      "--bl", "", "--status", str(tmp / "kbd.status"), "--knob-timeout-ms", "200",
                                      "--lock", str(tmp / "kbd.lock"), "--no-hid"], stderr=subprocess.DEVNULL)
        self._glock = threading.Lock()
        self._stop = threading.Event()
        self._echo = threading.Thread(target=self._echo_gd32, daemon=True)
        self._echo.start()
        self.raw: list[tuple[int, int, bool]] = []  # every key/knob event the Mac received (firmware coords)
        self.page_writes: list[bytes] = []  # every ui.page frame any of our links wrote
        self.c = Controller(DeckConfig(), demo_adapters(), sim=True)
        self.connect()

    def connect(self):
        """A Mac daemon connection (Supervisor style: new DeviceLink + Daemon, the controller survives)."""
        self.link = DeviceLink(RecordingTransport(self.hm, self.page_writes), ack_timeout=1.0, preamble_flush=4096)
        self.d = Daemon(self.c, Renderer(self.link.layout, animations=False), self.link, poll_interval=3600,
                        heartbeat=1.0, knob_map=dict(DEFAULT_KNOB_MAP), bg_poll_s=0)
        self.link.on_key = self._on_key
        self.link.on_event = self.d.on_device_event
        self.link.start()
        self.d.renderer.layout = self.link.handshake()
        self.c.drop_gestures()
        self._run_stop = threading.Event()
        self.loop = threading.Thread(target=self._run, daemon=True)
        self.loop.start()

    def disconnect(self):
        """The Mac daemon goes away (restart / sleep): no heartbeats, nothing read until the next connect()."""
        self._run_stop.set()
        self.d._wake()
        self.loop.join(5)
        self.link.close()
        self.link._reader.join(2)
        assert not self.loop.is_alive() and not self.link._reader.is_alive()

    def reconnect(self):
        self.disconnect()
        self.connect()

    def _on_key(self, ev):
        self.raw.append((ev.col, ev.row, ev.pressed))
        self.d.on_device_key(ev)

    def _run(self):
        try:
            self.d.run(self._run_stop)
        except ConnectionError:
            pass

    def _echo_gd32(self):  # knob init frames are echoed like the GD32 does
        while not self._stop.is_set():
            r, _, _ = select.select([self.gm], [], [], 0.1)
            if r:
                try:
                    data = os.read(self.gm, 4096)
                except OSError:
                    return
                with self._glock:
                    os.write(self.gm, data)

    def gd32(self, *frames):
        with self._glock:
            os.write(self.gm, b"".join(gd32_frame(f) for f in frames))

    def key(self, rc, down):
        self.gd32([0x16, int(down), rc[0], rc[1], 0, 0])

    def knob(self, code, down):
        self.gd32([0x16, int(down), code, code, 0, 0])

    def tap_knob(self, code):
        self.knob(code, True)
        time.sleep(0.08)
        self.knob(code, False)

    def wait_live(self, timeout=10.0):
        """Until the keyboard shows a live Mac page (K2 rotation is forwarded only then; it only scrolls here)."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            n = len(self.raw)
            self.knob(K2 + 2, True)  # K2 cw: press edge only
            t = time.monotonic() + 0.3
            while time.monotonic() < t:
                if any(e[:2] == (K2 + 2, K2 + 2) for e in self.raw[n:]):
                    time.sleep(0.05)
                    return True
                time.sleep(0.01)
        return False

    def device_on_l1(self):
        """A non-empty ui.hidKeys map is accepted only on the live L1 route; cleared again at once."""
        res = self.link.ui_hid_keys({1: 1})
        if res == "ok":
            assert self.link.ui_hid_keys({}) == "ok"
        return res == "ok"

    def boot(self, timeout=15.0):
        """Boot: HOME until the first ONLINE, then the one-time auto-L1 (no physical input before it: any key or knob
        event would cancel the auto-L1)."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.device_on_l1():
                return True
            time.sleep(0.3)
        return False

    def _stopped(self, timeout=2.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with open(f"/proc/{self.proc.pid}/stat") as f:
                if f.read().rsplit(")", 1)[1].split()[0] == "T":
                    return True
            time.sleep(0.001)
        return False

    def cross_next_page(self, stale):
        """The next ui.page the Mac writes meets a physical K2 on the keyboard.  stale=True: the request sits in the
        host tty while the keyboard handles the K2 (SIGSTOP / SIGCONT makes both readable at once and mk20-kbd serves
        the GD32 before host bytes), so the route boundary refuses it as "stale UI request" while the K2 tap is
        already on its way to the Mac.  stale=False: the K2 is handled (its tap seen by the Mac's reader) before the
        request is written: accepted, same page, the tap processed after the ack."""
        t = self.link.t
        orig = t.write

        def write(data):
            if b'"ui.page"' not in data:
                return orig(data)
            t.write = orig
            if stale:
                os.kill(self.proc.pid, signal.SIGSTOP)
                assert self._stopped()
                orig(data)
                self.gd32([0x16, 1, K2, K2, 0, 0], [0x16, 0, K2, K2, 0, 0])
                os.kill(self.proc.pid, signal.SIGCONT)
                return None
            n = len(self.raw)
            self.gd32([0x16, 1, K2, K2, 0, 0], [0x16, 0, K2, K2, 0, 0])
            end = time.monotonic() + 2
            while (K2, K2, False) not in self.raw[n:] and time.monotonic() < end:
                time.sleep(0.002)
            return orig(data)
        t.write = write

    def wait_page(self, page, timeout=3.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end and self.c.page != page:
            time.sleep(0.01)
        return self.c.page

    def close(self):
        self._run_stop.set()
        self.d._wake()
        self.loop.join(5)
        self._stop.set()
        self.link.close()
        self.proc.terminate()
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self._echo.join(2)
        for fd in (self.gm, self.hm, self._gs, self._hs):
            try:
                os.close(fd)
            except OSError:
                pass


@pytest.fixture
def rig(tmp_path):
    r = Rig(tmp_path)
    yield r
    r.close()


def agree(rig, page):
    """Mac page == keyboard route (L1 or L4), both on a live page."""
    assert rig.wait_page(page) == page
    assert rig.wait_live(), "keyboard never showed a live Mac page"
    assert rig.c.page == page
    assert rig.device_on_l1() == (page == "L1"), f"keyboard route != Mac page {page}"


def test_device_route_and_mac_page_agree_after_mode_long_press_k2_and_back_l1(rig):
    assert rig.link.supports("ui.page")
    assert rig.boot(), "keyboard never reached L1 after the first ONLINE"
    agree(rig, "L1")
    # MODE long press: the Mac goes to L4 by itself; the keyboard must follow (ui.page L4)
    rig.key(FB20, True)
    time.sleep(1.0)
    rig.key(FB20, False)
    agree(rig, "L4")
    # K2 short on the keyboard: route toggle + K2 tap; the Mac toggles too
    rig.tap_knob(K2)
    agree(rig, "L1")
    rig.tap_knob(K2)
    agree(rig, "L4")
    # 回 L1 (fb5) is decided on the keyboard (L4 route): the Mac gets a K2 tap, never session key 5
    n = len(rig.raw)
    rig.key(FB5, True)
    time.sleep(0.08)
    rig.key(FB5, False)
    agree(rig, "L1")
    got = rig.raw[n:]
    assert (K2, K2, True) in got and not any(e[:2] == FB5[::-1] for e in got), got
    # MODE long press again, then K2: the desync the bug produced (keyboard L4, Mac L1) never happens
    rig.key(FB20, True)
    time.sleep(1.0)
    rig.key(FB20, False)
    agree(rig, "L4")
    rig.tap_knob(K2)
    agree(rig, "L1")
    # Kimi K3 interleaving (a): MODE long -> ui.page L4 in flight; the user taps K2 on the keyboard (L1 -> L4) while
    # the request is still queued: refused as stale; the Mac then gets the tap (L4 -> L1): both must end on L1.
    rejected = rig.link.pages_rejected
    rig.cross_next_page(stale=True)
    rig.key(FB20, True)
    time.sleep(1.0)
    rig.key(FB20, False)
    agree(rig, "L1")
    assert rig.link.pages_rejected == rejected + 1 and rig.link.ui_page_error == "stale UI request"
    # (c): the keyboard toggled (tap on the wire) before our ui.page L4 arrived: accepted on the same page, the tap
    # reaches the controller after the ack and must not count twice.
    rig.cross_next_page(stale=False)
    rig.key(FB20, True)
    time.sleep(1.0)
    rig.key(FB20, False)
    agree(rig, "L1")
    assert rig.link.pages_rejected == rejected + 1
    # Synced L4: K1 short opens the keyboard's own LS page; the Mac never gets K1 nor opens its SettingsPage
    rig.key(FB20, True)
    time.sleep(1.0)
    rig.key(FB20, False)
    agree(rig, "L4")
    n = len(rig.raw)
    rig.tap_knob(K1)
    time.sleep(0.5)
    assert not any(e[:2] == (K1, K1) for e in rig.raw[n:]) and rig.c.page == "L4"
    assert not rig.device_on_l1()


# ------------------------------------------------------------------ fw 1.6.0: the hello names the page
def test_fw160_reconnect_sends_no_same_page_request(rig):
    """The Mac daemon reconnects while the keyboard stays on our page: its hello reply names that page, so no ui.page
    goes out at all (older firmware got a same-page request, which released a held PTT / Mac HID key)."""
    assert rig.boot(), "keyboard never reached L1 after the first ONLINE"
    agree(rig, "L1")
    for page in ("L1", "L4"):
        if page == "L4":  # MODE long press: the Mac goes to L4 by itself (one real request)
            n = len(rig.page_writes)
            rig.key(FB20, True)
            time.sleep(1.0)
            rig.key(FB20, False)
            agree(rig, "L4")
            assert len(rig.page_writes) == n + 1
        n = len(rig.page_writes)
        rig.reconnect()
        assert rig.link.ui_hello_page == page and rig.link.ui_hello_result.get("generation") == 0
        agree(rig, page)  # live again: the full frame was forced at ONLINE
        time.sleep(1.0)
        assert len(rig.page_writes) == n, "a same-page ui.page was sent after the reconnect"
        assert rig.link.pages_sent == 0 and rig.link.pages_rejected == 0


def test_fw160_keyboard_page_changed_while_the_mac_was_away(rig):
    """K2 on the keyboard while the Mac is away (no host ONLINE: no tap then).  The next hello first sends that K2 tap,
    then names the new route: the Mac follows without any ui.page."""
    assert rig.boot()
    agree(rig, "L1")
    rig.disconnect()
    time.sleep(3.5)  # SUSPECT: the keyboard has no live host now
    rig.tap_knob(K2)  # L1 -> L4 on the keyboard, nobody told
    time.sleep(0.3)
    n_raw, n = len(rig.raw), len(rig.page_writes)
    rig.connect()
    assert rig.link.ui_hello_page == "L4"
    assert (K2, K2, True) in rig.raw[n_raw:] and (K2, K2, False) in rig.raw[n_raw:]  # the tap before the reply
    agree(rig, "L4")
    time.sleep(1.0)
    assert len(rig.page_writes) == n and rig.c.page == "L4"


def test_fw160_ls_arrange_key_reaches_the_mac(rig):
    """K1 short on the synced L4 opens the keyboard's own LS page; its 会话安排 key (fb17) sends ui.openArrange: the
    Mac mints a one-time link file and opens it (here: stand-ins, no browser)."""
    assert rig.boot()
    agree(rig, "L1")
    opened = []

    class Arrange:
        def mint_file(self):
            opened.append("minted")
            return "/nonexistent/run/open-e2e.webloc"
    rig.d.arrange, rig.d.opener = Arrange(), opened.append
    rig.key(FB20, True)
    time.sleep(1.0)
    rig.key(FB20, False)
    agree(rig, "L4")
    rig.tap_knob(K1)  # the keyboard's LS page (never forwarded to the Mac)
    time.sleep(0.4)
    assert not rig.device_on_l1() and rig.c.page == "L4"
    rig.key(FB17, True)
    time.sleep(0.08)
    rig.key(FB17, False)
    end = time.monotonic() + 3
    while len(opened) < 2 and time.monotonic() < end:
        time.sleep(0.02)
    assert opened == ["minted", "/nonexistent/run/open-e2e.webloc"]
    assert rig.d.arrange_opened == 1 and not any(e[:2] == FB17[::-1] for e in rig.raw)  # no key event for it


def test_rc4_repeated_downs_of_a_held_host_key_are_one_long_press(rig):
    """rc4: a repeated GD32 down of a held host key is the same press (never forwarded again: a phantom storm
    cannot flood the Mac); a genuine long press is still one down + one up and the Mac's 600 ms pick-up works."""
    assert rig.boot()
    agree(rig, "L1")
    idx = next(i for i in range(15) if rig.c.mgr.session_at(i) is not None)
    rc = (idx // 5, idx % 5)  # firmware (row, col) of slot S<idx+1>
    n = len(rig.raw)
    rig.key(rc, True)
    for _ in range(5):  # phantom downs while it is held
        time.sleep(0.15)
        rig.key(rc, True)
    time.sleep(0.1)
    rig.key(rc, False)
    end = time.monotonic() + 3
    while (rc[1], rc[0], False) not in rig.raw[n:] and time.monotonic() < end:
        time.sleep(0.01)
    got = [e for e in rig.raw[n:] if e[:2] == (rc[1], rc[0])]
    assert got == [(rc[1], rc[0], True), (rc[1], rc[0], False)], got
    assert wait_for_moving(rig), "the 0.85 s press was not the Mac's long press (pick-up)"


def wait_for_moving(rig, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if rig.c.moving is not None:
            return True
        time.sleep(0.01)
    return False
