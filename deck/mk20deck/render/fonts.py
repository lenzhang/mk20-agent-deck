"""Font lookup (macOS first, then Linux) with a cached loader."""
from __future__ import annotations

import functools
import os

from PIL import ImageFont

CJK_REGULAR = (
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
)
CJK_BOLD = (
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
)
MONO = (
    "/System/Library/Fonts/SFNSMono.ttf",
    "/System/Library/Fonts/Menlo.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
)


def _first(paths: tuple[str, ...]) -> str | None:
    for p in paths:
        if os.path.exists(p):
            return p
    return None


@functools.lru_cache(maxsize=64)
def font(size: int, bold: bool = False, mono: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = _first(MONO if mono else (CJK_BOLD if bold else CJK_REGULAR)) or _first(CJK_REGULAR)
    if path is None:
        return ImageFont.load_default()
    return ImageFont.truetype(path, size)


def text_w(draw, text: str, f) -> int:
    l, t, r, b = draw.textbbox((0, 0), text, font=f)
    return r - l


def fit(draw, text: str, f, max_w: int) -> str:
    if text_w(draw, text, f) <= max_w:
        return text
    while text and text_w(draw, text + "…", f) > max_w:
        text = text[:-1]
    return text + "…" if text else ""
