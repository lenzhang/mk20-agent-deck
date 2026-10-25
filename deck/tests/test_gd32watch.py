"""GD32 stuck in its Maple DFU bootloader (incident 2026-10-25 14:08): read-only ioreg detector, the keyboard banner,
the arrange-page status.  Fixtures: the incident's ``ioreg -p IOUSB -l -w0`` text dump (verbatim) and a minimal plist
(``-a``) of the same registry, captured on the same Mac in the same state (other devices' serials dropped)."""
import copy
import logging
import plistlib
import subprocess
import threading
import time
from pathlib import Path

import pytest
from PIL import ImageDraw, ImageFont

from mk20deck.config import DeckConfig
from mk20deck.controller import GD32_DFU_ALERT, GD32_DFU_DETAIL, GD32_DFU_WEB, Controller
from mk20deck.daemon import Daemon
from mk20deck.hw import gd32watch as gw
from mk20deck.hw.gd32watch import Gd32DfuWatch, find_stuck, parse_ioreg_plist
from mk20deck.hw.usbenum import KNOWN, mk20_summary, parse_ioreg
from mk20deck.layout import DeviceLayout
from mk20deck.render.canvas import Renderer
from mk20deck.render.fonts import fit, font
from mk20deck.render.subscreen import ALERT_RED, W, render_sub
from mk20deck.rules import RulesStore
from mk20deck.sim import FakeClock, _ctl, full_adapters
from mk20deck.supervisor import Supervisor
from mk20deck.webui import ArrangeService, LoopCalls

FIX = Path(__file__).parent / "fixtures"
PLIST = (FIX / "ioreg-usb-20261025-maple.plist").read_bytes()
TEXT = (FIX / "ioreg-usb-20261025-maple.txt").read_text()
MAPLE_LOC, T113_LOC, HUB_LOC = 0x00142000, 0x00141000, 0x00140000  # "Maple 003@00142000", "Serial@00141000"


def _tree() -> dict:
    return plistlib.loads(PLIST)


def _walk(node):
    yield node
    for c in node.get("IORegistryEntryChildren", []):
        yield from _walk(c)


def _find(root, loc):
    return next(n for n in _walk(root) if n.get("locationID") == loc)


def _parent(root, loc):
    return next(n for n in _walk(root) if any(c.get("locationID") == loc for c in n.get("IORegistryEntryChildren", [])))


def variant(fn) -> bytes:
    root = copy.deepcopy(_tree())
    fn(root)
    return plistlib.dumps(root)


def normal(root):  # the healthy keyboard: the GD32 application "syk_keyboards" (4250:426f) instead of the bootloader
    n = _find(root, MAPLE_LOC)
    n.update({"IORegistryEntryName": "syk_keyboards", "USB Product Name": "syk_keyboards", "USB Vendor Name": "SYK",
              "idVendor": 0x4250, "idProduct": 0x426F, "sessionID": 1})
    n.pop("kUSBSerialNumberString"), n.pop("USB Serial Number")


def no_t113(root):  # the T113 off the bus for a moment (ECM re-arm) or not booted yet
    hub = _parent(root, T113_LOC)
    hub["IORegistryEntryChildren"] = [c for c in hub["IORegistryEntryChildren"] if c["locationID"] != T113_LOC]


def maple_elsewhere(root):  # some other Maple board on another hub, the MK20 healthy
    normal(root)
    other = _find(root, 0x00110000)  # the empty "USB2.0 Hub@00110000"
    other.setdefault("IORegistryEntryChildren", []).append(
        {"IORegistryEntryName": "Maple 003", "USB Product Name": "Maple 003", "idVendor": 7855, "idProduct": 3,
         "locationID": 0x00111000, "sessionID": 7})


