import pytest

from mk20deck.adapters.mock import MockAdapter
from mk20deck.config import DeckConfig, load_config
from mk20deck.controller import (BOTTOM_ROW, DEVICE_HID_FB, DEVICE_HID_ROLES, MODE_KEY_FB, Controller, Role,
                                 role_at, slot_pos)
from mk20deck.demo_data import demo_sessions
from mk20deck.model import Capabilities, Mode, State
from mk20deck.sim import FakeClock, demo_adapters, full_adapters, layout_shots
from mk20deck.slots import N_SLOTS


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


def focus_calls(ads, mode=Mode.DESKTOP):
    return [a for a in ads[mode].actions if a[0] == "focus"]


# ------------------------------------------------------------------ role map (PRD 1.5 L1/L4)
L1_MAP = [
    ["S1", "S2", "S3", "S4", "S5"],
    ["S6", "S7", "S8", "S9", "S10"],
    ["S11", "S12", "S13", "S14", "S15"],
    ["ptt", "delete", "ok", "no", "mode"],
]
L4_MAP = [
    ["mode_select:codex_desktop", "mode_select:herdr", "mode_select:tmux", "new", "back_l1"],
    L1_MAP[1], L1_MAP[2], L1_MAP[3],
]


def _name(kr):
    if kr.role == Role.AGENT:
        return f"S{kr.index + 1}"
    if kr.role == Role.MODE_SELECT:
        return f"mode_select:{kr.mode.value}"
    return kr.role.value


@pytest.mark.parametrize("page,table", [("L1", L1_MAP), ("L4", L4_MAP)])
def test_role_map_every_key(page, table):
    for row in range(1, 5):
        for col in range(1, 6):
            assert _name(role_at(page, col, row)) == table[row - 1][col - 1], (page, col, row)
    assert role_at(page, 0, 1) is None and role_at(page, 6, 1) is None and role_at(page, 1, 5) is None


def test_l4_rows_2_to_4_same_position_same_meaning():
    for row in (2, 3, 4):
        for col in range(1, 6):
            assert role_at("L1", col, row) == role_at("L4", col, row)
    for i in range(N_SLOTS):
        assert role_at("L1", *slot_pos(i)).index == i
    assert N_SLOTS == 15


def test_device_hid_fb_numbers_match_bottom_row():
    """fb = (row - 1) * 5 + col: fb16..fb19 are keyboard-side HID, fb20 the forwarded mode key."""
    for fb, what in DEVICE_HID_FB.items():
        col, row = (fb - 1) % 5 + 1, (fb - 1) // 5 + 1
        assert row == 4
        r = role_at("L1", col, row).role
        assert r == BOTTOM_ROW[col - 1] and (r == Role.PTT or r in DEVICE_HID_ROLES), (fb, what)
    assert set(DEVICE_HID_FB) == {16, 17, 18, 19} and MODE_KEY_FB == 20
    assert role_at("L1", 5, 4).role == Role.MODE == role_at("L4", 5, 4).role
    assert DEVICE_HID_ROLES == {Role.DELETE, Role.OK, Role.NO}


def test_removed_keys_are_gone():
    names = {r.value for r in Role}
    for gone in ("accept", "decline", "skill", "send", "stop", "focus"):
        assert gone not in names


# ------------------------------------------------------------------ single tap = select + focus
def test_single_tap_selects_and_focuses(env):
    c, clk, ads, saved = env
    c.on_key(1, 1, True)
    assert c.mgr.selected == c.mgr.slots[0].key  # selected on press (sub-screen feedback)
    assert focus_calls(ads) == []  # focus waits for the release decision (long press = pin)
    clk.advance(0.05)
    c.on_key(1, 1, False)
    assert focus_calls(ads) == [("focus", c.mgr.slots[0].key)]
    assert saved and saved[-1]["modes"]["codex_desktop"]["selected"] == c.mgr.slots[0].key


