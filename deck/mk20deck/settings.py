"""On-device settings page ("LS") — state model, persistence and apply interface.

Decided design: sim/SETTINGS.md.  Entry: short K1 press on L4.  Persistence goes through
``SettingsStore`` (JSON file; device: /data/mk20/settings.json, Mac: ~/.config/mk20-agent-deck,
sim/tests: a temp path).  ``apply`` hands the changed keys to a ``SettingsApplier``; the default
``LoggingApplier`` only logs — no real network/model change is made yet.
Layout (1-based doc col,row):

    row1  [网络] [模型商] [模型] [连接测试] [设备信息]      ← category tabs
    row2  item1  item2  item3  item4  item5                 ← items of the current tab
    row3  item6  item7  item8  item9  item10                  (K2 rotates the focus ring,
    row4  [返回L4] [应用] [撤销] [Mac/网页] [回L1]            K1 adjusts the focused value)
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from typing import Protocol

from .catalogue import CATALOGUE

log = logging.getLogger(__name__)

TABS = [("net", "网络"), ("provider", "模型商"), ("model", "模型"), ("test", "连接测试"), ("info", "设备信息")]
# Preserve legacy key positions for the optional Python page; C uses catalogue order.
PROVIDERS = [(p["id"], p["label"], p["domestic"])
             for p in sorted(CATALOGUE, key=lambda p: p["legacy_order"])]
MODELS = {p["id"]: [m["id"] for m in p["models"]] for p in CATALOGUE}
WEB_HINT = "密钥/Wi-Fi 密码不在设备上输入：请在 Mac 端或网页 http://172.30.250.1:8780 设置"
DEFAULTS = {"profile": "mac_proxy", "proxy_port": 7890, "provider": "qwen", "model": "qwen3-max",
            "brightness": 80, "volume": 50, "wifi_proxy": ""}
DEVICE_PATH = "/data/mk20/settings.json"
NET_KEYS = ("profile", "proxy_port")


def default_settings_path() -> str:
    if os.environ.get("MK20_SETTINGS"):
        return os.environ["MK20_SETTINGS"]
    if os.path.isdir("/data/mk20"):
        return DEVICE_PATH
    return os.path.join(os.path.expanduser("~"), ".config", "mk20-agent-deck", "settings.json")


class SettingsStore:
    """Atomic JSON persistence; unknown keys ignored, bad files fall back to defaults."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path or default_settings_path()

    @classmethod
    def temp(cls) -> "SettingsStore":
        fd, p = tempfile.mkstemp(prefix="mk20-settings-", suffix=".json")
        os.close(fd)
        os.unlink(p)
        return cls(p)

    def load(self) -> dict:
        out = dict(DEFAULTS)
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            out.update({k: d[k] for k in DEFAULTS if k in d and type(d[k]) is type(DEFAULTS[k])})
        except (OSError, ValueError):
            pass
        if out["profile"] not in ("mac_proxy", "board_wifi"):
            out["profile"] = DEFAULTS["profile"]
        if out["provider"] not in MODELS:
            out["provider"], out["model"] = DEFAULTS["provider"], DEFAULTS["model"]
        return out

    def save(self, cfg: dict) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({k: cfg[k] for k in DEFAULTS}, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)


class SettingsApplier(Protocol):
    def apply(self, changes: dict, full: dict) -> tuple[bool, str]: ...


@dataclass
class LoggingApplier:
    """Stub: records what would be applied (network profile, provider…); changes nothing."""
    calls: list = field(default_factory=list)

    def apply(self, changes: dict, full: dict) -> tuple[bool, str]:
        self.calls.append(dict(changes))
        log.info("settings apply (stub, no real change): %s", changes)
        return True, "已应用（仅保存配置，网络/模型切换待实现）"


@dataclass
class Item:
    id: str
    label: str
    value: str = ""
    chosen: bool = False
    readonly: bool = False
    adjustable: bool = False


