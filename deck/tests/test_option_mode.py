"""Option mode (user decision 2026-10-22): tap a needs_* session on L1 -> answer its numbered choice with digit keys.

Controller state machine (fake clock), rendering, ui.hidKeys over the link (MockDevice), daemon sync, adapters.
"""
import json
import threading
import time

import pytest
from PIL import Image

from mk20deck import agentstate
from mk20deck import protocol as P
from mk20deck.adapters.mock import MockAdapter
from mk20deck.config import DeckConfig
from mk20deck.controller import (OPTION_CHOSEN_S, OPTION_LEGACY, OPTION_PENDING, OPTION_PTT, OPTION_TIMEOUT,
                                 PERMISSION_LABELS, BOTTOM_ROW, Controller, KeyView, Role, option_labels, slot_pos)
from mk20deck.daemon import Daemon
from mk20deck.layout import DeviceLayout
from mk20deck.link import DeviceLink
from mk20deck.model import ActionResult, Capabilities, Mode, State
from mk20deck.render.canvas import Renderer
from mk20deck.render.keys import BLANK_BG, OPTION_AMBER, render_key
from mk20deck.render.subscreen import render_sub
from mk20deck.sim import FakeClock, demo_adapters, option_adapters, option_shots
from mk20deck.slots import N_SLOTS
from mk20deck.transport import MockDevice

Q, PERM, UNK = "数据库选型", "推送发布", "NAS挂载"
Q_LABELS = ("用 SQLite", "用 Postgres（docker compose）", "先不加数据库")


def make(ads=None, clk=None):
    clk = clk or FakeClock()
    ads = ads or option_adapters()
    c = Controller(DeckConfig(), ads, {"mode": "tmux"}, clock=clk, wall=lambda: 1.0, sim=True)
    c.refresh()
    return c, clk, ads


@pytest.fixture
def env():
    return make()


def tap(c, clk, col, row, hold=0.05):
    c.on_key(col, row, True)
    clk.advance(hold)
    c.tick()
    c.on_key(col, row, False)


def where(c, title):
    for i in range(N_SLOTS):
        s = c.mgr.session_at(i)
        if s is not None and s.title == title:
            return slot_pos(i)
    raise LookupError(title)


def session(ads, title):
    return next(s for s in ads[Mode.TMUX].sessions if s.title == title)


def focus_calls(ads):
    return [a for a in ads[Mode.TMUX].actions if a[0] == "focus"]


def enter(c, clk, title=Q):
    tap(c, clk, *where(c, title))
    assert c.option is not None
    return c.option


def rows13(view):
    return [k for k in view.keys if k.row <= 3]


# ------------------------------------------------------------------ enter + view
def test_tap_question_session_enters_option_mode_with_its_labels(env):
    c, clk, ads = env
    op = enter(c, clk)
    assert focus_calls(ads)[-1][1] == session(ads, Q).sid.key()  # the window came forward first
    assert op.labels == Q_LABELS and op.kind == "question"
    v = c.view()
    keys = rows13(v)
    assert [(k.col, k.row, k.option, k.label) for k in keys if k.option] == [
        (1, 1, 1, Q_LABELS[0]), (2, 1, 2, Q_LABELS[1]), (3, 1, 3, Q_LABELS[2])]
    assert sum(k.blank for k in keys) == 12 and not any(k.title or k.state for k in keys)  # session list cleared
    assert [k.role for k in v.keys if k.row == 4] == list(BOTTOM_ROW)  # PTT | DELETE | SEND/OK | NO | MODE unchanged
    assert v.sub.option == "选项：按 1–3 选择 · × 取消 · K1 返回" and v.sub.session.title == Q
    assert v.sub.option_note == "" and c.hid_keys_wanted() == {1: 1, 2: 2, 3: 3}


