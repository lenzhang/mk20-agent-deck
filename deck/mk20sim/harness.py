"""Headless simulator harness + tiny test DSL.

Input path is identical to the device: ``Sim.raw(col, row, pressed)`` builds a
``protocol.KeyEvent`` in *firmware* coordinates and hands it to
``Daemon.on_device_key`` (the same entry point the serial/TCP link reader uses),
so knob code mapping (``config.DEFAULT_KNOB_MAP``), unmapped-event filtering and
fw→doc coordinate translation are all exercised.  Output side:

* screen   – ``Daemon.sink`` captures every JPEG frame the daemon would push;
             ``key_image(name)`` / ``sub_image()`` crop it with the device layout.
* outputs  – what the router *would* emit, recorded instead of sent:
             ``hid``   (PTT → Right Option down/up),
             ``agent`` (adapter calls: focus/send/skill/stop/new/effort),
             ``local`` (page / mode changes) and ``toast`` (sub-screen messages).
"""
from __future__ import annotations

import io
import os
import tempfile
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from PIL import Image

from mk20deck.adapters.mock import MockAdapter
from mk20deck.config import DEFAULT_KNOB_MAP, DeckConfig
from mk20deck.controller import Controller
from mk20deck.daemon import Daemon
from mk20deck.layout import DeviceLayout
from mk20deck.model import ActionResult
from mk20deck.protocol import KeyEvent
from mk20deck.render.canvas import Renderer
from mk20deck.agentcfg import AgentConfigApplier, MockTester
from mk20deck.settings import SettingsPage, SettingsStore
from mk20deck.sim import FakeClock, demo_adapters


# PRD §6.2 L1 names → 1-based document (col, row)
KEY_NAMES: dict[str, tuple[int, int]] = {
    **{f"A{i + 1}": (i % 4 + 1, i // 4 + 1) for i in range(8)},
    "ACCEPT": (5, 1), "DECLINE": (5, 2),
    **{f"SKILL{i + 1}": (i + 1, 3) for i in range(4)},
    "SEND": (5, 3),
    "PTT": (1, 4), "STOP": (2, 4), "NEW": (3, 4), "FOCUS": (4, 4), "MODE": (5, 4),
}
# L4 aliases (same positions, row 3 differs)
KEY_NAMES.update({"DESK": (1, 3), "HERD": (2, 3), "TMUX": (3, 3), "BACK": (4, 3)})
# Knob raw codes (col == row), measured on V2.32
KNOB_CODES = {(k, a): int(code.split(",")[0]) for code, tag in DEFAULT_KNOB_MAP.items()
              for k, a in [tuple(tag.split(":"))]}


@dataclass
class Output:
    t: float          # sim seconds since start
    kind: str         # hid | agent | local | toast | input
    text: str


class _Recording(MockAdapter):
    """Wrap a MockAdapter so every call (allowed or refused) is logged as an output."""

    def __init__(self, inner: MockAdapter, emit: Callable[[str, str], None]) -> None:
        self.__dict__.update(inner.__dict__)
        self._emit = emit

    def _act(self, name: str, target: str, cap: str | None) -> ActionResult:
        res = super()._act(name, target, cap)
        self._emit("agent", f"{self.mode.value}.{name}({target}) -> {'OK' if res.ok else 'refused: ' + res.message}")
        return res

    def set_effort(self, s, level: str) -> ActionResult:
        res = super().set_effort(s, level)
        self._emit("agent", f"{self.mode.value}.set_effort({s.sid.key()},{level}) -> "
                            f"{'OK' if res.ok else 'refused: ' + res.message}")
        return res


@dataclass
class Latency:
    """Injected delays (ms).  input = GD32→T113 link, router = T113 processing before the
    controller sees the event, frame = render→LCD push.  Jitter is uniform ±jitter, seeded,
    and FIFO order is preserved (a serial link cannot reorder)."""
    input_ms: float = 0
    input_jitter_ms: float = 0
    router_ms: float = 0
    router_jitter_ms: float = 0
    frame_ms: float = 0
    frame_jitter_ms: float = 0
    seed: int = 1

    @classmethod
    def from_dict(cls, d: dict | None) -> "Latency":
        d = d or {}
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})

    def any(self) -> bool:
        return any((self.input_ms, self.input_jitter_ms, self.router_ms, self.router_jitter_ms, self.frame_ms,
                    self.frame_jitter_ms))