def test_every_tap_focuses_no_double_tap_gesture(env):
    c, clk, ads, _ = env
    tap(c, clk, 2, 1)
    clk.advance(0.1)
    tap(c, clk, 2, 1)  # quick second tap: just another select + focus
    clk.advance(1.0)
    tap(c, clk, 3, 1)
    assert [k for _, k in focus_calls(ads)] == [c.mgr.slots[1].key] * 2 + [c.mgr.slots[2].key]


def test_tap_on_done_clears_local_reminder(env):
    c, clk, ads, _ = env
    idx = next(i for i in range(N_SLOTS) if c.mgr.session_at(i) and c.mgr.session_at(i).state == State.DONE_UNREAD)
    tap(c, clk, *slot_pos(idx))
    assert focus_calls(ads)[-1][1] == c.mgr.slots[idx].key
    assert c.mgr.session_at(idx).state == State.IDLE
    c.refresh()  # backend still reports unread with the same marker -> stays locally cleared
    assert c.mgr.session_at(idx).state == State.IDLE


def test_long_press_pins_without_focus(env):
    c, clk, ads, saved = env
    hold(c, clk, 2, 1, 0.7)
    assert c.mgr.slots[1].pinned and c.mgr.selected == c.mgr.slots[1].key
    assert ads[Mode.DESKTOP].actions == []
    assert saved and saved[-1]["modes"]["codex_desktop"]["slots"][1]["pinned"]
    hold(c, clk, 2, 1, 0.7)
    assert not c.mgr.slots[1].pinned and ads[Mode.DESKTOP].actions == []


def test_tap_does_not_focus_when_target_changed_during_press(env):
    c, clk, ads, _ = env
    c.on_key(3, 2, True)  # S8 (last in K1 order)
    c.on_knob(1, "cw")  # K1 moves the selection (wraps to S1) while the key is down
    clk.advance(0.05)
    c.on_key(3, 2, False)
    assert c.mgr.selected == c.mgr.slots[0].key and focus_calls(ads) == []
    c.on_key(2, 1, True)
    c.on_knob(2, "down")
    c.on_knob(2, "up")  # page toggle mid-press: the press-time role still owns the release
    c.on_key(2, 1, False)
    assert c.page == "L4" and focus_calls(ads) == [("focus", c.mgr.slots[1].key)]


def test_mode_switch_mid_press_cancels_tap(env):
    c, clk, ads, _ = env
    c.on_key(1, 1, True)
    c.switch_mode(Mode.TMUX)
    c.on_key(1, 1, False)
    assert ads[Mode.DESKTOP].actions == [] and ads[Mode.TMUX].actions == []


def test_empty_slot_and_lost_pin_taps_do_nothing(env):
    c, clk, ads, _ = env
    tap(c, clk, 5, 3)  # S15: the default demo has 8 sessions
    assert c.log[-1] == "空槽" and c.mgr.selected is None and ads[Mode.DESKTOP].actions == []
    hold(c, clk, 1, 1, 0.7)  # pin S1, then its session vanishes
    gone = c.mgr.slots[0].key
    ads[Mode.DESKTOP].sessions = [s for s in ads[Mode.DESKTOP].sessions if s.sid.key() != gone]
    c.refresh()
    assert c.mgr.slots[0].lost
    tap(c, clk, 1, 1)
    assert c.log[-1] == "失联钉扎 · 长按解除" and ads[Mode.DESKTOP].actions == []


def test_focus_unsupported_tap_only_selects_with_note(env):
    c, clk, ads, _ = env
    c.switch_mode(Mode.TMUX)  # demo tmux: no terminal App bound
    n = len(c.log)
    tap(c, clk, 1, 1)
    assert c.mgr.selected is not None and ads[Mode.TMUX].actions == []
    assert len(c.log) == n  # no toast on every tap ...
    v = c.view()
    assert v.sub.note == "单击只选中 · 置前不可用：未绑定终端 App"  # ... the sub-screen says why
    c.on_knob(1, "down")
    c.on_knob(1, "up")  # an explicit K1 focus still explains
    assert "未绑定终端 App" in c.log[-1]
    c.switch_mode(Mode.DESKTOP)
    assert c.view().sub.note is None


