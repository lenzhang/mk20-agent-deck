# KD replacement plan: `mk20-kbd` (option D)

**Decision (2026-10-19):** replace the vendor KeyboardDevice (KD) completely. Stock themes, ScreenKey app compatibility and the XiaoZhi UI are dropped.

**Evidence base:**
- Static analysis of KD V2.32 (unstripped).
- Read-only device checks on fw 1.1.4.
- Details: `/workspace/kd-study/FEASIBILITY.md` on the box, summarised here.

## 1. What KD actually does, and what we keep

| KD duty | mk20-kbd |
|---|---|
| GD32 UART `/dev/ttyS1` | **keep** (module `gd32`), receive-only in phase 1 |
| Panels: Qt → DE capture → G2D → 21× `/dev/fbN` write+fsync, every frame, ~14 fps, 11 MB/s SPI | **replace** with direct fbN writes, dirty panels only (module `screen`) |
| Host protocol on ttyGS0 (A1A55A5E) | **keep a compatible subset** plus a new fast path (module `host`) |
| Backlight (`/sys/kernel/debug/dispdbg` name=`lcd0`, command=`setbl`, param=level) | **keep** (module `backlight`) |
| HID typing/mouse via GD32 cmds 0x17/0x18/0x1A | **drop**; we type via our own `/dev/hidg0` (router) |
| Keymap/macro writes to GD32 (0x05/0x0D/0x0F/0x10) | **drop** (never reconfigure the GD32 without an explicit later step) |
| atsha204a Check_Key | **drop** (anti-clone gate for KD only) |
| ALSA audio, XiaoZhi, video player, themes, file upload, self-upgrade | **drop now**; audio later as its own module |
| USB gadget (setusbconfig, UsbCDCStateThread) | **already ours** (`gadget.sh`); mk20-kbd only opens ttyGS0 |

## 2. Findings that shape the design

**GD32 frame.** `AA 55 SUM LEN ~LEN payload[LEN] F5 5F`, 115200 8N1, SUM = 8-bit sum of the payload.

**Key/knob event `0x16`:** `16 P C R KH KL`.
- P: pressed (≠0).
- C/R: col/row. Knobs report col==row: 100 press, 101 ccw and 102 cw for K1; 103–105 for K2 (measured earlier).
- KH:KL: the 16-bit QMK keycode the GD32 currently has at that position (KD strings `keyCodeH`/`keyCodeL`).
  - KD's `VideoPlayer::keyStateChanged` runs its local action only when both bytes are 0 (KC_NO). A non-zero keycode means the GD32 has already sent that key as USB HID itself.
  - mk20-kbd must apply the same rule, otherwise the Mac sees double input. This is high confidence from the decompile; still to be verified with `--monitor` on the device.

**No GD32 init or keepalive.**
- KD's `STM32CommunicationObject::started()` only opens the port and starts a 50 ms timer that drains a TX queue, and that queue is empty at boot.
- Keymap writes (0x05) and `clearState` (`02 AA E2 78`) are only reached from host-triggered lambdas (keyBoardMatrixChange/MacroChange). `request_bootloader_jump` (0x19) is only reached from the legacy JSON handler.
- **So host mode needs no init: mk20-kbd opens ttyS1 O_RDONLY.**

**Panel map.**
- KD writes getInfo rect *i* to `/dev/fb(i+1)`: fb1..fb20 are keys row-major ((0,0), (1,0), …, (4,3)) and fb21 is the subscreen.
- From device sysfs: fb1–fb9 are on spi0.0–0.8; fb10–fb20 on spi1.0–1.10; fb21 (nv3007, 428×142) on spi0.9.
- Bus 0 therefore carries 9 keys plus the subscreen (416 KB per full refresh); bus 1 carries 11 keys (360 KB).
- fbtft: `write()` plus `fsync()` flushes the whole panel immediately (KD does the same). The mmap/defio path would give dirty-line updates at 60 fps; that is a later optimisation.

