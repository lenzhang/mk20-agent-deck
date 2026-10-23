"""Connection supervisor: find port → open → handshake → run, with reconnect on
unplug / ack loss / wake (PRD §7.5, §8 port contention)."""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from . import protocol as P
from .config import DEFAULT_ARRANGE_PORT, DEFAULT_WEB_URL
from .controller import Controller
from .daemon import Daemon, WakeReconnect
from .layout import DeviceLayout
from .link import DeviceLink
from .render.canvas import Renderer

log = logging.getLogger(__name__)


@dataclass
class SupervisorStats:
    connects: int = 0
    events: list[tuple[float, str]] = field(default_factory=list)

    def note(self, msg: str) -> None:
        self.events.append((time.time(), msg))
        log.info(msg)


@dataclass
class Supervisor:
    controller: Controller
    find_ports: Callable[[], list[str]]
    open_transport: Callable[[str], object]
    make_renderer: Callable[[], Renderer]
    cfg: object
    power: object | None = None
    bg_adapters: dict | None = None  # separate adapter instances for the background poll of other modes
    retry_s: float = 2.0
    wake_settle_s: float = 2.0
    stats: SupervisorStats = field(default_factory=SupervisorStats)
    daemon: Daemon | None = None
    _last_port_msg: str = ""
    calls: object | None = None  # webui.LoopCalls: arrange-page changes, run on this thread (also while unplugged)
    arrange: object | None = None  # webui.ArrangeServer for the keyboard's 会话安排 key (ui.openArrange); None = off
    # While it cannot (re)connect: one line with the elapsed time at least this often (2026-10-23 incident: after the
    # last "未找到 MK20 CDC 端口" the log stayed silent for 50 min while the keyboard's bridge was stuck).
    down_note_s: float = 600.0
    clock: Callable[[], float] = time.monotonic
    _down_since: float | None = None  # not connected since (clock); None while connected
    _down_noted: float = 0.0

    def _wait(self, stop: threading.Event, secs: float) -> None:
        """``stop.wait`` that keeps serving queued arrange-page calls while no device is connected."""
        if self.calls is None:
            stop.wait(secs)
            return
        end = time.monotonic() + secs
        while not stop.is_set():
            self.calls.drain(None)
            left = end - time.monotonic()
            if left <= 0:
                return
            self.calls.wait(min(left, 0.5))

    def _note_down(self) -> None:
        """Every ``down_note_s`` without a connection: one line with the elapsed time and the last reason."""
        if self._down_since is None:
            return
        now = self.clock()
        if now - self._down_noted < self.down_note_s:
            return
        self._down_noted = now
        mins = int((now - self._down_since) // 60)
        self.stats.note(f"仍未连接 MK20：已 {mins} 分钟（最近：{self._last_port_msg or '等待接入'}）")

    def _say_once(self, msg: str) -> None:
        if msg != self._last_port_msg:
            self.stats.note(msg)
            self._last_port_msg = msg

    def apply_backlight(self, link: DeviceLink) -> bool | None:
        """Send ``setBacklight`` after each handshake (initial, replug, wake re-handshake).

        Connecting (getInfo) switches the device from offline typing (protocol V1, keys
        type their keycap characters via the GD32) into host mode (V2) with the backlight
        at 0, so without this the keys stay dark.  Failure is logged, never fatal.
        """
        level = getattr(self.cfg, "backlight", None)
        if level is None:
            return None
        try:
            ack = link.call(P.set_backlight(level))
        except Exception as e:  # noqa: BLE001
            self.stats.note(f"setBacklight({level}) 失败：{e}")
            return False
        ok = bool(ack and ack.success)
        self.stats.note(f"背光 {level}" + ("" if ok else f" 未确认（{ack.error if ack else 'timeout'}）"))
        return ok

    def run(self, stop: threading.Event, max_connects: int | None = None) -> SupervisorStats:
        cfg = self.cfg
        self._down_since = self._down_noted = self.clock()
        while not stop.is_set():
            if max_connects is not None and self.stats.connects >= max_connects:
                break
            self._note_down()
            ports = [cfg.port] if cfg.port != "auto" else self.find_ports()
            if not ports:
                self._say_once("未找到 MK20 CDC 端口，等待接入")
                self._wait(stop, self.retry_s)
                continue
            try:
                tr = self.open_transport(ports[0])
            except Exception as e:  # noqa: BLE001 — busy (ScreenKey?): never kill/steal (PRD §8)
                self._say_once(f"无法打开 {ports[0]}: {e} — 若 ScreenKey 在运行，请退出后重试")
                self._wait(stop, self.retry_s * 1.5)
                continue
            renderer = self.make_renderer()
            d = Daemon(self.controller, renderer, None, cfg.poll_interval_s, cfg.heartbeat_s, cfg.knob_map,
                       keycode_map=getattr(cfg, "keycode_map", {}), power=self.power,
                       web_url=getattr(cfg, "web_url", DEFAULT_WEB_URL),
                       bg_poll_s=getattr(cfg, "background_poll_s", 10.0), bg_adapters=self.bg_adapters,
                       calls=self.calls, arrange=self.arrange,
                       arrange_port=getattr(cfg, "arrange_port", DEFAULT_ARRANGE_PORT))
            link = DeviceLink(tr, on_key=d.on_device_key, ack_timeout=cfg.ack_timeout_s,
                              preamble_flush=cfg.preamble_flush, on_event=d.on_device_event)
            d.link = link
            self.daemon = d
            settle = 0.0
            connects_before = self.stats.connects
            try:
                link.start()
                renderer.layout = link.handshake(cfg.fw_coord_base)
                self.stats.connects += 1
                self._last_port_msg = ""
                self._down_since = None
                self.stats.note(f"已连接 {ports[0]} 画布 {renderer.layout.width}x{renderer.layout.height}"
                                f"{'（猜测）' if renderer.layout.guessed else ''}")
                self.apply_backlight(link)
                self.controller.drop_gestures()  # never replay gestures (presses, pick-up) across reconnects
                d.run(stop)
            except WakeReconnect as e:
                self.stats.note(f"唤醒（{e}），重新握手")
                settle = self.wake_settle_s
            except Exception as e:  # noqa: BLE001
                if self.stats.connects == connects_before:  # handshake failed (T113 booting?) — don't spam
                    self._say_once(f"握手失败：{e}")
                else:
                    self.stats.note(f"连接中断：{e}")
            finally:
                if stop.is_set() and link.connected:
                    d.offline_frame("守护进程已退出")
                link.close()
                if self._down_since is None:  # was connected: the down time counts from this loss
                    self._down_since = self._down_noted = self.clock()
            self._wait(stop, max(settle, 0.5))
        return self.stats


def default_renderer_factory(cfg) -> Callable[[], Renderer]:
    return lambda: Renderer(DeviceLayout.default(), animations=cfg.animations, max_fps=cfg.max_fps,
                            jpeg_quality=cfg.jpeg_quality)
