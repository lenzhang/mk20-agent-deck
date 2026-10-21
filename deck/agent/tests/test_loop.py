"""A5 offline host acceptance: injectable ports, unchanged real kbd ctl over IPC,
and the real daemon/FIFO/pty/Unix listener (kept enabled, even in bind sandboxes)."""
import contextlib
import json
import os
from pathlib import Path
import pty
import select
import shutil
import socket
import subprocess
import time
import tempfile
import tty

import pytest

AGENT = Path(__file__).resolve().parents[1]
KBD = AGENT.parent / "kbd"


def action(kind="move", **kw):
    a = dict(action=kind, risk="low", target="Notes")
    if kind in ("click", "double_click", "move", "scroll"):
        a.update(x=300, y=200)
    a.update(kw)
    return a


@pytest.fixture(scope="module")
def binary():
    subprocess.run(["make", "-j8", "-C", str(AGENT), "build/mk20-agent"], check=True, stdout=subprocess.DEVNULL)
    return AGENT / "build/mk20-agent"


def run(binary, tmp_path, script, *, fds=()):
    path = tmp_path / "loop.json"
    path.write_text(json.dumps(script, ensure_ascii=False))
    p = subprocess.run([str(binary), "--loop-test", str(path)], capture_output=True, text=True,
                       timeout=15, pass_fds=fds)
    assert not p.stderr, p.stderr
    assert len(p.stdout.splitlines()) == 1
    r = json.loads(p.stdout)
    assert (p.returncode == 0) == (r["state"] == "done"), r
    return r


def task(**kw):
    s = dict(allow=True, model=[action(), action("done")])
    s.update(kw)
    return s


def test_power_on_disabled(binary, tmp_path):
    r = run(binary, tmp_path, task(allow=False))
    assert r["state"] == "disabled" and not r["model_calls"] and not r["acts"]


def test_complete(binary, tmp_path):
    r = run(binary, tmp_path, task(model=[action("click"), action("type", text="hello"), action("done")]))
    assert r["state"] == "done" and r["steps"] == 3 and r["confirms"] == 1
    assert r["acts"] == [dict(action="click", input="hid"), dict(action="type", input="hid")]
    assert all(k in r["states"] for k in ("observing", "planning", "acting", "awaiting_confirm"))
    log = json.loads(r["logs"][1])
    assert log["text_len"] == 5 and log["text_hash"] == "a430d84680aabd0b"
    assert log["input_tokens"] == 7 and log["output_tokens"] == 3
    assert "hello" not in "".join(r["logs"])


@pytest.mark.parametrize("state", ["observing", "planning", "acting", "awaiting_confirm"])
def test_estop_every_state(binary, tmp_path, state):
    r = run(binary, tmp_path, task(events=[dict(state=state, kind="estop")]))
    assert r["state"] == "stopped" and r["reason"] == "estop" and not r["acts"] and r["cancelled"]


@pytest.mark.parametrize("where", ["observing", "planning", "acting"])
def test_estop_inside_port(binary, tmp_path, where):
    s = task()
    if where == "observing": s["observations"] = [dict(estop=True)]
    if where == "planning": s["model"][0]["estop"] = True
    if where == "acting": s["act_estop"] = True
    r = run(binary, tmp_path, s)
    assert r["state"] == "stopped" and r["reason"] == "estop" and not r["acts"]


@pytest.mark.parametrize("confirm", ["reject", "timeout"])
def test_confirm_denial(binary, tmp_path, confirm):
    r = run(binary, tmp_path, task(confirm=confirm, policy=dict(confirm_ms=1000), probe_service=True))
    assert (r["state"], r["reason"]) == ("stopped", "confirm_denied") and not r["acts"]
    assert all(rc != 0 for rc in r["service_results"])


def test_stale_confirmation_ignored(binary, tmp_path):
    r = run(binary, tmp_path, task(confirm="timeout", policy=dict(confirm_ms=1000),
            events=[dict(state="awaiting_confirm", kind="confirm", cid=999, ok=True)]))
    assert r["reason"] == "confirm_denied" and not r["acts"]


