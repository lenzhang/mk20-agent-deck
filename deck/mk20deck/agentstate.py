"""Agent state records written by ``tools/hooks/mk20-agent-state-hook.py`` (read side).

One small JSON file per agent session in ``$MK20DECK_HOME/agent-state`` (default
``~/.config/mk20-agent-deck/agent-state``): ``claude-<sid>.json`` / ``codex-<sid>.json``
with exactly tool, session_id, tmux_pane, tmux_socket, cwd, state, ts, ppid.
Everything here is read-only and defensive: malformed, foreign-owned, oversized or
unknown-state files are skipped, never trusted.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Callable, Iterable

NAME_RE = re.compile(r"^(claude|codex)-[A-Za-z0-9._-]{1,128}\.json$")
PANE_RE = re.compile(r"^%[0-9]{1,9}$")
STATES = frozenset({"idle", "running", "needs_approval", "needs_input", "done", "error", "removed"})
MAX_FILE = 8192
MAX_FILES = 512


def state_home(env: dict | None = None) -> str:
    env = os.environ if env is None else env
    return os.path.expanduser(env.get("MK20DECK_HOME") or "~/.config/mk20-agent-deck")


def default_state_dir(env: dict | None = None) -> str:
    return os.path.join(state_home(env), "agent-state")


@dataclass(frozen=True)
class AgentRecord:
    tool: str          # "claude" | "codex"
    session_id: str
    tmux_pane: str
    tmux_socket: str
    cwd: str
    state: str
    ts: float
    ppid: int


def parse_record(raw: bytes | str) -> AgentRecord | None:
    try:
        d = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(d, dict):
        return None
    tool, sid, state = d.get("tool"), d.get("session_id"), d.get("state")
    pane, sock, cwd, ts, ppid = (d.get("tmux_pane", ""), d.get("tmux_socket", ""), d.get("cwd", ""),
                                 d.get("ts"), d.get("ppid"))
    if tool not in ("claude", "codex") or not isinstance(sid, str) or not sid or state not in STATES:
        return None
    if not all(isinstance(x, str) for x in (pane, sock, cwd)) or (pane and not PANE_RE.match(pane)):
        return None
    if isinstance(ts, bool) or not isinstance(ts, (int, float)) or isinstance(ppid, bool) or not isinstance(ppid, int):
        return None
    return AgentRecord(tool, sid, pane, sock, cwd, state, float(ts), ppid)


def load_records(state_dir: str, now: float, max_age_s: float) -> list[AgentRecord]:
    """All valid records younger than ``max_age_s`` (by their ``ts``)."""
    out: list[AgentRecord] = []
    try:
        st = os.stat(state_dir)
        if st.st_mode & 0o022 or st.st_uid != os.getuid():
            return []  # group/world-writable or foreign directory: never trust its contents
        it = os.scandir(state_dir)
    except OSError:
        return []
    with it:
        for n, e in enumerate(it):
            if n >= MAX_FILES * 2:
                break
            if not NAME_RE.match(e.name):
                continue
            try:
                fst = e.stat(follow_symlinks=False)
                if not e.is_file(follow_symlinks=False) or fst.st_size > MAX_FILE or fst.st_uid != os.getuid():
                    continue
                with open(e.path, "rb") as f:
                    rec = parse_record(f.read(MAX_FILE))
            except OSError:
                continue
            if rec is None or not e.name.startswith(rec.tool + "-"):
                continue
            if now - rec.ts > max_age_s or rec.ts - now > 300:
                continue
            out.append(rec)
            if len(out) >= MAX_FILES:
                break
    return out


def pid_alive(pid: int) -> bool:
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def parse_ps(text: str) -> dict[int, int]:
    """``ps -A -o pid=,ppid=`` → {pid: ppid}."""
    out: dict[int, int] = {}
    for line in text.splitlines():
        p = line.split()
        if len(p) == 2 and p[0].isdigit() and p[1].isdigit():
            out[int(p[0])] = int(p[1])
    return out


def depth_below(pid: int, root: int, parents: dict[int, int], limit: int = 64) -> int | None:
    """Number of hops from ``pid`` up to ``root`` (0 = same), or None if ``root`` is not an ancestor."""
    cur, n = pid, 0
    while n <= limit:
        if cur == root:
            return n
        nxt = parents.get(cur)
        if nxt is None or nxt == cur or nxt <= 0:
            return None
        cur, n = nxt, n + 1
    return None


def best_by_pane(records: Iterable[AgentRecord], socket_path: str, panes: dict[str, int],
                 alive: Callable[[int], bool] = pid_alive,
                 parents: dict[int, int] | None = None) -> dict[str, AgentRecord]:
    """Map pane id → the record that describes the agent in it.

    A record counts only when it is on this tmux server (socket path), names a pane
    that still exists, is not ``removed`` and its agent process (``ppid``) is alive.
    With a process table, the agent must also run inside that pane (descendant of
    ``pane_pid``); the shallowest one wins, so a ``codex exec`` started by a Claude
    session in the same pane never hides the Claude session.  Ties: freshest ``ts``.
    """
    best: dict[str, tuple[int, float, AgentRecord]] = {}
    sock = os.path.realpath(socket_path) if socket_path else ""
    for r in records:
        if r.state == "removed" or not r.tmux_pane or r.tmux_pane not in panes:
            continue
        if sock and r.tmux_socket and os.path.realpath(r.tmux_socket) != sock:
            continue
        if not alive(r.ppid):
            continue
        depth = 0
        if parents:
            d = depth_below(r.ppid, panes[r.tmux_pane], parents)
            if d is None:
                continue  # pid reused by something outside this pane, or a recycled pane id
            depth = d
        cand = (-depth, r.ts, r)
        cur = best.get(r.tmux_pane)
        if cur is None or cand[:2] > cur[:2]:
            best[r.tmux_pane] = cand
    return {k: v[2] for k, v in best.items()}
