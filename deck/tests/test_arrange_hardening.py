"""Kimi K3 security review of feature/arrange-web (2026-10-22, NO-GO) — one test group per finding:

B1 anonymous GET / handed out the token        -> one-time token over a 0600 unix socket + session cookie
B2 unbounded threads / queues, slow trickle     -> connection cap, per-request deadline, bounded LoopCalls
M1 Codex preview text as title in the API       -> Session.title_private, id prefix in the API
M2 non-current modes: preview vs old manager    -> same batch for view and change, refuse when unreadable
M3 rules save failure reported success          -> transactional store, non-2xx
M4 huge ``created`` crashed startup              -> validated numbers, per-entry quarantine
M5 old background poll thread not joined        -> bounded join + one bg poll at a time

Re-review of eb145f2 (2026-10-22):
R1 one-time URL in `open` argv (ps)             -> 0600 link file in the 0700 run dir, only its path in argv
R2 token in the open-failure log                -> logs drop everything after /auth?
R3 several tabs vs the 8-connection cap         -> HTTP/1.1 keep-alive (+ quiet page back-off on "busy")
R4 flaky foreign-uid refusal (EPIPE)            -> reply-then-half-close-and-drain, tolerant client

r3 MINOR on d82cd58:
R5 keep-alive hogs could hold all 8 slots        -> per-connection cap (age / requests -> Connection: close),
                                                    at the cap the oldest idle connection is closed first
R6 first byte vs eviction race (5e36c55 MAJOR)   -> poll for the first byte (nothing consumed), busy before
                                                    reading, eviction re-checks "no bytes waiting" under the lock
R7 503 / refusals reset on macOS (8454b90 MAJOR) -> every close half-closes and drains (bounded, off the
                                                    accept/request threads) before close: no TCP reset
"""
import json
import os
import re
import socket
import sqlite3
import stat
import tempfile
import threading
import time
from argparse import Namespace

import pytest

from mk20deck import rules as rules_mod
from mk20deck import webauth, webui
from mk20deck.adapters.mock import MockAdapter
from mk20deck.config import DeckConfig
from mk20deck.controller import Controller
from mk20deck.daemon import Daemon
from mk20deck.layout import DeviceLayout
from mk20deck.model import Mode, Session, SessionId, State, Tool
from mk20deck.render.canvas import Renderer
from mk20deck.rules import RulesSaveError, RulesStore
from mk20deck.sim import full_adapters
from mk20deck.webauth import AuthSocket, AuthStore, request_link_file, request_url
from mk20deck.webui import ArrangeServer, ArrangeService, LoopBusy, LoopCalls

from test_arrange_web import Client, link_url, mode, raw_http, tmux_sessions


def short_dir():
    return tempfile.mkdtemp(prefix="arr", dir="/tmp")  # unix socket paths must stay short


@pytest.fixture
def deck(tmp_path):
    """Real Daemon loop (fake adapters, no device), arrange server + auth socket."""
    made = []

    def make(adapters=None, bg_poll_s=0.2, heartbeat=5.0, max_connections=webui.MAX_CONNECTIONS, state=None):
        ads = adapters or full_adapters()
        if adapters is None:
            ads[Mode.TMUX].sessions = tmux_sessions()
        saved = []
        ctl = Controller(DeckConfig(), ads, sim=True, on_state_change=saved.append, state=state)
        ctl.refresh()
        calls = LoopCalls()
        store = RulesStore(str(tmp_path / f"rules{len(made)}.json"))
        svc = ArrangeService(ctl, calls, store)
        frames = []
        d = Daemon(ctl, Renderer(DeviceLayout.default(), animations=False), None, poll_interval=0.3,
                   heartbeat=heartbeat, bg_poll_s=bg_poll_s, bg_first_delay_s=0.0,
                   sink=lambda b: frames.append(time.monotonic()), calls=calls)
        stop = threading.Event()
        loop = threading.Thread(target=d.run, args=(stop,), name="deck-loop", daemon=True)
        loop.start()
        sock = os.path.join(short_dir(), "run", "arrange.sock")
        srv = ArrangeServer(svc, 0, poll_interval=0.05, socket_path=sock, max_connections=max_connections)
        assert srv.start()
        cl = Client(srv)
        cl.login()
        if bg_poll_s:
            t0 = time.time()
            while Mode.TMUX not in d.bg_sessions and time.time() - t0 < 5:
                time.sleep(0.02)
        ns = Namespace(ctl=ctl, ads=ads, svc=svc, store=store, daemon=d, srv=srv, cl=cl, saved=saved,
                       frames=frames, loop=loop, sock=sock, stop=stop)
        made.append(ns)
        return ns

    yield make
    for ns in made:
        ns.stop.set()
        ns.srv.stop()
        ns.loop.join(5)


# ====================================================================== B1: authorization
def test_anonymous_get_root_gets_no_token(deck):
    w = deck()
    n = w.srv.auth.sessions()
    st, h, b = Client(w.srv).req("GET", "/")
    page = b.decode()
    assert st == 403 and "请在 Mac 上运行" in page and "mk20deck arrange" in page
    assert "mk20-csrf" not in page and "set-cookie" not in h and w.cl.token not in page
    assert w.srv.auth.sessions() == n
    st, _, b = Client(w.srv).req("GET", "/api/state")
    assert st == 403 and json.loads(b)["code"] == "auth"


