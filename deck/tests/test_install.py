"""M3: installer (loader install/prune/uninstall): pinned sha, safe tar listing, verify, idempotence, power cuts."""
import io, os, pathlib, subprocess, tarfile, hashlib
import pytest
from conftest import FW, P4, build_release, sha, BB

L = FW / "rescue/loader.sh"
CRASH = pathlib.Path(__file__).parent / "crashshims"


def tarball(dev, v):
    d = build_release(dev.tmp, v)
    t = d.parent / f"mk20-fw-{v}.tar"
    assert t.exists()
    return t


def inst(dev, t, *extra, env=None, pin=None):
    return dev.run(L, "install", str(t), pin or sha(t), *extra, env=env)


def st(dev, k):
    return dev.read(f"data/mk20/state/{k}")


def test_install_switches_to_pending(dev):
    t = tarball(dev, "1.0.0")
    r = inst(dev, t)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "1.0.0 installed" in r.stdout and st(dev, "pending") == "1.0.0"
    assert not list(dev.p("data/mk20/releases").glob(".incoming-*"))
    assert oct(dev.p("data/mk20/releases/1.0.0/sbin/mk20d").stat().st_mode & 0o777) == "0o755"
    b = dev.run(L, "boot")
    assert b.returncode == 0 and dev.read("tmp/mk20/release-active") == "1.0.0" and st(dev, "attempts") == "1"
    assert dev.run(L, "confirm", "1.0.0").returncode == 0 and st(dev, "good") == "1.0.0"


def test_upgrade_and_pending_refusal(dev):
    t0, t1, t2 = tarball(dev, "1.0.0"), tarball(dev, "1.0.1"), tarball(dev, "1.0.2")
    assert inst(dev, t0).returncode == 0
    dev.run(L, "boot"); dev.run(L, "confirm", "1.0.0")
    assert inst(dev, t1).returncode == 0 and st(dev, "pending") == "1.0.1"
    r = inst(dev, t2)
    assert r.returncode == 1 and "pending" in r.stdout and not dev.p("data/mk20/releases/1.0.2").exists()
    r = inst(dev, t2, "--no-switch")
    assert r.returncode == 0 and st(dev, "pending") == "1.0.1" and dev.p("data/mk20/releases/1.0.2").exists()


def test_wrong_sha_refused(dev):
    t = tarball(dev, "1.0.0")
    r = inst(dev, t, pin="0" * 64)
    assert r.returncode == 1 and "sha mismatch" in r.stdout
    assert not dev.p("data/mk20/releases/1.0.0").exists()
    assert dev.run(L, "install", str(t), "nothex").returncode == 1


def test_idempotent_and_corrupt_replaced(dev):
    t = tarball(dev, "1.0.0")
    assert inst(dev, t, "--no-switch").returncode == 0
    r = inst(dev, t, "--no-switch")
    assert r.returncode == 0 and "already installed" in r.stdout
    f = dev.p("data/mk20/releases/1.0.0/share/payload.txt"); f.write_text("corrupt\n")
    r = inst(dev, t, "--no-switch")
    assert r.returncode == 0 and dev.run(L, "verify", "1.0.0").returncode == 0


def test_corrupt_good_not_replaced(dev):
    t = tarball(dev, "1.0.0")
    inst(dev, t); dev.run(L, "boot"); dev.run(L, "confirm", "1.0.0")
    other = build_release(dev.tmp / "x", "1.0.0", extra={"share/z.txt": "different\n"}) if False else None
    m = dev.p("data/mk20/releases/1.0.0/MANIFEST"); m.write_text(m.read_text() + "\n")
    r = inst(dev, t, "--no-switch")
    assert r.returncode == 1 and "good/current" in r.stdout


def mktar(path, entries):
    """entries: list of (name, kind, data|linkname)"""
    with tarfile.open(path, "w") as tf:
        for name, kind, data in entries:
            ti = tarfile.TarInfo(name)
            if kind == "d":
                ti.type = tarfile.DIRTYPE; ti.mode = 0o755; tf.addfile(ti)
            elif kind == "f":
                b = data.encode(); ti.size = len(b); ti.mode = 0o644; tf.addfile(ti, io.BytesIO(b))
            elif kind == "l":
                ti.type = tarfile.SYMTYPE; ti.linkname = data; tf.addfile(ti)
            elif kind == "h":
                ti.type = tarfile.LNKTYPE; ti.linkname = data; tf.addfile(ti)
    return path


def good_entries(dev, v="1.0.5"):
    d = build_release(dev.tmp, v)
    out = []
    for p in sorted(d.rglob("*")):
        rel = f"mk20-fw-{v}/" + str(p.relative_to(d))
        out.append((rel, "d", None) if p.is_dir() else (rel, "f", p.read_text()))
    return [(f"mk20-fw-{v}", "d", None)] + out


