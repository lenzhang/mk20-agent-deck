"""1.7.0: ECM RX watchdog + probation host-traffic gate (incident 2026-10-23 23:07: after a reboot host->device was dead
- gadget configured, usb0 carrier 1, usb0 RX frozen at 1 packet - yet the selftest passed and the release was
confirmed). mk20d: ecm_tick (maint loop), ecm_gate (boot, probation only), ecm_report (selftest), health usb.ecm_*;
gadget.sh --rearm-now (ONE soft re-enumeration of the existing composite, then usb0 / bridge / web restored).
BusyBox 1.27.2 harness with a fake /sys, /proc/net/dev and /proc/net/arp; tests/shims/ping never sends anything (it
moves the fake counters and answers only when told to). Kimi K3 1.6.2-rc1 review (NO-GO) regressions included."""
import fcntl
import json
import os
import re
import subprocess
import time

import pytest
from conftest import BB, FW
from test_mk20d import rel, g1, conf, run_boot  # noqa: F401  (fixtures)

UDC = "musb-hdrc.1.auto"
MK20D = (FW / "release/sbin/mk20d").read_text()
GS = (FW / "release/share/gadget.sh").read_text()

FAKE_GADGET = r'''# fake gadget.sh: --rearm-now records its env. After it: rearm.fixes = the host answers again; rearm.rxonly = the
# host's frames arrive but nothing answers (a bare usb0 the Mac keeps ARPing)
case "$1" in
--rearm-now)
  echo "rearm gen=${MK20_GEN:-} rel=${MK20_REL:-} log=${MK20_GADGET_LOG:-}" >> "$MOCK_DIR/rearm.calls"
  [ -f "$MOCK_DIR/rearm.fixes" ] && touch "$MOCK_DIR/ping.answer"
  [ -f "$MOCK_DIR/rearm.rxonly" ] && touch "$MOCK_DIR/ping.rxonly"
  exit "$(cat "$MOCK_DIR/rearm.rc" 2>/dev/null || echo 0)";;
*) echo "$*" >> "$MOCK_DIR/gadget.calls";;
esac
'''


def netdev(dev, rx, tx):
    """/proc/net/dev as the device prints it (incident capture: usb0 rx 1 packet, tx 3)."""
    f = dev.tmp / "netdev"
    t = f.with_name("netdev.w")
    t.write_text("Inter-|   Receive                                                |  Transmit\n"
                 " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n"
                 f"  usb0:      28 {rx:7d}    0    0    0     0          0         0      126 {tx:7d}    0    0    0     0       0          0\n"
                 "    lo:  188216    2406    0    0    0     0          0         0   188216    2406    0    0    0     0       0          0\n")
    os.replace(t, f)


def counts(dev):
    for line in (dev.tmp / "netdev").read_text().splitlines():
        if line.strip().startswith("usb0:"):
            f = line.split(":", 1)[1].split()
            return int(f[1]), int(f[9])
    return None


def lines(dev, name):
    f = dev.mock / name
    return f.read_text().splitlines() if f.exists() else []


def mlog(dev):
    f = dev.p("data/mk20/logs/mk20d.log")
    return f.read_text() if f.exists() else ""


def state(dev):
    f = dev.p("tmp/mk20/ecm.state")
    return dict(line.split("=", 1) for line in f.read_text().splitlines()) if f.exists() else {}


def udc_state(dev, s):
    dev.p(f"sys/class/udc/{UDC}/state").write_text(s + "\n")


def kbd_status(dev, keys=0, knobs=0):
    k = dev.p("run/mk20/kbd.status"); k.parent.mkdir(parents=True, exist_ok=True)
    k.write_text(json.dumps({"key_events": keys, "knob_events": knobs}) + "\n")


@pytest.fixture
def ecm(dev, rel, g1):
    """Composite up with a configured, awake host whose ECM is active; usb0 rx 1 / tx 3 (the incident's numbers)."""
    (g1 / "configs/c.1/ecm.usb0").mkdir()
    (g1 / "UDC").write_text(UDC + "\n")
    dev.p(f"sys/class/udc/{UDC}").mkdir(parents=True)
    udc_state(dev, "configured")
    dev.p("sys/class/net/usb0").mkdir(parents=True)
    dev.p("sys/class/net/usb0/carrier").write_text("1\n")
    netdev(dev, 1, 3)
    (dev.tmp / "arp").write_text("IP address       HW type     Flags       HW address            Mask     Device\n"
                                 "172.30.250.1     0x1         0x2         02:11:22:33:44:56     *        usb0\n")
    dev.p("tmp/mk20/gadget.env").write_text("MK20_ECM_IP=172.30.250.2\n")
    dev.p("tmp/mk20/composite.gen").write_text("57-1234\n")
    dev.p("tmp/mk20/gadget-boot.sh").write_text(FAKE_GADGET)
    dev.clock = dev.tmp / "clock"
    dev.clock.write_text("1000\n")
    dev.env.update({"MK20_NETDEV": str(dev.tmp / "netdev"), "MK20_ARP": str(dev.tmp / "arp"),
                    "MK20_UPTIME_CMD": f"cat {dev.clock}", "MK20_ECM_PROBE_S": "1"})
    return dev


def sh(dev, rel, code):
    s = dev.tmp / "lib.sh"
    s.write_text(f'MK20D_LIB=1; . "{rel}/sbin/mk20d"; cfg_load\n{code}\n')
    r = dev.run(s)
    assert r.returncode == 0, r.stderr
    return r.stdout


def tick(dev, rel, at=None):
    """One mk20d maint watchdog step (mk20d sourced: the loop's own functions), at uptime <at>."""
    if at is not None:
        dev.clock.write_text(f"{at}\n")
    sh(dev, rel, "ecm_tick")
    return state(dev)


def health(dev, rel):
    return json.loads(dev.run(rel / "sbin/mk20d", "health").stdout)


def reboots(dev):
    f = dev.p("data/mk20/state/ecm-reboots")
    return f.read_text().strip() if f.exists() else None


# ---------------- mk20d ECM watchdog (maint) ----------------

def test_watchdog_rx_flowing_never_probes(ecm, rel):
    for i in range(6):
        netdev(ecm, 10 + i, 20 + i)
        s = tick(ecm, rel, at=1000 + 50 * i)
    assert s["state"] == "ok" and s["probes"] == "0" and s["stalls"] == "0"
    assert not lines(ecm, "ping.calls") and not lines(ecm, "rearm.calls")


def test_watchdog_idle_host_answers_the_probe(ecm, rel):
    """A Mac without the daemon may send nothing for a minute: the probe is answered -> healthy, no action."""
    (ecm.mock / "ping.answer").touch()
    tick(ecm, rel, at=1000)
    s = tick(ecm, rel, at=1059)                    # 59 s frozen: inside the window, no traffic generated
    assert s["probes"] == "0" and not lines(ecm, "ping.calls")
    s = tick(ecm, rel, at=1060)
    assert s["probes"] == "1" and s["state"] == "ok" and s["stalls"] == "0" and s["since"] == "1060"
    assert sorted(set(lines(ecm, "ping.calls"))) == ["169.254.7.1", "172.30.250.1"]
    assert not lines(ecm, "rearm.calls") and "stall" not in mlog(ecm)


def test_probe_targets_the_learned_host_address(ecm, rel):
    (ecm.tmp / "arp").write_text("IP address       HW type     Flags       HW address            Mask     Device\n"
                                 "172.30.250.77    0x1         0x2         02:11:22:33:44:56     *        usb0\n"
                                 "172.30.250.9     0x1         0x0         00:00:00:00:00:00     *        usb0\n"
                                 "192.168.88.1      0x1         0x2         02:00:00:00:00:01     *        wlan0\n")
    (ecm.mock / "ping.notx").touch()               # (no action: only the targets matter here)
    tick(ecm, rel, at=1000)
    tick(ecm, rel, at=1061)
    assert set(lines(ecm, "ping.calls")) == {"172.30.250.77", "172.30.250.1", "169.254.7.1"}


def test_probe_works_with_a_busybox_without_fancy_ping(ecm, rel):
    """`ping -c` is a bad host on a BusyBox without FEATURE_FANCY_PING: detected on loopback, plain `ping HOST` used."""
    (ecm.mock / "ping.nonfancy").touch(); (ecm.mock / "ping.answer").touch()
    tick(ecm, rel, at=1000)
    s = tick(ecm, rel, at=1061)
    assert s["state"] == "ok" and s["stalls"] == "0" and "172.30.250.1" in lines(ecm, "ping.calls")


