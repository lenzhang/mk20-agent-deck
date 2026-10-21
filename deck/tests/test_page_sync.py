"""Page sync (2026-10-22): the keyboard's L1/L4 route follows the Mac controller's page (``ui.page``).

The keyboard owns K2 short (route toggle + K2 tap), the L4 回 L1 key and K1 short on L4; the Mac changes its page by
itself (MODE long press, 回 L1 / mode keys, knob holds).  Protocol builder, link (MockDevice models the keyboard
route), daemon sync (fake clock), legacy firmware, reconnect.  The real-binary case is test_page_sync_e2e.py.
"""
import logging
import threading
import time

import pytest

from mk20deck import protocol as P
from mk20deck.config import DEFAULT_KNOB_MAP, DeckConfig
from mk20deck.controller import PAGE_RESYNC, Controller
from mk20deck.daemon import Daemon
from mk20deck.link import DeviceLink
from mk20deck.model import Mode, State
from mk20deck.render.canvas import Renderer
from mk20deck.sim import FakeClock, demo_adapters, option_adapters
from mk20deck.slots import N_SLOTS
from mk20deck.controller import slot_pos
from mk20deck.supervisor import Supervisor, default_renderer_factory
from mk20deck.transport import MockDevice

MODE_KEY, BACK_KEY = (5, 4), (5, 1)  # document coordinates
UNAVAILABLE = MockDevice.UI_REJECT


def wait_for(pred, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.005)
    return pred()


def linked(dev):
    link = DeviceLink(dev, ack_timeout=1.0, preamble_flush=0)
    link.start()
    link.handshake()
    return link


@pytest.fixture
def links():
    made = []
    yield made
    for link in made:
        link.close()


def rig(links, dev=None, option=False):
    """Daemon + controller on a fake clock, linked to ``dev`` (default: fw 1.5.1 MockDevice on L1)."""
    dev = dev or MockDevice(ui=True, hid_keys=True)
    clk = FakeClock()
    if option:
        c = Controller(DeckConfig(), option_adapters(), {"mode": "tmux"}, clock=clk, wall=lambda: 1.0, sim=True)
    else:
        c = Controller(DeckConfig(), demo_adapters(), clock=clk, wall=lambda: 1.0, sim=True)
    c.refresh()
    link = linked(dev)
    links.append(link)
    d = Daemon(c, Renderer(link.layout, animations=False), link, clock=clk, knob_map=dict(DEFAULT_KNOB_MAP))
    link.on_key = d.on_device_key
    d.bg_poll_s = 0
    d.step(force=True)  # connect: tell the keyboard our page (L1) once
    return d, c, clk, dev, link


def pages(dev):
    return dev.pages_accepted


def long_press_mode(d, clk):
    d.inject_key(*MODE_KEY, True)
    d.step()
    clk.advance(0.7)
    d.step()  # the long press fires in tick() -> L4 -> ui.page in the same step
    d.inject_key(*MODE_KEY, False)
    d.step()


def tap(d, clk, col, row):
    d.inject_key(col, row, True)
    clk.advance(0.05)
    d.inject_key(col, row, False)
    d.step()


def device_events(d, n=2):
    assert wait_for(lambda: d.events.qsize() >= n)
    d.step()


# ------------------------------------------------------------------ protocol + link
def test_protocol_ui_page_builder():
    assert P.ui_page("L4", 7) == {"method": "ui.page", "generation": 7, "page": "L4"}  # flat, no "parameters"
    assert "ui.page" in P.ALLOWED_METHODS and "ui.page" not in P.WRITE_METHODS and P.PAGES == ("L1", "L4")
    assert P.ui_page("L1", 999_999_999)["generation"] == 999_999_999
    for page, gen in (("LS", 1), ("HOME", 1), ("l1", 1), (None, 1), ("L1", 0), ("L1", 1_000_000_000), ("L1", True)):
        with pytest.raises(ValueError):
            P.ui_page(page, gen)


