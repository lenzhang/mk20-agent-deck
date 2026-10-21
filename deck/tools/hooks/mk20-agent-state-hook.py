#!/usr/bin/env python3
"""MK20 Agent Deck state hook for Claude Code and codex CLI ("which agent needs me").

    mk20-agent-state-hook.py claude|codex      (hook JSON on stdin)

Maps a hook event to a coarse agent state and atomically writes
``$MK20DECK_HOME/agent-state/<tool>-<session_id>.json`` (default
``~/.config/mk20-agent-deck``; dir 0700, file 0600, tmp + rename) holding ONLY:
tool, session_id, tmux_pane ($TMUX_PANE), tmux_socket (socket path of $TMUX),
cwd, state, ts, ppid, and while a numbered choice is pending (deck option mode,
2026-10-22) at most one of:
  options      Claude AskUserQuestion: questions[0].options[*].label, <= 5 labels x 24 chars,
               control/format characters stripped (the option labels only)
  prompt_kind  "permission": a tool permission prompt (PermissionRequest / Notification
               permission_prompt; codex PermissionRequest)
Both disappear with the next record the session writes (PostToolUse, UserPromptSubmit,
Stop, ...).  Question text, prompts, other tool input/output, notification messages and
transcript paths are never stored.  The deck's tmux adapter joins records to panes
by tmux socket + pane id.

Claude Code events (https://code.claude.com/docs/en/hooks):
  SessionStart                       -> idle  (source "compact": keep the current state)
  UserPromptSubmit/PostToolUse/PostToolUseFailure/ElicitationResult -> running
  PreToolUse                         -> running (AskUserQuestion -> needs_input, ExitPlanMode -> needs_approval)
  PermissionRequest                  -> needs_approval (fires immediately; Notification permission_prompt ~6 s later)
  Notification permission_prompt     -> needs_approval
  Notification elicitation_dialog / elicitation_url_dialog / agent_needs_input / quota_auto_resume_stale
                                     -> needs_input
  Notification idle_prompt           -> needs_input, unless the session is already "done" (no double alert)
  Elicitation                        -> needs_input
  Stop -> done, StopFailure (API error) -> error, SessionEnd -> removed
  anything else (SubagentStop, other notification types, ...) -> no write
codex CLI events (0.163: SessionStart, UserPromptSubmit, PreToolUse, PermissionRequest,
PostToolUse, Stop, Interrupt, SessionEnd; no Notification event):
  SessionStart -> idle, UserPromptSubmit/PostToolUse -> running,
  PreToolUse -> running (request_user_input -> needs_input), PermissionRequest -> needs_approval,
  Stop -> done, Interrupt -> idle, SessionEnd -> removed

Contract with the agent: stdlib only, no network, prints nothing, always exits 0, so
no decision is ever returned (PermissionRequest stays undecided; the native approval
flow is untouched).  Records older than 24 h are pruned; at most MAX_RECORDS kept.
"""
import json
import os
import re
import sys
import time

TOOLS = ("claude", "codex")
STATES = ("idle", "running", "needs_approval", "needs_input", "done", "error", "removed")
MAX_STDIN = 16 * 1024 * 1024   # bigger payloads (huge tool output) are skipped, not parsed
MAX_AGE_S = 24 * 3600
MAX_RECORDS = 256
TMP_STALE_S = 60
_SID_BAD = re.compile(r"[^A-Za-z0-9._-]")
NAME_RE = re.compile(r"^(claude|codex)-[A-Za-z0-9._-]{1,128}\.json$")

CLAUDE_NOTIFY = {
    "permission_prompt": "needs_approval",
    "elicitation_dialog": "needs_input",
    "elicitation_url_dialog": "needs_input",
    "agent_needs_input": "needs_input",
    "quota_auto_resume_stale": "needs_input",   # waits for Enter after a usage-limit reset
    "idle_prompt": "needs_input",
}
CLAUDE_EVENTS = {
    "UserPromptSubmit": "running",
    "PostToolUse": "running",
    "PostToolUseFailure": "running",
    "ElicitationResult": "running",
    "PermissionRequest": "needs_approval",
    "Elicitation": "needs_input",
    "Stop": "done",
    "StopFailure": "error",
    "SessionEnd": "removed",
}
CLAUDE_ASK_TOOLS = {"AskUserQuestion": "needs_input", "ExitPlanMode": "needs_approval"}
CODEX_EVENTS = {
    "UserPromptSubmit": "running",
    "PostToolUse": "running",
    "PermissionRequest": "needs_approval",
    "Stop": "done",
    "Interrupt": "idle",
    "SessionEnd": "removed",
}
CODEX_ASK_TOOLS = {"request_user_input": "needs_input"}
PROMPT_STATES = ("needs_input", "needs_approval")
MAX_OPTIONS = 5
MAX_LABEL = 24
# C0/C1 controls, zero-width/bidi format characters, line/paragraph separators, BOM
_LABEL_BAD = re.compile("[\x00-\x1f\x7f-\x9f\u200b-\u200f\u2028-\u202e\u2060-\u2069\ufeff]")


