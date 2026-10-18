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


def harness(tmp_path, cleanup_makes_stock=True, cleanup_s=1, hang=False, unkillable=False, stub=""):
    t = tmp_path
    (t / "c").mkdir(exist_ok=True)
    stub = "kill_tree() { :; }\n" if unkillable else stub
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


def cenv(dev, **kw):
    """env of a self-retrying caller (flapmon / confirm waiter / mk20d): one attempt, rc 1 when not verified."""
    return dict(dev.env, MK20_RB_CALLER_RETRIES="1", **kw)


def run(dev, script, wait=True):
    p = subprocess.Popen(["sh", str(script), "--rollback-now"], env=cenv(dev), stdout=subprocess.PIPE, text=True)
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
    env = cenv(dev, MK20_RB_TIMEOUT="1")
    t0 = time.time()
    rc = subprocess.run(["sh", str(s), "--rollback-now"], env=env, timeout=60).returncode
    assert rc == 1 and time.time() - t0 < 45
    assert "killing own child tree" in (tmp_path / "log").read_text()
    sl = int((tmp_path / "sleeper").read_text())
    time.sleep(0.3)
    assert _gone(sl)
    # mutex released: the next caller gets it at once
    (tmp_path / "sleeper").unlink()
    env2 = cenv(dev, MK20_RB_TIMEOUT="1", MK20_RB_LOCK_TRIES="2")
    assert subprocess.run(["sh", str(s), "--rollback-now"], env=env2, timeout=60).returncode == 1
    assert cleanups(tmp_path) == 2


def test_busy_mutex_refused_never_kills_holder(dev, tmp_path):
    s = harness(tmp_path, cleanup_s=0)
    (tmp_path / "mk20").mkdir(exist_ok=True)
    holder = subprocess.Popen(["sh", "-c", f"exec 7>{tmp_path}/mk20/transition.lock; flock -x 7; sleep 30"], env=dev.env)
    try:
        time.sleep(0.5)
        env = cenv(dev, MK20_RB_LOCK_TRIES="4")
        assert subprocess.run(["sh", str(s), "--rollback-now"], env=env, timeout=60).returncode == 4   # refused
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
    env = cenv(dev, MK20_RB_TIMEOUT="1")
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
    env = cenv(dev, MK20_RB_TIMEOUT=bad, MK20_RB_LOCK_TRIES=bad, MK20_SETUP_LOCK_TRIES=bad,
               MK20_SETUP_TIMEOUT=bad)
    r = subprocess.run(["sh", str(s), "--rollback-now"], env=env, timeout=60, capture_output=True, text=True)
    assert r.returncode == 0 and cleanups(tmp_path) == 1, r.stderr
    assert "arithmetic" not in r.stderr and "fell-through" not in r.stdout


@pytest.mark.parametrize("val,exp", [("abc", "330"), ("0", "1"), ("08", "8"), ("000", "1"), ("5", "5"),
                                     ("901", "900"), ("12345678901234567890", "900")])
def test_uint_cap(dev, tmp_path, val, exp):
    f = G[G.index("uint_cap() {"):]
    f = f[:f.index("\n}\n") + 3]
    r = subprocess.run(["sh", "-c", f + f'uint_cap "{val}" 330 1 900'], env=dev.env, capture_output=True, text=True)
    assert r.stdout.strip() == exp, r.stderr


def test_unkillable_cleanup_escalates_rc3_no_unbounded_wait(dev, tmp_path):
    s = harness(tmp_path, cleanup_makes_stock=False, cleanup_s=0, hang=True, unkillable=True)
    env = cenv(dev, MK20_RB_TIMEOUT="1")
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


