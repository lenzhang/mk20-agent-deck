"""K4: mk20-kbd (KD replacement, option D) release integration. mk20d decides before gadget (opt-in FEATURE_KBD,
debug/rescue-only overrides /data/mk20/use-kd + FAT mk20-use-kd), starts the `mk20d kbd` supervisor instead of the
router (stock KD stopped via procd qt_app2, retried), restarts mk20-kbd with backoff and NEVER falls back to stock KD
(crash loop on probation -> selftest FAIL -> A/B rollback; after confirm WARN + health flags), selftest gates confirm on
/run/mk20/kbd.status; gadget.sh quiesces mk20-kbd's hidg0 before any UDC/configfs change (stop file +
SIGUSR1, wait until /proc shows no hidg0 fd, fail closed TERM->KILL), stops it fully on every cleanup/setup path, and
omits the bridge --tap in kbd mode."""
import json, os, re, signal, subprocess, sys, time
from pathlib import Path
import pytest
from conftest import BB, DECK, scope_gadget_fn
from test_mk20d import rel, g1, conf, cfg, run_boot  # noqa: F401  (fixtures)

GOOD = {"version": "mk20-kbd-0.3", "gd32_rw": 1, "knob_init": "ok", "knob_ok": 6, "panels_written": 21,
        "key_events": 0, "knob_events": 0, "echo_frames": 0, "other_frames": 0, "host": 1}

FAKE_KBD = r'''#!/bin/sh
# fake mk20-kbd: records argv, writes $MOCK_DIR/kbd.status.json to --status, crashes if $MOCK_DIR/kbd.crash
echo "$*" >> "$MOCK_DIR/kbd.args"
st=""; while [ $# -gt 0 ]; do [ "$1" = --status ] && st=$2; shift; done
[ -f "$MOCK_DIR/kbd.crash" ] && exit 7
trap 'echo term >> "$MOCK_DIR/kbd.args"; exit 0' TERM
while :; do [ -n "$st" ] && [ -f "$MOCK_DIR/kbd.status.json" ] && cp "$MOCK_DIR/kbd.status.json" "$st"; sleep 0.1; done
'''
FAKE_SVC = r'''#!/bin/sh
# fake procd qt_app2: stop/start stock KD (pgrep.kd); $MOCK_DIR/kd.stubborn = KD survives stop
echo "$1" >> "$MOCK_DIR/qt_app2.log"
[ "$1" = stop ] && [ -f "$MOCK_DIR/svc.fail" ] && { rm -f "$MOCK_DIR/pgrep.kd"; exit 1; }   # KD gone, procd still armed
case "$1" in stop) [ -f "$MOCK_DIR/kd.stubborn" ] || rm -f "$MOCK_DIR/pgrep.kd";; start) echo 123 > "$MOCK_DIR/pgrep.kd";; esac
'''


def wait(pred, t=15):
    end = time.time() + t
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def kb(dev, rel, g1):
    """Composite up + hidg0, keyed pgrep (KD via pgrep.kd), fake qt_app2 and fake mk20-kbd in the release copy."""
    (g1 / "configs/c.1/ecm.usb0").mkdir(); (g1 / "configs/c.1/hid.usb0").mkdir()
    dev.p("dev/hidg0").write_bytes(b"")
    (dev.mock / "pgrep.keyed").write_text(""); (dev.mock / "pgrep.kd").write_text("123\n")
    dev.p("data/mk20/state").mkdir(parents=True, exist_ok=True)
    svc = dev.tmp / "qt_app2"; svc.write_text(FAKE_SVC); svc.chmod(0o755)
    b = rel / "bin/mk20-kbd"; b.write_text(FAKE_KBD); b.chmod(0o755)
    (dev.mock / "kbd.status.json").write_text(json.dumps(GOOD))
    (rel / "share/gadget.sh").write_text('[ "$1" = --boot ] && env | grep "^MK20_" | sort > "$MK20_ROOT/tmp/mk20/gadget.boot.env"\n'
                                         'echo "$*" >> "$MK20_ROOT/tmp/mk20/gadget.args"\n')
    dev.env.update({"MK20_KDSVC": str(svc), "MK20_KBD_BACKOFF_MAX": "0", "MK20_KBD_KD_TICKS": "5",
                    "MK20_KBD_KD_SETTLE": "0"})
    return dev


def svclog(dev):
    f = dev.mock / "qt_app2.log"
    return f.read_text().split() if f.exists() else []


def mlog(dev):
    return dev.p("data/mk20/logs/mk20d.log").read_text()


@pytest.mark.parametrize("how,want", [("default", "0"), ("conf", "1"), ("env", "1"), ("use-kd", "0"), ("fat", "0"),
                                      ("bad", "0")])
