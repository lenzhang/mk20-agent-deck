#!/bin/sh
# M2 one-shot ACM+adb TEST — self-diagnosing, self-recovering.
# No persistent setusbconfig wrapper (reboot-safe). Relies on stopping qt_app2/KD
# before UDC surgery so nothing calls setusbconfig serial during the window.
#
# Safety:
#  * Deletes itself FIRST → next power-cycle has no lunch.sh → stock KD serial
#  * qt_app2 stop is not disable → reboot restarts UI
#  * Fail / 10m watchdog: setusbconfig serial, start qt_app2, dump diag to ttyGS0
#  * Never touches /rom, flash, swupdate, ESP32
LOG=/mnt/SDCARD/mk20-gadget.log
STAMP=/mnt/SDCARD/m2test-status.txt
DUMP=/mnt/SDCARD/m2-diag-dump.txt

echo "m2test boot $(date 2>/dev/null || echo nodate)" > "$STAMP"
echo "=== m2test $(date 2>/dev/null || echo nodate) ===" > "$LOG"

# Heal any leftover wrapper from earlier overnight experiments
if [ -f /data/setusbconfig.real ]; then
  echo "healing leftover setusbconfig.real" >> "$LOG"
  rm -f /data/setusbconfig /mnt/SDCARD/m2-window.flag
  mv /data/setusbconfig.real /data/setusbconfig
  chmod +x /data/setusbconfig
fi

# ---- ONE-SHOT DISARM (before any USB change) ----
cp -f /mnt/SDCARD/lunch.sh /mnt/SDCARD/lunch.sh.m2test.bak 2>/dev/null || true
rm -f /mnt/SDCARD/lunch.sh /data/lunch.sh /mnt/SDCARD/m2-window.flag
cp -f /mnt/SDCARD/mk20-add-adb.sh /data/mk20-add-adb.sh 2>/dev/null || true
chmod +x /data/mk20-add-adb.sh 2>/dev/null || true
{
  echo "==== prior m2-adb-status ===="
  cat /mnt/SDCARD/m2-adb-status.txt 2>/dev/null || echo "(none)"
} > "$DUMP" 2>/dev/null
sync
echo "disarmed; helper staged; no-wrapper design" >> "$STAMP"

restore_serial() {
  echo "restore_serial $(date 2>/dev/null || echo nodate)" >> "$LOG"
  # heal wrapper if any
  if [ -f /data/setusbconfig.real ]; then
    rm -f /data/setusbconfig
    mv /data/setusbconfig.real /data/setusbconfig
    chmod +x /data/setusbconfig
  fi
  killall adbd 2>/dev/null || true
  [ -x /data/setusbconfig ] && /data/setusbconfig serial >> "$LOG" 2>&1
  sync
}

dump_diag_to_acm() {
  {
    echo ""
    echo "===== MK20 M2 DIAG BEGIN $(date 2>/dev/null || echo nodate) ====="
    echo "---- status ----"; cat "$STAMP" 2>/dev/null
    echo "---- gadget.log (tail) ----"; tail -n 80 "$LOG" 2>/dev/null
    echo "---- dmesg tail ----"; dmesg 2>/dev/null | tail -n 60
    echo "---- configs ----"; ls /sys/kernel/config/usb_gadget/g1/configs/c.1/ 2>/dev/null
    echo "UDC=$(cat /sys/kernel/config/usb_gadget/g1/UDC 2>/dev/null)"
    echo "adbd=$(pgrep -x adbd)"; echo "KD=$(pgrep -f KeyboardDevice)"
    echo "setusbconfig.real=$([ -f /data/setusbconfig.real ] && echo YES || echo no)"
    echo "usb_device=$(cat /sys/devices/platform/soc@3000000/soc@3000000:usbc0@0/usb_device 2>/dev/null)"
    echo "ffs=$(ls /dev/usb-ffs/adb 2>/dev/null | tr '\n' ' ')"
    echo "===== MK20 M2 DIAG END ====="; echo ""
  } > "$DUMP"
  cat "$DUMP" >> "$LOG"
  j=0
  while [ "$j" -lt 30 ]; do [ -e /dev/ttyGS0 ] && break; j=$((j+1)); sleep 0.5; done
  if [ -e /dev/ttyGS0 ]; then
    cat "$DUMP" > /dev/ttyGS0 2>/dev/null || true
    echo "dumped_to_ttyGS0=yes" >> "$STAMP"
  else
    echo "dumped_to_ttyGS0=no" >> "$STAMP"
  fi
  sync
}

(
  i=0
  while ! pgrep -f KeyboardDevice >/dev/null 2>&1; do
    i=$((i+1)); [ "$i" -gt 90 ] && break; sleep 1
  done
  echo "KeyboardDevice seen i=$i $(date 2>/dev/null || echo nodate)" >> "$LOG"
  sleep 5

  # Stop supervisor BEFORE UDC surgery (prevents setusbconfig serial race)
  if [ -x /etc/init.d/qt_app2 ]; then
    /etc/init.d/qt_app2 stop >> "$LOG" 2>&1 || true
  fi
  killall KeyboardDevice 2>/dev/null || true
  killall appLunch.sh 2>/dev/null || true
  sleep 1
  # Verify KD actually gone
  if pgrep -f KeyboardDevice >/dev/null 2>&1; then
    echo "WARN KD still alive after stop; kill -9" >> "$LOG"
    killall -9 KeyboardDevice 2>/dev/null || true
    sleep 1
  fi
  echo "phase=kd_stopped remaining=$(pgrep -f KeyboardDevice)" >> "$STAMP"

  if [ ! -x /data/mk20-add-adb.sh ]; then
    echo "RESULT=no-helper" >> "$STAMP"
    restore_serial
    [ -x /etc/init.d/qt_app2 ] && /etc/init.d/qt_app2 start >> "$LOG" 2>&1 || true
    dump_diag_to_acm
    exit 0
  fi

  export LOG
  if /data/mk20-add-adb.sh; then
    echo "RESULT=composite-ok (acm+adb); qt_app2 STOPPED" >> "$STAMP"
    echo "composite ok $(date 2>/dev/null || echo nodate)" >> "$LOG"
    [ -e /dev/ttyGS0 ] && echo "MK20_COMPOSITE_OK acm+adb $(date 2>/dev/null)" > /dev/ttyGS0 2>/dev/null || true
    (
      sleep 600
      echo "watchdog 10m fire" >> "$LOG"
      restore_serial
      [ -x /etc/init.d/qt_app2 ] && /etc/init.d/qt_app2 start >> "$LOG" 2>&1 || true
      dump_diag_to_acm
    ) &
    sync
    exit 0
  fi

  echo "RESULT=composite-fail -> restore+dump" >> "$STAMP"
  restore_serial
  [ -x /etc/init.d/qt_app2 ] && /etc/init.d/qt_app2 start >> "$LOG" 2>&1 || true
  sleep 2
  dump_diag_to_acm
  sync
) &

sync
exit 0