def test_watchdog_stall_rearms_once_then_recovers_on_an_answer(ecm, rel):
    """The incident: configured, carrier 1, RX frozen, the probe's frames reach the host (TX completes), nothing back."""
    (ecm.mock / "rearm.fixes").touch()
    tick(ecm, rel, at=1000)
    s = tick(ecm, rel, at=1061)
    assert s["stalls"] == "1" and s["rearms"] == "1" and s["episode"] == "1" and s["state"] == "stalled"
    log_path = ecm.p("data/mk20/logs/gadget.log")
    assert lines(ecm, "rearm.calls") == [f"rearm gen=57-1234 rel={rel} log={log_path}"]
    rx, tx = counts(ecm)
    assert rx == 1 and tx > 3                      # the probe went out (the host read it), nothing came back
    log = mlog(ecm)
    assert re.search(r"WARN mk20d ecm v=9\.9\.9 host->device stall #1: usb0 rx 1 frozen 61s \(udc configured, "
                     r"carrier 1\); probe tx \+[1-9]\d* rx \+0", log), log
    assert "WARN mk20d ecm v=9.9.9 re-arm #1 (watchdog): gadget.sh --rearm-now rc=0 (re-enumerated)" in log
    assert "WARN ecm host->device stalled (usb0 rx 1; no answer yet; 1 re-arm(s) so far)" in health(ecm, rel)["selftest"]["detail"]
    s = tick(ecm, rel, at=1070)                    # 9 s after the re-arm: not checked yet
    assert s["rearms"] == "1" and s["probes"] == "1" and s["episode"] == "1"
    s = tick(ecm, rel, at=1076)                    # the check: the host answers -> recovered, counted once
    assert s["episode"] == "0" and s["recoveries"] == "1" and s["state"] == "ok" and s["probes"] == "2"
    assert len(lines(ecm, "rearm.calls")) == 1 and reboots(ecm) in (None, "0")
    assert re.search(r"INFO mk20d ecm v=9\.9\.9 host->device back after 1 re-arm\(s\): the host answered \(usb0 rx [2-9]\)", mlog(ecm))
    u = health(ecm, rel)["usb"]
    assert (u["ecm_rx_stalls"], u["ecm_rearms"], u["ecm_recoveries"], u["ecm_probes"], u["ecm_gave_up"], u["ecm_reboots"]) \
        == (1, 1, 1, 2, False, 0)
    assert u["ecm_state"] == "ok" and u["ecm_rx_packets"] > 1 and u["ecm_tx_packets"] > 3
    assert "INFO ecm host->device stalls 1, re-arms 1, recovered 1" in health(ecm, rel)["selftest"]["detail"]


def test_watchdog_rx_growth_without_an_answer_is_not_a_recovery(ecm, rel):
    """Kimi K3 BLOCKER (c): after a re-arm the Mac's ARPs for .2 arrive (RX grows) yet nothing answers: still stalled,
    re-armed again, then escalated - never a false recovery."""
    (ecm.mock / "rearm.rxonly").touch()
    tick(ecm, rel, at=1000)
    tick(ecm, rel, at=1061)                        # stall #1, re-arm #1 -> RX grows from now on, no answer
    for t in (1080, 1100):
        rx, tx = counts(ecm); netdev(ecm, rx + 5, tx)   # the host's frames keep arriving
        s = tick(ecm, rel, at=t)
        assert s["episode"] == "1" and s["recoveries"] == "0" and s["state"] == "stalled", s
    s = tick(ecm, rel, at=1122)                    # 61 s after the re-arm, still no answer: re-arm #2
    assert s["rearms"] == "2" and s["recoveries"] == "0"
    assert "back after" not in mlog(ecm)


def test_watchdog_new_bridge_client_counts_as_an_answer(ecm, rel):
    """A Mac in stealth mode drops the echo; mk20deck reconnecting to the bridge (a TCP round trip) is the answer."""
    b = ecm.p("tmp/mk20/bridge.log"); b.write_text("bridge v4 /dev/pts/0 <-> 172.30.250.2:17890\nclient connected via 172.30.250.2:17890\n")
    tick(ecm, rel, at=1000)
    tick(ecm, rel, at=1061)                        # stall, re-arm (the fake fixes nothing: ping never answers)
    s = tick(ecm, rel, at=1076)
    assert s["episode"] == "1"                     # the old client line is the baseline, not an answer
    with b.open("a") as f:
        f.write("client gone\nclient connected via 172.30.250.2:17890\n")
    s = tick(ecm, rel, at=1092)
    assert s["episode"] == "0" and s["recoveries"] == "1", s


def test_watchdog_soft_path_exhausted_escalates_to_one_watcher_reboot(ecm, rel):
    """3 re-arms per rolling hour without an answer -> cmd-reboot (the watcher's WDT hardreset), at most once in a row
    across boots; a boot that proves the link resets that."""
    tick(ecm, rel, at=1000)
    for t in (1061, 1122, 1183):
        s = tick(ecm, rel, at=t)
    assert s["rearms"] == "3" and s["gave_up"] == "0" and s["stalls"] == "1"   # one episode, three re-arms
    s = tick(ecm, rel, at=1244)
    assert s["gave_up"] == "1" and s["state"] == "gave-up" and s["escalated"] == "1" and len(lines(ecm, "rearm.calls")) == 3
    assert ecm.p("mnt/SDCARD/cmd-reboot").exists() and reboots(ecm) == "1"
    assert not ecm.p("mnt/SDCARD/mk20-no-composite").exists()
    assert "ERROR mk20d ecm v=9.9.9 soft re-arms did not help (3 in this episode): watcher cmd-reboot (WDT hardreset)" in mlog(ecm)
    h = health(ecm, rel)
    assert h["usb"]["ecm_gave_up"] is True and h["usb"]["ecm_reboots"] == 1 and h["status"] != "ok"
    assert "WARN ecm host->device stalled: soft path exhausted after 3 re-arm(s), watcher reboot requested" in h["selftest"]["detail"]
    # the next boot (tmpfs state gone) is just as dead: re-arms again, but no second reboot in a row
    ecm.p("tmp/mk20/ecm.state").unlink(); ecm.p("mnt/SDCARD/cmd-reboot").unlink()
    tick(ecm, rel, at=100)
    for t in (161, 222, 283, 344):
        s = tick(ecm, rel, at=t)
    assert s["gave_up"] == "1" and s["escalated"] == "0" and not ecm.p("mnt/SDCARD/cmd-reboot").exists() and reboots(ecm) == "1"
    assert "not rebooting again" in mlog(ecm) and len(lines(ecm, "rearm.calls")) == 6
    s = tick(ecm, rel, at=400)                     # still observed (an answer check), nothing else
    assert len(lines(ecm, "rearm.calls")) == 6
    (ecm.mock / "ping.answer").touch()             # the link comes back: proven -> the counter resets
    s = tick(ecm, rel, at=420)
    assert s["episode"] == "0" and reboots(ecm) == "0"


def test_watchdog_never_reboots_on_probation(ecm, rel):
    ecm.p("data/mk20/state").mkdir(parents=True, exist_ok=True); ecm.p("data/mk20/state/pending").write_text("9.9.9\n")
    tick(ecm, rel, at=1000)
    for t in (1061, 1122, 1183, 1244):
        s = tick(ecm, rel, at=t)
    assert s["gave_up"] == "1" and not ecm.p("mnt/SDCARD/cmd-reboot").exists() and reboots(ecm) is None
    assert "on probation: no reboot from here (the loader decides)" in mlog(ecm)


def test_watchdog_rearm_off_after_two_ecm_reboots_in_a_row(ecm, rel):
    ecm.p("data/mk20/state").mkdir(parents=True, exist_ok=True); ecm.p("data/mk20/state/ecm-reboots").write_text("2\n")
    tick(ecm, rel, at=1000)
    s = tick(ecm, rel, at=1061)
    assert s["stalls"] == "1" and not lines(ecm, "rearm.calls") and "re-arm off: 2 ECM reboots in a row" in mlog(ecm)


