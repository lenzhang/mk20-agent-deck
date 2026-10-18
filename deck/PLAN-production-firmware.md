# MK20 Agent enhanced firmware: production plan (v0.2, 2026-10-18; v0.1 reviewed GO-with-conditions by both models, conditions folded in (§11))

Scope: turn the P2 device-side work (patched stock kernel, ECM+HID+ADB composite, KD over a PTY-TCP bridge,
watchdog hard reset, watcher/boot jobs) into a versioned, installable, recoverable product. The first target is
the user's own unit. The layout must allow other units later. Stock rootfs stays untouched: everything lives on
the TF card's FAT partition (/mnt/SDCARD) and /data, and is removable.

## 1. Current state (baseline being packaged)
- Kernel: stock 5.4.61 #221, binary-patched (ep3 ISO->INT, ep5out INT-capable), p4 sha 7326d9e5…;
  stock p4 9ac68ed9… kept on the FAT partition, in /data, on the Mac and on the box. Recovery: TF pull plus
  tools/tf-restore-boot.sh (slice s4).
- Hooks: qt_app1 -> /mnt/SDCARD/lunch.sh (stub) -> watcher.sh (/tmp copy) -> boot-once / boot-every /
  boot-delayed / job.sh (sha-gated).
- Device scripts: gadget-ecm-hid-adb.sh v3.4, boot-every/{adb,composite}.sh, hardreset.sh, mk20-add-adb.sh,
  ttygs_tcp_bridge (static ARM), usb_f_ecm.ko, flag files (mk20-no-*), logs in /mnt/SDCARD/mk20/logs (no rotation).
- Proven: composite at boot with flap and failure rollback to ACM+KD+adb, WDT reboot (~70 s to composite),
  KD dmesg-shim fix, daemon TCP-first with IP_BOUND_IF.

## 2. Target layout
```
/mnt/SDCARD/mk20/
  current -> releases/1.0.0      (symlinks are not possible on FAT: use a text pointer file `current` + `previous`)
  releases/<semver>/
    MANIFEST            name, version, build id, min/max kernel sha, file list with sha256 + mode + target
    bin/  ttygs_tcp_bridge, (later) vnc agent
    lib/  usb_f_ecm.ko (bound to kernel sha)
    sbin/ mk20d (single entry point: boot | health | reboot | rollback | status | composite on|off)
    share/ watcher.sh, hardreset.sh, gadget.sh, add-adb.sh, flapmon.sh, shims
  config/mk20.conf        key=value (shell-sourceable after validation), survives upgrades
  state/                  boot counter, last-good version, self-test results (JSON)
  logs/                   mk20.log (+ .1 .. .4, size-capped rotation), selftest-<boot>.json
  kernel/<kver-sha>/      boot.img artifact + MANIFEST + RECOVERY.txt (stock and patched)
```
- lunch.sh stub (unchanged contract, tiny): reads `current`, verifies MANIFEST sha of the entry point, runs
  `releases/<v>/sbin/mk20d boot`. If verification fails, it falls back to `previous`. If that fails too, it runs
  nothing (stock behaviour).
- Flags stay as overrides: /mnt/SDCARD/mk20-no-watch (stub level), mk20-no-composite, mk20-no-adb,
  mk20-gadget-nohid. Precedence: flag file > mk20.conf > release defaults.

## 3. Config (mk20.conf, validated)
```
ECM_SUBNET=172.30.250.0/24     # device .2, host .1; link-local alias 169.254.7.2/16 always kept
ECM_DEV_MAC=02:11:22:33:44:55
ECM_HOST_MAC=02:11:22:33:44:56
FEATURE_COMPOSITE=1  FEATURE_HID=1  FEATURE_ADB=1  FEATURE_BRIDGE=1
BRIDGE_PORT=17890
FLAP_DROPS=5  FLAP_WINDOW_S=60
LOG_MAX_KB=512  LOG_KEEP=4
```
- Subnet proposal: 172.30.250.0/24. Rationale: avoids 10/8 (corporate VPN pushes), 192.168/16 (home LANs),
  100.64/10 (Tailscale/CGNAT), 198.18/15 (mihomo/Clash fake-ip), and 172.31/16 (AWS default VPC: the suggested
  172.31.250.0/24 collides when VPN'd into AWS). Docker Desktop's 172.17-31 networks live inside its VM and are
  not host routes. Configurable; the Mac side (networksetup) and the daemon (MK20_TCP) read the same value.
  Migration: 10.20.0.2 stays as an extra alias for one release.

## 4. Installer / updater (atomic, with rollback)
- `mk20-release` build (Mac/box): stage the dir, write MANIFEST (sha256 of every file), tarball + sha256, semver
  from git tag, build id = git sha.
- Install (`mk20d install <tarball>` or host-side `mk20ctl install` over TCP/ACM/adb):
  1) upload to releases/.incoming-<v>; 2) verify the tarball sha, then every file vs MANIFEST; 3) check kernel
  compatibility (running p4 sha in MANIFEST allow-list); 4) write `previous` = old current, then `current.new`,
  fsync, rename to `current` (FAT rename is atomic enough for a single small file; the stub tolerates a
  missing/garbled pointer by falling back to `previous`); 5) mark pending-confirm, boot counter = 0.
