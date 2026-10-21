# MK20 Agent firmware: releases

Applies to: Waveshare MK20 (Allwinner T113, Tina Linux 5.4.61 #221, BusyBox 1.27.2). The firmware is a userland
layer on `/data` (ext4/UDISK). It never writes the rootfs. Kernels are only swapped as whole boot images (p4,
see RECOVERY.md level 5).

## 1.4.0-rc1 (UX revision + single settings entry)

`firmware/VERSION` is **1.4.0**; build id **rc1**: `sh tools/mk20-release-build.sh firmware out rc1`.
Three tracks merged on release/1.4.0: on-device UX (`bin/mk20-kbd`), the usb0 settings web as the only
settings entry (`bin/mk20-agent --web`, default on via FEATURE_WEB=1, agent still off; mk20d supervises
it; see deck/SETTINGS.md), and the Mac side (mk20deck `ui.hello`/`ui.heartbeat`, `ui.openSettings`,
"which agent needs me" hook states, K1 = next agent that needs me). Cross-reviews: Kimi K3 GO on all
three (deck/reviews/1.4.0/). PRD v1.4 §15 lists the interaction changes.

### 1.4.2-rc3: cap the debug adbd log (soak follow-up)

Build id **rc3**; `firmware/VERSION` is **1.4.2**. The 1.4.1 soak patrol saw MemAvailable fall ~0.75 MB/h: the
stock debug adbd was started with `>/tmp/adbd.log` (tmpfs = RAM) and logs every adb command (2.4 MB after 2.5 h of
soak/watch polling), never trimmed. adbd now appends (`>>`) and mk20d maint trims `/tmp/adbd.log` like the other
`/tmp` logs (>256 KiB -> last 64 KiB, every 300 s). Only `sbin/mk20d` and `share/{gadget,mk20-add-adb}.sh` changed.

### 1.4.1-rc2: `/run/mk20` on tmpfs (soak follow-up)

Build id **rc2**: `sh tools/mk20-release-build.sh firmware out rc2`; `firmware/VERSION` is **1.4.1** (the loader keeps one MANIFEST per version: a changed 1.4.0 cannot be reinstalled over the confirmed 1.4.0). Only
`sbin/mk20d` changed in the release; binaries are byte-identical to rc1. The rc1 soak patrol found that `/run` is not tmpfs
on the MK20 (rootfs = overlay with its upper dir on the TF card; only `/tmp` is tmpfs). mk20-kbd rewrites
`/run/mk20/kbd.status` (tmp + rename) every second from the main loop that also owns e-stop/HID all-up:
~86 400 TF writes/day, a stalled card could stall that loop, and the files survived reboots (the old
negative-age "stale").

- `mk20d boot` mounts `tmpfs /run/mk20` (`size=512k,mode=0755,nosuid,nodev,noexec`) first, before gadget
  setup, mk20-kbd or the web start. Decided from `/proc/mounts` (canonical path, longest mount prefix):
  already tmpfs (mk20d re-run, or a tmpfs `/run`) -> never stacked; a symlink, another mount at that path,
  or a running mk20-kbd/web (bound `kbd.sock`) -> not mounted. Every refusal and a failed mount are WARN in
  mk20d.log and boot carries on with `/run/mk20` on the overlay as before. Files from earlier boots are hidden.
- `mk20d health`: `"run_tmpfs": true|false`; selftest `INFO /run/mk20 on tmpfs` or
  `WARN /run/mk20 not on tmpfs (<fstype>)` — never FAIL, never blocks confirm.
- The selftest line `kbd status stale (<age>s, up <uptime>s)` now carries the uptime: the age is the
  wall-clock mtime (no RTC, clock sets), the uptime lines a stale sample up with mk20d.log.
- `tools/mk20_soak.py`: a sample whose health status is not `ok` keeps `selftest_warn` (the WARN/FAIL lines of
  `selftest.detail`, <= 8 x 160 chars); the summary counts samples per message (durations folded to `Ns`).
- mk20-kbd's main loop has no other periodic file writes: panel `fsync` goes to `/dev/fb*`, backlight to
  debugfs, the lock to `/tmp`, `kbd.sock` is bound once, its log is `/tmp/mk20/kbd.log`; HOME status probes
  only read, on a worker thread.

Host validation only (Docker mk20-build:trixie, BusyBox 1.27.2): `make test` green — tests 875 passed /
1 skipped, agent 398, onboard 11348, kbd 4018 ASan + 4018 TSan, ui 41396, sim 35, tools 64 passed / 1 skipped,
shellcheck clean; a real BusyBox 1.27.2 `mount -t tmpfs -o size=512k,mode=0755,nosuid,nodev,noexec` was
checked in a container. **No device acceptance claimed.**

### On-device UX (`bin/mk20-kbd`)

On-device UX revision of `bin/mk20-kbd` (rebuilt static armhf; other binaries byte-identical).
mk20-kbd reads `MK20_VERSION` (exported by mk20d) and `/tmp/mk20/gadget.env` for HOME status.

- HOME shows real status instead of "unknown" (new `kbd/devstat.c`: firmware version, usb0 IP,
  USB/Mac link, WiFi interface presence, net/provider config and marker, key presence/permissions
  only — key content is never read). Navigation tiles get a chevron, no value line.
- LS is read-only; the usb0 web page `http://172.30.250.2:8780` is the only settings entry. fb19
  网页设置 shows the URL and, with the Mac ONLINE, sends `{"method":"ui.openSettings"}` (<=1/3 s).
- Boot follows the Mac to L1 once if it comes ONLINE before any key/knob input.
- fb20 on L1/L4 is the Mac mode key unless the keyboard agent is allowed/leased (then e-stop);
  K2 hold/triple always e-stops; an e-stop stays on L1/L4 (Mac PTT released, 10 s footer banner).
- Page-switch grace window swallows keys; confirmation requests no longer auto-switch to L5.
- Live L1/L4 publish the host's full 142-px sub-screen; the device footer appears only for
  attention (pairing code, stop, confirmation, stale Mac, held PTT, GD32 mapping warning).
- Web pairing code from `/run/mk20/pair-code` (0600, <=64 B, <=600 s ahead) shown on every page.
- Follow-up (cross-review): status probes on a worker thread only (snapshot mailbox, stale after
  10 s, bounded shutdown); host forwarding pauses after an e-stop on L1/L4 until all released + 1 s;
  fb20-held/agent-activation race -> host up then e-stop; persistent "智能体已急停" notice when the
  agent was active; grace window also swallows K1/K2 short and is capped; safe mode shows the usb0
  IP and the web pairing code.

Host validation only (Docker mk20-build:trixie, gcc 14): `make -C kbd test` (test_kbd 4018 ASan
+ 4018 TSan, onboard 11337, safe mode 2 scenarios, e2e OK incl. stalled-host and blocked-status-
worker stop latencies), `make -C kbd ui-test` (ui 41396 checks, 85/170 conformance, 330 byte-exact
goldens), onboard TSan 11337, `tests_sim` 34 passed, release-binary/docs/mk20d-kbd pytest. **No device
acceptance claimed.** See kbd/UI.md "On-device UX revision" and ONBOARD-UI-PLAN.md decisions.

## 1.3.1-rc3 (K4 field fix)

`firmware/VERSION` is **1.3.1**; build id **rc3**: `sh tools/mk20-release-build.sh firmware out rc3`.
Binaries are unchanged from 1.3.0-rc2 (6f2788b); only `sbin/mk20d` changed.

1.3.0-rc2 field install (2026-10-20 21:08 SGT) booted mk20-kbd fine (21/21 panels, knobs 6/6, 0 kernel
WARN/Oops) but never confirmed: the device BusyBox has **no `stat` applet**, so `stat -c %Y` was empty, the
kbd status age became "now" (38525 s) -> `FAIL kbd status stale` on probation -> loader deadline reset ->
rollback to 1.2.1. Host tests passed because PATH fell back to GNU stat. Fixes: file mtime via
`date -r FILE +%s`, link count via `ls -ld` (the maintenance hook hard-link guard rejected every hook on the
device for the same reason), kbd status age < 0 is stale (`/run` persists across boots on the overlay and there
is no RTC), and `tests/shims/stat` fails like the device so the suite cannot fall back again.

## 1.3.0-rc1 (K4)

`firmware/VERSION` is **1.3.0**; package with build id **rc1**:
`sh tools/mk20-release-build.sh firmware out rc1`. Rebuild release binaries first
on Linux with `sh tools/mk20-build-bins.sh` (`arm-linux-gnueabihf-gcc`); source-only
changes are not ready to install until `bin/mk20-kbd` is rebuilt. Never hand-edit it.

This release defaults `FEATURE_KBD=1` and starts only mk20-kbd. Stock binaries stay
untouched on rootfs, p5 is never written, and no KD start remains in release scripts.
`MK20_KBD=0` / config `FEATURE_KBD=0` is developer-only; it never starts KD. Legacy
`use-kd`, FAT `mk20-use-kd` and `state/kbd-fallback` are ignored (one boot notice).

>=3 exits in 300 s latch safe mode and keep supervised restarts/backoff. Safe mode
renders static rescue text on all panels, retains GD32 typing/local PTT HID/K2
e-stop, uses the unchanged GD32 TX allow-list, and needs no JPEG/host/agent service.
ECM/usb0 and adb remain up. Selftest marks safe mode WARN even during probation;
missing kbd/no GD32/incomplete panels/stale status/panel errors remain FAIL during
probation and WARN after confirm. Loader A/B rollback is retained; no stock-KD
selector or keyboard combo exists. Restarting after HID re-bind may require reboot.

Rescue is safe mode + usb0 admin web + adb + A/B. K4 implements the validated
file-based [maintenance contract](MAINT-HOOKS.md); the web service is separate U3
work. The optional Mac mirror is out of scope. Hardware acceptance, forced-fail
rollback and 24 h soak remain release gates; this worktree makes no device claims.

## What is a release

A release lives in `/data/mk20/releases/<semver>/`:

| path | what |
|---|---|
| `sbin/mk20d` | entry point: gates, gadget launch, self-test, confirm, `health`, `selftest`, `config`, `maint`, `version` |
| `share/mk20lib.sh` | whitelisted config loader (`/data/mk20/config/mk20.conf`, never sourced) + structured logs/rotation |
| `share/gadget.sh` | ECM + HID + ADB, PTY↔TCP bridge; release rollback requests loader A/B, never starts KD |
| `share/flapmon.sh` | flap monitor (1.1.2+): ≥5 link drops in 60 s → rollback; a long host USB suspend (Mac sleep) is not a flap |
| `share/mk20-add-adb.sh` | stock-mode adb add (fallback) |
| `share/vendor.sha256` | expected hashes of the 4 vendor files the KD shims depend on |
| `bin/mk20-kbd` | Default UI/HID daemon; static safe-mode panels after crash loops, K2 e-stop retained |
| `bin/mk20-router` | Legacy router for developer kbd opt-out only |
| `bin/ttygs_tcp_bridge` | static ARM PTY↔TCP bridge (bound to `usb0`, explicit `--listen` only) |
| `lib/usb_f_ecm.ko` | ECM function module for the #221 kernel |
| `MANIFEST` | `name/version/build/kernels` header, `--`, then `sha256 mode path` for every file |

The **rescue tier** (`watcher.sh`, `hardreset.sh`, `loader.sh`, `lunch.sh` stub) is *not* part of any release. It
lives in `/data/mk20/rescue/` and `/mnt/SDCARD/mk20/`, each file with a `.sha256` next to it. It is updated
separately with `tools/mk20-rescue-update.sh`.

## Versioning

- `MAJOR.MINOR.PATCH`, each part 0–999 (`firmware/VERSION`, one line).
- PATCH: fixes with no behaviour change visible to the host. MINOR: host-visible change, such as the default
  subnet, new knobs or new self-test gates. MAJOR: a layout or rescue-protocol change that needs a newer loader.
- Never rebuild a released version with different content. Bump PATCH instead. The installer reuses an identical
  installed version and refuses a pending one.
- `firmware/KERNELS` lists the p4 sha256 values a release supports. These are copied into the MANIFEST
  `kernels=` line. At run time, mk20d also needs `/data/mk20/kernel/ALLOWLIST` to name the running p4 and its
  features.

## Build

```sh
cd deck
make test                                   # shellcheck + box tests (BusyBox 1.27.2) + host tests: must be green
sh tools/mk20-release-build.sh firmware out <build-id>   # or: make release (build id = git short sha)
# -> out/mk20-fw-<v>/ , out/mk20-fw-<v>.tar , out/mk20-fw-<v>.tar.sha256 , out/rescue/ (+ .sha256)
```

The build needs GNU tar and GNU stat (the box, or any Linux). It is reproducible: same tree and build id give the
same tarball sha. It rejects symlinks, unsafe names and a bad VERSION.

## Release checklist

1. `make test` is green on the box.
2. Code review of the milestone diff: check the mihomo AI route first (US-Exit-Hy2), then the reviews in
   `deck/reviews/`. Fix every BLOCKER and MAJOR before building.
3. Build. Copy the `.tar` and `.tar.sha256` to the Mac (`~/mk20-backup/releases/`) and check them with
   `shasum -a 256 -c`.
4. Install on hardware (INSTALL.md §3) with `--reboot`. Watch for `good=<v>` in `loader status` and a
   `mk20d health` status of `ok`.
5. Run the acceptance script (`tools/mk20_accept.py`, INSTALL.md §6).
6. Commit and push: the code, the review files, and the NOTES entry with the hardware evidence.

## Changelog

| version | date (SGT) | changes |
|---|---|---|
| 1.4.2 | 2026-10-21 | **1.4.2-rc3**: debug adbd log on tmpfs capped (append + mk20d maint trim, was unbounded ~1 MB/h under adb polling). |
| 1.4.1 | 2026-10-21 | **1.4.1-rc2**: `/run/mk20` on a 512 KiB tmpfs mounted first thing in boot (mk20-kbd wrote kbd.status 1/s to the TF-card overlay from its e-stop/HID main loop: wear + stall risk; files no longer survive reboots); refuses when a writer or its supervisor lock is live; health `run_tmpfs`, selftest INFO/WARN only; stale-status line carries uptime; soak records selftest WARN lines. Only `sbin/mk20d` changed. |
| 1.4.0 | 2026-10-21 | **1.4.0-rc1**: on-device UX revision (real HOME status, read-only LS, fb19 opens the web on the Mac, boot follows the Mac to L1, fb20 = Mac mode key on L1/L4 unless the keyboard agent is active, e-stop host pause with bounded resync, status probes on a worker thread, full host sub-screen, pairing code incl. safe mode); usb0 settings web default-on as the only settings entry (pairing via /run/mk20/pair-code, maintenance page, catalogue-driven provider/model); Mac mk20deck heartbeat + needs-me hook states. **rc2**: `/run/mk20` on a 512 KiB tmpfs mounted first by `mk20d boot` (kbd.status no longer rewritten on the TF card every second; WARN + overlay fallback), health `run_tmpfs` + INFO/WARN selftest line, stale line carries the uptime, soak `selftest_warn`. Release: only `sbin/mk20d` changed. |
| 1.3.1 | 2026-10-20 | **1.3.1-rc3**, build id `rc3`: K4 field fix — device BusyBox has no `stat`: kbd status age and maintenance hook link-count checks used `stat -c` and always failed on hardware (rc2 never confirmed, rolled back to 1.2.1); now `date -r` / `ls -ld`, negative age is stale, test shim removes `stat`. Binaries unchanged. |
| 1.3.0 | 2026-10-20 | **1.3.0-rc1**, build id `rc1`: K4 default mk20-kbd, no stock-KD starts/overrides, crash-loop safe mode, maintenance file hooks, unchanged verified one-switch A/B installer; no rootfs writes. Web handlers/device acceptance separate. |
| 1.0.0 | 2026-10-18 | M1: first release layout (A/B slots, MANIFEST, loader, rescue tier) |
| 1.0.1 | 2026-10-18 | M1 review fixes (upgrade 1.0.0 → 1.0.1 on hardware at 17:35) |
| 1.0.2 | 2026-10-18 | M2: mk20d (gates, health/self-test, logs, whitelisted config), bridge bound to usb0. **Bug:** an inline comment disabled bridge cleanup in gadget.sh rollback |
| 1.0.3 | 2026-10-18 | M2/M3 review fixes. **Bug:** `pgrep -x KeyboardDevice` never matches on BusyBox 1.27, so the self-test failed and the release auto-rolled back to 1.0.2 (rollback proven on hardware) |
| 1.0.4 | 2026-10-18 | KD count via `pgrep -f '^/data/KeyboardDevice'`, cleanup fix, installer/prune/uninstall |
| 1.1.0 | 2026-10-18 | M4: default subnet 172.30.250.0/24 (device .2, Mac .1); link-local listener 169.254.7.2 required by self-test |
| 1.1.1 | 2026-10-18 | During probation, a composite setup failure (gadget rc≠0) is a self-test FAIL, so the release rolls back |
| 1.1.4 | 2026-10-18 | M7-fixes reviews: `gadget.sh --rollback-now` guard: all transitions under flock (closed before cleanup), owner = pid + start time + 150-s deadline (dead/reused/hung owners reclaimed), `done` only after stock is verified (ACM+UDC+KD), exit 0 only on verified stock, composite path never clears a live owner; stronger stock check (no ECM/HID links, real ttyGS0, shims unmounted, anchored KD pgrep, 30-s poll) and status file `rollback-verified`/`rollback-NOT-verified`; a run refuses to start while a rollback is in progress; staged script copies replaced atomically; flapmon retries a failed rollback 3x then sets `mk20-no-composite` and hard-resets; gadget.sh waits for flapmon's lock-held ready marker. Host tools (not in the tarball): tf-restore-boot.sh whole-image needs exact card size, valid and identical primary+backup GPT (geometry, disk GUID, entry table) with slots 1-6 = the exact MK20 layout, and a matching `.sha256`; accept/soak write nothing to the device and never connect to the single-client bridge by default (soak never; accept only with the maintenance flag `--tcp`, bound, fail-closed client check), no HID writes, every ping bound to the MK20 interface, soak route check + single-instance lock; accept `--reboot` checks boot_id; tf-restore `--image` accepts only the three known p4 shas, whole-card needs typed confirmation, fast readback |
| 1.2.0 | 2026-10-19 | On-board router (plan steps 1-2): `ttygs_tcp_bridge` v3 `--tap /tmp/mk20/kd-tap.sock` (0600 unix socket, one client, KD→host copy, MSG_DONTWAIT drop-on-backpressure, never stalls KD or the Mac link; a tap that cannot be created is skipped); static armhf `bin/mk20-router` (non-blocking HID writes, ≤500 ms wait); `mk20d router` supervised loop (flock single instance, backoff 1→30 s, reset after a 60-s run; ends on `/tmp/mk20-router-stop`, composite down, or `FEATURE_ROUTER=0` / FAT flag `mk20-no-router` / `MK20_ROUTER=0`), launched by boot only on a clean composite with HID; selftest `INFO router …` lines only (never FAIL/WARN: no effect on confirm or status); health.json `router` {enabled, running, loop, tap}; maint trims `/tmp/mk20/router.log`. Binaries live in the release on /data (FAT unchanged: boot stub only); `make bins` / `tools/mk20-build-bins.sh` rebuilds both from source (test checks they match). r2 (r120 review blockers): gadget.sh `router_stop` (stop file, validated supervisor kill, SIGTERM ≤2 s, SIGKILL) runs before any UDC/configfs change in `cleanup()` and `setup_main` (f_hid cdev UAF on this kernel); router state changes only after a successful HID write, undelivered reports are resent on POLLOUT, all-keys-up at startup and (bounded) on EOF/SIGTERM/SIGINT; the bridge closes a tap client on any incomplete send (router EOF → release) |
| 1.1.3 | 2026-10-18 | M5–M7 review fixes: flapmon counts a short suspend that ends in a reset/detach (a long suspend = Mac sleep is not counted), single instance (flock), re-checks the stopfile before rolling back, validates its arguments; `gadget.sh --rollback-now` once-guard (`/tmp/mk20/rollback.once`), confirm waiter honours the flapmon stopfile, a re-run stops the old monitor; rollback env via `export` |
| 1.1.2 | 2026-10-18 | M7: flap monitor moved to `share/flapmon.sh` (unit-tested); a host USB suspend/resume (Mac sleep) is logged, not counted as a flap |

Decision record: adb stays on in stock fallback even when the vendor hashes don't match (remote reachability wins).
See NOTES-crash-20261018-and-tf-backup.md, "Decision (user, 18:32 SGT)".
