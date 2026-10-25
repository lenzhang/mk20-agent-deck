"""K1 scroll-back (user decision 2026-10-25): K1 rotation scrolls the selected session's conversation history, a K1
press returns it to the latest content.  K1 no longer walks the selection.

Controller (fake clock), tmux adapter argv (+ a private tmux server when tmux is installed), Codex Desktop helper calls,
``mk20deck scroll-setup`` (scripted subprocess), the helper itself (compiled on macOS only), daemon, rendering.
"""
import os
import shutil
import subprocess
import sys
import time
import uuid
from types import SimpleNamespace

import pytest

from mk20deck import scrollhelper
from mk20deck.adapters.base import RecordingRunner, RunResult, SubprocessRunner
from mk20deck.adapters.codex_desktop import CodexDesktopAdapter, CodexDesktopConfig
from mk20deck.adapters.herdr import HerdrAdapter
from mk20deck.adapters.mock import MockAdapter
from mk20deck.adapters.tmux import TmuxAdapter, TmuxConfig
from mk20deck.config import DeckConfig, load_config
from mk20deck.controller import (K1_NO_SESSION, SCROLL_FOCUS_SETTLE_S, SCROLL_HINT, SCROLL_IDLE_LIVE, SCROLL_IDLE_S,
                                 SCROLL_LATEST, SCROLL_LIVE, SCROLL_NOTE_S, SCROLL_OPTION, SCROLL_PTT,
                                 SCROLL_SETUP_QUIET_S, Controller, slot_pos)
from mk20deck.daemon import Daemon
from mk20deck.demo_data import demo_sessions
from mk20deck.layout import DeviceLayout
from mk20deck.model import ActionResult, Mode, Session, SessionId, Tool
from mk20deck.render.canvas import Renderer
from mk20deck.render.subscreen import SCROLL_BLUE, SCROLL_WARN, render_sub
from mk20deck.sim import FakeClock, demo_adapters, option_adapters, scroll_shots

GEN = "4242-1700000000"


def make(mode=Mode.TMUX, ads=None):
    clk = FakeClock()
    ads = ads or demo_adapters()
    c = Controller(DeckConfig(default_mode=mode), ads, clock=clk, wall=lambda: 1.0, sim=True)
    c.refresh()
    return c, clk, ads


def tap(c, clk, col, row):
    c.on_key(col, row, True)
    clk.advance(0.05)
    c.tick()
    c.on_key(col, row, False)


def press(c, clk):
    c.on_knob(1, "down")
    clk.advance(0.1)
    c.on_knob(1, "up")


def acts(ads, mode, kind):
    return [a[1] for a in ads[mode].actions if a[0] == kind]


def scrolls(ads, mode=Mode.TMUX):
    return [int(a.rsplit("|", 1)[1]) for a in acts(ads, mode, "scroll")]


@pytest.fixture
def tm():
    c, clk, ads = make()
    tap(c, clk, 1, 1)  # tmux demo: no terminal App, a tap only selects (scroll-back needs no focus)
    return c, clk, ads, c.mgr.selected


# ------------------------------------------------------------------ controller: rotate / press
def test_rotate_scrolls_the_selected_tmux_pane_without_focus(tm):
    c, clk, ads, key = tm
    c.on_knob(1, "ccw")
    assert ads[Mode.TMUX].actions == []  # only queued: the send happens on the next tick (one per loop step)
    c.tick()
    assert ads[Mode.TMUX].actions == [("scroll", f"{key}|3")]  # ccw = older, 3 lines per detent
    assert c.view().sub.scrollback == SCROLL_HINT == "回看中 · 按 K1 回到最新"
    clk.advance(0.5)
    c.on_knob(1, "cw")
    c.tick()
    assert scrolls(ads) == [3, -3] and c.mgr.selected == key  # cw = newer; the selection never moves
    assert acts(ads, Mode.TMUX, "focus") == [] and c.scrolling is not None


def test_fast_detents_accelerate_and_coalesce(tm):
    c, clk, ads, key = tm
    for _ in range(12):  # 50 ms apart: 3,3,6,6,9,9,12,12,15,15,15,15 (5x cap), sent as one sum
        c.on_knob(1, "ccw")
        clk.advance(0.05)
    c.tick()
    assert scrolls(ads) == [120]
    clk.advance(0.5)
    for _ in range(3):  # slow detents: 3 lines each
        c.on_knob(1, "ccw")
        c.tick()
        clk.advance(0.2)
    assert scrolls(ads) == [120, 3, 3, 3]
    c.on_knob(1, "ccw")
    clk.advance(0.05)
    c.on_knob(1, "cw")  # a direction change starts slow again
    clk.advance(0.05)
    c.on_knob(1, "cw")
    c.tick()
    assert scrolls(ads)[-1] == 3 - 3 - 3


