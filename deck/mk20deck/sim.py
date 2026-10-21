"""Desktop simulator: scripted screenshots, state gallery, GIF, and an
interactive local web page (no device needed).  Everything is marked SIM."""
from __future__ import annotations

import html
import io
import json
import os
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from PIL import Image, ImageDraw

from .adapters.herdr import HerdrAdapter
from .adapters.mock import MockAdapter
from .config import DeckConfig
from .controller import HID_FACES, Controller, KeyView, Role, slot_pos
from .demo_data import DESKTOP_CAPS, demo_sessions
from .layout import DeviceLayout
from .link import DeviceLink
from .model import Capabilities, Mode, Session, SessionId, State, Tool
from .render.canvas import Renderer, device_mockup
from .render.fonts import font
from .render.keys import render_key
from .transport import MockDevice


class FakeClock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def demo_adapters(herdr_unavailable: str = "herdr 未安装") -> dict[Mode, MockAdapter]:
    tmux_caps = Capabilities(focus="未绑定终端 App，请在配置中设置", new_session="未配置新会话命令",
                             skill_current="投递语义未验证（P0 禁用）", skill_new_thread="tmux 无新线程预填",
                             send="投递语义未验证（P0 禁用）", stop="中断能力未按工具版本验证",
                             approve="P1：请在终端内处理")
    tmux_sessions = demo_sessions(Mode.TMUX, (Tool.CCAI, Tool.CODEX))[:5]
    for s in tmux_sessions:
        s.state, s.state_reason = State.UNKNOWN, "tmux：无关联生命周期信号"
    return {
        Mode.DESKTOP: MockAdapter(Mode.DESKTOP, demo_sessions(), caps=DESKTOP_CAPS,
                                  limited="状态受限：粗状态，待审批请看 App"),
        Mode.HERDR: MockAdapter(Mode.HERDR, unavailable=herdr_unavailable, caps=HerdrAdapter().capabilities(),
                                limited="状态受限：herdr 未联调（P0 stub）"),
        Mode.TMUX: MockAdapter(Mode.TMUX, tmux_sessions, caps=tmux_caps, limited="状态受限：tmux 仅能确认窗格存在"),
    }


def full_adapters() -> dict[Mode, MockAdapter]:
    """demo_adapters with 15 Desktop sessions (every L1 slot occupied, all base states mixed)."""
    ads = demo_adapters()
    ads[Mode.DESKTOP].sessions = demo_sessions(n=15)
    return ads


def _ctl(clock: FakeClock, adapters=None, cfg: DeckConfig | None = None) -> Controller:
    c = Controller(cfg or DeckConfig(), adapters or demo_adapters(), clock=clock, wall=lambda: 1792244000.0,
                   sim=True)
    c.refresh()
    return c


def tap(c: Controller, clock: FakeClock, col: int, row: int, hold: float = 0.05) -> None:
    c.on_key(col, row, True)
    clock.advance(hold)
    c.tick()
    c.on_key(col, row, False)


def long_press(c: Controller, clock: FakeClock, col: int, row: int, dur: float = 0.7) -> None:
    """Hold with ticks so the 600 ms threshold fires while the key is down (slot: pick up)."""
    c.on_key(col, row, True)
    held = 0.0
    while held < dur:
        clock.advance(0.05)
        held += 0.05
        c.tick()
    c.on_key(col, row, False)


