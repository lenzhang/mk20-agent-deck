"""Local "会话安排" (session arrangement) web page, served by mk20deck at http://127.0.0.1:8781/.

User decision 2026-10-22: arranging slots by config file is too complex — a page instead.  It shows the
15 L1 session slots of every mode exactly like the keyboard (S1..S15, same state colours / labels), lets the
user place a session on a key (``SlotManager.place`` = move + pin, swap), unpin / clear a key and manage
**固定规则** (``rules.py``: directory / tmux window / title / tool → slot).

Threads.  The HTTP server runs in its own daemon threads (stdlib ``ThreadingHTTPServer``) and never touches
the controller, the slot managers or the rules store: every read and write is a job queued on
``LoopCalls`` and executed by the deck loop thread (``Daemon.step`` while a keyboard is connected,
``Supervisor._wait`` while none is).  A change forces a keyboard frame (it shows within one loop
iteration, plus a "网页：…" toast).  Reads return a snapshot built on the loop thread; if the loop is busy
(e.g. mid-handshake) the last snapshot is returned marked ``stale``.

Bounds (Kimi K3 review, blocker 2): at most ``MAX_CONNECTIONS`` connections (more → 503 at once); HTTP/1.1
keep-alive so a few open tabs polling every 2 s reuse their connection instead of hitting the cap; a
keep-alive connection idle for ``READ_TIMEOUT_S`` is closed; every request has a hard ``REQUEST_DEADLINE_S``
from its first byte (a slow trickle is cut off, not just an idle socket), ``LoopCalls`` holds at most ``MAX_PENDING`` jobs (full → 503), the loop wake-up is coalesced and one
loop iteration runs at most ``DRAIN_MAX`` jobs, so the page can never starve the keyboard loop.

Security (local page, but any local process — sandboxed apps too — can talk to 127.0.0.1):

* ``GET /`` without a session shows only "请在 Mac 上运行 mk20deck arrange 打开"; a session comes from a
  one-time token obtained through a 0600 unix socket (filesystem access required, see ``webauth``) and
  redeemed at ``/auth?t=…`` (``SameSite=Strict; HttpOnly`` cookie, 30 days, id rotated on page load) with a
  separate per-session CSRF token;
* binds 127.0.0.1 only; ``Host`` must be exactly ``127.0.0.1:<port>`` or ``localhost:<port>``
  (DNS rebinding);
* no CORS headers at all; ``OPTIONS``/``DELETE``/``PATCH``/``HEAD`` → 405;
* every state change is ``POST``/``PUT`` with a JSON body (``Content-Type: application/json``,
  ``Content-Length`` ≤ 8 KiB, no chunked bodies), ``Origin`` equal to ``http://<Host>``, ``Sec-Fetch-Site``
  (when sent) ``same-origin``, a valid session cookie and the session's CSRF token in ``X-MK20-CSRF``
  (constant-time compare); ``GET /api/state`` needs session + token too;
* CSP ``default-src 'none'`` with per-response nonces for the inline style / script (no external assets),
  ``frame-ancestors 'none'``, ``nosniff``, ``no-referrer``, ``no-store``, CORP/COOP same-origin;
* the API exposes session titles (never a title that is really prompt text: ``Session.title_private``),
  tools, states, tmux window names and directory *names* only — never full paths, summaries or prompts.
"""
from __future__ import annotations

import collections
import hmac
import os
import html
import http.client
import http.cookies
import json
import logging
import re
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import parse_qs

from .adapters.base import BackendUnavailable
from .model import MODE_CYCLE, PRIORITY, Mode, Session, State
from .render.palette import MODE_NAME, MODE_STRIP, STATE_COLOR, STATE_TEXT
from .rules import KIND_TEXT, KINDS, RULE_TOOLS, RulesSaveError, RulesStore, dir_name, worktree_root
from .slots import N_SLOTS, SlotManager
from .webauth import SESSION_TTL_S, AuthSocket, AuthStore

log = logging.getLogger(__name__)

MAX_BODY = 8 * 1024
MAX_PATH = 512
MAX_CONNECTIONS = 8
REQUEST_DEADLINE_S = 5.0   # from a request's first byte to the end of its response, header/body read included
READ_TIMEOUT_S = 4.0       # per socket read; also the idle limit of a keep-alive connection between requests
JOB_TIMEOUT_S = 3.0        # the loop must pick a job up within this (else cancelled, 503)
JOB_RUN_S = 1.5            # ... and finish a started one within this
STATE_TIMEOUT_S = 2.0
MAX_PENDING = 32
DRAIN_MAX = 8
RULE_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,32}$")
MODE_KINDS = {Mode.DESKTOP: ("cwd", "title", "tool"), Mode.HERDR: ("title", "tool"),
              Mode.TMUX: ("cwd", "window", "title", "tool")}
# Ink on the state colour (same choice as the key face chips, render/keys.py)
_DARK_INK = {State.ERROR, State.THINKING, State.OFFLINE, State.UNKNOWN}


class LoopTimeout(Exception):
    """The deck loop did not pick the job up in time (the job is cancelled, nothing changed), or a started
    job did not finish in time (``started``: it may still take effect)."""

    def __init__(self, started: bool = False) -> None:
        super().__init__("started" if started else "pending")
        self.started = started


class LoopBusy(Exception):
    """``MAX_PENDING`` jobs are already queued (503, nothing queued)."""


class NotFound(LookupError):
    """API: the rule does not exist (404)."""


class Conflict(Exception):
    """API: the request no longer matches the deck (409): stale page or the mode cannot be read now."""


class _Job:
    __slots__ = ("fn", "done", "result", "exc", "state")

    def __init__(self, fn: Callable) -> None:
        self.fn = fn
        self.done = threading.Event()
        self.result = None
        self.exc: BaseException | None = None
        self.state = "pending"  # pending -> running -> done | pending -> cancelled


class LoopCalls:
    """Hand-off from HTTP threads to the deck loop thread.

    ``submit`` (any thread) queues ``fn`` and blocks until the loop ran it; ``drain`` (loop thread only) runs
    at most ``max_items`` queued jobs per call.  A job not started within ``timeout`` is cancelled, so a
    timed-out request never changes anything later.  The queue is bounded (``LoopBusy``) and the wake-up of
    the loop is coalesced: one pending wake at a time, re-armed by ``drain`` while jobs are left."""

    def __init__(self, max_pending: int = MAX_PENDING) -> None:
        self.max_pending = max_pending
        self._q: collections.deque[_Job] = collections.deque()
        self._lock = threading.Lock()
        self._ev = threading.Event()
        self._waker: Callable[[], None] | None = None
        self._wake_pending = False
        self.wakes = 0
        self.drained = 0
        self.last_thread: int | None = None  # ident of the thread that ran the last job (tests)

    def attach(self, waker: Callable[[], None] | None) -> None:
        """The running loop's wake-up (Daemon: an input event), or None when it stops."""
        with self._lock:
            self._waker, self._wake_pending = waker, False
        if waker is not None and self.pending():
            self._wake()

    def wait(self, timeout: float) -> None:
        self._ev.wait(timeout)

    def pending(self) -> int:
        with self._lock:
            return sum(1 for j in self._q if j.state == "pending")

    def _wake(self) -> None:
        with self._lock:
            w = self._waker
            if w is None or self._wake_pending:
                return
            self._wake_pending = True
        self.wakes += 1
        try:
            w()
        except Exception:  # noqa: BLE001 — a dead loop only means the job times out
            pass

    def submit(self, fn: Callable, timeout: float | None = None, run_timeout: float | None = None):
        timeout = JOB_TIMEOUT_S if timeout is None else timeout
        run_timeout = JOB_RUN_S if run_timeout is None else run_timeout
        job = _Job(fn)
        with self._lock:
            live = [j for j in self._q if j.state == "pending"]
            if len(live) != len(self._q):
                self._q = collections.deque(live)  # forget cancelled jobs
            if len(self._q) >= self.max_pending:
                raise LoopBusy()
            self._q.append(job)
        self._ev.set()
        self._wake()
        if not job.done.wait(timeout):
            with self._lock:
                if job.state == "pending":
                    job.state = "cancelled"
                    raise LoopTimeout()
            if not job.done.wait(run_timeout):
                raise LoopTimeout(started=True)
        if job.exc is not None:
            raise job.exc
        return job.result

    def drain(self, daemon=None, max_items: int = DRAIN_MAX) -> int:
        with self._lock:
            self._wake_pending = False
        n = 0
        while n < max_items:
            with self._lock:
                if not self._q:
                    self._ev.clear()
                    return n
                job = self._q.popleft()
                if job.state != "pending":
                    continue
                job.state = "running"
            try:
                job.result = job.fn(daemon)
            except BaseException as e:  # noqa: BLE001 — handed back to the HTTP thread
                job.exc = e
            finally:
                self.last_thread = threading.get_ident()
                job.state = "done"
                job.done.set()
            n += 1
            self.drained += 1
        with self._lock:
            left = bool(self._q)
            if not left:
                self._ev.clear()
        if left:
            self._wake()  # the rest runs in the next loop iteration (keyboard frames in between)
        return n


