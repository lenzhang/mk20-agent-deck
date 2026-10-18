#!/bin/sh
# HID isolation test: add hid.usb0 to KD's g1 (ACM + ADB stay, NO ECM), hold, capture kmsg, ALWAYS roll back.
# EP plan: ACM bulk ep1 + notify ep3(INT) | ADB bulk ep2 | HID IN ep4(INT) + OUT ep5out(INT, patched).
# Variant: /mnt/SDCARD/mk20-hidtest-variant = full | noled | kbd | kbdnoled | mouse   (default full)
# KD stopped during test if /mnt/SDCARD/mk20-hidtest-nokd exists (ACM stays linked).
# Hold seconds: /mnt/SDCARD/mk20-hidtest-hold (default 45). Queued via watcher (adbd kills adb session pgrp).
S=/mnt/SDCARD; L=$S/mk20/logs/hid-test.log; G=/sys/kernel/config/usb_gadget/g1; C=$G/configs/c.1
if [ "${1:-}" != --run ]; then
  Q=/mnt/SDCARD/job.sh; [ -e $Q ] && { echo "watcher queue busy ($Q exists)"; exit 1; }
  echo "sh $(readlink -f "$0") --run" > $Q.tmp
  sha256sum $Q.tmp | awk '{print $1"  job.sh"}' > $Q.sha256.tmp; mv -f $Q.tmp $Q; mv -f $Q.sha256.tmp $Q.sha256
  echo "queued to watcher"; exit 0
fi
trap '' HUP
V=$(cat $S/mk20-hidtest-variant 2>/dev/null); V=${V:-full}
HOLD=$(cat $S/mk20-hidtest-hold 2>/dev/null); HOLD=${HOLD:-45}
K=$S/mk20/logs/kmsg-hid-$V-$(cut -d. -f1 /proc/uptime).txt
log() { echo "hid[$V] $(cut -d' ' -f1 /proc/uptime): $*" >> $L; }
udc() { cat $G/UDC 2>/dev/null; }
UDC=$(ls /sys/class/udc | head -1)
unbind() { i=0; while [ -n "$(udc)" ] && [ $i -lt 10 ]; do echo "" > $G/UDC 2>/dev/null; sleep 0.3; i=$((i+1)); done; }
KP=
restore() {
  log "ROLLBACK: $*"
  unbind; rm -f $C/hid.usb0; rmdir $G/functions/hid.usb0 2>/dev/null
  echo "$UDC" > $G/UDC 2>>$L; sleep 2
  if [ -z "$(udc)" ] || [ ! -e $C/acm.usb0 ]; then log "g1 broken -> setusbconfig serial"; killall adbd 2>/dev/null; /data/setusbconfig serial >>$L 2>&1; fi
  pidof adbd >/dev/null && [ -e $C/ffs.adb ] || sh $S/mk20/mk20-add-adb.sh
  [ "$NOKD" = 1 ] && { /etc/init.d/qt_app2 start >>$L 2>&1; sleep 3; log "KD restarted: $(pidof KeyboardDevice)"; }
  if [ "$SHIM" = 1 ]; then /etc/init.d/qt_app2 stop >>$L 2>&1; killall KeyboardDevice 2>/dev/null; sleep 1; umount /data/appLunch.sh; /etc/init.d/qt_app2 start >>$L 2>&1; sleep 3; log "KD restarted without shim: $(pidof KeyboardDevice)"; fi
  sleep 3; [ -n "$KP" ] && kill $KP 2>/dev/null
  log "after rollback UDC=$(udc) links=$(ls $C | tr '\n' ' ') kd=$(pidof KeyboardDevice) adbd=$(pidof adbd)"
  log "kmsg: $(grep -c rmmod_device_driver $K) rmmod, $(grep -c 'USB_STATE=DISCONNECTED' $K) DISCONNECTED, $(wc -l < $K) lines -> $K"
}
KB='\x05\x01\x09\x06\xa1\x01\x85\x01\x05\x07\x19\xe0\x29\xe7\x15\x00\x25\x01\x75\x01\x95\x08\x81\x02\x95\x01\x75\x08\x81\x03'
LED='\x95\x05\x75\x01\x05\x08\x19\x01\x29\x05\x91\x02\x95\x01\x75\x03\x91\x03'
KE='\x95\x06\x75\x08\x15\x00\x25\x65\x05\x07\x19\x00\x29\x65\x81\x00\xc0'
MS='\x05\x01\x09\x02\xa1\x01\x85\x02\x09\x01\xa1\x00\x05\x09\x19\x01\x29\x03\x15\x00\x25\x01\x95\x03\x75\x01\x81\x02\x95\x01\x75\x05\x81\x03\x05\x01\x09\x30\x09\x31\x16\x00\x00\x26\xff\x7f\x75\x10\x95\x02\x81\x02\xc0\xc0'
case $V in
  full) D="$KB$LED$KE$MS";; noled) D="$KB$KE$MS";; kbd) D="$KB$LED$KE";; kbdnoled) D="$KB$KE";; mouse) D="$MS";;
  *) log "ABORT unknown variant"; exit 1;;
