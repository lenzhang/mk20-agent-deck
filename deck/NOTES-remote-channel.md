# MK20 persistent remote channel + boot-swap dry run (staged 2026-10-18 ~12:15 SGT)

## Staged on device (via saveToFile over ACM, `deck/tools/mk20ctl.py stage-dryrun`)
| path | purpose |
|---|---|
| /data/mk20/boot-new.img (+.sha256) | patched kernel boot.img (sha 59c47e2b…), overlay/UDISK |
| /mnt/SDCARD/boot-swap.dry | DRY: bootswap backs up only, never writes boot |
| /mnt/SDCARD/mk20/watcher.sh | watcher (polls SDCARD every 2 s) |
| /mnt/SDCARD/mk20/boot-once/bootswap.sh (+.sha256) | runs once after KD is up |
| /mnt/SDCARD/mk20/boot-delayed/try-adb.sh (+.sha256), mk20/mk20-add-adb.sh | ACM+ADB attempt ~2 min after boot |
| /mnt/SDCARD/lunch.sh | stub (written last). Replaced lunch-m2test.sh (crc 3024599274) |

NOTE: `setFileCRC` only *records* a CRC (any value accepted; missing file rejected) and
`getFilesBySuffix` lists that registry, so neither verifies content. On-device integrity
is enforced by sha256 gates (boot-new.img, boot-once/boot-delayed scripts, job.sh).
saveToFile creates parent dirs (QDir::mkpath). The client deletes before each put.

## What one replug / reboot does
qt_app1 (S99) waits for SDCARD → copies lunch.sh → /data/lunch.sh → stub copies watcher
to /tmp/mk20-watcher.$$.sh, backgrounds it, returns (qt_app1 continues, adbd killed, KD up).
Watcher: flock singleton; waits for KeyboardDevice (≤90 s) + 8 s → boot-once/bootswap.sh:
backs up boot partition → /mnt/SDCARD/boot-stock.img (+.sha256) and /data/mk20/boot-stock.img,
verifies size+sha, writes `dry-ok …` to /mnt/SDCARD/boot-swap-status.txt, emits log over
ttyGS0 as `@@MK20 BEGIN boot-bootswap-1@@ … @@MK20 END …@@`. 110 s later try-adb
(stops qt_app2/KD, composite ACM+ADB with FFS-before-bind; 600 s watchdog restores serial+KD;
on failure restores immediately). Then heartbeat emit.

## Remote control afterwards (no replug)
`mk20ctl.py ping | reboot | job script.sh | listen --wait S` (Mac, p0 venv). Kill switches on
SDCARD: `mk20-no-watch` (watcher), `mk20-no-bootswap`, `mk20-no-adb`. Remove: delete lunch.sh.
Real swap = delete boot-swap.dry + re-stage bootswap.sh(.sha256) into boot-once + cmd-reboot.
bootswap refuses to overwrite a recorded stock backup if the partition differs from it
(`boot-swap.rebackup` overrides) and is a no-op if the new image is already in place.

Caveat: emit text shares ttyGS0 with Protocol A; the host FrameParser resyncs, but a marker
interleaved mid-frame can drop one frame/ack.

## Offline tests (box, device busybox 1.27.2 in qemu-arm chroot, loop "boot" partition)
dry-run OK; real write + readback OK (reboot stubbed); already-applied no-op; clobber refusal;
job rc capture; bad-sha job → job.sh.bad; ping; second stub = no duplicate; kill switch.
Fixed during test: lock fd leaked to child sleeps/jobs (blocked restart ≤300 s) → `9>&-`;
timeout now kills the job process itself; stub copies to a unique /tmp name.

## Real swap still BLOCKED: LCD drivers
Built kernel (MangoPi tina-5.4 tree) lacks Waveshare private fbtft drivers
CONFIG_FB_TFT_GC9107/GC9307/NV3007 (19 gc9107 key LCDs + nv3007 strip). Stock DTB is in
BOOTPKG (not boot.img) so a boot swap keeps it; audio/MMC/USB/UART compatibles all match.
Next: reconstruct fb_gc9107/fb_nv3007 from stock Image, rebuild, restage boot-new.img.
