# KD replacement plan: `mk20-kbd` (option D)

**Decision (2026-10-19):** replace the vendor KeyboardDevice (KD) completely. Stock themes, ScreenKey app compatibility and the XiaoZhi UI are dropped.

**On-board UI decision (2026-10-19):** the keyboard must work without the Mac app.
Pull the C renderer forward from K5: [ONBOARD-UI-PLAN.md](ONBOARD-UI-PLAN.md) U1 is
host-only renderer/font/home/LS/config/catalogue/golden-test foundation; U2 wires it
into the loop and screen ownership; U3–U7 cover device tests, L5 PTT, optional Mac
capture/AX, ASR and device acceptance. Home/LS/L5 always belong to mk20-kbd;
L1/L4 are optional host pages with an offline placeholder. No code/device change is
implied here; K0–K5 numbering stays intact.

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
  - `cmd 201`: reserved widget/state JSON (not implemented). The renderer foundation moves to ONBOARD-UI-PLAN U1/U2 now; it does not depend on cmd201 or a Mac. U2 gates cmd100/200 by route/owner and composites reserved safety/voice regions; hidden cmd100 frames retain their empty same-id ack.

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
   - The stock KD binary and `appLunch.sh` stay on the rootfs/data **untouched but never started**; the previous release stays in the other A/B slot.
2. No file, FAT flag or key combo switches back to stock KD. Legacy `use-kd`, FAT `mk20-use-kd` and `state/kbd-fallback` are ignored, with one notice per boot.
3. **Probation selftest** (all must pass within 60 s, else no confirm, and the loader rolls the A/B slot back to the previous release — firmware rollback, not a start of stock KD):
   - **GD32 link alive (passive):** at least one valid GD32 frame was parsed since start. The status file is written on every event.
     - The GD32 is quiet when untouched, so the acceptance step asks for one keypress during the window, or the release falls back.
     - **Resolved (K2/K3):** the GD32 sends nothing unprompted, but the knob init gives an active liveness proof: `knob_ok == 6` in the status file means six echoed frames, so the selftest can pass without a keypress. With the read-only switch set, fall back to the keypress rule.
   - **Screens written:** 21/21 panels written at least once with no write errors (boot splash pattern).
   - **Host link:** ttyGS0 open; getInfo answered if the Mac console is connected (optional).
   - **HID:** `/dev/hidg0` writable and the all-keys-up report sent.
4. Watchdog: `mk20d` restarts mk20-kbd on crash. A crash loop (≥3 exits in 5 min) puts mk20-kbd into its **own minimal safe mode**, never back to KD (see K4).

## 6. Milestones

| M | Content | Tests | On-device acceptance |
|---|---|---|---|
| **K0** (d2b966d, done) | gd32 codec, allow-list, panel map, screen writer, host codec (getInfo/keyStateChanged), `--monitor`/`--pattern`/`--getinfo`, static armhf build | `make -C deck/kbd test` (ASan/UBSan, corrupt + fuzz frames, allow-list 256-id sweep, fake fb dir) | none yet |
| **K1** (folded into K2) | Read-only device verification | — | (a) with KD running, run `mk20-kbd --monitor` on a **copy** of the traffic. KD owns ttyS1, so first try a passive second reader (reads may steal bytes; check). If that isn't possible, do it in K2 with KD stopped. (b) Confirm bytes 4–5 and the knob codes |
| **K2** (done 2026-10-19, K2-RESULTS.md / K2-KNOB-CONFIG.md) | KD stopped manually (one-shot, SSH, reversible by reboot): `--pattern` to verify the fbN ↔ physical key map and bus timing; `--monitor` for GD32 frames | timing script | photo of the pattern; event log for all 20 keys and both knobs |
| **K3** (in progress, see §8) | Host subset plus JPEG decoder plus per-bus threads; router merged; backlight | codec tests, JPEG conformance vs libjpeg on the box, Mac console e2e in simulator | Mac console works against mk20-kbd; latency press→HID and press→panel |
| **K4** | Release integration (mk20d boots only mk20-kbd, selftest, crash-loop safe mode, maintenance entry via usb0 admin web) | mk20d tests with a fake mk20-kbd | install via the A/B loader, forced-fail rollback test, crash-loop → safe-mode test, 24 h soak |
| **K5** | Remaining cmd201/binary widget fast path and audio follow-up (cmd200 already implemented). **Renderer pulled forward now to ONBOARD-UI-PLAN U1/U2**, not blocked on K5; L5/PTT U4, ASR U6 | U1 host C/glyph/config/golden/sim tests; U2 ownership/fallback tests; see U1–U7 | U7; no device integration in U1 |

The on-board UI adds no GD32 permissions: never send 0x19 or `02 AA E2 78`;
the only write remains `05 00 r r 00 00`, r=100..105 (RAM). Never log/print keys;
cloud/model/ASR traffic never direct-connects or falls back to direct. Configuration
and screen/PTT ownership details are normative in ONBOARD-UI-PLAN; optional Mac
helper, never required (2026-10-19), replaces the earlier no-helper decision.

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

**K4 release integration (2026-10-20, 1.3.0-rc1; source implemented, hardware acceptance pending).**

**User decision (2026-10-19, late), implemented:** boot starts only mk20-kbd;
stock KeyboardDevice is never started by this release. Stock binaries/rootfs p5
remain untouched. Firmware A/B rollback to the previous release remains independent
(the previous 1.2.x release retains its own historical behavior).