def test_permission_and_unknown_prompts(env):
    c, clk, ads = env
    op = enter(c, clk, PERM)
    assert op.labels == PERMISSION_LABELS == ("是", "是，且不再问", "否") and op.kind == "permission"
    assert c.hid_keys_wanted() == {1: 1, 2: 2, 3: 3}
    c.on_knob(1, "down")
    c.on_knob(1, "up")
    op = enter(c, clk, UNK)
    assert op.labels == ("",) * 5 and op.kind == "digits"
    v = c.view()
    assert [k.option for k in rows13(v) if k.option] == [1, 2, 3, 4, 5] and all(not k.label for k in rows13(v)[:5])
    assert v.sub.option == "选项：按 1–5 选择 · × 取消 · K1 返回" and c.hid_keys_wanted() == {i: i for i in range(1, 6)}


def test_option_labels_rules():
    from mk20deck.demo_data import demo_sessions
    s = demo_sessions(Mode.TMUX)[0]
    assert option_labels(s) == (("",) * 5, "digits")
    s.prompt_kind = "permission"
    assert option_labels(s) == (PERMISSION_LABELS, "permission")
    s.options = ("a", "b", "c", "d", "e", "f")  # labels win over the kind; at most row 1
    assert option_labels(s) == (("a", "b", "c", "d", "e"), "question")
    s.options = ("only",)
    c, clk, ads = make()
    assert option_labels(s)[0] == ("only",)
    sess = session(ads, Q)
    sess.options = ("only",)
    c.refresh()
    enter(c, clk)
    assert c.view().sub.option == "选项：按 1 选择 · × 取消 · K1 返回" and c.hid_keys_wanted() == {1: 1}


@pytest.mark.parametrize("why", ["running", "focus_unsupported", "focus_failed", "ptt", "L4", "long_press"])
def test_no_option_mode_without_a_confirmed_focus_on_l1(why):
    ads = option_adapters()
    if why == "focus_unsupported":
        ads[Mode.TMUX].caps = Capabilities(focus="未绑定终端 App，请在配置中设置")
    c, clk, _ = make(ads)
    title = Q
    if why == "running":
        title = next(s.title for s in ads[Mode.TMUX].sessions if s.state == State.RUNNING)
    if why == "focus_failed":
        ads[Mode.TMUX].focus = lambda s: ActionResult(False, "无法确定唯一 tmux client")
    if why == "ptt":
        c.on_key(1, 4, True)
    col, row = where(c, title)
    if why == "L4":  # rows 2-3 of L4 are the same slots; the keyboard maps digits on L1 only
        c.on_knob(2, "down")
        c.on_knob(2, "up")
        assert c.page == "L4"
        idx = next(i for i in range(5, N_SLOTS) if c.mgr.session_at(i) is not None)
        session(ads, c.mgr.session_at(idx).title).state = State.NEEDS_INPUT
        c.refresh()
        col, row = slot_pos(idx)
    if why == "long_press":
        c.on_key(col, row, True)
        for _ in range(14):
            clk.advance(0.05)
            c.tick()
        c.on_key(col, row, False)
        assert c.moving is not None
    else:
        tap(c, clk, col, row)
    assert c.option is None and c.hid_keys_wanted() == {}
    assert not any(k.option or k.blank for k in c.view().keys)


def test_desktop_and_unhooked_waiting_sessions_never_enter_option_mode():
    """Integrator 2026-10-22: only hook-reported prompts (tmux Claude/codex); Desktop/herdr taps stay plain focus."""
    clk = FakeClock()
    ads = demo_adapters()  # Codex Desktop demo: S1 needs_approval, S2 needs_input, focus supported
    c = Controller(DeckConfig(), ads, clock=clk, wall=lambda: 1.0, sim=True)
    c.refresh()
    assert c.mode == Mode.DESKTOP and c.mgr.session_at(0).state == State.NEEDS_APPROVAL
    for col in (1, 2, 1):
        tap(c, clk, col, 1)
        assert c.option is None and c.hid_keys_wanted() == {}
    assert len([a for a in ads[Mode.DESKTOP].actions if a[0] == "focus"]) == 3  # each tap focused, no option keys
    c.on_knob(1, "down")
    clk.advance(0.1)
    c.on_knob(1, "up")  # K1 press focuses the needs_* selection: still no option mode
    assert c.option is None and not any(k.option or k.blank for k in c.view().keys)
    # tmux pane in needs_input without a hook record behind the state: no option mode either
    c2, clk2, ads2 = make()
    s = session(ads2, Q)
    s.hooked = False
    c2.refresh()
    tap(c2, clk2, *where(c2, Q))
    assert c2.option is None and focus_calls(ads2)[-1][1] == s.sid.key()


