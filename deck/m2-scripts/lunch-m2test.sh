#!/bin/sh
# M2 one-shot ACM+adb composite TEST (self-recovering).
# Safety model:
#  * Deletes itself FIRST -> the NEXT power-cycle is guaranteed clean plain serial.
#  * If the composite fails to rebind, immediately restores plain serial.
#  * A long watchdog (3h) restores plain serial if nobody acts, so the device
#    never stays in test mode forever.
#  * Never touches /rom, flash, swupdate, or ESP32.
LOG=/mnt/SDCARD/mk20-gadget.log
STAMP=/mnt/SDCARD/m2test-status.txt
echo "m2test boot $(date 2>/dev/null || echo nodate)" > "$STAMP"
echo "=== m2test $(date 2>/dev/null || echo nodate) ===" > "$LOG"

# ---- ONE-SHOT DISARM (before any USB change) ----
cp -f /mnt/SDCARD/lunch.sh /mnt/SDCARD/lunch.sh.m2test.bak 2>/dev/null || true
rm -f /mnt/SDCARD/lunch.sh
rm -f /data/lunch.sh
# stage helper on writable overlay
cp -f /mnt/SDCARD/mk20-add-adb.sh /data/mk20-add-adb.sh 2>/dev/null || true
chmod +x /data/mk20-add-adb.sh 2>/dev/null || true
sync
echo "disarmed; helper staged" >> "$STAMP"

restore_serial() {
  echo "restore_serial $(date 2>/dev/null || echo nodate)" >> "$LOG"
  killall adbd 2>/dev/null || true
  [ -x /data/setusbconfig ] && /data/setusbconfig serial >> "$LOG" 2>&1
  sync
}

(
  # wait for KeyboardDevice (default serial gadget up), then settle
  i=0
  while ! pgrep -f KeyboardDevice >/dev/null 2>&1; do
    i=$((i + 1)); [ "$i" -gt 90 ] && break; sleep 1
  done
  echo "KeyboardDevice seen i=$i $(date 2>/dev/null || echo nodate)" >> "$LOG"
  sleep 8

  if [ ! -x /data/mk20-add-adb.sh ]; then
    echo "missing helper; leaving serial" >> "$LOG"
    echo "RESULT=no-helper (serial intact)" >> "$STAMP"
    exit 0
  fi

  export LOG
  if /data/mk20-add-adb.sh; then
    echo "RESULT=composite-ok (acm+adb)" >> "$STAMP"
    echo "composite ok $(date 2>/dev/null || echo nodate)" >> "$LOG"
  else
    echo "RESULT=composite-fail -> restoring serial" >> "$STAMP"
    restore_serial
    exit 0
  fi

  # Long watchdog: self-heal to plain serial after 3h if nobody persists it
  ( sleep 10800; echo "watchdog 3h fire" >> "$LOG"; restore_serial ) &
  sync
) &

sync
exit 0