def test_kbd_opt_in_and_kill_switches(dev, rel, how, want):
    env = {}
    if how in ("conf", "use-kd", "fat"):
        conf(dev, "FEATURE_KBD=1\n")
    if how == "env":
        env = {"MK20_KBD": "1"}
    elif how == "use-kd":
        dev.p("data/mk20/use-kd").write_text("")
    elif how == "fat":
        dev.p("mnt/SDCARD/mk20-use-kd").write_text("")
    elif how == "bad":
        conf(dev, "FEATURE_KBD=on\n")
    r = dev.run(rel / "sbin/mk20d", "config", env=env)
    c = dict(l.split("=", 1) for l in r.stdout.splitlines())
    assert c["FEATURE_KBD"] == want
    assert ("invalid FEATURE_KBD" in c.get("WARNINGS", "")) == (how == "bad")
    if how == "use-kd":   # the kill switch also beats the env opt-in
        r = dev.run(rel / "sbin/mk20d", "config", env={"MK20_KBD": "1"})
        assert "FEATURE_KBD=0" in r.stdout


def test_supervisor_stops_kd_runs_kbd_and_ends_on_stop_file(kb, rel):
    dev = kb
    p = subprocess.Popen([BB, "sh", str(rel / "sbin/mk20d"), "kbd"], env=dev.env)
    try:
        assert wait(lambda: (dev.mock / "kbd.args").exists())
        assert svclog(dev) == ["stop"] and not (dev.mock / "pgrep.kd").exists()
        args = (dev.mock / "kbd.args").read_text().split()
        R = str(dev.root)
        assert args[:3] == ["--run", "--host", f"{R}/dev/ttyGS0"]
        assert " ".join(args[3:]) == (f"--hid {R}/dev/hidg0 --hid-stop-file {R}/tmp/mk20-router-stop --status {R}/run/mk20/kbd.status"
                                      f" --lock {R}/tmp/mk20-kbd.lock --exit-file {R}/tmp/mk20-kbd-stop")
        assert wait(lambda: dev.p("run/mk20/kbd.status").exists())
        assert dev.read("tmp/mk20/kbd-loop.pid") == str(p.pid)
        assert dev.run(rel / "sbin/mk20d", "kbd", timeout=10).returncode == 0   # single instance (flock)
        assert (dev.mock / "kbd.args").read_text().count("--run") == 1
        # gadget.sh kbd_stop: stop file, then TERM to mk20-kbd -> supervisor exits, no relaunch, KD left to gadget
        dev.p("tmp/mk20-kbd-stop").write_text("")
        subprocess.run(["pkill", "-TERM", "-f", f"{rel}/bin/mk20-kbd --run"])
        assert p.wait(10) == 0
    finally:
        if p.poll() is None:
            p.kill(); p.wait(5)
    assert "stop (stop file)" in mlog(dev) and svclog(dev) == ["stop"]
    assert not dev.p("tmp/mk20/kbd-degraded").exists() and not dev.p("tmp/mk20/kbd.crashes").exists()
    assert (dev.mock / "kbd.args").read_text().count("--run") == 1 and not dev.p("tmp/mk20/kbd-loop.pid").exists()


def test_supervisor_nohid(kb, rel):
    dev = kb
    p = subprocess.Popen([BB, "sh", str(rel / "sbin/mk20d"), "kbd"], env=dict(dev.env, MK20_NOHID="1"))
    try:
        assert wait(lambda: (dev.mock / "kbd.args").exists())
        a = (dev.mock / "kbd.args").read_text()
        assert "--no-hid" in a and "--hid " not in a
        dev.p("tmp/mk20-kbd-stop").write_text(""); subprocess.run(["pkill", "-f", f"{rel}/bin/mk20-kbd --run"])
        p.wait(10)
    finally:
        if p.poll() is None:
            p.kill(); p.wait(5)


def run_sup(dev, rel, until, env=None, t=20):
    p = subprocess.Popen([BB, "sh", str(rel / "sbin/mk20d"), "kbd"], env=dict(dev.env, **(env or {})))
    try:
        ok = wait(until, t)
        alive = p.poll() is None
        dev.p("tmp/mk20-kbd-stop").write_text(""); subprocess.run(["pkill", "-f", f"{rel}/bin/mk20-kbd --run"])
        p.wait(10)
        return ok, alive
    finally:
        if p.poll() is None:
            p.kill(); p.wait(5)


def nruns(dev):
    f = dev.mock / "kbd.args"
    return f.read_text().count("--run") if f.exists() else 0