# ------------------------------------------------------------------ presses while shown
def test_digit_while_ptt_held_only_hints(env):
    c, clk, ads = env
    enter(c, clk)
    n, sel = len(focus_calls(ads)), c.mgr.selected
    clk.advance(20)
    c.on_key(2, 1, True, ignored="ptt")  # the keyboard typed nothing (Right Option held)
    c.on_key(2, 1, False)
    assert c.toast[0] == OPTION_PTT == "先松开按住说话" and c.option is not None and c.option.chosen is None
    assert len(focus_calls(ads)) == n and c.mgr.selected == sel and not c.presses
    assert c.option_deadline() == pytest.approx(clk.t + OPTION_TIMEOUT)  # counts as input
    c.on_knob(1, "down")
    c.on_knob(1, "up")
    c.on_key(2, 1, True, ignored="ptt")  # after option mode: still only the hint
    assert c.option is None and c.toast[0] == OPTION_PTT and len(focus_calls(ads)) == n



def test_marked_digit_press_gives_feedback_then_exits_after_3s(env):
    c, clk, ads = env
    enter(c, clk)
    n_focus, sel = len(focus_calls(ads)), c.mgr.selected
    c.on_key(2, 1, True, hid=P.HID_DIGIT[2])  # the keyboard typed "2" into the frontmost window
    c.on_key(2, 1, False)                     # its release is unmarked: nothing happens
    assert c.toast[0] == "已选 2" and c.option.chosen[0] == "2"
    assert len(focus_calls(ads)) == n_focus and c.mgr.selected == sel and not c.presses
    assert c.option_deadline() == pytest.approx(clk.t + OPTION_CHOSEN_S)
    clk.advance(OPTION_CHOSEN_S - 0.1)
    c.tick()
    assert c.option is not None
    clk.advance(0.1)
    c.tick()
    assert c.option is None and c.toast[0] == "回到会话列表" and c.hid_keys_wanted() == {}


def test_answer_detected_by_state_change(env):
    c, clk, ads = env
    enter(c, clk)
    c.on_key(1, 1, True, hid=P.HID_DIGIT[1])
    session(ads, Q).state = State.RUNNING  # hook: PostToolUse
    c.refresh()
    assert c.option is None and c.toast[0] == "已作答 · 回到会话列表"
    v = c.view()
    assert not any(k.option or k.blank for k in v.keys) and any(k.title == Q for k in v.keys)


def test_state_left_without_answer_and_session_gone(env):
    c, clk, ads = env
    enter(c, clk)
    session(ads, Q).state = State.DONE_UNREAD
    c.refresh()
    assert c.option is None and "会话已不在等待选择" in c.toast[0]
    enter(c, clk, PERM)
    ads[Mode.TMUX].sessions = [s for s in ads[Mode.TMUX].sessions if s.title != PERM]
    c.refresh()
    assert c.option is None
    enter(c, clk, UNK)
    ads[Mode.TMUX].unavailable = "tmux server 未运行"
    c.refresh()
    assert c.option is None  # backend gone: sessions offline


