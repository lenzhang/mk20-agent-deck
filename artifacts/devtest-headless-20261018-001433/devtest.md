# MK20 devtest 报告

- 开始：2026-10-18T00:14:37+08:00
- 结束：2026-10-18T00:21:51+08:00

| 项 | 结果 | 要点 |
|---|---|---|
| usb | ✅ pass | T113 CDC=True GD32 VIA=True  |
| port | ✅ pass | /dev/cu.usbmodem1101 |
| getinfo | ✅ pass | 画布 640×656，键 20，起始 0，握手 198.9 ms |
| methods | ✅ pass |  |
| files | ✅ pass |  |
| display | ✅ pass | ACK p50/p95 12.5/19.1 ms；满屏 pattern_q85 63.44 fps，noise_q85 18.0 fps；建议 max_fps 8.0 |
| backlight | ✅ pass |  |
| keys | ❌ fail | 事件起始 0（getInfo 0），缺失 3 |
| knobs | ⚠️ warn | 静默 5 项；knob_map 建议 1 项 |
| ptt | ✅ pass | CDC 可见=True 按住 160.2 ms IME=None |
| timing | ✅ pass | 单击 [106, 54, 101, 103, 109] 双击间隔 [217, 158, 218] 长按 [927, 865, 657] ms |
| hid_leak | ❌ fail | P0 硬门槛：19 个非 PTT 键与两旋钮不得产生副作用 HID；泄漏→按 VIA KC_NO→现有配置→hiduti |
| reconnect | ❌ fail | 拔线检测 9106.0 ms，重新就绪 None ms |
| sleep | ⏭ skip | 未启用（--with-sleep；无头模式不支持） |
| via | ✅ pass | VIA v12 4 层 4×5，备份 /Users/dev/.config/mk20-agent-deck/devtest/20261018-001433/via-backup.json |

## 建议配置（~/.config/mk20-agent-deck/config.toml）

```toml
[deck]
fw_coord_base = 0
max_fps = 8.0

[knobs]
"103,103" = "K2:press"
```

## 明细

### usb

```json
{
 "status": "pass",
 "source": "ioreg",
 "notes": [],
 "t113_cdc": true,
 "gd32_via": true,
 "gd32_dfu": false,
 "t113_fel": false,
 "devices": [
  {
   "vid": "0x05e3",
   "pid": "0x0610",
   "name": "USB2.0 Hub@00100000",
   "serial": "",
   "location": "0x100000",
   "role": "GL852G hub"
  },
  {
   "vid": "0x4250",
   "pid": "0x426f",
   "name": "syk_keyboards@00120000",
   "serial": "",
   "location": "0x120000",
   "role": "GD32 QMK/VIA keyboard (HID)"
  },
  {
   "vid": "0x1d6b",
   "pid": "0x0104",
   "name": "Serial@00110000",
   "serial": "",
   "location": "0x110000",
   "role": "T113 gadget (CDC-ECM + CDC-ACM, Protocol A)"
  }
 ]
}
```

### port

```json
{
 "status": "pass",
 "ports": [
  "/dev/cu.usbmodem1101"
 ],
 "usb_ids": [
  "1d6b:0104",
  "1234:5678"
 ],
 "users": {
  "pids": [],
  "screenkey": []
 },
 "note": ""
}
```

### getinfo

```json
{
 "status": "pass",
 "handshake_ms": 198.9,
 "canvas": [
  640,
  656
 ],
 "matches_default_guess": true,
 "key_rects": 20,
 "fw_base_inferred": 0,
 "cols": [
  0,
  1,
  2,
  3,
  4
 ],
 "rows": [
  0,
  1,
  2,
  3
 ],
 "subscreen": [
  106,
  0,
  428,
  142
 ],
 "model": "MK20",
 "version": "V2.32",
 "problems": []
}
```

### methods

```json
{
 "status": "pass",
 "available": [
  "deleteFiles",
  "getFilesBySuffix",
  "getInfo",
  "keyboardInput",
  "playAudio",
  "saveToFile",
  "setBacklight",
  "setFileCRC",
  "setVolume",
  "stopAudio"
 ],
 "new_vs_known": []
}
```

### files

```json
{
 "status": "pass",
 "suffixes": [
  ".jpg",
  ".png",
  ".gif",
  ".wav",
  ".mp3",
  ".json",
  ".txt",
  ".ini",
  ".conf",
  ".bin",
  ".sh",
  ".zip"
 ],
 "count": 0,
 "files": [],
 "error": null,
 "note": "协议只有列表/写入/删除，没有读文件方法 → 设备文件无法经 CDC 备份；P0 不写 T113 文件"
}
```

### display

