"""Slot rules engine (固定规则, user decision 2026-10-22): matching, reserved slots, precedence, conflicts,
persistence and malformed files.  Web/API side: test_arrange_web.py."""
import json
import os
import stat

import pytest

from mk20deck.config import DeckConfig
from mk20deck.controller import Controller, slot_pos
from mk20deck.model import Mode, Session, SessionId, State, Tool
from mk20deck.render.keys import render_key
from mk20deck.render.subscreen import render_sub
from mk20deck.rules import (RulesStore, SlotRule, default_rules_path, dir_name, resolve, validate,
                            worktree_root)
from mk20deck.sim import FakeClock, full_adapters
from mk20deck.slots import N_SLOTS, SlotManager
from mk20deck.webui import ArrangeService, LoopCalls


def T(i, title=None, cwd=None, window="", sess="", tool=Tool.CCAI, st=State.IDLE, rec=0):
    return Session(SessionId(Mode.TMUX, f"%{i}", "g1"), tool, title or f"t{i}", state=st, cwd=cwd,
                   updated_ms=rec, window=window, tmux_session=sess)


def D(i, title, cwd=None, st=State.IDLE, rec=0):
    return Session(SessionId(Mode.DESKTOP, f"thread-{i}"), Tool.CODEX, title, state=st, cwd=cwd, updated_ms=rec)


def R(slot, kind, value, mode=Mode.TMUX, rid=None, enabled=True, created=0.0):
    slot, kind, value = validate(mode, slot, kind, value)
    return SlotRule(rid or f"r{slot}{kind}", mode, slot, kind, value, enabled, "", created)


def keys(m):
    return [e.key if e else None for e in m.slots]


def k(s):
    return s.sid.key()


# ------------------------------------------------------------------ matching
def test_match_cwd_is_a_path_prefix_on_component_boundaries():
    r = R(0, "cwd", "/Users/dev/worktrees/mk20-arrange/")
    assert r.value == "/Users/dev/worktrees/mk20-arrange"
    assert r.matches(T(1, cwd="/Users/dev/worktrees/mk20-arrange"))
    assert r.matches(T(1, cwd="/Users/dev/worktrees/mk20-arrange/deck/mk20deck"))
    assert not r.matches(T(1, cwd="/Users/dev/worktrees/mk20-arrange2"))
    assert not r.matches(T(1, cwd=None))
    home = R(0, "cwd", "~/mk20-full")
    assert home.value == os.path.expanduser("~/mk20-full")
    assert home.matches(T(1, cwd=os.path.expanduser("~/mk20-full/deck")))


def test_match_tmux_window_or_session_name_exact_or_glob():
    r = R(0, "window", "ag-ccai-deck")
    assert r.matches(T(1, window="ag-ccai-deck"))
    assert r.matches(T(1, window="bash", sess="ag-ccai-deck"))  # tmux session name counts too
    assert not r.matches(T(1, window="ag-ccai-deck2"))
    g = R(1, "window", "ag-kimi-*")
    assert g.matches(T(1, window="ag-kimi-x")) and not g.matches(T(1, window="ag-ccai-x"))


def test_match_title_substring_case_insensitive():
    r = R(0, "title", "prd", mode=Mode.DESKTOP)
    assert r.matches(D(1, "PRD整合")) and r.matches(D(2, "写 prd"))
    assert not r.matches(D(3, "评审R2"))


def test_match_tool():
    r = R(0, "tool", "KIMI")
    assert r.value == "kimi"
    assert r.matches(T(1, tool=Tool.KIMI)) and not r.matches(T(1, tool=Tool.CCAI))


@pytest.mark.parametrize("mode,slot,kind,value,msg", [
    (Mode.DESKTOP, 0, "window", "x", "tmux"),
    (Mode.TMUX, 0, "tool", "grok", "ccai"),
    (Mode.TMUX, 0, "cwd", "relative/dir", "绝对路径"),
    (Mode.TMUX, 0, "title", "", "不能为空"),
    (Mode.TMUX, 0, "title", "a\nb", "控制字符"),
    (Mode.TMUX, 0, "title", "x" * 201, "太长"),
    (Mode.TMUX, 15, "title", "x", "键位"),
    (Mode.TMUX, True, "title", "x", "键位"),
    (Mode.TMUX, 0, "regex", "x", "匹配方式"),
    (Mode.TMUX, 0, "title", 5, "文字"),
])
def test_validate_rejects(mode, slot, kind, value, msg):
    with pytest.raises(ValueError, match=msg):
        validate(mode, slot, kind, value)