**Costs.**
- One 128×128 panel is 32 KB, about 3.3 ms on the wire at 80 MHz. Full 21 panels take ~40 ms with both buses in parallel.
- KD currently spends ~43% CPU and 35 MB RSS, and keeps a load of ~10 from fbtft fsync churn.

## 3. Architecture

```
            +--------------------------- mk20-kbd (C, static armhf, single binary) ---------------------------+
/dev/ttyS1 ->| gd32: RO reader, frame codec, 0x16 decode | -> event bus (key/knob, ts) ----+                      |
            |   outbound: allow-list (empty in P1) + raw forbidden-sequence gate        |                      |
            |                                                                           v                      |
            |  router (merged from deck/router/mk20_router.c): keymap/actions -> /dev/hidg0 (kbd+abs mouse),  |
            |          PTT Right-Option hold, all-keys-up on EOF/signal, agent/local actions                   |
            |                                                                           |                      |
/dev/ttyGS0<>| host: A1A55A5E codec; getInfo, keyStateChanged, cmd100 JPEG, setBacklight; cmd200 tile565,      |
            |       cmd201 widget/state (new)                                                                  |
            |        | cmd100 -> tiny static JPEG decoder (baseline, 4:2:0/4:4:4) -> RGB565 canvas -> diff       |
            |        v                                                                                         |
            |  screen: shadow+pending per panel, dirty flags; writer thread bus0 (fb1-9,21), bus1 (fb10-20)   |-> /dev/fb1..21
            |  backlight: dispdbg setbl                                                                        |-> debugfs
            |  status: /run/mk20/kbd.status (frames_ok, last_gd32_ts, panel writes, errors) for selftest       |
            +--------------------------------------------------------------------------------------------------+
```

- **Threads:** gd32 reader, host reader, two bus writers, main/event loop. Events are passed through a lock-free ring.
- **Priorities:** key → HID first. Display writes never block key handling.
- **Merge plan for the router:** the current router taps ttyGS0 through a pty. With KD gone, mk20-kbd owns ttyGS0 directly and the tap path disappears. The router's HID logic (resend/dirty, startup and EOF all-keys-up, `router_stop` before UDC changes) moves in as a module with its tests.

**Host protocol subset (compatible with our Mac console):**
- `getInfo` → the same 640×656 canvas and the same 21-rect map (byte-for-byte field names as KD).
- `keyStateChanged {col,row,pressed}`, sent by mk20-kbd. Knobs keep col=row ≥ 100.
- `cmd 100`: whole-canvas JPEG. Decode, diff into dirty panels, then ack with cmd 100 carrying the frame id (KD semantics).
- `setBacklight {level}`.
- Unknown methods → `{"ack_method":m,"success":false,"errorString":"unsupported"}`.
- **New:**
  - `cmd 200`: raw RGB565 tile `u8 fb, u16 x, y, w, h, pixels`, acked.
  - `cmd 201`: widget/state JSON for the future on-board renderer (labels, icons, progress, per-key state).

## 4. Safety

- **GD32 outbound allow-list:**
  - `GD32_OUT_ALLOW_LIST(X)` is empty in P1. Every listed id is checked by `_Static_assert` against 0x19 and 0x02 (compile time).
  - At runtime, `gd32_out_allowed()` rejects anything unlisted, 0x19, and any payload containing `02 AA E2 78`.
  - `gd32_encode()` re-checks the finished frame with `gd32_raw_is_forbidden()`.
  - In P1 the tty is opened O_RDONLY, so no write path exists at all.
  - Tests: all 256 ids are refused in P1; the 0x19 frame and the clearState bytes are detected anywhere in a buffer.
- **Displays:** only fbN writes of exact panel size. No fb0, no /dev/disp, no G2D.
- **Backlight:** clamp to 0–255. Never write 0 on boot; the minimum is a visible floor.

