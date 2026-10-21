#!/usr/bin/env python3
"""Codex hook: metadata-only log probe + MK20 agent-state record; makes no decision.

1. Appends one JSON line per hook call to ~/.config/mk20-agent-deck/hooks.log with
   event name, session_id, turn_id, transcript_path, cwd, tool_name and timing
   (rotated to hooks.log.1 beyond 8 MiB).  Prompt text / tool_input / messages are
   NOT logged.
2. Writes the deck state record ``agent-state/codex-<session_id>.json`` through
   ``mk20-agent-state-hook.py`` (same directory) when it is installed next to this
   file: running / needs_approval (PermissionRequest) / done / idle (Interrupt) /
   removed.  codex 0.163 has no Notification event, so "waiting for input" is only
   seen when the agent calls ``request_user_input``.
Prints nothing and exits 0, so PermissionRequest stays undecided and the native
approval flow is untouched.
"""
import json
import os
import sys
import time

KEEP = ("hook_event_name", "session_id", "turn_id", "transcript_path", "cwd", "tool_name", "model",
        "permission_mode", "source", "stop_hook_active")
LOG_MAX = 8 * 1024 * 1024


def log_line(raw: str, t: float) -> None:
    try:
        d = json.loads(raw) if raw.strip() else {}
    except Exception as e:  # noqa: BLE001
        d = {"parse_error": type(e).__name__}
    if not isinstance(d, dict):
        d = {"parse_error": "not_object"}
    rec = {k: d.get(k) for k in KEEP if k in d}
    rec["ts"] = round(t, 3)
    rec["keys"] = sorted(d.keys())[:40]
    rec["ppid"] = os.getppid()
    path = os.path.expanduser("~/.config/mk20-agent-deck/hooks.log")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        if os.path.getsize(path) > LOG_MAX:
            os.replace(path, path + ".1")
    except OSError:
        pass
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def state_record(raw: str) -> None:
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mk20-agent-state-hook.py")
    if not os.path.isfile(p):
        return
    import importlib.util
    spec = importlib.util.spec_from_file_location("mk20_agent_state_hook", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.handle("codex", raw)


def main() -> None:
    t = time.time()
    raw = sys.stdin.read()
    for step in (log_line, state_record):
        try:
            step(raw, t) if step is log_line else step(raw)
        except Exception:  # noqa: BLE001 — never disturb the agent
            pass


if __name__ == "__main__":
    try:
        main()
    finally:
        sys.exit(0)
