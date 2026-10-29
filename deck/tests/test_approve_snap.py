"""1.8.1 (2026-10-29): long-press approve in option mode and SNAP (L4 MODE long press = screenshot to clipboard).

Long-press approve: a permission prompt's 「是」/「是，且不再问」 answer only after being held approve_hold_s and
released; the Mac types the digit into the session's own pane (adapter.answer).  A short press, a timeout, a reconnect, a
changed prompt, another page / mode or the PTT never approve.  「否」 stays keyboard-typed (immediate).
"""
import dataclasses
import subprocess

import pytest
from PIL import Image

from mk20deck.adapters.base import RecordingRunner, RunResult
from mk20deck.adapters.tmux import TmuxAdapter, TmuxConfig
from mk20deck.config import DeckConfig, load_config, parse_sound
from mk20deck.controller import (APPROVE_FLASH_S, APPROVE_PTT, APPROVE_READY, APPROVE_STALE, BOTTOM_ROW, LONG_PRESS,
                                 PERMISSION_LABELS, SNAP_BUSY, Controller, KeyView, Role, slot_pos)
from mk20deck.daemon import Daemon
from mk20deck import effects as E
from mk20deck.layout import DeviceLayout
from mk20deck.model import Mode, Session, SessionId, State, Tool
from mk20deck.render.canvas import Renderer
from mk20deck.render.keys import OPTION_AMBER, key_fps, render_key
from mk20deck.sim import FakeClock, option_adapters
from mk20deck.slots import N_SLOTS

PERM, Q = "推送发布", "数据库选型"
MODE_COL = BOTTOM_ROW.index(Role.MODE) + 1


def approve_adapters():
    ads = option_adapters()
    ads[Mode.TMUX].caps = dataclasses.replace(ads[Mode.TMUX].caps, approve=None)  # like the real tmux adapter
    return ads


def make(hold=1.0, ads=None):
    clk = FakeClock()
    ads = ads or approve_adapters()
    c = Controller(DeckConfig(approve_hold_s=hold), ads, {"mode": "tmux"}, clock=clk, wall=lambda: 1.0, sim=True)
    c.refresh()
    return c, clk, ads


def where(c, title):
    for i in range(N_SLOTS):
        s = c.mgr.session_at(i)
        if s is not None and s.title == title:
            return slot_pos(i)
    raise LookupError(title)


def tap(c, clk, col, row, hold=0.05):
    c.on_key(col, row, True)
    clk.advance(hold)
    c.tick()
    c.on_key(col, row, False)


def enter_perm(c, clk):
    tap(c, clk, *where(c, PERM))
    assert c.option is not None and c.option.kind == "permission"
    return c.option


def answers(ads):
    return [a for a in ads[Mode.TMUX].actions if a[0] == "answer"]


def hold_key(c, clk, col, row, secs, step=0.1):
    c.on_key(col, row, True)
    t = 0.0
    while t < secs - 1e-9:
        clk.advance(step)
        t += step
        c.tick()


# ------------------------------------------------------------------ controller
def test_permission_hold_keys_are_not_keyboard_typed():
    c, clk, ads = make()
    enter_perm(c, clk)
    assert c.hid_keys_wanted() == {3: 3}  # 「否」 still typed by the keyboard on press
    v = c.view()
    k1, k2, k3 = (next(k for k in v.keys if (k.col, k.row) == (i, 1)) for i in (1, 2, 3))
    assert (k1.label, k2.label, k3.label) == PERMISSION_LABELS
    assert k1.sublabel == k2.sublabel == "长按 1 秒" and not k3.sublabel
    assert v.sub.option == "选项：长按 1/2 批准 · 按 3 拒绝 · × 取消 · K1 返回"


def test_hold_off_or_unsupported_adapter_keeps_old_behaviour():
    c, clk, ads = make(hold=0)
    enter_perm(c, clk)
    assert c.hid_keys_wanted() == {1: 1, 2: 2, 3: 3}
    c, clk, ads = make(ads=option_adapters())  # approve capability missing: the keyboard types as before
    enter_perm(c, clk)
    assert c.hid_keys_wanted() == {1: 1, 2: 2, 3: 3}


