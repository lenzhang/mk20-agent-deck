#!/bin/sh
# v3.9 Composite: ECM (macOS-native) + combined HID (kbd+abs pointer) + ADB (FFS), built IN g1
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
#  - KD gets a filtered `dmesg` (PATH shim via bind-mounted appLunch.sh) so its USB-reset hack does not fire on
#    f_hid's harmless -EOPNOTSUPP for SET_IDLE/SET_REPORT (root cause of the 3 s flapping, 2026-10-18 15:53).
#  - v3.2 boot mode (--boot, run by boot-every/composite.sh in the watcher): no host confirmation; instead a
#    detached flap monitor polls /sys/class/udc/<udc>/state and rolls back if the link drops >=5 times in 60 s.
#    Any rollback (failure, flapping, --rollback) ends in stock ACM + KD + adb (mk20-add-adb.sh).
#  - Addressing (1.1.0+): usb0 172.30.250.2/24 (Mac MK20 service manual 172.30.250.1/24) + alias 169.254.7.2/16;
#    fixed MACs. mk20d passes the configured values; the defaults here only apply outside release mode.
#  - It must NEVER run inside an adb shell (it kills adbd; adbd kills the session group). It queues itself to the watcher.
set -u
LOG=${MK20_GADGET_LOG:-/mnt/SDCARD/mk20/logs/gadget-ecm-hid.log}
# v3.8 knobs (set by mk20d from mk20.conf; defaults = the proven v3.7 behaviour)
# Release mode: a manual/queued run (no MK20_ECM_IP in the env) takes the knobs mk20d validated at boot from
# /tmp/mk20/gadget.env; mk20d writes that file only when the kernel/vendor/config gates allow the composite, so
# without it only a rollback is allowed (fail closed).
GENV=/tmp/mk20/gadget.env
if [ -n "${MK20_REL:-}" ] && [ -z "${MK20_ECM_IP:-}" ]; then
  if [ -f "$GENV" ]; then
    while IFS='=' read -r k v; do
      case "$v" in *[!0-9a-f.:]*|'') continue;; esac   # values are digits/dots/hex/colons only (mk20d validated them)
      case "$k" in
      MK20_ECM_IP) MK20_ECM_IP=$v;; MK20_ECM_MASK) MK20_ECM_MASK=$v;; MK20_DEV_MAC) MK20_DEV_MAC=$v;; MK20_HOST_MAC) MK20_HOST_MAC=$v;;
      MK20_BRIDGE_PORT) MK20_BRIDGE_PORT=$v;; MK20_FLAP_DROPS) MK20_FLAP_DROPS=$v;; MK20_FLAP_WINDOW) MK20_FLAP_WINDOW=$v;;
      MK20_NOHID) MK20_NOHID=$v;; MK20_NOADB) MK20_NOADB=$v;;
    esac; done < "$GENV"
  else case "${1:-}" in --rollback|--rollback-queued|--rollback-now) ;; *) echo "release mode: no $GENV (gates not passed at boot) -> refused"; exit 1;; esac; fi
fi
ECM_IP=${MK20_ECM_IP:-172.30.250.2}; ECM_MASK=${MK20_ECM_MASK:-255.255.255.0}; LL_IP=${MK20_LL_IP:-169.254.7.2}
DEV_MAC=${MK20_DEV_MAC:-02:11:22:33:44:55}; HOST_MAC=${MK20_HOST_MAC:-02:11:22:33:44:56}
BRIDGE_PORT=${MK20_BRIDGE_PORT:-17890}   # v3.9: the bridge binds the usb0 addresses only (ECM IP + link-local alias, SO_BINDTODEVICE usb0)
FLAP_DROPS=${MK20_FLAP_DROPS:-5}; FLAP_WINDOW=${MK20_FLAP_WINDOW:-60}
NOADB=${MK20_NOADB:-0}; [ -f /mnt/SDCARD/mk20-no-adb ] && NOADB=1
G=/sys/kernel/config/usb_gadget/g1
C=$G/configs/c.1
# release mode (firmware >= 1.0.0): the loader exports MK20_REL (verified tmpfs copy of the release)
if [ -n "${MK20_REL:-}" ]; then export MK20_REL; BR=$MK20_REL/bin/ttygs_tcp_bridge; ECMKO=$MK20_REL/lib/usb_f_ecm.ko; ADDADB=$MK20_REL/share/mk20-add-adb.sh
else BR=/data/ttygs_tcp_bridge; ECMKO=/data/mk20/usb_f_ecm.ko; ADDADB=/mnt/SDCARD/mk20/mk20-add-adb.sh; fi
mkdir -p "$(dirname "$LOG")" /mnt/SDCARD/mk20/logs
log() { echo "gadget $(cut -d' ' -f1 /proc/uptime): $*" >> "$LOG"; }

