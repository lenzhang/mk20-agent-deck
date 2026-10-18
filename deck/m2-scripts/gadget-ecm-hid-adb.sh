#!/bin/sh
# v3 Composite: ECM (macOS-native) + combined HID (kbd+abs pointer) + ADB (FFS), built IN g1
# (stock adbd binds g1/UDC itself, so we reuse g1 rather than a separate gadget).
# Requires the binary-patched STOCK kernel (ep3 ISO->INT + ep5out INT-capable, see
# NOTES-stock-kernel-patch.md) + /data/mk20/usb_f_ecm.ko. HID/FFS are built in.
#
# EP budget: f_hid (5.4) ALWAYS takes INT IN + INT OUT. ECM notify INT IN + HID IN/OUT = 3 INT:
#   ep3 (INT, patched), ep4 (INT), ep5out (bulk+INT, patched). Bulk: ep1 pair (ECM), ep2 pair (ADB).
#   ACM cannot also fit, so KD's ttyGS0 is replaced by a PTY<->TCP bridge (/data/ttygs_tcp_bridge).
#   Link order matters (configfs binds in link order): ECM, then HID, then ffs.adb.
#
# Safety (v3, per GPT-5.6 review 2026-10-18):
#  - adbd is started BEFORE the UDC is bound (FunctionFS needs descriptors first, else -ENODEV).
#  - HID report_length 9 (report ID 1 keyboard = 1 ID + 8 bytes; pointer = 1 + 5).
#  - EXIT trap: on ANY failure it unbinds, unlinks and removes our functions, restores stock serial
#    (/data/setusbconfig serial) and restarts KeyboardDevice.
#  - Stale links in g1/configs/c.1 are removed before reuse.
#  - KD is stopped via its procd service (no respawn race) and restarted at the end. setusbconfig is
#    shadowed by a tmpfs no-op bind mount (gone at reboot), so KD's restart does not wipe the composite.
#  - It must NEVER run inside an adb shell (it kills adbd; adbd kills the session group). It queues itself to the watcher.
set -u
LOG=/mnt/SDCARD/mk20/logs/gadget-ecm-hid.log
G=/sys/kernel/config/usb_gadget/g1
C=$G/configs/c.1
BR=/data/ttygs_tcp_bridge
ECMKO=/data/mk20/usb_f_ecm.ko
mkdir -p /mnt/SDCARD/mk20/logs
log() { echo "gadget $(cut -d' ' -f1 /proc/uptime): $*" >> "$LOG"; }

# Launch: adbd kills the whole process group of an adb shell when it ends (trap HUP does not help) and
# this busybox has no setsid, so we hand ourselves to the watcher job queue (runs outside adb/ACM sessions).
if [ "${1:-}" = --rollback ]; then set -- --rollback-queued; fi   # adb-safe: rollback also goes via the watcher
if [ "${1:-}" != --run ] && [ "${1:-}" != --rollback-now ]; then
  Q=/mnt/SDCARD/job.sh; [ -e $Q ] && { echo "watcher queue busy ($Q exists)"; exit 1; }
  A=--run; [ "${1:-}" = --rollback-queued ] && A=--rollback-now
  echo "sh $(readlink -f "$0") $A" > $Q.tmp
  sha256sum $Q.tmp | awk '{print $1"  job.sh"}' > $Q.sha256.tmp; mv -f $Q.tmp $Q; mv -f $Q.sha256.tmp $Q.sha256
  echo "queued to watcher"; exit 0
fi

KDSVC=/etc/init.d/qt_app2   # procd service (CMD=/data/appLunch.sh). NOT qt_app1 (plain sh, kills adbd)
[ -x $KDSVC ] || KDSVC=""
udc_now() { cat $G/UDC 2>/dev/null; }
kd_start() { if [ -n "$KDSVC" ]; then $KDSVC start >>"$LOG" 2>&1; fi; sleep 3
  pgrep -f KeyboardDevice >/dev/null || { [ -x /data/KeyboardDevice ] && ( trap '' HUP INT; exec /data/appLunch.sh </dev/null >/dev/null 2>&1 ) & }; }