def test_cw_without_scrollback_is_already_latest(tm):
    c, clk, ads, key = tm
    c.on_knob(1, "cw")
    c.tick()
    assert c.toast[0] == SCROLL_LATEST and ads[Mode.TMUX].actions == [] and c.scrolling is None


def test_press_returns_to_latest_and_leaves_scrollback(tm):
    c, clk, ads, key = tm
    c.on_knob(1, "ccw")
    c.tick()
    press(c, clk)
    c.tick()
    assert ads[Mode.TMUX].actions[-1] == ("bottom", key) and c.toast[0] == SCROLL_LIVE == "已回到最新"
    assert c.scrolling is None and c.view().sub.scrollback == "" and c.page == "L1"
    press(c, clk)  # not scrolled back: tmux still cancels copy mode of the pane (e.g. after a mouse scroll)
    c.tick()
    assert acts(ads, Mode.TMUX, "bottom") == [key, key] and acts(ads, Mode.TMUX, "focus") == []


def test_press_and_rotate_without_selection_or_support():
    c, clk, ads = make()
    press(c, clk)
    assert c.toast[0] == K1_NO_SESSION == "未选中会话 · 单击会话键选择"
    herdr = MockAdapter(Mode.HERDR, demo_sessions(Mode.HERDR, (Tool.CCAI,), n=2), caps=HerdrAdapter().capabilities())
    ads[Mode.HERDR] = herdr
    c.switch_mode(Mode.HERDR)
    tap(c, clk, 1, 1)
    c.on_knob(1, "ccw")
    c.tick()
    assert c.toast[0] == "回看不可用：herdr 未联调" and herdr.actions == [] and c.scrolling is None


def test_live_answer_ends_scrollback(tm):
    c, clk, ads, key = tm
    c.on_knob(1, "ccw")
    c.tick()
    ads[Mode.TMUX].scroll_reply = ActionResult(True, "", {"live": True})  # scrolled down out of copy mode
    c.on_knob(1, "cw")
    c.tick()
    assert c.scrolling is None and c.toast[0] == SCROLL_LIVE and c.view().sub.scrollback == ""


def test_adapter_error_is_a_toast(tm):
    c, clk, ads, key = tm
    ads[Mode.TMUX].scroll_reply = ActionResult(False, "tmux 回看失败：can't find pane")
    c.on_knob(1, "ccw")
    c.tick()
    assert c.toast[0] == "tmux 回看失败：can't find pane" and c.scrolling is None


# ------------------------------------------------------------------ controller: leaving the scroll-back
def test_selection_change_and_tap_release_the_tmux_pane(tm):
    c, clk, ads, key = tm
    c.on_knob(1, "ccw")
    c.tick()
    tap(c, clk, 2, 1)  # another session
    assert acts(ads, Mode.TMUX, "release") == [key] and c.scrolling is None
    other = c.mgr.selected
    c.on_knob(1, "ccw")
    c.tick()
    tap(c, clk, 2, 1)  # a tap on the same session = go to it: live again
    assert acts(ads, Mode.TMUX, "release") == [key, other] and c.scrolling is None


def test_mode_switch_and_vanishing_session_end_scrollback(tm):
    c, clk, ads, key = tm
    c.on_knob(1, "ccw")
    c.tick()
    c.switch_mode(Mode.DESKTOP)
    assert acts(ads, Mode.TMUX, "release") == [key] and c.scrolling is None
    c.switch_mode(Mode.TMUX)
    tap(c, clk, 1, 1)
    c.on_knob(1, "ccw")
    c.tick()
    ads[Mode.TMUX].sessions = [s for s in ads[Mode.TMUX].sessions if s.sid.key() != key]
    c.refresh()
    assert c.scrolling is None and acts(ads, Mode.TMUX, "release") == [key]  # gone: nothing to release


