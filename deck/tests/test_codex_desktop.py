import json
import os
import sqlite3
import time
import urllib.parse

import pytest

from mk20deck.adapters.base import BackendUnavailable, RecordingRunner, RunResult
from mk20deck.adapters.codex_desktop import (CodexDesktopAdapter, CodexDesktopConfig, SchemaChanged,
                                             build_new_thread_url, build_thread_url, parse_rollout_tail)
from mk20deck.model import State


def mk(home, alive=True, runner=None, **kw):
    return CodexDesktopAdapter(CodexDesktopConfig(codex_home=str(home), **kw), runner=runner or RecordingRunner(),
                               app_alive=lambda: alive)


def by_id(sessions):
    return {s.sid.native[-1]: s for s in sessions}


def test_list_filters_desktop_threads_only(codex_home):
    ss = by_id(mk(codex_home).poll())
    # 7 = codex-tui, 8 = exec, 9 = archived -> excluded; NULL originator (3) kept
    assert set(ss) == {"1", "2", "3", "4", "5", "6", "a"}
    assert ss["2"].title == "文档"  # name > title
    assert ss["1"].effort == "xhigh"
    assert all("来源推定" in s.source_note for s in ss.values())


def test_conservative_state_matrix(codex_home):
    ss = by_id(mk(codex_home).poll())
    assert ss["1"].state == State.RUNNING            # task_started, fresh mtime
    assert ss["2"].state == State.DONE_UNREAD        # complete + in App unread set
    assert ss["3"].state == State.IDLE               # complete, read
    assert ss["4"].state == State.IDLE               # turn_aborted
    assert ss["5"].state == State.UNKNOWN            # stale task_started
    assert ss["6"].state == State.ERROR              # explicit turn error
    assert ss["a"].state == State.UNKNOWN            # no rollout
    assert not ss["a"].cwd_confirmed and ss["1"].cwd_confirmed
    assert ss["2"].done_marker == "t-1"


def test_app_not_running_is_offline(codex_home):
    assert {s.state for s in mk(codex_home, alive=False).poll()} == {State.OFFLINE}


def test_db_opened_read_only(codex_home):
    db = codex_home / "state_5.sqlite"
    before = db.stat().st_mtime_ns, (codex_home / ".codex-global-state.json").read_bytes()
    a = mk(codex_home)
    a.poll()
    a.poll()
    assert db.stat().st_mtime_ns == before[0]
    assert (codex_home / ".codex-global-state.json").read_bytes() == before[1]


def test_schema_change_degrades(codex_home):
    con = sqlite3.connect(codex_home / "state_5.sqlite")
    con.execute("ALTER TABLE threads RENAME COLUMN rollout_path TO rp")
    con.commit()
    con.close()
    with pytest.raises(SchemaChanged):
        mk(codex_home).poll()


def test_missing_store_unavailable(tmp_path):
    with pytest.raises(BackendUnavailable):
        mk(tmp_path).poll()


def test_keep_ids_outside_window_are_reverified(codex_home):
    a = mk(codex_home, max_candidates=2)
    ids = {s.sid.native for s in a.poll()}
    assert len(ids) == 2
    ids2 = {s.sid.native for s in a.poll(keep_ids=["01a10000-0000-7000-8000-000000000006"])}
    assert "01a10000-0000-7000-8000-000000000006" in ids2


def test_tail_parser_ignores_partial_first_line():
    data = b'{"type":"event_msg","payload":{"type":"task_complete"' + b"\n" + \
           json.dumps({"type": "event_msg", "payload": {"type": "task_started", "turn_id": "x"}}).encode()
    assert parse_rollout_tail(data, from_start=False).kind == "task_started"


def test_unread_change_detected(codex_home):
    a = mk(codex_home)
    assert by_id(a.poll())["3"].state == State.IDLE
    p = codex_home / ".codex-global-state.json"
    d = json.loads(p.read_text())
    lst = d["electron-thread-read-state-v1"]["unreadByIdentity"]["a" * 64]["durable:" + "c" * 64]
    lst.append("01a10000-0000-7000-8000-000000000003")
    p.write_text(json.dumps(d))
    st = p.stat()
    os.utime(p, (st.st_atime, st.st_mtime + 5))
    assert by_id(a.poll())["3"].state == State.DONE_UNREAD


def test_deep_links():
    assert build_thread_url("01a10000-0000-7000-8000-000000000001") == \
        "codex://threads/01a10000-0000-7000-8000-000000000001"
    with pytest.raises(ValueError):
        build_thread_url("../../etc")
    u = build_new_thread_url("/Users/dev/a b", "审 PR & 修复\n第二行", 2000, 8000)
    assert u.startswith("codex://threads/new?path=/Users/dev/a%20b&prompt=")
    q = urllib.parse.parse_qs(urllib.parse.urlparse(u).query)
    assert q["prompt"] == ["审 PR & 修复\n第二行"] and q["path"] == ["/Users/dev/a b"]


def test_prompt_bounds_fail_not_truncate():
    with pytest.raises(ValueError, match="不截断"):
        build_new_thread_url("/a", "x" * 2001, 2000, 100000)
    with pytest.raises(ValueError):
        build_new_thread_url("/a", "bad\x07", 2000, 8000)
    with pytest.raises(ValueError, match="路径"):
        build_new_thread_url("rel/path", "x", 2000, 8000)
    with pytest.raises(ValueError, match="链接过长"):
        build_new_thread_url("/a", "中" * 1500, 2000, 8000)


def test_focus_and_skill_actions_use_open(codex_home):
    r = RecordingRunner()
    a = mk(codex_home, runner=r)
    ss = by_id(a.poll())
    assert a.focus(ss["1"]).ok
    assert r.calls[-1] == ["open", "codex://threads/01a10000-0000-7000-8000-000000000001"]
    res = a.skill(ss["1"], "请审查")
    assert res.ok and r.calls[-1][1].startswith("codex://threads/new?path=/Users/dev/proj/a&prompt=")
    # relative cwd -> skill refused, nothing opened
    n = len(r.calls)
    assert not a.skill(ss["a"], "x").ok and len(r.calls) == n
    assert not a.skill(None, "x").ok


def test_open_failure_reported(codex_home):
    r = RecordingRunner({("open",): RunResult(1, "", "LSOpenURLsWithRole() failed")})
    a = mk(codex_home, runner=r)
    s = by_id(a.poll())["1"]
    assert not a.focus(s).ok


def test_capabilities_p0():
    c = CodexDesktopAdapter(CodexDesktopConfig(codex_home="/nonexistent"), runner=RecordingRunner(),
                            app_alive=lambda: True).capabilities()
    assert c.focus is None and c.skill_new_thread is None
    assert c.approve and c.send and c.stop and c.effort_set == "此会话不支持"
