"""Core domain model: states, sessions, capabilities (PRD §3.1, §6.7)."""
from __future__ import annotations

import enum
from dataclasses import dataclass, field


class State(str, enum.Enum):
    UNASSIGNED = "unassigned"
    OFFLINE = "offline"
    UNKNOWN = "unknown"
    IDLE = "idle"
    THINKING = "thinking"
    RUNNING = "running"
    NEEDS_INPUT = "needs_input"
    NEEDS_APPROVAL = "needs_approval"
    DONE_UNREAD = "done_unread"
    ERROR = "error"


BASE_STATES = tuple(State)  # the 10 base states

# §6.3 Priority (higher number = more urgent).  unknown/offline rank below known
# active states; unassigned is never a candidate.
PRIORITY = {
    State.NEEDS_APPROVAL: 7,
    State.NEEDS_INPUT: 6,
    State.ERROR: 5,
    State.DONE_UNREAD: 4,
    State.RUNNING: 3,
    State.THINKING: 3,
    State.IDLE: 2,
    State.UNKNOWN: 1,
    State.OFFLINE: 0,
    State.UNASSIGNED: -1,
}

ATTENTION_STATES = frozenset({State.NEEDS_APPROVAL, State.NEEDS_INPUT, State.ERROR, State.DONE_UNREAD})


class Mode(str, enum.Enum):
    DESKTOP = "codex_desktop"
    HERDR = "herdr"
    TMUX = "tmux"

    @property
    def badge(self) -> str:
        return {"codex_desktop": "CODEX", "herdr": "HERDR", "tmux": "TMUX"}[self.value]

    @property
    def key_label(self) -> str:
        return {"codex_desktop": "DESK", "herdr": "HERD", "tmux": "TMUX"}[self.value]


MODE_CYCLE = (Mode.DESKTOP, Mode.HERDR, Mode.TMUX)  # D2: Desktop -> herdr -> tmux -> Desktop


class Tool(str, enum.Enum):
    CODEX = "codex"
    CCAI = "ccai"
    KIMI = "kimi"
    GROK = "grok"
    OTHER = "other"


EFFORT_LEVELS = ("minimal", "low", "medium", "high", "xhigh")


@dataclass(frozen=True)
class SessionId:
    """Mode namespace + verifiable native identity (+ generation for tmux/herdr)."""

    mode: Mode
    native: str
    generation: str = ""

    def key(self) -> str:
        return f"{self.mode.value}:{self.generation}:{self.native}"

    @classmethod
    def parse(cls, s: str) -> "SessionId":
        mode, gen, native = s.split(":", 2)
        return cls(Mode(mode), native, gen)


@dataclass
class Session:
    sid: SessionId
    tool: Tool
    title: str
    state: State = State.UNKNOWN
    cwd: str | None = None
    cwd_confirmed: bool = False
    effort: str | None = None
    summary: str = ""
    source_note: str = ""  # e.g. "来源推定" for heuristic Desktop source detection
    state_reason: str = ""  # why the state is what it is (shown on sub-screen)
    updated_ms: int = 0
    done_marker: str = ""  # identity of the completion that produced done_unread
    # Pending numbered choice (option mode, 2026-10-22; tmux hook records only): AskUserQuestion labels for
    # options 1..N, or prompt_kind "permission" (fixed 是 / 是，且不再问 / 否).  Empty = unknown → digits 1..5.
    options: tuple[str, ...] = ()
    prompt_kind: str = ""


@dataclass
class Capabilities:
    """Per-mode action capabilities; each unsupported action carries a reason."""

    focus: str | None = None  # None = supported; otherwise human-readable reason
    new_session: str | None = None
    skill_current: str | None = "当前线程预填未验证"
    skill_new_thread: str | None = None
    send: str | None = "P0 未启用"
    stop: str | None = "P0 未验证中断能力"
    approve: str | None = "P1：请在 App 内处理"
    effort_set: str | None = "此会话不支持"
    precise_state: str | None = "状态受限"

    def supported(self, action: str) -> bool:
        return getattr(self, action) is None


@dataclass
class ActionResult:
    ok: bool
    message: str
    detail: dict = field(default_factory=dict)
