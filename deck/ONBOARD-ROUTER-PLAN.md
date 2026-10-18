# On-board key router plan (T113)

Survey: read-only over `~/mk20-backup/mkadb.sh`, 2026-10-19 ~05:25 SGT, fw 1.1.3, soak stopped, KeyboardDevice (KD) running.

## 1. Survey facts
| Item | Finding |
|---|---|
| Kernel / CPU | Tina Linux 5.4.61 #221 SMP PREEMPT, 2× ARMv7 (Cortex-A7, neon, vfpv4, idiva) |
| Interpreters | **no python3/python, lua, micropython, perl**; busybox (1.27) + `nc` only |
| libc | glibc **2.23** armhf (`/lib/ld-linux-armhf.so.3`), libssl/libcrypto 1.1 in /usr/lib |
| Memory | 111 MiB total, **MemAvailable ≈ 57 MiB**, no swap; KD RSS ≈ 35 MiB, 7 threads |
| CPU | load avg ≈ 9–10 (D-state kworkers inflate it), top: KD ≈ 39 % CPU, idle ≈ 30 % |
| Disk | `/` overlay (UDISK, holds /data) 2.4 GiB free · FAT `/mnt/SDCARD` 8 MiB, **1.2 MiB free** · /tmp tmpfs 52 MiB free |
| HID gadget | `/dev/hidg0` (root 0600) in composite `g1` **1d6b:0104** (acm, ecm, ffs.adb, hid.usb0), UDC 4100000.udc-controller |
| HID report desc | `hid.usb0`: protocol 1, subclass 1, report_length 9. **ID 1 keyboard**: mods(8 bit) + reserved + 6 keys (+5 LED out); **ID 2 mouse**: 3 buttons + X,Y **absolute 16-bit 0..32767** |
| GD32 → KD | KD holds **/dev/ttyS1** (UART, `LCK..ttyS1`) — raw GD32 key events arrive there; exclusive, we must not open it |
| KD → host | KD writes Protocol A to **/dev/ttyGS0**, which is our `ttygs_tcp_bridge` pty (`--link /dev/ttyGS0`, real port `ttyGS0.real`), served on **172.30.250.2:17890** and 169.254.7.2:17890 |
| hidraw / input | no /dev/hidraw*; /dev/input event0–3 are sunxi keyboard/gpadc/IR (not GD32) |
| Other | KD also has UDP 127.0.0.1:5679 and :33547 open; `tt` listens on 8887/8888 (stock) |

## 2. Decisions
- **Language: C, static armhf** (`arm-linux-gnueabihf-gcc -static`, already used for `ttygs_tcp_bridge`).
  No interpreter on the device; python would cost ~10–20 MiB RAM and FAT space we do not have. A static
  C binary is ~0.5 MiB, <1 MiB RSS. Go (static ~2–3 MiB, 5–10 MiB RSS) is the fallback if the router
  grows (agent loop) — the agent itself is a separate process anyway.
- **Raw-event source: tap in `ttygs_tcp_bridge`**. KD owns ttyS1, so we take KD's own `keyStateChanged`
  output. The bridge already sits in the KD→host byte stream; add `--tap unix:/tmp/mk20/kd-tap.sock`
  that copies KD→host bytes to at most one local subscriber (non-blocking, drop on backpressure,
  never stalls the Mac link). The router connects there (`--src unix:...`). Direct ttyS1 is rejected
  (would fight KD for the UART). Latency added: one pty hop + one unix-socket write, sub-millisecond.
- **HID writer**: `write()` 9-byte reports (ID 1) to `/dev/hidg0`; mouse (ID 2, absolute) later for VNC
  computer-use. Release all modifiers on exit/EOF (done) and on start.
- **Routing (phase 1)**: PTT (doc 1,4) → Right Alt held; others local. Phase 2: config table (JSON in
  /data/mk20/router.json) for key → HID chord / agent event / local action, sharing the controller's
  role table (PRD §6.2) so sim cases describe the same behaviour.
- **Keytrace**: same format as `mk20deck/keytrace.py` (`recv/routed/hid_written/local`), tmpfs, rotated;
  enabled by `MK20_KEYTRACE=1` in the service env. `tools/mk20_key_latency.py` consumes it unchanged.

## 3. Packaging (release / loader / mk20d)
- Binary `release/bin/mk20-router` (built by `tools/mk20-release-build.sh` from `router/`, sha256 in MANIFEST).
- Service supervised by `mk20d` like the bridge loop: start after the bridge is up, restart with
  backoff, stop file `/tmp/mk20-router-stop`, logs to `/data/mk20/logs/router.log` (size-capped).
- Health: `mk20d selftest` gains `router` check — process alive, tap socket connected, hidg0 writable;
  failure does **not** fail boot confirmation in the first release (probation only), so a router bug
  cannot trigger rollback loops; later releases may gate on it.
- Rollback safety: router is user-space only, no USB/gadget changes, no GD32 access; disabling = stop file
  or `MK20_ROUTER=0` in mk20.conf (whitelisted key). FAT budget: binary lives in the release on /data, not FAT.

## 4. Budgets
- Memory: router < 1 MiB RSS; tap buffer 64 KiB. Total headroom after router ≈ 55 MiB.
- Latency target (key-latency doc): recv→hid_written p95 < 2 ms idle, < 10 ms under load; E2E idle p95 ≤ 15 ms.
- CPU: event-driven `read()`; zero work when idle.

## 5. Status / next
- Built (host-tested, not installed): `router/mk20_router.c` — Protocol A parser with CRC + resync,
  keyStateChanged extraction (wrapped and flat), PTT→Right Alt HID writer, stuck-modifier release on EOF,
  keytrace with rotation, strict args; armhf static build verified (`tests/test_router.py`, 13 tests).
- Next: bridge `--tap` (+ tests), mk20d service + selftest hook, release-build integration, then
  device install of a release containing it, then key-latency measurement.
- Open: confirm KD emits `keyStateChanged` while no host app is connected (tap test on device, read-only
  first: `nc` to 17890 is the Mac's port — use the tap once built); confirm fw coordinate base.
