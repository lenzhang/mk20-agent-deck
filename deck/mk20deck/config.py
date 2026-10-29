"""Config file (~/.config/mk20-agent-deck/config.toml) and persisted state."""
from __future__ import annotations

import ipaddress
import json
import logging
import math
import os
import tempfile

try:  # 3.11+
    import tomllib
except ModuleNotFoundError:  # macOS system python 3.9 (LAN-permitted, see docs/deploy-system-python.md)
    import tomli as tomllib
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from .adapters.codex_desktop import DEFAULT_ORIGINATORS, CodexDesktopConfig
from .adapters.herdr import HerdrConfig
from .adapters.tmux import DEFAULT_AGENT_COMMANDS, DEFAULT_PATTERNS, TmuxConfig
from .model import Mode

CONFIG_DIR = os.path.expanduser(os.environ.get("MK20DECK_HOME", "~/.config/mk20-agent-deck"))
# Device web settings, opened on the Mac when the keyboard sends {"method":"ui.openSettings"} (网页设置 key).
# The URL comes only from this config, never from the device.
DEFAULT_WEB_URL = "http://172.30.250.2:8780/"
# Local "会话安排" page served by mk20deck itself (webui.py), bound to 127.0.0.1 only; 0 = off.
DEFAULT_ARRANGE_PORT = 8781
log = logging.getLogger(__name__)

DEFAULT_SKILLS = (
    ("审 PR", "请审查当前分支相对主干的改动（PR review），列出问题与建议。"),
    ("调试测试", "请运行并调试失败的测试，找出根因并给出修复。"),
    ("重构", "请在不改变行为的前提下重构相关代码，并说明改动。"),
    ("写文档", "请为最近的改动补充/更新文档（README/注释）。"),
)


@dataclass
class Skill:
    label: str
    prompt: str


# Measured on V2.32 (devtest re-run 2026-10-18 00:28): knobs report keyStateChanged with
# col == row >= 100; rotation = one pressed:true per detent (no release).
DEFAULT_KNOB_MAP = {"100,100": "K1:press", "101,101": "K1:ccw", "102,102": "K1:cw",
                    "103,103": "K2:press", "104,104": "K2:ccw", "105,105": "K2:cw"}


@dataclass
class DeckConfig:
    default_mode: Mode = Mode.DESKTOP
    port: str = "auto"
    fw_coord_base: int | None = None  # None/"auto" = infer from getInfo rects
    max_fps: float = 8.0
    animations: bool = True
    jpeg_quality: int = 85
    poll_interval_s: float = 2.0
    background_poll_s: float = 10.0  # non-current modes (attention counts); 0 disables
    heartbeat_s: float = 2.0
    ack_timeout_s: float = 1.0
    preamble_flush: int = 1024 * 1024
    # setBacklight level sent right after every handshake (first connect, replug, wake).
    # The device comes out of offline mode with backlight 0 (dark keys) — see
    # NOTES-protocol-re-2026-10-17.md "Official app test".  false/"off" disables.
    backlight: int | None = 80
    ptt_fallback_b: bool = False  # PRD §6.5: B only after IME verification; not implemented in P0
    # Not bound to any key since the PRD 1.5 L1 layout; kept for config compatibility.
    skills: list[Skill] = field(default_factory=lambda: [Skill(l, p) for l, p in DEFAULT_SKILLS])
    codex: CodexDesktopConfig = field(default_factory=CodexDesktopConfig)
    tmux: TmuxConfig = field(default_factory=TmuxConfig)
    herdr: HerdrConfig = field(default_factory=HerdrConfig)
    # Knob event mapping (PRD §6.4).  Keys are "col,row" firmware coordinates reported
    # by keyStateChanged (knobs report col/row >= 100), values like "K1:press".
    knob_map: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_KNOB_MAP))
    # keycode-form key events (keyCodeH/L): "0x00e6" -> "1,4" (document col,row) or "K1:cw"
    keycode_map: dict[str, str] = field(default_factory=dict)
    state_path: str = os.path.join(CONFIG_DIR, "state.json")
    web_url: str = DEFAULT_WEB_URL  # see parse_web_url
    arrange_port: int = DEFAULT_ARRANGE_PORT  # [web] arrange_port, see parse_port
    # Long-press approve (option mode, permission prompts): hold 「是」 this long; 0 = off (typed on press).
    approve_hold_s: float = 1.0
    approve_sound: str = "Glass"  # /System/Library/Sounds/<name>.aiff after an approve; "" = silent
    snap_dir: str = ""  # [snap] save_dir: also keep each screenshot as a PNG there ("" = clipboard only)


