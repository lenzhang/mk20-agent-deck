"""Local web UI: real product photo + live per-key LCD overlay.  No hardware.

    cd deck && uv run python -m mk20sim --port 8766      → http://127.0.0.1:8766/
"""
from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .harness import Sim

HERE = os.path.dirname(os.path.abspath(__file__))
PHOTO = os.path.normpath(os.path.join(HERE, "..", "..", "sim", "assets", "photos", "mk20-b-top-unlit.jpg"))
# Geometry traced from the 800x800 Waveshare top-down photo (see sim/LAYOUT.md)
GEOM = {
    "photo": [800, 800],
    "keys_x": [222, 295, 367, 439, 512], "keys_y": [286, 369, 451, 534], "key_w": 66, "key_h": 76,
    "lcd_inset": [7, 6, 52],              # x, y, size of the visible LCD on the keycap face
    "sub": [284, 186, 233, 80],           # x, y, w, h of the 2.8" sub-screen window
    "knobs": {"K1": [241, 226, 25], "K2": [557, 224, 25]},
}

PAGE = r"""<!doctype html><meta charset="utf-8"><title>MK20 SIM</title>
<style>
body{background:#14161a;color:#ddd;font:14px -apple-system,system-ui,sans-serif;margin:16px;display:flex;gap:18px}
#dev{position:relative;width:800px;height:800px;flex:none;background:#fff url(/photo.jpg);border-radius:12px;user-select:none}
#dev canvas{position:absolute;left:0;top:0}
.hit{position:absolute;cursor:pointer;border-radius:6px}.hit.down{outline:3px solid #4af}
.knob{position:absolute;border-radius:50%;cursor:grab}.knob.down{outline:3px solid #fa4}
#side{flex:1;min-width:360px}h3{margin:6px 0}
#state{font:12px ui-monospace,monospace;background:#1d2026;padding:8px;border-radius:8px;white-space:pre}
#log{font:12px ui-monospace,monospace;background:#1d2026;padding:8px;border-radius:8px;height:430px;overflow:auto;white-space:pre}
.hid{color:#f9a}.agent{color:#8cf}.local{color:#ad8}.toast{color:#fd6}.input{color:#777}
.hint{color:#888;font-size:12px}
</style>
<div id=dev><canvas id=cv width=800 height=800></canvas></div>
<div id=side><h3>MK20 交互模拟器 · SIM（不连设备）</h3>
<div class=hint>鼠标按下/松开 = 键按下/抬起（长按 ≥600 ms、双击 ≤350 ms 按真实时序判定）· 旋钮：滚轮 = 旋转，点击 = 按下 ·
键盘：1-5/q-t/a-g/z-b = 四行键，[ ] = K1 转，; ' = K2 转，Shift+[ / Shift+; = 按 K1/K2</div>
<h3>状态</h3><div id=state></div><h3>事件 / 路由输出（记录，不真实发送）</h3><div id=log></div></div>
<script>
const G=__GEOM__;const dev=document.getElementById('dev'),cv=document.getElementById('cv'),cx=cv.getContext('2d');
let L=null,frame=new Image();
const post=u=>fetch(u,{method:'POST'});
function mkHit(x,y,w,h,col,row){const d=document.createElement('div');d.className='hit';Object.assign(d.style,{left:x+'px',top:y+'px',width:w+'px',height:h+'px'});
 const dn=()=>{d.classList.add('down');post(`/key?col=${col}&row=${row}&p=1`)},up=()=>{if(!d.classList.contains('down'))return;d.classList.remove('down');post(`/key?col=${col}&row=${row}&p=0`)};
 d.onmousedown=e=>{e.preventDefault();dn()};d.onmouseup=up;d.onmouseleave=up;dev.appendChild(d);return {dn,up};}
const hk={};const rows=['12345','qwert','asdfg','zxcvb'];
G.keys_y.forEach((y,r)=>G.keys_x.forEach((x,c)=>{hk[rows[r][c]]=mkHit(x,y,G.key_w,G.key_h,c+1,r+1)}));
for(const [k,[x,y,rad]] of Object.entries(G.knobs)){const d=document.createElement('div');d.className='knob';
 Object.assign(d.style,{left:(x-rad)+'px',top:(y-rad)+'px',width:2*rad+'px',height:2*rad+'px'});const n=k[1];
 d.onmousedown=e=>{e.preventDefault();d.classList.add('down');post(`/knob?k=${n}&a=down`)};
 const up=()=>{if(d.classList.contains('down')){d.classList.remove('down');post(`/knob?k=${n}&a=up`)}};d.onmouseup=up;d.onmouseleave=up;
 d.onwheel=e=>{e.preventDefault();post(`/knob?k=${n}&a=${e.deltaY>0?'cw':'ccw'}`)};dev.appendChild(d);}
const held=new Set();
document.onkeydown=e=>{if(e.repeat)return;const k=e.key.toLowerCase();
 if(hk[k]&&!held.has(k)){held.add(k);hk[k].dn();return}
 if(e.key==='['||e.key===']')post(`/knob?k=1&a=${e.key===']'?'cw':'ccw'}`);
 if(e.key===';'||e.key==="'")post(`/knob?k=2&a=${e.key==="'"?'cw':'ccw'}`);
 if(e.key==='{')post('/knob?k=1&a=down');if(e.key===':')post('/knob?k=2&a=down');};
document.onkeyup=e=>{const k=e.key.toLowerCase();if(held.has(k)){held.delete(k);hk[k].up()}
 if(e.key==='{')post('/knob?k=1&a=up');if(e.key===':')post('/knob?k=2&a=up');};
function draw(){cx.clearRect(0,0,800,800);if(!L||!frame.complete||!frame.naturalWidth)return;
 for(const k of L.keys){const x=G.keys_x[k.dc-1],y=G.keys_y[k.dr-1],[ix,iy,s]=G.lcd_inset;
  cx.drawImage(frame,k.x,k.y,k.w,k.h,x+ix,y+iy,s,s);}
 const s=L.sub,[sx,sy,sw,sh]=G.sub;cx.drawImage(frame,s.x,s.y,s.w,s.h,sx,sy,sw,sh);}
async function loop(){try{L=L||await (await fetch('/layout')).json();const img=new Image();img.onload=()=>{frame=img;draw()};img.src='/frame.jpg?'+Date.now();
 const st=await (await fetch('/state')).json();document.getElementById('state').textContent=
 `page=${st.page}  mode=${st.mode}  ptt=${st.ptt}  scroll=${st.scroll}\nselected=${st.selected}\ntoast=${st.toast||''}`;
 const lg=document.getElementById('log');lg.innerHTML=st.outputs.map(o=>`<span class=${o.kind}>${o.t.toFixed(3).padStart(9)}  ${o.kind.padEnd(6)} ${o.text.replace(/</g,'&lt;')}</span>`).join('\n');lg.scrollTop=1e9;}catch(e){}
 setTimeout(loop,120);}
loop();
</script>"""


