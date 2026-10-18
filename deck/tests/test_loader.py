import time, pathlib
from conftest import FW, P4, build_release, install_files, sha

L = FW / "rescue/loader.sh"


def setup_rel(dev, v, **kw):
    return install_files(dev, build_release(dev.tmp, v, **kw), v)


def wait_file(p, t=5):
    end = time.time() + t
    while time.time() < end:
        if pathlib.Path(p).exists():
            return True
        time.sleep(0.05)
    return False


def test_no_release_is_legacy(dev):
    assert dev.run(L, "boot").returncode == 10


def test_flag_disables(dev):
    setup_rel(dev, "1.0.0"); assert dev.run(L, "switch", "1.0.0").returncode == 0
    dev.p("mnt/SDCARD/mk20-no-release").touch()
    assert dev.run(L, "boot").returncode == 12


def test_install_boot_confirm(dev):
    setup_rel(dev, "1.0.0")
    r = dev.run(L, "switch", "1.0.0"); assert r.returncode == 0, r.stdout
    assert dev.read("data/mk20/state/pending") == "1.0.0"
    r = dev.run(L, "boot"); assert r.returncode == 0, dev.read("mnt/SDCARD/mk20/logs/loader.log")
    assert dev.read("data/mk20/state/attempts") == "1"
    assert dev.read("tmp/mk20/release-active") == "1.0.0"
    assert wait_file(dev.p("tmp/mk20/mk20d.ran"))
    # verified tmpfs copy matches the release
    assert sha(dev.p("tmp/mk20/rel/share/payload.txt")) == sha(dev.p("data/mk20/releases/1.0.0/share/payload.txt"))
    assert dev.run(L, "confirm", "1.0.0").returncode == 0
    assert dev.read("data/mk20/state/good") == "1.0.0"
    assert dev.read("data/mk20/state/pending") is None
    assert dev.run(L, "boot").returncode == 0
    assert dev.read("data/mk20/state/attempts") is None


def test_unconfirmed_pending_rolls_back_to_good(dev):
    setup_rel(dev, "1.0.0"); dev.run(L, "switch", "1.0.0"); dev.run(L, "boot"); dev.run(L, "confirm", "1.0.0")
    setup_rel(dev, "1.0.1"); assert dev.run(L, "switch", "1.0.1").returncode == 0
    assert dev.run(L, "boot").returncode == 0
    assert dev.read("tmp/mk20/release-active") == "1.0.1"
    # no confirm -> next boot rolls back without another reset
    assert dev.run(L, "boot").returncode == 0
    assert dev.read("tmp/mk20/release-active") == "1.0.0"
    assert dev.read("data/mk20/state/pending") is None
    assert dev.p("data/mk20/state/failed-1.0.1").exists()
    assert "1.0.1->1.0.0" in dev.read("data/mk20/state/last-rollback")
    assert dev.run(L, "boot").returncode == 0 and dev.read("tmp/mk20/release-active") == "1.0.0"


def test_first_install_unconfirmed_goes_stock(dev):
    setup_rel(dev, "1.0.0"); dev.run(L, "switch", "1.0.0")
    assert dev.run(L, "boot").returncode == 0
    assert dev.run(L, "boot").returncode == 11


def test_corrupt_pending_rolls_back(dev):
    setup_rel(dev, "1.0.0"); dev.run(L, "switch", "1.0.0"); dev.run(L, "boot"); dev.run(L, "confirm", "1.0.0")
    d = setup_rel(dev, "1.0.1"); dev.run(L, "switch", "1.0.1")
    (d / "share/payload.txt").write_text("tampered\n")
    assert dev.run(L, "boot").returncode == 0
    assert dev.read("tmp/mk20/release-active") == "1.0.0"
    assert "sha mismatch" in dev.read("data/mk20/state/last-rollback")


def test_corrupt_good_is_stock(dev):
    d = setup_rel(dev, "1.0.0"); dev.run(L, "switch", "1.0.0"); dev.run(L, "boot"); dev.run(L, "confirm", "1.0.0")
    (d / "sbin/mk20d").write_text("#!/bin/sh\necho evil\n")
    assert dev.run(L, "boot").returncode == 11


def test_missing_file_is_stock(dev):
    d = setup_rel(dev, "1.0.0"); dev.run(L, "switch", "1.0.0"); dev.run(L, "boot"); dev.run(L, "confirm", "1.0.0")
    (d / "share/payload.txt").unlink()
    assert dev.run(L, "boot").returncode == 11