def test_return_and_backspace_and_escape(env):
    c, clk, ads = env
    enter(c, clk)
    c.on_key(2, 4, True, hid=P.HID_BACKSPACE)
    assert c.option is not None and c.option.chosen is None
    c.on_key(3, 4, True, hid=P.HID_RETURN)
    assert c.option.chosen[0] == "Return" and c.toast[0] == "已确认 (Return)"
    c.on_key(4, 4, True, hid=P.HID_ESCAPE)  # ✗: the keyboard sent Escape itself
    assert c.option is None and c.toast[0] == "已取消选择 (Esc)"
    c.on_key(4, 4, False)
    c.on_key(4, 4, True)  # unmarked NO (no map active): keyboard-side HID only, never acted on
    assert c.option is None and focus_calls(ads)[-1][1] == session(ads, Q).sid.key()


def test_k1_press_returns_without_focus(env):
    c, clk, ads = env
    enter(c, clk)
    n = len(focus_calls(ads))
    c.on_knob(1, "cw")  # rotation: the selection stays on the session being answered
    assert c.option is not None and c.mgr.selected_session().title == Q
    c.on_knob(1, "down")
    assert c.option is None and c.toast[0] == "回到会话列表"
    clk.advance(0.1)
    c.on_knob(1, "up")
    assert len(focus_calls(ads)) == n  # K1 up after the exit does not focus
    for _ in range(20):  # nor does it become a K1 hold (L4)
        clk.advance(0.05)
        c.tick()
    assert c.page == "L1"


def test_blank_key_exits_without_select_or_focus(env):
    c, clk, ads = env
    enter(c, clk)
    n, sel = len(focus_calls(ads)), c.mgr.selected
    tap(c, clk, 5, 2)  # S10 area: blank in option mode
    assert c.option is None and c.toast[0] == "回到会话列表"
    assert len(focus_calls(ads)) == n and c.mgr.selected == sel


@pytest.mark.parametrize("how", ["k2", "mode", "k1_hold", "settings", "reconnect", "mode_select"])
def test_page_mode_switch_and_reconnect_exit(env, how):
    c, clk, ads = env
    enter(c, clk)
    if how == "k2":
        c.on_knob(2, "down")
        c.on_knob(2, "up")
    elif how == "mode":
        tap(c, clk, 5, 4)
    elif how == "k1_hold":
        c.on_knob(1, "down")  # K1 press already leaves option mode
    elif how == "settings":
        c.open_settings()
    elif how == "reconnect":
        c.option_hid = "ok"
        c.drop_gestures()
        assert c.option_hid == ""
    elif how == "mode_select":
        c.switch_mode(Mode.DESKTOP)
    assert c.option is None and c.hid_keys_wanted() == {}


def test_30s_without_input_times_out_and_input_extends(env):
    c, clk, ads = env
    enter(c, clk)
    t0 = clk.t
    assert c.option_deadline() == pytest.approx(t0 + OPTION_TIMEOUT)
    clk.advance(20)
    c.on_key(1, 1, True)  # unmarked option key = input (and a hint), the timer restarts
    c.on_key(1, 1, False)
    clk.advance(OPTION_TIMEOUT - 0.1)
    c.tick()
    assert c.option is not None
    clk.advance(0.1)
    c.tick()
    assert c.option is None and "30 s" in c.toast[0]
    assert clk.t - t0 == pytest.approx(20 + OPTION_TIMEOUT)


@pytest.mark.parametrize("status,hint", [("ok", "数字键未生效，请在 Mac 上按 2"), ("", "数字键未生效，请在 Mac 上按 2"),
                                         ("pending", "数字键未生效，请在 Mac 上按 2"), ("legacy", OPTION_LEGACY)])
def test_unmarked_option_key_hints_and_never_acts(env, status, hint):
    c, clk, ads = env
    enter(c, clk)
    c.set_option_hid(status)
    n, sel = len(focus_calls(ads)), c.mgr.selected
    c.on_key(2, 1, True)
    for _ in range(20):  # held past the long-press threshold: no pick-up
        clk.advance(0.05)
        c.tick()
    c.on_key(2, 1, False)
    assert c.toast[0] == hint and c.option is not None and c.moving is None
    assert len(focus_calls(ads)) == n and c.mgr.selected == sel
    assert c.view().sub.option_note == {"legacy": OPTION_LEGACY, "pending": OPTION_PENDING}.get(status, "")


