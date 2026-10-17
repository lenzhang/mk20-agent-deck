"""Primitive-drawn icons (no emoji / font glyph dependency)."""
from __future__ import annotations

import math

from PIL import ImageDraw


def check(d: ImageDraw.ImageDraw, cx, cy, s, col, w=None):
    w = w or max(2, int(s * 0.16))
    d.line([(cx - s * 0.4, cy), (cx - s * 0.1, cy + s * 0.32), (cx + s * 0.45, cy - s * 0.35)], fill=col, width=w,
           joint="curve")


def cross(d, cx, cy, s, col, w=None):
    w = w or max(2, int(s * 0.16))
    d.line([(cx - s * 0.35, cy - s * 0.35), (cx + s * 0.35, cy + s * 0.35)], fill=col, width=w)
    d.line([(cx - s * 0.35, cy + s * 0.35), (cx + s * 0.35, cy - s * 0.35)], fill=col, width=w)


def bang(d, cx, cy, s, col):
    w = max(2, int(s * 0.18))
    d.line([(cx, cy - s * 0.4), (cx, cy + s * 0.12)], fill=col, width=w)
    d.ellipse([cx - w * 0.6, cy + s * 0.25, cx + w * 0.6, cy + s * 0.25 + w * 1.2], fill=col)


def question(d, cx, cy, s, col):
    from .fonts import font
    d.text((cx, cy), "?", font=font(int(s * 1.1), bold=True), fill=col, anchor="mm")


def dots(d, cx, cy, s, col, phase=0.0, n=3):
    for i in range(n):
        a = phase * 2 * math.pi + i * 2 * math.pi / n
        r = s * 0.32
        x, y = cx + r * math.cos(a), cy + r * math.sin(a)
        rr = s * (0.12 if i == 0 else 0.09)
        d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=col)


def play(d, cx, cy, s, col):
    d.polygon([(cx - s * 0.3, cy - s * 0.38), (cx + s * 0.4, cy), (cx - s * 0.3, cy + s * 0.38)], fill=col)


def hand(d, cx, cy, s, col):
    d.rounded_rectangle([cx - s * 0.28, cy - s * 0.1, cx + s * 0.28, cy + s * 0.4], radius=s * 0.12, fill=col)
    for i in range(4):
        x = cx - s * 0.24 + i * s * 0.16
        d.rounded_rectangle([x, cy - s * 0.42, x + s * 0.11, cy], radius=s * 0.05, fill=col)


def zz(d, cx, cy, s, col):
    w = max(2, int(s * 0.12))
    z = s * 0.5
    d.line([(cx - z / 2, cy - z / 2), (cx + z / 2, cy - z / 2), (cx - z / 2, cy + z / 2), (cx + z / 2, cy + z / 2)],
           fill=col, width=w)


def nosignal(d, cx, cy, s, col):
    w = max(2, int(s * 0.1))
    for i, r in enumerate((0.45, 0.3)):
        d.arc([cx - s * r, cy - s * r + s * 0.15, cx + s * r, cy + s * r + s * 0.15], 220, 320, fill=col, width=w)
    d.ellipse([cx - s * 0.07, cy + s * 0.1, cx + s * 0.07, cy + s * 0.24], fill=col)
    d.line([(cx - s * 0.4, cy - s * 0.35), (cx + s * 0.4, cy + s * 0.35)], fill=col, width=w)


def mic(d, cx, cy, s, col):
    w = max(2, int(s * 0.08))
    d.rounded_rectangle([cx - s * 0.16, cy - s * 0.45, cx + s * 0.16, cy + s * 0.1], radius=s * 0.16, fill=col)
    d.arc([cx - s * 0.3, cy - s * 0.25, cx + s * 0.3, cy + s * 0.25], 0, 180, fill=col, width=w)
    d.line([(cx, cy + s * 0.25), (cx, cy + s * 0.42)], fill=col, width=w)
    d.line([(cx - s * 0.18, cy + s * 0.42), (cx + s * 0.18, cy + s * 0.42)], fill=col, width=w)


def square(d, cx, cy, s, col):
    d.rounded_rectangle([cx - s * 0.3, cy - s * 0.3, cx + s * 0.3, cy + s * 0.3], radius=s * 0.06, fill=col)


def plus(d, cx, cy, s, col):
    w = max(2, int(s * 0.14))
    d.line([(cx - s * 0.38, cy), (cx + s * 0.38, cy)], fill=col, width=w)
    d.line([(cx, cy - s * 0.38), (cx, cy + s * 0.38)], fill=col, width=w)


def target(d, cx, cy, s, col):
    w = max(2, int(s * 0.08))
    d.ellipse([cx - s * 0.36, cy - s * 0.36, cx + s * 0.36, cy + s * 0.36], outline=col, width=w)
    d.ellipse([cx - s * 0.1, cy - s * 0.1, cx + s * 0.1, cy + s * 0.1], fill=col)
    for a, b in (((0, -0.5), (0, -0.25)), ((0, 0.25), (0, 0.5)), ((-0.5, 0), (-0.25, 0)), ((0.25, 0), (0.5, 0))):
        d.line([(cx + s * a[0], cy + s * a[1]), (cx + s * b[0], cy + s * b[1])], fill=col, width=w)