def test_link_ui_page_generation_seeded_from_hello_and_rejects(links):
    dev = MockDevice(ui=True, hid_keys=True)
    dev.ui_generation, dev.hid_generation = 41, 7  # an earlier deck process: continue above both counters
    link = linked(dev)
    links.append(link)
    assert link.supports("ui.page") and link.ui_generation == 41
    assert link.ui_page("L4") == "ok" and dev.route == "L4" and dev.ui_generation == 42 and link.pages_sent == 1
    assert link.ui_hid_keys({}) == "ok" and dev.hid_generation == 43  # one counter, both stay increasing
    assert link.ui_page("L4") == "ok" and dev.ui_generation == 44  # same page: accepted (keyboard redraws)
    dev.page_live = False  # PROBING / SUSPECT
    assert link.ui_page("L1") == "rejected" and dev.route == "L4" and link.ui_page_error == UNAVAILABLE
    dev.page_live, dev.route = True, "HOME"  # the keyboard's own page: never claimed
    assert link.ui_page("L1") == "rejected" and dev.route == "HOME"
    dev.route, dev.ui_generation = "L1", 10 ** 6  # someone else advanced it: stale generation refused
    assert link.ui_page("L4") == "rejected" and dev.route == "L1" and link.pages_rejected == 3
    assert link.ui_hello() and link.ui_generation == 10 ** 6  # a hello re-seeds
    assert link.ui_page("L4") == "ok" and dev.ui_generation == 10 ** 6 + 1
    assert all(set(r) == {"method", "generation", "page"} for r in dev.page_requests)


@pytest.mark.parametrize("fw", ["stock", "no-capability", "not-negotiated"])
def test_link_ui_page_never_sent_without_the_capability(links, fw):
    dev = MockDevice(ui=fw != "stock")
    link = linked(dev)
    links.append(link)
    if fw == "no-capability":
        link.ui_hello_result["capabilities"] = ["ui.heartbeat"]
    if fw == "not-negotiated":
        link.ui_negotiated = False
    assert not link.supports("ui.page") and link.ui_page("L4") == "unsupported"
    assert not any(r.get("method") == "ui.page" for r in dev.json_requests)


# ------------------------------------------------------------------ daemon: Mac-initiated changes
def test_connect_tells_the_keyboard_our_page_once(links):
    d, c, clk, dev, link = rig(links)
    assert pages(dev) == ["L1"] and d._page_acked == "L1" and c.device_pages
    for _ in range(3):
        clk.advance(5)
        d.step()
    assert len(dev.page_requests) == 1  # coalesced: nothing new to say


def test_mode_long_press_sends_ui_page_l4_before_the_l4_frame(links):
    dev = MockDevice(ui=True, hid_keys=True)
    order = []
    orig = dev._page
    dev._page = lambda obj: (order.append((obj["page"], len(dev.frames))), orig(obj))[1]
    d, c, clk, dev, link = rig(links, dev)
    d.inject_key(*MODE_KEY, True)
    d.step()
    frames0 = len(dev.frames)
    clk.advance(0.7)
    d.step()  # long press -> L4 -> ui.page, then the L4 frame
    assert c.page == "L4" and dev.route == "L4" and pages(dev) == ["L1", "L4"]
    assert order[-1] == ("L4", frames0) and len(dev.frames) == frames0 + 1
    d.inject_key(*MODE_KEY, False)  # the keyboard released fb20 at its boundary already: nothing else happens
    d.step()
    assert c.page == "L4" and pages(dev) == ["L1", "L4"]


def test_back_l1_mode_select_and_k2_send_the_page(links):
    d, c, clk, dev, link = rig(links)
    long_press_mode(d, clk)
    tap(d, clk, *BACK_KEY)  # the Mac's 回 L1 (reaches the Mac only when the keyboard is not on L4)
    assert c.page == "L1" and dev.route == "L1"
    long_press_mode(d, clk)
    tap(d, clk, 3, 1)  # L4 row 1 TMUX: switch_mode -> the Mac shows L1 (was a silent desync: keyboard stayed L4)
    assert c.mode == Mode.TMUX and c.page == "L1" and dev.route == "L1"
    d.inject_knob(2, "down")  # a forwarded K2 tap (holds are the keyboard's own on this firmware)
    d.inject_knob(2, "up")
    d.step()
    assert c.page == "L4" and dev.route == "L4"
    assert pages(dev) == ["L1", "L4", "L1", "L4", "L1", "L4"]