def test_dir_name_and_worktree_root_never_need_more_than_a_name():
    assert worktree_root("/Users/dev/worktrees/mk20-arrange/deck/x") == "/Users/dev/worktrees/mk20-arrange"
    assert worktree_root("/Users/dev/mk20-full/deck") == "/Users/dev/mk20-full/deck"
    assert dir_name("/Users/dev/worktrees/mk20-arrange/deck") == "mk20-arrange"
    assert dir_name("/Users/dev/mk20-full/deck") == "deck"
    assert R(0, "cwd", "/Users/dev/secret/proj").label() == "目录 proj"


# ------------------------------------------------------------------ placement
def test_rule_places_matching_session_pinned_on_its_slot():
    m = SlotManager()
    ss = [T(i, rec=100 - i) for i in range(5)]
    m.update(ss)
    assert keys(m)[:5] == [k(s) for s in ss]
    m.set_rules([R(9, "title", "t3")])
    m.update(ss)
    e = m.slots[9]
    assert e.key == k(ss[3]) and e.pinned and e.rule == "r9title"
    assert k(ss[3]) not in keys(m)[:9]  # moved, not duplicated
    assert m.waiting == {}
    v = m.mapping_version
    m.update(ss)  # stable
    assert m.mapping_version == v


def test_reserved_slot_waits_and_auto_fill_never_uses_it():
    m = SlotManager()
    m.set_rules([R(2, "window", "ag-ccai-deck")])
    ss = [T(i, rec=100 - i) for i in range(20)]
    m.update(ss)
    assert m.slots[2] is None
    assert m.waiting_label(2) == "等待：ag-ccai…"
    assert sum(1 for e in m.slots if e) == N_SLOTS - 1
    assert m.overflow == 20 - (N_SLOTS - 1)
    # the session appears -> placed (pinned); it vanishes -> reserved again (never a lost pin)
    w = T(99, window="ag-ccai-deck", rec=1)
    m.update(ss + [w])
    assert m.slots[2].key == k(w) and m.slots[2].pinned and not m.slots[2].lost
    assert m.waiting_label(2) == ""
    m.update(ss)
    assert m.slots[2] is None and m.waiting_label(2).startswith("等待：")


def test_rules_beat_manual_pins_and_the_pin_moves_with_a_swap():
    m = SlotManager()
    ss = [T(i, title=f"s{i}", rec=100 - i) for i in range(6)]
    m.update(ss)
    m.move(2, 2)  # pin s2 on S3 by hand
    assert m.slots[2].pinned and m.slots[2].key == k(ss[2])
    m.set_rules([R(2, "title", "s5")])
    m.update(ss)
    assert m.slots[2].key == k(ss[5]) and m.slots[2].rule
    # the manual pin went to the slot the rule's session came from (S6) and kept its pin
    assert m.slots[5].key == k(ss[2]) and m.slots[5].pinned and not m.slots[5].rule


def test_manual_pins_beat_auto_fill_next_to_rules():
    m = SlotManager()
    ss = [T(i, title=f"s{i}", rec=100 - i) for i in range(16)]
    m.update(ss)
    m.move(4, 0)  # pin s4 on S1 (s0 swaps to S5)
    m.set_rules([R(1, "title", "s15")])  # overflowed session gets S2
    hot = ss[:15] + [T(15, title="s15", st=State.NEEDS_APPROVAL, rec=1)]
    m.update(hot)
    assert m.slots[0].key == k(ss[4]) and m.slots[0].pinned
    assert m.slots[1].key == k(hot[15]) and m.slots[1].rule
    assert m.overflow == 1  # s1 (auto, displaced by the rule) waits for a free slot


