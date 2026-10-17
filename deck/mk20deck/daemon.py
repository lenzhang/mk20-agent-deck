"""Main loop: input → controller → renderer → device, with poll/heartbeat/fps budget."""
from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from . import protocol as P
from .controller import LONG_PRESS, Controller
from .link import DeviceLink, LatencyStats
from .render.canvas import Renderer

log = logging.getLogger(__name__)


@dataclass
class InputEvent:
    kind: str  # "key" | "knob"
    a: int
    b: int | str
    pressed: bool = False
    t: float = 0.0


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

    # -------------------------------------------------------------- input
    def on_device_key(self, ev: P.KeyEvent) -> None:
        """Called from the link reader thread (firmware coordinates)."""
        now = self.clock()
        if ev.col < 0 and ev.keycode is not None:  # keycode-form event
            target = self.keycode_map.get(f"0x{ev.keycode:04x}")
            if target is None:
                log.info("unmapped keycode event 0x%04x (add it to [keycodes])", ev.keycode)
                return
            if ":" not in target:
                c, r = (int(x) for x in target.split(","))
                self.events.put(InputEvent("key", c, r, ev.pressed, t=now))
                return
            tag = target
        else:
            tag = self.knob_map.get(f"{ev.col},{ev.row}")
        if tag:  # e.g. "K1:press" → down/up from pressed
            knob = 1 if tag.startswith("K1") else 2
            action = tag.split(":", 1)[1] if ":" in tag else "press"
            if action == "press":
                self.events.put(InputEvent("knob", knob, "down" if ev.pressed else "up", t=now))
            elif ev.pressed:
                self.events.put(InputEvent("knob", knob, action, t=now))
            return
        lay = self.renderer.layout
        if lay.keys and not any(r.col == ev.col and r.row == ev.row for r in lay.keys):
            # knobs report col/row >= 100; never let an unmapped one reach the key grid
            log.info("unmapped non-key event %d,%d (add it to [knobs])", ev.col, ev.row)
            return
        col, row = lay.fw_to_doc(ev.col, ev.row)
        self.events.put(InputEvent("key", col, row, ev.pressed, t=now))

    def inject_key(self, col: int, row: int, pressed: bool) -> None:
        """Document coordinates (simulator)."""
        self.events.put(InputEvent("key", col, row, pressed, t=self.clock()))

    def inject_knob(self, knob: int, kind: str) -> None:
        self.events.put(InputEvent("knob", knob, kind, t=self.clock()))

    def _apply(self, ev: InputEvent) -> None:
        if ev.kind == "key":
            self.controller.on_key(ev.a, int(ev.b), ev.pressed)
        else:
            self.controller.on_knob(ev.a, str(ev.b))
        if self._pending_t is None:
            self._pending_t = ev.t

    # -------------------------------------------------------------- loop
    def step(self, force: bool = False) -> bool:
        """One iteration; returns True if a frame was produced."""
        now = self.clock()
        while True:
            try:
                self._apply(self.events.get_nowait())
            except queue.Empty:
                break
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
                self.controller.presses.clear()  # never replay gestures across sleep
                if ev == "system_wake" or was_paused:
                    raise WakeReconnect(ev)

    def run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            self.check_power()
            if self.paused:
                stop.wait(self.power_interval)
                while not self.events.empty():  # drop input while the Mac sleeps
                    self.events.get_nowait()
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
        when = time.strftime("%H:%M:%S")
        jpeg = self.renderer.jpeg(self.renderer.compose_offline(reason, when))
        if self.link is not None:
            try:
                self.link.show_jpeg(jpeg)
            except Exception:  # noqa: BLE001
                pass
        if self.sink:
            self.sink(jpeg)
