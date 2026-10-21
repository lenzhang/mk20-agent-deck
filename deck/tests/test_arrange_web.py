"""会话安排 web page (webui.py): HTTP security, API, loop-thread hand-off and end-to-end with a fake adapter.

Every request goes through a real ``ArrangeServer`` on an ephemeral 127.0.0.1 port; the deck loop is a
real ``Daemon`` (MockAdapter sessions, no device: frames go to a sink) running in its own thread."""
import http.client
import json
import logging
import os
import re
import socket
import tempfile
import threading
import time
from argparse import Namespace

import pytest

from mk20deck import webui
from mk20deck.cli import cmd_arrange
from mk20deck.config import DEFAULT_ARRANGE_PORT, DeckConfig, load_config
from mk20deck.controller import Controller, slot_pos
from mk20deck.daemon import Daemon
from mk20deck.layout import DeviceLayout
from mk20deck.model import Mode, Session, SessionId, State, Tool
from mk20deck.render.canvas import Renderer
from mk20deck.render.palette import STATE_TEXT
from mk20deck.rules import RulesStore
from mk20deck.sim import full_adapters
from mk20deck.slots import SlotManager
from mk20deck.supervisor import Supervisor
from mk20deck.webui import ArrangeServer, ArrangeService, LoopCalls, start_arrange_server

CWD = "/Users/dev/worktrees/mk20-arrange/deck"


def tmux_sessions():
    out = []
    for i, (win, tool, st) in enumerate([("ag-ccai-deck", Tool.CCAI, State.RUNNING), ("ag-codex-fw", Tool.CODEX,
                                         State.NEEDS_INPUT), ("ag-kimi-docs", Tool.KIMI, State.IDLE)]):
        out.append(Session(SessionId(Mode.TMUX, f"%{i + 1}", "4242-1"), tool, f"pane{i + 1}", state=st,
                           cwd=f"{CWD}/sub{i}" if i == 0 else f"/Users/dev/other{i}", window=win,
                           tmux_session="main", updated_ms=1000 + i))
    return out


class Client:
    """Browser stand-in: one-time link -> session cookie (rotated on page loads) + per-session CSRF token."""

    def __init__(self, srv, host=None):
        self.srv = srv
        self.port = srv.port
        self.host = host or f"127.0.0.1:{self.port}"
        self.token = None
        self.sid = None
        self.cookie_name = f"mk20arr_{self.port}"

    def req(self, method, path, body=None, headers=None, raw=None, host=True):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": self.host} if host else {}
        h.update(headers or {})
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        b = r.read()
        hdrs = {k.lower(): v for k, v in r.getheaders()}
        c.close()
        return r.status, hdrs, b

    def take_cookie(self, h):
        m = re.match(rf"{self.cookie_name}=([^;]+);", h.get("set-cookie", ""))
        if m:
            self.sid = m.group(1)
        return m

    def cookie(self):
        return {"Cookie": f"{self.cookie_name}={self.sid}"}

    def authorize(self):
        st, h, b = self.req("GET", self.srv.mint_url().split(f":{self.port}", 1)[1])
        assert st == 200 and self.take_cookie(h), b
        return h, b

    def page(self):
        st, h, b = self.req("GET", "/", headers=self.cookie())
        assert st == 200, b
        self.token = re.search(rb'name="mk20-csrf" content="([^"]+)"', b).group(1).decode()
        assert self.take_cookie(h)  # the session id rotates on every page load
        return b, h

    def login(self):
        self.authorize()
        return self.page()

    def auth(self, **over):
        h = {"Origin": f"http://{self.host}", "X-MK20-CSRF": self.token,
             "Cookie": f"{self.cookie_name}={self.sid}", "Content-Type": "application/json",
             "Sec-Fetch-Site": "same-origin"}
        h.update(over)
        return {k: v for k, v in h.items() if v is not None}

    def state(self):
        st, _, b = self.req("GET", "/api/state", headers=self.auth(Origin=None, **{"Content-Type": None}))
        assert st == 200, b
        return json.loads(b)

    def post(self, path, body, method="POST", **over):
        st, h, b = self.req(method, path, body=body, headers=self.auth(**over))
        return st, json.loads(b) if b else None


def mode(snap, m):
    return next(x for x in snap["modes"] if x["mode"] == m)


