"""Unit tests for the real settings page model (mk20deck.settings) and its controller wiring."""
import json

from mk20deck.config import DeckConfig
from mk20deck.controller import Controller
from mk20deck.settings import DEFAULTS, LoggingApplier, SettingsPage, SettingsStore
from mk20deck.sim import FakeClock, demo_adapters


def page(tmp_path, applier=None):
    return SettingsPage(store=SettingsStore(str(tmp_path / "s.json")), applier=applier or LoggingApplier())


def test_store_defaults_and_roundtrip(tmp_path):
    st = SettingsStore(str(tmp_path / "x" / "s.json"))
    assert st.load() == DEFAULTS
    st.save({**DEFAULTS, "provider": "glm"})
    assert st.load()["provider"] == "glm"


def test_store_ignores_garbage_and_wrong_types(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("{not json")
    assert SettingsStore(str(p)).load() == DEFAULTS
    p.write_text(json.dumps({"proxy_port": "7890", "evil": 1, "volume": 30}))
    d = SettingsStore(str(p)).load()
    assert d["proxy_port"] == 7890 and d["volume"] == 30 and "evil" not in d


def test_network_change_needs_second_apply_and_persists(tmp_path):
    ap = LoggingApplier()
    s = page(tmp_path, ap)
    s.open()
    s.key(2, 2)            # 板载 Wi-Fi
    s.key(2, 4)            # 应用 #1 → confirm armed
    assert s.confirm_net and not ap.calls
    s.key(2, 4)            # 应用 #2
    assert ap.calls == [{"profile": "board_wifi"}]
    assert SettingsStore(s.store.path).load()["profile"] == "board_wifi"


def test_confirm_disarmed_by_other_input(tmp_path):
    s = page(tmp_path)
    s.open()
    s.key(2, 2)
    s.key(2, 4)
    s.key(4, 1)            # switch tab → disarm
    assert not s.confirm_net
    s.key(2, 4)
    assert s.confirm_net   # needs confirm again


def test_non_network_apply_is_single_press(tmp_path):
    ap = LoggingApplier()
    s = page(tmp_path, ap)
    s.open()
    s.key(5, 1)            # 设备信息
    s.knob(1, "cw")        # brightness 80 → 90
    s.key(2, 4)
    assert ap.calls == [{"brightness": 90}]


def test_applier_failure_keeps_pending(tmp_path):
    class Bad:
        def apply(self, changes, full):
            return False, "boom"
    s = page(tmp_path, Bad())
    s.open()
    s.key(5, 1)
    s.knob(1, "cw")
    s.key(2, 4)
    assert s.pending == {"brightness": 90} and "boom" in s.toast


def test_staging_back_to_saved_value_clears_pending(tmp_path):
    s = page(tmp_path)
    s.open()
    s.key(5, 1)
    s.knob(1, "cw")
    s.knob(1, "ccw")
    assert s.pending == {}


def test_controller_l4_k1_short_press_opens_and_renders(tmp_path):
    clk = FakeClock()
    c = Controller(DeckConfig(), demo_adapters(), clock=clk, sim=True, settings=page(tmp_path))
    c.page = "L4"
    c.on_knob(1, "down"); clk.advance(0.1); c.on_knob(1, "up")
    assert c.page == "LS" and c.view().settings is c.settings
    from mk20deck.layout import DeviceLayout
    from mk20deck.render.canvas import Renderer
    img = Renderer(DeviceLayout.default(), animations=False).compose(c.view(), clk.t)
    assert img.size == (640, 656)
    c.on_key(5, 4, True)   # 回 L1
    assert c.page == "L1"


def test_controller_k1_short_press_on_l1_stays_on_l1(tmp_path):
    clk = FakeClock()
    c = Controller(DeckConfig(), demo_adapters(), clock=clk, sim=True, settings=page(tmp_path))
    c.on_knob(1, "down"); clk.advance(0.1); c.on_knob(1, "up")
    assert c.page == "L1"
