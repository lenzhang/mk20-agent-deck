"""128x128 key faces.  Every base state has a static single-frame form
(colour + icon + short text + mascot, PRD §7.1/G22); animation only enhances."""
from __future__ import annotations

import functools
import math

from PIL import Image, ImageDraw

from ..controller import KeyView, Role
from ..model import ATTENTION_STATES, State
from . import icons
from .fonts import fit, font
from .mascot import draw_mascot
from .palette import (BLACK, CMD_BG, DIM, KEY_BG, RED, SEA_GREEN, STATE_COLOR, STATE_TEXT, TEXT, WHITE, mix)

S = 128
OK_GREEN = (74, 222, 128)   # 发送 / 可以 (Return) glyph
NO_RED = (248, 113, 113)    # 不行 Esc glyph

# Expected fps caps (PRD §7.3) — shared global budget, never per-key guarantees.
FPS = {
    # Only "needs me" states move (user 2026-10-22: blinking idle sessions read as "needs me"). Idle is static;
    # running/thinking keep a small progress bar / dots, never a pulsing border or a moving face.
    State.IDLE: 0, State.THINKING: 6, State.RUNNING: 6, State.NEEDS_INPUT: 4, State.NEEDS_APPROVAL: 4,
    State.DONE_UNREAD: 8, State.ERROR: 8,
}
DONE_POP_S = 1.0   # done_unread: 8 fps pop, then 2 fps glow
ERROR_SHAKE_S = 1.5  # error: 8 fps shake, then static
PTT_FPS = 8
MOVE_FPS = 4  # picked-up slot: marching dashed border (static dashes when animations are off)
MOVE_PINK = (244, 114, 182)  # distinct from every state colour


def key_fps(kv: KeyView, age: float, animations: bool) -> float:
    """Desired fps for one key given time since its state began."""
    if not animations:
        return 0
    if kv.role == Role.PTT:
        return PTT_FPS if kv.active else 0
    if kv.role != Role.AGENT or kv.state is None:
        return 0
    base = _state_fps(kv, age)
    return max(base, MOVE_FPS) if kv.moving else base


def _state_fps(kv: KeyView, age: float) -> float:
    st = kv.state
    if st in ATTENTION_STATES and not kv.attention:
        return 0  # only one attention animation at a time (G26)
    if st == State.DONE_UNREAD:
        return 8 if age < DONE_POP_S else 2
    if st == State.ERROR:
        return 8 if age < ERROR_SHAKE_S else 0
    return FPS.get(st, 0)


@functools.lru_cache(maxsize=4)
def _rrect_points(x0: int, y0: int, x1: int, y1: int, r: int) -> tuple[tuple[float, float], ...]:
    """Points ~1 px apart along a rounded rectangle (clockwise from the top-left straight edge)."""
    pts: list[tuple[float, float]] = []

    def seg(ax, ay, bx, by):
        n = max(1, int(math.hypot(bx - ax, by - ay)))
        pts.extend((ax + (bx - ax) * i / n, ay + (by - ay) * i / n) for i in range(n))

    def arc(cx, cy, a0):
        n = max(1, int(r * math.pi / 2))
        pts.extend((cx + r * math.cos(a0 + math.pi / 2 * i / n), cy + r * math.sin(a0 + math.pi / 2 * i / n))
                   for i in range(n))

    seg(x0 + r, y0, x1 - r, y0)
    arc(x1 - r, y0 + r, -math.pi / 2)
    seg(x1, y0 + r, x1, y1 - r)
    arc(x1 - r, y1 - r, 0.0)
    seg(x1 - r, y1, x0 + r, y1)
    arc(x0 + r, y1 - r, math.pi / 2)
    seg(x0, y1 - r, x0, y0 + r)
    arc(x0 + r, y0 + r, math.pi)
    return tuple(pts)