@pytest.fixture
def web(tmp_path):
    ads = full_adapters()
    ads[Mode.TMUX].sessions = tmux_sessions()
    saved = []
    ctl = Controller(DeckConfig(), ads, sim=True, on_state_change=saved.append)
    ctl.refresh()
    calls = LoopCalls()
    store = RulesStore(str(tmp_path / "slot-rules.json"))
    svc = ArrangeService(ctl, calls, store)
    frames = []
    d = Daemon(ctl, Renderer(DeviceLayout.default(), animations=False), None, poll_interval=0.3, heartbeat=5.0,
               bg_poll_s=0.2, bg_first_delay_s=0.0, sink=frames.append, calls=calls)
    stop = threading.Event()
    loop = threading.Thread(target=d.run, args=(stop,), name="deck-loop", daemon=True)
    loop.start()
    sock = os.path.join(tempfile.mkdtemp(prefix="arr", dir="/tmp"), "run", "arrange.sock")
    srv = ArrangeServer(svc, 0, poll_interval=0.05, socket_path=sock)
    assert srv.start()
    cl = Client(srv)
    cl.login()
    # wait for the first background poll so tmux shows live sessions
    t0 = time.time()
    while Mode.TMUX not in d.bg_sessions and time.time() - t0 < 5:
        time.sleep(0.02)
    ns = Namespace(ctl=ctl, ads=ads, svc=svc, store=store, daemon=d, srv=srv, cl=cl, saved=saved, frames=frames,
                   loop=loop, tmp=tmp_path, sock=sock)
    yield ns
    stop.set()
    srv.stop()
    loop.join(5)


# ====================================================================== security
def test_binds_loopback_only(web):
    assert web.srv.httpd.server_address[0] == "127.0.0.1"


@pytest.mark.parametrize("host", ["evil.example:{p}", "127.0.0.1", "localhost", "127.0.0.1:1", "[::1]:{p}",
                                  "127.0.0.1:{p}.evil.example", "LOCALHOST:{p}x", "0.0.0.0:{p}"])
def test_host_header_must_be_loopback_with_port(web, host):
    cl = Client(web.srv, host=host.format(p=web.srv.port))
    st, _, b = cl.req("GET", "/")
    assert st == 403 and json.loads(b)["code"] == "host"
    cl.token, cl.sid = web.cl.token, web.cl.sid
    st, _ = cl.post("/api/clear", {"mode": "tmux", "slot": 0})
    assert st == 403


def test_localhost_host_is_accepted(web):
    cl = Client(web.srv, host=f"localhost:{web.srv.port}")
    cl.login()
    assert cl.state()["ok"]


def raw_http(port, data: bytes) -> bytes:
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    s.sendall(data)
    out = b""
    while True:
        chunk = s.recv(65536)
        if not chunk:
            break
        out += chunk
    s.close()
    return out


def test_missing_or_duplicate_host_rejected(web):
    p = web.srv.port
    assert raw_http(p, b"GET / HTTP/1.0\r\n\r\n").startswith(b"HTTP/1.0 403")
    dup = f"GET / HTTP/1.1\r\nHost: 127.0.0.1:{p}\r\nHost: evil:{p}\r\nConnection: close\r\n\r\n".encode()
    assert raw_http(p, dup).split(b"\r\n", 1)[0].endswith(b"403 Forbidden")


@pytest.mark.parametrize("origin", [None, "http://evil.example", "null", "http://localhost:{p}",
                                    "https://127.0.0.1:{p}", "http://127.0.0.1:{p}/"])
def test_origin_must_match_host(web, origin):
    o = origin.format(p=web.srv.port) if origin else None
    st, j = web.cl.post("/api/clear", {"mode": "tmux", "slot": 0}, Origin=o)
    assert st == 403 and j["code"] == "origin"


def test_cross_site_fetch_metadata_rejected(web):
    st, j = web.cl.post("/api/clear", {"mode": "tmux", "slot": 0}, **{"Sec-Fetch-Site": "cross-site"})
    assert st == 403 and j["code"] == "origin"
    st, _, _ = web.cl.req("GET", "/api/state", headers=web.cl.auth(**{"Sec-Fetch-Site": "same-site"}))
    assert st == 403


def _bad(t):
    return t[:-2] + ("aa" if not t.endswith("aa") else "bb")


@pytest.mark.parametrize("hdr,cookie,code", [(None, "ok", "csrf"), ("ok", None, "auth"), ("bad", "ok", "csrf"),
                                             ("ok", "bad", "auth"), ("bad", "bad", "auth"), ("", "", "auth"),
                                             ("other", "ok", "csrf"), ("ok", "other", "csrf")])
