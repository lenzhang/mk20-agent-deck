# MK20 devtest 报告

- 开始：2026-10-18T00:06:28+08:00
- 结束：2026-10-18T00:06:43+08:00

| 项 | 结果 | 要点 |
|---|---|---|
| usb | ✅ pass | T113 CDC=True GD32 VIA=True  |
| port | ✅ pass | /dev/cu.usbmodem1101 |
| getinfo | ✅ pass | 画布 640×656，键 20，起始 0，握手 142.3 ms |
| methods | ✅ pass |  |
| files | ✅ pass |  |
| display | ✅ pass | ACK p50/p95 15.2/17.7 ms；满屏 pattern_q85 67.01 fps，noise_q85 18.74 fps；建议 max_fps 8.0 |
| backlight | ⏭ skip | 未请求（--allow-device-writes --backlight N） |
| keys | ⏭ skip | 非交互模式（--auto） |
| knobs | ⏭ skip | 非交互模式（--auto） |
| hid_leak | ⏭ skip | 未执行按键/旋钮引导 |
| ptt | ⏭ skip | 非交互模式（--auto） |
| timing | ⏭ skip | 非交互模式（--auto） |
| reconnect | ⏭ skip | 非交互模式（--auto） |
| sleep | ⏭ skip | 未启用（--with-sleep） |
| via | ✅ pass | VIA v12 4 层 4×5，备份 /Users/dev/mk20-backup/2026-10-17/devtest-auto-2/via-backup.json |

## 建议配置（~/.config/mk20-agent-deck/config.toml）

```toml
[deck]
fw_coord_base = 0
max_fps = 8.0
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
 "handshake_ms": 142.3,
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
   "noise_bytes": 176378
  },
  "85": {
   "pattern_bytes": 54169,
   "noise_bytes": 281347
  },
  "95": {
   "pattern_bytes": 74891,
   "noise_bytes": 434376
  }
 },
 "ack": {
  "n": 20,
  "p50_ms": 15.2,
  "p95_ms": 17.7,
  "mean_ms": 16.2,
  "timeouts": 0
 },
 "full_screen": {
  "pattern_q85": {
   "frames": 336,
   "seconds": 5.01,
   "fps": 67.01,
   "ack_p50_ms": 15.1,
   "ack_p95_ms": 17.8,
   "bytes": 56265
  },
  "noise_q85": {
   "frames": 94,
   "seconds": 5.02,
   "fps": 18.74,
   "ack_p50_ms": 52.4,
   "ack_p95_ms": 65.1,
   "bytes": 283008
  }
 },
 "wrong_resolution": {
  "acked": false,
  "device_errors": [
   "Incorrect resolution, the device requires the resolution to be 640x656"
  ]
 },
 "visual_ok": null,
 "recommended_max_fps": 8.0
}
```

### backlight

```json
{
 "status": "skip",
 "reason": "未请求（--allow-device-writes --backlight N）"
}
```

### keys

```json
{
 "status": "skip",
 "reason": "非交互模式（--auto）"
}
```

### knobs

```json
{
 "status": "skip",
 "reason": "非交互模式（--auto）"
}
```

### hid_leak

```json
{
 "status": "skip",
 "reason": "未执行按键/旋钮引导"
}
```

### ptt

```json
{
 "status": "skip",
 "reason": "非交互模式（--auto）"
}
```

### timing

```json
{
 "status": "skip",
 "reason": "非交互模式（--auto）"
}
```

### reconnect

```json
{
 "status": "skip",
 "reason": "非交互模式（--auto）"
}
```

### sleep

```json
{
 "status": "skip",
 "reason": "未启用（--with-sleep）"
}
```

### via

```json
{
 "status": "pass",
 "backup": "/Users/dev/mk20-backup/2026-10-17/devtest-auto-2/via-backup.json",
 "protocol_version": 12,
 "layers": 4,
 "matrix": {
  "rows": 4,
  "cols": 5,
  "detected": true
 },
 "layer0_side_effect_keys": 0,
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
