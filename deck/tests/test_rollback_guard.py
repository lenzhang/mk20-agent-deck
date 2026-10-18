"""gadget.sh --rollback-now (1.1.4 r5): transition mutex (one cleanup for concurrent callers), verified exit,
owner-local bounded supervisor (kills/reaps only its own cleanup tree on timeout).
The guard block is cut out of gadget.sh and run under busybox ash with stubbed cleanup/sysfs."""
import re
import subprocess
import time
from pathlib import Path

G = (Path(__file__).resolve().parents[1] / "firmware/release/share/gadget.sh").read_text()
BLOCK = re.search(r"(TL=/tmp/mk20/transition\.lock\n.*?\nif \[ \"\$\{1:-\}\" = --rollback-now \]; then\n.*?\nfi\n)", G, re.S).group(1)


def harness(tmp_path, cleanup_makes_stock=True, cleanup_s=1, hang=False):
    t = tmp_path
    (t / "c").mkdir(exist_ok=True)
    body = BLOCK.replace("/tmp/mk20", str(t / "mk20")).replace("/dev/ttyGS0", "/dev/null") \
        .replace("/mnt/SDCARD/gadget-ecm-hid.status", str(t / "status"))
    mk = "touch $T/c/acm.usb0" if cleanup_makes_stock else ":"
    hangcmd = "sleep 60 & echo $! > $T/sleeper; wait;" if hang else ""
    script = f'''T={t}; C=$T/c
log() {{ echo "$*" >> $T/log; }}
udc_now() {{ echo udc; }}
pgrep() {{ return 0; }}
cleanup() {{ echo "$$" >> $T/cleanups; {hangcmd} sleep {cleanup_s}; {mk}; }}
{body}
echo "fell-through"; exit 9
'''
    (t / "g.sh").write_text(script)
    return t / "g.sh"


def run(dev, script, wait=True):
    p = subprocess.Popen(["sh", str(script), "--rollback-now"], env=dev.env, stdout=subprocess.PIPE, text=True)
    return p.wait(timeout=60) if wait else p


def cleanups(t):
    f = t / "cleanups"
    return len(f.read_text().split()) if f.exists() else 0


def test_concurrent_callers_one_cleanup(dev, tmp_path):
    s = harness(tmp_path, cleanup_s=2)
    ps = [run(dev, s, wait=False) for _ in range(3)]
    rcs = [p.wait(timeout=60) for p in ps]
    assert rcs == [0, 0, 0] and cleanups(tmp_path) == 1


def test_done_and_stock_ok_is_noop(dev, tmp_path):
    s = harness(tmp_path, cleanup_s=0)
    assert run(dev, s) == 0 and run(dev, s) == 0 and cleanups(tmp_path) == 1
    assert "already done" in (tmp_path / "log").read_text()
    assert (tmp_path / "status").read_text().strip() == "rollback-verified"


def test_composite_links_present_is_not_stock(dev, tmp_path):
    s = harness(tmp_path, cleanup_s=0)
    (tmp_path / "c").mkdir(exist_ok=True)
    (tmp_path / "c/acm.usb0").write_text(""); (tmp_path / "c/ecm.usb0").write_text("")   # cleanup doesn't remove ecm
    assert run(dev, s) == 1 and cleanups(tmp_path) == 1
    assert (tmp_path / "status").read_text().strip() == "rollback-NOT-verified"


def test_unverified_releases_guard_and_retries(dev, tmp_path):
    s = harness(tmp_path, cleanup_makes_stock=False, cleanup_s=0)
    assert run(dev, s) == 1 and run(dev, s) == 1 and cleanups(tmp_path) == 2


def _gone(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().split()[2] == "Z"
    except FileNotFoundError:
        return True


def test_hung_cleanup_own_tree_killed_and_mutex_released(dev, tmp_path):
    s = harness(tmp_path, cleanup_makes_stock=False, cleanup_s=0, hang=True)
    env = dict(dev.env, MK20_RB_TIMEOUT="1")
    t0 = time.time()
    rc = subprocess.run(["sh", str(s), "--rollback-now"], env=env, timeout=60).returncode
    assert rc == 1 and time.time() - t0 < 45
    assert "killing own child tree" in (tmp_path / "log").read_text()
    sl = int((tmp_path / "sleeper").read_text())
    time.sleep(0.3)
    assert _gone(sl)
    # mutex released: the next caller gets it at once
    (tmp_path / "sleeper").unlink()
    env2 = dict(dev.env, MK20_RB_TIMEOUT="1", MK20_RB_LOCK_TRIES="2")
    assert subprocess.run(["sh", str(s), "--rollback-now"], env=env2, timeout=60).returncode == 1
    assert cleanups(tmp_path) == 2


def test_busy_mutex_refused_never_kills_holder(dev, tmp_path):
    s = harness(tmp_path, cleanup_s=0)
    (tmp_path / "mk20").mkdir(exist_ok=True)
    holder = subprocess.Popen(["sh", "-c", f"exec 7>{tmp_path}/mk20/transition.lock; flock -x 7; sleep 30"], env=dev.env)
    try:
        time.sleep(0.5)
        env = dict(dev.env, MK20_RB_LOCK_TRIES="4")
        assert subprocess.run(["sh", str(s), "--rollback-now"], env=env, timeout=60).returncode == 1
        assert cleanups(tmp_path) == 0 and holder.poll() is None
        assert "mutex not acquired" in (tmp_path / "log").read_text()
    finally:
        holder.kill()


def test_spawned_daemons_close_mutex_fd():
    """Every background spawn in gadget.sh closes fd 7 so the mutex dies with its owner."""
    import re as _re
    for line in G.splitlines():
        code = line.split(" #")[0]
        if _re.search(r"[^&]&\s*(#.*)?$", code.rstrip()) and "kill" not in code and "cleanup )" not in code:
            assert "7>&-" in code, line
