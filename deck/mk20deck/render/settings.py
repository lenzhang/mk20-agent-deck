"""Renderer for the settings page (keys + sub-screen with K1/K2 encoder halves)."""
from __future__ import annotations

from PIL import Image, ImageDraw

from ..layout import DeviceLayout
from ..settings import TABS, Item, SettingsPage
from .fonts import font

BG, FG, DIM, ACC, OK, BAD = (14, 16, 20), (235, 235, 240), (120, 125, 135), (80, 170, 255), (90, 210, 120), (240, 90, 90)


class SettingsRenderer:
    def __init__(self, page: SettingsPage) -> None:
        self.p = page

    def render(self, lay: DeviceLayout) -> Image.Image:
        img = Image.new("RGB", (lay.width, lay.height), (0, 0, 0))
        items = self.p.items()
        for r in lay.keys:
            c, w = lay.fw_to_doc(r.col, r.row)
            img.paste(self._key(c, w, items), (r.x, r.y))
        s = lay.subscreen
        img.paste(self._sub(items, s.w, s.h), (s.x, s.y))
        self.p.dirty = False
        return img

    def _face(self, title: str, sub: str = "", *, hi=False, chosen=False, dim=False, color=FG) -> Image.Image:
        im = Image.new("RGB", (128, 128), BG)
        d = ImageDraw.Draw(im)
        if chosen:
            d.rounded_rectangle((4, 4, 123, 123), 14, fill=(22, 48, 80))
        if hi:
            d.rounded_rectangle((3, 3, 124, 124), 14, outline=ACC, width=4)
        d.text((64, 52), title, font=font(22 if len(title) <= 5 else 17, bold=True), fill=DIM if dim else color,
               anchor="mm")
        if sub:
            d.text((64, 90), sub, font=font(15), fill=OK if chosen else DIM, anchor="mm")
        if chosen:
            d.text((112, 16), "✓", font=font(18, bold=True), fill=OK, anchor="mm")
        return im

    def _key(self, c: int, w: int, items: list[Item]) -> Image.Image:
        if w == 1:
            tid, name = TABS[c - 1]
            return self._face(name, "", chosen=tid == self.p.tab, color=ACC if tid == self.p.tab else FG)
        if w in (2, 3):
            idx = (w - 2) * 5 + (c - 1)
            if idx >= len(items):
                return self._face("", "")
            it = items[idx]
            col = OK if it.value.startswith("成功") else BAD if it.value.startswith("失败") else FG
            return self._face(it.label, it.value, hi=idx == self.p.focus, chosen=it.chosen, dim=it.readonly,
                              color=col if it.id == "run_test" else FG)
        return [self._face("返回", "L4"), self._face("应用", "再按确认" if self.p.confirm_net else (f"{len(self.p.pending)} 项改动" if self.p.pending else ""),
                                                   color=OK if self.p.pending else DIM),
                self._face("撤销", ""), self._face("Mac/网页", "填密钥"), self._face("回 L1", "")][c - 1]

    def _sub(self, items: list[Item], w: int, h: int) -> Image.Image:
        im = Image.new("RGB", (w, h), (8, 10, 14))
        d = ImageDraw.Draw(im)
        d.text((10, 8), f"设置 · {dict(TABS)[self.p.tab]}", font=font(18, bold=True), fill=ACC)
        d.text((10, 38), self.p.summary() + ("  *未应用" if self.p.pending else ""), font=font(15),
               fill=(255, 210, 90) if self.p.pending else FG)
        msg = self.p.toast or (items[self.p.focus].label + "：" + (items[self.p.focus].value or "")) if items else ""
        d.text((10, 64), msg[:34], font=font(14), fill=FG)
        if len(msg) > 34:
            d.text((10, 84), msg[34:68], font=font(14), fill=FG)
        # two encoder halves, like the stock ScreenKey "Encoder 214x142" regions
        d.line((0, h - 30, w, h - 30), fill=(40, 44, 52))
        d.line((w // 2, h - 30, w // 2, h), fill=(40, 44, 52))
        d.text((10, h - 22), "K1 ⟲⟳ 调值 · 按=当前", font=font(13), fill=DIM)
        d.text((w // 2 + 10, h - 22), "K2 ⟲⟳ 选项 · 按=确认", font=font(13), fill=DIM)
        return im