def test_ptt_is_overlay_only_and_blocks_focus(env):
    c, clk, ads, _ = env
    c.on_key(1, 4, True)
    assert c.ptt_held and c.view().keys[15].active
    tap(c, clk, 1, 1)  # slot tap while PTT held: selects, never brings a window forward
    assert c.mgr.selected is not None and ads[Mode.DESKTOP].actions == []
    assert "PTT" in c.log[-1]
    tap(c, clk, 5, 4)  # mode switch does not release PTT
    assert c.ptt_held
    c.on_key(1, 4, False)
    assert not c.ptt_held


# ------------------------------------------------------------------ device-side HID keys
@pytest.mark.parametrize("page", ["L1", "L4"])
def test_device_hid_keys_ignored(env, page):
    c, clk, ads, saved = env
    tap(c, clk, 1, 2)  # S6 (visible on both pages)
    c.page = page
    sel, n_saved, n_log, n_act = c.mgr.selected, len(saved), len(c.log), len(ads[Mode.DESKTOP].actions)
    v0 = c.version
    for col in (2, 3, 4):
        c.on_key(col, 4, True)
        assert not c.presses
        hold(c, clk, col, 4, 0.8)  # a held DELETE repeats on the keyboard; the deck never long-presses
        c.on_key(col, 4, False)  # stray up without down
    assert (c.page, c.mode, c.mgr.selected) == (page, Mode.DESKTOP, sel)
    assert len(c.log) == n_log and len(saved) == n_saved and len(ads[Mode.DESKTOP].actions) == n_act
    assert c.version == v0 and not c.presses


def test_device_hid_key_faces(env):
    c, clk, ads, _ = env
    for page in ("L1", "L4"):
        c.page = page
        keys = {(k.col, k.row): k for k in c.view().keys}
        assert (keys[(1, 4)].role, keys[(1, 4)].label) == (Role.PTT, "按住说话")
        assert (keys[(2, 4)].role, keys[(2, 4)].label) == (Role.DELETE, "删除")
        assert (keys[(3, 4)].role, keys[(3, 4)].label) == (Role.OK, "发送 / 可以")
        assert (keys[(4, 4)].role, keys[(4, 4)].label) == (Role.NO, "不行 Esc")
        assert keys[(2, 4)].sublabel == "按住连删" and keys[(3, 4)].sublabel == "Return"
        assert keys[(4, 4)].sublabel == "可打断 Claude/codex"
        assert all(keys[(col, 4)].disabled is None for col in (2, 3, 4))  # always live: no backend involved


# ------------------------------------------------------------------ mode key / L4
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


def test_mode_long_press_opens_l4_and_mode_direct_select(env):
    c, clk, ads, _ = env
    hold(c, clk, 5, 4, 0.7)
    assert c.page == "L4" and c.mode == Mode.DESKTOP
    keys = {(k.col, k.row): k for k in c.view().keys}
    assert [keys[(i, 1)].label for i in range(1, 6)] == ["DESK", "HERD", "TMUX", "新会话", "回 L1"]
    assert keys[(1, 1)].active and not keys[(2, 1)].active
    tap(c, clk, 3, 1)  # TMUX direct select
    assert c.mode == Mode.TMUX and c.page == "L1"


def test_l4_back_and_new_session(env):
    c, clk, ads, _ = env
    tap(c, clk, 1, 1)
    c.page = "L4"
    tap(c, clk, 4, 1)  # ＋新会话 in the current mode, cwd of the confirmed selection
    assert ads[Mode.DESKTOP].actions[-1] == ("new", "/Users/dev/workspace/mk20-agent-deck")
    assert c.page == "L4"
    tap(c, clk, 5, 1)
    assert c.page == "L1" and c.log[-1] == "回到 L1"
    c.switch_mode(Mode.HERDR)
    c.page = "L4"
    assert next(k for k in c.view().keys if k.role == Role.NEW).disabled
    tap(c, clk, 4, 1)
    assert ads[Mode.HERDR].actions == []


