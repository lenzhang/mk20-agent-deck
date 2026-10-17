"""Desktop simulator: scripted screenshots, state gallery, GIF, and an
interactive local web page (no device needed).  Everything is marked SIM."""
from __future__ import annotations

import html
import io
import json
import os
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from PIL import Image, ImageDraw

from .adapters.herdr import HerdrAdapter
from .adapters.mock import MockAdapter
from .config import DeckConfig
from .controller import Controller, KeyView, Role
from .demo_data import DESKTOP_CAPS, demo_sessions
from .layout import DeviceLayout
from .link import DeviceLink
from .model import Capabilities, Mode, Session, SessionId, State, Tool
from .render.canvas import Renderer, device_mockup
from .render.fonts import font
from .render.keys import render_key
from .transport import MockDevice


class FakeClock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def demo_adapters(herdr_unavailable: str = "herdr 未安装") -> dict[Mode, MockAdapter]:
    tmux_caps = Capabilities(focus="未绑定终端 App，请在配置中设置", new_session="未配置新会话命令",
                             skill_current="投递语义未验证（P0 禁用）", skill_new_thread="tmux 无新线程预填",
                             send="投递语义未验证（P0 禁用）", stop="中断能力未按工具版本验证",
                             approve="P1：请在终端内处理")
    tmux_sessions = demo_sessions(Mode.TMUX, (Tool.CCAI, Tool.CODEX))[:5]
    for s in tmux_sessions:
        s.state, s.state_reason = State.UNKNOWN, "tmux：无关联生命周期信号"
    return {
        Mode.DESKTOP: MockAdapter(Mode.DESKTOP, demo_sessions(), caps=DESKTOP_CAPS,
                                  limited="状态受限：粗状态，待审批请看 App"),
        Mode.HERDR: MockAdapter(Mode.HERDR, unavailable=herdr_unavailable, caps=HerdrAdapter().capabilities(),
                                limited="状态受限：herdr 未联调（P0 stub）"),
        Mode.TMUX: MockAdapter(Mode.TMUX, tmux_sessions, caps=tmux_caps, limited="状态受限：tmux 仅能确认窗格存在"),
    }


def _ctl(clock: FakeClock, adapters=None, cfg: DeckConfig | None = None) -> Controller:
    c = Controller(cfg or DeckConfig(), adapters or demo_adapters(), clock=clock, wall=lambda: 1792244000.0,
                   sim=True)
    c.refresh()
    return c


def tap(c: Controller, clock: FakeClock, col: int, row: int, hold: float = 0.05) -> None:
    c.on_key(col, row, True)
    clock.advance(hold)
    c.tick()
    c.on_key(col, row, False)


def shots(out_dir: str, animations: bool = True) -> list[tuple[str, str]]:
    """Render the scripted scenario set.  Returns [(filename, caption)]."""
    os.makedirs(out_dir, exist_ok=True)
    lay = DeviceLayout.default()
    made: list[tuple[str, str]] = []

    def save(c: Controller, clock: FakeClock, name: str, caption: str, anim_t: float = 0.25) -> None:
        r = Renderer(lay, animations=animations)
        view = c.view()
        r.compose(view, clock.t - anim_t)  # seed ages so animations have a phase
        canvas = r.compose(view, clock.t)
        device_mockup(canvas, lay, title=f"MK20 Agent Deck · SIM · {caption}").save(os.path.join(out_dir, name))
        made.append((name, caption))

    clk = FakeClock()
    c = _ctl(clk)
    save(c, clk, "01-desktop-L1-default.png", "Desktop 默认 L1（未选中）")
    tap(c, clk, 3, 2)  # select A7 (OTA, needs_approval demo)
    clk.advance(0.5)
    c.toast = None
    save(c, clk, "02-desktop-L1-selected.png", "单击 A7 选中（只更新副屏）")
    tap(c, clk, 5, 1)
    save(c, clk, "03-accept-disabled-toast.png", "P0 接受键：只提示「请在 App 内处理」")
    clk.advance(2)
    c.on_key(5, 4, True)
    clk.advance(0.65)
    c.tick()
    c.on_key(5, 4, False)
    clk.advance(0.1)
    save(c, clk, "04-L4-mode-page.png", "长按模式键 → L4 模式页")
    tap(c, clk, 2, 3)  # HERD
    save(c, clk, "05-herdr-offline.png", "切到 herdr（未安装 → offline + 原因）")
    clk.advance(2)
    tap(c, clk, 5, 4)  # cycle -> tmux
    save(c, clk, "06-tmux-mode.png", "短按模式键 → tmux（状态 unknown）")
    clk.advance(2)
    tap(c, clk, 5, 4)  # cycle -> desktop (restores mapping/selection)
    clk.advance(2)
    c.on_key(1, 4, True)  # PTT held
    save(c, clk, "07-ptt-held.png", "按住 PTT（右 Option 由设备 HID 发出）")
    c.on_key(1, 4, False)

    # overflow: 12 sessions → +4
    clk2 = FakeClock()
    many = demo_sessions()
    extra = []
    for i in range(4):
        s = replace(many[i], sid=SessionId(Mode.DESKTOP, f"01a1{i:04d}-0000-7000-8000-00000000000{i}"),
                    title=f"会话{i + 9}", state=State.IDLE)
        extra.append(s)
    ads = demo_adapters()
    ads[Mode.DESKTOP].sessions = many + extra
    c2 = _ctl(clk2, ads)
    tap(c2, clk2, 1, 1)
    c2.on_key(2, 1, True)
    clk2.advance(0.65)
    c2.tick()
    c2.on_key(2, 1, False)  # pin A2
    clk2.advance(2)
    save(c2, clk2, "08-overflow-12-sessions.png", "12 个会话：副屏 +4，A2 钉扎")

    gallery(os.path.join(out_dir, "09-state-gallery-static.png"))
    made.append(("09-state-gallery-static.png", "AC10 单帧状态图鉴（动画关闭）"))
    gif(os.path.join(out_dir, "10-desktop-animated.gif"))
    made.append(("10-desktop-animated.gif", "动画预览（全局 ≤8fps，单路注意力动画）"))
    via_protocol(os.path.join(out_dir, "11-via-protocol-mockdevice.png"))
    made.append(("11-via-protocol-mockdevice.png", "经 Protocol A → MockDevice 回解的整画布（640×656）"))
    write_index(out_dir, made)
    return made


