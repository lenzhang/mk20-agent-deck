#!/bin/sh
# ECM bind test on the patched kernel: add ecm.usb0 to KD's g1 (ACM + ADB stay).
# EP plan: ACM bulk ep1 + notify ep3(INT, patched) | ADB bulk ep2 | ECM bulk ep5 pair + notify ep4.
# Rollback: on any failure, or if /mnt/SDCARD/mk20-ecm-keep is not created within 180 s, remove ECM and
# restore ACM+ADB. Always detached (no setsid on this busybox).
S=/mnt/SDCARD; L=$S/mk20/logs/ecm-test.log; G=/sys/kernel/config/usb_gadget/g1; C=$G/configs/c.1
# Launch: adbd kills the whole process group of an adb shell when it ends (trap HUP does not help) and
# this busybox has no setsid, so we hand ourselves to the watcher job queue (runs outside adb/ACM sessions).
if [ "${1:-}" != --run ]; then
  Q=/mnt/SDCARD/job.sh; [ -e $Q ] && { echo "watcher queue busy ($Q exists)"; exit 1; }
  echo "sh $(readlink -f "$0") --run" > $Q.tmp
  sha256sum $Q.tmp | awk '{print $1"  job.sh"}' > $Q.sha256.tmp; mv -f $Q.tmp $Q; mv -f $Q.sha256.tmp $Q.sha256
  echo "queued to watcher"; exit 0
fi
trap '' HUP
log() { echo "ecm $(cut -d' ' -f1 /proc/uptime): $*" >> $L; }
udc() { cat $G/UDC 2>/dev/null; }
UDC=$(ls /sys/class/udc | head -1)
rebind() { i=0; while [ -n "$(udc)" ] && [ $i -lt 10 ]; do echo "" > $G/UDC 2>/dev/null; sleep 0.3; i=$((i+1)); done; }
restore() {
  log "ROLLBACK: $*"
  rebind; rm -f $C/ecm.usb0; rmdir $G/functions/ecm.usb0 2>/dev/null
  echo "$UDC" > $G/UDC 2>>$L; sleep 2
  if [ -z "$(udc)" ] || [ ! -e $C/acm.usb0 ]; then log "g1 broken -> setusbconfig serial"; killall adbd 2>/dev/null; /data/setusbconfig serial >>$L 2>&1; fi
  pidof adbd >/dev/null && [ -e $C/ffs.adb ] || sh $S/mk20/mk20-add-adb.sh
  log "after rollback UDC=$(udc) links=$(ls $C | tr '\n' ' ') kd=$(pidof KeyboardDevice) adbd=$(pidof adbd)"
}
log "BEGIN uname=$(uname -v) links=$(ls $C | tr '\n' ' ') udc=$(udc)"
[ -e $C/acm.usb0 ] && [ -n "$(udc)" ] || { log "ABORT g1 not in ACM state"; exit 1; }
grep -q '^usb_f_ecm ' /proc/modules || insmod /data/mk20/usb_f_ecm.ko 2>>$L || { log "ABORT insmod failed"; exit 1; }
log "module: $(grep usb_f_ecm /proc/modules)"
mkdir -p $G/functions/ecm.usb0 || { log "ABORT mkdir ecm"; exit 1; }
echo 02:11:22:33:44:55 > $G/functions/ecm.usb0/dev_addr; echo 02:11:22:33:44:56 > $G/functions/ecm.usb0/host_addr
rebind; [ -z "$(udc)" ] || { restore "unbind failed"; exit 1; }
ln -s $G/functions/ecm.usb0 $C/ecm.usb0 || { restore "link"; exit 1; }
# ffs.adb has descriptors from the running adbd; adbd may also rebind itself
sleep 1; [ -n "$(udc)" ] || echo "$UDC" > $G/UDC 2>>$L
sleep 2
[ -n "$(udc)" ] || { restore "bind failed: $(dmesg | tail -5 | tr '\n' '|')"; exit 1; }
log "bound links=$(ls $C | tr '\n' ' ') ifname=$(cat $G/functions/ecm.usb0/ifname 2>/dev/null)"
N=$(cat $G/functions/ecm.usb0/ifname 2>/dev/null); [ -n "$N" ] || N=usb0
ifconfig $N 169.254.7.2 netmask 255.255.0.0 up 2>>$L
log "net: $(ifconfig $N 2>&1 | head -2 | tr '\n' ' ')"
pidof adbd >/dev/null || log "adbd gone (will be restored at rollback/keep)"
dmesg | grep -i -E 'udc|ecm|gadget' | tail -8 >> $L
# keep window
i=0; while [ $i -lt 90 ]; do [ -f $S/mk20-ecm-keep ] && { log "KEEP requested; leaving ECM up"; exit 0; }; sleep 2; i=$((i+1)); done
restore "keep window expired (expected for a test)"