@pytest.mark.parametrize("mutation,msg", [
    (lambda e: e + [("mk20-fw-1.0.5/share/evil", "l", "/etc/passwd")], "unsafe tar entry"),
    (lambda e: e + [("mk20-fw-1.0.5/share/hl", "h", "mk20-fw-1.0.5/MANIFEST")], "unsafe tar entry"),
    (lambda e: e + [("mk20-fw-1.0.5/../../evil", "f", "x")], "unsafe tar entry"),
    (lambda e: e + [("other/evil", "f", "x")], "unsafe tar entry"),
    (lambda e: e + [("mk20-fw-1.0.5/share/x y", "f", "x")], "unsafe tar entry"),
    (lambda e: e + [("mk20-fw-1.0.5/share/unlisted", "f", "x")], "unlisted files"),
    (lambda e: [(n, k, ("tampered\n" if n.endswith("payload.txt") else d)) for n, k, d in e], "verify failed"),
    (lambda e: [(n.replace("mk20-fw-1.0.5", "mk20-fw-x"), k, d) for n, k, d in e], "bad top-level"),
])
def test_malicious_tarballs(dev, mutation, msg):
    t = mktar(dev.tmp / "bad.tar", mutation(good_entries(dev)))
    r = inst(dev, t)
    assert r.returncode == 1 and msg in r.stdout, r.stdout + r.stderr
    rel = dev.p("data/mk20/releases")
    assert not (rel / "1.0.5").exists() and not list(rel.glob(".incoming-*")) if rel.exists() else True
    assert not pathlib.Path(dev.root.parent / "evil").exists() and not (dev.root / "evil").exists()
    assert st(dev, "pending") is None


def test_absolute_path_rejected(dev):
    e = good_entries(dev) + [("/tmp/abs-evil", "f", "x")]
    t = mktar(dev.tmp / "abs.tar", e)
    r = inst(dev, t)
    assert r.returncode == 1


def test_no_space(dev, tmp_path):
    sh = tmp_path / "dfshim"; sh.mkdir()
    (sh / "df").write_text("#!/bin/sh\necho 'Filesystem 1K-blocks Used Available Use% Mounted'\necho 'x 100 90 10 90% /'\n")
    (sh / "df").chmod(0o755)
    t = tarball(dev, "1.0.0")
    r = inst(dev, t, env={"PATH": f"{sh}:{dev.env['PATH']}"})
    assert r.returncode == 1 and "not enough space" in r.stdout


def test_busy_lock(dev):
    t = tarball(dev, "1.0.0")
    lk = dev.p("tmp/mk20/install.lock"); lk.touch()
    holder = subprocess.Popen(["flock", "-x", str(lk), "sleep", "5"])
    try:
        import time; time.sleep(0.3)
        assert inst(dev, t).returncode == 13
        assert dev.run(L, "prune").returncode == 13
    finally:
        holder.kill(); holder.wait()


def test_prune_keeps_referenced(dev):
    ts = {v: tarball(dev, v) for v in ("1.0.0", "1.0.1", "1.0.2", "1.0.3", "1.0.4")}
    inst(dev, ts["1.0.0"]); dev.run(L, "boot"); dev.run(L, "confirm", "1.0.0")
    for v in ("1.0.1", "1.0.2", "1.0.3"):
        assert inst(dev, ts[v], "--no-switch").returncode == 0
    dev.p("data/mk20/releases/.incoming-9.9.9").mkdir()
    assert inst(dev, ts["1.0.4"]).returncode == 0       # switch + prune
    left = sorted(p.name for p in dev.p("data/mk20/releases").iterdir())
    # good 1.0.0, pending/current 1.0.4, + MK20_KEEP=1 newest extra (1.0.3); 1.0.1/1.0.2 and leftovers pruned
    assert left == ["1.0.0", "1.0.3", "1.0.4"], left


def test_uninstall_to_legacy(dev):
    kept = [dev.p("data/mk20/" + name) for name in ("agent", "secrets")]
    for directory in kept:
        directory.mkdir()
    inodes = [directory.stat().st_ino for directory in kept]
    t = tarball(dev, "1.0.0")
    inst(dev, t); dev.run(L, "boot"); dev.run(L, "confirm", "1.0.0")
    r = dev.run(L, "uninstall")
    assert r.returncode == 0
    assert dev.run(L, "boot").returncode == 10
    assert dev.p("data/mk20/releases/1.0.0").exists()
    assert [directory.stat().st_ino for directory in kept] == inodes


def run_group(dev, args, env):
    e = dict(dev.env); e.update(env)
    return subprocess.run([BB, "sh", str(L), *args], env=e, capture_output=True, text=True, timeout=60,
                          start_new_session=True)