def test_crash_loop_keeps_restarting_never_stock_kd(kb, rel):
    dev = kb
    (dev.mock / "kbd.crash").write_text("")
    dev.p("tmp/mk20/kbd.mode").write_text("")
    ok, alive = run_sup(dev, rel, lambda: nruns(dev) >= 6)
    assert ok and alive                                                   # supervisor never gives up
    assert svclog(dev) == ["stop"] and not (dev.mock / "pgrep.kd").exists()   # never qt_app2 start
    assert dev.p("tmp/mk20/kbd.mode").exists() and not dev.p("data/mk20/state/kbd-fallback").exists()
    c = dev.read("tmp/mk20/kbd.crashes")
    assert re.match(r"total=\d+ last_rc=7 last_up=\d+$", c) and int(c.split()[0][6:]) >= 5
    assert len(dev.read("tmp/mk20/kbd-crash.times").split()) >= 5
    assert "mk20-kbd exit rc=7" in dev.read("tmp/mk20/kbd.log") and "restart #1" in dev.read("tmp/mk20/kbd.log")
    assert "WARN mk20d kbd" in mlog(dev) and "fallback" not in mlog(dev)


def test_restart_backoff_grows(kb, rel):
    dev = kb
    (dev.mock / "kbd.crash").write_text("")
    times = []
    def until():
        n = nruns(dev)
        if not times or n > len(times):
            times.extend([time.time()] * (n - len(times)))
        return n >= 4
    ok, alive = run_sup(dev, rel, until, env={"MK20_KBD_BACKOFF_MAX": "4"}, t=20)
    assert ok and alive
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert gaps[0] < 1.8 and gaps[1] > 1.5 and gaps[2] > 3.5, gaps        # 1 s, 2 s, 4 s
    assert "backoff 1s" in mlog(dev)


def test_kd_that_will_not_stop_means_no_kbd_degraded(kb, rel):
    dev = kb
    (dev.mock / "kd.stubborn").write_text(""); dev.p("tmp/mk20/kbd.mode").write_text("")
    r = dev.run(rel / "sbin/mk20d", "kbd", timeout=30)
    assert r.returncode == 0 and not (dev.mock / "kbd.args").exists()    # never two writers on ttyS1 / fb
    assert svclog(dev) == ["stop", "stop", "stop"]                       # retried, never "start"
    assert "did not stop (3 tries" in dev.read("tmp/mk20/kbd-degraded") and not dev.p("tmp/mk20/kbd.mode").exists()
    assert "stop (kd stop failed)" in mlog(dev)
    s = dev.run(rel / "sbin/mk20d", "selftest")
    assert "WARN mk20-kbd not active: stock KD did not stop (3 tries" in s.stdout and "PASS kd=1" in s.stdout


def test_composite_down_ends_without_touching_kd(kb, rel):
    dev = kb
    os.rmdir(dev.p("sys/kernel/config/usb_gadget/g1/configs/c.1/ecm.usb0"))
    r = dev.run(rel / "sbin/mk20d", "kbd", timeout=20)
    assert r.returncode == 0 and svclog(dev) == [] and "stop (composite down/busy)" in mlog(dev)   # KD untouched


@pytest.mark.parametrize("why", ["stop-marker", "busy"])
def test_late_supervisor_after_teardown_does_nothing(kb, rel, why):
    """codex kdr-r1 #2: a supervisor starting after teardown never clears the markers and never touches KD."""
    dev = kb
    dev.p("tmp/mk20-kbd-stop" if why == "stop-marker" else "tmp/mk20/gadget.busy").write_text("")
    r = dev.run(rel / "sbin/mk20d", "kbd", timeout=20)
    assert r.returncode == 0 and svclog(dev) == [] and not (dev.mock / "kbd.args").exists()
    assert why != "stop-marker" or dev.p("tmp/mk20-kbd-stop").exists()


def test_kd_absent_still_stops_procd_service(kb, rel):
    """codex kdr-r1 #5: KD between respawns (no pid) -> qt_app2 stop is still issued before mk20-kbd starts."""
    dev = kb
    os.unlink(dev.mock / "pgrep.kd")
    ok, alive = run_sup(dev, rel, lambda: (dev.mock / "kbd.args").exists())
    assert ok and svclog(dev)[0] == "stop"


def test_kd_respawn_after_start_stops_kbd_launches(kb, rel):
    dev = kb
    (dev.mock / "kbd.crash").write_text("")
    def respawn():
        if (dev.mock / "kbd.args").exists():
            (dev.mock / "kd.stubborn").write_text(""); (dev.mock / "pgrep.kd").write_text("123\n"); return True
    p = subprocess.Popen([BB, "sh", str(rel / "sbin/mk20d"), "kbd"], env=dict(dev.env, MK20_KBD_BACKOFF_MAX="1"))
    try:
        assert wait(respawn); p.wait(15)
    finally:
        if p.poll() is None:
            p.kill(); p.wait(5)
    assert "stop (kd came back)" in mlog(dev) and "came back" in dev.read("tmp/mk20/kbd-degraded")
    assert nruns(dev) <= 2 and "start" not in svclog(dev)