def _hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(rgb)


def _short(t: str, n: int = 24) -> str:
    return t if len(t) <= n else t[:n] + "…"


def web_title(s: Session) -> str:
    """Title as the API may show it: a title that is really prompt text becomes the id prefix."""
    if getattr(s, "title_private", False):
        return s.sid.native[:8]
    return s.title or "?"


class ArrangeService:
    """Loop-thread side: rules store + snapshots + changes.  Every public ``api_*`` runs on the loop thread
    (via ``LoopCalls``); ``ArrangeServer`` only calls ``calls.submit``."""

    def __init__(self, controller, calls: LoopCalls, store: RulesStore, idle_adapters: dict | None = None,
                 poll_interval: float = 2.0, bg_poll_s: float = 10.0, clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time) -> None:
        self.ctl = controller
        self.calls = calls
        self.store = store
        self.idle_adapters = idle_adapters  # polled for the page while no keyboard is connected
        self.poll_interval = poll_interval
        self.bg_poll_s = bg_poll_s
        self.clock = clock
        self.wall = wall
        self.cache: dict | None = None  # last snapshot (read by HTTP threads: atomic reference swap)
        self._idle_bg: dict[Mode, tuple] = {}
        self._idle_refresh = float("-inf")
        self.changes = 0
        self.attach()

    # ------------------------------------------------------------------ wiring (loop thread / startup)
    def attach(self) -> None:
        for mgr in self.ctl.managers.values():
            mgr.on_rule_override = self._on_override
        self.push_rules()

    def push_rules(self) -> None:
        for m, mgr in self.ctl.managers.items():
            mgr.set_rules(self.store.for_mode(m))
        self.ctl.rules_warning = self.store.warning

    def _on_override(self, rule, why: str) -> None:
        """A keyboard / web change on a rule slot: the rule is disabled (noted on the page) and saved."""
        when = time.strftime("%m-%d %H:%M", time.localtime(self.wall()))
        self.store.disable(rule.id, f"{why}（{when}）")  # never raises: a write failure becomes a warning
        log.info("slot rule %s (%s S%d) disabled: %s", rule.id, rule.mode.value, rule.slot + 1, why)
        self.push_rules()

    # ------------------------------------------------------------------ helpers
    def _bg(self, daemon) -> dict:
        return daemon.bg_sessions if daemon is not None else self._idle_bg

    def _idle_poll(self) -> None:
        """No keyboard connected (Supervisor waiting): keep the page live while it polls (on demand only)."""
        now = self.clock()
        if now - self._idle_refresh < self.poll_interval:
            return
        first = self._idle_refresh == float("-inf")
        self._idle_refresh = now
        self.ctl.refresh()
        if not self.idle_adapters or self.bg_poll_s <= 0:
            return
        for m in MODE_CYCLE:
            ad = self.idle_adapters.get(m)
            old = self._idle_bg.get(m)
            if m == self.ctl.mode or ad is None:
                continue
            if not first and old is not None and now - old[3] < self.bg_poll_s:
                continue
            try:
                res = (ad.poll(()), "", self.wall(), now)
            except BackendUnavailable as e:
                res = (None, str(e), self.wall(), now)
            except Exception as e:  # noqa: BLE001
                res = (None, f"读取失败：{type(e).__name__}", self.wall(), now)
            self._idle_bg[m] = res

    def batch(self, m: Mode, daemon) -> tuple[list[Session] | None, str, str]:
        """``(sessions, error, source)``: the one session batch both the page view and changes of mode ``m``
        use.  ``sessions`` is None when the mode cannot be read now (changes are refused, never applied to
        an empty list).  Sources: live (current mode), background (other modes, bg poll), stale (the mode
        was live more recently than its last bg poll), none (no data yet)."""
        ctl = self.ctl
        mgr = ctl.managers[m]
        if m == ctl.mode:
            ok, reason = ctl.backend[m]
            return (list(mgr.sessions.values()) if ok else None), reason, "live"
        bg = self._bg(daemon).get(m)
        lp = ctl.last_poll_wall.get(m)
        if bg is None or (lp is not None and lp > bg[2]):
            if lp is None:
                return None, "还没有读到会话（后台约每 10 秒读一次）", "none"
            ok, reason = ctl.backend[m]
            return (list(mgr.sessions.values()) if ok else None), reason, "stale"
        if bg[0] is None:
            return None, bg[1] or "不可用", "background"
        return [s for s in bg[0] if s.sid.key() not in mgr.excluded], "", "background"

    def _view(self, m: Mode, daemon) -> tuple[SlotManager, list[Session], bool, str, str]:
        """(slot manager to show, sessions of the mode, backend ok, reason, data source)."""
        mgr = self.ctl.managers[m]
        sessions, err, source = self.batch(m, daemon)
        if m == self.ctl.mode:
            ok, reason = self.ctl.backend[m]
            return mgr, list(mgr.sessions.values()), ok, reason, source
        if sessions is None:
            return mgr.preview(None), [], source == "none", err, source
        return mgr.preview(sessions), sessions, True, "", source

    def _prepare(self, m: Mode, daemon, body: dict, slot: int | None = None) -> tuple[SlotManager, dict]:
        """The real manager of mode ``m``, brought to the same state the page saw (same batch as the
        preview), plus the batch by key.  Refuses when the mode cannot be read or the page is stale."""
        sessions, err, _ = self.batch(m, daemon)
        if sessions is None:
            raise Conflict(f"{MODE_NAME[m]} 现在读不到会话（{err}），暂不能修改")
        mgr = self.ctl.managers[m]
        if m != self.ctl.mode:
            mgr.update(sessions)  # what the preview showed; persisted with the change
        if slot is not None and "expect" in body:
            e = mgr.slots[slot]
            cur = e.key if (e is not None and not e.lost) else None
            want = body.get("expect")
            if want is not None and not isinstance(want, str):
                raise ValueError("expect 无效")
            if cur != want:
                raise Conflict(f"S{slot + 1} 已经变了，请看最新的页面后再试")
        return mgr, {s.sid.key(): s for s in sessions}

    # ------------------------------------------------------------------ snapshot
    def snapshot(self, daemon=None) -> dict:
        if daemon is None:
            self._idle_poll()
        ctl = self.ctl
        if daemon is None:
            link = "disconnected"
        elif ctl.sim:
            link = "sim"
        else:
            lk = getattr(daemon, "link", None)
            link = "connected" if (lk is not None and lk.connected) else "disconnected"
        lp = ctl.last_poll_wall.get(ctl.mode)
        snap = {
            "ok": True,
            "current_mode": ctl.mode.value,
            "link": link,
            "updated": time.strftime("%H:%M:%S", time.localtime(lp)) if lp else "--:--:--",
            "warnings": [self.store.warning] if self.store.warning else [],
            "modes": [self._mode_json(m, daemon) for m in MODE_CYCLE],
            "stale": False,
        }
        self.cache = snap
        return snap

    def _mode_json(self, m: Mode, daemon) -> dict:
        vm, sessions, ok, reason, source = self._view(m, daemon)
        by_key = {s.sid.key(): s for s in sessions}
        slot_of = {e.key: i for i, e in enumerate(vm.slots) if e is not None}
        rules = self.store.for_mode(m)
        rule_at = {r.slot: r for r in rules}
        slots = []
        for i in range(N_SLOTS):
            e = vm.slots[i]
            r = rule_at.get(i)
            item = {"slot": i, "name": f"S{i + 1}", "pinned": False, "by_rule": False, "selected": False,
                    "key": None, "rule": self._rule_json(r, vm, by_key) if r else None}
            if e is None:
                w = vm.waiting.get(i)
                if w is not None:
                    item.update(kind="waiting", waiting=f"等待：{w.label()}")
                else:
                    item["kind"] = "empty" if ok else "offline"
            elif e.lost:
                item.update(kind="lost", pinned=True)
            else:
                s = vm.sessions.get(e.key) or by_key.get(e.key)
                item.update(kind="session", key=e.key, pinned=e.pinned, by_rule=bool(e.rule),
                            selected=e.key == vm.selected)
                item.update(self._session_fields(s) if s else
                            {"title": "?", "tool": "", "dir": "", "state": State.UNKNOWN.value,
                             "state_text": STATE_TEXT[State.UNKNOWN]})
            slots.append(item)
        order = sorted(sessions, key=lambda s: (slot_of.get(s.sid.key(), N_SLOTS), -PRIORITY[s.state],
                                                -s.updated_ms, web_title(s)))
        return {
            "mode": m.value, "name": MODE_NAME[m], "current": m == self.ctl.mode, "ok": ok, "reason": reason,
            "source": source, "overflow": vm.overflow, "kinds": list(MODE_KINDS[m]),
            "slots": slots,
            "sessions": [dict(self._session_fields(s), key=s.sid.key(), slot=slot_of.get(s.sid.key()))
                         for s in order],
            "rules": [self._rule_json(r, vm, by_key) for r in rules],
        }

    @staticmethod
    def _session_fields(s: Session) -> dict:
        d = {"title": web_title(s), "tool": s.tool.value, "dir": dir_name(s.cwd), "state": s.state.value,
             "state_text": STATE_TEXT[s.state]}
        if s.sid.mode == Mode.TMUX:
            d["window"] = s.window
            d["tmux_session"] = s.tmux_session
        return d

    @staticmethod
    def _rule_json(r, vm: SlotManager, by_key: dict) -> dict:
        e = vm.slots[r.slot]
        if not r.enabled:
            status, who = "disabled", ""
        elif e is not None and e.rule == r.id:
            s = vm.sessions.get(e.key) or by_key.get(e.key)
            status, who = "placed", (web_title(s) if s else "?")
        elif r.slot in vm.waiting:
            status, who = "waiting", ""
        else:
            status, who = "pending", ""
        return {"id": r.id, "slot": r.slot, "name": f"S{r.slot + 1}", "kind": r.kind,
                "kind_text": KIND_TEXT[r.kind], "value": r.display_value(), "label": r.label(),
                "enabled": r.enabled, "note": r.note, "status": status, "session": who}

    # ------------------------------------------------------------------ changes (loop thread)
    def _after(self, m: Mode, msg: str, daemon) -> dict:
        ctl = self.ctl
        if m == ctl.mode:
            ctl.managers[m].reapply()  # rules / fill on the last poll's sessions, no backend I/O
        ctl.persist()
        ctl._say(f"网页：{msg}" if m == ctl.mode else f"网页（{MODE_NAME[m]}）：{msg}")
        self.changes += 1
        log.info("arrange web: %s %s", m.value, msg)
        return {"ok": True, "message": msg, "state": self.snapshot(daemon)}

    @staticmethod
    def _mode(body: dict) -> Mode:
        try:
            return Mode(body.get("mode"))
        except (ValueError, TypeError):
            raise ValueError("模式无效") from None

    @staticmethod
    def _slot(body: dict) -> int:
        v = body.get("slot")
        if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v < N_SLOTS:
            raise ValueError("键位无效（S1–S15）")
        return v

    def api_move(self, body: dict, daemon=None) -> dict:
        m, slot = self._mode(body), self._slot(body)
        key = body.get("key")
        if not isinstance(key, str):
            raise ValueError("会话无效")
        mgr, known = self._prepare(m, daemon, body, slot)
        if key not in known:
            raise Conflict("会话已不存在，请刷新")
        return self._after(m, mgr.place(key, slot, "网页"), daemon)

    def api_unpin(self, body: dict, daemon=None) -> dict:
        m, slot = self._mode(body), self._slot(body)
        mgr, _ = self._prepare(m, daemon, body, slot)
        return self._after(m, mgr.unpin(slot, "网页"), daemon)

    def api_clear(self, body: dict, daemon=None) -> dict:
        m, slot = self._mode(body), self._slot(body)
        mgr, _ = self._prepare(m, daemon, body, slot)
        return self._after(m, mgr.clear(slot, "网页"), daemon)

    def _rule_value(self, m: Mode, kind, body: dict, daemon) -> str:
        """Explicit ``value`` or, with ``session``, derived from that session on the Mac side (a directory
        rule takes the session's worktree / cwd path, which the page never sees)."""
        if body.get("session") is None:
            return body.get("value")
        sessions, err, _ = self.batch(m, daemon)
        s = {x.sid.key(): x for x in sessions or []}.get(body.get("session"))
        if s is None:
            raise Conflict("所选会话已不存在，请刷新" if sessions is not None else f"{MODE_NAME[m]} 现在读不到会话（{err}）")
        if kind == "cwd":
            if not s.cwd:
                raise ValueError("该会话没有目录信息")
            return worktree_root(s.cwd)
        if kind == "window":
            return s.window or s.tmux_session
        if kind == "tool":
            return s.tool.value
        if getattr(s, "title_private", False):
            raise ValueError("该会话没有标题（只有提示词），不能用作标题规则")
        return s.title

    def api_rule_put(self, body: dict, daemon=None) -> dict:
        m, slot = self._mode(body), self._slot(body)
        kind = body.get("kind")
        if kind not in KINDS:
            raise ValueError("匹配方式无效")
        enabled = body.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError("enabled 必须是 true/false")
        r = self.store.put(m, slot, kind, self._rule_value(m, kind, body, daemon), enabled)
        self.push_rules()
        return self._after(m, f"S{slot + 1} 规则已保存：{_short(r.label(), 16)}", daemon)

    def api_rule_update(self, rid: str, body: dict, daemon=None) -> dict:
        r = self.store.get(rid)
        if r is None:
            raise NotFound("规则不存在")
        kw = {}
        if "enabled" in body:
            kw["enabled"] = body["enabled"]
        if "kind" in body:
            if body["kind"] not in KINDS:
                raise ValueError("匹配方式无效")
            kw["kind"] = body["kind"]
        if "value" in body or "session" in body:
            kw["value"] = self._rule_value(r.mode, kw.get("kind", r.kind), body, daemon)
        r = self.store.update(rid, **kw)
        self.push_rules()
        return self._after(r.mode, f"S{r.slot + 1} 规则已{'启用' if r.enabled else '停用'}", daemon)

    def api_rule_delete(self, rid: str, daemon=None) -> dict:
        if self.store.get(rid) is None:
            raise NotFound("规则不存在")
        r = self.store.delete(rid)
        self.push_rules()
        return self._after(r.mode, f"S{r.slot + 1} 规则已删除", daemon)


