#!/usr/bin/env python3
"""Codex hook probe: log *metadata only* and make no decision.

Writes one JSON line per hook call to ~/.config/mk20-agent-deck/hooks.log with
event name, session_id, turn_id, transcript_path, cwd, tool_name and timing.
Prompt text / tool_input / messages are NOT logged.  Prints nothing and exits 0,
so PermissionRequest stays undecided and the native approval flow is untouched.
"""
import json
import os
import sys
import time

KEEP = ("hook_event_name", "session_id", "turn_id", "transcript_path", "cwd", "tool_name", "model",
        "permission_mode", "source", "stop_hook_active")


def main() -> None:
    t = time.time()
    try:
        raw = sys.stdin.read()
        d = json.loads(raw) if raw.strip() else {}
    except Exception as e:  # noqa: BLE001
        d = {"parse_error": type(e).__name__}
    rec = {k: d.get(k) for k in KEEP if k in d}
    rec["ts"] = round(t, 3)
    rec["keys"] = sorted(d.keys())[:40]
    rec["ppid"] = os.getppid()
    path = os.path.expanduser("~/.config/mk20-agent-deck/hooks.log")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    try:
        main()
    finally:
        sys.exit(0)