def test_marked_press_after_exit_is_only_typed(env):
    """The keyboard's map can outlive option mode by a moment: such a press is never a slot tap."""
    c, clk, ads = env
    enter(c, clk)
    c.on_knob(1, "down")
    c.on_knob(1, "up")
    n, sel = len(focus_calls(ads)), c.mgr.selected
    c.on_key(2, 1, True, hid=P.HID_DIGIT[2])
    c.on_key(2, 1, False)
    assert c.option is None and c.toast[0] == "已发送数字 2" and len(focus_calls(ads)) == n and c.mgr.selected == sel


def test_labels_arrive_later_and_k1_press_enters_too(env):
    c, clk, ads = env
    s = session(ads, UNK)
    enter(c, clk, UNK)
    assert c.option.kind == "digits"
    s.options = ("A", "B")
    c.refresh()
    assert c.option.labels == ("A", "B") and c.hid_keys_wanted() == {1: 1, 2: 2}
    c.on_knob(1, "down")
    c.on_knob(1, "up")
    assert c.option is None
    c.on_knob(1, "down")  # K1 press = focus the selection = same as a tap
    clk.advance(0.1)
    c.on_knob(1, "up")
    assert c.option is not None and c.option.labels == ("A", "B")


# ------------------------------------------------------------------ render
def test_render_option_and_blank_faces():
    face = render_key(KeyView(1, 1, Role.AGENT, option=2, label="用 Postgres（docker compose）"), 0, 0, False)
    assert face.size == (128, 128) and face.getpixel((10, 64)) == OPTION_AMBER
    assert len(face.getcolors(1 << 16)) > 20  # digit + two label lines drawn
    digit = render_key(KeyView(1, 1, Role.AGENT, option=4), 0, 0, False)
    assert digit.getpixel((10, 64)) == OPTION_AMBER and digit.tobytes() != face.tobytes()
    blank = render_key(KeyView(5, 3, Role.AGENT, blank=True), 0, 0, False)
    assert blank.getcolors() == [(128 * 128, BLANK_BG)]


def test_compose_option_canvas_and_subscreen(env):
    c, clk, ads = env
    enter(c, clk)
    c.set_option_hid("legacy")
    lay = DeviceLayout.default()
    img = Renderer(lay, animations=False).compose(c.view(), clk.t)
    for col in range(4, 6):
        r = lay.key_rect(col, 1)
        assert img.crop((r.x, r.y, r.x + r.w, r.y + r.h)).getcolors() == [(r.w * r.h, BLANK_BG)]
    r = lay.key_rect(1, 1)
    px = img.getpixel((r.x + 10, r.y + 64))
    assert abs(px[0] - OPTION_AMBER[0]) < 12 and abs(px[1] - OPTION_AMBER[1]) < 12
    sv = c.view().sub
    with_note = render_sub(sv, clk.t)
    sv.option_note = ""
    assert render_sub(sv, clk.t).tobytes() != with_note.tobytes()
    sv.option = ""
    assert render_sub(sv, clk.t).tobytes() != with_note.tobytes()


def test_sim_option_shots(tmp_path):
    made = option_shots(str(tmp_path))
    assert [n for n, _ in made] == ["opt-00-L1-waiting.png", "opt-01-question-3-labels.png", "opt-02-chosen-2.png",
                                    "opt-03-permission.png", "opt-04-unknown-digits.png", "opt-05-legacy-firmware.png"]
    for n, _ in made:
        assert Image.open(tmp_path / n).size[0] > 600
    assert (tmp_path / "index.html").read_text(encoding="utf-8").count("<figure>") == 6