# ---------------------------------------------------------------------- HTTP
_BUSY_BODY = json.dumps({"ok": False, "error": "连接太多，请稍后重试", "code": "busy"}, ensure_ascii=False).encode()
_BUSY_RESPONSE = (b"HTTP/1.1 503 Service Unavailable\r\nContent-Type: application/json; charset=utf-8\r\n"
                  b"Content-Length: " + str(len(_BUSY_BODY)).encode() + b"\r\nCache-Control: no-store\r\n"
                  b"Connection: close\r\n\r\n" + _BUSY_BODY)


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 16
    app: "ArrangeServer"

    def __init__(self, *a, max_connections: int = MAX_CONNECTIONS, **kw) -> None:
        self._slots = threading.BoundedSemaphore(max_connections)
        self.rejected = 0
        self.accepted = 0
        super().__init__(*a, **kw)

    def process_request(self, request, client_address):
        """At most ``max_connections`` handler threads; beyond that: an immediate 503, no thread."""
        if not self._slots.acquire(blocking=False):
            self.rejected += 1
            try:
                request.setblocking(False)
                request.send(_BUSY_RESPONSE)
            except OSError:
                pass
            self.shutdown_request(request)
            return
        self.accepted += 1
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def handle_error(self, request, client_address):  # a cut-off client is not worth a traceback
        log.debug("arrange http: request from %s failed", client_address, exc_info=True)


