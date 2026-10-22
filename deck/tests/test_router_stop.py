"""1.2.0 r2 (Opus r120 #1, kernel UAF): gadget.sh stops the on-board router (and its mk20d supervisor) BEFORE any
UDC/configfs change on every rollback (cleanup) and (re-)setup (setup_main) path: stop file, validated supervisor kill,
bounded SIGTERM (router releases keys), then SIGKILL. Only real `.../bin/mk20-router --src` pids are signalled."""
import os, re, shutil, signal, socket, subprocess, threading, time
from pathlib import Path

import pytest
from conftest import BB, DECK, scope_gadget_fn

pytestmark = pytest.mark.skipif(not BB, reason="BusyBox unavailable (device-side router stop)")

G = (DECK / "firmware/release/share/gadget.sh").read_text()
FN = G[G.index("router_pids() {"):G.index("# KD stop:")]


def _body(start):
    a = G.index(start)
    return G[a:G.index("\n}\n", a)] if start.endswith("{") else G[a:]


def test_router_stopped_before_every_udc_configfs_change():
    cl = _body("cleanup() {")
    i = cl.index("router_stop")
    assert i < cl.index("$G/UDC") and i < cl.index("rmdir $G/functions") and i < cl.index('rm -f "$l"')
    sm = G[G.index("setup_main() {"):]
    i = sm.index("router_stop")
    assert i < sm.index('echo "" > $G/UDC') and i < sm.index('rm -f "$l"') and i < sm.index("mkdir -p $G/functions")
    # every UDC write / function rmdir lives in cleanup() or setup_main(), each of which calls router_stop first
    for m in re.finditer(r'> \$G/UDC|rmdir \$G/functions', G):
        head = G[:m.start()]
        fn = max(head.rfind("cleanup() {"), head.rfind("setup_main() {"))
        assert fn >= 0 and "router_stop" in G[fn:m.start()], G[m.start() - 80:m.start() + 20]
    # rollback-now and flapmon rollbacks all go through cleanup(); boot selftest-fail uses --rollback-now
    assert "router_stop   # before ANY UDC/configfs change" in cl


@pytest.fixture
def h(tmp_path):
    (tmp_path / "mk20").mkdir(); (tmp_path / "bin").mkdir()
    body = scope_gadget_fn(FN, tmp_path)
    s = tmp_path / "rs.sh"
    s.write_text(f'log() {{ echo "$*" >> {tmp_path}/log; }}\n{body}\nrouter_stop\necho done\n')
    return tmp_path, s


def _run(t, s, **env):
    e = dict(os.environ, **env)
    t0 = time.time(); r = subprocess.run([BB, "sh", str(s)], capture_output=True, text=True, timeout=30, env=e)
    return r, time.time() - t0


def _alive(p):
    return p.poll() is None


def test_real_router_gets_sigterm_and_releases(h):
    t, s = h
    rb = t / "bin/mk20-router"
    subprocess.run(["gcc", "-O2", "-o", str(rb), str(DECK / "router/mk20_router.c")], check=True)
    srv = socket.socket(socket.AF_UNIX); srv.bind(str(t / "tap.sock")); srv.listen(1)
    hid = t / "hidg0"; hid.write_bytes(b"")
    p = subprocess.Popen([str(rb), "--src", f"unix:{t / 'tap.sock'}", "--hid", str(hid)], stderr=subprocess.PIPE)
    c, _ = srv.accept()
    from mk20deck.protocol import CMD_JSON, encode_frame
    c.sendall(encode_frame(CMD_JSON, b'{"method":"keyStateChanged","parameters":{"col":0,"row":3,"pressed":1}}', 1))
    end = time.time() + 3
    while not hid.read_bytes().endswith(bytes([1, 0x40] + [0] * 7)) and time.time() < end:
        time.sleep(0.02)
    r, dt = _run(t, s)
    assert r.stdout.strip() == "done" and dt < 3
    assert p.wait(1) == 0 and hid.read_bytes().endswith(bytes([1] + [0] * 8))   # all-keys-up before exit
    assert (t / "stop").exists() and not (t / "log").exists()   # no SIGKILL needed
    c.close(); srv.close()


def test_stubborn_router_sigkilled_bounded_and_supervisor_killed(h):
    t, s = h
    (t / "bin/mk20-router").write_text("trap '' TERM INT\nwhile :; do sleep 0.1; done\n")
    p = subprocess.Popen([BB, "sh", str(t / "bin/mk20-router"), "--src", "unix:/nonexistent"])
    (t / "mk20d").write_text("while :; do sleep 0.1; done\n")
    sup = subprocess.Popen([BB, "sh", str(t / "mk20d"), "router"])
    (t / "mk20/router-loop.pid").write_text(f"{sup.pid}\n")
    time.sleep(0.3)
    r, dt = _run(t, s, MK20_ROUTER_TERM_TICKS="5")
    try:
        assert r.stdout.strip() == "done" and dt < 4
        assert p.wait(2) == -signal.SIGKILL
        assert sup.wait(2) == -signal.SIGTERM
        assert "SIGKILL" in (t / "log").read_text()
    finally:
        for x in (p, sup):
            if _alive(x):
                x.kill(); x.wait()


def test_unrelated_pids_untouched(h):
    t, s = h
    other = subprocess.Popen(["sleep", "30"])                     # stale pid file pointing at a foreign process
    (t / "mk20/router-loop.pid").write_text(f"{other.pid}\n")
    decoy = subprocess.Popen([BB, "sh", "-c", "sleep 30", "mk20-router-not-src"])
    try:
        r, dt = _run(t, s)
        assert r.stdout.strip() == "done" and dt < 2
        assert _alive(other) and _alive(decoy)
    finally:
        other.kill(); decoy.kill(); other.wait(); decoy.wait()


def test_mk20d_supervisor_records_pid_and_rechecks_stop():
    m = (DECK / "firmware/release/sbin/mk20d").read_text()
    a = m[m.index("router)\n"):m.index("selftest) cfg_load")]
    assert 'echo $$ > "$TM/router-loop.pid"' in a
    launch = a.index('"$RB" --src')
    assert a.rfind('[ -f "$STOP" ]', 0, launch) > a.index("t0=$(cut")   # re-check right before launch