def shots(out_dir: str, animations: bool = True) -> list[tuple[str, str]]:
    """Render the scripted scenario set.  Returns [(filename, caption)]."""
    os.makedirs(out_dir, exist_ok=True)
    lay = DeviceLayout.default()
    made: list[tuple[str, str]] = []

    def save(c: Controller, clock: FakeClock, name: str, caption: str, anim_t: float = 0.25) -> None:
        r = Renderer(lay, animations=animations)
        view = c.view()
        r.compose(view, clock.t - anim_t)  # seed ages so animations have a phase
        canvas = r.compose(view, clock.t)
        device_mockup(canvas, lay, title=f"MK20 Agent Deck · SIM · {caption}").save(os.path.join(out_dir, name))
        made.append((name, caption))

    clk = FakeClock()
    c = _ctl(clk, full_adapters())
    save(c, clk, "01-desktop-L1-default.png", "Desktop 默认 L1：15 会话槽（未选中）")
    tap(c, clk, 2, 2)  # S7: select + focus
    clk.advance(0.5)
    c.toast = None
    save(c, clk, "02-desktop-L1-selected.png", "单击 S7 = 选中并置前（口述/删除/回车/Esc 都去这个会话）")
    tap(c, clk, 2, 4)  # DELETE: keyboard-side Backspace, the deck ignores the event
    save(c, clk, "03-device-hid-keys.png", "第 4 行：PTT / 删除 / 发送·可以 / 不行 Esc 由键盘直发 HID，deck 只画键面")
    clk.advance(2)
    c.on_knob(2, "down")
    c.on_knob(2, "up")
    clk.advance(1.0)
    c.toast = None
    save(c, clk, "04-L4-mode-page.png", "K2 短按 → L4：第 1 行 DESK/HERD/TMUX/＋新会话/回 L1，第 2–3 行同 L1 的 S6–S15")
    tap(c, clk, 2, 1)  # HERD
    save(c, clk, "05-herdr-offline.png", "L4 点 HERD（未安装 → offline + 原因）")
    clk.advance(2)
    tap(c, clk, 5, 4)  # cycle -> tmux
    save(c, clk, "06-tmux-mode.png", "短按模式键 → tmux（状态 unknown；未绑定终端 App 时单击只选中）")
    clk.advance(2)
    tap(c, clk, 5, 4)  # cycle -> desktop (restores mapping/selection)
    clk.advance(2)
    c.on_key(1, 4, True)  # PTT held
    save(c, clk, "07-ptt-held.png", "按住 PTT（右 Option 由设备 HID 发出）")
    c.on_key(1, 4, False)

    # overflow: 19 sessions → +4
    clk2 = FakeClock()
    many = demo_sessions(n=15)
    extra = []
    for i in range(4):
        s = replace(many[i], sid=SessionId(Mode.DESKTOP, f"01a1{i:04d}-0000-7000-8000-00000000000{i}"),
                    title=f"会话{i + 16}", state=State.IDLE, updated_ms=1_000 + i)
        extra.append(s)
    ads = demo_adapters()
    ads[Mode.DESKTOP].sessions = many + extra
    c2 = _ctl(clk2, ads)
    tap(c2, clk2, 1, 1)
    long_press(c2, clk2, 2, 1)  # pick S2 up ...
    tap(c2, clk2, 2, 1)  # ... and press it again: pin
    clk2.advance(2)
    save(c2, clk2, "08-overflow-19-sessions.png", "19 个会话：副屏 +4，S2 钉扎")

    gallery(os.path.join(out_dir, "09-state-gallery-static.png"))
    made.append(("09-state-gallery-static.png", "AC10 单帧状态图鉴（动画关闭）"))
    gif(os.path.join(out_dir, "10-desktop-animated.gif"))
    made.append(("10-desktop-animated.gif", "动画预览（全局 ≤8fps，单路注意力动画）"))
    via_protocol(os.path.join(out_dir, "11-via-protocol-mockdevice.png"))
    made.append(("11-via-protocol-mockdevice.png", "经 Protocol A → MockDevice 回解的整画布（640×656）"))
    write_index(out_dir, made)
    return made


TMUX_HOOK_CAPS = Capabilities(focus=None, new_session="未配置新会话命令", skill_current="投递语义未验证（P0 禁用）",
                              skill_new_thread="tmux 无新线程预填", send="投递语义未验证（P0 禁用）",
                              stop="中断能力未按工具版本验证", approve="P1：请在终端内处理", precise_state=None)