# ------------------------------------------------------------------ protocol + link (MockDevice)
def test_protocol_ui_hid_keys_builder_and_marker():
    assert P.ui_hid_keys({2: 2, 1: 1}, 7) == {"method": "ui.hidKeys", "generation": 7, "keys": {"1": "1", "2": "2"}}
    assert P.ui_hid_keys({}, 1)["keys"] == {} and "ui.hidKeys" in P.ALLOWED_METHODS
    assert P.ui_hid_keys({15: 9}, 999_999_999)["keys"] == {"15": "9"}
    for keys, gen in (({1: 1}, 0), ({1: 1}, 1_000_000_000), ({0: 1}, 1), ({16: 1}, 1), ({1: 0}, 1), ({1: 10}, 1),
                      ({1: 1, 2: 1}, 1), ({i: i for i in range(1, 11)}, 1), ({True: 1}, 1), ({1: True}, 1)):
        with pytest.raises(ValueError):
            P.ui_hid_keys(keys, gen)
    ev = {"method": "keyStateChanged", "parameters": {"col": 1, "ignored": "ptt", "pressed": True, "row": 0}}
    assert P.classify_json(ev).ignored == "ptt" and P.classify_json(ev).hid is None
    for bad in ("PTT", 1, True, ["ptt"], ""):
        ev["parameters"]["ignored"] = bad
        assert P.classify_json(ev).ignored is None
    ev = {"method": "keyStateChanged", "parameters": {"col": 1, "hid": 31, "pressed": True, "row": 0}}
    assert P.classify_json(ev).hid == 31 and P.classify_json(ev).ignored is None
    for bad in ("31", 300, True, None, 3.0, 0):
        ev["parameters"]["hid"] = bad
        assert P.classify_json(ev).hid is None
    assert P.HID_DIGIT[1] == 0x1E and P.HID_DIGIT[9] == 0x26


def linked(dev):
    link = DeviceLink(dev, ack_timeout=1.0, preamble_flush=0)
    link.start()
    link.handshake()
    return link


def test_link_ui_hid_keys_generation_and_rejects():
    dev = MockDevice(ui=True, hid_keys=True)
    dev.hid_generation = 41  # e.g. an earlier deck process; the hello tells us where to continue
    link = linked(dev)
    try:
        assert link.supports("ui.hidKeys") and link.ui_generation == 41
        assert link.ui_hid_keys({1: 1, 2: 2}) == "ok" and dev.hid_map == {1: 1, 2: 2} and dev.hid_generation == 42
        assert link.ui_hid_keys({1: 1, 2: 2}) == "ok" and dev.hid_generation == 43  # refresh: new generation
        dev.hid_page_live = False  # e.g. keyboard on L4 / grace: non-empty refused, clearing still fine
        assert link.ui_hid_keys({1: 1}) == "rejected" and dev.hid_map == {1: 1, 2: 2}
        assert link.ui_hid_keys({}) == "ok" and dev.hid_map == {}
        dev.hid_generation = 10 ** 6  # someone else advanced it: stale generation refused
        assert link.ui_hid_keys({}) == "rejected" and link.hid_keys_rejected == 2
        epoch = link.ui_epoch
        assert link.ui_hello() and link.ui_epoch == epoch + 1 and link.ui_generation == 10 ** 6
        dev.hid_page_live = True
        assert link.ui_hid_keys({3: 3}) == "ok" and dev.hid_generation == 10 ** 6 + 1
        assert all(r["method"] == "ui.hidKeys" and set(r) == {"method", "generation", "keys"} for r in dev.hid_requests)
    finally:
        link.close()


@pytest.mark.parametrize("ui", [True, False])
def test_link_legacy_firmware_never_sends(ui):
    dev = MockDevice(ui=ui)  # fw < 1.5.1 (ui.hello without the capability) or stock KD (no ui.hello)
    link = linked(dev)
    try:
        assert not link.supports("ui.hidKeys") and link.ui_hid_keys({1: 1}) == "unsupported"
        assert not any(r.get("method") == "ui.hidKeys" for r in dev.json_requests)
    finally:
        link.close()


