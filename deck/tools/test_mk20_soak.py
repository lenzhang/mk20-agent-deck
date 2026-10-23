"""mk20_soak: event accounting + summary always finalized (adb/device mocked)."""
import json
import os
import pathlib
import shutil
import subprocess

import pytest

import mk20_soak as soak


def _samples(seq):
    it = iter(seq)
    left = [len(seq)]

    def fake(deep):
        left[0] -= 1
        if left[0] == 0:
            soak.STOP = True
        return dict(next(it), time="t", ok=True)
    return fake


def test_events_and_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("MK20_ADB", "true")
    seq = [{"health": {"uptime_s": 100, "mem_avail_kb": 60000, "fat_free_kb": 1300}, "kd_pid": "10", "loader": {"last_rollback": "old"}},
           {"health": {"uptime_s": 400, "mem_avail_kb": 50000, "fat_free_kb": 1200}, "kd_pid": "11", "loader": {"last_rollback": "old"}},
           {"health": {"uptime_s": 50, "mem_avail_kb": 61000, "fat_free_kb": 1250}, "kd_pid": "12", "loader": {"last_rollback": "1.1.2->1.1.1"}}]
    monkeypatch.setattr(soak, "sample", _samples(seq))
    monkeypatch.setattr(soak.time, "sleep", lambda s: None)
    monkeypatch.setattr("sys.argv", ["x", "--hours", "1", "--interval", "0", "--out", str(tmp_path)])
    monkeypatch.setattr(soak, "STOP", False)
    assert soak.main() == 0
    sm = json.loads(next(p for p in tmp_path.iterdir() if p.name.endswith(".summary.json")).read_text())
    assert sm["running"] is False and "ended" in sm
    assert sm["samples"] == 3 and sm["reboots"] == 1 and sm["kd_restarts"] == 1
    assert sm["rollbacks"] == [{"time": "t", "record": "1.1.2->1.1.1"}]
    assert sm["min_mem_kb"] == 50000 and sm["min_fat_free_kb"] == 1200
    assert sm["kernel_mem_trend"] == "insufficient" and sm["kernel_mem"]["SUnreclaim"] is None   # no meminfo: no crash


def test_keyboard_owner_restart_counts_mk20_kbd(tmp_path, monkeypatch):
    monkeypatch.setenv("MK20_ADB", "true")
    # pid noise (a transient pid in sample 3) must not count; mk20d's restarts counter is authoritative
    seq = [{"health": {"uptime_s": 100, "kbd": {"restarts": 0}}, "kd_pid": "", "kbd_pid": "20", "loader": {}},
           {"health": {"uptime_s": 400, "kbd": {"restarts": 1}}, "kd_pid": "", "kbd_pid": "21", "loader": {}},
           {"health": {"uptime_s": 700, "kbd": {"restarts": 1}}, "kd_pid": "", "kbd_pid": "4864", "loader": {}}]
    monkeypatch.setattr(soak, "sample", _samples(seq))
    monkeypatch.setattr(soak.time, "sleep", lambda s: None)
    monkeypatch.setattr("sys.argv", ["x", "--hours", "1", "--interval", "0", "--out", str(tmp_path)])
    monkeypatch.setattr(soak, "STOP", False)
    assert soak.main() == 0
    sm = json.loads(next(p for p in tmp_path.iterdir() if p.name.endswith(".summary.json")).read_text())
    assert sm["kd_restarts"] == 1 and sm["reboots"] == 0


# ---- rc2: attribute degraded samples (selftest WARN/FAIL lines in the sample, counted in the summary) ----
DETAIL = ("PASS kd=0 (mk20-kbd mode) PASS mk20-kbd running WARN kbd status stale (12s, up 3600s) "
          "PASS kbd gd32 knob_ok=6 frames=10 PASS kbd panels 21/21 PASS udc bound INFO /run/mk20 on tmpfs "
          "INFO router running WARN web not running (http://172.30.250.2:8780) FAIL watchdog not armed")