def parse_sound(v: Any) -> str:
    """A macOS system sound name (``Glass``, ``Ping`` …) or "" (off); anything else is off with a warning."""
    if v in (None, False, ""):
        return ""
    if isinstance(v, str) and 0 < len(v) <= 32 and all(c.isalnum() or c in " _-" for c in v):
        return v
    log.warning("approve_sound %r ignored (a name from /System/Library/Sounds, or \"\")", v)
    return ""


def parse_web_url(v: Any) -> str:
    """http(s) URL whose host is an IP literal or ``localhost`` and that carries no credentials; anything else
    falls back to DEFAULT_WEB_URL with a warning (it is handed to ``open`` on a device-initiated event)."""
    reason = None
    if not isinstance(v, str) or not v:
        reason = "not a string"
    elif any(c.isspace() or ord(c) < 0x20 or ord(c) == 0x7F for c in v):
        reason = "whitespace/control characters"
    else:
        u = urlsplit(v)
        try:
            u.port  # noqa: B018 — raises ValueError on a bad port
        except ValueError:
            reason = "bad port"
        host = u.hostname or ""
        if reason:
            pass
        elif u.scheme not in ("http", "https"):
            reason = "scheme must be http or https"
        elif u.username is not None or u.password is not None or "@" in u.netloc:
            reason = "credentials are not allowed"
        elif host != "localhost":
            try:
                ipaddress.ip_address(host)
            except ValueError:
                reason = "host must be an IP literal or localhost"
    if reason:
        shown = "<redacted>" if reason.startswith("credentials") else repr(v)[:120]
        log.warning("config web_url %s rejected (%s): using %s", shown, reason, DEFAULT_WEB_URL)
        return DEFAULT_WEB_URL
    return v


def parse_port(v: Any, default: int = DEFAULT_ARRANGE_PORT) -> int:
    """TCP port 1..65535, or 0 = off; anything else falls back to ``default`` with a warning."""
    if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 65535:
        return v
    log.warning("config [web] arrange_port %r rejected (integer 0..65535, 0 = off): using %s", v, default)
    return default


