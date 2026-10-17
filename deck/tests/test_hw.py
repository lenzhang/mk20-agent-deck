import json
import os
import struct
import threading
import time

import pytest

from mk20deck.adapters.base import RecordingRunner, RunResult
from mk20deck.hw import gd32dfu
from mk20deck.hw.power import PowerMonitor
from mk20deck.hw.usbenum import mk20_summary, parse_ioreg, parse_system_profiler
from mk20deck.hw.via import (KC_RALT, NEVER_CMDS, READ_ONLY_CMDS, Backup, MockViaKeyboard, ViaClient, ViaError,
                             apply_restore, backup, plan_restore)

VENDOR_BIN = "/workspace/mk20-research/gd32/syk_keyboards_mk20_plus_via.bin"


# ------------------------------------------------------------------ VIA
def test_via_backup_is_read_only_and_detects_matrix(tmp_path):
    kb = MockViaKeyboard(rows=4, cols=6, layers=4)
    b = backup(ViaClient(kb))
    assert (b.rows, b.cols) == (4, 6) and b.data["matrix"]["detected"]
    assert b.data["keymap"][0][3][5] == KC_RALT and b.data["keymap_names"][0][3][5] == "KC_RALT"
    assert b.data["keymap"][0][0][0] == 0x04 and b.data["keymap_vs_raw_mismatch"] == []
    assert b.data["encoders"][0][0] == {"ccw": 0x81, "cw": 0x80}
    assert b.data["macros"]["size"] == 64
    assert kb.writes == [] and set(kb.received) <= READ_ONLY_CMDS
    p = tmp_path / "b.json"
    b.save(p)
    assert Backup.load(p).data["raw_keymap_sha256"] == b.data["raw_keymap_sha256"]


def test_via_detects_other_shapes():
    for rows, cols in ((1, 22), (5, 5), (4, 5)):
        b = backup(ViaClient(MockViaKeyboard(rows=rows, cols=cols, layers=2)))
        assert (b.rows, b.cols) == (rows, cols)


def test_via_restore_dry_run_then_write(tmp_path):
    kb = MockViaKeyboard()
    b = backup(ViaClient(kb))
    kb._set(0, 1, 2, 0x29)
    kb.enc[(1, 1, 0)] = 0x7F
    kb.macros[3] = 9
    ro = ViaClient(kb)
    ops = plan_restore(ro, b, include_macros=True)
    assert [o[0] for o in ops] == ["key", "enc", "macros"] and kb.writes == []
    with pytest.raises(PermissionError):
        apply_restore(ro, ops)
    assert apply_restore(ViaClient(kb, allow_writes=True), ops) == []
    assert plan_restore(ViaClient(kb), b, include_macros=True) == []


def test_via_never_sends_destructive_commands():
    c = ViaClient(MockViaKeyboard(), allow_writes=True)
    for cmd in NEVER_CMDS:
        with pytest.raises(PermissionError):
            c._cmd(cmd)


def test_via_restore_refuses_protocol_mismatch():
    b = backup(ViaClient(MockViaKeyboard(protocol=12)))
    with pytest.raises(ViaError):
        plan_restore(ViaClient(MockViaKeyboard(protocol=9)), b)


def test_via_old_protocol_without_encoders():
    kb = MockViaKeyboard(encoder_support=False)
    b = backup(ViaClient(kb))
    assert "error" in b.data["encoders"][0][0]
    assert plan_restore(ViaClient(kb), b) == []


def test_via_ambiguous_matrix_needs_dims():
    kb = MockViaKeyboard()
    kb.eeprom[:] = bytes(len(kb.eeprom))
    with pytest.raises(ViaError):
        backup(ViaClient(kb))
    assert backup(ViaClient(kb), rows=4, cols=6).rows == 4


# ------------------------------------------------------------------ USB
SP_JSON = json.dumps({"SPUSBDataType": [{"_name": "USB31Bus", "_items": [
    {"_name": "USB2.0 Hub", "vendor_id": "0x05e3  (Genesys Logic, Inc.)", "product_id": "0x0610", "_items": [
        {"_name": "MK20", "vendor_id": "0x1d6b  (Linux Foundation)", "product_id": "0x0104", "serial_num": "abc"},
        {"_name": "MK20 Plus", "vendor_id": "0x4250", "product_id": "0x426f"}]}]}]})

IOREG = """+-o Root  <class IORegistryEntry>
  +-o MK20@01100000  <class IOUSBHostDevice>
      "idProduct" = 260
      "idVendor" = 7531
      "locationID" = 17825792
  +-o maple 003@01200000  <class IOUSBHostDevice>
      "idProduct" = 3
      "idVendor" = 7855
"""


def test_usb_parsers_and_roles():
    devs = parse_system_profiler(SP_JSON)
    assert [(d.vid, d.pid) for d in devs] == [(0x05E3, 0x0610), (0x1D6B, 0x0104), (0x4250, 0x426F)]
    from mk20deck.hw.usbenum import KNOWN
    for d in devs:
        d.role = KNOWN.get((d.vid, d.pid), "")
    s = mk20_summary(devs)
    assert s["t113_cdc"] and s["gd32_via"] and not s["gd32_dfu"]
    io = parse_ioreg(IOREG)
    assert [(d.vid, d.pid) for d in io] == [(0x1D6B, 0x0104), (0x1EAF, 0x0003)]
    for d in io:
        d.role = KNOWN.get((d.vid, d.pid), "")
    assert mk20_summary(io)["gd32_dfu"]