def test_idle_timeout_returns_to_latest(tm):
    c, clk, ads, key = tm
    c.on_knob(1, "ccw")
    c.tick()
    t0 = clk.t
    assert c.scroll_deadline() == pytest.approx(t0 + SCROLL_IDLE_S) and SCROLL_IDLE_S == 120.0
    clk.advance(SCROLL_IDLE_S - 1)
    c.tick()
    assert c.scrolling is not None
    clk.advance(1.0)
    c.tick()
    assert c.scrolling is None and acts(ads, Mode.TMUX, "release") == [key] and c.toast[0] == SCROLL_IDLE_LIVE
    assert c.scroll_deadline() is None


def test_ptt_blocks_rotation_and_ends_scrollback(tm):
    c, clk, ads, key = tm
    c.on_knob(1, "ccw")
    c.tick()
    c.on_key(1, 4, True)  # an observed PTT press: dictation must reach the live prompt
    assert acts(ads, Mode.TMUX, "release") == [key] and c.scrolling is None
    c.on_knob(1, "ccw")
    c.tick()
    assert c.toast[0] == SCROLL_PTT and scrolls(ads) == [3]
    c.on_key(1, 4, False)


def test_reconnect_drops_unsent_scrolls(tm):
    c, clk, ads, key = tm
    c.on_knob(1, "ccw")
    c.drop_gestures()
    c.tick()
    assert ads[Mode.TMUX].actions == [] and c.scrolling is None
    c.on_knob(1, "ccw")
    c.tick()
    c.on_knob(1, "ccw")
    c.drop_gestures()  # the scroll-back that happened stays; the queued detent is never replayed
    c.tick()
    assert scrolls(ads) == [3] and c.scrolling is not None


# ------------------------------------------------------------------ controller: option mode / pick-up keep their K1 press
def test_option_mode_rotation_does_nothing_and_press_leaves():
    c, clk, ads = make(ads=option_adapters())
    q = next(i for i in range(15) if c.mgr.session_at(i) and c.mgr.session_at(i).title == "数据库选型")
    tap(c, clk, *slot_pos(q))
    assert c.option is not None
    c.on_knob(1, "ccw")
    c.tick()
    assert c.toast[0] == SCROLL_OPTION and scrolls(ads) == [] and c.option is not None
    press(c, clk)
    c.tick()
    assert c.option is None and acts(ads, Mode.TMUX, "bottom") == []  # K1 press only left option mode


def test_pick_up_k1_press_only_cancels(tm):
    c, clk, ads, key = tm
    c.on_knob(1, "ccw")
    c.tick()
    c.on_key(1, 1, True)  # long press on the selected slot: pick up (selection unchanged: scroll-back stays)
    for _ in range(14):
        clk.advance(0.05)
        c.tick()
    c.on_key(1, 1, False)
    assert c.moving is not None and c.scrolling is not None
    c.on_knob(1, "ccw")  # rotation is input for the pick-up timeout and still scrolls
    c.tick()
    press(c, clk)
    c.tick()
    assert c.moving is None and c.toast[0] == "已取消移动" and acts(ads, Mode.TMUX, "bottom") == []
    assert scrolls(ads) == [3, 3]


def test_k1_hold_still_l4():
    c, clk, ads = make()
    tap(c, clk, 1, 1)
    c.on_knob(1, "down")
    clk.advance(0.7)
    c.tick()
    c.on_knob(1, "up")
    assert c.page == "L4" and acts(ads, Mode.TMUX, "bottom") == []


# ------------------------------------------------------------------ controller: Codex Desktop (focus first, helper)
def test_desktop_focuses_the_thread_then_scrolls_after_settle():
    c, clk, ads = make(Mode.DESKTOP)
    tap(c, clk, 3, 1)
    key = c.mgr.selected
    assert acts(ads, Mode.DESKTOP, "focus") == [key]
    c.on_knob(1, "ccw")  # brings the thread forward again (the helper scrolls the frontmost window)
    assert acts(ads, Mode.DESKTOP, "focus") == [key, key]
    c.tick()
    assert scrolls(ads, Mode.DESKTOP) == [] and c.scroll_deadline() == pytest.approx(clk.t + SCROLL_FOCUS_SETTLE_S)
    c.on_knob(1, "ccw")  # 2nd detent within the settle: summed, no second focus
    clk.advance(SCROLL_FOCUS_SETTLE_S)
    c.tick()
    assert scrolls(ads, Mode.DESKTOP) == [6] and acts(ads, Mode.DESKTOP, "focus") == [key, key]
    assert c.view().sub.scrollback == SCROLL_HINT
    clk.advance(1.0)
    c.on_knob(1, "cw")
    c.tick()
    assert scrolls(ads, Mode.DESKTOP) == [6, -3] and len(acts(ads, Mode.DESKTOP, "focus")) == 2
    clk.advance(SCROLL_IDLE_S)
    c.tick()  # idle: only the banner goes, Codex is never pulled forward / scrolled by itself
    assert c.scrolling is None and c.view().sub.scrollback == "" and acts(ads, Mode.DESKTOP, "bottom") == []
    assert c.toast is None or c.toast[0] != SCROLL_IDLE_LIVE


