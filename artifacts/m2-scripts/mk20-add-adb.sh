#!/bin/sh
# Add FunctionFS adb beside existing ACM.
# Order: verify unbind → mount ffs → link → adbd → wait ep1/ep2 → bind UDC
LOG=${LOG:-/mnt/SDCARD/mk20-gadget.log}
G=/sys/kernel/config/usb_gadget/g1
log() { echo "add-adb $(date 2>/dev/null || echo nodate): $*" >> "$LOG"; }

[ -d "$G" ] || { log "FAIL no g1"; exit 1; }
UDC_NAME=$(ls /sys/class/udc 2>/dev/null | head -n1)
[ -n "$UDC_NAME" ] || { log "FAIL no udc"; exit 1; }

if [ -e "$G/configs/c.1/ffs.adb" ] && [ -n "$(cat "$G/UDC" 2>/dev/null)" ] && pgrep -x adbd >/dev/null \
   && [ -e /dev/usb-ffs/adb/ep1 ]; then
  log "already composite+adbd+eps; ok"
  exit 0
fi

log "BEGIN udc=$UDC_NAME links=$(ls "$G/configs/c.1" 2>/dev/null | tr '\n' ' ')"

# Unbind — must actually clear UDC before changing links
echo "" > "$G/UDC" 2>>"$LOG" || true
sleep 0.5
i=0
while [ -n "$(cat "$G/UDC" 2>/dev/null)" ]; do
  i=$((i + 1))
  [ "$i" -gt 20 ] && { log "FAIL UDC still bound after unbind: $(cat "$G/UDC")"; exit 6; }
  echo "" > "$G/UDC" 2>/dev/null || true
  sleep 0.2
done
log "UDC unbound"

mkdir -p "$G/functions/ffs.adb"
mkdir -p /dev/usb-ffs/adb
if ! grep -q " /dev/usb-ffs/adb " /proc/mounts 2>/dev/null; then
  mount -o uid=2000,gid=2000 -t functionfs adb /dev/usb-ffs/adb 2>>"$LOG" \
    || mount -t functionfs adb /dev/usb-ffs/adb 2>>"$LOG" \
    || { log "FAIL functionfs mount"; exit 3; }
fi

if [ ! -e "$G/configs/c.1/ffs.adb" ]; then
  ln -s "$G/functions/ffs.adb" "$G/configs/c.1/ffs.adb" 2>>"$LOG" \
    || { log "FAIL ln ffs.adb"; exit 7; }
fi
[ -e "$G/configs/c.1/ffs.adb" ] || { log "FAIL ffs.adb link missing"; exit 7; }
# ACM should still be linked (we do not remove it)
[ -e "$G/configs/c.1/acm.usb0" ] || log "WARN acm.usb0 link missing (will still try adb)"

echo "Serial,ADB" > "$G/strings/0x409/product" 2>/dev/null || true

killall adbd 2>/dev/null || true
sleep 0.3
/bin/adbd -D >> "$LOG" 2>&1 &
ADB_PID=$!
log "adbd started pid=$ADB_PID"

i=0
while [ "$i" -lt 50 ]; do
  if [ -e /dev/usb-ffs/adb/ep1 ] && [ -e /dev/usb-ffs/adb/ep2 ]; then
    log "ffs eps ready i=$i ($(ls /dev/usb-ffs/adb 2>/dev/null | tr '\n' ' '))"
    break
  fi
  if ! kill -0 "$ADB_PID" 2>/dev/null && ! pgrep -x adbd >/dev/null; then
    log "FAIL adbd exited before eps"
    exit 4
  fi
  i=$((i + 1)); sleep 0.2
done
[ -e /dev/usb-ffs/adb/ep1 ] || { log "FAIL timeout ffs eps"; ls -la /dev/usb-ffs/adb >>"$LOG" 2>&1; exit 5; }

echo "$UDC_NAME" > "$G/UDC" 2>>"$LOG" || { log "FAIL echo UDC"; exit 2; }
sleep 0.8
BOUND=$(cat "$G/UDC" 2>/dev/null)
[ "$BOUND" = "$UDC_NAME" ] || { log "FAIL rebound mismatch got='$BOUND' want='$UDC_NAME'"; exit 2; }
[ -e "$G/configs/c.1/ffs.adb" ] || { log "FAIL ffs.adb disappeared after bind"; exit 8; }
pgrep -x adbd >/dev/null || { log "FAIL adbd dead after bind"; exit 9; }

log "OK rebound UDC=$BOUND links=$(ls "$G/configs/c.1" 2>/dev/null | tr '\n' ' ') adbd=$(pgrep -x adbd)"
cat /sys/devices/platform/soc@3000000/soc@3000000:usbc0@0/usb_device >> "$LOG" 2>/dev/null || true
exit 0