def gallery(path: str) -> None:
    """All 10 base states × {codex, ccai} × {normal, selected} + overlays, animations off."""
    states = list(State)
    cell, pad = 128, 12
    cols = len(states)
    rows = 5
    W = pad + cols * (cell + pad)
    H = 40 + rows * (cell + pad + 18) + pad
    img = Image.new("RGB", (W, H), (24, 25, 28))
    d = ImageDraw.Draw(img)
    d.text((pad, 10), "状态图鉴（单帧、动画关闭）· 行：codex / codex 选中 / ccai / ccai 选中 / 叠加与命令键",
           font=font(16, bold=True), fill=(230, 230, 235))
    for ci, st in enumerate(states):
        for ri, (tool, sel) in enumerate(((Tool.CODEX, False), (Tool.CODEX, True), (Tool.CCAI, False),
                                          (Tool.CCAI, True))):
            kv = KeyView(1, 1, Role.AGENT, state=st, tool=tool, title="会话名", selected=sel and st != State.UNASSIGNED)
            if st == State.UNASSIGNED:
                kv.label = "空槽"
            face = render_key(kv, 5.0, 0.0, animations=False)
            x, y = pad + ci * (cell + pad), 40 + ri * (cell + pad + 18)
            img.paste(face, (x, y))
            d.text((x + cell / 2, y + cell + 9), st.value, font=font(12), fill=(170, 170, 175), anchor="mm")
    extras = [
        KeyView(1, 4, Role.PTT, label="按住说话", active=True),
        KeyView(1, 4, Role.PTT, label="按住说话"),
        KeyView(1, 1, Role.AGENT, state=State.UNASSIGNED, lost=True, pinned=True, label="失联钉扎"),
        KeyView(1, 1, Role.AGENT, state=State.RUNNING, tool=Tool.CODEX, title="钉扎", pinned=True),
        KeyView(2, 4, Role.STOP, label="停止", progress=3),
        KeyView(5, 4, Role.MODE, label="DESK", mode=Mode.DESKTOP),
        KeyView(5, 4, Role.MODE, label="HERD", mode=Mode.HERDR),
        KeyView(5, 4, Role.MODE, label="TMUX", mode=Mode.TMUX),
        KeyView(1, 3, Role.SKILL, label="审 PR", badge="+新"),
        KeyView(5, 1, Role.ACCEPT, label="接受", disabled="请在 App 内处理（审批代答为 P1）"),
    ]
    names = ["PTT 按住", "PTT 静态", "失联钉扎", "钉扎", "停止进度 3/4", "模式 DESK", "模式 HERD", "模式 TMUX",
             "技能 +新", "接受(禁用)"]
    for ci, kv in enumerate(extras):
        face = render_key(kv, 5.0, 0.0, animations=False)
        x, y = pad + ci * (cell + pad), 40 + 4 * (cell + pad + 18)
        img.paste(face, (x, y))
        d.text((x + cell / 2, y + cell + 9), names[ci], font=font(12), fill=(170, 170, 175), anchor="mm")
    img.save(path)


def gif(path: str, seconds: float = 3.0) -> None:
    clk = FakeClock()
    c = _ctl(clk)
    tap(c, clk, 3, 2)
    clk.advance(1.6)
    lay = DeviceLayout.default()
    r = Renderer(lay)
    frames = []
    fps = 8
    for i in range(int(seconds * fps)):
        v = c.view()
        frames.append(device_mockup(r.compose(v, clk.t), lay, scale=0.6).convert("P", palette=Image.ADAPTIVE))
        clk.advance(1 / fps)
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=int(1000 / fps), loop=0)


