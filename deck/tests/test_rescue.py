import os, subprocess, time, shutil, hashlib, pathlib, signal
import pytest
from conftest import FW, DECK, sha

STUB = FW / "rescue/lunch.sh"
HR = FW / "rescue/hardreset.sh"
W = FW / "rescue/watcher.sh"


def put_rescue(dev, home, body, good_sha=True):
    h = dev.p(home); h.mkdir(parents=True, exist_ok=True)
    (h / "watcher.sh").write_text(body)
    s = sha(h / "watcher.sh") if good_sha else "0" * 64
    (h / "watcher.sh.sha256").write_text(f"{s}  watcher.sh\n")


def marker_body(dev, tag):
    return f'echo "{tag} $1" > {dev.mock}/watcher.ran\n'


def wait_text(p, t=5):
    end = time.time() + t
    while time.time() < end:
        if p.exists() and p.read_text().strip():
            return p.read_text().strip()
        time.sleep(0.05)
    return None


def test_stub_prefers_ext4_rescue(dev):
    put_rescue(dev, "data/mk20/rescue", marker_body(dev, "ext4"))
    put_rescue(dev, "mnt/SDCARD/mk20", marker_body(dev, "fat"))
    t0 = time.time(); assert dev.run(STUB).returncode == 0; assert time.time() - t0 < 2
    assert wait_text(dev.mock / "watcher.ran").startswith("ext4 ")
    assert "verified" in dev.read("tmp/mk20/stub.status")


def test_stub_falls_back_to_fat_when_ext4_corrupt(dev):
    put_rescue(dev, "data/mk20/rescue", marker_body(dev, "ext4"), good_sha=False)
    put_rescue(dev, "mnt/SDCARD/mk20", marker_body(dev, "fat"))
    dev.run(STUB)
    assert wait_text(dev.mock / "watcher.ran").startswith("fat ")


def test_stub_unverified_fallback_keeps_recovery(dev):
    put_rescue(dev, "mnt/SDCARD/mk20", marker_body(dev, "fat"), good_sha=False)
    dev.run(STUB)
    assert wait_text(dev.mock / "watcher.ran").startswith("fat ")
    assert "UNVERIFIED" in dev.read("tmp/mk20/stub.status")


def test_stub_kill_switch(dev):
    put_rescue(dev, "mnt/SDCARD/mk20", marker_body(dev, "fat"))
    dev.p("mnt/SDCARD/mk20-no-watch").touch()
    dev.run(STUB); time.sleep(0.5)
    assert not (dev.mock / "watcher.ran").exists()


def test_hardreset_refuses_without_watchdog(dev):
    (dev.mock / "wdt").write_text("offline")
    r = dev.run(HR, "test", env={"MK20_HR_NOSLEEP": "1"})
    assert r.returncode == 10
    assert "stop" not in (dev.mock / "ubus.calls").read_text()
    assert "REFUSED" in dev.read("mnt/SDCARD/mk20/logs/hardreset.log")


def test_hardreset_stops_pings_and_remounts(dev):
    (dev.mock / "wdt").write_text("running")
    r = dev.run(HR, "test", env={"MK20_HR_NOSLEEP": "1"})
    assert r.returncode == 0, dev.read("mnt/SDCARD/mk20/logs/hardreset.log")
    calls = (dev.mock / "ubus.calls").read_text()
    assert '{"stop":true}' in calls
    assert "remount,ro" in (dev.mock / "mount.calls").read_text()


def test_hardreset_waits_for_kernel_write_lock(dev):
    (dev.mock / "wdt").write_text("running")
    lock = dev.p("tmp/mk20/kwrite.lock")
    holder = subprocess.Popen(["/usr/bin/flock", str(lock), "sleep", "2.5"])
    time.sleep(0.3)
    t0 = time.time()
    r = dev.run(HR, "test", env={"MK20_HR_NOSLEEP": "1"})
    holder.wait()
    assert r.returncode == 0
    assert time.time() - t0 >= 2.0          # waited for the dd holder
    assert "waiting for kernel-write lock" in dev.read("mnt/SDCARD/mk20/logs/hardreset.log")


