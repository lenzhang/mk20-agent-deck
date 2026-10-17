"""Connection supervisor: find port → open → handshake → run, with reconnect on
unplug / ack loss / wake (PRD §7.5, §8 port contention)."""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

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
    retry_s: float = 2.0
    wake_settle_s: float = 2.0
    stats: SupervisorStats = field(default_factory=SupervisorStats)
    daemon: Daemon | None = None
    _last_port_msg: str = ""

    def _say_once(self, msg: str) -> None:
        if msg != self._last_port_msg:
            self.stats.note(msg)
            self._last_port_msg = msg

    def run(self, stop: threading.Event, max_connects: int | None = None) -> SupervisorStats:
        cfg = self.cfg
        while not stop.is_set():
            if max_connects is not None and self.stats.connects >= max_connects:
                break
            ports = [cfg.port] if cfg.port != "auto" else self.find_ports()
            if not ports:
                self._say_once("未找到 MK20 CDC 端口，等待接入")
                stop.wait(self.retry_s)
                continue
            try:
                tr = self.open_transport(ports[0])
            except Exception as e:  # noqa: BLE001 — busy (ScreenKey?): never kill/steal (PRD §8)
                self._say_once(f"无法打开 {ports[0]}: {e} — 若 ScreenKey 在运行，请退出后重试")
                stop.wait(self.retry_s * 1.5)
                continue
            renderer = self.make_renderer()
            d = Daemon(self.controller, renderer, None, cfg.poll_interval_s, cfg.heartbeat_s, cfg.knob_map,
                       keycode_map=getattr(cfg, "keycode_map", {}), power=self.power)
            link = DeviceLink(tr, on_key=d.on_device_key, ack_timeout=cfg.ack_timeout_s,
                              preamble_flush=cfg.preamble_flush)
            d.link = link
            self.daemon = d
            settle = 0.0
            connects_before = self.stats.connects
            try:
                link.start()
                renderer.layout = link.handshake(cfg.fw_coord_base)
                self.stats.connects += 1
                self._last_port_msg = ""
                self.stats.note(f"已连接 {ports[0]} 画布 {renderer.layout.width}x{renderer.layout.height}"
                                f"{'（猜测）' if renderer.layout.guessed else ''}")
                if getattr(cfg, "backlight", None) is not None:
                    from . import protocol as P
                    link.call(P.set_backlight(cfg.backlight))
                self.controller.presses.clear()  # never replay gestures across reconnects
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
            stop.wait(max(settle, 0.5))
        return self.stats


def default_renderer_factory(cfg) -> Callable[[], Renderer]:
    return lambda: Renderer(DeviceLayout.default(), animations=cfg.animations, max_fps=cfg.max_fps,
                            jpeg_quality=cfg.jpeg_quality)
