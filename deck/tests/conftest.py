import os, shutil, subprocess, pathlib, hashlib, pytest

DECK = pathlib.Path(__file__).resolve().parents[1]
FW = DECK / "firmware"
SHIMS = pathlib.Path(__file__).parent / "shims"
P4 = "7326d9e5ab9fd9f140b6cc7597b4eb0539b019139d12b52b4c133771e35843cc"


# Device: BusyBox v1.27.2. Set MK20_TEST_BUSYBOX to a 1.27.2 build for fidelity (the box keeps one at
# /workspace/tools/bb127/busybox); default: the host busybox.
BB = os.environ.get("MK20_TEST_BUSYBOX") or shutil.which("busybox")


def _bbdir(tmp):
    """PATH dir of busybox applet symlinks so scripts run against busybox tools, not GNU coreutils."""
    d = tmp / "bb"
    d.mkdir()
    bb = BB
    (d / "busybox").symlink_to(bb)
    for a in subprocess.run([bb, "--list"], capture_output=True, text=True).stdout.split():
        if a in ("flock", "mount", "pgrep", "ubus", "busybox"):
            continue
        (d / a).symlink_to(bb)
    return d


class Dev:
    def __init__(self, tmp):
        self.tmp = tmp
        self.root = tmp / "root"
        self.mock = tmp / "mock"
        self.mock.mkdir()
        for p in ["data/mk20", "mnt/SDCARD/mk20/logs", "tmp/mk20", "dev/by-name"]:
            (self.root / p).mkdir(parents=True, exist_ok=True)
        self.bb = _bbdir(tmp)
        self.env = {
            "PATH": f"{SHIMS}:{self.bb}:/usr/bin:/bin",
            "MK20_ROOT": str(self.root),
            "MOCK_DIR": str(self.mock),
            "MK20_P4_SHA": P4,
            "MK20_UPTIME_CMD": "echo 100",
            "MK20_UBUS": "ubus",
            "HOME": str(tmp),
        }

    def run(self, script, *args, env=None, timeout=60):
        e = dict(self.env)
        if env:
            e.update(env)
        return subprocess.run([BB, "sh", str(script), *args], env=e, capture_output=True, text=True, timeout=timeout)

    def p(self, rel):
        return self.root / rel

    def read(self, rel):
        f = self.p(rel)
        return f.read_text().strip() if f.exists() else None


@pytest.fixture
def dev(tmp_path):
    return Dev(tmp_path)


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def build_release(tmp, version, mk20d_body='echo "$MK20_VERSION boot" >> "$MK20_ROOT/tmp/mk20/mk20d.ran"\n',
                  kernels=(P4,), extra=None):
    """Build a test release with the real build tool; returns the release dir."""
    src = tmp / f"src-{version}"
    (src / "release/sbin").mkdir(parents=True)
    (src / "release/share").mkdir()
    (src / "VERSION").write_text(version + "\n")
    (src / "KERNELS").write_text("\n".join(kernels) + "\n")
    (src / "release/sbin/mk20d").write_text("#!/bin/sh\n" + mk20d_body)
    (src / "release/sbin/mk20d").chmod(0o755)
    (src / "release/share/payload.txt").write_text(f"payload {version}\n")
    for k, v in (extra or {}).items():
        f = src / "release" / k
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(v)
    shutil.copytree(FW / "rescue", src / "rescue")
    out = tmp / f"out-{version}"
    r = subprocess.run(["sh", str(DECK / "tools/mk20-release-build.sh"), str(src), str(out), "test"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return out / f"mk20-fw-{version}"


def install_files(dev, reldir, version):
    dst = dev.p(f"data/mk20/releases/{version}")
    shutil.copytree(reldir, dst)
    return dst
