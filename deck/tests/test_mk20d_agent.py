"""A8b host-only agent integration. Run the real mk20d in an isolated fake root.

Linux uses BusyBox when available. Darwin adapters supply only missing host utilities
(flock, proc uptime, stat -c); no USB/network/device operation runs. kbd.sock is an
existence gate; the real runtime still validates/connects the Unix socket itself.
"""
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import time

import pytest
from conftest import BB, FW, SHIMS
from test_mk20d import rel, g1, conf, cfg, run_boot  # noqa: F401

SHELL = [BB, "sh"] if BB else [shutil.which("dash") or "/bin/sh"]


def wait(pred, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.03)
    return False


class HostDev:
    def __init__(self, tmp):
        self.tmp, self.root, self.mock = tmp, tmp / "root", tmp / "mock"
        self.mock.mkdir()
        self.groups = []
        for p in ("data/mk20", "mnt/SDCARD/mk20/logs", "tmp/mk20", "dev/by-name"):
            self.p(p).mkdir(parents=True)
        self.bin = tmp / "host-bin"
        self.bin.mkdir()
        self.env = dict(MK20_ROOT=str(self.root), MOCK_DIR=str(self.mock),
                        MK20_P4_SHA="7326d9e5ab9fd9f140b6cc7597b4eb0539b019139d12b52b4c133771e35843cc",
                        PATH=f"{self.bin}:{SHIMS}:" + os.environ["PATH"])
        # Shell subprocesses must use the same implementation as the entry point.
        (self.bin / "sh").symlink_to(BB or SHELL[0])
        self.helper("flock", '''import fcntl,sys
try: fcntl.flock(int(sys.argv[-1]),fcntl.LOCK_EX|fcntl.LOCK_NB)
except BlockingIOError: sys.exit(1)
''')
        # Explicit process lookup scoped to THIS release, never another worktree's fake.
        self.helper("pgrep", '''import os,sys,signal
from pathlib import Path
pat=sys.argv[-1]; m=Path(os.environ['MOCK_DIR'])
if 'mk20-agent' in pat:
 try:
  p=int((m/'agent.pid').read_text()); os.kill(p,0); print(p)
 except (OSError,ValueError): sys.exit(1)
else:
 f=m/'pgrep.out'
 if not f.exists() or not f.read_text().strip(): sys.exit(1)
 print(f.read_text(),end='')
''')
        if not BB:
            # BusyBox wc prints an unpadded count; BSD wc left-pads stdin counts.
            self.helper("wc", '''import subprocess,sys
r=subprocess.run(['/usr/bin/wc']+sys.argv[1:],capture_output=True,text=True)
print(r.stdout.strip()); sys.exit(r.returncode)
''')
            # Loader consumes BusyBox's six-field tar listing; preserve real extraction.
            self.helper("tar", '''import os,sys,tarfile,stat
if sys.argv[1]=='-tvf':
 with tarfile.open(sys.argv[2]) as t:
  for f in t:
   mode=stat.filemode((stat.S_IFDIR if f.isdir() else stat.S_IFREG)|f.mode)
   print(f'{mode} 0/0 {f.size} 1970-01-01 00:00 {f.name}')
else: os.execv('/usr/bin/tar',['tar']+sys.argv[1:])
''')
        if not Path("/proc/uptime").exists():
            self.helper("cut", '''import os,sys,time
if sys.argv[-1]=='/proc/uptime': print(int(time.monotonic()))
else: os.execv('/usr/bin/cut',['cut']+sys.argv[1:])
''')
            self.helper("stat", '''import os,sys
if sys.argv[1:3]==['-c','%Y']: print(int(os.stat(sys.argv[3]).st_mtime))
else: os.execv('/usr/bin/stat',['stat']+sys.argv[1:])
''')

    def helper(self, name, code):
        p = self.bin / name
        p.write_text(f"#!{sys.executable}\n" + code)
        p.chmod(0o755)

    def p(self, name):
        return self.root / name

    def read(self, name):
        p = self.p(name)
        return p.read_text().strip() if p.exists() else None

    def start(self, script, *args, env=None):
        p = subprocess.Popen(SHELL + [str(script), *args], env=dict(self.env, **(env or {})),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        self.groups.append(p.pid)
        return p

    def run(self, script, *args, env=None, timeout=60):
        p = subprocess.Popen(SHELL + [str(script), *args], env=dict(self.env, **(env or {})),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        self.groups.append(p.pid)
        out, err = p.communicate(timeout=timeout)
        return subprocess.CompletedProcess(p.args, p.returncode, out, err)


@pytest.fixture
def dev(tmp_path):
    d = HostDev(tmp_path)
    yield d
    # All helpers/descendants inherit these private groups, including detached boot loops.
    for group in d.groups:
        try:
            os.killpg(group, signal.SIGKILL)
        except ProcessLookupError:
            pass


@pytest.fixture
def agent(dev, rel, g1):
    conf(dev, "FEATURE_AGENT=1\n")
    (g1 / "configs/c.1/ecm.usb0").mkdir()
    sock = dev.p("run/mk20/kbd.sock")
    sock.parent.mkdir(parents=True)
    sock.touch()
    b = rel / "bin/mk20-agent"
    b.write_text(f'''#!{sys.executable}
import json,os,signal,sys,time
from pathlib import Path
m=Path(os.environ['MOCK_DIR'])
(m/'agent.pid').write_text(str(os.getpid()))
with (m/'calls').open('a') as f:
 f.write(json.dumps(dict(args=sys.argv[1:],nice=os.getpriority(os.PRIO_PROCESS,0)))+'\\n')
print('fake agent started',flush=True)
def stop(*_):
 (m/'agent.pid').unlink(missing_ok=True); sys.exit(0)
signal.signal(signal.SIGTERM,stop)
if (m/'crash').exists():
 (m/'agent.pid').unlink(missing_ok=True); sys.exit(7)
while True: time.sleep(0.05)
''')
    b.chmod(0o755)
    return sock


def calls(dev):
    p = dev.mock / "calls"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


@pytest.mark.parametrize("config,env,fat,want", [
    ("", {}, False, "0"), ("FEATURE_AGENT=1\n", {}, False, "1"),
    ("FEATURE_AGENT=yes\n", {}, False, "0"), ("", {"MK20_AGENT": "1"}, False, "0"),
    ("FEATURE_AGENT=1\n", {"MK20_AGENT": "0"}, False, "0"),
    ("FEATURE_AGENT=1\n", {"MK20_AGENT": "1"}, True, "0"),
])
def test_agent_flags(dev, rel, config, env, fat, want):
    conf(dev, config)
    if fat:
        dev.p("mnt/SDCARD/mk20-no-agent").touch()
    out = dev.run(rel / "sbin/mk20d", "config", env=env)
    assert out.returncode == 0
    assert dict(x.split("=", 1) for x in out.stdout.splitlines())["FEATURE_AGENT"] == want


def test_agent_single_instance_priority_and_stop(dev, rel, agent):
    p = dev.start(rel / "sbin/mk20d", "agent")
    assert wait(lambda: calls(dev))
    assert dev.run(rel / "sbin/mk20d", "agent", timeout=5).returncode == 0
    assert len(calls(dev)) == 1
    assert calls(dev)[0]["args"] == ["--web", "--kbd-sock", str(agent), "--web-config-dir",
                                     str(dev.p("data/mk20/agent")), "--web-secrets-dir", str(dev.p("data/mk20/secrets"))]
    log = dev.read("tmp/mk20/agent.log")
    if "agent priority: nice=10 verified" in log:
        assert calls(dev)[0]["nice"] == 10
    else:
        # Darwin sandbox denies setpriority; exercise and require the explicit fallback.
        assert "WARN agent priority: nice=10 unavailable or not verified" in log
    if Path("/proc/self/oom_score_adj").exists():
        pid = (dev.mock / "agent.pid").read_text()
        assert Path(f"/proc/{pid}/oom_score_adj").read_text().strip() == "500"
    else:
        assert "WARN agent priority: oom_score_adj=500 unavailable" in log
    dev.p("run/mk20/agent.status").write_text('{"allowed":false,"state":"disabled","provider":"unknown","route":"proxy","last_error_class":"none"}')
    h = json.loads(dev.run(rel / "sbin/mk20d", "health").stdout)
    assert h["agent"]["running"] is True and h["agent"]["allowed"] is False
    dev.p("tmp/mk20-agent-stop").touch()
    assert p.wait(8) == 0
    assert not (dev.mock / "agent.pid").exists()
    assert not dev.p("tmp/mk20/agent-loop.pid").exists()
    # Supervisor must never erase an operator's pre-existing stop marker.
    assert dev.run(rel / "sbin/mk20d", "agent", timeout=5).returncode == 0
    assert len(calls(dev)) == 1


@pytest.mark.parametrize("how", ["conf", "fat", "env"])
def test_agent_kill_switch_stops_live_child(dev, rel, agent, how):
    if how == "env":
        r = dev.run(rel / "sbin/mk20d", "agent", env={"MK20_AGENT": "0"}, timeout=5)
        assert r.returncode == 0 and not calls(dev)
        return
    p = dev.start(rel / "sbin/mk20d", "agent")
    assert wait(lambda: calls(dev))
    if how == "conf":
        conf(dev, "FEATURE_AGENT=0\n")
    else:
        dev.p("mnt/SDCARD/mk20-no-agent").touch()
    assert p.wait(8) == 0 and not (dev.mock / "agent.pid").exists()


def test_agent_waits_and_rechecks_dependencies(dev, rel, agent):
    composite = dev.p("sys/kernel/config/usb_gadget/g1/configs/c.1/ecm.usb0")
    composite.rmdir()
    alternate = agent.with_name("test-kbd.sock")
    p = dev.start(rel / "sbin/mk20d", "agent", env={"MK20_AGENT_KBD_SOCK": str(alternate)})
    time.sleep(1.3)
    assert p.poll() is None and not calls(dev)
    composite.mkdir()
    time.sleep(1.3)
    assert not calls(dev)  # default path exists, override does not
    alternate.touch()
    assert wait(lambda: len(calls(dev)) == 1)
    alternate.unlink()
    assert wait(lambda: not (dev.mock / "agent.pid").exists())
    time.sleep(1.2)
    assert len(calls(dev)) == 1
    alternate.touch()
    assert wait(lambda: len(calls(dev)) == 2)
    composite.rmdir()
    assert wait(lambda: not (dev.mock / "agent.pid").exists())
    dev.p("tmp/mk20-agent-stop").touch()
    assert p.wait(8) == 0


def test_agent_backoff_and_healthy_reset(dev, rel, agent):
    # Real loop and crash launches, virtual monotonic uptime and sleeps. No production timing knobs.
    dev.helper("cut", '''import os,sys
from pathlib import Path
if sys.argv[-1]=='/proc/uptime':
 m=Path(os.environ['MOCK_DIR']); f=m/'clock'; n=int(f.read_text()) if f.exists() else 0
 # First 7 crashes are short; the 8th lasts exactly 60s, resetting the capped backoff.
 c=m/'calls'; count=len(c.read_text().splitlines()) if c.exists() else 0
 if count==8 and not (m/'healthy').exists(): n+=60; (m/'healthy').touch()
 f.write_text(str(n)); print(n)
else: os.execv('/usr/bin/cut',['cut']+sys.argv[1:])
''')
    dev.helper("sleep", '''import os,sys,time
from pathlib import Path
m=Path(os.environ['MOCK_DIR']); f=m/'clock'; n=int(f.read_text()) if f.exists() else 0
f.write_text(str(n+int(float(sys.argv[1])))); time.sleep(0.01)
''')
    (dev.mock / "crash").touch()
    p = dev.start(rel / "sbin/mk20d", "agent")
    log = dev.p("tmp/mk20/agent.log")
    assert wait(lambda: log.exists() and log.read_text().count("retry in") >= 9, 25)
    dev.p("tmp/mk20-agent-stop").touch()
    assert p.wait(8) == 0
    delays = [int(x.split("retry in ")[1][:-1]) for x in log.read_text().splitlines() if "retry in " in x]
    assert delays[:9] == [1, 2, 4, 8, 16, 30, 30, 1, 2]
    assert "mk20-agent exit rc=7" in log.read_text()
    assert dev.p("tmp/mk20/agent-crash.times").exists()


@pytest.mark.parametrize("mode", ["disabled", "missing", "stale", "crashing", "malformed"])
def test_agent_selftest_never_fails(dev, rel, g1, mode):
    # Agent failures remain nonfatal even on probation.
    dev.p("data/mk20/state").mkdir(parents=True, exist_ok=True)
    dev.p("data/mk20/state/pending").write_text("9.9.9\n")
    # Existing non-agent checks pass with stock KD; also assert same rc when another check fails.
    if mode != "disabled":
        conf(dev, "FEATURE_AGENT=1\n")
    status = dev.p("run/mk20/agent.status")
    status.parent.mkdir(parents=True)
    if mode in ("stale", "malformed"):
        status.write_text('{"state":"idle"}' if mode == "stale" else '{bad')
        if mode == "stale":
            os.utime(status, (1, 1))
    if mode == "crashing":
        (dev.mock / "pgrep.out").write_text("123\n")
        # uptime is host monotonic on Darwin, /proc uptime on Linux.
        now = int(float(Path("/proc/uptime").read_text().split()[0])) if Path("/proc/uptime").exists() else int(time.monotonic())
        dev.p("tmp/mk20/agent-crash.times").write_text(f"{now}\n" * 3)
    for other_failure in (False, True):
        if other_failure:
            (dev.mock / "wdt").write_text("offline")
        r = dev.run(rel / "sbin/mk20d", "selftest")
        lines = [x for x in r.stdout.splitlines() if "agent" in x]
        assert lines and all(x.startswith(("INFO agent", "WARN agent")) for x in lines)
        assert r.returncode == int(other_failure), r.stdout
        assert ("INFO agent disabled" in lines) == (mode == "disabled")
        if mode == "crashing":
            assert any("crash loop" in x for x in lines)


@pytest.mark.parametrize("status", [None, '{', '[]', '{"state":"idle",}', '{"allowed":true false}',
    '{"state":"idle","state":"acting"}', '{"state":"idle"} garbage',
    json.dumps(dict(allowed=True, state="idle", provider="openai", route="proxy", last_error_class="none",
                    api_key="SYNTHETIC_PRIVATE", token="SYNTHETIC_PRIVATE", password="SYNTHETIC_PRIVATE",
                    url="https://user:SYNTHETIC_PRIVATE@invalid.test")),
    '{"allowed":false,"state":"a\\\"b","provider":"https://user:SYNTHETIC_PRIVATE@host"}',
    '{"state":"idle","extra":{"token":"SYNTHETIC_PRIVATE"}}', 'x' * 5000])
def test_health_agent_whitelist(dev, rel, g1, status):
    conf(dev, "FEATURE_AGENT=1\n")
    p = dev.p("run/mk20/agent.status")
    p.parent.mkdir(parents=True)
    if status is not None:
        p.write_text(status)
    raw = dev.run(rel / "sbin/mk20d", "health").stdout
    h = json.loads(raw)
    a = h["agent"]
    assert set(a) == {"enabled", "allowed", "state", "provider", "route", "last_error_class", "running"}
    assert a["enabled"] is True and a["running"] is False
    assert a["allowed"] in (True, False, None)
    assert "SYNTHETIC_PRIVATE" not in raw
    assert not any(x in a for x in ("api_key", "token", "password", "url"))
    if status and '"provider": "openai"' in status:
        assert a == dict(enabled=True, allowed=True, state="idle", provider="openai", route="proxy",
                         last_error_class="none", running=False)
    elif status != '{"allowed":false,"state":"a\\\"b","provider":"https://user:SYNTHETIC_PRIVATE@host"}':
        assert a["allowed"] is None and a["state"] == "unknown"


def test_maint_trims_agent_log_in_place(dev, rel, g1):
    log = dev.p("tmp/mk20/agent.log")
    log.write_bytes(b"a" * 300000)
    inode = log.stat().st_ino
    p = dev.start(rel / "sbin/mk20d", "maint")
    assert wait(lambda: log.stat().st_size == 65536)
    assert log.stat().st_ino == inode  # O_APPEND child keeps writing to the same file
    p.terminate()
    assert p.wait(5) == -signal.SIGTERM


@pytest.mark.parametrize("enabled,binary", [(False, True), (True, False), (True, True)])
def test_boot_agent_launch_is_optional_and_detached(dev, rel, g1, enabled, binary):
    conf(dev, f"FEATURE_AGENT={int(enabled)}\nFEATURE_ROUTER=0\n")
    b = rel / "bin/mk20-agent"
    if binary:
        b.write_text("#!/bin/sh\nexit 0\n")
        b.chmod(0o755)
    else:
        b.unlink(missing_ok=True)
    ld = dev.p("tmp/mk20/rescue-loader.sh")
    ld.write_text('echo "$*" > "$MK20_ROOT/tmp/mk20/confirmed"\n')
    r, log = run_boot(dev, rel)
    assert r.returncode == 0
    assert ("INFO mk20d agent v=9.9.9 supervisor launched" in log) == (enabled and binary)
    assert dev.read("tmp/mk20/confirmed") == "confirm 9.9.9"
    assert not dev.p("mnt/SDCARD/cmd-reboot").exists()


def test_priority_helper_failure_still_executes(dev):
    dev.helper("renice", "import sys; sys.exit(1)\n")
    out = dev.run(FW / "release/share/mk20-agent-child.sh", "/bin/echo", "runtime-executed")
    assert out.returncode == 0 and "runtime-executed" in out.stdout
    assert "WARN agent priority: nice=10 unavailable or not verified" in out.stdout


def test_priority_helper_oom_write_and_verification(dev, tmp_path):
    # Simulate procfs only for this scoped wrapper on hosts without Linux procfs.
    wrapper = FW / "release/share/mk20-agent-child.sh"
    p = tmp_path / "child.sh"
    p.write_text(wrapper.read_text().replace('/proc/$$', '$MOCK_DIR/proc'))
    proc = dev.mock / "proc"
    proc.mkdir()
    (proc / "oom_score_adj").write_text("0\n")
    (proc / "stat").write_text("1 (ash) S " + "0 " * 15 + "10\n")
    dev.helper("renice", "import os,sys\nfrom pathlib import Path\nPath(os.environ['MOCK_DIR'],'renice.args').write_text(' '.join(sys.argv[1:]))\n")
    out = dev.run(p, "/bin/echo", "runtime-executed")
    assert out.returncode == 0 and "oom_score_adj=500 verified" in out.stdout
    assert (proc / "oom_score_adj").read_text().strip() == "500"
    assert "nice=10 verified" in out.stdout
    assert (dev.mock / "renice.args").read_text().split()[:2] == ["10", "-p"]


def tiny_release(dev, version):
    """Synthetic manifest/tar for exercising the real loader without GNU build tools."""
    import hashlib
    import tarfile
    d = dev.tmp / f"mk20-fw-{version}"
    for name, body in (("sbin/mk20d", "#!/bin/sh\nexit 0\n"), ("bin/mk20-agent", "synthetic agent payload\n")):
        f = d / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body)
        f.chmod(0o755)
    entries = "".join(f"{hashlib.sha256(f.read_bytes()).hexdigest()} 755 {f.relative_to(d)}\n"
                      for f in sorted(d.rglob("*")) if f.is_file())
    (d / "MANIFEST").write_text(f"version={version}\nkernels={dev.env['MK20_P4_SHA']}\n--\n" + entries)
    archive = dev.tmp / f"{version}.tar"
    with tarfile.open(archive, "w", format=tarfile.USTAR_FORMAT) as t:
        t.add(d, arcname=d.name)
    return archive, hashlib.sha256(archive.read_bytes()).hexdigest()


def test_loader_install_rollback_uninstall_preserves_agent_directories(dev):
    loader = FW / "rescue/loader.sh"
    # Directory/sentinel metadata only: no actual secret or key content is ever read.
    kept = [dev.p("data/mk20/" + name) for name in ("agent", "secrets")]
    for d in kept:
        d.mkdir()
        (d / "synthetic.keep").touch()
    metadata = [(d.stat().st_ino, (d / "synthetic.keep").stat().st_ino) for d in kept]
    for version in ("1.0.0", "1.0.1"):
        archive, digest = tiny_release(dev, version)
        r = dev.run(loader, "install", str(archive), digest)
        assert r.returncode == 0, r.stdout + r.stderr
        assert dev.run(loader, "confirm", version).returncode == 0
    assert dev.run(loader, "rollback").returncode == 0
    assert dev.run(loader, "uninstall").returncode == 0
    assert [(d.stat().st_ino, (d / "synthetic.keep").stat().st_ino) for d in kept] == metadata


@pytest.mark.parametrize("damage", ["missing", "mismatch"])
def test_agent_manifest_uses_regular_loader_check(dev, damage):
    loader = FW / "rescue/loader.sh"
    archive, digest = tiny_release(dev, "1.0.0")
    r = dev.run(loader, "install", str(archive), digest, "--no-switch")
    assert r.returncode == 0, r.stdout + r.stderr
    b = dev.p("data/mk20/releases/1.0.0/bin/mk20-agent")
    if damage == "missing":
        b.unlink()
    else:
        b.write_text("damaged\n")
    r = dev.run(loader, "verify", "1.0.0")
    assert r.returncode == 1 and ("missing or symlink bin/mk20-agent" if damage == "missing" else "sha mismatch bin/mk20-agent") in r.stdout


def test_boot_confirms_despite_agent_crashes(dev, rel, agent):
    conf(dev, "FEATURE_AGENT=1\nFEATURE_ROUTER=0\n")
    dev.p("data/mk20/state").mkdir(parents=True, exist_ok=True)
    dev.p("data/mk20/state/pending").write_text("9.9.9\n")
    (dev.mock / "crash").touch()
    (dev.mock / "netstat.out").write_text("tcp 0 0 172.30.250.2:17890 0.0.0.0:* LISTEN\n"
                                          "tcp 0 0 169.254.7.2:17890 0.0.0.0:* LISTEN\n")
    dev.p("tmp/mk20/rescue-loader.sh").write_text('echo "$*" > "$MK20_ROOT/tmp/mk20/confirmed"\n')
    r, log = run_boot(dev, rel)
    assert r.returncode == 0 and dev.read("tmp/mk20/confirmed") == "confirm 9.9.9", log
    assert wait(lambda: len(calls(dev)) >= 3, 15)
    assert dev.run(rel / "sbin/mk20d", "selftest").returncode == 0
    assert dev.read("tmp/mk20/gadget.args") == "--boot"
    assert not dev.p("mnt/SDCARD/cmd-reboot").exists()