def test_a_session_matching_several_rules_goes_to_the_lowest_slot():
    m = SlotManager()
    x = T(1, title="deck", window="ag-ccai-deck", rec=50)
    m.set_rules([R(7, "window", "ag-ccai-deck"), R(3, "title", "deck")])
    m.update([x, T(2, rec=10)])
    assert m.slots[3].key == k(x)
    assert m.slots[7] is None and m.waiting_label(7)


def test_two_sessions_one_rule_most_recent_wins_other_auto_fills():
    m = SlotManager()
    m.set_rules([R(4, "tool", "kimi")])
    a, b = T(1, tool=Tool.KIMI, rec=200), T(2, tool=Tool.KIMI, rec=100)
    m.update([a, b])
    assert m.slots[4].key == k(a)
    ib = keys(m).index(k(b))
    assert ib != 4 and not m.slots[ib].pinned
    # b becomes the most recently active: it takes the rule slot, a falls back to auto fill (unpinned)
    a2, b2 = T(1, tool=Tool.KIMI, rec=200), T(2, tool=Tool.KIMI, rec=300)
    m.update([a2, b2])
    assert m.slots[4].key == k(b2)
    ia = keys(m).index(k(a2))
    assert ia != 4 and not m.slots[ia].pinned and not m.slots[ia].rule


def test_resolve_tie_keeps_the_current_holder():
    a, b = T(1, tool=Tool.KIMI, rec=5), T(2, tool=Tool.KIMI, rec=5)
    r = R(0, "tool", "kimi")
    placed, _ = resolve([r], {k(a): a, k(b): b}, holder={0: k(a)})
    assert placed[0][1] == k(a)
    placed, _ = resolve([r], {k(a): a, k(b): b}, holder={0: k(b)})
    assert placed[0][1] == k(b)


def test_disabled_or_deleted_rule_leaves_an_ordinary_pin():
    m = SlotManager()
    ss = [T(i, title=f"s{i}") for i in range(3)]
    m.set_rules([R(8, "title", "s1")])
    m.update(ss)
    assert m.slots[8].rule
    m.set_rules([])
    m.update(ss)
    assert m.slots[8].key == k(ss[1]) and m.slots[8].pinned and not m.slots[8].rule
    m.update(ss[:1])  # now it is a plain pin: vanishing makes it a lost pin, as before
    assert m.slots[8].lost


def test_rules_are_per_mode():
    m_desk, m_tmux = SlotManager(), SlotManager()
    m_tmux.set_rules([R(0, "tool", "kimi")])
    d = [D(1, "kimi notes")]
    m_desk.update(d)
    assert m_desk.waiting == {} and m_desk.slots[0].key == k(d[0])


def test_rule_marker_survives_state_roundtrip():
    m = SlotManager()
    m.set_rules([R(3, "title", "s1")])
    m.update([T(1, title="s1")])
    d = m.to_dict()
    assert d["slots"][3] == {"key": k(T(1)), "pinned": True, "rule": "r3title"}
    assert "rule" not in json.dumps(d["slots"][:3])
    m2 = SlotManager.from_dict(json.loads(json.dumps(d)))
    assert m2.slots[3].rule == "r3title" and m2.slots[3].pinned


# ------------------------------------------------------------------ manual changes disable the slot's rule
def test_move_into_or_out_of_a_rule_slot_disables_the_rule():
    seen = []
    m = SlotManager(on_rule_override=lambda r, why: seen.append((r.slot, why)))
    ss = [T(i, title=f"s{i}", rec=100 - i) for i in range(4)]
    m.set_rules([R(9, "title", "s0"), R(12, "title", "nothing")])
    m.update(ss)
    assert keys(m)[:3] == [k(ss[1]), k(ss[2]), k(ss[3])] and m.slots[9].key == k(ss[0])
    msg = m.move(1, 12)  # keyboard: into the reserved slot
    assert msg.endswith("规则已停用") and seen == [(12, "键盘把 S2 移到此键")]
    msg = m.move(9, 0)  # out of the rule slot
    assert "规则已停用" in msg and seen[-1] == (9, "键盘把此键会话移到 S1")
    assert m.rules == ()
    m.update(ss)  # nothing pulls the sessions back
    assert m.slots[12].key == k(ss[2]) and m.slots[0].key == k(ss[0]) and m.slots[9].key == k(ss[1])


