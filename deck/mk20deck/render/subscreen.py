"""428x142 sub-screen (PRD §7.5)."""
from __future__ import annotations

from PIL import Image, ImageDraw

from ..controller import SubView
from ..model import State
from .fonts import fit, font
from .mascot import draw_mascot
from .palette import BG, DIM, MODE_NAME, MODE_STRIP, STATE_COLOR, STATE_TEXT, TEXT

W, H = 428, 142
AMBER = (245, 180, 60)


def render_sub(sv: SubView, t: float, heartbeat_on: bool = True) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 5, H], fill=MODE_STRIP[sv.mode])  # mode colour only as a thin strip (G8)
    f12, f14 = font(12), font(14)
    head = f"{sv.page} · {sv.mode.badge}"
    fh = font(14, bold=True)
    d.text((12, 4), head, font=fh, fill=TEXT)
    right = f"更新 {sv.updated}"
    if sv.sim:
        right = "SIM · " + right
    rw = d.textbbox((0, 0), right, font=f12)[2]
    d.text((W - 10 - rw, 6), right, font=f12, fill=DIM)
    if sv.other_modes:  # cross-mode attention (background poll), e.g. "tmux 2 待处理"
        x0 = 12 + d.textbbox((0, 0), head, font=fh)[2] + 8
        room = W - 10 - rw - 22 - x0
        if room > 40:
            txt = fit(d, sv.other_modes, font(12, bold=True), room - 12)
            tw = d.textbbox((0, 0), txt, font=font(12, bold=True))[2]
            d.rounded_rectangle([x0, 4, x0 + tw + 12, 21], radius=8, fill=AMBER)
            d.text((x0 + 6, 12), txt, font=font(12, bold=True), fill=(0, 0, 0), anchor="lm")
    if heartbeat_on:
        d.ellipse([W - 18 - rw, 9, W - 13 - rw, 14], fill=(34, 197, 94) if sv.mode_ok else (120, 120, 120))
    if sv.ptt:
        d.rounded_rectangle([150, 3, 240, 21], radius=8, fill=(32, 178, 170))
        d.text((195, 12), "PTT 按住", font=font(12, bold=True), fill=(0, 0, 0), anchor="mm")

    s = sv.session
    x = 110
    if s is not None:
        draw_mascot(img, 12, 32, 86, s.tool, s.state, 0.0)
        d.text((x, 26), fit(d, s.title, font(19, bold=True), W - x - 10), font=font(19, bold=True), fill=TEXT)
        col = STATE_COLOR[s.state]
        d.ellipse([x, 57, x + 10, 67], fill=col)
        eff = s.effort or "?"
        line2 = f"{s.tool.value} · {STATE_TEXT[s.state]} · effort {eff}"
        d.text((x + 16, 53), fit(d, line2, f14, W - x - 26), font=f14, fill=TEXT)
        detail = s.state_reason or ""
        if s.source_note:
            detail = (detail + " · " if detail else "") + s.source_note
        d.text((x, 74), fit(d, detail, f12, W - x - 10), font=f12, fill=DIM)
        summ = s.summary or ""
        if sv.scroll and summ:
            summ = summ[sv.scroll * 12:]
        d.text((x, 92), fit(d, summ, f12, W - x - 10), font=f12, fill=(190, 194, 200))
    else:
        d.text((x - 90, 40), "未选中", font=font(24, bold=True), fill=TEXT)
        hint = "选中项已消失，请重新选择" if sv.selection_lost else "单击会话键：选中并置前 · 长按钉扎 · 转 K1 挑待处理"
        d.text((x - 90, 74), fit(d, hint, f12, W - 30), font=f12, fill=DIM)
        line2 = sv.pin_info if sv.page == "L4" else (sv.note or "")
        if line2:
            d.text((x - 90, 92), fit(d, line2, f12, W - 30), font=f12, fill=DIM)

    # bottom: status-limited reason (AC11) + overflow + note
    yb = 120
    lim = sv.limited or ""
    over = f"+{sv.overflow}" if sv.overflow else ""
    if sv.overflow_attention:
        over += f" · {sv.overflow_attention} 待处理"
    ow = d.textbbox((0, 0), over, font=font(13, bold=True))[2] if over else 0
    d.text((12, yb), fit(d, lim, f12, W - 30 - ow), font=f12, fill=AMBER)
    if over:
        d.text((W - 10 - ow, yb - 1), over, font=font(13, bold=True), fill=TEXT)
    if sv.note and s is not None and sv.page != "L4":
        d.text((x, 106), fit(d, sv.note, font(11), W - x - 10), font=font(11), fill=(150, 160, 190))
    if sv.page == "L4" and s is not None and sv.pin_info:
        d.text((x, 106), fit(d, sv.pin_info, font(11), W - x - 10), font=font(11), fill=(150, 160, 190))

    if sv.toast:
        f = font(16, bold=True)
        tw = min(W - 20, d.textbbox((0, 0), sv.toast, font=f)[2] + 24)
        x0 = (W - tw) // 2
        d.rounded_rectangle([x0, 46, x0 + tw, 82], radius=10, fill=(245, 245, 245), outline=(0, 0, 0), width=2)
        d.text((W / 2, 64), fit(d, sv.toast, f, tw - 16), font=f, fill=(10, 10, 10), anchor="mm")
    return img


def render_offline(reason: str, when: str) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.text((W / 2, 52), "MK20 Deck 离线", font=font(24, bold=True), fill=TEXT, anchor="mm")
    d.text((W / 2, 88), reason, font=font(14), fill=DIM, anchor="mm")
    d.text((W / 2, 114), f"最后更新 {when}", font=font(12), fill=DIM, anchor="mm")
    return img
