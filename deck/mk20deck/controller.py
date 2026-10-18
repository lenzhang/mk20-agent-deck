"""Deck controller: pages, key roles, gestures, mode switching, actions.

Pure logic with an injectable clock; it never writes HID output.  PTT is a
device-side ``KC_RALT`` (PRD §6.5 path A); the controller only renders an overlay
when it can observe the press.  Path B (synthesised Right Option) is not
implemented in P0 and stays disabled.
"""
from __future__ import annotations

import enum
import logging
import time
from dataclasses import dataclass, field
from typing import Callable

from .adapters.base import BackendUnavailable, ModeAdapter
from .config import DeckConfig
from .model import (ATTENTION_STATES, MODE_CYCLE, PRIORITY, ActionResult, Mode, Session, State, Tool)
from .settings import SettingsPage
from .slots import N_SLOTS, SlotManager

log = logging.getLogger(__name__)

# PRD §6.5 status (2026-10-18): path A (VIA KC_RALT) has no effect in host mode (keys are
# routed GD32 → T113, the GD32 keymap does not emit HID); path B (synthesized Right
# Option) is on hold by user decision.  PTT therefore only shows this hint.
PTT_UNAVAILABLE = "PTT 不可用，请使用 Mac 原语音键"

LONG_PRESS = 0.600   # D14
DOUBLE_TAP = 0.350   # §6.3
TOAST_S = 1.5
SWITCH_TOAST_S = 1.2
STOP_SEGMENTS = 4


class Role(str, enum.Enum):
    AGENT = "agent"
    ACCEPT = "accept"
    DECLINE = "decline"
    SKILL = "skill"
    SEND = "send"
    PTT = "ptt"
    STOP = "stop"
    NEW = "new"
    FOCUS = "focus"
    MODE = "mode"
    MODE_SELECT = "mode_select"
    BACK_L1 = "back_l1"


@dataclass(frozen=True)
class KeyRole:
    role: Role
    index: int = 0          # agent slot 0..7 / skill 0..3
    mode: Mode | None = None  # for MODE_SELECT


def role_at(page: str, col: int, row: int) -> KeyRole | None:
    """1-based document coordinates (PRD §6.2 L1 / L4 tables)."""
    if not (1 <= col <= 5 and 1 <= row <= 4):
        return None
    if row in (1, 2) and col <= 4:
        return KeyRole(Role.AGENT, (row - 1) * 4 + (col - 1))
    if col == 5 and row == 1:
        return KeyRole(Role.ACCEPT)
    if col == 5 and row == 2:
        return KeyRole(Role.DECLINE)
    if col == 5 and row == 3:
        return KeyRole(Role.SEND)
    if row == 4:
        return KeyRole([Role.PTT, Role.STOP, Role.NEW, Role.FOCUS, Role.MODE][col - 1])
    # row 3, cols 1..4
    if page == "L4":
        if col == 4:
            return KeyRole(Role.BACK_L1)
        return KeyRole(Role.MODE_SELECT, mode=MODE_CYCLE[col - 1])
    return KeyRole(Role.SKILL, col - 1)


@dataclass
class KeyView:
    col: int
    row: int
    role: Role
    label: str = ""
    state: State | None = None
    tool: Tool | None = None
    title: str = ""
    selected: bool = False
    pinned: bool = False
    lost: bool = False
    disabled: str | None = None
    badge: str = ""
    progress: int = 0
    active: bool = False  # PTT held / current mode in L4
    mode: Mode | None = None
    attention: bool = False


@dataclass
class SubView:
    page: str
    mode: Mode
    mode_ok: bool
    mode_reason: str
    session: Session | None
    toast: str | None
    limited: str | None
    overflow: int
    overflow_attention: int
    skill_note: str | None
    updated: str
    ptt: bool
    scroll: int
    sim: bool = False
    pin_info: str = ""
    selection_lost: bool = False


@dataclass
class DeckView:
    keys: list[KeyView]
    sub: SubView
    settings: object | None = None  # SettingsPage when page == "LS"


@dataclass
class _Press:
    role: KeyRole
    t0: float
    mode: Mode
    target: str | None = None
    version: int = 0
    long_fired: bool = False
    cancelled: bool = False