def _moving_overlay(d: ImageDraw.ImageDraw, t: float, animate: bool, chip_y: int) -> None:
    """Picked-up slot: pink dashed border (marches at MOVE_FPS) + 「移动中」 chip instead of the state chip."""
    d.rounded_rectangle([0, 0, S - 1, S - 1], radius=16, outline=BLACK, width=7)
    dash, gap = 12, 8
    off = (int(t * MOVE_FPS) * 4) % (dash + gap) if animate else 0
    pts = _rrect_points(3, 3, S - 4, S - 4, 13)
    run: list[tuple[float, float]] = []
    for i, pt in enumerate(pts + pts[:1]):
        if (i + (dash + gap) - off) % (dash + gap) < dash:
            run.append(pt)
        elif run:
            if len(run) > 1:
                d.line(run, fill=MOVE_PINK, width=5)
            run = []
    if len(run) > 1:
        d.line(run, fill=MOVE_PINK, width=5)
    f = font(14, bold=True)
    tw = d.textbbox((0, 0), "移动中", font=f)[2] + 16
    d.rounded_rectangle([S / 2 - tw / 2, chip_y, S / 2 + tw / 2, chip_y + 18], radius=9, fill=MOVE_PINK,
                        outline=BLACK, width=1)
    d.text((S / 2, chip_y + 9), "移动中", font=f, fill=BLACK, anchor="mm")


def _state_badge(d: ImageDraw.ImageDraw, st: State, cx: float, cy: float, r: float, phase: float, animate: bool):
    col = STATE_COLOR[st]
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col, outline=BLACK, width=2)
    ink = BLACK if st in (State.IDLE, State.NEEDS_INPUT, State.NEEDS_APPROVAL, State.DONE_UNREAD,
                          State.RUNNING) else WHITE
    s = r * 1.3
    if st == State.IDLE:
        icons.zz(d, cx, cy, s, ink)
    elif st == State.THINKING:
        icons.dots(d, cx, cy, s, ink, phase if animate else 0.0)
    elif st == State.RUNNING:
        icons.play(d, cx, cy, s, ink)
    elif st == State.NEEDS_INPUT:
        icons.hand(d, cx, cy + r * 0.1, s * 0.9, ink)
    elif st == State.NEEDS_APPROVAL:
        icons.bang(d, cx, cy, s, ink)
    elif st == State.DONE_UNREAD:
        icons.check(d, cx, cy, s, ink)
    elif st == State.ERROR:
        icons.bang(d, cx, cy, s, ink)
    elif st == State.OFFLINE:
        icons.nosignal(d, cx, cy, s, WHITE)
    elif st == State.UNKNOWN:
        icons.question(d, cx, cy, s, WHITE)


