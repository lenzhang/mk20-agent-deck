"""On-device validation harness (PRD §10.1 / README checklist): ``mk20deck devtest``.

Read-only by default.  The only device writes are Protocol-A ``getInfo`` and
``SHOW_JPG`` test patterns (plus the vendor 1 MiB '0' preamble); anything else
(backlight) needs ``--allow-device-writes``.  VIA is read-only (backup dump).
Guided steps tell the user what to press — on the terminal *and* on the device
sub-screen, with the target key highlighted — and record raw device JSON.
Outputs ``devtest.json`` + ``devtest.md`` (+ ``via-backup.json``) in ``out_dir``.
"""
from __future__ import annotations

import datetime as _dt
import io
import json
import os
import statistics
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, Protocol

from .. import protocol as P
from ..controller import DOUBLE_TAP, LONG_PRESS
from ..layout import DeviceLayout
from ..link import DeviceLink
from ..transport import USB_IDS

PTT_DOC = (1, 4)
TIMING_DOC = (3, 2)


# ------------------------------------------------------------------ prompting
class Prompter(Protocol):
    def instruct(self, step: str, text: str) -> None: ...
    def ask(self, step: str, question: str, choices: str = "yn") -> str: ...
    def wait_enter(self, step: str, text: str) -> str: ...


class ConsolePrompter:
    def __init__(self, out=print, inp=input) -> None:
        self.out, self.inp = out, inp

    def instruct(self, step: str, text: str) -> None:
        self.out(f"\n▶ [{step}] {text}")

    def ask(self, step: str, question: str, choices: str = "yn") -> str:
        while True:
            a = self.inp(f"? [{step}] {question} [{'/'.join(choices)}/s=跳过] ").strip().lower()
            if a in choices or a == "s":
                return a

    def wait_enter(self, step: str, text: str) -> str:
        return self.inp(f"⏎ [{step}] {text}（回车继续，s=跳过）").strip().lower()


class HeadlessPrompter:
    """No terminal input: everything is shown on the device; progress goes to a log line."""

    def __init__(self, out=None) -> None:
        import sys
        self.out = out or (lambda m: (sys.stdout.write(m + "\n"), sys.stdout.flush()))

    def instruct(self, step: str, text: str) -> None:
        self.out(f"{time.strftime('%H:%M:%S')} ▶ [{step}] {text}")

    def ask(self, step: str, question: str, choices: str = "yn") -> str:
        self.out(f"{time.strftime('%H:%M:%S')} ? [{step}] {question} → 无终端输入，记为需人工确认")
        return "s"

    def wait_enter(self, step: str, text: str) -> str:
        self.out(f"{time.strftime('%H:%M:%S')} ⏎ [{step}] {text} → 自动继续")
        return ""


class ScriptedPrompter:
    """Test double: ``actions[step]()`` runs (in a thread, after ``delay``) when a step is announced."""

    def __init__(self, actions: dict[str, Callable[[], None]] | None = None,
                 answers: dict[str, str] | None = None, delay: float = 0.05) -> None:
        self.actions = actions or {}
        self.answers = answers or {}
        self.delay = delay
        self.log: list[tuple[str, str]] = []

    def _fire(self, step: str) -> None:
        fn = self.actions.get(step)
        if fn:
            threading.Timer(self.delay, fn).start()

    def instruct(self, step: str, text: str) -> None:
        self.log.append(("instruct", step))
        self._fire(step)

    def ask(self, step: str, question: str, choices: str = "yn") -> str:
        self.log.append(("ask", step))
        return self.answers.get(step, choices[-1] if step.endswith("leak") else choices[0])

    def wait_enter(self, step: str, text: str) -> str:
        self.log.append(("enter", step))
        fn = self.actions.get(step)
        if fn:
            fn()
        return self.answers.get(step, "")


# ------------------------------------------------------------------ event recorder
class Recorder:
    def __init__(self, clock=time.monotonic) -> None:
        self.clock = clock
        self.items: list[tuple[float, dict]] = []
        self._cv = threading.Condition()

    def add(self, obj: dict) -> None:
        with self._cv:
            self.items.append((self.clock(), obj))
            self._cv.notify_all()

    def since(self, t0: float) -> list[tuple[float, dict]]:
        with self._cv:
            return [x for x in self.items if x[0] >= t0]

    def wait(self, pred: Callable[[list[tuple[float, dict]]], bool], t0: float, timeout: float) -> bool:
        end = self.clock() + timeout
        with self._cv:
            while True:
                if pred([x for x in self.items if x[0] >= t0]):
                    return True
                left = end - self.clock()
                if left <= 0:
                    return False
                self._cv.wait(min(left, 0.1))

    def wait_quiet(self, t0: float, first_timeout: float, quiet: float, cap: float) -> list[tuple[float, dict]]:
        """Wait for a first event, then until no new event for ``quiet`` s (max ``cap``)."""
        if not self.wait(lambda xs: bool(_events(xs)), t0, first_timeout):
            return []
        end = self.clock() + cap
        while self.clock() < end:
            xs = _events(self.since(t0))
            if xs and self.clock() - xs[-1][0] >= quiet:
                break
            time.sleep(0.05)
        return _events(self.since(t0))


def _events(xs):
    """Device-originated JSON (not acks)."""
    return [x for x in xs if "ack_method" not in x[1]]


def _keys(xs, pressed=None):
    """(t, a, b, pressed): a,b = firmware col,row; for keycode-form events a="kc", b=keycode."""
    out = []
    for t, o in xs:
        ev = P.classify_json(o)
        if isinstance(ev, P.KeyEvent):
            if pressed is None or ev.pressed == pressed:
                a, b = (ev.col, ev.row) if ev.col >= 0 else ("kc", ev.keycode)
                out.append((t, a, b, ev.pressed))
    return out


def _pct(vals, p):
    if not vals:
        return None
    s = sorted(vals)
    return s[min(len(s) - 1, max(0, int(round(p / 100 * (len(s) - 1)))))]


def _ms(v):
    return None if v is None else round(v * 1000, 1)


