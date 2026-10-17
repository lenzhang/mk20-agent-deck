# Why M2 adb never enumerated (static diagnosis)

## Boot order (rootfs)

1. `S80adbd` may start early, then `S99qt_app1` runs `/mnt/SDCARD/lunch.sh`, then
   **`adbd stop/disable` + `killall adbd`**, then `S99qt_app2` procd-runs
   `appLunch.sh` → `KeyboardDevice` with `respawn`.
2. KD `updateUSBtype()` → `system("/data/setusbconfig serial")` → ACM only,
   opens `/dev/ttyGS0`.

## FunctionFS requirements (Tina `init.d/adbd` + `adbd` strings)

- Mount `functionfs` at `/dev/usb-ffs/adb`.
- Start `/bin/adbd -D` so it opens `/dev/usb-ffs/adb/ep0` and writes descriptors
  (`init_functionfs`; then `ep1`/`ep2` appear).
- **Only then** bind UDC (`echo $udc > .../UDC`).
- Stock `setusbconfig adb` binds UDC before adbd (inverted; relies on
  `restart_usb_service`). Our helper waits for `ep1`/`ep2`.

## Why ACM returned at ~02:47 (most likely)

Prior hook unbound UDC → **ttyGS0 gone** while KD under **qt_app2 respawn** →
fresh KD → `setusbconfig serial` → ACM-only. Watchdog (3h) too long for 02:47.

## Fix (final overnight, after Cursor review)

- Stop `qt_app2` + KD **before** UDC surgery; verify KD gone (`kill -9` fallback).
- **No persistent setusbconfig wrapper** (review blocker: wrapper survives reboot
  and blocks serial). Heals any leftover `.real` from earlier experiments.
- Harden `mk20-add-adb.sh`: require UDC actually empty after unbind; require
  `ffs.adb` link; require ep1/ep2; require UDC rebound equals expected name;
  require adbd still alive — no false success.
- On fail / 90m watchdog: `setusbconfig serial`, start qt_app2, dump diag to
  SDCARD + `/dev/ttyGS0`.
- One-shot self-delete before USB change; next power-cycle = stock serial.
