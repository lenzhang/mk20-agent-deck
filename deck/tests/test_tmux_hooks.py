"""tmux discovery + state from hook records (fake runner, temp state dir)."""
import json
import os

import pytest

from mk20deck import agentstate
from mk20deck.adapters.base import RecordingRunner, RunResult
from mk20deck.adapters.tmux import TmuxAdapter, TmuxConfig, title_from_cwd
from mk20deck.config import DeckConfig
from mk20deck.controller import Controller
from mk20deck.model import Mode, State, Tool
from mk20deck.sim import FakeClock, demo_adapters

SOCK = "/private/tmp/tmux-501/default"
NOW = 1_792_500_000.0
# session, window idx, window name, pane, title, path, command, pane_pid
PANES = [
    ("0", "0", "bash", "%0", "h", "/Users/dev/worktrees/mk20-k4-install", "bash", "100"),   # ccai -> bash wrapper
    ("0", "1", "zsh", "%1", "h", "/Users/dev/worktrees/kbd-ui/deck", "codex", "200"),
    ("0", "2", "zsh", "%2", "h", "/Users/dev/notes", "kimi", "300"),
    ("0", "3", "zsh", "%3", "h", "/Users/dev", "zsh", "400"),                               # plain shell
    ("0", "4", "node", "%4", "h", "/Users/dev/web", "node", "500"),                          # node w/o record
    ("0", "5", "py", "%5", "h", "/Users/dev/x", "python3", "600"),                          # python + record
    ("0", "6", "logs", "%6", "h", "/Users/dev", "tail", "700"),
]
# pid -> ppid: pane shell 100 -> ccai bash 101 -> claude 102 -> codex exec 103
PS = {101: 100, 102: 101, 103: 102, 201: 200, 601: 600, 9999: 1, 701: 700}


def runner(panes=PANES, ps=PS, ps_rc=0):
    return RecordingRunner({
        ("tmux", "display-message"): RunResult(0, f"4242-1700000000\t{SOCK}\n", ""),
        ("tmux", "list-panes"): RunResult(0, "\n".join("\t".join(p) for p in panes), ""),
        ("tmux", "list-clients"): RunResult(0, "client0\n", ""),
        ("ps",): RunResult(ps_rc, "\n".join(f"{p} {pp}" for p, pp in ps.items()), ""),
    }, binaries={"tmux"})


@pytest.fixture
def sdir(tmp_path):
    d = tmp_path / "agent-state"
    d.mkdir(mode=0o700)
    return d


def put(sdir, tool, sid, pane, state, ts=NOW - 5, ppid=102, sock=SOCK, cwd="/Users/dev/worktrees/mk20-k4-install"):
    p = sdir / f"{tool}-{sid}.json"
    p.write_text(json.dumps({"tool": tool, "session_id": sid, "tmux_pane": pane, "tmux_socket": sock, "cwd": cwd,
                             "state": state, "ts": ts, "ppid": ppid}))
    p.chmod(0o600)
    return p


def adapter(sdir, alive=None, r=None, **kw):
    alive = alive or (lambda pid: pid in PS or pid in (100, 200))
    cfg = TmuxConfig(state_dir=str(sdir), **kw)
    return TmuxAdapter(cfg, r or runner(), clock=lambda: NOW, pid_alive=alive)


def by_pane(a):
    return {s.sid.native: s for s in a.poll()}


def test_discovery_by_command_and_record(sdir):
    put(sdir, "claude", "c0", "%0", "needs_approval")
    put(sdir, "claude", "c5", "%5", "running", ppid=601, cwd="/Users/dev/x")
    a = adapter(sdir)
    ss = by_pane(a)
    assert set(ss) == {"%0", "%1", "%2", "%5"}  # zsh / node without record / tail are not agents
    assert ss["%0"].tool == Tool.CCAI and ss["%0"].state == State.NEEDS_APPROVAL
    assert ss["%0"].source_note == "hook 关联" and "Claude hook" in ss["%0"].state_reason
    assert ss["%1"].tool == Tool.CODEX and ss["%1"].state == State.UNKNOWN and ss["%1"].source_note == "命令线索发现"
    assert ss["%2"].tool == Tool.KIMI and ss["%2"].state == State.UNKNOWN
    assert ss["%5"].state == State.RUNNING and ss["%5"].tool == Tool.CCAI  # python wrapper mapped by the hook
    assert ss["%0"].title == "mk20-k4-install" and ss["%1"].title == "kbd-ui" and ss["%2"].title == "notes"
    assert a.status_limited() == "状态来自 Claude/codex hooks；2 个窗格无 hook"
    assert a.capabilities().precise_state is None


@pytest.mark.parametrize("state,want", [("running", State.RUNNING), ("needs_input", State.NEEDS_INPUT),
                                        ("done", State.DONE_UNREAD), ("error", State.ERROR), ("idle", State.IDLE)])
def test_state_mapping(sdir, state, want):
    put(sdir, "claude", "c0", "%0", state)
    s = by_pane(adapter(sdir))["%0"]
    assert s.state == want and s.updated_ms == int((NOW - 5) * 1000)
    assert (s.done_marker == f"claude:c0:{NOW - 5:.3f}") == (want == State.DONE_UNREAD)