- Boot-time confirm: mk20d boot runs the self-test. Pass -> last-good = current. Fail, or 3 boots without
  reaching "pass" -> automatic rollback to `previous` plus a hard reset. Keep the 2 newest releases + last-good.
- Kernel updates are a separate, explicit path (existing bootswap + postswap-check flow, now versioned under
  kernel/), never automatic with a userland update.

## 5. Health check and boot self-test
- `mk20d health` (JSON): kernel sha, release version, KD running (exactly 1), g1 links, UDC state, ttyGS0 or
  bridge alive, usb0 address, adbd, hidg0 writable, flapmon alive, shim mounts consistent, free mem/disk, last
  rollback reason.
- Boot self-test = health after composite settle + 30 s stability window. Result goes to
  state/selftest-<n>.json and to the summary log line. Exposed to the host: the `getFilesBySuffix`/file read
  path over KD and a tiny HTTP-less TCP status port (later), plus adb.

## 6. Logging
- One structured line format: `ts uptime level component event k=v...`; all scripts log via `mk20log`.
- Rotation by size (LOG_MAX_KB, LOG_KEEP), done by the watcher every N minutes and at boot. Never log
  secrets (VNC password).

## 7. Tests (CI-style, runnable on the box)
- shellcheck (POSIX sh, busybox dialect) for all device scripts.
- Unit tests with a fake sysfs/configfs tree + mocked busybox tools (bats or a Python harness using
  `sh` with PATH-shimmed commands): gadget build/rollback paths, flapmon counting, readd_adb loop, config
  validation, MANIFEST verify, installer pointer switch and rollback, stub fallback.
- Host-side: pytest for the daemon transport, mk20ctl install, release builder.
- Hardware acceptance script (manual trigger): reboot, composite, ping, getInfo TCP, adb, HID shift tap,
  forced failure, flap rollback, upgrade, downgrade/rollback.

## 8. Docs
- RELEASE.md (versioning, changelog, build), INSTALL.md (first install from the current ad-hoc layout,
  upgrades, Mac setup), RECOVERY.md (levels: flag files -> rollback release -> remove stub -> kernel restore via
  TF + tf-restore-boot.sh s4), plus kernel/<sha>/RECOVERY.txt.

## 9. Milestones and acceptance criteria
| M | Deliverable | Acceptance |
|---|---|---|
| M0 | This plan, reviewed by 2 models | Both reviews GO / GO-with-conditions, conditions folded in |
| M1 | Release layout + MANIFEST + build tool + stub v2 (pointer, verify, fallback) | Stub boots release 1.0.0 from the pointer; a corrupted file -> falls back to `previous`; absent release -> stock behaviour; box tests green |
| M2 | Config file + flags-as-override + configurable subnet (default 172.30.250.0/24) | Composite comes up on the configured subnet; flags override config; daemon/networksetup use the same subnet; link-local alias works |
| M3 | mk20d entry point + health + boot self-test + structured logs/rotation | `mk20d health` JSON correct in composite and rollback states; self-test file per boot; logs capped |
| M4 | Installer/updater with atomic switch + auto rollback | Upgrade 1.0.0->1.0.1 and back over TCP; forced self-test failure on 1.0.1 -> auto rollback to 1.0.0 within 3 boots; no manual steps |
| M5 | Kernel artifact versioning + docs set | kernel/<sha>/ for stock + patched with MANIFEST; RECOVERY.md walkthrough verified on the box with the real images |
| M6 | CI: shellcheck + box unit tests + host pytest in one `make test` | All green; failure paths covered (gadget rollback, flapmon, installer, stub) |
| M7 | Hardware acceptance run | Full acceptance script passes twice in a row after WDT reboots |
Then: VNC milestone (PRD §14 R-CU-1) on top of the packaged base.

