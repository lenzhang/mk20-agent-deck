#include "web.h"
#include "mbedtls/base64.h"
#include "mbedtls/sha256.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
/* Static local pages, one inline <style> and one shared inline <script>. The CSP
 * carries SHA-256 hashes computed from these exact bytes at first use. */
static const char style[] =
"body{font:16px system-ui,-apple-system,\"PingFang SC\",sans-serif;max-width:860px;margin:1.5rem auto;padding:0 1rem;background:#f5f6f8;color:#182331}fieldset,section{background:#fff;border:1px solid #ccd3de;border-radius:8px;padding:1rem;margin:1rem 0}label{display:block;margin:.7rem 0}input,select,textarea,button{font:inherit;padding:.5rem;max-width:100%;box-sizing:border-box}input[type=checkbox]{padding:0;margin-right:.4rem}textarea{width:100%}button{margin:.3rem .3rem .3rem 0;cursor:pointer}button.danger{background:#a21818;color:#fff}img{width:100%;height:auto}small{color:#46556a}nav a{margin-right:1rem}#message{white-space:pre-wrap;background:#e7edf7;padding:.8rem 1rem;border-radius:6px}dl{display:grid;grid-template-columns:max-content 1fr;gap:.3rem 1rem;margin:.5rem 0}dt{color:#46556a}dd{margin:0;overflow-wrap:anywhere}li{margin:.3rem 0}h1,h2{line-height:1.2}";
static const char script[] =
"'use strict';\n"
"let csrf=sessionStorage.getItem('mk20-csrf')||'',task='',stream=null,catalogue=null,agent=false;\n"
"const el=id=>document.getElementById(id);\n"
"function message(s){el('message').textContent=s;}\n"
"const errors={unauthorized:'未配对或配对已过期，请重新配对。',forbidden:'请求被拒绝：请直接用设备地址打开本页。',rate_limited:'尝试太频繁，请稍后再试',display_unavailable:'无法在键盘上显示配对码（写入配对码文件失败），请稍后重试。',sessions_full:'已有 2 个已配对的浏览器，请等待过期或在已配对页面退出。',busy:'设备忙，请稍后再试。',config:'设置无效。',storage:'写入设备存储失败。',apply_incomplete:'上次保存没有完成，请重新保存。',maint_unavailable:'维护通道不可用（键盘监督进程未运行）。',agent_disabled:'本设备未启用 agent。',confirm:'操作未确认。',rejected:'任务被拒绝。'};\n"
"async function api(path,method='GET',body){\n"
" const headers={};if(method!=='GET'){headers['X-CSRF-Token']=csrf;headers['Content-Type']='application/json';}\n"
" const r=await fetch(path,{method,headers,body:body===undefined?undefined:JSON.stringify(body),credentials:'same-origin',cache:'no-store'});\n"
" let data={};try{data=await r.json();}catch(e){}\n"
" if(!r.ok){let m=errors[data.error]||data.message||('请求失败（'+r.status+'）');if(data.retry_after_s)m+='（'+data.retry_after_s+' 秒后）';const e=Error(m);e.status=r.status;throw e;}\n"
" return data;\n"
"}\n"
"function on(id,fn){const e=el(id);if(e)e.addEventListener('click',async ev=>{ev.preventDefault();try{await fn();}catch(err){message(err.message);}});}\n"
"function option(sel,value,text,disabled){const o=document.createElement('option');o.value=value;o.textContent=text;o.disabled=!!disabled;sel.appendChild(o);}\n"
"function shown(v){return v===null||v===undefined||v===''?'—':String(v);}\n"
"function row(dl,k,v){const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=k;dd.textContent=shown(v);dl.append(dt,dd);}\n"
"function kb(v){return v===null||v===undefined?null:v>=10240?Math.round(v/1024)+' MiB':v+' KiB';}\n"
"function duration(s){const d=Math.floor(s/86400),h=Math.floor(s%86400/3600),m=Math.floor(s%3600/60);return(d?d+' 天 ':'')+h+' 小时 '+m+' 分';}\n"
"function fillModels(){const m=el('model');if(!m||!catalogue)return;const p=catalogue.providers.find(x=>x.id===el('provider').value);m.replaceChildren();if(p)for(const x of p.models)option(m,x.id,x.id+(x.vision?'（视觉）':''));}\n"
"async function loadSettings(){\n"
" if(!el('provider'))return;\n"
" catalogue=await api('/api/catalogue');\n"
" const ps=el('provider'),ss=el('secret-name');ps.replaceChildren();ss.replaceChildren();\n"
" for(const p of catalogue.providers)option(ps,p.id,p.label+(p.supported?'':'（暂不支持）'),!p.supported);\n"
" for(const s of catalogue.secrets)option(ss,s,s==='vnc'?'vnc（屏幕共享密码）':s);\n"
" el('hosts').textContent=catalogue.mac_hosts.join('、');\n"
" let saved=null;try{saved=await api('/config/provider');}catch(e){}\n"
" if(saved&&catalogue.providers.some(p=>p.id===saved.provider&&p.supported))ps.value=saved.provider;\n"
" fillModels();\n"
" if(saved){const known=[...el('model').options].some(o=>o.value===saved.model);if(known)el('model').value=saved.model;else if(saved.model)message('已保存的模型 '+saved.model+' 不在内置目录中，请重新选择并保存。');}\n"
" try{const n=await api('/config/net'),u=n.proxy&&n.proxy.url;el('profile').value=n.profile;if(n.profile==='mac_proxy'&&u){const m=/:([0-9]+)$/.exec(u);if(m)el('port').value=m[1];}else if(u)el('board-proxy').value=u;}catch(e){if(e.status===409)message(e.message);}\n"
"}\n"
"async function loadStatus(){\n"
" if(!el('status'))return;\n"
" const s=await api('/api/status'),dl=el('status'),r=s.release||{},k=s.kbd||{},m=s.maint||{},st=m.state||{},t=s.selftest||{};\n"
" dl.replaceChildren();\n"
" row(dl,'固件版本',s.version);row(dl,'当前槽位',r.slot);row(dl,'已确认版本',r.good);row(dl,'待确认版本',r.pending);row(dl,'上次回滚',r.last_rollback);\n"
" row(dl,'运行时间',typeof s.uptime_s==='number'?duration(s.uptime_s):null);row(dl,'usb0 地址',s.usb0_ip);\n"
" const mode=st.mode||k.mode;row(dl,'键盘程序',(mode==='safe'?'安全模式':mode==='normal'?'正常':shown(mode))+(k.running===false?'（未运行）':''));\n"
" row(dl,'键盘程序异常退出',typeof st.restarts==='number'?st.restarts:k.restarts);\n"
" row(dl,'可用内存',kb(s.mem_avail_kb));row(dl,'FAT 剩余',kb(s.fat_free_kb));row(dl,'/data 剩余',kb(s.data_free_kb));\n"
" row(dl,'自检',typeof t.pass==='boolean'?(t.pass?'通过':'失败')+'（PASS '+t.pass_count+' / WARN '+t.warn_count+' / FAIL '+t.fail_count+'）':null);\n"
" row(dl,'状态快照',s.health?s.health_age_s+' 秒前（mk20d health，约 5 分钟更新）':s.health_state==='unavailable'?'状态不可用（快照文件属主或权限不安全）':'暂无');\n"
" const ul=el('problems');ul.replaceChildren();for(const p of t.problems||[]){const li=document.createElement('li');li.textContent=p;ul.appendChild(li);}\n"
" const pending=(m.pending||[]).join('、');\n"
" el('kbd-state').textContent=(mode==='safe'?'安全模式':mode==='normal'?'正常':'未知')+(pending?'；等待处理：'+pending:'')+(typeof m.debug_verbose==='boolean'?'；详细日志：'+(m.debug_verbose?'开':'关'):'')+(m.available?'':'；维护通道不可用')+(m.state_status==='unavailable'?'；状态不可用':'');\n"
" if(typeof m.debug_verbose==='boolean')el('verbose').checked=m.debug_verbose;\n"
"}\n"
"async function session(){\n"
" let d;const lo=el('logout');try{d=await api('/api/session');}catch(e){if(e.status===401){if(lo)lo.hidden=true;message('请先配对：点“在键盘上显示配对码”。');return;}throw e;}\n"
" csrf=d.csrf;sessionStorage.setItem('mk20-csrf',csrf);agent=d.agent;\n"
" const nav=el('nav-chat');if(nav)nav.hidden=!agent;const off=el('agent-off');if(off)off.hidden=agent;if(lo)lo.hidden=false;\n"
" message('已配对。');await loadSettings();await loadStatus();\n"
"}\n"
"on('start',async()=>{const d=await api('/pair/start','POST',{});message('请看键盘屏幕上的 6 位配对码（'+Math.max(1,Math.ceil(d.expires_in_s/60))+' 分钟内有效）。');});\n"
"on('pair',async()=>{const input=el('code');try{const d=await api('/pair','POST',{code:input.value});csrf=d.csrf;sessionStorage.setItem('mk20-csrf',csrf);}finally{input.value='';}await session();});\n"
"on('logout',async()=>{if(!confirm('退出配对？本浏览器需要重新配对才能修改设置。'))return;await api('/api/logout','POST',{});csrf='';sessionStorage.removeItem('mk20-csrf');el('logout').hidden=true;message('已退出配对，名额已释放。');});\n"
"on('refresh-status',loadStatus);\n"
"if(el('provider'))el('provider').addEventListener('change',fillModels);\n"
"on('save-provider',async()=>{await api('/api/provider','PUT',{provider:el('provider').value,model:el('model').value});message('模型已保存。');});\n"
"on('test',async()=>{message('正在从设备测试连接…');const d=await api('/test','POST',{});message((d.ok?'可以连通':'连接失败：'+d.error_class)+' · '+d.latency_ms+' ms');});\n"
"on('save-secret',async()=>{const input=el('secret');try{await api('/secret/'+el('secret-name').value,'POST',{value:input.value});message('已保存。');}finally{input.value='';}});\n"
"on('secret-status',async()=>{const d=await api('/secret/'+el('secret-name').value);message(d.set?'已设置。':'未设置。');});\n"
"on('delete-secret',async()=>{if(!confirm('删除 '+el('secret-name').value+' 的密钥？'))return;await api('/secret/'+el('secret-name').value,'DELETE');message('已删除。');});\n"
"on('save-net',async()=>{\n"
" const profile=el('profile').value,port=Number(el('port').value),board=el('board-proxy').value.trim(),hosts=catalogue?catalogue.mac_hosts:[];\n"
" if(profile==='mac_proxy'&&(!Number.isInteger(port)||port<1||port>65535))throw Error('端口必须是 1–65535。');\n"
" if(profile==='mac_proxy'&&!hosts.length)throw Error('设置目录尚未加载，请刷新页面。');\n"
" const net=profile==='mac_proxy'?{version:1,profile,proxy:{url:'http://'+hosts[0]+':'+port,fallbacks:hosts.slice(1).map(h=>'http://'+h+':'+port)},routes:{domestic:'proxy',foreign:'proxy'}}:{version:1,profile,proxy:{url:board||null,fallbacks:[]},routes:{domestic:'direct',foreign:board?'proxy':'blocked'}};\n"
" await api('/config/net','PUT',net);message('网络设置已保存。');});\n"
"async function maint(action,extra,question){\n"
" if(!confirm(question))return;\n"
" await api('/api/maint/'+action,'POST',Object.assign({confirm:action},extra||{}));\n"
" message('已提交，等待键盘监督进程处理…');setTimeout(()=>loadStatus().catch(e=>message(e.message)),1500);\n"
"}\n"
"on('restart-kbd',()=>maint('restart-kbd',null,'重启键盘程序？按键和屏幕会中断几秒。'));\n"
"on('exit-safe-mode',()=>maint('exit-safe-mode',null,'退出安全模式，并以正常模式重启键盘程序？'));\n"
"on('save-debug',()=>{const v=el('verbose').checked;return maint('debug',{verbose:v},(v?'打开':'关闭')+'详细调试日志？下次键盘程序启动时生效。');});\n"
"function progress(d){if(!d.id)return;task=d.id;el('task-id').value=task;el('state').textContent=d.state;const list=el('steps');list.replaceChildren();for(const a of d.actions){const li=document.createElement('li');li.textContent=a;list.appendChild(li);}if(d.shots.length){const n=Math.max(...d.shots);el('shot').src='/api/tasks/'+task+'/steps/'+n+'/shot.jpg';el('shot').hidden=false;}else{el('shot').removeAttribute('src');el('shot').hidden=true;}}\n"
"on('submit',async()=>{const d=await api('/api/tasks','POST',{goal:el('goal').value});progress(d);message(d.state==='waiting_allow'?'等待键盘上的实体允许。':'任务已接受。');if(stream)stream.close();stream=new EventSource('/api/events');stream.addEventListener('task',ev=>progress(JSON.parse(ev.data)));stream.onerror=()=>message('进度连接中断。会话过期请重新配对；也可以按“刷新”查询任务。');});\n"
"on('refresh',async()=>{task=el('task-id').value;if(!/^t[0-9]+$/.test(task))throw Error('请输入任务 ID。');progress(await api('/api/tasks/'+task));});\n"
"for(const action of ['pause','resume','stop'])on(action,async()=>{if(!task)throw Error('请先选择任务。');progress(await api('/api/tasks/'+task+'/'+action,'POST',{}));message(action==='stop'?'已停止。只有键盘上的实体允许才能再次授权。':action==='pause'?'将在下一次观察或动作前暂停。':'已继续。');});\n"
"window.addEventListener('pagehide',()=>{if(stream)stream.close();});\n"
"session().catch(err=>message(err.message));\n";
static const char body0[] =
"<nav><a href='/'>设置与维护</a><a id='nav-chat' href='/chat' hidden>Agent 任务</a></nav>\n"
"<h1>MK20 键盘设置</h1>\n"
"<p id='message' role='status'>正在连接设备…</p>\n"
"<fieldset id='pairing'><legend>配对这个浏览器</legend>\n"
"<p>点下面的按钮后，键盘屏幕会显示 6 位配对码。本页不会显示配对码；配对码 5 分钟内有效，最多输错 5 次。</p>\n"
"<button id='start'>在键盘上显示配对码</button>\n"
"<label>配对码 <input id='code' inputmode='numeric' pattern='[0-9]{6}' maxlength='6' autocomplete='off'></label>\n"
"<button id='pair'>配对</button><button id='logout' hidden>退出配对（释放名额）</button>\n"
"<p><small>最多同时 2 个浏览器保持配对；用完后请在已配对的页面点“退出配对”。配对只在设备内存中保存：30 分钟无操作、12 小时或设备重启后需要重新配对。</small></p></fieldset>\n"
"<section><h2>设备状态</h2><button id='refresh-status'>刷新</button><dl id='status'></dl><ul id='problems'></ul></section>\n"
"<section><h2>模型</h2>\n"
"<label>模型商 <select id='provider'></select></label>\n"
"<label>模型 <select id='model'></select></label>\n"
"<button id='save-provider'>保存模型</button><button id='test'>从设备测试连接</button>\n"
"<p><small>模型商和模型列表来自固件内置目录（share/providers.json）。连接测试走已保存的网络线路，只说明能否连通。</small></p></section>\n"
"<section><h2>API 密钥</h2>\n"
"<label>用于 <select id='secret-name'></select></label>\n"
"<label>新的值 <input id='secret' type='password' autocomplete='new-password' maxlength='4096'></label>\n"
"<button id='save-secret'>保存</button><button id='secret-status'>是否已设置</button><button id='delete-secret'>删除</button>\n"
"<p><small>密钥只写入设备（文件权限 0600），网页和接口都不会读回、显示或报告长度。vnc 一项是 Mac 屏幕共享密码。</small></p></section>\n"
"<section><h2>网络</h2>\n"
"<label>线路 <select id='profile'><option value='mac_proxy'>经 Mac 代理（USB）</option><option value='board_wifi'>键盘 Wi-Fi</option></select></label>\n"
"<label>Mac 代理端口 <input id='port' type='number' min='1' max='65535' value='7890'></label>\n"
"<label>键盘 Wi-Fi 代理（可选） <input id='board-proxy' placeholder='http://127.0.0.1:7890'></label>\n"
"<button id='save-net'>保存网络</button>\n"
"<p><small>经 Mac 代理时依次尝试 <span id='hosts'>—</span> 的这个端口。代理失败不会改为直连；境外流量不允许直连。</small></p></section>\n"
"<section><h2>维护</h2>\n"
"<p>键盘程序：<span id='kbd-state'>未知</span></p>\n"
"<button id='restart-kbd'>重启键盘程序</button><button id='exit-safe-mode'>退出安全模式</button>\n"
"<label><input type='checkbox' id='verbose'>详细调试日志（下次键盘程序启动时生效）</label><button id='save-debug'>应用调试设置</button>\n"
"<p><small>每个操作都会先请你确认，由键盘监督进程约 1 秒内处理。本页没有重启设备或回滚固件的按钮。</small></p></section>\n"
"<section><h2>权限与隐私</h2><p><small>本服务只在 USB 网卡（usb0）上提供，不加载任何外部资源。配对不会授予 agent 权限：agent 默认关闭，只能在键盘上实体允许。所有修改都需要已配对的会话和同源请求。</small></p></section>\n";
static const char body1[] =
"<nav><a href='/'>设置与维护</a><a id='nav-chat' href='/chat' hidden>Agent 任务</a></nav>\n"
"<h1>Agent 任务</h1>\n"
"<p id='message' role='status'>正在连接设备…</p>\n"
"<p id='agent-off' hidden>本设备未启用 agent（默认关闭）。这里只能查看；设置请回到“设置与维护”。</p>\n"
"<fieldset id='pairing'><legend>配对这个浏览器</legend>\n"
"<p>点下面的按钮后，键盘屏幕会显示 6 位配对码。本页不会显示配对码；配对码 5 分钟内有效，最多输错 5 次。</p>\n"
"<button id='start'>在键盘上显示配对码</button>\n"
"<label>配对码 <input id='code' inputmode='numeric' pattern='[0-9]{6}' maxlength='6' autocomplete='off'></label>\n"
"<button id='pair'>配对</button><button id='logout' hidden>退出配对（释放名额）</button></fieldset>\n"
"<section><h2>新任务</h2><label>目标 <textarea id='goal' rows='4' maxlength='4096' placeholder='描述一个任务'></textarea></label><button id='submit'>提交任务</button>\n"
"<p><small>提交后任务处于 waiting_allow：只有在键盘上实体允许后才会观察和操作。网页不能开启权限、解除急停或代替实体确认。</small></p></section>\n"
"<section><h2>进度</h2><label>任务 ID <input id='task-id' maxlength='23' placeholder='t1'></label><button id='refresh'>刷新</button>\n"
"<p id='state' role='status'>未选择任务</p><button id='pause'>暂停</button><button id='resume'>继续</button><button id='stop' class='danger'>停止 / 撤销输入</button>\n"
"<ol id='steps'></ol><img id='shot' alt='最近一次任务截图（缩放）' hidden></section>\n"
"<section><h2>说明</h2><p><small>进度只包含固定的动作名；任务目标和输入文字不会写入进度或日志。截图只保存在内存中的 5 张环形缓冲里。关闭页面不会停止任务，请按“停止”。</small></p></section>\n";
static const char head[] = "<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>";
char *web_page(int chat, size_t *len)
{
    const char *title = chat ? "MK20 Agent 任务" : "MK20 键盘设置", *body = chat ? body1 : body0;
    size_t cap = strlen(head)+strlen(title)+strlen(style)+strlen(body)+strlen(script)+64;
    char *out = malloc(cap); if (!out) return NULL;
    int n = snprintf(out,cap,"%s%s</title><style>%s</style><body>%s<script>%s</script></body></html>",head,title,style,body,script);
    if (n < 0 || (size_t)n >= cap) { free(out); return NULL; }
    *len = (size_t)n; return out;
}
static int digest(const char *s, char out[45])
{
    unsigned char h[32]; size_t n = 0;
    if (mbedtls_sha256((const unsigned char *)s,strlen(s),h,0) || mbedtls_base64_encode((unsigned char *)out,45,&n,h,sizeof h) || n != 44) return -1;
    out[44] = 0; return 0;
}
const char *web_csp(void)
{
    static char csp[512];
    if (!*csp) {
        char js[45], css[45];
        if (digest(script,js) || digest(style,css)) return "default-src 'none'; frame-ancestors 'none'";
        snprintf(csp,sizeof csp,"default-src 'self'; script-src 'sha256-%s'; style-src 'sha256-%s'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",js,css);
    }
    return csp;
}
