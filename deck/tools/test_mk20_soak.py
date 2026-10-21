"""mk20_soak: event accounting + summary always finalized (adb/device mocked)."""
import json
import os

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


def _device(monkeypatch, health):
    """sample() against a fake adb/device and Mac network (no subprocesses)."""
    def sh(cmd, timeout=20):
        return (0, "up") if cmd == "echo up" else (0, "kd=\nkbd=321\nlogkb=12 34\nbridgelog=99\n")
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
