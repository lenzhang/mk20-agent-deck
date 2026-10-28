#!/usr/bin/env python3
"""Idempotent merge/removal of the MK20 state hook in Claude Code settings JSON files.

    mk20_hooks_merge.py install   [--dry-run] --command CMD  <settings.json>...
    mk20_hooks_merge.py uninstall [--dry-run]                <settings.json>...

* Existing hooks are kept; only handlers whose command contains MARKER are ours.
  Re-installing replaces our handlers (so a changed path is updated), never duplicates.
* All files are parsed first; any malformed / unexpected-shape file refuses the whole
  run (exit 2) and nothing is written.
* A file whose content would not change is left untouched (no backup, no write).
* Otherwise: backup ``<file>.bak-mk20-<YYYYmmdd-HHMMSS>`` (same mode), then atomic
  write (tmp + rename, original mode kept; a symlink is followed, not replaced).
* ``--dry-run`` prints a unified diff of the resulting JSON and writes nothing.
Hook format: https://code.claude.com/docs/en/hooks (settings ``hooks.<Event>[] =
{"matcher"?, "hooks": [{"type": "command", "command", "timeout"}]}``).
"""
import argparse
import copy
import difflib
import json
import os
import shutil
import sys
import tempfile
import time

MARKER = "mk20-agent-state-hook.py"
# Claude Code events the state hook maps (see mk20-agent-state-hook.py).
CLAUDE_EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PermissionRequest", "PostToolUse",
                 "PostToolUseFailure", "Notification", "Elicitation", "ElicitationResult", "Stop",
                 "StopFailure", "SessionEnd")
TIMEOUT_S = {"SessionEnd": 1}  # SessionEnd hooks share a 1.5 s budget; never raise it
DEFAULT_TIMEOUT_S = 3


class Refuse(Exception):
    pass


def _ours(h):
    return isinstance(h, dict) and isinstance(h.get("command"), str) and MARKER in h["command"]


def validate(doc, path="settings"):
    if not isinstance(doc, dict):
        raise Refuse("%s: top level is not a JSON object" % path)
    hooks = doc.get("hooks")
    if hooks is None:
        return
    if not isinstance(hooks, dict):
        raise Refuse("%s: \"hooks\" is not an object" % path)
    for ev, groups in hooks.items():
        if not isinstance(groups, list):
            raise Refuse("%s: hooks.%s is not a list" % (path, ev))
        for g in groups:
            if not isinstance(g, dict) or not isinstance(g.get("hooks", []), list):
                raise Refuse("%s: hooks.%s has an entry without a \"hooks\" list" % (path, ev))


def strip_ours(doc):
    """Copy of ``doc`` without our handlers; empty groups / events / hooks object are dropped."""
    out = copy.deepcopy(doc)
    hooks = out.get("hooks")
    if not isinstance(hooks, dict):
        return out
    removed = False
    for ev in list(hooks):
        groups, ev_removed = [], False
        for g in hooks[ev]:
            hs = g.get("hooks", [])
            keep = [h for h in hs if not _ours(h)]
            if len(keep) == len(hs):
                groups.append(g)
                continue
            ev_removed = removed = True
            if keep:
                g = dict(g)
                g["hooks"] = keep
                groups.append(g)
        if groups or not ev_removed:
            hooks[ev] = groups
        else:
            del hooks[ev]
    if removed and not hooks:
        del out["hooks"]
    return out


def merge(doc, command, events=CLAUDE_EVENTS):
    if MARKER not in command:
        raise Refuse("command must reference %s" % MARKER)
    out = strip_ours(doc)
    hooks = out.setdefault("hooks", {})
    for ev in events:
        hooks.setdefault(ev, []).append({"hooks": [{"type": "command", "command": command,
                                                    "timeout": TIMEOUT_S.get(ev, DEFAULT_TIMEOUT_S)}]})
    return out


def dumps(doc):
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def load(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        raise Refuse("%s: no such file (create it first; nothing was changed)" % path)
    except OSError as e:
        raise Refuse("%s: %s" % (path, e))
    try:
        doc = json.loads(text) if text.strip() else {}
    except ValueError as e:
        raise Refuse("%s: malformed JSON (%s); nothing was changed" % (path, e))
    validate(doc, path)
    return text, doc


def write_atomic(path, text):
    real = os.path.realpath(path)
    mode = os.stat(real).st_mode & 0o7777
    fd, tmp = tempfile.mkstemp(prefix=".mk20-", dir=os.path.dirname(real))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, real)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def backup(path, stamp):
    real = os.path.realpath(path)
    dst = "%s.bak-mk20-%s" % (real, stamp)
    n = 1
    while os.path.exists(dst):
        n += 1
        dst = "%s.bak-mk20-%s-%d" % (real, stamp, n)
    shutil.copy2(real, dst)
    return dst


def run(action, paths, command=None, dry_run=False, out=sys.stdout, stamp=None):
    """Returns a process exit code: 0 ok, 2 refused (nothing written)."""
    stamp = stamp or time.strftime("%Y%m%d-%H%M%S")
    try:
        if not paths:
            raise Refuse("no settings files given")
        loaded = [(p,) + load(p) for p in paths]
        plans = []
        for p, text, doc in loaded:
            new = merge(doc, command) if action == "install" else strip_ours(doc)
            plans.append((p, text, doc, new))
    except Refuse as e:
        print("refuse: %s" % e, file=out)
        return 2
    for p, text, doc, new in plans:
        if new == doc:
            print("%s: unchanged" % p, file=out)
            continue
        if dry_run:
            diff = difflib.unified_diff(dumps(doc).splitlines(True), dumps(new).splitlines(True),
                                        fromfile=p, tofile="%s (after %s)" % (p, action))
            out.write("".join(diff))
            continue
        b = backup(p, stamp)
        write_atomic(p, dumps(new))
        print("%s: %s (backup %s)" % (p, "hooks merged" if action == "install" else "mk20 hooks removed", b),
              file=out)
    if dry_run:
        print("dry run: nothing written", file=out)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=("install", "uninstall"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--command", default=None, help="hook command (install)")
    ap.add_argument("paths", nargs="*")
    a = ap.parse_intermixed_args(argv)  # <3.12 argparse: nargs="*" after options (system python 3.9)
    if a.action == "install" and not a.command:
        ap.error("install needs --command")
    return run(a.action, a.paths, a.command, a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
