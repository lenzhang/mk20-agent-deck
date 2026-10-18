#!/bin/sh
# MK20 remote HARD reset v2.1 (soft `reboot` hangs on this board).
# Why reboot hangs: ARM machine_restart() uses arm_pm_restart = psci_sys_reset (PSCI SYSTEM_RESET), which never
# returns here, and the reboot notifiers stop the sunxi WDT first. So: procd keeps the sunxi WDT armed (16 s,
# ping 5 s); we stop procd's pings -> the hardware WDT resets the SoC. No kernel restart path, no PSCI.
# v2 (production plan M0.5):
#  - refuses (exit 2, "needs power-cycle") if procd's watchdog is not armed: sysrq-b would hit the same PSCI hang
#  - waits for the kernel-write lock (bootswap/postswap dd of p4) so a reset never tears a boot-partition write
#  - flushes and remounts the FAT read-only before the pings stop (every reset is otherwise an unclean FAT stop)
# v2.1: busybox 1.27 flock has NO -w (v2 `flock -w` errored -> false "lock held" refusal -> the old v2 watcher
#   fell back to `reboot` -> PSCI hang, power-cycle needed, 16:37). Now: poll `flock -n`. remount ro is bounded
#   (backgrounded, 5 s) so it can never block the reset.
# Env for tests: MK20_ROOT (path prefix), MK20_HR_NOSLEEP=1.
R=${MK20_ROOT:-}
S=$R/mnt/SDCARD; L=$S/mk20/logs/hardreset.log; LOCK=$R/tmp/mk20/kwrite.lock
mkdir -p "$S/mk20/logs" "$R/tmp/mk20" 2>/dev/null
log() { echo "hardreset $(cut -d' ' -f1 /proc/uptime): $*" >> "$L" 2>/dev/null; sync; }
log "begin by ${1:-?}"
W=$(ubus call system watchdog 2>&1 | tr -d '\n\t '); log "wdt: $W"
case "$W" in *'"status":"running"'*) ;; *) log "REFUSED: procd watchdog not armed -> remote reset impossible, needs power-cycle"; exit 2;; esac
# kernel-write lock: bootswap/postswap hold it (fd 8) while writing p4
exec 8>"$LOCK"
i=0; while ! flock -n 8; do i=$((i+1)); [ $i -gt 600 ] && { log "REFUSED: kernel-write lock held >600 s (p4 write in progress?)"; exit 3; }
  [ $i = 1 ] && log "waiting for kernel-write lock"; sleep 1; done
log "lock ok; stopping writers, remount FAT ro, stop WDT pings"
sync
rm -f "$R/tmp/mk20/hr-ro"; ( mount -o remount,ro "$S" 2>/dev/null && touch "$R/tmp/mk20/hr-ro" ) & mp=$!
i=0; while kill -0 $mp 2>/dev/null && [ $i -lt 10 ]; do sleep 0.5; i=$((i+1)); done
[ -f "$R/tmp/mk20/hr-ro" ] && RO=y || RO=n
sync
ubus call system watchdog '{"stop":true}' >/dev/null 2>&1
# FAT may be ro now: log to tmpfs (and FAT if still rw)
echo "hardreset: pings stopped (fat_ro=$RO), expect reset <=16 s" > "$R/tmp/mk20/hardreset.last"
[ "$RO" = n ] && log "pings stopped (FAT remount ro failed, synced); expect reset <=16 s"
[ "${MK20_HR_NOSLEEP:-}" = 1 ] && exit 0
sleep 40
# Still alive: the WDT did not fire (should not happen with status=running). Re-enable pings so the board keeps
# working, put FAT back rw, report. No sysrq-b (same PSCI hang).
ubus call system watchdog '{"stop":false}' >/dev/null 2>&1
mount -o remount,rw "$S" 2>/dev/null
log "ERROR: still alive 40 s after stopping pings; pings re-enabled, FAT rw again; needs power-cycle"
exit 4
