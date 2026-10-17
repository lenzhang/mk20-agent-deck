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
    out = []
    for t, o in xs:
        if o.get("method") == "keyStateChanged":
            p = o.get("parameters") or {}
            if pressed is None or bool(p.get("pressed")) == pressed:
                out.append((t, int(p.get("col", -1)), int(p.get("row", -1)), bool(p.get("pressed"))))
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
            noise: bool = False, seed: int = 0, quality: int = 85) -> bytes:
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
        fill = (250, 200, 40) if hl else ((40, 70, 120) if (doc[0] + doc[1]) % 2 else (60, 60, 70))
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
        d.text((sub.x + 8, sub.y + 6), "MK20 devtest", font=f_small, fill=(0, 200, 120))
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

    # -------------------------------------------------- plumbing
    def _sec(self, name: str, status: str, **kw) -> dict:
        s = {"status": status, **kw}
        self.report["sections"][name] = s
        return s

    def _show(self, note: str, highlight=None) -> None:
        if self.link and self.link.connected:
            try:
                self.link.show_jpeg(pattern(self.link.layout, note, highlight))
            except Exception:  # noqa: BLE001
                pass

    def connect(self) -> DeviceLayout:
        ports = [self.opts.port] if self.opts.port != "auto" else self.find_ports()
        if not ports:
            raise ConnectionError("未找到 MK20 CDC-ACM 端口")
        self.port = ports[0]
        tr = self.open_transport(self.port)
        self.link = DeviceLink(tr, on_json=self.rec.add)
        self.link.start()
        return self.link.handshake(None)

    def disconnect(self) -> None:
        if self.link:
            self.link.close()
        self.link = None

    def run(self) -> dict:
        os.makedirs(self.out_dir, exist_ok=True)
        self.report = {"format": "mk20deck-devtest/1",
                       "started": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                       "options": asdict(self.opts), "sections": {}}
        steps = [("usb", self.t_usb), ("port", self.t_port), ("getinfo", self.t_getinfo),
                 ("display", self.t_display), ("backlight", self.t_backlight),
                 ("keys", self.t_keys), ("knobs", self.t_knobs), ("hid_leak", self.t_leak),
                 ("ptt", self.t_ptt), ("timing", self.t_timing), ("reconnect", self.t_reconnect),
                 ("sleep", self.t_sleep), ("via", self.t_via)]
        for name, fn in steps:
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                self._sec(name, "error", error=f"{type(e).__name__}: {e}")
        try:
            self._show("devtest 完成，可拔线或运行 mk20deck run")
        finally:
            self.disconnect()
        self.report["finished"] = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
        self.report["suggested_config"] = self.suggest()
        self.report["raw_events"] = [{"t": round(t, 3), **o} for t, o in _events(self.rec.items)][:2000]
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
        return self._need_link(name)

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
            if self.opts.interactive else "s"
        res["visual_ok"] = {"y": True, "n": False}.get(vis)
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
        self._show(text, highlight)
        t0 = self.clock()
        self.prompter.instruct(step, text)
        got = self.rec.wait(lambda xs: bool(_keys(xs, True)) and any(
            not k[3] and (k[1], k[2]) == _keys(xs, True)[0][1:3] for k in _keys(xs)), t0, self.opts.step_timeout)
        xs = self.rec.since(t0)
        ks = _keys(xs)
        down = next((k for k in ks if k[3]), None)
        up = next((k for k in ks if down and not k[3] and k[1:3] == down[1:3]), None)
        return {"ok": got, "fw": list(down[1:3]) if down else None,
                "hold_ms": _ms(up[0] - down[0]) if down and up else None,
                "raw": [o for _, o in _events(xs)][:6]}

    def t_keys(self) -> None:
        if not self.opts.keys or not self._guided("keys"):
            return
        self.prompter.wait_enter("keys_intro", "按键测试：请先打开并聚焦一个空白文本窗口（用于观察 HID 泄漏），"
                                 "然后按设备副屏/黄色高亮提示逐个按键；无需回到终端")
        results = {}
        for row in range(1, 5):
            for col in range(1, 6):
                results[f"{col},{row}"] = self._press_release(f"key:{col},{row}", f"按下并松开 第{row}行 第{col}列 键",
                                                              (col, row))
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
        out = {}
        key_cells = {(r.col, r.row) for r in self.link.layout.keys}
        for knob in ("K1", "K2"):
            side = "左" if knob == "K1" else "右"
            for act, text in (("cw", f"顺时针转 {side}旋钮 {knob} 三格"), ("ccw", f"逆时针转 {side}旋钮 {knob} 三格"),
                              ("press", f"按下并松开 {side}旋钮 {knob}")):
                step = f"knob:{knob}:{act}"
                self._show(text)
                t0 = self.clock()
                self.prompter.instruct(step, text)
                xs = self.rec.wait_quiet(t0, self.opts.step_timeout, self.opts.quiet_s, self.opts.step_timeout)
                ks = _keys(xs)
                out[step] = {"events": len(xs), "methods": sorted({o.get("method", "?") for _, o in xs}),
                             "key_cells": sorted({(k[1], k[2]) for k in ks}),
                             "outside_key_grid": sorted({(k[1], k[2]) for k in ks if (k[1], k[2]) not in key_cells}),
                             "raw": [o for _, o in xs][:12]}
        silent = [k for k, v in out.items() if not v["events"]]
        self._sec("knobs", "warn" if silent else "pass", silent_steps=silent,
                  note="无 CDC 事件的动作可能只走 GD32 HID（需 VIA/hidutil 净化，见 §6.5）" if silent else "",
                  results=out, suggested_knob_map=_suggest_knob_map(out))

    def t_leak(self) -> None:
        if not self.opts.interactive or (not self.opts.keys and not self.opts.knobs):
            self._sec("hid_leak", "skip", reason="未执行按键/旋钮引导")
            return
        a = self.prompter.ask("hid_leak", "刚才按键/转旋钮时，Mac 上是否出现了输入字符、音量变化或快捷键？(y=有泄漏)")
        self._sec("hid_leak", {"y": "fail", "n": "pass"}.get(a, "skip"), answer=a,
                  note="P0 硬门槛：19 个非 PTT 键与两旋钮不得产生副作用 HID；泄漏→按 VIA KC_NO→现有配置→hidutil 顺序处理")

    def t_ptt(self) -> None:
        if not self._guided("ptt"):
            return
        text = "按住左下角 PTT 键约 2 秒后松开（如 IME 语音被触发属正常）"
        r = self._press_release("ptt", text, PTT_DOC)
        ime = self.prompter.ask("ptt_ime", "按住期间 Mac 输入法语音（右 Option）是否被触发？")
        self._sec("ptt", "pass" if r["ok"] else "warn", cdc_observed=r["ok"], hold_ms=r["hold_ms"], fw=r["fw"],
                  ime_triggered={"y": True, "n": False}.get(ime),
                  note="" if r["ok"] else "PTT 无 CDC 事件 → 设备上无法画按住动画（PRD §6.5：静态麦标）")

    def _collect_presses(self, step: str, text: str, n: int) -> list[tuple[float, float]]:
        self._show(text, TIMING_DOC)
        t0 = self.clock()
        self.prompter.instruct(step, text)
        self.rec.wait(lambda xs: len(_pairs(_keys(xs))) >= n, t0, self.opts.step_timeout + n)
        return _pairs(_keys(self.rec.since(t0)))[:n]

    def t_timing(self) -> None:
        if not self.opts.timing or not self._guided("timing"):
            return
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
        port = self.port
        self._show("重连测试：请拔掉 MK20 的 USB 线")
        t0 = self.clock()
        self.prompter.instruct("unplug", "请拔掉 MK20 的 USB 线（等待检测）")
        detected = None
        while self.clock() - t0 < self.opts.step_timeout:
            if not self.link.connected or port not in self.find_ports():
                detected = self.clock() - t0
                break
            time.sleep(0.05)
        self.disconnect()
        t1 = self.clock()
        self.prompter.instruct("replug", "请重新插上 MK20（等待重新握手）")
        port_back = ready = None
        err = ""
        while self.clock() - t1 < self.opts.step_timeout + 20:
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
        if not self.opts.sleep:
            self._sec("sleep", "skip", reason="未启用（--with-sleep）")
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
            m[f"{cells[0][0]},{cells[0][1]}"] = f"{knob}:{act}"
    return m


# ------------------------------------------------------------------ markdown
_ICON = {"pass": "✅", "warn": "⚠️", "fail": "❌", "skip": "⏭", "error": "💥"}


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