def test_desktop_press_focuses_then_scrolls_to_bottom():
    c, clk, ads = make(Mode.DESKTOP)
    tap(c, clk, 3, 1)
    key = c.mgr.selected
    press(c, clk)
    c.tick()
    assert acts(ads, Mode.DESKTOP, "focus") == [key, key] and acts(ads, Mode.DESKTOP, "bottom") == []
    clk.advance(SCROLL_FOCUS_SETTLE_S)
    c.tick()
    assert acts(ads, Mode.DESKTOP, "bottom") == [key] and c.toast[0] == SCROLL_LIVE
    c.on_key(1, 4, True)  # PTT held: K1 never brings a window forward
    press(c, clk)
    assert c.toast[0] == SCROLL_PTT and len(acts(ads, Mode.DESKTOP, "focus")) == 2
    c.on_key(1, 4, False)


def test_desktop_missing_helper_hint_once_then_quiet():
    c, clk, ads = make(Mode.DESKTOP)
    tap(c, clk, 3, 1)
    ads[Mode.DESKTOP].scroll_reply = ActionResult(False, scrollhelper.SETUP_HINT, {"setup": "untrusted"})
    c.on_knob(1, "ccw")
    clk.advance(SCROLL_FOCUS_SETTLE_S)
    c.tick()
    sub = c.view().sub
    assert sub.scrollback == scrollhelper.SETUP_HINT == "回看需要辅助功能授权：运行 mk20deck scroll-setup"
    assert sub.scrollback_warn and c.scrolling is None
    n = len(ads[Mode.DESKTOP].actions)
    for _ in range(5):  # within the quiet period: nothing else happens (no focus, no helper call)
        clk.advance(1.0)
        c.on_knob(1, "ccw")
        press(c, clk)
        c.tick()
    assert len(ads[Mode.DESKTOP].actions) == n
    clk.advance(SCROLL_NOTE_S)
    assert c.view().sub.scrollback == ""
    clk.advance(SCROLL_SETUP_QUIET_S)
    ads[Mode.DESKTOP].scroll_reply = None  # allowed meanwhile: works on the next try
    c.on_knob(1, "ccw")
    clk.advance(SCROLL_FOCUS_SETTLE_S)
    c.tick()
    assert scrolls(ads, Mode.DESKTOP) == [3, 3] and c.view().sub.scrollback == SCROLL_HINT


# ------------------------------------------------------------------ tmux adapter: exact argv
def tmux_ad(responses=None):
    r = RecordingRunner(responses or {}, binaries={"tmux"})
    ad = TmuxAdapter(TmuxConfig(), runner=r)
    ad.generation = GEN
    return ad, r


def pane(native="%3", gen=GEN):
    return Session(SessionId(Mode.TMUX, native, gen), Tool.CCAI, "x")


IN_MODE = ["display-message", "-p", "-t", "%3", "#{pane_in_mode}"]


def test_tmux_scroll_up_enters_copy_mode_and_scrolls_the_pane():
    ad, r = tmux_ad({("tmux", "copy-mode"): RunResult(0, "1\n", "")})
    res = ad.scroll(pane(), 6)
    assert r.calls == [["tmux", "copy-mode", "-e", "-t", "%3", ";", "send-keys", "-t", "%3", "-X", "-N", "6",
                        "scroll-up", ";", *IN_MODE]]
    assert res.ok and res.detail == {"live": False}


def test_tmux_scroll_down_and_live_detection():
    ad, r = tmux_ad({("tmux", "send-keys"): RunResult(0, "0\n", "")})
    res = ad.scroll(pane(), -9)
    assert r.calls == [["tmux", "send-keys", "-t", "%3", "-X", "-N", "9", "scroll-down", ";", *IN_MODE]]
    assert res.ok and res.detail == {"live": True}  # -e: scrolled down to the bottom = left copy mode
    ad, r = tmux_ad({("tmux", "send-keys"): RunResult(1, "", "not in a mode\n")})
    assert ad.scroll(pane(), -3).detail == {"live": True}  # already live