# ---------------- selftest / health ----------------

def kbd_proc(rel):
    """A live process whose cmdline matches `bin/mk20-kbd --run` (what selftest's pgrep looks for)."""
    return subprocess.Popen([BB, "sh", "-c", "sleep 30; :", f"{rel}/bin/mk20-kbd", "--run"])


def status(dev, age=0, **kw):
    s = dict(GOOD, **kw); f = dev.p("run/mk20/kbd.status"); f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(s) + "\n")
    if age:
        t = time.time() - age; os.utime(f, (t, t))


@pytest.fixture
def kmode(kb, rel):
    """Selftest in kbd mode: composite with listeners is out of scope here; look only at the kd/kbd lines."""
    kb.p("tmp/mk20/kbd.mode").write_text(""); os.unlink(kb.mock / "pgrep.kd")
    p = kbd_proc(rel)
    yield kb
    p.kill(); p.wait()


def st(dev, rel):
    """(rc, kd/kbd lines); rc here = 1 iff a kd/kbd line FAILs (the fake composite has no bridge listeners)."""
    r = dev.run(rel / "sbin/mk20d", "selftest")
    ls = [l for l in r.stdout.splitlines() if re.search(r"\bkd=|kbd", l)]
    return subprocess.CompletedProcess(r.args, int(any(l.startswith("FAIL") for l in ls)), r.stdout, r.stderr), ls


def test_selftest_kbd_mode_pass(kmode, rel):
    status(kmode)
    r, ls = st(kmode, rel)
    assert ls == ["PASS kd=0 (mk20-kbd mode)", "PASS mk20-kbd running", ls[2], "PASS kbd gd32 knob_ok=6 frames=0",
                  "PASS kbd panels 21/21"] and ls[2].startswith("PASS kbd status fresh")
    status(kmode, knob_ok=0, knob_init="timeout", key_events=2)     # no knob echo, but GD32 frames seen -> link alive
    _, ls = st(kmode, rel)
    assert "PASS kbd gd32 knob_ok=0 frames=2" in ls and not [l for l in ls if l.startswith("FAIL")]


@pytest.mark.parametrize("bad,line", [
    ({"knob_ok": 3}, "FAIL kbd gd32 silent (knob_ok=3 frames=0)"),
    ({"panels_written": 20}, "FAIL kbd panels 20/21"),
    ({"age": 30}, "FAIL kbd status stale"),
    ({"missing": 1}, "FAIL kbd status missing"),
    ({"kd": 1}, "FAIL kd=1 beside mk20-kbd"),
])
def test_selftest_kbd_mode_fail(kmode, rel, bad, line):
    dev = kmode
    dev.p("data/mk20/state/pending").write_text("9.9.9\n")               # on probation: FAIL -> no confirm
    if "missing" not in bad:
        status(dev, **{k: v for k, v in bad.items() if k not in ("kd",)})
    if "kd" in bad:
        (dev.mock / "pgrep.kd").write_text("123\n")
    r, ls = st(dev, rel)
    assert r.returncode != 0 and any(l.startswith(line) for l in ls), ls
    if "kd" not in bad:                                                    # confirmed: WARN only, rc unaffected
        os.unlink(dev.p("data/mk20/state/pending"))
        r, ls = st(dev, rel)
        assert r.returncode == 0 and any(l.startswith("WARN" + line[4:]) for l in ls), ls
    else:                                                                  # KD beside mk20-kbd: always FAIL
        os.unlink(dev.p("data/mk20/state/pending"))
        r, ls = st(dev, rel)
        assert r.returncode != 0 and line in ls


def test_selftest_kbd_not_running_fails_on_probation(kb, rel):
    kb.p("tmp/mk20/kbd.mode").write_text(""); os.unlink(kb.mock / "pgrep.kd"); status(kb)
    kb.p("data/mk20/state/pending").write_text("9.9.9\n")
    r, ls = st(kb, rel)
    assert r.returncode != 0 and "FAIL mk20-kbd not running" in ls
    os.unlink(kb.p("data/mk20/state/pending"))
    r, ls = st(kb, rel)
    assert r.returncode == 0 and "WARN mk20-kbd not running" in ls


def uptime():
    return int(float(open("/proc/uptime").read().split()[0]))