def test_question_prompts_unchanged():
    c, clk, ads = make()
    tap(c, clk, *where(c, Q))
    assert c.option.kind == "question" and c.hid_keys_wanted() == {1: 1, 2: 2, 3: 3}


def test_full_hold_approves_on_the_matching_release_into_the_session_pane():
    c, clk, ads = make()
    op = enter_perm(c, clk)
    key = op.key
    hold_key(c, clk, 1, 1, 0.5)
    assert answers(ads) == [] and 0.4 < next(k for k in c.view().keys if (k.col, k.row) == (1, 1)).hold < 0.6
    for _ in range(6):
        clk.advance(0.1)
        c.tick()
    assert answers(ads) == [] and APPROVE_READY in c.view().sub.toast
    c.on_key(1, 1, False)
    assert len(answers(ads)) == 1 and answers(ads)[0][1].endswith("|1") and key.split(":", 2)[2] in answers(ads)[0][1]
    assert c.effects == [("sound", "approve")]
    v = c.view()
    k = next(k for k in v.keys if (k.col, k.row) == (1, 1))
    assert k.flash and k.hold == 0 and "已批准：1 是" in v.sub.toast
    for _ in range(20):  # still held: never a second answer
        clk.advance(0.1)
        c.tick()
    c.on_key(1, 1, False)
    assert len(answers(ads)) == 1
    clk.advance(APPROVE_FLASH_S)
    assert not any(k.flash for k in c.view().keys)


def test_second_key_answers_two():
    c, clk, ads = make()
    enter_perm(c, clk)
    hold_key(c, clk, 2, 1, 1.1)
    c.on_key(2, 1, False)
    assert [a[1].rsplit("|", 1)[1] for a in answers(ads)] == ["2"]


def test_short_press_never_approves():
    c, clk, ads = make()
    enter_perm(c, clk)
    hold_key(c, clk, 1, 1, 0.9)
    c.on_key(1, 1, False)
    assert "未批准：需按住 1 秒" in c.view().sub.toast
    for _ in range(30):
        clk.advance(0.1)
        c.tick()
    assert answers(ads) == [] and c.effects == []
    assert c.option is not None  # still answering: try again


def test_late_key_up_after_a_stall_does_not_approve():
    """Daemon order is events -> tick: a release that arrives together with a long stall is handled first."""
    c, clk, ads = make()
    enter_perm(c, clk)
    c.on_key(1, 1, True)
    clk.advance(5.0)  # the loop stalled; the up event is already queued
    c.on_key(1, 1, False)
    c.tick()
    assert answers(ads) == []


def test_missing_key_up_fails_closed_until_it_arrives():
    c, clk, ads = make()
    enter_perm(c, clk)
    c.on_key(1, 1, True)
    for _ in range(11):
        clk.advance(0.1)
        c.tick()
    assert answers(ads) == [] and APPROVE_READY in c.view().sub.toast
    c.on_key(1, 1, False)
    assert len(answers(ads)) == 1


def test_reconnect_and_timeout_never_approve():
    c, clk, ads = make()
    enter_perm(c, clk)
    hold_key(c, clk, 1, 1, 0.5)
    c.drop_gestures()  # reconnect / wake
    for _ in range(20):
        clk.advance(0.1)
        c.tick()
    assert answers(ads) == [] and c.option is None
    c, clk, ads = make()
    enter_perm(c, clk)
    c.on_key(1, 1, True)
    clk.advance(60.0)  # option-mode idle timeout passes while the key is "held" with no ticks
    c.tick()
    assert answers(ads) == [] and c.option is None


