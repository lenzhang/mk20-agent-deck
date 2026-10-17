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


def test_ac15_twelve_sessions_overflow_and_protection():
    m = SlotManager()
    ss = [S(i, State.IDLE, rec=100 - i) for i in range(12)]
    m.update(ss)
    assert m.overflow == 4
    m.select(0)
    assert m.toggle_pin(1) == "已钉扎"
    # a candidate with strictly higher priority replaces the lowest non-protected slot
    ss[11] = replace(ss[11], state=State.NEEDS_APPROVAL)
    m.update(ss)
    keys = [e.key for e in m.slots]
    assert ss[11].sid.key() in keys
    assert ss[0].sid.key() in keys and ss[1].sid.key() in keys  # selected + pinned kept
    assert m.overflow == 4
    # same-priority candidate does not replace
    v = m.mapping_version
    m.update(ss)
    assert m.mapping_version == v


def test_all_pinned_only_overflow_hint():
    m = SlotManager()
    ss = [S(i) for i in range(8)]
    m.update(ss)
    for i in range(8):
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