def test_one_time_token_is_single_use_and_expires(deck):
    w = deck()
    c = Client(w.srv)
    path = w.srv.mint_url().split(f":{w.srv.port}", 1)[1]
    st, h, b = c.req("GET", path)
    assert st == 200 and c.take_cookie(h)
    cookie = h["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Max-Age=2592000" in cookie
    assert 'http-equiv="refresh" content="0;url=/"' in b.decode()  # same-site navigation to the page
    assert h["referrer-policy"] == "no-referrer" and h["cache-control"] == "no-store"
    st, h2, b2 = c.req("GET", path)  # second use
    assert st == 403 and "链接已失效" in b2.decode() and "set-cookie" not in h2
    # expiry: 60 s
    path = w.srv.mint_url().split(f":{w.srv.port}", 1)[1]
    real = w.srv.auth.clock
    w.srv.auth.clock = lambda: real() + webauth.OTP_TTL_S + 1
    try:
        assert c.req("GET", path)[0] == 403
    finally:
        w.srv.auth.clock = real
    for bad in ("/auth", "/auth?t=", "/auth?t=" + "A" * 43, "/auth?t=x&t=y"):
        assert c.req("GET", bad)[0] == 403


def test_session_id_rotates_on_page_load_with_grace_and_expires(deck):
    w = deck()
    c = w.cl
    old = c.sid
    c.page()
    assert c.sid != old
    st = c.req("GET", "/api/state", headers=c.auth(Origin=None, Cookie=f"{c.cookie_name}={old}"))[0]
    assert st == 200  # in-flight requests / other tabs survive the rotation
    real = w.srv.auth.clock
    w.srv.auth.clock = lambda: real() + webauth.ROTATE_GRACE_S + 1
    try:
        st, _, b = c.req("GET", "/api/state", headers=c.auth(Origin=None, Cookie=f"{c.cookie_name}={old}"))
        assert st == 403 and json.loads(b)["code"] == "auth"
        assert c.req("GET", "/api/state", headers=c.auth(Origin=None))[0] == 200
        w.srv.auth.clock = lambda: real() + webauth.SESSION_TTL_S + 5
        assert c.req("GET", "/api/state", headers=c.auth(Origin=None))[0] == 403  # 30 days
    finally:
        w.srv.auth.clock = real


def test_auth_socket_is_private_and_mints_working_links(deck):
    w = deck()
    d = os.path.dirname(w.sock)
    assert stat.S_IMODE(os.stat(d).st_mode) == 0o700
    st_ = os.lstat(w.sock)
    assert stat.S_ISSOCK(st_.st_mode) and stat.S_IMODE(st_.st_mode) == 0o600
    url = request_url(w.sock)
    assert url.startswith(f"http://127.0.0.1:{w.srv.port}/auth?t=")
    c = Client(w.srv)
    st, h, _ = c.req("GET", url.split(f":{w.srv.port}", 1)[1])
    assert st == 200 and c.take_cookie(h)
    c.page()
    assert c.state()["ok"]
    # anything but "mint" is refused
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(w.sock)
    s.sendall(b"GET / HTTP/1.0\r\n\r\n")
    assert json.loads(s.recv(4096))["ok"] is False
    s.close()
    # a second daemon never steals a live socket
    other = AuthSocket(w.sock, lambda: "http://127.0.0.1:1/auth?t=x")
    assert other.start() is False
    assert request_url(w.sock).startswith(f"http://127.0.0.1:{w.srv.port}/")


def test_auth_socket_refuses_a_foreign_uid(monkeypatch):
    path = os.path.join(short_dir(), "run", "a.sock")
    a = AuthSocket(path, lambda: "http://127.0.0.1:1/auth?t=" + "x" * 40)
    assert a.start()
    try:
        monkeypatch.setattr(webauth, "_peer_uid", lambda conn: 4242)
        for _ in range(10):  # deterministic: the refusal line always arrives (no EPIPE / reset race)
            with pytest.raises(ConnectionError, match="uid"):
                request_url(path)
            with pytest.raises(ConnectionError, match="uid"):
                request_link_file(path)
        assert a.refused == 20 and a.served == 0
        monkeypatch.setattr(webauth, "_peer_uid", lambda conn: os.getuid())
        assert request_url(path).startswith("http://127.0.0.1:1/")
    finally:
        a.stop()
    assert not os.path.exists(path)


def test_stale_socket_file_is_replaced_and_foreign_files_are_not(tmp_path):
    d = os.path.join(short_dir(), "run")
    os.makedirs(d, mode=0o700)
    p = os.path.join(d, "a.sock")
    dead = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    dead.bind(p)
    dead.close()  # stale socket file, nobody listening
    a = AuthSocket(p, lambda: "http://127.0.0.1:1/auth?t=" + "y" * 40)
    assert a.start()
    a.stop()
    with open(p, "w") as f:
        f.write("not a socket")
    assert AuthSocket(p, lambda: "").start() is False
    assert open(p).read() == "not a socket"


def test_sessions_persist_hashed_0600_and_bad_file_is_ignored(tmp_path):
    p = str(tmp_path / "run" / "arrange-sessions.json")
    a = AuthStore(p)
    sid, s = a.create()
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(os.path.dirname(p)).st_mode) == 0o700
    assert sid not in open(p).read()  # only the hash of the cookie value is stored
    b = AuthStore(p)  # daemon restart
    assert b.lookup(sid) is not None and b.lookup(sid).csrf == s.csrf
    new = b.rotate(b.lookup(sid))
    assert AuthStore(p).lookup(new) is not None
    with open(p, "w") as f:
        f.write("{garbage")
    assert AuthStore(p).lookup(new) is None  # ignored, never a crash
    for _ in range(webauth.MAX_SESSIONS + 3):
        a.create()
    assert a.sessions() == webauth.MAX_SESSIONS


# ====================================================================== B2: bounds
def wait_slots_free(srv, n, timeout=3.0):
    t0 = time.monotonic()
    while srv.httpd._slots._value < n and time.monotonic() - t0 < timeout:  # earlier connections closing
        time.sleep(0.01)
    assert srv.httpd._slots._value == n


def test_connection_cap_answers_503_beyond_the_limit(deck):
    w = deck(max_connections=2)
    wait_slots_free(w.srv, 2)
    hogs = []
    for _ in range(2):
        s = socket.create_connection(("127.0.0.1", w.srv.port), timeout=5)
        s.sendall(b"GET / HTTP/1.1\r\n")  # never finishes the request
        hogs.append(s)
    time.sleep(0.2)
    third = raw_http(w.srv.port, f"GET /favicon.ico HTTP/1.0\r\nHost: 127.0.0.1:{w.srv.port}\r\n\r\n".encode())
    assert third.startswith(b"HTTP/1.1 503") and b'"busy"' in third
    assert w.srv.httpd.rejected >= 1
    for s in hogs:
        s.close()
    time.sleep(0.3)
    assert w.cl.state()["ok"]  # slots are released


def test_slow_trickle_is_cut_off_by_the_request_deadline(deck, monkeypatch):
    monkeypatch.setattr(webui, "REQUEST_DEADLINE_S", 0.8)
    w = deck()
    s = socket.create_connection(("127.0.0.1", w.srv.port), timeout=5)
    t0 = time.monotonic()
    s.sendall(b"GET / HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n" % w.srv.port)
    closed = None
    try:
        for _ in range(40):  # one header byte every 0.1 s: never idle long enough for the read timeout
            time.sleep(0.1)
            s.sendall(b"X")
    except OSError:
        closed = time.monotonic() - t0
    if closed is None:
        s.settimeout(3)
        try:
            closed = time.monotonic() - t0 if s.recv(100) in (b"",) else None
        except OSError:
            closed = time.monotonic() - t0
    s.close()
    assert closed is not None and closed < 3.0


def test_loopcalls_queue_is_bounded_wake_coalesced_drain_capped():
    calls = LoopCalls(max_pending=10)
    wakes = []
    calls.attach(lambda: wakes.append(1))
    done = []
    ths = [threading.Thread(target=lambda i=i: done.append(calls.submit(lambda d, i=i: i, timeout=5)), daemon=True)
           for i in range(10)]
    for t in ths:
        t.start()
    t0 = time.monotonic()
    while calls.pending() < 10 and time.monotonic() - t0 < 3:
        time.sleep(0.005)
    assert calls.pending() == 10
    with pytest.raises(LoopBusy):
        calls.submit(lambda d: None, timeout=0.1)
    assert len(wakes) == 1  # coalesced: one wake for ten jobs
    assert calls.drain(None, max_items=8) == 8
    assert len(wakes) == 2  # re-armed: the rest runs on the next loop iteration
    assert calls.drain(None) == 2
    for t in ths:
        t.join(2)
    assert sorted(done) == list(range(10))


