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
        self.link = DeviceLink(PtyTransport(self.hm), ack_timeout=1.0, preamble_flush=4096)
        self.c = Controller(DeckConfig(), demo_adapters(), sim=True)
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

    def wait_page(self, page, timeout=3.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end and self.c.page != page:
            time.sleep(0.01)
        return self.c.page

    def close(self):
        self._run_stop.set()
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
