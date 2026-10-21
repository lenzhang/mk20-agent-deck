"""Main loop: input → controller → renderer → device, with poll/heartbeat/fps budget."""
from __future__ import annotations

import logging
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from . import keytrace as _keytrace
from . import protocol as P
from .adapters.base import BackendUnavailable
from .config import DEFAULT_WEB_URL
from .controller import LONG_PRESS, Controller
from .link import DeviceLink, LatencyStats
from .model import MODE_CYCLE
from .render.canvas import Renderer

log = logging.getLogger(__name__)


OPEN_SETTINGS_EVENT = {"method": "ui.openSettings"}  # mk20-kbd 网页设置 key, exactly this (no payload)


def open_url(url: str) -> bool:
    """Open ``url`` in the Mac's default browser (``open``); elsewhere only log. Never raises."""
    if sys.platform != "darwin":
        log.info("web settings %s: not on macOS, not opening", url)
        return False
    try:
        r = subprocess.run(["open", url], timeout=5, capture_output=True)
    except Exception as e:  # noqa: BLE001 — timeout / missing binary: the deck keeps running
        log.warning("open %s failed: %s", url, e)
        return False
    if r.returncode != 0:
        log.warning("open %s exited %s", url, r.returncode)
    return r.returncode == 0


@dataclass
class InputEvent:
    kind: str  # "key" | "knob" | "ui"
    a: int
    b: int | str
    pressed: bool = False
    t: float = 0.0
    code: str = ""  # raw code for the key trace


class WakeReconnect(ConnectionError):
    """Raised after system/display wake so the caller re-opens the port and re-handshakes."""