def test_tmux_bottom_cancels_copy_mode_and_release():
    ad, r = tmux_ad()
    assert ad.scroll_bottom(pane()).ok and r.calls == [["tmux", "send-keys", "-t", "%3", "-X", "cancel"]]
    ad, r = tmux_ad({("tmux", "send-keys"): RunResult(1, "", "not in a mode\n")})
    assert ad.scroll_bottom(pane()).ok and ad.scroll_release(pane()) is True
    ad, r = tmux_ad({("tmux", "send-keys"): RunResult(1, "", "can't find pane: %3\n")})
    res = ad.scroll_bottom(pane())
    assert not res.ok and "can't find pane" in res.message and ad.scroll_release(pane()) is False


def test_tmux_scroll_refuses_stale_identity_and_reports_errors():
    ad, r = tmux_ad()
    res = ad.scroll(pane(gen="1-1"), 3)
    assert not res.ok and res.message == "tmux server 已重启，身份失效" and r.calls == []
    assert not ad.scroll_bottom(pane(gen="1-1")).ok and r.calls == []
    ad, r = tmux_ad({("tmux", "copy-mode"): RunResult(1, "", "can't find pane: %3\n")})
    res = ad.scroll(pane(), 3)
    assert not res.ok and res.message.startswith("tmux 回看失败")
    ad, r = tmux_ad()
    assert ad.scroll(pane(), 5000).ok and r.calls[-1][11] == "1000"  # bounded count
    assert ad.capabilities().scroll is None and not ad.capabilities().scroll_focus


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")
def test_tmux_scroll_on_a_private_tmux_server(tmp_path):
    """The real tmux: its own socket (-L) and no config, never the user's server."""
    sock = f"mk20k1-{uuid.uuid4().hex[:8]}"
    wrapper = tmp_path / "tmux"
    wrapper.write_text(f'#!/bin/sh\nexec env -u TMUX {shutil.which("tmux")} -L {sock} -f /dev/null "$@"\n')
    wrapper.chmod(0o755)
    tmx = [str(wrapper)]
    subprocess.run(tmx + ["new-session", "-d", "-s", "ag-ccai-k1", "-x", "80", "-y", "20", "seq 1 400; sleep 60"],
                   check=True, timeout=10)
    sock_path = subprocess.run(tmx + ["display-message", "-p", "#{socket_path}"], capture_output=True, text=True,
                               timeout=5).stdout.strip()
    try:
        ad = TmuxAdapter(TmuxConfig(binary=str(wrapper), use_hooks=False))
        for _ in range(50):
            got = subprocess.run(tmx + ["capture-pane", "-p", "-t", "ag-ccai-k1"], capture_output=True, text=True,
                                 timeout=5).stdout
            if "400" in got:
                break
            time.sleep(0.05)
        [s] = ad.poll()

        def in_mode():
            return subprocess.run(tmx + ["display-message", "-p", "-t", s.sid.native, "#{pane_in_mode}"],
                                  capture_output=True, text=True, timeout=5).stdout.strip()

        assert ad.scroll(s, -3).detail == {"live": True} and in_mode() == "0"  # nothing to scroll down to
        assert ad.scroll(s, 6).detail == {"live": False} and in_mode() == "1"
        assert ad.scroll(s, 3).detail == {"live": False}
        assert ad.scroll(s, -4).detail == {"live": False}
        assert ad.scroll(s, -30).detail == {"live": True} and in_mode() == "0"  # -e: out at the bottom
        assert ad.scroll(s, 9).ok and in_mode() == "1"
        assert ad.scroll_bottom(s).ok and in_mode() == "0"
        assert ad.scroll_bottom(s).ok  # not in a mode: already live
    finally:
        subprocess.run(tmx + ["kill-server"], timeout=10, capture_output=True)
        if sock_path.endswith(sock) and os.path.exists(sock_path):
            os.unlink(sock_path)  # tmux leaves the socket file behind


# ------------------------------------------------------------------ Codex Desktop adapter: the helper
def desk(tmp_path, responses=None, **cfg):
    helper = tmp_path / "mk20deck-scroll"
    helper.write_text("#!/bin/sh\nexit 0\n")
    helper.chmod(0o755)
    r = RecordingRunner(responses or {})
    ad = CodexDesktopAdapter(CodexDesktopConfig(scroll_helper=str(helper), **cfg), runner=r, app_alive=lambda: True)
    return ad, r, str(helper)