# Launch: adbd kills the whole process group of an adb shell when it ends (trap HUP does not help) and
# this busybox has no setsid, so we hand ourselves to the watcher job queue (runs outside adb/ACM sessions).
if [ "${1:-}" = --rollback ]; then set -- --rollback-queued; fi   # adb-safe: rollback also goes via the watcher
MODE=${1:-}
if [ "$MODE" != --run ] && [ "$MODE" != --boot ] && [ "$MODE" != --rollback-now ]; then
  Q=/mnt/SDCARD/job.sh; [ -e $Q ] && { echo "watcher queue busy ($Q exists)"; exit 1; }
  A=--run; [ "${1:-}" = --rollback-queued ] && A=--rollback-now
  echo "MK20_REL=${MK20_REL:-} MK20_GADGET_LOG=$LOG sh $(readlink -f "$0") $A" > $Q.tmp
  sha256sum $Q.tmp | awk '{print $1"  job.sh"}' > $Q.sha256.tmp; mv -f $Q.tmp $Q; mv -f $Q.sha256.tmp $Q.sha256
  echo "queued to watcher"; exit 0
fi

KDSVC=/etc/init.d/qt_app2   # procd service (CMD=/data/appLunch.sh). NOT qt_app1 (plain sh, kills adbd)
[ -x $KDSVC ] || KDSVC=""
udc_now() { cat $G/UDC 2>/dev/null; }
# KD start: via procd only when the service exists (a manual fallback raced procd -> TWO KD processes, 16:17).
# KD stop: procd may respawn appLunch.sh right after `stop`; killing that sh later orphaned its fresh KD
# (PPid 1) -> TWO KD after rollback (16:17 and 16:20). Kill appLunch.sh first, then KD, until both stay gone.
kd_stop() {
  [ -n "$KDSVC" ] && $KDSVC stop >>"$LOG" 2>&1
  i=0; while [ $i -lt 15 ]; do
    for p in $(pgrep -f /data/appLunch.sh); do kill $p 2>/dev/null; done
    killall KeyboardDevice 2>/dev/null; sleep 1
    pgrep -f /data/KeyboardDevice >/dev/null || pgrep -f /data/appLunch.sh >/dev/null || { sleep 1; pgrep -f /data/KeyboardDevice >/dev/null || pgrep -f /data/appLunch.sh >/dev/null || return 0; }
    i=$((i+1)); done
  killall -9 KeyboardDevice 2>/dev/null; log "WARN kd_stop: KD/appLunch still alive after 15 s (sent KILL)"
}
# reap any KD whose parent is not appLunch.sh (orphan from a respawn race)
kd_reap_orphans() {
  for p in $(pgrep -f /data/KeyboardDevice); do pp=$(awk '/^PPid:/{print $2}' /proc/$p/status 2>/dev/null)
    grep -q appLunch /proc/$pp/cmdline 2>/dev/null || { kill $p 2>/dev/null; log "reaped orphan KD $p (ppid $pp)"; }; done
}
kd_start() {
  if [ -n "$KDSVC" ]; then
    $KDSVC start >>"$LOG" 2>&1; i=0
    while ! pgrep -f /data/KeyboardDevice >/dev/null && [ $i -lt 20 ]; do sleep 1; i=$((i+1)); done
    pgrep -f /data/KeyboardDevice >/dev/null || log "WARN KD not up 20 s after procd start"
  else
    pgrep -f /data/KeyboardDevice >/dev/null || { [ -x /data/KeyboardDevice ] && ( trap '' HUP INT; exec /data/appLunch.sh </dev/null >/dev/null 2>&1 ) & }
  fi
  sleep 3; n=$(pgrep -f /data/KeyboardDevice | wc -l); [ "$n" -le 1 ] || { log "WARN $n KeyboardDevice processes"; kd_reap_orphans; }
}
# after a rollback: stock KD has re-run `setusbconfig serial` (ACM only) -> add adb back like boot-every/adb.sh
readd_adb() {
  [ "$NOADB" = 1 ] && return
  H=$ADDADB; [ -f "$H" ] || return
  i=0; until [ -n "$(udc_now)" ] && [ -e $C/acm.usb0 ] && [ -e /dev/ttyGS0 ] && pgrep -f KeyboardDevice >/dev/null; do
    i=$((i+1)); [ $i -gt 60 ] && { log "readd_adb: g1 not ready"; return; }; sleep 1; done
  sleep 5; cp -f $H /tmp/mk20-add-adb.sh; sh /tmp/mk20-add-adb.sh >>"$LOG" 2>&1 && log "adb re-added" || log "adb re-add FAILED"
  # KD's startup re-runs `setusbconfig serial` (drops ffs.adb) once or twice: keep re-adding for ~90 s
  n=0; while [ $n -lt 6 ]; do sleep 15; n=$((n+1)); [ -e $C/ffs.adb ] && continue
    log "adb gone again (KD reset) -> re-add #$n"; sh /tmp/mk20-add-adb.sh >>"$LOG" 2>&1; done
}
OK=0
cleanup() {
  [ "$OK" = 1 ] && return
  mkdir -p /tmp/mk20; touch /tmp/mk20/gadget.busy
  log "FAILSAFE: undo composite"
  touch /tmp/mk20-flapmon-stop
  killall adbd 2>/dev/null
  echo "" > $G/UDC 2>/dev/null
  for l in $C/*; do [ -L "$l" ] && rm -f "$l"; done
  rmdir $G/functions/ecm.usb0 $G/functions/hid.usb0 2>/dev/null
  # stop the bridge loop + bridge, restore the real ttyGS0 (validated pid: BusyBox `kill ""` = own process group)
  touch /tmp/mk20-bridge-stop; bp=$(cat /tmp/mk20-bridge-loop.pid 2>/dev/null); case "$bp" in ''|*[!0-9]*|0|1) ;; *) kill "$bp" 2>/dev/null;; esac
  killall ttygs_tcp_bridge 2>/dev/null; sleep 0.5; [ -L /dev/ttyGS0 ] && rm -f /dev/ttyGS0; [ -e /dev/ttyGS0.real ] && [ ! -e /dev/ttyGS0 ] && mv /dev/ttyGS0.real /dev/ttyGS0
  # KD may hold the dead PTY: restart it cleanly on real ttyGS0
  # unmount the shims only after KD + appLunch.sh have exited (a running sh keeps its script busy -> EBUSY)
  kd_stop
  for m in /data/setusbconfig /data/appLunch.sh; do i=0; while grep -q " $m " /proc/mounts && [ $i -lt 5 ]; do umount $m 2>/dev/null || sleep 1; i=$((i+1)); done
    grep -q " $m " /proc/mounts && log "WARN still mounted: $m"; done
  /data/setusbconfig serial >>"$LOG" 2>&1
  kd_start
  readd_adb
  log "FAILSAFE done UDC=$(udc_now) acm=$([ -e $C/acm.usb0 ] && echo y || echo n) kd=$(pgrep -f KeyboardDevice | head -1)"
  rm -f /tmp/mk20/gadget.busy
}
# rollback runs before any preflight so an abnormal state cannot block it
# once-guard: flapmon, the confirm waiter and mk20d can all ask; only the first undoes (cleared when a composite is up).
# Exit 0 = stock verified (or already done), 1 = rollback ran but stock not verified (guard released for a retry).
if [ "${1:-}" = --rollback-now ]; then mkdir -p /tmp/mk20; mkdir /tmp/mk20/rollback.once 2>/dev/null || { log "rollback already done/running"; exit 0; }
  log "rollback requested"; cleanup
  # verify stock: ACM linked, UDC bound, KD running. Only a verified rollback keeps the once-guard (retry otherwise).
  i=0; while ! pgrep -f KeyboardDevice >/dev/null && [ $i -lt 10 ]; do sleep 0.5; i=$((i+1)); done
  if [ -e $C/acm.usb0 ] && [ -n "$(udc_now)" ] && pgrep -f KeyboardDevice >/dev/null; then log "rollback verified"; exit 0; fi
  log "rollback NOT verified (acm=$([ -e $C/acm.usb0 ] && echo y || echo n) udc=$(udc_now)) -> guard released"
  rmdir /tmp/mk20/rollback.once 2>/dev/null; exit 1; fi

# ---- preflight (nothing touched yet) ----
UDC=$(ls /sys/class/udc 2>/dev/null | head -1)
[ -n "$UDC" ] || { log "ABORT no udc"; exit 1; }
[ -d $G ] && [ -d $C ] || { log "ABORT no g1 (KD has not configured USB yet)"; exit 1; }
[ "${1:-}" = --rollback-now ] || [ -x $BR ] || { log "ABORT $BR missing: KD would lose ttyGS0 (black screens)"; exit 1; }
[ -x /bin/adbd ] || { log "ABORT no /bin/adbd"; exit 1; }
grep -q '^usb_f_ecm ' /proc/modules || [ -f $ECMKO ] || { log "ABORT no $ECMKO"; exit 1; }
[ -n "${MK20_REL:-}" ] && [ -f "$MK20_REL/share/flapmon.sh" ] || { log "ABORT no flapmon.sh (MK20_REL unset?)"; exit 1; }
touch /tmp/mk20-flapmon-stop   # a re-run: any old flap monitor / confirm waiter exits (new ones start below)
log "BEGIN udc=$UDC kdsvc=${KDSVC:-none}"

trap cleanup EXIT
trap 'exit 1' INT TERM
trap '' HUP
die() { log "FAIL $*"; exit 1; }

# ---- stop KD (via procd so it is not respawned mid-change) and adbd ----
kd_stop; killall adbd 2>/dev/null; sleep 1
pgrep -f /data/KeyboardDevice >/dev/null && die "KD still running/respawned after stop"
# ---- unbind + clean stale links ----
i=0; while [ -n "$(udc_now)" ] && [ $i -lt 10 ]; do echo "" > $G/UDC 2>/dev/null; sleep 0.3; i=$((i+1)); done
[ -z "$(udc_now)" ] || die "cannot unbind"
for l in $C/*; do [ -L "$l" ] && rm -f "$l"; done
# ---- functions ----
grep -q '^usb_f_ecm ' /proc/modules || insmod $ECMKO 2>>"$LOG" || die "insmod ecm"
mkdir -p $G/functions/ecm.usb0 || die "mkdir ecm"
echo "$DEV_MAC" > $G/functions/ecm.usb0/dev_addr 2>/dev/null
echo "$HOST_MAC" > $G/functions/ecm.usb0/host_addr 2>/dev/null
NOHID=${MK20_NOHID:-0}; [ -f /mnt/SDCARD/mk20-gadget-nohid ] && NOHID=1   # ECM+ADB+bridge only (works on the ep3-only kernel)
if [ $NOHID = 0 ]; then
mkdir -p $G/functions/hid.usb0 || die "mkdir hid"
echo 1 > $G/functions/hid.usb0/protocol; echo 1 > $G/functions/hid.usb0/subclass
echo 9 > $G/functions/hid.usb0/report_length || die "report_length"
printf '\x05\x01\x09\x06\xa1\x01\x85\x01\x05\x07\x19\xe0\x29\xe7\x15\x00\x25\x01\x75\x01\x95\x08\x81\x02\x95\x01\x75\x08\x81\x03\x95\x05\x75\x01\x05\x08\x19\x01\x29\x05\x91\x02\x95\x01\x75\x03\x91\x03\x95\x06\x75\x08\x15\x00\x25\x65\x05\x07\x19\x00\x29\x65\x81\x00\xc0\x05\x01\x09\x02\xa1\x01\x85\x02\x09\x01\xa1\x00\x05\x09\x19\x01\x29\x03\x15\x00\x25\x01\x95\x03\x75\x01\x81\x02\x95\x01\x75\x05\x81\x03\x05\x01\x09\x30\x09\x31\x16\x00\x00\x26\xff\x7f\x75\x10\x95\x02\x81\x02\xc0\xc0' > /tmp/hid.desc
cat /tmp/hid.desc > $G/functions/hid.usb0/report_desc || die "report_desc"
fi
[ "$NOADB" = 1 ] || mkdir -p $G/functions/ffs.adb || die "mkdir ffs.adb"
# link order: ECM, HID, ADB
ln -s $G/functions/ecm.usb0 $C/ecm.usb0 || die "link ecm"
[ $NOHID = 1 ] || ln -s $G/functions/hid.usb0 $C/hid.usb0 || die "link hid"
[ "$NOADB" = 1 ] || ln -s $G/functions/ffs.adb $C/ffs.adb || die "link adb"
echo "ECM+HID+ADB" > $C/strings/0x409/configuration 2>/dev/null
# ---- adbd BEFORE bind (writes FFS descriptors; stock adbd then binds g1/UDC itself) ----
if [ "$NOADB" = 0 ]; then
mkdir -p /dev/usb-ffs/adb
grep -q ' /dev/usb-ffs/adb ' /proc/mounts || mount -t functionfs adb /dev/usb-ffs/adb || die "mount ffs"
/bin/adbd </dev/null >/tmp/adbd.log 2>&1 &
i=0; while [ ! -e /dev/usb-ffs/adb/ep1 ] && [ $i -lt 40 ]; do sleep 0.2; i=$((i+1)); done
[ -e /dev/usb-ffs/adb/ep1 ] || die "adbd did not write FFS descriptors"
fi
i=0; while [ -z "$(udc_now)" ] && [ $i -lt 40 ]; do sleep 0.2; i=$((i+1)); done
if [ -z "$(udc_now)" ]; then echo "$UDC" > $G/UDC 2>>"$LOG" || die "bind"; fi
[ "$(udc_now)" = "$UDC" ] || die "UDC not bound"
sleep 1
[ $NOHID = 1 ] || [ -e /dev/hidg0 ] || die "no /dev/hidg0"
[ -d /sys/class/net/usb0 ] || die "no usb0 netdev"
ifconfig usb0 mtu 478 2>>"$LOG" || die "mtu"   # <=512B OUT requests -> PIO only (sunxi_udc DMA/bounce bug, proven 14:4x)
ifconfig usb0 "$ECM_IP" netmask "$ECM_MASK" up 2>>"$LOG" || die "usb0 ip"
BRIDGE_LISTEN="--dev usb0 --listen $ECM_IP:$BRIDGE_PORT"
if ifconfig usb0:1 "$LL_IP" netmask 255.255.0.0 up 2>>"$LOG"; then BRIDGE_LISTEN="$BRIDGE_LISTEN --listen $LL_IP:$BRIDGE_PORT"
else log "WARN link-local alias $LL_IP failed (bridge on $ECM_IP only)"; fi
rm -f /tmp/mk20-bridge-stop
# ---- KD back up on a PTY bridge; shadow setusbconfig so KD's start does not reset USB ----
mkdir -p /tmp/mk20
[ -e /dev/ttyGS0 ] && [ ! -L /dev/ttyGS0 ] && mv /dev/ttyGS0 /dev/ttyGS0.real
( trap '' HUP INT; exec sh -c "echo \$\$ > /tmp/mk20-bridge-loop.pid; while [ ! -f /tmp/mk20-bridge-stop ]; do $BR $BRIDGE_LISTEN --link /dev/ttyGS0; sleep 1; done" </dev/null >>/tmp/mk20/bridge.log 2>&1 ) &   # supervised (tmpfs log: an open FAT file blocks hardreset's remount ro)
i=0; while [ ! -e /dev/ttyGS0 ] && [ $i -lt 25 ]; do sleep 0.2; i=$((i+1)); done
[ -e /dev/ttyGS0 ] || die "bridge did not create /dev/ttyGS0"
printf '#!/bin/sh\necho "setusbconfig shadowed by mk20 composite: $*"\nexit 0\n' > /tmp/setusbconfig-noop; chmod 755 /tmp/setusbconfig-noop
mount --bind /tmp/setusbconfig-noop /data/setusbconfig || die "shadow setusbconfig"
# ROOT CAUSE of the 15:40 flapping (found 15:53): KD's UsbCDCStateThread runs `dmesg -c` every 3 s and, if the
# output contains "ERR: Operation not supported", reads usbc0/usb_device = full USB rmmod/insmod. sunxi_udc prints
# that line whenever a function's setup() returns -EOPNOTSUPP; f_hid (5.4) does that for SET_IDLE / SET_REPORT on
# ep0, which macOS sends to a keyboard -> reset every ~3 s. Fix: give KD a PATH-first `dmesg` that drops that line.
mkdir -p /tmp/mk20-shim
printf '#!/bin/sh\n/bin/busybox dmesg "$@" | /bin/busybox grep -v "ERR: Operation not supported"\n' > /tmp/mk20-shim/dmesg; chmod 755 /tmp/mk20-shim/dmesg
umount /data/appLunch.sh 2>/dev/null; sed 's#^source /data/qt_env.sh#source /data/qt_env.sh; export PATH=/tmp/mk20-shim:$PATH#' /data/appLunch.sh > /tmp/appLunch-shim.sh; chmod 755 /tmp/appLunch-shim.sh
grep -q mk20-shim /tmp/appLunch-shim.sh || die "dmesg shim sed"
mount --bind /tmp/appLunch-shim.sh /data/appLunch.sh || die "shadow appLunch"
kd_start
pgrep -f KeyboardDevice >/dev/null || die "KD not running"
# test hook (one-shot): prove the failure rollback with everything set up (bridge, shims, KD on PTY)
if [ -f /mnt/SDCARD/mk20-composite-failtest ]; then rm -f /mnt/SDCARD/mk20-composite-failtest; sync; die "FAILTEST: forced failure (test hook)"; fi
rm -f /mnt/SDCARD/mk20-composite-ok
rmdir /tmp/mk20/rollback.once 2>/dev/null
OK=1
log "OK mode=$MODE UDC=$(udc_now) links=$(ls $C | tr '\n' ' ') hidg0 usb0=$ECM_IP bridge=[$BRIDGE_LISTEN] kd=$(pgrep -f KeyboardDevice | head -1) adbd=$(pidof adbd)"
# host must confirm (touch /mnt/SDCARD/mk20-composite-ok via adb or mk20ctl over socket://169.254.7.2:17890)
# within 180 s, otherwise roll back to stock ACM+KD. Detached waiter (no setsid here).
ME=$(readlink -f "$0"); cp -f "$ME" /tmp/mk20-gadget.sh && [ "$(sha256sum < "$ME")" = "$(sha256sum < /tmp/mk20-gadget.sh)" ] || { OK=0; die "cannot stage rollback copy"; }
# flap monitor (both modes, share/flapmon.sh): >=5 configured->not-configured drops within 60 s -> rollback to
# ACM+KD+adb. Host USB suspend (Mac sleep) is not a flap.
rm -f /tmp/mk20-flapmon-stop /tmp/mk20/flapmon.ready
FMSRC=${MK20_REL:+$MK20_REL/share/flapmon.sh}
{ [ -n "$FMSRC" ] && cp -f "$FMSRC" /tmp/mk20-flapmon.sh; } || { OK=0; die "no flapmon.sh in release"; }
( trap '' HUP INT; export MK20_REL="${MK20_REL:-}" MK20_GADGET_LOG="$LOG"
  exec sh /tmp/mk20-flapmon.sh "/sys/class/udc/$UDC/state" "$FLAP_WINDOW" "$FLAP_DROPS" "$LOG" /tmp/mk20-flapmon-stop -- \
    sh /tmp/mk20-gadget.sh --rollback-now </dev/null >/dev/null 2>&1 9>&- ) &
FP=$!; i=0; while [ ! -f /tmp/mk20/flapmon.ready ] && [ $i -lt 40 ] && kill -0 $FP 2>/dev/null; do sleep 0.25; i=$((i+1)); done
[ -f /tmp/mk20/flapmon.ready ] && kill -0 $FP 2>/dev/null || { OK=0; die "flap monitor did not start (no lock/ready marker)"; }
log "flap monitor started (pid $FP)"
if [ "$MODE" = --boot ]; then
  touch /mnt/SDCARD/mk20-composite-ok; echo "gadget-ok-boot" > /mnt/SDCARD/gadget-ecm-hid.status; exit 0
fi
( trap '' HUP INT; exec sh -c "i=0; while [ \$i -lt 90 ]; do [ -f /mnt/SDCARD/mk20-composite-ok ] && exit 0; [ -f /tmp/mk20-flapmon-stop ] && exit 0; sleep 2; i=\$((i+1)); done; MK20_REL=${MK20_REL:-} MK20_GADGET_LOG=$LOG sh /tmp/mk20-gadget.sh --rollback-now" </dev/null >/dev/null 2>&1 9>&- ) &
WP=$!; sleep 1; kill -0 $WP 2>/dev/null || { OK=0; die "confirm waiter did not start -> rollback now"; }
log "confirm window 180s started (pid $WP)"
echo "gadget-ok" > /mnt/SDCARD/gadget-ecm-hid.status
exit 0
