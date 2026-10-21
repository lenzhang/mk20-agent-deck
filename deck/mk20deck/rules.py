"""Slot rules ("固定规则", user decision 2026-10-22): pin a *kind* of session to a slot, per mode.

A rule says "the session whose <directory / tmux window / title / tool> matches <value> goes on S<n>".
``SlotManager.update`` applies the enabled rules of its mode on every poll (precedence: rules > manual pins
> auto fill); this module owns the rule objects, matching, resolution and the ``slot-rules.json`` store.

Match kinds:

* ``cwd``    目录 — the session cwd equals the value or lies below it (path prefix on component boundaries).
* ``window`` tmux 窗口名 — exact tmux window *or* session name; ``*``/``?``/``[...]`` make it a glob.
* ``title``  标题包含 — case-insensitive substring of the session title (Codex Desktop thread title).
* ``tool``   工具 — ``ccai`` / ``codex`` / ``kimi``.

Resolution (``resolve``): enabled rules in slot order; each takes the most recently active matching session
not already taken by a lower slot.  So a session matching several rules lands on the lowest slot, and when
two sessions match one rule the more recent one wins and the other falls back to auto fill.  An enabled rule
with no matching session *reserves* its slot ("等待：<label>"): auto fill never uses it.

Store: ``$MK20DECK_HOME/slot-rules.json`` (default ``~/.config/mk20-agent-deck``), schema ``version`` 1,
written atomically (tmp + fsync + rename, mode 0600).  A malformed file never crashes the deck: its rules are
ignored, ``warning`` says why (shown on the arrange page and the keyboard sub-screen) and the first save
moves the bad file aside (``slot-rules.json.bad-<time>``) instead of silently overwriting it.

Rule objects are frozen; the store replaces them on change.  All mutation happens on the deck loop thread
(the web server queues its changes there, see ``webui``).
"""
from __future__ import annotations

import fnmatch
import json
import logging
import math
import os
import secrets
import tempfile
import time
from dataclasses import asdict, dataclass, replace
from typing import Iterable, Mapping

from .model import PRIORITY, Mode, Session, Tool

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
N_SLOTS = 15  # == slots.N_SLOTS (slots imports this module; kept literal to avoid a cycle)
KINDS = ("cwd", "window", "title", "tool")
KIND_TEXT = {"cwd": "目录", "window": "tmux 窗口名", "title": "标题包含", "tool": "工具"}
RULE_TOOLS = (Tool.CCAI.value, Tool.CODEX.value, Tool.KIMI.value)
MAX_VALUE = 200
MAX_NOTE = 120
MAX_RULES = N_SLOTS * 3  # one rule per slot per mode


def default_rules_path() -> str:
    """Honours ``MK20DECK_HOME`` at call time (tests and the CLI may set it after import)."""
    home = os.path.expanduser(os.environ.get("MK20DECK_HOME", "~/.config/mk20-agent-deck"))
    return os.path.join(home, "slot-rules.json")


def _norm_path(p: str) -> str:
    p = os.path.normpath(os.path.expanduser(p.strip()))
    return p.rstrip("/") or "/"


def worktree_root(cwd: str) -> str:
    """The worktree directory (``…/worktrees/<name>``) containing ``cwd``, else ``cwd`` itself."""
    p = _norm_path(cwd)
    parts = p.split(os.sep)
    for i in range(len(parts) - 2, -1, -1):
        if parts[i] in ("worktrees", ".worktrees") and i + 1 < len(parts) and parts[i + 1]:
            return os.sep.join(parts[:i + 2]) or "/"
    return p


def dir_name(path: str | None) -> str:
    """Display name of a directory: worktree name, else basename (never the full path)."""
    if not path:
        return ""
    p = _norm_path(path)
    if p == os.path.expanduser("~"):
        return "~"
    parts = [x for x in p.split(os.sep) if x]
    for i in range(len(parts) - 2, -1, -1):
        if parts[i] in ("worktrees", ".worktrees"):
            return parts[i + 1]
    return parts[-1] if parts else "/"


def _clean_text(v: object, limit: int) -> str:
    if not isinstance(v, str):
        raise ValueError("值必须是文字")
    v = v.strip()
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in v):
        raise ValueError("值不能含控制字符")
    if len(v) > limit:
        raise ValueError(f"值太长（最多 {limit} 字）")
    return v