def test_selftest_crash_loop_fails_probation_warns_after(kmode, rel):
    dev = kmode
    status(dev)
    now = uptime()
    dev.p("tmp/mk20/kbd-crash.times").write_text(f"{now - 400}\n{now - 20}\n{now - 10}\n")
    dev.p("tmp/mk20/kbd.crashes").write_text(f"total=3 last_rc=7 last_up={now - 10}\n")
    r, ls = st(dev, rel)
    assert r.returncode == 0 and "INFO mk20-kbd restarted 3 times" in ls      # 2 recent < 3: info only
    dev.p("tmp/mk20/kbd-crash.times").write_text(f"{now - 30}\n{now - 20}\n{now - 10}\n")
    dev.p("data/mk20/state/pending").write_text("9.9.9\n")
    r, ls = st(dev, rel)
    assert r.returncode != 0 and "FAIL mk20-kbd crash loop: 3 exits in 300s (3 total)" in ls
    os.unlink(dev.p("data/mk20/state/pending"))
    r, ls = st(dev, rel)
    assert r.returncode == 0 and "WARN mk20-kbd crash loop: 3 exits in 300s (3 total)" in ls
    h = json.loads(dev.run(rel / "sbin/mk20d", "health", env={"MK20_KBD": "1"}).stdout)
    assert h["kbd"]["restarts"] == 3 and h["kbd"]["recent_crashes"] == 3 and h["status"] != "ok"


def test_stock_selftest_unchanged_without_kbd(dev, rel, g1):
    r = dev.run(rel / "sbin/mk20d", "selftest")
    assert "PASS kd=1" in r.stdout and "kbd" not in r.stdout and r.returncode == 0


def test_health_kbd_section(kmode, rel):
    status(kmode)
    h = json.loads(kmode.run(rel / "sbin/mk20d", "health", env={"MK20_KBD": "1"}).stdout)
    k = h["kbd"]
    assert k["enabled"] is True and k["mode"] is True and k["running"] is True and k["degraded"] == "" and k["restarts"] == 0
    assert json.loads(k["status"])["panels_written"] == 21


# ---------------- boot ----------------

def genv(dev):
    return dict(l.split("=", 1) for l in dev.read("tmp/mk20/gadget.boot.env").splitlines())


def stop_sup(dev, rel):
    dev.p("tmp/mk20-kbd-stop").write_text("")
    subprocess.run(["pkill", "-f", f"{rel}/bin/mk20-kbd --run"])
    wait(lambda: not dev.p("tmp/mk20/kbd-loop.pid").exists(), 10)


def test_boot_kbd_mode_replaces_router_and_tap(kb, rel):
    dev = kb
    conf(dev, "FEATURE_KBD=1\n")
    try:
        r, log = run_boot(dev, rel)
        assert genv(dev)["MK20_KBD"] == "1"
        assert "INFO mk20d kbd" in log and "supervisor launched (nohid=0); router off" in log
        assert "router v=9.9.9 supervisor launched" not in log and "kbd=1)" in log   # router not started
        assert wait(lambda: (dev.mock / "kbd.args").exists())
        assert not subprocess.run(["pgrep", "-f", f"{rel}/sbin/mk20d router"]).returncode == 0
    finally:
        stop_sup(dev, rel)


@pytest.mark.parametrize("why", ["off", "use-kd", "fat", "nobin"])
def test_boot_kbd_gated(kb, rel, why):
    dev = kb
    if why != "off":
        conf(dev, "FEATURE_KBD=1\n")
    if why == "use-kd":
        dev.p("data/mk20/use-kd").write_text("")
    elif why == "fat":
        dev.p("mnt/SDCARD/mk20-use-kd").write_text("")
    elif why == "nobin":
        os.unlink(rel / "bin/mk20-kbd")
    try:
        r, log = run_boot(dev, rel)
        assert genv(dev)["MK20_KBD"] == "0" and "INFO mk20d kbd \"supervisor" not in log
        assert "router off" not in log and not dev.p("tmp/mk20/kbd.mode").exists()
        assert "router v=9.9.9 supervisor launched" in log                        # stock path: router as before
        assert ("enabled but not used" in log) == (why == "nobin")
        time.sleep(0.3); assert not (dev.mock / "kbd.args").exists()
    finally:
        subprocess.run(["pkill", "-f", f"{rel}/sbin/mk20d router"])


def test_boot_ignores_legacy_fallback_mark(kb, rel):
    dev = kb
    conf(dev, "FEATURE_KBD=1\n")
    dev.p("data/mk20/state/kbd-fallback").write_text("9.9.9\n")   # no fallback mark path any more
    try:
        r, log = run_boot(dev, rel)
        assert genv(dev)["MK20_KBD"] == "1"
    finally:
        stop_sup(dev, rel)


# ---------------- gadget.sh ----------------

G = (DECK / "firmware/release/share/gadget.sh").read_text()
FN = G[G.index("router_pids() {"):G.index("# KD stop:")]


def test_gadget_kbd_stop_on_every_path_and_before_udc():
    for start, udc in (("cleanup() {", "$G/UDC"), ("setup_main() {", 'echo "" > $G/UDC')):
        body = G[G.index(start):]
        rs = body.index("router_stop"); ks = body.index("kbd_stop")
        assert rs < ks < body.index(udc)
    cl = G[G.index("cleanup() {"):]
    assert cl.index("kbd_stop") < cl.index("kd_start")
    assert "kbd_hid_quiesce\n  return 0\n}" in G[G.index("router_stop() {"):]