esac
log "BEGIN uname=$(uname -v) links=$(ls $C | tr '\n' ' ') udc=$(udc)"
[ -e $C/acm.usb0 ] && [ -n "$(udc)" ] || { log "ABORT g1 not in ACM state"; exit 1; }
NOKD=0; [ -f $S/mk20-hidtest-nokd ] && NOKD=1
if [ $NOKD = 1 ]; then /etc/init.d/qt_app2 stop >>$L 2>&1; killall KeyboardDevice 2>/dev/null; sleep 1; log "KD stopped for test: $(pidof KeyboardDevice)"; fi
SHIM=0; [ -f $S/mk20-hidtest-shim ] && SHIM=1
if [ $SHIM = 1 ]; then   # KD polls `dmesg -c` every 3 s and resets USB on "ERR: Operation not supported"
  mkdir -p /tmp/mk20-shim; printf '#!/bin/sh\n/bin/busybox dmesg "$@" | /bin/busybox grep -v "ERR: Operation not supported"\n' > /tmp/mk20-shim/dmesg; chmod 755 /tmp/mk20-shim/dmesg
  sed 's#^source /data/qt_env.sh#source /data/qt_env.sh; export PATH=/tmp/mk20-shim:$PATH#' /data/appLunch.sh > /tmp/appLunch-shim.sh; chmod 755 /tmp/appLunch-shim.sh
  grep -q mk20-shim /tmp/appLunch-shim.sh || { log "ABORT shim sed"; exit 1; }
  /etc/init.d/qt_app2 stop >>$L 2>&1; killall KeyboardDevice 2>/dev/null; sleep 1
  mount --bind /tmp/appLunch-shim.sh /data/appLunch.sh; /etc/init.d/qt_app2 start >>$L 2>&1; sleep 3
  log "KD restarted with dmesg shim: pid=$(pidof KeyboardDevice) PATH=$(tr '\0' '\n' < /proc/$(pidof KeyboardDevice | cut -d' ' -f1)/environ | grep ^PATH=)"
fi
( trap '' HUP INT; exec cat /proc/kmsg ) > $K 2>&1 </dev/null & KP=$!
mkdir -p $G/functions/hid.usb0 || { restore "mkdir hid"; exit 1; }
echo 1 > $G/functions/hid.usb0/protocol; echo 1 > $G/functions/hid.usb0/subclass
echo 9 > $G/functions/hid.usb0/report_length
printf "$D" > /tmp/hid-$V.desc; cat /tmp/hid-$V.desc > $G/functions/hid.usb0/report_desc || { restore "report_desc"; exit 1; }
log "desc bytes=$(wc -c < /tmp/hid-$V.desc)"
unbind; [ -z "$(udc)" ] || { restore "unbind failed"; exit 1; }
ln -s $G/functions/hid.usb0 $C/hid.usb0 || { restore "link"; exit 1; }
sleep 1; [ -n "$(udc)" ] || echo "$UDC" > $G/UDC 2>>$L
sleep 2
[ -n "$(udc)" ] || { restore "bind failed"; exit 1; }
log "bound links=$(ls $C | tr '\n' ' ') hidg=$(ls /dev/hidg* 2>/dev/null)"
sleep $HOLD
log "hold done: $(grep -c rmmod_device_driver $K) rmmod during test so far"
restore "end of hold (test)"