def invariant(dev):
    r = dev.run(L, "boot")
    if r.returncode == 11:
        return "stock"
    assert r.returncode == 0, r.stdout + r.stderr
    active = dev.read("tmp/mk20/release-active")
    good, pend, att = st(dev, "good"), st(dev, "pending"), st(dev, "attempts")
    assert active == good or (active == pend and att and int(att) >= 1), (active, good, pend, att)
    assert dev.run(L, "verify", active).returncode == 0
    return active


@pytest.mark.parametrize("n", range(1, 31))
def test_power_cut_during_install(dev, n):
    t0, t1 = tarball(dev, "1.0.0"), tarball(dev, "1.0.1")
    assert inst(dev, t0).returncode == 0
    dev.run(L, "boot"); assert dev.run(L, "confirm", "1.0.0").returncode == 0
    (dev.mock / "crash.n").unlink(missing_ok=True)
    run_group(dev, ["install", str(t1), sha(t1)],
              {"MOCK_CRASH_AT": str(n), "MOCK_CRASH_GROUP": "1", "PATH": f"{CRASH}:{dev.env['PATH']}"})
    crashed = (dev.mock / "crashed").exists()
    calls = int((dev.mock / "crash.n").read_text()) if (dev.mock / "crash.n").exists() else 0
    assert crashed or calls < n
    # after the cut the board boots good or 1.0.1 on probation, never a half-installed release
    a = invariant(dev)
    assert a in ("1.0.0", "1.0.1")
    if a == "1.0.0" and st(dev, "pending") is None:
        # re-running the installer completes the job
        r = inst(dev, t1)
        assert r.returncode == 0, r.stdout + r.stderr
        assert st(dev, "pending") == "1.0.1"
    assert not list(dev.p("data/mk20/releases").glob(".incoming-*")) or a == "1.0.0"


def test_install_fault_points_exist(dev):
    """The install path has enough mv/sync points that the power-cut matrix above really cuts mid-install."""
    t0, t1 = tarball(dev, "1.0.0"), tarball(dev, "1.0.1")
    inst(dev, t0); dev.run(L, "boot"); dev.run(L, "confirm", "1.0.0")
    (dev.mock / "crash.n").unlink(missing_ok=True)
    run_group(dev, ["install", str(t1), sha(t1)], {"MOCK_CRASH_AT": "999", "PATH": f"{CRASH}:{dev.env['PATH']}"})
    calls = int((dev.mock / "crash.n").read_text())
    assert 8 <= calls < 31, calls


@pytest.mark.parametrize("mutation,msg", [
    (lambda e: e + [e[3]], "unsafe tar entry"),                                       # duplicate member
    (lambda e: e + [("mk20-fw-1.0.5/share/" + "a" * 120, "f", "x")], "unsafe tar entry"),  # long name
    (lambda e: e + [("mk20-fw-1.0.5/share/.", "d", None)], "unsafe tar entry"),
    (lambda e: e + [("mk20-fw-1.0.5/share/f;reboot", "f", "x")], "unsafe tar entry"),
])
def test_more_malicious_tarballs(dev, mutation, msg):
    t = mktar(dev.tmp / "bad2.tar", mutation(good_entries(dev)))
    r = inst(dev, t)
    assert r.returncode == 1 and msg in r.stdout, r.stdout + r.stderr


def test_fifo_member_rejected(dev):
    e = good_entries(dev)
    t = dev.tmp / "fifo.tar"
    mktar(t, e)
    with tarfile.open(t, "a") as tf:
        ti = tarfile.TarInfo("mk20-fw-1.0.5/share/fifo"); ti.type = tarfile.FIFOTYPE; tf.addfile(ti)
    r = inst(dev, t)
    assert r.returncode == 1 and "unsafe tar entry" in r.stdout


def test_malformed_manifest_line_rejected(dev):
    e = good_entries(dev)
    e = [(n, k, (d.replace(" 644 share/payload.txt", "  644 share/payload.txt") if n.endswith("/MANIFEST") else d)) for n, k, d in e]
    t = mktar(dev.tmp / "mf.tar", e)
    r = inst(dev, t)
    assert r.returncode == 1 and "malformed manifest line" in r.stdout


def test_stale_incoming_does_not_wedge_install(dev):
    t = tarball(dev, "1.0.0")
    big = dev.p("data/mk20/releases/.incoming-1.0.0"); big.mkdir(parents=True)
    (big / "junk").write_text("x")
    dev.p("data/mk20/releases/.staging.tar").write_text("old")
    r = inst(dev, t, "--no-switch")
    assert r.returncode == 0 and not big.exists() and not dev.p("data/mk20/releases/.staging.tar").exists()