# ------------------------------------------------------------------ test pattern
def pattern(layout: DeviceLayout, note: str = "", highlight: tuple[int, int] | None = None,
            noise: bool = False, seed: int = 0, quality: int = 85, title: str = "MK20 devtest",
            done: frozenset = frozenset()) -> bytes:
    from PIL import Image, ImageDraw

    from ..render.fonts import font

    im = Image.new("RGB", (layout.width, layout.height), (10, 10, 14))
    if noise:  # worst-case JPEG size
        im = Image.frombytes("RGB", im.size, os.urandom(im.size[0] * im.size[1] * 3))
    d = ImageDraw.Draw(im)
    f_big, f_small = font(26, bold=True), font(15)
    for r in layout.keys:
        doc = layout.fw_to_doc(r.col, r.row)
        hl = highlight is not None and doc == highlight
        fill = (250, 200, 40) if hl else ((30, 120, 60) if doc in done else
                                          ((40, 70, 120) if (doc[0] + doc[1]) % 2 else (60, 60, 70)))
        if not noise or hl:
            d.rectangle([r.x + 2, r.y + 2, r.x + r.w - 3, r.y + r.h - 3], fill=fill)
        d.rectangle([r.x, r.y, r.x + r.w - 1, r.y + r.h - 1], outline=(255, 255, 255))
        ink = (0, 0, 0) if hl else (255, 255, 255)
        d.text((r.x + 8, r.y + 8), f"{doc[0]},{doc[1]}", font=f_big, fill=ink)
        d.text((r.x + 8, r.y + r.h - 24), f"fw {r.col},{r.row}", font=f_small, fill=ink)
        if hl:
            d.text((r.x + 8, r.y + 52), "按我", font=f_big, fill=ink)
    sub = layout.subscreen
    if sub is not None:
        d.rectangle([sub.x, sub.y, sub.x + sub.w - 1, sub.y + sub.h - 1], fill=(0, 0, 0), outline=(0, 200, 120))
        d.text((sub.x + 8, sub.y + 6), title, font=f_small, fill=(0, 200, 120))
        y = sub.y + 30
        line = ""
        for ch in note:  # naive wrap
            if d.textlength(line + ch, font=font(20)) > sub.w - 16:
                d.text((sub.x + 8, y), line, font=font(20), fill=(255, 255, 255))
                y, line = y + 26, ""
            line += ch
        d.text((sub.x + 8, y), line, font=font(20), fill=(255, 255, 255))
    for (x, y) in ((0, 0), (layout.width - 12, 0), (0, layout.height - 12), (layout.width - 12, layout.height - 12)):
        d.rectangle([x, y, x + 11, y + 11], fill=(255, 0, 80))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


# ------------------------------------------------------------------ harness
@dataclass
class DevTestOptions:
    interactive: bool = True
    port: str = "auto"
    fps_seconds: float = 5.0
    ack_frames: int = 20
    probe_reject: bool = True
    keys: bool = True
    knobs: bool = True
    timing: bool = True
    reconnect: bool = True
    sleep: bool = False
    via: bool = True
    allow_device_writes: bool = False
    backlight_level: int | None = None
    step_timeout: float = 20.0
    quiet_s: float = 1.5
    # headless: guided purely on the device (no terminal input); y/n questions become
    # automatic checks or "needs manual confirmation".
    headless: bool = False
    start_timeout: float = 180.0  # headless: wait this long for the first key press ("press any key")
    overall_timeout: float | None = None  # guided-phase budget (s) after the start gate; then skip+record
    key_subset: tuple = ()  # document "col,row" keys to test (default: all 20)
    only: tuple = ()  # restrict to these sections (+ usb/port/getinfo/backlight), e.g. ("knobs", "ptt")