@pytest.mark.parametrize("s,reason", [
    (task(policy=dict(max_steps=1)), "step_budget"),
    (task(policy=dict(model_calls=1)), "model_budget"),
    (task(policy=dict(wall_ms=100)), "wall_budget"),
    (task(act_delay_ms=2001, probe_service=True), "action_timeout"),
    (task(act_delay_ms=1000, policy=dict(confirm_first=False, wall_ms=300), probe_service=True), "wall_budget"),
    (task(observations=[dict(delay_ms=101)], policy=dict(wall_ms=100)), "wall_budget"),
    (task(model=[action(delay_ms=101)], policy=dict(wall_ms=100)), "wall_budget"),
])
def test_budgets(binary, tmp_path, s, reason):
    r = run(binary, tmp_path, s)
    assert (r["state"], r["reason"]) == ("error", reason)
    if s.get("probe_service"):
        assert not r["acts"]
        assert all(rc != 0 for rc in r["service_results"])


def test_no_progress(binary, tmp_path):
    r = run(binary, tmp_path, task(observations=[dict(hash=4)]*3, model=[action()]*4))
    assert (r["state"], r["reason"]) == ("stopped", "no_progress") and r["ask_user"]
    assert r["model_calls"] == 2


@pytest.mark.parametrize("first,reason,acts", [
    (dict(model=[action(), action(), action("done")],
          events=[dict(state="awaiting_confirm", kind="confirm", cid=1, ok=False)]), "confirm_denied", 1),
    (dict(observations=[dict(hash=4)]*3, model=[action()]*3+[action("done")]), "no_progress", 3),
    (dict(model=[dict(error="auth"), action(), action("done")]), "model", 1),
    (dict(model=[action("ask_user"), action(), action("done")]), "ask_user", 1),
])
def test_new_task_after_ordinary_finish(binary, tmp_path, first, reason, acts):
    state = "error" if reason == "model" else "stopped"
    r = run(binary, tmp_path, task(**first, resubmit=[dict(state=state, reason=reason)]))
    assert r["submissions"] == [dict(state=state, reason=reason, allowed=True, cancelled=False, accepted=True)]
    assert r["state"] == "done" and r["steps"] == 2 and len(r["acts"]) == acts, r


def test_allow_after_error(binary, tmp_path):
    r = run(binary, tmp_path, task(model=[dict(error="auth"), action("done")],
        events=[dict(state="error", kind="allow")], resubmit=[dict(state="idle")]))
    assert r["state"] == "done" and r["submissions"][0]["accepted"], r


@pytest.mark.parametrize("reallow", [False, True])
def test_estop_submit_requires_fresh_allow(binary, tmp_path, reallow):
    events = [dict(state="observing", kind="estop")]
    submissions = [dict(state="stopped", reason="estop")]
    if reallow:
        events.append(dict(state="stopped", kind="allow", tick=10))
        submissions.append(dict(state="idle", tick=10))
    r = run(binary, tmp_path, task(events=events, resubmit=submissions))
    assert r["submissions"][0] == dict(state="stopped", reason="estop", allowed=False, cancelled=True, accepted=False)
    if reallow:
        assert r["submissions"][1]["accepted"] and r["state"] == "done" and len(r["acts"]) == 1, r
    else:
        assert r["state"] == "stopped" and r["reason"] == "estop" and not r["acts"] and not r["model_calls"], r


def test_arbitration_no_replay(binary, tmp_path):
    # Three healthy probes arrive after VNC's first action, then switch at idle.
    events = [dict(state="acting", tick=12, kind="hid", ok=True)]*3
    r = run(binary, tmp_path, task(policy=dict(confirm_first=False), model=[action()]*5+[action("done")],
            hid_faults=[True, True], events=events))
    assert r["state"] == "done", r
    channels = [x["input"] for x in r["acts"]]
    assert channels[:2] == ["hid", "hid"] and "vnc" in channels and channels[-1] == "hid", r
    assert len(r["acts"]) == 5 and r["allups"] == 2


