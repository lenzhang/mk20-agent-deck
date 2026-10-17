"""herdr adapter — P0 stub (PRD §6.7, D9: install + macOS integration after sign-off).

Behaviour now: when the ``herdr`` binary is missing the mode stays in the cycle
and shows ``offline`` + "herdr 未安装".  When present, ``herdr agent list --json``
is attempted; its schema is unverified, so parsing is defensive and statuses are
mapped by the conservative matrix (§3.1 herdr column).  All actions are disabled
until verified (focus via ``agent.focus`` is a later task).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Sequence

from ..model import ActionResult, Capabilities, Mode, Session, SessionId, State, Tool
from .base import BackendUnavailable, ModeAdapter, Runner, SubprocessRunner

STATUS_MAP = {
    "idle": (State.IDLE, "空闲"),
    "working": (State.RUNNING, "运行（粗）"),
    "unknown": (State.UNKNOWN, "未知"),
    "blocked": (State.UNKNOWN, "待处理（原因不明）"),  # no precise reason in P0
    "done": (State.IDLE, "完成（未读语义未验证）"),
}


@dataclass
class HerdrConfig:
    binary: str = "herdr"


def map_status(status: str) -> tuple[State, str]:
    return STATUS_MAP.get((status or "").lower(), (State.UNKNOWN, f"未知状态 {status!r}"))


class HerdrAdapter(ModeAdapter):
    mode = Mode.HERDR

    def __init__(self, cfg: HerdrConfig | None = None, runner: Runner | None = None) -> None:
        self.cfg = cfg or HerdrConfig()
        self.r = runner or SubprocessRunner()

    def capabilities(self) -> Capabilities:
        reason = "herdr 未联调"
        return Capabilities(focus=reason, new_session=reason, skill_current=reason, skill_new_thread=reason,
                            send=reason, stop=reason, approve="P1：请在 herdr 内处理", effort_set="此会话不支持",
                            precise_state="状态受限：herdr 未联调")

    def status_limited(self) -> str | None:
        return "状态受限：herdr 未联调（P0 stub）"

    def poll(self, keep_ids: Sequence[str] = ()) -> list[Session]:
        if not self.r.which(self.cfg.binary):
            raise BackendUnavailable("herdr 未安装")
        r = self.r.run([self.cfg.binary, "agent", "list", "--json"], timeout=3)
        if r.returncode != 0:
            raise BackendUnavailable("herdr 未联调（agent list 失败）")
        try:
            data = json.loads(r.stdout or "[]")
        except json.JSONDecodeError:
            raise BackendUnavailable("herdr 输出格式未知")
        items = data.get("agents", []) if isinstance(data, dict) else data
        out = []
        for a in items if isinstance(items, list) else []:
            if not isinstance(a, dict) or not a.get("id"):
                continue
            name = str(a.get("name") or a.get("id"))
            tool = Tool.CCAI if "claude" in name.lower() or "ccai" in name.lower() else \
                Tool.CODEX if "codex" in name.lower() else Tool.OTHER
            if tool is Tool.OTHER:
                continue
            st, why = map_status(str(a.get("status", "unknown")))
            out.append(Session(SessionId(Mode.HERDR, str(a["id"]), str(a.get("instance", ""))), tool, name,
                               state=st, state_reason=why, source_note="herdr（未联调）"))
        return out

    def focus(self, s: Session) -> ActionResult:
        return ActionResult(False, "herdr 未联调")