def thread():
    return Session(SessionId(Mode.DESKTOP, "01a14a13-e433-7790-9fee-1b69ee1f2369"), Tool.CODEX, "x")


def test_desktop_adapter_runs_the_helper(tmp_path):
    ad, r, helper = desk(tmp_path)
    assert ad.scroll(thread(), 6).ok and ad.scroll(thread(), -3).ok and ad.scroll_bottom(thread()).ok
    base = [helper, "--app", "com.openai.codex", "--via", "pid"]
    assert r.calls == [base + ["--lines", "6"], base + ["--lines", "-3"], base + ["--bottom"]]
    assert ad.scroll(thread(), -99999).ok and r.calls[-1][-1] == "-1000"
    assert ad.scroll_release(thread()) is False  # never scrolls Codex by itself
    caps = ad.capabilities()
    assert caps.scroll is None and caps.scroll_focus


def test_desktop_adapter_exit_codes(tmp_path):
    for rc, msg, setup in ((3, scrollhelper.SETUP_HINT, "untrusted"), (4, "回看：Codex 窗口不在屏幕上", None),
                           (5, "回看失败（helper 退出码 5）", None), (124, "回看失败（helper 退出码 124）", None)):
        ad, r, helper = desk(tmp_path, {(str(tmp_path / "mk20deck-scroll"),): RunResult(rc, "", "x")})
        res = ad.scroll(thread(), 3)
        assert (res.ok, res.message, res.detail.get("setup")) == (False, msg, setup)


def test_desktop_adapter_missing_helper_and_config(tmp_path):
    r = RecordingRunner()
    ad = CodexDesktopAdapter(CodexDesktopConfig(scroll_helper=str(tmp_path / "nope")), runner=r, app_alive=lambda: True)
    res = ad.scroll(thread(), 3)
    assert not res.ok and res.message == scrollhelper.SETUP_HINT and res.detail == {"setup": "missing"} and not r.calls
    ad, r, helper = desk(tmp_path, scroll_via="hid", scroll_app="Codex")
    ad.scroll(thread(), 3)
    assert r.calls[-1][:5] == [helper, "--app", "Codex", "--via", "hid"]
    ad, r, helper = desk(tmp_path, scroll_via="mouse")
    ad.scroll(thread(), 3)
    assert r.calls[-1][4] == "pid"  # unknown value: the default
    (tmp_path / "config.toml").write_text('[codex_desktop]\nscroll_via = "hid"\nscroll_helper = "~/bin/x"\n')
    cfg = load_config(str(tmp_path / "config.toml"))
    assert (cfg.codex.scroll_via, cfg.codex.scroll_helper, cfg.codex.scroll_app) == ("hid", "~/bin/x", "com.openai.codex")


def test_default_helper_path_follows_mk20deck_home(monkeypatch, tmp_path):
    monkeypatch.setenv("MK20DECK_HOME", str(tmp_path))
    assert scrollhelper.default_path() == str(tmp_path / "bin" / "mk20deck-scroll")
    assert os.path.isfile(scrollhelper.source_path()) and scrollhelper.source_path().endswith("tools/macos/mk20deck-scroll.m")


# ------------------------------------------------------------------ mk20deck scroll-setup (scripted subprocess)
class FakeTools(RecordingRunner):
    """clang writes its -o file, codesign succeeds, the helper answers --check / --request as scripted."""

    def __init__(self, trust=("no", "no"), clang_fail=(), sdk_base=None, binaries=("clang",)):
        super().__init__(binaries=set(binaries))
        self.trust = list(trust)
        self.clang_fail = set(clang_fail)
        self.sdk_base = sdk_base

    def run(self, argv, timeout=5.0):
        argv = list(argv)
        self.calls.append(argv)
        if argv[:2] == ["xcrun", "--sdk"]:
            return RunResult(0, f"{self.sdk_base}/MacOSX27.0.sdk\n", "") if self.sdk_base else RunResult(1, "", "")
        if argv[0] == "clang":
            sysroot = argv[2] if argv[1] == "-isysroot" else None
            if sysroot in self.clang_fail:
                return RunResult(1, "", "ld: tapi error: malformed file\n")
            out = argv[argv.index("-o") + 1]
            with open(out, "w") as f:
                f.write("#!/bin/sh\n")
            return RunResult(0, "", "")
        if argv[0] == "codesign":
            return RunResult(0, "", "")
        if argv[-1] in ("--check", "--request"):
            ans = self.trust.pop(0) if self.trust else "no"
            return RunResult(0 if ans == "yes" else 3, f"trusted: {ans}\nresponsible: self\n", "")
        return RunResult(0, "", "")


