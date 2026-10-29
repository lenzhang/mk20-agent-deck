"""Deck controller: pages, key roles, gestures, mode switching, actions.

Pure logic with an injectable clock; it never writes HID output.  The bottom row is
keyboard-side HID (PRD 1.5 L1 layout, deck/docs/PRD-1.5-L1-LAYOUT.md): the firmware
sends PTT (hold = Right Option), DELETE (Backspace), SEND/OK (Return) and NO (Escape)
straight to the Mac's frontmost window.  The deck only renders those keys; it never
acts on their key events (the device does not forward them; stray ones are ignored).
PTT presses that *are* observed only drive the on-screen overlay.

Session slots (PRD 1.5 delta 2026-10-22, "拿起 / 放下"): a tap selects + focuses; a long press
picks the slot up ("移动中").  While picked up, the next slot press places it (other slot → move
there and pin, swapping with an occupant; same slot → toggle the pin) and never focuses.  A K1
press, 8 s without key/knob input, a page/mode switch (K2, MODE, L4 row 1) or a reconnect cancels.

Option mode (user decision 2026-10-22): a tap on L1 that focuses a needs_input / needs_approval session
whose state comes from a Claude/codex hook record (tmux) clears rows 1–3 down to the choice keys 1..N (AskUserQuestion labels from the hook record, the fixed
permission labels, or bare digits 1..5).  The daemon asks the keyboard (``ui.hidKeys``) to make those
keys type the digit into the frontmost window (the session just brought forward; once per press, never
while the Mac PTT is held); presses come back marked (``hid`` / ``ignored``) and only give feedback here.  Exit: the session leaves needs_*, ✗ Esc, a K1
press, a blank key, a page/mode switch, 30 s without input, 3 s after a choice, or a reconnect.

Page sync (2026-10-22): the keyboard owns K2 short (it toggles its L1/L4 route and sends a K2 tap), the L4 回 L1 key
and K1 short on L4 (its own LS page); the deck changes ``page`` itself on a MODE long press, 回 L1 / mode keys and
knob holds.  The controller stays I/O-free: the daemon compares ``page`` / ``page_sync`` with what the keyboard last
confirmed and sends ``ui.page`` (firmware with that capability: ``device_pages``).  Without it (stock KD / legacy
firmware) nothing is sent and K1 short on L4 still opens the deck's own settings page.  With it the keyboard also
decides knob holds itself (1 s, on the device) and forwards K1/K2 only as synthetic taps (down + up): every forwarded
knob down -> up is a short press however late we process the up, and knob holds never fire here (a loop stall
> LONG_PRESS between the two edges must not flip the page).  Legacy firmware keeps the timing (hold = L4 / L1).

K1 scroll-back (user decision 2026-10-25; K1 no longer walks the selection): rotating K1 scrolls the selected
session's conversation history (ccw = older, cw = newer; SCROLL_LINES per detent, up to SCROLL_MAX_MULT times that
when detents come fast), a K1 press returns it to the latest content.  Rotations are only summed here; tick() sends
the sum (one adapter call per loop step).  tmux scrolls its own pane (copy mode); Codex Desktop brings the thread
forward with the normal focus path first and lets the Accessibility helper scroll its window (scrollhelper.py).
The sub-screen says 「回看中 · 按 K1 回到最新」 while scrolled back.  The state ends on a K1 press, another selection or
mode, a slot tap, an observed PTT press, the session vanishing, or SCROLL_IDLE_S without K1 rotation (tmux: the
pane leaves copy mode; Desktop: only the banner goes, the deck never pulls Codex forward by itself).  In option mode
K1 rotation does nothing (digits must reach the prompt, not copy mode); a K1 press there still leaves option mode,
and with a slot picked up it still cancels the move.
"""
from __future__ import annotations

import enum
import logging
import time
from dataclasses import dataclass, field
from typing import Callable

from . import protocol as P
from .adapters.base import BackendUnavailable, ModeAdapter
from .config import DeckConfig
from .model import (ATTENTION_STATES, MODE_CYCLE, PRIORITY, ActionResult, Mode, Session, SessionId, State, Tool)
from .settings import SettingsPage
from .slots import N_SLOTS, SLOT_COLS, SlotManager

log = logging.getLogger(__name__)

# PRD §6.5 status (2026-10-18): path A (VIA KC_RALT) has no effect in host mode (keys are
# routed GD32 → T113, the GD32 keymap does not emit HID); path B (synthesized Right
# Option) is on hold by user decision.  PTT therefore only shows this hint.
PTT_UNAVAILABLE = "PTT 不可用，请使用 Mac 原语音键"