@dataclass
class Sim:
    realtime: bool = False
    animations: bool = False
    layout: DeviceLayout = field(default_factory=DeviceLayout.default)
    outputs: list[Output] = field(default_factory=list)
    latency: Latency = field(default_factory=Latency)

    def __post_init__(self) -> None:
        self.lock = threading.RLock()
        self._rng = random.Random(self.latency.seed)
        self._inq: list[tuple[float, int, int, bool]] = []   # (deliver_at, col, row, pressed)
        self._frameq: list[tuple[float, bytes]] = []
        self.frame_lag_ms: float = 0.0
        self.fake = FakeClock()
        self.clock: Callable[[], float] = time.monotonic if self.realtime else self.fake
        self.t0 = self.clock()
        self.adapters = {m: _Recording(a, self._emit) for m, a in demo_adapters().items()}
        self.agent_dir = tempfile.mkdtemp(prefix="mk20sim-agent-")
        sec = os.path.join(self.agent_dir, "secrets")
        os.makedirs(sec, mode=0o700)
        for p in ("qwen", "openai"):  # sim: these two providers have a key on file
            with open(os.path.join(sec, f"{p}.key"), "w") as f:
                f.write("sim-placeholder")
        self.tester = MockTester()
        self.applier = AgentConfigApplier(root=os.path.join(self.agent_dir, "agent"), secrets_dir=sec,
                                          tester=self.tester)
        self.controller = Controller(DeckConfig(), self.adapters, clock=self.clock,
                                     settings=SettingsPage(store=SettingsStore.temp(), applier=self.applier),
                                     wall=lambda: 1792244000.0 if not self.realtime else time.time(), sim=True)
        self.controller.refresh()
        self.renderer = Renderer(self.layout, animations=self.animations)
        self.frame: bytes = b""
        self.frames = 0
        self.daemon = Daemon(self.controller, self.renderer, link=None, clock=self.clock,
                             knob_map=dict(DEFAULT_KNOB_MAP), sink=self._sink, poll_interval=3600)
        self._seen_log = 0
        self.observer: Callable[["Sim", float], None] | None = None  # (sim, sim_dt) after each tick
        self.highlight: set[str] = set()  # names currently held (for UI replay)
        self._last = (self.controller.page, self.controller.mode, self.controller.ptt_held)
        self.step(force=True)

    # ------------------------------------------------------------- plumbing
    def now(self) -> float:
        return self.clock() - self.t0

    def _emit(self, kind: str, text: str) -> None:
        self.outputs.append(Output(round(self.now(), 3), kind, text))

    def set_latency(self, lat: Latency) -> None:
        with self.lock:
            self.latency, self._rng = lat, random.Random(lat.seed)

    def _delay(self, base: float, jit: float) -> float:
        return max(0.0, base + (self._rng.uniform(-jit, jit) if jit else 0.0)) / 1000

    def _sink(self, jpeg: bytes) -> None:
        self._publish(jpeg)

    def _publish(self, jpeg: bytes) -> None:
        d = self._delay(self.latency.frame_ms, self.latency.frame_jitter_ms)
        if d <= 0:
            self.frame, self.frame_lag_ms = jpeg, 0.0
            self.frames += 1
        else:
            self._frameq.append((self.clock() + d, jpeg))

    def _pump_queues(self) -> None:
        now = self.clock()
        while self._inq and self._inq[0][0] <= now:
            _t, c, r, p = self._inq.pop(0)
            self._deliver(c, r, p)
        while self._frameq and self._frameq[0][0] <= now:
            t, jpeg = self._frameq.pop(0)
            self.frame, self.frames = jpeg, self.frames + 1
            self.frame_lag_ms = round((now - t) * 1000 + self.latency.frame_ms, 1)

    def _deliver(self, col: int, row: int, pressed: bool) -> None:
        """Router → the real daemon/controller (settings page included)."""
        self.daemon.on_device_key(KeyEvent(col, row, pressed))

    @property
    def settings(self):
        return self.controller.settings

    def step(self, force: bool = False) -> None:
        with self.lock:
            self._pump_queues()
            self.daemon.step(force=force)
            for msg in self.settings.log[getattr(self, "_seen_slog", 0):]:
                self._emit("local", f"settings: {msg}")
            self._seen_slog = len(self.settings.log)
            c = self.controller
            for msg in c.log[self._seen_log:]:
                self._emit("toast", msg)
            self._seen_log = len(c.log)
            page, mode, ptt = c.page, c.mode, c.ptt_held
            if ptt != self._last[2]:
                self._emit("hid", f"RightOption {'down' if ptt else 'up'} (recorded, not sent)")
            if page != self._last[0]:
                self._emit("local", f"page {self._last[0]} -> {page}")
            if mode != self._last[1]:
                self._emit("local", f"mode {self._last[1].value} -> {mode.value}")
            self._last = (page, mode, ptt)

    # ------------------------------------------------------------- virtual input adapter
    def raw(self, col: int, row: int, pressed: bool) -> None:
        """Firmware-coordinate keyStateChanged, exactly as the link reader delivers it."""
        with self.lock:
            self._emit("input", f"raw {col},{row} {'down' if pressed else 'up'}")
            lat = self.latency
            d = self._delay(lat.input_ms, lat.input_jitter_ms) + self._delay(lat.router_ms, lat.router_jitter_ms)
            if d <= 0 and not self._inq:
                self._deliver(col, row, pressed)
            else:
                at = max(self.clock() + d, self._inq[-1][0] if self._inq else 0.0)  # FIFO link
                self._inq.append((at, col, row, pressed))
        self.step()

    def _fw(self, name: str) -> tuple[int, int]:
        n = name.upper()
        if n in KEY_NAMES:
            dc, dr = KEY_NAMES[n]
        elif "," in n:
            dc, dr = (int(x) for x in n.split(","))
        else:
            raise KeyError(f"unknown key {name!r}; use {sorted(KEY_NAMES)} or 'col,row'")
        b = self.layout.fw_base
        return dc - 1 + b, dr - 1 + b

    def down(self, name: str) -> None:
        self.highlight.add(name.upper())
        self.raw(*self._fw(name), True)
        self._observe(0)

    def up(self, name: str) -> None:
        self.raw(*self._fw(name), False)
        self.highlight.discard(name.upper())
        self._observe(0)

    def _observe(self, dt: float) -> None:
        if self.observer:
            self.observer(self, dt)

    def knob(self, k: str | int, action: str, n: int = 1) -> None:
        """k = 'K1'/'K2'; action = 'cw'|'ccw'|'down'|'up'|'press' (press = tap)."""
        ki = int(str(k).upper().lstrip("K"))
        if action == "press":
            self.knob(ki, "down")
            self.wait(50)
            self.knob(ki, "up")
            return
        if action in ("down", "up"):
            code = KNOB_CODES[(f"K{ki}", "press")]
            (self.highlight.add if action == "down" else self.highlight.discard)(f"K{ki}")
            self.raw(code, code, action == "down")
            self._observe(0)
            return
        code = KNOB_CODES[(f"K{ki}", action)]
        for i in range(n):
            self.highlight.add(f"K{ki}")
            self.raw(code, code, True)  # one pressed:true per detent, no release
            self._observe(0)
            self.highlight.discard(f"K{ki}")
            if i < n - 1:
                self.wait(30)

    # ------------------------------------------------------------- DSL
    def wait(self, ms: float) -> None:
        if self.realtime:
            time.sleep(ms / 1000)
            self.step()
            return
        end = self.fake.t + ms / 1000
        while self.fake.t < end:  # 10 ms ticks so long-press thresholds fire on time
            dt = min(0.01, end - self.fake.t)
            self.fake.advance(dt)
            self.step()
            self._observe(dt)

    def press(self, name: str, hold_ms: float = 50) -> None:
        self.down(name)
        self.wait(hold_ms)
        self.up(name)

    tap = press

    def hold(self, name: str, ms: float) -> None:
        self.press(name, hold_ms=ms)

    def double(self, name: str, gap_ms: float = 100, hold_ms: float = 40) -> None:
        self.press(name, hold_ms)
        self.wait(gap_ms)
        self.press(name, hold_ms)

    # ------------------------------------------------------------- queries
    @property
    def page(self) -> str:
        return self.controller.page

    @property
    def mode(self) -> str:
        return self.controller.mode.value

    def out(self, kind: str | None = None, since: float = -1) -> list[Output]:
        return [o for o in self.outputs if (kind is None or o.kind == kind) and o.t >= since]

    def agent_calls(self, name: str | None = None) -> list[Output]:
        return [o for o in self.out("agent") if name is None or f".{name}(" in o.text]

    def toast(self) -> str | None:
        v = self.controller.view().sub
        return v.toast

    def canvas(self) -> Image.Image:
        self.step(force=True)
        return Image.open(io.BytesIO(self.frame)).convert("RGB")

    def key_image(self, name: str) -> Image.Image:
        fc, fr = self._fw(name)
        r = next(r for r in self.layout.keys if r.col == fc and r.row == fr)
        return self.canvas().crop((r.x, r.y, r.x + r.w, r.y + r.h))

    def sub_image(self) -> Image.Image:
        r = self.layout.subscreen
        return self.canvas().crop((r.x, r.y, r.x + r.w, r.y + r.h))

    def state(self) -> dict:
        c = self.controller
        v = c.view()
        sel = c.mgr.selected_session()
        return {
            "t": round(self.now(), 3), "page": c.page, "mode": c.mode.value, "ptt": c.ptt_held,
            "toast": v.sub.toast, "selected": (sel.title if sel else None), "scroll": c.scroll,
            "frames": self.frames, "frame_lag_ms": self.frame_lag_ms, "queued_inputs": len(self._inq),
            "latency": self.latency.__dict__, "settings": self.settings.state(),
            "keys": [{"col": k.col, "row": k.row, "role": k.role.value, "label": k.label, "selected": k.selected,
                      "disabled": k.disabled} for k in v.keys],
        }