def test_source_changed_after_pin_uses_private_copy(dev):
    """The staged copy is hashed after copying; a different source (as if replaced) is refused."""
    t = tarball(dev, "1.0.0"); pin = sha(t)
    t2 = tarball(dev, "1.0.1")
    r = dev.run(L, "install", str(t2), pin)
    assert r.returncode == 1 and "sha mismatch" in r.stdout
    assert not dev.p("data/mk20/releases/.staging.tar").exists()


def test_boot_cleans_stale_incoming(dev):
    t = tarball(dev, "1.0.0")
    inst(dev, t); dev.run(L, "boot"); dev.run(L, "confirm", "1.0.0")
    d = dev.p("data/mk20/releases/.incoming-1.0.9"); d.mkdir()
    dev.run(L, "boot")
    assert not d.exists()


def test_state_lock_serialises_mutations(dev):
    t = tarball(dev, "1.0.0")
    inst(dev, t, "--no-switch")
    lk = dev.p("tmp/mk20/loader.lock"); lk.touch()
    holder = subprocess.Popen(["flock", "-x", str(lk), "sleep", "6"])
    try:
        import time; time.sleep(0.3)
        r = dev.run(L, "switch", "1.0.0", env={"MK20_LOCK_WAIT": "1"}, timeout=60)
        # switch waits for the lock (<=30 s) and succeeds once the holder exits
        assert r.returncode == 0 and st(dev, "pending") == "1.0.0"
    finally:
        holder.kill(); holder.wait()


def test_uninstall_disables_legacy_composite(dev):
    t = tarball(dev, "1.0.0")
    inst(dev, t); dev.run(L, "boot"); dev.run(L, "confirm", "1.0.0")
    e = dev.p("mnt/SDCARD/mk20/boot-every"); e.mkdir(parents=True)
    (e / "composite.sh").write_text("x"); (e / "composite.sh.sha256").write_text("y"); (e / "adb.sh").write_text("z")
    assert dev.run(L, "uninstall").returncode == 0
    assert not (e / "composite.sh").exists() and (e / "adb.sh").exists()
    assert dev.p("mnt/SDCARD/mk20/boot-every.disabled/composite.sh").exists()


def test_130_upgrade_from_12x_is_one_pending_slot_write_no_p5(dev):
    """K4 changes userland only: existing install -> one inactive-slot generation, no raw partition writes."""
    old = tarball(dev, "1.2.1")
    assert inst(dev, old).returncode == 0
    dev.run(L, "boot"); dev.run(L, "confirm", "1.2.1")
    slots = dev.p("data/mk20/slots")
    before = {p.name: p.read_bytes() for p in slots.iterdir() if p.name in ("A", "B")}
    partition = dev.p("dev/mmcblk0p5"); partition.write_bytes(b"rootfs unchanged")
    vendor = dev.p("data/KeyboardDevice"); vendor.write_bytes(b"vendor unchanged")
    log = dev.p("mnt/SDCARD/mk20/logs/loader.log")
    prior = log.read_text().count("slot ")
    # Record actual pointer publication renames, beyond comparing final bytes/logs.
    shim = dev.tmp / "slot-publish"; shim.mkdir()
    mv = shim / "mv"
    mv.write_text('''#!/bin/sh
last=""; for arg in "$@"; do last=$arg; done
case "$last" in "$MOCK_SLOT_DIR/A"|"$MOCK_SLOT_DIR/B") echo "$last" >> "$MOCK_SLOT_WRITES";; esac
exec busybox mv "$@"
''')
    mv.chmod(0o755)
    writes = dev.tmp / "slot-writes"
    env = {"PATH": f"{shim}:{dev.env['PATH']}", "MOCK_SLOT_DIR": str(slots), "MOCK_SLOT_WRITES": str(writes)}
    update = tarball(dev, "1.3.0")
    assert inst(dev, update, env=env).returncode == 0
    assert len(writes.read_text().splitlines()) == 1
    after = {p.name: p.read_bytes() for p in slots.iterdir() if p.name in ("A", "B")}
    assert sum(before.get(k) != v for k, v in after.items()) == 1
    assert log.read_text().count("slot ") - prior == 1
    assert st(dev, "pending") == "1.3.0" and st(dev, "good") == "1.2.1"
    assert partition.read_bytes() == b"rootfs unchanged" and vendor.read_bytes() == b"vendor unchanged"
    assert not dev.p("dev/by-name/rootfs").exists()
    # Repeating an identical pending install never writes another slot.
    committed = dict(after)
    inst(dev, update, env=env)
    assert len(writes.read_text().splitlines()) == 1
    assert {p.name: p.read_bytes() for p in slots.iterdir() if p.name in ("A", "B")} == committed