def test_enumerate_usb_darwin_uses_system_profiler():
    from mk20deck.hw.usbenum import enumerate_usb
    r = RecordingRunner({("system_profiler",): RunResult(0, SP_JSON, "")})
    devs, src = enumerate_usb(r, platform="Darwin")
    assert src == "system_profiler" and len(devs) == 3 and devs[1].role.startswith("T113")
    assert r.calls == [["system_profiler", "-json", "SPUSBDataType"]]


# ------------------------------------------------------------------ GD32 DFU
DFU_LIST = ('Found DFU: [1eaf:0003] ver=0201, devnum=5, cfg=1, intf=0, path="1-1", alt=2, '
            'name="STM32duino bootloader v1.0  Upload to Flash 0x8002000", serial="LLM 003"\n'
            'Found DFU: [1eaf:0003] ver=0201, devnum=5, cfg=1, intf=0, path="1-1", alt=1, '
            'name="STM32duino bootloader v1.0  Upload to Flash 0x8005000", serial="LLM 003"\n')


@pytest.fixture
def image(tmp_path):
    if os.path.exists(VENDOR_BIN):
        return VENDOR_BIN
    p = tmp_path / "fw.bin"
    p.write_bytes(struct.pack("<II", 0x20000400, 0x08002239) + bytes(1000))
    return str(p)


def test_gd32_plan_dry_run(image):
    r = RecordingRunner({("dfu-util", "-l"): RunResult(0, DFU_LIST, "")}, binaries={"dfu-util"})
    p = gd32dfu.plan_flash(image, r, allow_unknown=True)
    assert p.ok, p.problems
    assert p.alt == 2 and p.argv == ["dfu-util", "-d", "1eaf:0003", "-a", "2", "-D", image, "-R"]
    assert r.calls == [["dfu-util", "-l"]]  # listing only, nothing flashed
    rc, _ = gd32dfu.execute(p, r)
    assert r.calls[-1][:3] == ["dfu-util", "-d", "1eaf:0003"]


def test_gd32_known_vendor_image():
    if not os.path.exists(VENDOR_BIN):
        pytest.skip("vendor bin not on this machine")
    sha, size, sp, reset = gd32dfu.inspect_image(VENDOR_BIN)
    assert sha in gd32dfu.KNOWN_IMAGES and size == 39552 and reset == 0x08002239


def test_gd32_refuses_without_dfu_or_with_bad_image(tmp_path, image):
    r = RecordingRunner({("dfu-util", "-l"): RunResult(0, "", "")}, binaries={"dfu-util"})
    p = gd32dfu.plan_flash(image, r, allow_unknown=True)
    assert not p.ok and any("1eaf:0003" in x for x in p.problems)
    with pytest.raises(RuntimeError):
        gd32dfu.execute(p, r)
    bad = tmp_path / "bad.bin"
    bad.write_bytes(struct.pack("<II", 0x20000400, 0x08000101) + bytes(100))
    p = gd32dfu.plan_flash(str(bad), RecordingRunner({("dfu-util", "-l"): RunResult(0, DFU_LIST, "")},
                                                     binaries={"dfu-util"}))
    assert any("not the known vendor image" in x for x in p.problems)
    assert any("reset vector" in x for x in p.problems)


# ------------------------------------------------------------------ power
def test_power_monitor_detects_sleep_and_display():
    wall, mono = [1000.0], [10.0]
    disp = [False]
    pm = PowerMonitor(lambda: disp[0], gap_s=8, wall=lambda: wall[0], mono=lambda: mono[0])
    assert pm.poll() == []
    wall[0] += 2; mono[0] += 2
    assert pm.poll() == []
    disp[0] = True
    assert pm.poll() == ["display_sleep"]
    wall[0] += 600; mono[0] += 1  # suspended: monotonic stood still
    disp[0] = False
    assert pm.poll() == ["system_wake", "display_wake"] and pm.last_sleep_s == pytest.approx(599)


def test_via_detects_matrix_when_layer0_is_all_kc_no():
    """Real MK20: layer 0 sanitized to KC_NO, layers 1-3 KC_TRNS, 4x5 matrix."""
    from mk20deck.hw.via import MockViaKeyboard, ViaClient, backup, KC_NO, KC_TRNS
    kb = MockViaKeyboard(rows=4, cols=5, encoder_support=False)
    for l in range(kb.layers):
        for r in range(4):
            for c in range(5):
                kb._set(l, r, c, KC_NO if l == 0 else KC_TRNS)
    b = backup(ViaClient(kb), encoders=2)
    assert (b.rows, b.cols) == (4, 5) and b.data["matrix"]["detected"]
    assert not kb.writes


def test_via_set_one_and_parse_keycode():
    from mk20deck.hw.via import KC_RALT, MockViaKeyboard, ViaClient, ViaError, parse_keycode, set_one
    assert parse_keycode("KC_RALT") == parse_keycode("ralt") == parse_keycode("0xE6") == KC_RALT
    kb = MockViaKeyboard(rows=4, cols=5)
    with pytest.raises((ViaError, PermissionError)):
        set_one(ViaClient(kb), 0, 3, 0, KC_RALT)  # read-only client refuses writes
    old, new = set_one(ViaClient(kb, allow_writes=True), 0, 3, 0, KC_RALT)
    assert new == KC_RALT and ViaClient(kb).keycode(0, 3, 0) == KC_RALT