class Controller:
    def __init__(self, cfg: DeckConfig, adapters: dict[Mode, ModeAdapter], state: dict | None = None,
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time,
                 sim: bool = False, on_state_change: Callable[[dict], None] | None = None,
                 settings: "SettingsPage | None" = None) -> None:
        self.cfg = cfg
        self.adapters = adapters
        self.clock = clock
        self.wall = wall
        self.sim = sim
        self.on_state_change = on_state_change
        state = state or {}
        try:
            self.mode = Mode(state.get("mode", cfg.default_mode.value))
        except ValueError:
            self.mode = cfg.default_mode
        self.page = "L1"
        self.managers: dict[Mode, SlotManager] = {
            m: SlotManager.from_dict((state.get("modes") or {}).get(m.value, {})) for m in MODE_CYCLE}
        self.backend: dict[Mode, tuple[bool, str]] = {m: (True, "") for m in MODE_CYCLE}
        self.local_cleared: dict[str, str] = {}
        self.last_poll_wall: dict[Mode, float] = {}
        self.toast: tuple[str, float] | None = None
        self.ptt_held = False
        self.ptt_observed = False
        self.presses: dict[tuple, _Press] = {}
        self.last_agent_tap: tuple[int, str, float] | None = None
        self.scroll = 0
        self.version = 0  # bumps whenever the view may have changed
        self.log: list[str] = []
        self.settings = settings or SettingsPage()

    # ------------------------------------------------------------------ helpers
    @property
    def mgr(self) -> SlotManager:
        return self.managers[self.mode]

    @property
    def adapter(self) -> ModeAdapter:
        return self.adapters[self.mode]

    def _touch(self) -> None:
        self.version += 1

    def _say(self, text: str, dur: float = TOAST_S) -> None:
        self.toast = (text, self.clock() + dur)
        self.log.append(text)
        self._touch()

    def _result(self, res: ActionResult) -> ActionResult:
        self._say(res.message)
        return res

    def persist(self) -> dict:
        st = {"mode": self.mode.value, "modes": {m.value: self.managers[m].to_dict() for m in MODE_CYCLE}}
        if self.on_state_change:
            self.on_state_change(st)
        return st

    # ------------------------------------------------------------------ backend
    def refresh(self, mode: Mode | None = None) -> None:
        mode = mode or self.mode
        mgr, ad = self.managers[mode], self.adapters[mode]
        keep = []
        for e in mgr.slots:
            if e is not None:
                keep.append(e.key.split(":", 2)[2])
        if mgr.selected:
            keep.append(mgr.selected.split(":", 2)[2])
        before = (mgr.mapping_version, self.backend[mode])
        try:
            sessions = ad.poll(keep)
            self.backend[mode] = (True, "")
            self.last_poll_wall[mode] = self.wall()
        except BackendUnavailable as e:
            self.backend[mode] = (False, str(e))
            for s in mgr.sessions.values():
                s.state, s.state_reason = State.OFFLINE, str(e)
            self._touch()
            return
        except Exception as e:  # noqa: BLE001 — never crash the deck on a backend bug
            log.exception("poll failed")
            self.backend[mode] = (False, f"读取失败：{type(e).__name__}")
            self._touch()
            return
        for s in sessions:
            k = s.sid.key()
            marker = self.local_cleared.get(k)
            if marker is not None:
                if s.state == State.DONE_UNREAD and s.done_marker == marker:
                    s.state, s.state_reason = State.IDLE, "已聚焦（本地清提醒）"
                elif s.state != State.DONE_UNREAD:
                    self.local_cleared.pop(k, None)
        sel_before = mgr.selected
        mgr.update(sessions)
        if sel_before and mgr.selected is None:
            self._cancel_target_presses()
        if (mgr.mapping_version, self.backend[mode]) != before:
            self.persist()
        self._touch()

    # ------------------------------------------------------------------ input
    # ------------------------------------------------------------------ settings page (LS)
    def open_settings(self) -> None:
        self.presses.clear()
        self.settings.open()
        self.page = "LS"
        self._touch()

    def _settings_after(self) -> None:
        st = self.settings
        if not st.active:
            self.page = st.exit_to or "L1"
        self._touch()

    def on_key(self, col: int, row: int, pressed: bool) -> None:
        """1-based doc coordinates."""
        if self.page == "LS":
            if pressed:
                self.settings.key(col, row)
            self._settings_after()
            return
        t = self.clock()
        kr = role_at(self.page, col, row)
        if kr is None:
            return
        pk = ("key", col, row)
        if pressed:
            if pk in self.presses:  # duplicate down (bounce) — ignore
                return
            self.presses[pk] = p = _Press(kr, t, self.mode)
            self._on_down(kr, p, t)
        else:
            p = self.presses.pop(pk, None)
            if p is None:
                return
            self._on_up(kr, p, t)
        self._touch()

    def on_knob(self, knob: int, kind: str) -> None:
        if self.page == "LS":
            if kind in ("cw", "ccw"):
                self.settings.knob(knob, kind)
            elif kind == "up":
                self.settings.knob(knob, "press")
            self._settings_after()
            return
        t = self.clock()
        pk = ("knob", knob)
        if kind == "down":
            self.presses[pk] = _Press(KeyRole(Role.MODE), t, self.mode)
        elif kind == "up":
            p = self.presses.pop(pk, None)
            if p and not p.long_fired and not p.cancelled and t - p.t0 < LONG_PRESS:
                if knob == 1 and self.page == "L4":
                    self.open_settings()  # SETTINGS.md: short K1 press on L4 opens settings
                    return
                if knob == 1:
                    s = self.mgr.selected_session()
                    self._say(f"effort：{s.effort or '未知'}（只读）· {self.adapter.capabilities().effort_set or '可调'}"
                              if s else "未选中会话")
                else:
                    self.page = "L4" if self.page == "L1" else "L1"
                    self._say(f"切到 {self.page}", 0.8)
        elif kind in ("cw", "ccw"):
            if knob == 1:
                s = self.mgr.selected_session()
                if s is None:
                    self._say("未选中会话")
                else:
                    self._result(self.adapter.set_effort(s, "+" if kind == "cw" else "-"))
            else:
                self.scroll = max(0, self.scroll + (1 if kind == "cw" else -1))
        self._touch()

    def tick(self) -> None:
        """Fire long-press thresholds (independent of rendering)."""
        t = self.clock()
        for pk, p in list(self.presses.items()):
            if p.long_fired or p.cancelled or t - p.t0 < LONG_PRESS:
                if p.role.role == Role.STOP and not p.long_fired and not p.cancelled:
                    seg = min(STOP_SEGMENTS, int((t - p.t0) / (LONG_PRESS / STOP_SEGMENTS)))
                    if seg != getattr(p, "_seg", -1):
                        p._seg = seg  # type: ignore[attr-defined]
                        self._touch()
                continue
            p.long_fired = True
            if pk[0] == "knob":
                if pk[1] == 1:
                    self.page = "L4"
                    self._say("L4 模式/设置", 0.8)
                else:
                    self.page = "L1"
                    self._say("回到 L1", 0.8)
                continue
            self._on_long(p.role, p)
            self._touch()

    # ------------------------------------------------------------------ gestures
    def _on_down(self, kr: KeyRole, p: _Press, t: float) -> None:
        r = kr.role
        if r == Role.PTT:
            self.ptt_held = True
            self.ptt_observed = True
            if PTT_UNAVAILABLE:
                self._say(PTT_UNAVAILABLE)
            return
        if r == Role.AGENT:
            mgr = self.mgr
            entry = mgr.entry(kr.index)
            if entry is not None and entry.lost:
                self._say("失联钉扎 · 长按解除")
                p.target = entry.key
                return
            prev_sel = mgr.selected
            s = mgr.select(kr.index)
            if s is None:
                self._say("空槽")
                return
            if prev_sel != mgr.selected:
                self._cancel_target_presses(exclude=p)
                self.persist()
            key = s.sid.key()
            p.target = key
            lt = self.last_agent_tap
            if lt and lt[0] == kr.index and lt[1] == key and t - lt[2] <= DOUBLE_TAP:
                self.last_agent_tap = None
                self._focus(s)
            else:
                self.last_agent_tap = (kr.index, key, t)
            return
        if r == Role.STOP:
            s = self.mgr.selected_session()
            p.target = s.sid.key() if s else None
            p.version = self.mgr.mapping_version
            reason = self._stop_block_reason(s)
            if reason:
                p.cancelled = True
                self._say(reason)
            return
        if r in (Role.MODE,):
            return  # decided on release / long press
        # click-on-press keys
        self._click(kr)

    def _on_up(self, kr: KeyRole, p: _Press, t: float) -> None:
        r = kr.role
        if r == Role.PTT:
            self.ptt_held = False
            return
        if p.long_fired or p.cancelled:
            return
        held = t - p.t0
        if r == Role.MODE and held < LONG_PRESS:
            i = MODE_CYCLE.index(self.mode)
            self.switch_mode(MODE_CYCLE[(i + 1) % len(MODE_CYCLE)])
        elif r == Role.STOP and held < LONG_PRESS:
            self._say("长按停止")

    def _on_long(self, kr: KeyRole, p: _Press) -> None:
        r = kr.role
        if r == Role.AGENT:
            msg = self.mgr.toggle_pin(kr.index)
            self.persist()
            self._say(msg)
        elif r == Role.MODE:
            self.page = "L4"
            self._say("L4 模式页：点选模式", 0.8)
        elif r == Role.STOP:
            s = self.mgr.selected_session()
            if (p.mode != self.mode or s is None or s.sid.key() != p.target
                    or p.version != self.mgr.mapping_version):
                self._say("目标已变化，停止取消")
                return
            self._result(self.adapter.stop(s))

    def _stop_block_reason(self, s: Session | None) -> str | None:
        cap = self.adapter.capabilities().stop
        if cap:
            return cap
        if s is None:
            return "未选中会话"
        if s.state not in (State.RUNNING, State.THINKING):
            return "无可靠运行目标，不发送停止"
        return None

    def _cancel_target_presses(self, exclude: "_Press | None" = None) -> None:
        for p in self.presses.values():
            if p is not exclude and p.role.role in (Role.STOP, Role.AGENT):
                p.cancelled = True

    def _click(self, kr: KeyRole) -> None:
        r = kr.role
        s = self.mgr.selected_session()
        caps = self.adapter.capabilities()
        if r in (Role.ACCEPT, Role.DECLINE):
            # P0: no approval capability → explain only; no injection, no focus (AC2/AC11)
            self._say(caps.approve or "请在 App 内处理")
        elif r == Role.SEND:
            if caps.send:
                self._say(caps.send)
            elif s is None:
                self._say("未选中会话")
            elif self.ptt_held:
                self._say("PTT 按住中，禁止投递")
            else:
                self._result(self.adapter.send(s))
        elif r == Role.FOCUS:
            if s is None:
                self._say("未选中会话")
            else:
                self._focus(s)
        elif r == Role.NEW:
            if caps.new_session:
                self._say(caps.new_session)
            elif self.ptt_held:
                self._say("PTT 按住中，禁止置前")
            else:
                cwd = s.cwd if (s and s.cwd_confirmed) else None
                self._result(self.adapter.new_session(cwd))
        elif r == Role.SKILL:
            self._skill(kr.index, s, caps)
        elif r == Role.MODE_SELECT and kr.mode is not None:
            self.switch_mode(kr.mode)
        elif r == Role.BACK_L1:
            self.page = "L1"
            self._say("回到 L1", 0.8)

    def _skill(self, idx: int, s: Session | None, caps) -> None:
        skill = self.cfg.skills[idx]
        if caps.skill_current is None:
            if s is None:
                self._say("未选中会话")
                return
            self._result(self.adapter.skill(s, skill.prompt))
            return
        if caps.skill_new_thread is not None:
            self._say(caps.skill_current)
            return
        if self.ptt_held:
            self._say("PTT 按住中，禁止置前")
            return
        self._result(self.adapter.skill(s, skill.prompt))

    def _focus(self, s: Session) -> None:
        if self.ptt_held:
            self._say("PTT 按住中，禁止置前")
            return
        cap = self.adapter.capabilities().focus
        if cap:
            self._say(cap)
            return
        res = self.adapter.focus(s)
        log.info("focus %s (%s): ok=%s %s", s.sid.key(), s.title[:24] if getattr(s, "title", None) else "",
                 res.ok, getattr(res, "message", ""))
        if res.ok and s.state == State.DONE_UNREAD:
            self.local_cleared[s.sid.key()] = s.done_marker
            s.state, s.state_reason = State.IDLE, "已聚焦（本地清提醒）"
        self._result(res)

    # ------------------------------------------------------------------ modes
    def switch_mode(self, m: Mode) -> None:
        for pk, p in self.presses.items():
            if p.role.role != Role.PTT:
                p.cancelled = True  # G34: cancel pending long presses + old eligibility
        self.last_agent_tap = None
        self.mode = m
        self.page = "L1"
        self.scroll = 0
        self.refresh(m)
        ok, reason = self.backend[m]
        n = sum(1 for e in self.mgr.slots if e and not e.lost) + self.mgr.overflow
        name = {"codex_desktop": "Codex 桌面", "herdr": "herdr", "tmux": "tmux"}[m.value]
        self._say(f"已切换到 {name} · {n} 个会话" if ok else f"已切换到 {name} · 不可用：{reason}", SWITCH_TOAST_S)
        self.persist()

    # ------------------------------------------------------------------ view
    def view(self) -> DeckView:
        t = self.clock()
        if self.page == "LS":
            sv = SubView("LS", self.mode, True, "", None, None, None, 0, 0, None, "", self.ptt_held, 0, self.sim)
            return DeckView([], sv, settings=self.settings)
        if self.toast and t >= self.toast[1]:
            self.toast = None
        mgr, caps = self.mgr, self.adapter.capabilities()
        ok, reason = self.backend[self.mode]
        keys: list[KeyView] = []
        attention: tuple[int, int, str] | None = None  # (priority, -updated, key) best
        for row in range(1, 5):
            for col in range(1, 6):
                kr = role_at(self.page, col, row)
                kv = KeyView(col, row, kr.role)
                if kr.role == Role.AGENT:
                    e = mgr.entry(kr.index)
                    if e is None:
                        kv.state = State.OFFLINE if not ok else State.UNASSIGNED
                        kv.label = "离线" if not ok else "空槽"
                    elif e.lost:
                        kv.state, kv.lost, kv.pinned, kv.label = State.UNASSIGNED, True, True, "失联钉扎"
                    else:
                        s = mgr.sessions.get(e.key)
                        kv.state = s.state if s else State.UNKNOWN
                        kv.tool = s.tool if s else None
                        kv.title = s.title if s else ""
                        kv.pinned = e.pinned
                        kv.selected = e.key == mgr.selected
                        if s and s.state in ATTENTION_STATES:
                            cand = (PRIORITY[s.state], -s.updated_ms, f"{col},{row}")
                            if attention is None or cand[:2] > attention[:2]:
                                attention = cand
                elif kr.role in (Role.ACCEPT, Role.DECLINE):
                    kv.label = "接受" if kr.role == Role.ACCEPT else "拒绝"
                    kv.disabled = caps.approve
                elif kr.role == Role.SEND:
                    kv.label, kv.disabled = "发送", caps.send
                elif kr.role == Role.SKILL:
                    kv.label = self.cfg.skills[kr.index].label
                    if caps.skill_current is None:
                        kv.disabled = None
                    elif caps.skill_new_thread is None:
                        kv.badge = "+新"
                    else:
                        kv.disabled = caps.skill_current
                elif kr.role == Role.PTT:
                    kv.label, kv.active = "按住说话", self.ptt_held
                elif kr.role == Role.STOP:
                    kv.label = "停止"
                    kv.disabled = caps.stop
                    p = next((p for p in self.presses.values() if p.role.role == Role.STOP), None)
                    if p and not p.cancelled and not p.long_fired:
                        kv.progress = min(STOP_SEGMENTS, int((t - p.t0) / (LONG_PRESS / STOP_SEGMENTS)))
                elif kr.role == Role.NEW:
                    kv.label, kv.disabled = "新会话", caps.new_session
                elif kr.role == Role.FOCUS:
                    kv.label, kv.disabled = "聚焦", caps.focus
                elif kr.role == Role.MODE:
                    kv.label, kv.mode = self.mode.key_label, self.mode
                elif kr.role == Role.MODE_SELECT:
                    kv.mode, kv.label = kr.mode, kr.mode.key_label
                    kv.active = kr.mode == self.mode
                    mok, mreason = self.backend[kr.mode]
                    kv.disabled = None if mok else mreason
                elif kr.role == Role.BACK_L1:
                    kv.label = "回 L1"
                keys.append(kv)
        if attention:
            c, r = map(int, attention[2].split(","))
            for kv in keys:
                if kv.col == c and kv.row == r:
                    kv.attention = True
        sel = mgr.selected_session()
        lp = self.last_poll_wall.get(self.mode)
        upd = time.strftime("%H:%M:%S", time.localtime(lp)) if lp else "--:--:--"
        pin_info = ""
        if self.page == "L4":
            pinned = sum(1 for e in mgr.slots if e and e.pinned)
            pin_info = f"钉扎 {pinned}/8 · 长按槽位钉扎/解除"
        skill_note = None
        if caps.skill_current is not None and caps.skill_new_thread is None:
            skill_note = "技能将在新线程预填，不进入当前会话"
        sub = SubView(
            page=self.page, mode=self.mode, mode_ok=ok, mode_reason=reason, session=sel,
            toast=self.toast[0] if self.toast else None,
            limited=(self.adapter.status_limited() if ok else f"不可用：{reason}"),
            overflow=mgr.overflow, overflow_attention=mgr.overflow_attention,
            skill_note=skill_note, updated=upd, ptt=self.ptt_held, scroll=self.scroll, sim=self.sim,
            pin_info=pin_info, selection_lost=mgr.selection_lost and sel is None,
        )
        return DeckView(keys, sub)