def validate(mode: Mode, slot: object, kind: object, value: object) -> tuple[int, str, str]:
    """Normalised ``(slot, kind, value)`` or ValueError with a Chinese message."""
    if isinstance(slot, bool) or not isinstance(slot, int) or not 0 <= slot < N_SLOTS:
        raise ValueError("键位无效（S1–S15）")
    if kind not in KINDS:
        raise ValueError("匹配方式无效")
    v = _clean_text(value, MAX_VALUE)
    if not v:
        raise ValueError("匹配值不能为空")
    if kind == "cwd":
        if not (v.startswith("/") or v.startswith("~")):
            raise ValueError("目录必须是绝对路径（/… 或 ~/…）")
        v = _norm_path(v)
    elif kind == "window" and mode != Mode.TMUX:
        raise ValueError("tmux 窗口名规则只用于 tmux 模式")
    elif kind == "tool":
        v = v.lower()
        if v not in RULE_TOOLS:
            raise ValueError("工具只能是 ccai / codex / kimi")
    return slot, str(kind), v


@dataclass(frozen=True)
class SlotRule:
    id: str
    mode: Mode
    slot: int  # 0..14
    kind: str
    value: str
    enabled: bool = True
    note: str = ""  # why it was disabled (e.g. a keyboard move into its slot)
    created: float = 0.0

    def matches(self, s: Session) -> bool:
        v = self.value
        if self.kind == "cwd":
            if not s.cwd:
                return False
            c = _norm_path(s.cwd)
            return c == v or c.startswith(v.rstrip("/") + "/")
        if self.kind == "window":
            names = [n for n in (getattr(s, "window", ""), getattr(s, "tmux_session", "")) if n]
            if any(ch in v for ch in "*?["):
                return any(fnmatch.fnmatchcase(n, v) for n in names)
            return v in names
        if self.kind == "title":
            return v.casefold() in (s.title or "").casefold()
        if self.kind == "tool":
            return s.tool.value == v
        return False

    def display_value(self) -> str:
        """Value as shown to the user: directories by name only (no full paths leave the Mac process)."""
        return dir_name(self.value) if self.kind == "cwd" else self.value

    def label(self) -> str:
        return f"{KIND_TEXT.get(self.kind, self.kind)} {self.display_value()}"

    def short_label(self) -> str:
        """Key-face text after "等待：": the 128 px key face fits "等待：" + ~9 half-width units at 15 px
        (CJK = 2 units, everything else 1); longer values are cut to 7 units + "…"."""
        v = self.display_value()
        units = [2 if ord(c) > 0x2E7F else 1 for c in v]
        if sum(units) <= 9:
            return v
        n, used = 0, 0
        while n < len(v) and used + units[n] <= 7:
            used += units[n]
            n += 1
        return v[:n] + "…"

    def to_json(self) -> dict:
        d = asdict(self)
        d["mode"] = self.mode.value
        return d


MAX_CREATED = 1e11  # epoch seconds (year ~5138): anything larger is corrupt