def state_home(env=None):
    env = os.environ if env is None else env
    return os.path.expanduser(env.get("MK20DECK_HOME") or "~/.config/mk20-agent-deck")


def safe_session_id(sid):
    if not isinstance(sid, str):
        return ""
    s = _SID_BAD.sub("_", sid.strip())[:128].lstrip(".")
    return s if s.strip("_") else ""


def map_event(tool, d, prev_state=None):
    """Hook payload -> state string, or None (= do not write)."""
    ev = d.get("hook_event_name")
    if not isinstance(ev, str):
        return None
    if tool == "claude":
        if ev == "SessionStart":
            if d.get("source") == "compact":
                return prev_state or "idle"  # compaction is not a lifecycle change
            return "idle"
        if ev == "PreToolUse":
            return CLAUDE_ASK_TOOLS.get(d.get("tool_name"), "running")
        if ev == "Notification":
            st = CLAUDE_NOTIFY.get(d.get("notification_type"))
            if st is None:
                return None
            if d.get("notification_type") == "idle_prompt" and prev_state == "done":
                return None  # already shown as done; idle_prompt only fixes interrupted/denied turns
            return st
        return CLAUDE_EVENTS.get(ev)
    if tool == "codex":
        if ev == "SessionStart":
            return "idle"
        if ev == "PreToolUse":
            return CODEX_ASK_TOOLS.get(d.get("tool_name"), "running")
        return CODEX_EVENTS.get(ev)
    return None


def clean_label(s):
    """One option label for the key face: no control/format characters, whitespace collapsed, <= MAX_LABEL."""
    if not isinstance(s, str):
        return ""
    return " ".join(_LABEL_BAD.sub(" ", s[:256]).split())[:MAX_LABEL]


def question_options(tool_input):
    """AskUserQuestion tool_input -> labels of the FIRST question's options (never the question text)."""
    qs = tool_input.get("questions") if isinstance(tool_input, dict) else None
    if not isinstance(qs, list) or not qs or not isinstance(qs[0], dict):
        return []
    opts = qs[0].get("options")
    if not isinstance(opts, list):
        return []
    out = [clean_label(o.get("label") if isinstance(o, dict) else None) for o in opts[:MAX_OPTIONS]]
    return out if any(out) else []


def _carry(prev):
    """The still-pending choice of the previous record (re-validated, never trusted blindly)."""
    opts = prev.get("options") if isinstance(prev, dict) else None
    if isinstance(opts, list) and 0 < len(opts) <= MAX_OPTIONS and all(isinstance(o, str) for o in opts):
        out = [clean_label(o) for o in opts]
        if any(out):
            return {"options": out}
    return {}


def prompt_fields(tool, d, state, prev=None):
    """Which numbered choice is pending (deck option mode): {"options": [...]}, {"prompt_kind": "permission"} or {}."""
    if state not in PROMPT_STATES:
        return {}
    ev, tn = d.get("hook_event_name"), d.get("tool_name")
    if tool == "codex":
        return {"prompt_kind": "permission"} if ev == "PermissionRequest" else {}
    if ev == "PreToolUse" and tn == "AskUserQuestion":
        opts = question_options(d.get("tool_input"))
        return {"options": opts} if opts else {}
    notify = ev == "Notification" and d.get("notification_type") == "permission_prompt"
    if ev == "PermissionRequest" or notify:
        # The question / plan dialog itself may raise these: keep what its PreToolUse recorded.
        pending = (isinstance(prev, dict) and prev.get("state") in PROMPT_STATES
                   and prev.get("prompt_kind") != "permission")
        if (notify and pending) or (ev == "PermissionRequest" and tn in CLAUDE_ASK_TOOLS):
            return _carry(prev) if pending else {}
        return {"prompt_kind": "permission"}
    return {}


def tmux_socket(env):
    v = env.get("TMUX") or ""
    return v.split(",", 1)[0] if v else ""