## 5. Boot integration

1. A new release built as an A/B slot (loader unchanged): `mk20d` starts `mk20-kbd` instead of KD.
   - The KD binary and `appLunch.sh` stay **untouched** on /data as the fallback, and the previous KD-based release stays in the other slot.
2. **Kill switch:** the flag file `/data/mk20/use-kd` (also creatable from the TF card FAT) makes `mk20d` start KD instead of mk20-kbd without a slot change.
3. **Probation selftest** (all must pass within 60 s, else no confirm, and the loader rolls back to the KD release):
   - **GD32 link alive (passive):** at least one valid GD32 frame was parsed since start. The status file is written on every event.
     - The GD32 is quiet when untouched, so the acceptance step asks for one keypress during the window, or the release falls back.
     - **Open:** whether the GD32 emits anything unprompted, e.g. after a USB resume. If no allow-listed safe query exists, keep passive and require the keypress.
   - **Screens written:** 21/21 panels written at least once with no write errors (boot splash pattern).
   - **Host link:** ttyGS0 open; getInfo answered if the Mac console is connected (optional).
   - **HID:** `/dev/hidg0` writable and the all-keys-up report sent.
4. Watchdog: `mk20d` restarts mk20-kbd on crash. After 3 crashes in 5 min it falls back to KD (kill switch set automatically and logged).

## 6. Milestones

| M | Content | Tests | On-device acceptance |
|---|---|---|---|
| **K0** (this commit) | gd32 codec, allow-list, panel map, screen writer, host codec (getInfo/keyStateChanged), `--monitor`/`--pattern`/`--getinfo`, static armhf build | `make -C deck/kbd test` (ASan/UBSan, corrupt + fuzz frames, allow-list 256-id sweep, fake fb dir) | none yet |
| **K1** | Read-only device verification | — | (a) with KD running, run `mk20-kbd --monitor` on a **copy** of the traffic. KD owns ttyS1, so first try a passive second reader (reads may steal bytes; check). If that isn't possible, do it in K2 with KD stopped. (b) Confirm bytes 4–5 and the knob codes |
| **K2** | KD stopped manually (one-shot, SSH, reversible by reboot): `--pattern` to verify the fbN ↔ physical key map and bus timing; `--monitor` for GD32 frames | timing script | photo of the pattern; event log for all 20 keys and both knobs |
| **K3** | Host subset plus JPEG decoder plus per-bus threads; router merged; backlight | codec tests, JPEG conformance vs libjpeg on the box, Mac console e2e in simulator | Mac console works against mk20-kbd; latency press→HID and press→panel |
| **K4** | Release integration (mk20d, kill switch, selftest, watchdog fallback) | mk20d tests with a fake mk20-kbd | install via the A/B loader, forced-fail rollback test, kill-switch test, 24 h soak |
| **K5** | Fast path cmd 200/201, on-board renderer hooks; audio module | — | — |

## 7. Risks and open questions

- **GD32 double input:** the GD32 sends HID itself for non-zero keycodes. Rule: only act on keycode==0 (as KD does), or later set the keymap to KC_NO under an explicit, separate step.
- **"Offline typing stops in V2"** (earlier observation) may have been caused by the Mac app's keymap writes, not by KD mode. Recheck in K2.
- **Bytes 4–5** are interpreted as a keycode (decompile evidence); verify in K1/K2.
- **Physical fb ↔ key wiring** is assumed row-major like KD; verify in K2 with the stripe pattern (fbN draws N bars).
- **Kernel/fbtft:** the panels are initialised by the kernel driver at boot, so no panel init is needed from us. Backlight is a single global value.
- **Losing KD's DE/G2D pipeline:** fb0 stays blank. Fine, nothing is attached to it.
- **Parser re-scan** returns only the last frame if one replay contains two frames (rare; noted for K3).
- **atsha:** dropping KD removes the only user, so there is no impact.