def test_mode_switch_on_l1_is_coalesced(links):
    d, c, clk, dev, link = rig(links)
    n = len(dev.page_requests)
    tap(d, clk, *MODE_KEY)  # MODE short: next mode, page stays L1
    assert c.mode != Mode.DESKTOP and c.page == "L1" and len(dev.page_requests) == n


# ------------------------------------------------------------------ daemon: keyboard-initiated changes
def test_k2_from_keyboard_toggles_the_mac_and_confirms_the_absolute_page(links):
    d, c, clk, dev, link = rig(links)
    frames0 = len(dev.frames)
    dev.tap_k2()  # keyboard: L1 -> L4 + K2 tap
    device_events(d)
    assert c.page == "L4" and dev.route == "L4" and pages(dev)[-1] == "L4"
    assert len(dev.frames) > frames0
    dev.back_l1()  # keyboard L4 回 L1: route L1 + K2 tap
    device_events(d)
    assert c.page == "L1" and dev.route == "L1" and pages(dev)[-1] == "L1"


def test_bug_mode_long_press_then_k2_and_back_l1_stay_in_sync(links):
    """The reported bug: after a MODE long press the Mac showed L4 while the keyboard stayed on L1, so K2 inverted
    them and the keyboard took logical key 5 as 回 L1 while the Mac showed session 5 there."""
    d, c, clk, dev, link = rig(links)
    long_press_mode(d, clk)
    assert (c.page, dev.route) == ("L4", "L4")
    dev.tap_k2()
    device_events(d)
    assert (c.page, dev.route) == ("L1", "L1")
    dev.tap_k2()
    device_events(d)
    assert (c.page, dev.route) == ("L4", "L4")
    dev.back_l1()
    device_events(d)
    assert (c.page, dev.route) == ("L1", "L1")


def test_residual_desync_heals_on_the_next_k2(links):
    d, c, clk, dev, link = rig(links)
    dev.route = "L4"  # e.g. a K2 tap lost on the way: keyboard L4, Mac L1 (and acked L1)
    dev.tap_k2()      # keyboard L4 -> L1, tap
    device_events(d)
    assert c.page == "L4" and dev.route == "L4"  # the Mac toggled what the user saw, the keyboard follows


def test_k1_on_l4_resyncs_instead_of_opening_the_mac_settings_page(links):
    d, c, clk, dev, link = rig(links)
    long_press_mode(d, clk)
    dev.route = "L1"  # desync: the keyboard forwards K1 only from its L1 route
    frames0, n = len(dev.frames), len(dev.page_requests)
    dev.press(100, 100, True)
    dev.press(100, 100, False)
    device_events(d)
    assert c.page == "L4" and not c.settings.active and c.toast[0] == PAGE_RESYNC
    assert dev.route == "L4" and len(dev.page_requests) == n + 1 and len(dev.frames) > frames0  # re-sent though acked