@dataclass
class Daemon:
    controller: Controller
    renderer: Renderer
    link: DeviceLink | None
    poll_interval: float = 2.0
    heartbeat: float = 2.0
    knob_map: dict[str, str] = field(default_factory=dict)
    keycode_map: dict[str, str] = field(default_factory=dict)  # "0x00e6" -> "1,4" (doc) or "K1:cw"
    clock: Callable[[], float] = time.monotonic
    sink: Callable[[bytes], None] | None = None  # extra frame consumer (simulator)
    events: "queue.Queue[InputEvent]" = field(default_factory=queue.Queue)
    event_to_render: LatencyStats = field(default_factory=LatencyStats)
    event_to_ack: LatencyStats = field(default_factory=LatencyStats)
    frames_sent: int = 0
    frames_failed: int = 0
    _last_version: int = -1
    _last_frame: float = -1e9
    _next_poll: float = 0.0
    _pending_t: float | None = None
    power: object | None = None  # hw.power.PowerMonitor
    power_interval: float = 1.0
    paused: bool = False  # display asleep: no frames, no heartbeat
    _next_power: float = 0.0
    power_log: list = field(default_factory=list)
    trace: object | None = field(default_factory=lambda: _keytrace.from_env())  # MK20_KEYTRACE=1
    web_url: str = DEFAULT_WEB_URL  # validated config value (config.parse_web_url), never from the device
    open_settings_min_s: float = 3.0
    opener: Callable[[str], object] = open_url
    settings_opened: int = 0
    settings_dropped: int = 0  # rate-limited or malformed ui.openSettings events
    _last_settings: float = -1e9
    # Cross-mode awareness: non-current modes are polled on a worker thread every bg_poll_s
    # (0 = off); results are applied on the loop thread in step().  bg_adapters should be
    # separate instances (cli.build_adapters) so a poll never races the foreground refresh.
    bg_poll_s: float = 10.0
    bg_adapters: dict | None = None
    sleep_frame_s: float = 10.0  # re-send "Mac 已休眠" this often while paused (device drops frames off L1/L4)
    _last_sleep_frame: float = float("-inf")
    bg_first_delay_s: float = 1.0
    bg_results: "queue.Queue[tuple]" = field(default_factory=queue.Queue)
    bg_polls: int = 0
    bg_sessions: dict = field(default_factory=dict)  # mode -> (sessions | None, error, t0) of the last bg poll
    # Cross-thread hand-off (webui.LoopCalls): the arrange web page queues its changes here; they run on this
    # loop thread inside step() (never from the HTTP thread) and force a frame.
    calls: object | None = None

    # -------------------------------------------------------------- input
    def on_device_key(self, ev: P.KeyEvent) -> None:
        """Called from the link reader thread (firmware coordinates)."""
        now = self.clock()
        code = ""
        if self.trace:
            code = f"kc:0x{ev.keycode:04x}" if (ev.col < 0 and ev.keycode is not None) else f"{ev.col},{ev.row}"
            self.trace.ev("recv", code, ev.pressed)
        if ev.col < 0 and ev.keycode is not None:  # keycode-form event
            target = self.keycode_map.get(f"0x{ev.keycode:04x}")
            if target is None:
                log.info("unmapped keycode event 0x%04x (add it to [keycodes])", ev.keycode)
                return
            if ":" not in target:
                c, r = (int(x) for x in target.split(","))
                self.events.put(InputEvent("key", c, r, ev.pressed, t=now, code=code))
                return
            tag = target
        else:
            tag = self.knob_map.get(f"{ev.col},{ev.row}")
        if tag:  # e.g. "K1:press" → down/up from pressed
            knob = 1 if tag.startswith("K1") else 2
            action = tag.split(":", 1)[1] if ":" in tag else "press"
            if action == "press":
                self.events.put(InputEvent("knob", knob, "down" if ev.pressed else "up", ev.pressed, t=now, code=code))
            elif ev.pressed:
                self.events.put(InputEvent("knob", knob, action, True, t=now, code=code))
            return
        lay = self.renderer.layout
        if lay.keys and not any(r.col == ev.col and r.row == ev.row for r in lay.keys):
            # knobs report col/row >= 100; never let an unmapped one reach the key grid
            log.info("unmapped non-key event %d,%d (add it to [knobs])", ev.col, ev.row)
            return
        col, row = lay.fw_to_doc(ev.col, ev.row)
        self.events.put(InputEvent("key", col, row, ev.pressed, t=now, code=code))

    def on_device_event(self, obj: dict) -> None:
        """Non-key device JSON event, called from the link reader thread: only queue, never block or raise."""
        method = obj.get("method")
        if method != OPEN_SETTINGS_EVENT["method"]:
            log.info("ignored device event %r", str(method)[:64])
            return
        if obj != OPEN_SETTINGS_EVENT:  # the device never sends a payload: refuse to act on one
            self.settings_dropped += 1
            log.warning("ui.openSettings with extra fields %s dropped", sorted(str(k)[:32] for k in obj)[:8])
            return
        self.events.put(InputEvent("ui", 0, "openSettings", t=self.clock()))

    def _open_settings(self) -> None:
        now = self.clock()
        if now - self._last_settings < self.open_settings_min_s:
            self.settings_dropped += 1
            log.info("ui.openSettings rate-limited (%.1f s since the last one)", now - self._last_settings)
            return
        self._last_settings = now
        self.settings_opened += 1
        log.info("ui.openSettings: opening %s", self.web_url)
        # `open` can take a moment (LaunchServices): never stall the render loop for it.
        threading.Thread(target=self.opener, args=(self.web_url,), name="mk20-open-settings", daemon=True).start()

    def inject_key(self, col: int, row: int, pressed: bool) -> None:
        """Document coordinates (simulator)."""
        self.events.put(InputEvent("key", col, row, pressed, t=self.clock()))

    def inject_knob(self, knob: int, kind: str) -> None:
        self.events.put(InputEvent("knob", knob, kind, t=self.clock()))

    def _apply(self, ev: InputEvent) -> None:
        if ev.kind == "ui":  # device UI request, not controller input (no render/latency bookkeeping)
            if ev.b == "openSettings":
                self._open_settings()
            return
        log.info("input %s %s %s%s", ev.kind, ev.a, ev.b, "" if ev.kind != "key" else (" down" if ev.pressed else " up"))
        tr = self.trace
        if tr:
            c = self.controller
            ptt0, acts0 = c.ptt_held, c.action_count
        if ev.kind == "key":
            self.controller.on_key(ev.a, int(ev.b), ev.pressed)
        else:
            self.controller.on_knob(ev.a, str(ev.b))
        if tr:
            code = ev.code or f"inj:{ev.a},{ev.b}"
            tr.ev("routed", code, ev.pressed)
            out = "hid_written" if c.ptt_held != ptt0 else "agent" if c.action_count != acts0 else "local"
            tr.ev(out, code, ev.pressed)
        if self._pending_t is None:
            self._pending_t = ev.t

    # -------------------------------------------------------------- background (other modes)
    def poll_background_once(self) -> int:
        """Poll every non-current mode once (worker thread or tests); results are queued, not applied."""
        ads = self.bg_adapters or self.controller.adapters
        current = self.controller.mode
        n = 0
        for m in MODE_CYCLE:
            ad = ads.get(m)
            if m == current or ad is None:
                continue
            t0 = self.controller.wall()
            try:
                res = (m, ad.poll(()), "", t0)
            except BackendUnavailable as e:
                res = (m, None, str(e), t0)
            except Exception as e:  # noqa: BLE001 — a background backend bug never stops the deck
                log.warning("background poll %s failed: %s", m.value, type(e).__name__)
                res = (m, None, f"读取失败：{type(e).__name__}", t0)
            self.bg_results.put(res)
            n += 1
        self.bg_polls += 1
        return n

    def _bg_loop(self, stop: threading.Event, done: threading.Event) -> None:
        wait = self.bg_first_delay_s
        while not (stop.wait(wait) or done.is_set()):
            wait = self.bg_poll_s
            if not self.paused:
                self.poll_background_once()

    def _drain_background(self) -> None:
        while True:
            try:
                m, sessions, err, t0 = self.bg_results.get_nowait()
            except queue.Empty:
                return
            self.bg_sessions[m] = (sessions, err, t0)
            self.controller.apply_background(m, sessions, err, t0)

    # -------------------------------------------------------------- loop
    def step(self, force: bool = False) -> bool:
        """One iteration; returns True if a frame was produced."""
        now = self.clock()
        while True:
            try:
                self._apply(self.events.get_nowait())
            except queue.Empty:
                break
        if self.calls is not None and self.calls.drain(self):
            force = True  # an arrange-page change: show it on the keyboard now
        self._drain_background()
        self.controller.tick()
        if now >= self._next_poll:
            self.controller.refresh()
            self._next_poll = now + self.poll_interval
        view = self.controller.view()
        fps = self.renderer.desired_fps(view, now)
        due = force or self.controller.version != self._last_version
        if fps > 0 and now - self._last_frame >= 1.0 / fps:
            due = True
        if now - self._last_frame >= self.heartbeat:
            due = True
        if not due:
            return False
        self._last_version = self.controller.version
        img = self.renderer.compose(view, now, heartbeat_on=int(now / self.heartbeat) % 2 == 0)
        jpeg = self.renderer.jpeg(img)
        t_render = self.clock()
        if self._pending_t is not None:
            self.event_to_render.add(t_render - self._pending_t)
        ok = True
        if self.link is not None:
            ok = self.link.show_jpeg(jpeg)
            if ok and self._pending_t is not None:
                self.event_to_ack.add(self.clock() - self._pending_t)
        if self.sink:
            self.sink(jpeg)
        self._pending_t = None
        self._last_frame = now
        if ok:
            self.frames_sent += 1
        else:
            self.frames_failed += 1
        return True

    def next_wait(self) -> float:
        now = self.clock()
        waits = [self._next_poll - now, self._last_frame + self.heartbeat - now]
        view_fps = self.renderer.desired_fps(self.controller.view(), now)
        if view_fps > 0:
            waits.append(self._last_frame + 1.0 / view_fps - now)
        if self.controller.presses:
            waits.append(LONG_PRESS / 8)
        deadline = self.controller.move_deadline()
        if deadline is not None:  # a picked-up slot times out even when nothing animates
            waits.append(deadline - now)
        if self.power is not None:
            waits.append(self._next_power - now)
        return max(0.005, min(waits))

    def check_power(self) -> None:
        """Display sleep → offline frame + pause; wake → WakeReconnect (PRD §7.5, G24)."""
        if self.power is None:
            return
        now = self.clock()
        if now < self._next_power:
            return
        self._next_power = now + self.power_interval
        for ev in self.power.poll():
            self.power_log.append((time.time(), ev))
            log.info("power event: %s", ev)
            if ev == "display_sleep" and not self.paused:
                self.offline_frame("Mac 已休眠")
                self.paused = True
            elif ev in ("display_wake", "system_wake"):
                was_paused, self.paused = self.paused, False
                self.controller.drop_gestures()  # never replay gestures (presses, pick-up) across sleep
                if ev == "system_wake" or was_paused:
                    raise WakeReconnect(ev)

    def run(self, stop: threading.Event) -> None:
        # A connection made while the display already sleeps (e.g. the keyboard rebooted for an install while
        # the user is away) gets no new display_sleep edge: start paused from the monitor's current state.
        if self.power is not None and getattr(self.power, "display_asleep", False) and not self.paused:
            self.offline_frame("Mac 已休眠")
            self.paused = True
        done = threading.Event()
        if self.bg_poll_s > 0:
            threading.Thread(target=self._bg_loop, args=(stop, done), name="mk20-bg-poll", daemon=True).start()
        if self.calls is not None:  # wake the loop at once when the web page queues a change
            self.calls.attach(lambda: self.events.put(InputEvent("ui", 0, "wake", t=self.clock())))
        try:
            self._run(stop)
        finally:
            done.set()
            if self.calls is not None:
                self.calls.attach(None)

    def _run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            self.check_power()
            if self.paused:
                # Still watch the link while paused: a device reboot during display sleep must reconnect (and
                # show "Mac 已休眠" again) instead of leaving the keyboard with no host until the next wake.
                if self.link is not None and not self.link.connected:
                    raise ConnectionError(self.link.lost_reason or "device disconnected")
                # Re-send the sleep frame: the keyboard drops frames outside L1/L4 and needs a fresh one after
                # any page change (e.g. it follows the Mac to L1 after boot while we are paused).
                if self.clock() - self._last_sleep_frame >= self.sleep_frame_s:
                    self.offline_frame("Mac 已休眠")
                stop.wait(self.power_interval)
                while not self.events.empty():  # drop input while the Mac sleeps
                    self.events.get_nowait()
                if self.calls is not None:
                    self.calls.drain(self)  # arrange-page changes still apply (shown after wake)
                continue
            self.step()
            try:
                ev = self.events.get(timeout=self.next_wait())
                self._apply(ev)
            except queue.Empty:
                pass
            if self.link is not None and not self.link.connected:
                raise ConnectionError(self.link.lost_reason or "device disconnected")

    def offline_frame(self, reason: str) -> None:
        """Best effort on normal exit / sleep (PRD §7.5, AC12)."""
        if reason == "Mac 已休眠":
            self._last_sleep_frame = self.clock()
        when = time.strftime("%H:%M:%S")
        jpeg = self.renderer.jpeg(self.renderer.compose_offline(reason, when))
        if self.link is not None:
            try:
                self.link.show_jpeg(jpeg)
            except Exception:  # noqa: BLE001
                pass
        if self.sink:
            self.sink(jpeg)
