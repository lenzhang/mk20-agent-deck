#!/bin/sh
# MK20 remote HARD reset (soft `reboot` hangs on this board).
# Why reboot hangs: ARM machine_restart() uses arm_pm_restart = psci_sys_reset (PSCI SYSTEM_RESET to the
# secure firmware), which never returns here. Before that, the kernel's reboot notifiers stop the sunxi
# WDT (watchdog_reboot_notifier / sunxi_wdt_shutdown), so nothing recovers the hung CPU.
# Method: procd keeps the sunxi WDT armed (ubus: timeout 16 s, ping every 5 s). We sync and tell procd to
# stop pinging -> the hardware WDT resets the SoC within ~16 s. No kernel restart path, no PSCI.
# Fallback after 30 s: sysrq s,u,b (emergency_restart skips the notifiers, so the still-armed WDT fires
# even if PSCI hangs).
L=/mnt/SDCARD/mk20/logs/hardreset.log
log() { echo "hardreset $(cut -d' ' -f1 /proc/uptime): $*" >> $L; sync; }
log "begin by ${1:-?}"
W=$(ubus call system watchdog 2>&1 | tr -d '\n\t '); log "wdt before: $W"
case "$W" in *'"status":"running"'*) ;; *) log "WARNING: procd watchdog not running; going straight to sysrq";; esac
sync; sync
case "$W" in *'"status":"running"'*) ubus call system watchdog '{"stop":true}' >/dev/null 2>&1; log "procd WDT pings stopped; expect reset in <=16 s";; esac
sync
sleep 30
log "still alive after 30 s -> sysrq fallback"
echo 1 > /proc/sys/kernel/sysrq
echo s > /proc/sysrq-trigger; sleep 2; echo u > /proc/sysrq-trigger; sleep 1; echo b > /proc/sysrq-trigger
sleep 30; log "sysrq b returned?!"