# ------------------------------------------------------------------ daemon: refusals, legacy, reconnect
def test_rejected_page_retries_with_backoff_and_logs_once(links, caplog):
    d, c, clk, dev, link = rig(links)
    dev.page_live = False  # e.g. the keyboard is not ONLINE (yet)
    caplog.set_level(logging.INFO, logger="mk20deck.daemon")
    long_press_mode(d, clk)
    assert c.page == "L4" and dev.route == "L1"
    sent = [len(dev.page_requests)]
    for dt in (0.4, 0.1, 0.9, 0.1, 1.9, 0.1, 1.9, 0.1, 1.9, 0.1):  # back-off 0.5, 1, 2, 2 (cap)
        clk.advance(dt)
        d.step()
        sent.append(len(dev.page_requests))
    assert sent == [sent[0] + i for i in (0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5)]
    assert d._page_wait(clk()) == pytest.approx(2.0)  # the loop waits for the retry, never spins
    msgs = [r.getMessage() for r in caplog.records if r.name == "mk20deck.daemon" and "ui.page" in r.getMessage()]
    assert msgs == [f"ui.page L4 not accepted (rejected: {UNAVAILABLE}): retrying every <= 2.0 s"]
    dev.page_live = True
    clk.advance(2.0)
    d.step()
    assert dev.route == "L4" and d._page_acked == "L4"
    assert [r.getMessage() for r in caplog.records if r.name == "mk20deck.daemon"][-1] == "ui.page L4 accepted"


def test_new_page_while_failing_goes_out_at_once_and_a_return_to_the_acked_page_sends_nothing(links):
    d, c, clk, dev, link = rig(links)
    dev.page_live = False
    long_press_mode(d, clk)  # L4 refused: the keyboard stays on the L1 it confirmed
    n = len(dev.page_requests)
    tap(d, clk, *BACK_KEY)  # back to L1 before any retry: already confirmed, nothing to send
    assert c.page == "L1" and dev.route == "L1" and len(dev.page_requests) == n
    clk.advance(10)
    d.step()
    assert len(dev.page_requests) == n and d._page_wait(clk()) is None  # the failing L4 streak is over
    d.page_retry_s = 5.0
    assert link.ui_hello()  # new epoch: L1 goes out, refused -> 5 s back-off running
    d.step()
    assert len(dev.page_requests) == n + 1 and d._page_acked is None
    dev.page_live = True
    long_press_mode(d, clk)  # a new target does not wait for that back-off
    assert c.page == "L4" and dev.route == "L4" and len(dev.page_requests) == n + 2


def test_keyboard_answering_unsupported_stops_until_the_next_hello(links):
    d, c, clk, dev, link = rig(links)
    dev._page = lambda obj: dev.page_requests.append(obj)  # noqa: E731 — any refusal...
    dev.UI_REJECT = "unsupported"                           # ... worded like firmware without the method
    long_press_mode(d, clk)
    n = len(dev.page_requests)
    clk.advance(10)
    d.step()
    assert len(dev.page_requests) == n and not c.device_pages and d._page_wait(clk()) is None


@pytest.mark.parametrize("fw", ["stock", "no-capability"])
def test_legacy_firmware_sends_nothing_and_keeps_the_mac_settings_page(links, fw):
    dev = MockDevice(ui=fw != "stock")
    if fw == "no-capability":
        orig = dev._ui
        def ui(method, obj):
            res = orig(method, obj)
            if method == "ui.hello" and res:
                res["capabilities"] = ["ui.heartbeat"]
            return res
        dev._ui = ui
    d, c, clk, dev, link = rig(links, dev)
    long_press_mode(d, clk)
    assert c.page == "L4" and not c.device_pages
    d.inject_knob(1, "down")
    d.inject_knob(1, "up")
    d.step()
    assert c.page == "LS" and c.settings.active  # legacy: SETTINGS.md K1 on L4
    assert not any(r.get("method") == "ui.page" for r in dev.json_requests)
    assert d._page_wait(clk()) is None


def test_rehello_resends_the_page_with_a_generation_above_the_device(links):
    d, c, clk, dev, link = rig(links)
    long_press_mode(d, clk)
    n = len(dev.page_requests)
    dev.route, dev.ui_generation = "L1", 5000  # e.g. the keyboard rebooted into auto-L1 under another host's counter
    assert link.ui_hello()
    assert d.next_wait() == pytest.approx(0.005)  # wake at once for it
    d.step()
    assert dev.route == "L4" and dev.page_requests[n]["generation"] == 5001 and pages(dev)[-1] == "L4"


