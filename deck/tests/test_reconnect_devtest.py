import json
import os
import threading
import time

import pytest

from mk20deck.config import DeckConfig
from mk20deck.daemon import Daemon, WakeReconnect
from mk20deck.hw.devtest import DevTest, DevTestOptions, ScriptedPrompter, pattern
from mk20deck.hw.power import PowerMonitor
from mk20deck.hw.via import MockViaKeyboard, ViaClient
from mk20deck.layout import DeviceLayout
from mk20deck.link import DeviceLink
from mk20deck.render.canvas import Renderer
from mk20deck.sim import FakeClock, _ctl
from mk20deck.supervisor import Supervisor, default_renderer_factory
from mk20deck.transport import MockDevice


def wait_until(pred, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


# ------------------------------------------------------------------ layout base
def test_fw_base_inferred_from_get_info():
    one = DeviceLayout.default().with_base(1)
    lay = DeviceLayout.from_get_info(one.to_get_info())
    assert lay.fw_base == 1 and lay.extra["fw_base_inferred"] == 1
    assert lay.fw_to_doc(1, 1) == (1, 1) and lay.key_rect(5, 4).col == 5
    assert DeviceLayout.from_get_info(DeviceLayout.default().to_get_info()).fw_base == 0
    assert DeviceLayout.from_get_info(one.to_get_info(), fw_base=0).fw_base == 0  # explicit override


# ------------------------------------------------------------------ link loss
def _link(dev, **kw):
    link = DeviceLink(dev, preamble_flush=0, ack_timeout=0.1, **kw)
    link.start()
    link.handshake()
    return link


def _jpeg():
    return pattern(DeviceLayout.default(), "t")


def test_unplug_detected_by_reader_and_writer():
    dev = MockDevice()
    link = _link(dev)
    assert link.show_jpeg(_jpeg())
    dev.unplugged = True
    assert wait_until(lambda: not link.connected)
    assert "拔线" in link.lost_reason
    with pytest.raises(ConnectionError):
        link.show_jpeg(_jpeg())
    link.close()


def test_hung_device_declared_lost_after_ack_misses():
    dev = MockDevice()
    link = _link(dev, max_ack_misses=3)
    dev.hung = True
    for _ in range(3):
        assert not link.show_jpeg(_jpeg())
    assert not link.connected and "无 ACK" in link.lost_reason
    link.close()


# ------------------------------------------------------------------ daemon power
class FakePower:
    def __init__(self):
        self.queue = []
        self.last_sleep_s = 0.0

    def poll(self):
        q, self.queue = self.queue, []
        return q


def test_daemon_display_sleep_pauses_and_wake_reconnects():
    clk = FakeClock()
    ctl = _ctl(clk)
    frames = []
    pw = FakePower()
    d = Daemon(ctl, Renderer(DeviceLayout.default()), None, clock=clk, sink=frames.append, power=pw)
    d.step(force=True)
    pw.queue = ["display_sleep"]
    d.check_power()
    assert d.paused and len(frames) == 2  # offline "Mac 已休眠" frame pushed
    ctl.on_key(2, 1, True)  # a press in flight must not survive sleep
    clk.t += 5
    pw.queue = ["display_wake"]
    with pytest.raises(WakeReconnect):
        d.check_power()
    assert not d.paused and not ctl.presses


def test_paused_daemon_resends_sleep_frame():
    clk = FakeClock()
    frames = []
    pw = FakePower()
    d = Daemon(_ctl(clk), Renderer(DeviceLayout.default()), None, clock=clk, sink=frames.append, power=pw)
    pw.queue = ["display_sleep"]
    d.check_power()
    assert d.paused and len(frames) == 1
    stop = threading.Event()
    ticks = iter(range(100))

    class _Stop:
        def is_set(self):
            return next(ticks) >= 30

        def wait(self, t):
            clk.t += 1.0  # each paused iteration advances the fake clock by 1 s

    d._run(_Stop())
    assert 3 <= len(frames) <= 5  # ~ one "Mac 已休眠" frame per 10 s while paused, no live frames


class _DeadLink:
    connected = False
    lost_reason = "读取失败（拔线？）"

    def show_jpeg(self, jpeg):
        return False


def test_daemon_notices_link_loss_while_display_asleep():
    clk = FakeClock()
    d = Daemon(_ctl(clk), Renderer(DeviceLayout.default()), None, clock=clk, sink=lambda j: None, power=FakePower())
    d.paused = True
    d.link = _DeadLink()
    with pytest.raises(ConnectionError, match="拔线"):
        d._run(threading.Event())  # must not spin paused forever on a dead link (keyboard rebooted while away)


def test_new_connection_starts_paused_when_display_already_asleep():
    class AsleepPower(FakePower):
        display_asleep = True

    clk = FakeClock()
    frames = []
    d = Daemon(_ctl(clk), Renderer(DeviceLayout.default()), None, clock=clk, sink=frames.append, power=AsleepPower())
    stop = threading.Event()
    stop.set()
    d.run(stop)
    assert d.paused and len(frames) == 1  # only the "Mac 已休眠" frame, no live frames while asleep


# ------------------------------------------------------------------ supervisor
def test_supervisor_reconnects_after_unplug():
    cfg = DeckConfig()
    cfg.preamble_flush = 0
    cfg.ack_timeout_s = 0.2
    cfg.heartbeat_s = 0.05
    cfg.poll_interval_s = 0.5
    state = {"dev": MockDevice(), "present": True}
    ctl = _ctl(FakeClock())
    sup = Supervisor(ctl, lambda: ["/dev/mock"] if state["present"] else [],
                     lambda port: state["dev"], default_renderer_factory(cfg), cfg, retry_s=0.05)
    stop = threading.Event()
    th = threading.Thread(target=sup.run, args=(stop,), daemon=True)
    th.start()
    assert wait_until(lambda: sup.stats.connects == 1 and len(state["dev"].frames) >= 2)
    state["dev"].unplugged = True
    state["present"] = False
    assert wait_until(lambda: any("连接中断" in m for _, m in sup.stats.events))
    state["dev"] = MockDevice()
    state["present"] = True
    assert wait_until(lambda: sup.stats.connects == 2 and len(state["dev"].frames) >= 1)
    stop.set()
    th.join(3)
    assert not th.is_alive()
    assert state["dev"].frames  # offline frame on exit (best effort)


def test_supervisor_reports_busy_port_without_killing():
    cfg = DeckConfig()

    def busy(port):
        raise OSError(16, "Resource busy")

    sup = Supervisor(_ctl(FakeClock()), lambda: ["/dev/cu.usbmodem1"], busy, default_renderer_factory(cfg), cfg,
                     retry_s=0.01)
    stop = threading.Event()
    threading.Timer(0.2, stop.set).start()
    sup.run(stop)
    msgs = [m for _, m in sup.stats.events]
    assert len(msgs) == 1 and "ScreenKey" in msgs[0]


# ------------------------------------------------------------------ devtest
def _scripted(state, fw_base, power):
    def dev():
        return state["dev"]

    def tap(c, r, hold=0.05):
        dev().press(c, r, True)
        time.sleep(hold)
        dev().press(c, r, False)

    acts = {}
    for row in range(1, 5):
        for col in range(1, 6):
            acts[f"key:{col},{row}"] = (lambda c=col, r=row: tap(c - 1 + fw_base, r - 1 + fw_base))
    knob_cells = {"K1": 7, "K2": 8}
    for k, cell in knob_cells.items():
        acts[f"knob:{k}:cw"] = lambda cell=cell: [tap(cell, 0 + fw_base, 0.01) for _ in range(3)]
        acts[f"knob:{k}:ccw"] = lambda cell=cell: [tap(cell, 1 + fw_base, 0.01) for _ in range(3)]
        acts[f"knob:{k}:press"] = lambda k=k: dev().emit_json({"method": "knobPressed", "parameters": {"knob": k}})
    acts["ptt"] = lambda: tap(0 + fw_base, 3 + fw_base, 0.3)
    tc, tr = 2 + fw_base, 1 + fw_base

    def taps():
        for _ in range(5):
            tap(tc, tr, 0.08)
            time.sleep(0.05)

    def doubles():
        for _ in range(3):
            tap(tc, tr, 0.04)
            time.sleep(0.08)
            tap(tc, tr, 0.04)
            time.sleep(0.4)

    def longs():
        for _ in range(3):
            tap(tc, tr, 0.7)
            time.sleep(0.05)

    acts.update({"timing:tap": taps, "timing:double": doubles, "timing:long": longs})

    def unplug():
        state["dev"].unplugged = True
        state["present"] = False

    def replug():
        time.sleep(0.2)
        state["dev"] = MockDevice(DeviceLayout.default().with_base(fw_base))
        state["present"] = True

    acts["unplug"], acts["replug"] = unplug, replug
    acts["sleep"] = lambda: power.queue.append("system_wake") or setattr(power, "last_sleep_s", 42.0)
    return ScriptedPrompter(acts, answers={"display_visual": "y", "hid_leak": "n", "ptt_ime": "y"})


@pytest.mark.parametrize("fw_base", [0, 1])
def test_devtest_full_run_against_mock(tmp_path, fw_base):
    state = {"dev": MockDevice(DeviceLayout.default().with_base(fw_base)), "present": True}
    power = FakePower()
    kb = MockViaKeyboard(rows=4, cols=6)
    opts = DevTestOptions(fps_seconds=0.3, ack_frames=3, step_timeout=3.0, quiet_s=0.3, sleep=True)
    t = DevTest(opts, _scripted(state, fw_base, power), str(tmp_path),
                find_ports=lambda: ["/dev/mock"] if state["present"] else [],
                open_transport=lambda p: state["dev"],
                enumerate_usb=lambda: ([], "test"), via_open=lambda: ViaClient(kb), power=power)
    rep = t.run()
    s = rep["sections"]
    st = {k: v["status"] for k, v in s.items()}
    assert st["getinfo"] == "pass" and s["getinfo"]["fw_base_inferred"] == fw_base
    assert st["display"] == "pass" and s["display"]["ack"]["timeouts"] == 0
    assert s["display"]["wrong_resolution"]["device_errors"]
    assert st["keys"] == "pass" and s["keys"]["event_fw_base"] == fw_base, s["keys"]["missing"]
    assert st["knobs"] == "pass"
    km = s["knobs"]["suggested_knob_map"]
    assert km[f"7,{0 + fw_base}"] == "K1:cw" and km[f"8,{1 + fw_base}"] == "K2:ccw"
    assert "knobPressed" in s["knobs"]["results"]["knob:K1:press"]["methods"]
    assert st["hid_leak"] == "pass" and st["ptt"] == "pass" and s["ptt"]["hold_ms"] >= 250
    assert st["timing"] == "pass", s["timing"]
    assert st["reconnect"] == "pass" and s["reconnect"]["handshake_ready_ms"] is not None
    assert st["sleep"] == "pass" and s["sleep"]["slept_s"] == 42.0
    assert st["via"] == "pass" and s["via"]["layer0_has_ralt"] and kb.writes == []
    assert st["usb"] == "fail" and st["backlight"] == "skip"
    assert rep["suggested_config"]["fw_coord_base"] == fw_base
    assert rep["suggested_config"]["max_fps"] <= 8
    for f in ("devtest.json", "devtest.md", "via-backup.json"):
        assert (tmp_path / f).exists()
    md = (tmp_path / "devtest.md").read_text()
    assert "建议配置" in md and "[knobs]" in md
    # read-only by default: only getInfo JSON requests reached the device
    from mk20deck import protocol as P
    assert not {r["method"] for r in state["dev"].json_requests} & P.WRITE_METHODS
    assert s["methods"]["available"][0] == "getInfo" and s["files"]["count"] == 1


def test_devtest_auto_mode_skips_guided_and_gates_writes(tmp_path):
    dev = MockDevice()
    opts = DevTestOptions(interactive=False, fps_seconds=0.2, ack_frames=2, backlight_level=40)
    rep = DevTest(opts, ScriptedPrompter(), str(tmp_path), lambda: ["/dev/mock"], lambda p: dev).run()
    st = {k: v["status"] for k, v in rep["sections"].items()}
    assert st["keys"] == st["knobs"] == st["timing"] == st["reconnect"] == "skip"
    assert st["backlight"] == "skip" and "allow-device-writes" in rep["sections"]["backlight"]["reason"]
    # ui.hello: mk20-kbd liveness negotiation (no content, no write); the legacy mock answers "Unknown method"
    assert {r["method"] for r in dev.json_requests} == {"getInfo", "ui.hello", "getFilesBySuffix", "mk20deckProbe"}
    opts.allow_device_writes = True
    dev2 = MockDevice()
    rep = DevTest(opts, ScriptedPrompter(), str(tmp_path / "w"), lambda: ["/dev/mock"], lambda p: dev2).run()
    assert rep["sections"]["backlight"]["status"] == "pass"
    assert any(r["method"] == "setBacklight" for r in dev2.json_requests)


def test_keycode_form_events_are_parsed_and_mapped():
    from mk20deck import protocol as P
    ev = P.classify_json({"method": "keyStateChanged", "parameters": {"pressed": True, "keyCodeH": 0, "keyCodeL": 0xE6},
                          "requestId": 7})
    assert ev.col == -1 and ev.keycode == 0xE6 and ev.request_id == 7
    ev2 = P.classify_json({"method": "keyStateChanged", "parameters": {"col": 1, "row": 2, "pressed": False}})
    assert (ev2.col, ev2.row, ev2.keycode) == (1, 2, None)
    clk = FakeClock()
    d = Daemon(_ctl(clk), Renderer(DeviceLayout.default()), None, clock=clk,
               keycode_map={"0x00e6": "1,4", "0x0080": "K1:cw"})
    d.on_device_key(ev)
    d.on_device_key(P.KeyEvent(-1, -1, True, 0x80))
    d.on_device_key(P.KeyEvent(-1, -1, True, 0x99))  # unmapped -> ignored
    got = [d.events.get_nowait() for _ in range(d.events.qsize())]
    assert [(e.kind, e.a, e.b) for e in got] == [("key", 1, 4), ("knob", 1, "cw")]


def test_set_volume_range_matches_firmware():
    from mk20deck import protocol as P
    assert P.set_volume(50)["parameters"]["level"] == 7


# ------------------------------------------------------------------ backlight after every handshake
def test_backlight_config_parsing_and_default(tmp_path):
    from mk20deck.config import load_config, parse_backlight
    assert DeckConfig().backlight == 80
    assert parse_backlight(False) is None and parse_backlight("off") is None and parse_backlight(-1) is None
    assert parse_backlight(150) == 100 and parse_backlight(40) == 40 and parse_backlight(True) == 80
    p = tmp_path / "c.toml"
    p.write_text('[deck]\nbacklight = false\n')
    assert load_config(str(p)).backlight is None
    p.write_text('[deck]\nbacklight = 55\n')
    assert load_config(str(p)).backlight == 55


def test_supervisor_sets_backlight_after_every_handshake():
    cfg = DeckConfig()
    cfg.preamble_flush, cfg.ack_timeout_s, cfg.heartbeat_s, cfg.poll_interval_s = 0, 0.2, 0.05, 0.5
    state = {"dev": MockDevice(), "present": True}
    first = state["dev"]
    sup = Supervisor(_ctl(FakeClock()), lambda: ["/dev/mock"] if state["present"] else [],
                     lambda port: state["dev"], default_renderer_factory(cfg), cfg, retry_s=0.05)
    stop = threading.Event()
    th = threading.Thread(target=sup.run, args=(stop,), daemon=True)
    th.start()
    assert wait_until(lambda: sup.stats.connects == 1)
    assert wait_until(lambda: any(r["method"] == "setBacklight" for r in first.json_requests))
    methods = [r["method"] for r in first.json_requests]
    assert methods.index("getInfo") < methods.index("setBacklight")
    assert [r for r in first.json_requests if r["method"] == "setBacklight"][0]["parameters"]["level"] == 80
    first.unplugged, state["present"] = True, False
    assert wait_until(lambda: any("连接中断" in m for _, m in sup.stats.events))
    state["dev"], state["present"] = MockDevice(), True
    assert wait_until(lambda: sup.stats.connects == 2)
    assert wait_until(lambda: any(r["method"] == "setBacklight" for r in state["dev"].json_requests))
    stop.set()
    th.join(3)
    assert any("背光 80" in m for _, m in sup.stats.events)


def test_supervisor_backlight_disabled_sends_nothing():
    cfg = DeckConfig()
    cfg.preamble_flush, cfg.ack_timeout_s, cfg.heartbeat_s, cfg.backlight = 0, 0.2, 0.05, None
    dev = MockDevice()
    sup = Supervisor(_ctl(FakeClock()), lambda: ["/dev/mock"], lambda p: dev, default_renderer_factory(cfg), cfg,
                     retry_s=0.05)
    stop = threading.Event()
    th = threading.Thread(target=sup.run, args=(stop,), daemon=True)
    th.start()
    assert wait_until(lambda: sup.stats.connects == 1 and len(dev.frames) >= 1)
    stop.set()
    th.join(3)
    assert not any(r["method"] == "setBacklight" for r in dev.json_requests)


# ------------------------------------------------------------------ headless devtest
class FakeHid:
    def __init__(self):
        self.samples = []
        self.started = self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def add(self, name):
        self.samples.append((time.monotonic(), name))

    def between(self, t0, t1):
        return [s for s in self.samples if t0 <= s[0] <= t1]

    def info(self):
        return {"source": "fake"}


def _headless(tmp_path, state, acts, hid, delay=0.05, **kw):
    o = {"fps_seconds": 0.2, "ack_frames": 2, "step_timeout": 0.6, "quiet_s": 0.2, "headless": True,
         "start_timeout": 3.0, "allow_device_writes": True, "backlight_level": 80, **kw}
    return DevTest(DevTestOptions(**o), ScriptedPrompter(acts, delay=delay), str(tmp_path),
                   find_ports=lambda: ["/dev/mock"] if state["present"] else [],
                   open_transport=lambda p: state["dev"], hid_monitor=hid)


def test_headless_devtest_runs_without_input_and_auto_checks(tmp_path):
    from mk20deck.hw.devtest import step_plan
    state = {"dev": MockDevice(), "present": True}
    hid = FakeHid()
    prompter = _scripted(state, 0, FakePower())
    acts = dict(prompter.actions)
    acts["start"] = lambda: (state["dev"].press(4, 3, True), time.sleep(0.05), state["dev"].press(4, 3, False))
    del acts["key:3,2"]  # user never presses this one -> timeout, skip and record
    k52 = acts["key:5,2"]
    acts["key:5,2"] = lambda: (k52(), hid.add("keyDown"))  # this key leaks a keystroke to the Mac
    rep = _headless(tmp_path, state, acts, hid).run()
    s = rep["sections"]
    st = {k: v["status"] for k, v in s.items()}
    assert rep["start_gate"]["pressed"] and rep["step_plan"] == step_plan(DevTestOptions(headless=True))
    assert prompter.log == [] and hid.started and hid.stopped  # (scripted prompter only fires actions)
    assert s["keys"]["missing"] == ["3,2"] and s["keys"]["results"]["3,2"]["timed_out_s"] == 0.6
    assert st["keys"] == "fail" and st["knobs"] == "pass" and st["timing"] == "pass"
    assert st["hid_leak"] == "fail" and s["hid_leak"]["correlated"] >= 1 and s["hid_leak"]["auto"]
    assert s["display"]["needs_manual_confirmation"] and s["display"]["visual_ok"] is None
    assert "needs_manual_confirmation" in s["ptt"] and s["ptt"]["ime_triggered"] is None
    assert st["reconnect"] == "pass" and st["sleep"] == "skip"
    # backlight re-applied on the initial connect and after the replug
    assert [b["success"] for b in rep["backlight_applied"]] == [True, True]
    prog = json.loads((tmp_path / "progress.json").read_text())
    assert prog["state"] == "finished"


def test_headless_devtest_no_leak_passes_and_overall_timeout_skips(tmp_path):
    state = {"dev": MockDevice(), "present": True}
    hid = FakeHid()
    acts = dict(_scripted(state, 0, FakePower()).actions)
    acts["start"] = lambda: (state["dev"].press(0, 0, True), time.sleep(0.05), state["dev"].press(0, 0, False))
    rep = _headless(tmp_path, state, acts, hid, overall_timeout=2.0).run()
    s = rep["sections"]
    assert s["keys"]["results"]["1,1"]["ok"]
    skipped = [k for k, r in s["keys"]["results"].items() if r.get("skipped") == "overall timeout"]
    assert skipped  # budget ran out mid-way: remaining keys skipped and recorded
    assert s["timing"]["status"] == "skip" and "overall" in s["timing"]["reason"]
    assert s["hid_leak"]["status"] == "pass" and s["hid_leak"]["correlated"] == 0


def test_headless_leak_unavailable_is_manual(tmp_path):
    state = {"dev": MockDevice(), "present": True}
    acts = {"start": lambda: (state["dev"].press(0, 0, True), state["dev"].press(0, 0, False))}
    rep = _headless(tmp_path, state, acts, None, overall_timeout=1.0).run()
    assert rep["sections"]["hid_leak"]["status"] == "manual"
    assert rep["sections"]["hid_leak"]["needs_manual_confirmation"]


def test_hid_activity_monitor_detects_idle_resets():
    from mk20deck.hw.hidwatch import TYPES, HidActivityMonitor, correlate
    idle = {t: 100.0 for t in TYPES.values()}
    t = [10.0]
    m = HidActivityMonitor(lambda typ: idle[typ], clock=lambda: t[0])
    m.poll_once()
    for v in idle:
        idle[v] += 0.5
    t[0] = 10.5
    m.poll_once()
    assert m.samples == []
    idle[TYPES["keyDown"]] = 0.1
    t[0] = 11.0
    m.poll_once()
    assert m.samples == [(10.9, "keyDown")]
    c = correlate(m.samples, [10.8, 50.0])
    assert c["correlated"] == 1 and c["uncorrelated"] == 0
    assert correlate(m.samples, [20.0])["uncorrelated"] == 1


def test_mouse_click_systemdefined_is_not_hid_activity():
    from mk20deck.hw.hidwatch import drop_mouse_aux
    s = [(1.0, "leftMouseDown"), (1.01, "systemDefined"), (2.0, "systemDefined"), (3.0, "keyDown")]
    assert drop_mouse_aux(s) == [(1.0, "leftMouseDown"), (2.0, "systemDefined"), (3.0, "keyDown")]


def test_headless_key_step_does_not_advance_on_hid_alone(tmp_path):
    state = {"dev": MockDevice(), "present": True}
    hid = FakeHid()

    def late_press():
        hid.add("systemDefined")  # e.g. a media key / click on the Mac before the user reaches the deck
        time.sleep(0.3)
        state["dev"].press(0, 0, True)
        time.sleep(0.05)
        state["dev"].press(0, 0, False)

    acts = {"start": lambda: (state["dev"].press(4, 3, True), state["dev"].press(4, 3, False)), "key:1,1": late_press}
    rep = _headless(tmp_path, state, acts, hid, only=("keys",)).run()
    r = rep["sections"]["keys"]["results"]["1,1"]
    assert r["ok"] and r["fw"] == [0, 0]
    assert rep["sections"]["display"]["status"] == "skip" and rep["sections"]["via"]["status"] == "skip"


def test_headless_only_knobs_rerun_and_knob_coords(tmp_path):
    from mk20deck.hw.devtest import step_plan
    state = {"dev": MockDevice(), "present": True}
    hid = FakeHid()

    def tap(c, r):
        state["dev"].press(c, r, True)
        time.sleep(0.03)
        state["dev"].press(c, r, False)

    acts = {"start": lambda: tap(1, 1), "knob:K2:press": lambda: tap(103, 103),
            "knob:K1:cw": lambda: hid.add("systemDefined")}  # rotation arriving only as a Mac media key
    opts_only = ("knobs", "hid_leak")
    rep = _headless(tmp_path, state, acts, hid, only=opts_only).run()
    s = rep["sections"]
    assert rep["step_plan"] == step_plan(DevTestOptions(headless=True, only=opts_only))
    assert len(rep["step_plan"]) == 7
    assert s["keys"]["status"] == "skip" and s["ptt"]["status"] == "skip"
    assert s["knobs"]["suggested_knob_map"] == {"103,103": "K2:press"}
    assert s["knobs"]["results"]["knob:K1:cw"]["hid_activity"] == ["systemDefined"]
    assert s["hid_leak"]["status"] == "warn" and s["hid_leak"]["knob_steps_with_hid"] == ["knob:K1:cw"]
    assert rep["hid_samples"] and rep["step_log"][0]["step"] == "start"


def test_daemon_maps_k2_press_and_drops_unmapped_knob_coords():
    from mk20deck import protocol as P
    clk = FakeClock()
    cfg = DeckConfig()
    assert cfg.knob_map["103,103"] == "K2:press" and cfg.knob_map["102,102"] == "K1:cw"
    d = Daemon(_ctl(clk), Renderer(DeviceLayout.default()), None, clock=clk, knob_map=cfg.knob_map)
    d.on_device_key(P.KeyEvent(103, 103, True))
    d.on_device_key(P.KeyEvent(102, 102, True))
    d.on_device_key(P.KeyEvent(109, 109, True))  # unknown knob event: must not become a grid key
    d.on_device_key(P.KeyEvent(0, 3, True))
    got = [d.events.get_nowait() for _ in range(d.events.qsize())]
    assert [(e.kind, e.a, e.b) for e in got] == [("knob", 2, "down"), ("knob", 1, "cw"), ("key", 1, 4)]


def test_step_plan_key_subset():
    from mk20deck.hw.devtest import step_plan
    p = step_plan(DevTestOptions(headless=True, only=("keys", "knobs", "ptt", "hid_leak", "reconnect"),
                                 key_subset=("1,1", "2,1", "3,1")))
    assert len(p) == 1 + 3 + 6 + 1 + 1 and "第1行 第3列" in p[3]


def test_step_t0_taken_before_prompt_even_if_progress_is_slow(tmp_path, monkeypatch):
    """Regression: t0 used to be taken after _progress(); a slow progress write let the scripted
    press land before t0, so the step only saw the release and timed out."""
    from mk20deck.hw import devtest as D
    fired = []

    class P(D.ScriptedPrompter):
        def instruct(self, step, text):
            fired.append(time.monotonic())

    state = {"dev": MockDevice(), "present": True}
    dt = _headless(tmp_path, state, {}, FakeHid())
    dt.prompter = P()
    dt.clock = time.monotonic
    monkeypatch.setattr(dt, "_show", lambda *a, **k: None)
    monkeypatch.setattr(dt, "_progress", lambda *a, **k: time.sleep(0.15))
    t0 = dt._step("key:2,1", "x")
    assert fired and t0 <= fired[0]


# ------------------------------------------------------------------ HID rule since fw 1.5.0 (HID_RULE)
# kbd/onboard.c HOST_HID_KEYS / kbd/UI.md: on the live L1/L4 page (host ONLINE via ui.hello + heartbeats, fresh
# frame -- which devtest itself provides) the PTT (fb16, Right Option) and the bottom-row Mac keys fb17-19
# (Backspace/Return/Escape) only type on the Mac: the keyboard consumes their edges, no keyStateChanged.  Ordinary
# grid keys are forwarded only from that page, so their presses before/after a step are the live-page evidence.
# Option-mode digits exist only while the host grants a ui.hidKeys map, which devtest never does.
# SLOW: the user reaches the next highlighted key >= 0.45 s after a step starts (leaks are attributed to any CDC
# event within 0.35 s, across step boundaries).
LIVE_KEYS = ("2,1", "1,4", "2,4", "3,4", "4,4", "5,4")
SLOW = {"delay": 0.45, "step_timeout": 1.2}


def _tap(state, c, r, hold=0.03):
    state["dev"].press(c, r, True)
    time.sleep(hold)
    state["dev"].press(c, r, False)


def _fw15(hid_keys=True):
    """mk20-kbd host-page firmware; ``hid_keys``: 1.5.1 (ui.hidKeys capability = the only proof of fw >= 1.5)."""
    return {"dev": MockDevice(ui=True, hid_keys=hid_keys), "present": True}


def _live_walk(state, hid):
    """Device behaviour on a live L1 page: rows 1-3 and fb20 are CDC presses, PTT/bottom row HID only."""
    return {"start": lambda: _tap(state, 4, 3),
            "key:2,1": lambda: _tap(state, 1, 0),
            "key:1,4": lambda: (hid.add("flagsChanged"), time.sleep(0.05), hid.add("flagsChanged")),
            "key:2,4": lambda: hid.add("keyDown"),
            "key:3,4": lambda: hid.add("keyDown"),
            "key:4,4": lambda: hid.add("keyDown"),
            "key:5,4": lambda: _tap(state, 4, 3)}


def _walk(tmp_path, state, acts, hid, keys=LIVE_KEYS, **kw):
    return _headless(tmp_path, state, acts, hid, only=("keys", "hid_leak"), key_subset=keys, **SLOW, **kw).run()


def test_hid_rule_live_page_allows_ptt_and_bottom_row(tmp_path):
    from mk20deck.hw.devtest import HID_RULE, LEAK_NOTE
    state, hid = _fw15(), FakeHid()
    rep = _walk(tmp_path, state, _live_walk(state, hid), hid)
    s = rep["sections"]
    assert s["getinfo"]["ui_negotiated"] and "ui.hidKeys" in s["getinfo"]["fw_bottom_row_hid"]
    leak, keys = s["hid_leak"], s["keys"]
    assert leak["status"] == "pass", leak
    assert leak["correlated"] == leak["uncorrelated"] == 0 and leak["hid_only_keys"] == []
    assert leak["expected_hid_by_key"] == {"1,4": 2, "2,4": 1, "3,4": 1, "4,4": 1} and leak["expected_hid"] == 5
    assert leak["unconfirmed_hid_by_key"] == leak["wrong_type_by_key"] == {}
    assert set(leak["allowed_hid"]) == {"1,4", "2,4", "3,4", "4,4"} and leak["host_pages"] and leak["live_page_seen"]
    assert leak["rule"] == HID_RULE and leak["note"] == LEAK_NOTE and "19 个非 PTT" not in leak["note"]
    # explicit per-key expectation; the consumed (HID-only) keys are not "missing"
    r = keys["results"]
    assert [k for k in LIVE_KEYS if r[k]["hid_expected"]] == ["1,4", "2,4", "3,4", "4,4"]
    assert r["2,4"]["hid_allowed"] == ["keyDown"] and r["1,4"]["hid_allowed"] == ["flagsChanged"]
    assert r["3,4"]["hid_as_expected"] and not r["3,4"]["ok"] and r["3,4"]["cdc_exempt"] == "allowed"
    assert "Return" in r["3,4"]["hid_label"] and "hid_allowed" not in r["2,1"] and r["2,1"]["ok"]
    assert keys["status"] == "pass" and keys["missing"] == keys["unconfirmed"] == []
    assert keys["hid_only_expected"] == ["1,4", "2,4", "3,4", "4,4"]
    # guard: devtest never grants option-mode digits, so no digit key may type during a devtest run
    assert state["dev"].hid_requests == [] and not any(q["method"] == "ui.hidKeys" for q in state["dev"].json_requests)


def test_hid_rule_non_allowed_key_hid_fails(tmp_path):
    state, hid = _fw15(), FakeHid()
    acts = _live_walk(state, hid)
    acts["key:2,1"] = lambda: (_tap(state, 1, 0), hid.add("keyDown"))  # e.g. a row-1 digit typed without a grant
    leak = _walk(tmp_path, state, acts, hid)["sections"]["hid_leak"]
    assert leak["status"] == "fail" and leak["correlated"] == 1 and leak["correlated_by_type"] == {"keyDown": 1}
    assert leak["expected_hid_by_key"] == {"1,4": 2, "2,4": 1, "3,4": 1, "4,4": 1}


def test_hid_rule_wrong_type_on_bottom_row_fails(tmp_path):
    state, hid = _fw15(), FakeHid()
    acts = _live_walk(state, hid)
    acts["key:3,4"] = lambda: hid.add("systemDefined")  # Return key producing e.g. a volume event: not allowed
    rep = _walk(tmp_path, state, acts, hid)
    leak, keys = rep["sections"]["hid_leak"], rep["sections"]["keys"]
    assert leak["status"] == "fail" and leak["wrong_type_by_key"] == {"3,4": ["systemDefined"]}
    assert leak["correlated"] == leak["uncorrelated"] == 0 and leak["hid_only_keys"] == ["3,4"]
    assert keys["missing"] == ["3,4"] and keys["results"]["3,4"]["hid_as_expected"] is False


def test_hid_rule_cross_step_leak_is_attributed_not_exempted(tmp_path):
    """A non-allowed key's HID that arrives (delayed) inside the next, allowed key's step is still its leak."""
    state, hid = _fw15(), FakeHid()
    acts = _live_walk(state, hid)
    acts["key:5,3"] = lambda: (_tap(state, 4, 2), time.sleep(0.25), hid.add("keyDown"))  # lands in step 2,4
    rep = _walk(tmp_path, state, acts, hid, keys=("5,3", "2,4", "5,4"))
    leak = rep["sections"]["hid_leak"]
    r = rep["sections"]["keys"]["results"]
    assert r["2,4"]["window"][0] < hid.samples[0][0] < r["2,4"]["window"][1]  # really inside the allowed step
    assert leak["status"] == "fail" and leak["correlated"] == 1, leak
    assert leak["expected_hid_by_key"] == {"2,4": 1}  # 2,4's own Backspace, 0.45 s after the 5,3 release
    # indistinguishable from a fast user: the fail says so and records the gap for the re-test
    ff = leak["fast_follow"]
    assert [(f["key"], f["after"], f["type"]) for f in ff] == [("2,4", "5,3", "keyDown")] and 0 < ff[0]["gap_s"] < 0.35
    assert "请放慢重测" in leak["reason"]


def test_hid_rule_fast_user_gets_a_retest_hint(tmp_path):
    """Backspace pressed 0.15 s after the previous key's release: attributed to that key (safe direction), but the
    fail tells the user to re-test slower and records the gap."""
    state, hid = _fw15(), FakeHid()
    acts = _live_walk(state, hid)
    acts["key:2,1"] = lambda: (_tap(state, 1, 0), time.sleep(0.15), hid.add("keyDown"))  # = the fast ⌫ press
    acts["key:2,4"] = lambda: None
    rep = _walk(tmp_path, state, acts, hid, keys=("2,1", "2,4", "5,4"))
    leak = rep["sections"]["hid_leak"]
    assert leak["status"] == "fail" and leak["correlated"] == 1 and leak["expected_hid"] == 0
    assert leak["reason"].startswith("若该键紧跟上一个按键（<0.35 s），请放慢重测")
    (ff,) = leak["fast_follow"]
    assert ff["key"] == "2,4" and ff["after"] == "2,1" and 0.1 <= ff["gap_s"] < 0.35
    from mk20deck.hw.devtest import render_markdown
    assert "请放慢重测" in render_markdown(rep)


def test_hid_rule_stock_ptt_release_just_after_its_step_is_its_own(tmp_path):
    """Stock firmware PTT: CDC edges plus Right Option; the release's flagsChanged may land after the step ended."""
    state, hid = {"dev": MockDevice(), "present": True}, FakeHid()
    acts = {"start": lambda: _tap(state, 4, 3),
            "key:1,4": lambda: (hid.add("flagsChanged"), _tap(state, 0, 3), time.sleep(0.05), hid.add("flagsChanged")),
            "key:5,4": lambda: _tap(state, 4, 3)}
    rep = _walk(tmp_path, state, acts, hid, keys=("1,4", "5,4"))
    leak = rep["sections"]["hid_leak"]
    assert hid.samples[1][0] > rep["sections"]["keys"]["results"]["1,4"]["window"][1]
    assert leak["status"] == "pass" and leak["expected_hid_by_key"] == {"1,4": 2}, leak


@pytest.mark.parametrize("extra", ["wrong_type", "other_key"])
def test_hid_rule_ptt_window_only_exempts_its_right_option(tmp_path, extra):
    state, hid = _fw15(), FakeHid()

    def ptt():
        hid.add("flagsChanged")  # Right Option down
        if extra == "wrong_type":
            hid.add("keyDown")  # the PTT typing a character: not Right Option
            time.sleep(0.3)
            hid.add("flagsChanged")
        else:
            time.sleep(0.4)
            _tap(state, 4, 0)
            hid.add("keyDown")  # another key leaking while the PTT is held (its CDC edges end the step)

    acts = {"start": lambda: _tap(state, 4, 3), "ptt": ptt}
    rep = _headless(tmp_path, state, acts, hid, only=("ptt", "hid_leak"), **SLOW).run()
    leak = rep["sections"]["hid_leak"]
    assert leak["status"] == "fail", leak
    if extra == "wrong_type":
        assert leak["wrong_type_by_key"] == {"1,4": ["keyDown"]} and leak["correlated"] == 0
        assert leak["expected_hid_by_key"] == {"1,4": 2}
    else:
        assert leak["correlated"] == 1 and leak["wrong_type_by_key"] == {} and leak["expected_hid_by_key"] == {"1,4": 1}


def test_hid_rule_full_live_run_with_ptt_passes(tmp_path):
    """A complete 1.5.x headless run on the live page: the PTT gives Right Option and no CDC event -> pass."""
    state, hid = _fw15(), FakeHid()
    acts = _live_walk(state, hid)
    acts["ptt"] = lambda: (hid.add("flagsChanged"), time.sleep(0.3), hid.add("flagsChanged"))
    acts.update({k: v for k, v in _scripted(state, 0, FakePower()).actions.items() if k.startswith("timing:")})
    rep = _headless(tmp_path, state, acts, hid, only=("keys", "ptt", "timing", "hid_leak"), key_subset=LIVE_KEYS,
                    **SLOW).run()
    s = rep["sections"]
    assert s["ptt"]["status"] == "pass" and s["ptt"]["via"] == "hid" and not s["ptt"]["cdc_observed"], s["ptt"]
    assert s["hid_leak"]["status"] == "pass" and s["hid_leak"]["expected_hid_by_key"]["1,4"] == 4
    assert all(v["status"] in ("pass", "skip") for v in s.values()), {k: v["status"] for k, v in s.items()}


def test_hid_rule_ptt_without_later_evidence_is_unconfirmed(tmp_path):
    state, hid = _fw15(), FakeHid()
    acts = {"start": lambda: _tap(state, 4, 3), "ptt": lambda: hid.add("flagsChanged")}
    rep = _headless(tmp_path, state, acts, hid, only=("ptt", "hid_leak"), **SLOW).run()
    assert rep["sections"]["ptt"]["status"] == "manual" and rep["sections"]["ptt"]["via"] == "hid"
    assert rep["sections"]["hid_leak"]["status"] == "pass"  # Right Option from the PTT is always allowed


def test_hid_rule_bottom_row_unconfirmed_without_fw_proof(tmp_path):
    """ui.hello without ui.hidKeys (1.4.x has no bottom-row HID, 1.5.0 looks the same): never a pass."""
    state, hid = _fw15(hid_keys=False), FakeHid()
    rep = _walk(tmp_path, state, _live_walk(state, hid), hid)
    leak, keys = rep["sections"]["hid_leak"], rep["sections"]["keys"]
    assert rep["sections"]["getinfo"]["fw_bottom_row_hid"] is None
    assert leak["status"] == "manual" and leak["needs_manual_confirmation"]
    assert leak["unconfirmed_hid_by_key"] == {"2,4": 1, "3,4": 1, "4,4": 1} and leak["expected_hid_by_key"] == {"1,4": 2}
    assert keys["status"] == "manual" and keys["unconfirmed"] == ["2,4", "3,4", "4,4"]
    assert keys["hid_only_expected"] == ["1,4"]


def test_hid_rule_bottom_row_unconfirmed_without_live_page_evidence(tmp_path):
    """No ordinary key event after the step: the page may have left L1/L4 (non-live: keys act on-board)."""
    state, hid = _fw15(), FakeHid()
    rep = _walk(tmp_path, state, _live_walk(state, hid), hid, keys=("2,1", "2,4"))
    leak, keys = rep["sections"]["hid_leak"], rep["sections"]["keys"]
    assert leak["status"] == "manual" and leak["unconfirmed_hid_by_key"] == {"2,4": 1}
    assert keys["status"] == "manual" and keys["unconfirmed"] == ["2,4"] and keys["results"]["2,4"]["cdc_exempt"] == "unconfirmed"


def test_hid_rule_option_digit_allowed_only_when_granted(tmp_path):
    state, hid = _fw15(), FakeHid()
    acts = {"start": lambda: _tap(state, 4, 3),
            "key:1,1": lambda: (state["dev"].press(0, 0, True, hid=0x1E), hid.add("keyDown"),
                                time.sleep(0.03), state["dev"].press(0, 0, False)),
            "key:2,1": lambda: (_tap(state, 1, 0), hid.add("keyDown"))}
    dt = _headless(tmp_path, state, acts, hid, only=("keys", "hid_leak"), key_subset=("1,1", "2,1"), **SLOW)
    dt.granted_option_keys = frozenset({(1, 1)})  # as if this host had mapped fb1 -> "1" via ui.hidKeys
    leak = dt.run()["sections"]["hid_leak"]
    assert leak["allowed_hid"]["1,1"] == "主机授予的选项数字键" and "2,1" not in leak["allowed_hid"]
    assert leak["expected_hid_by_key"] == {"1,1": 1}
    assert leak["status"] == "fail" and leak["correlated"] == 1  # 2,1 was not granted


def test_hid_rule_knob_hid_still_fails_on_live_page(tmp_path):
    state, hid = _fw15(), FakeHid()
    acts = {"start": lambda: _tap(state, 1, 1),
            "knob:K1:cw": lambda: [(_tap(state, 7, 0, 0.01), hid.add("systemDefined")) for _ in range(3)]}
    rep = _headless(tmp_path, state, acts, hid, only=("knobs", "hid_leak")).run()
    leak = rep["sections"]["hid_leak"]
    assert leak["host_pages"] and "2,4" in leak["allowed_hid"]
    assert leak["status"] == "fail" and leak["correlated"] == 3 and leak["expected_hid"] == 0
    assert leak["knob_steps_with_hid"] == ["knob:K1:cw"]


def test_hid_rule_bottom_row_is_a_leak_without_host_pages(tmp_path):
    """Stock KD / legacy firmware (no ui.hello, no live L1/L4 page): only the PTT may produce HID."""
    state, hid = {"dev": MockDevice(), "present": True}, FakeHid()
    acts = {"start": lambda: _tap(state, 4, 3),
            "key:1,4": lambda: (_tap(state, 0, 3), hid.add("flagsChanged")),
            "key:2,4": lambda: (_tap(state, 1, 3), hid.add("keyDown"))}
    rep = _walk(tmp_path, state, acts, hid, keys=("1,4", "2,4"))
    s = rep["sections"]
    leak = s["hid_leak"]
    assert not s["getinfo"]["ui_negotiated"] and not leak["host_pages"]
    assert leak["allowed_hid"] == {"1,4": "PTT 右 Option"} and leak["expected_hid_by_key"] == {"1,4": 1}
    assert leak["status"] == "fail" and leak["correlated"] == 1 and leak["correlated_by_type"] == {"keyDown": 1}
    assert s["keys"]["results"]["2,4"]["hid_expected"] is False and s["keys"]["status"] == "pass"


@pytest.mark.parametrize("ui", [True, False])
def test_interactive_leak_question_states_the_rule(tmp_path, ui):
    from mk20deck.hw.devtest import LEAK_NOTE
    asked = {}

    class Ask(ScriptedPrompter):
        def ask(self, step, question, choices="yn"):
            asked[step] = question
            return super().ask(step, question, choices)

        def wait_enter(self, step, text):
            asked[step] = text
            return super().wait_enter(step, text)

    state = {"dev": MockDevice(ui=ui, hid_keys=ui), "present": True}
    opts = DevTestOptions(fps_seconds=0.2, ack_frames=2, step_timeout=0.3, only=("keys", "hid_leak"),
                          key_subset=("2,4",))
    rep = DevTest(opts, Ask({"key:2,4": lambda: _tap(state, 1, 3)}, answers={"hid_leak": "n"}), str(tmp_path),
                  find_ports=lambda: ["/dev/mock"], open_transport=lambda p: state["dev"]).run()
    leak = rep["sections"]["hid_leak"]
    assert leak["status"] == "pass" and leak["note"] == LEAK_NOTE and leak["host_pages"] is ui
    assert "PTT（右 Option）" in asked["hid_leak"]
    assert ("底排 ⌫/⏎/Esc" in asked["hid_leak"]) is ui and ("底排 ⌫/⏎/Esc" in asked["keys_intro"]) is ui
    assert ("2,4" in leak["allowed_hid"]) is ui and rep["sections"]["keys"]["results"]["2,4"]["hid_expected"] is ui


@pytest.mark.parametrize("hid_keys,answer,status", [(True, "y", "pass"), (True, "n", "fail"), (False, "y", "manual"),
                                                    (True, "s", "manual")])
def test_interactive_bottom_row_question_decides_the_consumed_keys(tmp_path, hid_keys, answer, status):
    """Interactive mode has no HID monitor: the live page's ⌫/⏎/Esc send no CDC event, so the user's answer
    decides them -- only with proof of fw >= 1.5 (ui.hidKeys); without it they stay unconfirmed (manual)."""
    asked = {}

    class Ask(ScriptedPrompter):
        def ask(self, step, question, choices="yn"):
            asked[step] = question
            return super().ask(step, question, choices)

    state = _fw15(hid_keys)
    acts = {"key:2,1": lambda: _tap(state, 1, 0), "key:5,4": lambda: _tap(state, 4, 3)}  # bottom row: no CDC
    opts = DevTestOptions(fps_seconds=0.2, ack_frames=2, step_timeout=0.4, only=("keys", "hid_leak"),
                          key_subset=("2,1", "2,4", "3,4", "4,4", "5,4"))
    rep = DevTest(opts, Ask(acts, answers={"hid_leak": "n", "keys_bottom_row": answer}), str(tmp_path),
                  find_ports=lambda: ["/dev/mock"], open_transport=lambda p: state["dev"]).run()
    keys = rep["sections"]["keys"]
    assert asked["keys_bottom_row"] == "底排 ⌫/⏎/Esc 是否分别在文本窗口里删除/换行/取消？"
    assert keys["status"] == status and keys["bottom_row_answer"] == answer, keys
    bottom = ["2,4", "3,4", "4,4"]
    assert {"pass": keys["hid_only_expected"], "fail": keys["missing"], "manual": keys["unconfirmed"]}[status] == bottom


def test_interactive_bottom_row_question_not_asked_without_host_pages(tmp_path):
    state = {"dev": MockDevice(), "present": True}
    prompter = ScriptedPrompter({"key:2,4": lambda: _tap(state, 1, 3)}, answers={"hid_leak": "n"})
    opts = DevTestOptions(fps_seconds=0.2, ack_frames=2, step_timeout=0.3, only=("keys", "hid_leak"),
                          key_subset=("2,4",))
    rep = DevTest(opts, prompter, str(tmp_path), find_ports=lambda: ["/dev/mock"],
                  open_transport=lambda p: state["dev"]).run()
    assert ("ask", "keys_bottom_row") not in prompter.log and "bottom_row_answer" not in rep["sections"]["keys"]