def test_toggle_pin_on_a_rule_slot_disables_the_rule():
    seen = []
    m = SlotManager(on_rule_override=lambda r, why: seen.append(why))
    m.set_rules([R(0, "title", "s1")])
    m.update([T(1, title="s1")])
    assert m.toggle_pin(0) == "已取消钉扎 · 规则已停用"
    assert seen == ["键盘切换钉住"] and not m.slots[0].pinned


def test_place_unpin_clear():
    m = SlotManager()
    ss = [T(i, title=f"s{i}", rec=100 - i) for i in range(4)]
    m.update(ss)
    extra = T(9, title="s9")
    m.sessions[k(extra)] = extra
    m.move(0, 0)  # pin s0 on S1
    assert m.place(k(extra), 0) == "已放到 S1 · 已钉扎"  # pinned occupant goes to the first free slot
    assert m.slots[0].key == k(extra) and m.slots[4].key == k(ss[0]) and m.slots[4].pinned
    assert m.place(k(extra), 0) == "已在 S1"
    assert m.place(k(ss[2]), 0).startswith("已与 S1 交换")  # in a slot: move semantics (swap)
    assert m.unpin(0) == "已取消钉住" and not m.slots[0].pinned
    assert m.unpin(0) == "未钉住"
    assert m.clear(0) == "已清空" and m.slots[0] is None
    assert m.clear(0) == "已是空槽"


# ------------------------------------------------------------------ store
def test_store_roundtrip_atomic_0600(tmp_path):
    p = tmp_path / "home" / "slot-rules.json"
    st = RulesStore(str(p), clock=lambda: 123.0)
    r = st.put(Mode.TMUX, 4, "window", "ag-ccai-*")
    st.put(Mode.DESKTOP, 0, "title", "PRD")
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    d = json.loads(p.read_text())
    assert d["version"] == 1 and len(d["rules"]) == 2
    assert [x.name for x in p.parent.iterdir()] == ["slot-rules.json"]  # no temp files left
    st2 = RulesStore(str(p))
    assert st2.warning == "" and st2.get(r.id) == r
    # one rule per (mode, slot): put replaces and keeps the id
    r2 = st2.put(Mode.TMUX, 4, "tool", "codex")
    assert r2.id == r.id and len(st2.for_mode(Mode.TMUX)) == 1
    st2.disable(r.id, "键盘把 S2 移到此键（10-22 14:03）")
    assert RulesStore(str(p)).get(r.id).note.startswith("键盘")
    st2.update(r.id, enabled=True)
    assert RulesStore(str(p)).get(r.id).note == ""
    st2.delete(r.id)
    assert RulesStore(str(p)).get(r.id) is None


@pytest.mark.parametrize("content,frag", [
    ("{not json", "格式错误"),
    ('{"version": 2, "rules": []}', "版本"),
    ('["a"]', "格式错误"),
    ('{"version": 1}', "格式错误"),
    (b"\xff\xfe", "格式错误"),
])
def test_malformed_file_is_ignored_with_a_warning_never_a_crash(tmp_path, content, frag):
    p = tmp_path / "slot-rules.json"
    (p.write_bytes if isinstance(content, bytes) else p.write_text)(content)
    st = RulesStore(str(p))
    assert st.rules == [] and frag in st.warning
    # the first save keeps the bad file (moved aside) instead of overwriting it silently
    st.put(Mode.TMUX, 0, "tool", "ccai")
    assert st.warning == ""
    bad = [x for x in tmp_path.iterdir() if x.name.startswith("slot-rules.json.bad-")]
    assert len(bad) == 1
    assert len(RulesStore(str(p)).rules) == 1