def setup_args(tmp_path, **kw):
    return SimpleNamespace(helper=str(tmp_path / "bin" / "mk20deck-scroll"), rebuild=False, open_settings=False, **kw)


def run_setup(args, runner):
    out = []
    rc = scrollhelper.main_setup(args, runner=runner, out=out.append, platform="darwin")
    return rc, "\n".join(out)


def test_scroll_setup_builds_signs_and_requests(tmp_path):
    args = setup_args(tmp_path)
    t = FakeTools(trust=["no", "no"])
    rc, text = run_setup(args, t)
    path = args.helper
    src = scrollhelper.source_path()
    assert rc == 1
    clang = [c for c in t.calls if c[0] == "clang"]
    assert clang == [["clang", *scrollhelper.CFLAGS, "-o", f"{path}.build-{os.getpid()}", src]]
    assert ["codesign", "--force", "--sign", "-", "--identifier", "com.leo.mk20deck-scroll",
            f"{path}.build-{os.getpid()}"] in t.calls
    assert [c for c in t.calls if c[0] == path] == [[path, "--check"], [path, "--request"]]
    assert os.access(path, os.X_OK) and not os.path.exists(f"{path}.build-{os.getpid()}")
    assert open(path + ".sha256").read().strip() == scrollhelper.source_digest(src)
    assert "系统设置 → 隐私与安全性 → 辅助功能" in text and path in text and "mk20deck-scroll" in text
    assert "不需要给 Python 或终端授权" in text
    # second run: up to date (the grant belongs to the binary: never rebuilt needlessly), now allowed
    t2 = FakeTools(trust=["yes"])
    rc, text = run_setup(args, t2)
    assert rc == 0 and "已授权" in text and not any(c[0] in ("clang", "codesign") for c in t2.calls)
    assert [c for c in t2.calls if c[0] == path] == [[path, "--check"]]


def test_scroll_setup_sdk_fallback_rebuild_and_settings(tmp_path):
    base = tmp_path / "SDKs"
    for name in ("MacOSX26.5.sdk", "MacOSX27.0.sdk"):
        (base / name).mkdir(parents=True)
    os.symlink(base / "MacOSX26.5.sdk", base / "MacOSX.sdk")
    args = setup_args(tmp_path, )
    args.open_settings = True
    t = FakeTools(trust=["no", "yes"], clang_fail={None}, sdk_base=str(base))
    rc, text = run_setup(args, t)
    sysroots = [c[2] if c[1] == "-isysroot" else None for c in t.calls if c[0] == "clang"]
    assert sysroots == [None, str(base / "MacOSX.sdk")]  # the default SDK failed: the toolchain's own link next
    assert ["open", scrollhelper.SETTINGS_URL] in t.calls and rc == 0
    args.rebuild = True
    t = FakeTools(trust=["yes"])
    assert run_setup(args, t)[0] == 0 and any(c[0] == "clang" for c in t.calls)


def test_scroll_setup_failures(tmp_path):
    args = setup_args(tmp_path)
    rc, text = run_setup(args, FakeTools(binaries=()))
    assert rc == 2 and "xcode-select --install" in text
    t = FakeTools(clang_fail={None}, sdk_base=None)
    rc, text = run_setup(args, t)
    assert rc == 2 and "编译失败" in text and "tapi error" in text and not os.path.exists(args.helper)
    out = []
    assert scrollhelper.main_setup(args, runner=FakeTools(), out=out.append, platform="linux") == 2
    assert "只用于 macOS" in out[0]


def test_scroll_setup_cli_uses_the_configured_helper(tmp_path, monkeypatch):
    from mk20deck import cli
    (tmp_path / "config.toml").write_text(f'[codex_desktop]\nscroll_helper = "{tmp_path}/h/mk20deck-scroll"\n')
    seen = {}
    monkeypatch.setattr(scrollhelper, "main_setup", lambda a: seen.setdefault("helper", a.helper) and 7)
    assert cli.main(["--config", str(tmp_path / "config.toml"), "scroll-setup"]) == 7
    assert seen["helper"] == f"{tmp_path}/h/mk20deck-scroll"