def test_selftest_problems_split_like_mk20d_health():
    want = ["WARN kbd status stale (12s, up 3600s)", "WARN web not running (http://172.30.250.2:8780)",
            "FAIL watchdog not armed"]
    assert soak.selftest_problems(DETAIL) == want                      # health JSON: lines joined with spaces
    assert soak.selftest_problems(DETAIL.replace(" PASS", "\nPASS").replace(" WARN", "\nWARN")) == want
    assert soak.selftest_problems("") == [] and soak.selftest_problems(None) == [] and soak.selftest_problems(7) == []
    assert soak.selftest_problems("PASS a INFO WARNING b WARN x FAILED y") == ["WARN x FAILED y"]


def test_selftest_problems_bounded():
    many = " ".join(f"WARN w{i} " + "x" * 300 for i in range(20))
    got = soak.selftest_problems(many)
    assert len(got) == soak.WARN_LINES == 8 and all(len(x) == soak.WARN_CHARS == 160 for x in got)
    assert got[0].startswith("WARN w0 ") and got[7].startswith("WARN w7 ")


def _device(monkeypatch, health, meminfo=""):
    """sample() against a fake adb/device and Mac network (no subprocesses). Returns the adb shell commands."""
    def sh(cmd, timeout=20):
        calls.append(cmd)
        if cmd == "echo up":
            return 0, "up"
        if cmd == soak.MEMINFO_SH:
            return 0, meminfo
        return 0, "kd=\nkbd=321\nlogkb=12 34\nbridgelog=99\n"
    calls: list = []
    def run_proc(cmd, timeout, env=None):
        if cmd[0] == "route":
            return 0, "   interface: en9\n", ""
        return 0, "3 packets transmitted, 3 received, 0.0% packet loss\nround-trip min/avg/max/stddev = 1/2.5/3/0.1 ms\n", ""
    monkeypatch.setattr(soak.acc, "sh", sh)
    monkeypatch.setattr(soak.acc, "run_proc", run_proc)
    monkeypatch.setattr(soak.acc, "loader_status", lambda: {"good": "1.4.0", "current_v": "1.4.0"})
    monkeypatch.setattr(soak.acc, "health", lambda v: health)
    monkeypatch.setattr(soak.acc, "keyboard_check", lambda h: ("mk20-kbd", True))
    monkeypatch.setattr(soak.acc, "bridge_listening", lambda: (True, True))
    monkeypatch.setattr(soak.acc, "bridge_clients", lambda: 1)
    monkeypatch.setattr(soak.tt, "mk20_interface", lambda *a: "en9")
    return calls


def test_sample_keeps_selftest_warn_only_when_not_ok(monkeypatch):
    h = {"status": "degraded", "uptime_s": 3600, "kd": 0, "selftest": {"pass": True, "detail": DETAIL}, "usb": {"composite": True}}
    _device(monkeypatch, h)
    s = soak.sample(deep=False)
    assert s["ok"] is False and s["health"]["status"] == "degraded" and s["kbd_pid"] == "321"
    assert s["selftest_warn"] == soak.selftest_problems(DETAIL) and len(s["selftest_warn"]) == 3
    _device(monkeypatch, dict(h, status="ok", selftest={"pass": True, "detail": "PASS kd=0 (mk20-kbd mode)"}))
    s = soak.sample(deep=False)
    assert s["ok"] is True and "selftest_warn" not in s