def test_prompt_change_or_ptt_refuses():
    c, clk, ads = make()
    enter_perm(c, clk)
    hold_key(c, clk, 1, 1, 0.5)
    s = next(s for s in ads[Mode.TMUX].sessions if s.title == PERM)
    s.state = State.RUNNING  # answered in the terminal meanwhile
    c.refresh()
    for _ in range(8):
        clk.advance(0.1)
        c.tick()
    c.on_key(1, 1, False)
    assert answers(ads) == []
    c, clk, ads = make()
    enter_perm(c, clk)
    hold_key(c, clk, 1, 1, 0.5)
    c.ptt_held = True
    for _ in range(8):
        clk.advance(0.1)
        c.tick()
    c.on_key(1, 1, False)
    assert answers(ads) == [] and APPROVE_PTT in c.view().sub.toast


def test_fire_rechecks_state():
    c, clk, ads = make()
    enter_perm(c, clk)
    hold_key(c, clk, 1, 1, 0.5)
    c.presses[("key", 1, 1)].target = "tmux:other:x"  # the press was for another session's prompt
    for _ in range(8):
        clk.advance(0.1)
        c.tick()
    c.on_key(1, 1, False)
    assert answers(ads) == [] and APPROVE_STALE in c.view().sub.toast


def test_adapter_refusal_is_shown_not_flashed():
    c, clk, ads = make()
    enter_perm(c, clk)
    ad = ads[Mode.TMUX]
    from mk20deck.model import ActionResult
    ad.answer = lambda s, d: ActionResult(False, "窗格在回看中：先按 K1 回到最新")
    hold_key(c, clk, 1, 1, 1.1)
    c.on_key(1, 1, False)
    v = c.view()
    assert "未批准：窗格在回看中" in v.sub.toast and not any(k.flash for k in v.keys) and c.effects == []


def test_long_press_pending_while_holding():
    c, clk, ads = make()
    enter_perm(c, clk)
    c.on_key(1, 1, True)
    assert c.long_press_pending()
    for _ in range(11):
        clk.advance(0.1)
        c.tick()
    assert c.long_press_pending()
    c.on_key(1, 1, False)
    assert not c.long_press_pending()


# ------------------------------------------------------------------ render
def test_render_ring_and_flash():
    kv = KeyView(1, 1, Role.AGENT, label="是", option=1, sublabel="长按 1 秒")
    plain = render_key(kv, 0, 0, animations=False)
    kv.hold = 0.5
    ring = render_key(kv, 0, 0, animations=False)
    assert plain.tobytes() != ring.tobytes() and ring.size == (128, 128)
    assert key_fps(kv, 0, False) == 8
    kv.hold, kv.flash = 0.0, True
    fl = render_key(kv, 0, 0, animations=False)
    assert fl.getpixel((10, 64)) != plain.getpixel((10, 64)) and key_fps(kv, 0, False) == 8
    r = Renderer(DeviceLayout.default(), animations=False)
    c, clk, ads = make()
    enter_perm(c, clk)
    c.on_key(1, 1, True)
    clk.advance(0.3)
    c.tick()
    assert r.desired_fps(c.view(), clk()) == 8


# ------------------------------------------------------------------ SNAP
def to_l4(c, clk):
    c.on_key(MODE_COL, 4, True)
    clk.advance(LONG_PRESS + 0.05)
    c.tick()
    c.on_key(MODE_COL, 4, False)
    assert c.page == "L4"


def test_snap_on_l4_mode_long_press():
    c, clk, ads = make()
    to_l4(c, clk)
    assert c.effects == []
    mode = next(k for k in c.view().keys if (k.col, k.row) == (MODE_COL, 4))
    assert mode.sublabel == "长按 截图"
    c.on_key(MODE_COL, 4, True)
    clk.advance(LONG_PRESS + 0.05)
    c.tick()
    c.on_key(MODE_COL, 4, False)
    assert c.effects == [("snap",)] and c.page == "L4" and SNAP_BUSY in c.view().sub.toast
    c.effect_result("snap", True, E.SNAP_OK)
    v = c.view()
    assert next(k for k in v.keys if (k.col, k.row) == (MODE_COL, 4)).flash and E.SNAP_OK in v.sub.toast
    c.effect_result("snap", False, E.SNAP_NEED_TCC)
    assert E.SNAP_NEED_TCC in c.view().sub.toast