def test_l4_slots_are_the_l1_slots(env):
    c, clk, ads, _ = env
    c.page = "L4"
    tap(c, clk, 2, 2)  # S7 on L4
    assert c.mgr.selected == c.mgr.slots[6].key and focus_calls(ads)[-1][1] == c.mgr.slots[6].key
    v = c.view()
    agents = [k for k in v.keys if k.role == Role.AGENT]
    assert len(agents) == 10 and {(k.col, k.row) for k in agents} == {slot_pos(i) for i in range(5, 15)}
    assert v.sub.pin_info == "钉扎 0/15 · 长按槽位钉扎/解除 · S1–S5 在 L1"
    n = len(focus_calls(ads))
    hold(c, clk, 2, 2, 0.7)  # pin S7 from L4 (long press: no focus)
    assert c.mgr.slots[6].pinned and len(focus_calls(ads)) == n
    assert c.view().sub.pin_info.startswith("钉扎 1/15")


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
    assert st["modes"]["codex_desktop"]["n_slots"] == 15 and len(st["modes"]["codex_desktop"]["slots"]) == 15
    c2 = Controller(DeckConfig(), demo_adapters(), state=st, clock=clk, sim=True)
    c2.refresh(Mode.DESKTOP)
    assert c2.mode == Mode.HERDR
    m = c2.managers[Mode.DESKTOP]
    assert m.slots[2].pinned and m.selected == c.managers[Mode.DESKTOP].selected


def test_old_8_slot_state_file_migrates_by_position():
    """Pre-1.5 state: A1..A4 = row 1, A5..A8 = row 2 (4 columns).  Same physical key afterwards."""
    ses = demo_sessions()
    keys = [s.sid.key() for s in ses]
    old = {"mode": "codex_desktop", "modes": {"codex_desktop": {
        "slots": [{"key": k, "pinned": i in (1, 5)} for i, k in enumerate(keys)],
        "selected": keys[6], "excluded": []}}}
    clk = FakeClock()
    c = Controller(DeckConfig(), demo_adapters(), state=old, clock=clk, sim=True)
    c.refresh()
    m = c.mgr
    for i, k in enumerate(keys):
        col, row = i % 4 + 1, i // 4 + 1  # where the session was on the old L1
        assert m.slots[role_at("L1", col, row).index].key == k
    assert m.slots[1].pinned and m.slots[6].pinned  # A2 → S2, A6 → S7
    assert m.slots[4] is None and m.slots[9] is None  # old 接受/拒绝 positions start empty
    assert m.selected == keys[6] and m.selected_session() is not None
    assert c.persist()["modes"]["codex_desktop"]["n_slots"] == 15


# ------------------------------------------------------------------ 15 slots
def test_fifteen_sessions_fill_every_slot():
    clk = FakeClock()
    c = Controller(DeckConfig(), full_adapters(), clock=clk, sim=True)
    c.refresh()
    assert all(c.mgr.session_at(i) is not None for i in range(N_SLOTS)) and c.mgr.overflow == 0
    v = c.view()
    agents = [k for k in v.keys if k.role == Role.AGENT]
    assert len(agents) == 15 and all(k.title for k in agents)
    assert len({k.state for k in agents}) >= 7  # mixed states
    assert sum(1 for k in agents if k.attention) == 1
    order = c.k1_order()
    assert len(order) == 15 and c.mgr.session_at(order[0]).state == State.NEEDS_APPROVAL


