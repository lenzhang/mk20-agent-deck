"""Per-mode slot manager: 8 sticky slots, pinning, overflow (PRD §6.3, G13/G32/G35, AC15)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .model import PRIORITY, Session, State

N_SLOTS = 8


@dataclass
class SlotEntry:
    key: str  # SessionId.key()
    pinned: bool = False
    lost: bool = False  # pinned but identity cannot be verified any more ("失联钉扎")


@dataclass
class SlotManager:
    slots: list[SlotEntry | None] = field(default_factory=lambda: [None] * N_SLOTS)
    selected: str | None = None
    excluded: set[str] = field(default_factory=set)
    mapping_version: int = 0
    overflow: int = 0
    overflow_attention: int = 0
    sessions: dict[str, Session] = field(default_factory=dict)
    selection_lost: bool = False

    # ------------------------------------------------------------------
    def _bump(self) -> None:
        self.mapping_version += 1

    def keys_in_slots(self) -> set[str]:
        return {e.key for e in self.slots if e is not None}

    def protected(self, e: SlotEntry) -> bool:
        return e.pinned or e.key == self.selected

    def slot_priority(self, e: SlotEntry) -> int:
        s = self.sessions.get(e.key)
        return PRIORITY[s.state] if s else PRIORITY[State.UNASSIGNED]

    def update(self, sessions: Iterable[Session]) -> None:
        present = {s.sid.key(): s for s in sessions if s.sid.key() not in self.excluded}
        self.sessions = present
        changed = False
        # 1. drop vanished targets; pinned ones become lost pins (keep their slot)
        for i, e in enumerate(self.slots):
            if e is None:
                continue
            if e.key in present:
                if e.lost:
                    e.lost = False
                    changed = True
                continue
            if e.pinned:
                if not e.lost:
                    e.lost = True
                    changed = True
            else:
                self.slots[i] = None
                changed = True
        if self.selected and self.selected not in present:
            # G32: clear selection, never auto-select a neighbour
            self.selected = None
            self.selection_lost = True
        # 2. candidates by Priority, then recency
        in_slots = self.keys_in_slots()
        cands = sorted((s for k, s in present.items() if k not in in_slots),
                       key=lambda s: (-PRIORITY[s.state], -s.updated_ms, s.sid.key()))
        # 3. fill empty slots in order
        rest = []
        for s in cands:
            try:
                idx = self.slots.index(None)
            except ValueError:
                rest.append(s)
                continue
            self.slots[idx] = SlotEntry(s.sid.key())
            changed = True
        # 4. overflow replacement: strictly higher than the lowest non-protected slot
        still_out = []
        for s in rest:
            victims = [(self.slot_priority(e), i) for i, e in enumerate(self.slots)
                       if e is not None and not self.protected(e)]
            if not victims:
                still_out.append(s)
                continue
            low_p, low_i = min(victims, key=lambda t: (t[0], -t[1]))
            if PRIORITY[s.state] > low_p:
                old = self.slots[low_i]
                self.slots[low_i] = SlotEntry(s.sid.key())
                if old is not None and old.key in self.sessions:
                    still_out.append(self.sessions[old.key])
                changed = True
            else:
                still_out.append(s)
        self.overflow = len(still_out)
        self.overflow_attention = sum(1 for s in still_out if s.state in (State.NEEDS_APPROVAL, State.NEEDS_INPUT,
                                                                         State.ERROR))
        if changed:
            self._bump()

    # ------------------------------------------------------------------
    def entry(self, idx: int) -> SlotEntry | None:
        return self.slots[idx]

    def session_at(self, idx: int) -> Session | None:
        e = self.slots[idx]
        if e is None or e.lost:
            return None
        return self.sessions.get(e.key)

    def select(self, idx: int) -> Session | None:
        s = self.session_at(idx)
        if s is None:
            return None
        self.selected = s.sid.key()
        self.selection_lost = False
        return s

    def selected_session(self) -> Session | None:
        return self.sessions.get(self.selected) if self.selected else None

    def toggle_pin(self, idx: int) -> str:
        e = self.slots[idx]
        if e is None:
            return "空槽不可钉扎"
        if e.lost:
            self.slots[idx] = None
            self._bump()
            return "已解除失联钉扎"
        e.pinned = not e.pinned
        self._bump()
        return "已钉扎" if e.pinned else "已取消钉扎"

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "slots": [None if e is None else {"key": e.key, "pinned": e.pinned} for e in self.slots],
            "selected": self.selected,
            "excluded": sorted(self.excluded),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SlotManager":
        """Restore after restart.  Unpinned entries are re-verified on the next
        ``update`` (dropped if gone); pinned ones become lost pins if unverifiable."""
        sm = cls()
        for i, e in enumerate((d.get("slots") or [])[:N_SLOTS]):
            if e and e.get("key"):
                sm.slots[i] = SlotEntry(e["key"], bool(e.get("pinned")))
        sm.selected = d.get("selected")
        sm.excluded = set(d.get("excluded") or [])
        return sm
