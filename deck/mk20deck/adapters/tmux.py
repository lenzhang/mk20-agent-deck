"""tmux adapter: discovery + identity, state from agent hook records when present.

Identity = tmux server generation (pid + start_time) + ``pane_id`` so a recycled
``%N`` after a server restart never re-binds an old pin (PRD G35).
Discovery (any of): session/window name matching ``ag-ccai-*`` / ``ag-codex-*`` /
``ag-kimi-*``; an explicitly registered pane id; ``pane_current_command`` in
``agent_commands`` (claude / codex / kimi); or a live Claude/codex hook record
(``tools/hooks/mk20-agent-state-hook.py``) that maps the pane — the only way a
``ccai`` session is found, because its pane command is the ``bash`` wrapper.
State: the freshest live hook record of the pane (same tmux socket, agent pid alive
and inside the pane, younger than ``hook_max_age_s``) → running / needs_approval /
needs_input / done_unread / error / idle; without one the pane stays ``unknown``
(pane existence and ``pane_current_command`` never prove a state, PRD §3.1).
Focus requires a configured terminal App and exactly one attached client;
otherwise disabled with "请绑定终端 App".
"""
from __future__ import annotations

import fnmatch
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Sequence

from .. import agentstate
from ..model import ActionResult, Capabilities, Mode, Session, SessionId, State, Tool
from .base import BackendUnavailable, ModeAdapter, Runner, SubprocessRunner

FIELDS = ["#{session_name}", "#{window_index}", "#{window_name}", "#{pane_id}", "#{pane_title}",
          "#{pane_current_path}", "#{pane_current_command}", "#{pane_pid}"]
FMT = "\t".join(FIELDS)
DEFAULT_PATTERNS = ("ag-ccai-*", "ag-codex-*", "ag-kimi-*")
DEFAULT_AGENT_COMMANDS = ("claude", "codex", "kimi")

HOOK_STATE = {
    "running": (State.RUNNING, "运行中（hook）"),
    "needs_approval": (State.NEEDS_APPROVAL, "等待批准（hook）"),
    "needs_input": (State.NEEDS_INPUT, "等待输入（hook）"),
    "done": (State.DONE_UNREAD, "已完成未查看（hook）"),
    "error": (State.ERROR, "turn 失败（hook：API 错误）"),
    "idle": (State.IDLE, "空闲（hook）"),
}
RECORD_TOOL = {"claude": Tool.CCAI, "codex": Tool.CODEX}
HOOK_LABEL = {"claude": "Claude", "codex": "codex"}


@dataclass
class TmuxConfig:
    binary: str = "tmux"
    patterns: Sequence[str] = DEFAULT_PATTERNS
    registered_panes: Sequence[str] = ()
    terminal_app: str = ""  # e.g. "Ghostty" / "iTerm" — never guessed
    new_session_cmd: str = ""  # e.g. "ccai sg"; empty = new-session key disabled
    agent_commands: Sequence[str] = DEFAULT_AGENT_COMMANDS  # pane_current_command values listed as agents
    use_hooks: bool = True
    state_dir: str = ""  # "" = $MK20DECK_HOME/agent-state
    hook_max_age_s: float = 12 * 3600.0  # older records are ignored
    running_stale_s: float = 900.0  # "running" with no hook event for this long → unknown (Esc interrupt)
    extra: dict = field(default_factory=dict)


def tool_from_name(name: str) -> Tool:
    n = name.lower()
    if "ccai" in n or "claude" in n:
        return Tool.CCAI
    if "codex" in n:
        return Tool.CODEX
    if "kimi" in n:
        return Tool.KIMI
    return Tool.OTHER


def title_from_cwd(path: str) -> str:
    """Worktree name (the component after ``worktrees``/``.worktrees``), else the cwd basename."""
    p = os.path.normpath(path or "")
    if not p or p == ".":
        return ""
    if p == os.path.expanduser("~"):
        return "~"
    parts = [x for x in p.split(os.sep) if x]
    for i in range(len(parts) - 2, -1, -1):
        if parts[i] in ("worktrees", ".worktrees"):
            return parts[i + 1]
    return parts[-1] if parts else p


