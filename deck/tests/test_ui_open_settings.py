"""mk20-kbd 网页设置 key: device event {"method":"ui.openSettings"} opens the configured web_url on the Mac."""
import io
import logging
import subprocess
import sys
import threading
import time

import pytest
from PIL import Image

from mk20deck import daemon as D
from mk20deck.config import DEFAULT_WEB_URL, DeckConfig, load_config, parse_web_url
from mk20deck.layout import DeviceLayout
from mk20deck.link import DeviceLink
from mk20deck.render.canvas import Renderer
from mk20deck.sim import FakeClock, _ctl
from mk20deck.supervisor import Supervisor, default_renderer_factory
from mk20deck.transport import MockDevice

EVENT = {"method": "ui.openSettings"}


def wait_until(pred, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def opened(monkeypatch):
    """subprocess.run stand-in on a pretend Mac: records ``open`` invocations."""
    calls = []

    def fake_run(argv, **kw):
        if list(argv)[:1] == ["open"]:
            calls.append((list(argv), kw))
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "platform", "darwin")
    return calls


def _jpeg():
    b = io.BytesIO()
    Image.new("RGB", (640, 656)).save(b, "JPEG")
    return b.getvalue()


def _setup(url=DEFAULT_WEB_URL):
    clk = FakeClock()
    dev = MockDevice()
    d = D.Daemon(_ctl(clk), Renderer(DeviceLayout.default()), None, clock=clk, web_url=url)
    link = DeviceLink(dev, preamble_flush=0, ack_timeout=0.5, on_key=d.on_device_key, on_event=d.on_device_event)
    d.link = link
    link.start()
    link.handshake()
    return clk, dev, d, link


def _deliver(dev, d, obj, n_events=1):
    dev.emit_json(obj, frame_id=MockDevice.EVENT_ID)
    if n_events:
        assert wait_until(lambda: d.events.qsize() >= n_events)
    else:
        time.sleep(0.2)
    d.step()


# ------------------------------------------------------------------ config
@pytest.mark.parametrize("url", [DEFAULT_WEB_URL, "https://192.168.88.6:8443/settings?x=1", "http://localhost:8780/",
                                 "http://[::1]:8780/", "http://LOCALHOST/"])
def test_web_url_accepted(url):
    assert parse_web_url(url) == url


@pytest.mark.parametrize("url", ["http://example.com/", "http://172.30.250.2.evil.com/", "ftp://1.2.3.4/",
                                 "javascript:alert(1)", "file:///etc/passwd", "http://1.2.3.4:99999/",
                                 "http://1.2.3.4/\nx", "http:///nohost", "", 42, None, "-a Terminal"])
def test_web_url_rejected_falls_back_to_default(url, caplog):
    with caplog.at_level(logging.WARNING, logger="mk20deck.config"):
        assert parse_web_url(url) == DEFAULT_WEB_URL
    assert "rejected" in caplog.text


def test_web_url_with_credentials_rejected_and_not_logged(caplog):
    with caplog.at_level(logging.WARNING, logger="mk20deck.config"):
        assert parse_web_url("http://admin:hunter2@172.30.250.2:8780/") == DEFAULT_WEB_URL
    assert "credentials" in caplog.text and "hunter2" not in caplog.text


def test_web_url_config_load(tmp_path):
    assert DeckConfig().web_url == DEFAULT_WEB_URL == "http://172.30.250.2:8780/"
    p = tmp_path / "c.toml"
    p.write_text('[deck]\nweb_url = "http://127.0.0.1:9999/x"\n')
    assert load_config(str(p)).web_url == "http://127.0.0.1:9999/x"
    p.write_text('[deck]\nweb_url = "http://mk20.local/"\n')
    assert load_config(str(p)).web_url == DEFAULT_WEB_URL


