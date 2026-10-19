# KD replacement plan: `mk20-kbd` (option D)

**Decision (2026-10-19):** replace the vendor KeyboardDevice (KD) completely. Stock themes, ScreenKey app compatibility and the XiaoZhi UI are dropped.

**Evidence base:**
- Static analysis of KD V2.32 (unstripped).
- Read-only device checks on fw 1.1.4.
- Details: `/workspace/kd-study/FEASIBILITY.md` on the box, summarised here.

## 1. What KD actually does, and what we keep

| KD duty | mk20-kbd |
|---|---|
| GD32 UART `/dev/ttyS1` | **keep** (module `gd32`/`gd32io`); the only TX is the knob-slot init (strict allow-list, §4) |
| Panels: Qt → DE capture → G2D → 21× `/dev/fbN` write+fsync, every frame, ~14 fps, 11 MB/s SPI | **replace** with direct fbN writes, dirty panels only (module `screen`) |
| Host protocol on ttyGS0 (A1A55A5E) | **keep a compatible subset** plus a new fast path (module `host`) |
| Backlight (`/sys/kernel/debug/dispdbg` name=`lcd0`, command=`setbl`, param=level) | **keep** (module `backlight`) |
| HID typing/mouse via GD32 cmds 0x17/0x18/0x1A | **drop**; we type via our own `/dev/hidg0` (router) |
| Keymap/macro writes to GD32 (0x05/0x0D/0x0F/0x10) | **drop**, except `05 00 r r 00 00` for encoder RAM slots r=100..105 at start (K2 → serial; see `kbd/K2-KNOB-CONFIG.md`). Rows <100 (EEPROM) are refused in code |
| atsha204a Check_Key | **drop** (anti-clone gate for KD only) |
| ALSA audio, XiaoZhi, video player, themes, file upload, self-upgrade | **drop now**; audio later as its own module |
| USB gadget (setusbconfig, UsbCDCStateThread) | **already ours** (`gadget.sh`); mk20-kbd only opens ttyGS0 |

## 2. Findings that shape the design

**GD32 frame.** `AA 55 SUM LEN ~LEN payload[LEN] F5 5F`, 115200 8N1, SUM = 8-bit sum of the payload.

**Key/knob event `0x16`:** `16 P C R KH KL`.
- P: pressed (≠0).
- Byte 2 = **row** (0..3), byte 3 = **col** (0..4), measured 2026-10-19 (K2-RESULTS.md). KD forwarded them to the host as `keyStateChanged{col,row}` 0-based, no transposition (NOTES-device-2026-10-17.md; KD `VideoPlayer::keyStateChanged(pressed,row,col)`). K0 had the names swapped; fixed in K3. Knobs report row==col: 100 press, 101 ccw and 102 cw for K1; 103–105 for K2 once its slots are 0.
- KH:KL: the 16-bit QMK keycode the GD32 currently has at that position (KD strings `keyCodeH`/`keyCodeL`).
  - KD's `VideoPlayer::keyStateChanged` runs its local action only when both bytes are 0 (KC_NO). A non-zero keycode means the GD32 has already sent that key as USB HID itself.
  - mk20-kbd must apply the same rule, otherwise the Mac sees double input. This is high confidence from the decompile; still to be verified with `--monitor` on the device.

**GD32 init (updated 2026-10-19, K2):** the only init is writing KC_NO to the six encoder RAM slots 100..105 (stock KD writes theme codes there on every start; K2 was left on media keys). Each frame is echoed identically by the GD32. No keepalive. The older analysis below holds for everything else.

**No GD32 init or keepalive (K0 analysis, superseded for the knob slots).**
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

- **GD32 outbound allow-list (K2/K3):**
  - `gd32_out_allowed()` accepts exactly one shape: 6-byte payload `05 00 r r 00 00`, r in 100..105. Everything else is refused: other ids (0x19, 0x02, 0x0A, 0x0B, …), 0x05 with row <100 (EEPROM keymap), row >105, layer ≠0, col ≠ row, non-zero keycode, any other length, any buffer containing `02 AA E2 78`. `_Static_assert`s pin the id and slot range.
  - `gd32_encode()` re-checks the finished frame with `gd32_raw_is_forbidden()`; `gd32_tx()` (gd32io.c) is the only function that writes to the tty and goes through `gd32_encode()`.
  - **Read-only switch:** `--no-gd32-write`, or the kill file `/data/mk20/kbd-no-gd32-write` (or an extra path in `MK20_KBD_NO_WRITE_FILE`): the tty is opened O_RDONLY and knob init is skipped (status `disabled`).
  - Knob init: per slot send, wait ≤1 s for the identical echo, ≤3 attempts, then log and continue (status `ok`/`partial`/`failed` in the status file; never fatal). Key frames arriving during init are dispatched; stale 0x05 echoes are dropped; 0x05 echoes in the main loop are counted, never treated as events.
  - Tests: every id × every row; every single-byte mutation of the six valid payloads; all lengths; 400k random payloads vs a reference predicate; exact wire bytes vs K2-KNOB-CONFIG.md; fake-GD32 echo / silent (18 timeouts) / flaky (retry) / noisy (key events + stale echoes + junk) / closed fd; e2e checks the wire bytes and the O_RDONLY open via `/proc/<pid>/fdinfo`.