def test_watchdog_rate_limit_is_a_rolling_hour(ecm, rel):
    """One stall every ~65 min, each fixed by its re-arm: never escalated (the cap is 3 per rolling hour)."""
    t = 1000
    for n in range(1, 5):
        (ecm.mock / "ping.answer").unlink(missing_ok=True)
        (ecm.mock / "rearm.fixes").touch()
        rx, tx = counts(ecm); netdev(ecm, rx + 1, tx)     # traffic until t: a fresh window
        tick(ecm, rel, at=t)
        s = tick(ecm, rel, at=t + 61)
        assert s["rearms"] == str(n) and s["episode"] == "1", (n, s)
        s = tick(ecm, rel, at=t + 125)
        assert s["recoveries"] == str(n) and s["episode"] == "0", (n, s)
        t += 3900
    assert s["gave_up"] == "0" and s["stalls"] == "4" and len(lines(ecm, "rearm.calls")) == 4
    assert not ecm.p("mnt/SDCARD/cmd-reboot").exists()


@pytest.mark.parametrize("why,want", [("suspended", "suspended"), ("not attached", "absent"), ("default", "absent"),
                                      ("carrier0", "inactive"), ("unbound", "absent"), ("busy", "idle")])
def test_watchdog_never_acts_without_an_awake_host(ecm, rel, why, want):
    """A sleeping Mac suspends the bus; unplugged / a charger is not configured; carrier 0 = the host has not enabled
    ECM; a gadget transition owns the USB: no probe, no re-arm, however long."""
    g = ecm.p("sys/kernel/config/usb_gadget/g1/UDC")
    if why == "carrier0":
        ecm.p("sys/class/net/usb0/carrier").write_text("0\n")
    elif why == "unbound":
        g.write_text("\n")
    elif why == "busy":
        ecm.p("tmp/mk20/gadget.busy").touch()
    else:
        udc_state(ecm, why)
    for t in (1000, 1100, 1500, 5000):
        s = tick(ecm, rel, at=t)
    assert s["state"] == want and s["probes"] == "0", s
    assert not lines(ecm, "ping.calls") and not lines(ecm, "rearm.calls")
    udc_state(ecm, "configured"); ecm.p("sys/class/net/usb0/carrier").write_text("1\n"); g.write_text(UDC + "\n")
    ecm.p("tmp/mk20/gadget.busy").unlink(missing_ok=True)
    s = tick(ecm, rel, at=5001)                    # the host is back: the window runs from its last absent sample
    assert s["since"] == "5000" and s["state"] == "ok"
    s = tick(ecm, rel, at=5059)
    assert s["probes"] == "0" and s["state"] == "ok"
    s = tick(ecm, rel, at=5061)
    assert s["probes"] == "1" and s["stalls"] == "1" and len(lines(ecm, "rearm.calls")) == 1


def test_watchdog_host_not_reading_usb0_is_not_this_fault(ecm, rel):
    """No TX completes either (the host is not polling the bulk IN endpoint, e.g. asleep without a USB suspend)."""
    (ecm.mock / "ping.notx").touch()
    tick(ecm, rel, at=1000)
    s = tick(ecm, rel, at=1061)
    assert s["state"] == "host-idle" and s["stalls"] == "0" and s["probes"] == "1"
    assert not lines(ecm, "rearm.calls") and counts(ecm) == (1, 3)


@pytest.mark.parametrize("kind", ["keys", "knobs"])
def test_watchdog_defers_the_rearm_while_the_keyboard_is_in_use(ecm, rel, kind):
    def use(n):
        kbd_status(ecm, keys=n if kind == "keys" else 0, knobs=n if kind == "knobs" else 0)
    use(1)
    tick(ecm, rel, at=1000)
    use(2)
    s = tick(ecm, rel, at=1061)
    assert s["stalls"] == "1" and s["state"] == "stalled" and s["rearms"] == "0" and not lines(ecm, "rearm.calls")
    use(5)
    tick(ecm, rel, at=1122)
    assert mlog(ecm).count("re-arm deferred: keyboard in use") == 1 and not lines(ecm, "rearm.calls")
    s = tick(ecm, rel, at=1183)                    # no key/knob event in the last tick: re-arm
    assert s["rearms"] == "1" and len(lines(ecm, "rearm.calls")) == 1


def test_watchdog_fat_flag_observes_only(ecm, rel):
    ecm.p("mnt/SDCARD/mk20-no-ecm-rearm").touch()
    tick(ecm, rel, at=1000)
    for t in (1061, 1122, 1183, 1244, 1305):
        s = tick(ecm, rel, at=t)
    assert s["stalls"] == "1" and s["state"] == "stalled" and s["rearms"] == "0" and not lines(ecm, "rearm.calls")
    assert not ecm.p("mnt/SDCARD/cmd-reboot").exists()
    assert mlog(ecm).count("re-arm disabled by FAT mk20-no-ecm-rearm: observe only") == 1
    assert "WARN ecm host->device stalled (usb0 rx 1; no answer yet; 0 re-arm(s) so far)" in health(ecm, rel)["selftest"]["detail"]


@pytest.mark.parametrize("rc,counted,note", [("4", False, "re-arm deferred: gadget.sh busy"),
                                             ("5", False, "re-arm skipped: stale composite generation"),
                                             ("6", False, "re-arm not applicable (rc 6"),
                                             ("1", True, "rc=1"), ("2", True, "rc=2")])
def test_watchdog_rearm_result_accounting(ecm, rel, rc, counted, note):
    """Only a re-arm that touched the USB/HID (rc 0-3) counts toward the 3/h cap; refusals are retried; gadget.sh's own
    reboot request (rc 2) counts as an ECM reboot."""
    (ecm.mock / "rearm.rc").write_text(rc)
    tick(ecm, rel, at=1000)
    tick(ecm, rel, at=1061)
    s = tick(ecm, rel, at=1122)
    assert len(lines(ecm, "rearm.calls")) == 2 and s["rearms"] == ("2" if counted else "0"), s
    assert note in mlog(ecm)
    assert reboots(ecm) == ("2" if rc == "2" else None)
    if not counted:
        assert mlog(ecm).count(note) == 1          # logged once per episode


def test_watchdog_counter_reset_rebaselines(ecm, rel):
    netdev(ecm, 500, 600)
    tick(ecm, rel, at=1000)
    netdev(ecm, 2, 3)                              # a re-created netdev: counters back to 0
    s = tick(ecm, rel, at=1061)
    assert s["rx"] == "2" and s["since"] == "1061" and s["probes"] == "0" and not lines(ecm, "ping.calls")


def test_watchdog_state_file_is_whitelisted(ecm, rel):
    ecm.p("tmp/mk20/ecm.state").write_text("state=$(reboot)\nrx=1;id\nsince=x\nstalls=9\ntimes=1 2 $(id)\nnote=a b\nlastv=-1\n")
    s = tick(ecm, rel, at=1000)
    assert s["state"] == "ok" and s["stalls"] == "9" and s["times"] == "" and s["note"] == "" and s["rx"] == "1"
    assert s["lastv"] == "0"


@pytest.mark.parametrize("value,want", [("", "5"), ("1", "1"), ("60", "60"), ("1.2.3", "5"), ("0.2", "5"), (".5", "5"),
                                        ("0", "5"), ("61", "5"), ("abc", "5"), ("007", "007")])
def test_tick_period_is_a_strict_integer(ecm, rel, value, want):
    """Kimi K3 MINOR: `1.2.3` used to pass the old pattern, `sleep` failed and the loop spun writing ecm.state."""
    assert sh(ecm, rel, f'MK20_ECM_TICK_S="{value}"; ecm_init; echo "$ECM_TICK_S"').strip() == want


def test_health_defaults_without_watchdog_state(ecm, rel):
    h = health(ecm, rel)
    u = h["usb"]
    assert (u["ecm_state"], u["ecm_rx_packets"], u["ecm_tx_packets"], u["ecm_rx_stalls"], u["ecm_rearms"],
            u["ecm_recoveries"], u["ecm_probes"], u["ecm_gave_up"], u["ecm_reboots"]) == ("idle", 1, 3, 0, 0, 0, 0, False, 0)
    assert "INFO ecm usb0 rx 1 tx 3 (udc configured)" in h["selftest"]["detail"]
    assert not lines(ecm, "ping.calls")            # health/selftest never probe


