"""Original flat-vector mascots (no vendor logos, PRD §7.2 / D7).

ccai  = warm-orange little robot;  codex = black/white terminal block.
Expressions change per state; ``phase`` (0..1) drives small motions.
"""
from __future__ import annotations

import math

from PIL import Image, ImageDraw

from ..model import State, Tool

ORANGE = (242, 140, 56)
ORANGE_DARK = (92, 45, 12)
INK = (18, 18, 20)
PAPER = (246, 246, 242)
GRAY = (150, 154, 160)

EXPRESSION = {
    State.IDLE: "sleep",
    State.THINKING: "think",
    State.RUNNING: "type",
    State.NEEDS_INPUT: "wave",
    State.NEEDS_APPROVAL: "sign",
    State.DONE_UNREAD: "happy",
    State.ERROR: "dizzy",
    State.UNKNOWN: "unknown",
    State.OFFLINE: "off",
    State.UNASSIGNED: "off",
}


def _eyes(d: ImageDraw.ImageDraw, cx: float, cy: float, s: float, expr: str, col, phase: float) -> None:
    dx = s * 0.17
    r = s * 0.055
    for sx in (-1, 1):
        x = cx + sx * dx
        if expr in ("sleep", "off"):
            d.line([(x - r * 1.3, cy), (x + r * 1.3, cy)], fill=col, width=max(2, int(s * 0.035)))
        elif expr == "happy":
            d.arc([x - r * 1.4, cy - r * 1.2, x + r * 1.4, cy + r * 1.6], 200, 340, fill=col,
                  width=max(2, int(s * 0.04)))
        elif expr == "dizzy":
            w = max(2, int(s * 0.035))
            d.line([(x - r, cy - r), (x + r, cy + r)], fill=col, width=w)
            d.line([(x - r, cy + r), (x + r, cy - r)], fill=col, width=w)
        elif expr == "think":
            d.ellipse([x - r, cy - r, x + r, cy + r], fill=col)
            d.ellipse([x + r * 0.1, cy - r * 0.9, x + r * 0.7, cy - r * 0.3], fill=(255, 255, 255))
        else:
            rr = r * (1.15 if expr == "type" else 1.0)
            d.ellipse([x - rr, cy - rr, x + rr, cy + rr], fill=col)


def _mouth(d, cx, cy, s, expr, col, phase):
    w = max(2, int(s * 0.035))
    if expr == "happy":
        d.arc([cx - s * 0.12, cy - s * 0.08, cx + s * 0.12, cy + s * 0.08], 20, 160, fill=col, width=w)
    elif expr == "dizzy":
        pts = [(cx - s * 0.12 + i * s * 0.04, cy + (s * 0.02 if i % 2 else -s * 0.02)) for i in range(7)]
        d.line(pts, fill=col, width=w)
    elif expr in ("wave", "sign"):
        d.ellipse([cx - s * 0.04, cy - s * 0.035, cx + s * 0.04, cy + s * 0.045], fill=col)
    elif expr == "think":
        d.line([(cx - s * 0.03, cy + s * 0.01), (cx + s * 0.08, cy - s * 0.01)], fill=col, width=w)
    elif expr in ("sleep", "off"):
        d.arc([cx - s * 0.05, cy - s * 0.03, cx + s * 0.05, cy + s * 0.03], 20, 160, fill=col, width=w)
    else:
        d.line([(cx - s * 0.07, cy), (cx + s * 0.07, cy)], fill=col, width=w)


