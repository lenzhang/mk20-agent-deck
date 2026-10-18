"""Compose the whole device canvas from a DeckView, with a global fps budget."""
from __future__ import annotations

import io
from dataclasses import dataclass, field

from PIL import Image, ImageDraw

from ..controller import DeckView, KeyView, Role
from ..layout import DeviceLayout
from ..model import State
from .fonts import font
from .keys import key_fps, render_key
from .palette import BG
from .subscreen import render_offline, render_sub

MAX_FPS = 8.0  # PRD §7.4 global ceiling


@dataclass
class Renderer:
    layout: DeviceLayout
    animations: bool = True
    max_fps: float = MAX_FPS
    jpeg_quality: int = 85
    _since: dict[tuple[int, int], tuple[object, float]] = field(default_factory=dict)

    def _age(self, kv: KeyView, t: float) -> float:
        k = (kv.col, kv.row)
        sig = (kv.role, kv.state, kv.title)
        prev = self._since.get(k)
        if prev is None or prev[0] != sig:
            self._since[k] = (sig, t)
            return 0.0
        return t - prev[1]

    def desired_fps(self, view: DeckView, t: float) -> float:
        """Global frame rate = max of per-key wishes, capped (shared budget)."""
        if not self.animations:
            return 0.0
        best = 0.0
        for kv in view.keys:
            best = max(best, key_fps(kv, self._age(kv, t), True))
        return min(best, self.max_fps)

    def compose(self, view: DeckView, t: float, heartbeat_on: bool = True) -> Image.Image:
        lay = self.layout
        if view.settings is not None:
            from .settings import SettingsRenderer
            return SettingsRenderer(view.settings).render(lay)
        img = Image.new("RGB", (lay.width, lay.height), BG)
        for kv in view.keys:
            r = lay.key_rect(kv.col, kv.row)
            if r is None:
                continue
            face = render_key(kv, self._age(kv, t), t, self.animations)
            if face.size != (r.w, r.h):
                face = face.resize((r.w, r.h))
            img.paste(face, (r.x, r.y))
        sr = lay.subscreen
        if sr is not None:
            sub = render_sub(view.sub, t, heartbeat_on)
            if sub.size != (sr.w, sr.h):
                sub = sub.resize((sr.w, sr.h))
            img.paste(sub, (sr.x, sr.y))
        return img

    def compose_offline(self, reason: str, when: str) -> Image.Image:
        lay = self.layout
        img = Image.new("RGB", (lay.width, lay.height), BG)
        d = ImageDraw.Draw(img)
        for r in lay.keys:
            d.rounded_rectangle([r.x + 3, r.y + 3, r.x + r.w - 4, r.y + r.h - 4], radius=14,
                                outline=(70, 74, 80), width=2)
            d.text((r.x + r.w / 2, r.y + r.h / 2), "离线", font=font(16), fill=(90, 94, 100), anchor="mm")
        sr = lay.subscreen
        if sr is not None:
            img.paste(render_offline(reason, when).resize((sr.w, sr.h)), (sr.x, sr.y))
        return img

    def jpeg(self, img: Image.Image) -> bytes:
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=self.jpeg_quality, optimize=False)
        return buf.getvalue()


def device_mockup(canvas: Image.Image, layout: DeviceLayout, scale: float = 1.0, title: str = "") -> Image.Image:
    """Simulator view: draw the canvas regions on a keyboard body with gaps, knobs and labels."""
    gap, pad, top = 14, 28, 40 if title else 20
    keys = layout.keys
    cols = max(k.col for k in keys) + 1
    rows = max(k.row for k in keys) + 1
    kw = keys[0].w
    sub = layout.subscreen
    body_w = pad * 2 + cols * kw + (cols - 1) * gap
    sub_h = sub.h if sub else 0
    body_h = top + pad + sub_h + gap * 2 + rows * kw + (rows - 1) * gap + pad
    img = Image.new("RGB", (body_w, body_h), (34, 36, 40))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([4, 4, body_w - 5, body_h - 5], radius=26, fill=(24, 25, 28), outline=(70, 72, 78), width=2)
    if title:
        d.text((pad, 12), title, font=font(16, bold=True), fill=(220, 222, 226))
    y0 = top + pad - 10
    if sub:
        sx = (body_w - sub.w) // 2
        img.paste(canvas.crop((sub.x, sub.y, sub.x + sub.w, sub.y + sub.h)), (sx, y0))
        d.rectangle([sx - 3, y0 - 3, sx + sub.w + 2, y0 + sub.h + 2], outline=(80, 82, 88), width=2)
        for i, kx in enumerate((sx - 60, sx + sub.w + 60)):
            cy = y0 + sub.h // 2
            d.ellipse([kx - 34, cy - 34, kx + 34, cy + 34], fill=(60, 62, 68), outline=(110, 112, 118), width=3)
            d.text((kx, cy), f"K{i + 1}", font=font(16, bold=True), fill=(200, 200, 205), anchor="mm")
    ky0 = y0 + sub_h + gap * 2
    for k in keys:
        x = pad + k.col * (kw + gap)
        y = ky0 + k.row * (kw + gap)
        d.rounded_rectangle([x - 5, y - 5, x + kw + 4, y + kw + 4], radius=12, fill=(12, 12, 14))
        img.paste(canvas.crop((k.x, k.y, k.x + k.w, k.y + k.h)), (x, y))
    if scale != 1.0:
        img = img.resize((int(body_w * scale), int(body_h * scale)), Image.LANCZOS)
    return img