class _Reject(Exception):
    def __init__(self, status: int, error: str, code: str = "") -> None:
        super().__init__(error)
        self.status, self.error, self.code = status, error, code


_NOAUTH_PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>MK20 会话安排</title></head>
<body><h1>MK20 会话安排</h1><p>请在 Mac 上运行 <code>mk20deck arrange</code> 打开（安全起见）。</p>
<p><code>python -m mk20deck arrange</code> 会用默认浏览器打开一个一次性的链接。</p></body></html>"""
_AUTH_BAD_PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>MK20 会话安排</title></head>
<body><h1>链接已失效</h1><p>这个链接只能用一次，并且 60 秒内有效。请在 Mac 上重新运行
<code>mk20deck arrange</code>。</p></body></html>"""
_AUTH_OK_PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>MK20 会话安排</title>
<meta http-equiv="refresh" content="0;url=/"></head><body><p>已授权，正在打开…… <a href="/">继续</a></p></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    server: _Server
    server_version = "mk20deck-arrange"
    sys_version = ""
    protocol_version = "HTTP/1.1"  # keep-alive: tabs polling every 2 s reuse one connection each
    timeout = READ_TIMEOUT_S  # per read (setup re-reads the module value); requests: REQUEST_DEADLINE_S

    def version_string(self) -> str:
        return self.server_version

    def log_message(self, fmt, *args):  # quiet (stderr is the launchd log)
        log.debug("arrange http: " + fmt, *args)

    # -------------------------------------------------------------- deadline
    def setup(self) -> None:
        self.timeout = READ_TIMEOUT_S
        self._deadline: threading.Timer | None = None
        super().setup()

    def handle_one_request(self) -> None:
        """Wait (idle, at most READ_TIMEOUT_S) for the next request's first byte, then give the whole request a
        hard REQUEST_DEADLINE_S: a client that trickles bytes is cut off (reads fail, the thread ends)."""
        try:
            if not self.rfile.peek(1):
                self.close_connection = True  # client closed its keep-alive connection
                return
        except (OSError, ValueError):
            self.close_connection = True  # idle keep-alive limit / reset
            return
        self._deadline = threading.Timer(REQUEST_DEADLINE_S, self._expire)
        self._deadline.daemon = True
        self._deadline.start()
        try:
            super().handle_one_request()
        finally:
            self._deadline.cancel()

    def _expire(self) -> None:
        self.close_connection = True
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def finish(self) -> None:
        if self._deadline is not None:
            self._deadline.cancel()
        try:
            super().finish()
        except OSError:
            pass

    def handle(self) -> None:
        try:
            super().handle()
        except (OSError, ValueError):  # cut off by the deadline / client gone
            self.close_connection = True

    # -------------------------------------------------------------- responses
    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("X-MK20-Arrange", "1")
        if self.close_connection:
            self.send_header("Connection", "close")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        if "Content-Security-Policy" not in (extra or {}):
            self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, obj: dict, extra: dict | None = None) -> None:
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8",
                   extra)

    def _html(self, status: int, text: str, extra: dict | None = None) -> None:
        self._send(status, text.encode("utf-8"), "text/html; charset=utf-8", extra)

    def _discard(self) -> None:
        """Read (and drop) a small unread request body before closing, so the client gets our error response
        instead of a TCP reset.  Bounded: 64 KiB, 0.2 s."""
        if self.command not in ("POST", "PUT", "PATCH", "DELETE") or getattr(self, "_body_read", False):
            return
        try:
            self.connection.settimeout(0.2)
            if self.headers.get("Transfer-Encoding") is None:
                n = int(self.headers.get("Content-Length") or 0)
                if 0 < n <= 64 * 1024:
                    self.rfile.read(n)
                return
            left = 64 * 1024
            while left > 0:
                chunk = self.rfile.read1(min(left, 8192))
                if not chunk:
                    break
                left -= len(chunk)
        except (OSError, ValueError):
            pass
        finally:
            self._body_read = True

    def _fail(self, e: _Reject) -> None:
        self.close_connection = True
        self._discard()
        d = {"ok": False, "error": e.error}
        if e.code:
            d["code"] = e.code
        self._json(e.status, d)

    # -------------------------------------------------------------- checks
    @property
    def app(self) -> "ArrangeServer":
        return self.server.app

    def _path(self) -> tuple[str, str]:
        if len(self.path) > MAX_PATH:
            raise _Reject(414, "地址过长")
        path, _, query = self.path.partition("?")
        return path, query

    def _check_host(self) -> str:
        hosts = self.headers.get_all("Host") or []
        if len(hosts) != 1 or hosts[0] not in self.app.allowed_hosts:
            raise _Reject(403, "Host 不允许（只接受 127.0.0.1 / localhost 本机访问）", "host")
        return hosts[0]

    def _check_fetch_site(self) -> None:
        site = self.headers.get("Sec-Fetch-Site")
        if site is not None and site != "same-origin":
            raise _Reject(403, "跨站请求被拒绝", "origin")

    def _check_origin(self, host: str) -> None:
        origins = self.headers.get_all("Origin") or []
        if len(origins) != 1 or origins[0] != f"http://{host}":
            raise _Reject(403, "Origin 不匹配", "origin")
        self._check_fetch_site()

    def _cookie(self) -> str:
        cookie = http.cookies.SimpleCookie()
        for raw in self.headers.get_all("Cookie") or []:
            try:
                cookie.load(raw)
            except http.cookies.CookieError:
                pass
        m = cookie.get(self.app.cookie_name)
        return m.value if m is not None else ""

    def _session(self):
        return self.app.auth.lookup(self._cookie())

    def _check_session_csrf(self):
        s = self._session()
        if s is None:
            raise _Reject(403, "未授权或会话已过期：请在 Mac 上运行 mk20deck arrange", "auth")
        hdr = (self.headers.get("X-MK20-CSRF") or "").encode("utf-8", "replace")
        if not hmac.compare_digest(hdr, s.csrf.encode()):
            raise _Reject(403, "页面令牌无效或已过期，请刷新页面", "csrf")
        return s

    def _body(self) -> dict:
        if self.headers.get("Transfer-Encoding") is not None:
            raise _Reject(411, "需要 Content-Length（不支持分块传输）")
        raw_len = self.headers.get_all("Content-Length") or []
        if len(raw_len) != 1:
            raise _Reject(411, "需要 Content-Length")
        try:
            n = int(raw_len[0])
        except ValueError:
            raise _Reject(400, "Content-Length 无效") from None
        if n < 0:
            raise _Reject(400, "Content-Length 无效")
        if n > MAX_BODY:
            raise _Reject(413, f"请求过大（最多 {MAX_BODY} 字节）")
        ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            raise _Reject(415, "只接受 application/json")
        raw = self.rfile.read(n) if n else b""
        self._body_read = True
        if len(raw) != n:
            raise _Reject(400, "请求体不完整")
        try:
            obj = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise _Reject(400, "JSON 无效") from None
        if not isinstance(obj, dict):
            raise _Reject(400, "JSON 必须是对象")
        return obj

    def _run(self, fn: Callable):
        try:
            return self.app.service.calls.submit(fn)
        except LoopBusy:
            raise _Reject(503, "键盘程序忙，请稍后重试", "busy") from None
        except LoopTimeout as e:
            if e.started:  # not "busy": the page must not retry a change that may have happened
                raise _Reject(503, "键盘程序忙：改动可能已生效，请刷新确认", "maybe") from None
            raise _Reject(503, "键盘程序忙（可能正在连接），请稍后重试", "busy") from None
        except NotFound as e:
            raise _Reject(404, str(e) or "不存在") from None
        except Conflict as e:
            raise _Reject(409, str(e), "conflict") from None
        except RulesSaveError as e:
            raise _Reject(500, str(e), "save") from None
        except (ValueError, IndexError) as e:
            raise _Reject(400, str(e) or "参数无效") from None

    # -------------------------------------------------------------- methods
    GET_ROUTES = ("/", "/auth", "/api/state", "/favicon.ico")

    def _allowed(self, path: str) -> str:
        if path in self.GET_ROUTES:
            return "GET"
        if path in ("/api/move", "/api/unpin", "/api/clear", "/api/rules") or \
                re.fullmatch(r"/api/rules/[^/]+/delete", path):
            return "POST"
        if re.fullmatch(r"/api/rules/[^/]+", path):
            return "PUT"
        return ""

    def _dispatch(self) -> None:
        self._body_read = False
        try:
            host = self._check_host()
            path, query = self._path()
            allowed = self._allowed(path)
            if not allowed:
                raise _Reject(404, "没有这个地址")
            if self.command != allowed:
                self.close_connection = True
                self._discard()
                self._json(405, {"ok": False, "error": f"只允许 {allowed}"}, {"Allow": allowed})
                return
            if allowed == "GET":
                self._get(path, query)
            else:
                self._check_origin(host)
                self._check_session_csrf()
                self._mutate(path, self._body())
        except _Reject as e:
            self._fail(e)
        except (OSError, ValueError) as e:  # socket cut off (deadline) / client gone: nothing to answer
            log.debug("arrange http: %s", e)
            self.close_connection = True
        except Exception:  # noqa: BLE001 — never kill the server thread on a bug
            log.exception("arrange web: request failed")
            self._fail(_Reject(500, "内部错误（见 mk20deck 日志）"))

    def _get(self, path: str, query: str) -> None:
        if path == "/favicon.ico":
            self._send(204, b"", "text/plain")
            return
        if path == "/auth":
            t = (parse_qs(query, max_num_fields=4).get("t") or [""])[0]
            if not self.app.auth.redeem(t):
                self._html(403, _AUTH_BAD_PAGE)
                return
            sid, _ = self.app.auth.create()
            log.info("会话安排网页：已授权一个浏览器会话")
            self._html(200, _AUTH_OK_PAGE, {"Set-Cookie": self.app.cookie(sid)})
            return
        if path == "/":
            s = self._session()
            if s is None:
                self._html(403, _NOAUTH_PAGE)
                return
            sid = self.app.auth.rotate(s)
            nonce = secrets.token_urlsafe(16)
            body = self.app.page(nonce, s.csrf).encode("utf-8")
            csp = (f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
                   "connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'; "
                   "frame-ancestors 'none'")
            self._send(200, body, "text/html; charset=utf-8",
                       {"Content-Security-Policy": csp, "Set-Cookie": self.app.cookie(sid)})
            return
        # /api/state
        self._check_fetch_site()
        self._check_session_csrf()
        svc = self.app.service
        try:
            snap = svc.calls.submit(lambda d: svc.snapshot(d), STATE_TIMEOUT_S)
        except (LoopTimeout, LoopBusy):
            cached = svc.cache
            if cached is None:
                raise _Reject(503, "键盘程序忙（可能正在连接），请稍后重试", "busy") from None
            snap = dict(cached, stale=True)
        self._json(200, snap)

    def _mutate(self, path: str, body: dict) -> None:
        svc = self.app.service
        if path == "/api/move":
            fn = lambda d: svc.api_move(body, d)  # noqa: E731
        elif path == "/api/unpin":
            fn = lambda d: svc.api_unpin(body, d)  # noqa: E731
        elif path == "/api/clear":
            fn = lambda d: svc.api_clear(body, d)  # noqa: E731
        elif path == "/api/rules":
            fn = lambda d: svc.api_rule_put(body, d)  # noqa: E731
        else:
            parts = path.split("/")  # /api/rules/<id>[/delete]
            rid = parts[3]
            if not RULE_ID_RE.match(rid):
                raise _Reject(404, "规则不存在")
            if len(parts) == 5:
                fn = lambda d: svc.api_rule_delete(rid, d)  # noqa: E731
            else:
                fn = lambda d: svc.api_rule_update(rid, body, d)  # noqa: E731
        self._json(200, self._run(fn))

    do_GET = do_POST = do_PUT = _dispatch

    def _no(self) -> None:
        self._body_read = False
        try:
            self._check_host()
            path, _ = self._path()
        except _Reject as e:
            self._fail(e)
            return
        allowed = self._allowed(path) or "GET"
        self.close_connection = True
        self._discard()
        self._json(405, {"ok": False, "error": "方法不允许"}, {"Allow": allowed})

    do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = do_TRACE = do_CONNECT = _no