def test_summary_counts_selftest_warn_per_message(tmp_path, monkeypatch):
    monkeypatch.setenv("MK20_ADB", "true")
    seq = [{"health": {"uptime_s": 100}, "selftest_warn": ["WARN kbd status stale (12s, up 100s)", "WARN web not running"]},
           {"health": {"uptime_s": 400}},
           {"health": {"uptime_s": 700}, "selftest_warn": ["WARN kbd status stale (31s, up 700s)"]},
           {"health": {"uptime_s": 1000}, "selftest_warn": ["FAIL watchdog not armed", "FAIL watchdog not armed"]}]
    monkeypatch.setattr(soak, "sample", _samples(seq))
    monkeypatch.setattr(soak.time, "sleep", lambda s: None)
    monkeypatch.setattr("sys.argv", ["x", "--hours", "1", "--interval", "0", "--out", str(tmp_path)])
    monkeypatch.setattr(soak, "STOP", False)
    assert soak.main() == 0
    sm = json.loads(next(p for p in tmp_path.iterdir() if p.name.endswith(".summary.json")).read_text())
    assert sm["selftest_warn"] == {"WARN kbd status stale (Ns, up Ns)": 2, "FAIL watchdog not armed": 1,
                                   "WARN web not running": 1}
    assert list(sm["selftest_warn"])[0] == "WARN kbd status stale (Ns, up Ns)"   # most frequent first
    lines = [json.loads(l) for l in next(p for p in tmp_path.iterdir() if p.name.endswith(".jsonl")).read_text().splitlines()]
    assert lines[0]["selftest_warn"] == seq[0]["selftest_warn"]                 # raw lines kept in the sample


# ---- kernel memory (/proc/meminfo Slab/SUnreclaim): slow kernel leaks under a flat MemAvailable ----
MEMINFO = """MemTotal:         115420 kB
MemFree:           41012 kB
MemAvailable:      62340 kB
Buffers:            1200 kB
Cached:            24100 kB
SwapCached:            7 kB
Active:            20000 kB
Active(anon):       5000 kB
Shmem:               812 kB
Slab:              15890 kB
SReclaimable:       4070 kB
SUnreclaim:        11820 kB
KernelStack:         600 kB
"""
MEMINFO_WANT = {"MemAvailable": 62340, "MemFree": 41012, "Slab": 15890, "SReclaimable": 4070,
                "SUnreclaim": 11820, "Shmem": 812, "Cached": 24100}


def test_parse_meminfo_proc_and_one_line_forms():
    assert soak.parse_meminfo(MEMINFO) == MEMINFO_WANT                    # SwapCached is not Cached
    one = " ".join(f"{k}:{v}" for k, v in MEMINFO_WANT.items())
    assert soak.parse_meminfo(one) == MEMINFO_WANT
    assert soak.parse_meminfo(one + " Cached:1") == MEMINFO_WANT          # first value wins


def test_parse_meminfo_missing_fields_are_none():
    none = dict.fromkeys(soak.MEMINFO_KEYS)
    for bad in (None, "", 7, b"Slab:1", "garbage", "Slab: n/a kB", "Slab:12x"):
        assert soak.parse_meminfo(bad) == none
    old = soak.parse_meminfo("MemFree:41012 Cached:24100 Slab:15890")   # e.g. an old kernel: no MemAvailable/SUnreclaim
    assert old == dict(none, MemFree=41012, Cached=24100, Slab=15890)
    assert soak.parse_meminfo("Active(anon):5000 Shmem:812")["Shmem"] == 812


def _busybox():
    return os.environ.get("MK20_TEST_BUSYBOX") or shutil.which("busybox")


