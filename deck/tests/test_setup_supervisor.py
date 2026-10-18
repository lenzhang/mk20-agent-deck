"""gadget.sh setup supervisor (1.1.4 r6, GPT r5 BLOCKER): the mutating setup runs in an owner-local child bounded by
MK20_SETUP_TIMEOUT; on timeout the owner kills+reaps its own setup tree, runs a cleanup bounded by MK20_RB_TIMEOUT
(also killed+reaped if it hangs), clears gadget.busy and only then releases the fd-7 transition mutex.
The rollback block (helpers) and the supervisor are cut out of gadget.sh by their explicit markers and run under
busybox ash with stubbed setup/cleanup that call hung "setusbconfig" / "add-adb" / procd service scripts."""
import subprocess
import time
from pathlib import Path

import pytest

G = (Path(__file__).resolve().parents[1] / "firmware/release/share/gadget.sh").read_text()


def _between(start, end):
    a = G.index(start)
    return G[a:G.index(end, a)]


RB = _between("# >>> rollback-block", "# <<< rollback-block")
SUP = G[G.index("supervise_setup() {"):]
SUP = SUP[:SUP.index("# <<< setup-supervisor\n}\n") + len("# <<< setup-supervisor\n}\n")]

HUNG = {
    "setusbconfig": "sh $T/hung-setusbconfig.sh serial",
    "service": "sh $T/qt_app2 start",
    "none": ":",
}


def harness(t, setup="none", setup_rc=0, cleanup_hang=False, unkillable=False):
    (t / "c").mkdir(exist_ok=True)
    (t / "mk20").mkdir(exist_ok=True)
    for n in ("hung-setusbconfig.sh", "qt_app2", "hung-add-adb.sh"):
        (t / n).write_text(f'echo $$ > {t}/pid.{n}\nexec sleep 600\n')
    body = (RB + SUP).replace("/tmp/mk20", str(t / "mk20")).replace("/dev/ttyGS0", "/dev/null") \
        .replace("/mnt/SDCARD/gadget-ecm-hid.status", str(t / "status"))
    add_adb = "sh $T/hung-add-adb.sh;" if cleanup_hang else ""
    script = f'''T={t}; C=$T/c; OK=0
log() {{ echo "$*" >> $T/log; }}
udc_now() {{ echo udc; }}
pgrep() {{ return 0; }}
cleanup() {{ [ "$OK" = 1 ] && return; touch $T/mk20/gadget.busy; echo "$$" >> $T/cleanups; {add_adb} touch $T/c/acm.usb0; rm -f $T/mk20/gadget.busy; }}
setup_main() {{
  trap cleanup EXIT; trap 'exit 1' INT TERM
  echo started >> $T/setup; {HUNG[setup]}
  [ {setup_rc} = 0 ] && OK=1; exit {setup_rc}
}}
{body}
{"kill_tree() { :; }   # simulate an unkillable (D-state) tree" if unkillable else ""}
supervise_setup
echo "fell-through"; exit 9
'''
    (t / "g.sh").write_text(script)
    return t / "g.sh"


def ENV(t, **kw):
    return dict({"MK20_FLAP_FLAG": str(t / "flag"), "MK20_FLAP_REBOOT": str(t / "reboot")}, **kw)