def test_maint_loop_runs_the_watchdog(ecm, rel):
    """The real `mk20d maint` loop (real uptime, 1 s ticks): stall -> one re-arm -> answered -> recovered."""
    (ecm.mock / "rearm.fixes").touch()
    env = dict(ecm.env, MK20_ECM_TICK_S="1", MK20_ECM_STALL_S="1", MK20_ECM_VERIFY_S="1", MK20_MAINT_S="300")
    env.pop("MK20_UPTIME_CMD")
    p = subprocess.Popen([BB, "sh", str(rel / "sbin/mk20d"), "maint"], env=env)
    try:
        end = time.time() + 40
        while time.time() < end and state(ecm).get("recoveries") != "1":
            time.sleep(0.1)
    finally:
        p.kill(); p.wait()
    s = state(ecm)
    assert s.get("recoveries") == "1" and s["rearms"] == "1" and len(lines(ecm, "rearm.calls")) == 1, s
    assert json.loads(ecm.read("tmp/mk20/health.json"))["maint"] is True


# ---------------- probation gate (mk20d boot) ----------------

@pytest.fixture
def gate(ecm, rel):
    dev = ecm
    (rel / "share/gadget.sh").write_text(FAKE_GADGET)   # boot copies it to gadget-boot.sh
    conf(dev, "FEATURE_ROUTER=0\nFEATURE_WEB=0\n")
    (dev.mock / "netstat.out").write_text("tcp 0 0 172.30.250.2:17890 0.0.0.0:* LISTEN\n"
                                          "tcp 0 0 169.254.7.2:17890 0.0.0.0:* LISTEN\n")
    dev.p("tmp/mk20/rescue-loader.sh").write_text('echo "$*" >> "$MK20_ROOT/tmp/mk20/loader.calls"\n')
    dev.p("data/mk20/state").mkdir(parents=True, exist_ok=True)
    dev.p("data/mk20/state/pending").write_text("9.9.9\n")
    dev.env.update({"MK20_ECM_GATE_WAIT_S": "10", "MK20_ECM_RECONF_S": "2"})
    return dev


def selftest_line(log):
    return next(line for line in log.splitlines() if "INFO mk20d selftest" in line and "rc=" in line)


def confirmed(dev):
    return dev.read("tmp/mk20/loader.calls") == "confirm 9.9.9"


def test_gate_host_traffic_confirms(gate, rel):
    (gate.mock / "ping.answer").touch()
    r, log = run_boot(gate, rel)
    st = selftest_line(log)
    assert "rc=0" in st and re.search(r"PASS ecm host traffic \(usb0 rx 1 -> [23], answered\)", st), st
    assert confirmed(gate) and not lines(gate, "rearm.calls")
    assert "MK20_LL_IP=169.254.7.2" in gate.read("tmp/mk20/gadget.env").splitlines()   # for --rearm-now's alias


def test_gate_stall_fixed_by_one_rearm_confirms(gate, rel):
    (gate.mock / "rearm.fixes").touch()
    r, log = run_boot(gate, rel)
    st = selftest_line(log)
    assert "rc=0" in st and re.search(r"PASS ecm host answered after re-arm \(usb0 rx 1 -> [23]\)", st), st
    assert len(lines(gate, "rearm.calls")) == 1 and confirmed(gate)
    s = state(gate)
    assert (s["stalls"], s["rearms"], s["recoveries"], s["episode"]) == ("1", "1", "1", "0")
    assert "re-arm #1 (gate): gadget.sh --rearm-now rc=0 (re-enumerated)" in log


def test_gate_dead_host_link_is_never_confirmed(gate, rel):
    """The incident on 1.6.1: confirmed. Now: one re-arm, still no answer -> FAIL -> no confirm (loader rolls back)."""
    r, log = run_boot(gate, rel)
    st = selftest_line(log)
    assert "rc=1" in st and "FAIL ecm host->device dead after re-arm: no answer from the host (udc configured, usb0 rx 1 -> 1)" in st, st
    assert not gate.p("tmp/mk20/loader.calls").exists() and len(lines(gate, "rearm.calls")) == 1
    assert "ERROR mk20d selftest v=9.9.9 FAILED: not confirming; loader owns A/B rollback" in log
    assert "FAIL ecm host->device dead after re-arm" in gate.read("tmp/mk20/selftest.txt")
    h = json.loads(gate.read("data/mk20/state/selftest-last.json"))
    assert h["usb"]["ecm_rx_stalls"] == 1 and h["status"] != "ok"


def test_gate_rx_growth_after_rearm_without_an_answer_fails(gate, rel):
    """Kimi K3 BLOCKER (b): a bare usb0 after the re-bind still receives the Mac's ARPs for .2 (RX grows), but nothing
    answers: that is not a working link -> FAIL (it used to PASS on RX growth)."""
    (gate.mock / "rearm.rxonly").touch()
    r, log = run_boot(gate, rel)
    st = selftest_line(log)
    assert "rc=1" in st and re.search(r"FAIL ecm host->device dead after re-arm: no answer from the host \(udc configured, "
                                      r"usb0 rx 1 -> [2-9]\)", st), st
    assert not gate.p("tmp/mk20/loader.calls").exists()


@pytest.mark.parametrize("why", ["suspended", "not attached", "carrier0"])
def test_gate_no_host_never_fails_an_install(gate, rel, why):
    """Mac asleep (bus suspended), unplugged / charger (not configured) or ECM not enabled by the host: INFO only."""
    if why == "carrier0":
        gate.p("sys/class/net/usb0/carrier").write_text("0\n")
    else:
        udc_state(gate, why)
    r, log = run_boot(gate, rel)
    st = selftest_line(log)
    want = "host has not enabled ECM (usb0 carrier 0)" if why == "carrier0" else f"no host (udc {why}: asleep, unplugged or charger)"
    assert "rc=0" in st and f"INFO ecm gate skipped: {want}" in st, st
    assert confirmed(gate) and not lines(gate, "ping.calls") and not lines(gate, "rearm.calls")


def test_gate_runs_only_on_probation(gate, rel):
    gate.p("data/mk20/state/pending").unlink()
    r, log = run_boot(gate, rel)
    st = selftest_line(log)
    assert "rc=0" in st and "ecm gate" not in st and "ecm host" not in st and "INFO ecm usb0 rx 1 tx 3" in st, st
    assert not lines(gate, "ping.calls") and not lines(gate, "rearm.calls")


def test_gate_host_not_reading_usb0_warns_and_confirms(gate, rel):
    (gate.mock / "ping.notx").touch()
    r, log = run_boot(gate, rel)
    st = selftest_line(log)
    assert "rc=0" in st and "WARN ecm gate: host not reading usb0 (rx 1, tx 3 unchanged in 1s)" in st, st
    assert confirmed(gate) and not lines(gate, "rearm.calls")


def test_gate_no_counters_is_not_judged(gate, rel):
    """Kimi K3 MAJOR: unreadable counters are not a dead link."""
    (gate.tmp / "netdev").write_text("Inter-|\n face |\n    lo: 1 2 0 0 0 0 0 0 1 2 0 0 0 0 0 0\n")
    r, log = run_boot(gate, rel)
    st = selftest_line(log)
    assert "rc=0" in st and "WARN ecm gate: no usb0 counters (not judged)" in st, st
    assert confirmed(gate)


def test_gate_rearm_busy_for_the_whole_wait_is_not_judged(gate, rel):
    """Kimi K3 MAJOR: refused for ECM_GATE_WAIT_S (a flap window, a transition) is not proof of a dead link: WARN,
    confirmed on the other checks."""
    (gate.mock / "rearm.rc").write_text("4")
    t0 = time.time()
    r, log = run_boot(gate, rel)
    st = selftest_line(log)
    assert "rc=0" in st and "WARN ecm gate: host->device stall not judged (re-arm rc=4: busy or keyboard in use for 10s)" in st, st
    assert len(lines(gate, "rearm.calls")) == 3 and 10 <= time.time() - t0 < 40   # 0, 5, 10 s (MK20_ECM_GATE_WAIT_S=10)
    assert confirmed(gate)


@pytest.mark.parametrize("rc,ok,text", [("1", True, "re-arm rc=1: aborted before any USB change"),
                                        ("5", True, "re-arm rc=5: stale generation"),
                                        ("6", True, "re-arm rc=6: not applicable"),
                                        ("2", False, "FAIL ecm host->device dead: the re-arm could not restore USB (rc=2"),
                                        ("3", False, "FAIL ecm host->device dead: the re-arm could not restore USB (rc=3")])