# ------------------------------------------------------------------ parser on the incident registry
def test_incident_plist_parses_and_finds_the_stuck_gd32():
    nodes = parse_ioreg_plist(PLIST)
    by_loc = {n.location: n for n in nodes}
    maple, t113 = by_loc[MAPLE_LOC], by_loc[T113_LOC]
    assert (maple.vid, maple.pid, maple.name, maple.vendor, maple.serial) == (0x1EAF, 0x0003, "Maple 003", "LeafLabs",
                                                                             "LLM 003")
    assert (t113.vid, t113.pid, t113.name, t113.vendor) == (0x1D6B, 0x0104, "Serial", "Allwinner Technology Inc.")
    assert nodes[maple.parent] is nodes[t113.parent] and nodes[maple.parent].location == HUB_LOC
    assert (nodes[maple.parent].vid, nodes[maple.parent].pid) == (0x05E3, 0x0610)  # the MK20's own GL852G hub
    hits, other, hubs = find_stuck(nodes)
    assert other == [] and hubs == {HUB_LOC} and len(hits) == 1
    h = hits[0]
    assert (h.location, h.hub, h.t113, h.session, h.serial) == (MAPLE_LOC, HUB_LOC, T113_LOC, 16810792315712, "LLM 003")
    assert "1eaf:0003" in h.describe() and "0x00142000" in h.describe() and "0x00141000" in h.describe()


def test_incident_text_dump_is_the_same_registry():
    """The verbatim text dump from the incident notes: the existing text parser (``usb-list`` fallback) flags the
    bootloader, at the location the plist fixture has, next to the T113 under the same hub."""
    devs = parse_ioreg(TEXT)
    dfu = [d for d in devs if (d.vid, d.pid) == (0x1EAF, 0x0003)]
    assert len(dfu) == 1 and dfu[0].serial == "LLM 003" and int(dfu[0].location, 16) == MAPLE_LOC
    for d in devs:
        d.role = KNOWN.get((d.vid, d.pid), "")
    assert mk20_summary(devs)["gd32_dfu"]
    assert "USB2.0 Hub@00140000" in TEXT and "Maple 003@00142000" in TEXT and "Serial@00141000" in TEXT
    assert '"sessionID" = 16810792315712' in TEXT and '"locationID" = 1318912' in TEXT
    maple = next(n for n in parse_ioreg_plist(PLIST) if n.location == MAPLE_LOC)
    assert maple.session == 16810792315712 and maple.location == 1318912


def test_healthy_keyboard_and_foreign_maple_are_not_stuck():
    assert find_stuck(parse_ioreg_plist(variant(normal)))[0] == []
    hits, other, hubs = find_stuck(parse_ioreg_plist(variant(maple_elsewhere)))
    assert hits == [] and [n.location for n in other] == [0x00111000] and hubs == {HUB_LOC}


def test_maple_on_a_hub_where_the_t113_was_counts_while_the_t113_is_away():
    nodes = parse_ioreg_plist(variant(no_t113))
    hits, other, hubs = find_stuck(nodes)
    assert hits == [] and len(other) == 1 and hubs == set()  # never seen a T113 there: not ours (yet)
    hits, other, _ = find_stuck(nodes, known_hubs={HUB_LOC})
    assert len(hits) == 1 and hits[0].t113 is None and other == []
    assert "where the MK20 T113 was" in hits[0].describe()


def test_parser_tolerates_odd_nodes_and_rejects_garbage():
    odd = plistlib.dumps({"IORegistryEntryChildren": [
        "not a dict", {"IORegistryEntryName": "x", "idVendor": "7855", "idProduct": True, "locationID": "0x1",
                       "IORegistryEntryChildren": "nope"}]})
    nodes = parse_ioreg_plist(odd)
    assert [(n.name, n.vid, n.pid, n.location) for n in nodes] == [("x", None, None, None)]
    assert find_stuck(nodes) == ([], [], set())
    for bad in (b"", b"<plist", TEXT.encode()):
        with pytest.raises(Exception):
            parse_ioreg_plist(bad)