# ------------------------------------------------------------------ daemon sync
def daemon_for(dev, clk=None):
    clk = clk or FakeClock()
    c, _, ads = make(clk=clk)
    link = linked(dev)
    d = Daemon(c, Renderer(link.layout, animations=False), link, clock=clk)
    link.on_key = d.on_device_key
    d.bg_poll_s = 0
    return d, c, clk, ads, link


def wait_for(pred, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.005)
    return pred()


def test_daemon_sends_map_before_the_frame_refreshes_and_clears():
    dev = MockDevice(ui=True, hid_keys=True)
    order = []
    orig = dev._hid_keys
    dev._hid_keys = lambda obj: (order.append(len(dev.frames)), orig(obj))[1]
    d, c, clk, ads, link = daemon_for(dev)
    try:
        d.step(force=True)
        frames0 = len(dev.frames)
        col, row = where(c, Q)
        d.inject_key(col, row, True)
        clk.advance(0.05)
        d.inject_key(col, row, False)
        d.step()
        assert c.option is not None and dev.hid_map == {1: 1, 2: 2, 3: 3} and c.option_hid == "ok"
        assert order == [frames0] and len(dev.frames) == frames0 + 1  # map first, then the option frame
        gen = dev.hid_generation
        clk.advance(5)
        d.step()
        assert dev.hid_generation == gen  # nothing new to say
        assert d.next_wait() <= 5.0 + 1e-6
        clk.advance(5)
        d.step()
        assert dev.hid_generation == gen + 1 and dev.hid_map == {1: 1, 2: 2, 3: 3}  # 10 s refresh
        dev.press(1, 0, True, hid=P.HID_DIGIT[2])  # firmware coordinates; marked press from the keyboard
        assert wait_for(lambda: not d.events.empty())
        d.step()
        assert c.toast[0] == "已选 2" and c.option.chosen
        dev.press(2, 0, True, ignored="ptt")  # a digit the keyboard ignored under the PTT
        assert wait_for(lambda: not d.events.empty())
        d.step()
        assert c.toast[0] == OPTION_PTT and c.option is not None
        clk.advance(OPTION_CHOSEN_S)
        d.step()
        assert c.option is None and dev.hid_map == {} and dev.hid_generation == gen + 2  # cleared on exit
        n = len(dev.hid_requests)
        clk.advance(20)
        d.step()
        assert len(dev.hid_requests) == n  # nothing mapped, nothing sent
    finally:
        link.close()


def test_daemon_legacy_firmware_shows_hint():
    dev = MockDevice(ui=True)
    d, c, clk, ads, link = daemon_for(dev)
    try:
        col, row = where(c, PERM)
        d.inject_key(col, row, True)
        d.inject_key(col, row, False)
        d.step()
        assert c.option is not None and c.option_hid == "legacy" and c.view().sub.option_note == OPTION_LEGACY
        assert not any(r.get("method") == "ui.hidKeys" for r in dev.json_requests)
        d.heartbeat, d._next_poll = 1e9, 1e12
        assert d.next_wait() == pytest.approx(OPTION_TIMEOUT, abs=0.1)  # no busy retry loop, only the 30 s exit
    finally:
        link.close()


def test_daemon_retries_rejected_map_and_resends_after_rehello():
    dev = MockDevice(ui=True, hid_keys=True)
    dev.hid_page_live = False
    d, c, clk, ads, link = daemon_for(dev)
    try:
        col, row = where(c, Q)
        d.inject_key(col, row, True)
        d.inject_key(col, row, False)
        d.step()
        assert c.option_hid == "pending" and dev.hid_map == {} and c.view().sub.option_note == OPTION_PENDING
        n = len(dev.hid_requests)
        clk.advance(0.5)
        d.step()
        assert len(dev.hid_requests) == n  # retry is rate limited
        dev.hid_page_live = True
        clk.advance(0.6)
        d.step()
        assert dev.hid_map == {1: 1, 2: 2, 3: 3} and c.option_hid == "ok"
        assert link.ui_hello() and dev.hid_map == {}  # a new hello: the keyboard dropped the map
        d.step()
        assert dev.hid_map == {1: 1, 2: 2, 3: 3}
    finally:
        link.close()


