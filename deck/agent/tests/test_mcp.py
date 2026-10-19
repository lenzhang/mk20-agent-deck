"""A6: handwritten HTTP/MCP client, real ctl/HID IPC and real listener gates.
No listener test is skipped or xfailed when a sandbox rejects bind()."""
import base64
import contextlib
import json
import os
from pathlib import Path
import select
import shutil
import socket
import subprocess
import threading
import time

import pytest
from .fakes.fake_vnc import FakeVNC
from .test_loop import kbd_build, real_daemon  # unchanged daemon build in a temporary tree

AGENT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def mcp_binary():
    subprocess.run(["make", "-j8", "-C", str(AGENT), "build/mk20-agent-mcp-test", "build/mk20-agent"],
                   check=True, stdout=subprocess.DEVNULL)
    return AGENT / "build/mk20-agent-mcp-test"


class Client:
    def __init__(self, sock, token, host="127.0.0.1:8765"):
        self.sock, self.token, self.host = sock, token, host
        self.session, self.sequence, self.buffer = None, 0, b""
        sock.settimeout(8)

    def packet(self, body=b"", method="POST", *, auth=True, headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        h = {"Host": self.host, "Content-Type": "application/json", "Content-Length": str(len(body))}
        if auth: h["Authorization"] = "Bearer " + self.token
        if self.session: h["Mcp-Session-Id"] = self.session
        if headers: h.update(headers)
        return (f"{method} /mcp HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in h.items()) + "\r\n").encode() + body

    def read(self):
        while b"\r\n\r\n" not in self.buffer:
            part = self.sock.recv(65536)
            assert part, "HTTP connection closed before response headers"
            self.buffer += part
        head, self.buffer = self.buffer.split(b"\r\n\r\n", 1)
        lines = head.decode().split("\r\n"); status = int(lines[0].split()[1])
        headers = dict(line.split(": ", 1) for line in lines[1:])
        length = int(headers["Content-Length"])
        while len(self.buffer) < length:
            part = self.sock.recv(65536)
            assert part, "HTTP connection closed before response body"
            self.buffer += part
        body, self.buffer = self.buffer[:length], self.buffer[length:]
        return status, headers, json.loads(body) if body else None

    def request(self, body=b"", **kw):
        self.sock.sendall(self.packet(body, **kw)); return self.read()

    def rpc(self, method, params=None, **kw):
        self.sequence += 1
        body = dict(jsonrpc="2.0", id=self.sequence, method=method)
        if params is not None: body["params"] = params
        return self.request(body, **kw)

    def initialize(self):
        status, headers, reply = self.rpc("initialize", dict(protocolVersion="2025-06-18", capabilities={}, clientInfo=dict(name="self", version="1")))
        assert status == 200 and reply["result"]["protocolVersion"] == "2025-06-18"
        assert reply["result"]["capabilities"] == dict(tools=dict(listChanged=False))
        self.session = headers["Mcp-Session-Id"]
        assert len(self.session) == 32 and all(c in "0123456789abcdef" for c in self.session)
        assert self.request(dict(jsonrpc="2.0", method="notifications/initialized"))[0] == 202

    def tool(self, name, **args):
        status, _, reply = self.rpc("tools/call", dict(name=name, arguments=args))
        assert status == 200 and "result" in reply, (status, reply)
        return reply["result"]


def text(result):
    return result["content"][0]["text"]


def click_args(**kw):
    args = dict(x=30, y=20, risk="low", target="Notes"); args.update(kw); return args


def write_calibration(path):
    points = [[0, 0, 0, 0], [32767, 0, 319, 0], [0, 32767, 0, 199],
              [32767, 32767, 319, 199], [16383.5, 16383.5, 159.5, 99.5]]
    path.write_text(json.dumps(dict(version=1, width=320, height=200, logical_width=320, logical_height=200, pairs=points)))
    path.chmod(0o600)


@contextlib.contextmanager
def keyboard(kbd_build, tmp_path, mode="manual"):
    fifo = tmp_path / "hidg0"; os.mkfifo(fifo)
    reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
    client, server = socket.socketpair(); cr, cw = os.pipe()
    p = subprocess.Popen([str(kbd_build[1]), str(server.fileno()), str(fifo), mode, str(cr)],
                         pass_fds=(server.fileno(), cr), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    server.close(); os.close(cr)
    try:
        assert select.select([reader], [], [], 15)[0], "kbd driver failed to start"
        yield client, reader, cw, p
    finally:
        client.close(); os.close(cw)
        try: out, err = p.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            p.terminate(); out, err = p.communicate(timeout=3)
        os.close(reader)
        assert p.returncode == 0 and not err, (out, err)


@contextlib.contextmanager
def server(binary, tmp_path, kbd, *, vnc=None, connections=1, policy=None, tcp=False, production=False, calibration_valid=True):
    token_path = tmp_path / "token"
    pw_path = tmp_path / "vnc.pass"; pw_path.write_bytes(b"secret12"); pw_path.chmod(0o600)
    calibration = tmp_path / "calib.json"; write_calibration(calibration)
    if not calibration_valid: calibration.write_text("{}")
    config = tmp_path / "policy.json"
    config.write_text(json.dumps(policy or dict(confirm_first=False, confirm_app_switch=False)))
    listen = "127.0.0.1:8765"
    clients, children, fds = [], [], []
    command = [str(AGENT / "build/mk20-agent" if production else binary), "--mcp", "--mcp-listen", listen,
               "--mcp-no-bindtodevice", "--mcp-token-file", str(token_path), "--policy", str(config), "--calibration", str(calibration)]
    if isinstance(kbd, Path): command += ["--kbd-sock", str(kbd)]
    else: command += ["--kbd-fd", str(kbd.fileno())]; fds.append(kbd.fileno())
    if vnc:
        command += ["--vnc-host", "127.0.0.1", "--vnc-port", str(vnc.port), "--password-file", str(pw_path)]
        if vnc.client:
            command += ["--vnc-fd", str(vnc.client.fileno())]; fds.append(vnc.client.fileno())
    if not tcp:
        for _ in range(connections):
            parent, child = socket.socketpair(); clients.append(parent); children.append(child); fds.append(child.fileno())
            command += ["--mcp-connected-fd", str(child.fileno())]
    logpath = tmp_path / "mcp.log"
    with logpath.open("w+") as log:
        p = subprocess.Popen(command, pass_fds=tuple(fds), stdout=subprocess.PIPE, stderr=log, text=True)
        for child in children: child.close()
        try:
            deadline = time.monotonic() + 15
            while "mcp ready" not in logpath.read_text() and p.poll() is None and time.monotonic() < deadline: time.sleep(.01)
            assert "mcp ready" in logpath.read_text(), "MCP startup failed: " + logpath.read_text()
            token = token_path.read_text()
            if tcp:
                for _ in range(connections): clients.append(socket.create_connection(("127.0.0.1", 8765)))
            yield [Client(c, token, listen) for c in clients], p, logpath
        finally:
            for client in clients: client.close()
            if p.poll() is None: p.terminate()
            out, _ = p.communicate(timeout=5)
            logs = logpath.read_text()
            if token_path.exists(): assert token_path.read_text() not in logs + out
            assert "Authorization" not in logs + out and "secret12" not in logs + out
            assert "AddressSanitizer" not in logs and "runtime error:" not in logs


def allowed(client, controls):
    os.write(controls, b"a")
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if "allowed=true" in text(client.tool("status")): return
        time.sleep(.02)
    raise AssertionError("keyboard allow status did not propagate")


def reports(reader):
    try: return os.read(reader, 65536)
    except BlockingIOError: return b""


def test_protocol_socketpair(mcp_binary, kbd_build, tmp_path):
    with keyboard(kbd_build, tmp_path) as (kbd, _, _, _), server(mcp_binary, tmp_path, kbd) as (cs, _, _):
        c = cs[0]
        status, h, body = c.rpc("ping", auth=False)
        assert status == 401 and h["WWW-Authenticate"] == "Bearer" and body is None
        wrong = c.token; c.token = "f" * 64
        assert c.rpc("ping")[0] == 401
        c.token = wrong
        assert c.rpc("ping")[0] == 400
        c.initialize()
        listed = c.rpc("tools/list")[2]["result"]["tools"]
        assert {t["name"] for t in listed} == {"status", "screenshot", "click", "double_click", "move", "drag", "scroll", "type_text", "key", "estop", "run_task", "task_status", "cancel_task"}
        assert all(t["inputSchema"]["type"] == "object" for t in listed)
        assert "disabled" in text(c.tool("status"))
        assert text(c.tool("click", **click_args())) == "disabled"
        assert text(c.tool("run_task", goal="private goal")) == "disabled"
        assert c.request(method="GET")[0:2][0] == 405
        assert c.request(method="GET")[1]["Allow"] == "POST"
        assert c.request([])[2]["error"]["code"] == -32600
        assert c.request(b"{")[2]["error"]["code"] == -32700
        assert c.rpc("ping", [])[2]["error"]["code"] == -32602
        assert c.rpc("ping", headers={"Content-Type": "application/json; charset=utf-8"})[0] == 200
        assert c.rpc("unknown")[2]["error"]["code"] == -32601
        assert c.rpc("tools/call", dict(name="enable"))[2]["error"]["code"] == -32602
        assert c.rpc("ping", headers={"MCP-Protocol-Version": "future"})[0] == 400
        assert c.rpc("ping", headers={"Origin": "http://evil"})[0] == 400
        assert c.rpc("ping", headers={"Host": "evil"})[0] == 400
        assert c.rpc("ping", headers={"Origin": "http://172.30.250.2:8765", "MCP-Protocol-Version": "2025-06-18"})[0] == 200
        saved = c.session; c.session = "not-a-session"
        assert c.rpc("ping")[0] == 404
        c.session = saved
        assert c.request(dict(jsonrpc="2.0", id=9, result={}))[0] == 202
        assert c.request(dict(jsonrpc="2.0", method="unknown"))[0] == 202
        assert c.request(method="DELETE")[0] == 200
        assert c.rpc("ping")[0] == 404


def test_screenshot_click_budget_ipc(mcp_binary, kbd_build, tmp_path):
    with keyboard(kbd_build, tmp_path) as (kbd, reader, controls, proc), FakeVNC(socketpair=True) as vnc:
        with server(mcp_binary, tmp_path, kbd, vnc=vnc, policy=dict(confirm_first=False, confirm_app_switch=False, max_steps=1)) as (cs, _, log):
            c = cs[0]; c.initialize()
            assert text(c.tool("click", **click_args())) == "disabled"
            allowed(c, controls)
            assert "screenshot/calibration" in text(c.tool("click", **click_args()))
            shot = c.tool("screenshot"); image = shot["content"][1]
            assert image["mimeType"] == "image/jpeg" and base64.b64decode(image["data"]).startswith(b"\xff\xd8")
            assert "width=320 height=200" in text(shot)
            assert text(c.tool("click", **click_args())) == "ok"
            assert text(c.tool("move", **click_args())) == "budget"
            assert not vnc.events  # RFB is read-only, all input goes through real ctl lease
            assert b"\x02" in reports(reader)
            assert "private" not in log.read_text()
        kbd.shutdown(socket.SHUT_RDWR)
        out, err = proc.communicate(timeout=3)
        summary = json.loads(out); assert summary["acts"] >= 3 and not err


@pytest.mark.parametrize("mode", ["reject", "timeout", "early", "ok"])
def test_confirmation_ipc(mcp_binary, kbd_build, tmp_path, mode):
    with keyboard(kbd_build, tmp_path, mode) as (kbd, reader, _, proc), FakeVNC(socketpair=True) as vnc:
        with server(mcp_binary, tmp_path, kbd, vnc=vnc, policy=dict(confirm_first=False, confirm_ms=1000)) as (cs, _, _):
            c = cs[0]; c.initialize(); c.tool("screenshot")
            result = c.tool("key", combo="cmd+q", risk="low", target="Notes")
            if mode in ("reject", "timeout"):
                assert result["isError"] and "needs_confirmation" in text(result)
                assert b"\x02" not in reports(reader) and not vnc.events
                # No key act beyond the startup neutral report.
                assert "allowed=true" in text(c.tool("status"))
            else:
                assert text(result) == "ok"
        kbd.shutdown(socket.SHUT_RDWR); out, err = proc.communicate(timeout=3)
        summary = json.loads(out)
        assert summary["acts"] == (0 if mode in ("reject", "timeout") else 3)
        assert summary["ignored"] == (1 if mode == "early" else 0) and not err


def test_estop_mid_action_and_keyboard_reallow_ipc(mcp_binary, kbd_build, tmp_path):
    with keyboard(kbd_build, tmp_path) as (kbd, reader, controls, proc), FakeVNC(socketpair=True) as vnc:
        with server(mcp_binary, tmp_path, kbd, vnc=vnc, connections=2) as (cs, _, _):
            c, other = cs; c.initialize(); other.session = c.session
            allowed(c, controls); c.tool("screenshot"); reports(reader)
            drag = dict(jsonrpc="2.0", id=77, method="tools/call", params=dict(name="drag", arguments=dict(x1=5, y1=5, x2=100, y2=100, risk="low", target="Notes")))
            c.sock.sendall(c.packet(drag))
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if select.select([reader], [], [], .01)[0] and b"\x02" in reports(reader): break
            else: raise AssertionError("drag did not begin")
            start = time.monotonic(); assert text(other.tool("estop")) == "stopped"
            assert time.monotonic() - start < .5
            response = c.read()[2]["result"]; assert text(response) == "stopped"
            time.sleep(.03); reports(reader)
            assert text(other.tool("click", **click_args())) == "stopped"
            assert text(other.tool("run_task", goal="private goal")) == "stopped"
            time.sleep(.03); assert not reports(reader)
            os.write(controls, b"d"); time.sleep(.3); allowed(other, controls)
            assert text(other.tool("click", **click_args())) == "ok"
        kbd.shutdown(socket.SHUT_RDWR); out, err = proc.communicate(timeout=3)
        assert json.loads(out)["estops"] >= 1 and not err


@pytest.mark.parametrize("target", ["password", "proxy settings", "unlock", "Security"])
def test_never_do_ipc(mcp_binary, kbd_build, tmp_path, target):
    with keyboard(kbd_build, tmp_path) as (kbd, reader, controls, proc), FakeVNC(socketpair=True) as vnc:
        with server(mcp_binary, tmp_path, kbd, vnc=vnc) as (cs, _, _):
            c = cs[0]; c.initialize(); allowed(c, controls); c.tool("screenshot")
            assert text(c.tool("click", **click_args(target=target))) == "rejected"
        kbd.shutdown(socket.SHUT_RDWR); out, err = proc.communicate(timeout=3)
        assert json.loads(out)["acts"] == 0 and not err


def test_task_slot_cancel_ipc(mcp_binary, kbd_build, tmp_path):
    with keyboard(kbd_build, tmp_path) as (kbd, _, controls, _), FakeVNC(socketpair=True, delay=.2) as vnc:
        with server(mcp_binary, tmp_path, kbd, vnc=vnc) as (cs, _, log):
            c = cs[0]; c.initialize(); assert text(c.tool("run_task", goal="private task")) == "disabled"
            allowed(c, controls)
            result = c.tool("run_task", goal="private task"); task_id = text(result)
            assert task_id.startswith("t") and not result["isError"]
            # No model is configured in this offline runtime, so observe then
            # model fails closed. The task summary must never contain the goal.
            summary = text(c.tool("task_status", id=task_id))
            assert "steps=" in summary and "private" not in summary
            assert not c.tool("cancel_task", id=task_id)["isError"]
            assert "private" not in log.read_text()


@pytest.mark.parametrize("wire,status", [
    (b"POST /mcp HTTP/1.1\r\nHost: x\r\n\r\n", 411),
    (b"POST /mcp HTTP/1.1\r\nHost: x\r\nContent-Length: 65537\r\n\r\n", 413),
    (b"POST /mcp HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n", 411),
    (b"POST /mcp HTTP/1.1\r\nHost: x\r\n" + b"x" * 8200, 431),
])
def test_http_limits_ipc(mcp_binary, kbd_build, tmp_path, wire, status):
    with keyboard(kbd_build, tmp_path) as (kbd, _, _, _), server(mcp_binary, tmp_path, kbd) as (cs, _, _):
        c = cs[0]; c.sock.sendall(wire); assert c.read()[0] == status


def test_slowloris_ipc(mcp_binary, kbd_build, tmp_path):
    with keyboard(kbd_build, tmp_path) as (kbd, _, _, _), server(mcp_binary, tmp_path, kbd) as (cs, _, _):
        c = cs[0]; c.sock.sendall(b"POST /mcp HTTP/1.1\r\nHost:")
        start = time.monotonic(); assert c.sock.recv(1) == b""; elapsed = time.monotonic() - start
        assert 4.7 < elapsed < 6


def test_pipelining_ipc(mcp_binary, kbd_build, tmp_path):
    with keyboard(kbd_build, tmp_path) as (kbd, _, _, _), server(mcp_binary, tmp_path, kbd) as (cs, _, _):
        c = cs[0]; c.initialize()
        packet = c.packet(dict(jsonrpc="2.0", id=8, method="ping"))
        c.sock.sendall(packet * 2)
        assert c.read()[0] == c.read()[0] == 200


def test_real_socket_protocol(mcp_binary, kbd_build, tmp_path):
    with keyboard(kbd_build, tmp_path) as (kbd, _, _, _), server(mcp_binary, tmp_path, kbd, tcp=True) as (cs, _, _):
        c = cs[0]; c.initialize(); assert not c.tool("status")["isError"]
        assert c.request([])[2]["error"]["code"] == -32600


@pytest.mark.parametrize("allow", [False, True])
def test_real_socket_daemon_mcp(mcp_binary, kbd_build, tmp_path, allow):
    with real_daemon(kbd_build, tmp_path, allowed=allow) as (path, reader), FakeVNC() as vnc:
        with server(mcp_binary, tmp_path, path, vnc=vnc, tcp=True, policy=dict(confirm_first=False, confirm_ms=1000)) as (cs, _, _):
            c = cs[0]; c.initialize(); c.tool("screenshot")
            result = c.tool("click", **click_args())
            assert text(result) == ("ok" if allow else "disabled")
            if not allow: assert text(c.tool("run_task", goal="private task")) == "disabled"
            if allow:
                assert b"\x02" in reports(reader)
                denied = c.tool("key", combo="cmd+q", risk="low", target="Notes")
                assert "needs_confirmation" in text(denied)
            assert text(c.tool("estop")) == "stopped"
            assert text(c.tool("click", **click_args())) == "stopped"


def test_bind_refusal_without_namespace_tools(mcp_binary, tmp_path):
    # Always proves bind-time input refusal, including hosts lacking netns tools.
    subprocess.run(["make", "-C", str(AGENT), "build/mcp-bind-probe"], check=True, stdout=subprocess.DEVNULL)
    if "usb0" not in [name for _, name in socket.if_nameindex()]:
        p = subprocess.run([str(AGENT / "build/mcp-bind-probe")], capture_output=True, text=True, timeout=3)
        assert p.returncode and "usb0 bind" in p.stderr
    for address, no_device in [("0.0.0.0:8765", True), ("[::]:8765", True), ("172.30.250.2:8765", True), ("127.0.0.1:8765", False)]:
        command = [str(mcp_binary), "--mcp", "--mcp-listen", address]
        if no_device: command += ["--mcp-no-bindtodevice"]
        p = subprocess.run(command, capture_output=True, text=True)
        assert p.returncode and "invalid configuration" in p.stderr


def test_usb0_namespace_isolation(mcp_binary, tmp_path):
    # Tool absence has the mandatory refusal proof above. If tools exist but
    # unshare/dummy/bind fails, fail visibly, never skip/xfail the gate.
    unshare, ip = shutil.which("unshare"), shutil.which("ip")
    if not unshare or not ip:
        test_bind_refusal_without_namespace_tools(mcp_binary, tmp_path)
        return
    script = AGENT / "tests/netns_mcp.py"
    result = subprocess.run([unshare, "-Urn", "--map-root-user", os.sys.executable, str(script), str(mcp_binary)],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, "netns isolation gate failed: " + result.stderr + result.stdout


def test_all_action_shapes_ipc(mcp_binary, kbd_build, tmp_path):
    actions = [("move", click_args()), ("click", click_args()), ("double_click", click_args()),
               ("drag", dict(x1=5, y1=5, x2=40, y2=30, risk="low", target="Notes")),
               ("type_text", dict(text="hello", risk="low", target="Notes")),
               ("key", dict(combo="cmd+a", risk="low", target="Notes"))]
    with keyboard(kbd_build, tmp_path) as (kbd, _, controls, _), FakeVNC(socketpair=True) as vnc:
        with server(mcp_binary, tmp_path, kbd, vnc=vnc) as (cs, _, _):
            c = cs[0]; c.initialize(); allowed(c, controls); c.tool("screenshot")
            for name, args in actions: assert text(c.tool(name, **args)) == "ok", name
            assert text(c.tool("type_text", text="你好", risk="low", target="Notes")) == "unsupported"
            # The real ctl driver has the baseline non-scroll descriptor.
            assert text(c.tool("scroll", **click_args(dy=1, dx=0))) == "unsupported"


@pytest.mark.parametrize("policy,kind,args,outcome", [
    (dict(confirm_first=False, exec_ms=5), "type_text", dict(text="a" * 256, risk="low", target="Notes"), "error"),
    (dict(confirm_first=False, wall_ms=1), "click", click_args(), "budget"),
    (dict(confirm_first=False), "click", click_args(x=320), "rejected"),
])
def test_action_deadlines_and_bounds_ipc(mcp_binary, kbd_build, tmp_path, policy, kind, args, outcome):
    with keyboard(kbd_build, tmp_path) as (kbd, _, controls, _), FakeVNC(socketpair=True) as vnc:
        with server(mcp_binary, tmp_path, kbd, vnc=vnc, policy=policy) as (cs, _, _):
            c = cs[0]; c.initialize(); allowed(c, controls); c.tool("screenshot")
            result = c.tool(kind, **args)
            assert result["isError"] and text(result) == outcome


def test_latest_frame_and_calibration_ipc(mcp_binary, kbd_build, tmp_path):
    with keyboard(kbd_build, tmp_path) as (kbd, _, controls, _), FakeVNC(socketpair=True) as vnc:
        with server(mcp_binary, tmp_path, kbd, vnc=vnc, connections=2) as (cs, _, _):
            c, other = cs; c.initialize(); other.initialize(); allowed(c, controls)
            c.tool("screenshot"); other.tool("screenshot")
            assert "screenshot/calibration" in text(c.tool("click", **click_args()))
            assert text(other.tool("click", **click_args())) == "ok"


def test_keepalive_is_bounded_ipc(mcp_binary, kbd_build, tmp_path):
    with keyboard(kbd_build, tmp_path) as (kbd, _, _, _), server(mcp_binary, tmp_path, kbd) as (cs, _, _):
        c = cs[0]; c.initialize()  # two requests already consumed
        for _ in range(29): assert c.rpc("ping")[0] == 200
        status, headers, _ = c.rpc("ping")
        assert status == 200 and headers["Connection"] == "close"
        assert c.sock.recv(1) == b""


def test_four_connections_and_signal_cleanup_ipc(mcp_binary, kbd_build, tmp_path):
    with keyboard(kbd_build, tmp_path) as (kbd, _, _, _), server(mcp_binary, tmp_path, kbd, connections=4) as (cs, p, _):
        for c in cs: c.initialize(); assert not c.tool("status")["isError"]
        p.terminate(); p.wait(timeout=2); assert p.returncode == 0


def test_production_rejects_test_seams(mcp_binary):
    p = subprocess.run([str(AGENT / "build/mk20-agent"), "--mcp", "--mcp-connected-fd", "0"], capture_output=True, text=True)
    assert p.returncode and "invalid configuration" in p.stderr


def test_calibration_refusal_ipc(mcp_binary, kbd_build, tmp_path):
    with keyboard(kbd_build, tmp_path) as (kbd, _, controls, _), FakeVNC(socketpair=True) as vnc:
        with server(mcp_binary, tmp_path, kbd, vnc=vnc, calibration_valid=False) as (cs, _, _):
            c = cs[0]; c.initialize(); allowed(c, controls)
            assert "calibration=required" in text(c.tool("screenshot"))
            assert "screenshot/calibration" in text(c.tool("click", **click_args()))


def test_real_socket_estop_mid_drag(mcp_binary, kbd_build, tmp_path):
    with real_daemon(kbd_build, tmp_path) as (path, reader), FakeVNC() as vnc:
        with server(mcp_binary, tmp_path, path, vnc=vnc, tcp=True, connections=2) as (cs, _, _):
            c, other = cs; c.initialize(); other.session = c.session; c.tool("screenshot"); reports(reader)
            c.sock.sendall(c.packet(dict(jsonrpc="2.0", id=77, method="tools/call", params=dict(name="drag",
                arguments=dict(x1=5, y1=5, x2=100, y2=100, risk="low", target="Notes")))))
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if select.select([reader], [], [], .01)[0] and b"\x02" in reports(reader): break
            else: raise AssertionError("real daemon drag did not begin")
            assert text(other.tool("estop")) == "stopped"
            assert text(c.read()[2]["result"]) == "stopped"
            time.sleep(.1); reports(reader)
            assert text(c.tool("click", **click_args())) == "stopped"
            time.sleep(.05); assert not reports(reader)


def test_disabled_readonly_screenshot_ipc(mcp_binary, kbd_build, tmp_path):
    with keyboard(kbd_build, tmp_path) as (kbd, _, _, proc), FakeVNC(socketpair=True) as vnc:
        with server(mcp_binary, tmp_path, kbd, vnc=vnc, calibration_valid=False) as (cs, _, _):
            c = cs[0]; c.initialize()
            shot = c.tool("screenshot"); assert not shot["isError"]
            assert shot["content"][1]["mimeType"] == "image/jpeg"
            assert text(c.tool("click", **click_args())) == "disabled"
        kbd.shutdown(socket.SHUT_RDWR); out, err = proc.communicate(timeout=3)
        assert json.loads(out)["acts"] == 0 and not err


def test_disconnect_pending_logs_fixed_registry_ipc(mcp_binary, kbd_build, tmp_path):
    with keyboard(kbd_build,tmp_path) as (kbd,_,controls,_), FakeVNC(socketpair=True) as vnc:
        with server(mcp_binary,tmp_path,kbd,vnc=vnc,connections=2,policy=dict(confirm_first=False,confirm_ms=1000)) as (cs,_,log):
            c,other=cs; c.initialize(); other.session=c.session
            allowed(c,controls); c.tool("screenshot")
            c.sock.sendall(c.packet(dict(jsonrpc="2.0",id=99,method="tools/call",params=dict(name="key",arguments=dict(combo="cmd+q",risk="low",target="Notes")))))
            time.sleep(.05); c.sock.shutdown(socket.SHUT_RDWR)
            deadline=time.monotonic()+2
            while time.monotonic()<deadline:
                if "status=499" in log.read_text(): break
                time.sleep(.01)
            assert "method=tools/call tool=key status=499 latency_ms=" in log.read_text()
            assert "allowed=false" in text(other.tool("status"))