def test_mode_short_press_on_l4_does_not_snap():
    c, clk, ads = make()
    to_l4(c, clk)
    tap(c, clk, MODE_COL, 4)
    assert c.effects == []


class FakeProc:
    def __init__(self, argv, rc=0, polls=1):
        self.argv, self.rc, self.killed, self.polls = argv, rc, False, polls

    def poll(self):
        if self.polls is None:  # never finishes
            return None
        self.polls -= 1
        return self.rc if self.polls < 0 else None

    def kill(self):
        self.killed = True


class FakePopen:
    def __init__(self, rc=0, polls=1):
        self.procs, self.rc, self.polls = [], rc, polls

    def __call__(self, argv, **kw):
        p = FakeProc(argv, self.rc, self.polls)
        self.procs.append(p)
        return p


class TickClock:
    """Every read advances 1 s (drives the worker's timeout without sleeping)."""
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        self.t += 1.0
        return self.t


def fx(**kw):
    kw.setdefault("platform", "darwin")
    kw.setdefault("access", lambda req: True)
    return E.Effects(threaded=False, **kw)


def test_effects_snap_clipboard_and_sound():
    pop = FakePopen()
    ef = fx(sound="Glass", popen=pop)
    ef.run(("snap",))
    assert pop.procs[0].argv == ["/usr/sbin/screencapture", "-x", "-c"]
    assert ef.busy() and ef.poll() == [("snap", True, E.SNAP_OK)] and not ef.busy()
    ef.run(("sound", "approve"))
    assert pop.procs[1].argv == ["/usr/bin/afplay", "/System/Library/Sounds/Glass.aiff"]
    fx(sound="", popen=pop).play()
    fx(sound="Glass", popen=pop, platform="linux").play()
    assert len(pop.procs) == 2


def test_effects_snap_to_file_then_clipboard(tmp_path):
    pop = FakePopen()
    ef = fx(snap_dir=str(tmp_path / "snaps"), popen=pop)
    ef.snap()
    argv, clip = pop.procs[0].argv, pop.procs[1].argv
    assert argv[:2] == ["/usr/sbin/screencapture", "-x"] and argv[2].startswith(str(tmp_path / "snaps"))
    assert clip[0] == "/usr/bin/osascript" and clip[-1] == argv[2] and "PNGf" in " ".join(clip)
    assert (tmp_path / "snaps").is_dir() and ef.poll() == [("snap", True, E.SNAP_OK)]


def test_effects_snap_file_names_are_unique_within_one_second(tmp_path):
    pop = FakePopen()
    ef = fx(snap_dir=str(tmp_path / "snaps"), popen=pop)
    ef.snap()
    first = pop.procs[0].argv[2]
    ef.snap()
    second = pop.procs[2].argv[2]
    assert first != second and first.endswith(".png") and second.endswith(".png")


def test_effects_snap_needs_permission_asks_once():
    asked, pop = [], FakePopen()
    ef = fx(popen=pop, access=lambda req: asked.append(req) or False)
    ef.snap()
    ef.snap()
    assert pop.procs == [] and asked == [True, False]
    assert ef.poll() == [("snap", False, E.SNAP_NEED_TCC)] * 2


def test_effects_snap_not_mac_timeout_failure():
    pop = FakePopen()
    ef = fx(platform="linux", popen=pop)
    ef.snap()
    assert ef.poll() == [("snap", False, E.SNAP_NOT_MAC)] and pop.procs == []
    pop = FakePopen(polls=None)
    ef = fx(popen=pop, clock=TickClock())
    ef.snap()
    assert ef.poll() == [("snap", False, E.SNAP_FAILED + "：超时")] and pop.procs[0].killed
    ef = fx(popen=FakePopen(rc=1))
    ef.snap()
    assert ef.poll() == [("snap", False, E.SNAP_FAILED)]