- `FEATURE_KBD=1` is the release default. `MK20_KBD=0` / config `FEATURE_KBD=0`
  is developer-only, never a stock-KD selector; with no router/UI the panels remain
  unpainted. `/data/mk20/use-kd`, FAT `mk20-use-kd` and `state/kbd-fallback` are inert,
  with one ignored-overrides notice per boot. No hidden keyboard combo.
- **Boot:** resolve kbd before gadget; normal composite bridge has no `--tap` and
  no standalone router. Stop leftover KD via `qt_app2 stop` only. Launch kbd even if
  gadget setup failed or the binary is missing (failed exec is supervised and
  selftest detects it). A busy transition never clears its teardown markers.
- **Supervisor:** one flock, validated pid/stop markers and the daemon lifetime lock.
  Stop procd unconditionally before launch, retry while KD remains, stop it again
  if it reappears; never permit two intentional UART/panel writers. Missing kbd or
  KD that cannot stop does not select a vendor UI. The supervisor keeps retrying.
- **Crash loop:** >=3 exits in 300 s latch persistent safe mode and add `--safe-mode`
  on subsequent starts. Backoff stays 1,2,4…30 s, reset after a >=60-s run; supervisor
  continues. Safe mode paints all 21 panels with static error/admin text, retains
  GD32-owned typing, the existing local PTT HID mapping and K2 e-stop. It uses no
  host JPEG, on-board navigation or agent socket, and reports `mode=safe` in status.
  No new ttyS1 writes: only the existing six allow-listed encoder RAM slots, or none
  when the read-only switch is set. ECM/usb0 and adb stay up during crashes/safe mode.
- **Maintenance contract:** implemented in mk20d and specified in
  [firmware/MAINT-HOOKS.md](firmware/MAINT-HOOKS.md). Persistent/runtime fixed-name
  inboxes accept empty `exit-safe-mode` / `restart-kbd` and validated `debug`
  (`verbose=0|1` only); atomically consume requests, never execute/source input.
  Exit-safe clears recent crashes/latch and restarts normally; maintenance restarts
  do not count as crashes. Atomic read-only runtime `state.json` publishes mode,
  restarts, recent crashes, last rc, reason, version. The authenticated usb0 admin
  `mk20-agent --web` at `172.30.250.2:8780` consumes this contract; U3 adds
  config/key/maintenance pages to that sole service (AGENT-BUILD-PLAN §3.8b),
  never a second server on 8780. Agent/web implementation is outside K4. Mac mirror
  is optional/out of scope; no keytrace/secret logging.
- **Selftest:** kd=0 always required. Kbd missing/not running, stale status, no
  GD32 proof (`knob_ok=6` or parsed frame), panels !=21 or panel errors are FAIL
  on probation, WARN after confirm. Safe mode/crash-loop indication is WARN even
  on probation; these indications alone never trigger rollback. Other probation
  FAILs leave the release unconfirmed for loader A/B rollback. Health includes kbd
  mode and counters. HID unavailable after restart remains WARN (reboot clears it).
- **Gadget:** every mutating setup/cleanup first quiesces HID, stops kbd, and fails
  closed on surviving processes/threads. No `kd_start` or `qt_app2 start` remains.
  Setup failure restores ACM+adb without KD. Release flap/manual `--rollback-now`
  requests watcher `cmd-rollback` (loader A/B), preserving USB until reset; stale
  generations are ignored. Kbd crashes themselves never request gadget teardown.
- **HID lifetime limitation remains:** Tina may recreate hidg0 after re-bind.
  Stale descriptors remain parked; all normal HID recovery is in-process. A real
  crash or requested restart can close parked fds, lose HID/leave a held modifier
  until reboot. No guardian is added in K4. Kernel `feature/hidfix` acceptance is
  separate; [firmware/RECOVERY.md](firmware/RECOVERY.md) retains the reboot advice.
- **Install:** existing verified userland installer, one pending A/B switch from
  1.2.x to 1.3.0, no additional partition/slot writes or rootfs p5 changes. Semver
  is `1.3.0`; build id `rc1`; [firmware/RELEASE.md](firmware/RELEASE.md) records RC
  status. Rebuild `bin/mk20-kbd` on Linux with `tools/mk20-build-bins.sh` before
  packaging; binaries are never hand-edited.
- **Tests:** `tests/test_mk20d_kbd.py` covers default/ignored flags, missing binary,
  gadget failure, crashes/backoff/safe mode, both maintenance inboxes, validation,
  graceful restart accounting, no KD starts, selftest and actual loader confirm/
  rollback. `tests/test_install.py` covers 1.2.1→1.3.0 one-slot upgrade and untouched
  p5/vendor sentinels. `kbd/tests/safe_mode.py` exercises real safe-mode rendering,
  HID, both K2 paths and exact GD32 TX with fake devices. BusyBox/proc tests require
  Linux; portable shell/daemon tests supplement them on macOS. Hardware acceptance
  remains: cold boot, forced probation failure, safe WARN confirm, usb0 web/adb,
  HID across rebind/restarts, resource measurements and 24 h soak.

**On-device checks still needed (user at home):** knob init echo on the real GD32 and K2 events `16 .. 67/68/69`; JPEG frames from the Mac console shown correctly; brightness levels look right with the KD mapping; PTT RAlt from the on-board daemon and the hidg0 close on gadget teardown; host link reopen across a bridge restart; latency press→keyStateChanged and press→panel.
