"""Pick-up-and-place gesture for session slots (PRD 1.5 delta, user decision 2026-10-22).

Long press a slot = pick it up ("移动中"); the next slot press places it (other slot: move there and
pin, swapping with an occupant; same slot: toggle the pin) and never focuses.  K1 press, 8 s without
key/knob input, a page/mode switch or a reconnect cancels without changing anything.
"""
import threading
import time

import pytest

from mk20deck.config import DeckConfig
from mk20deck.controller import MOVE_TIMEOUT, Controller, KeyView, Role, slot_pos
from mk20deck.daemon import Daemon, WakeReconnect
from mk20deck.layout import DeviceLayout
from mk20deck.model import Mode, State
from mk20deck.render.canvas import MAX_FPS, Renderer
from mk20deck.render.keys import MOVE_FPS, MOVE_PINK, key_fps, render_key
from mk20deck.render.subscreen import render_sub
from mk20deck.sim import FakeClock, _ctl, demo_adapters, full_adapters, move_shots
from mk20deck.slots import N_SLOTS


@pytest.fixture
def env():
    clk = FakeClock()
    ads = full_adapters()  # 15 Desktop sessions: every slot occupied
    saved = []
    c = Controller(DeckConfig(), ads, clock=clk, wall=lambda: 1.0, sim=True, on_state_change=saved.append)
    c.refresh()
    return c, clk, ads, saved


def tap(c, clk, col, row, hold=0.05):
    c.on_key(col, row, True)
    clk.advance(hold)
    c.tick()
    c.on_key(col, row, False)


def hold(c, clk, col, row, dur=0.7):
    c.on_key(col, row, True)
    t = 0.0
    while t < dur:
        clk.advance(0.05)
        t += 0.05
        c.tick()
    c.on_key(col, row, False)


def knob(c, k, clk=None, dur=0.05):
    c.on_knob(k, "down")
    if clk is not None:
        clk.advance(dur)
        c.tick()
    c.on_knob(k, "up")


def keys(c):
    return [e.key if e else None for e in c.mgr.slots]


def pins(c):
    return [bool(e and e.pinned) for e in c.mgr.slots]


def focus_calls(ads, mode=Mode.DESKTOP):
    return [a for a in ads[mode].actions if a[0] == "focus"]


def pick(c, clk, idx):
    """Long press slot ``idx`` (same physical key on L1 and, for S6..S15, on L4)."""
    hold(c, clk, *slot_pos(idx))
    assert c.moving is not None and c.moving.src == idx


# ------------------------------------------------------------------ long press = pick up
def test_long_press_picks_up_highlight_and_line(env):
    c, clk, ads, saved = env
    before = keys(c)
    title = c.mgr.session_at(2).title
    c.on_key(3, 1, True)  # S3
    clk.advance(0.65)
    c.tick()
    assert c.moving is not None and c.moving.src == 2  # picked up while still held
    c.on_key(3, 1, False)  # the release of the long press does nothing else
    assert keys(c) == before and not any(pins(c)) and ads[Mode.DESKTOP].actions == []
    assert not any(e and e["pinned"] for e in saved[-1]["modes"]["codex_desktop"]["slots"])
    v = c.view()
    moving = [k for k in v.keys if k.moving]
    assert [(k.col, k.row) for k in moving] == [(3, 1)]
    short = title if len(title) <= 6 else title[:6] + "…"
    assert v.sub.moving == f"移动『{short}』：点目标键放下 · 点自己=切换钉住 · K1 取消"
    assert c.log[-1] == "已拿起 S3"


def test_long_press_on_empty_slot_picks_nothing():
    clk = FakeClock()
    c = _ctl(clk)  # demo: 8 sessions, S15 empty
    hold(c, clk, 5, 3)
    assert c.moving is None and c.log[-1] == "空槽不可移动"