# ------------------------------------------------------------------ event -> open
def test_open_settings_event_opens_configured_url_once(opened):
    clk, dev, d, link = _setup("http://127.0.0.1:8780/")
    try:
        _deliver(dev, d, EVENT)
        assert wait_until(lambda: opened)
        assert opened == [(["open", "http://127.0.0.1:8780/"], opened[0][1])] and opened[0][1]["timeout"] == 5
        assert d.settings_opened == 1 and not link.device_errors
        assert link.show_jpeg(_jpeg())  # reader thread alive, link unaffected
    finally:
        link.close()


def test_open_settings_rate_limited_to_once_per_3s(opened):
    clk, dev, d, link = _setup()
    try:
        for _ in range(3):
            _deliver(dev, d, EVENT)
            clk.advance(1.0)
        assert wait_until(lambda: opened) and d.settings_opened == 1 and d.settings_dropped == 2
        clk.advance(0.1)  # 3.1 s after the first
        _deliver(dev, d, EVENT)
        assert wait_until(lambda: len(opened) == 2)
        assert [c[0] for c in opened] == [["open", DEFAULT_WEB_URL]] * 2
    finally:
        link.close()


@pytest.mark.parametrize("obj", [{"method": "ui.openSettings", "url": "http://evil.example/"},
                                 {"method": "ui.openSettings", "parameters": {}}])
def test_open_settings_with_payload_dropped(opened, obj):
    clk, dev, d, link = _setup()
    try:
        _deliver(dev, d, obj, n_events=0)
        time.sleep(0.1)
        assert opened == [] and d.settings_opened == 0 and d.settings_dropped == 1
    finally:
        link.close()


def test_non_darwin_only_logs(monkeypatch, caplog):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(sys, "platform", "linux")
    with caplog.at_level(logging.INFO, logger="mk20deck.daemon"):
        assert D.open_url(DEFAULT_WEB_URL) is False
    assert calls == [] and "not on macOS" in caplog.text


def test_open_failure_never_raises(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(a[0], 5)

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(sys, "platform", "darwin")
    assert D.open_url(DEFAULT_WEB_URL) is False


def test_unknown_device_events_ignored_safely(opened):
    clk, dev, d, link = _setup()
    try:
        for obj in ({"method": "somethingNew", "parameters": {"x": 1}}, {"method": 7}, {"foo": "bar"}):
            dev.emit_json(obj, frame_id=MockDevice.EVENT_ID)
        time.sleep(0.2)
        assert d.events.empty() and opened == []
        assert link.show_jpeg(_jpeg()) and link.connected
    finally:
        link.close()


def test_failing_event_hook_does_not_kill_reader():
    dev = MockDevice()

    def boom(obj):
        raise RuntimeError("hook bug")

    link = DeviceLink(dev, preamble_flush=0, ack_timeout=0.5, on_event=boom)
    link.start()
    try:
        link.handshake()
        dev.emit_json(EVENT, frame_id=MockDevice.EVENT_ID)
        time.sleep(0.1)
        assert link.show_jpeg(_jpeg()) and link.connected
    finally:
        link.close()


def test_supervisor_wires_event_to_configured_url(opened):
    cfg = DeckConfig()
    cfg.preamble_flush, cfg.ack_timeout_s, cfg.heartbeat_s, cfg.poll_interval_s = 0, 0.2, 0.05, 0.5
    cfg.web_url = "http://127.0.0.1:18780/"
    dev = MockDevice()
    sup = Supervisor(_ctl(FakeClock()), lambda: ["/dev/mock"], lambda p: dev, default_renderer_factory(cfg), cfg,
                     retry_s=0.05)
    stop = threading.Event()
    th = threading.Thread(target=sup.run, args=(stop,), daemon=True)
    th.start()
    try:
        assert wait_until(lambda: sup.stats.connects == 1)
        dev.emit_json(EVENT, frame_id=MockDevice.EVENT_ID)
        assert wait_until(lambda: opened)
        assert opened[0][0] == ["open", "http://127.0.0.1:18780/"]
        time.sleep(0.3)
        assert len(opened) == 1
    finally:
        stop.set()
        th.join(3)
