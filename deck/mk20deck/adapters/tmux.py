"""tmux adapter (P0: discovery + identity; state stays ``unknown``).

Identity = tmux server generation (pid + start_time) + ``pane_id`` so a recycled
``%N`` after a server restart never re-binds an old pin (PRD G35).
Discovery: session or window name matching ``ag-ccai-*`` / ``ag-codex-*`` or an
explicitly registered pane id.  ``pane_current_command`` is only a hint (PRD §6.7).
Focus requires a configured terminal App and exactly one attached client;
otherwise disabled with "请绑定终端 App".
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Sequence

from ..model import ActionResult, Capabilities, Mode, Session, SessionId, State, Tool
from .base import BackendUnavailable, ModeAdapter, Runner, SubprocessRunner

FMT = "\t".join(["#{session_name}", "#{window_index}", "#{window_name}", "#{pane_id}", "#{pane_title}",
                 "#{pane_current_path}", "#{pane_current_command}"])


@dataclass
class TmuxConfig:
    binary: str = "tmux"
    patterns: Sequence[str] = ("ag-ccai-*", "ag-codex-*")
    registered_panes: Sequence[str] = ()
    terminal_app: str = ""  # e.g. "Ghostty" / "iTerm" — never guessed
    new_session_cmd: str = ""  # e.g. "ccai sg"; empty = new-session key disabled
    extra: dict = field(default_factory=dict)


def tool_from_name(name: str) -> Tool:
    n = name.lower()
    if "ccai" in n or "claude" in n:
        return Tool.CCAI
    if "codex" in n:
        return Tool.CODEX
    return Tool.OTHER


class TmuxAdapter(ModeAdapter):
    mode = Mode.TMUX

    def __init__(self, cfg: TmuxConfig | None = None, runner: Runner | None = None) -> None:
        self.cfg = cfg or TmuxConfig()
        self.r = runner or SubprocessRunner()
        self.generation = ""

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
            precise_state="状态受限：tmux 无生命周期信号",
        )

    def status_limited(self) -> str | None:
        return "状态受限：tmux 仅能确认窗格存在"

    def _tmux(self, *args: str):
        return self.r.run([self.cfg.binary, *args], timeout=3)

    def poll(self, keep_ids: Sequence[str] = ()) -> list[Session]:
        if not self.r.which(self.cfg.binary):
            raise BackendUnavailable("tmux 未安装")
        g = self._tmux("display-message", "-p", "#{pid}-#{start_time}")
        if g.returncode != 0:
            raise BackendUnavailable("tmux server 未运行")
        self.generation = g.stdout.strip()
        r = self._tmux("list-panes", "-a", "-F", FMT)
        if r.returncode != 0:
            raise BackendUnavailable("tmux 列表失败")
        out = []
        for line in r.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) != 7:
                continue
            sess, widx, wname, pane_id, ptitle, path, cmd = parts
            matched = any(fnmatch.fnmatch(sess, p) or fnmatch.fnmatch(wname, p) for p in self.cfg.patterns)
            if not matched and pane_id not in self.cfg.registered_panes:
                continue
            label = wname if any(fnmatch.fnmatch(wname, p) for p in self.cfg.patterns) else sess
            tool = tool_from_name(label)
            if tool not in (Tool.CCAI, Tool.CODEX) and pane_id not in self.cfg.registered_panes:
                continue
            out.append(Session(
                sid=SessionId(Mode.TMUX, pane_id, self.generation),
                tool=tool,
                title=label.removeprefix("ag-"),
                state=State.UNKNOWN,
                state_reason="tmux：无关联生命周期信号",
                cwd=path or None,
                cwd_confirmed=False,
                summary=f"{sess}:{widx} {pane_id} · {cmd}",
                source_note="命名约定发现" if matched else "显式登记",
            ))
        return out

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
