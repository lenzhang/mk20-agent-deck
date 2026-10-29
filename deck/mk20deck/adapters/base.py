"""ModeAdapter interface: "who is the session, where does the action go" (PRD §6.7)."""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Protocol, Sequence

from ..model import ActionResult, Capabilities, Mode, Session


class BackendUnavailable(Exception):
    """Backend cannot be reached; sessions of this mode show ``offline`` + reason."""


@dataclass
class RunResult:
    returncode: int
    stdout: str
    stderr: str


class Runner(Protocol):
    def run(self, argv: Sequence[str], timeout: float = 5.0) -> RunResult: ...
    def which(self, name: str) -> str | None: ...


class SubprocessRunner:
    def run(self, argv: Sequence[str], timeout: float = 5.0) -> RunResult:
        try:
            p = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError as e:
            return RunResult(127, "", str(e))
        except subprocess.TimeoutExpired:
            return RunResult(124, "", "timeout")
        return RunResult(p.returncode, p.stdout, p.stderr)

    def which(self, name: str) -> str | None:
        return shutil.which(name)


class RecordingRunner:
    """Test double: records argv, returns scripted results keyed by argv[0:2]."""

    def __init__(self, responses: dict[tuple[str, ...], RunResult] | None = None,
                 binaries: set[str] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.responses = responses or {}
        self.binaries = binaries if binaries is not None else set()

    def run(self, argv: Sequence[str], timeout: float = 5.0) -> RunResult:
        argv = list(argv)
        self.calls.append(argv)
        for n in range(len(argv), 0, -1):
            r = self.responses.get(tuple(argv[:n]))
            if r is not None:
                return r
        return RunResult(0, "", "")

    def which(self, name: str) -> str | None:
        return f"/usr/bin/{name}" if name in self.binaries else None


class ModeAdapter:
    mode: Mode

    def capabilities(self) -> Capabilities:
        return Capabilities()

    def status_limited(self) -> str | None:
        """Persistent "状态受限" reason (PRD §6.8.3 / AC11), or None when precise."""
        return "状态受限：尽力而为"

    def poll(self, keep_ids: Sequence[str] = ()) -> list[Session]:
        """Return current sessions.  ``keep_ids`` are native ids that must be
        re-verified even when outside the candidate window (pinned/selected)."""
        raise NotImplementedError

    def focus(self, s: Session) -> ActionResult:
        return ActionResult(False, self.capabilities().focus or "不支持")

    def new_session(self, cwd: str | None) -> ActionResult:
        return ActionResult(False, self.capabilities().new_session or "不支持")

    def skill(self, s: Session | None, prompt: str) -> ActionResult:
        return ActionResult(False, "技能不可用")

    def send(self, s: Session) -> ActionResult:
        return ActionResult(False, self.capabilities().send or "不支持")

    def stop(self, s: Session) -> ActionResult:
        return ActionResult(False, self.capabilities().stop or "不支持")

    def set_effort(self, s: Session, level: str) -> ActionResult:
        return ActionResult(False, self.capabilities().effort_set or "此会话不支持")

    # K1 scroll-back (2026-10-25).  ``lines`` > 0 = older (up), < 0 = newer (down).  ``detail["live"]``: the view is
    # back at the latest content by itself (tmux left copy mode); ``detail["setup"]``: a one-time setup is missing
    # (the message says what to run).
    def scroll(self, s: Session, lines: int) -> ActionResult:
        return ActionResult(False, self.capabilities().scroll or "此模式不支持回看")

    def scroll_bottom(self, s: Session) -> ActionResult:
        """K1 press: back to the latest content, out of any scroll-back state."""
        return ActionResult(False, self.capabilities().scroll or "此模式不支持回看")

    def answer(self, s: Session, digit: int) -> ActionResult:
        """Long-press approve (2026-10-29): type the choice ``digit`` into ``s`` itself (not the frontmost window).
        Only offered when ``capabilities().approve`` is None."""
        return ActionResult(False, self.capabilities().approve or "此模式不支持代答")

    def scroll_release(self, s: Session) -> bool:
        """The deck stops scrolling ``s`` (another session / mode, idle timeout): leave the scroll-back state if that
        touches nothing else (tmux: only that pane).  True when the view is back at the latest content."""
        return False