def test_gate_rearm_outcomes(gate, rel, rc, ok, text):
    (gate.mock / "rearm.rc").write_text(rc)
    r, log = run_boot(gate, rel)
    st = selftest_line(log)
    assert (("rc=0" if ok else "rc=1") in st) and text in st, st
    assert confirmed(gate) == ok and len(lines(gate, "rearm.calls")) == 1


def test_gate_with_rearm_disabled_does_not_judge_a_stall(gate, rel):
    """Kimi K3 MAJOR: FAT mk20-no-ecm-rearm = observe only, also for the gate (it used to FAIL with rc=7)."""
    gate.p("mnt/SDCARD/mk20-no-ecm-rearm").touch()
    r, log = run_boot(gate, rel)
    st = selftest_line(log)
    assert "rc=0" in st and "WARN ecm gate: host->device stall not judged (re-arm rc=7: disabled)" in st, st
    assert not lines(gate, "rearm.calls") and confirmed(gate)


def test_gate_defers_its_rearm_while_the_keyboard_is_in_use(gate, rel):
    """Kimi K3 MINOR: the gate's re-arm waits for the keyboard (keys or knobs) like the watchdog's."""
    kbd_status(gate, keys=1)
    gate.env["MK20_ECM_GATE_WAIT_S"] = "30"
    w = subprocess.Popen([BB, "sh", "-c", f'i=2; while [ $i -lt 18 ]; do printf \'{{"key_events": 1, "knob_events": %d}}\\n\' $i > '
                                          f'{gate.p("run/mk20/kbd.status")}; i=$((i+1)); sleep 0.25; done'])
    try:
        (gate.mock / "rearm.fixes").touch()
        r, log = run_boot(gate, rel)
    finally:
        w.kill(); w.wait()
    st = selftest_line(log)
    assert "re-arm deferred: keyboard in use" in log and len(lines(gate, "rearm.calls")) == 1, log
    assert "rc=0" in st and "PASS ecm host answered after re-arm" in st, st


def test_gate_budget_shared_with_the_watchdog(gate, rel):
    """The gate's re-arm counts in the same 3/h budget the maint watchdog continues with after the confirm."""
    (gate.mock / "rearm.fixes").touch()
    run_boot(gate, rel)
    s = state(gate)
    assert s["times"] == "1000" and s["rearms"] == "1"


# ---------------- gadget.sh --rearm-now (the re-enumeration primitive) ----------------

RB = GS[GS.index("# >>> rollback-block"):GS.index("# <<< rearm-block")]
REARM = GS[GS.index("# >>> rearm-block"):GS.index("# <<< rearm-block")]

FAKE_ADBD = r'''#!__BB__ sh
# fake stock adbd: writes its FFS descriptors (ep1); $T/adbd.binds = binds the UDC itself (stock adbd does)
T=__T__
[ -p $T/adbd.fifo ] || mkfifo $T/adbd.fifo
echo "adbd-start udc=$(cat $T/g/UDC)" >> $T/calls
mkdir -p $T/ffs; touch $T/ffs/ep1
[ -f $T/adbd.binds ] && { sleep 0.3; echo __UDC__ > $T/g/UDC; echo adbd-bound >> $T/calls; }
echo $$ > $T/adbd.pid   # from here idle like a daemon: no child process (an adbd child = an adb session)
while :; do read -t 1 x <> $T/adbd.fifo; done
'''
FAKE_KBD = r'''T=__T__; trap 'echo "usr1 udc=$(cat $T/g/UDC) rstop=$([ -e $T/rstop ] && echo 1 || echo 0)" >> $T/calls; __RELEASE__' USR1
echo $$ > $T/hid.holders; while :; do sleep 0.1; done'''
# the kernel side of usb0 (Kimi K3 BLOCKER): $T/net.recreate = the UDC unbind unregisters usb0, the bind registers a
# bare one (new ifindex, no address, MTU 1500); otherwise usb0 lives on
FAKE_KERNEL = r'''T=__T__; last=$(cat $T/g/UDC)
while :; do u=$(cat $T/g/UDC 2>/dev/null)
  if [ -f $T/net.recreate ]; then
    if [ -n "$last" ] && [ -z "$u" ]; then rm -rf $T/sysnet/usb0 $T/if; mkdir -p $T/if; echo netdev-unregistered >> $T/calls; fi
    if [ -z "$last" ] && [ -n "$u" ]; then ix=$(cat $T/ifindex.next); echo $((ix + 1)) > $T/ifindex.next
      mkdir -p $T/sysnet/usb0; echo "$ix" > $T/sysnet/usb0/ifindex; echo "netdev-registered ifindex=$ix" >> $T/calls; fi
  fi
  last=$u; sleep 0.02; done'''
FAKE_BRIDGE = r'''#!/bin/sh
T=__T__; echo $$ > $T/bridge.pid; echo "bridge-start ifindex=$(cat $T/sysnet/usb0/ifindex 2>/dev/null)" >> $T/calls
trap 'rm -f $T/listen $T/bridge.pid; exit 0' TERM
printf '%s\n' 172.30.250.2:17890 169.254.7.2:17890 > $T/listen
while :; do sleep 0.1; done
'''
STUBS = r'''log() { echo "$*" >> $T/log; }
udc_now() { cat $G/UDC 2>/dev/null; }
router_pids() { cat $T/router.pids 2>/dev/null; }
kbd_hid_holders() { [ -s $T/hid.holders ] && cat $T/hid.holders; return 0; }
pidof() { case "$1" in adbd) f=$T/adbd.pid;; ttygs_tcp_bridge) f=$T/bridge.pid;; *) return 1;; esac; [ -s $f ] && cat $f; return 0; }
killall() { echo "killall $* udc=$(udc_now)" >> $T/calls; if [ "$1" = adbd ] && [ -s $T/adbd.pid ]; then kill "$(cat $T/adbd.pid)"; rm -f $T/adbd.pid; fi; return 0; }
netstat() { [ -f $T/listen ] && sed 's/^/tcp 0 0 /; s/$/ 0.0.0.0:* LISTEN/' $T/listen; return 0; }
ifconfig() { # fake BusyBox ifconfig over $T/if/<name>.{addr,mask,mtu}; usb0 exists while $T/sysnet/usb0 does
  n=$1; shift
  [ -d $T/sysnet/usb0 ] || { echo "ifconfig: $n: error fetching interface information: Device not found" >&2; return 1; }
  if [ $# = 0 ]; then
    a=$(cat $T/if/$n.addr 2>/dev/null)
    case "$n" in *:*) [ -n "$a" ] || { echo "ifconfig: $n: error fetching interface information: Cannot assign requested address" >&2; return 1; };; esac
    echo "$n      Link encap:Ethernet  HWaddr 02:11:22:33:44:55"
    [ -n "$a" ] && echo "          inet addr:$a  Bcast:x.x.x.255  Mask:$(cat $T/if/$n.mask 2>/dev/null)"
    echo "          UP BROADCAST RUNNING MULTICAST  MTU:$(cat $T/if/${n%%:*}.mtu 2>/dev/null || echo 1500)  Metric:1"
    return 0; fi
  echo "ifconfig $n $*" >> $T/calls
  [ -f $T/ifconfig.fails ] && return 1
  mkdir -p $T/if
  if [ "$1" = mtu ]; then echo "$2" > $T/if/$n.mtu; return 0; fi
  echo "$1" > $T/if/$n.addr; [ "${2:-}" = netmask ] && echo "$3" > $T/if/$n.mask; return 0; }
'''


