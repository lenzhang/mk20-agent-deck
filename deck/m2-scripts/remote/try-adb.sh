#!/bin/sh
# Non-fatal: ACM+ADB composite via proven helper (stops KD; 10m watchdog restores serial+KD).
[ -f /mnt/SDCARD/mk20-no-adb ] && { echo "mk20-no-adb set; skip"; exit 0; }
cp -f /mnt/SDCARD/mk20/mk20-add-adb.sh /data/mk20-add-adb.sh && chmod +x /data/mk20-add-adb.sh
restore() { killall adbd 2>/dev/null; [ -x /data/setusbconfig ] && /data/setusbconfig serial; [ -x /etc/init.d/qt_app2 ] && /etc/init.d/qt_app2 start; }
/etc/init.d/qt_app2 stop 2>/dev/null; killall KeyboardDevice appLunch.sh 2>/dev/null; sleep 1
pgrep -f KeyboardDevice >/dev/null && killall -9 KeyboardDevice
export LOG=/mnt/SDCARD/mk20/logs/adb-helper.log
if /data/mk20-add-adb.sh; then
  echo "COMPOSITE OK acm+adb; KD stopped; watchdog restores in 600s"
  ( sleep 600; restore ) </dev/null >/dev/null 2>&1 &
else
  echo "composite FAIL rc=$? -> restore"; restore
fi
tail -n 30 $LOG
