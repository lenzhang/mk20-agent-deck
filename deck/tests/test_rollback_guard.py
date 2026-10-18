"""gadget.sh --rollback-now guard (1.1.4): one cleanup for concurrent callers, verified exit, stale-owner takeover.
The guard block is cut out of gadget.sh and run under busybox ash with stubbed cleanup/sysfs."""
import re
import subprocess
import time
from pathlib import Path

G = (Path(__file__).resolve().parents[1] / "firmware/release/share/gadget.sh").read_text()
BLOCK = re.search(r"(RB=/tmp/mk20/rollback\.once; RBL=.*?\nif \[ \"\$\{1:-\}\" = --rollback-now \]; then\n.*?\nfi\n)", G, re.S).group(1)


def harness(tmp_path, cleanup_makes_stock=True, cleanup_s=1):
    t = tmp_path
    (t / "c").mkdir(exist_ok=True)
    body = BLOCK.replace("/tmp/mk20", str(t / "mk20")).replace("/dev/ttyGS0", "/dev/null") \
        .replace("/mnt/SDCARD/gadget-ecm-hid.status", str(t / "status"))
    mk = "touch $T/c/acm.usb0" if cleanup_makes_stock else ":"
    script = f'''T={t}; C=$T/c
log() {{ echo "$*" >> $T/log; }}
udc_now() {{ echo udc; }}
pgrep() {{ return 0; }}
cleanup() {{ echo "$$" >> $T/cleanups; sleep {cleanup_s}; {mk}; }}
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
    assert not (tmp_path / "mk20/rollback.once").exists()


def test_dead_owner_reclaimed(dev, tmp_path):
    s = harness(tmp_path, cleanup_s=0)
    rb = tmp_path / "mk20/rollback.once"
    rb.mkdir(parents=True)
    (rb / "owner").write_text("999999 12345 999999\n")   # no such pid
    assert run(dev, s) == 0 and cleanups(tmp_path) == 1


def test_reused_pid_reclaimed(dev, tmp_path):
    s = harness(tmp_path, cleanup_s=0)
    rb = tmp_path / "mk20/rollback.once"
    rb.mkdir(parents=True)
    me = subprocess.Popen(["sleep", "30"])
    try:
        (rb / "owner").write_text(f"{me.pid} 1 999999\n")   # live pid, wrong start time
        assert run(dev, s) == 0 and cleanups(tmp_path) == 1
    finally:
        me.kill()


def test_live_owner_past_deadline_killed(dev, tmp_path):
    s = harness(tmp_path, cleanup_s=0)
    rb = tmp_path / "mk20/rollback.once"
    rb.mkdir(parents=True)
    hung = subprocess.Popen(["sleep", "60"])
    try:
        st = Path(f"/proc/{hung.pid}/stat").read_text().split()[21]
        (rb / "owner").write_text(f"{hung.pid} {st} 0\n")   # deadline long past
        assert run(dev, s) == 0 and cleanups(tmp_path) == 1
        time.sleep(0.3)
        assert hung.poll() is not None
    finally:
        hung.kill()
