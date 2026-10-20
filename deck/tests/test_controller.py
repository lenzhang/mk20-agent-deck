import pytest

from mk20deck.adapters.mock import MockAdapter
from mk20deck.config import DeckConfig
from mk20deck.controller import Controller, Role, role_at
from mk20deck.demo_data import DESKTOP_CAPS, demo_sessions
from mk20deck.model import Capabilities, Mode, State, Tool
from mk20deck.sim import FakeClock, demo_adapters


@pytest.fixture
def env():
    clk = FakeClock()
    ads = demo_adapters()
    saved = []
    c = Controller(DeckConfig(), ads, clock=clk, wall=lambda: 1.0, sim=True, on_state_change=saved.append)
    c.refresh()
    return c, clk, ads, saved


def tap(c, clk, col, row, hold=0.05):
    c.on_key(col, row, True)
    clk.advance(hold)
    c.tick()
    c.on_key(col, row, False)


def hold(c, clk, col, row, dur):
    c.on_key(col, row, True)
    t = 0.0
    while t < dur:
        clk.advance(0.05)
        t += 0.05
        c.tick()
    c.on_key(col, row, False)


def test_l1_l4_same_position_except_row3():
    for row in range(1, 5):
        for col in range(1, 6):
            a, b = role_at("L1", col, row), role_at("L4", col, row)
            if row == 3 and col <= 4:
                assert a.role == Role.SKILL and b.role in (Role.MODE_SELECT, Role.BACK_L1)
            else:
                assert a == b
    assert role_at("L1", 1, 4).role == Role.PTT and role_at("L1", 5, 4).role == Role.MODE


def test_default_mode_desktop_and_click_only_selects(env):
    c, clk, ads, _ = env
    assert c.mode == Mode.DESKTOP
    tap(c, clk, 1, 1)
    assert c.mgr.selected is not None
    assert ads[Mode.DESKTOP].actions == []  # single click: no focus / no external action


def test_double_tap_focuses_and_clears_local_done(env):
    c, clk, ads, _ = env
    # find slot holding the done_unread demo session
    idx = next(i for i in range(8) if c.mgr.session_at(i) and c.mgr.session_at(i).state == State.DONE_UNREAD)
    col, row = idx % 4 + 1, idx // 4 + 1
    tap(c, clk, col, row)
    clk.advance(0.2)
    tap(c, clk, col, row)
    assert ads[Mode.DESKTOP].actions and ads[Mode.DESKTOP].actions[-1][0] == "focus"
    assert c.mgr.session_at(idx).state == State.IDLE
    c.refresh()  # backend still reports unread with same marker -> stays locally cleared
    assert c.mgr.session_at(idx).state == State.IDLE


def test_slow_double_tap_does_not_focus(env):
    c, clk, ads, _ = env
    tap(c, clk, 1, 1)
    clk.advance(0.5)
    tap(c, clk, 1, 1)
    assert ads[Mode.DESKTOP].actions == []


def test_long_press_agent_pins_without_click_side_effects(env):
    c, clk, ads, saved = env
    hold(c, clk, 2, 1, 0.7)
    assert c.mgr.slots[1].pinned
    assert ads[Mode.DESKTOP].actions == []
    assert saved  # persisted


def test_accept_decline_p0_no_action(env):
    c, clk, ads, _ = env
    tap(c, clk, 1, 1)
    tap(c, clk, 5, 1)
    tap(c, clk, 5, 2)
    assert ads[Mode.DESKTOP].actions == []
    assert "App 内处理" in c.log[-1]


def test_mode_short_press_cycles_on_release_no_skip(env):
    c, clk, ads, saved = env
    c.on_key(5, 4, True)
    assert c.mode == Mode.DESKTOP  # decided on release
    clk.advance(0.1)
    c.tick()
    c.on_key(5, 4, False)
    assert c.mode == Mode.HERDR  # unavailable but not skipped
    assert c.view().keys[19].label == "HERD"
    assert "不可用" in c.log[-1]
    tap(c, clk, 5, 4)
    assert c.mode == Mode.TMUX
    tap(c, clk, 5, 4)
    assert c.mode == Mode.DESKTOP
    assert saved[-1]["mode"] == "codex_desktop"