# ------------------------------------------------------------------ the watch: confirm, clear, no spam, rate limit
class Probe:
    def __init__(self, data=PLIST):
        self.data = data
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if isinstance(self.data, BaseException):
            raise self.data
        return self.data


def _watch(probe, clk=None, **kw):
    clk = clk or FakeClock()
    return Gd32DfuWatch(probe, clock=clk, wall=lambda: 1792244000.0 + clk.t, **kw), clk


def _due(w, clk):
    clk.advance(w.interval_s)
    return w.poll()


def _records(caplog, level=None):
    return [r for r in caplog.records if r.name == gw.__name__ and (level is None or r.levelno == level)]


def test_watch_confirms_after_two_checks_and_warns_once(caplog):
    caplog.set_level(logging.DEBUG, logger=gw.__name__)
    p = Probe()
    w, clk = _watch(p)
    assert w.poll() is None and p.calls == 0  # nothing before first_s
    clk.advance(gw.FIRST_S)
    assert w.poll() is False and not w.stuck and w.seen == 1  # one sighting: a normal power-on shows it ~1 s
    assert _due(w, clk) is True and w.stuck and w.status()["location"] == "0x00142000"
    warns = _records(caplog, logging.WARNING)
    assert len(warns) == 1 and "Maple DFU bootloader" in warns[0].getMessage()
    assert "Replug the MK20 USB cable" in warns[0].getMessage() and "1eaf:0003" in warns[0].getMessage()
    for _ in range(20):  # stays stuck: never another line
        assert _due(w, clk) is True
    assert len(_records(caplog)) == 1 and p.calls == 22


def test_watch_clears_when_the_bootloader_is_gone_and_logs_once(caplog):
    caplog.set_level(logging.DEBUG, logger=gw.__name__)
    p = Probe()
    w, clk = _watch(p, first_s=0.0)
    w.poll(), _due(w, clk)
    assert w.stuck
    p.data = variant(normal)  # replugged: "syk_keyboards" is back
    assert _due(w, clk) is False and not w.stuck and w.seen == 0 and w.since is None
    infos = _records(caplog, logging.INFO)
    assert len(infos) == 1 and "left its DFU bootloader" in infos[0].getMessage()
    for _ in range(5):
        assert _due(w, clk) is False
    assert len(_records(caplog)) == 2  # one WARNING + one INFO in total
    p.data = PLIST  # stuck again later: needs two sightings again, then one new WARNING
    assert _due(w, clk) is False and _due(w, clk) is True
    assert len(_records(caplog, logging.WARNING)) == 2


def test_single_sighting_between_healthy_checks_never_alerts(caplog):
    caplog.set_level(logging.DEBUG, logger=gw.__name__)
    p = Probe(variant(normal))
    w, clk = _watch(p, first_s=0.0)
    for data in (PLIST, variant(normal), PLIST, variant(normal), PLIST, variant(normal)):
        p.data = data
        assert _due(w, clk) is False
    assert not w.stuck and _records(caplog) == []


def test_foreign_maple_logged_once_and_never_alerts(caplog):
    caplog.set_level(logging.DEBUG, logger=gw.__name__)
    w, clk = _watch(Probe(variant(maple_elsewhere)), first_s=0.0)
    for _ in range(5):
        _due(w, clk)
    assert not w.stuck
    recs = _records(caplog)
    assert len(recs) == 1 and recs[0].levelno == logging.INFO and "not on an MK20 hub" in recs[0].getMessage()


def test_t113_away_for_a_moment_keeps_the_alert():
    p = Probe()
    w, clk = _watch(p, first_s=0.0)
    w.poll(), _due(w, clk)
    p.data = variant(no_t113)  # ECM re-arm: the T113 re-enumerates, the GD32 stays in its bootloader
    assert _due(w, clk) is True and w.stuck and w.sighting.t113 is None