def test_full_queue_is_503_over_http(deck, monkeypatch):
    w = deck()
    gate = threading.Event()
    blocker = threading.Thread(target=lambda: w.svc.calls.submit(lambda d: gate.wait(5), timeout=5, run_timeout=6),
                               daemon=True)
    blocker.start()  # the loop is now busy inside this job
    time.sleep(0.1)
    fillers = [threading.Thread(target=lambda: _quiet(w.svc.calls.submit, lambda d: None, timeout=5), daemon=True)
               for _ in range(webui.MAX_PENDING)]
    for t in fillers:
        t.start()
    t0 = time.monotonic()
    while w.svc.calls.pending() < webui.MAX_PENDING and time.monotonic() - t0 < 3:
        time.sleep(0.01)
    try:
        st, j = w.cl.post("/api/clear", {"mode": "codex_desktop", "slot": 0})
        assert st == 503 and j["code"] == "busy"
        assert time.monotonic() - t0 < 3  # refused at once, not after a timeout
    finally:
        gate.set()
        blocker.join(5)
        for t in fillers:
            t.join(5)


def _quiet(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except Exception:  # noqa: BLE001
        return None


def test_flood_and_slowloris_do_not_delay_keyboard_frames(deck, monkeypatch):
    monkeypatch.setattr(webui, "REQUEST_DEADLINE_S", 1.0)
    w = deck(heartbeat=0.1)  # a frame is due every 100 ms
    stop = threading.Event()
    stats = {"ok": 0, "busy": 0, "err": 0}

    def flood():
        c = Client(w.srv)
        c.token, c.sid = w.cl.token, w.cl.sid
        while not stop.is_set():
            try:
                st, _, _ = c.req("GET", "/api/state", headers=c.auth(Origin=None))
                stats["ok" if st == 200 else "busy" if st == 503 else "err"] += 1
            except OSError:
                stats["err"] += 1

    def loris():
        while not stop.is_set():
            try:
                s = socket.create_connection(("127.0.0.1", w.srv.port), timeout=2)
                s.sendall(b"POST /api/move HTTP/1.1\r\n")
                while not stop.is_set():
                    s.sendall(b"X")
                    time.sleep(0.2)
            except OSError:
                pass

    ths = [threading.Thread(target=flood, daemon=True) for _ in range(6)] + \
          [threading.Thread(target=loris, daemon=True) for _ in range(4)]
    time.sleep(0.5)
    start = time.monotonic()
    for t in ths:
        t.start()
    time.sleep(3.0)
    stop.set()
    end = time.monotonic()
    for t in ths:
        t.join(3)
    fr = [t for t in w.frames if start <= t <= end]
    gaps = [b - a for a, b in zip(fr, fr[1:])]
    assert len(fr) >= 10, fr
    assert max(gaps) < 1.0, max(gaps)  # keyboard frames keep coming during the flood
    assert stats["ok"] > 10
    assert w.srv.httpd.rejected > 0  # the cap was hit
    time.sleep(1.2)
    assert w.cl.state()["ok"]  # and everything recovers


# ====================================================================== M1: Codex preview never in the API
def test_codex_untitled_thread_keeps_keyboard_title_but_api_shows_id(codex_home):
    from mk20deck.adapters.base import RecordingRunner
    from mk20deck.adapters.codex_desktop import CodexDesktopAdapter, CodexDesktopConfig
    con = sqlite3.connect(codex_home / "state_5.sqlite")
    tid = "01a10000-0000-7000-8000-000000000001"
    con.execute("UPDATE threads SET name = NULL, title = '', preview = 'SECRET 提示词 deploy key abc' WHERE id = ?",
                (tid,))
    con.commit()
    con.close()
    ad = CodexDesktopAdapter(CodexDesktopConfig(codex_home=str(codex_home)), runner=RecordingRunner(),
                             app_alive=lambda: True)
    ss = {s.sid.native: s for s in ad.poll()}
    s = ss[tid]
    assert s.title.startswith("SECRET") and s.title_private  # keyboard unchanged (it shows the preview anyway)
    assert not any(x.title_private for k, x in ss.items() if k != tid)
    assert webui.web_title(s) == tid[:8]
    # through the API: the prompt text never appears, a title rule cannot be made from it
    ctl = Controller(DeckConfig(), {Mode.DESKTOP: MockAdapter(Mode.DESKTOP, list(ss.values())),
                                    Mode.HERDR: MockAdapter(Mode.HERDR, unavailable="herdr 未安装"),
                                    Mode.TMUX: MockAdapter(Mode.TMUX, [])}, sim=True)
    ctl.refresh()
    svc = ArrangeService(ctl, LoopCalls(), RulesStore(str(codex_home / "r.json")))
    snap = json.dumps(svc.snapshot(None), ensure_ascii=False)
    assert "SECRET" not in snap and "提示词" not in snap and tid[:8] in snap
    with pytest.raises(ValueError, match="没有标题"):
        svc.api_rule_put({"mode": "codex_desktop", "slot": 0, "kind": "title", "session": s.sid.key()})
    assert "SECRET" not in json.dumps(svc.api_rule_put({"mode": "codex_desktop", "slot": 0, "kind": "tool",
                                                         "session": s.sid.key()}), ensure_ascii=False)


# ====================================================================== M2: non-current modes
def test_non_current_change_hits_the_session_the_page_showed(deck):
    sess = tmux_sessions()
    gone = Session(SessionId(Mode.TMUX, "%9", "4242-1"), Tool.CCAI, "gone")
    # persisted tmux arrangement from an earlier run: S1 = pane3, S2 = a pane that no longer exists (pinned)
    state = {"mode": "codex_desktop", "modes": {"tmux": {"n_slots": 15, "slots": [
        {"key": sess[2].sid.key(), "pinned": False}, {"key": gone.sid.key(), "pinned": True}] + [None] * 13}}}
    ads = full_adapters()
    ads[Mode.TMUX].sessions = sess
    w = deck(adapters=ads, state=state)
    tm = mode(w.cl.state(), "tmux")
    shown = {s["slot"]: s for s in tm["slots"]}
    assert shown[0]["key"] == sess[2].sid.key() and shown[1]["kind"] == "lost"
    target = shown[2]  # filled by the preview from the background batch; empty in the stale manager
    assert target["kind"] == "session"
    real = w.ctl.managers[Mode.TMUX]
    assert real.slots[2] is None  # the old manager did not have it
    st, j = w.cl.post("/api/move", {"mode": "tmux", "slot": 2, "key": target["key"], "expect": target["key"]})
    assert st == 200 and j["message"] == "已钉在 S3"  # pinned in place: the same session the page showed
    assert real.slots[2].key == target["key"] and real.slots[2].pinned
    assert w.saved[-1]["modes"]["tmux"]["slots"][2] == {"key": target["key"], "pinned": True}
    after = {s["slot"]: s for s in mode(j["state"], "tmux")["slots"]}
    assert after[2]["key"] == target["key"] and after[0]["key"] == sess[2].sid.key()


def test_stale_page_is_refused_with_409(deck):
    w = deck()
    tm = mode(w.cl.state(), "tmux")
    s0 = tm["slots"][0]
    other = tm["slots"][1]["key"]
    before = w.ctl.managers[Mode.TMUX].to_dict()
    st, j = w.cl.post("/api/clear", {"mode": "tmux", "slot": 0, "expect": other})
    assert st == 409 and j["code"] == "conflict" and "S1" in j["error"]
    assert w.ctl.managers[Mode.TMUX].slots[0].key == s0["key"]
    st, j = w.cl.post("/api/unpin", {"mode": "codex_desktop", "slot": 3, "expect": "codex_desktop::nope"})
    assert st == 409
    assert before["slots"][0] is None  # the old (never read) manager; the page showed the bg batch


def test_unreadable_mode_refuses_changes(deck):
    w = deck()
    st, j = w.cl.post("/api/clear", {"mode": "herdr", "slot": 0})
    assert st == 409 and "herdr 未安装" in j["error"] and "暂不能修改" in j["error"]
    st, j = w.cl.post("/api/move", {"mode": "herdr", "slot": 0, "key": "herdr::x"})
    assert st == 409
    # current mode with a failed poll: refused, not applied to stale data
    w.ads[Mode.DESKTOP].unavailable = "Codex 读取失败"
    w.svc.calls.submit(lambda d: w.ctl.refresh())
    key = w.ctl.mgr.slots[4].key
    st, j = w.cl.post("/api/move", {"mode": "codex_desktop", "slot": 0, "key": key})
    assert st == 409 and "Codex 读取失败" in j["error"]


def test_mode_without_any_data_yet_refuses(deck):
    w = deck(bg_poll_s=0)  # no background polls: tmux was never read
    tm = mode(w.cl.state(), "tmux")
    assert tm["source"] == "none"
    st, j = w.cl.post("/api/clear", {"mode": "tmux", "slot": 0})
    assert st == 409 and "还没有读到会话" in j["error"]


# ====================================================================== M3: rules save is transactional
def test_rules_save_failure_changes_nothing(tmp_path, monkeypatch):
    p = tmp_path / "slot-rules.json"
    st = RulesStore(str(p))
    r = st.put(Mode.TMUX, 1, "tool", "ccai")
    disk = p.read_text()

    def boom(*a, **kw):
        raise PermissionError(13, "denied")

    monkeypatch.setattr(rules_mod.os, "replace", boom)
    with pytest.raises(RulesSaveError, match="未做修改"):
        st.put(Mode.TMUX, 2, "tool", "kimi")
    with pytest.raises(RulesSaveError):
        st.update(r.id, enabled=False)
    with pytest.raises(RulesSaveError):
        st.delete(r.id)
    assert st.rules == [r] and p.read_text() == disk
    assert [x.name for x in tmp_path.iterdir()] == ["slot-rules.json"]  # no temp files left
    # the keyboard path (a manual change already happened) still disables in memory and says so
    st.disable(r.id, "键盘把 S1 移到此键")
    assert not st.get(r.id).enabled and st.dirty and "本次运行" in st.warning
    monkeypatch.undo()
    st.put(Mode.TMUX, 3, "tool", "codex")  # the next successful save writes everything
    assert not RulesStore(str(p)).get(r.id).enabled and not st.dirty


def test_api_reports_save_failure_and_the_deck_is_unchanged(deck, monkeypatch):
    w = deck()

    def boom(*a, **kw):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(rules_mod.os, "replace", boom)
    st, j = w.cl.post("/api/rules", {"mode": "codex_desktop", "slot": 4, "kind": "title", "value": "不存在"})
    assert st == 500 and j["code"] == "save" and "规则保存失败" in j["error"]
    assert w.store.rules == [] and w.ctl.mgr.rules == () and w.ctl.mgr.waiting_label(4) == ""


# ====================================================================== M4: never crash on numbers
@pytest.mark.parametrize("created", ["1e400", "-1e400", "NaN", "Infinity", "1" + "0" * 400, "-5", "1e12",
                                     "true", '"x"', "[]"])
def test_bad_created_is_quarantined_and_the_rest_loads(tmp_path, created):
    p = tmp_path / "slot-rules.json"
    good = '{"id": "r1", "mode": "tmux", "slot": 1, "kind": "tool", "value": "ccai", "created": 5}'
    bad = f'{{"id": "r2", "mode": "tmux", "slot": 2, "kind": "tool", "value": "kimi", "created": {created}}}'
    p.write_text(f'{{"version": 1, "rules": [{good}, {bad}]}}')
    st = RulesStore(str(p))
    assert [r.id for r in st.rules] == ["r1"] and "1 条无效规则" in st.warning
    st.put(Mode.TMUX, 3, "tool", "codex")  # the quarantined entry survives in the backup
    bak = [x for x in tmp_path.iterdir() if x.name.startswith("slot-rules.json.bad-")]
    assert len(bak) == 1 and '"r2"' in bak[0].read_text()


def test_hostile_rules_files_never_stop_startup(tmp_path):
    from mk20deck.cli import build_arrange
    for i, content in enumerate(["[" * 100000, '{"version": 1, "rules": [' + "{}," * 5000 + "{}]}",
                                 '{"version": 1e999, "rules": []}', '{"version": 1, "rules": [{"slot": 1e999}]}']):
        p = tmp_path / f"r{i}.json"
        p.write_text(content)
        ctl = Controller(DeckConfig(), full_adapters(), sim=True)
        calls, svc = build_arrange(DeckConfig(), ctl, str(p))
        assert svc.store.rules == [] and svc.store.warning


# ====================================================================== M5: background poll thread
class SlowAdapter(MockAdapter):
    def __init__(self, mode, delay=0.3):
        super().__init__(mode, [])
        self.delay = delay
        self.threads = []
        self.active = 0
        self.max_active = 0
        self._l = threading.Lock()

    def poll(self, keep_ids=()):
        with self._l:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.threads.append(threading.current_thread())
        time.sleep(self.delay)
        with self._l:
            self.active -= 1
        return []


def test_run_joins_its_background_thread_before_returning():
    ads = full_adapters()
    bg = {Mode.HERDR: SlowAdapter(Mode.HERDR), Mode.TMUX: SlowAdapter(Mode.TMUX)}
    ctl = Controller(DeckConfig(), ads, sim=True)
    d = Daemon(ctl, Renderer(DeviceLayout.default(), animations=False), None, poll_interval=1, heartbeat=5,
               bg_poll_s=0.05, bg_first_delay_s=0.0, bg_adapters=bg)
    stop = threading.Event()
    th = threading.Thread(target=d.run, args=(stop,), daemon=True)
    th.start()
    t0 = time.monotonic()
    while not bg[Mode.HERDR].threads and time.monotonic() - t0 < 3:
        time.sleep(0.01)
    stop.set()  # e.g. a disconnect: run() returns ...
    th.join(5)
    assert not th.is_alive()
    worker = bg[Mode.HERDR].threads[0]
    assert not worker.is_alive()  # ... only after its poll thread is gone


def test_background_polls_never_overlap_across_daemon_generations():
    ads = full_adapters()
    shared = {Mode.HERDR: SlowAdapter(Mode.HERDR, 0.1), Mode.TMUX: SlowAdapter(Mode.TMUX, 0.1)}
    ds = [Daemon(Controller(DeckConfig(), ads, sim=True), Renderer(DeviceLayout.default(), animations=False), None,
                 bg_adapters=shared) for _ in range(3)]
    ths = [threading.Thread(target=d.poll_background_once) for d in ds for _ in range(2)]
    for t in ths:
        t.start()
    for t in ths:
        t.join(5)
    assert shared[Mode.TMUX].max_active == 1 and shared[Mode.HERDR].max_active == 1
    assert len(shared[Mode.TMUX].threads) == 6


# ====================================================================== R1: no token in any argv
def _cfg(tmp_path, port):
    p = tmp_path / "c.toml"
    p.write_text(f"[web]\narrange_port = {port}\n")
    return str(p)


def test_open_gets_a_private_link_file_path_never_the_token(deck, tmp_path, capsys):
    from mk20deck.cli import cmd_arrange
    w = deck()
    opened = []
    assert cmd_arrange(Namespace(config=_cfg(tmp_path, w.srv.port), print_only=False, socket=w.sock),
                       opener=opened.append) == 0
    (path,) = opened
    run_dir = os.path.dirname(w.sock)
    assert os.path.dirname(path) == run_dir and os.path.basename(path).startswith("open-")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600 and stat.S_IMODE(os.stat(run_dir).st_mode) == 0o700
    url = link_url(path)
    token = url.split("/auth?t=", 1)[1]
    assert len(token) >= 40 and token not in path and token not in capsys.readouterr().out
    c = Client(w.srv)
    st, h, _ = c.req("GET", url.split(f":{w.srv.port}", 1)[1])
    assert st == 200 and c.take_cookie(h)
    assert not os.path.exists(path)  # redeemed -> deleted


def test_macos_open_argv_is_only_the_webloc_path(deck, tmp_path, monkeypatch):
    import plistlib
    import subprocess
    import sys
    from mk20deck import daemon as D
    from mk20deck.cli import cmd_arrange
    w = deck()
    argv = []
    monkeypatch.setattr(sys, "platform", "darwin")  # also makes the daemon write a .webloc
    monkeypatch.setattr(D.subprocess, "run", lambda a, **k: argv.append(list(a)) or
                        subprocess.CompletedProcess(a, 0, b"", b""))
    assert cmd_arrange(Namespace(config=_cfg(tmp_path, w.srv.port), print_only=False, socket=w.sock)) == 0
    assert len(argv) == 1 and argv[0][0] == "open" and len(argv[0]) == 2
    path = argv[0][1]
    assert path.endswith(".webloc") and "auth" not in path and "t=" not in path
    url = plistlib.loads(open(path, "rb").read())["URL"]
    assert url.startswith(f"http://127.0.0.1:{w.srv.port}/auth?t=") and url.split("t=")[1] not in " ".join(argv[0])


def test_link_files_are_deleted_on_expiry_stop_and_start_up(deck, tmp_path):
    w = deck()
    path, page = request_link_file(w.sock)
    assert page == w.srv.url and os.path.exists(path)
    real = w.srv.auth.clock
    w.srv.auth.clock = lambda: real() + webauth.OTP_TTL_S + 1
    try:
        t0 = time.monotonic()
        while os.path.exists(path) and time.monotonic() - t0 < 3:  # the socket thread prunes ~5x per second
            time.sleep(0.05)
        assert not os.path.exists(path)
    finally:
        w.srv.auth.clock = real
    # an outstanding file goes away when the server stops
    p2, _ = request_link_file(w.sock)
    w.srv.stop()
    assert not os.path.exists(p2)
    # stale files of an earlier run are removed at start-up (not other files)
    run_dir = os.path.dirname(w.sock)
    for n in ("open-old.webloc", "open-x.html", "keep.txt"):
        open(os.path.join(run_dir, n), "w").write("x")
    srv = ArrangeServer(w.svc, 0, socket_path=w.sock)
    assert srv.start()
    try:
        assert sorted(os.listdir(run_dir)) == ["arrange.sock", "keep.txt"]
    finally:
        srv.stop()


def test_redeeming_a_stale_token_also_removes_its_file(tmp_path):
    d = os.path.join(short_dir(), "run")
    a = AuthStore(link_dir=d)
    path = a.mint_file(lambda t: "http://127.0.0.1:1/auth?t=" + t, kind="html")
    tok = link_url(path).split("t=")[1]
    real = a.clock
    a.clock = lambda: real() + webauth.OTP_TTL_S + 1
    assert a.redeem(tok) is False and not os.path.exists(path)
    w = a.mint_file(lambda t: "http://127.0.0.1:1/auth?t=" + t, kind="webloc")
    import plistlib
    assert plistlib.loads(open(w, "rb").read())["URL"].startswith("http://127.0.0.1:1/auth?t=")
    for _ in range(webauth.MAX_OTP + 4):  # dropped tokens (MAX_OTP churn) never leave files behind
        a.mint_file(lambda t: "http://127.0.0.1:1/auth?t=" + t, kind="html")
    assert len(os.listdir(d)) <= webauth.MAX_OTP


# ====================================================================== R2: never log the token
def test_open_failure_logs_never_show_the_token(monkeypatch, caplog):
    import logging
    import subprocess
    import sys
    from mk20deck import daemon as D
    url = "http://127.0.0.1:8781/auth?t=" + "S3CR3T" * 8

    def boom(a, **k):
        raise subprocess.TimeoutExpired(a, 5)  # its message would contain the argv

    with caplog.at_level(logging.INFO, logger="mk20deck.daemon"):
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(D.subprocess, "run", boom)
        assert D.open_url(url) is False
        monkeypatch.setattr(D.subprocess, "run", lambda a, **k: subprocess.CompletedProcess(a, 1, b"", b""))
        assert D.open_url(url) is False
        monkeypatch.setattr(sys, "platform", "linux")
        assert D.open_url(url) is False
    assert "S3CR3T" not in caplog.text and caplog.text.count("127.0.0.1:8781/auth?…") == 3


# ====================================================================== R3: keep-alive, several tabs
def test_several_tabs_polling_reuse_connections_and_never_hit_the_cap(deck):
    import http.client
    w = deck()
    before = w.srv.httpd.accepted
    statuses = []
    stop = threading.Event()

    def tab(post=False):
        c = http.client.HTTPConnection("127.0.0.1", w.srv.port, timeout=5)
        h = w.cl.auth(Origin=None)
        h["Host"] = w.cl.host
        n = 0
        while not stop.is_set():
            if post and n == 3:
                hp = dict(w.cl.auth(), Host=w.cl.host)
                c.request("POST", "/api/clear", body=b'{"mode": "codex_desktop", "slot": 14}', headers=hp)
            else:
                c.request("GET", "/api/state", headers=h)
            r = c.getresponse()
            r.read()
            statuses.append(r.status)
            n += 1
            time.sleep(0.25)
        c.close()

    ths = [threading.Thread(target=tab, args=(i == 0,), daemon=True) for i in range(6)]
    for t in ths:
        t.start()
    time.sleep(2.5)
    stop.set()
    for t in ths:
        t.join(5)
    assert len(statuses) >= 6 * 6 and set(statuses) == {200}, set(statuses)
    assert w.srv.httpd.rejected == 0
    assert w.srv.httpd.accepted - before <= 6 + 2  # one connection per tab, reused


def test_idle_keep_alive_connection_is_closed(deck, monkeypatch):
    monkeypatch.setattr(webui, "READ_TIMEOUT_S", 0.5)
    w = deck()
    s = socket.create_connection(("127.0.0.1", w.srv.port), timeout=5)
    s.sendall(f"GET /favicon.ico HTTP/1.1\r\nHost: 127.0.0.1:{w.srv.port}\r\n\r\n".encode())
    first = s.recv(4096)
    assert first.startswith(b"HTTP/1.1 204") and b"Connection: close" not in first  # kept alive
    t0 = time.monotonic()
    assert s.recv(4096) == b""  # the server closes the idle connection ...
    assert time.monotonic() - t0 < 2.0  # ... after READ_TIMEOUT_S
    s.close()


def test_a_change_that_may_have_happened_is_not_reported_as_retryable(deck, monkeypatch):
    w = deck()
    monkeypatch.setattr(webui, "JOB_TIMEOUT_S", 0.2)
    monkeypatch.setattr(webui, "JOB_RUN_S", 0.2)
    orig = w.store.put

    def slow_put(*a, **k):
        time.sleep(0.8)
        return orig(*a, **k)

    w.store.put = slow_put
    st, j = w.cl.post("/api/rules", {"mode": "codex_desktop", "slot": 4, "kind": "title", "value": "慢"})
    assert st == 503 and j["code"] == "maybe"  # the page retries only "busy" (refused before running)
    t0 = time.monotonic()
    while not w.store.rules and time.monotonic() - t0 < 3:
        time.sleep(0.05)
    assert w.store.rules and w.store.rules[0].value == "慢"  # it did happen


def test_page_retries_quietly_only_on_busy(deck):
    page = Client(deck().srv)
    page.login()
    js = page.page()[0].decode()
    assert "e.code !== 'busy'" in js and "fails < 3" in js  # quiet back-off; "maybe" is never retried


# ====================================================================== R4: refusal is always delivered
def test_client_maps_an_abrupt_refusal_to_a_refusal():
    """A server that answers and closes without reading (the old behaviour): the client still reports a
    refusal (the reply, or "closed without a reply"), never a raw BrokenPipe."""
    path = os.path.join(short_dir(), "abrupt.sock")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(8)

    def serve():
        for _ in range(10):
            c, _ = srv.accept()
            c.sendall(b'{"ok": false, "error": "uid"}\n')
            c.close()

    th = threading.Thread(target=serve, daemon=True)
    th.start()
    for _ in range(10):
        with pytest.raises(ConnectionError, match="daemon refused"):
            request_url(path)
    th.join(5)
    srv.close()


# ====================================================================== R5: per-connection cap
def _read_head(s):
    """One response on a keep-alive socket: returns the header block (body consumed), b"" on EOF."""
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = s.recv(4096)
        if not chunk:
            return b""
        buf += chunk
    head, _, body = buf.partition(b"\r\n\r\n")
    n = int(re.search(rb"Content-Length: (\d+)", head).group(1))
    while len(body) < n:
        body += s.recv(4096)
    return head


def _ka_get(s, port, path="/favicon.ico"):
    s.sendall(f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode())
    return _read_head(s)


def test_keep_alive_connection_is_recycled_after_max_requests(deck, monkeypatch):
    monkeypatch.setattr(webui, "MAX_CONN_REQUESTS", 5)
    w = deck()
    s = socket.create_connection(("127.0.0.1", w.srv.port), timeout=5)
    heads = [_ka_get(s, w.srv.port) for _ in range(5)]
    assert all(h.startswith(b"HTTP/1.1 204") for h in heads)
    assert [b"Connection: close" in h for h in heads] == [False] * 4 + [True]
    assert s.recv(16) == b""  # the server closed it after the 5th response
    s.close()


def test_keep_alive_connection_is_recycled_after_max_age(deck, monkeypatch):
    monkeypatch.setattr(webui, "MAX_CONN_AGE_S", 0.6)
    w = deck()
    s = socket.create_connection(("127.0.0.1", w.srv.port), timeout=5)
    assert b"Connection: close" not in _ka_get(s, w.srv.port)
    time.sleep(0.7)
    assert b"Connection: close" in _ka_get(s, w.srv.port)  # still answered, then closed
    assert s.recv(16) == b""
    s.close()


def test_the_page_keeps_working_across_the_recycling(deck, monkeypatch):
    import http.client
    monkeypatch.setattr(webui, "MAX_CONN_REQUESTS", 3)
    w = deck()
    before = w.srv.httpd.accepted
    c = http.client.HTTPConnection("127.0.0.1", w.srv.port, timeout=5)  # like the page's fetch: reconnects
    statuses = []
    for i in range(10):
        if i == 4:
            c.request("POST", "/api/clear", body=b'{"mode": "codex_desktop", "slot": 14}',
                      headers=dict(w.cl.auth(), Host=w.cl.host))
        else:
            c.request("GET", "/api/state", headers=dict(w.cl.auth(Origin=None), Host=w.cl.host))
        r = c.getresponse()
        r.read()
        statuses.append(r.status)
    c.close()
    assert statuses == [200] * 10
    assert w.srv.httpd.accepted - before >= 4  # reconnected every 3 requests


def _hog(port, stop, gap, out):
    """A keep-alive client that never lets go of its connection (until the server closes it)."""
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=5)
        out.append(s)
        while not stop.is_set():
            head = _ka_get(s, port)
            if not head or b"Connection: close" in head:
                return
            if gap:
                time.sleep(gap)
    except OSError:
        return