def serve(host: str = "127.0.0.1", port: int = 8766) -> None:
    sim = Sim(realtime=True, animations=True)
    stop = threading.Event()

    def pump() -> None:
        while not stop.is_set():
            sim.step()
            time.sleep(0.02)

    threading.Thread(target=pump, daemon=True).start()
    lay = sim.layout
    keys = []
    for r in lay.keys:
        dc, dr = lay.fw_to_doc(r.col, r.row)
        keys.append({"x": r.x, "y": r.y, "w": r.w, "h": r.h, "dc": dc, "dr": dr})
    sub = lay.subscreen
    layout_json = json.dumps({"keys": keys, "sub": {"x": sub.x, "y": sub.y, "w": sub.w, "h": sub.h}}).encode()
    page = PAGE.replace("__GEOM__", json.dumps(GEOM)).encode()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _ok(self, body: bytes, ctype: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            p = urlparse(self.path).path
            if p == "/":
                self._ok(page, "text/html; charset=utf-8")
            elif p == "/photo.jpg":
                with open(PHOTO, "rb") as f:
                    self._ok(f.read(), "image/jpeg")
            elif p == "/frame.jpg":
                with sim.lock:
                    self._ok(sim.frame, "image/jpeg")
            elif p == "/layout":
                self._ok(layout_json, "application/json")
            elif p == "/state":
                with sim.lock:
                    st = sim.state()
                    st["outputs"] = [o.__dict__ for o in sim.outputs[-200:]]
                self._ok(json.dumps(st, ensure_ascii=False).encode(), "application/json")
            else:
                self.send_error(404)

        def do_POST(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            try:
                if u.path == "/key":  # doc coords from the UI → firmware coords → raw event
                    b = lay.fw_base
                    sim.raw(int(q["col"]) - 1 + b, int(q["row"]) - 1 + b, q.get("p") == "1")
                elif u.path == "/knob":
                    sim.knob(int(q["k"]), q["a"])
                else:
                    self.send_error(404)
                    return
            except (KeyError, ValueError) as e:
                self.send_error(400, str(e))
                return
            self._ok(b"ok", "text/plain")

    srv = ThreadingHTTPServer((host, port), H)
    print(f"MK20 SIM: http://{host}:{port}/", flush=True)
    try:
        srv.serve_forever()
    finally:
        stop.set()
