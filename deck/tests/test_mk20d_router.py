"""1.2.0 on-board router service under mk20d: kill switches, supervised loop (backoff, stop file, single instance),
selftest INFO-only reporting (never gates confirm), health.json "router"."""
import json, os, shutil, socket, subprocess, threading, time
import pytest
from conftest import DECK
from test_mk20d import rel, g1, conf, cfg, run_boot  # noqa: F401  (fixtures)
from mk20deck.protocol import CMD_JSON, encode_frame

pytestmark = pytest.mark.skipif(not shutil.which("gcc"), reason="needs gcc")


def ksc(col, row, pressed):
    return encode_frame(CMD_JSON, json.dumps({"method": "keyStateChanged", "parameters":
                                              {"col": col, "row": row, "pressed": pressed}}).encode(), 1)


@pytest.mark.parametrize("how", ["default", "flag", "env", "conf", "bad"])
def test_router_kill_switches(dev, rel, how):
    env = {}
    if how == "flag":
        dev.p("mnt/SDCARD/mk20-no-router").write_text("")
    elif how == "env":
        env = {"MK20_ROUTER": "0"}
    elif how == "conf":
        conf(dev, "FEATURE_ROUTER=0\n")
    elif how == "bad":
        conf(dev, "FEATURE_ROUTER=yes\n")
    r = dev.run(rel / "sbin/mk20d", "config", env=env)
    c = dict(l.split("=", 1) for l in r.stdout.splitlines())
    assert c["FEATURE_ROUTER"] == ("1" if how in ("default", "bad") else "0")
    assert ("invalid FEATURE_ROUTER" in c.get("WARNINGS", "")) == (how == "bad")


@pytest.fixture
def svc(dev, rel, g1):
    """Composite up with hidg0 + a host build of mk20-router in the release copy."""
    subprocess.run(["gcc", "-O2", "-o", str(rel / "bin/mk20-router"), str(DECK / "router/mk20_router.c")], check=True)
    (g1 / "configs/c.1/ecm.usb0").mkdir(); (g1 / "configs/c.1/hid.usb0").mkdir()
    dev.p("dev/hidg0").write_bytes(b"")
    dev.env.update({"MK20_ROUTER_BACKOFF_MAX": "1"})
    return dev.p("tmp/mk20/kd-tap.sock")


def tap_server(path, payload, conns):
    s = socket.socket(socket.AF_UNIX); s.bind(str(path)); s.listen(4)
    def loop():
        while True:
            try:
                c, _ = s.accept()
            except OSError:
                return
            conns.append(1); c.sendall(payload); time.sleep(0.2); c.close()   # bridge restart: tap closes
    threading.Thread(target=loop, daemon=True).start()
    return s


def wait(pred, t=15):
    end = time.time() + t
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.1)
    return False