def _number(v: object, lo: float, hi: float, what: str) -> float:
    """Finite JSON number in [lo, hi]; huge ints / NaN / inf / bools are rejected (never OverflowError)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"{what} 无效")
    try:
        f = float(v)
    except (OverflowError, ValueError):
        raise ValueError(f"{what} 超出范围") from None
    if not math.isfinite(f) or not lo <= f <= hi:
        raise ValueError(f"{what} 超出范围")
    return f


def _rule_from_json(d: object) -> SlotRule:
    if not isinstance(d, dict):
        raise ValueError("不是对象")
    rid = d.get("id")
    if not isinstance(rid, str) or not rid or len(rid) > 32 or not rid.replace("-", "").isalnum() \
            or not rid.isascii():
        raise ValueError("id 无效")
    try:
        mode = Mode(d.get("mode"))
    except (ValueError, TypeError):
        raise ValueError("mode 无效") from None
    slot, kind, value = validate(mode, d.get("slot"), d.get("kind"), d.get("value"))
    enabled = d.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("enabled 无效")
    note = d.get("note", "")
    note = note[:MAX_NOTE] if isinstance(note, str) else ""
    created = _number(d.get("created", 0.0), 0.0, MAX_CREATED, "created") if "created" in d else 0.0
    return SlotRule(rid, mode, slot, kind, value, enabled, note, created)


def recency(s: Session) -> tuple:
    return (s.updated_ms, PRIORITY[s.state])


def resolve(rules: Iterable[SlotRule], sessions: Mapping[str, Session],
            holder: Mapping[int, str] | None = None) -> tuple[dict[int, tuple[SlotRule, str]], dict[int, SlotRule]]:
    """Assign sessions to enabled rules.

    Returns ``(placed, waiting)``: ``placed[slot] = (rule, session key)``; ``waiting[slot] = rule`` for enabled
    rules with no (free) matching session.  ``holder[slot]`` = key currently in that slot: on an exact recency
    tie it keeps the slot (no flapping between two identical candidates)."""
    holder = holder or {}
    taken: set[str] = set()
    placed: dict[int, tuple[SlotRule, str]] = {}
    waiting: dict[int, SlotRule] = {}
    seen_slots: set[int] = set()
    for r in sorted((r for r in rules if r.enabled), key=lambda r: (r.slot, r.created, r.id)):
        if r.slot in seen_slots:  # one rule per slot (the store enforces it; never trust a list blindly)
            continue
        seen_slots.add(r.slot)
        cands = [s for k, s in sessions.items() if k not in taken and r.matches(s)]
        if not cands:
            waiting[r.slot] = r
            continue
        cur = holder.get(r.slot)
        best = max(cands, key=lambda s: (recency(s), s.sid.key() == cur, s.sid.key()))
        k = best.sid.key()
        taken.add(k)
        placed[r.slot] = (r, k)
    return placed, waiting


class RulesSaveError(OSError):
    """A rule change could not be written: nothing was changed (``str`` is the Chinese message)."""


class RulesStore:
    """All rules of all modes, persisted in ``slot-rules.json``.  Loop-thread only (not locked).

    Changes are transactional: the candidate list is written atomically first and only then swapped in
    memory, so a failed write leaves both the file and the in-memory rules unchanged (``RulesSaveError``).
    The one exception is ``disable`` after a manual keyboard change (``strict=False``): the user's change
    already happened, so the rule is disabled in memory anyway and ``warning`` says it is not on disk yet
    (the next successful save writes it)."""

    def __init__(self, path: str | None = None, clock=time.time) -> None:
        self.path = path or default_rules_path()
        self.clock = clock
        self.rules: list[SlotRule] = []
        self.warning = ""  # malformed file / skipped entries / save failure (Chinese, no paths)
        self._bad_file = False  # the file on disk has content we did not load: move it aside before saving
        self.dirty = False  # in-memory rules newer than the file (a non-strict save failed)
        self.revision = 0
        try:
            self.load()
        except Exception as e:  # noqa: BLE001 — a rules file never stops the daemon
            self.rules = []
            self._warn(f"规则文件无法解析（{type(e).__name__}），全部规则已忽略", bad=True)

    # ------------------------------------------------------------------ io
    def load(self) -> None:
        self.rules, self.warning, self._bad_file, self.dirty = [], "", False, False
        try:
            with open(self.path, "rb") as f:
                raw = f.read(1024 * 1024 + 1)
        except FileNotFoundError:
            return
        except OSError as e:
            self._warn(f"规则文件无法读取（{type(e).__name__}），规则未生效", bad=False)
            return
        try:
            if len(raw) > 1024 * 1024:
                raise ValueError("文件过大")
            d = json.loads(raw.decode("utf-8"))
            if not isinstance(d, dict) or not isinstance(d.get("rules"), list):
                raise ValueError("格式不对")
            if d.get("version") != SCHEMA_VERSION:
                raise ValueError(f"版本 {str(d.get('version'))[:12]!r} 不支持")
        except (ValueError, UnicodeDecodeError, RecursionError) as e:
            if isinstance(e, json.JSONDecodeError):
                why = f"JSON 第 {e.lineno} 行第 {e.colno} 列"
            elif isinstance(e, UnicodeDecodeError):
                why = "不是 UTF-8"
            elif isinstance(e, RecursionError):
                why = "嵌套过深"
            else:
                why = str(e)[:40]
            self._warn(f"规则文件格式错误（{why}），全部规则已忽略", bad=True)
            return
        out, bad, slots = [], 0, set()
        for item in d["rules"][:MAX_RULES]:
            try:
                r = _rule_from_json(item)
            except Exception:  # noqa: BLE001 — quarantine this entry, keep the rest
                bad += 1
                continue
            if (r.mode, r.slot) in slots or any(x.id == r.id for x in out):
                bad += 1
                continue
            slots.add((r.mode, r.slot))
            out.append(r)
        bad += max(0, len(d["rules"]) - MAX_RULES)
        self.rules = out
        if bad:  # the skipped entries stay in a backup (.bad-<time>) when the file is next rewritten
            self._warn(f"规则文件有 {bad} 条无效规则，已忽略（下次保存前备份原文件）", bad=True)

    def _warn(self, msg: str, bad: bool) -> None:
        self.warning = msg
        self._bad_file = self._bad_file or bad
        log.warning("slot rules: %s", msg)

    def _write(self, rules: list[SlotRule]) -> None:
        """Atomic write of ``rules`` (tmp + fsync + rename, 0600); raises OSError, never half-writes."""
        d = {"version": SCHEMA_VERSION, "rules": [r.to_json() for r in rules]}
        folder = os.path.dirname(self.path) or "."
        os.makedirs(folder, mode=0o700, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".slot-rules.", dir=folder)  # mkstemp: mode 0600
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=1)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            if self._bad_file and os.path.exists(self.path):
                os.replace(self.path, f"{self.path}.bad-{time.strftime('%Y%m%d-%H%M%S')}")
                self._bad_file = False
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def save(self) -> bool:
        try:
            self._write(self.rules)
        except OSError as e:
            self.warning = f"规则保存失败（{type(e).__name__}）"
            log.warning("slot rules: save failed: %s", e)
            return False
        self.dirty, self.warning = False, ""
        return True

    # ------------------------------------------------------------------ queries
    def for_mode(self, mode: Mode) -> list[SlotRule]:
        return sorted((r for r in self.rules if r.mode == mode), key=lambda r: r.slot)

    def get(self, rid: str) -> SlotRule | None:
        return next((r for r in self.rules if r.id == rid), None)

    def at(self, mode: Mode, slot: int) -> SlotRule | None:
        return next((r for r in self.rules if r.mode == mode and r.slot == slot), None)

    # ------------------------------------------------------------------ changes (each saves)
    def _commit(self, candidate: list[SlotRule], strict: bool = True) -> None:
        try:
            self._write(candidate)
        except OSError as e:
            msg = f"规则保存失败（{type(e).__name__}）"
            log.warning("slot rules: save failed: %s", e)
            if strict:  # nothing changed: the API error says so (no lingering warning)
                raise RulesSaveError(msg + "，未做修改") from e
            self.rules, self.dirty = candidate, True
            self.warning = msg + "：停用只在本次运行有效"
            self.revision += 1
            return
        self.rules, self.dirty, self.warning = candidate, False, ""
        self.revision += 1

    def put(self, mode: Mode, slot: int, kind: str, value: str, enabled: bool = True) -> SlotRule:
        """Create the rule of ``(mode, slot)`` (replacing the slot's previous rule)."""
        slot, kind, value = validate(mode, slot, kind, value)
        old = self.at(mode, slot)
        r = SlotRule(old.id if old else "r" + secrets.token_hex(5), mode, slot, kind, value, bool(enabled), "",
                     _number(self.clock(), 0.0, MAX_CREATED, "created"))
        self._commit([x for x in self.rules if x is not old] + [r])
        return r

    def update(self, rid: str, strict: bool = True, **kw) -> SlotRule:
        old = self.get(rid)
        if old is None:
            raise KeyError(rid)
        kind = kw.get("kind", old.kind)
        value = kw.get("value", old.value)
        slot, kind, value = validate(old.mode, old.slot, kind, value)
        enabled = kw.get("enabled", old.enabled)
        if not isinstance(enabled, bool):
            raise ValueError("enabled 必须是 true/false")
        note = "" if enabled else str(kw.get("note", old.note))[:MAX_NOTE]
        r = replace(old, kind=kind, value=value, enabled=enabled, note=note)
        self._commit([r if x.id == rid else x for x in self.rules], strict)
        return r

    def delete(self, rid: str) -> SlotRule:
        old = self.get(rid)
        if old is None:
            raise KeyError(rid)
        self._commit([x for x in self.rules if x.id != rid])
        return old

    def disable(self, rid: str, note: str) -> SlotRule | None:
        """After a manual change on the rule's slot (keyboard): never raises on a write failure."""
        old = self.get(rid)
        if old is None or not old.enabled:
            return old
        return self.update(rid, strict=False, enabled=False, note=note)
