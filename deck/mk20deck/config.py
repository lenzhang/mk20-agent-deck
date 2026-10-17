"""Config file (~/.config/mk20-agent-deck/config.toml) and persisted state."""
from __future__ import annotations

import json
import os
import tempfile
import tomllib
from dataclasses import dataclass, field
from typing import Any

from .adapters.codex_desktop import DEFAULT_ORIGINATORS, CodexDesktopConfig
from .adapters.herdr import HerdrConfig
from .adapters.tmux import TmuxConfig
from .model import Mode

CONFIG_DIR = os.path.expanduser(os.environ.get("MK20DECK_HOME", "~/.config/mk20-agent-deck"))

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
    heartbeat_s: float = 2.0
    ack_timeout_s: float = 1.0
    preamble_flush: int = 1024 * 1024
    # setBacklight level sent right after every handshake (first connect, replug, wake).
    # The device comes out of offline mode with backlight 0 (dark keys) — see
    # NOTES-protocol-re-2026-10-17.md "Official app test".  false/"off" disables.
    backlight: int | None = 80
    ptt_fallback_b: bool = False  # PRD §6.5: B only after IME verification; not implemented in P0
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
    if cfg.fw_coord_base == "auto":
        cfg.fw_coord_base = None
    cfg.max_fps = min(float(cfg.max_fps), 8.0)  # PRD §7.4 hard ceiling
    cfg.state_path = os.path.expanduser(cfg.state_path)
    if "skills" in d:
        sk = [Skill(str(s["label"]), str(s["prompt"])) for s in d["skills"][:4]]
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
        patterns=tuple(tm.get("patterns", ("ag-ccai-*", "ag-codex-*"))),
        registered_panes=tuple(tm.get("registered_panes", ())),
        terminal_app=tm.get("terminal_app", ""),
        new_session_cmd=tm.get("new_session_cmd", ""),
    )
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
