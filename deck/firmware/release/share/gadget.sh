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
#    (/data/setusbconfig serial) and adds adb, with KD stopped.
#  - Stale links in g1/configs/c.1 are removed before reuse.
#  - KD is stopped via its procd service; no path starts it.
#  - v3.2 boot mode (--boot, run by boot-every/composite.sh in the watcher): no host confirmation; instead a
#    detached flap monitor polls /sys/class/udc/<udc>/state and rolls back if the link drops >=5 times in 60 s.
#    Release rollback requests loader A/B; setup failure restores ACM + adb, never KD.
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
      MK20_NOHID) MK20_NOHID=$v;; MK20_NOADB) MK20_NOADB=$v;; MK20_HID_SCROLL) MK20_HID_SCROLL=$v;; MK20_KBD) MK20_KBD=$v;;
    esac; done < "$GENV"
  else case "${1:-}" in --rollback|--rollback-queued|--rollback-now) ;; *) m="release mode: no $GENV (gates not passed at boot) -> refused"; echo "$m"
    echo "gadget $(cut -d' ' -f1 /proc/uptime): $m" >> "$LOG" 2>/dev/null; exit 1;; esac; fi   # r8 #1: detached jobs have no stdout
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
  # Opus r7 BLOCKER: the watcher kills a job tree after MK20_JOB_TIMEOUT (300 s) - shorter than the setup/rollback
  # supervisors' own bounds - so the transition is detached from the job (reparented to init, no FAT job log held).
  echo "( MK20_REL=${MK20_REL:-} MK20_GADGET_LOG=$LOG sh $(readlink -f "$0") $A </dev/null >/dev/null 2>&1 7>&- & ); echo started" > $Q.tmp
  sha256sum $Q.tmp | awk '{print $1"  job.sh"}' > $Q.sha256.tmp; mv -f $Q.tmp $Q; mv -f $Q.sha256.tmp $Q.sha256
  echo "queued to watcher"; exit 0
fi

KDSVC=/etc/init.d/qt_app2   # procd service (CMD=/data/appLunch.sh). NOT qt_app1 (plain sh, kills adbd)
[ -x $KDSVC ] || KDSVC=""
udc_now() { cat $G/UDC 2>/dev/null; }
# KD is stop-only. No release or developer-opt-out path starts the vendor UI.
# 1.2.0 r2 (Opus r120 #1): the on-board router holds /dev/hidg0 open. On this kernel (pre-6.2 f_hid: cdev embedded in
# f_hidg, freed on configfs unlink) removing hid.usb0 under an open fd is a use-after-free, so every path that touches
# the UDC/configfs (cleanup = all rollbacks, setup_main = (re-)setup) first stops it: stop file (mk20d supervisor
# will not relaunch), kill the validated supervisor loop, SIGTERM (router sends all-keys-up) for <= ~2 s, then SIGKILL.
# Only pids whose cmdline really is `.../bin/mk20-router --src ...` are signalled.
router_pids() { for p in $(pgrep -f 'bin/mk20-router --src' 2>/dev/null); do
  case "$p" in ''|*[!0-9]*) continue;; esac
  tr '\000' ' ' < "/proc/$p/cmdline" 2>/dev/null | grep -q 'bin/mk20-router --src' && echo "$p"; done; }
router_stop() {
  touch /tmp/mk20-router-stop
  rp=$(cat /tmp/mk20/router-loop.pid 2>/dev/null)
  case "$rp" in ''|*[!0-9]*|0|1) ;; *) tr '\000' ' ' < "/proc/$rp/cmdline" 2>/dev/null | grep -q 'mk20d router' && kill "$rp" 2>/dev/null;; esac
  ri=0; rps=$(router_pids)
  while [ -n "$rps" ] && [ $ri -lt "${MK20_ROUTER_TERM_TICKS:-10}" ]; do
    for rp in $rps; do kill "$rp" 2>/dev/null; done; sleep 0.2; ri=$((ri+1)); rps=$(router_pids); done
  if [ -n "$rps" ]; then log "router did not exit on SIGTERM -> SIGKILL $rps"
    for rp in $rps; do kill -9 "$rp" 2>/dev/null; done
    ri=0; while [ -n "$(router_pids)" ] && [ $ri -lt 10 ]; do sleep 0.1; ri=$((ri+1)); done
    [ -n "$(router_pids)" ] && log "WARN router still present after SIGKILL"; fi
  kbd_hid_quiesce || return 1
  return 0
}
# mk20-kbd (KD replacement) also holds /dev/hidg0. Same UAF rule: before any UDC/configfs change it must have closed
# it. It watches /tmp/mk20-router-stop (touched above) and closes on SIGUSR1; we wait until /proc shows no hidg0 fd.
# Fail closed: still held after ~3 s -> SIGTERM, then SIGKILL (the fd dies with the process).
# Only pids whose cmdline really is `.../bin/mk20-kbd --run ...` are signalled.
kbd_pids() { for p in $(pgrep -f 'bin/mk20-kbd --run' 2>/dev/null); do
  case "$p" in ''|*[!0-9]*) continue;; esac
  tr '\000' ' ' < "/proc/$p/cmdline" 2>/dev/null | grep -q 'bin/mk20-kbd --run' && echo "$p"; done; }