LONG_PRESS = 0.600   # D14
MOVE_TIMEOUT = 8.0   # a picked-up slot is dropped back after this long without key/knob input
DOUBLE_TAP = 0.350   # devtest timing reference only: double-tap focus was removed (single tap focuses)
TOAST_S = 1.5
SWITCH_TOAST_S = 1.2
# Option mode (2026-10-22)
PROMPT_STATES = frozenset({State.NEEDS_INPUT, State.NEEDS_APPROVAL})
OPTION_TIMEOUT = 30.0   # back to the session grid after this long without key/knob input
OPTION_CHOSEN_S = 3.0   # after a digit / Return was typed: stay this long unless the state changes first
OPTION_DIGITS = 5       # unknown prompt: bare digits 1..5
OPTION_MAX = 5          # row 1 only
PERMISSION_LABELS = ("是", "是，且不再问", "否")  # Claude Code / codex tool permission menus
OPTION_LEGACY = "固件不支持数字键，请在 Mac 上按数字"
OPTION_PENDING = "数字键暂未生效（键盘未确认），请在 Mac 上按数字"
OPTION_PTT = "先松开按住说话"  # the keyboard ignores digits while the Mac PTT (Right Option) is held
# K1 short reached the deck on L4 although the keyboard handles it there itself: its route is L1 -> re-send L4.
PAGE_RESYNC = "键盘页面已重新同步到 L4 · 再按 K1 进入键盘设置"
# GD32 key MCU stuck in its Maple DFU bootloader (hw/gd32watch, incident 2026-10-25 14:08): the screens and the host
# link work, the keys and knobs do not.  A persistent red banner on the L1/L4 sub-screen while the Mac sees it.
GD32_DFU_ALERT = "按键控制器停在升级模式：请重新插拔键盘 USB 线"
GD32_DFU_DETAIL = "按键和旋钮（含 K2 急停）现在都不起作用"
GD32_DFU_WEB = "键盘的按键控制器（GD32）停在升级模式（Maple DFU）：按键和旋钮（含 K2 急停）都不起作用，请重新插拔键盘 USB 线"
# K1 scroll-back (2026-10-25)
SCROLL_LINES = 3            # lines per K1 detent ...
SCROLL_FAST_S = 0.12        # ... detents closer than this in the same direction accelerate ...
SCROLL_MAX_MULT = 5         # ... up to 5x (every second fast detent adds one step)
SCROLL_IDLE_S = 120.0       # scroll-back ends after this long without K1 rotation
SCROLL_FOCUS_SETTLE_S = 0.35  # Desktop: the thread was just brought forward; its first scroll waits this long
SCROLL_SETUP_QUIET_S = 20.0   # after "helper missing / not allowed": no helper call, no new hint for this long
SCROLL_NOTE_S = 8.0           # ... and the setup hint stays on the sub-screen this long
SCROLL_HINT = "回看中 · 按 K1 回到最新"
SCROLL_LIVE = "已回到最新"
SCROLL_IDLE_LIVE = "回看 2 分钟无操作 · 已回到最新"
SCROLL_LATEST = "已是最新"
SCROLL_PTT = "按住说话中，暂不回看"
SCROLL_OPTION = "选项中不回看 · 按 K1 返回会话列表"
K1_NO_SESSION = "未选中会话 · 单击会话键选择"
# Long-press approve (2026-10-29): in option mode a permission prompt's 「是」/「是，且不再问」 keys answer only after
# being held ``approve_hold_s`` (DeckConfig, default 1 s; 0 = off: the keyboard types the digit on press as before).
# The Mac then types the digit into the session's own pane (adapter.answer: tmux send-keys -t <pane>) only after the
# threshold has been observed and the matching key-up has arrived. Requiring the up edge is deliberate: the keyboard
# has no held-key heartbeat, so a lost physical key-up must fail closed rather than turn a short press into approval.
# 「否」, ✗ Esc and Return stay immediate (the keyboard types them). Never on a timeout, reconnect / wake, a changed
# prompt, another page / mode or with the PTT held.
APPROVE_HOLD_S = 1.0
APPROVE_FLASH_S = 0.6  # the key flashes this long after the answer went out (and the Mac plays a short sound)
APPROVE_STALE = "未批准：提示已变化"
APPROVE_PTT = "按住说话中，未批准"
APPROVE_READY = "已按住足够时间 · 松开批准"
# There is no held-key heartbeat from the keyboard (firmware unchanged), so the hold must be *observed*: the loop must
# see it continuously (the daemon wakes every LONG_PRESS/8 = 75 ms while it is pending; a larger gap = a stalled
# loop), the keyboard must have acked a frame recently (link alive: it renders the progress ring at 8 fps), and no
# other input may arrive during the hold. The final action waits for the physical key-up; a missing up therefore
# fails closed. Any other doubt cancels; never approves.
APPROVE_MAX_GAP_S = 0.5
APPROVE_LINK_S = 0.6
APPROVE_LATE = "未批准：响应延迟，请重新按住"
APPROVE_NO_LINK = "未批准：键盘连接无响应"
APPROVE_OTHER = "未批准：按住期间有其他输入"
APPROVE_EDGE = "未批准：按键状态不确定，请重新按住"
# SNAP (2026-10-29): long press of the MODE key (fb20) while L4 is shown = screenshot of the Mac screen to the
# clipboard (macOS screencapture; the daemon runs it, see effects.py).  On L1 the long press still opens L4.
SNAP_FLASH_S = 0.6
SNAP_BUSY = "截图中…"
MODE_SHORT = {Mode.DESKTOP: "桌面", Mode.HERDR: "herdr", Mode.TMUX: "tmux"}


class Role(str, enum.Enum):
    AGENT = "agent"
    PTT = "ptt"            # device HID: hold = Right Option (IME dictation)
    DELETE = "delete"      # device HID: Backspace, hold repeats
    OK = "ok"              # device HID: Return — 发送 / 可以
    NO = "no"              # device HID: Escape — 不行 / 打断 Claude、codex
    NEW = "new"
    MODE = "mode"
    MODE_SELECT = "mode_select"
    BACK_L1 = "back_l1"


# Rendered only: the keyboard firmware emits these as USB HID; key events for them are ignored.
DEVICE_HID_ROLES = frozenset({Role.DELETE, Role.OK, Role.NO})
BOTTOM_ROW = (Role.PTT, Role.DELETE, Role.OK, Role.NO, Role.MODE)  # row 4 on L1 and L4
# Logical key numbers (fb = (row - 1) * 5 + col) the firmware must treat as device-side HID on L1/L4.
DEVICE_HID_FB = {16: "PTT → Right Option (hold)", 17: "DELETE → Backspace (hold repeats)",
                 18: "SEND/OK → Return", 19: "NO → Escape"}
MODE_KEY_FB = 20  # forwarded to the Mac on L1/L4 while the keyboard agent is inactive
L4_TOP = (Role.MODE_SELECT, Role.MODE_SELECT, Role.MODE_SELECT, Role.NEW, Role.BACK_L1)
# Key face text for the device-HID keys: (label, small second line naming what the keyboard sends).
HID_FACES = {Role.DELETE: ("删除", "按住连删"), Role.OK: ("发送 / 可以", "Return"),
             Role.NO: ("不行 Esc", "可打断 Claude/codex")}


@dataclass(frozen=True)
class KeyRole:
    role: Role
    index: int = 0          # agent slot 0..14
    mode: Mode | None = None  # for MODE_SELECT


def slot_pos(index: int) -> tuple[int, int]:
    """Slot index → 1-based document (col, row): S1..S5 row 1, S6..S10 row 2, S11..S15 row 3."""
    return index % SLOT_COLS + 1, index // SLOT_COLS + 1


def role_at(page: str, col: int, row: int) -> KeyRole | None:
    """1-based document coordinates (PRD 1.5 L1/L4 tables, deck/docs/PRD-1.5-L1-LAYOUT.md).

    L1: rows 1-3 = slots S1..S15; row 4 = PTT, DELETE, SEND/OK, NO, MODE.
    L4: row 1 = DESK, HERD, TMUX, ＋新会话, 回 L1; rows 2-3 = S6..S15 (same keys as on L1); row 4 as L1.
    """
    if not (1 <= col <= 5 and 1 <= row <= 4):
        return None
    if row == 4:
        return KeyRole(BOTTOM_ROW[col - 1])
    if page == "L4" and row == 1:
        r = L4_TOP[col - 1]
        return KeyRole(r, mode=MODE_CYCLE[col - 1]) if r == Role.MODE_SELECT else KeyRole(r)
    return KeyRole(Role.AGENT, (row - 1) * SLOT_COLS + (col - 1))


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
    sublabel: str = ""  # small second line (device-HID keys: what the keyboard sends)
    active: bool = False  # PTT held / current mode in L4
    mode: Mode | None = None
    attention: bool = False
    badge_alert: bool = False  # badge is an attention count (other modes)
    moving: bool = False  # this slot is picked up (pick-up-and-place gesture)
    hold: float = 0.0  # long-press approve in progress: 0..1 of approve_hold_s (progress ring on the key)
    flash: bool = False  # just answered / screenshot taken: the key flashes for a moment
    option: int = 0  # option mode: this key answers choice N (1-based); label = the option text ("" = digit only)
    blank: bool = False  # option mode: rows 1–3 beyond the choices (dark; a press leaves option mode)


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
    note: str | None  # persistent hint line, e.g. why a tap only selects (focus unsupported)
    updated: str
    ptt: bool
    scroll: int
    sim: bool = False
    pin_info: str = ""
    selection_lost: bool = False
    other_modes: str = ""  # e.g. "tmux 2 待处理" (background poll of non-current modes)
    moving: str = ""  # instruction line while a slot is picked up
    option: str = ""  # option mode instruction line ("选项：按 1–N 选择 · × 取消 · K1 返回")
    option_note: str = ""  # option mode: why digits may not work (legacy firmware / not accepted yet)
    scrollback: str = ""  # K1 scroll-back banner (「回看中 · 按 K1 回到最新」) or the helper setup hint
    scrollback_warn: bool = False  # the setup hint (another colour)
    alert: str = ""  # persistent hardware banner (GD32 stuck in its DFU bootloader); "" = none
    alert_detail: str = ""  # its second line