def test_meminfo_shell_command_on_device_awk(tmp_path):
    """The real device-side snippet (BusyBox 1.27.2 awk when MK20_TEST_BUSYBOX is set, e.g. in Docker)."""
    fx = pathlib.Path(soak.HERE).parent / "tests/fixtures/device-commands.txt"
    if not fx.exists():
        pytest.skip("device command fixture not in this tree")
    bb = _busybox()
    if not bb:
        pytest.skip("BusyBox unavailable: the device-side awk must be tested with BusyBox (MK20_TEST_BUSYBOX)")
    devcmds = fx.read_text().split("\n")
    assert "awk" in devcmds and "echo" in soak.MEMINFO_SH.split()[0]
    (tmp_path / "meminfo").write_text(MEMINFO)
    cmd = soak.MEMINFO_SH.replace("/proc/meminfo", str(tmp_path / "meminfo"))
    assert cmd != soak.MEMINFO_SH
    d = tmp_path / "bin"
    d.mkdir()
    (d / "awk").symlink_to(bb)
    out = subprocess.run([bb, "sh", "-c", cmd], capture_output=True, text=True, env={"PATH": str(d)}, timeout=10)
    assert out.returncode == 0 and out.stdout.count("\n") == 1 and out.stdout.startswith("meminfo=")
    assert soak.parse_meminfo(out.stdout.split("=", 1)[1]) == MEMINFO_WANT
    gone = soak.MEMINFO_SH.replace("/proc/meminfo", str(tmp_path / "nope"))   # unreadable: empty, not an error
    out = subprocess.run(["/bin/sh", "-c", gone], capture_output=True, text=True, timeout=10)
    assert out.stdout == "meminfo=\n" and soak.parse_meminfo("") == dict.fromkeys(soak.MEMINFO_KEYS)


def test_sample_reads_meminfo_in_its_own_short_adb_call(monkeypatch):
    h = {"status": "ok", "uptime_s": 3600, "kd": 0, "selftest": {"pass": True}, "usb": {"composite": True}}
    one = " ".join(f"{k}:{v}" for k, v in MEMINFO_WANT.items())
    calls = _device(monkeypatch, h, meminfo=f"meminfo={one}\n")
    s = soak.sample(deep=False)
    assert s["ok"] is True and s["meminfo"] == MEMINFO_WANT and s["kbd_pid"] == "321" and s["bridge_log_b"] == "99"
    assert calls == ["echo up", calls[1], soak.MEMINFO_SH] and "echo kd=" in calls[1]   # one extra short call
    assert all(soak.acc.adb_payload_len(c) <= 400 for c in calls)   # well below the 512-byte adbd limit
    for out in ("", "meminfo=\n", "meminfo=Slab:oops\n"):              # old/odd output: nulls, sample still ok
        _device(monkeypatch, h, meminfo=out)
        s = soak.sample(deep=False)
        assert s["ok"] is True and s["meminfo"] == dict.fromkeys(soak.MEMINFO_KEYS)


def _line(kb_h, hours, start=11820.0, every=300, t0=1_000_000):
    return [(t0 + i * every, start + kb_h * i * every / 3600) for i in range(int(hours * 3600 / every) + 1)]


def test_slope_least_squares_and_minimums():
    assert soak.slope_kb_h(_line(30, 2)) == 30.0
    assert soak.slope_kb_h(_line(-120, 1.5)) == -120.0
    noisy = [(t, v + (40 if i % 2 else -40)) for i, (t, v) in enumerate(_line(25, 3))]
    assert abs(soak.slope_kb_h(noisy) - 25) < 2
    assert soak.slope_kb_h(_line(30, 2)[:5]) is None                     # < 6 samples
    assert soak.slope_kb_h(_line(30, 0.9)) is None                       # < 1 h
    assert soak.slope_kb_h(_line(30, 1, every=600)) == 30.0              # 7 samples over 1 h
    assert soak.slope_kb_h([]) is None and soak.slope_kb_h([(1, 2)] * 10) is None
    mixed = _line(30, 2) + [(None, 5), (2_000_000, None), (2_000_001, "7"), (2_000_002, True)]
    assert soak.slope_kb_h(mixed) == 30.0                               # non-numbers ignored