_ATTN = [  # (worktree, tool, final state, reason)
    ("mk20-deck-attn", Tool.CCAI, State.ERROR, "turn 失败（Claude hook：API 错误）"),
    ("k4-install", Tool.CCAI, State.RUNNING, "运行中（Claude hook）"),
    ("router-ota", Tool.CCAI, State.NEEDS_APPROVAL, "等待批准（Claude hook）"),
    ("kbd-ui", Tool.CODEX, State.RUNNING, "运行中（codex hook）"),
    ("prd-v14", Tool.CODEX, State.RUNNING, "运行中（codex hook）"),
    ("nas-deploy", Tool.CODEX, State.NEEDS_APPROVAL, "等待批准（codex hook）"),
    ("web-sim", Tool.CCAI, State.RUNNING, "运行中（Claude hook）"),
    ("hooks-doc", Tool.CCAI, State.DONE_UNREAD, "已完成未查看（Claude hook）"),
]


def attention_adapters() -> dict[Mode, MockAdapter]:
    """tmux with hook-derived states (all running first, so slots are sticky), Desktop with 1 done_unread."""
    tm = []
    for i, (wt, tool, _st, _why) in enumerate(_ATTN):
        tm.append(Session(SessionId(Mode.TMUX, f"%{i + 3}", "4242-1700000000"), tool, wt, state=State.RUNNING,
                          cwd=f"/Users/dev/worktrees/{wt}/deck", summary=f"ag:{i} {tool.value} %{i + 3} · bash",
                          source_note="hook 关联", state_reason="运行中（Claude hook）",
                          updated_ms=1_792_244_000_000 - i * 60_000))
    desk = demo_sessions()
    for s in desk:
        if s.state != State.DONE_UNREAD:
            s.state, s.state_reason = State.IDLE, "空闲"
    return {
        Mode.DESKTOP: MockAdapter(Mode.DESKTOP, desk, caps=DESKTOP_CAPS, limited="状态受限：粗状态，待审批请看 App"),
        Mode.HERDR: MockAdapter(Mode.HERDR, unavailable="herdr 未安装", caps=HerdrAdapter().capabilities(),
                                limited="状态受限：herdr 未联调（P0 stub）"),
        Mode.TMUX: MockAdapter(Mode.TMUX, tm, caps=TMUX_HOOK_CAPS, limited="状态来自 Claude/codex hooks"),
    }


def attention_shots(out_dir: str, animations: bool = True) -> list[tuple[str, str]]:
    """tmux L1 with 2 needs_approval, 1 error, 1 done, rest running; K1 rotation; cross-mode counts."""
    os.makedirs(out_dir, exist_ok=True)
    lay = DeviceLayout.default()
    made: list[tuple[str, str]] = []

    def save(c: Controller, clock: FakeClock, name: str, caption: str) -> None:
        r = Renderer(lay, animations=animations)
        view = c.view()
        r.compose(view, clock.t - 0.25)
        device_mockup(r.compose(view, clock.t), lay, title=f"MK20 Agent Deck · SIM · {caption}").save(
            os.path.join(out_dir, name))
        made.append((name, caption))

    clk = FakeClock()
    ads = attention_adapters()
    cfg = DeckConfig(default_mode=Mode.TMUX)
    c = Controller(cfg, ads, clock=clk, wall=lambda: 1792244000.0, sim=True)
    c.refresh()  # all running: slots fill in recency order and then stay put
    for s, (_wt, _tool, st, why) in zip(ads[Mode.TMUX].sessions, _ATTN):
        s.state, s.state_reason = st, why
        if st == State.DONE_UNREAD:
            s.done_marker = "claude:demo:1792243990.000"
    clk.advance(1.0)
    c.refresh()
    for m in (Mode.DESKTOP, Mode.HERDR):  # what the Daemon background poll delivers
        try:
            c.apply_background(m, ads[m].poll())
        except Exception as e:  # noqa: BLE001
            c.apply_background(m, None, str(e))
    clk.advance(2.0)
    save(c, clk, "attn-01-tmux-L1.png", "tmux L1：2 待审批 · 1 出错 · 1 完成 · 其余运行；副屏「桌面 1 待处理」")
    c.on_knob(1, "cw")
    clk.advance(0.5)
    save(c, clk, "attn-02-k1-cw-first-approval.png", "K1 顺转 1 格 → 选中最急的「待审批」(S3)")
    c.on_knob(1, "cw")
    c.on_knob(1, "cw")
    clk.advance(0.5)
    save(c, clk, "attn-03-k1-cw-error.png", "再转 2 格：待审批 S6 → 出错 S1（顺序 审批>输入>出错>完成>槽位）")
    c.on_knob(1, "cw")
    clk.advance(0.5)
    save(c, clk, "attn-04-k1-cw-done.png", "再转 1 格 → 完成未读 S8；按 K1 = 聚焦（同单击会话键）")
    c.on_knob(1, "down")
    c.on_knob(1, "up")
    clk.advance(0.3)
    save(c, clk, "attn-05-k1-press-focus.png", "按 K1：聚焦选中会话（完成未读本地清除）")
    clk.advance(2.0)
    c.on_knob(2, "down")
    c.on_knob(2, "up")
    clk.advance(1.0)
    c.toast = None
    save(c, clk, "attn-06-L4-mode-badges.png", "L4：DESK 键角标 = 桌面待处理数（后台 10 s 轮询）")
    write_index(out_dir, made)
    return made