def via_protocol(path: str) -> None:
    """End-to-end: controller → renderer → Protocol A frames → MockDevice → decode."""
    dev = MockDevice()
    link = DeviceLink(dev, ack_timeout=2.0, preamble_flush=0)
    link.start()
    lay = link.handshake()
    clk = FakeClock()
    c = _ctl(clk)
    tap(c, clk, 2, 1)
    r = Renderer(lay)
    assert link.show_jpeg(r.jpeg(r.compose(c.view(), clk.t)))
    Image.open(io.BytesIO(dev.frames[-1])).save(path)
    link.close()


def write_index(out_dir: str, made: list[tuple[str, str]]) -> None:
    items = "\n".join(
        f'<figure><img src="{html.escape(n)}" loading="lazy"><figcaption>{html.escape(cap)}</figcaption></figure>'
        for n, cap in made)
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(f"""<!doctype html><meta charset="utf-8"><title>MK20 Agent Deck · SIM screenshots</title>
<style>body{{background:#16181c;color:#ddd;font-family:-apple-system,system-ui,sans-serif;margin:24px}}
figure{{display:inline-block;margin:12px;vertical-align:top}}img{{max-width:760px;border-radius:12px}}
figcaption{{margin-top:6px}}</style><h1>MK20 Agent Deck · P0 模拟器截图（SIM）</h1>{items}""")


# ---------------------------------------------------------------------------- web sim
PAGE = """<!doctype html><meta charset="utf-8"><title>MK20 Deck SIM</title>
<style>body{background:#16181c;color:#ddd;font-family:-apple-system,system-ui,sans-serif;margin:20px}
#wrap{position:relative;display:inline-block}#cv{display:block;border-radius:10px}
.k{position:absolute;cursor:pointer}.k:active{outline:2px solid #fff}
button{margin:4px;padding:6px 10px;background:#333;color:#eee;border:1px solid #555;border-radius:6px}
#log{white-space:pre;font:12px monospace;color:#aaa;max-height:200px;overflow:auto}</style>
<h2>MK20 Agent Deck · SIM（__SRC__）</h2>
<div id=wrap><img id=cv src="/frame.jpg"></div>
<div>K1 <button onmousedown="kn(1,'down')" onmouseup="kn(1,'up')">按</button><button onclick="kn(1,'ccw')">⟲</button><button onclick="kn(1,'cw')">⟳</button>
&nbsp; K2 <button onmousedown="kn(2,'down')" onmouseup="kn(2,'up')">按</button><button onclick="kn(2,'ccw')">⟲</button><button onclick="kn(2,'cw')">⟳</button></div>
<div id=log></div>
<script>
const L=__LAYOUT__;const w=document.getElementById('wrap');
for(const r of L.keys){const d=document.createElement('div');d.className='k';
d.style.left=r.x+'px';d.style.top=r.y+'px';d.style.width=r.w+'px';d.style.height=r.h+'px';
d.onmousedown=()=>fetch(`/key?col=${r.dc}&row=${r.dr}&pressed=1`,{method:'POST'});
d.onmouseup=()=>fetch(`/key?col=${r.dc}&row=${r.dr}&pressed=0`,{method:'POST'});w.appendChild(d);}
function kn(k,a){fetch(`/knob?k=${k}&a=${a}`,{method:'POST'})}
setInterval(()=>{document.getElementById('cv').src='/frame.jpg?'+Date.now()},150);
setInterval(async()=>{const r=await fetch('/log');document.getElementById('log').textContent=await r.text()},1000);
</script>"""


def serve(daemon, host: str = "127.0.0.1", port: int = 8765, source: str = "demo") -> None:
    latest = {"jpeg": b""}
    daemon.sink = lambda b: latest.__setitem__("jpeg", b)
    stop = threading.Event()
    th = threading.Thread(target=daemon.run, args=(stop,), daemon=True)
    th.start()
    lay = daemon.renderer.layout
    keys = [{"x": r.x, "y": r.y, "w": r.w, "h": r.h, "dc": lay.fw_to_doc(r.col, r.row)[0],
             "dr": lay.fw_to_doc(r.col, r.row)[1]} for r in lay.keys]
    page = PAGE.replace("__LAYOUT__", json.dumps({"keys": keys})).replace("__SRC__", html.escape(source))

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _ok(self, body: bytes, ctype: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/":
                self._ok(page.encode(), "text/html; charset=utf-8")
            elif u.path == "/frame.jpg":
                self._ok(latest["jpeg"], "image/jpeg")
            elif u.path == "/log":
                self._ok("\n".join(daemon.controller.log[-30:]).encode(), "text/plain; charset=utf-8")
            else:
                self.send_error(404)

        def do_POST(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if u.path == "/key":
                daemon.inject_key(int(q["col"]), int(q["row"]), q.get("pressed") == "1")
            elif u.path == "/knob":
                daemon.inject_knob(int(q["k"]), q["a"])
            else:
                self.send_error(404)
                return
            self._ok(b"ok", "text/plain")

    srv = ThreadingHTTPServer((host, port), H)
    print(f"SIM: http://{host}:{port}/  (source={source})")
    try:
        srv.serve_forever()
    finally:
        stop.set()