@dataclass
class DevTest:
    opts: DevTestOptions
    prompter: Prompter
    out_dir: str
    find_ports: Callable[[], list[str]]
    open_transport: Callable[[str], object]
    enumerate_usb: Callable[[], tuple[list, str]] | None = None
    port_users: Callable[[str], dict] | None = None
    via_open: Callable[[], object] | None = None
    power: object | None = None
    clock: Callable[[], float] = time.monotonic
    report: dict = field(default_factory=dict)
    link: DeviceLink | None = None
    port: str | None = None
    rec: Recorder = field(default_factory=Recorder)
    hid_monitor: object | None = None
    deadline: float | None = None
    step_no: int = 0
    done_keys: set = field(default_factory=set)
    progress_path: str | None = None
    step_log: list = field(default_factory=list)

    # -------------------------------------------------- plumbing
    def _sec(self, name: str, status: str, **kw) -> dict:
        s = {"status": status, **kw}
        self.report["sections"][name] = s
        return s

    def _title(self) -> str:
        t = "MK20 自检"
        if self.step_no:
            t += f"  步骤 {self.step_no}/{len(step_plan(self.opts))}"
        left = self.remaining()
        if left != float("inf"):
            t += f"  剩余 {int(left // 60)}:{int(left % 60):02d}"
        return t

    def _show(self, note: str, highlight=None) -> None:
        if self.link and self.link.connected:
            try:
                self.link.show_jpeg(pattern(self.link.layout, note, highlight, title=self._title(),
                                            done=frozenset(self.done_keys)))
            except Exception:  # noqa: BLE001
                pass

    def remaining(self) -> float:
        return float("inf") if self.deadline is None else max(0.0, self.deadline - self.clock())

    def _tmo(self, base: float) -> float:
        return max(0.0, min(base, self.remaining()))

    def _step(self, name: str, text: str, highlight=None) -> float:
        """Announce one guided step (device + log + progress file); returns its start time."""
        self.step_no += 1
        self.step_log.append({"t": round(self.clock(), 3), "step": name})
        self._show(text, highlight)
        # t0 BEFORE the prompt: a scripted (or very fast) press can arrive while _progress() is
        # still writing its file; taking t0 afterwards dropped that press from the step window
        # (flaky test_headless_devtest_*: key "2,1" missing when the progress write took >50 ms).
        t0 = self.clock()
        self.prompter.instruct(name, text)
        self._progress(name, text)
        return t0

    def _progress(self, name: str, text: str, state: str = "waiting") -> None:
        if not self.progress_path:
            return
        try:
            with open(self.progress_path, "w", encoding="utf-8") as f:
                json.dump({"step_no": self.step_no, "total": len(step_plan(self.opts)), "step": name, "text": text,
                           "state": state, "remaining_s": None if self.deadline is None else round(self.remaining()),
                           "at": _dt.datetime.now().astimezone().isoformat(timespec="seconds")}, f, ensure_ascii=False)
        except OSError:
            pass

    def _hid_since(self, t0: float, t1: float | None = None) -> list:
        if self.hid_monitor is None:
            return []
        from .hidwatch import drop_mouse_aux
        return [x for x in drop_mouse_aux(self.hid_monitor.between(t0, t1 if t1 is not None else self.clock() + 1))
                if x[1] in LEAK_TYPES]

    def connect(self) -> DeviceLayout:
        ports = [self.opts.port] if self.opts.port != "auto" else self.find_ports()
        if not ports:
            raise ConnectionError("未找到 MK20 CDC-ACM 端口")
        self.port = ports[0]
        tr = self.open_transport(self.port)
        self.link = DeviceLink(tr, on_json=self.rec.add)
        self.link.start()
        lay = self.link.handshake(None)
        # Host mode starts with backlight 0 (dark keys): re-apply on every (re)connect.
        if self.opts.backlight_level is not None and self.opts.allow_device_writes:
            ack = self.link.call(P.set_backlight(self.opts.backlight_level))
            self.report.setdefault("backlight_applied", []).append(
                {"level": self.opts.backlight_level, "success": bool(ack and ack.success),
                 "error": ack.error if ack else "timeout"})
        return lay

    def disconnect(self) -> None:
        if self.link:
            self.link.close()
        self.link = None

    def run(self) -> dict:
        os.makedirs(self.out_dir, exist_ok=True)
        self.report = {"format": "mk20deck-devtest/1",
                       "started": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                       "options": asdict(self.opts), "sections": {},
                       "step_plan": step_plan(self.opts) if self.opts.headless else None}
        if self.opts.headless:
            self.progress_path = os.path.join(self.out_dir, "progress.json")
            if self.hid_monitor is not None:
                try:
                    self.hid_monitor.start()
                except Exception:  # noqa: BLE001
                    self.hid_monitor = None
        steps = [("usb", self.t_usb), ("port", self.t_port), ("getinfo", self.t_getinfo), ("methods", self.t_methods),
                 ("files", self.t_files),
                 ("display", self.t_display), ("backlight", self.t_backlight),
                 ("keys", self.t_keys), ("knobs", self.t_knobs),
                 ("ptt", self.t_ptt), ("timing", self.t_timing), ("hid_leak", self.t_leak),
                 ("reconnect", self.t_reconnect),
                 ("sleep", self.t_sleep), ("via", self.t_via)]
        for name, fn in steps:
            if self.opts.only and name not in self.opts.only and name not in ALWAYS:
                self._sec(name, "skip", reason="不在本次 --only 范围内")
                continue
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                self._sec(name, "error", error=f"{type(e).__name__}: {e}")
        self.step_no = 0
        self.deadline = None
        try:
            self._show("自检完成 ✓  报告已保存。可拔线（拔插后恢复离线打字）或运行 mk20deck run")
        finally:
            self.disconnect()
            if self.hid_monitor is not None:
                self.hid_monitor.stop()
            self._progress("finished", "done", state="finished")
        self.report["finished"] = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
        self.report["suggested_config"] = self.suggest()
        self.report["raw_events"] = [{"t": round(t, 3), **o} for t, o in _events(self.rec.items)][:2000]
        if self.hid_monitor is not None:
            self.report["hid_samples"] = [{"t": round(t, 3), "type": n}
                                          for t, n in getattr(self.hid_monitor, "samples", [])][:2000]
        self.report["step_log"] = self.step_log
        with open(os.path.join(self.out_dir, "devtest.json"), "w", encoding="utf-8") as f:
            json.dump(self.report, f, ensure_ascii=False, indent=1)
        with open(os.path.join(self.out_dir, "devtest.md"), "w", encoding="utf-8") as f:
            f.write(render_markdown(self.report))
        return self.report

    def _need_link(self, name: str) -> bool:
        if self.link is None or not self.link.connected:
            self._sec(name, "skip", reason="未连接设备")
            return False
        return True

    def _guided(self, name: str) -> bool:
        if not self.opts.interactive:
            self._sec(name, "skip", reason="非交互模式（--auto）")
            return False
        if self.remaining() <= 0:
            self._sec(name, "skip", reason="超出总时限（overall timeout）")
            return False
        return self._need_link(name)

    def _ensure_gate(self) -> None:
        if self.opts.headless and "start_gate" not in self.report and self.link is not None:
            self.start_gate()

    def start_gate(self) -> bool:
        """Headless: wait for any key press, then start the guided-phase budget."""
        text = "设备自检开始：请按 任意一个键 开始（之后按屏幕提示操作，无需碰电脑）"
        t0 = self._step("start", text)
        got = self.rec.wait(lambda xs: bool(_keys(xs, True)) or any(
            any(w in str(o.get("method", "")).lower() for w in ("key", "knob")) for _, o in _events(xs)),
            t0, self.opts.start_timeout)
        if not got and self._hid_since(t0):
            got = True  # key typed via HID only (no CDC) — the user is there anyway
        waited = self.clock() - t0
        self.report["start_gate"] = {"pressed": got, "waited_s": round(waited, 1),
                                     "via": "cdc" if _events(self.rec.since(t0)) else ("hid" if got else None)}
        time.sleep(0.6)  # let the starting press be released before step 1
        if self.opts.overall_timeout:
            self.deadline = self.clock() + self.opts.overall_timeout
        return got

    # -------------------------------------------------- sections
    def t_usb(self) -> None:
        if self.enumerate_usb is None:
            self._sec("usb", "skip", reason="no enumerator")
            return
        from .usbenum import mk20_summary
        devs, source = self.enumerate_usb()
        s = mk20_summary(devs)
        status = "pass" if s["t113_cdc"] and s["gd32_via"] else ("fail" if not s["t113_cdc"] else "warn")
        notes = []
        if s["gd32_dfu"]:
            notes.append("GD32 处于 DFU 引导模式（键盘未运行）")
        if s["t113_fel"]:
            notes.append("T113 处于 FEL 模式（系统未启动）")
        if not s["gd32_via"]:
            notes.append("未见 GD32 VIA 键盘 4250:426f")
        self._sec("usb", status, source=source, notes=notes, **s)

    def t_port(self) -> None:
        ports = [self.opts.port] if self.opts.port != "auto" else self.find_ports()
        users = self.port_users(ports[0]) if (ports and self.port_users) else {}
        self._sec("port", "pass" if ports else "fail", ports=ports, usb_ids=[f"{v:04x}:{p:04x}" for v, p in USB_IDS],
                  users=users, note="端口被占用时请退出 ScreenKey 后重试（不会 kill）" if users.get("pids") else "")

    def t_getinfo(self) -> None:
        t0 = self.clock()
        lay = self.connect()
        dt = self.clock() - t0
        acks = [o for _, o in self.rec.items if o.get("ack_method") == "getInfo"]
        raw = acks[-1].get("result") if acks else None
        guess = DeviceLayout.default()
        keys = lay.keys
        problems = []
        if len(keys) != 20:
            problems.append(f"isKey 矩形 {len(keys)} 个（预期 20）")
        if lay.extra.get("fw_base_inferred") is None:
            problems.append("无法从 rects 推断 0/1 起始坐标")
        sub = lay.subscreen
        self._sec("getinfo", "warn" if problems else "pass", handshake_ms=_ms(dt),
                  canvas=[lay.width, lay.height], matches_default_guess=(lay.width, lay.height) == (guess.width, guess.height),
                  key_rects=len(keys), fw_base_inferred=lay.extra.get("fw_base_inferred"),
                  cols=sorted({r.col for r in keys}), rows=sorted({r.row for r in keys}),
                  subscreen=[sub.x, sub.y, sub.w, sub.h] if sub else None, model=lay.model,
                  version=lay.extra.get("deviceVersion"), problems=problems, raw=raw)

    def t_methods(self) -> None:
        """Ask the firmware for its method list via a deliberately unknown method (no side effects)."""
        if not self._need_link("methods"):
            return
        fr = self.link._send(P.CMD_JSON, P.json_bytes(P.unknown_method_probe()), wait=True)
        obj = (fr.json() if fr else None) or {}
        err = str(obj.get("errorString", ""))
        avail = []
        if "Available methods:" in err:
            avail = [m.strip(" []'\"") for m in err.split("Available methods:", 1)[1].split(",") if m.strip(" []")]
        unknown = sorted(set(avail) - set(P.KNOWN_METHODS))
        self._sec("methods", "pass" if avail else "warn", available=avail, new_vs_known=unknown, raw=obj)

    def t_files(self) -> None:
        """Read-only inventory of device files the firmware exposes (getFilesBySuffix)."""
        if not self._need_link("files"):
            return
        sufs = [".jpg", ".png", ".gif", ".wav", ".mp3", ".json", ".txt", ".ini", ".conf", ".bin", ".sh", ".zip"]
        ack = self.link.call(P.get_files_by_suffix(sufs))
        res = ack.result if ack else {}
        files = res.get("filePaths") if isinstance(res, dict) else None
        self._sec("files", "pass" if ack and ack.success else "warn", suffixes=sufs,
                  count=len(files) if isinstance(files, list) else None, files=(files or [])[:500],
                  error=ack.error if ack else "timeout",
                  note="协议只有列表/写入/删除，没有读文件方法 → 设备文件无法经 CDC 备份；P0 不写 T113 文件")

    def t_display(self) -> None:
        if not self._need_link("display"):
            return
        lay = self.link.layout
        res: dict = {"jpeg": {}}
        for q in (60, 85, 95):
            res["jpeg"][str(q)] = {"pattern_bytes": len(pattern(lay, "q", quality=q)),
                                   "noise_bytes": len(pattern(lay, "q", noise=True, quality=q))}
        self.link.frame_ack.samples.clear()
        fails = 0
        for i in range(self.opts.ack_frames):
            if not self.link.show_jpeg(pattern(lay, f"ACK 计时 {i + 1}/{self.opts.ack_frames}")):
                fails += 1
        res["ack"] = {**self.link.frame_ack.summary(), "timeouts": fails}
        fps = {}
        for label, kw in (("pattern_q85", {}), ("noise_q85", {"noise": True})):
            frames = [pattern(lay, f"满屏 fps {label}", seed=i, **kw) for i in range(4)]
            n, t0 = 0, self.clock()
            lat = []
            while self.clock() - t0 < self.opts.fps_seconds:
                t1 = self.clock()
                if not self.link.show_jpeg(frames[n % 4]):
                    break
                lat.append(self.clock() - t1)
                n += 1
            el = max(1e-6, self.clock() - t0)
            fps[label] = {"frames": n, "seconds": round(el, 2), "fps": round(n / el, 2),
                          "ack_p50_ms": _ms(_pct(lat, 50)), "ack_p95_ms": _ms(_pct(lat, 95)),
                          "bytes": len(frames[0])}
        res["full_screen"] = fps
        if self.opts.probe_reject:
            from PIL import Image
            b = io.BytesIO()
            Image.new("RGB", (64, 64), (255, 0, 0)).save(b, "JPEG")
            before = len(self.link.device_errors)
            ok = self.link.show_jpeg(b.getvalue())
            time.sleep(0.2)
            res["wrong_resolution"] = {"acked": ok, "device_errors": self.link.device_errors[before:][:3]}
            self.link.ack_misses = 0
        self._show("请看键面：每个键左上角应显示自己的坐标（左上 1,1 → 右下 5,4）")
        vis = self.prompter.ask("display_visual", "键面是否显示正确（左上=1,1，右下=5,4，无错位/镜像）？") \
            if self.opts.interactive and not self.opts.headless else "s"
        res["visual_ok"] = {"y": True, "n": False}.get(vis)
        if self.opts.headless:
            res["needs_manual_confirmation"] = "目视：键面坐标是否正确（无错位/镜像）——无头模式无法自动判定"
        worst = min(v["fps"] for v in fps.values()) if fps else 0
        res["recommended_max_fps"] = round(min(8.0, worst * 0.8), 1)
        st = "pass" if fails == 0 and worst > 0 and res["visual_ok"] is not False else "warn"
        if res["visual_ok"] is False:
            st = "fail"
        self._sec("display", st, **res)

    def t_backlight(self) -> None:
        if self.opts.backlight_level is None:
            self._sec("backlight", "skip", reason="未请求（--allow-device-writes --backlight N）")
            return
        if not self.opts.allow_device_writes:
            self._sec("backlight", "skip", reason="需要 --allow-device-writes")
            return
        if not self._need_link("backlight"):
            return
        ack = self.link.call(P.set_backlight(self.opts.backlight_level))
        self._sec("backlight", "pass" if ack and ack.success else "fail", level=self.opts.backlight_level,
                  ack=asdict(ack) if ack else None, note="原背光值不可读，结束后请手动调回")

    def _press_release(self, step: str, text: str, highlight) -> dict:
        if self.remaining() <= 0:
            return {"ok": False, "fw": None, "hold_ms": None, "skipped": "overall timeout", "raw": []}
        t0 = self._step(step, text, highlight)

        def done(xs):
            ks = _keys(xs)
            first = next((k for k in ks if k[3]), None)
            return first is not None and any(not k[3] and k[1:3] == first[1:3] for k in ks)

        tmo = self._tmo(self.opts.step_timeout)
        # Key steps wait for the CDC press only: HID activity alone (mouse clicks show up as
        # systemDefined, typing on the Mac as keyDown) skipped 3 keys in the 00:14 run.
        got = self.rec.wait(done, t0, tmo)
        xs = self.rec.since(t0)
        ks = _keys(xs)
        down = next((k for k in ks if k[3]), None)
        up = next((k for k in ks if down and not k[3] and k[1:3] == down[1:3]), None)
        hid = self._hid_since(t0)
        out = {"ok": got, "fw": list(down[1:3]) if down else None,
               "hold_ms": _ms(up[0] - down[0]) if down and up else None,
               "raw": [o for _, o in _events(xs)][:6]}
        if self.opts.headless:
            out["hid_activity"] = sorted({n for _, n in hid})
            if not got:
                out["timed_out_s"] = round(tmo, 1)
                if hid:
                    out["hid_only"] = True  # recorded, not used to advance
        if got and highlight is not None:
            self.done_keys.add(tuple(highlight))
        return out

    def _wait_or_hid(self, pred, t0: float, timeout: float) -> bool:
        """Wait for a CDC condition; also advance (returning False) after HID-only activity settles."""
        end = self.clock() + timeout
        while self.clock() < end:
            if self.rec.wait(pred, t0, min(0.1, max(0.0, end - self.clock()))):
                return True
            hid = self._hid_since(t0)
            if hid and self.clock() - hid[-1][0] > 0.8 and not _events(self.rec.since(t0)):
                return False
        return pred(self.rec.since(t0))

    def t_keys(self) -> None:
        if not self.opts.keys or not self._guided("keys"):
            return
        if self.opts.headless:
            self._ensure_gate()
        else:
            self.prompter.wait_enter("keys_intro", "按键测试：请先打开并聚焦一个空白文本窗口（用于观察 HID 泄漏），"
                                     "然后按设备副屏/黄色高亮提示逐个按键；无需回到终端")
        results = {}
        for row in range(1, 5):
            for col in range(1, 6):
                if self.opts.key_subset and f"{col},{row}" not in self.opts.key_subset:
                    continue
                results[f"{col},{row}"] = self._press_release(
                    f"key:{col},{row}", f"按下并松开 黄色高亮键（第{row}行 第{col}列）"
                    + (f"；不按则 {self.opts.step_timeout:.0f} 秒后自动跳过" if self.opts.headless else ""), (col, row))
        kc_form = {k: r["fw"][1] for k, r in results.items() if r["fw"] and r["fw"][0] == "kc"}
        if kc_form:
            missing = [k for k, r in results.items() if not r["ok"]]
            dup = len(set(kc_form.values())) != len(kc_form)
            self._sec("keys", "warn" if missing or dup else "pass", event_form="keycode", missing=missing,
                      duplicate_keycodes=dup, getinfo_fw_base=self.link.layout.fw_base,
                      suggested_keycode_map={f"0x{v:04x}": k for k, v in kc_form.items()},
                      note="键事件只带 keyCode：同 keycode 的键无法区分（KC_NO 净化会让所有键变 0）" if dup else "",
                      results=results)
            return
        offs = {(r["fw"][0] - (int(k.split(',')[0]) - 1), r["fw"][1] - (int(k.split(',')[1]) - 1))
                for k, r in results.items() if r["fw"]}
        missing = [k for k, r in results.items() if not r["ok"]]
        swapped = all(r["fw"] and r["fw"][0] - int(k.split(',')[1]) == r["fw"][1] - int(k.split(',')[0])
                      for k, r in results.items() if r["fw"]) and len(offs) > 1
        base = None
        if len(offs) == 1:
            ox, oy = next(iter(offs))
            base = ox if ox == oy and ox in (0, 1) else None
        lb = self.link.layout.fw_base
        st = "pass" if not missing and base is not None and base == lb else ("fail" if missing else "warn")
        self._sec("keys", st, event_fw_base=base, getinfo_fw_base=lb, offsets=sorted(offs), missing=missing,
                  transposed_suspect=swapped, results=results)

    def t_knobs(self) -> None:
        if not self.opts.knobs or not self._guided("knobs"):
            return
        self._ensure_gate()
        out = {}
        key_cells = {(r.col, r.row) for r in self.link.layout.keys}
        for knob in ("K1", "K2"):
            side = "左" if knob == "K1" else "右"
            for act, text in (("cw", f"顺时针转 {side}旋钮 {knob} 三格"), ("ccw", f"逆时针转 {side}旋钮 {knob} 三格"),
                              ("press", f"按下并松开 {side}旋钮 {knob}")):
                step = f"knob:{knob}:{act}"
                if self.remaining() <= 0:
                    out[step] = {"events": 0, "methods": [], "key_cells": [], "outside_key_grid": [], "raw": [],
                                 "skipped": "overall timeout"}
                    continue
                t0 = self._step(step, text + (f"（不动则 {self.opts.step_timeout:.0f} 秒后自动跳过）" if self.opts.headless else ""))
                tmo = self._tmo(self.opts.step_timeout)
                if self.opts.headless:
                    self._wait_or_hid(lambda xs: bool(_events(xs)), t0, tmo)
                xs = self.rec.wait_quiet(t0, tmo if not self.opts.headless else 0.05, self.opts.quiet_s, tmo)
                ks = _keys(xs)
                out[step] = {"events": len(xs), "methods": sorted({o.get("method", "?") for _, o in xs}),
                             "key_cells": sorted({(k[1], k[2]) for k in ks}, key=str),
                             "outside_key_grid": sorted({(k[1], k[2]) for k in ks if (k[1], k[2]) not in key_cells},
                                                        key=str),
                             "raw": [o for _, o in xs][:12]}
                if self.opts.headless:
                    out[step]["hid_activity"] = sorted({n for _, n in self._hid_since(t0)})
        silent = [k for k, v in out.items() if not v["events"]]
        self._sec("knobs", "warn" if silent else "pass", silent_steps=silent,
                  note="无 CDC 事件的动作可能只走 GD32 HID（需 VIA/hidutil 净化，见 §6.5）" if silent else "",
                  results=out, suggested_knob_map=_suggest_knob_map(out))

    def t_leak(self) -> None:
        if not self.opts.interactive or (not self.opts.keys and not self.opts.knobs):
            self._sec("hid_leak", "skip", reason="未执行按键/旋钮引导")
            return
        if self.opts.headless:
            self._leak_auto()
            return
        a = self.prompter.ask("hid_leak", "刚才按键/转旋钮时，Mac 上是否出现了输入字符、音量变化或快捷键？(y=有泄漏)")
        self._sec("hid_leak", {"y": "fail", "n": "pass"}.get(a, "skip"), answer=a,
                  note="P0 硬门槛：19 个非 PTT 键与两旋钮不得产生副作用 HID；泄漏→按 VIA KC_NO→现有配置→hidutil 顺序处理")

    def _guided_window(self) -> tuple[float, float] | None:
        g = self.report.get("start_gate")
        if not g:
            return None
        ts = [t for t, _ in _events(self.rec.items)]
        return (min(ts) - 1.0, self.clock()) if ts else None

    def _leak_auto(self) -> None:
        note = "P0 硬门槛：19 个非 PTT 键与两旋钮不得产生副作用 HID；泄漏→按 VIA KC_NO→现有配置→hidutil 顺序处理"
        if self.hid_monitor is None:
            self._sec("hid_leak", "manual", needs_manual_confirmation=True, reason="无 HID 监视器（非 macOS？）",
                      note=note)
            return
        from .hidwatch import correlate
        win = self._guided_window()
        if win is None:
            self._sec("hid_leak", "manual", needs_manual_confirmation=True, monitor=self.hid_monitor.info(),
                      reason="引导期间没有收到任何设备按键事件，无法关联", note=note)
            return
        ptt = self.report["sections"].get("ptt", {}).get("window")
        key_times = [t for t, *_ in _keys(self.rec.since(win[0]))]
        from .hidwatch import drop_mouse_aux
        samples = [x for x in drop_mouse_aux(self.hid_monitor.between(*win)) if x[1] in LEAK_TYPES]
        non_ptt = [x for x in samples if not (ptt and ptt[0] - 0.3 <= x[0] <= ptt[1] + 0.3)]
        c = correlate(non_ptt, key_times)
        hid_only = [k for k, r in self.report["sections"].get("keys", {}).get("results", {}).items()
                    if r.get("hid_only")]
        knob_hid = [k for k, r in self.report["sections"].get("knobs", {}).get("results", {}).items()
                    if r.get("hid_activity")]
        if c["correlated"]:
            st = "fail"
        elif c["uncorrelated"] or hid_only or knob_hid:
            st = "warn"  # activity not tied to an MK20 press (Mac keyboard/mouse?) — check samples
        else:
            st = "pass"
        self._sec("hid_leak", st, auto=True, monitor=self.hid_monitor.info(), guided_window_s=round(win[1] - win[0], 1),
                  **c, hid_only_keys=hid_only, knob_steps_with_hid=knob_hid,
                  limitation="只能看到“有无 HID 事件及类型”，看不到字符与来源设备；与 MK20 键事件时间相关(±0.35 s)即判泄漏；"
                             "期间若有人用 Mac 键盘会产生不相关样本(→warn)",
                  note=note)

    def t_ptt(self) -> None:
        if not self._guided("ptt"):
            return
        self._ensure_gate()
        text = "PTT：按住黄色高亮键（左下角）不放，心里数 1、2、3 再松开"
        t0 = self.clock()
        r = self._press_release("ptt", text, PTT_DOC)
        extra = {}
        if self.opts.headless:
            ime = "s"
            hid = self._hid_since(t0)
            extra = {"window": [t0, self.clock()], "hid_activity": sorted({n for _, n in hid}),
                     "right_option_seen": any(n == "flagsChanged" for _, n in hid),
                     "needs_manual_confirmation": "IME 语音是否被触发需目视确认（无头模式只能看到 flagsChanged）"}
        else:
            ime = self.prompter.ask("ptt_ime", "按住期间 Mac 输入法语音（右 Option）是否被触发？")
        self._sec("ptt", "pass" if r["ok"] else "warn", cdc_observed=r["ok"], hold_ms=r["hold_ms"], fw=r["fw"],
                  ime_triggered={"y": True, "n": False}.get(ime),
                  note="" if r["ok"] else "PTT 无 CDC 事件 → 设备上无法画按住动画（PRD §6.5：静态麦标）", **extra)

    def _collect_presses(self, step: str, text: str, n: int) -> list[tuple[float, float]]:
        if self.remaining() <= 0:
            return []
        t0 = self._step(step, text, TIMING_DOC)
        self.rec.wait(lambda xs: len(_pairs(_keys(xs))) >= n, t0, self._tmo(self.opts.step_timeout + n))
        return _pairs(_keys(self.rec.since(t0)))[:n]

    def t_timing(self) -> None:
        if not self.opts.timing or not self._guided("timing"):
            return
        self._ensure_gate()
        taps = self._collect_presses("timing:tap", "用正常速度单击 高亮键 5 次", 5)
        dbl = self._collect_presses("timing:double", "双击 高亮键 3 次（共 6 下）", 6)
        lng = self._collect_presses("timing:long", "长按 高亮键 约 1 秒，重复 3 次", 3)
        tap_ms = [round((u - d) * 1000) for d, u in taps]
        gaps = [round((dbl[i + 1][0] - dbl[i][0]) * 1000) for i in range(0, len(dbl) - 1, 2)]
        long_ms = [round((u - d) * 1000) for d, u in lng]
        lp, dt = LONG_PRESS * 1000, DOUBLE_TAP * 1000
        checks = {
            "tap_below_long_press": bool(tap_ms) and max(tap_ms) < lp * 0.8,
            "double_gap_below_window": bool(gaps) and max(gaps) < dt,
            "long_above_threshold": bool(long_ms) and min(long_ms) > lp,
        }
        self._sec("timing", "pass" if all(checks.values()) else "warn", tap_ms=tap_ms, double_gap_ms=gaps,
                  long_ms=long_ms, long_press_ms=lp, double_tap_ms=dt, checks=checks)

    def t_reconnect(self) -> None:
        if not self.opts.reconnect or not self._guided("reconnect"):
            return
        self._ensure_gate()
        port = self.port
        t0 = self._step("unplug", "最后一步：拔掉 MK20 的 USB 线，等 3 秒再插回（屏幕会黑，正常）"
                        if self.opts.headless else "重连测试：请拔掉 MK20 的 USB 线")
        detected = None
        while self.clock() - t0 < (self.opts.step_timeout + 10 if self.opts.headless else self.opts.step_timeout):
            if not self.link.connected or port not in self.find_ports():
                detected = self.clock() - t0
                break
            time.sleep(0.05)
        if detected is None and self.opts.headless:
            self._sec("reconnect", "skip", reason=f"{self.opts.step_timeout:.0f} s 内未拔线（自动跳过）")
            return
        self.disconnect()
        t1 = self.clock()
        self.prompter.instruct("replug", "请重新插上 MK20（等待重新握手）")
        self._progress("replug", "waiting for replug + handshake")
        port_back = ready = None
        err = ""
        # T113 cold boot takes ~35-40 s (00:03:30 hub -> 00:04:04 CDC on 10-18); allow 90 s headless.
        while self.clock() - t1 < (max(90.0, self.opts.step_timeout + 20) if self.opts.headless
                                   else self.opts.step_timeout + 20):
            if port_back is None and self.find_ports():
                port_back = self.clock() - t1
            if port_back is not None:
                try:
                    self.connect()
                    ready = self.clock() - t1
                    break
                except Exception as e:  # noqa: BLE001 — T113 may still be booting
                    err = str(e)
                    self.disconnect()
            time.sleep(0.3)
        if ready is not None:
            self._show("重连成功")
        self._sec("reconnect", "pass" if detected is not None and ready is not None else "fail",
                  unplug_detect_ms=_ms(detected), port_back_ms=_ms(port_back), handshake_ready_ms=_ms(ready),
                  same_port=self.port == port, last_error=err)

    def t_sleep(self) -> None:
        if not self.opts.sleep or self.opts.headless:
            self._sec("sleep", "skip", reason="未启用（--with-sleep；无头模式不支持）")
            return
        if not self._guided("sleep"):
            return
        if self.power is None:
            self._sec("sleep", "skip", reason="无电源监视器")
            return
        self.power.poll()
        self._show("睡眠测试：请让 Mac 睡眠 ≥15 秒再唤醒")
        a = self.prompter.wait_enter("sleep", "请让 Mac 睡眠（ 菜单→睡眠）≥15 秒，唤醒后回到这里")
        if a == "s":
            self._sec("sleep", "skip", reason="用户跳过")
            return
        ev = self.power.poll()
        slept = getattr(self.power, "last_sleep_s", 0.0)
        alive = bool(self.link and self.link.connected and self.link.show_jpeg(pattern(self.link.layout, "唤醒检查")))
        t0 = self.clock()
        rehandshake = None
        if not alive:
            self.disconnect()
            for _ in range(60):
                try:
                    self.connect()
                    rehandshake = self.clock() - t0
                    break
                except Exception:  # noqa: BLE001
                    self.disconnect()
                    time.sleep(0.5)
        self._sec("sleep", "pass" if "system_wake" in ev and (alive or rehandshake is not None) else "warn",
                  wake_detected="system_wake" in ev, slept_s=round(slept, 1), link_survived=alive,
                  rehandshake_ms=_ms(rehandshake), events=ev)

    def t_via(self) -> None:
        if not self.opts.via or self.via_open is None:
            self._sec("via", "skip", reason="未启用或缺少 hid 模块（pip install hidapi）")
            return
        from .via import KC_RALT, backup, has_side_effect
        client = self.via_open()
        b = backup(client)
        path = os.path.join(self.out_dir, "via-backup.json")
        b.save(path)
        km = b.data["keymap"]
        l0 = [k for row in km[0] for k in row] if km else []
        enc = b.data["encoders"][0] if b.data["encoders"] else []
        self._sec("via", "pass", backup=path, protocol_version=b.data["protocol_version"], layers=b.data["layers"],
                  matrix=b.data["matrix"], layer0_side_effect_keys=sum(map(has_side_effect, l0)),
                  layer0_has_ralt=KC_RALT in l0, encoders_layer0=enc,
                  note="只读导出；KC_NO 净化/PTT KC_RALT 需另行批准后用 via-restore 写入")

    # -------------------------------------------------- summary
    def suggest(self) -> dict:
        s = self.report["sections"]
        out: dict = {}
        if s.get("keys", {}).get("event_fw_base") is not None:
            out["fw_coord_base"] = s["keys"]["event_fw_base"]
        elif s.get("getinfo", {}).get("fw_base_inferred") is not None:
            out["fw_coord_base"] = s["getinfo"]["fw_base_inferred"]
        if "recommended_max_fps" in s.get("display", {}):
            out["max_fps"] = s["display"]["recommended_max_fps"]
        km = s.get("knobs", {}).get("suggested_knob_map")
        if km:
            out["knob_map"] = km
        return out