def layout_shots(out_dir: str, animations: bool = False) -> list[tuple[str, str]]:
    """PRD 1.5 L1/L4 key layout (deck/docs/PRD-1.5-L1-LAYOUT.md): 15 slots in mixed states, one selected."""
    os.makedirs(out_dir, exist_ok=True)
    lay = DeviceLayout.default()
    made: list[tuple[str, str]] = []

    def save(c: Controller, clock: FakeClock, name: str, caption: str) -> None:
        r = Renderer(lay, animations=animations)
        view = c.view()
        r.compose(view, clock.t - 0.25)
        device_mockup(r.compose(view, clock.t), lay, title=f"MK20 Agent Deck · SIM · {caption}").save(
            os.path.join(out_dir, name))
        made.append((name, caption))

    clk = FakeClock()
    c = _ctl(clk, full_adapters())
    tap(c, clk, 2, 2)  # S7: single tap = select AND focus
    clk.advance(2.0)
    c.toast = None
    save(c, clk, "l1v2-01-L1-15-sessions-selected.png", "L1：S1–S15 会话（混合状态），单击 S7 = 选中并置前")
    c.on_knob(2, "down")
    c.on_knob(2, "up")
    clk.advance(2.0)
    c.toast = None
    save(c, clk, "l1v2-02-L4.png", "L4：DESK/HERD/TMUX/＋新会话/回 L1 · S6–S15 同位 · 第 4 行同 L1")
    clk2 = FakeClock()
    ads = full_adapters()
    tm = demo_sessions(Mode.TMUX, (Tool.CCAI, Tool.CODEX), n=15)
    ads[Mode.TMUX] = MockAdapter(Mode.TMUX, tm, caps=TMUX_HOOK_CAPS, limited="状态来自 Claude/codex hooks")
    c2 = Controller(DeckConfig(default_mode=Mode.TMUX), ads, clock=clk2, wall=lambda: 1792244000.0, sim=True)
    c2.refresh()
    c2.on_knob(1, "cw")  # K1: the most urgent session
    clk2.advance(2.0)
    c2.toast = None
    save(c2, clk2, "l1v2-03-L1-tmux-mixed-tools.png", "tmux L1：ccai/codex 混排 15 会话，转 K1 选中最急的「待审批」")
    write_index(out_dir, made)
    return made