def test_router_loop_restarts_and_stops(dev, rel, svc):
    conns = []
    srv = tap_server(svc, ksc(0, 3, 1) + ksc(0, 3, 0), conns)
    p = subprocess.Popen(["busybox", "sh", str(rel / "sbin/mk20d"), "router"], env=dev.env,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert wait(lambda: len(conns) >= 2), "router not restarted after the tap closed"
        hid = dev.p("dev/hidg0").read_bytes()
        up = bytes([1] + [0] * 8)   # startup all-keys-up, PTT press -> RAlt, release -> 0
        assert hid[:27] == up + bytes([1, 0x40] + [0] * 7) + up
        r2 = dev.run(rel / "sbin/mk20d", "router", timeout=10)                 # single instance (flock)
        assert r2.returncode == 0
        dev.p("tmp/mk20-router-stop").write_text("")
        p.wait(10)
    finally:
        if p.poll() is None:
            p.kill(); p.wait(5)
        srv.close()
    log = dev.p("data/mk20/logs/mk20d.log").read_text()
    assert "INFO mk20d router" in log and "stop (stop file)" in log
    assert "mk20-router exit rc=0" in dev.p("tmp/mk20/router.log").read_text()


def test_router_loop_ends_when_disabled_or_composite_down(dev, rel, svc):
    dev.p("mnt/SDCARD/mk20-no-router").write_text("")
    assert dev.run(rel / "sbin/mk20d", "router", timeout=10).returncode == 0
    os.unlink(dev.p("mnt/SDCARD/mk20-no-router"))
    shutil.rmtree(dev.p("sys/kernel/config/usb_gadget/g1/configs/c.1/ecm.usb0"))
    assert dev.run(rel / "sbin/mk20d", "router", timeout=10).returncode == 0
    log = dev.p("data/mk20/logs/mk20d.log").read_text()
    assert "stop (disabled)" in log and "stop (composite down)" in log


def test_router_absent_tap_backs_off(dev, rel, svc):
    """No tap socket yet: the loop waits (backoff) without spinning, then stops on the stop file."""
    p = subprocess.Popen(["busybox", "sh", str(rel / "sbin/mk20d"), "router"], env=dev.env)
    try:
        time.sleep(2.5); assert p.poll() is None
        dev.p("tmp/mk20-router-stop").write_text(""); p.wait(10)
    finally:
        if p.poll() is None:
            p.kill(); p.wait(5)
    assert not dev.p("tmp/mk20/router.log").exists()   # binary never started without a tap


def test_selftest_router_is_info_only(dev, rel, g1):
    base = dev.run(rel / "sbin/mk20d", "selftest")
    (g1 / "configs/c.1/ecm.usb0").mkdir()
    r = dev.run(rel / "sbin/mk20d", "selftest")
    assert "INFO router" in r.stdout and "WARN router" not in r.stdout and "FAIL router" not in r.stdout
    dev.p("mnt/SDCARD/mk20-no-router").write_text("")
    r2 = dev.run(rel / "sbin/mk20d", "selftest")
    assert "INFO router disabled" in r2.stdout and r2.returncode == r.returncode
    h = json.loads(dev.run(rel / "sbin/mk20d", "health").stdout)
    assert h["router"] == {"enabled": False, "running": h["router"]["running"], "loop": False, "tap": False}
    assert base.returncode == 0


@pytest.mark.parametrize("kbd_enabled", [False, True])
def test_health_status_not_degraded_by_router(dev, rel, g1, kbd_enabled):
    # Healthy USB + independent KD/kbd/router process lists: changing router state must
    # not invent a forbidden KD or hide a missing mk20-kbd (K4 defaults to FEATURE_KBD=1).
    (g1 / "configs/c.1/ecm.usb0").mkdir()
    (dev.mock / "netstat.out").write_text("Proto Recv-Q Send-Q Local Address Foreign Address State\n"
        "tcp 0 0 172.30.250.2:17890 0.0.0.0:* LISTEN\n"
        "tcp 0 0 169.254.7.2:17890 0.0.0.0:* LISTEN\n")
    (dev.mock / "pgrep.keyed").touch()
    (dev.mock / "pgrep.kd").write_text("")
    (dev.mock / "pgrep.kbd").write_text("456\n" if kbd_enabled else "")
    (dev.mock / "pgrep.router").write_text("")
    if kbd_enabled:
        status = dev.p("run/mk20/kbd.status"); status.parent.mkdir(parents=True, exist_ok=True)
        status.write_text(json.dumps({"knob_ok": 6, "panels_written": 21, "write_errors": 0}))
    env = {"MK20_KBD": str(int(kbd_enabled))}
    h = json.loads(dev.run(rel / "sbin/mk20d", "health", env=env).stdout)
    st = h["status"]
    assert st == "ok" and h["selftest"]["pass"] is True and h["kd"] == 0
    assert h["kbd"]["enabled"] is kbd_enabled and h["kbd"]["running"] is kbd_enabled
    assert h["router"]["running"] is False
    (dev.mock / "pgrep.router").write_text("123\n")
    running = json.loads(dev.run(rel / "sbin/mk20d", "health", env=env).stdout)
    assert running["status"] == st and running["router"]["running"] is True
    h2 = json.loads(dev.run(rel / "sbin/mk20d", "health", env=dict(env, MK20_ROUTER="0")).stdout)
    assert h2["status"] == st and h2["router"]["enabled"] is False and h["router"]["enabled"] is True
    assert h2["router"]["running"] is True and h2["kd"] == 0 and h2["selftest"]["pass"] is True


def test_boot_launch_gated(dev, rel, g1):
    r, log = run_boot(dev, rel)
    assert "supervisor launched" in log
    dev.p("mnt/SDCARD/mk20-no-router").write_text("")
    r, log = run_boot(dev, rel)
    assert "not started (mode=composite" in log and "enabled=0" in log


@pytest.mark.skipif(not shutil.which("arm-linux-gnueabihf-gcc"), reason="needs armhf cross gcc")
def test_release_binaries_match_source(tmp_path):
    """firmware/release/bin/{ttygs_tcp_bridge,mk20-router,mk20-kbd,mk20-agent} are the static armhf builds of the current sources."""
    rel = DECK / "firmware/release/bin"
    env = dict(os.environ, TZ="UTC", SOURCE_DATE_EPOCH=(os.environ.get("SOURCE_DATE_EPOCH") or
               (DECK / "agent/SOURCE_DATE_EPOCH").read_text().strip()))
    kbd = [str(DECK / "kbd" / f) for f in (
        "mk20_kbd.c", "gd32.c", "gd32io.c", "host.c", "panels.c", "screen.c", "jpeg.c", "kbd.c", "hid.c", "ctl.c", "ctlscr.c", "k2estop.c",
        "onboard.c", "devstat.c", "ui.c", "ui_font.c", "ui_font_render.c", "uicfg.c")] + ["-pthread"]
    for name, src in (("ttygs_tcp_bridge", [str(DECK / "m2-scripts/bridge/ttygs_tcp_bridge.c")]),
                      ("mk20-router", [str(DECK / "router/mk20_router.c")]), ("mk20-kbd", kbd)):
        out = tmp_path / name
        flags = ["-std=c99", "-D_DEFAULT_SOURCE"] if name == "mk20-kbd" else []
        subprocess.run(["arm-linux-gnueabihf-gcc", "-Wall", "-Wextra", "-Werror", "-O2", "-static",
                        f"-ffile-prefix-map={DECK}=.", f"-fdebug-prefix-map={DECK}=.", *flags, "-o", str(out)] + src,
                       check=True, env=env)
        subprocess.run(["arm-linux-gnueabihf-strip", str(out)], check=True)
        assert out.read_bytes() == (rel / name).read_bytes(), f"{name}: run tools/mk20-build-bins.sh"

    agent_build = tmp_path / "agent-build"
    subprocess.run(["make", "-s", "-C", str(DECK / "agent"), f"BUILD={agent_build}",
                    "CC=arm-linux-gnueabihf-gcc", "SAN=", "LDFLAGS=-static",
                    f"CFLAGS=-std=c11 -Wall -Wextra -Werror -O2 -ffile-prefix-map={DECK}=. -fdebug-prefix-map={DECK}=.",
                    str(agent_build / "mk20-agent-nosan")], check=True, env=env)
    subprocess.run(["arm-linux-gnueabihf-strip", str(agent_build / "mk20-agent-nosan")], check=True)
    assert (agent_build / "mk20-agent-nosan").read_bytes() == (rel / "mk20-agent").read_bytes(), "mk20-agent: run tools/mk20-build-bins.sh"
    assert (DECK / "firmware/release/share/ca-bundle.pem").read_bytes() == (DECK / "agent/vendor/ca/cacert.pem").read_bytes()


def test_agent_release_ca_bundle():
    """Public roots ship as an ordinary manifest-enumerated payload file."""
    assert (DECK / "firmware/release/share/ca-bundle.pem").read_bytes() == (DECK / "agent/vendor/ca/cacert.pem").read_bytes()
    script = (DECK / "tools/mk20-release-build.sh").read_text()
    assert 'find . -type f' in script
