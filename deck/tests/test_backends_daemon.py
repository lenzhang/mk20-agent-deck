import threading
import time

import pytest

from mk20deck.adapters.base import BackendUnavailable, RecordingRunner, RunResult
from mk20deck.adapters.herdr import HerdrAdapter, map_status
from mk20deck.adapters.tmux import TmuxAdapter, TmuxConfig
from mk20deck.daemon import Daemon
from mk20deck.layout import DeviceLayout
from mk20deck.link import DeviceLink
from mk20deck.model import State, Tool
from mk20deck.render.canvas import Renderer
from mk20deck.sim import FakeClock, _ctl
from mk20deck.transport import MockDevice

PANES = "\n".join([
    "ag-ccai-api\t0\tmain\t%1\thost\t/Users/dev/api\tnode",
    "work\t1\tag-codex-web\t%2\thost\t/Users/dev/web\tcodex",
    "misc\t0\tzsh\t%3\thost\t/Users/dev\tzsh",
    "misc\t1\tlogs\t%9\thost\t/Users/dev\ttail",
])


def tmux_runner(clients="client0\n"):
    return RecordingRunner({
        ("tmux", "display-message"): RunResult(0, "4242-1700000000\n", ""),
        ("tmux", "list-panes"): RunResult(0, PANES, ""),
        ("tmux", "list-clients"): RunResult(0, clients, ""),
    }, binaries={"tmux"})


def test_tmux_discovery_identity_and_unknown_state():
    a = TmuxAdapter(TmuxConfig(registered_panes=("%9",)), tmux_runner())
    ss = {s.sid.native: s for s in a.poll()}
    assert set(ss) == {"%1", "%2", "%9"}
    assert ss["%1"].tool == Tool.CCAI and ss["%2"].tool == Tool.CODEX
    assert all(s.state == State.UNKNOWN for s in ss.values())
    assert ss["%1"].sid.generation == "4242-1700000000"


def test_tmux_unavailable_reasons():
    with pytest.raises(BackendUnavailable, match="未安装"):
        TmuxAdapter(runner=RecordingRunner()).poll()
    r = RecordingRunner({("tmux", "display-message"): RunResult(1, "", "no server")}, binaries={"tmux"})
    with pytest.raises(BackendUnavailable, match="未运行"):
        TmuxAdapter(runner=r).poll()


def test_tmux_focus_requires_terminal_and_single_client():
    a = TmuxAdapter(TmuxConfig(), tmux_runner())
    s = a.poll()[0]
    assert not a.focus(s).ok and a.capabilities().focus
    r = tmux_runner("c0\nc1\n")
    a = TmuxAdapter(TmuxConfig(terminal_app="Ghostty"), r)
    s = a.poll()[0]
    assert not a.focus(s).ok
    r = tmux_runner()
    a = TmuxAdapter(TmuxConfig(terminal_app="Ghostty"), r)
    s = a.poll()[0]
    assert a.focus(s).ok
    assert r.calls[-1] == ["open", "-a", "Ghostty"]


def test_herdr_stub():
    with pytest.raises(BackendUnavailable, match="herdr 未安装"):
        HerdrAdapter(runner=RecordingRunner()).poll()
    assert map_status("blocked")[0] == State.UNKNOWN  # no reason -> unknown (§3.1)
    assert map_status("working")[0] == State.RUNNING
    r = RecordingRunner({("herdr", "agent"): RunResult(0, '[{"id":"a1","name":"codex-1","status":"idle"},'
                                                          '{"id":"a2","name":"vim","status":"idle"}]', "")},
                        binaries={"herdr"})
    ss = HerdrAdapter(runner=r).poll()
    assert [s.sid.native for s in ss] == ["a1"]


def test_daemon_end_to_end_with_mock_device():
    dev = MockDevice()
    clk = FakeClock()
    ctl = _ctl(clk)
    r = Renderer(DeviceLayout.default())
    d = Daemon(ctl, r, None, poll_interval=2.0, heartbeat=2.0)
    link = DeviceLink(dev, on_key=d.on_device_key, ack_timeout=1.0, preamble_flush=0)
    d.link = link
    link.start()
    r.layout = link.handshake()
    assert d.step()  # first frame
    assert not d.step()  # nothing changed, no animation due at same instant? (fps>0 but same t)
    dev.press(0, 0, True)  # firmware (0,0) -> doc (1,1) -> select A1
    for _ in range(100):
        if not d.events.empty():
            break
        time.sleep(0.01)
    assert d.step()
    assert ctl.mgr.selected is not None
    assert d.event_to_ack.summary()["n"] == 1
    clk.advance(2.1)
    assert d.step()  # heartbeat
    n = len(dev.frames)
    d.offline_frame("test")
    assert len(dev.frames) == n + 1
    assert all(q.get("method") != "keyboardInput" for q in dev.json_requests)
    link.close()


def test_daemon_run_loop_latency_mock():
    dev = MockDevice(draw_delay=0.01)
    ctl = _ctl(FakeClock())
    ctl.clock = time.monotonic
    r = Renderer(DeviceLayout.default())
    d = Daemon(ctl, r, None, poll_interval=0.5, heartbeat=2.0)
    link = DeviceLink(dev, on_key=d.on_device_key, ack_timeout=1.0, preamble_flush=0)
    d.link = link
    link.start()
    r.layout = link.handshake()
    stop = threading.Event()
    th = threading.Thread(target=d.run, args=(stop,), daemon=True)
    th.start()
    for i in range(10):
        dev.press(i % 4, i % 2, True)
        time.sleep(0.03)
        dev.press(i % 4, i % 2, False)
        time.sleep(0.25)
    stop.set()
    th.join(2)
    s = d.event_to_ack.summary()
    assert s["n"] >= 10
    assert s["p95_ms"] < 1000  # AC1 mock target (event→ack incl. render), not a device measurement
    link.close()