def test_eight_idle_hogs_cannot_starve_a_new_client(deck):
    w = deck()
    wait_slots_free(w.srv, webui.MAX_CONNECTIONS)
    stop = threading.Event()
    socks = []
    ths = [threading.Thread(target=_hog, args=(w.srv.port, stop, 0.5, socks), daemon=True)
           for _ in range(webui.MAX_CONNECTIONS)]
    for t in ths:
        t.start()
    t0 = time.monotonic()
    while w.srv.httpd._slots._value > 0 and time.monotonic() - t0 < 3:
        time.sleep(0.01)
    assert w.srv.httpd._slots._value == 0  # all 8 slots held by keep-alive hogs
    time.sleep(0.3)
    rejected = w.srv.httpd.rejected
    t1 = time.monotonic()
    snap = w.cl.state()  # a new client: the oldest idle hog is closed to make room
    assert snap["ok"] and time.monotonic() - t1 < 1.5
    assert w.srv.httpd.evicted >= 1 and w.srv.httpd.rejected == rejected
    stop.set()
    for t in ths:
        t.join(5)
    for s in socks:
        s.close()


def test_busy_hogs_hold_slots_only_until_the_connection_age_cap(deck, monkeypatch):
    monkeypatch.setattr(webui, "MAX_CONN_AGE_S", 1.0)
    w = deck()
    wait_slots_free(w.srv, webui.MAX_CONNECTIONS)
    stop = threading.Event()
    socks = []
    ths = [threading.Thread(target=_hog, args=(w.srv.port, stop, 0, socks), daemon=True)  # never idle
           for _ in range(webui.MAX_CONNECTIONS)]
    for t in ths:
        t.start()
    t0 = time.monotonic()
    while w.srv.httpd._slots._value > 0 and time.monotonic() - t0 < 3:
        time.sleep(0.01)
    start = time.monotonic()
    got = None
    while time.monotonic() - start < 4:
        c = Client(w.srv)
        c.token, c.sid = w.cl.token, w.cl.sid
        try:
            st, _, _ = c.req("GET", "/api/state", headers=c.auth(Origin=None))
        except OSError:
            st = None
        if st == 200:
            got = time.monotonic() - start
            break
        time.sleep(0.05)
    stop.set()
    for t in ths:
        t.join(5)
    for s in socks:
        s.close()
    assert got is not None and got < webui.MAX_CONN_AGE_S + 1.0  # starved at most until the cap