def test_daemon_next_wait_covers_option_timeout():
    dev = MockDevice(ui=True, hid_keys=True)
    d, c, clk, ads, link = daemon_for(dev)
    try:
        d.heartbeat = d.poll_interval = d.hid_refresh_s = 1e9  # only the option timeout is left to wake for
        d.step(force=True)
        col, row = where(c, Q)
        d.inject_key(col, row, True)
        d.inject_key(col, row, False)
        d.step()
        clk.advance(9.9)
        d.step()
        assert c.option is not None and d.next_wait() == pytest.approx(OPTION_TIMEOUT - 9.9)
        d.hid_refresh_s = 10.0
        assert d.next_wait() == pytest.approx(0.1)  # ... and the 10 s ui.hidKeys refresh
    finally:
        link.close()


# ------------------------------------------------------------------ adapters
def test_agent_record_options_validation():
    base = {"tool": "claude", "session_id": "s", "tmux_pane": "%1", "tmux_socket": "/t", "cwd": "/w",
            "state": "needs_input", "ts": 1.0, "ppid": 2}
    assert agentstate.parse_record(json.dumps(base)).options == ()
    r = agentstate.parse_record(json.dumps({**base, "options": ["A", "用 Postgres"], "prompt_kind": "permission"}))
    assert r.options == ("A", "用 Postgres") and r.prompt_kind == "permission"
    for bad in (["a"] * 6, ["x" * 25], ["a\u0007"], ["a‮"], "A", [1], [], [""], None, {"a": 1}):
        r = agentstate.parse_record(json.dumps({**base, "options": bad}))
        assert r is not None and r.options == ()  # dropped, the state record itself is still valid
    for bad in ("question", 1, None, ["permission"]):
        assert agentstate.parse_record(json.dumps({**base, "prompt_kind": bad})).prompt_kind == ""


def test_tmux_adapter_exposes_pending_choice(tmp_path):
    from test_tmux_hooks import adapter, by_pane, put
    sdir = tmp_path / "agent-state"
    sdir.mkdir(mode=0o700)
    p = put(sdir, "claude", "c0", "%0", "needs_input")
    d = json.loads(p.read_text())
    p.write_text(json.dumps({**d, "options": ["用 SQLite", "先不加"]}))
    ss = by_pane(adapter(sdir))
    assert ss["%0"].options == ("用 SQLite", "先不加") and ss["%0"].prompt_kind == ""
    assert ss["%0"].hooked and not ss["%1"].hooked  # %1: codex found by its command, no record
    p.write_text(json.dumps({**d, "state": "needs_approval", "prompt_kind": "permission"}))
    ss = by_pane(adapter(sdir))
    assert ss["%0"].prompt_kind == "permission" and ss["%0"].options == ()
    p.write_text(json.dumps({**d, "state": "running", "options": ["stale"], "prompt_kind": "permission"}))
    ss = by_pane(adapter(sdir))
    assert ss["%0"].options == () and ss["%0"].prompt_kind == ""  # only a pending prompt carries a choice


def test_hid_keys_generation_exhaustion_is_rejected_not_raised():
    """Kimi MINOR: ui_generation beyond 999999999 must not raise into the frame loop."""
    from mk20deck.link import DeviceLink
    from mk20deck.transport import MockDevice
    dev = MockDevice(ui=True)
    link = DeviceLink(dev, ack_timeout=0.5, preamble_flush=0)
    link.start()
    try:
        link.handshake()
        link.ui_generation = 999999999
        assert link.ui_hid_keys({1: 1}) in ("rejected", "unsupported")
    finally:
        link.close()