class Rearm:
    """gadget.sh from the rollback-block to the rearm-block end, every path moved under the test dir, with stub
    helpers (log, udc_now, router/kbd discovery, adbd/bridge pidof, killall, netstat, ifconfig); everything else is the
    real code, run with `set -u` like gadget.sh."""

    def __init__(self, dev, t):
        self.dev, self.t, self.procs = dev, t, []
        g = t / "g"
        for d in ("g/configs/c.1/ecm.usb0", "g/configs/c.1/hid.usb0", "g/configs/c.1/ffs.adb", "mk20", "sd",
                  f"udc/{UDC}", "ffs", "sysnet/usb0", "if", "incoming", "mk20/incoming"):
            (t / d).mkdir(parents=True, exist_ok=True)
        (g / "UDC").write_text(UDC + "\n")
        (t / f"udc/{UDC}/state").write_text("configured\n")
        (t / "mk20/gadget.env").write_text("MK20_ECM_IP=172.30.250.2\n")
        (t / "mk20/composite.gen").write_text("57-1234\n")
        (t / "sysnet/usb0/ifindex").write_text("4\n"); (t / "ifindex.next").write_text("5\n")
        for n, v in (("usb0.addr", "172.30.250.2"), ("usb0.mask", "255.255.255.0"), ("usb0.mtu", "478"),
                     ("usb0:1.addr", "169.254.7.2"), ("usb0:1.mask", "255.255.0.0")):
            (t / "if" / n).write_text(v + "\n")
        (t / "listen").write_text("172.30.250.2:17890\n169.254.7.2:17890\n")   # setup_main's bridge (static here)
        (t / "hidg0").write_text("")
        (t / "gadget.log").write_text("")
        (t / "netdev").write_text("  usb0: 28 1 0 0 0 0 0 0 126 3 0 0 0 0 0 0\n")
        for name, src in (("adbd", FAKE_ADBD), ("bridge.sh", FAKE_BRIDGE)):
            f = t / name; f.write_text(src.replace("__T__", str(t)).replace("__UDC__", UDC).replace("__BB__", BB)); f.chmod(0o755)
        body = RB
        for old, new in (("/tmp/mk20-router-stop", f"{t}/rstop"), ("/tmp/mk20-kbd-stop", f"{t}/kstop"),
                         ("/tmp/mk20-flapmon-stop", f"{t}/fstop"), ("/tmp/mk20-bridge-loop.pid", f"{t}/bloop.pid"),
                         ("/tmp/mk20-bridge-stop", f"{t}/bstop"), ("/tmp/mk20/", f"{t}/mk20/"),
                         ("/tmp/adbd.log", f"{t}/adbd.log"), ("/mnt/SDCARD/", f"{t}/sd/"),
                         ("/data/mk20/incoming", f"{t}/incoming"), ("/sys/class/udc", f"{t}/udc"),
                         ("/sys/class/net/usb0", f"{t}/sysnet/usb0"), ("/dev/usb-ffs/adb/ep1", f"{t}/ffs/ep1"),
                         ("/dev/hidg0", f"{t}/hidg0"), ("/bin/adbd", f"{t}/adbd"), ("/proc/net/dev", f"{t}/netdev")):
            body = body.replace(old, new)
        rest = body.replace(str(t), "")
        assert "/tmp/" not in rest and "/mnt/" not in rest and "/data/mk20" not in rest and "/sys/" not in rest, "unscoped path"
        (t / "g.sh").write_text(f'''set -u
T={t}; G=$T/g; C=$G/configs/c.1; GENV=$T/mk20/gadget.env; LOG=$T/gadget.log
FLAP_DROPS=${{FD:-5}}; FLAP_WINDOW=60; MK20_REL=/rel
ECM_IP=172.30.250.2; ECM_MASK=255.255.255.0; LL_IP=169.254.7.2; BRIDGE_PORT=17890
{STUBS}
{body}
echo fell-through; exit 9
''')

    def start(self, src, *argv):
        p = subprocess.Popen([BB, "sh", "-c", src.replace("__T__", str(self.t)), *argv])
        self.procs.append(p)
        return p

    def kbd(self, obeys=True):
        """A fake mk20-kbd holding hidg0; on SIGUSR1 it records the moment and (obeys) closes it."""
        rel = f"rm -f {self.t}/hid.holders" if obeys else ":"
        p = self.start(FAKE_KBD.replace("__RELEASE__", rel))
        for _ in range(50):
            if (self.t / "hid.holders").exists():
                break
            time.sleep(0.05)
        return p

    def adbd(self, binds=True):
        """adbd running (the composite's adb); binds = like the stock one, the restarted adbd binds the UDC."""
        p = subprocess.Popen([str(self.t / "adbd")]); self.procs.append(p)
        for _ in range(50):
            if (self.t / "adbd.pid").exists():
                break
            time.sleep(0.05)
        (self.t / "calls").unlink(missing_ok=True)
        if binds:
            (self.t / "adbd.binds").touch()
        return p

    def kernel(self, recreate):
        if recreate:
            (self.t / "net.recreate").touch()
        self.start(FAKE_KERNEL)

    def bridge_loop(self):
        """setup_main's supervised bridge: `while [ ! -f stop ]; do bridge; sleep 1; done`, pid file like the real one."""
        (self.t / "listen").unlink(missing_ok=True)
        self.start(f'echo $$ > {self.t}/bloop.pid; while [ ! -f {self.t}/bstop ]; do sh {self.t}/bridge.sh; sleep 1; done')
        for _ in range(100):
            if (self.t / "listen").exists():
                break
            time.sleep(0.05)
        (self.t / "calls").unlink(missing_ok=True)

    def run(self, env=None, timeout=90):
        e = dict(self.dev.env, **(env or {}))
        return subprocess.run([BB, "sh", str(self.t / "g.sh"), "--rearm-now"], env=e, capture_output=True, text=True,
                              timeout=timeout)

    def calls(self):
        f = self.t / "calls"
        return f.read_text().splitlines() if f.exists() else []

    def log(self):
        f = self.t / "log"
        return f.read_text() if f.exists() else ""

    def udc(self):
        return (self.t / "g/UDC").read_text().strip()

    def last(self):
        f = self.t / "mk20/rearm.last"
        return f.read_text().strip() if f.exists() else ""

    def ifc(self, name):
        f = self.t / "if" / name
        return f.read_text().strip() if f.exists() else None


@pytest.fixture
def ra(dev, tmp_path):
    h = Rearm(dev, tmp_path / "ra")
    yield h
    for p in h.procs:
        p.kill(); p.wait()


def links(ra):
    return sorted(os.listdir(ra.t / "g/configs/c.1"))


def no_hid_no_adb(ra):
    os.rmdir(ra.t / "g/configs/c.1/ffs.adb"); os.rmdir(ra.t / "g/configs/c.1/hid.usb0")


def test_rearm_reenumerates_the_existing_composite(ra):
    ra.kbd(); ra.adbd(binds=True); ra.kernel(recreate=False)
    r = ra.run({"MK20_GEN": "57-1234"})          # mk20d passes the generation it observed
    assert r.returncode == 0, (r.stdout, ra.log())
    c = ra.calls()
    # HID released while still bound (stop file set) -> adbd stopped -> adbd restarted on an UNBOUND gadget -> it binds
    assert c[0] == f"usr1 udc={UDC} rstop=1" and c[1] == f"killall adbd udc={UDC}", c
    assert c[2] == "adbd-start udc=" and c[3] == "adbd-bound" and len(c) == 4, c   # usb0 lived on: no ifconfig
    assert ra.udc() == UDC and not (ra.t / "rstop").exists()           # mk20-kbd may reopen hidg0
    assert links(ra) == ["ecm.usb0", "ffs.adb", "hid.usb0"]             # configfs untouched
    assert "REARM begin udc=musb-hdrc.1.auto gen=57-1234 hid=1 adb=1 usb0 rx/tx=1/3" in ra.log()
    assert "REARM OK udc=musb-hdrc.1.auto state=configured" in ra.log() and "usb0=kept" in ra.log()
    assert not (ra.t / "mk20/web.rebind").exists() and ra.last().startswith("rc=0 ") and ra.last().endswith(" re-enumerated")
    assert not (ra.t / "sd/cmd-reboot").exists() and not (ra.t / "sd/mk20-no-composite").exists()
    _held(ra.t / "mk20/transition.lock").close()                       # the mutex is released


def test_rearm_reconfigures_a_recreated_bare_usb0_and_rebinds_the_listeners(ra):
    """Kimi K3 BLOCKER: the unbind unregisters usb0 and the bind registers a bare one (no address, MTU 1500 - the
    sunxi_udc PIO workaround lost - new ifindex). setup_main's three ifconfig lines again, the bridge restarted through
    its loop (SO_BINDTODEVICE on the old ifindex), mk20d web asked to re-bind."""
    ra.kbd(); ra.bridge_loop(); ra.adbd(binds=True); ra.kernel(recreate=True)
    r = ra.run()
    assert r.returncode == 0, (r.stdout, ra.log())
    c = ra.calls()
    reg = c.index("netdev-registered ifindex=5")
    i = [c.index(x) for x in ("ifconfig usb0 mtu 478", "ifconfig usb0 172.30.250.2 netmask 255.255.255.0 up",
                               "ifconfig usb0:1 169.254.7.2 netmask 255.255.0.0 up")]
    assert c.index("netdev-unregistered") < reg < i[0] < i[1] < i[2], c
    assert "bridge-start ifindex=5" in c[i[2]:], c                     # a new bridge bound to the new usb0
    assert (ra.ifc("usb0.addr"), ra.ifc("usb0.mtu"), ra.ifc("usb0:1.addr")) == ("172.30.250.2", "478", "169.254.7.2")
    assert (ra.t / "listen").read_text().split() == ["172.30.250.2:17890", "169.254.7.2:17890"]
    assert (ra.t / "mk20/web.rebind").exists()
    assert "REARM: usb0 re-created/bare (ifindex 4 -> 5)" in ra.log() and "usb0=reconfigured" in ra.log()
    assert not (ra.t / "rstop").exists() and not (ra.t / "sd/cmd-reboot").exists()


