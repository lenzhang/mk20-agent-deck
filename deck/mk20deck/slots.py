"""Per-mode slot manager: 15 sticky slots, pinning, overflow (PRD §6.3, G13/G32/G35, AC15).

PRD 1.5 L1 layout (2026-10-21): slots S1..S15 fill rows 1-3 of the 5x4 key grid, slot index
``i`` sits at document ``(col, row) = (i % 5 + 1, i // 5 + 1)``.  The old layout had 8 slots
in a 4-column block (A1..A8 at ``(i % 4 + 1, i // 4 + 1)``); ``from_dict`` migrates such state
files position-preserving (A1..A4 → S1..S4, A5..A8 → S6..S9) so pins keep their physical key.
``move`` implements the user's pick-up-and-place gesture (2026-10-22): placed sessions are pinned.

Slot rules (``rules.py``, arrange web page 2026-10-22): ``rules`` holds the enabled rules of this mode and
``update`` applies them first — precedence rules > manual pins > auto fill.  A rule's session is pinned on
the rule's slot (``SlotEntry.rule``); an enabled rule without a session reserves its slot (``waiting``,
key face "等待：…") and auto fill skips it.  A manual change of a rule slot (keyboard or web move into or
out of it, pin toggle, unpin, clear) disables that rule: ``on_rule_override(rule, why)`` persists it.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Callable, Iterable

from . import rules as _rules
from .model import PRIORITY, Session, State

N_SLOTS = 15
SLOT_COLS = 5
LEGACY_N_SLOTS, LEGACY_COLS = 8, 4  # state files written before PRD 1.5 (no "n_slots" field)
assert _rules.N_SLOTS == N_SLOTS


def legacy_index(i: int) -> int:
    """Old 8-slot index (4 per row) → new index at the same physical key."""
    return (i // LEGACY_COLS) * SLOT_COLS + i % LEGACY_COLS


@dataclass
class SlotEntry:
    key: str  # SessionId.key()
    pinned: bool = False
    lost: bool = False  # pinned but identity cannot be verified any more ("失联钉扎")
    rule: str = ""  # id of the slot rule that put it here ("" = keyboard / web / auto fill)


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
    rules: tuple = ()  # enabled rules.SlotRule of this mode, slot order (set_rules)
    waiting: dict = field(default_factory=dict)  # slot -> rule without a matching session (reserved)
    on_rule_override: Callable | None = field(default=None, repr=False, compare=False)

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

    # ------------------------------------------------------------------ rules
    def rule_at(self, idx: int):
        return next((r for r in self.rules if r.slot == idx), None)

    def rule_slots(self) -> set[int]:
        return {r.slot for r in self.rules}

    def _free_slot(self, prefer: int | None = None) -> int | None:
        """First empty slot that no rule reserves (``prefer`` first)."""
        reserved = self.rule_slots()
        if prefer is not None and self.slots[prefer] is None and prefer not in reserved:
            return prefer
        return next((i for i, e in enumerate(self.slots) if e is None and i not in reserved), None)

    def set_rules(self, rules: Iterable) -> None:
        """Enabled rules of this mode (rules for other modes / disabled ones are ignored by the caller)."""
        self.rules = tuple(sorted((r for r in rules if r.enabled), key=lambda r: r.slot))
        ids = {r.id for r in self.rules}
        for e in self.slots:
            if e is not None and e.rule and e.rule not in ids:
                e.rule = ""  # rule deleted / disabled: the session stays as an ordinary pin
        self.waiting = {r.slot: r for r in self.rules
                        if self.slots[r.slot] is None or self.slots[r.slot].rule != r.id}

    def waiting_label(self, idx: int) -> str:
        r = self.waiting.get(idx)
        return f"等待：{r.short_label()}" if r is not None and self.slots[idx] is None else ""

    def _apply_rules(self, present: dict[str, Session]) -> bool:
        changed = False
        ids = {r.id for r in self.rules}
        for e in self.slots:
            if e is not None and e.rule and e.rule not in ids:
                e.rule = ""
        holder = {i: e.key for i, e in enumerate(self.slots) if e is not None}
        placed, self.waiting = _rules.resolve(self.rules, present, holder)
        displaced: list[tuple[SlotEntry, int | None]] = []
        for i in sorted(set(placed) | set(self.waiting)):
            e = self.slots[i]
            want = placed[i][1] if i in placed else None
            if e is not None and e.key == want:
                rid = placed[i][0].id
                if not e.pinned or e.lost or e.rule != rid:
                    e.pinned, e.lost, e.rule = True, False, rid
                    changed = True
                continue
            freed = None
            if want is not None:
                for j, o in enumerate(self.slots):
                    if o is not None and o.key == want and j != i:
                        self.slots[j], freed = None, j
                self.slots[i] = SlotEntry(want, pinned=True, rule=placed[i][0].id)
            else:
                self.slots[i] = None
            changed = changed or e is not None or want is not None
            if e is not None:
                displaced.append((e, freed))
        for e, freed in displaced:
            # A rule's previous session and unpinned / lost entries fall back to auto fill; a manual pin
            # moves to the slot the rule's session came from (swap), else to the first free slot.
            if e.rule or e.lost or not e.pinned or e.key not in present or e.key in self.keys_in_slots():
                continue
            j = self._free_slot(prefer=freed)
            if j is not None:
                self.slots[j] = SlotEntry(e.key, pinned=True)
        return changed

    def _override(self, r, why: str) -> None:
        """A manual change hit rule ``r``'s slot: disable it here at once and tell the owner (persist)."""
        self.rules = tuple(x for x in self.rules if x.id != r.id)
        self.waiting.pop(r.slot, None)
        for e in self.slots:
            if e is not None and e.rule == r.id:
                e.rule = ""
        if self.on_rule_override is not None:
            self.on_rule_override(r, why)

    def _overrides(self, notes: dict[int, str]) -> str:
        """``notes[slot]`` = why a manual change disables the rule of that slot (if it has one)."""
        hit = [r for r in self.rules if r.slot in notes]
        for r in hit:
            self._override(r, notes[r.slot])
        return " · 规则已停用" if hit else ""

    # ------------------------------------------------------------------
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
        # 1b. slot rules (rules > manual pins > auto fill); reserved slots stay empty
        if self.rules or self.waiting or any(e is not None and e.rule for e in self.slots):
            changed = self._apply_rules(present) or changed
        # 2. candidates by Priority, then recency
        in_slots = self.keys_in_slots()
        cands = sorted((s for k, s in present.items() if k not in in_slots),
                       key=lambda s: (-PRIORITY[s.state], -s.updated_ms, s.sid.key()))
        # 3. fill empty slots in order (never a slot a rule reserves)
        rest = []
        for s in cands:
            idx = self._free_slot()
            if idx is None:
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

    def toggle_pin(self, idx: int, actor: str = "键盘") -> str:
        e = self.slots[idx]
        if e is None:
            return "空槽不可钉扎"
        if e.lost:
            self.slots[idx] = None
            self._bump()
            return "已解除失联钉扎" + self._overrides({idx: f"{actor}解除失联钉扎"})
        e.pinned = not e.pinned
        self._bump()
        return ("已钉扎" if e.pinned else "已取消钉扎") + self._overrides({idx: f"{actor}切换钉住"})

    def index_of(self, key: str) -> int | None:
        return next((i for i, e in enumerate(self.slots) if e is not None and e.key == key), None)

    def place(self, key: str, dst: int, actor: str = "网页") -> str:
        """Arrange page "放到这里": put session ``key`` on ``dst`` pinned (``move`` semantics).

        Already in a slot: ``move`` (swap).  Not in a slot: a pinned occupant moves to the first free slot
        (keeping its pin), an unpinned one goes back to auto fill.  Presence is checked by the caller."""
        if not 0 <= dst < N_SLOTS:
            raise IndexError(f"slot index out of range: {dst}")
        cur = self.slots[dst]
        if cur is not None and cur.key == key:
            if cur.pinned and not cur.lost:
                return f"已在 S{dst + 1}"
            cur.pinned, cur.lost = True, False
            self._bump()
            return f"已钉在 S{dst + 1}" + self._overrides({dst: f"{actor}钉住此键"})
        src = self.index_of(key)
        if src is not None:
            return self.move(src, dst, actor)
        self.slots[dst] = SlotEntry(key, pinned=True)
        if cur is not None and cur.pinned and not cur.lost:
            j = self._free_slot()
            if j is not None:
                self.slots[j] = SlotEntry(cur.key, pinned=True)
        self._bump()
        return f"已放到 S{dst + 1} · 已钉扎" + self._overrides({dst: f"{actor}放入其他会话"})

    def unpin(self, idx: int, actor: str = "网页") -> str:
        e = self.slots[idx]
        if e is None:
            return "空槽"
        if e.lost:
            self.slots[idx] = None
            msg = "已解除失联钉扎"
        elif not e.pinned:
            return "未钉住"
        else:
            e.pinned = False
            msg = "已取消钉住"
        self._bump()
        return msg + self._overrides({idx: f"{actor}取消钉住"})

    def clear(self, idx: int, actor: str = "网页") -> str:
        """Empty the key (pin, lost pin or rule placement); auto fill may use it again on the next poll."""
        e = self.slots[idx]
        if e is None and self.rule_at(idx) is None:
            return "已是空槽"
        self.slots[idx] = None
        self._bump()
        return "已清空" + self._overrides({idx: f"{actor}清空此键"})

    def reapply(self) -> None:
        """Re-run placement (rules / fill) on the sessions of the last poll, e.g. after a rule change."""
        self.update(list(self.sessions.values()))

    def preview(self, sessions: Iterable[Session] | None = None) -> "SlotManager":
        """Side-effect-free copy (arrange page view of a non-current mode), updated with ``sessions``."""
        sm = SlotManager(slots=[None if e is None else replace(e) for e in self.slots], selected=self.selected,
                         excluded=set(self.excluded), rules=self.rules, waiting=dict(self.waiting))
        if sessions is None:
            sm.sessions = dict(self.sessions)
            sm.overflow, sm.overflow_attention = self.overflow, self.overflow_attention
        else:
            sm.update(sessions)
        return sm

    def move(self, src: int, dst: int, actor: str = "键盘") -> str:
        """Pick-up-and-place (PRD 1.5 delta 2026-10-22): put the entry of ``src`` on ``dst`` and pin it.

        * ``dst == src``: toggle the pin (the only way to pin / unpin; a lost pin is released).
        * ``dst`` occupied: the two swap; the displaced entry keeps its own pin / lost flags.
        * ``dst`` empty: ``src`` becomes empty (the normal fill rules may refill it later).

        The placed entry is pinned, so ``update`` never moves or replaces it; the selection
        follows the session (it is a key, not an index)."""
        if not (0 <= src < N_SLOTS and 0 <= dst < N_SLOTS):
            raise IndexError(f"slot index out of range: {src} -> {dst}")
        if src == dst:
            return self.toggle_pin(src, actor)
        e = self.slots[src]
        if e is None:
            return "空槽不可移动"
        other = self.slots[dst]
        self.slots[dst], self.slots[src] = e, other
        e.pinned = True
        self._bump()
        # A rule slot touched by hand (moved into or out of) stops following its rule.
        tail = self._overrides({src: f"{actor}把此键会话移到 S{dst + 1}",
                                dst: f"{actor}把 S{src + 1} 移到此键"})
        msg = f"已移到 S{dst + 1} · 已钉扎" if other is None else f"已与 S{dst + 1} 交换 · 已钉扎"
        return msg + tail

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "n_slots": N_SLOTS,
            "slots": [None if e is None else
                      {"key": e.key, "pinned": e.pinned, **({"rule": e.rule} if e.rule else {})} for e in self.slots],
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
            rule = e.get("rule") if isinstance(e.get("rule"), str) else ""
            sm.slots[legacy_index(i) if legacy else i] = SlotEntry(str(e["key"]), bool(e.get("pinned")), rule=rule)
        sm.selected = d.get("selected")
        sm.excluded = set(d.get("excluded") or [])
        return sm