def test_invalid_entries_are_skipped_individually(tmp_path):
    p = tmp_path / "slot-rules.json"
    good = {"id": "r1", "mode": "tmux", "slot": 1, "kind": "tool", "value": "ccai", "enabled": True}
    p.write_text(json.dumps({"version": 1, "rules": [
        good, dict(good, id="r2"),  # duplicate (mode, slot)
        {"id": "r3", "mode": "nope", "slot": 1, "kind": "tool", "value": "ccai"},
        {"id": "r4", "mode": "codex_desktop", "slot": 1, "kind": "window", "value": "x"},
        {"id": "../x", "mode": "tmux", "slot": 2, "kind": "tool", "value": "ccai"},
        "junk"]}))
    st = RulesStore(str(p))
    assert [r.id for r in st.rules] == ["r1"]
    assert "5 条无效规则" in st.warning


def test_default_path_honours_mk20deck_home(monkeypatch, tmp_path):
    monkeypatch.setenv("MK20DECK_HOME", str(tmp_path / "h"))
    assert default_rules_path() == str(tmp_path / "h" / "slot-rules.json")
    assert RulesStore().path == str(tmp_path / "h" / "slot-rules.json")


# ------------------------------------------------------------------ keyboard side (controller + render)
@pytest.fixture
def deck(tmp_path):
    clk = FakeClock()
    ads = full_adapters()
    saved = []
    c = Controller(DeckConfig(), ads, clock=clk, wall=lambda: 1792244000.0, sim=True, on_state_change=saved.append)
    c.refresh()
    svc = ArrangeService(c, LoopCalls(), RulesStore(str(tmp_path / "slot-rules.json")), wall=lambda: 1792244000.0)
    return c, clk, ads, svc, saved


def test_keyboard_key_shows_waiting_placeholder(deck):
    c, clk, ads, svc, _ = deck
    svc.store.put(Mode.DESKTOP, 4, "title", "不存在的线程")
    svc.push_rules()
    c.mgr.reapply()
    kv = next(kv for kv in c.view().keys if (kv.col, kv.row) == slot_pos(4))
    assert kv.state == State.UNASSIGNED and kv.label == "等待：不存在…"
    render_key(kv, 0.0, 0.0, animations=False)
    assert c.mgr.overflow == 1  # 15 sessions, 14 usable slots


@pytest.mark.parametrize("value,label", [("周报", "周报"), ("不存在的线程", "不存在…"), ("ag-ccai-deck", "ag-ccai…"),
                                         ("ccai", "ccai"), ("OTA发布v2", "OTA发布v2"), ("OTA发布v2x", "OTA发布…")])
def test_waiting_label_fits_the_key_face(value, label):
    from PIL import Image, ImageDraw
    from mk20deck.render.fonts import font, text_w
    r = R(0, "title", value, mode=Mode.DESKTOP)
    assert r.short_label() == label
    d = ImageDraw.Draw(Image.new("RGB", (128, 128)))
    assert text_w(d, f"等待：{label}", font(15)) <= 124


def test_keyboard_move_into_rule_slot_disables_and_persists_the_rule(deck):
    c, clk, ads, svc, saved = deck
    r = svc.store.put(Mode.DESKTOP, 4, "title", "不存在的线程")
    svc.push_rules()
    c.mgr.reapply()
    # long press S1, then press S5 (the reserved slot)
    c.on_key(1, 1, True)
    for _ in range(14):
        clk.advance(0.05)
        c.tick()
    c.on_key(1, 1, False)
    c.on_key(*slot_pos(4), True)
    c.on_key(*slot_pos(4), False)
    assert c.toast[0].endswith("规则已停用")
    disk = RulesStore(svc.store.path).get(r.id)
    assert not disk.enabled and disk.note.startswith("键盘把 S1 移到此键（")
    c.refresh()
    assert c.mgr.slots[4].pinned and c.mgr.waiting == {}


def test_malformed_rules_warning_reaches_the_sub_screen(tmp_path):
    p = tmp_path / "slot-rules.json"
    p.write_text("{oops")
    c = Controller(DeckConfig(), full_adapters(), clock=FakeClock(), sim=True)
    c.refresh()
    ArrangeService(c, LoopCalls(), RulesStore(str(p)))
    sv = c.view().sub
    assert sv.note.startswith("规则文件格式错误")
    render_sub(sv, 0.0)
    c.mgr.select(0)
    render_sub(c.view().sub, 0.0)