LEAK_TYPES = ("keyDown", "flagsChanged", "systemDefined", "scrollWheel")


ALWAYS = ("usb", "port", "getinfo", "backlight")


def step_plan(opts: DevTestOptions) -> list[str]:
    """Guided steps as the user sees them on the device (headless numbering)."""
    out = ["按任意键开始"] if opts.headless else []
    on = lambda n: not opts.only or n in opts.only  # noqa: E731
    if opts.keys and on("keys"):
        out += [f"按下并松开 黄色高亮键（第{r}行 第{c}列）" for r in range(1, 5) for c in range(1, 6)
                if not opts.key_subset or f"{c},{r}" in opts.key_subset]
    if opts.knobs and on("knobs"):
        for knob, side in (("K1", "左"), ("K2", "右")):
            out += [f"顺时针转 {side}旋钮 {knob} 三格", f"逆时针转 {side}旋钮 {knob} 三格", f"按下并松开 {side}旋钮 {knob}"]
    if on("ptt"):
        out.append("PTT：按住左下角键不放，数 1、2、3 再松开")
    if opts.timing and on("timing"):
        out += ["用正常速度单击 高亮键 5 次", "双击 高亮键 3 次（共 6 下）", "长按 高亮键 约 1 秒，重复 3 次"]
    if opts.reconnect and on("reconnect"):
        out.append("拔掉 USB 线，等 3 秒再插回")
    return out