def _extras(d, x0, y0, s, expr, accent, phase):
    if expr == "sleep":
        f = 0.5 + 0.5 * math.sin(phase * 2 * math.pi)
        zx, zy = x0 + s * 0.78, y0 + s * (0.16 - 0.04 * f)
        z = s * 0.09
        d.line([(zx, zy), (zx + z, zy), (zx, zy + z), (zx + z, zy + z)], fill=GRAY, width=max(1, int(s * 0.025)))
    elif expr == "wave":
        a = math.sin(phase * 2 * math.pi) * 0.25
        hx, hy = x0 + s * (0.9 + 0.03 * a), y0 + s * (0.42 - 0.05 * a)
        d.line([(x0 + s * 0.8, y0 + s * 0.62), (hx, hy)], fill=accent, width=max(2, int(s * 0.05)))
        d.ellipse([hx - s * 0.06, hy - s * 0.06, hx + s * 0.06, hy + s * 0.06], fill=accent)
    elif expr == "sign":
        bx, by = x0 + s * 0.62, y0 + s * 0.62
        d.rectangle([bx, by, bx + s * 0.32, by + s * 0.24], fill=(255, 214, 102), outline=INK, width=1)
        d.line([(bx + s * 0.16, by + s * 0.04), (bx + s * 0.16, by + s * 0.15)], fill=(200, 40, 40),
               width=max(2, int(s * 0.04)))
        d.ellipse([bx + s * 0.14, by + s * 0.18, bx + s * 0.18, by + s * 0.22], fill=(200, 40, 40))
    elif expr == "type":
        k = int(phase * 3) % 3
        for i in range(3):
            c = accent if i == k else GRAY
            d.rectangle([x0 + s * (0.28 + i * 0.16), y0 + s * 0.9, x0 + s * (0.4 + i * 0.16), y0 + s * 0.97], fill=c)
    elif expr == "unknown":
        from .fonts import font
        f = font(int(s * 0.32), bold=True)
        d.text((x0 + s * 0.8, y0 + s * 0.02), "?", font=f, fill=(230, 230, 235), anchor="ma")


def draw_mascot(img: Image.Image, x0: int, y0: int, s: int, tool: Tool | None, state: State,
                phase: float = 0.0) -> None:
    d = ImageDraw.Draw(img)
    expr = EXPRESSION.get(state, "unknown")
    off = state in (State.OFFLINE,)
    if tool == Tool.CCAI:
        body = GRAY if off else ORANGE
        face = (60, 60, 64) if off else ORANGE_DARK
        # antenna
        d.line([(x0 + s * 0.5, y0 + s * 0.06), (x0 + s * 0.5, y0 + s * 0.2)], fill=body, width=max(2, s // 24))
        d.ellipse([x0 + s * 0.44, y0 + s * 0.01, x0 + s * 0.56, y0 + s * 0.13], fill=body)
        d.rounded_rectangle([x0 + s * 0.12, y0 + s * 0.18, x0 + s * 0.88, y0 + s * 0.86], radius=s * 0.16, fill=body)
        d.rounded_rectangle([x0 + s * 0.2, y0 + s * 0.3, x0 + s * 0.8, y0 + s * 0.74], radius=s * 0.1, fill=face)
        # ears
        d.rectangle([x0 + s * 0.05, y0 + s * 0.42, x0 + s * 0.12, y0 + s * 0.6], fill=body)
        d.rectangle([x0 + s * 0.88, y0 + s * 0.42, x0 + s * 0.95, y0 + s * 0.6], fill=body)
        eye = (255, 236, 210) if not off else (140, 140, 140)
        _eyes(d, x0 + s * 0.5, y0 + s * 0.47, s, expr, eye, phase)
        _mouth(d, x0 + s * 0.5, y0 + s * 0.62, s, expr, eye, phase)
        _extras(d, x0, y0, s, expr, body, phase)
    elif tool == Tool.CODEX:
        paper = (170, 170, 170) if off else PAPER
        d.rounded_rectangle([x0 + s * 0.1, y0 + s * 0.14, x0 + s * 0.9, y0 + s * 0.86], radius=s * 0.2,
                            fill=paper, outline=INK, width=max(2, s // 20))
        # terminal title bar dots
        for i in range(3):
            cx = x0 + s * (0.24 + i * 0.09)
            d.ellipse([cx - s * 0.025, y0 + s * 0.22, cx + s * 0.025, y0 + s * 0.27], fill=INK)
        _eyes(d, x0 + s * 0.5, y0 + s * 0.48, s, expr, INK, phase)
        _mouth(d, x0 + s * 0.5, y0 + s * 0.64, s, expr, INK, phase)
        if expr in ("type",):
            # blinking prompt cursor
            if int(phase * 2) % 2 == 0:
                d.rectangle([x0 + s * 0.62, y0 + s * 0.7, x0 + s * 0.7, y0 + s * 0.76], fill=INK)
        _extras(d, x0, y0, s, expr, (40, 40, 44), phase)
    else:
        col = (90, 94, 100)
        d.ellipse([x0 + s * 0.3, y0 + s * 0.14, x0 + s * 0.7, y0 + s * 0.54], fill=col)
        d.rounded_rectangle([x0 + s * 0.18, y0 + s * 0.56, x0 + s * 0.82, y0 + s * 0.9], radius=s * 0.15, fill=col)
        if expr == "unknown":
            _extras(d, x0, y0, s, expr, col, phase)