def test_rate_limit_at_most_one_probe_per_interval():
    p = Probe()
    w, clk = _watch(p, first_s=0.0)
    assert 30.0 <= gw.INTERVAL_S <= 60.0 and w.interval_s == gw.INTERVAL_S
    for _ in range(100):
        w.poll()
    assert p.calls == 1
    clk.advance(w.interval_s - 0.1)
    for _ in range(100):
        w.poll()
    assert p.calls == 1
    clk.advance(0.1)
    w.poll(), w.poll()
    assert p.calls == 2 and w.checks == 2


@pytest.mark.parametrize("err", [OSError("ioreg exited 1"), subprocess.TimeoutExpired(["ioreg"], 5.0),
                                 FileNotFoundError("ioreg"), ValueError("bad plist")])
def test_probe_failures_never_raise_and_log_once_at_debug(caplog, err):
    caplog.set_level(logging.DEBUG, logger=gw.__name__)
    p = Probe(err)
    w, clk = _watch(p, first_s=0.0)
    for _ in range(5):
        assert (w.poll() if p.calls == 0 else _due(w, clk)) is None
    recs = _records(caplog)
    assert p.calls == 5 and w.failures == 5 and len(recs) == 1 and recs[0].levelno == logging.DEBUG
    p.data = variant(normal)
    assert _due(w, clk) is False  # a success resets the "failing" streak ...
    p.data = err
    _due(w, clk), _due(w, clk)
    assert len(_records(caplog)) == 2  # ... so the next streak is noted once more (still debug)
    assert all(r.levelno == logging.DEBUG for r in _records(caplog))


def test_failure_is_no_information_the_alert_stays():
    p = Probe()
    w, clk = _watch(p, first_s=0.0)
    w.poll(), _due(w, clk)
    p.data = b"garbage"
    assert _due(w, clk) is None and w.stuck  # an unreadable registry never clears (or sets) the notice


def test_ioreg_probe_is_bounded_and_raises_on_failure(monkeypatch):
    seen = {}

    def fake_run(argv, **kw):
        seen.update(argv=argv, **kw)
        return subprocess.CompletedProcess(argv, 0, PLIST, b"")

    monkeypatch.setattr(gw.subprocess, "run", fake_run)
    assert gw.ioreg_probe() == PLIST
    assert seen["argv"][1:] == ["-p", "IOUSB", "-l", "-w0", "-a"] and seen["argv"][0].endswith("ioreg")
    assert seen["timeout"] == gw.TIMEOUT_S and seen["capture_output"] and seen["stdin"] == subprocess.DEVNULL
    monkeypatch.setattr(gw.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 1, b"", b"x"))
    with pytest.raises(OSError):
        gw.ioreg_probe()

    def timeout(argv, **kw):
        raise subprocess.TimeoutExpired(argv, kw["timeout"])

    monkeypatch.setattr(gw.subprocess, "run", timeout)
    w = Gd32DfuWatch(first_s=0.0)  # the real probe, failing: poll() never raises
    assert w.poll() is None and w.failures == 1


def test_watch_thread_polls_and_stops():
    p = Probe()
    w = Gd32DfuWatch(p, interval_s=0.05, first_s=0.0)
    stop = threading.Event()
    th = w.start(stop)
    t0 = time.monotonic()
    while not w.stuck and time.monotonic() - t0 < 3:
        time.sleep(0.01)
    assert w.stuck and th.name == "mk20-gd32-watch" and th.daemon
    stop.set()
    th.join(2)
    assert not th.is_alive()
    n = p.calls
    time.sleep(0.15)
    assert p.calls == n


def test_default_watch_only_on_macos(monkeypatch):
    monkeypatch.setattr(gw.sys, "platform", "linux")
    assert gw.default_watch() is None
    monkeypatch.setattr(gw.sys, "platform", "darwin")
    w = gw.default_watch()
    assert isinstance(w, Gd32DfuWatch) and w.probe is gw.ioreg_probe and w.interval_s == gw.INTERVAL_S


