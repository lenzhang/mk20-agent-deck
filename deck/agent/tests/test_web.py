"""A7 offline web acceptance: real Linux listener gates and fd-injected equivalents.
Every listener gate remains enabled and fails visibly on sandbox EPERM.
"""
import ast
import base64
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import select
import socket
import subprocess
import time

import pytest
from .fakes.fake_model import ScriptedTLSModel
from .fakes.fake_proxy import FakeProxy
from .fakes.fake_vnc import FakeVNC
from .test_mcp import keyboard, write_calibration, reports, Client as MCPClient
from .test_loop import kbd_build, real_daemon

AGENT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def web_binary():
    subprocess.run(["make", "-j8", "-C", str(AGENT), "build/mk20-agent-web-test", "build/mk20-agent"],
                   check=True, stdout=subprocess.DEVNULL)
    return AGENT / "build/mk20-agent-web-test"


class Client:
    def __init__(self, sock, host="127.0.0.1:8780"):
        self.sock, self.host, self.buffer = sock, host, b""
        self.cookie, self.csrf = "", ""
        sock.settimeout(8)

    def packet(self, path, method="GET", body=None, *, headers=None, auth=True):
        data = b"" if body is None else json.dumps(body).encode() if isinstance(body, (dict, list)) else body
        h = {"Host": self.host, "Content-Type": "application/json", "Content-Length": str(len(data)),
             "Sec-Fetch-Site": "same-origin"}
        if auth and self.cookie: h["Cookie"] = self.cookie
        if auth and self.csrf: h["X-CSRF-Token"] = self.csrf
        if headers:
            for name, value in headers.items():
                if value is None: h.pop(name, None)
                else: h[name] = value
        return (f"{method} {path} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in h.items()) + "\r\n").encode() + data

    def head(self):
        while b"\r\n\r\n" not in self.buffer:
            part = self.sock.recv(65536)
            if not part: raise AssertionError("HTTP closed before response headers")
            self.buffer += part
        head, self.buffer = self.buffer.split(b"\r\n\r\n", 1)
        lines = head.decode().split("\r\n")
        return int(lines[0].split()[1]), dict(line.split(": ", 1) for line in lines[1:])

    def request(self, path, method="GET", body=None, **kw):
        self.sock.sendall(self.packet(path, method, body, **kw)); status, h = self.head()
        n = int(h["Content-Length"])
        while len(self.buffer) < n:
            part = self.sock.recv(65536)
            if not part: raise AssertionError("HTTP closed before response body")
            self.buffer += part
        body, self.buffer = self.buffer[:n], self.buffer[n:]
        return status, h, json.loads(body) if h["Content-Type"] == "application/json" and body else body

    def pair(self, pair_file):
        # mk20-kbd's view: the challenge file exists once /pair/start has answered.
        assert self.request("/pair/start", "POST", {})[0] == 200
        code, expires = read_pair(pair_file)
        status, h, d = self.request("/pair", "POST", dict(code=code))
        assert status == 200 and not pair_file.exists()
        self.cookie = h["Set-Cookie"].split(";", 1)[0]; self.csrf = d["csrf"]
        assert "HttpOnly; SameSite=Strict; Path=/" in h["Set-Cookie"]
        return code

    def share(self, other): self.cookie, self.csrf = other.cookie, other.csrf

    def event(self):
        while b"\n\n" not in self.buffer:
            part = self.sock.recv(65536)
            if not part: raise AssertionError("event stream closed")
            self.buffer += part
        event, self.buffer = self.buffer.split(b"\n\n", 1)
        data = next(line[6:] for line in event.split(b"\n") if line.startswith(b"data: "))
        return json.loads(data)


def uptime():
    return int(float(Path("/proc/uptime").read_text().split()[0]))


def read_pair(pair_file):
    """Exact /run/mk20/pair-code contract: 0600, <=64 bytes, code + uptime expiry (TTL <= 10 min)."""
    text = pair_file.read_text()
    m = re.fullmatch(r"code=([0-9]{6})\nexpires_uptime=([0-9]+)\n", text)
    assert m and len(text.encode()) <= 64 and pair_file.stat().st_mode & 0o7777 == 0o600, text
    if Path("/proc/uptime").exists(): assert 0 < int(m[2]) - uptime() <= 600
    return m[1], int(m[2])


@contextlib.contextmanager
def server(binary, tmp_path, kbd, *, tcp=False, vnc=None, model_streams=(), ca=None, connections=3, display=True, combined=False, small_send=False, extra=()):
    # resolve() avoids Darwin's /var and /tmp symlink ancestors, which the server
    # deliberately refuses for persisted credentials/configuration.
    tmp_path = tmp_path.resolve()
    secrets, config = tmp_path / "secrets", tmp_path / "agent"
    pw = tmp_path / "vnc.pass"; pw.write_bytes(b"secret12"); pw.chmod(0o600)
    calibration = tmp_path / "calib.json"; write_calibration(calibration)
    policy = tmp_path / "policy.json"; policy.write_text(json.dumps(dict(confirm_first=False, confirm_app_switch=False)))
    pair_file, maint = tmp_path / "run/pair-code", tmp_path / "maint"
    cmd = [str(binary), "--web", "--web-listen", "127.0.0.1:8780", "--web-no-bindtodevice",
           "--web-secrets-dir", str(secrets), "--web-config-dir", str(config),
           "--web-pair-file", str(pair_file), "--web-health-file", str(tmp_path / "health.json"),
           "--web-maint-dir", str(maint), "--web-debug-file", str(tmp_path / "debug.conf"), *extra]
    if kbd is not None: cmd += ["--calibration", str(calibration), "--policy", str(policy)]
    clients, children, inherited = [], [], []
    # Without a usable display directory no challenge can be shown (display_unavailable).
    if not display: (tmp_path / "run").mkdir(mode=0o777); (tmp_path / "run").chmod(0o777)
    if kbd is None: pass  # settings-only web (FEATURE_AGENT=0): no keyboard client
    elif isinstance(kbd, Path): cmd += ["--kbd-sock", str(kbd)]
    else: cmd += ["--kbd-fd", str(kbd.fileno())]; inherited.append(kbd.fileno())
    if vnc:
        cmd += ["--vnc-host", "127.0.0.1", "--vnc-port", str(vnc.port), "--password-file", str(pw)]
        if vnc.client: cmd += ["--vnc-fd", str(vnc.client.fileno())]; inherited.append(vnc.client.fileno())
    if ca: cmd += ["--ca-file", str(ca), "--web-trust-clock"]
    for stream in model_streams: cmd += ["--web-model-fd", str(stream.fileno())]; inherited.append(stream.fileno())
    if not tcp:
        for _ in range(connections):
            parent, child = socket.socketpair();
            if small_send: child.setsockopt(socket.SOL_SOCKET,socket.SO_SNDBUF,1024)
            clients.append(Client(parent)); children.append(child); inherited.append(child.fileno())
            cmd += ["--web-connected-fd", str(child.fileno())]
    mcp_parent = None
    if combined:
        cmd += ["--mcp", "--mcp-listen", "127.0.0.1:8765", "--mcp-no-bindtodevice", "--mcp-token-file", str(tmp_path / "mcp.token")]
        mcp_parent, child = socket.socketpair(); children.append(child); inherited.append(child.fileno())
        cmd += ["--mcp-connected-fd", str(child.fileno())]
    logpath = tmp_path / "web.log"
    with logpath.open("w+") as log:
        proc = subprocess.Popen(cmd, pass_fds=tuple(inherited), stdout=subprocess.PIPE, stderr=log)
        for child in children: child.close()
        try:
            deadline = time.monotonic() + 10
            while "web ready" not in logpath.read_text() and proc.poll() is None and time.monotonic() < deadline: time.sleep(.01)
            if "web ready" not in logpath.read_text(): raise AssertionError("web startup failed: " + logpath.read_text())
            if tcp: clients = [Client(socket.create_connection(("127.0.0.1", 8780))) for _ in range(connections)]
            mcp = MCPClient(mcp_parent,(tmp_path / "mcp.token").read_text()) if combined else None
            yield clients, pair_file, proc, logpath, secrets, config, mcp
        finally:
            for c in clients: c.sock.close()
            if mcp_parent: mcp_parent.close()
            if proc.poll() is None: proc.terminate()
            out, _ = proc.communicate(timeout=6)
            logs = logpath.read_bytes() + out
            forbidden = [b"secret12", b"Authorization", b"private web task", b"web-synthetic-key"]
            forbidden += [c.cookie.encode() for c in clients if c.cookie] + [c.csrf.encode() for c in clients if c.csrf]
            if any(value in logs for value in forbidden): raise AssertionError("sensitive content reached process logs")
            assert b"AddressSanitizer" not in logs and b"runtime error:" not in logs
            assert proc.returncode == 0 or b"bind refused errno=1" in logs


def configuration(secrets, port=7890):
    net = dict(version=1, profile="mac_proxy", proxy=dict(url=f"http://172.30.250.1:{port}", fallbacks=[]), routes=dict(domestic="proxy", foreign="proxy"))
    provider = dict(version=1, provider="qwen", model="qwen-vl-max", base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                    domestic=True, route="domestic", api_key_file=str(secrets / "qwen.key"))
    return net, provider


@pytest.fixture(scope="module")
def web_certs(tmp_path_factory):
    path = tmp_path_factory.mktemp("a7-certs")
    def run(*args): subprocess.run(["openssl", *map(str, args)], cwd=path, check=True, capture_output=True)
    run("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "ca.key", "-out", "ca.pem", "-days", "30", "-subj", "/CN=A7 offline CA", "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign")
    run("req", "-new", "-newkey", "rsa:2048", "-nodes", "-keyout", "server.key", "-out", "server.csr", "-subj", "/CN=dashscope.aliyuncs.com")
    (path / "ext").write_text("basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName=DNS:dashscope.aliyuncs.com\n")
    run("x509", "-req", "-in", "server.csr", "-CA", "ca.pem", "-CAkey", "ca.key", "-CAcreateserial", "-out", "server.pem", "-days", "20", "-extfile", "ext")
    return path


def full_flow(binary, kbd_build, tmp_path, certs, tcp):
    # Each fake CONNECT/TLS request uses one injected socketpair. Official
    # provider URL + certificate verification + configured proxy route stay live.
    script = [dict(status=200, body={"reachable": True})] + [dict(tool_calls=[dict(name="wait", arguments=dict(ms=1, risk="low", target="Notes"))]) for _ in range(6)] + [dict(tool_calls=[dict(name="done", arguments=dict(risk="low", target="Notes"))],delay=.3)]
    with keyboard(kbd_build, tmp_path) as (kbd, reader, controls, _), FakeVNC(socketpair=not tcp, changing=True) as vnc, ScriptedTLSModel(certs / "server.pem", certs / "server.key", script, socketpair=True, key="web-synthetic-key") as model:
        proxies = [FakeProxy(socketpair=True, upstream_factory=model.open_stream) for _ in range(8)]
        try:
            with server(binary,tmp_path,kbd,tcp=tcp,vnc=vnc,model_streams=[p.client for p in proxies],ca=certs / "ca.pem",connections=3) as (cs, fd, _, log, secrets, config, _):
                c, status_client, events = cs
                assert c.request("/config/net")[0] == 401
                code = c.pair(fd); status_client.share(c); events.share(c)
                assert c.request("/secret/qwen","POST",dict(value="web-synthetic-key"))[2] == dict(set=True)
                assert c.request("/secret/qwen")[2] == dict(set=True)
                assert (secrets / "qwen.key").stat().st_mode & 0o7777 == 0o600
                net, provider = configuration(secrets)
                assert c.request("/config/net","PUT",net)[0] == 200
                assert c.request("/config/provider","PUT",provider)[0] == 200
                assert c.request("/config/provider")[2] == provider
                assert c.request("/test","POST",{})[2]["ok"]
                d = c.request("/api/tasks","POST",dict(goal="private web task"))[2]
                task = d["id"]; assert d["state"] == "waiting_allow"
                assert c.request("/test","POST",{})[0] == 409
                time.sleep(.05); assert not vnc.updates and len(model.requests) == 1
                assert c.request(f"/api/tasks/{task}/pause","POST",{})[0] == 200
                os.write(controls,b"a"); time.sleep(.3); assert not vnc.updates
                events.sock.sendall(events.packet("/api/events")); status, h = events.head()
                assert status == 200 and h["Content-Type"] == "text/event-stream" and "Content-Length" not in h
                assert events.event()["state"] == "paused"
                assert c.request(f"/api/tasks/{task}/resume","POST",{})[0] == 200
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    d = status_client.request(f"/api/tasks/{task}")[2]
                    if d["steps"] >= 6:
                        assert c.request(f"/api/tasks/{task}/pause","POST",{})[0] == 200
                        break
                    time.sleep(.05)
                assert d["steps"] == 6 and d["state"] not in ("done","error","stopped")
                d = c.request(f"/api/tasks/{task}")[2]
                assert len(d["shots"]) == 5 and len(d["actions"]) == 6
                assert status_client.request(f"/api/tasks/{task}/steps/1/shot.jpg")[0] == 404
                newest = max(d["shots"])
                status,h,image = c.request(f"/api/tasks/{task}/steps/{newest}/shot.jpg")
                assert status == 200 and h["Content-Type"] == "image/jpeg" and image.startswith(b"\xff\xd8")
                assert c.request(f"/api/tasks/{task}/resume","POST",{})[0] == 200
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    d = c.request(f"/api/tasks/{task}")[2]
                    if d["state"] in ("done","error","stopped"): break
                    time.sleep(.1)
                assert d["state"] == "done" and d["steps"] == 7 and not d["shots"]
                assert c.request(f"/api/tasks/{task}/steps/{newest}/shot.jpg")[0] == 404
                assert c.request(f"/api/tasks/{task}/stop","POST",{})[2]["state"] == "done"
                assert c.request("/api/tasks","POST",dict(goal="private web task"))[2]["state"] == "waiting_allow"
                assert status_client.request(f"/api/tasks/{task}/steps/7/shot.jpg")[0] == 404
                assert not vnc.events
                assert all(p.connects == ["dashscope.aliyuncs.com:443"] and not p.violations for p in proxies)
                logs = log.read_text()
                if code in logs: raise AssertionError("pairing code reached logs")
                for req in model.requests:
                    if b"web-synthetic-key" in req["raw"]: raise AssertionError("provider key reached request body")
        finally:
            for proxy in proxies: proxy.close()


def test_full_flow_ipc(web_binary,kbd_build,tmp_path,web_certs): full_flow(web_binary,kbd_build,tmp_path,web_certs,False)
def test_real_socket_full_flow(web_binary,kbd_build,tmp_path,web_certs): full_flow(web_binary,kbd_build,tmp_path,web_certs,True)


def test_auth_csrf_origin_and_secrets_ipc(web_binary,kbd_build,tmp_path):
    with keyboard(kbd_build,tmp_path) as (kbd,_,_,_), server(web_binary,tmp_path,kbd) as (cs,fd,_,log,secrets,_,_):
        c,other,_ = cs
        for path in ("/config/net","/secret/qwen","/api/tasks/t1","/api/events","/unknown"):
            assert c.request(path)[2] == dict(error="unauthorized")
        code = c.pair(fd)
        assert c.request("/secret/qwen","POST",dict(value="web-synthetic-key"),headers={"X-CSRF-Token":None})[0] == 403
        assert c.request("/secret/qwen","POST",dict(value="web-synthetic-key"),headers={"Sec-Fetch-Site":None})[0] == 403
        assert c.request("/secret/qwen","POST",dict(value="web-synthetic-key"),headers={"Origin":"http://evil"})[0] == 403
        assert c.request("/secret/qwen","POST",dict(value="web-synthetic-key"),headers={"Sec-Fetch-Site":None,"Origin":"http://172.30.250.2:8780"})[0] == 403
        assert c.request("/secret/qwen","POST",dict(value="web-synthetic-key"),headers={"Sec-Fetch-Site":None,"Origin":"http://127.0.0.1:8780"})[0] == 200
        assert other.request("/secret/qwen")[0] == 401
        assert c.request("/secret/qwen")[2] == dict(set=True)
        assert c.request("/secret/qwen","DELETE")[0] == 200
        assert c.request("/secret/qwen")[2] == dict(set=False)
        for value in ("", "line\nbreak", "nul\x00byte", "x"*4097): assert c.request("/secret/vnc","POST",dict(value=value))[0] == 400
        (secrets / "glm.key").symlink_to(secrets / "qwen.key")
        assert c.request("/secret/glm","POST",dict(value="web-synthetic-key"))[0] == 500
        assert c.request("/secret/../../passwd")[0] == 404
        if code in log.read_text(): raise AssertionError("pairing code reached logs")


def test_glm_coding_provider_and_secret_ipc(web_binary,kbd_build,tmp_path):
    with keyboard(kbd_build,tmp_path) as (kbd,_,_,_), server(web_binary,tmp_path,kbd) as (cs,fd,_,_,secrets,_,_):
        c = cs[0]; c.pair(fd)
        provider = dict(version=1,provider="glm-coding",model="glm-5.3",
                        base_url="https://open.bigmodel.cn/api/coding/paas/v4",
                        domestic=True,route="domestic",api_key_file=str(secrets / "glm-coding.key"))
        assert c.request("/config/provider","PUT",provider)[0] == 200
        assert c.request("/config/provider")[2] == provider
        for field,value in (("base_url","https://open.bigmodel.cn/api/paas/v4"),
                            ("domestic",False),("route","foreign"),
                            ("api_key_file",str(secrets / "glm.key"))):
            assert c.request("/config/provider","PUT",dict(provider,**{field:value}))[0] == 400
        assert c.request("/secret/glm-coding","POST",dict(value="web-synthetic-key"))[0] == 200
        assert (secrets / "glm-coding.key").stat().st_mode & 0o7777 == 0o600
        assert c.request("/secret/glm-coding")[2] == dict(set=True)
        assert c.request("/secret/glm-coding","DELETE")[0] == 200
        assert c.request("/secret/glm-coding")[2] == dict(set=False)


def test_display_unavailable_ipc(web_binary,kbd_build,tmp_path):
    with keyboard(kbd_build,tmp_path) as (kbd,_,_,_), server(web_binary,tmp_path,kbd,display=False) as (cs,_,_,_,_,_,_):
        assert cs[0].request("/pair/start","POST",{})[2] == dict(error="display_unavailable")
        assert cs[0].request("/pair","POST",dict(code="000000"))[0] == 401
        assert not (tmp_path.resolve() / "run/pair-code").exists()


def test_static_pages_hashes_ipc(web_binary,kbd_build,tmp_path):
    with keyboard(kbd_build,tmp_path) as (kbd,_,_,_), server(web_binary,tmp_path,kbd) as (cs,_,_,_,_,_,_):
        for path in ("/","/chat"):
            status,h,body = cs[0].request(path); assert status == 200 and h["Cache-Control"] == "no-store"
            assert len(body) >= 6000 and b"https://" not in body and b"eval(" not in body and b"lang=\"zh-CN\"" in body
            for kind in ("script","style"):
                values = re.findall(fr"<{kind}>(.*?)</{kind}>",body.decode(),re.S)
                assert len(values) == 1
                digest = base64.b64encode(hashlib.sha256(values[0].encode()).digest()).decode()
                assert f"'sha256-{digest}'" in h["Content-Security-Policy"]
            assert "frame-ancestors 'none'" in h["Content-Security-Policy"]
        assert cs[0].request("/web.js")[0] == 401


def test_shared_mcp_task_slot_ipc(web_binary,kbd_build,tmp_path):
    with keyboard(kbd_build,tmp_path) as (kbd,_,_,_), server(web_binary,tmp_path,kbd,combined=True,connections=2) as (cs,fd,_,_,_,_,mcp):
        c = cs[0]; c.pair(fd); mcp.initialize()
        assert c.request("/api/tasks","POST",dict(goal="private web task"))[2]["state"] == "waiting_allow"
        from .test_mcp import text
        assert "task=t1" in text(mcp.tool("status"))
        assert "state=waiting_allow" in text(mcp.tool("task_status",id="t1"))
        assert text(mcp.tool("cancel_task",id="t1")) == "stopped"
        assert c.request("/api/tasks/t1")[2]["state"] == "stopped"
        assert text(mcp.tool("run_task",goal="private web task")) == "stopped"


def test_sse_limit_and_disconnect_ipc(web_binary,kbd_build,tmp_path):
    with keyboard(kbd_build,tmp_path) as (kbd,_,_,_), server(web_binary,tmp_path,kbd,connections=4) as (cs,fd,_,_,_,_,_):
        c,a,b,extra = cs; c.pair(fd)
        for stream in (a,b):
            stream.share(c); stream.sock.sendall(stream.packet("/api/events")); assert stream.head()[0] == 200; assert "state" in stream.event()
        extra.share(c); assert extra.request("/api/events")[0] == 503
        a.sock.shutdown(socket.SHUT_RDWR); time.sleep(.05)
        extra.sock.sendall(extra.packet("/api/events")); assert extra.head()[0] == 200; assert "state" in extra.event()


@pytest.mark.parametrize("wire,status", [
    (b"PUT /config/net HTTP/1.1\r\nHost: x\r\n\r\n",411),
    (b"POST /pair HTTP/1.1\r\nHost: x\r\nContent-Length: 65537\r\n\r\n",413),
    (b"GET / HTTP/1.1\r\nHost: x\r\nCookie: a\r\nCookie: b\r\n\r\n",400),
    (b"GET / HTTP/1.1\r\nHost: x\r\nX-CSRF-Token: a\r\nX-CSRF-Token: b\r\n\r\n",400),
    (b"GET / HTTP/1.1\r\nHost: x\r\n"+b"x"*8200,431),
])
def test_http_limits_ipc(web_binary,kbd_build,tmp_path,wire,status):
    with keyboard(kbd_build,tmp_path) as (kbd,_,_,_), server(web_binary,tmp_path,kbd) as (cs,_,_,_,_,_,_):
        cs[0].sock.sendall(wire); assert cs[0].head()[0] == status


def test_read_deadline_ipc(web_binary,kbd_build,tmp_path):
    with keyboard(kbd_build,tmp_path) as (kbd,_,_,_), server(web_binary,tmp_path,kbd) as (cs,_,_,_,_,_,_):
        cs[0].sock.sendall(b"POST /pair HTTP/1.1\r\nHost:")
        start=time.monotonic(); assert cs[0].sock.recv(1) == b""; assert 4.7 < time.monotonic()-start < 6


def test_provider_proxy_schema_drift(web_binary):
    # Read source data without importing mk20deck or executing its AST.
    module=ast.parse((AGENT.parent / "mk20deck/agentcfg.py").read_text()); values={}
    for node in module.body:
        if isinstance(node,ast.AnnAssign) and isinstance(node.target,ast.Name): values[node.target.id]=node.value
        elif isinstance(node,ast.Assign):
            for target in node.targets:
                if isinstance(target,ast.Name): values[target.id]=node.value
    package = AGENT.parent / "mk20deck"
    catalogue_module = ast.parse((package / "catalogue.py").read_text())
    catalogue_node = next(node.value for node in catalogue_module.body if isinstance(node,ast.Assign)
                          and any(isinstance(t,ast.Name) and t.id == "CATALOGUE" for t in node.targets))
    if isinstance(catalogue_node,(ast.List,ast.Tuple)):
        catalogue = ast.literal_eval(catalogue_node)
    else:
        # The installed catalogue loads JSON: resolve its literal filename and
        # table key via AST, then read the same data as catalogue.py.
        assert isinstance(catalogue_node,ast.Subscript)
        assert ast.unparse(catalogue_node.value.func) == "json.loads"
        filenames = [n.args[0] for n in ast.walk(catalogue_node) if isinstance(n,ast.Call)
                     and isinstance(n.func,ast.Attribute) and n.func.attr == "with_name"]
        assert len(filenames) == 1
        catalogue = json.loads((package / ast.literal_eval(filenames[0])).read_text())[ast.literal_eval(catalogue_node.slice)]
    expected = {p["id"]: {"base_url": p["base_url"], "domestic": p["domestic"]} for p in catalogue}
    result=subprocess.run([str(web_binary),"--web","--web-schema-fd","1"],capture_output=True,check=True)
    schema=json.loads(result.stdout)
    assert schema["providers"] == expected
    assert schema["mac_hosts"] == list(ast.literal_eval(values["MAC_HOSTS"]))
    assert not result.stderr


def test_backup_excludes_secrets():
    excluded=(AGENT.parent / "tools/backup-exclude.txt").read_text().splitlines()
    assert any(line.strip().rstrip("/") == "/data/mk20/secrets" for line in excluded if not line.startswith("#"))


@pytest.mark.parametrize("arg",["--web-pair-fd","--web-connected-fd","--web-model-fd","--web-schema-fd"])
def test_production_rejects_test_seams(web_binary,arg):
    result=subprocess.run([str(AGENT / "build/mk20-agent"),"--web",arg,"0"],capture_output=True)
    assert result.returncode and b"invalid configuration" in result.stderr


@pytest.mark.parametrize("address,no_device",[("0.0.0.0:8780",True),("[::]:8780",True),("192.168.1.3:8780",False),("172.30.250.1:8780",False),
    ("169.254.7.2:8780",False),("0.1.2.2:8780",False),("172.30.250.2:8780",True),("127.0.0.1:8780",False),("172.30.250.2:9999",False),("172.30.9.2:18780",False)])
def test_listener_override_refusal(web_binary,address,no_device):
    command=[str(web_binary),"--web","--web-listen",address]
    if no_device: command += ["--web-no-bindtodevice"]
    result=subprocess.run(command,capture_output=True)
    assert result.returncode and b"invalid configuration" in result.stderr


def test_real_socket_daemon_web(web_binary,kbd_build,tmp_path):
    with real_daemon(kbd_build,tmp_path,allowed=False) as (path,_), server(web_binary,tmp_path,path,tcp=True) as (cs,fd,_,_,_,_,_):
        c=cs[0]; c.pair(fd)
        assert c.request("/api/tasks","POST",dict(goal="private web task"))[2]["state"] == "waiting_allow"
        assert c.request("/api/tasks/t1/stop","POST",{})[2]["state"] == "stopped"


def test_sse_slow_client_dropped_ipc(web_binary,kbd_build,tmp_path):
    with keyboard(kbd_build,tmp_path) as (kbd,_,_,_), server(web_binary,tmp_path,kbd,connections=3,small_send=True) as (cs,fd,_,_,_,_,_):
        c,slow,active=cs; c.pair(fd); slow.share(c); active.share(c)
        for stream in (slow,active):
            stream.sock.sendall(stream.packet("/api/events")); assert stream.head()[0] == 200; stream.event()
        # Small server send buffer + accelerated test-binary heartbeat fills the
        # slow stream without large allocations. Keep the control socket alive.
        deadline=time.monotonic()+7
        while time.monotonic()<deadline:
            assert c.request("/secret/qwen")[0] == 200
            active.event(); time.sleep(.5)
        slow.sock.settimeout(2)
        while slow.sock.recv(65536): pass
        active.sock.shutdown(socket.SHUT_RDWR); time.sleep(.05)
        c.sock.sendall(c.packet("/api/events")); assert c.head()[0] == 200


def test_web_stop_mid_drag_ipc(web_binary,kbd_build,tmp_path,web_certs):
    step=dict(tool_calls=[dict(name="drag",arguments=dict(x1=5,y1=5,x2=200,y2=150,risk="low",target="Notes"))])
    with keyboard(kbd_build,tmp_path) as (kbd,reader,controls,proc), FakeVNC(socketpair=True) as vnc, ScriptedTLSModel(web_certs / "server.pem",web_certs / "server.key",[step],socketpair=True,key="web-synthetic-key") as model, FakeProxy(socketpair=True,upstream_factory=model.open_stream) as proxy:
        with server(web_binary,tmp_path,kbd,vnc=vnc,model_streams=[proxy.client],ca=web_certs / "ca.pem",connections=2) as (cs,fd,_,_,secrets,_,_):
            c,other=cs; c.pair(fd); other.share(c)
            c.request("/secret/qwen","POST",dict(value="web-synthetic-key")); net,provider=configuration(secrets)
            assert c.request("/config/net","PUT",net)[0] == c.request("/config/provider","PUT",provider)[0] == 200
            assert c.request("/api/tasks","POST",dict(goal="private web task"))[2]["state"] == "waiting_allow"
            reports(reader); os.write(controls,b"a")
            deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                if select.select([reader],[],[],.01)[0] and b"\x02" in reports(reader): break
            else: raise AssertionError("web task did not start drag")
            start=time.monotonic(); assert other.request("/api/tasks/t1/stop","POST",{})[2]["state"] == "stopped"
            assert time.monotonic()-start < .5
            time.sleep(.05); reports(reader); time.sleep(.05); assert not reports(reader)
            assert c.request("/api/tasks/t1")[2]["state"] == "stopped"
            assert c.request("/api/tasks/t1/steps/1/shot.jpg")[0] == 404
            assert not vnc.events and proxy.connects == ["dashscope.aliyuncs.com:443"]
        kbd.shutdown(socket.SHUT_RDWR); out,err=proc.communicate(timeout=3)
        assert json.loads(out)["estops"] >= 1 and not err


def test_real_socket_web_listener(web_binary,kbd_build,tmp_path):
    with keyboard(kbd_build,tmp_path) as (kbd,_,_,_), server(web_binary,tmp_path,kbd,tcp=True) as (cs,fd,_,_,secrets,_,_):
        c=cs[0]; c.pair(fd); net,provider=configuration(secrets)
        assert c.request("/config/net","PUT",net)[0] == 200
        assert c.request("/config/provider","PUT",provider)[0] == 200
        assert c.request("/api/tasks","POST",dict(goal="private web task"))[2]["state"] == "waiting_allow"
        assert c.request("/api/tasks/t1/stop","POST",{})[2]["state"] == "stopped"


def test_web_usb0_namespace_isolation(web_binary):
    import shutil
    subprocess.run(["make","-C",str(AGENT),"build/web-bind-probe"],check=True,stdout=subprocess.DEVNULL)
    unshare,ip=shutil.which("unshare"),shutil.which("ip")
    if not unshare or not ip:
        if "usb0" not in [name for _,name in socket.if_nameindex()]:
            result=subprocess.run([str(AGENT / "build/web-bind-probe")],capture_output=True,timeout=3)
            assert result.returncode and b"usb0 web bind" in result.stderr
        return
    result=subprocess.run([unshare,"-Urn","--map-root-user",os.sys.executable,str(AGENT / "tests/netns_web.py")],capture_output=True,timeout=20)
    assert result.returncode == 0, "web netns isolation gate failed: " + result.stderr.decode()


def test_config_update_applies_to_mcp_tasks_ipc(web_binary,kbd_build,tmp_path,web_certs):
    from .test_mcp import allowed, text
    step=dict(tool_calls=[dict(name="done",arguments=dict(risk="low",target="Notes"))])
    with keyboard(kbd_build,tmp_path) as (kbd,_,controls,_), FakeVNC(socketpair=True) as vnc, ScriptedTLSModel(web_certs / "server.pem",web_certs / "server.key",[step],socketpair=True,key="web-synthetic-key") as model, FakeProxy(socketpair=True,upstream_factory=model.open_stream) as proxy:
        with server(web_binary,tmp_path,kbd,vnc=vnc,model_streams=[proxy.client],ca=web_certs / "ca.pem",connections=1,combined=True) as (cs,fd,_,_,secrets,_,mcp):
            c=cs[0]; c.pair(fd); net,provider=configuration(secrets)
            c.request("/secret/qwen","POST",dict(value="web-synthetic-key"))
            assert c.request("/config/net","PUT",net)[0] == 200
            assert c.request("/config/provider","PUT",provider)[0] == 200
            mcp.initialize(); allowed(mcp,controls)
            task=text(mcp.tool("run_task",goal="private web task")); assert task == "t1"
            deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                status=text(mcp.tool("task_status",id=task))
                if "state=done" in status: break
                time.sleep(.1)
            assert "state=done" in status and len(model.requests) == 1
            assert c.request("/api/tasks/t1")[2]["state"] == "done"


def test_oversized_header_response_halfclose_ipc(web_binary,kbd_build,tmp_path):
    with keyboard(kbd_build,tmp_path) as (kbd,_,_,_), server(web_binary,tmp_path,kbd) as (cs,_,_,_,_,_,_):
        c=cs[0]
        c.sock.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n"+b"x"*8192)
        assert c.head()[0] == 431
        # The complete response is available even though the rejected sender
        # still has bytes to write. The server drains them under its deadline.
        c.sock.sendall(b"x"*128)
        assert c.sock.recv(1) == b""


def test_settings_only_web_default_ipc(web_binary,tmp_path):
    # FEATURE_AGENT=0 default: no keyboard client, no task API, settings + maintenance only.
    root = tmp_path.resolve(); maint = root / "maint"; maint.mkdir(mode=0o700)
    (root / "health.json").write_text(json.dumps(dict(version="9.9.9", release=dict(slot="A", good="9.9.8", pending="9.9.9", last_rollback=""),
        kbd=dict(mode="normal", running=True, restarts=0, recent_crashes=0, status='{"private":"KBD-RAW"}'), mem_avail_kb=50000,
        fat_free_kb=4096, data_free_kb=90000, config=dict(src="/data/mk20/config/mk20.conf"),
        selftest={"pass": True, "detail": "PASS kd=0 (mk20-kbd mode) WARN web not running"}, status="degraded")))
    with server(web_binary,tmp_path,None,connections=2) as (cs,pair_file,proc,log,secrets,config,_):
        c, other = cs
        assert "web ready (settings only)" in log.read_text()
        status, h, body = c.request("/")
        assert status == 200 and "script-src 'sha256-" in h["Content-Security-Policy"]
        assert c.request("/", headers={"Host": "mk20.attacker.example:8780"})[0] == 403  # DNS rebinding
        assert c.request("/api/status")[0] == 401
        code = c.pair(pair_file)
        assert c.request("/api/session")[2] == dict(csrf=c.csrf, agent=False, web_url="http://127.0.0.1:8780")
        other.cookie = c.cookie  # a new tab recovers its CSRF token from the same-origin session endpoint
        assert other.request("/api/session")[2]["csrf"] == c.csrf
        for path, method in (("/api/tasks","POST"), ("/api/tasks/t1","GET"), ("/api/events","GET")):
            assert c.request(path, method, dict(goal="x") if method == "POST" else None)[2] == dict(error="agent_disabled")
        cat = c.request("/api/catalogue")[2]
        assert [p["id"] for p in cat["providers"]] == [p["id"] for p in json.loads((AGENT.parent / "share/providers.json").read_text())["providers"]]
        assert c.request("/api/provider","PUT",dict(provider="glm-coding",model="glm-5.3-flash"))[0] == 200
        assert c.request("/config/provider")[2]["api_key_file"] == str(secrets / "glm-coding.key")
        assert c.request("/api/provider","PUT",dict(provider="glm-coding",model="free-text"))[0] == 400
        assert c.request("/secret/glm-coding","POST",dict(value="web-synthetic-key"))[2] == dict(set=True)
        s = c.request("/api/status")[2]
        assert s["version"] == "9.9.9" and s["release"]["slot"] == "A" and s["kbd"]["mode"] == "normal" and s["agent"] is False
        assert s["selftest"]["problems"] == ["WARN web not running"] and "KBD-RAW" not in json.dumps(s) and "mk20.conf" not in json.dumps(s)
        assert c.request("/api/maint/restart-kbd","POST",dict(confirm="restart-kbd"))[0] == 202
        assert (maint / "restart-kbd").stat().st_size == 0 and (maint / "restart-kbd").stat().st_mode & 0o7777 == 0o600
        assert c.request("/api/maint/debug","POST",dict(confirm="debug",verbose=True))[0] == 202
        assert (maint / "debug").read_text() == "verbose=1\n"
        assert c.request("/api/status")[2]["maint"]["pending"] == ["restart-kbd","debug"]
        assert not [p for p in maint.iterdir() if p.name.startswith(".web-")]
        assert code not in log.read_text()
    assert not pair_file.exists()


def test_pair_file_removed_on_exit_and_restart_ipc(web_binary,tmp_path):
    root = tmp_path.resolve(); stale = root / "run/pair-code"; stale.parent.mkdir(mode=0o700)
    stale.write_text("code=123456\nexpires_uptime=99999999\n"); stale.chmod(0o600)
    with server(web_binary,tmp_path,None,connections=1) as (cs,pair_file,proc,_,_,_,_):
        assert pair_file == stale and not stale.exists()  # a previous instance's challenge is void
        assert cs[0].request("/pair/start","POST",{})[0] == 200 and read_pair(pair_file)
    assert not stale.exists()  # SIGTERM shutdown removes a live challenge


def test_settings_only_refuses_agent_inputs(web_binary,tmp_path):
    for extra in (["--vnc-host","172.30.250.1"],["--provider",str(tmp_path / "p.json"),"--net",str(tmp_path / "n.json")]):
        r = subprocess.run([str(web_binary),"--web","--web-listen","127.0.0.1:8780","--web-no-bindtodevice",*extra],capture_output=True)
        assert r.returncode and b"invalid configuration" in r.stderr


@pytest.mark.parametrize("address",["172.30.9.2:8780","10.20.0.2:8780"])
def test_configured_subnet_listener_accepted(web_binary,address):
    # Any mk20d ECM_SUBNET device address (.2) passes validation; binding still needs usb0.
    r = subprocess.run([str(AGENT / "build/mk20-agent"),"--web","--web-listen",address],capture_output=True,timeout=10)
    assert b"invalid configuration" not in r.stderr
    if "usb0" not in [name for _,name in socket.if_nameindex()]:
        assert r.returncode and b"web startup: bind refused" in r.stderr


def test_web_catalogue_generated_from_single_source(tmp_path):
    # share/providers.json -> tools/gen-providers.py -> kbd/providers.inc + agent/src/web_providers.inc
    deck = AGENT.parent
    assert subprocess.run([os.sys.executable, str(deck / "tools/gen-providers.py"), "--check"], capture_output=True).returncode == 0
    for rel in ("tools/gen-providers.py", "share/providers.json", "kbd/providers.inc", "agent/src/web_providers.inc"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes((deck / rel).read_bytes())
    data = json.loads((tmp_path / "share/providers.json").read_text())
    data["providers"][0]["models"].append(dict(id="new-model", vision=True, tools=True))
    (tmp_path / "share/providers.json").write_text(json.dumps(data))
    r = subprocess.run([os.sys.executable, str(tmp_path / "tools/gen-providers.py"), "--check"], capture_output=True, text=True)
    assert r.returncode and "web_providers.inc" in r.stderr and "providers.inc" in r.stderr
    assert subprocess.run([os.sys.executable, str(tmp_path / "tools/gen-providers.py")]).returncode == 0
    assert '"new-model"' in (tmp_path / "agent/src/web_providers.inc").read_text()
    assert subprocess.run([os.sys.executable, str(tmp_path / "tools/gen-providers.py"), "--check"]).returncode == 0
