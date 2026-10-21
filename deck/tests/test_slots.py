from dataclasses import replace

from mk20deck.model import Mode, Session, SessionId, State, Tool
from mk20deck.slots import SlotManager


def S(i, st=State.IDLE, gen="", rec=0):
    return Session(SessionId(Mode.TMUX, f"%{i}", gen), Tool.CODEX, f"s{i}", state=st, updated_ms=rec)


def test_priority_initial_fill_then_sticky():
    m = SlotManager()
    ss = [S(1, State.IDLE), S(2, State.NEEDS_APPROVAL), S(3, State.RUNNING), S(4, State.ERROR)]
    m.update(ss)
    order = [e.key.split(":")[-1] for e in m.slots if e]
    assert order == ["%2", "%4", "%3", "%1"]
    # state changes do not reorder occupied slots
    ss2 = [replace(s, state=State.IDLE) for s in ss]
    ss2[3] = replace(ss[3], state=State.NEEDS_APPROVAL)
    m.update(ss2)
    assert [e.key.split(":")[-1] for e in m.slots if e] == order


def test_ac15_overflow_and_protection():
    m = SlotManager()
    ss = [S(i, State.IDLE, rec=100 - i) for i in range(19)]  # 15 slots + 4
    m.update(ss)
    assert m.overflow == 4
    m.select(0)
    assert m.toggle_pin(1) == "已钉扎"
    # a candidate with strictly higher priority replaces the lowest non-protected slot
    ss[18] = replace(ss[18], state=State.NEEDS_APPROVAL)
    m.update(ss)
    keys = [e.key for e in m.slots]
    assert ss[18].sid.key() in keys
    assert ss[0].sid.key() in keys and ss[1].sid.key() in keys  # selected + pinned kept
    assert m.overflow == 4
    # same-priority candidate does not replace
    v = m.mapping_version
    m.update(ss)
    assert m.mapping_version == v


def test_all_pinned_only_overflow_hint():
    m = SlotManager()
    ss = [S(i) for i in range(15)]
    m.update(ss)
    for i in range(15):
        m.toggle_pin(i)
    m.update(ss + [S(99, State.NEEDS_APPROVAL)])
    assert S(99).sid.key() not in [e.key for e in m.slots]
    assert m.overflow == 1 and m.overflow_attention == 1


def test_vanished_selected_cleared_no_autoselect_and_lost_pin():
    m = SlotManager()
    ss = [S(i) for i in range(3)]
    m.update(ss)
    m.select(0)
    m.toggle_pin(1)
    m.update([ss[2]])
    assert m.selected is None and m.selection_lost
    assert m.slots[0] is None
    assert m.slots[1].lost and m.session_at(1) is None
    assert m.toggle_pin(1) == "已解除失联钉扎" and m.slots[1] is None


def test_restore_and_id_reuse_not_misbound():
    m = SlotManager()
    ss = [S(1, gen="g1"), S(2, gen="g1")]
    m.update(ss)
    m.toggle_pin(0)
    m.select(1)
    d = m.to_dict()
    m2 = SlotManager.from_dict(d)
    # tmux server restarted: same %1 pane id but new generation -> must not rebind
    m2.update([S(1, gen="g2"), S(2, gen="g1")])
    assert m2.slots[0].lost
    assert m2.selected == S(2, gen="g1").sid.key()
    assert S(1, gen="g2").sid.key() in [e.key for e in m2.slots if e]


# ------------------------------------------------------------------ PRD 1.5: 15 slots
from mk20deck.slots import N_SLOTS, legacy_index  # noqa: E402