def test_kernel_not_allowed(dev):
    setup_rel(dev, "1.0.0", kernels=("0" * 64,))
    r = dev.run(L, "switch", "1.0.0"); assert r.returncode == 1 and "kernel" in r.stdout
    assert dev.run(L, "boot").returncode == 10


def test_kernel_changed_after_install_good_is_stock(dev):
    setup_rel(dev, "1.0.0"); dev.run(L, "switch", "1.0.0"); dev.run(L, "boot"); dev.run(L, "confirm", "1.0.0")
    assert dev.run(L, "boot", env={"MK20_P4_SHA": "9" * 64}).returncode == 11


def test_garbage_slots(dev):
    setup_rel(dev, "1.0.0"); dev.run(L, "switch", "1.0.0"); dev.run(L, "confirm", "1.0.0")
    setup_rel(dev, "1.0.1"); dev.run(L, "switch", "1.0.1"); dev.run(L, "confirm", "1.0.1")
    st = dev.run(L, "status").stdout; assert "1.0.1" in st.split("current=")[1].split()[2]
    # corrupt the newest slot -> the other one is used
    newest = "A" if "current=A" in st else "B"
    dev.p(f"data/mk20/slots/{newest}").write_text("gen=99\nversion=../../x\n")
    assert dev.run(L, "boot").returncode == 0 and dev.read("tmp/mk20/release-active") == "1.0.0"
    other = "B" if newest == "A" else "A"
    dev.p(f"data/mk20/slots/{other}").write_text("")
    assert dev.run(L, "boot").returncode == 11


def test_slot_checksum_tamper(dev):
    setup_rel(dev, "1.0.0"); dev.run(L, "switch", "1.0.0")
    for s in ("A", "B"):
        f = dev.p(f"data/mk20/slots/{s}")
        if f.exists():
            f.write_text(f.read_text().replace("gen=1", "gen=7"))
    assert dev.run(L, "boot").returncode == 11


def test_unsafe_manifest_path(dev):
    d = build_release(dev.tmp, "1.0.0")
    m = d / "MANIFEST"
    m.write_text(m.read_text() + f"{'0'*64} 644 ../../etc/passwd\n")
    install_files(dev, d, "1.0.0")
    r = dev.run(L, "switch", "1.0.0"); assert r.returncode == 1 and "unsafe path" in r.stdout


def test_switch_refusals(dev):
    setup_rel(dev, "1.0.0"); setup_rel(dev, "1.0.1")
    assert dev.run(L, "switch", "1.0.0").returncode == 0
    assert "pending" in dev.run(L, "switch", "1.0.1").stdout
    dev.run(L, "confirm", "1.0.0")
    assert "already current" in dev.run(L, "switch", "1.0.0").stdout
    assert dev.run(L, "switch", "../1.0.0").returncode == 1
    assert dev.run(L, "switch", "9.9.9").returncode == 1


def test_manual_rollback(dev):
    setup_rel(dev, "1.0.0"); dev.run(L, "switch", "1.0.0"); dev.run(L, "confirm", "1.0.0")
    setup_rel(dev, "1.0.1"); dev.run(L, "switch", "1.0.1"); dev.run(L, "confirm", "1.0.1")
    r = dev.run(L, "rollback"); assert r.returncode == 0, r.stdout
    assert dev.run(L, "boot").returncode == 0 and dev.read("tmp/mk20/release-active") == "1.0.0"


def test_deadline_resets_once(dev):
    setup_rel(dev, "1.0.0"); dev.run(L, "switch", "1.0.0")
    hr = dev.tmp / "hr.sh"; hr.write_text(f'echo reset >> {dev.mock}/resets\n')
    env = {"MK20_DEADLINE": "50", "MK20_HR": f"sh {hr}"}
    assert dev.run(L, "deadline", "1.0.0", env=env).returncode == 0
    assert dev.run(L, "deadline", "1.0.0", env=env).returncode == 0
    assert (dev.mock / "resets").read_text().count("reset") == 1


def test_deadline_noop_when_confirmed(dev):
    setup_rel(dev, "1.0.0"); dev.run(L, "switch", "1.0.0"); dev.run(L, "confirm", "1.0.0")
    hr = dev.tmp / "hr.sh"; hr.write_text(f'echo reset >> {dev.mock}/resets\n')
    dev.run(L, "deadline", "1.0.0", env={"MK20_DEADLINE": "50", "MK20_HR": f"sh {hr}"})
    assert not (dev.mock / "resets").exists()
