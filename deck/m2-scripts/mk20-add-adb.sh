#!/bin/sh
# mk20-add-adb v2: add FunctionFS adb beside the existing ACM (KD keeps ttyGS0).
# Root cause of v1 failures (2026-10-18): adbd binds the UDC itself (set_udc); our extra
# `echo UDC` got EBUSY and was treated as FAIL -> restore. Also ffs.adb must be linked in c.1.
# Sequence: kill adbd -> unbind UDC -> link ffs.adb -> mount functionfs -> start adbd (binds)
#           -> only if UDC still empty after eps appear, bind it ourselves (EBUSY ignored).
LOG=${LOG:-/mnt/SDCARD/mk20/logs/adb-helper.log}
G=/sys/kernel/config/usb_gadget/g1
log() { echo "add-adb $(cut -d' ' -f1 /proc/uptime): $*" >> "$LOG"; }
udc() { cat "$G/UDC" 2>/dev/null; }
adbd_up() { pidof adbd >/dev/null 2>&1 || pgrep adbd >/dev/null 2>&1; }
# NEVER run this from an adb shell in the foreground: killing adbd kills the caller mid-way
# (UDC left unbound). From adb: queue it to the watcher (job.sh) - adbd kills the session process group; no setsid here.
[ -d "$G" ] || { log "FAIL no g1"; exit 1; }
UDC_NAME=$(ls /sys/class/udc 2>/dev/null | head -n1); [ -n "$UDC_NAME" ] || { log "FAIL no udc"; exit 1; }

if [ -e "$G/configs/c.1/ffs.adb" ] && [ -n "$(udc)" ] && adbd_up; then
  log "already composite (links: $(ls $G/configs/c.1 | tr '\n' ' ')) adbd=$(pidof adbd); ok"; exit 0
fi
log "BEGIN udc=$(udc) links=$(ls $G/configs/c.1 2>/dev/null | tr '\n' ' ')"
killall adbd 2>/dev/null; sleep 1
echo "" > "$G/UDC" 2>/dev/null
i=0; while [ -n "$(udc)" ] && [ $i -lt 20 ]; do echo "" > "$G/UDC" 2>/dev/null; usleep 200000; i=$((i+1)); done
[ -z "$(udc)" ] || { log "FAIL UDC still bound: $(udc)"; exit 6; }
mkdir -p "$G/functions/ffs.adb" /dev/usb-ffs/adb
[ -e "$G/configs/c.1/ffs.adb" ] || ln -s "$G/functions/ffs.adb" "$G/configs/c.1/ffs.adb" 2>>"$LOG" || { log "FAIL ln"; exit 7; }
grep -q " /dev/usb-ffs/adb " /proc/mounts || mount -t functionfs adb /dev/usb-ffs/adb 2>>"$LOG" || { log "FAIL ffs mount"; exit 3; }
[ -e "$G/configs/c.1/acm.usb0" ] || log "WARN acm.usb0 not linked"
/bin/adbd </dev/null >/tmp/adbd.log 2>&1 &
i=0; while [ $i -lt 40 ]; do [ -n "$(udc)" ] && break; usleep 200000; i=$((i+1)); done
if [ -z "$(udc)" ]; then
  if [ -e /dev/usb-ffs/adb/ep1 ]; then echo "$UDC_NAME" > "$G/UDC" 2>>"$LOG"; sleep 1; fi
fi
[ "$(udc)" = "$UDC_NAME" ] || { log "FAIL UDC not bound after adbd (got '$(udc)')"; exit 2; }
adbd_up || { log "FAIL adbd died"; exit 9; }
log "OK UDC=$(udc) links=$(ls $G/configs/c.1 | tr '\n' ' ') adbd=$(pidof adbd) eps=$(ls /dev/usb-ffs/adb | tr '\n' ' ')"
exit 0
