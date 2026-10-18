I'll read the plan and the context files first, then write the review without changing anything.The `git diff` call was rejected, so I won't retry it; I'll review the working-tree version of the gadget script. Next I'm checking `mk20-add-adb.sh` and the earlier composite reviews for context.# MK20 production plan v0.1: review

## 1. Verdict: GO-with-conditions

The architecture is sound: versioned releases, verify before switching, a self-test, a boot counter, and a separate kernel path. The layout as written can't be deployed, though. It doesn't fit on the card, and it puts the remote-rescue path inside the thing that gets rolled back. B1–B4 below must be fixed before M1 touches the device.

## 2. Blocking issues

**B1. The layout doesn't fit, and FAT is the wrong place for the switch.**
- `/mnt/SDCARD` is an **8 MiB** FAT (M1 probe in `NOTES-kernel-patch-recovery.md`). `boot-stock.img` already takes 6.15 MiB of it, leaving under 2 MiB.
- None of this fits in that space:
  - `kernel/<sha>/` (two or three more boot images)
  - 5 × 512 KB of logs, plus one `selftest-<boot>.json` per boot with no limit
  - three releases plus `.incoming`
- A full FAT also breaks host recovery, because `saveToFile` of `job.sh` fails.
- On FAT, `current` and `previous` sit in the same directory sector, so one torn write loses both. Falling back to `previous` doesn't cover the main failure.

Fix:
- Move `releases/`, the pointers, `state/` and `kernel/` to `/data/mk20` on UDISK. Confirm it's ext4, which gives a real atomic `rename` and `fsync`. KD's `saveToFile` (any path, creates directories) can still write there.
- Keep only these on FAT: `lunch.sh`, the flags, the rescue tier (B2), the one stock image, and size-capped logs. Always keep at least 1 MiB free.
- First, back up the vendor files in `/data` (`KeyboardDevice`, `appLunch.sh`, `setusbconfig`, `qt_env.sh`). p6 has no backup.

**B2. The rescue tier must sit outside the releases.** The plan puts `watcher.sh` and `hardreset.sh` in `share/`. Its rule "if both releases fail, run nothing" then also removes `job.sh` and `cmd-reboot`, and only a TF pull can recover the device.
- The stub should always start a fixed, separately sha-checked watcher and `hardreset.sh` first, then the release.
- That watcher should accept `cmd-rollback` and `cmd-no-release`.
- Stub v2 must do no more work in the foreground than v1, because `qt_app1` waits for it.

**B3. Boot-counter rules.**
- Only the stub increments the counter, before it launches a pending release. A release that crashes can't count itself.
- Roll back to `good` (last confirmed), not `previous`. Installing over an unconfirmed release turns `previous` into a bad release, so refuse installs while one is pending.
- A hang produces no further boots. M4's "no manual steps" therefore needs a deadline in the stub: no pass by 600 s uptime counts as a fail.
- Decide rollback only on checks that don't need the host: exactly one KD, mk20d alive, UDC bound, ttyGS0 present. A composite or flap rollback, no host attached, or a sleeping Mac means "degraded", not "failed".
- Allow at most one automatic reset per pending install, and record it before it fires. If `good` also fails, stay up on stock ACM+KD+adb. Otherwise two bad releases, or a flaky cable, become a watchdog reset loop roughly every 70 s.

**B4. Resets and kernel writes must exclude each other.**
- The watcher's main loop can already run `cmd-reboot` while the detached postswap revert is writing p4 with `dd`. Auto-rollback adds more resets.
  - Kernel writers should hold a `/tmp` flock that every reset path respects.
  - Allow only one pending state (kernel or userland) at a time.
- postswap ends in `reboot -f` and bootswap in `reboot`. Both hit the proven PSCI hang, so use `hardreset.sh` instead.
- Every reboot today is an unclean FAT shutdown. Before `hardreset.sh` stops the watchdog pings, it should stop the FAT writers and run `mount -o remount,ro /mnt/SDCARD`.
- Add "procd watchdog running" to the self-test, because remote reboot depends on it.

**Also required**
- **Kernel identity:**
  - `uname` reports #221 on all three kernels.
  - After a `noreboot` swap, p4 no longer matches the running kernel. Record the p4 sha once at boot.
  - Replace "min/max kernel sha" with an allow-list that maps each sha to its features: stock = ACM+adb, ep3 = no-HID, ep5out = full. A postswap revert to stock then degrades instead of failing.
  - postswap should revert to the last confirmed baseline, not to stock.