# ------------------------------------------------------------------ place
def test_tap_other_occupied_swaps_and_pins_without_focus(env):
    c, clk, ads, saved = env
    before = keys(c)
    pick(c, clk, 2)  # S3
    sel = c.mgr.selected
    n_focus = len(focus_calls(ads))
    c.on_key(4, 2, True)  # S9: placed on press
    assert c.moving is None
    clk.advance(0.05)
    c.on_key(4, 2, False)
    after = keys(c)
    assert after[8] == before[2] and after[2] == before[8]
    assert after[:2] + after[3:8] + after[9:] == before[:2] + before[3:8] + before[9:]
    assert c.mgr.slots[8].pinned and not c.mgr.slots[2].pinned  # displaced keeps its own (unpinned) flag
    assert len(focus_calls(ads)) == n_focus and ads[Mode.DESKTOP].actions == []  # never brings a window forward
    assert c.mgr.selected == sel  # the placing press does not re-select
    assert c.log[-1] == "已与 S9 交换 · 已钉扎"
    st = saved[-1]["modes"]["codex_desktop"]  # persisted immediately
    assert st["slots"][8] == {"key": before[2], "pinned": True}
    assert st["slots"][2] == {"key": before[8], "pinned": False}
    v = c.view()
    assert not any(k.moving for k in v.keys) and v.sub.moving == ""
    assert next(k for k in v.keys if (k.col, k.row) == (4, 2)).pinned


def test_swap_with_pinned_target_keeps_its_pin(env):
    c, clk, ads, _ = env
    pick(c, clk, 5)
    tap(c, clk, 1, 2)  # S6 itself: pin it
    before = keys(c)
    pick(c, clk, 0)
    tap(c, clk, 1, 2)  # S1 -> S6 (pinned)
    assert keys(c)[5] == before[0] and keys(c)[0] == before[5]
    assert c.mgr.slots[0].pinned and c.mgr.slots[5].pinned


def test_tap_other_empty_moves_and_source_empties():
    clk = FakeClock()
    saved = []
    c = Controller(DeckConfig(), demo_adapters(), clock=clk, wall=lambda: 1.0, sim=True,
                   on_state_change=saved.append)
    c.refresh()
    k1 = c.mgr.slots[1].key
    pick(c, clk, 1)
    tap(c, clk, 5, 3)  # S15 (empty)
    assert c.mgr.slots[14].key == k1 and c.mgr.slots[14].pinned and c.mgr.slots[1] is None
    assert c.log[-1] == "已移到 S15 · 已钉扎" and c.adapters[Mode.DESKTOP].actions == []
    c.refresh()  # nothing waiting: S2 stays empty, S15 keeps the placed session
    assert c.mgr.slots[1] is None and c.mgr.slots[14].key == k1
    assert saved[-1]["modes"]["codex_desktop"]["slots"][1] is None


def test_tap_self_toggles_pin(env):
    c, clk, ads, _ = env
    pick(c, clk, 6)
    tap(c, clk, 2, 2)
    assert c.mgr.slots[6].pinned and c.log[-1] == "已钉扎" and c.moving is None
    pick(c, clk, 6)
    tap(c, clk, 2, 2)
    assert not c.mgr.slots[6].pinned and c.log[-1] == "已取消钉扎"
    assert ads[Mode.DESKTOP].actions == []


def test_placing_press_held_long_does_not_pick_up_again(env):
    c, clk, ads, _ = env
    pick(c, clk, 0)
    hold(c, clk, 2, 1, 1.0)  # the placing press is held past 600 ms
    assert c.moving is None and c.mgr.slots[1].pinned and ads[Mode.DESKTOP].actions == []


def test_tap_after_placement_is_normal_again(env):
    c, clk, ads, _ = env
    pick(c, clk, 0)
    tap(c, clk, 2, 1)
    tap(c, clk, 2, 1)  # a plain tap: select + focus as before
    assert focus_calls(ads) == [("focus", c.mgr.slots[1].key)]