def test_no_script_uses_flock_w():
    for f in list((FW / "rescue").glob("*.sh")) + list((DECK / "m2-scripts/remote").rglob("*.sh")):
        for line in f.read_text().splitlines():
            code = line.split("#", 1)[0]
            assert "flock -w" not in code, f"{f}: {line}"


def test_watcher_kill_tree(dev, tmp_path):
    # job that spawns a grandchild; kill_tree must take down the whole tree
    script = tmp_path / "t.sh"
    script.write_text(f'''
MK20_WATCHER_LIB=1 . {W}
( sh -c 'sleep 30 & echo $! > {tmp_path}/gc; wait' ) & jp=$!
sleep 0.5
kill_tree $jp
sleep 0.3
kill -0 $(cat {tmp_path}/gc) 2>/dev/null && echo ALIVE || echo DEAD
''')
    r = dev.run(script)
    assert "DEAD" in r.stdout, r.stdout + r.stderr


def test_watcher_rescue_copy_verifies(dev, tmp_path):
    h = dev.p("data/mk20/rescue"); h.mkdir(parents=True)
    (h / "loader.sh").write_text("echo hi\n")
    (h / "loader.sh.sha256").write_text(f"{sha(h/'loader.sh')}  loader.sh\n")
    script = tmp_path / "t.sh"
    script.write_text(f'''
set -- {h}
MK20_WATCHER_LIB=1 . {W}
rescue_copy loader.sh && echo OK1
echo tamper >> {h}/loader.sh
rescue_copy loader.sh || echo REFUSED
''')
    r = dev.run(script)
    assert "OK1" in r.stdout and "REFUSED" in r.stdout, r.stdout + r.stderr


def test_build_reproducible_and_manifest(tmp_path):
    outs = []
    for i in (1, 2):
        o = tmp_path / f"o{i}"
        r = subprocess.run(["sh", str(DECK / "tools/mk20-release-build.sh"), str(FW), str(o), "t"], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        outs.append(o)
    v = (FW / "VERSION").read_text().strip()
    a, b = (o / f"mk20-fw-{v}.tar" for o in outs)
    assert a.read_bytes() == b.read_bytes()
    m = (outs[0] / f"mk20-fw-{v}/MANIFEST").read_text().split("--\n", 1)[1]
    for line in m.strip().splitlines():
        s, mode, p = line.split()
        assert hashlib.sha256((outs[0] / f"mk20-fw-{v}" / p).read_bytes()).hexdigest() == s
    for f in ("watcher.sh", "hardreset.sh", "loader.sh", "lunch.sh"):
        assert (outs[0] / "rescue" / f"{f}.sha256").read_text().split()[0] == sha(outs[0] / "rescue" / f)


SH_FILES = sorted(set(list((FW).rglob("*.sh")) + [FW / "release/sbin/mk20d", DECK / "tools/mk20-release-build.sh"]))


@pytest.mark.parametrize("f", SH_FILES, ids=lambda p: str(p.relative_to(DECK)))
def test_shellcheck(f):
    r = subprocess.run(["shellcheck", "-s", "sh", "-S", "warning", str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


def test_hardreset_refuses_in_test_root_without_ubus_override(dev):
    (dev.mock / "wdt").write_text("running")
    env = dict(dev.env); env.pop("MK20_UBUS")
    r = subprocess.run(["busybox", "sh", str(HR), "t"], env={**env, "MK20_HR_NOSLEEP": "1"}, capture_output=True, text=True)
    assert r.returncode == 13


def test_hardreset_refuses_magicclose(dev):
    (dev.mock / "wdt").write_text("running")
    shim = dev.tmp / "ubus2"; shim.write_text('#!/bin/sh\ncase "$*" in "call system watchdog") echo \'{"status":"running","timeout":16,"magicclose":true}\';; esac\n'); shim.chmod(0o755)
    r = dev.run(HR, "t", env={"MK20_UBUS": str(shim), "MK20_HR_NOSLEEP": "1"})
    assert r.returncode == 10