def test_session_cookie_and_its_csrf_token_both_required(web, hdr, cookie, code):
    other = Client(web.srv)
    other.login()  # a second, valid session: its CSRF token never works with our cookie (and vice versa)
    tok, sid = web.cl.token, web.cl.sid
    hv = {"ok": tok, "bad": _bad(tok), "": "", None: None, "other": other.token}[hdr]
    cv = {"ok": sid, "bad": _bad(sid), "": "", None: None, "other": other.sid}[cookie]
    over = {"X-MK20-CSRF": hv, "Cookie": None if cv is None else f"{web.cl.cookie_name}={cv}"}
    before = web.ctl.managers[Mode.TMUX].to_dict()
    st, j = web.cl.post("/api/clear", {"mode": "tmux", "slot": 0}, **over)
    assert st == 403 and j["code"] == code
    assert web.ctl.managers[Mode.TMUX].to_dict() == before
    st, _, b = web.cl.req("GET", "/api/state", headers=web.cl.auth(Origin=None, **over))
    assert st == 403 and json.loads(b)["code"] == code


def test_session_of_another_server_instance_is_useless(web, tmp_path):
    other = ArrangeServer(web.svc, 0)
    sid, s = other.auth.create()
    st, j = web.cl.post("/api/clear", {"mode": "tmux", "slot": 0}, **{"X-MK20-CSRF": s.csrf,
                        "Cookie": f"{web.cl.cookie_name}={sid}"})
    assert st == 403 and j["code"] == "auth"


def test_state_read_needs_session_and_token(web):
    st, h, b = web.cl.req("GET", "/api/state")
    assert st == 403 and json.loads(b)["code"] == "auth"
    st, h, b = web.cl.req("GET", "/api/state", headers=web.cl.cookie())
    assert st == 403 and json.loads(b)["code"] == "csrf"
    assert "access-control-allow-origin" not in h


def test_body_limits_and_content_type(web):
    big = {"mode": "tmux", "slot": 0, "pad": "x" * (webui.MAX_BODY + 10)}
    st, j = web.cl.post("/api/clear", big)
    assert st == 413
    p = web.srv.port
    auth = (f"Host: 127.0.0.1:{p}\r\nOrigin: http://127.0.0.1:{p}\r\nX-MK20-CSRF: {web.cl.token}\r\n"
            f"Cookie: {web.cl.cookie_name}={web.cl.sid}\r\nContent-Type: application/json\r\n")
    assert raw_http(p, f"POST /api/clear HTTP/1.0\r\n{auth}\r\n".encode()).startswith(b"HTTP/1.0 411")
    st, _, b = web.cl.req("POST", "/api/clear", raw=b'{"mode":"tmux","slot":0}',
                          headers=web.cl.auth(**{"Content-Type": "text/plain"}))
    assert st == 415
    st, _, b = web.cl.req("POST", "/api/clear", raw=b"mode=tmux&slot=0",
                          headers=web.cl.auth(**{"Content-Type": "application/x-www-form-urlencoded"}))
    assert st == 415
    st, _, b = web.cl.req("POST", "/api/clear", raw=b"{nope", headers=web.cl.auth())
    assert st == 400
    st, _, b = web.cl.req("POST", "/api/clear", raw=b"[1,2]", headers=web.cl.auth())
    assert st == 400 and "对象" in json.loads(b)["error"]
    st, _, b = web.cl.req("POST", "/api/clear", raw=iter([b'{"mode":"tmux","slot":0}']), headers=dict(
        web.cl.auth(), **{"Transfer-Encoding": "chunked"}))
    assert st == 411
    hdr = f"POST /api/clear HTTP/1.0\r\n{auth}Content-Length: 999999999\r\n\r\n".encode()
    assert raw_http(p, hdr).startswith(b"HTTP/1.0 413")
    st, _, _ = web.cl.req("GET", "/" + "a" * 600)
    assert st == 414


@pytest.mark.parametrize("method,path,allow", [
    ("DELETE", "/api/rules/r1", "PUT"), ("OPTIONS", "/api/move", "POST"), ("GET", "/api/move", "POST"),
    ("PUT", "/api/move", "POST"), ("POST", "/", "GET"), ("PATCH", "/api/rules/r1", "PUT"),
    ("HEAD", "/", "GET"), ("POST", "/api/state", "GET"), ("GET", "/api/rules/r1", "PUT")])
def test_wrong_methods_405_no_cors(web, method, path, allow):
    st, h, _ = web.cl.req(method, path, headers=web.cl.auth(**{"Access-Control-Request-Method": "POST"}))
    assert st == 405 and h["allow"] == allow
    assert not any(k.startswith("access-control-") for k in h)


def test_unknown_path_404(web):
    st, _, _ = web.cl.req("GET", "/etc/passwd")
    assert st == 404
    st, j = web.cl.post("/api/rules/..%2f/delete", {})
    assert st == 404