OK=0
cleanup() {
  [ "$OK" = 1 ] && return
  log "FAILSAFE: undo composite"
  killall adbd 2>/dev/null
  echo "" > $G/UDC 2>/dev/null
  for l in $C/*; do [ -L "$l" ] && rm -f "$l"; done
  rmdir $G/functions/ecm.usb0 $G/functions/hid.usb0 2>/dev/null
  umount /data/setusbconfig 2>/dev/null
  touch /tmp/mk20-bridge-stop; [ -f /tmp/mk20-bridge-loop.pid ] && kill $(cat /tmp/mk20-bridge-loop.pid) 2>/dev/null; killall ttygs_tcp_bridge 2>/dev/null; sleep 0.5; [ -L /dev/ttyGS0 ] && rm -f /dev/ttyGS0; [ -e /dev/ttyGS0.real ] && [ ! -e /dev/ttyGS0 ] && mv /dev/ttyGS0.real /dev/ttyGS0
  # KD may hold the dead PTY: restart it cleanly on real ttyGS0
  [ -n "$KDSVC" ] && $KDSVC stop >>"$LOG" 2>&1; killall KeyboardDevice 2>/dev/null; sleep 1
  /data/setusbconfig serial >>"$LOG" 2>&1
  kd_start
  log "FAILSAFE done UDC=$(udc_now) acm=$([ -e $C/acm.usb0 ] && echo y || echo n) kd=$(pgrep -f KeyboardDevice | head -1)"
}
# rollback runs before any preflight so an abnormal state cannot block it
if [ "${1:-}" = --rollback-now ]; then log "rollback requested"; trap cleanup EXIT; exit 1; fi

# ---- preflight (nothing touched yet) ----
UDC=$(ls /sys/class/udc 2>/dev/null | head -1)
[ -n "$UDC" ] || { log "ABORT no udc"; exit 1; }
[ -d $G ] && [ -d $C ] || { log "ABORT no g1 (KD has not configured USB yet)"; exit 1; }
[ "${1:-}" = --rollback-now ] || [ -x $BR ] || { log "ABORT $BR missing: KD would lose ttyGS0 (black screens)"; exit 1; }
[ -x /bin/adbd ] || { log "ABORT no /bin/adbd"; exit 1; }
grep -q '^usb_f_ecm ' /proc/modules || [ -f $ECMKO ] || { log "ABORT no $ECMKO"; exit 1; }
log "BEGIN udc=$UDC kdsvc=${KDSVC:-none}"

trap cleanup EXIT
trap 'exit 1' INT TERM
trap '' HUP
die() { log "FAIL $*"; exit 1; }

# ---- stop KD (via procd so it is not respawned mid-change) and adbd ----
if [ -n "$KDSVC" ]; then $KDSVC stop >>"$LOG" 2>&1; fi
killall KeyboardDevice 2>/dev/null; killall adbd 2>/dev/null; sleep 2
pgrep -f /data/KeyboardDevice >/dev/null && die "KD still running/respawned after stop"
# ---- unbind + clean stale links ----
i=0; while [ -n "$(udc_now)" ] && [ $i -lt 10 ]; do echo "" > $G/UDC 2>/dev/null; sleep 0.3; i=$((i+1)); done
[ -z "$(udc_now)" ] || die "cannot unbind"
for l in $C/*; do [ -L "$l" ] && rm -f "$l"; done
# ---- functions ----
grep -q '^usb_f_ecm ' /proc/modules || insmod $ECMKO 2>>"$LOG" || die "insmod ecm"
mkdir -p $G/functions/ecm.usb0 || die "mkdir ecm"
echo "02:11:22:33:44:55" > $G/functions/ecm.usb0/dev_addr 2>/dev/null
echo "02:11:22:33:44:56" > $G/functions/ecm.usb0/host_addr 2>/dev/null
NOHID=0; [ -f /mnt/SDCARD/mk20-gadget-nohid ] && NOHID=1   # ECM+ADB+bridge only (works on the ep3-only kernel)
if [ $NOHID = 0 ]; then
mkdir -p $G/functions/hid.usb0 || die "mkdir hid"
echo 1 > $G/functions/hid.usb0/protocol; echo 1 > $G/functions/hid.usb0/subclass
echo 9 > $G/functions/hid.usb0/report_length || die "report_length"
printf '\x05\x01\x09\x06\xa1\x01\x85\x01\x05\x07\x19\xe0\x29\xe7\x15\x00\x25\x01\x75\x01\x95\x08\x81\x02\x95\x01\x75\x08\x81\x03\x95\x05\x75\x01\x05\x08\x19\x01\x29\x05\x91\x02\x95\x01\x75\x03\x91\x03\x95\x06\x75\x08\x15\x00\x25\x65\x05\x07\x19\x00\x29\x65\x81\x00\xc0\x05\x01\x09\x02\xa1\x01\x85\x02\x09\x01\xa1\x00\x05\x09\x19\x01\x29\x03\x15\x00\x25\x01\x95\x03\x75\x01\x81\x02\x95\x01\x75\x05\x81\x03\x05\x01\x09\x30\x09\x31\x16\x00\x00\x26\xff\x7f\x75\x10\x95\x02\x81\x02\xc0\xc0' > /tmp/hid.desc
cat /tmp/hid.desc > $G/functions/hid.usb0/report_desc || die "report_desc"
fi
mkdir -p $G/functions/ffs.adb || die "mkdir ffs.adb"
# link order: ECM, HID, ADB
ln -s $G/functions/ecm.usb0 $C/ecm.usb0 || die "link ecm"
[ $NOHID = 1 ] || ln -s $G/functions/hid.usb0 $C/hid.usb0 || die "link hid"
ln -s $G/functions/ffs.adb $C/ffs.adb || die "link adb"
echo "ECM+HID+ADB" > $C/strings/0x409/configuration 2>/dev/null
# ---- adbd BEFORE bind (writes FFS descriptors; stock adbd then binds g1/UDC itself) ----
mkdir -p /dev/usb-ffs/adb
grep -q ' /dev/usb-ffs/adb ' /proc/mounts || mount -t functionfs adb /dev/usb-ffs/adb || die "mount ffs"
/bin/adbd </dev/null >/tmp/adbd.log 2>&1 &
i=0; while [ ! -e /dev/usb-ffs/adb/ep1 ] && [ $i -lt 40 ]; do sleep 0.2; i=$((i+1)); done
[ -e /dev/usb-ffs/adb/ep1 ] || die "adbd did not write FFS descriptors"
i=0; while [ -z "$(udc_now)" ] && [ $i -lt 40 ]; do sleep 0.2; i=$((i+1)); done
if [ -z "$(udc_now)" ]; then echo "$UDC" > $G/UDC 2>>"$LOG" || die "bind"; fi
[ "$(udc_now)" = "$UDC" ] || die "UDC not bound"
sleep 1
[ $NOHID = 1 ] || [ -e /dev/hidg0 ] || die "no /dev/hidg0"
[ -d /sys/class/net/usb0 ] || die "no usb0 netdev"
ifconfig usb0 mtu 478 2>>"$LOG" || die "mtu"   # <=512B OUT requests -> PIO only (sunxi_udc DMA/bounce bug, proven 14:4x)
ifconfig usb0 169.254.7.2 netmask 255.255.0.0 up 2>>"$LOG"; ifconfig usb0:1 192.168.7.2 netmask 255.255.255.0 up 2>>"$LOG"
rm -f /tmp/mk20-bridge-stop
# ---- KD back up on a PTY bridge; shadow setusbconfig so KD's start does not reset USB ----
[ -e /dev/ttyGS0 ] && [ ! -L /dev/ttyGS0 ] && mv /dev/ttyGS0 /dev/ttyGS0.real
( trap '' HUP INT; exec sh -c "echo \$\$ > /tmp/mk20-bridge-loop.pid; while [ ! -f /tmp/mk20-bridge-stop ]; do $BR --listen 0.0.0.0:17890 --link /dev/ttyGS0; sleep 1; done" </dev/null >>"$LOG" 2>&1 ) &   # supervised
i=0; while [ ! -e /dev/ttyGS0 ] && [ $i -lt 25 ]; do sleep 0.2; i=$((i+1)); done
[ -e /dev/ttyGS0 ] || die "bridge did not create /dev/ttyGS0"
printf '#!/bin/sh\necho "setusbconfig shadowed by mk20 composite: $*"\nexit 0\n' > /tmp/setusbconfig-noop; chmod 755 /tmp/setusbconfig-noop
mount --bind /tmp/setusbconfig-noop /data/setusbconfig || die "shadow setusbconfig"
kd_start
pgrep -f KeyboardDevice >/dev/null || die "KD not running"
rm -f /mnt/SDCARD/mk20-composite-ok
OK=1
log "OK UDC=$(udc_now) links=$(ls $C | tr '\n' ' ') hidg0 usb0=192.168.7.2 kd=$(pgrep -f KeyboardDevice | head -1) adbd=$(pidof adbd)"
# host must confirm (touch /mnt/SDCARD/mk20-composite-ok via adb or mk20ctl over socket://169.254.7.2:17890)
# within 180 s, otherwise roll back to stock ACM+KD. Detached waiter (no setsid here).
ME=$(readlink -f "$0"); cp -f "$ME" /tmp/mk20-gadget.sh && [ "$(sha256sum < "$ME")" = "$(sha256sum < /tmp/mk20-gadget.sh)" ] || { OK=0; die "cannot stage rollback copy"; }
( trap '' HUP INT; exec sh -c "i=0; while [ \$i -lt 90 ]; do [ -f /mnt/SDCARD/mk20-composite-ok ] && exit 0; sleep 2; i=\$((i+1)); done; sh /tmp/mk20-gadget.sh --rollback-now" </dev/null >/dev/null 2>&1 9>&- ) &
WP=$!; sleep 1; kill -0 $WP 2>/dev/null || { OK=0; die "confirm waiter did not start -> rollback now"; }
log "confirm window 180s started (pid $WP)"
echo "gadget-ok" > /mnt/SDCARD/gadget-ecm-hid.status
exit 0