```json
{
 "status": "pass",
 "jpeg": {
  "60": {
   "pattern_bytes": 39607,
   "noise_bytes": 176671
  },
  "85": {
   "pattern_bytes": 54169,
   "noise_bytes": 281298
  },
  "95": {
   "pattern_bytes": 74891,
   "noise_bytes": 434870
  }
 },
 "ack": {
  "n": 20,
  "p50_ms": 12.5,
  "p95_ms": 19.1,
  "mean_ms": 13.5,
  "timeouts": 0
 },
 "full_screen": {
  "pattern_q85": {
   "frames": 318,
   "seconds": 5.01,
   "fps": 63.44,
   "ack_p50_ms": 15.2,
   "ack_p95_ms": 20.0,
   "bytes": 56265
  },
  "noise_q85": {
   "frames": 90,
   "seconds": 5.0,
   "fps": 18.0,
   "ack_p50_ms": 56.3,
   "ack_p95_ms": 71.5,
   "bytes": 283158
  }
 },
 "wrong_resolution": {
  "acked": false,
  "device_errors": [
   "Incorrect resolution, the device requires the resolution to be 640x656"
  ]
 },
 "visual_ok": null,
 "needs_manual_confirmation": "目视：键面坐标是否正确（无错位/镜像）——无头模式无法自动判定",
 "recommended_max_fps": 8.0
}
```

### backlight

```json
{
 "status": "pass",
 "level": 80,
 "ack": {
  "method": "setBacklight",
  "success": true,
  "result": {},
  "error": null
 },
 "note": "原背光值不可读，结束后请手动调回"
}
```

### keys

```json
{
 "status": "fail",
 "event_fw_base": 0,
 "getinfo_fw_base": 0,
 "offsets": [
  [
   0,
   0
  ]
 ],
 "missing": [
  "1,1",
  "2,1",
  "3,1"
 ],
 "transposed_suspect": false
}
```

### knobs

```json
{
 "status": "warn",
 "silent_steps": [
  "knob:K1:cw",
  "knob:K1:ccw",
  "knob:K1:press",
  "knob:K2:cw",
  "knob:K2:ccw"
 ],
 "note": "无 CDC 事件的动作可能只走 GD32 HID（需 VIA/hidutil 净化，见 §6.5）",
 "suggested_knob_map": {
  "103,103": "K2:press"
 }
}
```

### ptt

```json
{
 "status": "pass",
 "cdc_observed": true,
 "hold_ms": 160.2,
 "fw": [
  0,
  3
 ],
 "ime_triggered": null,
 "note": "",
 "window": [
  45957.904384583,
  45961.425723625
 ],
 "hid_activity": [],
 "right_option_seen": false,
 "needs_manual_confirmation": "IME 语音是否被触发需目视确认（无头模式只能看到 flagsChanged）"
}
```

### timing

```json
{
 "status": "pass",
 "tap_ms": [
  106,
  54,
  101,
  103,
  109
 ],
 "double_gap_ms": [
  217,
  158,
  218
 ],
 "long_ms": [
  927,
  865,
  657
 ],
 "long_press_ms": 600.0,
 "double_tap_ms": 350.0,
 "checks": {
  "tap_below_long_press": true,
  "double_gap_below_window": true,
  "long_above_threshold": true
 }
}
```

### hid_leak

```json
{
 "status": "fail",
 "auto": true,
 "monitor": {
  "source": "CGEventSourceSecondsSinceLastEventType",
  "listen_access": false,
  "types": [
   "keyDown",
   "flagsChanged",
   "systemDefined",
   "leftMouseDown",
   "scrollWheel"
  ]
 },
 "guided_window_s": 363.8,
 "correlated": 0,
 "uncorrelated": 9,
 "correlated_by_type": {},
 "uncorrelated_by_type": {
  "keyDown": 3,
  "systemDefined": 6
 },
 "window_s": 0.35,
 "hid_only_keys": [
  "1,1",
  "2,1",
  "3,1"
 ],
 "knob_steps_with_hid": [
  "knob:K2:cw"
 ],
 "limitation": "只能看到“有无 HID 事件及类型”，看不到字符与来源设备；与 MK20 键事件时间相关(±0.35 s)即判泄漏；期间若有人用 Mac 键盘会产生不相关样本(→warn)",
 "note": "P0 硬门槛：19 个非 PTT 键与两旋钮不得产生副作用 HID；泄漏→按 VIA KC_NO→现有配置→hidutil 顺序处理"
}
```

### reconnect

```json
{
 "status": "fail",
 "unplug_detect_ms": 9106.0,
 "port_back_ms": null,
 "handshake_ready_ms": null,
 "same_port": true,
 "last_error": ""
}
```

### sleep

```json
{
 "status": "skip",
 "reason": "未启用（--with-sleep；无头模式不支持）"
}
```

### via

```json
{
 "status": "pass",
 "backup": "/Users/dev/.config/mk20-agent-deck/devtest/20261018-001433/via-backup.json",
 "protocol_version": 12,
 "layers": 4,
 "matrix": {
  "rows": 4,
  "cols": 5,
  "detected": true
 },
 "layer0_side_effect_keys": 20,
 "layer0_has_ralt": false,
 "encoders_layer0": [
  {
   "error": "VIA command 0x14 unhandled by firmware"
  },
  {
   "error": "VIA command 0x14 unhandled by firmware"
  }
 ],
 "note": "只读导出；KC_NO 净化/PTT KC_RALT 需另行批准后用 via-restore 写入"
}
```