def test_page_headers_csp_nonce_cookie(web):
    b, h = web.cl.login()
    csp = h["content-security-policy"]
    nonce = re.search(r"script-src 'nonce-([^']+)'", csp).group(1)
    assert f"style-src 'nonce-{nonce}'" in csp and "default-src 'none'" in csp and "frame-ancestors 'none'" in csp
    html_ = b.decode()
    assert html_.count(f'nonce="{nonce}"') == 2  # the one <style> and the one <script>
    assert h["x-frame-options"] == "DENY" and h["x-content-type-options"] == "nosniff"
    assert h["referrer-policy"] == "no-referrer" and h["cache-control"] == "no-store"
    cookie = h["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Path=/" in cookie
    assert "Max-Age=2592000" in cookie  # 30 days
    # second load: same per-session CSRF token, fresh nonce, rotated session id
    old = web.cl.sid
    b2, h2 = web.cl.page()
    assert re.search(r"nonce-([^']+)'", h2["content-security-policy"]).group(1) != nonce
    assert web.cl.sid != old and web.cl.token in b2.decode()


def test_page_is_self_contained_chinese_and_uses_keyboard_labels(web):
    b, _ = web.cl.login()
    page = b.decode()
    assert "MK20 会话安排" in page and 'lang="zh-CN"' in page
    assert not re.search(r"(src|href)=[\"']?(https?:)?//", page)  # no external assets
    assert "innerHTML" not in page and "eval(" not in page
    for st in State:
        assert json.dumps(STATE_TEXT[st], ensure_ascii=False) in page
    for label in ("放到这里", "取消钉住", "清空此键", "固定规则", "目录", "tmux 窗口名", "标题包含", "工具"):
        assert label in page


def test_api_never_exposes_full_paths(web):
    snap = web.cl.state()
    text = json.dumps(snap, ensure_ascii=False)
    assert "/Users/" not in text
    assert any(s["dir"] == "mk20-agent-deck" for s in mode(snap, "codex_desktop")["sessions"])
    key = next(s["key"] for s in mode(snap, "tmux")["sessions"] if s["dir"] == "mk20-arrange")
    st, j = web.cl.post("/api/rules", {"mode": "tmux", "slot": 3, "kind": "cwd", "session": key})
    assert st == 200, j
    assert "/Users/" not in json.dumps(j, ensure_ascii=False)
    r = mode(j["state"], "tmux")["rules"][0]
    assert r["value"] == "mk20-arrange" and r["label"] == "目录 mk20-arrange"
    assert web.store.rules[0].value == "/Users/dev/worktrees/mk20-arrange"  # full path stays on the Mac side


# ====================================================================== API
def test_state_snapshot_shape(web):
    snap = web.cl.state()
    assert snap["current_mode"] == "codex_desktop" and snap["link"] == "sim" and snap["stale"] is False
    assert [m["mode"] for m in snap["modes"]] == ["codex_desktop", "herdr", "tmux"]
    desk, herdr, tmux = snap["modes"]
    assert desk["current"] and desk["source"] == "live" and len(desk["slots"]) == 15
    assert [s["name"] for s in desk["slots"]] == [f"S{i}" for i in range(1, 16)]
    s1 = desk["slots"][0]
    assert s1["kind"] == "session" and s1["title"] == "OTA发布" and s1["state_text"] == "待审批"
    assert not herdr["ok"] and herdr["reason"] == "herdr 未安装"
    assert all(s["kind"] == "offline" for s in herdr["slots"])
    assert tmux["source"] == "background" and tmux["kinds"] == ["cwd", "window", "title", "tool"]
    assert {s["window"] for s in tmux["sessions"]} == {"ag-ccai-deck", "ag-codex-fw", "ag-kimi-docs"}
    assert desk["kinds"] == ["cwd", "title", "tool"]


def test_move_swaps_pins_persists_and_reaches_the_keyboard(web):
    ctl = web.ctl
    key8 = ctl.mgr.slots[7].key
    key3 = ctl.mgr.slots[2].key
    n_saved = len(web.saved)
    st, j = web.cl.post("/api/move", {"mode": "codex_desktop", "slot": 2, "key": key8})
    assert st == 200 and j["message"] == "已与 S3 交换 · 已钉扎"
    assert ctl.mgr.slots[2].key == key8 and ctl.mgr.slots[2].pinned
    assert ctl.mgr.slots[7].key == key3
    assert len(web.saved) > n_saved and web.saved[-1]["modes"]["codex_desktop"]["slots"][2]["pinned"]
    assert mode(j["state"], "codex_desktop")["slots"][2]["key"] == key8
    assert any(t.startswith("网页：已与 S3 交换") for t in ctl.log)


@pytest.mark.parametrize("body,code,frag", [
    ({"mode": "codex_desktop", "slot": 2, "key": "codex_desktop::nope"}, 409, "不存在"),
    ({"mode": "codex_desktop", "slot": 2, "key": 7}, 400, "会话"),
    ({"mode": "codex_desktop", "slot": 15, "key": "x"}, 400, "键位"),
    ({"mode": "codex_desktop", "slot": "2", "key": "x"}, 400, "键位"),
    ({"mode": "vim", "slot": 2, "key": "x"}, 400, "模式"),
])
def test_move_validation(web, body, code, frag):
    st, j = web.cl.post("/api/move", body)
    assert st == code and frag in j["error"]


def test_unpin_and_clear(web):
    ctl = web.ctl
    key = ctl.mgr.slots[4].key
    web.cl.post("/api/move", {"mode": "codex_desktop", "slot": 4, "key": key})  # already there: pin it
    assert ctl.mgr.slots[4].pinned
    st, j = web.cl.post("/api/unpin", {"mode": "codex_desktop", "slot": 4})
    assert st == 200 and j["message"] == "已取消钉住" and not ctl.mgr.slots[4].pinned
    # non-current mode: tmux S10 gets a session by hand, then is cleared (no reapply outside the current mode)
    tm = ctl.managers[Mode.TMUX]
    tkey = mode(web.cl.state(), "tmux")["sessions"][2]["key"]
    st, j = web.cl.post("/api/move", {"mode": "tmux", "slot": 9, "key": tkey})
    assert st == 200 and tm.slots[9].key == tkey and tm.slots[9].pinned
    assert mode(j["state"], "tmux")["slots"][9]["key"] == tkey
    st, j = web.cl.post("/api/clear", {"mode": "tmux", "slot": 9})
    assert st == 200 and j["message"] == "已清空" and tm.slots[9] is None
    assert web.saved[-1]["modes"]["tmux"]["slots"][9] is None


def test_rules_crud(web):
    ctl, store = web.ctl, web.store
    st, j = web.cl.post("/api/rules", {"mode": "codex_desktop", "slot": 0, "kind": "title", "value": "OTA"})
    assert st == 200, j
    rid = store.rules[0].id
    desk = mode(j["state"], "codex_desktop")
    # two sessions match "OTA": the most recently active one (OTA发布) takes S1, the other auto fills
    assert desk["slots"][0]["title"] == "OTA发布" and desk["slots"][0]["by_rule"] and desk["slots"][0]["pinned"]
    assert desk["rules"][0]["status"] == "placed" and desk["rules"][0]["session"] == "OTA发布"
    assert any(s["title"] == "OTA回滚" and s["slot"] not in (None, 0) for s in desk["sessions"])
    assert json.load(open(store.path))["rules"][0]["value"] == "OTA"
    # disable: the session stays as an ordinary pin
    st, j = web.cl.post(f"/api/rules/{rid}", {"enabled": False}, method="PUT")
    assert st == 200 and not store.get(rid).enabled
    s0 = mode(j["state"], "codex_desktop")["slots"][0]
    assert s0["title"] == "OTA发布" and s0["pinned"] and not s0["by_rule"] and s0["rule"]["status"] == "disabled"
    # edit + re-enable
    st, j = web.cl.post(f"/api/rules/{rid}", {"enabled": True, "kind": "title", "value": "不存在"}, method="PUT")
    assert st == 200 and store.get(rid).value == "不存在"
    s0 = mode(j["state"], "codex_desktop")["slots"][0]
    assert s0["kind"] == "waiting" and s0["waiting"] == "等待：标题包含 不存在"
    kv = next(k for k in ctl.view().keys if (k.col, k.row) == slot_pos(0))
    assert kv.label == "等待：不存在"
    # replace via POST on the same slot keeps the id
    st, j = web.cl.post("/api/rules", {"mode": "codex_desktop", "slot": 0, "kind": "tool", "value": "codex"})
    assert st == 200 and [r.id for r in store.rules] == [rid]
    # delete
    st, j = web.cl.post(f"/api/rules/{rid}/delete", {})
    assert st == 200 and store.rules == [] and mode(j["state"], "codex_desktop")["rules"] == []
    st, j = web.cl.post(f"/api/rules/{rid}/delete", {})
    assert st == 404
    st, j = web.cl.post(f"/api/rules/{rid}", {"enabled": True}, method="PUT")
    assert st == 404


@pytest.mark.parametrize("body,code,frag", [
    ({"mode": "codex_desktop", "slot": 1, "kind": "window", "value": "x"}, 400, "tmux"),
    ({"mode": "tmux", "slot": 1, "kind": "regex", "value": "x"}, 400, "匹配方式"),
    ({"mode": "tmux", "slot": 1, "kind": "cwd", "value": "deck"}, 400, "绝对路径"),
    ({"mode": "tmux", "slot": 1, "kind": "tool", "value": "vim"}, 400, "ccai"),
    ({"mode": "tmux", "slot": 1, "kind": "title", "session": "tmux:x:%9"}, 409, "不存在"),
    ({"mode": "tmux", "slot": 1, "kind": "title", "value": "x", "enabled": "yes"}, 400, "enabled"),
])
def test_rule_validation(web, body, code, frag):
    st, j = web.cl.post("/api/rules", body)
    assert st == code and frag in j["error"]
    assert web.store.rules == []


def test_rule_values_prefilled_from_a_session(web):
    keys = {s["window"]: s["key"] for s in mode(web.cl.state(), "tmux")["sessions"]}
    st, j = web.cl.post("/api/rules", {"mode": "tmux", "slot": 0, "kind": "window", "session": keys["ag-kimi-docs"]})
    assert st == 200 and web.store.rules[0].value == "ag-kimi-docs"
    st, j = web.cl.post("/api/rules", {"mode": "tmux", "slot": 1, "kind": "tool", "session": keys["ag-codex-fw"]})
    assert st == 200 and web.store.at(Mode.TMUX, 1).value == "codex"
    # tmux is not the current mode: the page shows the preview of what the keyboard will show
    tm = mode(j["state"], "tmux")
    assert tm["slots"][0]["title"] == "pane3" and tm["slots"][1]["title"] == "pane2"
    assert web.ctl.managers[Mode.TMUX].rules  # pushed, applied when tmux becomes current
    web.svc.calls.submit(lambda d: web.ctl.switch_mode(Mode.TMUX))  # on the loop thread, like the MODE key
    assert web.ctl.mgr.slots[0].key == keys["ag-kimi-docs"] and web.ctl.mgr.slots[0].rule


def test_malformed_rules_file_warning_on_the_page(tmp_path):
    p = tmp_path / "slot-rules.json"
    p.write_text("[[[")
    ctl = Controller(DeckConfig(), full_adapters(), sim=True)
    ctl.refresh()
    calls = LoopCalls()
    svc = ArrangeService(ctl, calls, RulesStore(str(p)))
    snap = svc.snapshot(None)
    assert snap["warnings"] and snap["warnings"][0].startswith("规则文件格式错误")
    assert ctl.view().sub.note.startswith("规则文件格式错误")
    svc.api_rule_put({"mode": "codex_desktop", "slot": 2, "kind": "title", "value": "PRD"})
    assert svc.snapshot(None)["warnings"] == [] and ctl.rules_warning == ""


# ====================================================================== threads
def test_changes_run_on_the_loop_thread(web, monkeypatch):
    seen = []
    orig = SlotManager.place

    def spy(self, *a, **kw):
        seen.append(threading.get_ident())
        return orig(self, *a, **kw)

    monkeypatch.setattr(SlotManager, "place", spy)
    key = web.ctl.mgr.slots[9].key
    st, _ = web.cl.post("/api/move", {"mode": "codex_desktop", "slot": 0, "key": key})
    assert st == 200
    assert seen == [web.loop.ident] and web.svc.calls.last_thread == web.loop.ident
    assert threading.get_ident() != web.loop.ident


def test_no_loop_no_change_request_times_out_and_is_cancelled(tmp_path, monkeypatch):
    """Nothing drains the queue: the HTTP thread must not touch the deck; the job never runs later."""
    monkeypatch.setattr(webui, "JOB_TIMEOUT_S", 0.3)
    monkeypatch.setattr(webui, "STATE_TIMEOUT_S", 0.3)
    ctl = Controller(DeckConfig(), full_adapters(), sim=True)
    ctl.refresh()
    calls = LoopCalls()
    svc = ArrangeService(ctl, calls, RulesStore(str(tmp_path / "r.json")))
    srv = ArrangeServer(svc, 0, poll_interval=0.05)
    assert srv.start()
    try:
        cl = Client(srv)
        cl.login()
        st, _, b = cl.req("GET", "/api/state", headers=cl.auth(Origin=None))
        assert st == 503 and json.loads(b)["code"] == "busy"  # no snapshot yet
        before = ctl.mgr.to_dict()
        key = ctl.mgr.slots[5].key
        st, j = cl.post("/api/move", {"mode": "codex_desktop", "slot": 0, "key": key})
        assert st == 503 and j["code"] == "busy"
        assert ctl.mgr.to_dict() == before
        assert calls.drain(None) == 0  # both jobs were cancelled: nothing runs late
        assert ctl.mgr.to_dict() == before
    finally:
        srv.stop()


def test_cancelled_jobs_never_run_late():
    calls = LoopCalls()
    ran = []
    with pytest.raises(webui.LoopTimeout):
        calls.submit(lambda d: ran.append(1), timeout=0.05)
    assert calls.drain(None) == 0 and ran == []
    # a live loop answers
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            calls.drain("D")
            calls.wait(0.05)

    th = threading.Thread(target=loop, daemon=True)
    th.start()
    try:
        assert calls.submit(lambda d: d * 2, timeout=2) == "DD"
        with pytest.raises(ZeroDivisionError):
            calls.submit(lambda d: 1 / 0, timeout=2)
    finally:
        stop.set()
        th.join(2)


def test_stale_snapshot_when_loop_busy(web, monkeypatch):
    monkeypatch.setattr(webui, "STATE_TIMEOUT_S", 0.2)
    snap = web.cl.state()
    assert snap["stale"] is False
    gate = threading.Event()
    th = threading.Thread(target=lambda: web.svc.calls.submit(lambda d: gate.wait(5), timeout=5, run_timeout=6),
                          daemon=True)
    th.start()
    time.sleep(0.1)
    try:
        snap2 = web.cl.state()
        assert snap2["stale"] is True and len(snap2["modes"]) == 3
    finally:
        gate.set()
        th.join(5)


def test_supervisor_serves_the_page_while_no_keyboard_is_connected(tmp_path):
    ctl = Controller(DeckConfig(), full_adapters(), sim=False)
    calls = LoopCalls()
    svc = ArrangeService(ctl, calls, RulesStore(str(tmp_path / "r.json")))
    cfg = DeckConfig()
    sup = Supervisor(ctl, lambda: [], lambda p: None, lambda: None, cfg, retry_s=0.2, calls=calls)
    stop = threading.Event()
    th = threading.Thread(target=sup.run, args=(stop,), daemon=True)
    th.start()
    try:
        snap = calls.submit(lambda d: svc.snapshot(d), timeout=2)
        assert snap["link"] == "disconnected"
        assert mode(snap, "codex_desktop")["slots"][0]["title"] == "OTA发布"  # refreshed on demand
        key = ctl.mgr.slots[3].key
        res = calls.submit(lambda d: svc.api_move({"mode": "codex_desktop", "slot": 0, "key": key}, d), timeout=2)
        assert res["ok"] and ctl.mgr.slots[0].key == key
        assert calls.last_thread == th.ident
    finally:
        stop.set()
        th.join(3)


# ====================================================================== end to end
def wait_for(pred, timeout=1.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if pred():
            return time.monotonic() - t0
        time.sleep(0.01)
    raise AssertionError("timed out")


def test_e2e_move_shows_on_the_keyboard_within_a_second(web):
    ctl, d = web.ctl, web.daemon
    key = ctl.mgr.slots[12].key
    title = ctl.mgr.sessions[key].title
    frames0 = d.frames_sent
    t0 = time.monotonic()
    st, j = web.cl.post("/api/move", {"mode": "codex_desktop", "slot": 1, "key": key})
    assert st == 200
    kv = next(k for k in ctl.view().keys if (k.col, k.row) == slot_pos(1))
    assert kv.title == title and kv.pinned
    wait_for(lambda: d.frames_sent > frames0, 1.0)  # a forced frame went out right after the change
    assert time.monotonic() - t0 < 1.5


def test_e2e_rule_then_keyboard_move_disables_it(web):
    ctl, d = web.ctl, web.daemon
    st, j = web.cl.post("/api/rules", {"mode": "codex_desktop", "slot": 4, "kind": "title", "value": "还没有的线程"})
    assert st == 200
    wait_for(lambda: next(k for k in ctl.view().keys if (k.col, k.row) == slot_pos(4)).label == "等待：还没有…")
    # the keyboard picks S1 up (long press) and drops it on the reserved S5
    d.inject_key(1, 1, True)
    time.sleep(0.75)
    d.inject_key(1, 1, False)
    wait_for(lambda: ctl.moving is not None, 1.0)
    d.inject_key(*slot_pos(4), True)
    d.inject_key(*slot_pos(4), False)
    wait_for(lambda: not web.store.rules[0].enabled, 2.0)
    snap = web.cl.state()
    r = mode(snap, "codex_desktop")["rules"][0]
    assert r["status"] == "disabled" and r["note"].startswith("键盘把 S1 移到此键（")
    assert mode(snap, "codex_desktop")["slots"][4]["pinned"]


def test_e2e_page_poll_sees_live_state_changes(web):
    web.ads[Mode.DESKTOP].sessions[0].state = State.NEEDS_APPROVAL
    wait_for(lambda: mode(web.cl.state(), "codex_desktop")["slots"][0]["state_text"] == "待审批", 3.0)


# ====================================================================== port / config / CLI
def test_busy_port_logs_one_warning_and_returns_none(web, caplog):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    try:
        with caplog.at_level(logging.WARNING, logger="mk20deck.webui"):
            assert start_arrange_server(web.svc, port) is None
        warns = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warns) == 1 and "会话安排网页未启动" in warns[0].getMessage()
    finally:
        s.close()
    assert start_arrange_server(web.svc, 0) is None  # 0 = off (no bind at all)


def test_config_web_arrange_port(tmp_path, caplog):
    p = tmp_path / "c.toml"
    assert load_config(str(p)).arrange_port == DEFAULT_ARRANGE_PORT == 8781
    for v, want in (("0", 0), ("8790", 8790), ('"x"', 8781), ("true", 8781), ("70000", 8781)):
        p.write_text(f"[web]\narrange_port = {v}\n")
        assert load_config(str(p)).arrange_port == want


def test_cli_arrange_opens_a_one_time_link(web, tmp_path, capsys):
    p = tmp_path / "c.toml"
    p.write_text(f"[web]\narrange_port = {web.srv.port}\n")
    opened = []
    assert cmd_arrange(Namespace(config=str(p), print_only=False, socket=web.sock), opener=opened.append) == 0
    assert len(opened) == 1 and opened[0].startswith(f"http://127.0.0.1:{web.srv.port}/auth?t=")
    out = capsys.readouterr().out
    assert "auth?t=" not in out  # the token goes to the browser only
    # the link works once: a fresh browser gets a session, a second use is refused
    b = Client(web.srv)
    st, h, _ = b.req("GET", opened[0].split(f":{web.srv.port}", 1)[1])
    assert st == 200 and b.take_cookie(h)
    st, _, body = b.req("GET", opened[0].split(f":{web.srv.port}", 1)[1])
    assert st == 403 and "链接已失效" in body.decode()
    assert cmd_arrange(Namespace(config=str(p), print_only=True, socket=web.sock), opener=opened.append) == 0
    assert len(opened) == 1 and "/auth?t=" in capsys.readouterr().out
    p.write_text("[web]\narrange_port = 0\n")
    assert cmd_arrange(Namespace(config=str(p), print_only=False, socket=web.sock), opener=opened.append) == 1
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    free = s.getsockname()[1]
    s.close()
    p.write_text(f"[web]\narrange_port = {free}\n")
    assert cmd_arrange(Namespace(config=str(p), print_only=False, socket=str(tmp_path / "none.sock")),
                       opener=opened.append) == 1
    assert "没有运行" in capsys.readouterr().out and len(opened) == 1


def test_cli_parser_has_arrange():
    from mk20deck import cli
    with pytest.raises(SystemExit) as e:
        cli.main(["arrange", "--help"])
    assert e.value.code == 0


def test_daemon_step_runs_queued_calls_and_forces_a_frame(tmp_path):
    from mk20deck.sim import FakeClock
    clk = FakeClock()
    ctl = Controller(DeckConfig(), full_adapters(), clock=clk, sim=True)
    calls = LoopCalls()
    svc = ArrangeService(ctl, calls, RulesStore(str(tmp_path / "r.json")))
    d = Daemon(ctl, Renderer(DeviceLayout.default(), animations=False), None, poll_interval=100, heartbeat=100,
               clock=clk, bg_poll_s=0, calls=calls)
    assert d.step() is True  # first frame
    assert d.step() is False  # nothing changed
    key = ctl.mgr.slots[6].key
    out = {}
    th = threading.Thread(target=lambda: out.setdefault("r", calls.submit(
        lambda dd: svc.api_move({"mode": "codex_desktop", "slot": 0, "key": key}, dd), timeout=5)))
    th.start()
    t0 = time.monotonic()
    while not calls._q and time.monotonic() - t0 < 2:
        time.sleep(0.005)
    assert d.step() is True  # the change ran here (the loop thread) and forced a frame
    th.join(5)
    assert out["r"]["ok"] and ctl.mgr.slots[0].key == key and calls.last_thread == threading.get_ident()