# ---- r5 #7: generation token ----
def test_stale_generation_is_ignored_without_cleanup(dev, tmp_path):
    s = harness(tmp_path, cleanup_makes_stock=False, cleanup_s=0)
    (tmp_path / "mk20").mkdir(exist_ok=True)
    (tmp_path / "mk20/composite.gen").write_text("200.5-42\n")   # a newer composite is up
    (tmp_path / "mk20/gadget.busy").write_text("")
    env = cenv(dev, MK20_GEN="100.1-7")
    assert subprocess.run(["sh", str(s), "--rollback-now"], env=env, timeout=60).returncode == 5
    assert cleanups(tmp_path) == 0 and "stale request" in (tmp_path / "log").read_text()
    assert (tmp_path / "mk20/gadget.busy").exists()   # nothing touched


def test_matching_generation_rolls_back(dev, tmp_path):
    s = harness(tmp_path, cleanup_s=0)
    (tmp_path / "mk20").mkdir(exist_ok=True)
    (tmp_path / "mk20/composite.gen").write_text("100.1-7\n")
    env = cenv(dev, MK20_GEN="100.1-7")
    assert subprocess.run(["sh", str(s), "--rollback-now"], env=env, timeout=60).returncode == 0 and cleanups(tmp_path) == 1


def test_no_generation_is_unconditional(dev, tmp_path):
    s = harness(tmp_path, cleanup_s=0)
    (tmp_path / "mk20").mkdir(exist_ok=True)
    (tmp_path / "mk20/composite.gen").write_text("100.1-7\n")
    assert run(dev, s) == 0 and cleanups(tmp_path) == 1   # mk20d / manual rollback targets whatever is current


def test_generation_written_under_mutex_and_passed_to_monitors():
    sm = G[G.index("setup_main() {"):]
    assert 'echo "$GEN" > "$GENF.t" && mv -f "$GENF.t" "$GENF"' in sm
    assert 'MK20_GEN="$GEN"' in sm and sm.count("MK20_GEN=$GEN ") == 2   # flapmon + both confirm-waiter attempts
    assert "[ \\$rc = 5 ] && exit 0" in sm


# ---- r5 #5: rollback bound covers a healthy cleanup incl. add-adb ----
def _default(name):
    import re as _re
    return int(_re.search(r'%s:-\}" (\d+) ' % name, G).group(1))


def test_rb_timeout_default_covers_healthy_cleanup():
    rbt, sut, tries = _default("MK20_RB_TIMEOUT"), _default("MK20_SETUP_TIMEOUT"), _default("MK20_RB_LOCK_TRIES")
    # healthy budget from cleanup(): kd_stop 32 + umount 10 + kd_start 23 + readd_adb (60 + 5 + 7*4 + 90)
    assert rbt >= 330 and rbt >= 32 + 10 + 23 + 60 + 5 + 28 + 90
    assert sut >= 60 + rbt or sut >= 450
    assert tries * 0.25 >= sut + rbt + 30 + 2 * 10 + 20 + 60   # outlasts the worst supervisor hold + overhead (r8 #4)


# ---- r5 #9 / #4: fd 7 + detach ----
def test_every_service_and_helper_call_in_cleanup_closes_fd7():
    import re as _re
    body = G[G.index("kd_stop() {"):G.index("# rollback runs before any preflight")]
    for line in body.splitlines():
        code = line.split(" #")[0]
        if code.lstrip().startswith("#"):
            continue
        if _re.search(r"\$KDSVC (start|stop)|setusbconfig serial|mk20-add-adb\.sh >>|appLunch\.sh <", code):
            assert "7>&-" in code, line


def test_daemons_started_by_cleanup_are_detached():
    body = G[G.index("kd_start() {"):G.index("OK=0\ncleanup()")]
    assert "( ( trap '' HUP INT; exec /data/appLunch.sh" in body
    a = (Path(__file__).resolve().parents[1] / "firmware/release/share/mk20-add-adb.sh").read_text()
    assert "( /bin/adbd </dev/null >/tmp/adbd.log 2>&1 7>&- & )" in a