def move_shots(out_dir: str, animations: bool = False) -> list[tuple[str, str]]:
    """Pick-up-and-place (PRD 1.5 delta 2026-10-22): long press = pick up, next slot press = place + pin."""
    os.makedirs(out_dir, exist_ok=True)
    lay = DeviceLayout.default()
    made: list[tuple[str, str]] = []

    def save(c: Controller, clock: FakeClock, name: str, caption: str, clear_toast: bool = True) -> None:
        if clear_toast:
            c.toast = None
        r = Renderer(lay, animations=animations)
        view = c.view()
        r.compose(view, clock.t - 0.25)
        device_mockup(r.compose(view, clock.t), lay, title=f"MK20 Agent Deck · SIM · {caption}").save(
            os.path.join(out_dir, name))
        made.append((name, caption))

    clk = FakeClock()
    c = _ctl(clk, full_adapters())
    tap(c, clk, 3, 1)  # S3: select + focus
    clk.advance(2.0)
    long_press(c, clk, 3, 1)  # S3: pick up
    clk.advance(1.0)
    save(c, clk, "move-01-L1-picked-up.png", "长按 S3 = 拿起：粉色虚线框 +「移动中」，副屏写明怎么放下")
    tap(c, clk, 4, 2)  # S9: swap + pin, no focus
    clk.advance(0.3)
    save(c, clk, "move-02-L1-placed-swap.png", "点 S9 放下：S3↔S9 交换，S9 钉住（白钉），不置前", clear_toast=False)
    clk.advance(2.0)
    c.on_knob(2, "down")
    c.on_knob(2, "up")  # L4
    clk.advance(1.0)
    long_press(c, clk, 2, 3)  # S12 on L4 (same key as on L1)
    clk.advance(1.0)
    save(c, clk, "move-03-L4-picked-up.png", "L4 第 2–3 行与 L1 同槽：长按 S12 拿起")
    tap(c, clk, 2, 3)  # the same slot: toggle the pin
    clk.advance(0.3)
    save(c, clk, "move-04-L4-tap-self-pin.png", "点自己 = 切换钉住：S12 已钉扎（钉扎 2/15）", clear_toast=False)
    clk.advance(2.0)
    long_press(c, clk, 4, 2)  # S9 again
    c.on_knob(1, "down")
    c.on_knob(1, "up")  # K1 press = cancel
    clk.advance(0.3)
    save(c, clk, "move-05-K1-cancel.png", "按 K1 = 取消移动：什么都不变，副屏「已取消移动」", clear_toast=False)
    clk2 = FakeClock()
    c2 = _ctl(clk2)  # demo: 8 sessions, S9..S15 empty
    long_press(c2, clk2, 2, 1)  # S2: pick up
    tap(c2, clk2, 5, 3)  # S15 (empty): move there
    clk2.advance(0.3)
    save(c2, clk2, "move-06-L1-placed-empty.png", "放到空槽 S15：S2 变空（之后按补位规则可再填），S15 钉住",
         clear_toast=False)
    write_index(out_dir, made)
    return made


def option_adapters() -> dict[Mode, MockAdapter]:
    """tmux with a bound terminal (focus works) and three sessions waiting for a numbered choice."""
    ads = demo_adapters()
    ss = demo_sessions(Mode.TMUX, (Tool.CCAI, Tool.CODEX), n=12)
    for s in ss:  # states from hook records (the only prompts option mode answers)
        s.state_reason, s.hooked = ("Claude hook" if s.tool == Tool.CCAI else "codex hook"), True
    for s in ss[3:]:  # exactly three sessions wait for a choice
        if s.state in (State.NEEDS_INPUT, State.NEEDS_APPROVAL):
            s.state = State.RUNNING
    q, perm, unk = ss[0], ss[1], ss[2]
    q.tool, q.title, q.state, q.state_reason = Tool.CCAI, "数据库选型", State.NEEDS_INPUT, "等待输入（Claude hook）"
    q.options = ("用 SQLite", "用 Postgres（docker compose）", "先不加数据库")
    perm.tool, perm.title, perm.state, perm.state_reason = (Tool.CCAI, "推送发布", State.NEEDS_APPROVAL,
                                                            "等待批准（Claude hook）")
    perm.prompt_kind = "permission"
    unk.tool, unk.title, unk.state, unk.state_reason = Tool.CODEX, "NAS挂载", State.NEEDS_INPUT, "等待输入（codex hook）"
    caps = Capabilities(focus=None, new_session=None, skill_new_thread=None, approve="P1：请在终端内处理")
    ads[Mode.TMUX] = MockAdapter(Mode.TMUX, ss, caps=caps, limited="状态来自 Claude/codex hooks")
    return ads


