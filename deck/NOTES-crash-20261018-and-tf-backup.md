# MK20 hang 2026-10-18 ~12:52 SGT: root cause, recovery, TF backup

## Root cause (agent error)
- The mk20-add-adb.sh v2 helper ran in the FOREGROUND through `adb shell`. Its "already composite" check
  failed (the ep1/UDC condition), so it ran `killall adbd`. That killed the adb session running it. The
  script died after unbinding the UDC and before rebinding, so the T113 dropped off USB.
- KeyboardDevice (qt_app2) lost ttyGS0 and stalled, leaving the screens backlit but black. The GD32 was
  unaffected. A replug (power cycle) recovered it on the stock kernel.
- Fix: the v2.1 helper redirects adbd's stdio (`</dev/null >/tmp/adbd.log`) and uses
  `adbd_up = pidof || pgrep`. It must never run in the foreground from adb; use
  `setsid sh ... </dev/null >/dev/null 2>&1 &` or a watcher job.

## Secondary issue: boot race
- boot-every/adb.sh v2 ran 17 s after boot, before KD's `setusbconfig serial` had created g1
  ("FAIL no g1"). The keepalive re-adds then failed ("adbd died"), which made ACM blip.
- v2.2 waits up to 120 s for g1 to be ready (UDC bound + acm.usb0 linked + /dev/ttyGS0 + KeyboardDevice),
  then waits another 5 s. If g1 never becomes ready, it skips and changes nothing.
- The v2.1 helper ran as a watcher job at uptime 142 s: OK, ACM + ffs.adb, adbd running, KD getInfo
  V2.32 OK.

## TF backup (via adb pull of block devices; device sha == local sha)
| part | size | sha256 |
|---|---|---|
| disk head (sectors 0..41463: boot0/uboot/GPT) | 21229568 | fc5120bef68599d3dab3ca18210d535f9b27f6071e42e9917c7bdc7d60d79da4 |
| p1 boot-resource/SDCARD | 33554432 | 171cba89751ab053e4ab8396840ecb058c90c3c3f450a86b416131d0477c1252 |
| p2 env | 258048 | be8b60fed84878474c09394985a2d5e2cd18d8536dca853a38bad74560fd504b |
| p3 env-redund | 258048 | be8b60fed84878474c09394985a2d5e2cd18d8536dca853a38bad74560fd504b |
| p4 boot (#221) | 6451200 | 9ac68ed98a70a3b5211dc8a25cf9427afdc54543ebfe003337c77032929029a8 |
| p5 rootfs | 1073741824 | d1d8c96b25f995b6d5b5bd9b5adcdcf824faced1464b4bccd348e7f869b9a2e1 |
- Not backed up: p6 UDISK (2.7 GiB, rw overlay).
- Copies: Mac ~/mk20-backup/tf/live-221-parts/ and box /workspace/mk20-backup/tf/.

## 13:17 reboot test (mk20ctl reboot, stock #221, boot partition untouched)
- The watcher accepted cmd-reboot and the T113 dropped off USB at 13:17:09 SGT. Neither the T113
  (ACM/adb) nor anything else from the MK20 re-enumerated within 10+ min, so the soft `reboot` did not
  bring the unit back. A replug (power cycle) recovered it at 12:5x. The suspicion is that a soft
  reboot hangs in shutdown or in the BROM/SPL path on this board; to be confirmed after the next
  replug (see logs/watcher.log, every-adb.log).
- Consequence: bootswap.sh ends with `reboot`. Any swap test must expect that a human power cycle is
  needed after the write, so the "auto-restore on failure" path can't rely on a soft reboot.

## Independent verification of boot-221-ep3int.img (verify-staged.py, separate from mkboot221.py)
- sha 38703734a1c78407b5db77e05d3f0961a5561d4b0970ee0077d55afbc5bc1ece, 5433344 B (prefix of a 6451200 B partition)
- Header page identical to stock (ks 5429672, page 2048, ramdisk 12 B "ramdisk.img\n" beyond the prefix, untouched).
- zImage decompressor prefix (15884 B) and tail identical. piggy length identical (5413726).
- Decompressed Image diffs: exactly 0xd326d8 0x32->0x38, 0xd326f5 0x01->0x03.
- Expected full-partition sha after the write: f2a19d3cb8fddc7f177cb518bb64e69645fadd3ce1b1422c4f056db39e4ee4c0