- **Displays:** only fbN writes of exact panel size. No fb0, no /dev/disp, no G2D.
- **Backlight:** clamp to 0–255. Never write 0 on boot; the minimum is a visible floor.

## 5. Boot integration

1. A new release built as an A/B slot (loader unchanged): `mk20d` starts `mk20-kbd` instead of KD.
   - The KD binary and `appLunch.sh` stay **untouched** on /data as the fallback, and the previous KD-based release stays in the other slot.
2. **Kill switch:** the flag file `/data/mk20/use-kd` (also creatable from the TF card FAT) makes `mk20d` start KD instead of mk20-kbd without a slot change.
3. **Probation selftest** (all must pass within 60 s, else no confirm, and the loader rolls back to the KD release):
   - **GD32 link alive (passive):** at least one valid GD32 frame was parsed since start. The status file is written on every event.
     - The GD32 is quiet when untouched, so the acceptance step asks for one keypress during the window, or the release falls back.
     - **Resolved (K2/K3):** the GD32 sends nothing unprompted, but the knob init gives an active liveness proof: `knob_ok == 6` in the status file means six echoed frames, so the selftest can pass without a keypress. With the read-only switch set, fall back to the keypress rule.
   - **Screens written:** 21/21 panels written at least once with no write errors (boot splash pattern).
   - **Host link:** ttyGS0 open; getInfo answered if the Mac console is connected (optional).
   - **HID:** `/dev/hidg0` writable and the all-keys-up report sent.
4. Watchdog: `mk20d` restarts mk20-kbd on crash. After 3 crashes in 5 min it falls back to KD (kill switch set automatically and logged).

## 6. Milestones

| M | Content | Tests | On-device acceptance |
|---|---|---|---|
| **K0** (d2b966d, done) | gd32 codec, allow-list, panel map, screen writer, host codec (getInfo/keyStateChanged), `--monitor`/`--pattern`/`--getinfo`, static armhf build | `make -C deck/kbd test` (ASan/UBSan, corrupt + fuzz frames, allow-list 256-id sweep, fake fb dir) | none yet |
| **K1** (folded into K2) | Read-only device verification | — | (a) with KD running, run `mk20-kbd --monitor` on a **copy** of the traffic. KD owns ttyS1, so first try a passive second reader (reads may steal bytes; check). If that isn't possible, do it in K2 with KD stopped. (b) Confirm bytes 4–5 and the knob codes |
| **K2** (done 2026-10-19, K2-RESULTS.md / K2-KNOB-CONFIG.md) | KD stopped manually (one-shot, SSH, reversible by reboot): `--pattern` to verify the fbN ↔ physical key map and bus timing; `--monitor` for GD32 frames | timing script | photo of the pattern; event log for all 20 keys and both knobs |
| **K3** (in progress, see §8) | Host subset plus JPEG decoder plus per-bus threads; router merged; backlight | codec tests, JPEG conformance vs libjpeg on the box, Mac console e2e in simulator | Mac console works against mk20-kbd; latency press→HID and press→panel |
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

## 8. Status (2026-10-19)

**K2 done.** fb map, orientation, key/K1 event bytes verified on device; K2 root cause found (stock KD writes media keycodes into GD32 encoder RAM slots 103–105). The user chose to apply the slot reset inside the new daemon, not as a one-off command.