@dataclass
class SettingsPage:
    store: SettingsStore = field(default_factory=SettingsStore.temp)
    applier: SettingsApplier = field(default_factory=LoggingApplier)
    active: bool = False
    tab: str = "net"
    focus: int = 0
    saved: dict = field(default_factory=dict)
    pending: dict = field(default_factory=dict)
    confirm_net: bool = False
    keys_set: dict = field(default_factory=lambda: {"qwen": True, "openai": True})  # mock: which providers have an API key on file
    test_result: str | None = None
    toast: str | None = None
    dirty: bool = True
    armed: bool = False
    exit_to: str | None = None   # "L4" | "L1" after leaving
    log: list = field(default_factory=list)
    version: int = 0

    def __post_init__(self) -> None:
        self.saved = self.store.load()
        self.refresh_keys()

    def refresh_keys(self) -> None:
        hk = getattr(self.applier, "has_key", None)
        if hk:
            self.keys_set = {p: hk(p) for p, _n, _d in PROVIDERS}

    # ------------------------------------------------------------- model
    def cfg(self, k: str):
        return self.pending.get(k, self.saved[k])

    def items(self) -> list[Item]:
        c = self.cfg
        if self.tab == "net":
            return [Item("profile:mac_proxy", "Mac 代理", "默认", c("profile") == "mac_proxy"),
                    Item("profile:board_wifi", "板载 Wi-Fi", "需网卡", c("profile") == "board_wifi"),
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
        return [Item("brightness", "亮度", f"{c('brightness')}%", adjustable=True),
                Item("volume", "音量", f"{c('volume')}%", adjustable=True),
                Item("i_fw", "固件", "1.1.4", readonly=True), Item("i_kernel", "内核", "5.4.61 #221", readonly=True),
                Item("i_ip", "IP(ECM)", "172.30.250.2", readonly=True), Item("i_gd32", "GD32", "V2.32", readonly=True),
                Item("i_net", "出口", "Mac 代理" if c("profile") == "mac_proxy" else "Wi-Fi", readonly=True)]

    def _say(self, msg: str) -> None:
        self.toast = msg
        self.log.append(msg)

    # ------------------------------------------------------------- input (doc coords / knob actions)
    def open(self) -> None:
        self.refresh_keys()
        self.active, self.tab, self.focus, self.exit_to, self.dirty = True, "net", 0, None, True
        self._say("进入设置")

    def key(self, col: int, row: int) -> None:
        self.dirty = True
        self.toast = None
        armed, self.armed = self.armed, False
        if col != 2 or row != 4:
            self.confirm_net = False
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
            return
        elif col == 3:
            self.pending.clear()
            self._say("已撤销未应用的改动")
        elif col == 4:
            self._say(WEB_HINT)
        elif col == 5:
            self.pending.clear()
            self._leave("L1")

    def _leave(self, page: str) -> None:
        self.active, self.exit_to, self.pending, self.confirm_net = False, page, {}, False
        self.log.append(f"离开设置 -> {page}")

    def _choose(self, it: Item) -> None:
        if it.readonly:
            self._say(WEB_HINT if it.id == "wifi_ssid" else f"{it.label}: {it.value}（只读）")
            return
        k, _, v = it.id.partition(":")
        if k == "provider":
            self._stage("provider", v)
            if self.cfg("model") not in MODELS[v]:
                self._stage("model", MODELS[v][0])
            if not self.keys_set.get(v):
                self._say(f"{it.label} 尚无 API Key — " + WEB_HINT)
        elif k in ("profile", "model"):
            self._stage(k, v)
        elif it.adjustable:
            self._say(f"旋转 K1 调整{it.label}")
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
            sign = 1 if action == "cw" else -1
            if it and it.id == "proxy_port":
                self._stage("proxy_port", max(1, min(65535, self.cfg("proxy_port") + sign)))
            elif it and it.id in ("brightness", "volume"):
                self._stage(it.id, max(0 if it.id == "volume" else 10, min(100, self.cfg(it.id) + 10 * sign)))
            else:
                self._say("此项不可调")
        elif k == 1 and action == "press":
            self._say(f"当前：{self.summary()}")

    def _stage(self, k: str, v) -> None:
        if v == self.saved.get(k):
            self.pending.pop(k, None)
        else:
            self.pending[k] = v
        self.confirm_net = False

    def apply(self) -> None:
        changes = {k: v for k, v in self.pending.items() if self.saved.get(k) != v}
        if not changes:
            self.pending.clear()
            self._say("没有改动")
            return
        if any(k in NET_KEYS for k in changes) and not self.confirm_net:
            self.confirm_net = True
            self._say("将切换网络出口，可能中断模型连接 — 再按一次「应用」确认")
            return
        self.confirm_net = False
        full = {**self.saved, **changes}
        ok, msg = self.applier.apply(changes, full)
        if not ok:
            self._say(f"应用失败：{msg}")
            return
        self.saved = full
        self.store.save(self.saved)
        self.pending.clear()
        self.test_result = None
        self._say(msg)

    def run_test(self) -> None:
        """Tests the *applied* config (what the agent would use); pending edits must be applied first."""
        p = self.saved["provider"]
        if self.pending:
            self._say("请先「应用」再测试")
            return
        if not self.keys_set.get(p):
            self.test_result = "失败：无 Key"
            self._say(f"连接测试：{self.test_result}")
            return
        rt = getattr(self.applier, "run_test", None)
        if rt is None:
            self.test_result = "未实现"
            return
        self.test_result = "测试中…"

        def done(ok: bool, msg: str) -> None:
            self.test_result = msg if ok or msg.startswith("失败") else f"失败：{msg}"
            self.log.append(f"连接测试：{self.test_result}")
            self.dirty = True
            self.version += 1

        rt(dict(self.saved), done)

    def summary(self) -> str:
        prof = "Mac 代理:" + str(self.cfg("proxy_port")) if self.cfg("profile") == "mac_proxy" else "板载 Wi-Fi"
        return f"{prof} · {self.cfg('provider')} / {self.cfg('model')}"

    def state(self) -> dict:
        return {"active": self.active, "tab": self.tab, "focus": self.focus, "saved": dict(self.saved),
                "pending": dict(self.pending), "test": self.test_result, "toast": self.toast,
                "confirm_net": self.confirm_net}