def test_mode_long_press_opens_l4_and_swallows_release(env):
    c, clk, ads, _ = env
    hold(c, clk, 5, 4, 0.7)
    assert c.page == "L4" and c.mode == Mode.DESKTOP
    tap(c, clk, 3, 3)  # TMUX direct select
    assert c.mode == Mode.TMUX and c.page == "L1"


def test_per_mode_selection_restored(env):
    c, clk, ads, _ = env
    tap(c, clk, 2, 2)
    sel = c.mgr.selected
    tap(c, clk, 5, 4)
    tap(c, clk, 5, 4)  # tmux
    assert c.mgr.selected is None  # no inherit across modes
    tap(c, clk, 5, 4)  # back to desktop
    assert c.mgr.selected == sel


def test_restart_restores_state(env):
    c, clk, ads, saved = env
    tap(c, clk, 2, 1)
    hold(c, clk, 3, 1, 0.7)
    tap(c, clk, 5, 4)
    st = saved[-1]
    c2 = Controller(DeckConfig(), demo_adapters(), state=st, clock=clk, sim=True)
    c2.refresh(Mode.DESKTOP)
    assert c2.mode == Mode.HERDR
    m = c2.managers[Mode.DESKTOP]
    assert m.slots[2].pinned and m.selected == c.managers[Mode.DESKTOP].selected


def test_stop_unsupported_desktop_explains(env):
    c, clk, ads, _ = env
    tap(c, clk, 1, 1)
    hold(c, clk, 2, 4, 0.8)
    assert ads[Mode.DESKTOP].actions == []
    assert "P0" in c.log[-1] or "不可达" in c.log[-1]