## 10. Risks
- FAT has no symlinks/atomic multi-file ops -> pointer files + verify-before-switch + fallback.
- Bricking risk stays limited to userland (stub is tiny and unchanged in contract; kernel path separate).
- KD behaviour (dmesg-c USB reset, setusbconfig at start) is undocumented vendor logic: the shims are part of
  the release and covered by the self-test.

## 11. v0.2 changes from the reviews (reviews/production-plan-*.md, AI route US-Exit-Hy2 checked 16:19)
Both reviewers: GO-with-conditions. These rules override §2-§9 where they conflict.

**Storage (verified 16:30).** /mnt/SDCARD is an 8 MiB FAT with 1.36 MiB free (boot-stock.img alone is 6.2 MiB).
/data sits on the UDISK ext4 overlay with 2.5 GB free. Changes:
- releases/, pointers, state/ and kernel/ move to `/data/mk20/` (ext4: real atomic rename + fsync).
- FAT keeps only: the lunch.sh stub, flag files, the stock boot image and size-capped logs (max 256 KiB total).
  Keep at least 1 MiB free; the self-test fails "degraded" below that.
- Vendor /data backup done 16:33: Mac ~/mk20-backup/data-p6-20261018/ (upper + rom tars, SHA256SUMS);
  box /workspace/mk20-backup/data-p6-20261018/ (vendor files + rom tar).
  - KeyboardDevice 25d8a404…  appLunch.sh a52a7938…  setusbconfig 0ad94f70…  qt_env.sh ad412634…

**Rescue tier is outside the releases.** The stub always starts a fixed, separately sha-checked rescue watcher
first. That watcher handles job.sh, cmd-reboot, cmd-rollback, cmd-no-release, plus hardreset.sh. Only then is the
release launched in the background. A bad release, or both releases bad, never removes remote recovery. The stub
does no more foreground work than v1 (qt_app1 waits on it).

**Pointers / state machine.**
- Pointers: two slots (A/B) on ext4. Each holds generation, version, MANIFEST sha256 and a self-checksum. Write
  and fsync the inactive slot; boot picks the highest valid generation. Strict semver regex; no paths.
- Install steps: verify the tarball against the host-pinned sha256 (MANIFEST digest trusted only via that pin;
  signing before any second unit), then every file, then rename `.incoming-<v>` -> `<v>`, fsync, switch the slot.
  - Never install over `current` or `good`. Refuse an install while one is pending.
  - Clean stale `.incoming-*` at boot.
- States: pending -> (stub increments and flushes the attempt counter BEFORE launch) -> self-test -> confirmed
  (= `good`) | rolled-back.
  - Rollback target is `good` (last confirmed), not `previous`.
  - No pass by 600 s uptime counts as a fail.
  - At most ONE automatic reset per pending install, recorded before it fires. If `good` also fails: stay on stock
    ACM+KD+adb with the rescue watcher. No reset loop.
- Fail vs degraded: rollback only on host-independent checks (exactly 1 KD, mk20d alive, UDC bound, ttyGS0/bridge
  present, procd watchdog armed). No host / Mac asleep / composite flap-rollback = "degraded", not "failed". Host
  confirmation (KD request over TCP) upgrades probation to "confirmed+host" when available.

**Resets and kernel writes exclude each other.**
- A /tmp lock is held by kernel writers (bootswap/postswap dd). Every reset path (cmd-reboot, auto-rollback,
  hardreset) waits for it.
- Only one pending item (kernel OR userland) at a time.
- bootswap/postswap use hardreset.sh instead of reboot / reboot -f (both hit the PSCI hang).
- hardreset.sh: stop FAT writers, `sync`, `mount -o remount,ro /mnt/SDCARD`, then stop the watchdog pings.
  It refuses (and logs "needs power-cycle") if the procd watchdog is not armed, because sysrq-b goes through the
  same hanging PSCI path.
- Watcher job timeout must kill the whole job (process group/pid list), and gadget transitions must be bounded.