@dataclass
class ModeSummary:
    """Background view of a non-current mode: only availability + attention counts."""
    ok: bool
    reason: str = ""
    attention: int = 0  # needs_approval / needs_input / error / done_unread (local clears respected)
    urgent: int = 0     # needs_approval / needs_input / error
    t: float = 0.0


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
    long_fired: bool = False
    cancelled: bool = False
    approve: int = 0  # option mode long-press approve: the digit this press answers once held (0 = not one)
    approve_done: bool = False  # answered, refused or released: never fires (again)
    approve_ready: bool = False  # observed the threshold; waits for the matching key-up before answering


@dataclass
class _Move:
    """A picked-up slot (long press) waiting for the placing press."""
    src: int
    key: str
    title: str
    mode: Mode
    last: float  # last key/knob input; MOVE_TIMEOUT after it the pick-up is cancelled


@dataclass
class _Option:
    """Option mode: the numbered choice of one needs_* session, answered with the digit keys."""
    key: str
    mode: Mode
    title: str
    labels: tuple[str, ...]  # one per choice 1..N; "" = digit only
    kind: str  # "question" (AskUserQuestion labels) | "permission" | "digits" (unknown)
    last: float  # last key/knob input (OPTION_TIMEOUT)
    chosen: tuple[str, float] | None = None  # (what was typed, when): OPTION_CHOSEN_S grace


@dataclass
class _Scroll:
    """K1 scroll-back of one session (2026-10-25)."""
    key: str
    mode: Mode
    native: str   # sid.native / sid.generation: the pane can outlive our session list (hook record expired)
    generation: str
    last: float  # last K1 rotation (SCROLL_IDLE_S, acceleration)
    ready: float  # nothing is sent before this (Desktop: the thread was just brought forward)
    pending: int = 0  # lines not sent yet (> 0 = older)
    bottom: bool = False  # K1 press: back to the latest content at the next send
    active: bool = False  # something was scrolled: the banner shows, leaving puts the view back where possible
    dir: int = 0
    streak: int = 0  # consecutive fast detents in ``dir``


def option_prompt(s: Session) -> bool:
    """Only hook-reported prompts (tmux Claude/codex) are answered with digit keys; Desktop/herdr never are."""
    return s.hooked and s.state in PROMPT_STATES


def option_labels(s: Session) -> tuple[tuple[str, ...], str]:
    """Choices for a session's pending prompt: hook labels, the permission menu, or bare digits 1..5."""
    if s.options:
        return tuple(str(x) for x in s.options[:OPTION_MAX]), "question"
    if s.prompt_kind == "permission":
        return PERMISSION_LABELS, "permission"
    return ("",) * OPTION_DIGITS, "digits"