class TmuxAdapter(ModeAdapter):
    mode = Mode.TMUX

    def __init__(self, cfg: TmuxConfig | None = None, runner: Runner | None = None,
                 clock: Callable[[], float] = time.time,
                 pid_alive: Callable[[int], bool] = agentstate.pid_alive) -> None:
        self.cfg = cfg or TmuxConfig()
        self.r = runner or SubprocessRunner()
        self.clock = clock
        self.pid_alive = pid_alive
        self.generation = ""
        self.socket_path = ""
        self.hooked = 0      # panes whose state came from a hook record in the last poll
        self.unhooked = 0    # listed panes without one

    def capabilities(self) -> Capabilities:
        return Capabilities(
            focus=None if self.cfg.terminal_app else "未绑定终端 App，请在配置中设置",
            new_session=None if self.cfg.new_session_cmd else "未配置新会话命令",
            skill_current="投递语义未验证（P0 禁用）",
            skill_new_thread="tmux 无新线程预填",
            send="投递语义未验证（P0 禁用）",
            stop="中断能力未按工具版本验证",
            approve="P1：请在终端内处理",
            effort_set="此会话不支持",
            precise_state=None if self.hooked else "状态受限：tmux 无生命周期信号（未发现 hook 记录）",
        )

    def status_limited(self) -> str | None:
        if self.hooked:
            extra = f"；{self.unhooked} 个窗格无 hook" if self.unhooked else ""
            return f"状态来自 Claude/codex hooks{extra}"
        return "状态受限：tmux 仅能确认窗格存在（未发现 hook 记录）"

    def _tmux(self, *args: str):
        return self.r.run([self.cfg.binary, *args], timeout=3)

    def _records(self, panes: dict[str, int]) -> dict[str, agentstate.AgentRecord]:
        if not self.cfg.use_hooks:
            return {}
        recs = agentstate.load_records(self.cfg.state_dir or agentstate.default_state_dir(), self.clock(),
                                       self.cfg.hook_max_age_s)
        if not recs:
            return {}
        ps = self.r.run(["ps", "-A", "-o", "pid=,ppid="], timeout=3)
        parents = agentstate.parse_ps(ps.stdout) if ps.returncode == 0 else {}
        return agentstate.best_by_pane(recs, self.socket_path, panes, alive=self.pid_alive,
                                       parents=parents or None)

    def poll(self, keep_ids: Sequence[str] = ()) -> list[Session]:
        if not self.r.which(self.cfg.binary):
            raise BackendUnavailable("tmux 未安装")
        g = self._tmux("display-message", "-p", "#{pid}-#{start_time}\t#{socket_path}")
        if g.returncode != 0:
            raise BackendUnavailable("tmux server 未运行")
        gen, _, sock = g.stdout.strip().partition("\t")
        self.generation, self.socket_path = gen.strip(), sock.strip()
        r = self._tmux("list-panes", "-a", "-F", FMT)
        if r.returncode != 0:
            raise BackendUnavailable("tmux 列表失败")
        rows = []
        for line in r.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) == 7:
                parts.append("")
            if len(parts) != 8:
                continue
            rows.append(parts)
        panes = {p[3]: int(p[7]) if p[7].isdigit() else 0 for p in rows}
        records = self._records(panes)
        now = self.clock()
        cmds = {c.lower() for c in self.cfg.agent_commands}
        out = []
        hooked = 0
        for sess, widx, wname, pane_id, ptitle, path, cmd, _pid in rows:
            named = any(fnmatch.fnmatch(sess, p) or fnmatch.fnmatch(wname, p) for p in self.cfg.patterns)
            registered = pane_id in self.cfg.registered_panes
            by_cmd = cmd.lower() in cmds
            rec = records.get(pane_id)
            if not (named or registered or by_cmd or rec):
                continue
            label = wname if any(fnmatch.fnmatch(wname, p) for p in self.cfg.patterns) else sess
            if rec is not None:
                tool = RECORD_TOOL[rec.tool]
            elif by_cmd:
                tool = tool_from_name(cmd)
            else:
                tool = tool_from_name(label)
            if tool not in (Tool.CCAI, Tool.CODEX, Tool.KIMI) and not registered:
                continue
            cwd = (rec.cwd if rec and rec.cwd else path) or None
            title = title_from_cwd(cwd or "") or label.removeprefix("ag-")
            s = Session(
                sid=SessionId(Mode.TMUX, pane_id, self.generation),
                tool=tool,
                title=title,
                state=State.UNKNOWN,
                state_reason="tmux：无关联生命周期信号",
                cwd=cwd,
                cwd_confirmed=False,
                summary=f"{sess}:{widx} {wname} {pane_id} · {cmd}",
                source_note=("hook 关联" if rec else "命名约定发现" if named else "显式登记" if registered
                             else "命令线索发现"),
            )
            if rec is not None:
                hooked += 1
                self._apply_record(s, rec, now)
            out.append(s)
        self.hooked, self.unhooked = hooked, len(out) - hooked
        return out

    def _apply_record(self, s: Session, rec: agentstate.AgentRecord, now: float) -> None:
        st, why = HOOK_STATE.get(rec.state, (State.UNKNOWN, "hook 状态未知"))
        if st == State.RUNNING and now - rec.ts > self.cfg.running_stale_s:
            st, why = State.UNKNOWN, "运行信号过期（hook 久无事件）"
        s.state = st
        s.state_reason = why.replace("hook", f"{HOOK_LABEL[rec.tool]} hook", 1)
        s.updated_ms = int(rec.ts * 1000)
        if st == State.DONE_UNREAD:
            s.done_marker = f"{rec.tool}:{rec.session_id}:{rec.ts:.3f}"

    def focus(self, s: Session) -> ActionResult:
        if not self.cfg.terminal_app:
            return ActionResult(False, "未绑定终端 App，请在配置中设置")
        if s.sid.generation != self.generation:
            return ActionResult(False, "tmux server 已重启，身份失效")
        clients = self._tmux("list-clients", "-F", "#{client_name}")
        names = [c for c in clients.stdout.splitlines() if c.strip()]
        if clients.returncode != 0 or len(names) != 1:
            return ActionResult(False, "无法确定唯一 tmux client")
        pane = s.sid.native
        for args in (("switch-client", "-c", names[0], "-t", pane), ("select-window", "-t", pane),
                     ("select-pane", "-t", pane)):
            rr = self._tmux(*args)
            if rr.returncode != 0:
                return ActionResult(False, f"tmux {args[0]} 失败")
        a = self.r.run(["open", "-a", self.cfg.terminal_app], timeout=5)
        if a.returncode != 0:
            return ActionResult(False, f"无法激活 {self.cfg.terminal_app}")
        return ActionResult(True, f"已聚焦 {pane}")

    def new_session(self, cwd: str | None) -> ActionResult:
        if not self.cfg.new_session_cmd:
            return ActionResult(False, "未配置新会话命令")
        tool = tool_from_name(self.cfg.new_session_cmd)
        name = f"ag-{tool.value}-deck"
        args = ["new-window", "-n", name]
        if cwd:
            args += ["-c", cwd]
        args.append(self.cfg.new_session_cmd)
        r = self._tmux(*args)
        return ActionResult(r.returncode == 0, "已新建窗口" if r.returncode == 0 else "新建失败")