# ------------------------------------------------------------------ cancel
def test_k1_press_cancels_without_focus_or_l4(env):
    c, clk, ads, saved = env
    before, pin0 = keys(c), pins(c)
    pick(c, clk, 3)
    n_saved = len(saved)
    knob(c, 1)
    assert c.moving is None and c.log[-1] == "已取消移动"
    assert keys(c) == before and pins(c) == pin0 and ads[Mode.DESKTOP].actions == []  # K1 release did not focus
    assert len(saved) == n_saved
    pick(c, clk, 3)
    knob(c, 1, clk, 0.8)  # K1 held: still only a cancel, no L4
    assert c.moving is None and c.page == "L1"
    knob(c, 1)  # the next K1 press focuses again as usual
    assert len(focus_calls(ads)) == 1


def test_timeout_8s_without_input_cancels(env):
    c, clk, ads, _ = env
    before = keys(c)
    pick(c, clk, 4)
    assert c.move_deadline() == pytest.approx(clk.t + MOVE_TIMEOUT)
    clk.advance(MOVE_TIMEOUT - 0.1)
    c.tick()
    assert c.moving is not None
    clk.advance(0.2)
    c.tick()
    assert c.moving is None and c.log[-1] == "已取消移动" and keys(c) == before
    assert c.move_deadline() is None


def test_any_key_or_knob_event_restarts_timeout(env):
    c, clk, ads, _ = env
    pick(c, clk, 4)
    clk.advance(5.0)
    c.on_knob(2, "cw")  # K2 rotation: scroll only, but it is input
    clk.advance(5.0)
    c.tick()
    assert c.moving is not None  # 10 s since pick-up, 5 s since the last input
    c.on_knob(1, "cw")  # K1 rotation moves the selection, keeps the pick-up
    clk.advance(7.9)
    c.tick()
    assert c.moving is not None
    clk.advance(0.2)
    c.tick()
    assert c.moving is None


def test_device_side_keys_ignored_while_moving(env):
    c, clk, ads, _ = env
    before = keys(c)
    pick(c, clk, 4)
    t_last = c.moving.last
    clk.advance(4.0)
    for col in (2, 3, 4):  # DELETE / SEND-OK / NO: never seen in practice; ignored if they arrive
        tap(c, clk, col, 4)
    c.on_key(1, 4, True)  # PTT observed: overlay only, not a cancel, not input for the timeout
    c.on_key(1, 4, False)
    assert c.moving is not None and c.moving.last == t_last and keys(c) == before
    clk.advance(4.1)
    c.tick()
    assert c.moving is None and keys(c) == before


def test_k2_page_switch_cancels(env):
    c, clk, ads, _ = env
    before = keys(c)
    pick(c, clk, 5)
    knob(c, 2)  # K2 short: L1 -> L4
    assert c.page == "L4" and c.moving is None and c.log[-1] == "已取消移动 · 切到 L4"
    pick(c, clk, 5)  # S6 is on L4 row 2 too
    knob(c, 2, clk, 0.8)  # K2 hold: back to L1
    assert c.page == "L1" and c.moving is None and c.log[-1] == "已取消移动 · 回到 L1"
    assert keys(c) == before and not any(pins(c))


def test_mode_key_cancels_short_and_long(env):
    c, clk, ads, _ = env
    before = keys(c)
    pick(c, clk, 0)
    tap(c, clk, 5, 4)  # MODE short: cycle to herdr
    assert c.mode == Mode.HERDR and c.moving is None and c.log[-1].startswith("已取消移动 · 已切换到 herdr")
    tap(c, clk, 5, 4)
    tap(c, clk, 5, 4)  # back to Desktop: arrangement untouched (per-mode memory)
    assert c.mode == Mode.DESKTOP and keys(c) == before
    pick(c, clk, 0)
    hold(c, clk, 5, 4, 0.8)  # MODE hold: L4
    assert c.page == "L4" and c.moving is None and c.log[-1] == "已取消移动 · L4 模式页：点选模式"
    assert keys(c) == before and not any(pins(c))


