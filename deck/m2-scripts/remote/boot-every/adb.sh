#!/bin/sh
# v2.2 Persistent ACM+adb every boot (run by watcher after KD is up). Kill switch: /mnt/SDCARD/mk20-no-adb
S=/mnt/SDCARD; H=$S/mk20/mk20-add-adb.sh; export LOG=$S/mk20/logs/adb-helper.log
[ -f $S/mk20-no-adb ] && { echo "mk20-no-adb set; skip"; exit 0; }
[ -f $H ] || { echo "no helper"; exit 1; }
restore() { echo "restore serial"; killall adbd 2>/dev/null; [ -x /data/setusbconfig ] && /data/setusbconfig serial; }
cp -f $H /tmp/mk20-add-adb.sh
# v2.2: boot race fix - wait (<=120s) until KD has configured g1 (UDC bound, acm linked, ttyGS0 present)
G=/sys/kernel/config/usb_gadget/g1; i=0
until [ -n "$(cat $G/UDC 2>/dev/null)" ] && [ -e $G/configs/c.1/acm.usb0 ] && [ -e /dev/ttyGS0 ] && pgrep -f KeyboardDevice >/dev/null; do
  i=$((i+1)); [ $i -gt 120 ] && { echo "g1 never configured by KD; skip adb (no changes)"; exit 0; }; sleep 1
done
echo "g1 ready after ${i}s"; sleep 5
if sh /tmp/mk20-add-adb.sh; then echo "ACM+ADB OK"; else rc=$?; echo "add-adb FAIL rc=$rc"; restore; fi
sleep 2
# ACM must still be there for KD; otherwise back out adb
if [ ! -e /dev/ttyGS0 ] || [ ! -e /sys/kernel/config/usb_gadget/g1/configs/c.1/acm.usb0 ]; then echo "ACM missing -> restore"; restore; fi
# keepalive: if KD respawn resets gadget to serial-only, re-add adb (max 5 times/boot)
(
  exec 9>&-; n=0
  while [ $n -lt 5 ]; do
    sleep 30
    [ -f $S/mk20-no-adb ] && exit 0
    # only the serial-reset signature (ffs.adb unlinked by setusbconfig serial) triggers a re-add
    if [ ! -e /sys/kernel/config/usb_gadget/g1/configs/c.1/ffs.adb ]; then
      pgrep -f KeyboardDevice >/dev/null || continue
      sleep 10; n=$((n+1)); echo "keepalive re-add #$n" >> $LOG; sh /tmp/mk20-add-adb.sh || restore
    fi
  done
) </dev/null >/dev/null 2>&1 &
tail -n 8 $LOG
