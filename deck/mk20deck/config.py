"""Config file (~/.config/mk20-agent-deck/config.toml) and persisted state."""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import tempfile
import tomllib
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


def parse_backlight(v: Any) -> int | None:
    """``false``/``"off"``/``"none"``/negative -> None (don't touch); otherwise clamp to 0..100."""
    if v is None or v is False or (isinstance(v, str) and v.strip().lower() in ("off", "none", "false", "")):
        return None
    if v is True:
        return 80
    n = int(v)
    return None if n < 0 else max(0, min(100, n))


def load_config(path: str | None = None) -> DeckConfig:
    cfg = DeckConfig()
    path = path or os.path.join(CONFIG_DIR, "config.toml")
    if not os.path.exists(path):
        return cfg
    with open(path, "rb") as f:
        d = tomllib.load(f)
    deck = d.get("deck", {})
    if "default_mode" in deck:
        cfg.default_mode = Mode(deck["default_mode"])
    for k in ("port", "fw_coord_base", "max_fps", "animations", "jpeg_quality", "poll_interval_s",
              "heartbeat_s", "ack_timeout_s", "preamble_flush", "backlight", "state_path"):
        if k in deck:
            setattr(cfg, k, deck[k])
    cfg.backlight = parse_backlight(cfg.backlight)
    if "web_url" in deck:
        cfg.web_url = parse_web_url(deck["web_url"])
    web = d.get("web", {})
    if isinstance(web, dict) and "arrange_port" in web:
        cfg.arrange_port = parse_port(web["arrange_port"])
    if cfg.fw_coord_base == "auto":
        cfg.fw_coord_base = None
    cfg.max_fps = min(float(cfg.max_fps), 8.0)  # PRD §7.4 hard ceiling
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
    cx = d.get("codex_desktop", {})
    cfg.codex = CodexDesktopConfig(
        codex_home=cx.get("codex_home", "~/.codex"),
        originators=tuple(cx.get("originators", DEFAULT_ORIGINATORS)),
        running_window_s=float(cx.get("running_window_s", 600)),
        max_candidates=int(cx.get("max_candidates", 24)),
        max_tail_threads=int(cx.get("max_tail_threads", 16)),
        max_prompt_chars=int(cx.get("max_prompt_chars", 2000)),
        max_url_len=int(cx.get("max_url_len", 8000)),
        require_cwd_exists=bool(cx.get("require_cwd_exists", False)),
    )
    tm = d.get("tmux", {})
    cfg.tmux = TmuxConfig(
        binary=tm.get("binary", "tmux"),
        patterns=tuple(tm.get("patterns", DEFAULT_PATTERNS)),
        registered_panes=tuple(tm.get("registered_panes", ())),
        terminal_app=tm.get("terminal_app", ""),
        new_session_cmd=tm.get("new_session_cmd", ""),
        agent_commands=tuple(str(c) for c in tm.get("agent_commands", DEFAULT_AGENT_COMMANDS)),
        use_hooks=bool(tm.get("use_hooks", True)),
        state_dir=os.path.expanduser(str(tm.get("state_dir", ""))),
        hook_max_age_s=float(tm.get("hook_max_age_s", 12 * 3600)),
        running_stale_s=float(tm.get("running_stale_s", 900)),
    )
    if "background_poll_s" in deck:
        cfg.background_poll_s = max(0.0, float(deck["background_poll_s"]))
    cfg.herdr = HerdrConfig(binary=d.get("herdr", {}).get("binary", "herdr"))
    if "knobs" in d:  # a [knobs] table replaces the measured defaults
        cfg.knob_map = {str(k): str(v) for k, v in d["knobs"].items()}
    cfg.keycode_map = {str(k).lower(): str(v) for k, v in d.get("keycodes", {}).items()}
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