def test_kernel_mem_trend_flag():
    assert soak.TREND_KB_H == 50 and soak.TREND_MIN_H == 6
    assert soak.kernel_mem_trend(_line(60, 7)) == "growing"
    assert soak.kernel_mem_trend(_line(30, 7)) == "flat"                 # the 25-30 kB/h seen after boot
    assert soak.kernel_mem_trend(_line(-60, 7)) == "flat"
    assert soak.kernel_mem_trend(_line(60, 5.9)) == "insufficient"       # not 6 h yet
    assert soak.kernel_mem_trend(_line(60, 7, every=7 * 3600)) == "insufficient"   # 2 samples
    assert soak.kernel_mem_trend([]) == "insufficient" and soak.kernel_mem_trend([(None, None)] * 99) == "insufficient"
    step = [(t, v + (600 if t >= 1_000_000 + 2 * 3600 else 0)) for t, v in _line(0, 7)]
    assert soak.slope_kb_h(step) > 50 and soak.kernel_mem_trend(step) == "flat"   # one-off step, not sustained
    late = _line(0, 4) + _line(80, 4, start=11820, t0=1_000_000 + 4 * 3600)[1:]
    assert soak.kernel_mem_trend(late) == "flat"                         # growth only in the second half
    gap = _line(60, 1) + [(1_000_000 + 7 * 3600, 12240)]                 # adb gone for 6 h: one late sample
    assert soak.kernel_mem_trend(gap) == "insufficient"


def _timed(monkeypatch, seq, every=300):
    """main() with a fake host clock: sample i is taken at t0 + i * every."""
    clock = [1_000_000.0]
    monkeypatch.setattr(soak.time, "time", lambda: clock[0])
    it, left = iter(enumerate(seq)), [len(seq)]

    def fake(deep):
        i, x = next(it)
        left[0] -= 1
        if left[0] == 0:
            soak.STOP = True
        clock[0] += every
        return dict(x, time=f"t{i}", ok=True)
    monkeypatch.setattr(soak, "sample", fake)
    monkeypatch.setattr(soak.time, "sleep", lambda s: None)
    monkeypatch.setattr(soak, "STOP", False)


def _summary(tmp_path):
    return json.loads(next(p for p in tmp_path.iterdir() if p.name.endswith(".summary.json")).read_text())


def test_summary_kernel_mem_growing_and_robust(tmp_path, monkeypatch):
    monkeypatch.setenv("MK20_ADB", "true")
    seq = []
    for i in range(12 * 7 + 1):                                  # 7 h every 5 min: SUnreclaim +60 kB/h, MemAvailable -60
        mi = dict(MEMINFO_WANT, SUnreclaim=11820 + 5 * i, Slab=15890 + 5 * i - 300 * (i == 40), MemAvailable=62340 - 5 * i)
        seq.append({"health": {"uptime_s": 100 + 300 * i}, "meminfo": mi})
    seq[10] = {"health": {"uptime_s": 3100}, "meminfo": dict.fromkeys(soak.MEMINFO_KEYS)}   # parse failure
    seq[11] = {"health": {"uptime_s": 3400}, "meminfo": None}
    seq[12] = {"health": {"uptime_s": 3700}}                                              # old tool/sample
    seq[13] = {"health": {"uptime_s": 4000, "mem_avail_kb": 62340 - 65}, "meminfo": "garbage"}
    _timed(monkeypatch, seq)
    monkeypatch.setattr("sys.argv", ["x", "--hours", "100", "--interval", "0", "--out", str(tmp_path)])
    assert soak.main() == 0
    sm = _summary(tmp_path)
    km = sm["kernel_mem"]
    assert sm["samples"] == 85 and sm["kernel_mem_trend"] == "growing"
    assert km["SUnreclaim"] == {"first": 11820, "last": 11820 + 5 * 84, "min": 11820, "max": 11820 + 5 * 84}
    assert km["Slab"] == {"first": 15890, "last": 15890 + 420, "min": 15890 + 200 - 300, "max": 15890 + 420}
    assert km["sunreclaim_kb_h"] == 60.0 and km["mem_avail_kb_h"] == -60.0 and km["sunreclaim_half_kb_h"] == [60.0, 60.0]
    assert km["slope_since"] == "t0" and km["slope_samples"] == 81 and km["slope_hours"] == 7.0
    lines = [json.loads(l) for l in next(p for p in tmp_path.iterdir() if p.name.endswith(".jsonl")).read_text().splitlines()]
    assert lines[0]["meminfo"]["SUnreclaim"] == 11820 and lines[10]["meminfo"]["Slab"] is None