def render_agent(kv: KeyView, age: float, t: float, animations: bool) -> Image.Image:
    st = kv.state or State.UNKNOWN
    col = STATE_COLOR[st]
    # State animation only; a picked-up slot's marching border (MOVE_FPS) never animates the state itself.
    animate = animations and _state_fps(kv, age) > 0
    phase = (t * 1.0) % 1.0 if animate else 0.0
    bg = KEY_BG
    if st == State.UNKNOWN:
        bg = (58, 63, 71)  # 静态灰底
    elif st in (State.NEEDS_INPUT, State.NEEDS_APPROVAL, State.ERROR, State.DONE_UNREAD):
        bg = mix(KEY_BG, col, 0.18)
    img = Image.new("RGB", (S, S), bg)
    d = ImageDraw.Draw(img)

    if st == State.UNASSIGNED and not kv.lost:
        d.rounded_rectangle([3, 3, S - 4, S - 4], radius=14, outline=col, width=3)
        d.line([(44, 64), (84, 64)], fill=(90, 94, 100), width=5)
        d.text((S / 2, 104), kv.label or "空槽", font=font(15), fill=(110, 114, 120), anchor="mm")
        return img
    if st == State.OFFLINE and not kv.title:
        d.rounded_rectangle([3, 3, S - 4, S - 4], radius=14, outline=col, width=3)
        icons.nosignal(d, S / 2, 52, 50, col)
        d.text((S / 2, 104), kv.label or "离线", font=font(15, bold=True), fill=col, anchor="mm")
        return img
    if kv.lost:
        d.rounded_rectangle([3, 3, S - 4, S - 4], radius=14, outline=(150, 120, 60), width=2)
        icons.pin(d, 50, 28, 40, (200, 160, 80))
        d.text((S / 2, 88 if kv.moving else 92), "失联钉扎", font=font(17, bold=True), fill=(220, 190, 120),
               anchor="mm")
        if kv.moving:
            _moving_overlay(d, t, animations, 102)
        else:
            d.text((S / 2, 113), "长按后点自己解除", font=font(12), fill=DIM, anchor="mm")
        return img

    # --- border (state colour) + animation ----------------------------------
    bw = 5
    bcol = col
    if animate and st == State.IDLE:
        bcol = mix(col, KEY_BG, 0.45 * (0.5 + 0.5 * math.sin(phase * 2 * math.pi)))
    if animate and st == State.NEEDS_INPUT:
        bw = 5 + int(3 * (0.5 + 0.5 * math.sin(phase * 2 * math.pi)))
    ox = 0
    if animate and st == State.ERROR:
        ox = int(3 * math.sin(t * 2 * math.pi * 4))
    inset = 0
    if kv.selected:
        # G23: static double light/dark border, outside the state ring
        d.rounded_rectangle([0, 0, S - 1, S - 1], radius=16, outline=WHITE, width=3)
        d.rounded_rectangle([3, 3, S - 4, S - 4], radius=14, outline=BLACK, width=2)
        inset = 5
    d.rounded_rectangle([inset + ox, inset, S - 1 - inset + ox, S - 1 - inset], radius=14 - inset // 2,
                        outline=bcol, width=bw)

    # --- mascot -------------------------------------------------------------
    ms = 58
    draw_mascot(img, (S - ms) // 2 - 10 + ox, 8 + inset // 2, ms, kv.tool, st, phase if st in ATTENTION_STATES else 0.0)

    # --- state badge (top-right) ---------------------------------------------
    br = 15
    _state_badge(d, st, S - 12 - br - inset // 2 + ox, 12 + br + inset // 2, br, phase, animate)
    if st == State.NEEDS_APPROVAL:
        on = (not animate) or int(t * 4) % 2 == 0
        if on:
            d.ellipse([S - 30 - inset, 4 + inset, S - 14 - inset, 20 + inset], fill=RED, outline=WHITE, width=2)

    # --- done pop / glow, error glitch, running bar ---------------------------
    if animate and st == State.DONE_UNREAD and age < DONE_POP_S:
        k = min(1.0, age / DONE_POP_S)
        icons.check(d, S / 2 - 10, 44, 20 + 26 * (1 - abs(1 - 2 * k)), (34, 197, 94), 5)
    if animate and st == State.ERROR:
        for i in range(3):
            y = int((t * 97 + i * 37) % 70) + 10
            d.line([(10, y), (S - 10, y)], fill=mix(RED, WHITE, 0.3), width=1)
    if st == State.RUNNING:
        y = S - inset - 10
        d.line([(16, y), (S - 16, y)], fill=mix(col, KEY_BG, 0.6), width=4)
        pos = (phase if animate else 0.3)
        x = 16 + pos * (S - 52)
        d.line([(x, y), (x + 20, y)], fill=col, width=4)

    # --- pin glyph (top-left) -----------------------------------------------
    if kv.pinned:
        icons.pin(d, 9 + inset, 9 + inset, 16, WHITE)

    # --- name plate + status chip -------------------------------------------
    f_name = font(16, bold=True)
    name = fit(d, (kv.title or "?")[:6], f_name, S - 20 - 2 * inset)
    ny = 72
    if kv.selected:
        d.rounded_rectangle([10, ny - 1, S - 11, ny + 19], radius=5, fill=WHITE)  # 反色名牌
        d.text((S / 2, ny + 9), name, font=f_name, fill=BLACK, anchor="mm")
    else:
        d.text((S / 2, ny + 9), name, font=f_name, fill=TEXT, anchor="mm")
    chip_y = 96
    f_st = font(14, bold=True)
    label = STATE_TEXT[st]
    tw = d.textbbox((0, 0), label, font=f_st)[2] + 14
    cx = S / 2
    chip_fg = BLACK if st not in (State.ERROR, State.THINKING, State.OFFLINE, State.UNKNOWN) else WHITE
    if kv.moving:
        _moving_overlay(d, t, animations, chip_y)  # the state stays visible in the top-right badge
        return img
    d.rounded_rectangle([cx - tw / 2, chip_y, cx + tw / 2, chip_y + 18], radius=9, fill=col)
    d.text((cx, chip_y + 9), label, font=f_st, fill=chip_fg, anchor="mm")
    return img


def render_command(kv: KeyView, t: float, animations: bool) -> Image.Image:
    img = Image.new("RGB", (S, S), CMD_BG)
    d = ImageDraw.Draw(img)
    disabled = kv.disabled is not None
    fg = (100, 104, 112) if disabled else TEXT
    r = kv.role
    cy = 50
    if r == Role.PTT:
        if kv.active:
            img.paste(mix(CMD_BG, SEA_GREEN, 0.55), [0, 0, S, S])
            d = ImageDraw.Draw(img)
            ph = t * 8 if animations else 0
            for i in range(9):
                h = 8 + 26 * abs(math.sin(ph + i * 0.7))
                x = 16 + i * 11
                d.line([(x, cy - h / 2), (x, cy + h / 2)], fill=WHITE, width=5)
            d.text((S / 2, 96), "PTT 按住", font=font(16, bold=True), fill=WHITE, anchor="mm")
            d.text((S / 2, 115), "右 Option", font=font(12), fill=WHITE, anchor="mm")
            return img
        icons.mic(d, S / 2, cy, 54, fg)
    elif r == Role.DELETE:
        icons.backspace(d, S / 2, cy - 2, 56, fg)
    elif r == Role.OK:
        icons.enter(d, S / 2, cy - 2, 54, OK_GREEN)
    elif r == Role.NO:
        icons.cross(d, S / 2, cy - 2, 44, NO_RED)
    elif r == Role.NEW:
        icons.plus(d, S / 2, cy, 50, fg)
    elif r in (Role.MODE, Role.MODE_SELECT):
        icons.MODE_ICONS[kv.mode.value](d, S / 2, cy - 2, 56, fg)
    elif r == Role.BACK_L1:
        icons.back(d, S / 2, cy, 50, fg)

    label = kv.label
    f = font(20 if r in (Role.MODE, Role.MODE_SELECT) else 17, bold=True)
    d.text((S / 2, 96), fit(d, label, f, S - 12), font=f, fill=fg, anchor="mm")
    if kv.sublabel and not disabled:  # device-HID keys: what the keyboard itself sends
        d.text((S / 2, 117), fit(d, kv.sublabel, font(11), S - 10), font=font(11), fill=DIM, anchor="mm")
    if r == Role.MODE_SELECT and kv.active:
        d.rounded_rectangle([2, 2, S - 3, S - 3], radius=14, outline=WHITE, width=4)
    if disabled:
        reason = kv.disabled or ""
        short = _short_reason(reason)
        d.text((S / 2, 117), fit(d, short, font(11), S - 10), font=font(11), fill=(150, 130, 90), anchor="mm")
    if kv.badge:
        f2 = font(13, bold=True)
        w = max(20, d.textbbox((0, 0), kv.badge, font=f2)[2] + 10)
        fill, ink = ((245, 158, 11), BLACK) if kv.badge_alert else ((90, 96, 110), WHITE)
        d.rounded_rectangle([S - w - 4, 4, S - 4, 22], radius=8, fill=fill)
        d.text((S - 4 - w / 2, 13), kv.badge, font=f2, fill=ink, anchor="mm")
    return img


def _short_reason(reason: str) -> str:
    rules = (("App 内", "请在 App 内处理"), ("终端内", "请在终端内处理"), ("herdr 内", "请在 herdr 内处理"),
             ("辅助功能", "需辅助功能 · P0 未启用"), ("中断", "P0 未支持"), ("终端 App", "未绑定终端 App"),
             ("新会话命令", "未配置命令"), ("投递语义", "P0 未验证"), ("未联调", "herdr 未联调"),
             ("未安装", "未安装"), ("不支持", "此会话不支持"))
    for k, v in rules:
        if k in reason:
            return v
    return reason.split("，")[0].split("（")[0][:10]


def render_key(kv: KeyView, age: float, t: float, animations: bool = True) -> Image.Image:
    if kv.role == Role.AGENT:
        return render_agent(kv, age, t, animations)
    return render_command(kv, t, animations)
