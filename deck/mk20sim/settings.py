"""DRAFT on-device settings page ("LS") — clickable mock, state only.

Nothing here changes real network/model configuration; see sim/SETTINGS-DRAFT.md.
Layout (1-based doc col,row):

    row1  [网络] [模型商] [模型] [连接测试] [设备信息]      ← category tabs
    row2  item1  item2  item3  item4  item5                 ← items of the current tab
    row3  item6  item7  item8  item9  item10                  (K2 rotates the focus ring,
    row4  [返回L4] [应用] [撤销] [Mac/网页] [回L1]            K1 adjusts the focused value)
"""
from __future__ import annotations

from dataclasses import dataclass, field

from PIL import Image, ImageDraw

from mk20deck.layout import DeviceLayout
from mk20deck.render.fonts import font

TABS = [("net", "网络"), ("provider", "模型商"), ("model", "模型"), ("test", "连接测试"), ("info", "设备信息")]
PROVIDERS = [  # (id, label, domestic)
    ("qwen", "Qwen 通义", True), ("glm", "GLM 智谱", True), ("kimi", "Kimi", True), ("deepseek", "DeepSeek", True),
    ("openai", "OpenAI", False), ("anthropic", "Anthropic", False), ("gemini", "Gemini", False)]
MODELS = {  # example names only — the real list comes from the provider at runtime
    "qwen": ["qwen3-max", "qwen3-coder-plus", "qwen-vl-max"], "glm": ["glm-4.6", "glm-4.5v"],
    "kimi": ["kimi-k2", "kimi-k2-turbo"], "deepseek": ["deepseek-chat", "deepseek-reasoner"],
    "openai": ["gpt-5", "gpt-5-mini"], "anthropic": ["claude-sonnet-4.5", "claude-haiku-4.5"],
    "gemini": ["gemini-2.5-pro", "gemini-2.5-flash"]}
WEB_HINT = "密钥/Wi-Fi 密码不在设备上输入：请在 Mac 端或网页 http://172.30.250.1:8780 设置"
BG, FG, DIM, ACC, OK, BAD = (14, 16, 20), (235, 235, 240), (120, 125, 135), (80, 170, 255), (90, 210, 120), (240, 90, 90)


@dataclass
class Item:
    id: str
    label: str
    value: str = ""
    chosen: bool = False
    readonly: bool = False
    adjustable: bool = False