class CSim(Sim):
    """Offline C HOME/LS + U2 route compositor; no Python controller or JPEG rasterizer."""

    def __post_init__(self) -> None:
        import pathlib
        import subprocess
        binary = pathlib.Path(__file__).resolve().parents[1] / "kbd/tests/onboard_headless"
        if not getattr(CSim, "_binary_checked", False):
            subprocess.run(["make", "-C", str(binary.parent.parent), "tests/onboard_headless"], check=True)
            CSim._binary_checked = True
        self.process = subprocess.Popen([str(binary)], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.lock = threading.RLock()
        self.fake = FakeClock()
        self.clock = self.fake
        self.t0 = self.clock()
        self._rng = random.Random(self.latency.seed)
        self._inq, self._frameq = [], []
        self.frames, self.frame_lag_ms = 0, 0.0
        self.observer, self.highlight = None, set()
        self._state = self._command("state")

    def close(self) -> None:
        if getattr(self, "process", None):
            self.process.stdin.close()
            self.process.wait(timeout=5)
            self.process.stdout.close()
            self.process = None

    def __del__(self):
        self.close()

    def _command(self, line: str) -> dict:
        import json
        self.process.stdin.write((line + "\n").encode())
        self.process.stdin.flush()
        data = self.process.stdout.readline()
        if not data:
            raise RuntimeError(f"C backend exited: {self.process.poll()}")
        self.frames += 1
        self._state = json.loads(data)
        return self._state

    def _deliver(self, col: int, row: int, pressed: bool) -> None:
        self._command(f"key {row} {col} {int(pressed)}")

    def step(self, force: bool = False) -> None:
        with self.lock:
            self._pump_queues()
            self._command(f"time {round(self.now() * 1000)}")

    @property
    def settings(self):
        return self

    def cfg(self, field):
        return self._state["settings"].get(field)

    @property
    def page(self) -> str:
        return self._state["page"]

    @property
    def mode(self) -> str:
        return "c-onboard"

    def state(self) -> dict:
        return {**self._state, "t": round(self.now(), 3), "mode": self.mode,
                "ptt": False, "scroll": 0, "selected": None, "frames": self.frames,
                "frame_lag_ms": 0, "queued_inputs": len(self._inq), "keys": []}

    def canvas(self) -> Image.Image:
        self.step(force=True)
        self.process.stdin.write(b"canvas\n")
        self.process.stdin.flush()
        header = b"".join(self.process.stdout.readline() for _ in range(3))
        size = 640 * 656 * 3
        data = self.process.stdout.read(size)
        if len(data) != size:
            raise RuntimeError("short C canvas")
        return Image.open(io.BytesIO(header + data)).convert("RGB")

    @property
    def frame(self) -> bytes:
        # JPEG is only the existing browser transport; every pixel originates in C.
        if not hasattr(self, "_frame_cache") or self.now() - self._frame_at >= 0.125:
            out = io.BytesIO()
            self.canvas().save(out, "JPEG", quality=90)
            self._frame_cache, self._frame_at = out.getvalue(), self.now()
        return self._frame_cache

    def inject_host(self, request: dict | None) -> None:
        import json
        self._command("loss" if request is None else "host " + json.dumps(request, separators=(",", ":")))

    def inject_config_fault(self) -> None:
        self._command("fault")

    def inject_test(self, result: int, key_present: bool) -> None:
        self._command(f"test {result} {int(key_present)}")