def test_invalid_records_ignored(sdir):
    put(sdir, "claude", "dead", "%0", "needs_approval", ppid=31337)          # agent process gone
    put(sdir, "claude", "gone", "%42", "needs_approval")                    # pane no longer exists
    put(sdir, "claude", "other", "%0", "needs_approval", sock="/tmp/tmux-501/other")  # other tmux server
    put(sdir, "claude", "old", "%0", "needs_approval", ts=NOW - 13 * 3600)  # older than hook_max_age_s
    put(sdir, "claude", "rm", "%0", "removed", ts=NOW)
    put(sdir, "claude", "reuse", "%0", "needs_approval", ppid=9999)         # pid alive but outside the pane
    (sdir / "claude-junk.json").write_text("{not json")
    (sdir / "codex-wrongtool.json").write_text(json.dumps({"tool": "claude", "session_id": "x", "state": "done",
                                                          "tmux_pane": "%0", "ts": NOW, "ppid": 102}))
    (sdir / "claude-badstate.json").write_text(json.dumps({"tool": "claude", "session_id": "x", "state": "hacked",
                                                          "tmux_pane": "%0", "ts": NOW, "ppid": 102}))
    a = adapter(sdir)
    ss = by_pane(a)
    assert "%0" not in ss and "%42" not in ss  # only the record could have listed the bash pane
    assert a.status_limited().startswith("状态受限") and a.capabilities().precise_state


def test_running_goes_stale(sdir):
    put(sdir, "claude", "c0", "%0", "running", ts=NOW - 901)
    s = by_pane(adapter(sdir))["%0"]
    assert s.state == State.UNKNOWN and "过期" in s.state_reason


def test_nested_codex_exec_does_not_hide_claude(sdir):
    put(sdir, "claude", "c0", "%0", "running", ts=NOW - 30, ppid=102)
    put(sdir, "codex", "x0", "%0", "done", ts=NOW - 1, ppid=103)  # newer, but a child of the claude session
    s = by_pane(adapter(sdir))["%0"]
    assert s.tool == Tool.CCAI and s.state == State.RUNNING


def test_freshest_wins_without_process_table(sdir):
    put(sdir, "claude", "c0", "%0", "running", ts=NOW - 30, ppid=102)
    put(sdir, "claude", "c1", "%0", "done", ts=NOW - 1, ppid=102)  # /clear: same process, new session
    s = by_pane(adapter(sdir, r=runner(ps_rc=1)))["%0"]
    assert s.state == State.DONE_UNREAD


def test_use_hooks_off_and_foreign_dir(sdir):
    put(sdir, "claude", "c0", "%0", "needs_approval")
    assert "%0" not in by_pane(adapter(sdir, use_hooks=False))
    sdir.chmod(0o777)
    try:
        assert "%0" not in by_pane(adapter(sdir))  # world-writable state dir is never trusted
    finally:
        sdir.chmod(0o700)


def test_old_seven_field_lines_and_names_still_work(sdir):
    panes = ["ag-ccai-api\t0\tmain\t%1\thost\t/Users/dev/api\tnode", "w\t1\tag-kimi-x\t%2\th\t/Users/dev/k\tnode"]
    r = RecordingRunner({("tmux", "display-message"): RunResult(0, "4242-1700000000\n", ""),
                         ("tmux", "list-panes"): RunResult(0, "\n".join(panes), "")}, binaries={"tmux"})
    ss = {s.sid.native: s for s in TmuxAdapter(TmuxConfig(state_dir=str(sdir)), r).poll()}
    assert ss["%1"].tool == Tool.CCAI and ss["%2"].tool == Tool.KIMI and ss["%1"].title == "api"


def test_title_from_cwd():
    assert title_from_cwd("/Users/dev/worktrees/mk20-deck-attn/deck") == "mk20-deck-attn"
    assert title_from_cwd("/repo/.claude/worktrees/feat-x/src") == "feat-x"
    assert title_from_cwd("/Users/dev/mk20-full") == "mk20-full"
    assert title_from_cwd(os.path.expanduser("~")) == "~"
    assert title_from_cwd("") == ""


def test_agentstate_helpers():
    assert agentstate.depth_below(103, 100, PS) == 3 and agentstate.depth_below(9999, 100, PS) is None
    assert agentstate.parse_ps("  1 0\n 102 101\nbad line\n") == {1: 0, 102: 101}
    assert agentstate.pid_alive(os.getpid()) and not agentstate.pid_alive(0)
    assert agentstate.parse_record(b'{"tool":"claude","session_id":"s","state":"done","ts":true,"ppid":1}') is None
    assert agentstate.default_state_dir({"MK20DECK_HOME": "/x"}) == "/x/agent-state"


def test_done_cleared_on_focus_until_next_stop(sdir):
    """Controller integration: done_unread → explicit focus clears locally; a new Stop alerts again."""
    put(sdir, "claude", "c0", "%0", "done", ts=NOW - 5)
    ads = demo_adapters()
    tm = adapter(sdir, terminal_app="Ghostty")
    ads[Mode.TMUX] = tm
    clk = FakeClock()
    c = Controller(DeckConfig(default_mode=Mode.TMUX), ads, clock=clk, wall=lambda: NOW, sim=True)
    c.refresh()
    idx = next(i for i in range(8) if c.mgr.session_at(i) and c.mgr.session_at(i).sid.native == "%0")
    assert c.mgr.session_at(idx).state == State.DONE_UNREAD
    c.on_knob(1, "cw")  # K1: the done session is the only attention item → selected first
    assert c.mgr.selected_session().sid.native == "%0"
    c.on_knob(1, "down")
    c.on_knob(1, "up")  # K1 press → focus
    assert tm.r.calls[-1] == ["open", "-a", "Ghostty"]
    c.refresh()
    assert c.mgr.session_at(idx).state == State.IDLE  # same Stop record: stays cleared
    put(sdir, "claude", "c0", "%0", "done", ts=NOW - 1)
    c.refresh()
    assert c.mgr.session_at(idx).state == State.DONE_UNREAD