@dataclass
class SettingsMock:
    active: bool = False
    tab: str = "net"
    focus: int = 0
    saved: dict = field(default_factory=lambda: {"profile": "mac_proxy", "proxy_port": 7890, "provider": "qwen",
                                                  "model": "qwen3-max"})
    pending: dict = field(default_factory=dict)
    keys_set: dict = field(default_factory=lambda: {"qwen": True, "openai": True})  # mock: which providers have an API key on file
    test_result: str | None = None
    toast: str | None = None
    dirty: bool = True
    armed: bool = False
    exit_to: str | None = None   # "L4" | "L1" after leaving
    log: list = field(default_factory=list)

    # ------------------------------------------------------------- model
    def cfg(self, k: str):
        return self.pending.get(k, self.saved[k])

    def items(self) -> list[Item]:
        c = self.cfg
        if self.tab == "net":
            return [Item("profile:mac_proxy", "Mac 代理", "默认", c("profile") == "mac_proxy"),
                    Item("profile:wifi", "板载 Wi-Fi", "需网卡", c("profile") == "wifi"),
                    Item("proxy_port", "代理端口", str(c("proxy_port")), adjustable=True),
                    Item("wifi_ssid", "Wi-Fi", "Mac/网页设置", readonly=True),
                    Item("split", "国内直连", "自动", readonly=True)]
        if self.tab == "provider":
            return [Item(f"provider:{p}", n, ("国内" if d else "需代理") + ("" if self.keys_set.get(p) else " · 无Key"),
                         c("provider") == p) for p, n, d in PROVIDERS]
        if self.tab == "model":
            return [Item(f"model:{m}", m, "", c("model") == m) for m in MODELS[c("provider")]]
        if self.tab == "test":
            return [Item("run_test", "测试连接", self.test_result or "按下测试")]
        return [Item("i_fw", "固件", "1.1.4", readonly=True), Item("i_kernel", "内核", "5.4.61 #221", readonly=True),
                Item("i_ip", "IP(ECM)", "172.30.250.2", readonly=True), Item("i_gd32", "GD32", "V2.32", readonly=True),
                Item("i_net", "出口", "Mac 代理" if c("profile") == "mac_proxy" else "Wi-Fi", readonly=True)]

    def _say(self, msg: str) -> None:
        self.toast = msg
        self.log.append(msg)

    # ------------------------------------------------------------- input (doc coords / knob actions)
    def open(self) -> None:
        self.active, self.tab, self.focus, self.exit_to, self.dirty = True, "net", 0, None, True
        self._say("进入设置（草案）")

    def key(self, col: int, row: int) -> None:
        self.dirty = True
        self.toast = None
        armed, self.armed = self.armed, False
        if row == 1:
            self.tab, self.focus = TABS[col - 1][0], 0
            return
        if row in (2, 3):
            idx = (row - 2) * 5 + (col - 1)
            if idx < len(self.items()):
                self.focus = idx
                self._choose(self.items()[idx])
            return
        if col == 1:      # back to L4 (discard needs a second press)
            if self.pending and not armed:
                self._say("有未应用的改动，再按一次放弃")
                self.armed = True
                return
            self._leave("L4")
        elif col == 2:
            self.apply()
        elif col == 3:
            self.pending.clear()
            self._say("已撤销未应用的改动")
        elif col == 4:
            self._say(WEB_HINT)
        elif col == 5:
            self.pending.clear()
            self._leave("L1")

    def _leave(self, page: str) -> None:
        self.active, self.exit_to, self.pending = False, page, {}
        self.log.append(f"离开设置 -> {page}")

    def _choose(self, it: Item) -> None:
        if it.readonly:
            self._say(WEB_HINT if it.id == "wifi_ssid" else f"{it.label}: {it.value}（只读）")
            return
        k, _, v = it.id.partition(":")
        if k == "provider":
            self.pending["provider"] = v
            if self.cfg("model") not in MODELS[v]:
                self.pending["model"] = MODELS[v][0]
            if not self.keys_set.get(v):
                self._say(f"{it.label} 尚无 API Key — " + WEB_HINT)
        elif k in ("profile", "model"):
            self.pending[k] = v
        elif k == "proxy_port":
            self._say("旋转 K1 调整端口")
        elif k == "run_test":
            self.run_test()

    def knob(self, k: int, action: str) -> None:
        self.dirty = True
        items = self.items()
        if k == 2 and action in ("cw", "ccw"):
            self.focus = (self.focus + (1 if action == "cw" else -1)) % max(1, len(items))
        elif k == 2 and action == "press":
            self._choose(items[self.focus])
        elif k == 1 and action in ("cw", "ccw"):
            it = items[self.focus] if items else None
            if it and it.id == "proxy_port":
                self.pending["proxy_port"] = max(1, min(65535, self.cfg("proxy_port") + (1 if action == "cw" else -1)))
            else:
                self._say("此项不可调")
        elif k == 1 and action == "press":
            self._say(f"当前：{self.summary()}")

    def apply(self) -> None:
        if not self.pending:
            self._say("没有改动")
            return
        self.saved.update(self.pending)
        self.pending.clear()
        self.test_result = None
        self._say("已应用（SIM：仅状态，不改真实网络）")

    def run_test(self) -> None:
        p = self.cfg("provider")
        dom = next(d for i, _n, d in PROVIDERS if i == p)
        if not self.keys_set.get(p):
            self.test_result = "失败：无 Key"
        elif not dom and self.cfg("profile") == "wifi":
            self.test_result = "失败：需代理"
        else:
            self.test_result = "成功 · 模拟 " + ("180" if dom else "420") + " ms"
        self._say(f"连接测试（模拟）：{self.test_result}")

    def summary(self) -> str:
        prof = "Mac 代理:" + str(self.cfg("proxy_port")) if self.cfg("profile") == "mac_proxy" else "板载 Wi-Fi"
        return f"{prof} · {self.cfg('provider')} / {self.cfg('model')}"

    def state(self) -> dict:
        return {"active": self.active, "tab": self.tab, "focus": self.focus, "saved": dict(self.saved),
                "pending": dict(self.pending), "test": self.test_result, "toast": self.toast}

    # ------------------------------------------------------------- render
    def render(self, lay: DeviceLayout) -> Image.Image:
        img = Image.new("RGB", (lay.width, lay.height), (0, 0, 0))
        items = self.items()
        for r in lay.keys:
            c, w = lay.fw_to_doc(r.col, r.row)
            img.paste(self._key(c, w, items), (r.x, r.y))
        s = lay.subscreen
        img.paste(self._sub(items, s.w, s.h), (s.x, s.y))
        self.dirty = False
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
            return self._face(name, "", chosen=tid == self.tab, color=ACC if tid == self.tab else FG)
        if w in (2, 3):
            idx = (w - 2) * 5 + (c - 1)
            if idx >= len(items):
                return self._face("", "")
            it = items[idx]
            col = OK if it.value.startswith("成功") else BAD if it.value.startswith("失败") else FG
            return self._face(it.label, it.value, hi=idx == self.focus, chosen=it.chosen, dim=it.readonly,
                              color=col if it.id == "run_test" else FG)
        return [self._face("返回", "L4"), self._face("应用", f"{len(self.pending)} 项改动" if self.pending else "",
                                                   color=OK if self.pending else DIM),
                self._face("撤销", ""), self._face("Mac/网页", "填密钥"), self._face("回 L1", "")][c - 1]

    def _sub(self, items: list[Item], w: int, h: int) -> Image.Image:
        im = Image.new("RGB", (w, h), (8, 10, 14))
        d = ImageDraw.Draw(im)
        d.text((10, 8), f"设置 · {dict(TABS)[self.tab]}   (草案 SIM)", font=font(18, bold=True), fill=ACC)
        d.text((10, 38), self.summary() + ("  *未应用" if self.pending else ""), font=font(15),
               fill=(255, 210, 90) if self.pending else FG)
        msg = self.toast or (items[self.focus].label + "：" + (items[self.focus].value or "")) if items else ""
        d.text((10, 64), msg[:34], font=font(14), fill=FG)
        if len(msg) > 34:
            d.text((10, 84), msg[34:68], font=font(14), fill=FG)
        # two encoder halves, like the stock ScreenKey "Encoder 214x142" regions
        d.line((0, h - 30, w, h - 30), fill=(40, 44, 52))
        d.line((w // 2, h - 30, w // 2, h), fill=(40, 44, 52))
        d.text((10, h - 22), "K1 ⟲⟳ 调值 · 按=当前", font=font(13), fill=DIM)
        d.text((w // 2 + 10, h - 22), "K2 ⟲⟳ 选项 · 按=确认", font=font(13), fill=DIM)
        return im
