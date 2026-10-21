"""tools/hooks/mk20-agent-state-hook.py: payload -> record, privacy, atomicity, pruning."""
import importlib.util
import json
import os
import pathlib
import stat
import subprocess
import sys
import threading
import time

import pytest

HOOKS = pathlib.Path(__file__).resolve().parents[1] / "tools" / "hooks"
SCRIPT = HOOKS / "mk20-agent-state-hook.py"
FIELDS = {"tool", "session_id", "tmux_pane", "tmux_socket", "cwd", "state", "ts", "ppid"}
SECRETS = ["SECRET-PROMPT-1", "SECRET-MSG-2", "SECRET-TOOLINPUT-3", "/secret/transcript-4.jsonl", "SECRET-TITLE-5",
           "SECRET-LAST-6", "SECRET-RESP-7"]


def _load():
    spec = importlib.util.spec_from_file_location("mk20_agent_state_hook", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


H = _load()


@pytest.fixture
def home(tmp_path):
    return {"MK20DECK_HOME": str(tmp_path / "deckhome"), "TMUX": "/private/tmp/tmux-501/default,67692,0",
            "TMUX_PANE": "%7"}


def payload(ev, **kw):
    d = {"session_id": "abc-123", "transcript_path": SECRETS[3], "cwd": "/Users/dev/worktrees/mk20-deck-attn/deck",
         "permission_mode": "default", "hook_event_name": ev}
    d.update(kw)
    return json.dumps(d).encode()


def call(env, ev, tool="claude", now=1_792_500_000.0, **kw):
    return H.handle(tool, payload(ev, **kw), env=env, now=now, ppid=4242)


def rec_file(env, tool="claude", sid="abc-123"):
    return pathlib.Path(env["MK20DECK_HOME"]) / "agent-state" / f"{tool}-{sid}.json"


def read(env, tool="claude", sid="abc-123"):
    return json.loads(rec_file(env, tool, sid).read_text())


CLAUDE_CASES = [
    ("UserPromptSubmit", {"prompt": SECRETS[0]}, "running"),
    ("PreToolUse", {"tool_name": "Bash", "tool_input": {"command": SECRETS[2]}, "tool_use_id": "t1"}, "running"),
    ("PreToolUse", {"tool_name": "AskUserQuestion", "tool_input": {"questions": [SECRETS[2]]}}, "needs_input"),
    ("PreToolUse", {"tool_name": "ExitPlanMode", "tool_input": {"plan": SECRETS[2]}}, "needs_approval"),
    ("PermissionRequest", {"tool_name": "Bash", "tool_input": {"command": SECRETS[2]}}, "needs_approval"),
    ("PostToolUse", {"tool_name": "Bash", "tool_input": {}, "tool_response": {"text": SECRETS[6]}}, "running"),
    ("PostToolUseFailure", {"tool_name": "Bash", "error": SECRETS[6]}, "running"),
    ("Notification", {"notification_type": "permission_prompt", "message": SECRETS[1], "title": SECRETS[4]},
     "needs_approval"),
    ("Notification", {"notification_type": "elicitation_dialog", "message": SECRETS[1]}, "needs_input"),
    ("Notification", {"notification_type": "agent_needs_input", "message": SECRETS[1]}, "needs_input"),
    ("Notification", {"notification_type": "idle_prompt", "message": SECRETS[1]}, "needs_input"),
    ("Elicitation", {"mcp_server_name": "x", "message": SECRETS[1]}, "needs_input"),
    ("ElicitationResult", {"mcp_server_name": "x"}, "running"),
    ("Stop", {"stop_hook_active": False, "last_assistant_message": SECRETS[5]}, "done"),
    ("StopFailure", {"error": "rate_limit", "last_assistant_message": SECRETS[5]}, "error"),
    ("SessionStart", {"source": "startup", "model": "claude-opus-5"}, "idle"),
    ("SessionEnd", {"reason": "prompt_input_exit"}, "removed"),
]


@pytest.mark.parametrize("ev,extra,want", CLAUDE_CASES, ids=[f"{c[0]}-{c[2]}" for c in CLAUDE_CASES])
def test_claude_event_to_state_and_privacy(home, ev, extra, want):
    rec = call(home, ev, **extra)
    assert rec["state"] == want
    raw = rec_file(home).read_text()
    for s in SECRETS:  # never: prompt, message/title, tool input/output, transcript path, assistant text
        assert s not in raw
    on_disk = json.loads(raw)
    assert set(on_disk) == FIELDS
    assert on_disk == {"tool": "claude", "session_id": "abc-123", "tmux_pane": "%7",
                       "tmux_socket": "/private/tmp/tmux-501/default", "cwd": "/Users/dev/worktrees/mk20-deck-attn/deck",
                       "state": want, "ts": 1_792_500_000.0, "ppid": 4242}


@pytest.mark.parametrize("ev,extra", [
    ("Notification", {"notification_type": "auth_success", "message": "x"}),
    ("Notification", {"notification_type": "agent_completed"}),
    ("SubagentStop", {"agent_id": "a", "agent_type": "Explore"}),  # also fires for prompt suggestions after Stop
    ("SubagentStart", {}), ("PreCompact", {}), ("TotallyNewEvent", {}),
])
def test_unmapped_events_write_nothing(home, ev, extra):
    assert call(home, ev, **extra) is None
    assert not rec_file(home).exists()


def test_idle_prompt_does_not_override_done_but_fixes_interrupted_turns(home):
    call(home, "Stop")
    assert call(home, "Notification", notification_type="idle_prompt") is None
    assert read(home)["state"] == "done"
    call(home, "UserPromptSubmit", prompt="x")  # then Esc: no Stop fires
    call(home, "Notification", notification_type="idle_prompt", now=1_792_500_060.0)
    assert read(home)["state"] == "needs_input"


def test_compact_session_start_keeps_state(home):
    call(home, "PreToolUse", tool_name="Bash")
    call(home, "SessionStart", source="compact")
    assert read(home)["state"] == "running"
    call(home, "SessionStart", source="clear")
    assert read(home)["state"] == "idle"


CODEX_CASES = [
    ("SessionStart", {"source": "startup"}, "idle"),
    ("UserPromptSubmit", {"prompt": SECRETS[0], "turn_id": "t"}, "running"),
    ("PreToolUse", {"tool_name": "exec", "tool_input": {"cmd": SECRETS[2]}}, "running"),
    ("PreToolUse", {"tool_name": "request_user_input"}, "needs_input"),
    ("PermissionRequest", {"tool_name": "exec"}, "needs_approval"),
    ("PostToolUse", {"tool_name": "exec", "tool_response": SECRETS[6]}, "running"),
    ("Stop", {"last_assistant_message": SECRETS[5]}, "done"),
    ("Interrupt", {}, "idle"),
    ("SessionEnd", {}, "removed"),
]


@pytest.mark.parametrize("ev,extra,want", CODEX_CASES, ids=[f"{c[0]}-{c[2]}" for c in CODEX_CASES])
def test_codex_events(home, ev, extra, want):
    assert call(home, ev, tool="codex", **extra)["state"] == want
    raw = rec_file(home, "codex").read_text()
    assert all(s not in raw for s in SECRETS) and set(json.loads(raw)) == FIELDS


def test_session_id_sanitised_and_bad_input_ignored(home):
    rec = H.handle("claude", payload("Stop", session_id="../../../etc/passwd x"), env=home, now=1.0, ppid=1)
    d = pathlib.Path(home["MK20DECK_HOME"]) / "agent-state"
    names = os.listdir(d)
    assert names == [f"claude-{rec['session_id']}.json"] and "/" not in rec["session_id"]
    assert not rec["session_id"].startswith(".")
    for raw in (b"", b"not json", b"[1,2]", b'{"hook_event_name":"Stop"}', b'{"session_id":"...","hook_event_name":"Stop"}',
                payload("Stop", session_id=7)):
        assert H.handle("claude", raw, env=home, now=1.0, ppid=1) is None
    assert H.handle("grok", payload("Stop"), env=home, now=1.0, ppid=1) is None
    rec = H.handle("claude", payload("Stop", session_id="x"), env={"MK20DECK_HOME": home["MK20DECK_HOME"],
                                                                  "TMUX_PANE": "%1; rm -rf /"}, now=1.0, ppid=1)
    assert rec["tmux_pane"] == "" and rec["tmux_socket"] == ""


def test_permissions_and_no_tmp_left(home):
    call(home, "Stop")
    d = pathlib.Path(home["MK20DECK_HOME"]) / "agent-state"
    assert stat.S_IMODE(d.stat().st_mode) == 0o700
    assert stat.S_IMODE(rec_file(home).stat().st_mode) == 0o600
    assert [p.name for p in d.iterdir()] == ["claude-abc-123.json"]
    d.chmod(0o755)
    call(home, "UserPromptSubmit")
    assert stat.S_IMODE(d.stat().st_mode) == 0o700


def test_write_failure_keeps_old_record_and_no_tmp(home, monkeypatch):
    call(home, "Stop")
    before = rec_file(home).read_bytes()

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(H.json, "dump", boom)
    with pytest.raises(OSError):
        call(home, "UserPromptSubmit")
    assert rec_file(home).read_bytes() == before
    assert [p.name for p in rec_file(home).parent.iterdir()] == ["claude-abc-123.json"]


def test_concurrent_writers_never_expose_partial_json(home):
    stop = threading.Event()
    bad = []

    def reader():
        p = rec_file(home)
        while not stop.is_set():
            try:
                json.loads(p.read_bytes())
            except FileNotFoundError:
                pass
            except ValueError as e:
                bad.append(e)
    th = threading.Thread(target=reader)
    th.start()
    evs = ["UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"]
    workers = [threading.Thread(target=lambda i=i: [call(home, evs[(i + j) % 4], cwd="/x" * 300)
                                                     for j in range(40)]) for i in range(4)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    stop.set()
    th.join()
    assert not bad
    assert read(home)["state"] in ("running", "done")
    assert [p.name for p in rec_file(home).parent.iterdir()] == ["claude-abc-123.json"]


def test_prune_old_records_tmp_and_cap(home):
    now = 1_792_500_000.0
    d = pathlib.Path(home["MK20DECK_HOME"]) / "agent-state"
    call(home, "SessionStart", source="startup", now=now)
    old = d / "claude-old.json"
    old.write_text("{}")
    os.utime(old, (now - 25 * 3600, now - 25 * 3600))
    tmp = d / ".tmp-1-claude-x.json"
    tmp.write_text("{")
    os.utime(tmp, (now - 120, now - 120))
    foreign = d / "notes.txt"
    foreign.write_text("keep")
    os.utime(foreign, (now - 99 * 3600, now - 99 * 3600))
    for i in range(300):
        p = d / f"codex-s{i:03d}.json"
        p.write_text("{}")
        os.utime(p, (now - 3600 + i, now - 3600 + i))
    call(home, "Stop", now=now)
    names = set(os.listdir(d))
    recs = {n for n in names if n.endswith(".json")}
    assert "claude-old.json" not in names and ".tmp-1-claude-x.json" not in names and "notes.txt" in names
    assert len(recs) == H.MAX_RECORDS and "claude-abc-123.json" in recs
    assert "codex-s299.json" in recs and "codex-s000.json" not in recs  # oldest go first
    call(home, "PreToolUse", tool_name="Bash", now=now)  # tool events skip the directory scan
    assert len({n for n in os.listdir(d) if n.endswith(".json")}) == H.MAX_RECORDS


def test_handle_fast_on_large_payload(home):
    big = payload("PostToolUse", tool_name="Bash", tool_response={"text": "x" * 2_000_000})
    t0 = time.perf_counter()
    H.handle("claude", big, env=home, ppid=1)
    assert time.perf_counter() - t0 < 0.1


def test_script_subprocess_silent_exit0_and_fast(home, tmp_path):
    env = dict(os.environ, **home)
    env.pop("PYTHONPATH", None)
    for raw in (payload("Stop", last_assistant_message=SECRETS[5]), b"garbage", b""):
        t0 = time.perf_counter()
        p = subprocess.run([sys.executable, "-I", "-S", str(SCRIPT), "claude"], input=raw, env=env,
                           capture_output=True, timeout=10)
        dt = time.perf_counter() - t0
        assert p.returncode == 0 and p.stdout == b"" and p.stderr == b""
        assert dt < 2.0  # wall incl. interpreter start; the hook work itself is < 100 ms (test above)
    rec = read(home)
    assert rec["state"] == "done" and rec["ppid"] == os.getpid()
    # unwritable home: still silent, still exit 0
    bad = dict(env, MK20DECK_HOME="/proc/definitely/not/writable")
    p = subprocess.run([sys.executable, "-I", "-S", str(SCRIPT), "claude"], input=payload("Stop"), env=bad,
                       capture_output=True, timeout=10)
    assert p.returncode == 0 and p.stdout == b"" and p.stderr == b""


def test_huge_stdin_is_drained_not_parsed(home, monkeypatch):
    env = dict(os.environ, **home)
    raw = b'{"session_id":"abc-123","hook_event_name":"Stop","pad":"' + b"x" * (H.MAX_STDIN + 10) + b'"}'
    p = subprocess.run([sys.executable, "-I", "-S", str(SCRIPT), "claude"], input=raw, env=env,
                       capture_output=True, timeout=30)
    assert p.returncode == 0 and p.stdout == b"" and not rec_file(home).exists()


def test_codex_log_hook_writes_log_and_state_record(tmp_path):
    env = dict(os.environ, HOME=str(tmp_path), TMUX_PANE="%3", TMUX="/tmp/tmux-0/default,1,0")
    env.pop("MK20DECK_HOME", None)
    raw = json.dumps({"session_id": "019f-aa", "hook_event_name": "PermissionRequest", "tool_name": "exec",
                      "tool_input": {"cmd": SECRETS[2]}, "cwd": "/w", "turn_id": "t1"}).encode()
    p = subprocess.run([sys.executable, str(HOOKS / "mk20_hook_log.py")], input=raw, env=env, capture_output=True,
                       timeout=10)
    assert p.returncode == 0 and p.stdout == b""
    base = tmp_path / ".config" / "mk20-agent-deck"
    log = (base / "hooks.log").read_text()
    assert '"PermissionRequest"' in log and SECRETS[2] not in log
    rec = json.loads((base / "agent-state" / "codex-019f-aa.json").read_text())
    assert rec["state"] == "needs_approval" and rec["tmux_pane"] == "%3" and rec["tool"] == "codex"


def test_codex_log_rotates(tmp_path):
    env = dict(os.environ, HOME=str(tmp_path))
    base = tmp_path / ".config" / "mk20-agent-deck"
    base.mkdir(parents=True)
    (base / "hooks.log").write_bytes(b"x" * (8 * 1024 * 1024 + 1))
    subprocess.run([sys.executable, str(HOOKS / "mk20_hook_log.py")], input=b"{}", env=env, timeout=10, check=True)
    assert (base / "hooks.log.1").stat().st_size > 8 * 1024 * 1024
    assert (base / "hooks.log").stat().st_size < 1000