**K3 done so far (box only, no device):**
- `mk20-kbd --run`: single-threaded poll loop over ttyS1 and the host tty (`/dev/ttyGS0`, which the PTY↔TCP bridge symlinks; reopened after EIO/EOF). `--no-host` = local mode: pressed key panel turns white, released redraws idle.
- Knob init + strict TX allow-list + read-only switch (§4).
- Host subset: A1A55A5E codec; `getInfo` (version `mk20-kbd-0.3`); `keyStateChanged` (id 0xFFFFFFFF, col/row as KD); cmd 100 JPEG → vendored stb_image v2.30 (JPEG only, baseline + progressive, 4:4:4/4:2:2/4:2:0) → RGB565 canvas → only changed panels written → empty cmd-100 ack with the same id (also on bad frames, so the host never stalls); `setBacklight`/`setDeviceBl` 0..100 → dispdbg `lcd0`/`setbl`/param/start; cmd 200 tile; everything else → `success:false, "unsupported"`.
- Status file `/run/mk20/kbd.status` (1 Hz, atomic): knob_init, panels_written, event and error counters.
- Inbound GD32 LEN capped at 64 (a false header can no longer swallow 255 bytes).
- Tests: `make -C deck/kbd test` = ASan/UBSan unit tests + `tests/e2e_run.py` (real binary on two ptys, the console's own `mk20deck.protocol` codec). Wired into `deck/Makefile` `test`.

**K3 chunk 2 (2026-10-19):**
- **Per-bus writer threads:** one thread per SPI bus (bus 0: fb1–9 + fb21, bus 1: fb10–20). Producers only copy into `pending` under the bus mutex; the writer snapshots a panel under the lock and writes+fsyncs unlocked. Latest content wins per panel; a panel re-dirtied during its write is written again (unless it went back to the written content); a failing fb is retried with a 200 ms back-off; stop = bounded drain or immediate. Tests: producer never blocked (40 updates with 200 ms writes < 60 ms), coalescing, re-dirty, parallel buses, missing fb, shutdown variants; whole suite also under TSan (`make -C deck/kbd tsan`, part of `make test`).
- **Backlight = KD exactly:** `BacklightAdjuster` = `Adjuster(0, 100, 10, 255)` (@0xd16b8), `mapToControl` (@0xd1228): `param = 10 + (int)(level/100.0 * 245)`, clamped 10..255; dispdbg `name=lcd0, command=setbl, param, start=1` (`backlightValueChanged` @0x52c68). Reply `{"ack_method":"setBacklight","success":true,"result":<level>}`; out of range → KD's text `Backlight level must be between 0 and 100, got N`.
- **getInfo parity:** `handleGetInfo` (@0x63550) fields deviceVersion/deviceWidth/deviceHeight/deviceModel/devicePanel; devicePanel adds `screenWidth`/`screenHeight`. e2e compares field-for-field with a captured KD V2.32 getInfo (`tests/data/kd-getinfo-v232.json`); only deviceVersion differs (`mk20-kbd-0.3`).
- **Router merge, step 1 (`hid.c`):** `/dev/hidg0` output inside mk20-kbd: keyboard report 1 (mods + 6 keys), absolute pointer report 2 (FIFO, button edges never dropped), PTT key (doc 1,4 → fw col 0,row 3; `--ptt C,R`) holds Right Alt; knobs and other keys local. r120 rules kept: all-up at open, exit, EOF and SIGTERM (bounded 1.5 s, under gadget.sh's TERM→KILL window); state only changes on a fully accepted write; dirty re-send on POLLOUT; non-EAGAIN errors throttled to 100 ms. **Teardown:** the daemon checks `/tmp/mk20-router-stop` every ≤100 ms tick while hidg0 is open (and closes immediately on SIGUSR1), sends a short all-up and closes; it reopens only after the file is gone. e2e covers all of this with a FIFO as hidg0.

**K3 remaining:**
- Router features beyond PTT that lived on the Mac/agent side (keymaps/actions, MCP agent hooks), keytrace (`MK20_KEYTRACE`) not yet ported.
- Console e2e through the real TCP bridge plus the Mac console in the simulator.

**K4 release integration (2026-10-19, box only, `tests/test_mk20d_kbd.py`). Revised on the user's decision: no stock-KD fallback while OTA works.**
- **Principle:** OTA/update needs only the composite (ECM) + mk20d, not KD. So mk20-kbd problems never bring back stock KD automatically. Safety comes from A/B: on probation, a broken mk20-kbd fails selftest, so the release is not confirmed and the loader rolls back to the previous release.
- **Opt-in + overrides** (`mk20lib.sh`):
  - `FEATURE_KBD` (default 0; mk20.conf or env `MK20_KBD=1`).
  - `/data/mk20/use-kd` or FAT `mk20-use-kd` force stock KD. These are **manual debug/rescue overrides only** (RECOVERY.md); nothing creates them automatically.
  - No fallback mark (an old `state/kbd-fallback` file is ignored).
- **Boot** (`mk20d boot`):
  - Decided before gadget, composite only. `MK20_KBD=1` goes to gadget-boot.sh, which then starts the bridge without `--tap`.
  - On gadget rc 0: touch `/tmp/mk20/kbd.mode`, launch `mk20d kbd`, and do NOT start the standalone router.
  - On rc != 0: stock KD stays (that boot is a composite failure anyway).
  - "enabled but not used" is logged when the binary is missing or the composite is off.
- **Supervisor** (`mk20d kbd`, flock, `/tmp/mk20/kbd-loop.pid`):
  - **Stopping KD:** `qt_app2 stop` (procd), retried up to 3× (≤5 s wait each).
  - **KD that will not stop:** mk20-kbd is NOT started (never a ttyS1/fb double writer). It writes `/tmp/mk20/kbd-degraded`, removes kbd.mode, and selftest reports WARN "not active" while KD keeps the panels.
  - **Running mk20-kbd:** `bin/mk20-kbd --run --host /dev/ttyGS0 --hid /dev/hidg0 --hid-stop-file /tmp/mk20-router-stop --status /run/mk20/kbd.status` (`--no-hid` when NOHID).
  - **Exits:** every exit is restarted with backoff 1, 2, 4 … 30 s; a run ≥60 s resets the backoff. Exits are recorded in `/tmp/mk20/kbd.crashes` (total, last rc) and `/tmp/mk20/kbd-crash.times` (uptimes, last 20).
  - **Never** `qt_app2 start`.
  - **Ends on:** `/tmp/mk20-kbd-stop` (gadget.sh) or composite down (gadget.sh cleanup owns KD on that path).
- **Selftest in kbd mode:**
  - kd=0 is required; KD beside mk20-kbd is always FAIL.
  - These checks FAIL on probation and are WARN once confirmed, so a confirmed release never gets the stock `--rollback-now` because of mk20-kbd:
    - mk20-kbd running;
    - status fresh ≤10 s;
    - knob_ok=6 or any GD32 frame;
    - panels_written=21;
    - crash loop: ≥3 exits within 300 s.
  - Fewer exits show as INFO "restarted N times".
  - health.json `"kbd"`: enabled, mode, running, restarts, recent_crashes, degraded, status (the status file text).
- **gadget.sh:**
  - `router_stop` ends with `kbd_hid_quiesce`: SIGUSR1 to validated `bin/mk20-kbd --run` pids, wait until no `/proc/<pid>/fd` points at `/dev/hidg0` (≤3 s), else SIGTERM, then SIGKILL.
  - `kbd_stop` (stop file, validated supervisor kill, TERM→KILL) runs right after `router_stop` in `cleanup()` and `setup_main`, before any UDC write and before `kd_start`. Those paths (flap rollback, `--rollback-now`, gadget failure) end in stock ACM+KD by design.
- **hidg0 replacement (fw 1.2.0 field finding):**
  - The node is deleted and re-created about 13 s after gadget setup (link drop near uptime 74 s), so an early fd becomes "(deleted)" and new opens fail ENXIO until the link is up.
  - `hid.c` checks the open node every ~1 s (fstat vs stat: dev/ino/rdev, nlink 0, path gone) and right after a fatal write error (ENXIO/ESHUTDOWN/ENODEV/EPIPE/EBADF).
  - **A stale fd is PARKED, never closed.** Same rule as router 1.2.1: Tina's `hidg_bind()` re-runs `cdev_init()` on the same cdev, so closing the stale fd after a re-bind breaks every later open (codex r121 #1).
  - The new node is opened by path with back-off 100 ms → 1 s (reset only after a delivered report), then all-keys-up and held keys/mods are re-sent.
  - A fatal error on a node that is not stale closes and reopens nothing; retries stay on the 100 ms hard-error tick (no open/ESHUTDOWN spin, r121 #2).
  - Teardown (stop file / SIGUSR1) closes the current and all parked fds. gadget.sh's holder check also matches `/dev/hidg0 (deleted)`.
  - **Limitation:** if mk20-kbd dies while the gadget stays up with a parked fd, hidg0 stays unopenable until the next re-bind or reboot. mk20-kbd keeps retrying the open (≤1 s), and keys/panels keep working without HID.
  - **Consequence for supervision:** a process that has been through a re-bind cannot be restarted without losing HID until reboot. So all HID recovery is in-process: mk20-kbd never exits for HID reasons (no exit path on hidg0 errors; SIGUSR1/stop-file teardown closes and later reopens inside the same process). The supervisor's restarts exist only for real crashes. After such a restart, selftest WARNs (never FAILs) "hidg0 not open after mk20-kbd restart"; a reboot clears it. Documented in RECOVERY.md.
  - Status file: `hid_open`, `hid_reopens`, `hid_replaced`. Covered by unit tests and e2e.
- **Release binary:** `tools/mk20-build-bins.sh` builds `bin/mk20-kbd` (static armhf, stripped, 529,696 B) next to the bridge and router. `test_release_binaries_match_source` covers it. FEATURE_KBD stays off by default.

**codex review kdr-r1 (2026-10-19, NO-GO, 6 findings; reviews/kdr-codex-r1.md on the Mac worktree), fixes in the next commit:**
- **#1 teardown fail-open:** `kbd_stop` and `kbd_hid_quiesce` now return 1 when a holder or mk20-kbd survives SIGKILL. `cleanup()` then aborts with UDC/configfs untouched and KD not started; `setup_main` dies.
- **#2 late supervisor undoing teardown:** markers are cleared only by `mk20d boot`, under the gadget transition mutex and only with no `gadget.busy` and the composite up. The supervisor publishes its pid before the first marker check and does nothing (KD untouched) when the marker, busy flag or composite-down is seen. mk20-kbd also gets `--exit-file /tmp/mk20-kbd-stop`: it refuses to start, or exits, once the marker exists. kbd_stop rescans once for a late exec.
- **#4 host write deadlock:** the host fd stays O_NONBLOCK. A reply waits at most 200 ms, then later replies fail fast until a write succeeds (`host_tx_timeouts`). e2e floods getInfo without reading and checks that the PTT release still reaches HID within 1.5 s and that replies resume.
- **#5 KD between respawns:** `qt_app2 stop` is issued unconditionally, followed by a 1 s settle. A KD that comes back mid-run stops further launches (degraded).
- **#6 single instance:** mk20-kbd takes a daemon-lifetime flock (`--lock`, supervisor passes `/tmp/mk20/kbd-daemon.lock`) before touching ttyS1/fb; a second instance exits 3.
- **#3 (open, needs a decision):** a CRASH of mk20-kbd closes its parked hidg0 fds. After the ~74 s re-bind, HID then stays ENXIO until reboot, and a RAlt that was held at crash time stays held on the host until USB reset or reboot. The router 1.2.1 has the same property. The fix codex suggests is a separate HID guardian process (owns hidg0 + parked fds, releases modifiers on control-socket EOF), or fixing the kernel cdev lifetime. Mitigations today:
  - HID errors never end the process;
  - selftest WARNs about lost HID after a restart;
  - RECOVERY.md says to reboot.
- **#3 decision (2026-10-19): no separate HID guardian process for now.** The real fix is the kernel side: the hidfix module (branch `feature/hidfix`, codex GO after r3) fixes the f_hid cdev lifetime ("open fails after re-bind" and "crash on function unlink"). It is tested on the device LAST, after all other work has landed. Until then the mitigations above stay as they are: HID errors never exit the process, selftest shows WARN (not FAIL) for lost HID after a restart, and RECOVERY.md says to reboot. A HID guardian is the fallback only if hidfix fails on the device; then it is designed as its own milestone (sole owner of hidg0 + parked fds, modifiers released on control-socket EOF, mk20-kbd talks to it over a socket).

**Plan to make FEATURE_KBD default-on:**
1. On-device trial with `FEATURE_KBD=1` in mk20.conf on a release that carries the binary. Pass criteria:
   - confirm passes;
   - kbd.status shows 6/6 knobs and 21 panels;
   - `hid_replaced`≥1 after the 74 s link drop, with PTT still working afterwards;
   - console JPEG/backlight/getInfo OK;
   - OTA install of the next release works from kbd mode;
   - a forced crash loop on probation rolls back A/B.
2. 24 h soak plus a few reboots and host replug/flap cycles with kbd on: no `restarts` growth, no stuck RAlt, no KD respawn.
3. Feature parity left on the console side (keymaps/actions, keytrace) accepted or ported.
4. Flip the default in `mk20lib.sh` (`FEATURE_KBD=1`) in a new release (version bump). The overrides `use-kd` / `mk20-use-kd` stay documented as debug/rescue. Then retire the standalone router and the bridge `--tap` path one release later.

**On-device checks still needed (user at home):** knob init echo on the real GD32 and K2 events `16 .. 67/68/69`; JPEG frames from the Mac console shown correctly; brightness levels look right with the KD mapping; PTT RAlt from the on-board daemon and the hidg0 close on gadget teardown; host link reopen across a bridge restart; latency press→keyStateChanged and press→panel.