def test_effects_snap_runs_off_the_loop_thread():
    import threading
    gate, seen = threading.Event(), []

    def access(req):
        seen.append(threading.current_thread().name)
        gate.wait(5)  # a slow TCC probe must not block snap() / poll()
        return False
    ef = E.Effects(platform="darwin", access=access, popen=FakePopen())
    ef.snap()
    assert ef.busy() and ef.poll() == []
    ef.snap()  # second while running
    assert ef.poll() == [("snap", False, E.SNAP_RUNNING)]
    gate.set()
    for _ in range(100):
        out = ef.poll()
        if out:
            break
        threading.Event().wait(0.02)
    assert out == [("snap", False, E.SNAP_NEED_TCC)] and seen == ["mk20-snap"] and not ef.busy()


def test_daemon_runs_queued_effects_and_reports_back():
    c, clk, ads = make()
    ran = []

    class FX:
        def run(self, e):
            ran.append(e)

        def poll(self):
            return [("snap", True, E.SNAP_OK)] if ran else []

        def busy(self):
            return False

    d = Daemon(c, Renderer(DeviceLayout.default(), animations=False), None, clock=clk, bg_poll_s=0, effects=FX())
    to_l4(c, clk)
    c.on_key(MODE_COL, 4, True)
    clk.advance(LONG_PRESS + 0.05)
    d.step()
    assert ran == [("snap",)] and c.effects == [] and E.SNAP_OK in c.view().sub.toast
    d2 = Daemon(c, Renderer(DeviceLayout.default(), animations=False), None, clock=clk, bg_poll_s=0)
    c.effects.append(("snap",))
    d2.step()  # no runner: dropped, never raises
    assert c.effects == []


# ------------------------------------------------------------------ tmux adapter answer: exact argv
GEN = "4242-1700000000"


def tmux_ad(responses=None):
    r = RecordingRunner(responses or {}, binaries={"tmux"})
    ad = TmuxAdapter(TmuxConfig(), runner=r)
    ad.generation = GEN
    return ad, r


def pane(native="%3", gen=GEN):
    return Session(SessionId(Mode.TMUX, native, gen), Tool.CCAI, "x")


def test_tmux_answer_argv_and_refusals():
    ad, r = tmux_ad({("tmux", "display-message"): RunResult(0, "0\n", "")})
    assert ad.capabilities().approve is None
    assert ad.answer(pane(), 1).ok
    assert r.calls == [["tmux", "display-message", "-p", "-t", "%3", "#{pane_in_mode}"],
                       ["tmux", "send-keys", "-t", "%3", "-l", "1"]]
    ad, r = tmux_ad({("tmux", "display-message"): RunResult(0, "1\n", "")})
    res = ad.answer(pane(), 2)
    assert not res.ok and "回看" in res.message and not any("send-keys" in c for c in r.calls)
    ad, r = tmux_ad()
    assert not ad.answer(pane(gen="old"), 1).ok and r.calls == []
    assert not ad.answer(pane(), 0).ok and not ad.answer(pane(), "1; rm").ok and r.calls == []
    ad, r = tmux_ad({("tmux", "display-message"): RunResult(1, "", "can't find pane")})
    assert not ad.answer(pane(), 1).ok and len(r.calls) == 1


