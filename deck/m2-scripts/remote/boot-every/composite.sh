#!/bin/sh
# v1 ECM+HID+ADB composite (+KD via PTY<->TCP bridge) at every boot. Run by the watcher after adb.sh.
# Kill switch: /mnt/SDCARD/mk20-no-composite  (then adb.sh gives the old ACM+KD+adb).
# Failsafe: gadget-ecm-hid-adb.sh --boot rolls back to ACM+KD+adb on any failure or if the link flaps.
S=/mnt/SDCARD; GS=/data/mk20/gadget-ecm-hid-adb.sh
[ -f $S/mk20-no-composite ] && { echo "mk20-no-composite set; skip"; exit 0; }
[ -f $GS ] && [ -f $GS.sha256 ] || { echo "no gadget script"; exit 1; }
[ "$(sha256sum $GS | cut -d' ' -f1)" = "$(cut -d' ' -f1 $GS.sha256)" ] || { echo "gadget script sha mismatch; skip"; exit 1; }
G=/sys/kernel/config/usb_gadget/g1; i=0
until [ -n "$(cat $G/UDC 2>/dev/null)" ] && [ -e $G/configs/c.1/acm.usb0 ] && [ -e /dev/ttyGS0 ] && pgrep -f KeyboardDevice >/dev/null; do
  i=$((i+1)); [ $i -gt 120 ] && { echo "g1 never configured by KD; skip composite (adb.sh fallback not run!)"; cp -f $S/mk20/mk20-add-adb.sh /tmp/mk20-add-adb.sh 2>/dev/null && sh /tmp/mk20-add-adb.sh; exit 0; }; sleep 1
done
echo "g1 ready after ${i}s; letting KD finish its startup USB reset"; sleep 20
cp -f $GS /tmp/mk20-gadget-boot.sh
sh /tmp/mk20-gadget-boot.sh --boot; rc=$?
echo "gadget --boot rc=$rc status=$(cat $S/gadget-ecm-hid.status 2>/dev/null)"
tail -n 6 $S/mk20/logs/gadget-ecm-hid.log
