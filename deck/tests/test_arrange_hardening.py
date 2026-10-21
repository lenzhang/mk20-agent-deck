"""Kimi K3 security review of feature/arrange-web (2026-10-22, NO-GO) — one test group per finding:

B1 anonymous GET / handed out the token        -> one-time token over a 0600 unix socket + session cookie
B2 unbounded threads / queues, slow trickle     -> connection cap, per-request deadline, bounded LoopCalls
M1 Codex preview text as title in the API       -> Session.title_private, id prefix in the API
M2 non-current modes: preview vs old manager    -> same batch for view and change, refuse when unreadable
M3 rules save failure reported success          -> transactional store, non-2xx
M4 huge ``created`` crashed startup              -> validated numbers, per-entry quarantine
M5 old background poll thread not joined        -> bounded join + one bg poll at a time
"""
import json
import os
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
from mk20deck.webauth import AuthSocket, AuthStore, request_url
from mk20deck.webui import ArrangeServer, ArrangeService, LoopBusy, LoopCalls

from test_arrange_web import Client, mode, raw_http, tmux_sessions


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
        with pytest.raises(ConnectionError, match="uid"):
            request_url(path)
        assert a.refused == 1 and a.served == 0
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
def test_connection_cap_answers_503_beyond_the_limit(deck):
    w = deck(max_connections=2)
    hogs = []
    for _ in range(2):
        s = socket.create_connection(("127.0.0.1", w.srv.port), timeout=5)
        s.sendall(b"GET / HTTP/1.1\r\n")  # never finishes the request
        hogs.append(s)
    time.sleep(0.2)
    third = raw_http(w.srv.port, f"GET /favicon.ico HTTP/1.0\r\nHost: 127.0.0.1:{w.srv.port}\r\n\r\n".encode())
    assert third.startswith(b"HTTP/1.0 503") and b'"busy"' in third
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
