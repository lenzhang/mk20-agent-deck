"""Claude settings hook installer/uninstaller (temp files only) + codex hooks.json installer."""
import importlib.util
import io
import json
import os
import pathlib
import shlex
import stat
import subprocess
import sys

import pytest

HOOKS = pathlib.Path(__file__).resolve().parents[1] / "tools" / "hooks"
spec = importlib.util.spec_from_file_location("mk20_hooks_merge", HOOKS / "mk20_hooks_merge.py")
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)

CMD = "exec '/usr/bin/python3' -I -S '/h/mk20-agent-state-hook.py' claude"
USER = {
    "model": "opus",
    "env": {"A": "1"},
    "hooks": {
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "~/bin/guard.sh"}]}],
        "Notification": [{"hooks": [{"type": "command", "command": "say hi"}]}],
    },
}


def write(p, doc, mode=0o644):
    p.write_text(json.dumps(doc, indent=2))
    p.chmod(mode)
    return p


def backups(p):
    return sorted(x for x in p.parent.iterdir() if x.name.startswith(p.name + ".bak-mk20-"))


def run(action, paths, dry=False, stamp="20261020-230000"):
    out = io.StringIO()
    rc = M.run(action, [str(p) for p in paths], CMD if action == "install" else None, dry, out, stamp)
    return rc, out.getvalue()


def ours(doc):
    return [(ev, h["command"]) for ev, gs in doc.get("hooks", {}).items() for g in gs for h in g["hooks"]
            if M.MARKER in h["command"]]


def test_install_merges_keeps_existing_backup_and_is_idempotent(tmp_path):
    p = write(tmp_path / "settings.json", USER, 0o600)
    rc, out = run("install", [p])
    assert rc == 0 and "hooks merged" in out
    doc = json.loads(p.read_text())
    assert doc["model"] == "opus" and doc["env"] == {"A": "1"}
    assert doc["hooks"]["PreToolUse"][0] == USER["hooks"]["PreToolUse"][0]  # user's hook kept, first
    assert doc["hooks"]["Notification"][0] == USER["hooks"]["Notification"][0]
    assert sorted(ev for ev, _ in ours(doc)) == sorted(M.CLAUDE_EVENTS)
    sess_end = doc["hooks"]["SessionEnd"][-1]["hooks"][0]
    assert sess_end["timeout"] == 1 and doc["hooks"]["Stop"][-1]["hooks"][0]["timeout"] == 3
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    (b,) = backups(p)
    assert json.loads(b.read_text()) == USER and b.name.endswith("20261020-230000")
    # second run: unchanged, no new backup, byte-identical file
    before = p.read_bytes()
    rc, out = run("install", [p], stamp="20261020-230500")
    assert rc == 0 and "unchanged" in out and p.read_bytes() == before and len(backups(p)) == 1
    # a changed command path replaces our entries instead of duplicating them
    rc = M.run("install", [str(p)], CMD.replace("/h/", "/h2/"), False, io.StringIO(), "20261020-231000")
    doc = json.loads(p.read_text())
    assert rc == 0 and len(ours(doc)) == len(M.CLAUDE_EVENTS) and all("/h2/" in c for _, c in ours(doc))


def test_uninstall_removes_only_ours(tmp_path):
    p = write(tmp_path / "settings.json", USER)
    run("install", [p])
    rc, out = run("uninstall", [p], stamp="20261020-232000")
    assert rc == 0 and "removed" in out
    assert json.loads(p.read_text()) == USER
    rc, out = run("uninstall", [p], stamp="20261020-233000")
    assert "unchanged" in out
    q = write(tmp_path / "bare.json", {"theme": "dark"})
    run("install", [q])
    run("uninstall", [q], stamp="20261020-234000")
    assert json.loads(q.read_text()) == {"theme": "dark"}  # empty "hooks" object dropped again


def test_mixed_group_keeps_foreign_handler(tmp_path):
    doc = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say done"},
                                         {"type": "command", "command": CMD}]}], "Empty": []}}
    p = write(tmp_path / "s.json", doc)
    run("uninstall", [p])
    assert json.loads(p.read_text()) == {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say done"}]}],
                                                   "Empty": []}}


@pytest.mark.parametrize("bad", ["{not json", "[1, 2]", '{"hooks": []}', '{"hooks": {"Stop": {}}}',
                                 '{"hooks": {"Stop": ["x"]}}'])
def test_refuses_malformed_and_writes_nothing(tmp_path, bad):
    good = write(tmp_path / "a.json", USER)
    badp = tmp_path / "b.json"
    badp.write_text(bad)
    g0, b0 = good.read_bytes(), badp.read_bytes()
    rc, out = run("install", [good, badp])
    assert rc == 2 and out.startswith("refuse:")
    assert good.read_bytes() == g0 and badp.read_bytes() == b0 and not backups(good) and not backups(badp)


def test_missing_file_refused(tmp_path):
    rc, out = run("install", [tmp_path / "nope.json"])
    assert rc == 2 and "no such file" in out and not (tmp_path / "nope.json").exists()


def test_dry_run_prints_diff_writes_nothing(tmp_path):
    p = write(tmp_path / "settings.json", USER)
    before = p.read_bytes()
    rc, out = run("install", [p], dry=True)
    assert rc == 0 and p.read_bytes() == before and not backups(p)
    assert out.startswith("---") and "+++" in out and "mk20-agent-state-hook.py" in out and "dry run" in out
    assert '+      "SessionEnd": [' in out or '+    "SessionEnd": [' in out