def option_shots(out_dir: str, animations: bool = False) -> list[tuple[str, str]]:
    """Option mode (user decision 2026-10-22): tap a needs_* session = answer its numbered choice with digit keys."""
    from . import protocol as P
    from .slots import N_SLOTS
    os.makedirs(out_dir, exist_ok=True)
    lay = DeviceLayout.default()
    made: list[tuple[str, str]] = []

    def save(c: Controller, clock: FakeClock, name: str, caption: str, clear_toast: bool = True) -> None:
        if clear_toast:
            c.toast = None
        r = Renderer(lay, animations=animations)
        view = c.view()
        r.compose(view, clock.t - 0.25)
        device_mockup(r.compose(view, clock.t), lay, title=f"MK20 Agent Deck · SIM · {caption}").save(
            os.path.join(out_dir, name))
        made.append((name, caption))

    def slot_of(c: Controller, title: str) -> tuple[int, int]:
        for i in range(N_SLOTS):
            s = c.mgr.session_at(i)
            if s is not None and s.title == title:
                return slot_pos(i)
        raise LookupError(title)

    clk = FakeClock()
    c = Controller(DeckConfig(), option_adapters(), {"mode": "tmux"}, clock=clk, wall=lambda: 1792244000.0, sim=True)
    c.refresh()
    c.option_hid = "ok"  # what the daemon reports once the keyboard accepted ui.hidKeys
    save(c, clk, "opt-00-L1-waiting.png", "L1：三个会话在等我选（待输入 / 待审批）")
    tap(c, clk, *slot_of(c, "数据库选型"))
    clk.advance(0.5)
    save(c, clk, "opt-01-question-3-labels.png",
         "点待输入会话 = 置前 + 选项模式（AskUserQuestion 3 个选项）")
    c.on_key(2, 1, True, hid=P.HID_DIGIT[2])  # the keyboard typed "2" into the frontmost window
    clk.advance(0.05)
    c.on_key(2, 1, False)
    clk.advance(0.3)
    save(c, clk, "opt-02-chosen-2.png", "按 2：键盘直发数字 2，副屏「已选 2」，状态一变 / 3 s 后回列表",
         clear_toast=False)
    clk.advance(3.1)
    c.tick()
    c.toast = None
    tap(c, clk, *slot_of(c, "推送发布"))
    clk.advance(0.5)
    save(c, clk, "opt-03-permission.png", "权限确认：固定「是 / 是，且不再问 / 否」")
    c.on_knob(1, "down")
    c.on_knob(1, "up")  # K1 = back to the grid
    clk.advance(0.5)
    tap(c, clk, *slot_of(c, "NAS挂载"))
    clk.advance(0.5)
    save(c, clk, "opt-04-unknown-digits.png", "未知提示（无选项信息）：只显示数字 1–5")
    c.option_hid = "legacy"
    c._touch()
    save(c, clk, "opt-05-legacy-firmware.png", "旧固件（无 ui.hidKeys）：提示在 Mac 上按数字")
    write_index(out_dir, made)
    return made