def _pairs(ks) -> list[tuple[float, float]]:
    out, down = [], {}
    for t, c, r, p in ks:
        if p:
            down[(c, r)] = t
        elif (c, r) in down:
            out.append((down.pop((c, r)), t))
    return out


def _suggest_knob_map(results: dict) -> dict:
    m = {}
    for step, v in results.items():
        _, knob, act = step.split(":")
        cells = v["outside_key_grid"]
        if len(cells) == 1:
            a, b = cells[0]
            m[f"0x{b:04x}" if a == "kc" else f"{a},{b}"] = f"{knob}:{act}"
    return m


# ------------------------------------------------------------------ markdown
_ICON = {"pass": "✅", "warn": "⚠️", "fail": "❌", "skip": "⏭", "error": "💥", "manual": "👁"}


def render_markdown(rep: dict) -> str:
    L = [f"# MK20 devtest 报告", "", f"- 开始：{rep['started']}", f"- 结束：{rep.get('finished', '')}", ""]
    L += ["| 项 | 结果 | 要点 |", "|---|---|---|"]
    for name, s in rep["sections"].items():
        L.append(f"| {name} | {_ICON.get(s['status'], s['status'])} {s['status']} | {_brief(name, s)} |")
    L += ["", "## 建议配置（~/.config/mk20-agent-deck/config.toml）", "", "```toml", "[deck]"]
    sc = rep.get("suggested_config", {})
    for k in ("fw_coord_base", "max_fps"):
        if k in sc:
            L.append(f"{k} = {sc[k]}")
    if sc.get("knob_map"):
        L += ["", "[knobs]"] + [f'"{k}" = "{v}"' for k, v in sc["knob_map"].items()]
    L += ["```", "", "## 明细", ""]
    for name, s in rep["sections"].items():
        d = {k: v for k, v in s.items() if k not in ("raw", "results")}
        L += [f"### {name}", "", "```json", json.dumps(d, ensure_ascii=False, indent=1)[:4000], "```", ""]
    return "\n".join(L)