def test_rearm_restarts_the_bridge_when_a_listener_is_missing(ra):
    no_hid_no_adb(ra); ra.bridge_loop(); ra.kernel(recreate=False)
    (ra.t / "listen").write_text("172.30.250.2:17890\n")             # the link-local listener is gone
    r = ra.run()
    assert r.returncode == 0, ra.log()
    assert any(x.startswith("bridge-start") for x in ra.calls()) and "ifconfig usb0 mtu 478" not in ra.calls()
    assert (ra.t / "listen").read_text().split() == ["172.30.250.2:17890", "169.254.7.2:17890"]


@pytest.mark.parametrize("what", ["ifconfig", "bridge-loop"])
def test_rearm_that_cannot_restore_usb0_requests_the_watcher_reboot(ra, what):
    no_hid_no_adb(ra); ra.kernel(recreate=True)
    if what == "ifconfig":
        ra.bridge_loop(); (ra.t / "ifconfig.fails").touch()
    # bridge-loop: setup_main's bridge loop is gone (static listeners only): nobody can re-bind the bridge
    r = ra.run()
    assert r.returncode == 2, ra.log()
    assert (ra.t / "sd/cmd-reboot").exists() and not (ra.t / "sd/mk20-no-composite").exists()
    want = "REARM FAIL: usb0 not configured again" if what == "ifconfig" else "REARM FAIL: no bridge loop to restart the bridge"
    assert want in ra.log() and ra.last().startswith("rc=2 ")


def test_rearm_binds_itself_when_adbd_does_not(ra):
    ra.kbd(); ra.adbd(binds=False); ra.kernel(recreate=False)
    r = ra.run()
    assert r.returncode == 0, ra.log()
    assert ra.udc() == UDC and "adbd-bound" not in ra.calls() and "REARM OK" in ra.log()


def test_rearm_without_adb_or_hid(ra):
    no_hid_no_adb(ra); ra.kernel(recreate=False)
    r = ra.run()
    assert r.returncode == 0, ra.log()
    assert ra.calls() == [] and ra.udc() == UDC and "hid=0 adb=0" in ra.log()   # no adbd, no HID handshake


def test_rearm_aborts_when_mk20_kbd_keeps_hidg0(ra):
    """Fail closed WITHOUT killing mk20-kbd: no UDC/adbd change at all, the stop file removed again."""
    k = ra.kbd(obeys=False); ra.adbd()
    r = ra.run()
    assert r.returncode == 1, ra.log()
    assert ra.calls() == [f"usr1 udc={UDC} rstop=1"] and ra.udc() == UDC and k.poll() is None
    assert not (ra.t / "rstop").exists() and "REARM ABORT: mk20-kbd still holds hidg0" in ra.log()
    assert ra.last().startswith("rc=1 ")


def _held(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "w"); fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return f


def _adbd_with_child(ra, child):
    p = subprocess.Popen([BB, "sh", "-c", f"{child}; :"])          # the stand-in adbd: one child running <child>
    ra.procs.append(p); time.sleep(0.3)
    (ra.t / "adbd.pid").write_text(f"{p.pid}\n")


@pytest.mark.parametrize("why,msg", [
    ("mutex", "transition mutex busy"), ("gadget.busy", "gadget.busy present"), ("fstop", "fstop present"),
    ("kstop", "kstop present"), ("rstop", "rstop present"), ("cmd-rollback", "cmd-rollback present"),
    ("cmd-reboot", "cmd-reboot present"), ("job.sh", "job.sh present"), ("install", "release install in progress"),
    ("loader", "loader state change in progress"), ("unbound", "UDC not bound"), ("suspended", "host not configured"),
    ("not attached", "host not configured"), ("flapdrop", "flapmon link drop within 65s"),
    ("adb-shell", "adb session active (adbd has a child)"), ("adb-child", "adb session active (adbd has a child)"),
    ("push-data", "install inbox changed within 120 s"), ("push-tmp", "install inbox changed within 120 s")])
def test_rearm_refused_with_nothing_touched(ra, why, msg):
    held = None
    if why == "mutex":
        held = _held(ra.t / "mk20/transition.lock")
    elif why in ("install", "loader"):
        held = _held(ra.t / f"mk20/{why}.lock")
    elif why == "gadget.busy":
        (ra.t / "mk20/gadget.busy").touch()
    elif why in ("fstop", "kstop", "rstop"):
        (ra.t / why).touch()
    elif why in ("cmd-rollback", "cmd-reboot", "job.sh"):
        (ra.t / "sd" / why).touch()
    elif why == "unbound":
        (ra.t / "g/UDC").write_text("\n")
    elif why in ("suspended", "not attached"):
        (ra.t / f"udc/{UDC}/state").write_text(why + "\n")
    elif why == "flapdrop":
        up = int(float(open("/proc/uptime").read().split()[0]))
        (ra.t / "gadget.log").write_text(f"flapmon {up - 30}: link drop (not attached), 1 in 60s\n")
    elif why == "adb-shell":   # an `adb shell` running: a sh child of adbd
        _adbd_with_child(ra, f'{BB} sh -c "sleep 30; :"')
    elif why == "adb-child":   # any other adbd child (e.g. a sync/push helper): refused too (Kimi K3 MINOR)
        _adbd_with_child(ra, "sleep 30")
    elif why == "push-data":   # an adb push into the install inbox (no adbd child): its file is being written
        (ra.t / "incoming/upload.tar").write_text("x")
    elif why == "push-tmp":
        (ra.t / "mk20/incoming/upload.tar").write_text("x")
    before = ra.udc()
    try:
        r = ra.run()
    finally:
        if held:
            held.close()
    assert r.returncode == 4, (why, ra.log())
    assert ra.calls() == [] and ra.udc() == before and re.search(rf"rearm refused \(rc 4\): .*{re.escape(msg)}", ra.log()), ra.log()
    assert "REARM begin" not in ra.log() and ra.last().startswith("rc=4 ") and "refused:" in ra.last()


def test_rearm_old_flapmon_drop_and_old_inbox_file_do_not_block(ra):
    up = int(float(open("/proc/uptime").read().split()[0]))
    (ra.t / "gadget.log").write_text(f"flapmon {up - 70}: link drop (not attached), 1 in 60s\n")
    f = ra.t / "incoming/upload.tar"; f.write_text("x"); old = time.time() - 600; os.utime(f, (old, old))   # a leftover
    no_hid_no_adb(ra)
    assert ra.run().returncode == 0, ra.log()


def test_rearm_queued_by_the_operator_runs_beside_its_adb_command(ra):
    """The adb command that queued `--rearm` may still be closing when the watcher runs it: not refused for that; an adb
    push into the inbox still is."""
    _adbd_with_child(ra, f'{BB} sh -c "sleep 30; :"')
    no_hid_no_adb(ra)
    assert ra.run({"MK20_REARM_MANUAL": "1"}).returncode == 0, ra.log()
    (ra.t / "incoming/upload.tar").write_text("x")
    assert ra.run({"MK20_REARM_MANUAL": "1"}).returncode == 4


@pytest.mark.parametrize("why,msg", [("nogenv", "not a release composite"), ("drops", "FLAP_DROPS=3 < 4"),
                                     ("router", "mk20-router owns the HID"), ("noecm", "no ecm.usb0"),
                                     ("noadbd", "adbd for ffs.adb")])
def test_rearm_not_applicable(ra, why, msg):
    env = {}
    if why == "nogenv":
        (ra.t / "mk20/gadget.env").unlink()
    elif why == "drops":
        env["FD"] = "3"
    elif why == "router":
        (ra.t / "router.pids").write_text("4242\n")
    elif why == "noecm":
        os.rmdir(ra.t / "g/configs/c.1/ecm.usb0")
    elif why == "noadbd":
        (ra.t / "adbd").unlink()
    r = ra.run(env)
    assert r.returncode == 6 and ra.calls() == [] and msg in ra.log(), ra.log()