def test_gadget_tap_omitted_in_kbd_mode(tmp_path):
    line = [l for l in G.splitlines() if "--tap" in l and "BRIDGE_LISTEN=" in l]
    assert len(line) == 1
    for rel_, kbd, want in (("x", "0", True), ("x", "1", False), ("", "0", False)):
        r = subprocess.run([BB, "sh", "-c", f'BRIDGE_LISTEN=L; {line[0]}; echo "$BRIDGE_LISTEN"'],
                           env={"MK20_REL": rel_, "MK20_KBD": kbd, "PATH": os.environ["PATH"]}, capture_output=True, text=True)
        assert ("--tap" in r.stdout) == want


HOLDER = r'''import os, signal, sys, time
# fake mk20-kbd: holds the "hidg0" file open; mode: close (SIGUSR1 closes fd), ignore (USR1 ignored), stubborn (+TERM)
hid, mode, ready = sys.argv[2], sys.argv[3], sys.argv[4]
fd = [os.open(hid, os.O_WRONLY)]
if mode == "deleted":   # parked stale fd: "/dev/hidg0 (deleted)"; a new node appears at the path
    os.unlink(hid); open(hid, "w").close()
def usr1(*_):
    if mode == "close" and fd[0] >= 0:
        os.close(fd[0]); fd[0] = -1
signal.signal(signal.SIGUSR1, usr1)
if mode == "stubborn":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
open(ready, "w").close()
while True:
    time.sleep(0.05)
'''


@pytest.fixture
def gh(tmp_path):
    (tmp_path / "mk20").mkdir(); (tmp_path / "bin").mkdir()
    body = scope_gadget_fn(FN, tmp_path)
    (tmp_path / "bin/mk20-kbd").write_text(HOLDER)
    hid = tmp_path / "hidg0"; hid.write_bytes(b"")
    procs = []

    def start(mode):
        ready = tmp_path / f"ready-{len(procs)}"
        p = subprocess.Popen([sys.executable, str(tmp_path / "bin/mk20-kbd"), "--run", str(hid), mode, str(ready)])
        procs.append(p); assert wait(ready.exists, 10); return p

    def run(fn, **env):
        s = tmp_path / "g.sh"
        s.write_text(f'log() {{ echo "$*" >> {tmp_path}/log; }}\n{body}\n{fn}\necho done\n')
        t0 = time.time()
        r = subprocess.run([BB, "sh", str(s)], capture_output=True, text=True, timeout=30,
                           env=dict(os.environ, MK20_HIDG=str(hid), **env))
        return r, time.time() - t0

    yield tmp_path, start, run, hid
    for p in procs:
        if p.poll() is None:
            p.kill(); p.wait()


def holds(p, hid):
    try:
        return any(os.readlink(f"/proc/{p.pid}/fd/{f}") == str(hid) for f in os.listdir(f"/proc/{p.pid}/fd"))
    except OSError:
        return False


def test_router_stop_quiesces_kbd_hid_via_sigusr1(gh):
    t, start, run, hid = gh
    p = start("close"); assert holds(p, hid)
    r, dt = run("router_stop")
    assert r.stdout.strip() == "done" and dt < 3
    assert p.poll() is None and not holds(p, hid)          # still alive (panels/host keep running), hidg0 closed
    assert (t / "stop").exists() and not (t / "log").exists()


def test_router_stop_fails_closed_term_then_kill(gh):
    t, start, run, hid = gh
    p = start("ignore")
    r, dt = run("router_stop", MK20_KBD_HID_TICKS="3")
    assert r.stdout.strip() == "done" and p.wait(2) == -signal.SIGTERM and "SIGTERM" in (t / "log").read_text()
    p2 = start("stubborn")
    os.unlink(t / "log")
    r, dt = run("router_stop", MK20_KBD_HID_TICKS="3")
    assert r.stdout.strip() == "done" and dt < 6 and p2.wait(2) == -signal.SIGKILL
    assert "SIGKILL" in (t / "log").read_text()


def test_router_stop_sees_parked_deleted_hidg0_fd(gh):
    t, start, run, hid = gh
    p = start("deleted")
    assert any(os.readlink(f"/proc/{p.pid}/fd/{f}") == f"{hid} (deleted)" for f in os.listdir(f"/proc/{p.pid}/fd"))
    r, dt = run("router_stop", MK20_KBD_HID_TICKS="3")
    assert r.stdout.strip() == "done" and p.wait(3) == -signal.SIGTERM   # still held after USR1 -> fail closed