# ------------------------------------------------------------------ keyboard banner (daemon -> controller -> render)
class FakeWatch:
    stuck = False


def _red(img) -> int:
    px = img.load()
    return sum(1 for x in range(0, W, 2) for y in range(92, 138, 2) if px[x, y] == ALERT_RED)


def test_daemon_shows_the_banner_on_l1_and_l4_and_clears_it():
    clk = FakeClock()
    c = _ctl(clk)
    fw = FakeWatch()
    d = Daemon(c, Renderer(DeviceLayout.default()), None, clock=clk, gd32=fw)
    d.step(force=True)
    sv = c.view().sub
    assert sv.alert == "" and _red(render_sub(sv, clk.t)) == 0
    v0 = c.version
    fw.stuck = True
    clk.advance(0.01)
    assert d.step() is True  # the change alone produces a frame (no heartbeat wait)
    sv = c.view().sub
    assert (sv.alert, sv.alert_detail) == (GD32_DFU_ALERT, GD32_DFU_DETAIL) and c.version == v0 + 1
    assert _red(render_sub(sv, clk.t)) > 1500
    c.on_knob(2, "down"), c.on_knob(2, "up")  # K2 short: L4 (as on legacy firmware)
    assert c.page == "L4" and c.view().sub.alert == GD32_DFU_ALERT and _red(render_sub(c.view().sub, clk.t)) > 1500
    for _ in range(5):  # persistent, and no extra frames or version bumps for an unchanged state
        clk.advance(0.01)
        v = c.version
        d.step()
        assert c.version == v and c.view().sub.alert == GD32_DFU_ALERT
    fw.stuck = False
    clk.advance(0.01)
    assert d.step() is True
    assert c.view().sub.alert == "" and _red(render_sub(c.view().sub, clk.t)) == 0


def test_real_watch_through_the_daemon_end_to_end():
    clk = FakeClock()
    c = _ctl(clk)
    p = Probe()
    w = Gd32DfuWatch(p, clock=clk, first_s=0.0)
    d = Daemon(c, Renderer(DeviceLayout.default()), None, clock=clk, gd32=w)
    frames = []
    d.sink = frames.append
    w.poll(), d.step(force=True)
    assert not c.gd32_dfu  # one sighting
    clk.advance(w.interval_s)
    w.poll(), d.step()
    assert c.gd32_dfu and c.view().sub.alert == GD32_DFU_ALERT and frames
    p.data = variant(normal)
    clk.advance(w.interval_s)
    w.poll(), d.step()
    assert not c.gd32_dfu and c.view().sub.alert == ""


def test_banner_keeps_the_header_and_lets_a_toast_show():
    clk = FakeClock()
    c = _ctl(clk)
    c.set_gd32_dfu(True)
    c._say("已切换到 tmux")
    img = render_sub(c.view().sub, clk.t)
    assert _red(img) > 1500
    plain = copy.copy(c.view().sub)
    plain.toast = None
    assert render_sub(plain, clk.t).crop((0, 46, W, 83)).tobytes() != img.crop((0, 46, W, 83)).tobytes()
    plain.alert = plain.alert_detail = ""
    assert render_sub(plain, clk.t).crop((0, 0, W, 24)).tobytes() == img.crop((0, 0, W, 24)).tobytes()


def _missing(f: ImageFont.FreeTypeFont, ch: str) -> bool:
    probe = f.getmask("\U000F0000")  # a private-use code point no CJK font has: the "missing glyph" rendering
    m = f.getmask(ch)
    return (m.size, bytes(m)) == (probe.size, bytes(probe))


