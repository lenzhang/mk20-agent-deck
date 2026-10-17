"""Codex Desktop adapter (default mode) — read-only, per SPIKE-codex-desktop.md §3.

Sources (all read-only, never written):
  * ``~/.codex/state_5.sqlite`` table ``threads`` (``mode=ro``): list, title, cwd, effort.
  * rollout JSONL tail (last 64 KiB) of slot/candidate threads: coarse lifecycle.
  * ``~/.codex/.codex-global-state.json`` → ``electron-thread-read-state-v1``
    ``unreadByIdentity.*.*`` (union) ∩ local threads: done-unread.
  * app liveness (``pgrep``) → offline when the App is not running.
Actions: ``open codex://threads/<id>`` (focus) and
``open codex://threads/new?path=&prompt=`` (new / skill prefill, never submits).
Not used: app-server, ipc.sock, control.sock, JS injection (PRD §2.2/§6.9).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Callable, Sequence

from ..model import ActionResult, Capabilities, Mode, Session, SessionId, State, Tool
from .base import BackendUnavailable, ModeAdapter, Runner, SubprocessRunner

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
REQUIRED_COLUMNS = {"id", "rollout_path", "source", "cwd", "title", "archived"}
OPTIONAL_COLUMNS = ("name", "preview", "originator", "reasoning_effort", "recency_at_ms", "updated_at",
                    "is_pinned", "model")
LIFECYCLE = {"task_started", "task_complete", "turn_aborted", "error"}
TAIL_BYTES = 64 * 1024
DEFAULT_ORIGINATORS = ("Codex Desktop", "codex_work_desktop", "")


class SchemaChanged(BackendUnavailable):
    pass


@dataclass
class Lifecycle:
    kind: str | None  # last lifecycle event type, or None
    ts: str = ""
    turn_id: str = ""
    reason: str = ""


def parse_rollout_tail(data: bytes, from_start: bool) -> Lifecycle:
    """Find the last lifecycle event in a rollout tail.  The first line is dropped
    unless the read started at offset 0 (it may be cut mid-record)."""
    text = data.decode("utf-8", "replace")
    lines = text.splitlines()
    if not from_start and lines:
        lines = lines[1:]
    last = Lifecycle(None)
    for line in lines:
        if '"event_msg"' not in line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("type") != "event_msg":
            continue
        p = d.get("payload") or {}
        t = p.get("type")
        if t in LIFECYCLE:
            last = Lifecycle(t, str(d.get("timestamp", "")), str(p.get("turn_id", "")),
                             str(p.get("reason", "") or p.get("message", ""))[:80])
    return last


def build_thread_url(thread_id: str) -> str:
    if not UUID_RE.match(thread_id):
        raise ValueError("threadId 格式不可信")
    return f"codex://threads/{thread_id}"


def build_new_thread_url(path: str | None, prompt: str | None, max_prompt_chars: int, max_url_len: int) -> str:
    """codex://threads/new?path=<abs>&prompt=<text>.  Fails (never truncates) on
    over-long prompts, unsafe characters or non-absolute paths (PRD §6.2/G65)."""
    q: list[tuple[str, str]] = []
    if path is not None:
        if not os.path.isabs(path) or "\x00" in path:
            raise ValueError("项目路径不明")
        q.append(("path", path))
    if prompt is not None:
        if len(prompt) > max_prompt_chars:
            raise ValueError(f"提示过长（{len(prompt)}>{max_prompt_chars} 字），不截断")
        if any((ord(c) < 32 and c not in "\n\t") or ord(c) == 0x7F for c in prompt):
            raise ValueError("提示含不安全控制字符")
        q.append(("prompt", prompt))
    url = "codex://threads/new"
    if q:
        url += "?" + urllib.parse.urlencode(q, quote_via=urllib.parse.quote, safe="/")
    if len(url) > max_url_len:
        raise ValueError(f"链接过长（{len(url)}>{max_url_len}），不截断")
    return url


def default_app_alive(runner: Runner) -> Callable[[], bool | None]:
    def check() -> bool | None:
        if sys.platform != "darwin":
            return None
        r = runner.run(["pgrep", "-f", "/Applications/ChatGPT.app/Contents/MacOS/"], timeout=2)
        return r.returncode == 0
    return check


@dataclass
class CodexDesktopConfig:
    codex_home: str = "~/.codex"
    originators: Sequence[str] = DEFAULT_ORIGINATORS
    running_window_s: float = 600.0
    max_candidates: int = 24
    max_tail_threads: int = 16
    max_prompt_chars: int = 2000
    max_url_len: int = 8000
    require_cwd_exists: bool = False
    extra: dict = field(default_factory=dict)


class CodexDesktopAdapter(ModeAdapter):
    mode = Mode.DESKTOP

    def __init__(self, cfg: CodexDesktopConfig | None = None, runner: Runner | None = None,
                 app_alive: Callable[[], bool | None] | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.cfg = cfg or CodexDesktopConfig()
        self.runner = runner or SubprocessRunner()
        self.app_alive = app_alive or default_app_alive(self.runner)
        self.clock = clock
        self.home = os.path.expanduser(self.cfg.codex_home)
        self._tail_cache: dict[str, tuple[int, float, Lifecycle]] = {}
        self._unread_cache: tuple[float, frozenset[str]] | None = None
        self.last_limit_reason: str | None = None
        self.columns: set[str] = set()

    # ------------------------------------------------------------------ caps
    def capabilities(self) -> Capabilities:
        return Capabilities(
            focus=None,
            new_session=None,
            skill_current="当前线程预填未验证",
            skill_new_thread=None,
            send="需辅助功能确认 composer，P0 未启用",
            stop="中断接口不可达（仅 AX），P0 禁用",
            approve="请在 App 内处理（审批代答为 P1）",
            effort_set="此会话不支持",
            precise_state="状态受限：无待审批/实时错误信号（P1 hooks）",
        )

    def status_limited(self) -> str | None:
        base = "状态受限：粗状态，待审批请看 App"
        return f"{base}；{self.last_limit_reason}" if self.last_limit_reason else base

    # ------------------------------------------------------------------ io
    def _db(self) -> sqlite3.Connection:
        path = os.path.join(self.home, "state_5.sqlite")
        if not os.path.exists(path):
            raise BackendUnavailable("未找到 Codex 存储（state_5.sqlite）")
        uri = "file:" + urllib.parse.quote(path) + "?mode=ro"
        con = sqlite3.connect(uri, uri=True, timeout=1.0)
        con.row_factory = sqlite3.Row
        return con

    def _probe_schema(self, con: sqlite3.Connection) -> set[str]:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(threads)")}
        missing = REQUIRED_COLUMNS - cols
        if missing:
            raise SchemaChanged(f"存储格式变化：threads 缺列 {sorted(missing)}")
        return cols

    def _rows(self, keep_ids: Sequence[str]) -> list[sqlite3.Row]:
        con = self._db()
        try:
            cols = self._probe_schema(con)
            self.columns = cols
            sel = ["id", "rollout_path", "source", "cwd", "title", "archived"] + \
                  [c for c in OPTIONAL_COLUMNS if c in cols]
            order = "recency_at_ms" if "recency_at_ms" in cols else "updated_at" if "updated_at" in cols else "rowid"
            origin_ok = self._origin_clause(cols)
            sql = (f"SELECT {', '.join(sel)} FROM threads WHERE archived = 0 AND source = 'vscode' {origin_ok} "
                   f"ORDER BY {order} DESC LIMIT ?")
            rows = list(con.execute(sql, (*self.cfg.originators, self.cfg.max_candidates)))
            have = {r["id"] for r in rows}
            extra = [i for i in keep_ids if i not in have and UUID_RE.match(i)]
            if extra:
                q = ",".join("?" * len(extra))
                rows += list(con.execute(
                    f"SELECT {', '.join(sel)} FROM threads WHERE archived = 0 AND source = 'vscode' {origin_ok} "
                    f"AND id IN ({q})", (*self.cfg.originators, *extra)))
            return rows
        finally:
            con.close()

    def _origin_clause(self, cols: set[str]) -> str:
        if "originator" not in cols:
            return ""
        marks = ",".join("?" * len(self.cfg.originators))
        null_ok = " OR originator IS NULL" if "" in self.cfg.originators else ""
        return f"AND (originator IN ({marks}){null_ok})"

    def _lifecycle(self, rollout_path: str) -> tuple[Lifecycle, float | None]:
        try:
            st = os.stat(rollout_path)
        except OSError:
            return Lifecycle(None, reason="rollout 缺失"), None
        cached = self._tail_cache.get(rollout_path)
        if cached and cached[0] == st.st_size and cached[1] == st.st_mtime:
            return cached[2], st.st_mtime
        with open(rollout_path, "rb") as f:
            start = max(0, st.st_size - TAIL_BYTES)
            f.seek(start)
            data = f.read(TAIL_BYTES)
        lc = parse_rollout_tail(data, from_start=start == 0)
        self._tail_cache[rollout_path] = (st.st_size, st.st_mtime, lc)
        return lc, st.st_mtime

    def unread_ids(self) -> frozenset[str]:
        path = os.path.join(self.home, ".codex-global-state.json")
        try:
            mtime = os.stat(path).st_mtime
        except OSError:
            return frozenset()
        if self._unread_cache and self._unread_cache[0] == mtime:
            return self._unread_cache[1]
        out: set[str] = set()
        try:
            with open(path, "rb") as f:
                d = json.load(f)
            rs = d.get("electron-thread-read-state-v1") or {}
            for per_identity in (rs.get("unreadByIdentity") or {}).values():
                if isinstance(per_identity, dict):
                    for lst in per_identity.values():
                        if isinstance(lst, list):
                            out.update(x for x in lst if isinstance(x, str))
        except (OSError, json.JSONDecodeError, AttributeError):
            self.last_limit_reason = "未读状态不可读"
            return frozenset()
        res = frozenset(out)
        self._unread_cache = (mtime, res)
        return res

    # ------------------------------------------------------------------ poll
    def poll(self, keep_ids: Sequence[str] = ()) -> list[Session]:
        self.last_limit_reason = None
        rows = self._rows(keep_ids)
        alive = self.app_alive()
        unread = self.unread_ids()
        now = self.clock()
        out: list[Session] = []
        keep = set(keep_ids)
        tail_budget = self.cfg.max_tail_threads
        for r in rows:
            tid = r["id"]
            if not UUID_RE.match(tid):
                continue
            keys = r.keys()
            name = (r["name"] if "name" in keys else None) or r["title"] or \
                   (r["preview"] if "preview" in keys else "") or tid[:8]
            name = " ".join(str(name).split())
            cwd = r["cwd"] or None
            cwd_ok = bool(cwd and os.path.isabs(cwd) and (not self.cfg.require_cwd_exists or os.path.isdir(cwd)))
            originator = (r["originator"] if "originator" in keys else None) or ""
            s = Session(
                sid=SessionId(Mode.DESKTOP, tid),
                tool=Tool.CODEX,
                title=name,
                cwd=cwd,
                cwd_confirmed=cwd_ok,
                effort=(r["reasoning_effort"] if "reasoning_effort" in keys else None),
                summary=" ".join(str((r["preview"] if "preview" in keys else "") or "").split())[:120],
                # originator whitelist is a heuristic (codex-tui also writes source=vscode)
                source_note=f"来源推定（{originator or '无 originator'}）",
                updated_ms=int((r["recency_at_ms"] if "recency_at_ms" in keys else 0) or 0),
            )
            if alive is False:
                s.state, s.state_reason = State.OFFLINE, "Codex App 未运行"
            elif tail_budget > 0 or tid in keep:
                tail_budget -= 1
                lc, mtime = self._lifecycle(r["rollout_path"])
                s.state, s.state_reason, s.done_marker = self._map_state(lc, mtime, now, tid in unread)
            else:
                s.state, s.state_reason = State.UNKNOWN, "超出跟踪窗口"
            out.append(s)
        if alive is None:
            self.last_limit_reason = "无法确认 App 进程"
        return out

    def _map_state(self, lc: Lifecycle, mtime: float | None, now: float, unread: bool) -> tuple[State, str, str]:
        """Conservative matrix (PRD §3.1, Desktop/P0 column)."""
        if lc.kind is None:
            return State.UNKNOWN, lc.reason or "无近期生命周期事件", ""
        if lc.kind == "task_started":
            if mtime is not None and now - mtime <= self.cfg.running_window_s:
                return State.RUNNING, "进行中（粗）", ""
            return State.UNKNOWN, "运行信号过期", ""
        if lc.kind == "error":
            return State.ERROR, "turn 失败", ""
        if lc.kind == "task_complete":
            marker = lc.turn_id or lc.ts
            if unread:
                return State.DONE_UNREAD, "完成未读（App 未读集合）", marker
            return State.IDLE, "空闲", marker
        if lc.kind == "turn_aborted":
            return State.IDLE, "已中断", ""
        return State.UNKNOWN, "未知事件", ""

    # ------------------------------------------------------------------ actions
    def _open(self, url: str) -> ActionResult:
        r = self.runner.run(["open", url], timeout=5)
        if r.returncode != 0:
            return ActionResult(False, f"打开失败：{(r.stderr or '').strip()[:40]}", {"url": url})
        return ActionResult(True, "已打开", {"url": url})

    def focus(self, s: Session) -> ActionResult:
        try:
            url = build_thread_url(s.sid.native)
        except ValueError as e:
            return ActionResult(False, str(e))
        res = self._open(url)
        if res.ok:
            res.message = f"已聚焦 · {s.title[:12]}"
        return res

    def new_session(self, cwd: str | None) -> ActionResult:
        try:
            url = build_new_thread_url(cwd, None, self.cfg.max_prompt_chars, self.cfg.max_url_len)
        except ValueError as e:
            return ActionResult(False, str(e))
        res = self._open(url)
        if res.ok:
            res.message = "已打开新线程"
        return res

    def skill(self, s: Session | None, prompt: str) -> ActionResult:
        """Degraded skill: prefill a *new* thread in the selected thread's confirmed
        project (never the current thread, never submits)."""
        if s is None:
            return ActionResult(False, "未选中会话")
        if not s.cwd_confirmed:
            return ActionResult(False, "项目路径不明，技能禁用")
        try:
            url = build_new_thread_url(s.cwd, prompt, self.cfg.max_prompt_chars, self.cfg.max_url_len)
        except ValueError as e:
            return ActionResult(False, str(e))
        res = self._open(url)
        if res.ok:
            res.message = "已在新线程预填（未提交）"
        return res