def test_overflow_beyond_fifteen_and_switch_toast():
    clk = FakeClock()
    ads = full_adapters()
    tm = demo_sessions(Mode.TMUX, n=15) + [s for s in demo_sessions(Mode.TMUX, n=4)]
    for i, s in enumerate(tm[15:]):
        s.sid = type(s.sid)(Mode.TMUX, f"%{50 + i}", s.sid.generation)
        s.state = State.IDLE
    ads[Mode.TMUX] = MockAdapter(Mode.TMUX, tm)
    c = Controller(DeckConfig(), ads, clock=clk, sim=True)
    c.refresh()
    c.switch_mode(Mode.TMUX)
    assert c.mgr.overflow == 4 and c.view().sub.overflow == 4
    assert c.log[-1] == "已切换到 tmux · 19 个会话"


def test_layout_sim_shots(tmp_path):
    made = layout_shots(str(tmp_path))
    assert [n for n, _ in made] == ["l1v2-01-L1-15-sessions-selected.png", "l1v2-02-L4.png",
                                    "l1v2-03-L1-tmux-mixed-tools.png"]
    assert all((tmp_path / n).stat().st_size > 10_000 for n, _ in made)


# ------------------------------------------------------------------ knobs (unchanged behaviour)
def test_knobs(env):
    c, clk, ads, _ = env
    c.on_knob(1, "down")
    c.on_knob(1, "up")
    assert "未选中" in c.log[-1] and ads[Mode.DESKTOP].actions == []
    c.on_knob(1, "cw")
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
    c.on_knob(1, "down")
    c.on_knob(1, "up")
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


# ------------------------------------------------------------------ sub-screen text
def test_subscreen_texts_describe_new_behaviour(env):
    from mk20deck.render.subscreen import render_sub
    import inspect
    from mk20deck.render import subscreen
    src = inspect.getsource(subscreen)
    assert "双击" not in src and "聚焦键" not in src and "技能" not in src
    assert "单击会话键：选中并置前" in src
    c, clk, ads, _ = env
    v = c.view()
    assert v.sub.session is None and v.sub.note is None and v.sub.pin_info == ""
    assert render_sub(v.sub, 0.0).size == (428, 142)
    tap(c, clk, 1, 1)
    assert c.view().sub.session is not None


def test_old_skills_config_still_loads(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("[[skills]]\nlabel = \"a\"\nprompt = \"b\"\n" * 4)
    cfg = load_config(str(p))
    assert [s.label for s in cfg.skills] == ["a"] * 4
    p.write_text("[[skills]]\nlabel = \"only label\"\n")  # malformed: ignored, never fatal
    cfg = load_config(str(p))
    assert len(cfg.skills) == 4 and cfg.skills[0].label == "审 PR"
    c = Controller(cfg, demo_adapters(), clock=FakeClock(), sim=True)
    c.refresh()
    assert not any(k.role.value == "skill" for k in c.view().keys)


# ------------------------------------------------------------------ cross-mode awareness
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


def test_cross_mode_counts_cover_all_fifteen_slots():
    clk = FakeClock()
    ads = full_adapters()
    c = Controller(DeckConfig(default_mode=Mode.TMUX), ads, clock=clk, wall=lambda: 1.0, sim=True)
    c.refresh()
    c.apply_background(Mode.DESKTOP, ads[Mode.DESKTOP].poll())
    # 15 Desktop sessions: approval + input + error + 2 done_unread (slots 9..15 included)
    assert c.bg[Mode.DESKTOP].attention == 5 and c.view().sub.other_modes == "桌面 5 待处理"


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


def test_new_session_respects_ptt_and_caps():
    clk = FakeClock()
    ads = demo_adapters()
    ads[Mode.DESKTOP] = MockAdapter(Mode.DESKTOP, demo_sessions(), caps=Capabilities(focus=None, new_session=None))
    c = Controller(DeckConfig(), ads, clock=clk, sim=True)
    c.refresh()
    c.page = "L4"
    c.on_key(1, 4, True)
    tap(c, clk, 4, 1)
    assert ads[Mode.DESKTOP].actions == [] and "PTT" in c.log[-1]
    c.on_key(1, 4, False)
    tap(c, clk, 4, 1)  # nothing selected: new session without a cwd
    assert ads[Mode.DESKTOP].actions == [("new", "")]
