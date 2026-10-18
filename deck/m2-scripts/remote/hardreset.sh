#!/bin/sh
# MK20 remote HARD reset v2.3 (v2.3: outcome persisted to /data/mk20/state/hardreset.last) (soft `reboot` hangs on this board).
# Why reboot hangs: ARM machine_restart() uses arm_pm_restart = psci_sys_reset (PSCI SYSTEM_RESET), which never
# returns here, and the reboot notifiers stop the sunxi WDT first. So: procd keeps the sunxi WDT armed (16 s,
# ping 5 s); we stop procd's pings -> the hardware WDT resets the SoC. No kernel restart path, no PSCI.
# v2 (production plan M0.5):
#  - refuses (exit 10, "needs power-cycle") if procd's watchdog is not armed: sysrq-b would hit the same PSCI hang
#  - waits for the kernel-write lock (bootswap/postswap dd of p4) so a reset never tears a boot-partition write
#  - flushes and remounts the FAT read-only before the pings stop (every reset is otherwise an unclean FAT stop)
# v2.1: busybox 1.27 flock has NO -w (v2 `flock -w` errored -> false "lock held" refusal -> the old v2 watcher
#   fell back to `reboot` -> PSCI hang, power-cycle needed, 16:37). Now: poll `flock -n`. remount ro is bounded
#   (backgrounded, 5 s) so it can never block the reset.
# v2.2 (M0.5 review): fd 9 (watcher lock) closed at entry; the remount helper does not inherit fd 8 and is killed
#   if it hangs (then no RW remount is attempted while it may still complete).
# Env for tests: MK20_ROOT (path prefix), MK20_HR_NOSLEEP=1.
R=${MK20_ROOT:-}
UBUS=${MK20_UBUS:-ubus}
S=$R/mnt/SDCARD; L=$S/mk20/logs/hardreset.log; LOCK=$R/tmp/mk20/kwrite.lock
mkdir -p "$S/mk20/logs" "$R/tmp/mk20" 2>/dev/null
log() { echo "hardreset $(cut -d' ' -f1 /proc/uptime): $*" >> "$L" 2>/dev/null; sync; }
exec 9>&- </dev/null >/dev/null 2>&1   # no inherited watcher lock; no FAT stdout (would block remount ro)
log "begin by ${1:-?}"
[ -n "$R" ] && [ -z "${MK20_UBUS:-}" ] && { log "REFUSED: test root without MK20_UBUS (would stop the real watchdog)"; exit 13; }
W=$($UBUS call system watchdog 2>&1 | tr -d '\n\t '); log "wdt: $W"
wdt_ok() { case "$1" in *'"status":"running"'*) ;; *) return 1;; esac; case "$1" in *'"magicclose":true'*) return 1;; esac
  t=$(echo "$1" | sed -n 's/.*"timeout":\([0-9]*\).*/\1/p'); [ -n "$t" ] && [ "$t" -lt 40 ]; }
wdt_ok "$W" || { log "REFUSED: procd watchdog not armed/usable -> remote reset impossible, needs power-cycle"; exit 10; }
# kernel-write lock: bootswap/postswap hold it (fd 8) while writing p4
exec 8>"$LOCK"
i=0; while ! flock -n 8; do i=$((i+1)); [ $i -gt 600 ] && { log "REFUSED: kernel-write lock held >600 s (p4 write in progress?)"; exit 11; }
  [ $i = 1 ] && log "waiting for kernel-write lock"; sleep 1; done
W=$($UBUS call system watchdog 2>&1 | tr -d '\n\t '); wdt_ok "$W" || { log "REFUSED: watchdog changed while waiting: $W"; exit 10; }
log "lock ok; stopping writers, remount FAT ro, stop WDT pings"
# guarantee: pings stop within 12 s even if sync/remount wedge on the card (keeps fd 8 = lock held until reset)
( exec 9>&-; sleep 12; $UBUS call system watchdog '{"stop":true}' ) & TP=$!
# diagnostics: who still holds files on the FAT (would make remount ro EBUSY)
for f in "$R"/proc/[0-9]*/fd/*; do l=$(readlink "$f" 2>/dev/null); case "$l" in "$S"/*) echo "${f#"$R"/proc/} $l";; esac; done > "$R/tmp/mk20/hr-fatfds" 2>/dev/null
sync
rm -f "$R/tmp/mk20/hr-ro"; ( exec 8>&-; mount -o remount,ro "$S" 2>/dev/null && touch "$R/tmp/mk20/hr-ro" ) & mp=$!
i=0; while kill -0 $mp 2>/dev/null && [ $i -lt 10 ]; do sleep 0.5; i=$((i+1)); done
MHUNG=n; kill -0 $mp 2>/dev/null && { kill -9 $mp 2>/dev/null; MHUNG=y; }
[ -f "$R/tmp/mk20/hr-ro" ] && RO=y || RO=n
# persistent outcome on ext4 (FAT may be ro now): read after the reset to prove a clean FAT remount
[ -d "$R/data/mk20/state" ] && echo "uptime=$(cut -d' ' -f1 /proc/uptime) by=${1:-?} fat_ro=$RO mount_hung=$MHUNG" > "$R/data/mk20/state/hardreset.last" 2>/dev/null
sync
$UBUS call system watchdog '{"stop":true}' >/dev/null 2>&1
# FAT may be ro now: log to tmpfs (and FAT if still rw)
echo "hardreset: pings stopped (fat_ro=$RO), expect reset <=16 s" > "$R/tmp/mk20/hardreset.last"
[ "$RO" = n ] && log "pings stopped (FAT remount ro failed, synced); expect reset <=16 s"
[ "${MK20_HR_NOSLEEP:-}" = 1 ] && { kill $TP 2>/dev/null; exit 0; }
sleep 40
# Still alive: the WDT did not fire (should not happen with status=running). Re-enable pings so the board keeps
# working, put FAT back rw, report. No sysrq-b (same PSCI hang).
kill $TP 2>/dev/null; $UBUS call system watchdog '{"stop":false}' >/dev/null 2>&1
[ "$RO" = y ] && [ "$MHUNG" = n ] && { ( exec 8>&-; mount -o remount,rw "$S" 2>/dev/null ) & rp=$!; sleep 3; kill -9 $rp 2>/dev/null; }
log "ERROR: still alive 40 s after stopping pings; pings re-enabled, FAT rw again; needs power-cycle"
exit 12
