"""share/flapmon.sh (1.1.3): flaps roll back exactly once; long host suspend (Mac sleep) does not count."""
import os
import subprocess
import time
from pathlib import Path

FM = Path(__file__).resolve().parents[1] / "firmware/release/share/flapmon.sh"


def put(p: Path, text: str) -> None:
    """Atomic write so flapmon never reads a truncated (empty) state."""
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, p)


def start(dev, tmp_path, window=60, drops=5, cmd=None):
    st, log, stop, mark, clock = (tmp_path / n for n in ("state", "fm.log", "stop", "rolled", "clock"))
    put(st, "configured\n"); put(clock, "100\n")
    env = dict(dev.env, MK20_FLAP_POLL="0.05", MK20_BUSY=str(tmp_path / "busy"),
               MK20_FLAP_LOCK=str(tmp_path / "fm.lock"), MK20_FLAP_READY=str(tmp_path / "ready"), MK20_UPTIME_CMD=f"cat {clock}")
    cmd = cmd or ["sh", "-c", f"echo 'roll back' >> '{mark}'"]
    p = subprocess.Popen(["sh", str(FM), str(st), str(window), str(drops), str(log), str(stop), "--", *cmd], env=env)
    time.sleep(0.4)
    return p, st, log, stop, mark, clock


def run_fm(dev, tmp_path, states, **kw):
    p, st, log, stop, mark, clock = start(dev, tmp_path, **kw)
    for s, t in states:
        put(clock, f"{t}\n"); put(st, s + "\n"); time.sleep(0.25)
        if p.poll() is not None:
            break
    put(stop, ""); p.wait(timeout=5)
    n = len(mark.read_text().splitlines()) if mark.exists() else 0
    return n, (log.read_text() if log.exists() else ""), (tmp_path / "busy").exists()


def test_five_drops_in_window_roll_back_once(dev, tmp_path):
    seq = []
    for i in range(7):
        seq += [("default", 101 + i), ("configured", 101 + i)]
    n, log, busy = run_fm(dev, tmp_path, seq, cmd=["sh", "-c",
                          f"[ -f {tmp_path}/busy ] && echo busy-during > {tmp_path}/bd; echo 'roll back' >> '{tmp_path}/rolled'"])
    assert n == 1 and not busy and log.count("FLAPPING -> rollback") == 1 and "5 in 60s" in log
    assert (tmp_path / "bd").exists()   # gadget.busy held while the rollback ran, cleared after


def test_drops_outside_window_do_not_roll_back(dev, tmp_path):
    seq = []
    for i in range(6):
        seq += [("not attached", 100 + 30 * i), ("configured", 100 + 30 * i)]
    n, log, _ = run_fm(dev, tmp_path, seq)
    assert n == 0 and "3 in 60s" in log and "4 in 60s" not in log


def test_long_host_suspend_is_not_a_flap(dev, tmp_path):
    seq = []
    for i in range(8):   # Mac sleep/wake x8, each >= 5 s, incl. a bus reset on resume
        t = 100 + 6 * i
        seq += [("suspended", t), ("default", t + 5), ("configured", t + 5)] if i % 2 else \
               [("suspended", t), ("configured", t + 5)]
    n, log, busy = run_fm(dev, tmp_path, seq, window=600)
    assert n == 0 and not busy and "link drop" not in log
    assert log.count("host suspend") == 8 and log.count("host resume after 5s") == 4
    assert log.count("'default' after 5s suspend") == 4


def test_short_suspend_then_reset_counts(dev, tmp_path):
    seq = []
    for i in range(5):   # bus glitch the poll caught as a 1-s idle: real drops
        seq += [("suspended", 101 + 2 * i), ("default", 102 + 2 * i), ("configured", 102 + 2 * i)]
    n, log, _ = run_fm(dev, tmp_path, seq)
    assert n == 1 and "link drop (default)" in log


def test_unplug_after_long_suspend_not_counted(dev, tmp_path):
    seq = [("suspended", 100), ("not attached", 160)]
    n, log, _ = run_fm(dev, tmp_path, seq)
    assert n == 0 and "'not attached' after 60s suspend" in log