# a parked stale fd (mk20-kbd keeps it open after a node re-create, see kbd/hid.h) reads "/dev/hidg0 (deleted)"
# a recorded pid is gone only when /proc/<pid> vanished or it is a zombie leader with no thread left: after SIGKILL
# the leader can be a zombie (empty cmdline, fd dir unreadable) while a worker thread sits in uninterruptible fb I/O
# and still holds the shared fd table (codex kdr-r2 #1)
kbd_st() { sed 's/.*) //' "/proc/$1/stat" 2>/dev/null | cut -d' ' -f20; }   # process start time (field 22)
kbd_alive() { # $1 = pid or pid:starttime (a reused pid with another start time is not ours)
  set -- "${1%%:*}" "$(case "$1" in *:*) echo "${1#*:}";; esac)"
  [ -d "/proc/$1" ] || return 1
  [ -z "$2" ] || [ "$(kbd_st "$1")" = "$2" ] || return 1
  [ "$(sed -n 's/^State:[[:space:]]*\(.\).*/\1/p' "/proc/$1/status" 2>/dev/null)" = Z ] || return 0
  [ "$(ls "/proc/$1/task" 2>/dev/null | wc -l)" -gt 1 ]; }
# every validated mk20-kbd pid ever signalled is persisted (/tmp/mk20/kbd-pids.rec) until it is verifiably gone: a
# zombie leader has an empty cmdline, so later discovery (kbd_stop, a rollback retry) would otherwise forget a
# surviving worker thread (codex kdr-r3 #1)
kbd_rec() { [ $# -gt 0 ] || return 0; mkdir -p /tmp/mk20; for p in "$@"; do echo "$p:$(kbd_st "$p")"; done >> /tmp/mk20/kbd-pids.rec; }
kbd_any_alive() { for p in "$@"; do kbd_alive "$p" && { echo "$p"; return 0; }; done; return 1; }
kbd_hid_holders() { for p in $(kbd_pids); do for f in /proc/"$p"/fd/*; do
  case "$(readlink "$f" 2>/dev/null)" in "${MK20_HIDG:-/dev/hidg0}"|"${MK20_HIDG:-/dev/hidg0} (deleted)") echo "$p"; break;; esac; done; done; }
kbd_hid_quiesce() {
  # shellcheck disable=SC2046   # numeric pids, word splitting intended
  kbd_rec $(kbd_pids)
  kh=$(kbd_hid_holders); [ -n "$kh" ] || return 0
  for kp in $kh; do kill -USR1 "$kp" 2>/dev/null; done
  ki=0; while [ -n "$kh" ] && [ $ki -lt "${MK20_KBD_HID_TICKS:-15}" ]; do sleep 0.2; ki=$((ki+1)); kh=$(kbd_hid_holders); done
  [ -n "$kh" ] || return 0
  log "mk20-kbd still holds hidg0 after SIGUSR1 -> SIGTERM $kh"
  for kp in $kh; do kill "$kp" 2>/dev/null; done
  ki=0; while [ -n "$(kbd_hid_holders)" ] && [ $ki -lt 10 ]; do sleep 0.2; ki=$((ki+1)); done
  kh=$(kbd_hid_holders); [ -n "$kh" ] || return 0
  log "mk20-kbd ignored SIGTERM -> SIGKILL $kh"; for kp in $kh; do kill -9 "$kp" 2>/dev/null; done
  ki=0; while kbd_any_alive $kh >/dev/null && [ $ki -lt 20 ]; do sleep 0.1; ki=$((ki+1)); done
  { [ -n "$(kbd_hid_holders)" ] || kbd_any_alive $kh >/dev/null; } && { log "FAIL mk20-kbd still holds hidg0 / threads alive after SIGKILL ($kh)"; return 1; }
  return 0
}
# Full stop before every mutating cleanup/setup path. Stop file (the mk20d kbd supervisor will not relaunch), validated supervisor kill, bounded
# SIGTERM (mk20-kbd sends all-keys-up), then SIGKILL. The next boot decides again.
kbd_stop() {
  touch /tmp/mk20-kbd-stop
  kl=$(cat /tmp/mk20/kbd-loop.pid 2>/dev/null)
  case "$kl" in ''|*[!0-9]*|0|1) ;; *) tr '\000' ' ' < "/proc/$kl/cmdline" 2>/dev/null | grep -q 'mk20d kbd' && kill "$kl" 2>/dev/null;; esac
  ki=0; kps=$(kbd_pids); kbd_rec $kps
  krec="$(cat /tmp/mk20/kbd-pids.rec 2>/dev/null) $kps"   # every validated pid ever seen, also by earlier calls
  while [ -n "$kps" ] && [ $ki -lt "${MK20_KBD_TERM_TICKS:-15}" ]; do
    for kp in $kps; do kill "$kp" 2>/dev/null; done; sleep 0.2; ki=$((ki+1)); kps=$(kbd_pids); kbd_rec $kps; krec="$krec $kps"; done
  if [ -n "$kps" ]; then log "mk20-kbd did not exit on SIGTERM -> SIGKILL $kps"; for kp in $kps; do kill -9 "$kp" 2>/dev/null; done
    ki=0; while [ -n "$(kbd_pids)" ] && [ $ki -lt 10 ]; do sleep 0.1; ki=$((ki+1)); done; fi
  sleep 0.2; kps=$(kbd_pids); kbd_rec $kps; krec="$krec $kps"   # a launch forked just before the supervisor died execs late
  [ -n "$kps" ] && { for kp in $kps; do kill -9 "$kp" 2>/dev/null; done; sleep 0.3; }
  ki=0; while kbd_any_alive $krec >/dev/null && [ $ki -lt 20 ]; do sleep 0.1; ki=$((ki+1)); done
  { [ -n "$(kbd_pids)" ] || kbd_any_alive $krec >/dev/null; } && { log "FAIL mk20-kbd survives SIGKILL ($(kbd_pids) $(kbd_any_alive $krec))"; return 1; }
  rm -f /tmp/mk20/kbd.mode /tmp/mk20/kbd-pids.rec   # all recorded groups verifiably gone
  return 0
}
# KD stop: procd may respawn appLunch.sh right after `stop`; killing that sh later orphaned its fresh KD
# (PPid 1) -> TWO KD after rollback (16:17 and 16:20). Kill appLunch.sh first, then KD, until both stay gone.
kd_stop() {
  [ -n "$KDSVC" ] && $KDSVC stop >>"$LOG" 2>&1 7>&-
  i=0; while [ $i -lt 15 ]; do
    for p in $(pgrep -f /data/appLunch.sh); do kill $p 2>/dev/null; done
    killall KeyboardDevice 2>/dev/null; sleep 1
    pgrep -f /data/KeyboardDevice >/dev/null || pgrep -f /data/appLunch.sh >/dev/null || { sleep 1; pgrep -f /data/KeyboardDevice >/dev/null || pgrep -f /data/appLunch.sh >/dev/null || return 0; }
    i=$((i+1)); done
  killall -9 KeyboardDevice 2>/dev/null; log "WARN kd_stop: KD/appLunch still alive after 15 s (sent KILL)"
}
# Setup failsafe restores serial ACM -> add adb, without any vendor UI.
readd_adb() {
  [ "$NOADB" = 1 ] && return
  H=$ADDADB; [ -f "$H" ] || return
  i=0; until [ -n "$(udc_now)" ] && [ -e $C/acm.usb0 ] && [ -e /dev/ttyGS0 ]; do
    i=$((i+1)); [ $i -gt 60 ] && { log "readd_adb: g1 not ready"; return; }; sleep 1; done
  sleep 5; cp -f $H /tmp/mk20-add-adb.sh; sh /tmp/mk20-add-adb.sh >>"$LOG" 2>&1 7>&- && log "adb re-added" || log "adb re-add FAILED"
  # Keep the serial rescue adb available during the settling window.
  n=0; while [ $n -lt 6 ]; do sleep 15; n=$((n+1)); [ -e $C/ffs.adb ] && continue
    log "adb gone again (KD reset) -> re-add #$n"; sh /tmp/mk20-add-adb.sh >>"$LOG" 2>&1 7>&-; done
}
OK=0
cleanup() {
  [ "$OK" = 1 ] && return
  mkdir -p /tmp/mk20; touch /tmp/mk20/gadget.busy
  log "FAILSAFE: undo composite"
  router_stop   # before ANY UDC/configfs change (hidg0 fd vs f_hid free)
  # Fail closed: a surviving mk20-kbd means no
  # UDC/configfs change (hidg0 fd) and no KD start (two writers on ttyS1/fb)
  kbd_stop || { log "FAILSAFE ABORT: mk20-kbd not stopped -> UDC/configfs untouched, KD not started"; rm -f /tmp/mk20/gadget.busy; return 1; }
  touch /tmp/mk20-flapmon-stop
  killall adbd 2>/dev/null
  echo "" > $G/UDC 2>/dev/null
  for l in $C/*; do [ -L "$l" ] && rm -f "$l"; done
  rmdir $G/functions/ecm.usb0 $G/functions/hid.usb0 2>/dev/null
  # stop the bridge loop + bridge, restore the real ttyGS0 (validated pid: BusyBox `kill ""` = own process group)
  touch /tmp/mk20-bridge-stop; bp=$(cat /tmp/mk20-bridge-loop.pid 2>/dev/null); case "$bp" in ''|*[!0-9]*|0|1) ;; *) kill "$bp" 2>/dev/null;; esac
  killall ttygs_tcp_bridge 2>/dev/null; sleep 0.5; [ -L /dev/ttyGS0 ] && rm -f /dev/ttyGS0; [ -e /dev/ttyGS0.real ] && [ ! -e /dev/ttyGS0 ] && mv /dev/ttyGS0.real /dev/ttyGS0
  # Clear any leftover KD before restoring the serial rescue transport
  # unmount the shims only after KD + appLunch.sh have exited (a running sh keeps its script busy -> EBUSY)
  kd_stop
  for m in /data/setusbconfig /data/appLunch.sh; do i=0; while grep -q " $m " /proc/mounts && [ $i -lt 5 ]; do umount $m 2>/dev/null || sleep 1; i=$((i+1)); done
    grep -q " $m " /proc/mounts && log "WARN still mounted: $m"; done
  /data/setusbconfig serial >>"$LOG" 2>&1 7>&-
  readd_adb
  log "FAILSAFE done UDC=$(udc_now) acm=$([ -e $C/acm.usb0 ] && echo y || echo n) kd=$(pgrep -f KeyboardDevice | head -1)"
  rm -f /tmp/mk20/gadget.busy
}
# rollback runs before any preflight so an abnormal state cannot block it
# Transition mutex (1.1.4, r5): ONE flock (fd 7 on /tmp/mk20/transition.lock) is held for the WHOLE setup or the WHOLE
# rollback, so they can never overlap (flapmon, the confirm waiter and mk20d may all ask for --rollback-now). Every
# process spawned while it is held gets 7>&- (adbd, bridge loop, KD, add-adb, flapmon, confirm waiter), so only the
# owner holds it and it is released when the owner exits. A rollback that finds stock verified is a no-op: exit 0
# always means stock is up. Every lock step is checked (fail closed: no mutation without the mutex).
# Owner-local bounded supervisor: the rollback runs cleanup in its own child and waits at most MK20_RB_TIMEOUT
# (default 330 s); on timeout it stops, kills and reaps ITS OWN child tree before releasing the mutex. No caller ever
# kills another owner; a waiter that cannot get the mutex in MK20_RB_LOCK_TRIES*0.25 s (default 900 s) gives up (rc 4).
# >>> rollback-block (tests extract from here to the matching end marker)
TL=/tmp/mk20/transition.lock
stock_ok() { [ -e $C/acm.usb0 ] && [ ! -e $C/ecm.usb0 ] && [ ! -e $C/hid.usb0 ] && [ -n "$(udc_now)" ] &&
  [ -c /dev/ttyGS0 ] && [ ! -L /dev/ttyGS0 ] && ! grep -qE ' /data/(setusbconfig|appLunch\.sh) ' /proc/mounts &&
  ! pgrep -f '^/data/KeyboardDevice' >/dev/null; }
rb_status() { echo "$1" > /mnt/SDCARD/gadget-ecm-hid.status 2>/dev/null; }
pstart() { awk '$3 != "Z" {print $22}' "/proc/$1/stat" 2>/dev/null; }   # empty if gone or a zombie
# tl_lock <tries>: take the transition mutex on fd 7 (tries 0.25 s apart; BusyBox flock has no -w). 1 = not held.
tl_lock() {
  mkdir -p "$(dirname "$TL")" 2>/dev/null || return 1
  touch "$TL" 2>/dev/null || return 1   # an exec redirect error would exit the shell (special builtin)
  exec 7>"$TL" || return 1
  n=0; until flock -n 7; do n=$((n+1)); [ "$n" -ge "$1" ] && { exec 7>&-; return 1; }; sleep 0.25; done
}
kids() { for d in /proc/[0-9]*; do [ "$(awk '/^PPid:/{print $2}' "$d/status" 2>/dev/null)" = "$1" ] && echo "${d#/proc/}"; done; }
# kill_tree prints "pid:starttime" for every process it stops (taken while STOPped, before SIGKILL reparents the
# descendants to init) so bounded_wait can check the WHOLE tree afterwards (r6: an unkillable grandchild may hold fd 7).
kill_tree() { kill -STOP "$1" 2>/dev/null; echo "$1:$(pstart "$1")"; for k in $(kids "$1"); do kill_tree "$k"; done; kill -9 "$1" 2>/dev/null; }
# any_alive "<pid:start ...>": 0 if any listed process still runs with the same start time (not a zombie)
any_alive() { for e in $1; do [ -n "${e#*:}" ] && [ "$(pstart "${e%%:*}")" = "${e#*:}" ] && return 0; done; return 1; }
# uint_cap <value> <default> <min> <max>: a validated, clamped decimal (no octal, no overflow: >6 digits = max).
# Every timeout/lock-try knob goes through this BEFORE any lock or fork (r5: bad arithmetic must not kill the owner).
uint_cap() {
  case "$1" in ''|*[!0-9]*) echo "$2"; return;; esac
  v=${1#"${1%%[!0]*}"}; [ -n "$v" ] || v=0
  [ ${#v} -gt 6 ] && { echo "$4"; return; }
  [ "$v" -lt "$3" ] && v=$3; [ "$v" -gt "$4" ] && v=$4; echo "$v"
}
# r5 #5: cleanup worst case on a HEALTHY board = kd_stop<=32 + umount<=10 + readd_adb (g1 wait<=60,
# 5, add-adb ~4 x7, 6x15 re-add window) ~= 260 s -> 330 s so a healthy rollback incl. add-adb is never cut off.
RBRETRY=$(uint_cap "${MK20_RB_RETRY_S:-}" 20 0 300)       # queued/manual rollback: pause before its one retry (s)
RBT=$(uint_cap "${MK20_RB_TIMEOUT:-}" 330 1 900)          # rollback / post-timeout cleanup bound (s)
RBTRIES=$(uint_cap "${MK20_RB_LOCK_TRIES:-}" 4400 1 4800)  # rollback mutex wait (x0.25 s = 1100 s) > worst owner hold:
# setup supervisor SUT+RBT+30 verify+2x10 kill checks+20 escalate (~850 s) + fork/poll overhead (r8 #4)
SUTRIES=$(uint_cap "${MK20_SETUP_LOCK_TRIES:-}" 40 1 4800) # setup mutex wait (x0.25 s)
SUT=$(uint_cap "${MK20_SETUP_TIMEOUT:-}" 450 1 1800)       # whole setup incl. its own failsafe (~60 s + cleanup <=330 s)
# bounded_wait <pid> <s>: 0 = the child exited in time (rc in BW_RC); 1 = timed out: its whole tree was stopped,
# SIGKILLed and the child reaped; 2 = timed out AND still alive 10 s after SIGKILL (D state, e.g. a stuck UDC write):
# not waited for (an unbounded wait would wedge us). "Alive" covers EVERY process of the tree snapshotted by
# kill_tree, not just the root (r6). Only ever used on OUR OWN children.
bounded_wait() {
  bst=$(pstart "$1"); bi=0; BW_RC=1
  while [ -n "$bst" ] && [ "$(pstart "$1")" = "$bst" ] && [ $bi -lt $(($2*4)) ]; do sleep 0.25; bi=$((bi+1)); done
  if [ -n "$bst" ] && [ "$(pstart "$1")" = "$bst" ]; then
    bsnap="$1:$bst $(kill_tree "$1")"; bi=0
    while any_alive "$bsnap" && [ $bi -lt 40 ]; do sleep 0.25; bi=$((bi+1)); done
    any_alive "$bsnap" && return 2
    wait "$1" 2>/dev/null; return 1
  fi
  wait "$1" 2>/dev/null; BW_RC=$?; return 0
}
# escalate <why>: no transition can complete (an unkillable child holds fd 7, or stock is not reached): persist
# stock mode (flag read at boot) and hard-reset via the rescue watcher (cmd-reboot) - same path as flapmon's.
# r6: the writes run in a bounded child WITHOUT fd 7 (a hung FAT write cannot wedge the mutex); success = both files
# exist afterwards (sync cannot mask a failed touch). 0 = escalated, 1 = could not persist (caller exits 1, not 3).
# r8: BW_RC is saved/restored (callers still need their own child's rc); the writer marks success on tmpfs BEFORE
# sync (a slow sync or the watcher consuming cmd-reboot is not a failure); the STUCK/ESCALATED status is written last.
escalate() {
  ebrc=${BW_RC:-1}; EF=${MK20_FLAP_FLAG:-/mnt/SDCARD/mk20-no-composite}; ER=${MK20_FLAP_REBOOT:-/mnt/SDCARD/cmd-reboot}
  EOKF=/tmp/mk20/escalate.ok; rm -f "$EOKF"
  log "ESCALATE: $1 -> $EF + reboot"
  ( touch "$EF" && touch "$ER" && [ -e "$EF" ] && [ -e "$ER" ] && touch "$EOKF"; sync ) </dev/null 7>&- &
  bounded_wait $! 20; ebw=$?; BW_RC=$ebrc
  if [ -e "$EOKF" ]; then rm -f "$EOKF"; rb_status "$2"; return 0; fi
  log "ESCALATE FAILED (bw=$ebw): $EF / $ER not persisted"; rb_status "$2-ESCALATE-FAILED"; return 1
}
# stuck <why> <status>: rc-3 exit (escalated; flapmon/confirm waiter do not retry). Clears gadget.busy like every
# other exit under the mutex; rc 1 if the escalation itself could not be persisted.
stuck() { rm -f /tmp/mk20/gadget.busy; escalate "$1" "$2" && exit 3; exit 1; }
# Exit codes of --rollback-now: 0 A/B queued (release) / ACM verified (standalone) | 1 not verified | 3 stuck+escalated | 4 refused (mutex busy: another
# owner is mid-transition and owns gadget.busy) | 5 stale (r5 #7: MK20_GEN names an older composite than the current
# generation in $GENF -> a queued rollback from a dead composite must not tear down a newer one; nothing touched).
GENF=/tmp/mk20/composite.gen
# stale only when a DIFFERENT current generation is published (GPT r7: a missing/empty token is not "stale")
gen_stale() { gcur=$(cat "$GENF" 2>/dev/null); [ -n "${MK20_GEN:-}" ] && [ -n "$gcur" ] && [ "$gcur" != "$MK20_GEN" ]; }
if [ "${1:-}" = --rollback-now ] && [ -n "${MK20_REL:-}" ]; then
  gen_stale && { log "stale release rollback ignored"; exit 5; }
  log "release rollback requested -> loader A/B (USB stays up until reset)"
  touch /mnt/SDCARD/cmd-rollback && sync
  exit $?
fi
if [ "${1:-}" = --rollback-now ]; then
  # GPT r7: a stale request that could not get the mutex is rc 5 (no-op), never rc 4 (callers would retry/escalate)
  tl_lock "$RBTRIES" || { gen_stale && { log "rollback: mutex busy and request stale ($MK20_GEN) -> ignored"; exit 5; }
    log "rollback: transition mutex not acquired (busy or lock error) -> refused"; exit 4; }
  if gen_stale; then
    log "rollback: stale request for generation $MK20_GEN (current $(cat "$GENF" 2>/dev/null || echo none)) -> ignored"; exit 5; fi
  if stock_ok; then log "rollback already done (stock verified)"; exit 0; fi
  log "rollback requested"
  # r6: flapmon, the confirm waiter and mk20d retry/escalate themselves (MK20_RB_CALLER_RETRIES=1: one attempt, rc 1).
  # A queued/manual run has nobody behind it: it retries once (still under the mutex) and then escalates (rc 3).
  RBA=2; [ "${MK20_RB_CALLER_RETRIES:-0}" = 1 ] && RBA=1; a=0
  while :; do a=$((a+1))
    ( trap - EXIT INT TERM; cleanup ) </dev/null &
    CP=$!
    bounded_wait $CP "$RBT"; bw=$?
    [ $bw = 0 ] || log "rollback cleanup exceeded ${RBT}s -> killing own child tree $CP ($([ $bw = 1 ] && echo killed+reaped || echo UNKILLABLE))"
    [ $bw = 2 ] && stuck "rollback cleanup tree $CP unkillable (D state)" rollback-STUCK   # flapmon: rc 3 = no retry
    rm -f /tmp/mk20/gadget.busy   # always, still under the mutex (a killed cleanup never reached its own rm)
    i=0; while ! stock_ok && [ $i -lt 30 ]; do sleep 1; i=$((i+1)); done   # KD's own setusbconfig reset can lag
    if stock_ok; then log "rollback verified"; rb_status rollback-verified; exit 0; fi
    rb_status rollback-NOT-verified
    log "rollback NOT verified, attempt $a/$RBA (acm=$([ -e $C/acm.usb0 ] && echo y || echo n) udc=$(udc_now) ttyGS0=$([ -L /dev/ttyGS0 ] && echo link || echo dev))"
    [ $a -ge $RBA ] && break
    sleep "$RBRETRY"
  done
  [ $RBA = 1 ] && exit 1   # the caller retries / escalates
  stuck "queued/manual rollback not verified after $RBA attempts" rollback-NOT-verified-ESCALATED
fi
# <<< rollback-block

# ---- preflight (nothing touched yet) ----
UDC=$(ls /sys/class/udc 2>/dev/null | head -1)
[ -n "$UDC" ] || { log "ABORT no udc"; exit 1; }
[ -d $G ] && [ -d $C ] || { log "ABORT no g1 (KD has not configured USB yet)"; exit 1; }
[ "${1:-}" = --rollback-now ] || [ -x $BR ] || { log "ABORT $BR missing: KD would lose ttyGS0 (black screens)"; exit 1; }
[ -x /bin/adbd ] || { log "ABORT no /bin/adbd"; exit 1; }
grep -q '^usb_f_ecm ' /proc/modules || [ -f $ECMKO ] || { log "ABORT no $ECMKO"; exit 1; }
[ -n "${MK20_REL:-}" ] && [ -f "$MK20_REL/share/flapmon.sh" ] || { log "ABORT no flapmon.sh (MK20_REL unset?)"; exit 1; }
supervise_setup() {
# >>> setup-supervisor (r6): the owner holds fd 7 and runs the mutating setup (and its own EXIT failsafe) in a
# child bounded by MK20_SETUP_TIMEOUT. A hung service script / setusbconfig / add-adb can no longer wedge the mutex:
# on timeout the owner kills+reaps ITS OWN setup tree, runs a cleanup bounded by MK20_RB_TIMEOUT (killed+reaped
# too if it hangs), clears gadget.busy and only then releases fd 7 (exit). The child inherits fd 7 on purpose, so
# the mutex also stays held if the owner itself dies while the child is still mutating.
tl_lock "$SUTRIES" || { log "ABORT transition in progress (mutex busy) or lock error"; exit 1; }   # held until exit
trap '' HUP
( setup_main ) </dev/null &
SP=$!
bounded_wait $SP "$SUT"; bw=$?
if [ $bw = 0 ]; then
  src=$BW_RC; [ "$src" = 0 ] && exit 0   # r8 BLOCKER: saved before escalate() can run another bounded_wait
  # setup failed and ran its own failsafe: verify stock, else escalate (flapmon is stopped at this point)
  i=0; while ! stock_ok && [ $i -lt 30 ]; do sleep 1; i=$((i+1)); done
  stock_ok || escalate "setup failed (rc $src) and its failsafe did not reach stock" setup-failsafe-NOT-verified
  rm -f /tmp/mk20/gadget.busy; exit "$src"
fi
[ $bw = 2 ] && stuck "setup tree $SP unkillable after ${SUT}s (D state)" setup-STUCK
log "SETUP TIMEOUT: exceeded ${SUT}s -> killed+reaped own setup tree $SP; bounded cleanup"
( trap - EXIT INT TERM; OK=0; cleanup ) </dev/null &
SC=$!
bounded_wait $SC "$RBT"; bw=$?
[ $bw = 0 ] || log "cleanup after setup timeout exceeded ${RBT}s -> killed+reaped own cleanup tree $SC"
[ $bw = 2 ] && stuck "cleanup tree $SC unkillable (D state)" setup-STUCK
rm -f /tmp/mk20/gadget.busy
i=0; while ! stock_ok && [ $i -lt 30 ]; do sleep 1; i=$((i+1)); done
if stock_ok; then rb_status setup-timeout; else escalate "setup timeout and cleanup did not reach stock" setup-timeout-NOT-verified; fi
exit 1
# <<< setup-supervisor
}
setup_main() {
GEN="$(cut -d' ' -f1 /proc/uptime)-$$"; mkdir -p /tmp/mk20; echo "$GEN" > "$GENF.t" && mv -f "$GENF.t" "$GENF"   # under the mutex (r5 #7)
[ "$(cat "$GENF" 2>/dev/null)" = "$GEN" ] || { log "FAIL cannot publish generation $GEN (nothing mutated)"; exit 1; }   # GPT r7
touch /tmp/mk20-flapmon-stop   # r8 #5: only after the publish: a re-run: any old flap monitor / confirm waiter exits
log "BEGIN udc=$UDC kdsvc=${KDSVC:-none}"

trap cleanup EXIT
trap 'exit 1' INT TERM
trap '' HUP
die() { log "FAIL $*"; exit 1; }

# ---- stop KD (via procd so it is not respawned mid-change) and adbd ----
kd_stop; killall adbd 2>/dev/null; sleep 1
pgrep -f /data/KeyboardDevice >/dev/null && die "KD still running/respawned after stop"
# ---- unbind + clean stale links ----
router_stop   # 1.2.0: no open /dev/hidg0 across unbind/unlink (re-setup over a live composite)
kbd_stop || die "mk20-kbd not stopped (no open /dev/hidg0 across unbind/unlink, no KD beside it)"   # mk20d boot clears markers after the transition
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
# agent A1 (opt-in, default off -> descriptor byte-identical to 1.2.x): MK20_HID_SCROLL=1 appends Wheel (0x38) and
# AC Pan (0x0C/0x238) as signed bytes to pointer report 2 (1+7 = 8 B, still <= report_length 9). mk20-kbd MUST then
# run with --hid-scroll (8-byte pointer reports); release uses the same resolved FEATURE_HID_SCROLL.
if [ "${MK20_HID_SCROLL:-0}" = 1 ]; then
printf '\x05\x01\x09\x06\xa1\x01\x85\x01\x05\x07\x19\xe0\x29\xe7\x15\x00\x25\x01\x75\x01\x95\x08\x81\x02\x95\x01\x75\x08\x81\x03\x95\x05\x75\x01\x05\x08\x19\x01\x29\x05\x91\x02\x95\x01\x75\x03\x91\x03\x95\x06\x75\x08\x15\x00\x25\x65\x05\x07\x19\x00\x29\x65\x81\x00\xc0\x05\x01\x09\x02\xa1\x01\x85\x02\x09\x01\xa1\x00\x05\x09\x19\x01\x29\x03\x15\x00\x25\x01\x95\x03\x75\x01\x81\x02\x95\x01\x75\x05\x81\x03\x05\x01\x09\x30\x09\x31\x16\x00\x00\x26\xff\x7f\x75\x10\x95\x02\x81\x02\x09\x38\x15\x81\x25\x7f\x75\x08\x95\x01\x81\x06\x05\x0c\x0a\x38\x02\x15\x81\x25\x7f\x75\x08\x95\x01\x81\x06\xc0\xc0' > /tmp/hid.desc
echo 1 > /tmp/mk20-hid-scroll 2>/dev/null
else
printf '\x05\x01\x09\x06\xa1\x01\x85\x01\x05\x07\x19\xe0\x29\xe7\x15\x00\x25\x01\x75\x01\x95\x08\x81\x02\x95\x01\x75\x08\x81\x03\x95\x05\x75\x01\x05\x08\x19\x01\x29\x05\x91\x02\x95\x01\x75\x03\x91\x03\x95\x06\x75\x08\x15\x00\x25\x65\x05\x07\x19\x00\x29\x65\x81\x00\xc0\x05\x01\x09\x02\xa1\x01\x85\x02\x09\x01\xa1\x00\x05\x09\x19\x01\x29\x03\x15\x00\x25\x01\x95\x03\x75\x01\x81\x02\x95\x01\x75\x05\x81\x03\x05\x01\x09\x30\x09\x31\x16\x00\x00\x26\xff\x7f\x75\x10\x95\x02\x81\x02\xc0\xc0' > /tmp/hid.desc
rm -f /tmp/mk20-hid-scroll 2>/dev/null
fi
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
/bin/adbd </dev/null >>/tmp/adbd.log 2>&1 7>&- &   # O_APPEND: mk20d maint caps it (debug adbd logs every command)
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
# 1.2.0: KD->host copy for the on-board router (release bridge only; a legacy /data bridge has no --tap)
# mk20-kbd mode (MK20_KBD=1 from mk20d): no KD output to tap, mk20-kbd owns ttyGS0 and hidg0 itself
[ -n "${MK20_REL:-}" ] && [ "${MK20_KBD:-0}" != 1 ] && BRIDGE_LISTEN="$BRIDGE_LISTEN --tap /tmp/mk20/kd-tap.sock"
rm -f /tmp/mk20-bridge-stop
# ---- PTY bridge for mk20-kbd; stock binaries/scripts remain untouched ----
mkdir -p /tmp/mk20
[ -e /dev/ttyGS0 ] && [ ! -L /dev/ttyGS0 ] && mv /dev/ttyGS0 /dev/ttyGS0.real
( trap '' HUP INT; exec sh -c "echo \$\$ > /tmp/mk20-bridge-loop.pid; while [ ! -f /tmp/mk20-bridge-stop ]; do $BR $BRIDGE_LISTEN --link /dev/ttyGS0; sleep 1; done" </dev/null >>/tmp/mk20/bridge.log 2>&1 7>&- ) &   # supervised (tmpfs log: an open FAT file blocks hardreset's remount ro)
i=0; while [ ! -e /dev/ttyGS0 ] && [ $i -lt 25 ]; do sleep 0.2; i=$((i+1)); done
[ -e /dev/ttyGS0 ] || die "bridge did not create /dev/ttyGS0"
# test hook (one-shot): prove setup failsafe with the bridge ready and KD stopped
if [ -f /mnt/SDCARD/mk20-composite-failtest ]; then rm -f /mnt/SDCARD/mk20-composite-failtest; sync; die "FAILTEST: forced failure (test hook)"; fi
rm -f /mnt/SDCARD/mk20-composite-ok
OK=1
log "OK mode=$MODE UDC=$(udc_now) links=$(ls $C | tr '\n' ' ') hidg0 usb0=$ECM_IP bridge=[$BRIDGE_LISTEN] kd=$(pgrep -f KeyboardDevice | head -1) adbd=$(pidof adbd)"
# host must confirm (touch /mnt/SDCARD/mk20-composite-ok via adb or mk20ctl over socket://169.254.7.2:17890)
# within 180 s, otherwise request loader A/B rollback. Detached waiter (no setsid here).
ME=$(readlink -f "$0"); cp -f "$ME" /tmp/mk20-gadget.sh.new && [ "$(sha256sum < "$ME")" = "$(sha256sum < /tmp/mk20-gadget.sh.new)" ] &&
  mv -f /tmp/mk20-gadget.sh.new /tmp/mk20-gadget.sh || { OK=0; die "cannot stage rollback copy"; }   # atomic: a running rollback keeps its file
# flap monitor (both modes, share/flapmon.sh): >=5 configured->not-configured drops within 60 s -> rollback to
# loader A/B via cmd-rollback. Host USB suspend (Mac sleep) is not a flap.
rm -f /tmp/mk20-flapmon-stop /tmp/mk20/flapmon.ready
FMSRC=${MK20_REL:+$MK20_REL/share/flapmon.sh}
{ [ -n "$FMSRC" ] && cp -f "$FMSRC" /tmp/mk20-flapmon.sh.new && mv -f /tmp/mk20-flapmon.sh.new /tmp/mk20-flapmon.sh; } || { OK=0; die "no flapmon.sh in release"; }
( trap '' HUP INT; export MK20_REL="${MK20_REL:-}" MK20_GADGET_LOG="$LOG" MK20_GEN="$GEN" MK20_RB_CALLER_RETRIES=1
  exec sh /tmp/mk20-flapmon.sh "/sys/class/udc/$UDC/state" "$FLAP_WINDOW" "$FLAP_DROPS" "$LOG" /tmp/mk20-flapmon-stop -- \
    sh /tmp/mk20-gadget.sh --rollback-now </dev/null >/dev/null 2>&1 7>&- 9>&- ) &
FP=$!; i=0; while [ ! -f /tmp/mk20/flapmon.ready ] && [ $i -lt 40 ] && kill -0 $FP 2>/dev/null; do sleep 0.25; i=$((i+1)); done
[ -f /tmp/mk20/flapmon.ready ] && kill -0 $FP 2>/dev/null || { OK=0; die "flap monitor did not start (no lock/ready marker)"; }
log "flap monitor started (pid $FP)"
if [ "$MODE" = --boot ]; then
  touch /mnt/SDCARD/mk20-composite-ok; echo "gadget-ok-boot" > /mnt/SDCARD/gadget-ecm-hid.status; exit 0
fi
( trap '' HUP INT; exec sh -c "i=0; while [ \$i -lt 90 ]; do [ -f /mnt/SDCARD/mk20-composite-ok ] && exit 0; [ -f /tmp/mk20-flapmon-stop ] && exit 0; sleep 2; i=\$((i+1)); done; MK20_GEN=$GEN MK20_RB_CALLER_RETRIES=1 MK20_REL=${MK20_REL:-} MK20_GADGET_LOG=$LOG sh /tmp/mk20-gadget.sh --rollback-now; rc=\$?; [ \$rc = 0 ] && exit 0; [ \$rc = 3 ] && exit 3; [ \$rc = 5 ] && exit 0; sleep 20; MK20_GEN=$GEN MK20_RB_CALLER_RETRIES=1 MK20_REL=${MK20_REL:-} MK20_GADGET_LOG=$LOG sh /tmp/mk20-gadget.sh --rollback-now; rc=\$?; [ \$rc = 0 ] || [ \$rc = 5 ] && exit 0; touch /mnt/SDCARD/mk20-no-composite && touch /mnt/SDCARD/cmd-reboot; sync" </dev/null >/dev/null 2>&1 7>&- 9>&- ) &
WP=$!; sleep 1; kill -0 $WP 2>/dev/null || [ -f /mnt/SDCARD/mk20-composite-ok ] || { OK=0; die "confirm waiter did not start -> rollback now"; }
log "confirm window 180s started (pid $WP)"
echo "gadget-ok" > /mnt/SDCARD/gadget-ecm-hid.status
exit 0
}
supervise_setup