# ------------------------------------------------------------------ config
def test_config_keys(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[deck]\napprove_hold_s = 2.5\napprove_sound = "Ping"\n[snap]\nsave_dir = "~/Pictures/mk20"\n')
    cfg = load_config(str(p))
    assert (cfg.approve_hold_s, cfg.approve_sound) == (2.5, "Ping") and cfg.snap_dir.endswith("/Pictures/mk20")
    p.write_text('[deck]\napprove_hold_s = 99\napprove_sound = "../../etc/x"\n')
    cfg = load_config(str(p))
    assert (cfg.approve_hold_s, cfg.approve_sound, cfg.snap_dir) == (5.0, "", "")
    p.write_text('[deck]\napprove_hold_s = -1\napprove_sound = false\n')
    cfg = load_config(str(p))
    assert (cfg.approve_hold_s, cfg.approve_sound) == (0.0, "")
    d = DeckConfig()
    assert (d.approve_hold_s, d.approve_sound, d.snap_dir) == (1.0, "Glass", "")
    assert parse_sound("Glass") == "Glass" and parse_sound("a/b") == ""


def test_config_malformed_values_fall_back_without_startup_failure(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(
        """[deck]
backlight = \"oops\"
default_mode = \"not-a-mode\"
max_fps = \"oops\"
heartbeat_s = nan
approve_hold_s = \"oops\"
[snap]
save_dir = 42
[codex_desktop]
running_window_s = \"oops\"
max_candidates = []
require_cwd_exists = \"false\"
[tmux]
hook_max_age_s = \"oops\"
use_hooks = \"false\"
"""
    )
    cfg = load_config(str(p))
    assert cfg.backlight == 80 and cfg.default_mode is Mode.DESKTOP
    assert cfg.max_fps == 8.0 and cfg.heartbeat_s == 2.0 and cfg.approve_hold_s == 1.0
    assert cfg.snap_dir == "" and cfg.codex.running_window_s == 600.0
    assert cfg.codex.max_candidates == 24 and not cfg.codex.require_cwd_exists
    assert cfg.tmux.hook_max_age_s == 12 * 3600 and cfg.tmux.use_hooks
    p.write_text("[knobs]\n")
    assert load_config(str(p)).knob_map == {}
    p.write_text("[deck\n")
    cfg = load_config(str(p))
    assert cfg.default_mode is Mode.DESKTOP and cfg.backlight == 80


# ------------------------------------------------------------------ r1 BLOCKER: the hold must be observed
def test_stall_without_key_up_never_approves():
    """A lost / queued key-up plus a stalled loop: the tick after the stall sees a gap and cancels."""
    c, clk, ads = make()
    enter_perm(c, clk)
    c.on_key(1, 1, True)
    clk.advance(0.1)
    c.tick()
    clk.advance(1.5)  # stall: no tick watched the hold
    c.tick()
    for _ in range(10):
        clk.advance(0.1)
        c.tick()
    assert answers(ads) == []


def test_stale_link_never_approves():
    c, clk, ads = make()
    enter_perm(c, clk)
    c.note_link_ack()
    hold_key(c, clk, 1, 1, 1.1)  # no frame acked during the hold
    assert answers(ads) == [] and "未批准：键盘连接无响应" in c.view().sub.toast
    c, clk, ads = make()
    enter_perm(c, clk)
    c.on_key(1, 1, True)
    for _ in range(11):
        clk.advance(0.1)
        c.note_link_ack()
        c.tick()
    c.on_key(1, 1, False)
    assert len(answers(ads)) == 1


def test_other_input_during_hold_cancels():
    c, clk, ads = make()
    enter_perm(c, clk)
    hold_key(c, clk, 1, 1, 0.5)
    c.on_key(2, 1, True)  # another key
    for _ in range(10):
        clk.advance(0.1)
        c.tick()
    assert answers(ads) == []
    c, clk, ads = make()
    enter_perm(c, clk)
    hold_key(c, clk, 1, 1, 0.5)
    c.on_knob(2, "cw")
    for _ in range(10):
        clk.advance(0.1)
        c.tick()
    assert answers(ads) == []


def test_daemon_marks_link_ack_on_frames():
    c, clk, ads = make()

    class Link:
        def show_jpeg(self, jpeg):
            return True
    d = Daemon(c, Renderer(DeviceLayout.default(), animations=False), Link(), clock=clk, bg_poll_s=0)
    assert c.link_ack is None
    d.step(force=True)
    assert c.link_ack == clk()
