"""gadget.sh --rollback-now (1.1.4 r5): transition mutex (one cleanup for concurrent callers), verified exit,
owner-local bounded supervisor (kills/reaps only its own cleanup tree on timeout).
The guard block is cut out of gadget.sh and run under busybox ash with stubbed cleanup/sysfs."""
import re
import subprocess
import time
from pathlib import Path

import pytest

G = (Path(__file__).resolve().parents[1] / "firmware/release/share/gadget.sh").read_text()


def _between(start, end):
    """Explicit start/end markers (r5 MINOR: a non-greedy regex stopped at an inner `fi`)."""
    a = G.index(start)
    b = G.index(end, a)
    return G[a:b]


BLOCK = _between("# >>> rollback-block", "# <<< rollback-block")


def harness(tmp_path, cleanup_makes_stock=True, cleanup_s=1, hang=False, unkillable=False):
    t = tmp_path
    (t / "c").mkdir(exist_ok=True)
    stub = "kill_tree() { :; }\n" if unkillable else ""
    body = BLOCK.replace("if [ \"${1:-}\" = --rollback-now ]; then", stub + "if [ \"${1:-}\" = --rollback-now ]; then", 1).replace("/tmp/mk20", str(t / "mk20")).replace("/dev/ttyGS0", "/dev/null") \
        .replace("/mnt/SDCARD/gadget-ecm-hid.status", str(t / "status")) \
        .replace("/mnt/SDCARD/mk20-no-composite", str(t / "flag")).replace("/mnt/SDCARD/cmd-reboot", str(t / "reboot"))
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
        if _re.search(r"[^&]&\s*(#.*)?$", code.rstrip()) and "kill" not in code and "cleanup )" not in code \
                and "setup_main )" not in code:   # owner-local supervised children keep fd 7 on purpose
            assert "7>&-" in code, line


def test_block_markers_cover_whole_rollback_if():
    assert BLOCK.count("\nif [ \"${1:-}\" = --rollback-now ]; then\n") == 1 and BLOCK.rstrip().endswith("fi")


def test_hung_cleanup_clears_busy_marker(dev, tmp_path):
    s = harness(tmp_path, cleanup_makes_stock=False, cleanup_s=0, hang=True)
    (tmp_path / "mk20").mkdir(exist_ok=True)
    (tmp_path / "mk20/gadget.busy").write_text("")
    env = dict(dev.env, MK20_RB_TIMEOUT="1")
    assert subprocess.run(["sh", str(s), "--rollback-now"], env=env, timeout=60).returncode == 1
    assert not (tmp_path / "mk20/gadget.busy").exists()


def test_busy_marker_cleared_on_success(dev, tmp_path):
    s = harness(tmp_path, cleanup_s=0)
    (tmp_path / "mk20").mkdir(exist_ok=True)
    (tmp_path / "mk20/gadget.busy").write_text("")
    assert run(dev, s) == 0 and not (tmp_path / "mk20/gadget.busy").exists()


@pytest.mark.parametrize("bad", ["abc", "-5", "1e9", "99999999999999999999999", "0x10", "", "08"])
def test_invalid_timeout_and_tries_are_validated(dev, tmp_path, bad):
    s = harness(tmp_path, cleanup_s=0)
    env = dict(dev.env, MK20_RB_TIMEOUT=bad, MK20_RB_LOCK_TRIES=bad, MK20_SETUP_LOCK_TRIES=bad,
               MK20_SETUP_TIMEOUT=bad)
    r = subprocess.run(["sh", str(s), "--rollback-now"], env=env, timeout=60, capture_output=True, text=True)
    assert r.returncode == 0 and cleanups(tmp_path) == 1, r.stderr
    assert "arithmetic" not in r.stderr and "fell-through" not in r.stdout


@pytest.mark.parametrize("val,exp", [("abc", "240"), ("0", "1"), ("08", "8"), ("000", "1"), ("5", "5"),
                                     ("901", "900"), ("12345678901234567890", "900")])
def test_uint_cap(dev, tmp_path, val, exp):
    f = G[G.index("uint_cap() {"):]
    f = f[:f.index("\n}\n") + 3]
    r = subprocess.run(["sh", "-c", f + f'uint_cap "{val}" 240 1 900'], env=dev.env, capture_output=True, text=True)
    assert r.stdout.strip() == exp, r.stderr


def test_unkillable_cleanup_escalates_rc3_no_unbounded_wait(dev, tmp_path):
    s = harness(tmp_path, cleanup_makes_stock=False, cleanup_s=0, hang=True, unkillable=True)
    env = dict(dev.env, MK20_RB_TIMEOUT="1")
    t0 = time.time()
    rc = subprocess.run(["sh", str(s), "--rollback-now"], env=env, timeout=60).returncode
    try:
        assert rc == 3 and time.time() - t0 < 30
        assert (tmp_path / "flag").exists() and (tmp_path / "reboot").exists()
        assert (tmp_path / "status").read_text().strip() == "rollback-STUCK"
    finally:
        subprocess.run(["kill", "-9", (tmp_path / "sleeper").read_text().strip()])


def test_confirm_waiter_retries_then_escalates():
    w = [ln for ln in G.splitlines() if "confirm" not in ln and "mk20-composite-ok ] && exit 0" in ln][0]
    assert "rc = 3 ] && exit 3" in w and "sleep 20" in w and "mk20-no-composite" in w and "cmd-reboot" in w
