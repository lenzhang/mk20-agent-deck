"""Sleep / wake detection.

* System sleep: ``time.monotonic`` (mach_absolute_time on macOS, CLOCK_MONOTONIC on
  Linux) does not advance while suspended, wall time does, so a jump in
  ``time.time() - time.monotonic()`` larger than ``gap_s`` means we slept.
* Display sleep (macOS): ``CGDisplayIsAsleep(CGMainDisplayID())`` through ctypes —
  no permissions needed.  Display-off usually precedes system sleep, so this is
  where the best-effort "Mac 已休眠" frame is pushed (PRD §7.5 / G24).
"""
from __future__ import annotations

import ctypes
import ctypes.util
import sys
import time
from typing import Callable


def mac_display_asleep_probe() -> Callable[[], bool | None] | None:
    if sys.platform != "darwin":
        return None
    path = ctypes.util.find_library("CoreGraphics") or \
        "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
    try:
        cg = ctypes.cdll.LoadLibrary(path)
        cg.CGMainDisplayID.restype = ctypes.c_uint32
        cg.CGDisplayIsAsleep.argtypes = [ctypes.c_uint32]
        cg.CGDisplayIsAsleep.restype = ctypes.c_int
    except (OSError, AttributeError):
        return None

    def probe() -> bool | None:
        try:
            return bool(cg.CGDisplayIsAsleep(cg.CGMainDisplayID()))
        except Exception:  # noqa: BLE001
            return None

    return probe


class PowerMonitor:
    """``poll()`` returns a list of events: "display_sleep", "display_wake", "system_wake"."""

    def __init__(self, display_probe: Callable[[], bool | None] | None = None, gap_s: float = 8.0,
                 wall: Callable[[], float] = time.time, mono: Callable[[], float] = time.monotonic) -> None:
        self.display_probe = display_probe
        self.gap_s = gap_s
        self.wall, self.mono = wall, mono
        self._offset = wall() - mono()
        self._display_asleep: bool | None = None
        self.last_sleep_s: float = 0.0

    @classmethod
    def default(cls) -> "PowerMonitor":
        return cls(mac_display_asleep_probe())

    @property
    def display_asleep(self) -> bool:
        return bool(self._display_asleep)

    def poll(self) -> list[str]:
        ev: list[str] = []
        off = self.wall() - self.mono()
        jump = off - self._offset
        self._offset = off
        if jump > self.gap_s:
            self.last_sleep_s = jump
            ev.append("system_wake")
        if self.display_probe is not None:
            a = self.display_probe()
            if a is not None and a != self._display_asleep:
                if self._display_asleep is not None or a:
                    ev.append("display_sleep" if a else "display_wake")
                self._display_asleep = a
        return ev