**Compatibility set.** An exact full-p4 sha256 allow-list, recorded once at boot (uname is #221 for all three),
maps each kernel to its features:
- stock 9ac68ed9: ACM + adb
- ep3 (sha in kernel/ inventory): no HID
- ep5out 7326d9e5: full composite
An unknown kernel gets no USB changes at all. KD shims enable only when the vendor hashes above match (fail
closed). MTU 478 is set before `usb0 up` and reported by health (over-MTU test). Health reads /dev/kmsg, never
`dmesg`, because KD's `dmesg -c` empties the ring.

**Config.** mk20.conf is parsed against a key whitelist, never sourced.

**Network/security.**
- ECM default subnet 172.30.250.0/24 (device .2, Mac .1). Both reviewers reject 172.31/16 (AWS default VPC).
- GPT preferred link-local-only. Rejected as default: macOS routes 169.254/16 via the primary interface only, and
  Screen Sharing (VNC milestone) cannot be interface-bound. Link-local 169.254.7.2 stays as the rescue/discovery
  alias.
- Host preflight falls back to link-local if an existing route of /24 or narrower overlaps.
- 10.20.0.2 alias kept only until the daemon update ships, then removed.
- Bridge and status port bind to usb0 addresses only (today 0.0.0.0:17890, an unauthenticated KD/root path if
  Wi-Fi is ever up).
- Re-check mihomo for 172.30/16 (excluded via 172.16.0.0/12) with `route -n get`, TUN on.
- Later units: MACs derived from the SoC SID; per-unit /24.

**Added acceptance tests.**
- Power-cut (hardreset) injection at every install/pointer/counter step, ≥20 cycles. Pass = working release, or
  stock with a live rescue watcher; `fsck_msdos -n` clean after a TF pull.
- Corrupt/empty/`../` pointer, missing MANIFEST, corrupt .ko/bridge/gadget, full FAT, both releases bad (cmd-ping
  still answers).
- Each release on all 3 kernels; postswap revert while an install is pending; forced failure on current AND good
  -> at most one reset.
- Intentional SET_IDLE/SET_REPORT -> no usbc0 reload. Then rollback -> no bind mounts, stock setusbconfig works,
  exactly 1 KD (proven manually 16:21 with gadget v3.5).
- Host events: Mac sleep/wake ×10, Mac reboot, replug, other port/hub, charger-only power. None may cause a false
  rollback (decide: a USB suspend is not a flap).
- 10 consecutive WDT boots; 24 h soak (TCP+ADB+HID, KD restarts, log rotation) with bounded memory and FAT usage.
- Migration from the ad-hoc v3.5 layout disables the old boot-every jobs in the same switch. Uninstall returns the
  device to stock.

**Milestone order (v0.2).**
| M | Deliverable |
|---|---|
| M0 | Plan + 2 reviews (done) |
| M0.5 | Baseline hashes + /data backup (done); kernel/ inventory on ext4; rehearsed TF restore (USER: TF pull); reset/kernel-write lock; hardreset remount-ro + watchdog-armed guard; bootswap/postswap -> hardreset |
| M1 | Box test harness (fake sysfs/configfs, mocked busybox, fault injection) + shellcheck, THEN stub v2 + rescue tier + release layout + A/B pointers + MANIFEST + build tool |
| M2 | mk20d entry point, health/self-test (kmsg), structured logs + rotation, whitelisted config, vendor hash gates, bridge bind usb0 |
| M3 | Installer/updater + state machine + auto rollback, with power-cut injection |
| M4 | Subnet switch to 172.30.250.0/24 shipped as 1.1.0 THROUGH the installer (real upgrade/rollback exercise) + Mac/daemon update | DONE 19:12 SGT: 1.1.0 + 1.1.1 good on HW, Mac en13 172.30.250.1 + MK20 link-local svc, daemon candidates (p0) |
| M5 | Docs: RELEASE / INSTALL / RECOVERY + kernel/<sha>/RECOVERY.txt | DONE 20:10 SGT: firmware/{RELEASE,INSTALL,RECOVERY}.md (Mac service changes + undo), kernel notes on device, tests/test_docs.py |
| M6 | `make test` wiring everything (box) | DONE: shellcheck + py_compile + host pytest (28) + box pytest (240) green |
| M7 | Hardware acceptance (2 runs) + host events + soak; then VNC milestone | IN PROGRESS: 1.1.3 accept PASS x2 after WDT reboots (20:06, 20:08 SGT); Serial off/on x3 OK; 24h soak started 20:11 SGT |
