"""Scripted adapter for the simulator and tests (explicitly marked SIM)."""
from __future__ import annotations

from typing import Sequence

from ..model import ActionResult, Capabilities, Mode, Session
from .base import BackendUnavailable, ModeAdapter


class MockAdapter(ModeAdapter):
    def __init__(self, mode: Mode, sessions: list[Session] | None = None, unavailable: str | None = None,
                 caps: Capabilities | None = None, limited: str | None = "状态受限：SIM 数据") -> None:
        self.mode = mode
        self.sessions = sessions or []
        self.unavailable = unavailable
        self.caps = caps or Capabilities(focus=None, new_session=None, skill_new_thread=None)
        self.limited = limited
        self.actions: list[tuple[str, str]] = []

    def capabilities(self) -> Capabilities:
        return self.caps

    def status_limited(self) -> str | None:
        return self.limited

    def poll(self, keep_ids: Sequence[str] = ()) -> list[Session]:
        if self.unavailable:
            raise BackendUnavailable(self.unavailable)
        return list(self.sessions)

    def _act(self, name: str, target: str, cap: str | None) -> ActionResult:
        if cap is not None:
            return ActionResult(False, cap)
        self.actions.append((name, target))
        return ActionResult(True, f"SIM {name}")

    def focus(self, s: Session) -> ActionResult:
        return self._act("focus", s.sid.key(), self.caps.focus)

    def new_session(self, cwd: str | None) -> ActionResult:
        return self._act("new", cwd or "", self.caps.new_session)

    def skill(self, s: Session | None, prompt: str) -> ActionResult:
        if s is None:
            return ActionResult(False, "未选中会话")
        return self._act("skill", f"{s.sid.key()}|{prompt}", self.caps.skill_new_thread)

    def stop(self, s: Session) -> ActionResult:
        return self._act("stop", s.sid.key(), self.caps.stop)

    def send(self, s: Session) -> ActionResult:
        return self._act("send", s.sid.key(), self.caps.send)