@pytest.mark.parametrize("obs,a,reason", [
    (dict(locked=True), action("type", text="hello"), "lock_screen"),
    (dict(locked=True), action("click"), "lock_screen"),
    ({}, action(target="Unlock"), "never_do"),
    (dict(locked=True), action("paste", text="你好"), "lock_screen"),
    (dict(title="loginwindow"), action("click"), "never_do"),
    (dict(title="Screen Sharing"), action("click"), "never_do"),
    (dict(title="mihomo proxy"), action(), "never_do"),
    (dict(title="Security & Privacy"), action(), "never_do"),
    (dict(secure_input=True), action("type", text="hi"), "secure_input"),
    ({}, action("type", text="hi", target="Password"), "secure_input"),
    ({}, action("paste", text="你好"), "unsupported"),
])
def test_never_do(binary, tmp_path, obs, a, reason):
    r = run(binary, tmp_path, task(observations=[obs], model=[a]))
    assert (r["state"], r["reason"]) == ("error", reason) and not r["confirms"] and not r["acts"]


@pytest.mark.parametrize("kind", ["auth", "tls", "proxy", "bad_response", "config"])
def test_nonretryable(binary, tmp_path, kind):
    r = run(binary, tmp_path, task(model=[dict(error=kind, retryable=True), action("done")]))
    assert r["state"] == "error" and r["model_calls"] == 1


@pytest.mark.parametrize("kind", ["rate", "server", "timeout", "network", "dns", "connect", "io"])
def test_retry_backoff(binary, tmp_path, kind):
    r = run(binary, tmp_path, task(model=[dict(error=kind, retryable=True)]*2+[action("done")]))
    assert r["state"] == "done" and r["model_calls"] == 3 and r["elapsed_ms"] >= 1500


def test_retry_limit(binary, tmp_path):
    r = run(binary, tmp_path, task(model=[dict(error="rate", retryable=True)]*4))
    assert r["state"] == "error" and r["model_calls"] == 3


def test_retry_counts_budget(binary, tmp_path):
    r = run(binary, tmp_path, task(model=[dict(error="rate", retryable=True)]*3, policy=dict(model_calls=2)))
    assert r["reason"] == "model_budget" and r["model_calls"] == 2


def test_lease_loss(binary, tmp_path):
    r = run(binary, tmp_path, task(act_delay_ms=1500, renew_fail=True))
    assert r["state"] == "stopped" and r["reason"] == "lease_lost" and not r["acts"]


def test_model_does_not_override_local_risk(binary, tmp_path):
    r = run(binary, tmp_path, task(policy=dict(confirm_first=False), model=[action(target="发送"), action("done")]))
    assert r["state"] == "done" and r["confirms"] == 1


@pytest.mark.parametrize("model,secret", [("sk-modelSECRET", None), ("Bearer model", None),
        ("Authorization", None), ("opaqueKEYvalue", "opaqueKEYvalue"), ("vncPASSvalue", "vncPASSvalue")])
def test_log_redaction(binary, tmp_path, model, secret):
    r = run(binary, tmp_path, task(model=[action(model=model), action("done")], secrets=[secret] if secret else []))
    assert r["state"] == "done" and r["logs"][0] == "[redacted]"
    assert model not in "".join(r["logs"])


@pytest.fixture(scope="module")
def kbd_build(tmp_path_factory):
    build = tmp_path_factory.mktemp("a5-kbd")
    # Unchanged sources + original Makefile build entirely in a temporary tree.
    shutil.copytree(KBD, build / "kbd", ignore=shutil.ignore_patterns("mk20-kbd", "test_kbd", "test_kbd_tsan", "__pycache__"))
    root = build / "kbd"
    subprocess.run(["make", "-C", str(root), "host"], check=True, stdout=subprocess.DEVNULL)
    port = build / "kbd-port"
    subprocess.run(["cc", "-std=c11", "-D_DEFAULT_SOURCE", "-Wall", "-Wextra", "-Werror", "-O1", "-g",
        "-fsanitize=address,undefined", "-fno-sanitize-recover=undefined", "-I", str(root),
        str(AGENT / "tests/kbd_port_server.c"), str(root / "ctl.c"), str(root / "ctlscr.c"), str(root / "panels.c"), str(root / "hid.c"), "-o", str(port)], check=True)
    return root / "mk20-kbd", port