def test_router_stop_ignores_kbd_without_hid_and_foreign_pids(gh):
    t, start, run, hid = gh
    p = start("close"); subprocess.run(["kill", "-USR1", str(p.pid)]); assert wait(lambda: not holds(p, hid), 5)
    other = subprocess.Popen(["sleep", "30"])
    try:
        r, dt = run("router_stop")
        assert r.stdout.strip() == "done" and dt < 2 and p.poll() is None and other.poll() is None
    finally:
        other.kill(); other.wait()


def test_kbd_stop_kills_validated_supervisor_and_kbd(gh):
    t, start, run, hid = gh
    p = start("stubborn")
    sup_s = t / "mk20d"; sup_s.write_text("while :; do sleep 0.1; done\n")
    sup = subprocess.Popen([BB, "sh", str(sup_s), "kbd"])          # cmdline "... mk20d kbd"
    (t / "mk20/kbd-loop.pid").write_text(f"{sup.pid}\n"); (t / "mk20/kbd.mode").write_text("")
    time.sleep(0.2)
    r, dt = run("kbd_stop", MK20_KBD_TERM_TICKS="3")
    assert r.stdout.strip() == "done" and dt < 4
    assert sup.wait(2) == -signal.SIGTERM and p.wait(2) == -signal.SIGKILL
    assert (t / "kstop").exists() and not (t / "mk20/kbd.mode").exists()
    other = subprocess.Popen(["sleep", "30"]); (t / "mk20/kbd-loop.pid").write_text(f"{other.pid}\n")
    try:
        run("kbd_stop"); assert other.poll() is None                  # stale pid file -> foreign pid untouched
    finally:
        other.kill(); other.wait()


def test_selftest_warns_hid_lost_after_restart(kmode, rel):
    """A restart after the USB re-bind closes the parked hidg0 fd (f_hid cdev quirk): WARN only, reboot clears it."""
    dev = kmode
    status(dev, hid_open=0)
    _, ls = st(dev, rel)
    assert not [l for l in ls if "hidg0" in l]                              # no restart yet: in-process recovery
    dev.p("tmp/mk20/kbd.crashes").write_text("total=1 last_rc=139 last_up=1\n")
    dev.p("data/mk20/state/pending").write_text("9.9.9\n")
    r, ls = st(dev, rel)
    assert "WARN hidg0 not open after mk20-kbd restart (f_hid re-bind quirk: reboot clears it)" in ls and r.returncode == 0
    status(dev, hid_open=1)
    _, ls = st(dev, rel)
    assert not [l for l in ls if "hidg0" in l]


def test_mk20_kbd_has_no_hid_exit_path():
    """HID recovery is in-process: the run loop leaves only on a signal, the teardown exit file, poll() or GD32 read failure."""
    src = (DECK / "kbd/mk20_kbd.c").read_text()
    loop = src[src.index("while (!g_stop)"):src.index("if (k.hid) { hid_release_all")]
    assert loop.count("break;") == 3 and "perror(\"poll\")" in loop and "perror(\"gd32 read\")" in loop and "present: exiting" in loop
    assert "exit(" not in loop and "return" not in loop.replace("return;", "")


def test_gadget_fails_closed_when_kbd_survives(gh):
    """codex kdr-r1 #1: a surviving holder/mk20-kbd makes kbd_stop / kbd_hid_quiesce return 1, and cleanup() /
    setup_main refuse the UDC/configfs change and the KD start."""
    t, start, run, hid = gh
    cl = G[G.index("cleanup() {"):]
    assert 'kbd_stop || { log "FAILSAFE ABORT' in cl and cl.index("kbd_stop ||") < cl.index("$G/UDC") < cl.index("kd_start")
    sm = G[G.index("setup_main() {"):]
    assert 'kbd_stop || die "mk20-kbd not stopped' in sm
    # simulate a survivor: pgrep keeps reporting a validated mk20-kbd pid that cannot be killed (our own shell's
    # child running `sleep` under the matching cmdline is killable, so fake kill instead)
    p = start("stubborn")
    s = t / "ks.sh"
    s.write_text(f'log() {{ echo "$*" >> {t}/log; }}\nkill() {{ :; }}\n'
                 + scope_gadget_fn(FN, t)
                 + '\nkbd_stop; echo "rc=$?"\nkbd_hid_quiesce; echo "rq=$?"\n')
    r = subprocess.run([BB, "sh", str(s)], capture_output=True, text=True, timeout=30,
                       env=dict(os.environ, MK20_HIDG=str(hid), MK20_KBD_TERM_TICKS="2", MK20_KBD_HID_TICKS="2"))
    assert "rc=1" in r.stdout and "rq=1" in r.stdout and "FAIL" in (t / "log").read_text()
    assert p.poll() is None