class ArrangeServer:
    """The HTTP side + the local authorization channel.  ``start`` returns False (one warning, the daemon keeps
    running) if the port is taken."""

    def __init__(self, service: ArrangeService, port: int, host: str = "127.0.0.1", poll_interval: float = 0.5,
                 sessions_path: str | None = None, socket_path: str | None = None,
                 max_connections: int = MAX_CONNECTIONS) -> None:
        self.service = service
        self.host = host
        self.port = port
        self.poll_interval = poll_interval  # serve_forever shutdown check
        self.max_connections = max_connections
        self.auth = AuthStore(sessions_path, link_dir=os.path.dirname(socket_path) if socket_path else None)
        self.socket_path = socket_path
        self.auth_socket: AuthSocket | None = None
        self.httpd: _Server | None = None
        self.thread: threading.Thread | None = None
        self.allowed_hosts: frozenset[str] = frozenset()
        self.cookie_name = ""

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def mint_url(self) -> str:
        """A one-time authorization link (60 s, single use) — for ``mk20deck arrange`` and in-process callers
        such as a future keyboard link."""
        return f"http://127.0.0.1:{self.port}/auth?t={self.auth.mint()}"

    def mint_file(self) -> str:
        """A one-time link in a 0600 file of the 0700 run directory; returns the path for ``open``."""
        return self.auth.mint_file(lambda t: f"http://127.0.0.1:{self.port}/auth?t={t}")

    def cookie(self, sid: str) -> str:
        return f"{self.cookie_name}={sid}; Path=/; HttpOnly; SameSite=Strict; Max-Age={int(SESSION_TTL_S)}"

    def start(self) -> bool:
        try:
            httpd = _Server((self.host, self.port), _Handler, max_connections=self.max_connections)
        except OSError as e:
            log.warning("会话安排网页未启动：%s:%s 不可用（%s）；守护进程照常运行", self.host, self.port,
                        e.strerror or e)
            return False
        httpd.app = self
        self.httpd = httpd
        self.port = httpd.server_address[1]
        self.allowed_hosts = frozenset({f"127.0.0.1:{self.port}", f"localhost:{self.port}"})
        self.cookie_name = f"mk20arr_{self.port}"
        self.thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": self.poll_interval},
                                       name="mk20-arrange-web", daemon=True)
        self.thread.start()
        if self.socket_path:
            self.auth_socket = AuthSocket(self.socket_path, self.mint_url, mint_file=self.mint_file,
                                          page_url=self.url, on_tick=self.auth.prune)
            if self.auth_socket.start():
                self.auth.clear_links()  # stale link files of an earlier run (we own the run dir now)
            else:
                self.auth_socket = None
        log.info("会话安排网页：%s（用 mk20deck arrange 打开）", self.url)
        return True

    def stop(self) -> None:
        if self.auth_socket is not None:
            self.auth_socket.stop()
            self.auth_socket = None
            self.auth.clear_links()
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None

    def page(self, nonce: str, csrf: str) -> str:
        return render_page(nonce, csrf)


def start_arrange_server(service: ArrangeService, port: int, sessions_path: str | None = None,
                         socket_path: str | None = None) -> ArrangeServer | None:
    """``[web] arrange_port``: 0 = off; a busy port logs one warning and returns None."""
    if not port:
        log.info("会话安排网页已关闭（[web] arrange_port = 0）")
        return None
    srv = ArrangeServer(service, port, sessions_path=sessions_path, socket_path=socket_path)
    return srv if srv.start() else None


def probe(port: int, timeout: float = 2.0) -> str:
    """CLI: "ok" if our page answers on 127.0.0.1:port, "other" if something else does, "down" if nothing."""
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        c.request("GET", "/favicon.ico", headers={"Host": f"127.0.0.1:{port}"})
        r = c.getresponse()
        r.read()
        c.close()
    except OSError:
        return "down"
    except http.client.HTTPException:
        return "other"
    return "ok" if r.getheader("X-MK20-Arrange") == "1" else "other"