@pytest.mark.parametrize("col", [1, 2, 3, 4, 5])
def test_l4_row1_keys_cancel(env, col):
    c, clk, ads, _ = env
    knob(c, 2)  # to L4
    before = keys(c)
    hold(c, clk, 2, 2)  # S7 on L4
    assert c.moving is not None
    tap(c, clk, col, 1)  # DESK / HERD / TMUX / ＋新会话 / 回 L1
    assert c.moving is None and c.log[-1].startswith("已取消移动 · ")
    assert c.managers[Mode.DESKTOP].slots[6].key == before[6] and not c.managers[Mode.DESKTOP].slots[6].pinned
    assert [e.key if e else None for e in c.managers[Mode.DESKTOP].slots] == before


def test_switch_mode_programmatic_cancels(env):
    c, clk, ads, _ = env
    pick(c, clk, 0)
    c.switch_mode(Mode.TMUX)
    assert c.moving is None and c.log[-1].startswith("已取消移动 · 已切换到 tmux")


def test_source_vanishes_while_moving_cancels(env):
    c, clk, ads, _ = env
    k = c.mgr.slots[3].key
    pick(c, clk, 3)
    ads[Mode.DESKTOP].sessions = [s for s in ads[Mode.DESKTOP].sessions if s.sid.key() != k]
    c.refresh()
    assert c.moving is None and c.log[-1] == "已取消移动：会话已消失"
    assert not any(kv.moving for kv in c.view().keys)


# ------------------------------------------------------------------ L1 / L4 rows 2-3
def test_moving_on_l4_rows_2_3_is_the_l1_slot(env):
    c, clk, ads, saved = env
    knob(c, 2)  # L4
    assert c.page == "L4"
    before = keys(c)
    hold(c, clk, 2, 2)  # S7 on L4
    assert c.moving.src == 6
    v = c.view()
    assert [(k.col, k.row) for k in v.keys if k.moving] == [(2, 2)]
    tap(c, clk, 4, 3)  # S14 on L4
    assert keys(c)[13] == before[6] and keys(c)[6] == before[13] and c.mgr.slots[13].pinned
    assert ads[Mode.DESKTOP].actions == []
    assert c.view().sub.pin_info.startswith("钉扎 1/15")
    knob(c, 2)  # L1 shows the same arrangement on the same physical keys
    v1 = {(k.col, k.row): k for k in c.view().keys}
    assert v1[slot_pos(13)].pinned and v1[slot_pos(13)].title == c.mgr.session_at(13).title
    hold(c, clk, 2, 3)  # S12 picked up on L1 ...
    tap(c, clk, 1, 1)  # ... placed on S1 (L1 only)
    assert keys(c)[0] == before[11] and keys(c)[11] == before[0] and c.mgr.slots[0].pinned


def test_l1_pick_up_l4_press_after_page_switch_is_cancelled(env):
    c, clk, ads, _ = env
    before = keys(c)
    pick(c, clk, 7)  # S8 on L1
    knob(c, 2)  # to L4: cancels
    tap(c, clk, 3, 3)  # S13 on L4: an ordinary tap now (select + focus)
    assert keys(c) == before and not any(pins(c)) and len(focus_calls(ads)) == 1


# ------------------------------------------------------------------ persistence / stickiness
def test_placement_persists_and_survives_restart_and_polls(env):
    c, clk, ads, saved = env
    before = keys(c)
    pick(c, clk, 14)
    tap(c, clk, 1, 1)  # S15 -> S1
    st = saved[-1]
    c2 = Controller(DeckConfig(), full_adapters(), state=st, clock=clk, sim=True)
    c2.refresh()
    assert keys(c2) == keys(c) and keys(c2)[0] == before[14] and c2.mgr.slots[0].pinned
    for _ in range(3):
        c2.refresh()
    assert keys(c2) == keys(c)


def test_per_mode_memory_kept(env):
    c, clk, ads, _ = env
    c.switch_mode(Mode.TMUX)
    tm_before = keys(c)
    pick(c, clk, 0)
    tap(c, clk, 2, 1)  # tmux S1 <-> S2
    tm_after = keys(c)
    assert tm_after[0] == tm_before[1] and tm_after[1] == tm_before[0]
    c.switch_mode(Mode.DESKTOP)
    assert not any(pins(c))  # Desktop slots untouched
    c.switch_mode(Mode.TMUX)
    assert keys(c) == tm_after and c.mgr.slots[1].pinned


