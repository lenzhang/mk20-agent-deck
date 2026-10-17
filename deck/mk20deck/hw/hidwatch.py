"""Permission-free HID activity watcher for the devtest HID-leak gate (macOS).

``CGEventSourceSecondsSinceLastEventType`` (HID system state) reports, per event
type, how long ago the window server last saw such an event.  It needs no Input
Monitoring / Accessibility permission (unlike a CGEventTap) but cannot tell which
device produced the event and cannot see the characters.  We poll it and turn every
reset of the idle counter into an activity sample; devtest then correlates samples
with MK20 key events in time.  A full event tap is reported as unavailable unless
``CGPreflightListenEventAccess()`` is already true (we never trigger a TCC prompt).
"""
from __future__ import annotations

import sys
import threading
import time
from typing import Callable

# CGEventType values
TYPES = {"keyDown": 10, "flagsChanged": 12, "systemDefined": 14, "leftMouseDown": 1, "scrollWheel": 22}
HID_STATE = 1  # kCGEventSourceStateHIDSystemState


def _cg_idle_source() -> tuple[Callable[[int], float], bool] | None:
    if sys.platform != "darwin":
        return None
    try:
        import ctypes
        import ctypes.util
        cg = ctypes.CDLL(ctypes.util.find_library("CoreGraphics")
                         or "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        f = cg.CGEventSourceSecondsSinceLastEventType
        f.restype, f.argtypes = ctypes.c_double, [ctypes.c_int32, ctypes.c_uint32]
        listen = False
        try:
            p = cg.CGPreflightListenEventAccess
            p.restype = ctypes.c_bool
            listen = bool(p())
        except AttributeError:
            pass
        f(HID_STATE, TYPES["keyDown"])
        return (lambda t: float(f(HID_STATE, t))), listen
    except Exception:  # noqa: BLE001
        return None


class HidActivityMonitor:
    """Polls idle seconds per event type; records (t_monotonic, type_name) on every reset."""

    def __init__(self, idle: Callable[[int], float], interval: float = 0.03,
                 clock: Callable[[], float] = time.monotonic, listen_access: bool = False,
                 source: str = "CGEventSourceSecondsSinceLastEventType") -> None:
        self.idle, self.interval, self.clock = idle, interval, clock
        self.listen_access, self.source = listen_access, source
        self.samples: list[tuple[float, str]] = []
        self._last: dict[str, float] = {}
        self._stop = threading.Event()
        self._th: threading.Thread | None = None
        self._lock = threading.Lock()

    @classmethod
    def macos(cls) -> "HidActivityMonitor | None":
        r = _cg_idle_source()
        return cls(r[0], listen_access=r[1]) if r else None

    def poll_once(self) -> None:
        now = self.clock()
        for name, t in TYPES.items():
            v = self.idle(t)
            prev = self._last.get(name)
            self._last[name] = v
            if prev is not None and v + 1e-3 < prev:  # counter reset -> new event(s)
                with self._lock:
                    self.samples.append((now - v, name))

    def start(self) -> None:
        self.poll_once()
        self._th = threading.Thread(target=self._loop, name="hid-watch", daemon=True)
        self._th.start()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.poll_once()
            except Exception:  # noqa: BLE001
                return

    def stop(self) -> None:
        self._stop.set()

    def between(self, t0: float, t1: float) -> list[tuple[float, str]]:
        with self._lock:
            return [s for s in self.samples if t0 <= s[0] <= t1]

    def info(self) -> dict:
        return {"source": self.source, "listen_access": self.listen_access, "types": list(TYPES)}


def correlate(samples: list[tuple[float, str]], key_times: list[float], window: float = 0.35) -> dict:
    """Split HID activity into samples within ``window`` s of an MK20 key event and the rest."""
    near, far = [], []
    for t, name in samples:
        (near if any(abs(t - k) <= window for k in key_times) else far).append((t, name))
    by = lambda xs: {n: sum(1 for _, m in xs if m == n) for n in sorted({m for _, m in xs})}  # noqa: E731
    return {"correlated": len(near), "uncorrelated": len(far), "correlated_by_type": by(near),
            "uncorrelated_by_type": by(far), "window_s": window}
