"""mk20-kbd host liveness (kbd/UI.md): ui.hello negotiation + ui.heartbeat thread in DeviceLink."""
import io
import statistics
import threading
import time

import pytest
from PIL import Image

from mk20deck import protocol as P
from mk20deck.config import DeckConfig
from mk20deck.link import DeviceLink
from mk20deck.sim import FakeClock, _ctl
from mk20deck.supervisor import Supervisor, default_renderer_factory
from mk20deck.transport import MockDevice


def wait_until(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


def _jpeg(w=640, h=656):
    b = io.BytesIO()
    Image.new("RGB", (w, h), (10, 20, 30)).save(b, "JPEG")
    return b.getvalue()


def _link(tr, **kw):
    link = DeviceLink(tr, preamble_flush=0, **kw)
    link.ui_beat_min_s = 0.05  # tests use heartbeat_ms in the 100 ms range
    link.start()
    link.handshake()
    return link


@pytest.fixture
def links():
    made = []
    yield made
    for link in made:
        link.close()


def test_hello_negotiates_and_heartbeats_stay_inside_device_window(links):
    dev = MockDevice(ui=True, heartbeat_ms=200)
    dev.ui_generation = 7
    link = _link(dev)
    links.append(link)
    assert link.ui_negotiated and link.ui_hello_result["generation"] == 7
    assert link.ui_beat_s == pytest.approx(0.24)
    hello = [r for r in dev.json_requests if r["method"] == "ui.hello"]
    assert hello == [{"method": "ui.hello", "version": 1}]  # flat JSON, no "parameters"
    assert [r["method"] for r in dev.json_requests][:2] == ["getInfo", "ui.hello"]
    assert wait_until(lambda: len(dev.heartbeats) >= 8 and link.ui_beats >= 8)  # device got it AND we saw the ack
    assert wait_until(lambda: dev.ui_online)  # 3 counted samples spanning >= 2 periods
    gaps = [b - a for a, b in zip(dev.heartbeats, dev.heartbeats[1:])]
    # Lower bound is by construction (next beat scheduled one period after the previous ack): never "too close".
    assert min(gaps) >= 0.2 and dev.beats_too_close == 0
    assert dev.hellos[0] + 0.2 <= dev.heartbeats[0]  # first sample one period after the hello, not at once
    assert 0.2 <= statistics.median(gaps) <= 0.3  # typical gap inside [1, 1.5] periods (scheduler jitter aside)
    assert link.ui_beats >= 8 and link.ui_rehellos == 0 and link.ui_beat_rejects == 0
    assert dev.heartbeats_rejected == 0 and not any("parameters" in r for r in dev.json_requests
                                                    if r["method"] == "ui.heartbeat")
    assert link.show_jpeg(_jpeg()) and len(dev.frames) == 1


def test_legacy_device_stays_legacy_without_heartbeat_thread(links):
    dev = MockDevice()  # stock KD / fw < 1.3: "Unknown method" for ui.*
    link = _link(dev, ack_timeout=0.5)
    links.append(link)
    assert not link.ui_negotiated and link._beat is None  # no heartbeat thread
    assert [r["method"] for r in dev.json_requests] == ["getInfo", "ui.hello"]
    assert link.show_jpeg(_jpeg()) and len(dev.frames) == 1
    time.sleep(0.7)  # longer than a heartbeat period at the clamp minimum
    assert [r["method"] for r in dev.json_requests] == ["getInfo", "ui.hello"]
    assert link.connected and link.ui_beats == 0


def test_heartbeat_rejected_after_loss_triggers_rehello(links):
    dev = MockDevice(ui=True, heartbeat_ms=100)
    link = _link(dev)
    links.append(link)
    assert wait_until(lambda: len(dev.heartbeats) >= 3)
    lost_at = time.monotonic()
    dev.lose()
    assert wait_until(lambda: link.ui_rehellos == 1 and len(dev.hellos) == 2)
    assert dev.heartbeats_rejected >= 1 and link.ui_beat_rejects >= 1
    assert wait_until(lambda: sum(t > dev.hellos[1] for t in dev.heartbeats) >= 3)
    assert wait_until(lambda: dev.ui_online)
    assert dev.hellos[1] > lost_at and link.ui_negotiated and link.connected
    # One rejection -> one hello: the rate limit keeps a device that keeps rejecting from making us spin.
    assert link.ui_rehellos == 1


def test_rehello_is_rate_limited_when_device_keeps_rejecting(links):
    dev = MockDevice(ui=True, heartbeat_ms=100)
    link = _link(dev)
    links.append(link)
    link.ui_rehello_min_s = 0.6
    orig = dev._ui

    def stuck(method, obj):  # SUSPECT that never clears: hello accepted, heartbeats rejected
        r = orig(method, obj)
        if method == "ui.hello":
            dev.ui_lost = True
        return r

    dev.ui_lost = True
    dev._ui = stuck
    time.sleep(1.5)
    assert link.ui_beat_rejects >= 6  # one beat per 0.12 s keeps going ...
    assert 1 <= link.ui_rehellos <= 3  # ... but at most one hello per 0.6 s
    assert link.connected


class OverlapProbe:
    """Transport wrapper that detects two threads inside write() at once and records frame ids."""

    def __init__(self, dev):
        self.dev = dev
        self._busy = threading.Lock()
        self._parser = P.FrameParser()
        self.overlaps = 0
        self.writes = 0
        self.ids = []

    def write(self, data):
        if not self._busy.acquire(blocking=False):
            self.overlaps += 1
            self._busy.acquire()
        try:
            self.writes += 1
            self.ids += [fr.frame_id for fr in self._parser.feed(data)]
            time.sleep(0.002)  # widen the window a concurrent writer would hit
            self.dev.write(data)
        finally:
            self._busy.release()

    def read(self, timeout):
        return self.dev.read(timeout)

    def close(self):
        self.dev.close()


def test_concurrent_pushes_and_heartbeats_never_overlap_writes(links):
    dev = MockDevice(ui=True, heartbeat_ms=50)
    probe = OverlapProbe(dev)
    link = _link(probe, ack_timeout=2.0)
    links.append(link)
    jpeg = _jpeg()
    calls = []

    def caller():  # a third writer (e.g. setBacklight / devtest calls) alongside pushes and beats
        for _ in range(40):
            ack = link.call(P.get_info())
            calls.append(bool(ack and ack.success))

    th = threading.Thread(target=caller)
    th.start()
    pushed = [link.show_jpeg(jpeg) for _ in range(60)]
    th.join(10)
    beats_before = link.ui_beats
    assert wait_until(lambda: link.ui_beats >= beats_before + 2)
    assert all(pushed) and len(dev.frames) == 60
    assert len(calls) == 40 and all(calls)
    assert probe.overlaps == 0, f"{probe.overlaps} overlapping writes"
    assert len(probe.ids) == len(set(probe.ids)), "duplicate frame ids"
    assert dev._parser.bad_size_crc == dev._parser.bad_data_crc == dev._parser.dropped_bytes == 0
    assert link.ui_beats >= 5 and link.ui_beat_timeouts == 0 and link.ui_beat_rejects == 0
    assert dev.beats_too_close == 0


class DelayedBeat(OverlapProbe):
    """Holds back one heartbeat write (as if queued behind a large SHOW_JPG write)."""

    def __init__(self, dev, nth, delay):
        super().__init__(dev)
        self.nth, self.delay, self.beats = nth, delay, 0

    def write(self, data):
        frames = P.FrameParser().feed(data)
        if any((fr.json() or {}).get("method") == "ui.heartbeat" for fr in frames):
            self.beats += 1
            if self.beats == self.nth:
                time.sleep(self.delay)
        super().write(data)


def test_delayed_heartbeat_does_not_shorten_the_next_gap(links):
    dev = MockDevice(ui=True, heartbeat_ms=200)  # beats every 0.24 s, device counts gaps >= 0.2 s
    link = _link(DelayedBeat(dev, nth=3, delay=0.12))
    links.append(link)
    assert wait_until(lambda: len(dev.heartbeats) >= 6)
    gaps = [b - a for a, b in zip(dev.heartbeats, dev.heartbeats[1:])]
    assert gaps[1] >= 0.3  # the held-back beat arrived late ...
    assert gaps[2] >= 0.2 and dev.beats_too_close == 0  # ... and the following one is still a full period later


def test_close_stops_idle_heartbeat_thread_promptly():
    dev = MockDevice(ui=True, heartbeat_ms=1000)  # beat thread sleeps 1.2 s between samples
    link = _link(dev)
    beat = link._beat
    assert beat is not None and beat.is_alive()
    t0 = time.monotonic()
    link.close()
    assert time.monotonic() - t0 < 0.5 and not beat.is_alive()
    n = len(dev.json_requests)
    time.sleep(1.5)
    assert len(dev.json_requests) == n  # nothing sent after close


def test_close_wakes_heartbeat_blocked_on_its_ack():
    dev = MockDevice(ui=True, heartbeat_ms=100)
    link = _link(dev, ack_timeout=10.0)
    assert wait_until(lambda: len(dev.heartbeats) >= 1)
    dev.hung = True  # T113 stuck: the next heartbeat never gets an ack
    assert wait_until(lambda: link._pending)  # beat thread waiting on its ack
    t0 = time.monotonic()
    link.close()
    assert time.monotonic() - t0 < 0.5 and not link._beat.is_alive()


def test_device_loss_ends_heartbeat_thread():
    dev = MockDevice(ui=True, heartbeat_ms=100)
    link = _link(dev)
    assert wait_until(lambda: len(dev.heartbeats) >= 1)
    dev.unplugged = True
    assert wait_until(lambda: not link._beat.is_alive(), timeout=2)
    assert not link.connected
    link.close()


def test_supervisor_reconnect_runs_one_heartbeat_thread_per_link():
    cfg = DeckConfig()
    cfg.preamble_flush, cfg.ack_timeout_s, cfg.heartbeat_s, cfg.poll_interval_s = 0, 0.5, 0.05, 0.5
    # heartbeat_ms=400 -> 0.5 s beats (the production clamp minimum; the supervisor builds its own links)
    state = {"dev": MockDevice(ui=True, heartbeat_ms=400), "present": True}
    first = state["dev"]
    sup = Supervisor(_ctl(FakeClock()), lambda: ["/dev/mock"] if state["present"] else [],
                     lambda port: state["dev"], default_renderer_factory(cfg), cfg, retry_s=0.05)
    stop = threading.Event()
    th = threading.Thread(target=sup.run, args=(stop,), daemon=True)
    th.start()
    try:
        assert wait_until(lambda: sup.stats.connects == 1)
        link1 = sup.daemon.link
        assert wait_until(lambda: len(first.heartbeats) >= 2)
        assert link1.ui_negotiated
        first.unplugged, state["present"] = True, False
        assert wait_until(lambda: not link1._beat.is_alive())
        n1 = len(first.heartbeats)
        second = MockDevice(ui=True, heartbeat_ms=400)
        state["dev"], state["present"] = second, True
        assert wait_until(lambda: sup.stats.connects == 2)
        link2 = sup.daemon.link
        assert link2 is not link1 and link2._beat is not link1._beat
        assert wait_until(lambda: len(second.heartbeats) >= 2)
        assert len(second.hellos) == 1 and len(first.heartbeats) == n1
        names = [t for t in threading.enumerate() if t.name == "mk20-ui-heartbeat" and t.is_alive()]
        assert link2._beat in names and link1._beat not in names
    finally:
        stop.set()
        th.join(5)
    assert not th.is_alive()
    assert wait_until(lambda: not link2._beat.is_alive(), timeout=3)