def test_reconnect_pushes_the_persisted_page_to_the_new_device():
    cfg = DeckConfig()
    cfg.preamble_flush, cfg.ack_timeout_s, cfg.heartbeat_s, cfg.poll_interval_s = 0, 0.5, 0.05, 0.5
    c = Controller(cfg, demo_adapters(), sim=True)
    c.page = "L4"  # the controller outlives connections
    state = {"dev": MockDevice(ui=True, hid_keys=True), "present": True}
    first = state["dev"]
    first.ui_generation = 30
    sup = Supervisor(c, lambda: ["/dev/mock"] if state["present"] else [], lambda port: state["dev"],
                     default_renderer_factory(cfg), cfg, retry_s=0.05)
    stop = threading.Event()
    th = threading.Thread(target=sup.run, args=(stop,), daemon=True)
    th.start()
    try:
        assert wait_for(lambda: first.route == "L4", 5)
        assert first.page_requests[0]["generation"] == 31
        first.unplugged, state["present"] = True, False
        second = MockDevice(ui=True, hid_keys=True)  # rebooted: auto-L1
        state["dev"], state["present"] = second, True
        assert wait_for(lambda: second.route == "L4", 5) and second.page_requests[0]["generation"] == 1
    finally:
        stop.set()
        th.join(5)
    assert not th.is_alive()


# ------------------------------------------------------------------ redraw + option mode ordering
def test_resync_forces_a_full_frame_after_the_page(links):
    dev = MockDevice(ui=True, hid_keys=True)
    order = []
    orig = dev._page
    dev._page = lambda obj: (order.append(len(dev.frames)), orig(obj))[1]
    d, c, clk, dev, link = rig(links, dev)
    d.step(force=True)
    frames0, version = len(dev.frames), c.version
    c.page_sync += 1  # a resync without a visible change (no version bump): the keyboard still needs a frame
    assert c.version == version
    d.step()
    assert order[-1] == frames0 and len(dev.frames) == frames0 + 1


def where(c, title):
    for i in range(N_SLOTS):
        s = c.mgr.session_at(i)
        if s is not None and s.title == title:
            return slot_pos(i)
    raise LookupError(title)


def enter_option(d, c, clk):
    s = next(s for s in c.mgr.sessions.values() if s.hooked and s.state == State.NEEDS_INPUT and s.options)
    d.step(force=True)
    tap(d, clk, *where(c, s.title))
    assert c.option is not None and dev_map(d) == c.hid_keys_wanted() != {}


def dev_map(d):
    return d.link.t.hid_map


def test_option_mode_map_is_resent_after_a_page_boundary_in_order(links):
    dev = MockDevice(ui=True, hid_keys=True)
    log_ = []
    p0, h0 = dev._page, dev._hid_keys
    dev._page = lambda obj: (log_.append(("page", obj["page"], len(dev.frames))), p0(obj))[1]
    dev._hid_keys = lambda obj: (log_.append(("hid", len(obj["keys"]), len(dev.frames))), h0(obj))[1]
    d, c, clk, dev, link = rig(links, dev, option=True)
    enter_option(d, c, clk)
    want = c.hid_keys_wanted()
    del log_[:]
    assert link.ui_hello()  # e.g. a re-hello: the keyboard dropped the map; our page goes out again (L1)
    frames0 = len(dev.frames)
    d.step()
    assert c.option is not None and dev.route == "L1" and dev.hid_map == want
    assert log_ == [("page", "L1", frames0), ("hid", len(want), frames0)] and len(dev.frames) == frames0 + 1
    del log_[:]
    dev.tap_k2()  # leaving L1 ends option mode; the boundary already cleared the map: no extra clear needed
    device_events(d)
    assert c.option is None and c.page == "L4" and dev.route == "L4" and dev.hid_map == {}
    assert [e[:2] for e in log_] == [("page", "L4")]