def _table(value: Any, name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    log.warning("config [%s] ignored (expected a table)", name)
    return {}


def _float(value: Any, default: float, name: str, *, minimum: float | None = None,
           maximum: float | None = None) -> float:
    try:
        if isinstance(value, bool):
            raise ValueError
        n = float(value)
        if not math.isfinite(n):
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        log.warning("config %s %r ignored (using %s)", name, value, default)
        return default
    if minimum is not None and n < minimum:
        log.warning("config %s %r clamped to %s", name, value, minimum)
        n = minimum
    if maximum is not None and n > maximum:
        log.warning("config %s %r clamped to %s", name, value, maximum)
        n = maximum
    return n


def _int(value: Any, default: int, name: str, *, minimum: int | None = None,
         maximum: int | None = None) -> int:
    try:
        if isinstance(value, bool):
            raise ValueError
        n = int(value)
    except (TypeError, ValueError, OverflowError):
        log.warning("config %s %r ignored (using %s)", name, value, default)
        return default
    if minimum is not None and n < minimum:
        log.warning("config %s %r clamped to %s", name, value, minimum)
        n = minimum
    if maximum is not None and n > maximum:
        log.warning("config %s %r clamped to %s", name, value, maximum)
        n = maximum
    return n


def _bool(value: Any, default: bool, name: str) -> bool:
    if isinstance(value, bool):
        return value
    log.warning("config %s %r ignored (using %s)", name, value, default)
    return default


def parse_backlight(v: Any) -> int | None:
    """``false``/``"off"``/``"none"``/negative -> None (don't touch); otherwise clamp to 0..100."""
    if v is None or v is False or (isinstance(v, str) and v.strip().lower() in ("off", "none", "false", "")):
        return None
    if v is True:
        return 80
    try:
        n = int(v)
    except (TypeError, ValueError, OverflowError):
        log.warning("config backlight %r ignored (using 80)", v)
        return 80
    return None if n < 0 else max(0, min(100, n))


def load_config(path: str | None = None) -> DeckConfig:
    cfg = DeckConfig()
    path = path or os.path.join(CONFIG_DIR, "config.toml")
    if not os.path.exists(path):
        return cfg
    try:
        with open(path, "rb") as f:
            d = tomllib.load(f)
    except (OSError, ValueError) as e:
        log.warning("config %s ignored: %s", path, e)
        return cfg
    if not isinstance(d, dict):
        log.warning("config %s ignored (root is not a table)", path)
        return cfg
    deck = _table(d.get("deck"), "deck")
    if "default_mode" in deck:
        try:
            cfg.default_mode = Mode(deck["default_mode"])
        except (TypeError, ValueError):
            log.warning("config default_mode %r ignored (using %s)", deck["default_mode"], cfg.default_mode.value)
    if "port" in deck:
        if isinstance(deck["port"], str):
            cfg.port = deck["port"]
        else:
            log.warning("config port %r ignored (using %s)", deck["port"], cfg.port)
    if "fw_coord_base" in deck:
        raw = deck["fw_coord_base"]
        if raw in ("auto", None):
            cfg.fw_coord_base = None
        elif not isinstance(raw, bool) and raw in (0, 1):
            cfg.fw_coord_base = raw
        else:
            log.warning("config fw_coord_base %r ignored (using auto)", raw)
    if "max_fps" in deck:
        cfg.max_fps = _float(deck["max_fps"], cfg.max_fps, "max_fps", minimum=0.0, maximum=8.0)
    if "animations" in deck:
        if isinstance(deck["animations"], bool):
            cfg.animations = deck["animations"]
        else:
            log.warning("config animations %r ignored (using %s)", deck["animations"], cfg.animations)
    if "jpeg_quality" in deck:
        cfg.jpeg_quality = _int(deck["jpeg_quality"], cfg.jpeg_quality, "jpeg_quality", minimum=1, maximum=100)
    if "poll_interval_s" in deck:
        cfg.poll_interval_s = _float(deck["poll_interval_s"], cfg.poll_interval_s, "poll_interval_s", minimum=0.01)
    if "heartbeat_s" in deck:
        cfg.heartbeat_s = _float(deck["heartbeat_s"], cfg.heartbeat_s, "heartbeat_s", minimum=0.01)
    if "ack_timeout_s" in deck:
        cfg.ack_timeout_s = _float(deck["ack_timeout_s"], cfg.ack_timeout_s, "ack_timeout_s", minimum=0.01)
    if "preamble_flush" in deck:
        cfg.preamble_flush = _int(deck["preamble_flush"], cfg.preamble_flush, "preamble_flush", minimum=0)
    if "state_path" in deck:
        if isinstance(deck["state_path"], str):
            cfg.state_path = deck["state_path"]
        else:
            log.warning("config state_path %r ignored (using %s)", deck["state_path"], cfg.state_path)
    if "backlight" in deck:
        cfg.backlight = parse_backlight(deck["backlight"])
    if "web_url" in deck:
        cfg.web_url = parse_web_url(deck["web_url"])
    if "approve_hold_s" in deck:
        cfg.approve_hold_s = _float(deck["approve_hold_s"], cfg.approve_hold_s, "approve_hold_s", minimum=0.0,
                                    maximum=5.0)
    if "approve_sound" in deck:
        cfg.approve_sound = parse_sound(deck["approve_sound"])
    snap = _table(d.get("snap"), "snap")
    if isinstance(snap.get("save_dir"), str):
        cfg.snap_dir = os.path.expanduser(snap["save_dir"])
    web = _table(d.get("web"), "web")
    if "arrange_port" in web:
        cfg.arrange_port = parse_port(web["arrange_port"])
    cfg.state_path = os.path.expanduser(cfg.state_path)
    if "skills" in d:
        # PRD 1.5 L1 layout: no skill keys any more.  [[skills]] is still parsed (adapters keep
        # skill()) so old config files load unchanged; a malformed table is ignored, never fatal.
        try:
            sk = [Skill(str(s["label"]), str(s["prompt"])) for s in d["skills"][:4]]
        except (KeyError, TypeError):
            log.warning("config [[skills]] malformed (ignored; skill keys were removed in the 1.5 layout)")
            sk = []
        if len(sk) == 4:
            cfg.skills = sk
    cx = _table(d.get("codex_desktop"), "codex_desktop")
    originators = cx.get("originators", DEFAULT_ORIGINATORS)
    if not isinstance(originators, (list, tuple)):
        log.warning("config codex_desktop.originators %r ignored", originators)
        originators = DEFAULT_ORIGINATORS
    cfg.codex = CodexDesktopConfig(
        codex_home=cx.get("codex_home", "~/.codex") if isinstance(cx.get("codex_home", "~/.codex"), str) else "~/.codex",
        originators=tuple(str(x) for x in originators),
        running_window_s=_float(cx.get("running_window_s", 600), 600.0, "codex_desktop.running_window_s", minimum=0.1),
        max_candidates=_int(cx.get("max_candidates", 24), 24, "codex_desktop.max_candidates", minimum=1),
        max_tail_threads=_int(cx.get("max_tail_threads", 16), 16, "codex_desktop.max_tail_threads", minimum=1),
        max_prompt_chars=_int(cx.get("max_prompt_chars", 2000), 2000, "codex_desktop.max_prompt_chars", minimum=1),
        max_url_len=_int(cx.get("max_url_len", 8000), 8000, "codex_desktop.max_url_len", minimum=256),
        require_cwd_exists=_bool(cx.get("require_cwd_exists", False), False, "codex_desktop.require_cwd_exists"),
        scroll_app=str(cx.get("scroll_app", "com.openai.codex")),
        scroll_via=str(cx.get("scroll_via", "pid")),
        scroll_helper=str(cx.get("scroll_helper", "")),
    )
    tm = _table(d.get("tmux"), "tmux")
    patterns = tm.get("patterns", DEFAULT_PATTERNS)
    if not isinstance(patterns, (list, tuple)):
        log.warning("config tmux.patterns %r ignored", patterns)
        patterns = DEFAULT_PATTERNS
    registered_panes = tm.get("registered_panes", ())
    if not isinstance(registered_panes, (list, tuple)):
        log.warning("config tmux.registered_panes %r ignored", registered_panes)
        registered_panes = ()
    agent_commands = tm.get("agent_commands", DEFAULT_AGENT_COMMANDS)
    if not isinstance(agent_commands, (list, tuple)):
        log.warning("config tmux.agent_commands %r ignored", agent_commands)
        agent_commands = DEFAULT_AGENT_COMMANDS
    cfg.tmux = TmuxConfig(
        binary=str(tm.get("binary", "tmux")),
        patterns=tuple(str(x) for x in patterns),
        registered_panes=tuple(str(x) for x in registered_panes),
        terminal_app=str(tm.get("terminal_app", "")),
        new_session_cmd=str(tm.get("new_session_cmd", "")),
        agent_commands=tuple(str(c) for c in agent_commands),
        use_hooks=_bool(tm.get("use_hooks", True), True, "tmux.use_hooks"),
        state_dir=os.path.expanduser(str(tm.get("state_dir", ""))),
        hook_max_age_s=_float(tm.get("hook_max_age_s", 12 * 3600), 12 * 3600.0, "tmux.hook_max_age_s", minimum=0.1),
        running_stale_s=_float(tm.get("running_stale_s", 900), 900.0, "tmux.running_stale_s", minimum=0.1),
    )
    if "background_poll_s" in deck:
        cfg.background_poll_s = _float(deck["background_poll_s"], cfg.background_poll_s, "background_poll_s", minimum=0.0)
    herdr = _table(d.get("herdr"), "herdr")
    cfg.herdr = HerdrConfig(binary=str(herdr.get("binary", "herdr")))
    knobs = _table(d.get("knobs"), "knobs")
    if "knobs" in d:  # a [knobs] table replaces the measured defaults, including an explicit empty table
        cfg.knob_map = {str(k): str(v) for k, v in knobs.items()}
    keycodes = _table(d.get("keycodes"), "keycodes")
    cfg.keycode_map = {str(k).lower(): str(v) for k, v in keycodes.items()}
    return cfg


def load_state(path: str) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(path: str, state: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".state.", dir=os.path.dirname(path))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