@contextlib.contextmanager
def kbd_ipc(kbd_build, tmp_path, mode="ok"):
    fifo = tmp_path / "hidg0"; os.mkfifo(fifo)
    reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
    client, server = socket.socketpair()
    p = subprocess.Popen([str(kbd_build[1]), str(server.fileno()), str(fifo), mode], pass_fds=(server.fileno(),),
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    server.close()
    try:
        # Startup is outside task/confirmation/action deadlines. New ASan
        # executables can spend seconds in the host loader on a busy Mac.
        deadline = time.monotonic() + 15
        while not select.select([reader], [], [], .05)[0]:
            if p.poll() is not None:
                out, err = p.communicate()
                raise AssertionError(("kbd IPC driver exited before startup", out, err))
            assert time.monotonic() < deadline, "kbd IPC driver did not start"
        yield client, reader, p
    finally:
        client.close()
        try:
            out, err = p.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            p.terminate(); out, err = p.communicate(timeout=3)
        os.close(reader)
        assert p.returncode == 0 and not err, (out, err)


@pytest.mark.parametrize("mode", ["ok", "early", "reject", "timeout"])
def test_real_ctl_confirmation_ipc(binary, kbd_build, tmp_path, mode):
    with kbd_ipc(kbd_build, tmp_path, mode) as (client, reader, proc):
        r = run(binary, tmp_path, task(kbd_fd=client.fileno(), policy=dict(confirm_ms=1000)), fds=(client.fileno(),))
        reports = os.read(reader, 65536)
        if mode in ("ok", "early"):
            assert r["state"] == "done", r
            assert b"\x02\x00" in reports
        else:
            assert r["state"] == "stopped" and r["reason"] == "confirm_denied", r
            assert len(reports) == 9  # startup keyboard all-up only; no pointer report
        client.shutdown(socket.SHUT_RDWR)
        out, err = proc.communicate(timeout=3)
        assert not err
        status = json.loads(out)
        assert status["ignored"] == (1 if mode == "early" else 0)


@pytest.mark.parametrize("reason", ["confirm_denied", "no_progress", "model"])
def test_real_ctl_new_task_after_finish_ipc(binary, kbd_build, tmp_path, reason):
    mode = "reject" if reason == "confirm_denied" else "ok"
    s = task(policy=dict(confirm_first=False), model=[dict(error="auth"), action(), action("done")])
    if reason == "confirm_denied":
        s = task(model=[action(), action("done")])
    elif reason == "no_progress":
        s = task(policy=dict(confirm_first=False), observations=[dict(hash=4)]*3,
                 model=[action()]*3+[action("done")])
    state = "error" if reason == "model" else "stopped"
    s["resubmit"] = [dict(state=state, reason=reason)]
    with kbd_ipc(kbd_build, tmp_path, mode) as (client, reader, proc):
        s["kbd_fd"] = client.fileno()
        r = run(binary, tmp_path, s, fds=(client.fileno(),))
        assert r["state"] == "done" and r["submissions"][0]["accepted"], r
        client.shutdown(socket.SHUT_RDWR)
        out, err = proc.communicate(timeout=3)
        assert not err
        status = json.loads(out)
        assert status["allowed"] and not status["estops"]


def test_released_event_after_reply_allows_next_task(binary, tmp_path):
    import threading
    client, server = socket.socketpair()
    operations = []

    def serve():
        with server, server.makefile("rb") as wire:
            for line in wire:
                q = json.loads(line)
                op = q["op"]; operations.append(op)
                reply = dict(ok=True, id=q["id"])
                if op == "status":
                    reply.update(v=1, allowed=1, estopped=0)
                elif op == "lease.acquire":
                    reply.update(token=1, ttl_ms=3000)
                payload = json.dumps(reply).encode() + b"\n"
                if op == "lease.release":
                    # Reply arrives first; kbdc clears its token before reading
                    # the notification during the next UI/status operation.
                    payload += b'{"ev":"lease_revoked","reason":"released"}\n'
                server.sendall(payload)

    thread = threading.Thread(target=serve); thread.start()
    try:
        r = run(binary, tmp_path, task(kbd_fd=client.fileno(), policy=dict(confirm_first=False),
            model=[action(), action("ask_user"), action(), action("done")],
            resubmit=[dict(state="stopped", reason="ask_user")]), fds=(client.fileno(),))
        assert r["state"] == "done" and r["submissions"][0]["accepted"], r
        assert operations.count("lease.acquire") == operations.count("lease.release") == 2
        assert "estop" not in operations
    finally:
        client.close(); thread.join(2)
        assert not thread.is_alive()


@pytest.mark.parametrize("state", ["observing", "planning", "acting", "awaiting_confirm"])
def test_real_ctl_estop_ipc(binary, kbd_build, tmp_path, state):
    with kbd_ipc(kbd_build, tmp_path, "estop-"+state) as (client, reader, proc):
        r = run(binary, tmp_path, task(kbd_fd=client.fileno()), fds=(client.fileno(),))
        assert r["state"] == "stopped" and not r["steps"], r
        reports = os.read(reader, 65536)
        assert b"\x02" not in reports


@contextlib.contextmanager
def real_daemon(kbd_build, tmp_path, *, allowed=True):
    # Darwin's pytest base path can exceed sockaddr_un.sun_path and hid.path.
    # Keep the real daemon's fixture paths short on every host.
    tmp_path = Path(tempfile.mkdtemp(prefix="a5-kbd-", dir="/tmp"))
    fifo = tmp_path / "hidg0"; os.mkfifo(fifo)
    reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
    gm, gs = pty.openpty(); hm, hs = pty.openpty()
    for fd in (gm, gs, hm, hs): tty.setraw(fd)
    fb = tmp_path / "fb"; fb.mkdir()
    for i in range(1, 22): (fb / f"fb{i}").touch()
    path = tmp_path / "kbd.sock"
    command = [str(kbd_build[0]), "--run", "--gd32", os.ttyname(gs), "--host", os.ttyname(hs),
        "--fb", str(fb), "--bl", "", "--status", str(tmp_path / "status"), "--knob-timeout-ms", "1",
        "--lock", str(tmp_path / "kbd.lock"), "--no-gd32-write", "--hid", str(fifo),
        "--hid-stop-file", str(tmp_path / "stop"), "--ctl", str(path)]
    if allowed: command.append("--ctl-allow")
    p = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        end = time.monotonic() + 3
        while not path.exists() and p.poll() is None and time.monotonic() < end: time.sleep(.01)
        if not path.exists():
            if p.poll() is not None:
                _, err = p.communicate()
                raise RuntimeError("real Unix kbd listener unavailable: " + err.decode())
            p.terminate(); _, err = p.communicate(timeout=3)
            raise RuntimeError("real Unix kbd listener unavailable: " + err.decode())
        yield path, reader
    finally:
        if p.poll() is None: p.terminate()
        p.communicate(timeout=3)
        for fd in (reader, gm, gs, hm, hs): os.close(fd)
        shutil.rmtree(tmp_path)


def test_real_socket_daemon_complete(binary, kbd_build, tmp_path):
    with real_daemon(kbd_build, tmp_path) as (path, reader):
        r = run(binary, tmp_path, task(kbd_socket=str(path), policy=dict(confirm_first=False)))
        assert r["state"] == "done", r
        assert b"\x02\x00" in os.read(reader, 65536)


def test_real_socket_daemon_confirm_timeout(binary, kbd_build, tmp_path):
    with real_daemon(kbd_build, tmp_path) as (path, reader):
        r = run(binary, tmp_path, task(kbd_socket=str(path), policy=dict(confirm_ms=1000)))
        assert r["reason"] == "confirm_denied" and r["state"] == "stopped", r


@pytest.mark.parametrize("state", ["observing", "planning", "acting", "awaiting_confirm"])
def test_real_socket_daemon_estop(binary, kbd_build, tmp_path, state):
    with real_daemon(kbd_build, tmp_path) as (sockpath, reader):
        script = task(kbd_socket=str(sockpath), observations=[dict(delay_ms=300)],
                      model=[action(delay_ms=300), action("done")], act_delay_ms=300)
        if state == "acting":
            # Reach acting without a keyboard confirmation, then stop during
            # act_delay before kbdc_execute can send any pointer report.
            script["policy"] = dict(confirm_first=False)
        path = tmp_path / "loop.json"; path.write_text(json.dumps(script))
        agent = subprocess.Popen([str(binary), "--loop-test", str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as control:
                control.settimeout(2); control.connect(str(sockpath))
                wire = control.makefile("rwb", buffering=0)
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    wire.write(b'{"op":"status"}\n')
                    status = json.loads(wire.readline())
                    if status["ui"]["state"] == state: break
                    time.sleep(.005)
                else: raise AssertionError("agent did not reach " + state)
                wire.write(b'{"op":"estop"}\n')
                out, err = agent.communicate(timeout=3)
                assert not err
                r = json.loads(out)
                assert r["state"] == "stopped" and not r["steps"], r
                assert b"\x02" not in os.read(reader, 65536)
        finally:
            if agent.poll() is None: agent.terminate(); agent.communicate(timeout=3)


@pytest.mark.parametrize("reply", [b"{}\n", b"[]\n", b"{"+b"x"*1023+b"\n", b'{"ok":true,"id":1,"id":1}\n',
    b'{"ok":true,"id":9}\n', b'{"ok":true,"id":1,"allowed":1}\n', b'{"ev":"confirm.result","cid":1,"ok":"true"}\n'])
def test_kbd_malformed_reply_fails_closed(binary, tmp_path, reply):
    import threading
    client, server = socket.socketpair()
    def serve():
        with server:
            server.recv(2048)
            try: server.sendall(reply)
            except BrokenPipeError: pass
    thread = threading.Thread(target=serve); thread.start()
    try:
        r = run(binary, tmp_path, task(kbd_fd=client.fileno()), fds=(client.fileno(),))
        assert r["state"] == "stopped" and not r["model_calls"] and not r["steps"]
    finally:
        client.close(); thread.join(2)


def test_unknown_transport_no_retry(binary, tmp_path):
    r = run(binary, tmp_path, task(model=[dict(error="future_permanent", retryable=True), action("done")]))
    assert r["state"] == "error" and r["model_calls"] == 1


def test_script_cannot_enable_real_kbd(binary, kbd_build, tmp_path):
    with kbd_ipc(kbd_build, tmp_path, "disabled") as (client, reader, proc):
        r = run(binary, tmp_path, task(kbd_fd=client.fileno()), fds=(client.fileno(),))
        assert r["state"] == "disabled" and not r["model_calls"] and not r["steps"]


def test_real_ctl_renew_during_model_ipc(binary, kbd_build, tmp_path):
    with kbd_ipc(kbd_build, tmp_path) as (client, reader, proc):
        r = run(binary, tmp_path, task(kbd_fd=client.fileno(), policy=dict(confirm_first=False),
            model=[action(), action("done", delay_ms=3200)]), fds=(client.fileno(),))
        assert r["state"] == "done" and r["renews"] >= 3, r


def test_screen_coordinate_bounds(binary, tmp_path):
    r = run(binary, tmp_path, task(model=[action(x=1001)]))
    assert r["state"] == "error" and r["reason"] == "coordinates" and not r["confirms"]


def test_real_ctl_partial_type_timeout_allup(binary, kbd_build, tmp_path):
    with kbd_ipc(kbd_build, tmp_path) as (client, reader, proc):
        r = run(binary, tmp_path, task(kbd_fd=client.fileno(), policy=dict(confirm_first=False, exec_ms=50),
                                      model=[action("type", text="a"*256)]), fds=(client.fileno(),))
        assert r["state"] in ("error", "stopped") and not r["steps"], r
        # Give the unchanged kbd service one poll tick to flush the safety report.
        time.sleep(.02)
        reports = os.read(reader, 65536)
        assert reports.endswith(b"\x01" + bytes(8)), reports.hex()
