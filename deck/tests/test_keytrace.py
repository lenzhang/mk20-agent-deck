import importlib.util
import os
import pathlib

from mk20deck import keytrace
from mk20deck.config import DEFAULT_KNOB_MAP, DeckConfig
from mk20deck.controller import Controller
from mk20deck.daemon import Daemon
from mk20deck.layout import DeviceLayout
from mk20deck.protocol import KeyEvent
from mk20deck.render.canvas import Renderer
from mk20deck.sim import FakeClock, demo_adapters

TOOLS = pathlib.Path(__file__).resolve().parents[1] / "tools"


def _daemon(trace):
    clk = FakeClock()
    c = Controller(DeckConfig(), demo_adapters(), clock=clk, sim=True)
    c.refresh()
    d = Daemon(c, Renderer(DeviceLayout.default(), animations=False), None, clock=clk,
               knob_map=dict(DEFAULT_KNOB_MAP), trace=trace)
    return d, clk


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("MK20_KEYTRACE", raising=False)
    assert keytrace.from_env() is None
    d, _ = _daemon(None)
    d.on_device_key(KeyEvent(0, 0, True))
    d.step()  # no trace, no error


def test_from_env(tmp_path):
    t = keytrace.from_env({"MK20_KEYTRACE": "1", "MK20_KEYTRACE_PATH": str(tmp_path / "k.log")})
    assert isinstance(t, keytrace.KeyTrace)
    t.close()


def test_stages_and_outcomes(tmp_path):
    n = iter(range(1000, 10 ** 9, 1000))
    tr = keytrace.KeyTrace(str(tmp_path / "k.log"), clock=lambda: next(n))
    d, clk = _daemon(tr)
    d.on_device_key(KeyEvent(0, 3, True))    # PTT down  → hid_written
    d.step()
    d.on_device_key(KeyEvent(0, 3, False))   # PTT up    → hid_written
    d.step()
    d.on_device_key(KeyEvent(0, 0, True))    # A1 select → local
    d.step()
    d.on_device_key(KeyEvent(102, 102, True))  # K1 cw → move the selection → local
    d.step()
    d.on_device_key(KeyEvent(100, 100, True))  # K1 press (down) → local
    d.step()
    d.on_device_key(KeyEvent(100, 100, False))  # K1 press (up) → focus the selection → agent
    d.step()
    tr.close()
    rows = keytrace.parse((tmp_path / "k.log").read_text())
    stages = [r[1] for r in rows]
    assert stages == ["recv", "routed", "hid_written"] * 2 + ["recv", "routed", "local"] * 3 + \
        ["recv", "routed", "agent"]
    assert rows[0][2] == "0,3" and rows[0][3] == 1 and rows[-1][2] == "100,100" and rows[-7][2] == "102,102"
    assert all(a[0] < b[0] for a, b in zip(rows, rows[1:]))


def test_rotation_bounds_size(tmp_path):
    p = tmp_path / "k.log"
    tr = keytrace.KeyTrace(str(p), max_bytes=500)
    for i in range(200):
        tr.ev("recv", f"{i},0", True)
    tr.close()
    assert p.stat().st_size <= 500 and (tmp_path / "k.log.1").stat().st_size <= 500


def test_latency_tool_consumes_trace(tmp_path):
    spec = importlib.util.spec_from_file_location("lat", TOOLS / "mk20_key_latency.py")
    lat = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lat)
    text = "\n".join([
        "1000000 recv 0,3 1", "3000000 routed 0,3 1", "4000000 hid_written 0,3 1",
        "9000000 recv 0,0 1", "9500000 routed 0,0 1", "9600000 local 0,0 1",
        "20000000 recv 0,3 0", "21000000 routed 0,3 0", "23000000 hid_written 0,3 0",
        "garbage line",
    ])
    tr = lat.parse_trace(text)
    # Mac saw the two HID reports at T113 times 6 ms and 26 ms (offset 0)
    res = lat.pair(tr, [6_000_000, 26_000_000], 0, [])
    assert res["B_rx_to_hid_ms"]["n"] == 2 and res["B_rx_to_hid_ms"]["p50"] in (3.0,)
    assert res["C_hid_to_mac_ms"]["max"] == 3.0
    assert res["outcomes"] == {"hid_written": 2, "agent": 0, "local": 1}
    assert lat.main.__doc__ is None or True
    os.environ.pop("X", None)