def test_failed_rollback_retries_then_flags_stock_and_reboots(dev, tmp_path, monkeypatch):
    monkeypatch.setattr(dev, "env", dict(dev.env, MK20_FLAP_TRIES="2", MK20_FLAP_RETRY_S="0",
                                         MK20_FLAP_FLAG=str(tmp_path / "nocomp"), MK20_FLAP_REBOOT=str(tmp_path / "reboot")))
    n, log, busy = run_fm(dev, tmp_path, [("default", 101), ("configured", 101), ("default", 102)],
                          drops=2, cmd=["sh", "-c", f"echo x >> {tmp_path}/tries; exit 7"])
    assert "rollback attempt 1 failed rc=7" in log and "rollback attempt 2 failed rc=7" in log and not busy
    assert len((tmp_path / "tries").read_text().split()) == 2
    assert (tmp_path / "nocomp").exists() and (tmp_path / "reboot").exists()


def test_stopfile_ends_monitor(dev, tmp_path):
    n, _, _ = run_fm(dev, tmp_path, [])
    assert n == 0


def test_second_instance_refused(dev, tmp_path):
    p, st, log, stop, mark, clock = start(dev, tmp_path)
    env = dict(dev.env, MK20_FLAP_LOCK=str(tmp_path / "fm.lock"), MK20_FLAP_POLL="0.05",
               MK20_FLAP_READY=str(tmp_path / "ready2"))
    r = subprocess.run(["sh", str(FM), str(st), "60", "5", str(log), str(stop), "--", "true"], env=env, timeout=15)
    put(stop, ""); p.wait(timeout=5)
    assert r.returncode == 3 and "another monitor holds" in log.read_text()
    assert (tmp_path / "ready").exists() and not (tmp_path / "ready2").exists()


def test_bad_args_exit_2(dev, tmp_path):
    for args in (["s", "x", "5", "l", "st", "--", "true"], ["s", "60", "5", "l", "st", "true", "x"], ["s"]):
        r = subprocess.run(["sh", str(FM), *args], env=dev.env, capture_output=True, timeout=5)
        assert r.returncode == 2


# ---- r5 #6 ----
def test_refused_rollback_keeps_other_owners_busy_marker(dev, tmp_path, monkeypatch):
    monkeypatch.setattr(dev, "env", dict(dev.env, MK20_FLAP_TRIES="1", MK20_FLAP_RETRY_S="0",
                                         MK20_FLAP_FLAG=str(tmp_path / "nocomp"), MK20_FLAP_REBOOT=str(tmp_path / "reboot")))
    n, log, busy = run_fm(dev, tmp_path, [("default", 101), ("configured", 101), ("default", 102)],
                          drops=2, cmd=["sh", "-c", f"touch {tmp_path}/busy; exit 4"])   # owner set it; we were refused
    assert "failed rc=4" in log and busy


def test_preexisting_busy_marker_not_removed(dev, tmp_path):
    (tmp_path / "busy").write_text("")
    n, log, busy = run_fm(dev, tmp_path, [("default", 101), ("configured", 101), ("default", 102)], drops=2)
    assert n == 1 and busy   # not ours: the owner (gadget.sh, under the mutex) clears it


def test_stale_rollback_rc5_exits_without_retry_or_escalation(dev, tmp_path, monkeypatch):
    monkeypatch.setattr(dev, "env", dict(dev.env, MK20_FLAP_TRIES="3", MK20_FLAP_RETRY_S="0",
                                         MK20_FLAP_FLAG=str(tmp_path / "nocomp"), MK20_FLAP_REBOOT=str(tmp_path / "reboot")))
    n, log, busy = run_fm(dev, tmp_path, [("default", 101), ("configured", 101), ("default", 102)],
                          drops=2, cmd=["sh", "-c", f"echo x >> {tmp_path}/tries; exit 5"])
    assert "stale (rc 5" in log and not busy and len((tmp_path / "tries").read_text().split()) == 1
    assert not (tmp_path / "nocomp").exists() and not (tmp_path / "reboot").exists()


def test_refused_then_stale_clears_own_marker(dev, tmp_path, monkeypatch):
    """GPT r7 MAJOR: ownership is sticky across retries (rc 4 -> rc 5 must not leak flapmon's marker)."""
    monkeypatch.setattr(dev, "env", dict(dev.env, MK20_FLAP_TRIES="3", MK20_FLAP_RETRY_S="0",
                                         MK20_FLAP_FLAG=str(tmp_path / "nocomp"), MK20_FLAP_REBOOT=str(tmp_path / "reboot")))
    cmd = f"echo x >> {tmp_path}/tries; [ $(wc -l < {tmp_path}/tries) -ge 2 ] && exit 5; exit 4"
    n, log, busy = run_fm(dev, tmp_path, [("default", 101), ("configured", 101), ("default", 102)],
                          drops=2, cmd=["sh", "-c", cmd])
    assert "failed rc=4" in log and "stale (rc 5" in log and not busy
    assert not (tmp_path / "nocomp").exists()