# ------------------------------------------------------------------ reconnect / wake
def test_drop_gestures_cancels_pick_up(env):
    c, clk, ads, _ = env
    before = keys(c)
    pick(c, clk, 2)
    c.on_key(5, 4, True)  # a MODE press in flight too
    c.drop_gestures()
    assert c.moving is None and not c.presses and c.log[-1] == "已取消移动"
    c.on_key(5, 4, False)  # its release after the reconnect does nothing
    assert keys(c) == before and c.mode == Mode.DESKTOP and ads[Mode.DESKTOP].actions == []


class _FakePower:
    def __init__(self):
        self.queue = []

    def poll(self):
        q, self.queue = self.queue, []
        return q


def test_wake_drops_pick_up():
    clk = FakeClock()
    c = _ctl(clk, full_adapters())
    pw = _FakePower()
    d = Daemon(c, Renderer(DeviceLayout.default()), None, clock=clk, sink=lambda b: None, power=pw)
    d.step(force=True)
    pick(c, clk, 1)
    pw.queue = ["display_sleep"]
    d.check_power()
    clk.advance(2.0)
    pw.queue = ["display_wake"]
    with pytest.raises(WakeReconnect):
        d.check_power()
    assert c.moving is None and not c.presses


def test_supervisor_reconnect_drops_pick_up():
    from mk20deck.supervisor import Supervisor, default_renderer_factory
    from mk20deck.transport import MockDevice
    cfg = DeckConfig()
    cfg.preamble_flush = 0
    cfg.ack_timeout_s = 0.2
    cfg.heartbeat_s = 0.05
    cfg.poll_interval_s = 0.5
    clk = FakeClock()
    c = _ctl(clk, full_adapters())
    pick(c, clk, 1)  # picked up before the (re)connect
    sup = Supervisor(c, lambda: ["/dev/mock"], lambda port: MockDevice(), default_renderer_factory(cfg), cfg,
                     retry_s=0.05)
    stop = threading.Event()
    th = threading.Thread(target=sup.run, args=(stop,), daemon=True)
    th.start()
    end = time.monotonic() + 3
    while time.monotonic() < end and sup.stats.connects < 1:
        time.sleep(0.01)
    stop.set()
    th.join(3)
    assert sup.stats.connects >= 1 and c.moving is None and "已取消移动" in c.log


def test_daemon_wakes_for_move_timeout_without_animation():
    clk = FakeClock()
    c = _ctl(clk, full_adapters())
    d = Daemon(c, Renderer(DeviceLayout.default(), animations=False), None, clock=clk, sink=lambda b: None,
               poll_interval=3600, heartbeat=3600)
    d.step(force=True)
    pick(c, clk, 1)
    d.step(force=True)
    assert d.next_wait() <= MOVE_TIMEOUT + 1e-6
    clk.advance(MOVE_TIMEOUT + 0.01)
    d.step()
    assert c.moving is None


# ------------------------------------------------------------------ render
def test_moving_key_face_and_fps_budget(env):
    c, clk, ads, _ = env
    pick(c, clk, 2)
    v = c.view()
    kv = next(k for k in v.keys if k.moving)
    assert MOVE_FPS <= key_fps(kv, 10.0, True) <= MAX_FPS and key_fps(kv, 10.0, False) == 0
    for st in (State.IDLE, State.UNKNOWN, State.NEEDS_APPROVAL):  # static / gated states still march
        assert key_fps(KeyView(1, 1, Role.AGENT, state=st, title="x", moving=True), 10.0, True) == MOVE_FPS
    r = Renderer(DeviceLayout.default())
    assert 0 < r.desired_fps(v, clk.t) <= MAX_FPS
    face = render_key(kv, 10.0, clk.t, animations=False)
    plain = render_key(type(kv)(**{**kv.__dict__, "moving": False}), 10.0, clk.t, animations=False)
    assert face.size == (128, 128) and face.tobytes() != plain.tobytes()
    px = face.load()
    pink = sum(1 for x in range(128) for y in (2, 3, 4) if px[x, y] == MOVE_PINK)
    assert pink > 20  # dashed pink border along the top edge
    a = render_key(kv, 10.0, 0.0, animations=True).tobytes()
    b = render_key(kv, 10.0, 0.25, animations=True).tobytes()
    assert a != b  # the dashes march when animations are on


