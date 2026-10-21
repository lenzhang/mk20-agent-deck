"""Cross-mode awareness: background poll of non-current modes, counts on sub-screen/L4, sim shots."""
import threading
import time

from mk20deck.adapters.base import BackendUnavailable
from mk20deck.adapters.mock import MockAdapter
from mk20deck.controller import Role, SubView
from mk20deck.daemon import Daemon
from mk20deck.layout import DeviceLayout
from mk20deck.model import Mode, State
from mk20deck.render.canvas import Renderer
from mk20deck.render.subscreen import AMBER, render_sub
from mk20deck.sim import FakeClock, _ctl, attention_shots, demo_adapters


class CountingAdapter(MockAdapter):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.polls = 0

    def poll(self, keep_ids=()):
        self.polls += 1
        return super().poll(keep_ids)


class Broken(MockAdapter):
    def poll(self, keep_ids=()):
        raise KeyError("bug")


def _tmux_attention(ads, n=2):
    for s in ads[Mode.TMUX].sessions[:n]:
        s.state = State.NEEDS_APPROVAL
    return ads


def test_background_poll_skips_current_mode_and_applies_on_step():
    clk = FakeClock()
    fg = _tmux_attention(demo_adapters())
    c = _ctl(clk, fg)
    bg = {m: CountingAdapter(m, list(a.sessions), a.unavailable) for m, a in fg.items()}
    d = Daemon(c, Renderer(DeviceLayout.default()), None, clock=clk, bg_adapters=bg)
    assert d.poll_background_once() == 2
    assert bg[Mode.DESKTOP].polls == 0 and bg[Mode.TMUX].polls == 1 and bg[Mode.HERDR].polls == 1
    assert c.bg == {}  # queued, not applied off the loop thread
    d.step(force=True)
    assert c.view().sub.other_modes == "tmux 2 待处理"
    assert not c.bg[Mode.HERDR].ok and c.bg[Mode.HERDR].reason == "herdr 未安装"


def test_background_poll_bug_never_raises():
    clk = FakeClock()
    ads = demo_adapters()
    ads[Mode.TMUX] = Broken(Mode.TMUX)
    c = _ctl(clk, ads)
    d = Daemon(c, Renderer(DeviceLayout.default()), None, clock=clk)
    d.poll_background_once()
    d.step(force=True)
    assert not c.bg[Mode.TMUX].ok and "KeyError" in c.bg[Mode.TMUX].reason


def test_run_loop_polls_other_modes_in_background_without_blocking():
    ads = _tmux_attention(demo_adapters(), 3)
    c = _ctl(FakeClock(), ads)
    c.clock = time.monotonic
    gate = threading.Event()

    class Slow(CountingAdapter):
        def poll(self, keep_ids=()):
            gate.wait(2)  # a stuck backend must not stall the frame loop
            return super().poll(keep_ids)

    bg = {Mode.TMUX: Slow(Mode.TMUX, list(ads[Mode.TMUX].sessions)), Mode.HERDR: ads[Mode.HERDR]}
    d = Daemon(c, Renderer(DeviceLayout.default()), None, poll_interval=0.2, heartbeat=0.2, bg_poll_s=0.05,
               bg_first_delay_s=0.0, bg_adapters=bg)
    stop = threading.Event()
    th = threading.Thread(target=d.run, args=(stop,), daemon=True)
    th.start()
    time.sleep(0.6)
    frames_while_stuck = d.frames_sent + d.frames_failed
    assert frames_while_stuck >= 2 and Mode.TMUX not in c.bg
    gate.set()
    deadline = time.time() + 3
    while time.time() < deadline and Mode.TMUX not in c.bg:
        time.sleep(0.05)
    stop.set()
    th.join(3)
    assert c.bg[Mode.TMUX].attention == 3 and d.bg_polls >= 1


def test_bg_poll_disabled():
    c = _ctl(FakeClock())
    c.clock = time.monotonic
    d = Daemon(c, Renderer(DeviceLayout.default()), None, bg_poll_s=0, heartbeat=0.1)
    stop = threading.Event()
    th = threading.Thread(target=d.run, args=(stop,), daemon=True)
    th.start()
    time.sleep(0.3)
    stop.set()
    th.join(2)
    assert d.bg_polls == 0 and c.bg == {}


def test_subscreen_pill_and_mode_badge_render():
    sv = SubView("L1", Mode.DESKTOP, True, "", None, None, None, 0, 0, None, "12:00:00", False, 0,
                 other_modes="tmux 2 待处理")
    def amber_px(img):  # header band, right of "L1 · CODEX", left of the clock
        return sum(1 for x in range(60, 300) for y in range(4, 22)
                   if all(abs(a - b) < 30 for a, b in zip(img.getpixel((x, y)), AMBER)))
    plain = SubView("L1", Mode.DESKTOP, True, "", None, None, None, 0, 0, None, "12:00:00", False, 0)
    assert amber_px(render_sub(sv, 0.0)) > 500 and amber_px(render_sub(plain, 0.0)) == 0


def test_l4_mode_key_badge_face_differs():
    from mk20deck.render.keys import render_key
    c = _ctl(FakeClock(), _tmux_attention(demo_adapters()))
    c.apply_background(Mode.TMUX, c.adapters[Mode.TMUX].poll())
    c.page = "L4"
    kv = next(k for k in c.view().keys if k.role == Role.MODE_SELECT and k.mode == Mode.TMUX)
    assert kv.badge == "2" and kv.badge_alert
    with_badge = render_key(kv, 0, 0, False)
    kv.badge, kv.badge_alert = "", False
    assert with_badge.getpixel((116, 12)) != render_key(kv, 0, 0, False).getpixel((116, 12))


def test_attention_sim_shots(tmp_path):
    made = attention_shots(str(tmp_path), animations=False)
    assert len(made) == 6 and all((tmp_path / n).stat().st_size > 10_000 for n, _ in made)
    assert (tmp_path / "index.html").exists()