def test_rearm_stale_generation_ignored(ra):
    r = ra.run({"MK20_GEN": "12-999"})
    assert r.returncode == 5 and ra.calls() == [] and "stale generation 12-999" in ra.log() and ra.last().startswith("rc=5 ")


def _fight(ra, what):
    """bind: after the preconditions, every bind is undone at once; unbind: an unbind is undone at once."""
    u = ra.t / "g/UDC"
    loop = (f'sleep 0.5; while :; do [ -s {u} ] && : > {u}; sleep 0.02; done' if what == "bind" else
            f'while :; do [ -n "$(cat {u})" ] || echo {UDC} > {u}; sleep 0.02; done')
    ra.procs.append(subprocess.Popen([BB, "sh", "-c", loop]))


@pytest.mark.parametrize("what", ["bind", "unbind"])
def test_rearm_that_cannot_complete_requests_the_watcher_reboot(ra, what):
    """The UDC is not bound again (or never unbound): cmd-reboot (WDT hardreset), never mk20-no-composite."""
    no_hid_no_adb(ra)
    _fight(ra, what)
    r = ra.run()
    assert r.returncode == 2, ra.log()
    assert (ra.t / "sd/cmd-reboot").exists() and not (ra.t / "sd/mk20-no-composite").exists()
    want = "UDC not bound again" if what == "bind" else "UDC could not be unbound"
    assert f"REARM FAIL: {want}" in ra.log() and "-> cmd-reboot (watcher WDT hardreset)" in ra.log()


def test_rearm_owner_timeout_kills_its_child_and_keeps_usb(ra):
    """A rearm child that overruns MK20_REARM_TIMEOUT is killed by its owner; the UDC still bound -> rc 1, HID released."""
    ra.kbd(obeys=False); ra.adbd()
    t0 = time.time()
    r = ra.run({"MK20_REARM_TIMEOUT": "10", "MK20_KBD_HID_TICKS": "300"})
    assert r.returncode == 1 and 9 <= time.time() - t0 < 30, ra.log()
    assert "REARM TIMEOUT: exceeded 10s" in ra.log() and "REARM: UDC bound (and adbd up) after the timeout" in ra.log()
    assert ra.udc() == UDC and not (ra.t / "rstop").exists() and not (ra.t / "sd/cmd-reboot").exists()


def test_rearm_owner_timeout_without_adbd_takes_the_reboot_path(ra):
    """Timed out with the UDC bound but adb not running (state unknown): the watcher reboot, not a silent rc 1."""
    ra.kbd(obeys=False)                          # ffs.adb linked, no adbd running
    r = ra.run({"MK20_REARM_TIMEOUT": "10", "MK20_KBD_HID_TICKS": "300"})
    assert r.returncode == 2 and (ra.t / "sd/cmd-reboot").exists() and not (ra.t / "sd/mk20-no-composite").exists(), ra.log()


def test_rearm_block_restores_usb0_like_setup_main_and_never_reboots():
    body = re.sub(r"#.*", "", REARM)
    for bad in ("rmdir", "ln -s", "mkdir", "insmod", "setusbconfig", "kd_stop", "kbd_stop", "router_stop",
                "--rollback", "mk20-no-composite"):
        assert bad not in body, bad
    assert not re.search(r"(^|[;&|(]|\bthen|\bdo)\s*(/sbin/)?reboot\b", body, re.M)
    # the usb0 configuration is setup_main's, line for line
    setup = GS[GS.index("setup_main() {"):]
    for cmd in ('ifconfig usb0 mtu 478', 'ifconfig usb0 "$ECM_IP" netmask "$ECM_MASK" up',
                'ifconfig usb0:1 "$LL_IP" netmask 255.255.0.0 up'):
        assert cmd in setup and cmd in body, cmd
    i = REARM.index("rearm_main() {")
    m = REARM[i:REARM.index("\n}\n", i)]
    assert m.index("touch /tmp/mk20-router-stop") < m.index("killall adbd") < m.index('echo "" > $G/UDC') \
        < m.index("/bin/adbd </dev/null") < m.index('echo "$RUDC" > $G/UDC') < m.index("ra_netcfg || exit 2") \
        < m.index("ra_bridge || exit 2") < m.rindex("rm -f /tmp/mk20-router-stop")
    o = REARM[REARM.index('if [ "${1:-}" = --rearm-now ]'):]
    assert o.index("tl_lock 1") < o.index("rearm_main )") and "kill -9" not in m and "kill -TERM" not in m


def test_genv_whitelist_carries_the_link_local_address():
    """--rearm-now restores the usb0:1 alias mk20d configured at boot (Kimi K3: MK20_LL_IP in the GENV whitelist)."""
    loader = GS[GS.index('if [ -f "$GENV" ]; then'):GS.index("ECM_IP=${MK20_ECM_IP:-")]
    assert "MK20_LL_IP) MK20_LL_IP=$v;;" in loader and "LL_IP=${MK20_LL_IP:-169.254.7.2}" in GS
    assert "MK20_LL_IP=%s" in MK20D and "MK20_LL_IP=$LLIP" in MK20D


def test_rearm_runs_detached_only():
    """--rearm (from a shell) queues --rearm-now to the watcher; only --rearm-now runs in place (mk20d's detached loop)."""
    disp = GS[GS.index("MODE=${1:-}"):GS.index("KDSVC=/etc/init.d/qt_app2")]
    assert '[ "$MODE" != --rearm-now ]' in disp and '[ "${1:-}" = --rearm ] && { A=--rearm-now; M="MK20_REARM_MANUAL=1 "; }' in disp
    assert '"( ${M}MK20_REL=${MK20_REL:-} MK20_GADGET_LOG=$LOG sh' in disp


def test_rearm_queue_job_line(dev, tmp_path):
    """`gadget.sh --rearm` writes the watcher job: detached, --rearm-now, the operator mark, the release and log env."""
    d = tmp_path / "q"; d.mkdir()
    src = GS.replace("/mnt/SDCARD", f"{d}/sd").replace('GENV=/tmp/mk20/gadget.env', f'GENV={d}/gadget.env')
    (d / "gadget.env").write_text("MK20_ECM_IP=172.30.250.2\n")
    (d / "g.sh").write_text(src)
    r = subprocess.run([BB, "sh", str(d / "g.sh"), "--rearm"], env=dict(dev.env, MK20_REL=str(d), MK20_GADGET_LOG=str(d / "gl")),
                       capture_output=True, text=True, timeout=20)
    assert r.returncode == 0 and "queued to watcher" in r.stdout, (r.stdout, r.stderr)
    job = (d / "sd/job.sh").read_text()
    assert job.startswith(f"( MK20_REARM_MANUAL=1 MK20_REL={d} MK20_GADGET_LOG={d}/gl sh ") and " --rearm-now </dev/null" in job
    assert job.rstrip().endswith("& ); echo started") and (d / "sd/job.sh.sha256").exists()


def test_no_plain_reboot_in_supervisor_code():
    """The board's reboot path is the watcher's cmd-reboot -> WDT hardreset; a soft `reboot` hangs (PSCI)."""
    for name in ("release/sbin/mk20d", "release/share/gadget.sh", "release/share/flapmon.sh", "release/share/mk20lib.sh"):
        for n, line in enumerate((FW / name).read_text().splitlines(), 1):
            code = re.sub(r"'[^']*'|\"[^\"]*\"", "", line.split(" #")[0] if not line.lstrip().startswith("#") else "")
            assert not re.search(r"(^|[;&|(`]|\bthen|\bdo|\belse)\s*(/sbin/|/bin/)?reboot\b", code), f"{name}:{n}: {line}"


def test_mk20d_calls_rearm_with_its_locks_closed():
    """The maint lock (fd 5) and any other inherited lock fd never reach gadget.sh / adbd / ping."""
    line = next(l for l in MK20D.splitlines() if "--rearm-now </dev/null" in l)
    assert "5>&- 6>&- 7>&- 8>&- 9>&-" in line and "MK20_GEN=$gen" in line
    assert "</dev/null >/dev/null 2>&1 5>&- &" in MK20D[MK20D.index("ecm_probe() {"):MK20D.index("ecm_rb_get() {")]
