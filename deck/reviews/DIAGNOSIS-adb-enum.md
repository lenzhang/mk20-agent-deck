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
- Stock `setusbconfig adb` binds UDC at end of script **before** init.d starts
  adbd (inverted). Stock path relies on adbd `restart_usb_service` recovery.
  Our helper does the correct order and waits for `ep1`/`ep2`.

## Why ACM returned at ~02:47 (most likely)

Prior hook unbinds UDC to add `ffs.adb` → **ttyGS0 disappears** while KD still
runs under **qt_app2 respawn**. KD death/exit → respawn → `setusbconfig serial`
→ composite torn down to ACM-only. Mac sees brief ACM loss then ACM back; adb
never stable. Watchdog (3h) was too long to be the 02:47 cause; supervisor race
fits the timeline.

Secondary risks: ffs mount fail; adbd exit before bind; EP OK for ACM+ADB but
Mac composite quirk (less likely than KD race).

## Fix in lunch-m2test.sh / mk20-add-adb.sh (2026-10-18 03:20+)

- Stop `qt_app2` + kill KD **before** UDC surgery.
- Temporary `/data/setusbconfig` wrapper **blocks `serial`** during the window.
- Wait for ffs `ep1`/`ep2` before bind.
- On failure (or 90m watchdog): restore serial, restore real setusbconfig,
  restart qt_app2, **dump status+log+dmesg to SDCARD and `/dev/ttyGS0`**.
