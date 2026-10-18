"""Scripted, headless interaction tests driving the real controller via raw firmware events."""
import pytest

from mk20sim import Sim


@pytest.fixture
def s():
    return Sim()


def test_single_tap_selects_without_focus(s):
    s.press("A1")
    assert s.state()["selected"]
    assert not s.agent_calls("focus")


def test_double_tap_within_350ms_focuses(s):
    s.press("A1", 40)
    t = s.now()
    s.wait(100)
    s.press("A1", 40)
    focus = s.agent_calls("focus")
    assert focus and focus[0].t - t <= 0.350


def test_slow_double_tap_does_not_focus(s):
    s.press("A1", 40)
    s.wait(400)
    s.press("A1", 40)
    assert not s.agent_calls("focus")


def test_stop_short_press_only_hints(s):
    s.press("A1")
    s.press("STOP", 100)
    assert not s.agent_calls("stop")


def test_mode_short_cycles_long_opens_l4(s):
    m0 = s.mode
    s.press("MODE", 100)
    assert s.mode != m0 and s.page == "L1"
    s.hold("MODE", 650)
    assert s.page == "L4"
    s.press("BACK")
    assert s.page == "L1"


def test_knob_codes_paging(s):
    s.knob("K2", "press")
    assert s.page == "L4"
    s.knob("K2", "press")
    assert s.page == "L1"
    s.knob("K1", "down"); s.wait(650); s.knob("K1", "up")
    assert s.page == "L4"
    s.knob("K2", "down"); s.wait(650); s.knob("K2", "up")
    assert s.page == "L1"


def test_k1_rotate_routes_effort_to_selected(s):
    s.press("A1")
    s.knob("K1", "cw", 3)
    assert len(s.agent_calls("set_effort")) == 3


def test_k2_rotate_scrolls_only(s):
    s.press("A1")
    sel = s.state()["selected"]
    s.knob("K2", "cw", 2)
    assert s.state()["scroll"] == 2 and s.state()["selected"] == sel


def test_ptt_hold_emits_hid_down_up(s):
    s.down("PTT"); s.wait(300); s.up("PTT")
    hid = [o.text for o in s.out("hid")]
    assert hid[0].startswith("RightOption down") and hid[1].startswith("RightOption up")


def test_unmapped_knob_code_never_hits_grid(s):
    s.raw(106, 106, True)
    assert not s.agent_calls() and s.page == "L1"


def test_screen_sink_captures_key_and_sub_images(s):
    assert s.key_image("A1").size == (128, 128)
    assert s.sub_image().size == (428, 142)
    assert s.frames > 0