def test_detached_child_survives_kill_tree(dev, tmp_path):
    """kill_tree walks PPid; a double-forked daemon is reparented to init and survives a killed cleanup tree."""
    kt = G[G.index("kids() {"):G.index("# uint_cap")]
    sh = tmp_path / "k.sh"
    sh.write_text(kt + f"""( ( exec sleep 30 ) & echo $! > {tmp_path}/det ) ; ( sleep 30 & echo $! > {tmp_path}/att; wait ) &
CP=$!; sleep 0.5; kill_tree $CP; sleep 0.3
""")
    subprocess.run(["sh", str(sh)], env=dev.env, timeout=30)
    det, att = (int((tmp_path / n).read_text()) for n in ("det", "att"))
    try:
        assert not _gone(det) and _gone(att)
    finally:
        subprocess.run(["kill", "-9", str(det)])


# ---- GPT r6: whole-tree liveness, queued/manual retry+escalate, bounded escalate, rc-3 clears busy ----
def test_unkillable_grandchild_escalates_rc3(dev, tmp_path):
    """kill_tree kills only the root: the snapshotted grandchild (holding fd 7) survives -> rc 2 -> escalate rc 3."""
    stub = ('kill_tree() { echo "$1:$(pstart "$1")"; for k in $(kids "$1"); do echo "$k:$(pstart "$k")"; done; '
            'kill -9 "$1"; }\n')
    s = harness(tmp_path, cleanup_makes_stock=False, cleanup_s=0, hang=True, stub=stub)
    (tmp_path / "mk20").mkdir(exist_ok=True)
    (tmp_path / "mk20/gadget.busy").write_text("")
    t0 = time.time()
    rc = subprocess.run(["sh", str(s), "--rollback-now"], env=cenv(dev, MK20_RB_TIMEOUT="1"), timeout=60).returncode
    try:
        assert rc == 3 and time.time() - t0 < 30
        assert (tmp_path / "flag").exists() and (tmp_path / "reboot").exists()
        assert "UNKILLABLE" in (tmp_path / "log").read_text()
        assert not (tmp_path / "mk20/gadget.busy").exists()   # rc-3 path clears the marker
    finally:
        subprocess.run(["kill", "-9", (tmp_path / "sleeper").read_text().strip()])


def test_killable_tree_is_still_rc1(dev, tmp_path):
    s = harness(tmp_path, cleanup_makes_stock=False, cleanup_s=0, hang=True)
    assert subprocess.run(["sh", str(s), "--rollback-now"], env=cenv(dev, MK20_RB_TIMEOUT="1"),
                          timeout=60).returncode == 1
    assert not (tmp_path / "flag").exists()


def test_manual_rollback_retries_once_then_escalates(dev, tmp_path):
    s = harness(tmp_path, cleanup_makes_stock=False, cleanup_s=0)
    (tmp_path / "mk20").mkdir(exist_ok=True)
    (tmp_path / "mk20/gadget.busy").write_text("")
    rc = subprocess.run(["sh", str(s), "--rollback-now"], env=dict(dev.env, MK20_RB_RETRY_S="0"), timeout=120).returncode
    assert rc == 3 and cleanups(tmp_path) == 2
    assert (tmp_path / "flag").exists() and (tmp_path / "reboot").exists()
    assert (tmp_path / "status").read_text().strip() == "rollback-NOT-verified-ESCALATED"
    assert not (tmp_path / "mk20/gadget.busy").exists()


def test_manual_rollback_second_attempt_succeeds(dev, tmp_path):
    s = harness(tmp_path, cleanup_s=0)
    s.write_text(s.read_text().replace("touch $T/c/acm.usb0", "[ -f $T/once ] && touch $T/c/acm.usb0; touch $T/once"))
    rc = subprocess.run(["sh", str(s), "--rollback-now"], env=dict(dev.env, MK20_RB_RETRY_S="0"), timeout=120).returncode
    assert rc == 0 and cleanups(tmp_path) == 2 and not (tmp_path / "flag").exists()