def test_failed_service_stop_means_no_kbd(kb, rel):
    """codex kdr-r2 #2: qt_app2 stop failing (procd still armed) -> retried, mk20-kbd not launched, degraded."""
    dev = kb
    (dev.mock / "svc.fail").write_text("")
    r = dev.run(rel / "sbin/mk20d", "kbd", timeout=30)
    assert r.returncode == 0 and not (dev.mock / "kbd.args").exists() and svclog(dev) == ["stop", "stop", "stop"]
    assert "service stop ok=0" in dev.read("tmp/mk20/kbd-degraded")


def test_default_lock_is_the_supervised_lock():
    """codex kdr-r2 #3: a manual `mk20-kbd --run` and the supervised one contend for the same lock."""
    src = (DECK / "kbd/mk20_kbd.c").read_text(); m = (DECK / "firmware/release/sbin/mk20d").read_text()
    assert '"/tmp/mk20-kbd.lock"' in src and '--lock "$R/tmp/mk20-kbd.lock"' in m


def test_kbd_alive_thread_group(tmp_path):
    """codex kdr-r2 #1: a zombie leader counts as gone only when no thread is left; a live process is alive."""
    fn = FN[FN.index("kbd_st() {"):FN.index("kbd_hid_holders() {")]
    s = tmp_path / "a.sh"; s.write_text(fn + '\nfor p in "$@"; do kbd_alive "$p" && echo "$p alive" || echo "$p gone"; done\n')
    live = subprocess.Popen([sys.executable, "-c", "import threading,time; threading.Thread(target=time.sleep, args=(30,)).start(); time.sleep(30)"])
    z = subprocess.Popen(["sleep", "30"]); z.kill(); time.sleep(0.3)        # killed, not reaped: zombie, 1 task
    try:
        out = subprocess.run([BB, "sh", str(s), str(live.pid), str(z.pid), "999999"], capture_output=True, text=True).stdout
        assert f"{live.pid} alive" in out and f"{z.pid} gone" in out and "999999 gone" in out
    finally:
        live.kill(); live.wait(); z.wait()



def test_survivor_remembered_across_calls(gh):
    """codex kdr-r3 #1: a pid signalled by kbd_hid_quiesce (or an earlier kbd_stop) is remembered in kbd-pids.rec, so a
    later kbd_stop fails while that group lives even when cmdline discovery no longer finds it; a pid reused with
    another start time is ignored; success clears the record."""
    t, start, run, hid = gh
    p = start("close")
    st = open(f"/proc/{p.pid}/stat").read().rsplit(") ", 1)[1].split()[19]
    (t / "mk20").mkdir(exist_ok=True)
    (t / "mk20/kbd-pids.rec").write_text(f"{p.pid}:{st}\n")
    # discovery blind (pgrep finds nothing), recorded survivor alive -> fail closed
    s = t / "k2.sh"
    s.write_text(f'log() {{ echo "$*" >> {t}/log; }}\npgrep() {{ return 1; }}\n'
                 + scope_gadget_fn(FN, t)
                 + '\nkbd_stop; echo "rc=$?"\n')
    r = subprocess.run([BB, "sh", str(s)], capture_output=True, text=True, timeout=30, env=dict(os.environ, MK20_KBD_TERM_TICKS="2"))
    assert "rc=1" in r.stdout and (t / "mk20/kbd-pids.rec").exists()
    (t / "mk20/kbd-pids.rec").write_text(f"{p.pid}:1\n")                  # same pid, other start time: not ours
    r = subprocess.run([BB, "sh", str(s)], capture_output=True, text=True, timeout=30, env=dict(os.environ, MK20_KBD_TERM_TICKS="2"))
    assert "rc=0" in r.stdout and not (t / "mk20/kbd-pids.rec").exists()


def test_harness_scoped_to_own_fakes(gh, tmp_path_factory):
    """Cross-test isolation: the cut-out kbd_stop only sees mk20-kbd started from this test's tmp dir, never a
    parallel test's fake (another `.../bin/mk20-kbd --run` on the same box) and never its pid record."""
    t, start, run, hid = gh
    o = tmp_path_factory.mktemp("other"); (o / "bin").mkdir()
    (o / "bin/mk20-kbd").write_text(HOLDER); ohid = o / "hidg0"; ohid.write_bytes(b""); ready = o / "ready"
    other = subprocess.Popen([sys.executable, str(o / "bin/mk20-kbd"), "--run", str(ohid), "stubborn", str(ready)])
    try:
        assert wait(ready.exists, 10)
        p = start("stubborn")
        r, dt = run("kbd_stop", MK20_KBD_TERM_TICKS="2")
        assert r.stdout.strip() == "done" and p.wait(3) == -signal.SIGKILL and other.poll() is None
        assert not (Path("/tmp/mk20/kbd-pids.rec").exists() and str(p.pid) in Path("/tmp/mk20/kbd-pids.rec").read_text())
    finally:
        other.kill(); other.wait()
