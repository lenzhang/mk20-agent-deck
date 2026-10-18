"""Local web UI: real product photo + live per-key LCD overlay.  No hardware.

    cd deck && uv run python -m mk20sim --port 8766      → http://127.0.0.1:8766/
"""
from __future__ import annotations

import base64
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .cases import load_cases, run_case, save_draft
from .harness import KEY_NAMES, Sim

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
#log{font:12px ui-monospace,monospace;background:#1d2026;padding:8px;border-radius:8px;height:170px;overflow:auto;white-space:pre}
#cases,#results{background:#1d2026;padding:8px;border-radius:8px;max-height:230px;overflow:auto;font-size:12px}
.grp{color:#9ab;font-weight:600;margin-top:4px}.cd{color:#888}.pass{color:#6d6}.fail{color:#f66}.run{color:#fd6}
button,select{background:#2a2e36;color:#ddd;border:1px solid #444;border-radius:6px;padding:4px 8px;margin:2px}
#rec.on{background:#a22;color:#fff}#banner{font:12px ui-monospace,monospace;color:#fd6;min-height:16px}
.fbox{margin:4px 0 8px 14px;color:#f99;white-space:pre-wrap}.fbox img{width:320px;border:1px solid #533;border-radius:6px;display:block;margin-top:4px}
.hid{color:#f9a}.agent{color:#8cf}.local{color:#ad8}.toast{color:#fd6}.input{color:#777}
.hint{color:#888;font-size:12px}
</style>
<div id=dev><canvas id=cv width=800 height=800></canvas></div>
<div id=side><h3>MK20 交互模拟器 · SIM（不连设备）</h3>
<div class=hint>鼠标按下/松开 = 键按下/抬起（长按 ≥600 ms、双击 ≤350 ms 按真实时序判定）· 旋钮：滚轮 = 旋转，点击 = 按下 ·
键盘：1-5/q-t/a-g/z-b = 四行键，[ ] = K1 转，; ' = K2 转，Shift+[ / Shift+; = 按 K1/K2</div>
<h3>状态</h3><div id=state></div>
<div class=hint style="margin-top:6px">延迟注入 (ms，±抖动)：输入 GD32→T113 <input id=li size=3 value=0>±<input id=lij size=2 value=0>
 路由 <input id=lr size=3 value=0>±<input id=lrj size=2 value=0> 推帧 <input id=lf size=3 value=0>±<input id=lfj size=2 value=0>
 <button onclick="setLat()">应用</button><button onclick="preset(30,15,10,5,40,20)">30ms</button><button onclick="preset(80,40,20,10,120,40)">80ms</button><button onclick="preset(0,0,0,0,0,0)">0</button></div>
<h3>测试用例 <span class=hint>（与 pytest / run-cases 同一份 mk20sim/cases/*.json）</span></h3>
<div><button onclick="runSel()">运行所选</button><button onclick="runAll()">全部运行</button><button onclick="post('/run/stop')">停止</button>
速度 <select id=speed><option value=1x>实时 1x</option><option value=fast selected>快速</option><option value=instant>瞬时</option></select>
<button id=rec onclick="toggleRec()">● 录制</button></div>
<div id=banner></div><div id=cases></div><h3>结果</h3><div id=results></div>
<h3>事件 / 路由输出（记录，不真实发送）</h3><div id=log></div></div>
<script>
const G=__GEOM__;const dev=document.getElementById('dev'),cv=document.getElementById('cv'),cx=cv.getContext('2d');
let L=null,frame=new Image();
let REC=null;const post=(u,body)=>{if(REC&&!u.startsWith('/run')&&!u.startsWith('/record'))REC.push({t:performance.now(),u});
 return fetch(u,{method:'POST',body:body?JSON.stringify(body):undefined})};
const hitEl={},knobEl={};let RUN=null,NAMES={},expanded=new Set();
function mkHit(x,y,w,h,col,row){const d=document.createElement('div');d.className='hit';Object.assign(d.style,{left:x+'px',top:y+'px',width:w+'px',height:h+'px'});
 const dn=()=>{d.classList.add('down');post(`/key?col=${col}&row=${row}&p=1`)},up=()=>{if(!d.classList.contains('down'))return;d.classList.remove('down');post(`/key?col=${col}&row=${row}&p=0`)};
 d.onmousedown=e=>{e.preventDefault();dn()};d.onmouseup=up;d.onmouseleave=up;dev.appendChild(d);hitEl[col+','+row]=d;return {dn,up};}
const hk={};const rows=['12345','qwert','asdfg','zxcvb'];
G.keys_y.forEach((y,r)=>G.keys_x.forEach((x,c)=>{hk[rows[r][c]]=mkHit(x,y,G.key_w,G.key_h,c+1,r+1)}));
for(const [k,[x,y,rad]] of Object.entries(G.knobs)){const d=document.createElement('div');d.className='knob';
 Object.assign(d.style,{left:(x-rad)+'px',top:(y-rad)+'px',width:2*rad+'px',height:2*rad+'px'});const n=k[1];
 d.onmousedown=e=>{e.preventDefault();d.classList.add('down');post(`/knob?k=${n}&a=down`)};
 const up=()=>{if(d.classList.contains('down')){d.classList.remove('down');post(`/knob?k=${n}&a=up`)}};d.onmouseup=up;d.onmouseleave=up;
 d.onwheel=e=>{e.preventDefault();post(`/knob?k=${n}&a=${e.deltaY>0?'cw':'ccw'}`)};dev.appendChild(d);knobEl[k]=d;}
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
async function loop(){try{L=L||await (await fetch('/layout')).json();RUN=await (await fetch('/run/state')).json();
 const live=!(RUN.running||RUN.hold_view);const img=new Image();img.onload=()=>{frame=img;draw()};img.src=(live?'/frame.jpg?':'/run/frame.jpg?')+Date.now();
 renderRun();
 const st=await (await fetch('/state')).json();document.getElementById('state').textContent=
 `page=${st.page}  mode=${st.mode}  ptt=${st.ptt}  scroll=${st.scroll}\nselected=${st.selected}\ntoast=${st.toast||''}`+
 `\nlatency: in ${st.latency.input_ms}±${st.latency.input_jitter_ms} / router ${st.latency.router_ms}±${st.latency.router_jitter_ms} / frame ${st.latency.frame_ms}±${st.latency.frame_jitter_ms} ms  queued=${st.queued_inputs} lag=${st.frame_lag_ms}`+
 (st.settings.active?`\nSETTINGS tab=${st.settings.tab} focus=${st.settings.focus} pending=${JSON.stringify(st.settings.pending)} test=${st.settings.test||''}`:`\n(L4 上 K1 短按 = 进入设置)`);
 const lg=document.getElementById('log');const outs=live?st.outputs:(RUN.outputs||[]);
 lg.dataset.src=live?'live':'run';lg.innerHTML=(live?'':'<span class=run>— 用例运行日志: '+RUN.current+' —</span>\n')+outs.map(o=>`<span class=${o.kind}>${o.t.toFixed(3).padStart(9)}  ${o.kind.padEnd(6)} ${o.text.replace(/</g,'&lt;')}</span>`).join('\n');lg.scrollTop=1e9;}catch(e){}
 setTimeout(loop,120);}

async function loadCases(){const cs=await (await fetch('/cases')).json();NAMES=cs.names;let g='',h='';
 for(const c of cs.cases){if(c.group!==g){g=c.group;h+=`<div class=grp>${g}</div>`}
  h+=`<label><input type=checkbox class=ck value="${c.id}"> ${c.name} <span class=cd>— ${c.desc} (${c.steps} 步)</span></label><br>`}
 document.getElementById('cases').innerHTML=h;}
function runIds(ids){fetch('/run',{method:'POST',body:JSON.stringify({ids,speed:document.getElementById('speed').value})})}
function runSel(){runIds([...document.querySelectorAll('.ck:checked')].map(e=>e.value))}
function runAll(){runIds([...document.querySelectorAll('.ck')].filter(e=>!e.value.startsWith('drafts/')).map(e=>e.value))}
function setLat(){const v=id=>+document.getElementById(id).value||0;
 fetch('/latency',{method:'POST',body:JSON.stringify({input_ms:v('li'),input_jitter_ms:v('lij'),router_ms:v('lr'),router_jitter_ms:v('lrj'),frame_ms:v('lf'),frame_jitter_ms:v('lfj')})})}
function preset(...a){['li','lij','lr','lrj','lf','lfj'].forEach((id,i)=>document.getElementById(id).value=a[i]);setLat()}
function renderRun(){if(!RUN)return;const hl=new Set(RUN.highlight||[]);
 for(const [k,el] of Object.entries(hitEl))el.classList.toggle('down',hl.has(k));for(const [k,el] of Object.entries(knobEl))el.classList.toggle('down',hl.has(k));
 document.getElementById('banner').textContent=RUN.running?`▶ ${RUN.current}  步骤 ${RUN.step}/${RUN.nsteps}: ${JSON.stringify(RUN.step_src)}`:(RUN.results.length?'已完成 · 键盘停留在最后一个用例画面（点任一键回到实时模式）':'');
 const p=RUN.results.filter(r=>r.ok).length,f=RUN.results.length-p;
 document.getElementById('results').innerHTML=(RUN.results.length?`<div>${p} 通过 · <span class=fail>${f} 失败</span></div>`:'')+RUN.results.map(r=>
  `<div class=${r.ok?'pass':'fail'} style="cursor:pointer" onclick="tog('${r.id}')">${r.ok?'✔':'✘'} ${r.id} <span class=cd>${r.duration_ms} ms 墙钟 / ${r.sim_ms} ms 模拟</span></div>`+
  (!r.ok&&(expanded.has(r.id)||location.hash=='#expand')?`<div class=fbox>${r.failure?r.failure.text:r.error}${r.failure?`\n期望: ${JSON.stringify(r.failure.expected)}\n实际: ${JSON.stringify(r.failure.actual)}`:''}<img src="/run/screen?id=${encodeURIComponent(r.id)}"></div>`:'')).join('');}
function tog(id){expanded.has(id)?expanded.delete(id):expanded.add(id)}
document.getElementById('dev').addEventListener('mousedown',()=>{if(RUN&&RUN.hold_view&&!RUN.running)post('/run/release')},true);
function nameOf(c,r){for(const [n,v] of Object.entries(NAMES))if(v[0]==c&&v[1]==r&&!['DESK','HERD','TMUX','BACK'].includes(n))return n;return c+','+r}
function toggleRec(){const b=document.getElementById('rec');if(!REC){REC=[];b.classList.add('on');b.textContent='■ 停止录制';return}
 const ev=REC;REC=null;b.classList.remove('on');b.textContent='● 录制';const steps=[];let last=ev.length?ev[0].t:0;const open={};
 for(const e of ev){const u=new URL(e.u,location.href),q=u.searchParams,gap=Math.round(e.t-last);last=e.t;
  if(gap>0&&steps.length)steps.push({wait:gap});
  if(u.pathname=='/key'){const n=nameOf(q.get('col'),q.get('row'));if(q.get('p')=='1'){steps.push({down:n});open[n]=steps.length-1}
   else{const i=open[n];if(i!==undefined&&i===steps.length-2&&steps[i+1].wait!==undefined){const hold=steps[i+1].wait;steps.splice(i,2,{press:n,hold})}else steps.push({up:n});delete open[n]}}
  else if(u.pathname=='/knob'){const a=q.get('a');steps.push({knob:'K'+q.get('k'),action:a})}}
 const name=prompt('用例名（保存到 cases/drafts/，之后补 expect）','rec_'+new Date().toISOString().slice(11,19).replace(/:/g,''));
 if(name)fetch('/record/save',{method:'POST',body:JSON.stringify({name,steps})}).then(r=>r.text()).then(t=>{alert('已保存 '+t);loadCases()});}
loadCases();loop();
</script>"""


SPEED = {"1x": 1.0, "fast": 0.2, "instant": 0.0}


class Runner:
    """Runs cases on private (fake-clock) Sims, pacing ticks in wall time so the UI can replay them."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = False
        self.hold_view = False
        self.stop_ev = threading.Event()
        self.frame = b""
        self.current, self.step, self.nsteps, self.step_src, self.highlight = "", 0, 0, None, []
        self.results: list[dict] = []
        self.full: dict[str, dict] = {}
        self.sim = None

    def state(self) -> dict:
        with self.lock:
            return {"running": self.running, "hold_view": self.hold_view, "current": self.current, "step": self.step,
                    "outputs": [o.__dict__ for o in (self.sim.outputs[-150:] if self.sim else [])],
                    "nsteps": self.nsteps, "step_src": self.step_src, "highlight": self.highlight,
                    "results": self.results}

    def start(self, ids: list[str], speed: str) -> None:
        if self.running:
            return
        cases = {c.id: c for c in load_cases(include_drafts=True)}
        todo = [cases[i] for i in ids if i in cases]
        self.stop_ev.clear()
        with self.lock:
            self.running, self.hold_view, self.results, self.full = True, True, [], {}
        threading.Thread(target=self._run, args=(todo, SPEED.get(speed, 0.2)), daemon=True).start()

    def _run(self, todo, factor: float) -> None:
        class Stopped(Exception):
            pass

        def observer(sim, dt):
            if self.stop_ev.is_set():
                raise Stopped()
            with self.lock:
                self.frame, self.highlight = sim.frame, sorted(
                    ",".join(map(str, KEY_NAMES[h])) if h in KEY_NAMES else h for h in sim.highlight)
            if factor:
                time.sleep(max(dt * factor, 0.12 * factor if dt == 0 else 0))

        def on_step(i, src, sim):
            with self.lock:
                self.step, self.step_src = i, src
            if factor:
                time.sleep(0.15 * factor)

        try:
            for c in todo:
                if self.stop_ev.is_set():
                    break
                with self.lock:
                    self.current, self.nsteps, self.step = c.id, len(c.steps), 0
                from .harness import Latency
                sim_c = Sim(latency=Latency.from_dict(c.latency))
                with self.lock:
                    self.sim = sim_c
                r = run_case(c, observer=observer, on_step=on_step, sim=sim_c).to_json()
                if r["error"] and "Stopped" in r["error"]:
                    break
                with self.lock:
                    self.full[c.id] = r
                    self.results.append({k: v for k, v in r.items() if k not in ("screen", "outputs")})
        finally:
            with self.lock:
                self.running, self.highlight, self.step_src = False, [], None


def serve(host: str = "127.0.0.1", port: int = 8766) -> None:
    sim = Sim(realtime=True, animations=True)
    stop = threading.Event()

    def pump() -> None:
        while not stop.is_set():
            sim.step()
            time.sleep(0.02)

    threading.Thread(target=pump, daemon=True).start()
    run = Runner()
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
            elif p == "/cases":
                cs = [{"id": c.id, "group": c.group, "name": c.name, "desc": c.desc, "steps": len(c.steps),
                       "draft": c.draft} for c in load_cases(include_drafts=True)]
                self._ok(json.dumps({"cases": cs, "names": KEY_NAMES}, ensure_ascii=False).encode(), "application/json")
            elif p == "/run/state":
                self._ok(json.dumps(run.state(), ensure_ascii=False).encode(), "application/json")
            elif p == "/run/frame.jpg":
                self._ok(run.frame or sim.frame, "image/jpeg")
            elif p == "/run/screen":
                q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
                r = run.full.get(q.get("id", ""))
                if not r or not r.get("screen"):
                    self.send_error(404)
                    return
                self._ok(base64.b64decode(r["screen"]), "image/png")
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
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            if u.path == "/run":
                run.start(body.get("ids") or [], body.get("speed", "fast"))
                self._ok(b"ok", "text/plain")
                return
            if u.path in ("/run/stop", "/run/release"):
                run.stop_ev.set() if u.path == "/run/stop" else None
                run.hold_view = False
                self._ok(b"ok", "text/plain")
                return
            if u.path == "/latency":
                from .harness import Latency
                sim.set_latency(Latency.from_dict(body))
                self._ok(b"ok", "text/plain")
                return
            if u.path == "/record/save":
                path = save_draft(str(body.get("name", "recording")), list(body.get("steps") or []))
                self._ok(os.path.relpath(path, os.path.dirname(HERE)).encode(), "text/plain")
                return
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
