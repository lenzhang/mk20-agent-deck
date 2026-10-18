# MK20 devtest 报告

- 开始：2026-10-18T00:28:39+08:00
- 结束：2026-10-18T00:30:19+08:00

| 项 | 结果 | 要点 |
|---|---|---|
| usb | ✅ pass | T113 CDC=True GD32 VIA=True  |
| port | ✅ pass | /dev/cu.usbmodem1101 |
| getinfo | ✅ pass | 画布 640×656，键 20，起始 0，握手 156.0 ms |
| methods | ⏭ skip | 不在本次 --only 范围内 |
| files | ⏭ skip | 不在本次 --only 范围内 |
| display | ⏭ skip | 不在本次 --only 范围内 |
| backlight | ✅ pass |  |
| keys | ✅ pass | 事件起始 0（getInfo 0），缺失 0 |
| knobs | ✅ pass | 静默 0 项；knob_map 建议 6 项 |
| ptt | ✅ pass | CDC 可见=True 按住 108.7 ms IME=None |
| timing | ⏭ skip | 不在本次 --only 范围内 |
| hid_leak | ✅ pass | P0 硬门槛：19 个非 PTT 键与两旋钮不得产生副作用 HID；泄漏→按 VIA KC_NO→现有配置→hiduti |
| reconnect | ✅ pass | 拔线检测 3904.0 ms，重新就绪 32773.1 ms |
| sleep | ⏭ skip | 不在本次 --only 范围内 |
| via | ⏭ skip | 不在本次 --only 范围内 |

## 建议配置（~/.config/mk20-agent-deck/config.toml）

```toml
[deck]
fw_coord_base = 0

[knobs]
"102,102" = "K1:cw"
"101,101" = "K1:ccw"
"100,100" = "K1:press"
"105,105" = "K2:cw"
"104,104" = "K2:ccw"
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
 "handshake_ms": 156.0,
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
 "status": "skip",
 "reason": "不在本次 --only 范围内"
}
```

### files

```json
{
 "status": "skip",
 "reason": "不在本次 --only 范围内"
}
```

### display

```json
{
 "status": "skip",
 "reason": "不在本次 --only 范围内"
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
 "status": "pass",
 "event_fw_base": 0,
 "getinfo_fw_base": 0,
 "offsets": [
  [
   0,
   0
  ]
 ],
 "missing": [],
 "transposed_suspect": false
}
```

### knobs

```json
{
 "status": "pass",
 "silent_steps": [],
 "note": "",
 "suggested_knob_map": {
  "102,102": "K1:cw",
  "101,101": "K1:ccw",
  "100,100": "K1:press",
  "105,105": "K2:cw",
  "104,104": "K2:ccw",
  "103,103": "K2:press"
 }
}
```

### ptt

```json
{
 "status": "pass",
 "cdc_observed": true,
 "hold_ms": 108.7,
 "fw": [
  0,
  3
 ],
 "ime_triggered": null,
 "note": "",
 "window": [
  46503.507085583,
  46505.049406625
 ],
 "hid_activity": [],
 "right_option_seen": false,
 "needs_manual_confirmation": "IME 语音是否被触发需目视确认（无头模式只能看到 flagsChanged）"
}
```

### timing

```json
{
 "status": "skip",
 "reason": "不在本次 --only 范围内"
}
```

### hid_leak

```json
{
 "status": "pass",
 "auto": true,
 "monitor": {
  "source": "CGEventSourceSecondsSinceLastEventType",
  "listen_access": false,
  "types": [
   "keyDown",
   "flagsChanged",
   "systemDefined",
   "leftMouseDown",
   "leftMouseUp",
   "rightMouseDown",
   "rightMouseUp",
   "otherMouseDown",
   "otherMouseUp",
   "scrollWheel"
  ]
 },
 "guided_window_s": 57.5,
 "correlated": 0,
 "uncorrelated": 0,
 "correlated_by_type": {},
 "uncorrelated_by_type": {},
 "window_s": 0.35,
 "hid_only_keys": [],
 "knob_steps_with_hid": [],
 "limitation": "只能看到“有无 HID 事件及类型”，看不到字符与来源设备；与 MK20 键事件时间相关(±0.35 s)即判泄漏；期间若有人用 Mac 键盘会产生不相关样本(→warn)",
 "note": "P0 硬门槛：19 个非 PTT 键与两旋钮不得产生副作用 HID；泄漏→按 VIA KC_NO→现有配置→hidutil 顺序处理"
}
```

### reconnect

```json
{
 "status": "pass",
 "unplug_detect_ms": 3904.0,
 "port_back_ms": 32577.5,
 "handshake_ready_ms": 32773.1,
 "same_port": true,
 "last_error": ""
}
```

### sleep

```json
{
 "status": "skip",
 "reason": "不在本次 --only 范围内"
}
```

### via

```json
{
 "status": "skip",
 "reason": "不在本次 --only 范围内"
}
```