def _brief(name: str, s: dict) -> str:
    if s["status"] in ("skip", "error"):
        return s.get("reason") or s.get("error", "")
    try:
        if name == "usb":
            return f"T113 CDC={s['t113_cdc']} GD32 VIA={s['gd32_via']} {' '.join(s['notes'])}"
        if name == "port":
            return ", ".join(s["ports"]) or "无"
        if name == "getinfo":
            return f"画布 {s['canvas'][0]}×{s['canvas'][1]}，键 {s['key_rects']}，起始 {s['fw_base_inferred']}，握手 {s['handshake_ms']} ms"
        if name == "display":
            fs = s["full_screen"]
            return (f"ACK p50/p95 {s['ack'].get('p50_ms')}/{s['ack'].get('p95_ms')} ms；满屏 "
                    + "，".join(f"{k} {v['fps']} fps" for k, v in fs.items()) + f"；建议 max_fps {s['recommended_max_fps']}")
        if name == "keys":
            return f"事件起始 {s['event_fw_base']}（getInfo {s['getinfo_fw_base']}），缺失 {len(s['missing'])}"
        if name == "knobs":
            return f"静默 {len(s['silent_steps'])} 项；knob_map 建议 {len(s['suggested_knob_map'])} 项"
        if name == "ptt":
            return f"CDC 可见={s['cdc_observed']} 按住 {s['hold_ms']} ms IME={s['ime_triggered']}"
        if name == "timing":
            return f"单击 {s['tap_ms']} 双击间隔 {s['double_gap_ms']} 长按 {s['long_ms']} ms"
        if name == "reconnect":
            return f"拔线检测 {s['unplug_detect_ms']} ms，重新就绪 {s['handshake_ready_ms']} ms"
        if name == "sleep":
            return f"唤醒检测={s['wake_detected']} 睡眠 {s['slept_s']} s 链路保持={s['link_survived']}"
        if name == "via":
            return f"VIA v{s['protocol_version']} {s['layers']} 层 {s['matrix']['rows']}×{s['matrix']['cols']}，备份 {s['backup']}"
        if name == "hid_leak":
            return s.get("note", "")[:60]
    except (KeyError, TypeError, ValueError):
        pass
    return ""