def _gone(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().split()[2] == "Z"
    except FileNotFoundError:
        return True


def _pid(t, n):
    f = t / f"pid.{n}"
    return int(f.read_text()) if f.exists() else None


def _mutex_free(dev, t):
    r = subprocess.run(["sh", "-c", f"exec 7>{t}/mk20/transition.lock; flock -n 7"], env=dev.env)
    return r.returncode == 0


def _cleanups(t):
    f = t / "cleanups"
    return len(f.read_text().split()) if f.exists() else 0


def test_normal_setup_rc_passthrough(dev, tmp_path):
    s = harness(tmp_path)
    r = dev.run(s, env=ENV(tmp_path, **{"MK20_SETUP_TIMEOUT": "20"}))
    assert r.returncode == 0 and "fell-through" not in r.stdout
    assert _cleanups(tmp_path) == 0 and _mutex_free(dev, tmp_path)


def test_failed_setup_runs_own_failsafe_once(dev, tmp_path):
    s = harness(tmp_path, setup_rc=1)
    r = dev.run(s, env=ENV(tmp_path, **{"MK20_SETUP_TIMEOUT": "20"}))
    assert r.returncode == 1 and _cleanups(tmp_path) == 1
    assert "SETUP TIMEOUT" not in (tmp_path / "log").read_text() if (tmp_path / "log").exists() else True


@pytest.mark.parametrize("which,name", [("setusbconfig", "hung-setusbconfig.sh"), ("service", "qt_app2")])
def test_hung_setup_step_killed_cleanup_bounded_mutex_released(dev, tmp_path, which, name):
    s = harness(tmp_path, setup=which)
    t0 = time.time()
    r = dev.run(s, env=ENV(tmp_path, **{"MK20_SETUP_TIMEOUT": "2", "MK20_RB_TIMEOUT": "2"}))
    assert r.returncode == 1 and time.time() - t0 < 30
    log = (tmp_path / "log").read_text()
    assert "SETUP TIMEOUT" in log and "killed+reaped own setup tree" in log
    time.sleep(0.3)
    assert _gone(_pid(tmp_path, name))
    assert _cleanups(tmp_path) == 1            # the owner's bounded cleanup ran (the killed child's trap could not)
    assert not (tmp_path / "mk20/gadget.busy").exists()
    assert (tmp_path / "status").read_text().strip() == "setup-timeout"
    assert _mutex_free(dev, tmp_path) and not (tmp_path / "flag").exists()


def test_hung_add_adb_in_both_cleanups_still_bounded(dev, tmp_path):
    """setup fails, its own failsafe hangs in add-adb; the owner's cleanup hangs again: both trees killed."""
    s = harness(tmp_path, setup_rc=1, cleanup_hang=True)
    t0 = time.time()
    r = dev.run(s, env=ENV(tmp_path, **{"MK20_SETUP_TIMEOUT": "2", "MK20_RB_TIMEOUT": "2"}))
    assert r.returncode == 1 and time.time() - t0 < 60   # incl. the 30 s stock-settle wait before escalating
    log = (tmp_path / "log").read_text()
    assert "SETUP TIMEOUT" in log and "killed+reaped own cleanup tree" in log
    time.sleep(0.3)
    assert _gone(_pid(tmp_path, "hung-add-adb.sh"))
    assert _cleanups(tmp_path) == 2
    assert not (tmp_path / "mk20/gadget.busy").exists() and _mutex_free(dev, tmp_path)
    # stock never reached -> escalated (persistent stock flag + watcher hard reset)
    assert (tmp_path / "flag").exists() and (tmp_path / "reboot").exists() and "ESCALATE" in log or \
        ((tmp_path / "flag").exists() and "ESCALATE" in (tmp_path / "log").read_text())


def test_mutex_held_during_setup_rollback_refused(dev, tmp_path):
    s = harness(tmp_path, setup="setusbconfig")
    p = subprocess.Popen(["sh", str(s)], env=dict(dev.env, **ENV(tmp_path, MK20_SETUP_TIMEOUT="4", MK20_RB_TIMEOUT="1")))
    try:
        for _ in range(40):
            if _pid(tmp_path, "hung-setusbconfig.sh"):
                break
            time.sleep(0.1)
        assert not _mutex_free(dev, tmp_path)
        assert p.wait(timeout=30) == 1 and _mutex_free(dev, tmp_path)
    finally:
        p.kill()


@pytest.mark.parametrize("bad", ["abc", "-1", "99999999999999999999"])
def test_invalid_setup_knobs_do_not_break_supervisor(dev, tmp_path, bad):
    s = harness(tmp_path)
    r = dev.run(s, env=ENV(tmp_path, **{"MK20_SETUP_TIMEOUT": bad, "MK20_SETUP_LOCK_TRIES": bad, "MK20_RB_TIMEOUT": bad}))
    assert r.returncode == 0 and "arithmetic" not in r.stderr, r.stderr


def test_unkillable_setup_tree_escalates_rc3(dev, tmp_path):
    s = harness(tmp_path, setup="setusbconfig", unkillable=True)
    # DEVNULL: the deliberately unkilled hung child keeps any captured pipe open
    rc = subprocess.run(["sh", str(s)], env=dict(dev.env, **ENV(tmp_path, MK20_SETUP_TIMEOUT="1", MK20_RB_TIMEOUT="1")),
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90).returncode
    try:
        assert rc == 3
        assert (tmp_path / "flag").exists() and (tmp_path / "reboot").exists()
        assert (tmp_path / "status").read_text().strip() == "setup-STUCK"
    finally:
        p = _pid(tmp_path, "hung-setusbconfig.sh")
        p and subprocess.run(["kill", "-9", str(p)])


def test_failed_setup_unverified_escalates_and_keeps_rc1(dev, tmp_path):
    """Opus r8 BLOCKER: escalate()'s own bounded_wait must not overwrite the setup rc (was: exit 0 after a failed setup)."""
    s = harness(tmp_path, setup_rc=1)
    s.write_text(s.read_text().replace("touch $T/c/acm.usb0;", ":;"))   # failsafe never reaches stock
    r = dev.run(s, env=ENV(tmp_path, **{"MK20_SETUP_TIMEOUT": "60"}), timeout=120)
    assert r.returncode == 1 and "fell-through" not in r.stdout
    assert (tmp_path / "flag").exists() and (tmp_path / "reboot").exists()
    assert (tmp_path / "status").read_text().strip() == "setup-failsafe-NOT-verified"   # written last, on success
    assert not (tmp_path / "mk20/gadget.busy").exists() and _mutex_free(dev, tmp_path)


def test_escalate_preserves_bw_rc_and_writes_status_last():
    e = G[G.index("escalate() {"):]
    e = e[:e.index("\n}\n")]
    assert "ebrc=${BW_RC:-1}" in e and "BW_RC=$ebrc" in e
    assert e.index('touch "$EOKF"') < e.index("sync")            # success marked before sync
    assert e.index("rb_status") > e.index("bounded_wait")        # status only after the flags are known