def send(d, cx, cy, s, col):
    d.polygon([(cx - s * 0.42, cy - s * 0.34), (cx + s * 0.44, cy), (cx - s * 0.42, cy + s * 0.34),
               (cx - s * 0.24, cy)], fill=col)


def window(d, cx, cy, s, col):
    w = max(2, int(s * 0.08))
    d.rounded_rectangle([cx - s * 0.42, cy - s * 0.32, cx + s * 0.42, cy + s * 0.32], radius=s * 0.06, outline=col,
                        width=w)
    d.line([(cx - s * 0.42, cy - s * 0.14), (cx + s * 0.42, cy - s * 0.14)], fill=col, width=w)


def flock(d, cx, cy, s, col):
    """herdr: three little sheep-ish panes."""
    for i, (ox, oy) in enumerate(((-0.24, -0.12), (0.24, -0.12), (0, 0.2))):
        x, y = cx + s * ox, cy + s * oy
        d.ellipse([x - s * 0.17, y - s * 0.13, x + s * 0.17, y + s * 0.13], outline=col, width=max(2, int(s * 0.07)))
        d.ellipse([x + s * 0.1, y - s * 0.06, x + s * 0.2, y + s * 0.04], fill=col)


def split(d, cx, cy, s, col):
    w = max(2, int(s * 0.08))
    d.rectangle([cx - s * 0.42, cy - s * 0.32, cx + s * 0.42, cy + s * 0.32], outline=col, width=w)
    d.line([(cx, cy - s * 0.32), (cx, cy + s * 0.32)], fill=col, width=w)
    d.line([(cx, cy), (cx + s * 0.42, cy)], fill=col, width=w)


def back(d, cx, cy, s, col):
    w = max(2, int(s * 0.1))
    d.arc([cx - s * 0.3, cy - s * 0.3, cx + s * 0.4, cy + s * 0.3], 270, 90, fill=col, width=w)
    d.line([(cx + s * 0.05, cy + s * 0.3), (cx - s * 0.35, cy + s * 0.3)], fill=col, width=w)
    d.polygon([(cx - s * 0.45, cy + s * 0.3), (cx - s * 0.25, cy + s * 0.15), (cx - s * 0.25, cy + s * 0.45)],
              fill=col)


def branch(d, cx, cy, s, col):
    w = max(2, int(s * 0.08))
    d.line([(cx - s * 0.2, cy - s * 0.38), (cx - s * 0.2, cy + s * 0.38)], fill=col, width=w)
    d.arc([cx - s * 0.2, cy - s * 0.3, cx + s * 0.3, cy + s * 0.1], 270, 90, fill=col, width=w)
    for x, y in ((-0.2, -0.38), (-0.2, 0.38), (0.25, -0.3)):
        d.ellipse([cx + s * x - s * 0.09, cy + s * y - s * 0.09, cx + s * x + s * 0.09, cy + s * y + s * 0.09],
                  fill=col)


def bug(d, cx, cy, s, col):
    w = max(2, int(s * 0.07))
    d.ellipse([cx - s * 0.22, cy - s * 0.25, cx + s * 0.22, cy + s * 0.38], fill=col)
    d.ellipse([cx - s * 0.13, cy - s * 0.42, cx + s * 0.13, cy - s * 0.18], fill=col)
    for y in (-0.08, 0.1, 0.28):
        d.line([(cx - s * 0.42, cy + s * y), (cx + s * 0.42, cy + s * y)], fill=col, width=w)


def cycle(d, cx, cy, s, col):
    w = max(2, int(s * 0.1))
    d.arc([cx - s * 0.36, cy - s * 0.36, cx + s * 0.36, cy + s * 0.36], 200, 340, fill=col, width=w)
    d.arc([cx - s * 0.36, cy - s * 0.36, cx + s * 0.36, cy + s * 0.36], 20, 160, fill=col, width=w)
    d.polygon([(cx + s * 0.36, cy - s * 0.02), (cx + s * 0.22, cy - s * 0.2), (cx + s * 0.48, cy - s * 0.2)], fill=col)
    d.polygon([(cx - s * 0.36, cy + s * 0.02), (cx - s * 0.22, cy + s * 0.2), (cx - s * 0.48, cy + s * 0.2)], fill=col)


def doc(d, cx, cy, s, col):
    w = max(2, int(s * 0.07))
    d.polygon([(cx - s * 0.3, cy - s * 0.42), (cx + s * 0.14, cy - s * 0.42), (cx + s * 0.3, cy - s * 0.26),
               (cx + s * 0.3, cy + s * 0.42), (cx - s * 0.3, cy + s * 0.42)], outline=col, width=w)
    for y in (-0.12, 0.06, 0.24):
        d.line([(cx - s * 0.18, cy + s * y), (cx + s * 0.18, cy + s * y)], fill=col, width=w)


def pin(d, x, y, s, col):
    d.ellipse([x, y, x + s * 0.6, y + s * 0.6], fill=col)
    d.polygon([(x + s * 0.12, y + s * 0.45), (x + s * 0.48, y + s * 0.45), (x + s * 0.3, y + s)], fill=col)


SKILL_ICONS = (branch, bug, cycle, doc)
MODE_ICONS = {"codex_desktop": window, "herdr": flock, "tmux": split}