# ------------------------------------------------------------------ the helper itself (macOS only; never --request)
@pytest.mark.skipif(sys.platform != "darwin" or shutil.which("clang") is None, reason="macOS with clang only")
def test_helper_compiles_signs_and_checks_without_trust(tmp_path):
    path = str(tmp_path / "bin" / "mk20deck-scroll")
    b = scrollhelper.build(SubprocessRunner(), path)
    assert b.ok and b.built, (b.message, b.log)
    sig = subprocess.run(["codesign", "-dv", path], capture_output=True, text=True, timeout=30)
    assert "Identifier=com.leo.mk20deck-scroll" in sig.stderr
    r = subprocess.run([path, "--check"], capture_output=True, text=True, timeout=10)
    assert r.returncode in (0, 3) and ("trusted: yes" in r.stdout) == (r.returncode == 0), (r.stdout, r.stderr)
    assert "responsible: self" in r.stdout  # TCC judges the helper itself, not pytest / the terminal
    ok, info = scrollhelper.trusted(SubprocessRunner(), path)
    assert ok is (r.returncode == 0) and "trusted:" in info
    assert subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10).stdout.startswith(
        "mk20deck-scroll ")
    assert subprocess.run([path, "--lines", "3"], capture_output=True, timeout=10).returncode == 2  # no --app
    assert subprocess.run([path, "--app", "x", "--lines", "0"], capture_output=True, timeout=10).returncode == 2
    gone = subprocess.run([path, "--app", "com.example.mk20.none", "--lines", "3", "--dry-run"], capture_output=True,
                          text=True, timeout=10)
    assert gone.returncode == 4 and "not running" in gone.stderr
    again = scrollhelper.build(SubprocessRunner(), path)
    assert again.ok and not again.built  # up to date: the binary (and a grant for it) stays


# ------------------------------------------------------------------ daemon + rendering
def test_daemon_sends_one_scroll_per_step_and_wakes_for_the_settle():
    clk = FakeClock()
    ads = demo_adapters()
    c = Controller(DeckConfig(default_mode=Mode.TMUX), ads, clock=clk, wall=lambda: 1.0, sim=True)
    d = Daemon(c, Renderer(DeviceLayout.default(), animations=False), None, clock=clk, bg_poll_s=0)
    d.step()
    tap(c, clk, 1, 1)
    for _ in range(3):
        d.inject_knob(1, "ccw")
    d.step()
    assert scrolls(ads) == [3 + 3 + 6]  # three queued detents: one tmux call
    c.switch_mode(Mode.DESKTOP)
    tap(c, clk, 3, 1)
    d.inject_knob(1, "ccw")
    d.step()
    d.heartbeat, d._next_poll = 1e9, 1e12
    assert d.next_wait() == pytest.approx(SCROLL_FOCUS_SETTLE_S, abs=0.01)  # wakes for the deferred send
    clk.advance(SCROLL_FOCUS_SETTLE_S)
    d.step()
    assert scrolls(ads, Mode.DESKTOP) == [3]


def _band(img, color, y=96):  # top of the banner, above its text
    px = img.load()
    return sum(1 for x in range(10, 418) if px[x, y] == color)


def test_subscreen_scrollback_banner_and_setup_hint(tm):
    c, clk, ads, key = tm
    assert _band(render_sub(c.view().sub, clk.t), SCROLL_BLUE) == 0
    c.on_knob(1, "ccw")
    c.tick()
    c.toast = None
    sub = c.view().sub
    assert sub.scrollback == SCROLL_HINT and not sub.scrollback_warn
    assert _band(render_sub(sub, clk.t), SCROLL_BLUE) > 300
    c.scroll_note = (scrollhelper.SETUP_HINT, clk.t + 5)
    c.scrolling = None
    sub = c.view().sub
    assert sub.scrollback_warn and _band(render_sub(sub, clk.t), SCROLL_WARN) > 300


def test_help_texts_and_sim_shots(tmp_path):
    import inspect
    from mk20deck.render import subscreen
    src = inspect.getsource(subscreen)
    assert "转 K1 挑待处理" not in src and "转 K1 回看" in src
    made = scroll_shots(str(tmp_path))
    assert [n for n, _ in made] == ["scroll-01-tmux-scrolled-back.png", "scroll-02-k1-press-latest.png",
                                    "scroll-03-desktop-needs-setup.png"]
    assert all((tmp_path / n).stat().st_size > 10_000 for n, _ in made)