def test_moving_lost_pin_face_renders(env):
    c, clk, ads, _ = env
    pick(c, clk, 0)
    tap(c, clk, 1, 1)  # pin S1
    gone = c.mgr.slots[0].key
    ads[Mode.DESKTOP].sessions = [s for s in ads[Mode.DESKTOP].sessions if s.sid.key() != gone]
    c.refresh()
    pick(c, clk, 0)
    v = c.view()
    kv = next(k for k in v.keys if k.moving)
    assert kv.lost and v.sub.moving.startswith("移动『失联钉扎』")
    assert render_key(kv, 1.0, clk.t, animations=False).size == (128, 128)
    tap(c, clk, 1, 1)
    assert c.mgr.slots[0] is None and c.log[-1] == "已解除失联钉扎"


def test_subscreen_moving_banner(env):
    c, clk, ads, _ = env
    pick(c, clk, 2)
    sub = c.view().sub
    img = render_sub(sub, clk.t)
    assert img.size == (428, 142)
    px = img.load()
    assert sum(1 for x in range(10, 418) if px[x, 95] == MOVE_PINK) > 300  # the instruction strip
    c.on_knob(1, "down")
    c.on_knob(1, "up")
    sub = c.view().sub
    assert sub.moving == "" and sub.toast == "已取消移动"
    img = render_sub(sub, clk.t)
    px = img.load()
    assert sum(1 for x in range(10, 418) if px[x, 95] == MOVE_PINK) == 0


def test_help_texts_describe_new_gesture(env):
    import inspect
    from mk20deck.render import subscreen
    src = inspect.getsource(subscreen)
    assert "长按钉扎" not in src and "长按拿起移动/钉扎" in src
    c, clk, ads, _ = env
    c.page = "L4"
    assert c.view().sub.pin_info == "钉扎 0/15 · 长按拿起，点自己=钉扎/解除 · S1–S5 在 L1"


def test_move_sim_shots(tmp_path):
    made = move_shots(str(tmp_path))
    assert [n for n, _ in made] == ["move-01-L1-picked-up.png", "move-02-L1-placed-swap.png",
                                    "move-03-L4-picked-up.png", "move-04-L4-tap-self-pin.png",
                                    "move-05-K1-cancel.png", "move-06-L1-placed-empty.png"]
    assert all((tmp_path / n).stat().st_size > 10_000 for n, _ in made)


def test_role_of_every_slot_on_both_pages_can_be_picked_up(env):
    c, clk, ads, _ = env
    for page, idxs in (("L1", range(N_SLOTS)), ("L4", range(5, N_SLOTS))):
        for i in idxs:
            c.page = page
            col, row = slot_pos(i)
            hold(c, clk, col, row)
            assert c.moving is not None and c.moving.src == i, (page, i)
            knob(c, 1)  # cancel
            assert c.moving is None
    assert not any(pins(c)) and ads[Mode.DESKTOP].actions == []


def test_moving_idle_key_only_the_border_moves():
    """release/1.5.0 policy (2026-10-22): only needs-me states animate; the pick-up highlight must not
    bring back idle/running face or border animation — only the dashed border marches."""
    for st in (State.IDLE, State.RUNNING, State.THINKING):
        kv = KeyView(1, 1, Role.AGENT, state=st, title="会话", selected=True, moving=True)
        a = render_key(kv, 10.0, 0.0, animations=True)
        b = render_key(kv, 10.0, 0.25, animations=True)
        assert a.tobytes() != b.tobytes()  # the dashes march
        inner = (8, 8, 120, 92)  # mascot, badge, name plate: unchanged between frames
        if st == State.IDLE:
            assert a.crop(inner).tobytes() == b.crop(inner).tobytes(), st