def _stop_env():
    clk = FakeClock()
    caps = Capabilities(focus=None, new_session=None, stop=None)
    ses = demo_sessions()
    ads = demo_adapters()
    ads[Mode.DESKTOP] = MockAdapter(Mode.DESKTOP, ses, caps=caps)
    c = Controller(DeckConfig(), ads, clock=clk, sim=True)
    c.refresh()
    idx = next(i for i in range(8) if c.mgr.session_at(i).state == State.RUNNING)
    return c, clk, ads, (idx % 4 + 1, idx // 4 + 1)


def test_stop_long_press_threshold_once():
    c, clk, ads, (col, row) = _stop_env()
    tap(c, clk, col, row)
    hold(c, clk, 2, 4, 0.55)
    assert ads[Mode.DESKTOP].actions == []
    assert c.log[-1] == "长按停止"
    hold(c, clk, 2, 4, 1.5)
    assert [a for a in ads[Mode.DESKTOP].actions if a[0] == "stop"] and \
        len([a for a in ads[Mode.DESKTOP].actions if a[0] == "stop"]) == 1


def test_stop_progress_segments_visible():
    c, clk, ads, (col, row) = _stop_env()
    tap(c, clk, col, row)
    c.on_key(2, 4, True)
    clk.advance(0.31)
    c.tick()
    kv = next(k for k in c.view().keys if k.role == Role.STOP)
    assert kv.progress == 2
    c.on_key(2, 4, False)


def test_stop_cancelled_by_reselect_or_mode_switch():
    c, clk, ads, (col, row) = _stop_env()
    tap(c, clk, col, row)
    c.on_key(2, 4, True)
    clk.advance(0.3)
    other = (1, 1) if (col, row) != (1, 1) else (2, 1)
    tap(c, clk, *other)
    clk.advance(0.5)
    c.tick()
    c.on_key(2, 4, False)
    assert not [a for a in ads[Mode.DESKTOP].actions if a[0] == "stop"]


def test_stop_refuses_idle_target():
    c, clk, ads, _ = _stop_env()
    idx = next(i for i in range(8) if c.mgr.session_at(i).state == State.IDLE)
    tap(c, clk, idx % 4 + 1, idx // 4 + 1)
    hold(c, clk, 2, 4, 0.8)
    assert not [a for a in ads[Mode.DESKTOP].actions if a[0] == "stop"]
    assert "无可靠运行目标" in c.log[-1]


def test_ptt_is_overlay_only_and_blocks_focus(env):
    c, clk, ads, _ = env
    tap(c, clk, 1, 1)
    c.on_key(1, 4, True)
    assert c.ptt_held and c.view().keys[15].active
    tap(c, clk, 4, 4)  # focus key while PTT held
    assert ads[Mode.DESKTOP].actions == []
    tap(c, clk, 5, 4)  # mode switch does not release PTT
    assert c.ptt_held
    c.on_key(1, 4, False)
    assert not c.ptt_held


def test_skill_degrades_to_new_thread_with_badge(env):
    c, clk, ads, _ = env
    v = c.view()
    skills = [k for k in v.keys if k.role == Role.SKILL]
    assert all(k.badge == "+新" for k in skills)
    assert v.sub.skill_note == "技能将在新线程预填，不进入当前会话"
    tap(c, clk, 1, 3)
    assert "未选中" in c.log[-1] and ads[Mode.DESKTOP].actions == []
    tap(c, clk, 1, 1)
    tap(c, clk, 1, 3)
    assert ads[Mode.DESKTOP].actions[-1][0] == "skill"


def test_knobs(env):
    c, clk, ads, _ = env
    c.on_knob(1, "down")
    c.on_knob(1, "up")
    assert "未选中" in c.log[-1] and ads[Mode.DESKTOP].actions == []
    tap(c, clk, 1, 1)
    c.on_knob(2, "down")
    clk.advance(0.1)
    c.on_knob(2, "up")
    assert c.page == "L4"
    c.on_knob(2, "down")
    clk.advance(0.7)
    c.tick()
    c.on_knob(2, "up")
    assert c.page == "L1"
    c.on_knob(1, "down")
    clk.advance(0.7)
    c.tick()
    c.on_knob(1, "up")
    assert c.page == "L4"
    sel = c.mgr.selected
    c.on_knob(2, "cw")
    assert c.scroll == 1 and c.mgr.selected == sel  # K2 rotation never changes selection


def test_status_limited_always_visible_in_desktop(env):
    c, clk, ads, _ = env
    assert "状态受限" in c.view().sub.limited


def test_selected_disappears_clears_and_grays(env):
    c, clk, ads, _ = env
    tap(c, clk, 1, 1)
    sel = c.mgr.selected
    ads[Mode.DESKTOP].sessions = [s for s in ads[Mode.DESKTOP].sessions if s.sid.key() != sel]
    c.refresh()
    assert c.mgr.selected is None and c.view().sub.selection_lost
    tap(c, clk, 4, 4)
    assert "未选中" in c.log[-1]


K1_FIRST = [State.NEEDS_APPROVAL, State.NEEDS_INPUT, State.ERROR, State.DONE_UNREAD]


def test_k1_rotation_walks_attention_order_then_slots(env):
    c, clk, ads, saved = env
    order = c.k1_order()
    states = [c.mgr.session_at(i).state for i in order]
    assert states[:4] == K1_FIRST
    rest = order[4:]
    assert rest == sorted(rest) and len(order) == 8  # others by slot order
    seen = []
    for _ in range(9):
        c.on_knob(1, "cw")
        seen.append(c.mgr.selected)
    keys = [c.mgr.slots[i].key for i in order]
    assert seen == keys + keys[:1]  # wraps around
    c.on_knob(1, "ccw")
    assert c.mgr.selected == keys[-1]
    assert saved and saved[-1]["modes"]["codex_desktop"]["selected"] == keys[-1]  # persisted like a key select
    assert ads[Mode.DESKTOP].actions == []  # rotation never acts on the agent
    assert not any("effort" in m or "此会话不支持" in m for m in c.log)


def test_k1_order_follows_state_changes(env):
    c, clk, ads, _ = env
    c.on_knob(1, "cw")
    first = c.mgr.selected_session()
    assert first.state == State.NEEDS_APPROVAL
    first.state = State.RUNNING  # handled in the App
    c.refresh()
    keys = [c.mgr.slots[i].key for i in c.k1_order()]
    pos = keys.index(first.sid.key())
    assert pos >= 3  # it left the attention block
    c.on_knob(1, "ccw")  # one step back in the re-sorted ring
    assert c.mgr.selected == keys[pos - 1]
    c.mgr.selected = None
    c.on_knob(1, "cw")
    assert c.mgr.selected_session().state == State.NEEDS_INPUT  # now the most urgent one


def test_k1_press_focuses_selected_and_clears_done(env):
    c, clk, ads, _ = env
    for _ in range(4):
        c.on_knob(1, "cw")
    s = c.mgr.selected_session()
    assert s.state == State.DONE_UNREAD
    c.on_knob(1, "down")
    clk.advance(0.1)
    c.on_knob(1, "up")
    assert ads[Mode.DESKTOP].actions == [("focus", s.sid.key())]
    assert s.state == State.IDLE and c.page == "L1"
    c.on_key(1, 4, True)  # PTT held: never bring a window forward
    c.on_knob(1, "down")
    c.on_knob(1, "up")
    assert "PTT" in c.log[-1] and len(ads[Mode.DESKTOP].actions) == 1
    c.on_key(1, 4, False)


def test_k1_long_press_still_l4_and_l4_press_settings(env):
    c, clk, ads, _ = env
    c.on_knob(1, "down")
    clk.advance(0.7)
    c.tick()
    c.on_knob(1, "up")
    assert c.page == "L4" and ads[Mode.DESKTOP].actions == []
    c.on_knob(1, "down")
    c.on_knob(1, "up")
    assert c.page == "LS"


def test_k1_rotation_without_sessions(env):
    c, clk, ads, _ = env
    c.switch_mode(Mode.HERDR)  # herdr not installed → no sessions
    c.on_knob(1, "cw")
    assert c.log[-1] == "无会话可选" and c.mgr.selected is None


def test_cross_mode_attention_counts(env):
    c, clk, ads, _ = env
    tm = ads[Mode.TMUX].sessions
    tm[0].state, tm[1].state, tm[2].state = State.NEEDS_APPROVAL, State.DONE_UNREAD, State.RUNNING
    v0 = c.version
    c.apply_background(Mode.TMUX, list(tm))
    c.apply_background(Mode.HERDR, None, "herdr 未安装")
    assert c.version > v0
    v = c.view()
    assert v.sub.other_modes == "tmux 2 待处理"
    mode_key = next(k for k in v.keys if k.role == Role.MODE)
    assert mode_key.badge == "2" and mode_key.badge_alert
    v1 = c.version
    c.apply_background(Mode.TMUX, list(tm))  # same result: no redraw
    assert c.version == v1
    c.apply_background(Mode.DESKTOP, [])  # the current mode is owned by refresh(), never by a late bg result
    assert Mode.DESKTOP not in c.bg
    c.page = "L4"
    keys = {k.mode: k for k in c.view().keys if k.role == Role.MODE_SELECT}
    assert keys[Mode.TMUX].badge == "2" and keys[Mode.TMUX].badge_alert
    assert keys[Mode.HERDR].badge == "" and keys[Mode.DESKTOP].badge == ""


def test_cross_mode_respects_local_clear_and_switch_snapshot(env):
    c, clk, ads, _ = env
    done = next(s for s in c.mgr.sessions.values() if s.state == State.DONE_UNREAD)
    c._focus(done)  # local clear in Desktop
    c.switch_mode(Mode.TMUX)
    # leaving Desktop: its count is known immediately (needs_approval + needs_input + error; done was cleared)
    assert c.bg[Mode.DESKTOP].attention == 3 and c.bg[Mode.DESKTOP].urgent == 3
    assert c.view().sub.other_modes == "桌面 3 待处理"
    done.state = State.DONE_UNREAD  # the backend still reports the same completion
    c.apply_background(Mode.DESKTOP, ads[Mode.DESKTOP].poll())
    assert c.bg[Mode.DESKTOP].attention == 3  # the locally cleared done_unread is not counted again
    c.switch_mode(Mode.DESKTOP)
    assert Mode.DESKTOP not in c.bg and c.view().sub.other_modes == ""


def test_background_results_out_of_order_dropped(env):
    c, clk, ads, _ = env
    tm = ads[Mode.TMUX].sessions
    tm[0].state = State.NEEDS_APPROVAL
    c.apply_background(Mode.TMUX, list(tm), t=5.0)
    c.apply_background(Mode.TMUX, [], t=4.0)  # a poll that started earlier but finished later
    assert c.bg[Mode.TMUX].attention == 1
    c.apply_background(Mode.TMUX, [], t=6.0)
    assert c.bg[Mode.TMUX].attention == 0 and c.view().sub.other_modes == ""
