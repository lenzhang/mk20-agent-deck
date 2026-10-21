"""Per-mode slot manager: 15 sticky slots, pinning, overflow (PRD §6.3, G13/G32/G35, AC15).

PRD 1.5 L1 layout (2026-10-21): slots S1..S15 fill rows 1-3 of the 5x4 key grid, slot index
``i`` sits at document ``(col, row) = (i % 5 + 1, i // 5 + 1)``.  The old layout had 8 slots
in a 4-column block (A1..A8 at ``(i % 4 + 1, i // 4 + 1)``); ``from_dict`` migrates such state
files position-preserving (A1..A4 → S1..S4, A5..A8 → S6..S9) so pins keep their physical key.
``move`` implements the user's pick-up-and-place gesture (2026-10-22): placed sessions are pinned.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .model import PRIORITY, Session, State

N_SLOTS = 15
SLOT_COLS = 5
LEGACY_N_SLOTS, LEGACY_COLS = 8, 4  # state files written before PRD 1.5 (no "n_slots" field)


def legacy_index(i: int) -> int:
    """Old 8-slot index (4 per row) → new index at the same physical key."""
    return (i // LEGACY_COLS) * SLOT_COLS + i % LEGACY_COLS


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

    def move(self, src: int, dst: int) -> str:
        """Pick-up-and-place (PRD 1.5 delta 2026-10-22): put the entry of ``src`` on ``dst`` and pin it.

        * ``dst == src``: toggle the pin (the only way to pin / unpin; a lost pin is released).
        * ``dst`` occupied: the two swap; the displaced entry keeps its own pin / lost flags.
        * ``dst`` empty: ``src`` becomes empty (the normal fill rules may refill it later).

        The placed entry is pinned, so ``update`` never moves or replaces it; the selection
        follows the session (it is a key, not an index)."""
        if not (0 <= src < N_SLOTS and 0 <= dst < N_SLOTS):
            raise IndexError(f"slot index out of range: {src} -> {dst}")
        if src == dst:
            return self.toggle_pin(src)
        e = self.slots[src]
        if e is None:
            return "空槽不可移动"
        other = self.slots[dst]
        self.slots[dst], self.slots[src] = e, other
        e.pinned = True
        self._bump()
        return f"已移到 S{dst + 1} · 已钉扎" if other is None else f"已与 S{dst + 1} 交换 · 已钉扎"

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "n_slots": N_SLOTS,
            "slots": [None if e is None else {"key": e.key, "pinned": e.pinned} for e in self.slots],
            "selected": self.selected,
            "excluded": sorted(self.excluded),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SlotManager":
        """Restore after restart.  Unpinned entries are re-verified on the next
        ``update`` (dropped if gone); pinned ones become lost pins if unverifiable.

        A file without ``n_slots`` is the pre-1.5 8-slot layout: its entries move to the
        slot under the same physical key (``legacy_index``); new slots start empty."""
        sm = cls()
        raw = d.get("slots") or []
        legacy = "n_slots" not in d
        seen: set[str] = set()
        for i, e in enumerate(raw[:LEGACY_N_SLOTS] if legacy else raw[:N_SLOTS]):
            if not isinstance(e, dict) or not e.get("key") or e["key"] in seen:
                continue
            seen.add(e["key"])
            sm.slots[legacy_index(i) if legacy else i] = SlotEntry(str(e["key"]), bool(e.get("pinned")))
        sm.selected = d.get("selected")
        sm.excluded = set(d.get("excluded") or [])
        return sm
