#!/bin/sh
# Add FunctionFS adb to the already-built ACM gadget, keeping acm.usb0 (ttyGS0 stays).
# Returns 0 only if the UDC rebinds. No /rom, flash, or swupdate.
LOG=${LOG:-/mnt/SDCARD/mk20-gadget.log}
G=/sys/kernel/config/usb_gadget/g1
log() { echo "add-adb: $*" >> "$LOG"; }

[ -d "$G" ] || { log "no g1 gadget"; return 1 2>/dev/null || exit 1; }
UDC_NAME=$(ls /sys/class/udc 2>/dev/null | head -n1)
[ -n "$UDC_NAME" ] || { log "no udc"; exit 1; }

# If adb already linked and bound, nothing to do
if [ -e "$G/configs/c.1/ffs.adb" ] && [ -n "$(cat "$G/UDC" 2>/dev/null)" ]; then
  log "ffs.adb already present and bound"
  exit 0
fi

# Unbind (required to change config links)
echo "" > "$G/UDC" 2>/dev/null || true
sleep 0.3

# Create + mount FunctionFS for adb
mkdir -p "$G/functions/ffs.adb"
mkdir -p /dev/usb-ffs/adb
if ! grep -q " /dev/usb-ffs/adb " /proc/mounts 2>/dev/null; then
  mount -o uid=2000,gid=2000 -t functionfs adb /dev/usb-ffs/adb 2>>"$LOG" \
    || mount -t functionfs adb /dev/usb-ffs/adb 2>>"$LOG" \
    || { log "functionfs mount failed"; }
fi

# Link adb into the config (keep acm.usb0 link intact)
[ -e "$G/configs/c.1/ffs.adb" ] || ln -s "$G/functions/ffs.adb" "$G/configs/c.1/ffs.adb"

# Product string hint
echo "Serial,ADB" > "$G/strings/0x409/product" 2>/dev/null || true

# adbd must open the functionfs ep0 and write descriptors BEFORE we bind
killall adbd 2>/dev/null || true
sleep 0.3
/bin/adbd -D >> "$LOG" 2>&1 &
sleep 1.5

# Bind UDC
echo "$UDC_NAME" > "$G/UDC" 2>>"$LOG"
sleep 0.5
BOUND=$(cat "$G/UDC" 2>/dev/null)
if [ -z "$BOUND" ]; then
  log "REBIND FAILED (UDC empty)"
  exit 2
fi
log "rebound UDC=$BOUND links: $(ls "$G/configs/c.1" 2>/dev/null | tr '\n' ' ')"
exit 0
