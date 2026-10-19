# MK20 right knob (K2): what controls it and how to get it onto ttyS1

Offline research, 2026-10-19 SGT. No device, Mac or GD32 interaction. Confidence: **high** (static RE of both ends agrees with today's on-device observations).

## TL;DR
- The knobs are configured on the T113 side, not by a PC tool talking to the GD32. The PC app ScreenKey stores the knob setting in a theme ("Theme editing → Encoder", then "Save & Run"). On the board, KeyboardDevice (KD) turns it into GD32 keycodes and writes them over **/dev/ttyS1** with the VIA-style command **0x05 dynamic_keymap_set_keycode** at **virtual matrix rows 100–105**.
- The GD32 holds the 6 encoder slots in a **RAM table** (`0x200010c6`, `.bss`). There is **no EEPROM write**, and a GD32 reset clears them to 0.
- Slot value **0 means "report over serial"**: `16 P r r 00 00` with r = 100..105. A non-zero value means the GD32 sends that HID keycode straight to the host.
- K2 sends Play/Pause today because KD wrote `system_media` codes (0xAE/0xAC/0xAB) to slots 103/104/105 at its last start. The GD32 keeps them in RAM after KD is stopped.
- **The fix: our daemon writes keycode 0 to rows 103, 104 and 105** (and also 100–102, so K1 can't drift) on ttyS1 at startup:

| slot | meaning | frame (AA 55 SUM LEN ~LEN payload F5 5F) |
|---|---|---|
| 100 | K1 press | `AA 55 CD 06 F9 05 00 64 64 00 00 F5 5F` |
| 101 | K1 ccw | `AA 55 CF 06 F9 05 00 65 65 00 00 F5 5F` |
| 102 | K1 cw | `AA 55 D1 06 F9 05 00 66 66 00 00 F5 5F` |
| 103 | **K2 press** | `AA 55 D3 06 F9 05 00 67 67 00 00 F5 5F` |
| 104 | **K2 ccw** | `AA 55 D5 06 F9 05 00 68 68 00 00 F5 5F` |
| 105 | **K2 cw** | `AA 55 D7 06 F9 05 00 69 69 00 00 F5 5F` |

- **Payload:** `05 layer row col kc_hi kc_lo`, with layer 0 and col = row (as KD writes it; the GD32 ignores col for rows ≥100). SUM is the 8-bit sum of the payload.
- **Reply:** the GD32 answers each one with an identical echo frame (id 0x05). KD keeps the entry pending until it sees the echo, which is the "stale id=0x05 len=6 burst" we saw at first open.
- After the writes, K2 should produce `16 01 67 67 00 00` / `16 00 67 67` for press/release, and `16 01 68 68` (ccw) / `16 01 69 69` (cw) for turns. Turns send press-only, like K1.

## Evidence

### A. GD32 firmware: vendor QMK/VIA image `syk_keyboards_mk20_plus_via.bin`
- **Source and method:** the image came from the vendor app's update set (`/workspace/mk20-research/gd32/`, 39,552 B). I disassembled it with capstone, Thumb, base 0x08000000 (`/workspace/k2research/gd32.dis`, script `gd32dis.py`). It matches the observed protocol exactly (16-frames, rows 100+, AA55…F55F), so it is very likely what runs on the board. That has not been proven byte-for-byte, and nothing was read from the device.
- **ttyS1 frame parser → command dispatcher:**
  - The parser is the state machine at 0x8002520–0x80025c0 (checks F5, 5F and the checksum). It calls the dispatcher at 0x8002284.
  - The dispatcher uses `tbh` on `cmd-1` at 0x80022a4. Decoded table:
    - `01`→22dc (protocol version), `02`→230e, `03`→235c, `04`→2390 (get keycode), **`05`→239c (set keycode)**, `06`→23d0
    - `07/08/09`→23d6 (custom value)
    - **`0A` eeprom_reset and `0B` bootloader_jump are disabled** (→2366, which replies 0xFF)
    - `0C..13` → macro/buffer handlers
    - **`14/15` (VIA encoder get/set) are disabled** (→2366)
    - `16` → disabled
    - `17`→2458 (tap/press/release keycode), `18`→24ae (mouse)
    - **`19`→24c8 → `bl 0x800620c` = jump to bootloader (FORBIDDEN)**
    - `1A`→24ce (string)
  - Replies are packed by 0x8001eda and sent by 0x8006284 on the UART. This dispatcher is serial-only.
- **Set-keycode handler** (0x800239c–0x80023ce):
  ```
  row = p[2]; if (row >= 100 && row <= 105) { enc_kc[row-100] = (p[4]<<8)|p[5]; }   // strh to 0x200010c6 (lit @0x80024e4)
  else if (row < 100) dynamic_keymap_set_keycode(p[1], p[2], p[3], kc)  // 0x8004f0e, normal QMK EEPROM path
  ```
  Rows above 105 are ignored. Encoder slots never reach EEPROM.
- **The table lives in `.bss`:** crt0 literals at 0x8000210 give `.data` = 0x20000c00..0x20000f54 and `.bss` = 0x20000f58..0x20002100. 0x200010c6 is inside `.bss`, so it is zeroed on every GD32 reset. There is no other writer: the only literal references are at 0x8001fb4 and 0x800217c (readers) and 0x80024e4 (the setter).
- **Encoder rotation** (0x8001f60, called from the QMK encoder_update at 0x8004e76/0x8004e8e with clockwise=0/1):
  - `idx = 3*enc + cw + 1`.
  - If `enc_kc[idx] != 0`, it does a HID tap (`bl 0x8002738`).
  - Otherwise it sends the serial frame `16 01 (100+idx) (100+idx) kc kc` (kc=0).
  - So K1 is 101 (ccw) / 102 (cw), and K2 is 104 / 105.
- **Encoder press** (0x80020dc–0x800216e, slots 0 and 3):
  - If `enc_kc[i] != 0`, it calls register_code (0x80026c0) on press and unregister (0x80026ec) on release.
  - Otherwise it sends `16 P (100+i) (100+i) 00 00`. K1 press is 100 and K2 press is 103.
- **There is also a raw-HID interface** (usage page 0xFF60 descriptor at 0x8009403). It was not analysed: the vendor stack doesn't use it for knobs, and we don't need it.

### B. KeyboardDevice V2.32 (unstripped `embedded_arm.elf` == `/workspace/kd/KeyboardDevice`, disasm in `/workspace/mk20-research/dl/arm_disasm.txt`)
- **`STM32CommunicationObject::packFrame`** @0x5d808: `AA 55 SUM LEN ~LEN payload F5 5F`, with SUM = the 8-bit payload sum written into byte 2.
- **`dynamic_keymap_set_keycode(layer,row,col,kc)`** @0x5dd88 builds the 6-byte payload `05 layer row col kc>>8 kc&ff` and writes it.
- **`dynamic_keymap_set_keycodes(QMap<QPair<int,int>,u16>)`** @0x5e8e8 queues the map. `on_timeout` @0x5f394 then sends one entry per tick (`set_keycode(0, pair.first, pair.second, kc)` @0x5f488). `processData` @0x5f7b4 removes the entry when the GD32 echoes id 0x05 with the same row/col (findNode/remove @0x5f84c/0x5f868).
- **`VideoPlayer::get_encoder_keycode`** @0xeaabc: for each encoder i, base = 102+3i, and it inserts keys (base-2,base-2), (base-1,base-1), (base,base) = press/ccw/cw.
  - The value depends on the theme's encoder control `encoderFun` (strings `encoder_system_volume / _system_brightness / _system_media / _device_volume / _device_brightness / _keyboard / _qmk_mouse`).
  - **system_volume:** 0xA8 mute, 0xAA vol-, 0xA9 vol+ (@0xeb728–eb790)
  - **system_brightness:** (press), 0xBE, 0xBD (@0xeb7e0–eb838)
  - **system_media:** **0xAE play/pause, 0xAC prev, 0xAB next** (@0xeb858–eb8c0). This matches today's "K2 press launched Apple Music".
  - **device_\* (handled on the T113):** 0. Then KD gets `16` events and, in **`VideoPlayer::encoderValueToMap`** @0xe7df8, maps code ≥100 to encoder `(code-100)/3`.
  - This explains the old note "K2 = 103/104/105 while KD ran": the theme at that time used a device_* function for K2.
- **`via_command_kb`** @0x5d5cc is a stub (returns 0). KD never sends VIA 0x14/0x15. Knob config uses **only id 0x05**.

### C. PC side (ScreenKey, `waveshareteam/Waveshare-ScreenKey` V1.1, local copies in `/workspace/mk20-research/screenkey-gh` and `dl`)
- The wiki (https://www.waveshare.com/wiki/MK20) describes "Theme editing → Encoder: Encoder knob (214×142)"; you then pick rotation/press actions and click "Save & Run". The theme goes to the board over the USB CDC JSON channel (A1A55A5E framing, see `screenkey-oss/FINDINGS.md`).
- **Strings in the Windows exe:**
  - `encoderIndex`, `encoderFun`.
  - Theme JSON blocks `"encoder": [{"row":103,"col":0,"keycode":0,...},{"row":104,...},{"row":100,"keycode":8,"keyString":"E"},{"row":105,"keycode":38992}]` (win_strings.txt ~307330).
  - The device method `keyBoardMatrixChange`.
  - The PC app carries `dfu-util-static.exe` plus `qmk_firmware/` URLs; that is the GD32 *flashing* path and must not be used.
- So the "PC driver" never configures K2 directly. It ships a theme, and KD on the T113 pushes the keycodes to the GD32 over ttyS1.

## Channel, persistence, risk
- **Channel:** /dev/ttyS1 from the T113 (115200 8N1), the same link and framing our daemon already reads. No USB/host action is needed.
- **Persistence:** volatile (GD32 RAM). Values survive a T113 reboot or a KD stop as long as the GD32 stays powered, and are cleared to 0 (= serial) on a GD32 power cycle/reset. Because stock KD rewrites them on every start, **our daemon must (re)write 0 to rows 100–105 on every start**. That is idempotent and causes no flash wear.
- **Risk: low.**
  - Stock KD sends exactly this command on every boot.
  - The bytes are disjoint from the forbidden id 0x19 and from the `02 AA E2 78` sequence.
  - It is not bootloader, flashing or EEPROM.
  - **Rows <100 with id 0x05 DO write the persistent QMK EEPROM keymap.** The daemon should whitelist only `05 00 r r 00 00` with r in 100..105 (or explicit keycodes for these rows), and keep the existing hard-block on 0x19 / `02 AA E2 78` / 0x0A / 0x0B.
  - Don't use 0x04 (get keycode) on rows ≥100: that handler has no encoder branch and would index the normal keymap out of range.
  - To verify after the write, watch for `16 .. 67/68/69` frames, then wait for the echo with a timeout of 1 s and retry at most 3×, like KD.
- **To restore stock behaviour:** restart KD (it rewrites its theme's codes) or power-cycle the GD32.

## Not verified
- That the board's GD32 runs byte-identical firmware to the analysed vendor image. The behaviour matches on every observed point.
- The live send itself. **Sending these frames is a device write to the GD32 (RAM only), so it needs the user's OK under the project rules.**