def test_escalate_failure_is_not_rc3(dev, tmp_path):
    """touch of the flag fails (parent dir missing): escalate must not claim success (no rc 3)."""
    s = harness(tmp_path, cleanup_makes_stock=False, cleanup_s=0)
    s.write_text(s.read_text().replace(str(tmp_path / "flag"), str(tmp_path / "nodir/flag")))
    rc = subprocess.run(["sh", str(s), "--rollback-now"], env=dict(dev.env, MK20_RB_RETRY_S="0"), timeout=120).returncode
    assert rc == 1 and not (tmp_path / "reboot").exists()
    assert "ESCALATE FAILED" in (tmp_path / "log").read_text()


def test_escalate_is_bounded_child_without_fd7():
    e = G[G.index("escalate() {"):]
    e = e[:e.index("\n}\n")]
    assert "7>&- &" in e and "bounded_wait $! " in e and '[ -e "$EF" ] && [ -e "$ER" ]' in e
    assert "&& touch \"$ER\"; sync" not in e   # sync no longer masks a failed touch


def test_every_rc3_exit_clears_busy():
    rest = G.replace('escalate "$1" "$2" && exit 3', "").replace("[ \\$rc = 3 ] && exit 3", "")   # waiter: rc passthrough
    assert "exit 3" not in rest   # only stuck() exits 3 under the mutex
    st = G[G.index("stuck() {"):].split("\n")[0]
    assert st.index("rm -f /tmp/mk20/gadget.busy") < st.index("escalate")


def test_self_retrying_callers_pass_caller_flag():
    assert 'MK20_GEN="$GEN" MK20_RB_CALLER_RETRIES=1' in G   # flapmon export
    assert G.count("MK20_RB_CALLER_RETRIES=1 MK20_REL=") == 2   # both confirm-waiter attempts
    m = (Path(__file__).resolve().parents[1] / "firmware/release/sbin/mk20d").read_text()
    assert "rbn() { MK20_GEN='' MK20_RB_CALLER_RETRIES=1 " in m


# ---- GPT r7: generation publication + stale under a busy mutex ----
def test_missing_generation_is_not_stale(dev, tmp_path):
    s = harness(tmp_path, cleanup_s=0)
    assert subprocess.run(["sh", str(s), "--rollback-now"], env=cenv(dev, MK20_GEN="100.1-7"),
                          timeout=60).returncode == 0 and cleanups(tmp_path) == 1


def test_stale_request_with_busy_mutex_is_rc5_not_rc4(dev, tmp_path):
    s = harness(tmp_path, cleanup_s=0)
    (tmp_path / "mk20").mkdir(exist_ok=True)
    (tmp_path / "mk20/composite.gen").write_text("200.5-42\n")
    holder = subprocess.Popen(["sh", "-c", f"exec 7>{tmp_path}/mk20/transition.lock; flock -x 7; sleep 30"], env=dev.env)
    try:
        time.sleep(0.5)
        env = cenv(dev, MK20_RB_LOCK_TRIES="4", MK20_GEN="100.1-7")
        assert subprocess.run(["sh", str(s), "--rollback-now"], env=env, timeout=60).returncode == 5
        assert cleanups(tmp_path) == 0 and holder.poll() is None
    finally:
        holder.kill()


def test_generation_publication_is_checked():
    sm = G[G.index("setup_main() {"):]
    assert '[ "$(cat "$GENF" 2>/dev/null)" = "$GEN" ] || {' in sm
    assert sm.index("cannot publish generation") < sm.index("touch /tmp/mk20-flapmon-stop")   # r8 #5
    assert sm.index("cannot publish generation") < sm.index("trap cleanup EXIT")   # before any mutation


def test_queued_job_detaches_transition_from_watcher_timeout():
    q = [ln for ln in G.splitlines() if "> $Q.tmp" in ln][0]
    assert q.lstrip().startswith('echo "( ') and "</dev/null >/dev/null 2>&1 7>&- & ); echo started" in q