# ====================================================================== R6: first byte vs eviction
def _read_head_keep(s):
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = s.recv(4096)
        if not chunk:
            return b"", b""
        buf += chunk
    head, _, body = buf.partition(b"\r\n\r\n")
    n = int(re.search(rb"Content-Length: (\d+)", head).group(1))
    while len(body) < n:
        body += s.recv(4096)
    return head, body


def _post_bytes(w, path, body):
    raw = json.dumps(body).encode()
    h = dict(w.cl.auth(), Host=w.cl.host, **{"Content-Length": str(len(raw))})
    return (f"POST {path} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in h.items()) + "\r\n").encode() + raw


def _race_setup(deck, monkeypatch):
    """Two slots: A = idle keep-alive connection (one request served), B = holds the other slot mid-request.
    The hook parks A in the window "bytes arrived, not yet busy" until ``gate`` is set."""
    w = deck(max_connections=2)
    wait_slots_free(w.srv, 2)
    port = w.srv.port
    a = socket.create_connection(("127.0.0.1", port), timeout=5)
    assert _ka_get(a, port).startswith(b"HTTP/1.1 204")
    b = socket.create_connection(("127.0.0.1", port), timeout=5)
    b.sendall(b"GET /favicon.ico HTTP/1.1\r\n")  # busy: never an eviction candidate
    t0 = time.monotonic()
    while w.srv.httpd._slots._value > 0 and time.monotonic() - t0 < 3:
        time.sleep(0.01)
    assert w.srv.httpd._slots._value == 0
    time.sleep(webui.EVICT_MIN_IDLE_S + 0.15)  # A is idle long enough to be a candidate
    gate, entered = threading.Event(), threading.Event()

    def hook(h):
        if h._requests >= 1 and not entered.is_set():  # A's next request
            entered.set()
            gate.wait(5)

    monkeypatch.setattr(webui, "_READY_HOOK", hook)
    return w, a, b, gate, entered


def _new_client_at_the_cap(w):
    return raw_http(w.srv.port, f"GET /favicon.ico HTTP/1.0\r\nHost: 127.0.0.1:{w.srv.port}\r\n\r\n".encode())


def test_an_arrived_post_is_executed_and_answered_not_evicted(deck, monkeypatch):
    w, a, b, gate, entered = _race_setup(deck, monkeypatch)
    before = w.ctl.mgr.slots[14].key
    assert before is not None
    a.sendall(_post_bytes(w, "/api/clear", {"mode": "codex_desktop", "slot": 14, "expect": before}))
    assert entered.wait(3)  # A: readable, still marked idle (the window of the old race)
    third = _new_client_at_the_cap(w)
    assert third.startswith(b"HTTP/1.1 503")  # nothing evictable: A has bytes waiting, B is busy
    assert w.srv.httpd.evicted == 0
    gate.set()
    head, body = _read_head_keep(a)
    assert head.startswith(b"HTTP/1.1 200"), head
    assert json.loads(body)["message"] == "已清空"  # the POST was executed
    a.close()
    b.close()


def test_a_partial_request_is_not_evicted_either(deck, monkeypatch):
    w, a, b, gate, entered = _race_setup(deck, monkeypatch)
    full = f"GET /favicon.ico HTTP/1.1\r\nHost: 127.0.0.1:{w.srv.port}\r\n\r\n".encode()
    a.sendall(full[:9])  # "GET /favi"
    assert entered.wait(3)
    assert _new_client_at_the_cap(w).startswith(b"HTTP/1.1 503")
    assert w.srv.httpd.evicted == 0
    gate.set()
    time.sleep(0.1)
    a.sendall(full[9:])
    head, _ = _read_head_keep(a)
    assert head.startswith(b"HTTP/1.1 204"), head  # answered, not cut off
    a.close()
    b.close()


def test_a_truly_idle_connection_is_still_evicted(deck, monkeypatch):
    w, a, b, gate, entered = _race_setup(deck, monkeypatch)
    gate.set()  # the hook must not hold the evicted handler (it wakes on the EOF of its shutdown)
    third = _new_client_at_the_cap(w)  # A has nothing waiting: it is closed to make room
    assert third.startswith(b"HTTP/1.1 204"), third[:40]
    assert w.srv.httpd.evicted == 1
    a.settimeout(2)
    assert a.recv(16) == b""
    gate.set()
    a.close()
    b.close()


def test_pipelined_requests_are_answered_without_waiting(deck, monkeypatch):
    """Requests already in the read buffer (pipelining) are served at once, not after the idle limit."""
    monkeypatch.setattr(webui, "READ_TIMEOUT_S", 3.0)
    w = deck()
    s = socket.create_connection(("127.0.0.1", w.srv.port), timeout=5)
    one = f"GET /favicon.ico HTTP/1.1\r\nHost: 127.0.0.1:{w.srv.port}\r\n\r\n".encode()
    t0 = time.monotonic()
    s.sendall(one * 3)
    data = b""
    while data.count(b"\r\n\r\n") < 3 and time.monotonic() - t0 < 2.5:  # 204s: header blocks only
        chunk = s.recv(4096)
        if not chunk:
            break
        data += chunk
    assert data.count(b"HTTP/1.1 204") == 3, data[:200]
    assert time.monotonic() - t0 < 1.0
    s.close()


# ====================================================================== R7: refusals are delivered, never reset
def _read_all(s, limit=3.0):
    """Everything until EOF; a TCP reset fails the test (that is the macOS bug this guards)."""
    s.settimeout(limit)
    out = b""
    while True:
        chunk = s.recv(65536)  # ConnectionResetError propagates
        if not chunk:
            return out
        out += chunk


def _refusal_cases(w):
    p = w.srv.port
    host = f"Host: 127.0.0.1:{p}\r\n"
    o = f"Origin: http://127.0.0.1:{p}\r\n"
    auth = (f"{host}{o}X-MK20-CSRF: {w.cl.token}\r\nCookie: {w.cl.cookie_name}={w.cl.sid}\r\n"
            "Content-Type: application/json\r\n")
    big = b"x" * 9000
    return {
        "405": (f"DELETE /api/rules/r1 HTTP/1.1\r\n{host}Content-Length: 5\r\n\r\nhello".encode(), b"405"),
        "403-auth": (f"POST /api/clear HTTP/1.1\r\n{host}{o}Content-Type: application/json\r\n"
                     f"Content-Length: 2\r\n\r\n{{}}".encode(), b"403"),
        "403-host": (b"GET / HTTP/1.1\r\nHost: evil:1\r\n\r\n", b"403"),
        "404": (f"GET /nope HTTP/1.1\r\n{host}\r\n".encode(), b"404"),
        "413": (f"POST /api/clear HTTP/1.1\r\n{auth}Content-Length: {len(big)}\r\n\r\n".encode() + big, b"413"),
        "400-json": (f"POST /api/clear HTTP/1.1\r\n{auth}Content-Length: 5\r\n\r\n{{nope".encode(), b"400"),
        "414": (f"GET /{'a' * 600} HTTP/1.1\r\n{host}\r\n".encode(), b"414"),
        "400-line": (b"GARBAGE\r\n\r\n", b"400"),
    }


@pytest.mark.parametrize("case", ["405", "403-auth", "403-host", "404", "413", "400-json", "414", "400-line"])
def test_refusals_reach_the_client_even_with_unread_bytes(deck, case):
    w = deck()
    req, status = _refusal_cases(w)[case]
    s = socket.create_connection(("127.0.0.1", w.srv.port), timeout=5)
    # 40 KiB the server never reads (more than its 8 KiB read buffer, less than the 64 KiB drain): a plain
    # close with these unread bytes resets the connection on macOS and the client loses the response
    s.sendall(req + b"T" * 40000)
    out = _read_all(s)
    s.close()
    if case == "400-line":  # http.server answers an unparsable request line HTTP/0.9-style (body only)
        assert b"Error code: 400" in out, out[:120]
    else:
        assert out.split(b"\r\n", 1)[0].split(b" ")[1] == status, out[:120]


def test_503_at_the_cap_reaches_a_client_that_sent_a_whole_post(deck):
    w = deck(max_connections=2)
    wait_slots_free(w.srv, 2)
    hogs = []
    for _ in range(2):
        h = socket.create_connection(("127.0.0.1", w.srv.port), timeout=5)
        h.sendall(b"GET / HTTP/1.1\r\n")  # busy, not evictable
        hogs.append(h)
    t0 = time.monotonic()
    while w.srv.httpd._slots._value > 0 and time.monotonic() - t0 < 3:
        time.sleep(0.01)
    for _ in range(5):
        s = socket.create_connection(("127.0.0.1", w.srv.port), timeout=5)
        s.sendall(_post_bytes(w, "/api/clear", {"mode": "codex_desktop", "slot": 1, "pad": "y" * 6000}))
        out = _read_all(s)
        s.close()
        assert out.startswith(b"HTTP/1.1 503") and b'"code": "busy"' in out, out[:120]
    for h in hogs:
        h.close()


def test_lingering_is_bounded_and_never_blocks_the_accept_loop(deck):
    w = deck(max_connections=2)
    wait_slots_free(w.srv, 2)
    hogs = []
    for _ in range(2):
        h = socket.create_connection(("127.0.0.1", w.srv.port), timeout=5)
        h.sendall(b"GET / HTTP/1.1\r\n")
        hogs.append(h)
    t0 = time.monotonic()
    while w.srv.httpd._slots._value > 0 and time.monotonic() - t0 < 3:
        time.sleep(0.01)
    stop = threading.Event()
    keep = []

    def chatty():  # a refused client that keeps sending and never closes
        try:
            s = socket.create_connection(("127.0.0.1", w.srv.port), timeout=5)
            keep.append(s)
            while not stop.is_set():
                s.sendall(b"z" * 4096)
                time.sleep(0.01)
        except OSError:
            pass

    talkers = [threading.Thread(target=chatty, daemon=True) for _ in range(4)]
    for t in talkers:
        t.start()
    t1 = time.monotonic()
    silent = [socket.create_connection(("127.0.0.1", w.srv.port), timeout=5) for _ in range(webui.MAX_LINGER + 20)]
    accept_time = time.monotonic() - t1
    time.sleep(0.2)
    assert w.srv.httpd.lingerer.pending() <= webui.MAX_LINGER  # bounded (the rest is closed at once)
    assert accept_time < 3.0  # the accept loop never waited on a lingering client
    t2 = time.monotonic()
    while w.srv.httpd.lingerer.pending() and time.monotonic() - t2 < 3:
        time.sleep(0.05)
    assert w.srv.httpd.lingerer.pending() == 0  # every lingering socket closed within LINGER_S (+ poll)
    assert time.monotonic() - t2 < webui.LINGER_S + 1.0
    stop.set()
    for t in talkers:
        t.join(3)
    for s in silent + keep + hogs:
        s.close()
    time.sleep(0.3)
    assert w.cl.state()["ok"]


# ---------------------------------------------------------------- R8: lingerer robustness (Kimi r3 MINORs)
def _linger_pair():
    a, b = socket.socketpair()
    a.shutdown(socket.SHUT_WR)  # as shutdown_request does before handing over
    return a, b


def _wait(pred, timeout=3.0):
    t0 = time.monotonic()
    while not pred() and time.monotonic() - t0 < timeout:
        time.sleep(0.02)
    return pred()


def test_lingerer_survives_an_unexpected_error_in_a_pass(monkeypatch):
    lg = webui._Lingerer()
    real, calls = lg._pass, []

    def flaky(items):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")  # e.g. something odd from poll/recv
        return real(items)

    monkeypatch.setattr(lg, "_pass", flaky)
    a, b = _linger_pair()
    lg.add(a)
    b.close()  # the client closes: the lingering socket must still be closed after the failed pass
    assert _wait(lambda: lg.pending() == 0)
    assert len(calls) >= 2 and lg._thread is not None and lg._thread.is_alive()
    lg.stop()


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")  # the thread is killed on purpose
def test_lingerer_thread_that_ends_is_restarted_by_the_next_add(monkeypatch):
    lg = webui._Lingerer()
    real = lg._pass
    monkeypatch.setattr(lg, "_pass", lambda items: (_ for _ in ()).throw(SystemExit()))  # not an Exception
    a, b = _linger_pair()
    lg.add(a)
    assert _wait(lambda: lg._thread is None)  # the dead thread cleared itself
    monkeypatch.setattr(lg, "_pass", real)
    c, d = _linger_pair()
    lg.add(c)
    assert lg._thread is not None and lg._thread.is_alive()  # a fresh thread
    b.close(); d.close()
    assert _wait(lambda: lg.pending() == 0)  # both sockets (the old one too) are drained and closed
    lg.stop()


def test_lingerer_stop_joins_the_thread_and_closes_everything():
    lg = webui._Lingerer()
    socks = [_linger_pair() for _ in range(3)]
    for a, _ in socks:
        lg.add(a)
    t = lg._thread
    lg.stop()
    assert t is not None and not t.is_alive() and lg._thread is None
    assert lg.pending() == 0 and all(a.fileno() == -1 for a, _ in socks)
    for _, b in socks:
        b.close()