def test_summary_kernel_mem_slopes_restart_at_reboot(tmp_path, monkeypatch):
    monkeypatch.setenv("MK20_ADB", "true")
    seq = [{"health": {"uptime_s": 100 + 300 * i}, "meminfo": dict(MEMINFO_WANT, SUnreclaim=13000 + 50 * i)}
           for i in range(24)]                                                     # 2 h of +600 kB/h
    seq += [{"health": {"uptime_s": 60 + 300 * i}, "meminfo": dict(MEMINFO_WANT, SUnreclaim=11820 + i % 2)}
            for i in range(13)]                                                    # reboot, then 1 h flat
    _timed(monkeypatch, seq)
    monkeypatch.setattr("sys.argv", ["x", "--hours", "100", "--interval", "0", "--out", str(tmp_path)])
    assert soak.main() == 0
    sm = _summary(tmp_path)
    km = sm["kernel_mem"]
    assert sm["reboots"] == 1 and km["slope_since"] == "t24" and km["slope_samples"] == 13
    assert abs(km["sunreclaim_kb_h"]) < 2 and sm["kernel_mem_trend"] == "insufficient"
    assert km["SUnreclaim"]["first"] == 13000 and km["SUnreclaim"]["max"] == 13000 + 50 * 23 and km["SUnreclaim"]["last"] == 11820
    assert km["mem_avail_kb_h"] == 0.0


def test_every_adb_request_of_a_sample_stays_far_below_the_adbd_limit(monkeypatch):
    # The device's adbd fails a request over 512 bytes (EOVERFLOW) and the whole USB gadget re-enumerates.
    h = {"status": "ok", "uptime_s": 3600, "kd": 0, "selftest": {"pass": True}, "usb": {"composite": True}}
    calls = _device(monkeypatch, h)
    soak.sample(deep=True)
    assert calls and max(soak.acc.adb_payload_len(c) for c in calls) <= 400


def test_accept_sh_refuses_an_oversized_adb_request(monkeypatch):
    sent = []
    monkeypatch.setattr(soak.acc, "run_proc", lambda cmd, timeout, env=None: sent.append(cmd) or (0, "ok", ""))
    monkeypatch.setattr(soak.acc, "adb_cmd", lambda: ["adb"])
    ok = "x" * (soak.acc.ADB_SHELL_MAX - len("shell:") - 1)
    assert soak.acc.adb_payload_len(ok) == soak.acc.ADB_SHELL_MAX and soak.acc.sh(ok)[0] == 0 and len(sent) == 1
    with pytest.raises(ValueError, match="EOVERFLOW"):
        soak.acc.sh(ok + "y")
    assert len(sent) == 1   # never sent
    assert soak.acc.adb_payload_len("ü") == len("shell:") + 2 + 1   # bytes, not characters


def test_recent_slope_shows_a_late_leak_and_half_slopes_need_half_an_hour():
    late = _line(0, 4) + _line(80, 4, start=11820, t0=1_000_000 + 4 * 3600)[1:]
    assert soak.kernel_mem_trend(late) == "flat" and soak.recent_slope_kb_h(late) > 50   # visible in the last 6 h
    assert soak.recent_slope_kb_h(_line(80, 5)) is None                                   # not 6 h of data yet
    assert soak.recent_slope_kb_h(_line(0, 7)) == 0
    short = [(1_000_000 + i * 60, 11800 + i * 10) for i in range(8)]                     # 7 minutes of samples
    assert soak.half_slopes_kb_h(short) == [None, None]
    assert soak.kernel_mem_summary({}, {"SUnreclaim": late, "MemAvailable": []}, "t")["sunreclaim_last6h_kb_h"] > 50
