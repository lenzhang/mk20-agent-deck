"""Per-event key timing trace (MK20_KEYTRACE=1).  RAM-only, bounded, zero cost when off.

Line format (one per stage, ASCII, space separated)::

    <CLOCK_MONOTONIC ns> <stage> <code> <pressed 0|1>

stage: recv (link reader got the raw event) · routed (controller handled it) · then exactly one
outcome: hid_written | agent | local.   code: "<col>,<row>" for raw key/knob events, "kc:0x...."
for keycode-form events, "inj:<col>,<row>" for injected (simulator) events.
The file lives on tmpfs (/tmp/mk20/keytrace.log on the T113); when it exceeds ``max_bytes``
it is rotated to ``.1`` (one generation kept) so it can never fill RAM.
"""
from __future__ import annotations

import os
import threading
import time

DEFAULT_PATH = "/tmp/mk20/keytrace.log"
DEFAULT_MAX = 256 * 1024
STAGES = ("recv", "routed", "hid_written", "agent", "local")


class KeyTrace:
    def __init__(self, path: str = DEFAULT_PATH, max_bytes: int = DEFAULT_MAX,
                 clock=time.monotonic_ns) -> None:
        self.path, self.max_bytes, self.clock = path, max_bytes, clock
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._f = open(path, "a", buffering=1, encoding="ascii")
        self._size = self._f.tell()

    def ev(self, stage: str, code: str, pressed: bool, t_ns: int | None = None) -> None:
        line = f"{self.clock() if t_ns is None else t_ns} {stage} {code} {1 if pressed else 0}\n"
        with self._lock:
            if self._size + len(line) > self.max_bytes:
                self._f.close()
                os.replace(self.path, self.path + ".1")
                self._f = open(self.path, "a", buffering=1, encoding="ascii")
                self._size = 0
            self._f.write(line)
            self._size += len(line)

    def close(self) -> None:
        with self._lock:
            self._f.close()


def from_env(env=os.environ) -> KeyTrace | None:
    """None unless MK20_KEYTRACE=1 — callers guard with ``if trace:`` so the off path costs one test."""
    if env.get("MK20_KEYTRACE") != "1":
        return None
    return KeyTrace(env.get("MK20_KEYTRACE_PATH", DEFAULT_PATH),
                    int(env.get("MK20_KEYTRACE_MAX", DEFAULT_MAX)))


def parse(text: str) -> list[tuple[int, str, str, int]]:
    out = []
    for line in text.splitlines():
        p = line.split()
        if len(p) == 4 and p[1] in STAGES and p[0].isdigit():
            out.append((int(p[0]), p[1], p[2], int(p[3])))
    return out
