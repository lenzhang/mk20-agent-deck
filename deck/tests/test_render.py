import io

from PIL import Image, ImageChops, ImageStat

from mk20deck.controller import KeyView, Role
from mk20deck.layout import DeviceLayout
from mk20deck.model import Mode, State, Tool
from mk20deck.render.canvas import Renderer
from mk20deck.render.keys import key_fps, render_key
from mk20deck.render.palette import STATE_COLOR
from mk20deck.sim import FakeClock, _ctl, tap


def face(st, sel=False, tool=Tool.CODEX):
    return render_key(KeyView(1, 1, Role.AGENT, state=st, tool=tool, title="会话", selected=sel), 5, 0, False)


def diff(a, b):
    return sum(ImageStat.Stat(ImageChops.difference(a, b)).mean)


def test_all_base_states_single_frame_distinct():
    faces = {st: face(st) for st in State}
    for st, im in faces.items():
        assert im.size == (128, 128)
    states = list(State)
    for i, a in enumerate(states):
        for b in states[i + 1:]:
            assert diff(faces[a], faces[b]) > 1.0, (a, b)


def test_unknown_offline_approval_strongly_distinct():
    for a, b in ((State.UNKNOWN, State.OFFLINE), (State.UNKNOWN, State.NEEDS_APPROVAL),
                 (State.OFFLINE, State.NEEDS_APPROVAL), (State.NEEDS_INPUT, State.NEEDS_APPROVAL)):
        assert diff(face(a), face(b)) > 2.0, (a, b)


def test_selected_overlay_visible_for_every_state():
    for st in State:
        if st == State.UNASSIGNED:
            continue
        assert diff(face(st), face(st, sel=True)) > 1.0, st


def test_state_border_colour_present():
    for st in (State.THINKING, State.DONE_UNREAD, State.ERROR, State.RUNNING):
        im = face(st)
        px = im.getpixel((2, 64))
        assert sum(abs(px[i] - STATE_COLOR[st][i]) for i in range(3)) < 60, st


def test_mode_key_neutral_background():
    for m in Mode:
        im = render_key(KeyView(5, 4, Role.MODE, label=m.key_label, mode=m), 0, 0, False)
        r, g, b = im.getpixel((6, 6))
        assert max(r, g, b) - min(r, g, b) < 16  # no orange/green/purple fill


def test_fps_budget_and_single_attention():
    clk = FakeClock()
    c = _ctl(clk)
    r = Renderer(DeviceLayout.default())
    v = c.view()
    att = [k for k in v.keys if k.attention]
    assert len(att) == 1  # only one attention animation
    assert att[0].state == State.NEEDS_APPROVAL  # highest priority first
    assert 0 < r.desired_fps(v, clk.t) <= 8
    r2 = Renderer(DeviceLayout.default(), animations=False)
    assert r2.desired_fps(v, clk.t) == 0
    kv = KeyView(1, 1, Role.AGENT, state=State.ERROR, attention=True)
    assert key_fps(kv, 0.1, True) == 8 and key_fps(kv, 2.0, True) == 0
    kv = KeyView(1, 1, Role.AGENT, state=State.DONE_UNREAD, attention=True)
    assert key_fps(kv, 0.1, True) == 8 and key_fps(kv, 2.0, True) == 2


def test_canvas_jpeg_matches_layout():
    clk = FakeClock()
    c = _ctl(clk)
    tap(c, clk, 1, 1)
    r = Renderer(DeviceLayout.default())
    jpg = r.jpeg(r.compose(c.view(), clk.t))
    im = Image.open(io.BytesIO(jpg))
    assert im.size == (640, 656) and im.format == "JPEG"
    assert len(jpg) < 200_000
    off = Image.open(io.BytesIO(r.jpeg(r.compose_offline("x", "00:00:00"))))
    assert off.size == (640, 656)


def test_render_speed_reasonable():
    import time
    clk = FakeClock()
    c = _ctl(clk)
    r = Renderer(DeviceLayout.default())
    v = c.view()
    t0 = time.perf_counter()
    for i in range(5):
        r.jpeg(r.compose(v, clk.t + i / 8))
    per = (time.perf_counter() - t0) / 5
    assert per < 0.5, per