# ---------------------------------------------------------------------- page
def _page_consts() -> str:
    states = {st.value: {"color": _hex(STATE_COLOR[st]), "text": STATE_TEXT[st],
                         "ink": "#ffffff" if st in _DARK_INK else "#000000"} for st in State}
    modes = {m.value: {"name": MODE_NAME[m], "color": _hex(MODE_STRIP[m])} for m in MODE_CYCLE}
    kinds = {k: KIND_TEXT[k] for k in KINDS}
    return json.dumps({"states": states, "modes": modes, "kinds": kinds, "tools": list(RULE_TOOLS),
                       "n": N_SLOTS}, ensure_ascii=False).replace("<", "\\u003c")


def render_page(nonce: str, token: str) -> str:
    return (PAGE.replace("__NONCE__", html.escape(nonce, quote=True))
            .replace("__TOKEN__", html.escape(token, quote=True))
            .replace("__CONSTS__", _page_consts()))


PAGE = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="mk20-csrf" content="__TOKEN__">
<title>MK20 会话安排</title>
<style nonce="__NONCE__">
:root{color-scheme:dark;--bg:#0e1014;--key:#16191e;--cmd:#20242a;--line:#2a2f37;--text:#eceff4;--dim:#8a909a;
--amber:#f5b43c;--m:#8b5cf6}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
font:14px/1.45 -apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB","Noto Sans CJK SC",sans-serif}
button{font:inherit;color:inherit}
header{display:flex;align-items:center;gap:12px;padding:12px 20px;border-bottom:1px solid var(--line)}
h1{font-size:18px;margin:0}
h2{font-size:15px;margin:18px 0 8px}
h3{font-size:13px;margin:14px 0 6px;color:var(--dim);font-weight:600}
.pill{padding:2px 9px;border-radius:10px;font-size:12px;background:#333}
.pill.ok{background:#14532d;color:#bbf7d0}.pill.bad{background:#7f1d1d;color:#fecaca}.pill.sim{background:#1e3a8a;color:#bfdbfe}
#upd{color:var(--dim);font-size:12px;margin-left:auto}
.warn{margin:10px 20px 0;padding:8px 12px;border-radius:8px;background:#422006;color:#fde68a;border:1px solid #a16207}
.err{margin:10px 20px 0;padding:8px 12px;border-radius:8px;background:#450a0a;color:#fecaca;border:1px solid #b91c1c}
.hidden{display:none!important}
nav{display:flex;gap:6px;padding:14px 20px 0;flex-wrap:wrap}
.tab{background:var(--cmd);border:0;border-bottom:3px solid transparent;padding:8px 14px;border-radius:8px 8px 0 0;cursor:pointer}
.tab.on{background:#2d323b;border-bottom-color:var(--m)}
.tab .cur{margin-left:6px;font-size:11px;padding:1px 6px;border-radius:8px;background:var(--m);color:#000;font-weight:600}
.tab .na{margin-left:6px;font-size:11px;color:#fca5a5}
#modeinfo{padding:8px 20px 0;color:var(--dim);font-size:12px}
main{display:flex;gap:22px;padding:12px 20px 40px;align-items:flex-start;flex-wrap:wrap}
.grid{display:grid;grid-template-columns:repeat(5,116px);grid-auto-rows:116px;gap:10px;padding:12px;background:#0a0c0f;
border-radius:14px;border-left:6px solid var(--m)}
.slot{position:relative;background:var(--key);border:3px solid var(--c,#34383e);border-radius:14px;padding:6px 8px;
cursor:pointer;display:flex;flex-direction:column;overflow:hidden;text-align:left;min-width:0}
.slot:hover{filter:brightness(1.15)}
.slot:focus-visible{outline:2px solid #fff;outline-offset:2px}
.slot.picked{box-shadow:0 0 0 3px #f472b6}
.slot.selected .t{background:#fff;color:#000;border-radius:4px;padding:0 3px}
.slot.att{background:var(--att,var(--key))}
.slot.empty{border-color:#34383e;color:#6e737a}
.slot.offline{border-color:#6b7280;color:#9ca3af}
.slot.waiting{border:3px dashed var(--amber);background:#1b1710}
.slot.lost{border:2px solid #96783c;color:#dcbe78}
.slot .no{font-size:11px;color:var(--dim);font-weight:600}
.slot .top{display:flex;align-items:center;gap:3px;min-width:0}
.slot .top .no{margin-right:auto}
.b{font-size:10px;padding:0 5px;border-radius:7px;background:#3a3f48;color:#fff;line-height:16px;white-space:nowrap}
.b.pin{background:#e5e7eb;color:#000}.b.rule{background:var(--amber);color:#000}
.b.off{background:#4b5563;color:#d1d5db;text-decoration:line-through}
.slot .t{font-weight:700;font-size:15px;margin-top:6px;word-break:break-all;overflow:hidden;max-height:44px}
.slot .m{font-size:11px;color:var(--dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.slot .w{margin-top:auto;font-size:12px;color:var(--amber);font-weight:600;word-break:break-all;max-height:34px;overflow:hidden}
.slot .e{margin:auto 0;text-align:center;font-size:14px}
.chip{display:inline-block;font-size:12px;font-weight:700;padding:0 8px;border-radius:9px;background:var(--c);color:var(--ink);line-height:18px}
.slot .chip{margin-top:auto;align-self:flex-start}
.hint{color:var(--dim);font-size:12px;max-width:660px}
.panel{width:400px;max-width:100%;background:#14171c;border:1px solid var(--line);border-radius:14px;padding:14px 16px}
.panel .hd{display:flex;align-items:center;gap:8px}
.panel .hd b{font-size:16px}
.x{margin-left:auto;background:none;border:0;font-size:18px;cursor:pointer;color:var(--dim)}
.btn{background:#2d323b;border:1px solid #3d434d;border-radius:8px;padding:4px 10px;cursor:pointer;font-size:13px}
.btn:hover{background:#3a404a}.btn:disabled{opacity:.45;cursor:default}
.btn.pri{background:#2563eb;border-color:#2563eb}.btn.dan{background:#7f1d1d;border-color:#991b1b}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.cur-occ{margin:8px 0;color:var(--dim)}
.cur-occ b{color:var(--text)}
.list{display:flex;flex-direction:column;gap:6px;max-height:340px;overflow:auto}
.sess{display:flex;gap:8px;align-items:center;padding:6px 8px;border-radius:8px;background:#1b1f25;border:1px solid transparent}
.sess.src{border-color:var(--amber)}
.sess .info{flex:1;min-width:0}
.sess .tt{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sess .mm{font-size:11px;color:var(--dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.dot{width:10px;height:10px;border-radius:50%;background:var(--c);flex:none}
.rulebox{padding:8px 10px;border-radius:8px;background:#1f1b12;border:1px solid #6b5418;margin-bottom:8px}
.rulebox.off{background:#1b1f25;border-color:#3d434d;color:var(--dim)}
.rulebox .st{font-size:12px;color:var(--dim);margin:2px 0 6px}
label.f{display:block;font-size:12px;color:var(--dim);margin:8px 0 3px}
select,input[type=text]{width:100%;background:#0e1116;border:1px solid #3d434d;border-radius:7px;padding:6px 8px;color:var(--text);font:inherit}
.small{font-size:12px;color:var(--dim)}
table{border-collapse:collapse;width:100%;max-width:660px;font-size:13px}
td,th{border-bottom:1px solid var(--line);padding:6px 6px;text-align:left;vertical-align:top}
th{color:var(--dim);font-weight:600}
.toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);background:#f5f5f5;color:#111;padding:8px 16px;
border-radius:10px;font-weight:600;box-shadow:0 6px 20px #0008;max-width:80vw}
.toast.bad{background:#fecaca}
</style></head>
<body>
<header><h1>MK20 会话安排</h1><span id="conn" class="pill">连接中…</span><span id="upd"></span></header>
<div id="warn" class="warn hidden"></div>
<div id="err" class="err hidden"></div>
<nav id="tabs" role="tablist"></nav>
<div id="modeinfo"></div>
<main>
 <section>
  <div id="grid" class="grid" aria-label="L1 会话键 S1–S15"></div>
  <p class="hint">和键盘 L1 前三排一样：S1–S5 / S6–S10 / S11–S15。点一个键，在右侧安排。
  优先级：固定规则 &gt; 手动钉住 &gt; 自动填充。键盘上把会话移入/移出规则键（或切换钉住）会停用那条规则。</p>
  <h2>本模式的固定规则</h2>
  <table><thead><tr><th>键</th><th>规则</th><th>状态</th><th></th></tr></thead><tbody id="rules"></tbody></table>
  <p id="norules" class="small">还没有规则。点一个键 → “固定规则”。</p>
 </section>
 <aside id="panel" class="panel hidden" aria-live="polite">
  <div class="hd"><b id="p-title">S1</b><span id="p-mode" class="small"></span><button id="p-close" class="x" title="关闭 (Esc)">×</button></div>
  <div id="p-occ" class="cur-occ"></div>
  <div class="row"><button id="p-unpin" class="btn">取消钉住</button><button id="p-clear" class="btn">清空此键</button></div>
  <p class="small">清空后此键交回自动填充（没有别的会话等着上键时，可能又放回原来的会话）。</p>
  <h3>放到这里（移动并钉住；目标键已有会话时互换）</h3>
  <div id="p-list" class="list"></div>
  <p id="p-nosess" class="small hidden">此模式现在没有会话。</p>
  <h3>固定规则</h3>
  <div id="p-rule"></div>
  <label class="f" for="f-kind">匹配方式</label>
  <select id="f-kind"></select>
  <label class="f" for="f-val">匹配值</label>
  <input id="f-val" type="text" maxlength="200" autocomplete="off" spellcheck="false">
  <div id="f-hint" class="small"></div>
  <div class="row"><label class="small"><input id="f-en" type="checkbox" checked> 启用</label>
   <button id="f-save" class="btn pri">保存规则</button></div>
  <p class="small">点会话旁的“用作规则”可自动填入匹配值。规则会让匹配的会话自动出现在这个键上（钉住）；
  没有匹配会话时此键显示“等待：…”并且不会被自动填充占用。同一会话匹配多条规则时放在编号最小的键；
  多个会话匹配同一规则时，最近活动的那个占这个键，其余照常自动填充。</p>
 </aside>
</main>
<div id="toast" class="toast hidden"></div>
<script nonce="__NONCE__">
'use strict';
const C = __CONSTS__;
const TOKEN = document.querySelector('meta[name="mk20-csrf"]').content;
const $ = (id) => document.getElementById(id);
let S = null, tab = null, sel = null, src = null, busy = false, tokenDead = false, fails = 0;
const KHINT = {cwd: '目录前缀：会话目录等于它或在它下面。点“用作规则”取所选会话的目录（网页只显示目录名），也可输入 /… 或 ~/… 路径。',
  window: 'tmux 窗口名或会话名，完全一致；含 * ? 时按通配符匹配。',
  title: '标题包含这段文字（不区分大小写），适合 Codex 桌面线程。',
  tool: 'ccai / codex / kimi 之一。'};

function h(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = String(text);
  return e;
}
function stateVars(e, st) {
  const s = C.states[st] || C.states.unknown;
  e.style.setProperty('--c', s.color);
  e.style.setProperty('--ink', s.ink);
  return s;
}
function toast(msg, bad) {
  const t = $('toast');
  t.textContent = msg; t.className = 'toast' + (bad ? ' bad' : '');
  clearTimeout(toast.tm); toast.tm = setTimeout(() => t.classList.add('hidden'), bad ? 4000 : 2200);
}
async function api(method, path, body) {
  const opt = {method, headers: {'X-MK20-CSRF': TOKEN}, credentials: 'same-origin', cache: 'no-store'};
  if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  let j = null;
  try { j = await r.json(); } catch (e) { j = null; }
  if (!r.ok || !j || j.ok === false) {
    const err = new Error((j && j.error) || ('HTTP ' + r.status));
    err.code = j && j.code;
    throw err;
  }
  return j;
}
function showErr(msg) { const e = $('err'); if (msg) { e.textContent = msg; e.classList.remove('hidden'); } else e.classList.add('hidden'); }
function modeData() { return S && S.modes.find((m) => m.mode === tab); }

function deadToken() {
  tokenDead = true;
  // mk20deck restarted (new token): reload once to get a fresh page + cookie, never in a loop
  if (!sessionStorage.getItem('mk20-reloaded')) { sessionStorage.setItem('mk20-reloaded', '1'); location.reload(); return; }
  showErr('授权已失效：请在 Mac 上运行 mk20deck arrange 重新打开。');
}
async function poll() {
  if (tokenDead) return;
  try {
    S = await api('GET', '/api/state');
    sessionStorage.removeItem('mk20-reloaded');
    fails = 0;
    showErr(S.stale ? '键盘程序忙，显示的是上次的状态…' : '');
    render();
  } catch (e) {
    fails += 1;
    if (e.code === 'csrf' || e.code === 'auth') deadToken();
    else if ((e.code === 'busy' || !e.code) && fails < 3) { /* transient (busy / dropped): retry quietly */ }
    else { showErr('连不上 mk20deck：' + e.message + '（稍后重试）'); setConn(null); }
  } finally { if (!tokenDead) setTimeout(poll, fails ? Math.min(2000 * fails, 8000) : 2000); }
}
function setConn(s) {
  const c = $('conn');
  if (!s) { c.className = 'pill bad'; c.textContent = '未连接 mk20deck'; return; }
  const m = {connected: ['ok', '键盘已连接'], sim: ['sim', '模拟器'], disconnected: ['bad', '键盘未连接']}[s.link] || ['', s.link];
  c.className = 'pill ' + m[0]; c.textContent = m[1];
  $('upd').textContent = '更新 ' + s.updated;
}
async function act(method, path, body) {
  if (busy) return;
  busy = true; document.body.style.setProperty('cursor', 'progress');
  try {
    let j = null;
    for (let i = 0; ; i++) {  // "busy" = refused before anything happened: safe to retry; never "maybe"
      try { j = await api(method, path, body); break; }
      catch (e) { if (e.code !== 'busy' || i >= 2) throw e; await new Promise((r) => setTimeout(r, 400 * (i + 1))); }
    }
    if (j.state) { S = j.state; render(); }
    toast(j.message || '完成');
  } catch (e) {
    if (e.code === 'csrf' || e.code === 'auth') deadToken();
    else if (e.code === 'conflict') { try { S = await api('GET', '/api/state'); render(); } catch (e2) { /* next poll */ } }
    toast(e.message, true);
  } finally { busy = false; document.body.style.removeProperty('cursor'); }
}

function render() {
  if (!S) return;
  setConn(S);
  const w = $('warn');
  if (S.warnings && S.warnings.length) { w.textContent = '注意：' + S.warnings.join('；'); w.classList.remove('hidden'); }
  else w.classList.add('hidden');
  if (!tab) tab = S.current_mode;
  renderTabs(); renderGrid(); renderRules(); renderPanel();
}
function renderTabs() {
  const nav = $('tabs'); nav.replaceChildren();
  for (const m of S.modes) {
    const b = h('button', 'tab' + (m.mode === tab ? ' on' : ''), m.name);
    b.setAttribute('role', 'tab');
    b.style.setProperty('--m', C.modes[m.mode].color);
    if (m.current) b.appendChild(h('span', 'cur', '键盘当前'));
    if (!m.ok) b.appendChild(h('span', 'na', '不可用'));
    b.onclick = () => { if (tab !== m.mode) { tab = m.mode; sel = null; src = null; render(); } };
    nav.appendChild(b);
  }
  const md = modeData();
  document.documentElement.style.setProperty('--m', C.modes[tab].color);
  const src_ = {live: '实时（每 2 秒）', background: '后台轮询（约每 10 秒）；切到此模式后按这个安排显示', stale: '上次切到此模式时的状态（等后台轮询更新）'}[md.source] || '';
  $('modeinfo').textContent = (md.ok ? '' : '不可用：' + md.reason + ' · ') + '数据：' + src_ +
    (md.overflow ? ' · 另有 ' + md.overflow + ' 个会话没有上键' : '');
}
function renderGrid() {
  const g = $('grid'), md = modeData(); g.replaceChildren();
  for (const s of md.slots) {
    const e = h('button', 'slot ' + s.kind);
    e.type = 'button';
    e.setAttribute('aria-label', s.name);
    if (sel === s.slot) e.classList.add('picked');
    if (s.selected) e.classList.add('selected');
    const top = h('div', 'top');
    top.appendChild(h('span', 'no', s.name));
    if (s.pinned) top.appendChild(h('span', 'b pin', s.kind === 'lost' ? '失联' : '钉住'));
    if (s.rule) {
      const rb = h('span', 'b ' + (s.rule.enabled ? 'rule' : 'off'), '规则');
      rb.title = s.rule.label + (s.rule.enabled ? '' : '（已停用）');
      top.appendChild(rb);
    }
    e.appendChild(top);
    if (s.kind === 'session') {
      const st = stateVars(e, s.state);
      if (['needs_approval', 'needs_input', 'error', 'done_unread'].includes(s.state))
        { e.classList.add('att'); e.style.setProperty('--att', 'color-mix(in srgb, #16191e 80%, ' + st.color + ')'); }
      e.appendChild(h('div', 't', s.title));
      e.appendChild(h('div', 'm', [s.tool, s.window || s.dir].filter(Boolean).join(' · ')));
      e.appendChild(h('span', 'chip', st.text));
    } else if (s.kind === 'waiting') {
      e.appendChild(h('div', 'w', s.waiting));
    } else if (s.kind === 'lost') {
      e.appendChild(h('div', 'e', '失联钉扎'));
    } else {
      e.appendChild(h('div', 'e', s.kind === 'offline' ? '离线' : C.states.unassigned.text));
    }
    e.title = s.kind === 'session' ? s.title : s.name;
    e.onclick = () => { if (sel !== s.slot) { $('f-val').value = ''; $('f-val').dataset.session = ''; }
      sel = s.slot; src = null; prefill(); render(); };
    g.appendChild(e);
  }
}
function ruleStatus(r) {
  if (r.status === 'placed') return '生效中 → ' + r.session;
  if (r.status === 'waiting') return '等待匹配的会话（此键保留）';
  if (r.status === 'pending') return '下次刷新生效';
  return '已停用' + (r.note ? '：' + r.note : '');
}
function renderRules() {
  const tb = $('rules'), md = modeData(); tb.replaceChildren();
  $('norules').classList.toggle('hidden', md.rules.length > 0);
  for (const r of md.rules) {
    const tr = h('tr');
    tr.appendChild(h('td', '', r.name));
    tr.appendChild(h('td', '', r.label));
    tr.appendChild(h('td', r.enabled ? '' : 'small', ruleStatus(r)));
    const td = h('td'); td.appendChild(ruleButtons(r)); tr.appendChild(td);
    tb.appendChild(tr);
  }
}
function ruleButtons(r) {
  const row = h('div', 'row');
  const t = h('button', 'btn', r.enabled ? '停用' : '启用');
  t.onclick = () => act('PUT', '/api/rules/' + encodeURIComponent(r.id), {enabled: !r.enabled});
  const d = h('button', 'btn dan', '删除');
  d.onclick = () => { if (confirm('删除 ' + r.name + ' 的规则“' + r.label + '”？')) act('POST', '/api/rules/' + encodeURIComponent(r.id) + '/delete', {}); };
  row.appendChild(t); row.appendChild(d);
  return row;
}
function renderPanel() {
  const p = $('panel'), md = modeData();
  if (sel === null || !md) { p.classList.add('hidden'); return; }
  p.classList.remove('hidden');
  const s = md.slots[sel];
  $('p-title').textContent = s.name;
  $('p-mode').textContent = md.name + (md.current ? '（键盘当前模式）' : '');
  const occ = $('p-occ'); occ.replaceChildren();
  if (s.kind === 'session') {
    occ.appendChild(document.createTextNode('现在：'));
    occ.appendChild(h('b', '', s.title));
    occ.appendChild(document.createTextNode(' · ' + s.tool + ' · ' + C.states[s.state].text + (s.pinned ? ' · 钉住' : ' · 自动') + (s.by_rule ? '（规则）' : '')));
  } else occ.textContent = '现在：' + ({waiting: s.waiting, lost: '失联钉扎（会话找不到了）', offline: '离线', empty: '空槽'}[s.kind] || s.kind);
  $('p-unpin').disabled = !(s.pinned);
  $('p-clear').disabled = (s.kind === 'empty' || s.kind === 'offline') && !s.rule;
  const list = $('p-list'); list.replaceChildren();
  $('p-nosess').classList.toggle('hidden', md.sessions.length > 0);
  for (const x of md.sessions) {
    const row = h('div', 'sess' + (src === x.key ? ' src' : ''));
    const dot = h('span', 'dot'); const st = stateVars(dot, x.state); row.appendChild(dot);
    const info = h('div', 'info');
    info.appendChild(h('div', 'tt', x.title));
    const where = x.slot === null || x.slot === undefined ? '未上键' : '在 S' + (x.slot + 1);
    const loc = x.window ? 'tmux ' + (x.tmux_session ? x.tmux_session + ':' : '') + x.window : x.dir;
    info.appendChild(h('div', 'mm', [where, st.text, x.tool, loc].filter(Boolean).join(' · ')));
    row.appendChild(info);
    const put = h('button', 'btn pri', x.slot === sel ? '已在此键' : '放到这里');
    put.disabled = x.slot === sel && s.pinned;
    put.onclick = () => act('POST', '/api/move', {mode: tab, slot: sel, key: x.key, expect: s.key});
    const use = h('button', 'btn', '用作规则');
    use.onclick = () => { src = x.key; prefill(); renderPanel(); };
    row.appendChild(put); row.appendChild(use);
    list.appendChild(row);
  }
  const box = $('p-rule'); box.replaceChildren();
  if (s.rule) {
    const r = s.rule, b = h('div', 'rulebox' + (r.enabled ? '' : ' off'));
    b.appendChild(h('div', '', r.label)); b.appendChild(h('div', 'st', ruleStatus(r))); b.appendChild(ruleButtons(r));
    box.appendChild(b);
    $('f-save').textContent = '替换规则';
  } else $('f-save').textContent = '保存规则';
  const kinds = md.kinds, ks = $('f-kind');
  if (ks.dataset.mode !== tab) {
    ks.replaceChildren();
    for (const k of kinds) { const o = h('option', '', C.kinds[k]); o.value = k; ks.appendChild(o); }
    ks.dataset.mode = tab; prefill();
  }
}
function prefill() {
  const md = modeData(); if (!md) return;
  const kind = $('f-kind').value, v = $('f-val');
  $('f-hint').textContent = KHINT[kind] || '';
  v.placeholder = kind === 'cwd' ? '/… 或 ~/…，或点会话的“用作规则”' : (kind === 'tool' ? C.tools.join(' / ') : '');
  const x = src ? md.sessions.find((s) => s.key === src) : null;
  if (!x) { if (v.dataset.session) v.value = ''; v.dataset.session = ''; return; }  // keep what the user typed
  v.dataset.session = '';
  if (kind === 'cwd') { v.value = x.dir ? '（所选会话的目录）' + x.dir : ''; v.dataset.session = x.dir ? x.key : ''; }
  else if (kind === 'window') v.value = x.window || x.tmux_session || '';
  else if (kind === 'tool') v.value = x.tool;
  else v.value = x.title;
}
$('f-kind').onchange = prefill;
$('f-val').oninput = () => { $('f-val').dataset.session = ''; };
$('f-save').onclick = () => {
  if (sel === null) return;
  const kind = $('f-kind').value, v = $('f-val');
  const body = {mode: tab, slot: sel, kind, enabled: $('f-en').checked};
  if (v.dataset.session) body.session = v.dataset.session; else body.value = v.value.trim();
  if (!body.session && !body.value) { toast('请填写匹配值，或点会话的“用作规则”', true); return; }
  act('POST', '/api/rules', body);
};
function slotKey() { const md = modeData(); return md && sel !== null ? md.slots[sel].key : null; }
$('p-unpin').onclick = () => act('POST', '/api/unpin', {mode: tab, slot: sel, expect: slotKey()});
$('p-clear').onclick = () => act('POST', '/api/clear', {mode: tab, slot: sel, expect: slotKey()});
$('p-close').onclick = () => { sel = null; src = null; render(); };
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && sel !== null) { sel = null; src = null; render(); } });
poll();
</script>
</body></html>
"""