def _short(title: str, n: int = 6) -> str:
    return title if len(title) <= n else title[:n] + "…"


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
        self.device_pages = False  # daemon: the keyboard has ui.page (follows our L1/L4, owns K1 on L4)
        self.page_sync = 0  # +1 when the keyboard is known to show another route: the daemon re-sends ui.page
        self.managers: dict[Mode, SlotManager] = {
            m: SlotManager.from_dict((state.get("modes") or {}).get(m.value, {})) for m in MODE_CYCLE}
        self.backend: dict[Mode, tuple[bool, str]] = {m: (True, "") for m in MODE_CYCLE}
        self.local_cleared: dict[str, str] = {}
        self.last_poll_wall: dict[Mode, float] = {}
        self.bg: dict[Mode, ModeSummary] = {}  # non-current modes, from Daemon background polls
        self.toast: tuple[str, float] | None = None
        self.ptt_held = False
        self.ptt_observed = False
        self.presses: dict[tuple, _Press] = {}
        self.moving: _Move | None = None
        self.option: _Option | None = None
        self.option_hid = ""  # daemon: "ok" keyboard types the digits, "legacy" no ui.hidKeys, "pending" not (yet) accepted
        self.scroll = 0
        self.version = 0  # bumps whenever the view may have changed
        self.log: list[str] = []
        self.action_count = 0
        self.settings = settings or SettingsPage()
        self.rules_warning = ""  # slot-rules.json problem (set by webui.ArrangeService), shown on the sub-screen
        self.gd32_dfu = False  # the Mac sees the GD32 in its DFU bootloader (daemon, from hw/gd32watch): red banner
        self.scrolling: _Scroll | None = None  # K1 scroll-back of the selected session
        self.scroll_note: tuple[str, float] | None = None  # (setup hint, until)
        self._scroll_quiet: dict[Mode, float] = {}  # mode -> clock until which K1 scroll does nothing (setup hint)
        self.flash: dict[tuple[int, int], float] = {}  # (col, row) -> clock until which that key flashes
        # Side effects the daemon runs off the controller (I/O-free here): ("sound", "approve"), ("snap",).
        self.effects: list[tuple] = []
        self._approve_seen: float | None = None  # clock of the last tick (long-press approve continuity)
        self.link_ack: float | None = None  # daemon: clock of the keyboard's last frame ack (None = no link: sim)

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

    def _result(self, res: ActionResult, pre: str = "") -> ActionResult:
        self.action_count += 1  # adapter (agent) action attempted — key trace outcome
        self._say(pre + res.message)
        return res

    def persist(self) -> dict:
        st = {"mode": self.mode.value, "modes": {m.value: self.managers[m].to_dict() for m in MODE_CYCLE}}
        if self.on_state_change:
            self.on_state_change(st)
        return st

    # ------------------------------------------------------------------ pick up / place (2026-10-22)
    def _drop_move(self) -> str:
        """Forget a picked-up slot without a toast; returns the prefix for the caller's own toast."""
        if self.moving is None:
            return ""
        self.moving = None
        self._touch()
        return "已取消移动 · "

    def _cancel_move(self, then: str = "") -> bool:
        """Cancel a pick-up (the arrangement does not change); the toast says so, then ``then``."""
        pre = self._drop_move()
        if pre:
            self._say(pre + then if then else "已取消移动")
        return bool(pre)

    def _goto(self, page: str, toast: str) -> None:
        """Page switch (K2, K1/K2/MODE hold, 回 L1): also cancels a pick-up and leaves option mode."""
        self.page = page
        self._drop_option()
        if not self._cancel_move(toast):
            self._say(toast, 0.8)

    def set_gd32_dfu(self, on: bool) -> None:
        """Daemon (loop thread): the GD32 DFU state confirmed by hw/gd32watch; a new frame only on a change."""
        on = bool(on)
        if on != self.gd32_dfu:
            self.gd32_dfu = on
            self._touch()

    def request_page_sync(self) -> None:
        """The keyboard's route is known to differ from ``page``: the daemon sends ui.page again, even if acked."""
        self.page_sync += 1
        self._touch()

    def long_press_pending(self) -> bool:
        """A press that can still reach LONG_PRESS (the daemon wakes up for it); knob presses only on legacy firmware.
        A long-press approve in progress counts too (its threshold and the progress ring)."""
        return any((not (p.long_fired or p.cancelled) and (pk[0] != "knob" or not self.device_pages))
                   or (p.approve and not p.approve_done) for pk, p in self.presses.items())

    def drop_gestures(self) -> None:
        """Reconnect / wake: presses in flight, a picked-up slot, option mode and unsent K1 scrolls are never
        replayed (a scroll-back that already happened stays until K1 / its idle timeout)."""
        self.presses.clear()
        self._cancel_move()
        self._drop_option()
        self.option_hid = ""
        sc = self.scrolling
        if sc is not None:
            sc.pending, sc.bottom = 0, False
            if not sc.active:
                self.scrolling = None

    def move_deadline(self) -> float | None:
        """Clock time at which the pick-up times out (the daemon wakes up for it)."""
        return None if self.moving is None else self.moving.last + MOVE_TIMEOUT

    def _pick_up(self, idx: int) -> None:
        mgr = self.mgr
        e = mgr.entry(idx)
        if e is None:
            self._say("空槽不可移动")
            return
        s = mgr.sessions.get(e.key)
        title = "失联钉扎" if (e.lost or s is None) else (s.title or "?")
        self.moving = _Move(idx, e.key, title, self.mode, self.clock())
        self._say(f"已拿起 S{idx + 1}", 1.0)

    def _place(self, dst: int) -> None:
        """The press after a pick-up: move / swap + pin, or toggle the pin on the same slot.  Never focuses."""
        mv, self.moving = self.moving, None
        mgr = self.mgr
        e = mgr.entry(mv.src)
        if mv.mode != self.mode or e is None or e.key != mv.key:
            self._say("已取消移动：会话已不在原槽")
            return
        msg = mgr.move(mv.src, dst)
        self.persist()
        self._say(msg)

    # ------------------------------------------------------------------ option mode (2026-10-22)
    def _enter_option(self, s: Session) -> None:
        labels, kind = option_labels(s)
        self.option = _Option(s.sid.key(), self.mode, s.title or "?", labels, kind, self.clock())
        self._touch()

    def _drop_option(self) -> bool:
        if self.option is None:
            return False
        self.option = None
        for p in self.presses.values():
            if p.approve:
                p.approve_done = True  # leaving option mode never lets a held approve key fire later
        self._touch()
        return True

    def _exit_option(self, toast: str = "") -> None:
        if self._drop_option() and toast:
            self._say(toast)

    def option_deadline(self) -> float | None:
        """Clock time of the next option-mode timeout (the daemon wakes up for it)."""
        op = self.option
        if op is None:
            return None
        d = op.last + OPTION_TIMEOUT
        return min(d, op.chosen[1] + OPTION_CHOSEN_S) if op.chosen else d

    def hid_keys_wanted(self) -> dict[int, int]:
        """Logical fb -> digit the keyboard should type while option mode is shown ({} = none).  The long-press
        approve keys are left out: the keyboard forwards them as plain key presses and the Mac answers after the
        observed hold and matching key-up."""
        op = self.option
        if op is None or self.page != "L1" or op.mode != self.mode:
            return {}
        hold = self._approve_keys(op)
        return {i + 1: i + 1 for i in range(len(op.labels)) if i not in hold}  # row 1: fb1..fbN = S1..SN

    # ------------------------------------------------------------------ long-press approve (2026-10-29)
    def approve_hold(self) -> float:
        try:
            return max(0.0, float(getattr(self.cfg, "approve_hold_s", APPROVE_HOLD_S)))
        except (TypeError, ValueError):
            return APPROVE_HOLD_S

    def _approve_keys(self, op: "_Option") -> frozenset:
        """Slot indices (row 1) of the option keys that approve only after a hold: 「是」 and 「是，且不再问」 of a
        permission prompt, when the hold is on and the mode's adapter can type the digit into the session itself."""
        if op.kind != "permission" or self.approve_hold() <= 0:
            return frozenset()
        if self.adapters[op.mode].capabilities().approve is not None:
            return frozenset()  # e.g. Desktop / herdr (no option mode anyway): the keyboard types as before
        return frozenset(i for i, lab in enumerate(op.labels[:2]) if lab in PERMISSION_LABELS[:2])

    def note_link_ack(self) -> None:
        """Daemon: the keyboard acknowledged a frame (the link is alive)."""
        self.link_ack = self.clock()

    def _approve_pending(self) -> bool:
        return any(p.approve and not p.approve_done for p in self.presses.values())

    def _approve_cancel(self, msg: str) -> None:
        hit = False
        for p in self.presses.values():
            if p.approve and not p.approve_done:
                p.approve_done = hit = True
        if hit:
            log.info("approve cancelled: %s", msg)
            self._say(msg)
            self._touch()

    def _approve_link_ok(self, t: float) -> bool:
        """Only a live device link may complete an approval; the simulator has no frame ACKs."""
        return self.sim or (self.link_ack is not None and t - self.link_ack <= APPROVE_LINK_S)

    def _approve_tick(self, t: float) -> None:
        prev, self._approve_seen = self._approve_seen, t
        hold = self.approve_hold()
        for pk, p in list(self.presses.items()):
            if not p.approve or p.approve_done:
                continue
            if t - max(p.t0, prev if prev is not None else p.t0) > APPROVE_MAX_GAP_S:
                p.approve_done = True  # the loop did not watch the hold (stall): never fire from a stale press
                self._say(APPROVE_LATE)
                self._touch()
                continue
            if not self._approve_link_ok(t):
                p.approve_done = True
                self._say(APPROVE_NO_LINK)
                self._touch()
                continue
            if t - p.t0 >= hold and not p.approve_ready:
                p.approve_ready = True
                self._say(APPROVE_READY)
                self._touch()

    def _fire_approve(self, pk: tuple, p: _Press, t: float) -> None:
        """The approve key was held and released: type its digit only if nothing changed."""
        if not self._approve_link_ok(t):
            p.approve_done = True
            self._say(APPROVE_NO_LINK)
            self._touch()
            return
        p.approve_done = True
        self._touch()
        op = self.option
        s = self.managers[op.mode].sessions.get(op.key) if op is not None else None
        if (op is None or s is None or op.key != p.target or op.mode != p.mode or op.mode != self.mode
                or self.page != "L1" or not option_prompt(s) or option_labels(s)[1] != "permission"
                or (p.approve - 1) not in self._approve_keys(op)):
            self._say(APPROVE_STALE)
            return
        if self.ptt_held:
            self._say(APPROVE_PTT)
            return
        res = self.adapters[op.mode].answer(s, p.approve)
        self.action_count += 1
        log.info("approve %s digit %d: ok=%s %s", op.key, p.approve, res.ok, res.message)
        if not res.ok:
            self._say(f"未批准：{res.message}", 3.0)
            return
        op.chosen, op.last = (str(p.approve), t), t
        self.flash[(pk[1], pk[2])] = t + APPROVE_FLASH_S
        self.effects.append(("sound", "approve"))
        self._say(f"已批准：{p.approve} {op.labels[p.approve - 1]}")

    def set_option_hid(self, status: str) -> None:
        if status != self.option_hid:
            self.option_hid = status
            self._touch()

    def _option_tick(self, t: float) -> None:
        op = self.option
        if op is None:
            return
        if self.page != "L1" or op.mode != self.mode:
            self._drop_option()
            return
        s = self.managers[op.mode].sessions.get(op.key)
        if s is None or not option_prompt(s):
            self._exit_option("已作答 · 回到会话列表" if op.chosen else "会话已不在等待选择 · 回到会话列表")
        elif op.chosen and t - op.chosen[1] >= OPTION_CHOSEN_S:
            self._exit_option("回到会话列表")
        elif t - op.last >= OPTION_TIMEOUT:
            self._exit_option("选项 30 s 无操作 · 回到会话列表")
        elif not op.chosen and option_labels(s) != (op.labels, op.kind):
            op.labels, op.kind = option_labels(s)  # e.g. the hook record got the labels a moment later
            self._touch()

    def _option_hid_key(self, kr: KeyRole, hid: int, t: float) -> None:
        """A press the keyboard itself typed on the Mac (marked ``hid``): feedback only, never a deck action."""
        digit = next((d for d, u in P.HID_DIGIT.items() if u == hid), None)
        op = self.option
        if op is None:  # the map outlived option mode by a moment: the key is typed, nothing else happens
            if digit is not None:
                self._say(f"已发送数字 {digit}")
            return
        op.last = t
        if hid == P.HID_ESCAPE:
            self._exit_option("已取消选择 (Esc)")
        elif hid == P.HID_RETURN:
            op.chosen = ("Return", t)
            self._say("已确认 (Return)")
        elif digit is not None:
            op.chosen = (str(digit), t)
            self._say(f"已选 {digit}")
        self._touch()

    def _option_key(self, kr: KeyRole, p: _Press, t: float) -> None:
        """An unmarked rows 1–3 press while option mode is shown (never selects, focuses or picks up)."""
        op = self.option
        p.cancelled = True
        op.last = t
        if kr.index >= len(op.labels):
            self._exit_option("回到会话列表")
            return
        n = kr.index + 1
        if kr.index in self._approve_keys(op):
            p.approve, p.target = n, op.key  # _approve_tick observes the hold; the matching release submits it
            if any(q is not p for q in self.presses.values()):
                p.approve_done = True  # another key / knob is held too: a lone, observed hold only
                self._say(APPROVE_OTHER)
                return
            hold = self.approve_hold()
            self._say(f"按住 {hold:g} 秒批准「{op.labels[kr.index]}」· 松开取消", hold + 1.0)
            return
        self._say(OPTION_LEGACY if self.option_hid == "legacy" else f"数字键未生效，请在 Mac 上按 {n}")

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
            self._option_tick(self.clock())
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
        sc = self.scrolling
        if sc is not None and sc.mode == mode and mgr.selected != sc.key:
            self._end_scroll()  # the scrolled session vanished
        mv = self.moving
        if mv is not None and mv.mode == mode and (mgr.entry(mv.src) is None or mgr.entry(mv.src).key != mv.key):
            self._drop_move()
            self._say("已取消移动：会话已消失")
        if (mgr.mapping_version, self.backend[mode]) != before:
            self.persist()
        self._option_tick(self.clock())  # answered / gone: leave option mode with the fresh state
        self._touch()

    def _summarise(self, sessions) -> tuple[int, int]:
        att = urg = 0
        for s in sessions:
            st = s.state
            if st == State.DONE_UNREAD and self.local_cleared.get(s.sid.key()) == s.done_marker:
                continue  # focused here earlier: the reminder stays cleared
            if st in ATTENTION_STATES:
                att += 1
                urg += st != State.DONE_UNREAD
        return att, urg

    def apply_background(self, mode: Mode, sessions=None, error: str = "", t: float | None = None) -> None:
        """Result of a background poll of a non-current mode (called on the loop thread).
        ``t`` = wall time the poll started; results older than what we already know are dropped."""
        if mode == self.mode:
            return  # the current mode is owned by refresh(); a late result must not override it
        t = self.wall() if t is None else t
        old = self.bg.get(mode)
        if old is not None and t < old.t:
            return  # e.g. polled before we left that mode; the switch snapshot is newer
        if sessions is None:
            new = ModeSummary(False, error or "不可用", t=t)
        else:
            excluded = self.managers[mode].excluded
            att, urg = self._summarise(s for s in sessions if s.sid.key() not in excluded)
            new = ModeSummary(True, "", att, urg, t)
        self.bg[mode] = new
        if old is None or (old.ok, old.reason, old.attention, old.urgent) != (new.ok, new.reason, new.attention,
                                                                               new.urgent):
            self._touch()

    def other_attention(self) -> list[tuple[Mode, int]]:
        return [(m, self.bg[m].attention) for m in MODE_CYCLE
                if m != self.mode and m in self.bg and self.bg[m].ok and self.bg[m].attention > 0]

    # ------------------------------------------------------------------ input
    # ------------------------------------------------------------------ settings page (LS)
    def open_settings(self) -> None:
        self.presses.clear()
        self._drop_move()
        self._drop_option()
        self.settings.open()
        self.page = "LS"
        self._touch()

    def _settings_after(self) -> None:
        st = self.settings
        if not st.active:
            self.page = st.exit_to or "L1"
        self._touch()

    def on_key(self, col: int, row: int, pressed: bool, hid: int | None = None, ignored: str = "") -> None:
        """1-based doc coordinates.  ``hid``: the keyboard typed this press itself (option mode);
        ``ignored="ptt"``: the keyboard ignored this digit press because the Mac PTT is held."""
        if self._approve_pending() and any(p.approve and not p.approve_done and k != ("key", col, row)
                                           for k, p in self.presses.items()):
            self._approve_cancel(APPROVE_OTHER)  # anything else during a long-press approve cancels it
        if self.page == "LS":
            if pressed:
                self.settings.key(col, row)
            self._settings_after()
            return
        t = self.clock()
        kr = role_at(self.page, col, row)
        if kr is None:
            return
        if ignored:
            if pressed:  # nothing was typed; its unmarked release finds no press here
                if self.option is not None:
                    self.option.last = t
                self._say(OPTION_PTT)
            return
        if hid is not None:
            if pressed:  # its release carries no mark and finds no press here: nothing happens
                self._option_hid_key(kr, hid, t)
            return
        if kr.role in DEVICE_HID_ROLES:
            # DELETE / SEND-OK / NO are keyboard-side HID (Backspace / Return / Escape to the frontmost
            # window).  The device does not forward them; if one arrives anyway, never act on it.
            log.debug("ignored device-HID key %s %s", kr.role.value, "down" if pressed else "up")
            return
        if self.moving is not None and kr.role != Role.PTT:
            self.moving.last = t  # any Mac key event restarts the pick-up timeout (PTT is device-side)
        pk = ("key", col, row)
        if pressed:
            if pk in self.presses:  # duplicate down (bounce or a new press after a lost up)
                duplicate = self.presses[pk]
                if duplicate.approve and not duplicate.approve_done:
                    # Edge-only device reports cannot tell bounce from a new physical press.  Keeping the old
                    # press would let a later up answer a prompt that was no longer continuously held.
                    duplicate.approve_done = True
                    self._say(APPROVE_EDGE)
                return
            self.presses[pk] = p = _Press(kr, t, self.mode)
            if self.option is not None and kr.role == Role.AGENT and self.page == "L1":
                self._option_key(kr, p, t)
            elif self.moving is not None and kr.role == Role.AGENT:
                p.cancelled = True  # the placing press: no select, no focus on release, no long press
                self._place(kr.index)
            else:
                self._on_down(kr, p, t)
        else:
            p = self.presses.pop(pk, None)
            if p is None:
                return
            if p.approve and not p.approve_done:
                p.cancelled = True  # an approve release must never fall through to the normal agent tap path
                if p.approve_ready and t - p.t0 >= self.approve_hold():
                    self._fire_approve(pk, p, t)
                else:
                    p.approve_done = True  # released before the hold: a preview only, nothing is typed
                    self._say(f"未批准：需按住 {self.approve_hold():g} 秒")
            self._on_up(p.role, p, t)  # the press-time role owns the release (page may have changed)
        self._touch()

    def on_knob(self, knob: int, kind: str, target: str | None = None, t: float | None = None) -> None:
        """``target`` (daemon, ui.page firmware): for a K2 tap from the keyboard whose resulting page is known
        ("L1"/"L4"), go there instead of toggling — the keyboard already shows it (no pull-back if our page moved
        meanwhile, e.g. a K2 on the keyboard while it was PROBING).  ``t`` = the event time (the daemon passes
        ``InputEvent.t``, when the keyboard sent it): a slow loop step (a background poll can take seconds) must
        not make queued slow detents look fast for the scroll acceleration."""
        self._approve_cancel(APPROVE_OTHER)
        if self.page == "LS":
            if kind in ("cw", "ccw"):
                self.settings.knob(knob, kind)
            elif kind == "up":
                self.settings.knob(knob, "press")
            self._settings_after()
            return
        t = self.clock() if t is None else t
        pk = ("knob", knob)
        if self.moving is not None:
            self.moving.last = t
        if self.option is not None:
            self.option.last = t
        if kind == "down":
            self.presses[pk] = p = _Press(KeyRole(Role.MODE), t, self.mode)
            if knob == 1 and self._cancel_move():
                p.cancelled = True  # K1 press = cancel the pick-up only (no focus, no L4 on hold)
            elif knob == 1 and self.option is not None:
                p.cancelled = True  # K1 press = leave option mode only (no scroll, no L4 on hold)
                self._exit_option("回到会话列表")
        elif kind == "up":
            p = self.presses.pop(pk, None)
            # ui.page firmware forwards knob taps only (it decides holds itself): never judged by our timing
            if p and not p.long_fired and not p.cancelled and (self.device_pages or t - p.t0 < LONG_PRESS):
                if knob == 1 and self.page == "L4":
                    if self.device_pages:
                        # On its L4 route the keyboard handles K1 itself (its LS page; the web page is the settings
                        # entry), so this K1 came from its L1 route while we show L4: make it follow us again.
                        self.request_page_sync()
                        self._say(PAGE_RESYNC)
                    else:
                        self.open_settings()  # legacy firmware only (SETTINGS.md): short K1 on L4 opens settings
                    return
                if knob == 1:
                    self._k1_press(t)  # K1 press = back to the latest content of the selected session
                else:
                    page = target if knob == 2 and target in P.PAGES else "L4" if self.page == "L1" else "L1"
                    if page != self.page:
                        self._goto(page, f"切到 {page}")
        elif kind in ("cw", "ccw"):
            if knob == 1:
                self._k1_rotate(1 if kind == "ccw" else -1, t)  # ccw = older (up), cw = newer (down)
            else:
                self.scroll = max(0, self.scroll + (1 if kind == "cw" else -1))
        self._touch()

    def tick(self) -> None:
        """Fire long-press thresholds (independent of rendering)."""
        t = self.clock()
        if self.moving is not None and t - self.moving.last >= MOVE_TIMEOUT:
            self._cancel_move()  # 8 s without key/knob input: nothing changes
        self._option_tick(t)
        self._approve_tick(t)
        self._scroll_tick(t)
        for pk, p in list(self.presses.items()):
            if p.long_fired or p.cancelled or t - p.t0 < LONG_PRESS or (pk[0] == "knob" and self.device_pages):
                continue  # knob holds: legacy firmware only (ui.page firmware sends taps; its up decides)
            p.long_fired = True
            if pk[0] == "knob":
                if pk[1] == 1:
                    self._goto("L4", "L4 模式/设置")
                else:
                    self._goto("L1", "回到 L1")
                continue
            self._on_long(p.role, p)
            self._touch()

    # ------------------------------------------------------------------ K1 scroll-back (2026-10-25)
    def scroll_deadline(self) -> float | None:
        """Clock time of the next scroll-back step (a queued send, the idle timeout); the daemon wakes up for it."""
        sc = self.scrolling
        if sc is None:
            return None
        return sc.ready if (sc.pending or sc.bottom) else sc.last + SCROLL_IDLE_S

    def _k1_session(self) -> Session | None:
        """The session K1 scrolls (the selection), or None after a toast saying why not."""
        s = self.mgr.selected_session()
        if s is None:
            self._say(K1_NO_SESSION)
            return None
        cap = self.adapter.capabilities().scroll
        if cap:
            self._say(cap)
            return None
        if self.ptt_held:
            self._say(SCROLL_PTT)
            return None
        return s

    def _scroll_for(self, s: Session) -> _Scroll | None:
        """The running scroll-back of ``s``; one of another session / mode ends first."""
        sc = self.scrolling
        if sc is not None and (sc.key != s.sid.key() or sc.mode != self.mode):
            self._end_scroll()
            sc = None
        return sc

    def _start_scroll(self, s: Session, t: float) -> _Scroll | None:
        ready = t
        if self.adapter.capabilities().scroll_focus:
            # the helper scrolls the App's frontmost window: bring this thread forward first (normal focus path)
            if not self._focus(s):
                return None
            ready = t + SCROLL_FOCUS_SETTLE_S
        self.scrolling = _Scroll(s.sid.key(), self.mode, s.sid.native, s.sid.generation, last=t, ready=ready)
        return self.scrolling

    def _k1_rotate(self, direction: int, t: float) -> None:
        """K1 rotation (+1 = older / ccw, -1 = newer / cw): queue lines for the selected session."""
        if self.option is not None:
            self._say(SCROLL_OPTION)  # the digit keys answer the prompt: never put its pane into copy mode
            return
        if t < self._scroll_quiet.get(self.mode, float("-inf")):
            return  # the setup hint was just shown: nothing else happens for a while
        s = self._k1_session()
        if s is None:
            return
        sc = self._scroll_for(s)
        if sc is None:
            if direction < 0:
                self._say(SCROLL_LATEST)  # nothing newer than the live view
                return
            sc = self._start_scroll(s, t)
            if sc is None:
                return
        fast = sc.dir == direction and t - sc.last < SCROLL_FAST_S
        sc.streak = sc.streak + 1 if fast else 0
        sc.dir, sc.last = direction, t
        sc.pending += direction * SCROLL_LINES * min(SCROLL_MAX_MULT, 1 + sc.streak // 2)
        self._touch()

    def _k1_press(self, t: float) -> None:
        """K1 press: the selected session back to its latest content, out of any scroll-back."""
        if t < self._scroll_quiet.get(self.mode, float("-inf")):
            return
        s = self._k1_session()
        if s is None:
            return
        sc = self._scroll_for(s) or self._start_scroll(s, t)
        if sc is None:
            return
        sc.pending, sc.bottom, sc.last = 0, True, t
        self._touch()

    def _scroll_tick(self, t: float) -> None:
        """Send what K1 queued (summed over the loop step), or end an idle scroll-back."""
        sc = self.scrolling
        if sc is None:
            return
        s = self.managers[sc.mode].sessions.get(sc.key)
        if sc.mode != self.mode or s is None:
            self._end_scroll()
            return
        if (sc.pending or sc.bottom) and t < sc.ready:
            return
        ad = self.adapters[sc.mode]
        if sc.bottom:
            self.scrolling = None
            self._touch()
            res = ad.scroll_bottom(s)
            if res.ok:
                self._say(SCROLL_LIVE)
            else:
                self._scroll_failed(res, t)
        elif sc.pending:
            n, sc.pending = sc.pending, 0
            res = ad.scroll(s, n)
            self._touch()
            if not res.ok:
                self.scrolling = None
                self._scroll_failed(res, t)
                self._scroll_release(sc, s)  # an earlier scroll may have left the tmux pane in copy mode
            elif res.detail.get("live"):  # tmux scrolled down out of copy mode by itself
                self.scrolling = None
                self._say(SCROLL_LIVE)
            else:
                sc.active = True
        elif t - sc.last >= SCROLL_IDLE_S and self._end_scroll():
            self._say(SCROLL_IDLE_LIVE)

    def _scroll_failed(self, res: ActionResult, t: float) -> None:
        if res.detail.get("setup"):  # helper missing / not allowed: the hint once, then quiet for a while
            self._scroll_quiet[self.mode] = t + SCROLL_SETUP_QUIET_S
            self.scroll_note = (res.message, t + SCROLL_NOTE_S)
            self._touch()
        else:
            self._say(res.message)

    def _end_scroll(self) -> bool:
        """Leave the scroll-back: the adapter puts the view back where that touches nothing else (tmux: the pane
        leaves copy mode; Desktop: nothing, the deck never pulls Codex forward by itself).  True = live again."""
        sc, self.scrolling = self.scrolling, None
        if sc is None:
            return False
        self._touch()
        if not sc.active:
            return False
        return self._scroll_release(sc, self.managers[sc.mode].sessions.get(sc.key))

    def _scroll_release(self, sc: _Scroll, s: Session | None) -> bool:
        """The session is already gone (it left the poll / the hook record expired): the tmux pane may still sit in
        copy mode, so release it anyway through a minimal Session built from the identity stored in ``_Scroll``
        (stale generation / dead pane: the adapter refuses without touching anything)."""
        if s is None:
            s = Session(SessionId(sc.mode, sc.native, sc.generation), Tool.OTHER, "")
        return bool(self.adapters[sc.mode].scroll_release(s))

    # ------------------------------------------------------------------ gestures
    def _on_down(self, kr: KeyRole, p: _Press, t: float) -> None:
        r = kr.role
        if r == Role.PTT:
            self.ptt_held = True
            self.ptt_observed = True
            self._end_scroll()  # dictation must reach the live prompt (a tmux pane in copy mode would eat it)
            if PTT_UNAVAILABLE:
                self._say(PTT_UNAVAILABLE)
            return
        if r == Role.AGENT:
            # Select on press (immediate sub-screen feedback); the focus half of a tap is decided on
            # release so a long press (pick up to move / pin) never brings a window forward.
            mgr = self.mgr
            entry = mgr.entry(kr.index)
            if entry is not None and entry.lost:
                self._say("失联钉扎 · 长按后点自己解除")
                p.target = entry.key
                return
            prev_sel = mgr.selected
            s = mgr.select(kr.index)
            if s is None:
                self._say("空槽")
                return
            if prev_sel != mgr.selected:
                self._cancel_target_presses(exclude=p)
                self._end_scroll()
                self.persist()
            p.target = s.sid.key()
            return
        if r == Role.MODE:
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
        elif r == Role.AGENT and held < LONG_PRESS:
            self._tap_focus(p)

    def _tap_focus(self, p: _Press) -> None:
        """Single tap on a slot = select AND focus, so dictation / device keys go to that session."""
        s = self.mgr.selected_session()
        if p.target is None or p.mode != self.mode or s is None or s.sid.key() != p.target:
            return  # empty / lost slot, or the target changed while the key was down
        self._end_scroll()  # a tap = go to the session: its live view (tmux leaves copy mode)
        if self.adapter.capabilities().focus is not None:
            return  # select only; the sub-screen note explains why (no toast on every tap)
        # Option mode only after the window really came forward: the keyboard types into the frontmost one.
        if self._focus(s) and option_prompt(s) and self.page == "L1":
            self._enter_option(s)

    def _on_long(self, kr: KeyRole, p: _Press) -> None:
        r = kr.role
        if r == Role.AGENT:
            # Pick up (移动中); the release does nothing else.  Pin toggle = pick up + press the same slot.
            if p.mode == self.mode:
                self._pick_up(kr.index)
        elif r == Role.MODE:
            if self.page == "L4":
                self._snap()  # SNAP: long press of the mode key on L4 (on L1 it opens L4)
            else:
                self._goto("L4", "L4 模式页：点选模式")

    # ------------------------------------------------------------------ SNAP (2026-10-29)
    def _snap(self) -> None:
        self.effects.append(("snap",))
        self._say(SNAP_BUSY, 10.0)

    def effect_result(self, kind: str, ok: bool, message: str) -> None:
        """Daemon: a side effect finished (``kind`` = "snap"); feedback on the keyboard."""
        if kind == "snap":
            if ok:
                col, row = BOTTOM_ROW.index(Role.MODE) + 1, 4  # the MODE key (fb20)
                self.flash[(col, row)] = self.clock() + SNAP_FLASH_S
            self._say(message, TOAST_S if ok else 5.0)

    def _cancel_target_presses(self, exclude: "_Press | None" = None) -> None:
        for p in self.presses.values():
            if p is not exclude and p.role.role == Role.AGENT:
                p.cancelled = True

    def _click(self, kr: KeyRole) -> None:
        r = kr.role
        if r == Role.NEW:
            pre = self._drop_move()  # an L4 row 1 key cancels a pick-up
            s = self.mgr.selected_session()
            caps = self.adapter.capabilities()
            if caps.new_session:
                self._say(pre + caps.new_session)
            elif self.ptt_held:
                self._say(pre + "按住说话中，暂不新建会话")
            else:
                cwd = s.cwd if (s and s.cwd_confirmed) else None
                self._result(self.adapter.new_session(cwd), pre)
        elif r == Role.MODE_SELECT and kr.mode is not None:
            self.switch_mode(kr.mode)
        elif r == Role.BACK_L1:
            self._goto("L1", "回到 L1")

    def _focus(self, s: Session) -> bool:
        """Bring the session's window forward; True only if the adapter confirmed it."""
        if self.ptt_held:
            self._say("PTT 按住中，禁止置前")
            return False
        cap = self.adapter.capabilities().focus
        if cap:
            self._say(cap)
            return False
        res = self.adapter.focus(s)
        log.info("focus %s (%s): ok=%s %s", s.sid.key(), s.title[:24] if getattr(s, "title", None) else "",
                 res.ok, getattr(res, "message", ""))
        if res.ok and s.state == State.DONE_UNREAD:
            self.local_cleared[s.sid.key()] = s.done_marker
            s.state, s.state_reason = State.IDLE, "已聚焦（本地清提醒）"
        self._result(res)
        return bool(res.ok)

    # ------------------------------------------------------------------ modes
    def switch_mode(self, m: Mode) -> None:
        pre = self._drop_move()  # a mode switch cancels a pick-up (nothing changes)
        self._drop_option()
        self._end_scroll()
        for pk, p in self.presses.items():
            if p.role.role != Role.PTT:
                p.cancelled = True  # G34: cancel pending long presses + old eligibility
        old = self.mode
        if old != m:
            ok_old, why_old = self.backend[old]
            self.bg.pop(m, None)
            if ok_old:
                att, urg = self._summarise(self.managers[old].sessions.values())
                self.bg[old] = ModeSummary(True, "", att, urg, self.wall())
            else:
                self.bg[old] = ModeSummary(False, why_old, t=self.wall())
        self.mode = m
        self.page = "L1"
        self.scroll = 0
        self.refresh(m)
        ok, reason = self.backend[m]
        n = sum(1 for e in self.mgr.slots if e and not e.lost) + self.mgr.overflow
        name = {"codex_desktop": "Codex 桌面", "herdr": "herdr", "tmux": "tmux"}[m.value]
        self._say(pre + (f"已切换到 {name} · {n} 个会话" if ok else f"已切换到 {name} · 不可用：{reason}"),
                  SWITCH_TOAST_S)
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
        others = self.other_attention()
        mv = self.moving if (self.moving is not None and self.moving.mode == self.mode) else None
        op = self.option if (self.option is not None and self.option.mode == self.mode and self.page == "L1") else None
        attention: tuple[int, int, str] | None = None  # (priority, -updated, key) best
        approve_keys = self._approve_keys(op) if op is not None else frozenset()
        hold_s = self.approve_hold()
        for k in [k for k, until in self.flash.items() if until <= t]:
            del self.flash[k]
            self._touch()  # the frame without the flash is due
        for row in range(1, 5):
            for col in range(1, 6):
                kr = role_at(self.page, col, row)
                kv = KeyView(col, row, kr.role)
                if op is not None and kr.role == Role.AGENT:
                    if kr.index < len(op.labels):  # the choice keys (row 1); the session list is cleared
                        kv.option, kv.label = kr.index + 1, op.labels[kr.index]
                        if kr.index in approve_keys:
                            kv.sublabel = f"长按 {hold_s:g} 秒"
                            p = self.presses.get(("key", col, row))
                            if p is not None and p.approve and not p.approve_done and hold_s > 0:
                                kv.hold = 1.0 if p.approve_ready else max(0.001, min(1.0, (t - p.t0) / hold_s))
                                if p.approve_ready:
                                    kv.sublabel = "松开批准"
                    else:
                        kv.blank = True
                elif kr.role == Role.AGENT:
                    e = mgr.entry(kr.index)
                    kv.moving = mv is not None and mv.src == kr.index
                    if e is None:
                        kv.state = State.OFFLINE if not ok else State.UNASSIGNED
                        kv.label = "离线" if not ok else (mgr.waiting_label(kr.index) or "空槽")  # 等待：<rule>
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
                elif kr.role == Role.PTT:
                    kv.label, kv.active = "按住说话", self.ptt_held
                elif kr.role in DEVICE_HID_ROLES:
                    kv.label, kv.sublabel = HID_FACES[kr.role]
                elif kr.role == Role.NEW:
                    kv.label, kv.disabled = "新会话", caps.new_session
                elif kr.role == Role.MODE:
                    kv.label, kv.mode = self.mode.key_label, self.mode
                    if self.page == "L4":
                        kv.sublabel = "长按 截图"
                    total = sum(n for _, n in others)
                    if total:
                        kv.badge, kv.badge_alert = str(total), True
                elif kr.role == Role.MODE_SELECT:
                    kv.mode, kv.label = kr.mode, kr.mode.key_label
                    kv.active = kr.mode == self.mode
                    mok, mreason = self.backend[kr.mode]
                    kv.disabled = None if mok else mreason
                    n = dict(others).get(kr.mode, 0)
                    if n:
                        kv.badge, kv.badge_alert = str(n), True
                elif kr.role == Role.BACK_L1:
                    kv.label = "回 L1"
                kv.flash = self.flash.get((col, row), float("-inf")) > t
                keys.append(kv)
        if attention:
            c, r = map(int, attention[2].split(","))
            for kv in keys:
                if kv.col == c and kv.row == r:
                    kv.attention = True
        sel = mgr.selected_session()
        if op is not None:
            sel = mgr.sessions.get(op.key) or sel
        lp = self.last_poll_wall.get(self.mode)
        upd = time.strftime("%H:%M:%S", time.localtime(lp)) if lp else "--:--:--"
        pin_info = ""
        if self.page == "L4":
            pinned = sum(1 for e in mgr.slots if e and e.pinned)
            pin_info = f"钉扎 {pinned}/{N_SLOTS} · 长按拿起，点自己=钉扎/解除 · S1–S5 在 L1"
        note = f"单击只选中 · 置前不可用：{caps.focus.split('，')[0]}" if (ok and caps.focus) else None
        sc = self.scrolling
        scrollback, warn = "", False
        if sc is not None and sc.active and sc.mode == self.mode and sc.key == mgr.selected:
            scrollback = SCROLL_HINT
        elif self.scroll_note is not None:
            if t < self.scroll_note[1]:
                scrollback, warn = self.scroll_note[0], True
            else:
                self.scroll_note = None
        if self.rules_warning:
            note = self.rules_warning + (f" · {note}" if note else "")
        sub = SubView(
            page=self.page, mode=self.mode, mode_ok=ok, mode_reason=reason, session=sel,
            toast=self.toast[0] if self.toast else None,
            limited=(self.adapter.status_limited() if ok else f"不可用：{reason}"),
            overflow=mgr.overflow, overflow_attention=mgr.overflow_attention,
            note=note, updated=upd, ptt=self.ptt_held, scroll=self.scroll, sim=self.sim,
            pin_info=pin_info, selection_lost=mgr.selection_lost and sel is None,
            other_modes=(" · ".join(f"{MODE_SHORT[m]} {n}" for m, n in others) + " 待处理") if others else "",
            moving=f"移动『{_short(mv.title)}』：点目标键放下 · 点自己=切换钉住 · K1 取消" if mv else "",
            # "×" not "✗": the Mac CJK fonts (PingFang / STHeiti) have no U+2717 glyph
            option=(f"选项：长按 {'/'.join(str(i + 1) for i in sorted(approve_keys))} 批准 · 按 "
                    f"{len(op.labels)} 拒绝 · × 取消 · K1 返回" if approve_keys and len(op.labels) > len(approve_keys)
                    else f"选项：按 1–{len(op.labels)} 选择 · × 取消 · K1 返回" if len(op.labels) > 1 else
                    "选项：按 1 选择 · × 取消 · K1 返回") if op else "",
            option_note=({"legacy": OPTION_LEGACY, "pending": OPTION_PENDING}.get(self.option_hid, "")) if op else "",
            alert=GD32_DFU_ALERT if self.gd32_dfu else "", alert_detail=GD32_DFU_DETAIL if self.gd32_dfu else "",
            scrollback=scrollback, scrollback_warn=warn,
        )
        return DeckView(keys, sub)