@pytest.mark.parametrize("text,size,bold", [(GD32_DFU_ALERT, 14, True), (GD32_DFU_DETAIL, 12, False)])
def test_banner_text_has_glyphs_and_fits_the_sub_screen(text, size, bold):
    f = font(size, bold=bold)
    if not isinstance(f, ImageFont.FreeTypeFont):
        pytest.skip("no CJK font on this host")
    assert [ch for ch in text if not ch.isspace() and _missing(f, ch)] == []
    assert _missing(f, "✗")  # the check itself: ✗ is known to be missing (controller.py note)
    d = ImageDraw.Draw(render_sub(_ctl(FakeClock()).view().sub, 0.0))
    assert fit(d, text, f, W - 24) == text  # never truncated with "…"


# ------------------------------------------------------------------ status: arrange page + supervisor
def test_arrange_page_status_names_the_problem(tmp_path):
    ctl = Controller(DeckConfig(), full_adapters(), sim=True)
    ctl.refresh()
    svc = ArrangeService(ctl, LoopCalls(), RulesStore(str(tmp_path / "r.json")))
    snap = svc.snapshot(None)
    assert snap["gd32_dfu"] is False and snap["warnings"] == []
    ctl.set_gd32_dfu(True)
    snap = svc.snapshot(None)
    assert snap["gd32_dfu"] is True and snap["warnings"] == [GD32_DFU_WEB]
    assert "重新插拔键盘 USB 线" in GD32_DFU_WEB and "K2 急停" in GD32_DFU_WEB


def test_supervisor_hands_the_watch_on_and_syncs_while_unplugged():
    ctl = Controller(DeckConfig(), full_adapters(), sim=False)
    fw = FakeWatch()
    fw.stuck = True
    sup = Supervisor(ctl, lambda: [], lambda p: None, lambda: None, DeckConfig(), retry_s=0.05, calls=LoopCalls(),
                     gd32=fw)
    stop = threading.Event()
    th = threading.Thread(target=sup.run, args=(stop,), daemon=True)
    th.start()
    try:
        t0 = time.monotonic()
        while not ctl.gd32_dfu and time.monotonic() - t0 < 2:
            time.sleep(0.01)
        assert ctl.gd32_dfu
        fw.stuck = False
        t0 = time.monotonic()
        while ctl.gd32_dfu and time.monotonic() - t0 < 2:
            time.sleep(0.01)
        assert not ctl.gd32_dfu
    finally:
        stop.set()
        th.join(3)


def test_supervisor_passes_the_watch_to_every_connection():
    from mk20deck.supervisor import default_renderer_factory
    from mk20deck.transport import MockDevice
    cfg = DeckConfig()
    cfg.preamble_flush, cfg.ack_timeout_s, cfg.heartbeat_s, cfg.poll_interval_s = 0, 0.2, 0.05, 0.5
    state = {"dev": MockDevice(), "present": True}
    ctl = _ctl(FakeClock())
    fw = FakeWatch()
    fw.stuck = True
    sup = Supervisor(ctl, lambda: ["/dev/mock"] if state["present"] else [], lambda port: state["dev"],
                     default_renderer_factory(cfg), cfg, retry_s=0.05, gd32=fw)
    stop = threading.Event()
    th = threading.Thread(target=sup.run, args=(stop,), daemon=True)
    th.start()

    def until(pred, timeout=3.0):
        t0 = time.monotonic()
        while not pred() and time.monotonic() - t0 < timeout:
            time.sleep(0.01)
        return pred()

    try:
        assert until(lambda: sup.stats.connects == 1 and len(state["dev"].frames) >= 2)
        first = sup.daemon
        assert first.gd32 is fw and ctl.view().sub.alert == GD32_DFU_ALERT
        state["dev"].unplugged, state["present"] = True, False
        assert until(lambda: any("连接中断" in m for _, m in sup.stats.events))
        state["dev"], state["present"] = MockDevice(), True
        assert until(lambda: sup.stats.connects == 2 and len(state["dev"].frames) >= 1)
        assert sup.daemon is not first and sup.daemon.gd32 is fw and ctl.gd32_dfu
    finally:
        stop.set()
        th.join(3)