def gallery(path: str) -> None:
    """All 10 base states × {codex, ccai} × {normal, selected} + overlays, animations off."""
    states = list(State)
    cell, pad = 128, 12
    cols = len(states) + 1  # the overlay row has one more face (picked-up slot)
    rows = 5
    W = pad + cols * (cell + pad)
    H = 40 + rows * (cell + pad + 18) + pad
    img = Image.new("RGB", (W, H), (24, 25, 28))
    d = ImageDraw.Draw(img)
    d.text((pad, 10), "状态图鉴（单帧、动画关闭）· 行：codex / codex 选中 / ccai / ccai 选中 / 叠加与命令键",
           font=font(16, bold=True), fill=(230, 230, 235))
    for ci, st in enumerate(states):
        for ri, (tool, sel) in enumerate(((Tool.CODEX, False), (Tool.CODEX, True), (Tool.CCAI, False),
                                          (Tool.CCAI, True))):
            kv = KeyView(1, 1, Role.AGENT, state=st, tool=tool, title="会话名", selected=sel and st != State.UNASSIGNED)
            if st == State.UNASSIGNED:
                kv.label = "空槽"
            face = render_key(kv, 5.0, 0.0, animations=False)
            x, y = pad + ci * (cell + pad), 40 + ri * (cell + pad + 18)
            img.paste(face, (x, y))
            d.text((x + cell / 2, y + cell + 9), st.value, font=font(12), fill=(170, 170, 175), anchor="mm")
    extras = [
        KeyView(1, 4, Role.PTT, label="按住说话", active=True),
        KeyView(1, 4, Role.PTT, label="按住说话"),
        KeyView(2, 4, Role.DELETE, label=HID_FACES[Role.DELETE][0], sublabel=HID_FACES[Role.DELETE][1]),
        KeyView(3, 4, Role.OK, label=HID_FACES[Role.OK][0], sublabel=HID_FACES[Role.OK][1]),
        KeyView(4, 4, Role.NO, label=HID_FACES[Role.NO][0], sublabel=HID_FACES[Role.NO][1]),
        KeyView(1, 1, Role.AGENT, state=State.UNASSIGNED, lost=True, pinned=True, label="失联钉扎"),
        KeyView(1, 1, Role.AGENT, state=State.RUNNING, tool=Tool.CODEX, title="钉扎", pinned=True),
        KeyView(5, 4, Role.MODE, label="DESK", mode=Mode.DESKTOP),
        KeyView(4, 1, Role.NEW, label="新会话", disabled="未配置新会话命令"),
        KeyView(5, 1, Role.BACK_L1, label="回 L1"),
        KeyView(1, 1, Role.AGENT, state=State.RUNNING, tool=Tool.CCAI, title="移动中", selected=True, moving=True),
    ]
    names = ["PTT 按住", "PTT 静态", "删除", "发送/可以", "不行 Esc", "失联钉扎", "钉扎", "模式 DESK",
             "新会话(禁用)", "回 L1", "拿起(移动中)"]
    for ci, kv in enumerate(extras):
        face = render_key(kv, 5.0, 0.0, animations=False)
        x, y = pad + ci * (cell + pad), 40 + 4 * (cell + pad + 18)
        img.paste(face, (x, y))
        d.text((x + cell / 2, y + cell + 9), names[ci], font=font(12), fill=(170, 170, 175), anchor="mm")
    img.save(path)


def gif(path: str, seconds: float = 3.0) -> None:
    clk = FakeClock()
    c = _ctl(clk, full_adapters())
    tap(c, clk, 3, 2)
    clk.advance(1.6)
    lay = DeviceLayout.default()
    r = Renderer(lay)
    frames = []
    fps = 8
    for i in range(int(seconds * fps)):
        v = c.view()
        frames.append(device_mockup(r.compose(v, clk.t), lay, scale=0.6).convert("P", palette=Image.ADAPTIVE))
        clk.advance(1 / fps)
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=int(1000 / fps), loop=0)


def via_protocol(path: str) -> None:
    """End-to-end: controller → renderer → Protocol A frames → MockDevice → decode."""
    dev = MockDevice()
    link = DeviceLink(dev, ack_timeout=2.0, preamble_flush=0)
    link.start()
    lay = link.handshake()
    clk = FakeClock()
    c = _ctl(clk)
    tap(c, clk, 2, 1)
    r = Renderer(lay)
    assert link.show_jpeg(r.jpeg(r.compose(c.view(), clk.t)))
    Image.open(io.BytesIO(dev.frames[-1])).save(path)
    link.close()


def write_index(out_dir: str, made: list[tuple[str, str]]) -> None:
    items = "\n".join(
        f'<figure><img src="{html.escape(n)}" loading="lazy"><figcaption>{html.escape(cap)}</figcaption></figure>'
        for n, cap in made)
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(f"""<!doctype html><meta charset="utf-8"><title>MK20 Agent Deck · SIM screenshots</title>
<style>body{{background:#16181c;color:#ddd;font-family:-apple-system,system-ui,sans-serif;margin:24px}}
figure{{display:inline-block;margin:12px;vertical-align:top}}img{{max-width:760px;border-radius:12px}}
figcaption{{margin-top:6px}}</style><h1>MK20 Agent Deck · P0 模拟器截图（SIM）</h1>{items}""")