def test_symlinked_settings_followed(tmp_path):
    real = write(tmp_path / "real.json", {"a": 1})
    link = tmp_path / "settings.json"
    link.symlink_to(real)
    run("install", [link])
    assert link.is_symlink() and ours(json.loads(real.read_text()))


def _sh_env(tmp_path, **kw):
    env = dict(os.environ, HOME=str(tmp_path), MK20_HOOK_PYTHON=sys.executable, **kw)
    env.pop("MK20DECK_HOME", None)
    env.update(kw)
    return env


def test_shell_installer_end_to_end_and_hook_command_runs(tmp_path):
    deck_home = tmp_path / "deckhome"
    s1 = write(tmp_path / "settings.json", USER)
    s2 = write(tmp_path / "settings-clean.json", {})
    env = _sh_env(tmp_path, MK20DECK_HOME=str(deck_home))
    dry = subprocess.run(["bash", str(HOOKS / "claude-hooks-install.sh"), "--dry-run", str(s1), str(s2)], env=env,
                         capture_output=True, text=True, timeout=30)
    assert dry.returncode == 0 and "+++" in dry.stdout and not (deck_home / "hooks").exists()
    assert json.loads(s1.read_text()) == USER
    r = subprocess.run(["bash", str(HOOKS / "claude-hooks-install.sh"), str(s1), str(s2)], env=env,
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stdout + r.stderr
    script = deck_home / "hooks" / "mk20-agent-state-hook.py"
    assert stat.S_IMODE((deck_home / "hooks").stat().st_mode) == 0o700 and os.access(script, os.X_OK)
    doc = json.loads(s2.read_text())
    cmd = doc["hooks"]["Stop"][-1]["hooks"][0]["command"]
    assert cmd.startswith("exec env MK20DECK_HOME=") and " -I -S " in cmd and cmd.endswith(" claude")
    # Claude Code runs shell-form hooks with `sh -c`; exec makes our parent the agent process itself
    payload = json.dumps({"session_id": "s1", "hook_event_name": "Stop", "cwd": "/w"}).encode()
    p = subprocess.run(["sh", "-c", cmd], input=payload, env=dict(env, TMUX_PANE="%4"), capture_output=True,
                       timeout=10)
    assert p.returncode == 0 and p.stdout == b""
    rec = json.loads((deck_home / "agent-state" / "claude-s1.json").read_text())
    assert rec["state"] == "done" and rec["tmux_pane"] == "%4" and rec["ppid"] == os.getpid()
    again = subprocess.run(["bash", str(HOOKS / "claude-hooks-install.sh"), str(s1), str(s2)], env=env,
                           capture_output=True, text=True, timeout=30)
    assert again.stdout.count("unchanged") == 2
    un = subprocess.run(["bash", str(HOOKS / "claude-hooks-uninstall.sh"), str(s1), str(s2)], env=env,
                        capture_output=True, text=True, timeout=30)
    assert un.returncode == 0 and json.loads(s1.read_text()) == USER and json.loads(s2.read_text()) == {}


def test_shell_installer_refuses_malformed_before_copying(tmp_path):
    good = write(tmp_path / "settings.json", USER)
    bad = tmp_path / "settings-glm.json"
    bad.write_text("{oops")
    r = subprocess.run(["bash", str(HOOKS / "claude-hooks-install.sh"), str(good), str(bad)], env=_sh_env(tmp_path),
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 2 and "refuse" in r.stdout
    assert not (tmp_path / ".config" / "mk20-agent-deck" / "hooks").exists()
    assert json.loads(good.read_text()) == USER and not backups(good)


def test_default_home_command_has_no_env_prefix(tmp_path):
    s = write(tmp_path / "s.json", {})
    r = subprocess.run(["bash", str(HOOKS / "claude-hooks-install.sh"), str(s)], env=_sh_env(tmp_path),
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    cmd = json.loads(s.read_text())["hooks"]["Stop"][-1]["hooks"][0]["command"]
    want = str(tmp_path / ".config" / "mk20-agent-deck" / "hooks" / "mk20-agent-state-hook.py")
    assert cmd.startswith("exec ") and "env MK20DECK_HOME" not in cmd and shlex.split(cmd)[-2] == want


def test_codex_installer_dry_run_and_clean_install(tmp_path):
    codex = tmp_path / "codex"
    codex.mkdir()
    env = _sh_env(tmp_path, CODEX_HOME=str(codex))
    r = subprocess.run(["bash", str(HOOKS / "hooks-install.sh"), "--dry-run"], env=env, capture_output=True,
                       text=True, timeout=30)
    hooks = json.loads(r.stdout)["hooks"]
    assert r.returncode == 0 and {"Interrupt", "SessionEnd", "PermissionRequest", "Stop"} <= set(hooks)
    assert not (codex / "hooks.json").exists()
    r = subprocess.run(["bash", str(HOOKS / "hooks-install.sh")], env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    st = tmp_path / ".config" / "mk20-agent-deck" / "hooks-install"
    assert (st / "mk20-agent-state-hook.py").exists() and (st / "mk20_hook_log.py").exists()
    r = subprocess.run(["bash", str(HOOKS / "hooks-install.sh")], env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 2 and "refuse" in r.stdout
    r = subprocess.run(["bash", str(HOOKS / "hooks-uninstall.sh")], env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and not (codex / "hooks.json").exists()