- **KD shims:**
  - Enable them only when the sha256 of `/data/{appLunch.sh,KeyboardDevice,setusbconfig}` matches a known value; `S99swupdate_autorun` exists. Keep them tmpfs-only.
  - The health check can't use `dmesg`, because KD runs `dmesg -c` every 3 s; postswap's dmesg check is already blind for this reason. Read `/dev/kmsg`, which keeps its own read position.
- **Install hygiene:**
  - Validate the pointer with a strict semver regex, so it can't contain `../`.
  - Verify every MANIFEST file, not just mk20d, and run verified copies from `/tmp`.
  - Add the missing step: rename `.incoming-<v>` to `<v>` and `sync` before switching the pointer.
  - Never install over `current` or `good`.
  - Drop the MANIFEST `target` field.
  - Parse `mk20.conf` against a whitelist; never source it.

## 3. ECM subnet: keep 172.30.250.0/24

- The device subnet is a directly connected /24, which beats any broader route a VPN or TUN adds: mihomo auto-route, or split-includes like 10/8 and 172.16/12. A real clash needs an equal or more specific pushed route, or a LAN that uses the same /24.
- **172.31/16** is the default VPC in every AWS account and region, so it's the 172.x range VPNs push most often. **172.30/16** is OpenShift's default service range, but that stays inside the cluster.
- Docker Desktop, Colima and Podman networks stay inside their VM and get no host routes. Avoid 172.16/16 (VMware Fusion) and 192.168.64/24 (Apple vmnet).
- **Link-local only: no.**
  - macOS routes `169.254/16` through the primary interface only, so clients that don't bind en13 go out en0. That's why `en-relay.py` binds en13.
  - Screen Sharing for the VNC milestone won't bind the interface.
  - Keep 169.254.7.2 as the discovery/rescue alias.
- "Block local LAN" VPN policies break every option equally.

Conditions:
- Re-run the mihomo check for 172.30/16, since only the 10/8 exclusion was verified. Use `route -n get` with TUN on and off.
- Have the host preflight fall back to link-local if another route of /24 or narrower already overlaps.
- For other units: derive each unit's MACs from the SoC SID and give each unit its own /24.

## 4. Missing acceptance criteria and tests

- **Power-cut injection:** `hardreset` at every install, rollback and counter-write step, at least 20 cycles. Pass = a working release, or stock with a live watcher, plus a clean `fsck_msdos -n` after a TF pull.
- **Corruption cases**, each must end in a working release or stock with a live watcher:
  - empty, garbage or `../` pointer
  - missing MANIFEST
  - corrupt `.ko`, bridge or gadget script
  - full FAT
  - both releases bad (`cmd-ping` must still answer)
- Each release on all three p4 shas, plus a postswap revert while an install is pending.
- A forced failure on both `current` and `good` causes at most one reset.
- **Host events:** Mac sleep/wake ×10, Mac reboot, replug, another port or hub, charger-only power. None may cause a false rollback. Decide whether a USB suspend counts as a flap-monitor drop.
- **24 h soak** with ECM, KD and HID traffic: memory, FAT usage and KD restarts stay bounded.
- A reboot requested during an install or a kernel `dd` waits until it finishes. Every `mk20d` subcommand queues itself through the watcher when run from `adb shell`.
- Migration from the current v3.4 setup disables the old boot-every jobs in the same switch; otherwise two gadget builds race. Uninstall returns the device to stock.
- The bridge and status port listen only on the usb0 addresses, not `0.0.0.0`. Wi-Fi is the likely VNC transport, and `saveToFile` plus `job.sh` amounts to unauthenticated root access. Sign releases before other units get them.

## 5. Milestone order

1. **New M0.5, before any device change:** the B1 storage move, the `/data` backup, one real rehearsed TF restore, and the B4 fixes (pulled forward from M5).
2. **M1**, with the B2 rescue tier and M6's fake-sysfs and fault-injection harness. The stub is the only part with no fallback, so it needs those tests before deployment.
3. **M3**, with the config parser.
4. **M4**, with power-cut injection.
5. **M2's subnet switch**, shipped as 1.1.0 through the new installer. That exercises upgrade and rollback for real. Keep the 10.20.0.2 alias until the daemon update ships.
6. **M5.**
7. **M6**, reduced to wiring everything into `make test`.
8. **M7**, plus the soak and host-event tests.