# ---------------------------------------------------------------------------- web sim
PAGE = """<!doctype html><meta charset="utf-8"><title>MK20 Deck SIM</title>
<style>body{background:#16181c;color:#ddd;font-family:-apple-system,system-ui,sans-serif;margin:20px}
#wrap{position:relative;display:inline-block}#cv{display:block;border-radius:10px}
.k{position:absolute;cursor:pointer}.k:active{outline:2px solid #fff}
button{margin:4px;padding:6px 10px;background:#333;color:#eee;border:1px solid #555;border-radius:6px}
#log{white-space:pre;font:12px monospace;color:#aaa;max-height:200px;overflow:auto}</style>
<h2>MK20 Agent Deck · SIM（__SRC__）</h2>
<div id=wrap><img id=cv src="/frame.jpg"></div>
<div>K1 <button onmousedown="kn(1,'down')" onmouseup="kn(1,'up')">按</button><button onclick="kn(1,'ccw')">⟲</button><button onclick="kn(1,'cw')">⟳</button>
&nbsp; K2 <button onmousedown="kn(2,'down')" onmouseup="kn(2,'up')">按</button><button onclick="kn(2,'ccw')">⟲</button><button onclick="kn(2,'cw')">⟳</button></div>
<div id=log></div>
<script>
const L=__LAYOUT__;const w=document.getElementById('wrap');
for(const r of L.keys){const d=document.createElement('div');d.className='k';
d.style.left=r.x+'px';d.style.top=r.y+'px';d.style.width=r.w+'px';d.style.height=r.h+'px';
d.onmousedown=()=>fetch(`/key?col=${r.dc}&row=${r.dr}&pressed=1`,{method:'POST'});
d.onmouseup=()=>fetch(`/key?col=${r.dc}&row=${r.dr}&pressed=0`,{method:'POST'});w.appendChild(d);}
function kn(k,a){fetch(`/knob?k=${k}&a=${a}`,{method:'POST'})}
setInterval(()=>{document.getElementById('cv').src='/frame.jpg?'+Date.now()},150);
setInterval(async()=>{const r=await fetch('/log');document.getElementById('log').textContent=await r.text()},1000);
</script>"""


def serve(daemon, host: str = "127.0.0.1", port: int = 8765, source: str = "demo") -> None:
    latest = {"jpeg": b""}
    daemon.sink = lambda b: latest.__setitem__("jpeg", b)
    stop = threading.Event()
    th = threading.Thread(target=daemon.run, args=(stop,), daemon=True)
    th.start()
    lay = daemon.renderer.layout
    keys = [{"x": r.x, "y": r.y, "w": r.w, "h": r.h, "dc": lay.fw_to_doc(r.col, r.row)[0],
             "dr": lay.fw_to_doc(r.col, r.row)[1]} for r in lay.keys]
    page = PAGE.replace("__LAYOUT__", json.dumps({"keys": keys})).replace("__SRC__", html.escape(source))

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _ok(self, body: bytes, ctype: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/":
                self._ok(page.encode(), "text/html; charset=utf-8")
            elif u.path == "/frame.jpg":
                self._ok(latest["jpeg"], "image/jpeg")
            elif u.path == "/log":
                self._ok("\n".join(daemon.controller.log[-30:]).encode(), "text/plain; charset=utf-8")
            else:
                self.send_error(404)

        def do_POST(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if u.path == "/key":
                daemon.inject_key(int(q["col"]), int(q["row"]), q.get("pressed") == "1")
            elif u.path == "/knob":
                daemon.inject_knob(int(q["k"]), q["a"])
            else:
                self.send_error(404)
                return
            self._ok(b"ok", "text/plain")

    srv = ThreadingHTTPServer((host, port), H)
    print(f"SIM: http://{host}:{port}/  (source={source})")
    try:
        srv.serve_forever()
    finally:
        stop.set()
