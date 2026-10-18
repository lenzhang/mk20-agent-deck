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


def _kill_strays(tmp):
    """Kill every process still running a script from this test's tmp dir (e.g. the detached loader-deadline.sh
    poller, which loops forever under the fixed test uptime). Test artifacts only: matched by the tmp path."""
    import signal
    key = str(tmp).encode()
    for d in pathlib.Path("/proc").glob("[0-9]*"):
        try:
            if key in (d / "cmdline").read_bytes() and int(d.name) != os.getpid():
                os.kill(int(d.name), signal.SIGKILL)
        except (OSError, ValueError):
            pass


@pytest.fixture
def dev(tmp_path):
    yield Dev(tmp_path)
    _kill_strays(tmp_path)


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


# ---- p0 (mk20deck) fixtures ----
import json
import os
import sqlite3
import sys
import time



sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FIX = os.path.join(os.path.dirname(__file__), "fixtures")

# Sanitized synthetic thread rows mirroring the real state_5.sqlite schema
# (column subset observed 2026-10-17 read-only on the Mac; titles/cwds invented).
THREADS = [
    # id, source, originator, title, name, cwd, effort, recency, lifecycle, archived
    ("01a10000-0000-7000-8000-000000000001", "vscode", "Codex Desktop", "Fix flaky test", None, "/Users/dev/proj/a", "xhigh", 9000, "running", 0),
    ("01a10000-0000-7000-8000-000000000002", "vscode", "codex_work_desktop", "Write docs", "文档", "/Users/dev/proj/b", "high", 8000, "complete", 0),
    ("01a10000-0000-7000-8000-000000000003", "vscode", None, "Refactor", None, "/Users/dev/proj/c", "medium", 7000, "complete", 0),
    ("01a10000-0000-7000-8000-000000000004", "vscode", "Codex Desktop", "Aborted", None, "/Users/dev/proj/d", "low", 6000, "aborted", 0),
    ("01a10000-0000-7000-8000-000000000005", "vscode", "Codex Desktop", "Stale run", None, "/Users/dev/proj/e", "high", 5000, "stale", 0),
    ("01a10000-0000-7000-8000-000000000006", "vscode", "Codex Desktop", "Failed", None, "/Users/dev/proj/f", "high", 4000, "error", 0),
    ("01a10000-0000-7000-8000-000000000007", "vscode", "codex-tui", "TUI thread", None, "/Users/dev/proj/g", "high", 3500, "complete", 0),
    ("01a10000-0000-7000-8000-000000000008", "exec", "codex_exec", "exec thread", None, "/Users/dev/proj/h", "high", 3400, "complete", 0),
    ("01a10000-0000-7000-8000-000000000009", "vscode", "Codex Desktop", "Archived", None, "/Users/dev/proj/i", "high", 3300, "complete", 1),
    ("01a10000-0000-7000-8000-00000000000a", "vscode", "Codex Desktop", "No events", None, "relative/path", None, 3000, "none", 0),
]

SCHEMA = """CREATE TABLE threads (
    id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
    source TEXT NOT NULL, model_provider TEXT NOT NULL, cwd TEXT NOT NULL, title TEXT NOT NULL,
    sandbox_policy TEXT NOT NULL, approval_mode TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0,
    model TEXT, reasoning_effort TEXT, preview TEXT NOT NULL DEFAULT '', recency_at_ms INTEGER NOT NULL DEFAULT 0,
    name TEXT, is_pinned INTEGER NOT NULL DEFAULT 0, originator TEXT)"""


def _ev(ts, typ, **kw):
    return json.dumps({"timestamp": ts, "type": "event_msg", "payload": {"type": typ, **kw}})


def _noise(ts):
    return json.dumps({"timestamp": ts, "type": "response_item", "payload": {"type": "reasoning", "summary": []}})


def write_rollout(path, kind):
    lines = [json.dumps({"timestamp": "2026-10-17T00:00:00Z", "type": "session_meta",
                         "payload": {"id": "x", "originator": "Codex Desktop"}})]
    if kind in ("running", "stale"):
        lines += [_ev("t1", "task_started", turn_id="t-1"), _noise("t2")]
    elif kind == "complete":
        lines += [_ev("t1", "task_started", turn_id="t-1"), _noise("t2"),
                  _ev("t3", "task_complete", turn_id="t-1", last_agent_message="done")]
    elif kind == "aborted":
        lines += [_ev("t1", "task_started", turn_id="t-1"), _ev("t3", "turn_aborted", reason="interrupted")]
    elif kind == "error":
        lines += [_ev("t1", "task_started", turn_id="t-1"), _ev("t3", "error", message="stream disconnected")]
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


@pytest.fixture
def codex_home(tmp_path):
    home = tmp_path / "codex"
    sess = home / "sessions" / "2026" / "10" / "17"
    sess.mkdir(parents=True)
    con = sqlite3.connect(home / "state_5.sqlite")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(SCHEMA)
    now = time.time()
    for tid, src, orig, title, name, cwd, eff, rec, kind, arch in THREADS:
        rp = sess / f"rollout-2026-10-17T00-00-00-{tid}.jsonl"
        if kind != "none":
            write_rollout(rp, kind)
            if kind == "stale":
                os.utime(rp, (now - 3600, now - 3600))
        con.execute("INSERT INTO threads (id, rollout_path, created_at, updated_at, source, model_provider, cwd, "
                    "title, sandbox_policy, approval_mode, archived, reasoning_effort, preview, recency_at_ms, name, "
                    "originator) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (tid, str(rp), 1, 1, src, "openai", cwd, title, "{}", "on-request", arch, eff,
                     f"preview of {title}", rec, name, orig))
    con.commit()
    con.close()
    gs = {"electron-thread-read-state-v1": {"version": 1, "unreadByIdentity": {
        "a" * 64: {"local:" + "b" * 64: [], "durable:" + "c" * 64: [
            "01a10000-0000-7000-8000-000000000002",  # complete + unread -> done_unread
            "01a10000-0000-7000-8000-000000000001",  # running + unread -> still running
            "99999999-0000-7000-8000-000000000000",  # remote thread not in local table -> ignored
        ]}}}}
    (home / ".codex-global-state.json").write_text(json.dumps(gs))
    return home
