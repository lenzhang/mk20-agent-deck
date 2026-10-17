#!/bin/sh
# One-shot morning combo probe — SELF-DELETES before any USB change.
# Covers: dump prior status, try ACM+ADB add (keep serial), collect EP/gadget facts to SDCARD.
# On ANY failure → setusbconfig serial. 90-minute watchdog also restores serial.
# Next power-cycle is always stock (this file already gone).
LOG=/mnt/SDCARD/morning-combo.log
STATUS=/mnt/SDCARD/morning-combo-status.txt
ADD=/mnt/SDCARD/mk20-add-adb.sh
DONEFLAG=/mnt/SDCARD/morning-combo.done
rm -f "$0"  # one-shot FIRST
exec >>"$LOG" 2>&1
echo "BEGIN $(date) pid=$$"
echo "phase=start $(date)" > "$STATUS"
# Dump prior M2 leftovers if readable
for f in /mnt/SDCARD/m2-adb-status.txt /mnt/SDCARD/m2test-status.txt /mnt/SDCARD/mk20-gadget.log; do
  [ -f "$f" ] && { echo "==== $f ===="; cat "$f"; }
done
# Wait for KeyboardDevice + ttyGS0 (serial already up)
i=0
while [ $i -lt 60 ]; do
  pidof KeyboardDevice >/dev/null 2>&1 && [ -e /dev/ttyGS0 ] && break
  i=$((i+1)); sleep 1
done
echo "waited=${i}s kd=$(pidof KeyboardDevice) ttyGS0=$([ -e /dev/ttyGS0 ] && echo y || echo n)"
echo "phase=kd_ready waited=$i $(date)" >> "$STATUS"
# Snapshot before
echo "==== before gadget ===="
ls /sys/kernel/config/usb_gadget/g1/configs/c.1/ 2>/dev/null
ls /sys/class/udc 2>/dev/null
cat /sys/devices/platform/soc@3000000/soc@3000000:usbc0@0/usb_device 2>/dev/null
# Prefer helper; else inline ACM+ADB
ok=0
if [ -x "$ADD" ]; then
  sh "$ADD" && ok=1
else
  # inline: add ffs.adb beside acm without tearing ACM
  mkdir -p /sys/kernel/config/usb_gadget/g1/functions/ffs.adb 2>/dev/null
  mkdir -p /dev/usb-ffs/adb
  mount -o uid=2000,gid=2000 -t functionfs adb /dev/usb-ffs/adb/ 2>/dev/null || true
  ln -sf /sys/kernel/config/usb_gadget/g1/functions/ffs.adb /sys/kernel/config/usb_gadget/g1/configs/c.1/ffs.adb 2>/dev/null
  # rebind UDC
  echo "" > /sys/kernel/config/usb_gadget/g1/UDC 2>/dev/null
  sleep 0.3
  for u in /sys/class/udc/*; do [ -e "$u" ] && basename "$u" > /sys/kernel/config/usb_gadget/g1/UDC && break; done
  adbd &
  sleep 1
  pidof adbd >/dev/null && ok=1
fi
echo "==== after gadget ok=$ok ===="
ls /sys/kernel/config/usb_gadget/g1/configs/c.1/ 2>/dev/null
ls /dev/usb-ffs/adb 2>/dev/null
pidof adbd; cat /sys/devices/platform/soc@3000000/soc@3000000:usbc0@0/usb_device 2>/dev/null
if [ "$ok" != 1 ]; then
  echo "FAIL → serial restore"
  echo "phase=fail_restore $(date)" >> "$STATUS"
  /data/setusbconfig serial
  killall adbd 2>/dev/null
  touch "$DONEFLAG"
  exit 1
fi
echo "phase=adb_up $(date)" >> "$STATUS"
touch "$DONEFLAG"
# Watchdog restore
(
  sleep 5400
  echo "watchdog restore serial $(date)" >> "$LOG"
  /data/setusbconfig serial
  killall adbd 2>/dev/null
) &
echo "END $(date)"
