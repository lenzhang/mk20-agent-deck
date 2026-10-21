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
