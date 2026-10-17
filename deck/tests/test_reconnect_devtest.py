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
    assert {r["method"] for r in state["dev"].json_requests} == {"getInfo"}


def test_devtest_auto_mode_skips_guided_and_gates_writes(tmp_path):
    dev = MockDevice()
    opts = DevTestOptions(interactive=False, fps_seconds=0.2, ack_frames=2, backlight_level=40)
    rep = DevTest(opts, ScriptedPrompter(), str(tmp_path), lambda: ["/dev/mock"], lambda p: dev).run()
    st = {k: v["status"] for k, v in rep["sections"].items()}
    assert st["keys"] == st["knobs"] == st["timing"] == st["reconnect"] == "skip"
    assert st["backlight"] == "skip" and "allow-device-writes" in rep["sections"]["backlight"]["reason"]
    assert {r["method"] for r in dev.json_requests} == {"getInfo"}
    opts.allow_device_writes = True
    dev2 = MockDevice()
    rep = DevTest(opts, ScriptedPrompter(), str(tmp_path / "w"), lambda: ["/dev/mock"], lambda p: dev2).run()
    assert rep["sections"]["backlight"]["status"] == "pass"
    assert any(r["method"] == "setBacklight" for r in dev2.json_requests)