def build_record(tool, d, state, env, now, ppid, prev=None):
    """Only the whitelisted fields; never message/prompt/question/tool_input/transcript_path."""
    cwd = d.get("cwd")
    pane = env.get("TMUX_PANE") or ""
    rec = {
        "tool": tool,
        "session_id": safe_session_id(d.get("session_id")),
        "tmux_pane": pane if re.match(r"^%[0-9]{1,9}$", pane) else "",
        "tmux_socket": tmux_socket(env)[:1024],
        "cwd": cwd[:4096] if isinstance(cwd, str) else "",
        "state": state,
        "ts": round(now, 3),
        "ppid": int(ppid),
    }
    rec.update(prompt_fields(tool, d, state, prev))
    return rec


def read_prev(path, sid):
    """This session's previous record (dict) or None."""
    try:
        with open(path, "rb") as f:
            raw = f.read(8192)
        rec = json.loads(raw)
        if isinstance(rec, dict) and rec.get("session_id") == sid and rec.get("state") in STATES:
            return rec
    except (OSError, ValueError):
        pass
    return None


def read_prev_state(path, sid):
    rec = read_prev(path, sid)
    return rec["state"] if rec else None


def write_atomic(dirpath, name, rec):
    tmp = os.path.join(dirpath, ".tmp-%d-%s-%s" % (os.getpid(), os.urandom(4).hex(), name))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, os.path.join(dirpath, name))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def prune(dirpath, now, keep=None, max_age=MAX_AGE_S, max_records=MAX_RECORDS):
    """Drop records older than ``max_age`` and stale tmp files; cap the count (oldest first)."""
    recs = []
    try:
        it = os.scandir(dirpath)
    except OSError:
        return
    with it:
        for e in it:
            try:
                if not e.is_file(follow_symlinks=False):
                    continue
                mt = e.stat(follow_symlinks=False).st_mtime
            except OSError:
                continue
            if e.name.startswith(".tmp-"):
                if now - mt > TMP_STALE_S:
                    _unlink(e.path)
                continue
            if not NAME_RE.match(e.name):
                continue
            if now - mt > max_age:
                _unlink(e.path)
            elif e.name != keep:
                recs.append((mt, e.path))
    if len(recs) + (1 if keep else 0) > max_records:
        recs.sort()
        for _, p in recs[:len(recs) + (1 if keep else 0) - max_records]:
            _unlink(p)


def _unlink(p):
    try:
        os.unlink(p)
    except OSError:
        pass


def ensure_dir(home):
    d = os.path.join(home, "agent-state")
    os.makedirs(d, mode=0o700, exist_ok=True)
    st = os.lstat(d)
    if not os.path.isdir(d) or os.path.islink(d) or st.st_uid != os.getuid():
        raise OSError("agent-state is not our directory")
    if st.st_mode & 0o777 != 0o700:
        os.chmod(d, 0o700)
    return d


def handle(tool, raw, env=None, now=None, ppid=None):
    """Process one hook call; returns the record written (or None).  Never raises for bad input."""
    env = os.environ if env is None else env
    if tool not in TOOLS or not raw or len(raw) > MAX_STDIN:
        return None
    try:
        d = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None
    sid = safe_session_id(d.get("session_id"))
    if not sid:
        return None
    now = time.time() if now is None else now
    dirpath = ensure_dir(state_home(env))
    name = "%s-%s.json" % (tool, sid)
    prev = None
    if d.get("hook_event_name") in ("SessionStart", "Notification", "PermissionRequest"):
        prev = read_prev(os.path.join(dirpath, name), sid)
    state = map_event(tool, d, prev["state"] if prev else None)
    if state is None:
        return None
    rec = build_record(tool, d, state, env, now, os.getppid() if ppid is None else ppid, prev)
    write_atomic(dirpath, name, rec)
    if d.get("hook_event_name") not in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
        prune(dirpath, now, keep=name)
    return rec


def main(argv=None):
    argv = sys.argv if argv is None else argv
    tool = argv[1] if len(argv) > 1 else "claude"
    try:
        raw = sys.stdin.buffer.read(MAX_STDIN + 1)
        if len(raw) > MAX_STDIN:
            while sys.stdin.buffer.read(1 << 20):  # drain so the writer never sees EPIPE
                pass
            return
        handle(tool, raw)
    except BaseException:  # noqa: BLE001 — a state hook must never disturb the agent
        pass


if __name__ == "__main__":
    try:
        main()
    finally:
        os._exit(0)
