# MK20 Agent firmware: releases

Applies to: Waveshare MK20 (Allwinner T113, Tina Linux 5.4.61 #221, BusyBox 1.27.2). The firmware is a userland
layer on `/data` (ext4/UDISK). It never writes the rootfs. Kernels are only swapped as whole boot images (p4,
see RECOVERY.md level 5).

## What is a release

A release lives in `/data/mk20/releases/<semver>/`:

| path | what |
|---|---|
| `sbin/mk20d` | entry point: gates, gadget launch, self-test, confirm, `health`, `selftest`, `config`, `maint`, `version` |
| `share/mk20lib.sh` | whitelisted config loader (`/data/mk20/config/mk20.conf`, never sourced) + structured logs/rotation |
| `share/gadget.sh` | ECM + HID + ADB composite, PTY↔TCP bridge, rollback to stock ACM+KD |
| `share/flapmon.sh` | flap monitor (1.1.2+): ≥5 link drops in 60 s → rollback; a long host USB suspend (Mac sleep) is not a flap |
| `share/mk20-add-adb.sh` | stock-mode adb add (fallback) |
| `share/vendor.sha256` | expected hashes of the 4 vendor files the KD shims depend on |
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
| 1.0.0 | 2026-10-18 | M1: first release layout (A/B slots, MANIFEST, loader, rescue tier) |
| 1.0.1 | 2026-10-18 | M1 review fixes (upgrade 1.0.0 → 1.0.1 on hardware at 17:35) |
| 1.0.2 | 2026-10-18 | M2: mk20d (gates, health/self-test, logs, whitelisted config), bridge bound to usb0. **Bug:** an inline comment disabled bridge cleanup in gadget.sh rollback |
| 1.0.3 | 2026-10-18 | M2/M3 review fixes. **Bug:** `pgrep -x KeyboardDevice` never matches on BusyBox 1.27, so the self-test failed and the release auto-rolled back to 1.0.2 (rollback proven on hardware) |
| 1.0.4 | 2026-10-18 | KD count via `pgrep -f '^/data/KeyboardDevice'`, cleanup fix, installer/prune/uninstall |
| 1.1.0 | 2026-10-18 | M4: default subnet 172.30.250.0/24 (device .2, Mac .1); link-local listener 169.254.7.2 required by self-test |
| 1.1.1 | 2026-10-18 | During probation, a composite setup failure (gadget rc≠0) is a self-test FAIL, so the release rolls back |
| 1.1.4 | 2026-10-18 | M7-fixes reviews: `gadget.sh --rollback-now` guard: all transitions under flock (closed before cleanup), owner = pid + start time + 150-s deadline (dead/reused/hung owners reclaimed), `done` only after stock is verified (ACM+UDC+KD), exit 0 only on verified stock, composite path never clears a live owner; stronger stock check (no ECM/HID links, real ttyGS0, shims unmounted, anchored KD pgrep, 30-s poll) and status file `rollback-verified`/`rollback-NOT-verified`; a run refuses to start while a rollback is in progress; staged script copies replaced atomically; flapmon retries a failed rollback 3x then sets `mk20-no-composite` and hard-resets; gadget.sh waits for flapmon's lock-held ready marker. Host tools (not in the tarball): tf-restore-boot.sh whole-image needs exact card size, valid and identical primary+backup GPT (geometry, disk GUID, entry table) with slots 1-6 = the exact MK20 layout, and a matching `.sha256`; accept/soak write nothing to the device and never connect to the single-client bridge by default (soak never; accept only with the maintenance flag `--tcp`, bound, fail-closed client check), no HID writes, every ping bound to the MK20 interface, soak route check + single-instance lock; accept `--reboot` checks boot_id; tf-restore `--image` accepts only the three known p4 shas, whole-card needs typed confirmation, fast readback |
| 1.1.3 | 2026-10-18 | M5–M7 review fixes: flapmon counts a short suspend that ends in a reset/detach (a long suspend = Mac sleep is not counted), single instance (flock), re-checks the stopfile before rolling back, validates its arguments; `gadget.sh --rollback-now` once-guard (`/tmp/mk20/rollback.once`), confirm waiter honours the flapmon stopfile, a re-run stops the old monitor; rollback env via `export` |
| 1.1.2 | 2026-10-18 | M7: flap monitor moved to `share/flapmon.sh` (unit-tested); a host USB suspend/resume (Mac sleep) is logged, not counted as a flap |

Decision record: adb stays on in stock fallback even when the vendor hashes don't match (remote reachability wins).
See NOTES-crash-20261018-and-tf-backup.md, "Decision (user, 18:32 SGT)".
