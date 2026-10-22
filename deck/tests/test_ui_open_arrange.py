"""fw 1.6.0 keyboard LS 会话安排 key: device event {"method":"ui.openArrange"} opens the local 会话安排 page through a
one-time link FILE minted by the in-process ArrangeServer (the token lives only in that 0600 file in the 0700 run
dir: never in an argv, a log line or on the cable).  Page off / not running / mint failure: a toast instead."""
import logging
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time

import pytest

from mk20deck import daemon as D
from mk20deck import protocol as P
from mk20deck.config import DeckConfig
from mk20deck.layout import DeviceLayout
from mk20deck.link import DeviceLink
from mk20deck.render.canvas import Renderer
from mk20deck.rules import RulesStore
from mk20deck.sim import FakeClock, _ctl
from mk20deck.supervisor import Supervisor, default_renderer_factory
from mk20deck.transport import MockDevice
from mk20deck.webui import ArrangeServer, ArrangeService, LoopCalls

from test_arrange_web import Client, link_url

EVENT = {"method": "ui.openArrange"}


def wait_until(pred, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def server(tmp_path):
    """A real ArrangeServer (ephemeral 127.0.0.1 port, auth socket + link dir in a short 0700 run dir)."""
    clk = FakeClock()
    ctl = _ctl(clk)
    sock = os.path.join(tempfile.mkdtemp(prefix="arr", dir="/tmp"), "run", "arrange.sock")
    srv = ArrangeServer(ArrangeService(ctl, LoopCalls(), RulesStore(str(tmp_path / "rules.json"))), 0,
                        poll_interval=0.05, socket_path=sock)
    assert srv.start()
    yield srv
    srv.stop()


def _setup(arrange=None, arrange_port=8781, opener=None):
    clk = FakeClock()
    dev = MockDevice(ui=True, hid_keys=True, fw160=True)
    opened = []
    d = D.Daemon(_ctl(clk), Renderer(DeviceLayout.default()), None, clock=clk, arrange=arrange,
                 arrange_port=arrange_port, opener=opener or opened.append)
    link = DeviceLink(dev, preamble_flush=0, ack_timeout=0.5, on_key=d.on_device_key, on_event=d.on_device_event)
    d.link = link
    link.start()
    link.handshake()
    return clk, dev, d, link, opened


def _deliver(dev, d, obj=EVENT, n_events=1):
    dev.emit_json(obj, frame_id=MockDevice.EVENT_ID)
    if n_events:
        assert wait_until(lambda: d.events.qsize() >= n_events)
    else:
        time.sleep(0.2)
    d.step()


# ------------------------------------------------------------------ protocol whitelist
def test_device_ui_event_whitelist_exact_payloads_only():
    assert P.DEVICE_UI_EVENTS == ("ui.openSettings", "ui.openArrange")
    assert P.device_ui_event({"method": "ui.openArrange"}) == "ui.openArrange"
    assert P.device_ui_event({"method": "ui.openSettings"}) == "ui.openSettings"
    for bad in ({"method": "ui.openArrange", "url": "http://evil/"}, {"method": "ui.openArrange", "parameters": {}},
                {"method": "ui.openArrange", "token": "x"}, {"method": "ui.page"}, {"method": "ui.hello"},
                {"method": "keyStateChanged"}, {"method": ["ui.openArrange"]}, {"method": None}, {}, "ui.openArrange",
                None, ["ui.openArrange"]):
        assert P.device_ui_event(bad) is None, bad
    # not a key event and not an ack: the link hands it to on_event
    assert P.classify_json(dict(EVENT)) == EVENT
    assert "ui.openArrange" not in P.ALLOWED_METHODS  # device -> host only: the deck never sends it


# ------------------------------------------------------------------ event -> one-time link file -> open
def test_open_arrange_mints_a_private_link_file_and_opens_only_its_path(server, caplog):
    with caplog.at_level(logging.DEBUG):
        clk, dev, d, link, opened = _setup(arrange=server)
        try:
            _deliver(dev, d)
            assert wait_until(lambda: opened)
        finally:
            link.close()
    (path,) = opened
    run_dir = os.path.dirname(server.socket_path)
    assert os.path.dirname(path) == run_dir and os.path.basename(path).startswith("open-")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600 and stat.S_IMODE(os.stat(run_dir).st_mode) == 0o700
    url = link_url(path)
    token = url.split("/auth?t=", 1)[1]
    assert url.startswith(f"http://127.0.0.1:{server.port}/auth?t=") and len(token) >= 40
    assert token not in path and token not in caplog.text and "auth?t=" not in caplog.text
    assert d.arrange_opened == 1 and d.arrange_refused == 0 and not link.device_errors
    c = Client(server)  # the browser redeems it once: session cookie, file gone
    st, h, _ = c.req("GET", url.split(f":{server.port}", 1)[1])
    assert st == 200 and c.take_cookie(h) and not os.path.exists(path)


def test_macos_open_argv_is_the_link_file_path_only(server, monkeypatch):
    argv = []
    monkeypatch.setattr(sys, "platform", "darwin")  # .webloc + `open`
    monkeypatch.setattr(D.subprocess, "run", lambda a, **k: argv.append(list(a)) or
                        subprocess.CompletedProcess(a, 0, b"", b""))
    clk, dev, d, link, _ = _setup(arrange=server, opener=D.open_url)
    try:
        _deliver(dev, d)
        assert wait_until(lambda: argv)
    finally:
        link.close()
    assert len(argv) == 1 and argv[0][0] == "open" and len(argv[0]) == 2
    path = argv[0][1]
    assert path.endswith(".webloc") and "auth" not in path and "t=" not in path
    token = link_url(path).split("t=", 1)[1]
    assert token not in " ".join(argv[0])


def test_open_arrange_rate_limited_to_once_per_3s(server):
    clk, dev, d, link, opened = _setup(arrange=server)
    try:
        for _ in range(3):
            _deliver(dev, d)
            clk.advance(1.0)
        assert wait_until(lambda: opened) and d.arrange_opened == 1 and d.arrange_dropped == 2
        clk.advance(0.1)  # 3.1 s after the first
        _deliver(dev, d)
        assert wait_until(lambda: len(opened) == 2) and opened[0] != opened[1]  # a new one-time link each time
        # its own limiter: the settings key right after it still works
        dev.emit_json(P.OPEN_SETTINGS_EVENT, frame_id=MockDevice.EVENT_ID)
        assert wait_until(lambda: d.events.qsize() >= 1)
        d.step()
        assert wait_until(lambda: len(opened) == 3) and opened[2] == d.web_url and d.settings_opened == 1
    finally:
        link.close()


@pytest.mark.parametrize("obj", [{"method": "ui.openArrange", "url": "http://evil.example/"},
                                 {"method": "ui.openArrange", "parameters": {"t": "x"}}])
def test_open_arrange_with_payload_dropped(server, obj):
    clk, dev, d, link, opened = _setup(arrange=server)
    try:
        _deliver(dev, d, obj, n_events=0)
        time.sleep(0.1)
        assert opened == [] and d.arrange_opened == 0 and d.arrange_dropped == 1
        assert not [f for f in os.listdir(os.path.dirname(server.socket_path)) if f.startswith("open-")]
    finally:
        link.close()


# ------------------------------------------------------------------ page off / not running / mint failure: a toast
@pytest.mark.parametrize("port,toast", [(0, D.ARRANGE_OFF), (8781, D.ARRANGE_DOWN)])
def test_disabled_or_not_running_shows_a_toast(port, toast):
    clk, dev, d, link, opened = _setup(arrange=None, arrange_port=port)
    try:
        _deliver(dev, d)
        time.sleep(0.1)
        assert opened == [] and d.arrange_refused == 1 and d.arrange_opened == 0
        assert d.controller.toast and d.controller.toast[0] == toast
        assert d.controller.toast[1] - clk() == pytest.approx(D.ARRANGE_TOAST_S)  # readable after 返回 to L1/L4
        clk.advance(1.0)
        _deliver(dev, d)  # rate-limited like any other
        assert d.arrange_dropped == 1 and d.arrange_refused == 1
    finally:
        link.close()


def test_mint_failure_shows_a_toast_and_logs_no_details(caplog):
    class Broken:
        def mint_file(self):
            raise OSError("/secret/path auth?t=S3CR3TS3CR3T")

    with caplog.at_level(logging.DEBUG, logger="mk20deck.daemon"):
        clk, dev, d, link, opened = _setup(arrange=Broken())
        try:
            _deliver(dev, d)
        finally:
            link.close()
    assert opened == [] and d.arrange_refused == 1 and d.controller.toast[0] == D.ARRANGE_FAILED
    assert "OSError" in caplog.text and "S3CR3T" not in caplog.text and "/secret" not in caplog.text


def test_old_firmware_never_sends_it_and_the_deck_needs_no_capability():
    """Nothing to negotiate: the keyboard initiates; firmware without the key simply never sends the event.  An older
    deck (no handler) ignores it — logged as an unknown device event, nothing opened."""
    clk, dev, d, link, opened = _setup(arrange=None)
    try:
        assert "ui.openArrange" not in (link.ui_hello_result.get("capabilities") or [])
    finally:
        link.close()


# ------------------------------------------------------------------ wiring
def test_supervisor_passes_the_arrange_server_to_each_daemon(server, monkeypatch):
    argv = []
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(D.subprocess, "run", lambda a, **k: argv.append(list(a)) or
                        subprocess.CompletedProcess(a, 0, b"", b""))
    cfg = DeckConfig()
    cfg.preamble_flush, cfg.ack_timeout_s, cfg.heartbeat_s, cfg.poll_interval_s = 0, 0.2, 0.05, 0.5
    dev = MockDevice(ui=True, hid_keys=True, fw160=True)
    sup = Supervisor(_ctl(FakeClock()), lambda: ["/dev/mock"], lambda p: dev, default_renderer_factory(cfg), cfg,
                     retry_s=0.05, arrange=server)
    stop = threading.Event()
    th = threading.Thread(target=sup.run, args=(stop,), daemon=True)
    th.start()
    try:
        assert wait_until(lambda: sup.stats.connects == 1)
        assert sup.daemon.arrange is server and sup.daemon.arrange_port == cfg.arrange_port
        dev.ui_event("ui.openArrange")
        assert wait_until(lambda: argv)
        assert argv[0][0] == "open" and argv[0][1].endswith(".webloc") and "t=" not in argv[0][1]
    finally:
        stop.set()
        th.join(3)


# ------------------------------------------------------------------ Kimi K3 1.6.0 MINOR 4: access log redaction
def test_redact_query_drops_every_query_value():
    from mk20deck.webui import redact_query
    assert redact_query('"GET /auth?t=abc_DEF-123 HTTP/1.1" 200 -') == '"GET /auth?… HTTP/1.1" 200 -'
    assert redact_query("Bad request syntax ('GET /auth?t=xyz')") == "Bad request syntax ('GET /auth?…')"
    assert redact_query('"GET /api/state HTTP/1.1" 200 -') == '"GET /api/state HTTP/1.1" 200 -'
    assert redact_query("x ?a=1&t=2 y ?q") == "x ?… y ?…"


def test_access_log_never_records_a_one_time_token(server, caplog):
    """DEBUG access lines (BaseHTTPRequestHandler.log_request / log_error) are the request line: a still-valid
    /auth?t=<token> must not reach any log level — neither redeemed, refused nor in a malformed request line."""
    import socket
    caplog.set_level(logging.DEBUG)
    tokens = [server.mint_url().split("/auth?t=", 1)[1] for _ in range(3)]
    c = Client(server)
    st, h, _ = c.req("GET", f"/auth?t={tokens[0]}")  # redeemed
    assert st == 200 and c.take_cookie(h)
    st, _, _ = c.req("GET", f"/auth?t={tokens[0]}")  # refused (used)
    assert st == 403
    st, _, _ = c.req("GET", f"/nothing?t={tokens[1]}")
    assert st in (403, 404)
    with socket.create_connection(("127.0.0.1", server.port), timeout=5) as s:  # malformed request line
        s.sendall(f"GET /auth?t={tokens[2]} HTTP/1.1 extra\r\nHost: 127.0.0.1:{server.port}\r\n\r\n".encode())
        s.settimeout(5)
        try:
            while s.recv(4096):
                pass
        except OSError:
            pass
    assert wait_until(lambda: "arrange http:" in caplog.text)
    time.sleep(0.2)
    for t in tokens:
        assert t not in caplog.text
    assert "/auth?…" in caplog.text  # the lines are there, only the query is gone
