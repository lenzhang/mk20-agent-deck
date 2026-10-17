# MK20 实机记录（2026-10-17 10:05–10:20 CDT / 23:05–23:20 SGT）

只读操作；没有写过设备（没有 VIA 写入、没有 DFU、没有 SHOW_JPG）。

## 枚举
- T113 `1d6b:0104` "Serial"（Allwinner，bcdDevice 0x0504），**只有 CDC-ACM**（没有 ECM 接口）→ `/dev/cu.usbmodem1101`，
  位置 0x00110000，接在机内 GL852G hub（`05e3:0610`）上，hub 直连 Mac 根口 `AppleT6000USBXHCI@00000000`。
  （之前是 `/dev/cu.usbmodem21101`，说明重新插到了另一个口。）`en10` 是另一个 USB 网卡，**不是** MK20 的 ECM。
- GD32 `4250:426f` "syk_keyboards"（SYK，bcd 0x0100，全速）：hidapi 能看到 if0 键盘、if1 VIA `0xFF60/0x61`、
  if2 鼠标/系统/consumer、if3 `0xFF31/0x74`。
- **异常**：GD32 在 30 秒内大约每 1–2 秒重新枚举一次；之后虽然稳定在 `IOUSBHostDevice@00120000`，但没有产品名字符串，
  也没有挂上 HID 驱动（hidapi 枚举为空），说明描述符/HID 请求超时。

## Protocol A
- 写入正常（1 MiB `'0'` 前导约 0.11 s 写完），但发 `getInfo` 后 **T113 完全没有回复**：试过加/不加前导、DTR/RTS 高/低、
  24 秒内每 2 秒重发一次、空闲监听，收到 0 字节。没有其他进程占用端口（lsof 为空，ScreenKey 未运行）。
- 推断：T113 上的 KeyboardDevice 没有在服务 CDC（挂起或反复重启；GD32 反复重枚举和这一点一致）。

## 需要人工
- 把 MK20 断电重插（拔掉 USB-C 等 5 秒再插）。最好插回之前那个口（usbmodem21101 那条链路）或带电源的 hub；
  再看是否稳定：`python3 -m mk20deck usb-list`，然后跑 `devtest --auto`。
- C920 现在对着人，没对着 MK20；要拍设备照片得先把摄像头转过去（拍到人的那张已删除）。

## Session 2 — after replug (23:12:44 SGT)

Gated run order: usb-list → via-backup → devtest --auto → photo. Nothing was flashed or written to the device.

- **usb-list: pass.** Topology: GL852G hub 05e3:0610 @0x00100000 on the root XHCI (AppleT6000USBXHCI@00000000).
  - GD32 4250:426f @0x00120000, 500 mA allocation.
  - T113 CDC 1d6b:0104 @0x00110000 → `/dev/cu.usbmodem1101`, 500 mA allocation.
  - The GD32 enumerated with the hub. The T113 CDC enumerated about 33 s later (23:13:17 SGT), consistent with a Linux boot. Each device has had a single sessionID since then, so there have been no re-enumerations. The GD32 flapping from session 1 is gone. No ESP32 port was present or touched.
- **via-backup: pass.**
  - VIA protocol 12, 4 layers, 4×5 matrix.
  - Layer 0 is all KC_NO and layers 1–3 are all KC_TRNS.
  - Encoder command 0x14 is unhandled.
  - Macros: 16 slots, 1847 B, all zero.
  - Two dumps are identical (raw keymap sha256 f8457663d8a5…). `plan_restore` against the live device = [] (a no-op).
  - Copy: `artifacts/backups/via-backup-2026-10-17.json` (file sha256 16b301ec…). `Backup.load` passes on the box.
- **devtest --auto: getinfo error.**
  - getInfo timed out. A manual probe got 0 bytes back, both without a preamble (4 s wait) and with the 1 MiB `'0'` preamble (15 s wait). The host write of 1 MiB completes, so the gadget's OUT endpoint is being drained.
  - via: matrix auto-detect failed because layer 0 is all KC_NO. Fixed: `detect_matrix` now falls back to later layers (test added).
- **Photo:** `artifacts/device-2026-10-17-2317-t113-silent.jpg`. The screen is alive and showing the vendor home UI: icons on the 20 keys plus the top strip. So the T113 UI app runs but does not answer Protocol A on the CDC.
- **Hypotheses (untested):**
  - The current T113 firmware build listens for the host protocol on a different interface or framing (e.g. Protocol B).
  - Or the CDC reader is a different process that isn't started.
  - Next read-only steps: watch the CDC passively while a human presses a key (is keyStateChanged emitted?), and compare with a USB capture of the vendor app (requires the user; the app is not to be opened by us).
- **Serial console options:**
  - The T113 UART0 pads are internal; reaching them means opening the case and using a 3.3 V USB-UART at 115200 8N1.
  - FEL mode (1f3a:efe8) needs the FEL/uboot button or an SD card removal.
  - `/dev/cu.debug-console` is the Mac's own console, not the MK20.