def test_fifteen_slots_fill_in_priority_then_sticky():
    assert N_SLOTS == 15
    m = SlotManager()
    ss = [S(i, State.IDLE, rec=1000 - i) for i in range(13)] + [S(90, State.NEEDS_APPROVAL), S(91, State.ERROR)]
    m.update(ss)
    keys = [e.key.split(":")[-1] for e in m.slots]
    assert len(keys) == 15 and keys[:2] == ["%90", "%91"] and keys[2:] == [f"%{i}" for i in range(13)]
    assert m.overflow == 0
    flipped = [replace(s, state=State.NEEDS_INPUT) if s.sid.native == "%12" else
               replace(s, state=State.IDLE) for s in ss]
    v = m.mapping_version
    m.update(flipped)  # state changes never reorder occupied slots
    assert [e.key.split(":")[-1] for e in m.slots] == keys and m.mapping_version == v


def test_fifteen_slots_overflow_strictly_higher_replaces_unprotected():
    m = SlotManager()
    ss = [S(i, State.IDLE, rec=100 - i) for i in range(19)]
    m.update(ss)
    assert m.overflow == 4 and m.overflow_attention == 0
    m.select(14)
    assert m.toggle_pin(13) == "已钉扎"
    ss[18] = replace(ss[18], state=State.NEEDS_APPROVAL)
    m.update(ss)
    keys = [e.key for e in m.slots]
    assert ss[18].sid.key() in keys
    assert ss[13].sid.key() in keys and ss[14].sid.key() in keys  # pinned + selected kept
    assert keys.index(ss[18].sid.key()) == 12  # the lowest non-protected slot (last one wins ties)
    assert m.overflow == 4
    ss.append(S(77, State.ERROR))
    for i in range(15):
        if m.slots[i] and not m.protected(m.slots[i]):
            m.toggle_pin(i)
    m.update(ss)  # every slot protected: only the overflow hint changes
    assert S(77).sid.key() not in [e.key for e in m.slots]
    assert m.overflow == 5 and m.overflow_attention == 1


def test_legacy_index_preserves_physical_key():
    assert [legacy_index(i) for i in range(8)] == [0, 1, 2, 3, 5, 6, 7, 8]


def test_from_dict_migrates_8_slot_state_and_roundtrips():
    old = {"slots": [{"key": f"tmux:g:%{i}", "pinned": i == 4} for i in range(8)],
           "selected": "tmux:g:%7", "excluded": ["tmux:g:%99"]}
    m = SlotManager.from_dict(old)
    assert len(m.slots) == 15
    assert [e.key if e else None for e in m.slots] == (
        ["tmux:g:%0", "tmux:g:%1", "tmux:g:%2", "tmux:g:%3", None,
         "tmux:g:%4", "tmux:g:%5", "tmux:g:%6", "tmux:g:%7", None] + [None] * 5)
    assert m.slots[5].pinned and m.selected == "tmux:g:%7" and m.excluded == {"tmux:g:%99"}
    d = m.to_dict()
    assert d["n_slots"] == 15 and len(d["slots"]) == 15
    m2 = SlotManager.from_dict(d)  # new-format file: indices kept as-is
    assert [e.key if e else None for e in m2.slots] == [e.key if e else None for e in m.slots]
    assert m2.slots[5].pinned


def test_from_dict_tolerates_short_long_and_junk_entries():
    m = SlotManager.from_dict({"slots": [{"key": "a:b:c"}, None, "junk", {"pinned": True}]})
    assert m.slots[0].key == "a:b:c" and all(e is None for e in m.slots[1:])
    long = {"n_slots": 15, "slots": [{"key": f"x:y:{i}"} for i in range(20)]}
    m = SlotManager.from_dict(long)
    assert len(m.slots) == 15 and m.slots[14].key == "x:y:14"
    dup = {"n_slots": 15, "slots": [{"key": "x:y:1"}, {"key": "x:y:1", "pinned": True}]}
    m = SlotManager.from_dict(dup)
    assert m.slots[0].key == "x:y:1" and m.slots[1] is None  # one session never occupies two keys
    legacy_long = {"slots": [{"key": f"x:y:{i}"} for i in range(12)]}
    m = SlotManager.from_dict(legacy_long)  # a legacy file never had more than 8: extra entries dropped
    assert sum(1 for e in m.slots if e) == 8 and m.slots[4] is None