# ------------------------------------------------------------------ knob taps on ui.page firmware (2026-10-22)
def knob_tap(d, clk, knob, delay):
    """Forwarded knob down ... up, the up processed ``delay`` s later (e.g. the loop stalled on a slow poll)."""
    d.inject_knob(knob, "down")
    d.step()
    clk.advance(delay)
    d.step()  # tick() runs here: on legacy firmware a hold would fire now
    d.inject_knob(knob, "up")
    d.step()


@pytest.mark.parametrize("delay", [0.05, 0.7, 3.0])
def test_delayed_k2_release_still_toggles_exactly_once(links, delay):
    d, c, clk, dev, link = rig(links)
    knob_tap(d, clk, 2, delay)
    assert c.page == "L4" and dev.route == "L4" and pages(dev) == ["L1", "L4"]
    knob_tap(d, clk, 2, delay)
    assert c.page == "L1" and dev.route == "L1" and pages(dev) == ["L1", "L4", "L1"]


def test_no_knob_long_press_page_change_on_ui_page_firmware(links):
    d, c, clk, dev, link = rig(links)
    d.inject_knob(1, "down")
    d.step()
    for _ in range(4):
        clk.advance(0.5)
        d.step()
        assert c.page == "L1" and pages(dev) == ["L1"]  # a legacy K1 hold would have gone to L4 at 0.6 s
    d.heartbeat, d._next_poll = 1e9, 1e12
    assert d.next_wait() > 1.0  # no 75 ms long-press wake-ups while waiting for a tap's up
    d.inject_knob(1, "up")
    d.step()  # the tap: K1 on L1 = focus the selection
    assert c.page == "L1" and c.toast[0] == "未选中会话 · 转 K1 选择" and pages(dev) == ["L1"]
    long_press_mode(d, clk)
    knob_tap(d, clk, 2, 2.0)  # a stalled K2 on L4 is a toggle, never the legacy "hold = 回到 L1" plus nothing
    assert c.page == "L1" and dev.route == "L1" and pages(dev) == ["L1", "L4", "L1"]


def test_controller_knob_timing_ui_page_firmware_vs_legacy():
    clk = FakeClock()
    c = Controller(DeckConfig(), demo_adapters(), clock=clk, wall=lambda: 1.0, sim=True)
    c.device_pages = True
    c.on_knob(2, "down")
    clk.advance(2.0)
    c.tick()
    assert c.page == "L1" and not c.long_press_pending()
    c.on_knob(2, "up")
    assert c.page == "L4"
    c.device_pages = False  # legacy: a hold is a hold, a late up is no tap
    c.on_knob(2, "down")
    assert c.long_press_pending()
    clk.advance(0.7)
    c.tick()
    assert c.page == "L1" and c.log[-1] == "回到 L1"
    c.on_knob(2, "up")
    assert c.page == "L1"
    c.on_knob(1, "down")
    clk.advance(0.7)
    c.tick()
    c.on_knob(1, "up")
    assert c.page == "L4" and not c.settings.active


@pytest.mark.parametrize("fw", ["stock", "no-capability"])
def test_legacy_firmware_keeps_knob_hold_timing(links, fw):
    dev = MockDevice(ui=fw != "stock")
    if fw == "no-capability":
        orig = dev._ui
        def ui(method, obj):
            res = orig(method, obj)
            if method == "ui.hello" and res:
                res["capabilities"] = ["ui.heartbeat"]
            return res
        dev._ui = ui
    d, c, clk, dev, link = rig(links, dev)
    knob_tap(d, clk, 1, 0.7)  # K1 hold: L4, the late up does nothing more
    assert c.page == "L4" and not c.settings.active
    knob_tap(d, clk, 2, 0.7)  # K2 hold: back to L1
    assert c.page == "L1"
    knob_tap(d, clk, 2, 0.05)  # K2 tap: toggle
    assert c.page == "L4"
    d.inject_knob(1, "down")
    d.step()
    d.heartbeat, d._next_poll = 1e9, 1e12
    assert d.next_wait() == pytest.approx(0.6 / 8)  # legacy hold detection still wakes up
    assert not any(r.get("method") == "ui.page" for r in dev.json_requests)
