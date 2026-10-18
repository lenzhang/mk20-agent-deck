#!/bin/sh
# In-place boot partition swap via lunch.sh (root, one-shot).
# Layout (GPT review fix): fat SDCARD is only 8MiB — cannot hold BOTH new+stock.
#   - Stage NEW on UDISK:  /mnt/UDISK/boot-new.img (+ .sha256)
#   - Backup STOCK on FAT: /mnt/SDCARD/boot-stock.img  (full partition, Mac-readable)
#   - Mirror stock to UDISK as well
#   - lunch.sh itself lives on SDCARD (small)
#
# Arming (host saveToFile):
#   /mnt/UDISK/boot-new.img
#   /mnt/UDISK/boot-new.img.sha256   # "hex  boot-new.img"
#   /mnt/SDCARD/lunch.sh             # this script
# Optional: /mnt/SDCARD/boot-swap.dry
# Kill:     /mnt/SDCARD/mk20-no-bootswap
# Fail:     /mnt/SDCARD/boot-swap-fail  → restore stock from SDCARD + reboot

LOG=/mnt/SDCARD/boot-swap.log
DONE=/mnt/SDCARD/boot-swap.done
STATUS=/mnt/SDCARD/boot-swap-status.txt
STOCK=/mnt/SDCARD/boot-stock.img
STOCKUD=/mnt/UDISK/boot-stock.img
NEW=/mnt/UDISK/boot-new.img
NEWSHA=/mnt/UDISK/boot-new.img.sha256
# Compat: also accept new image staged on SDCARD if it fits alone (dry/debug)
if [ ! -f "$NEW" ] && [ -f /mnt/SDCARD/boot-new.img ]; then
  NEW=/mnt/SDCARD/boot-new.img
  NEWSHA=/mnt/SDCARD/boot-new.img.sha256
fi
FAILFLAG=/mnt/SDCARD/boot-swap-fail
ARMED=/mnt/SDCARD/boot-swap.armed

log() { echo "$(date -Iseconds 2>/dev/null || date) $*" | tee -a "$LOG"; }

resolve_bootdev() {
  if [ -e /dev/by-name/boot ]; then echo /dev/by-name/boot; return; fi
  if [ -L /dev/by-name/boot ]; then readlink -f /dev/by-name/boot; return; fi
  echo ""
}

# --- self-fallback: restore stock from FAT ---
if [ -f "$FAILFLAG" ] && [ -f "$STOCK" ]; then
  log "FAILFLAG set — restoring stock boot from SDCARD"
  BOOTDEV=$(resolve_bootdev)
  if [ -z "$BOOTDEV" ]; then log "ERROR: no bootdev"; echo "no-bootdev-fail" > "$STATUS"; exit 1; fi
  STOCKSZ=$(wc -c < "$STOCK")
  PARTSIZE=$(blockdev --getsize64 "$BOOTDEV" 2>/dev/null || echo 0)
  if [ "$PARTSIZE" -le 0 ] || [ "$STOCKSZ" -ne "$PARTSIZE" ]; then
    log "ERROR: stock size $STOCKSZ != PARTSIZE $PARTSIZE — refuse restore"
    echo "stock-size-mismatch" > "$STATUS"; exit 1
  fi
  dd if="$STOCK" of="$BOOTDEV" bs=1M conv=fsync 2>>"$LOG"
  sync
  rm -f "$FAILFLAG" "$ARMED"
  log "stock restored; rebooting"
  sleep 1; reboot -f 2>/dev/null || reboot; exit 0
fi

if [ -f "$DONE" ]; then log "already done"; exit 0; fi
if [ -f /mnt/SDCARD/mk20-no-bootswap ]; then log "kill switch"; exit 0; fi

# One-shot: remove lunch early
if [ -f /mnt/SDCARD/lunch.sh ]; then
  cp /mnt/SDCARD/lunch.sh /mnt/SDCARD/lunch.sh.boot-swap.bak 2>/dev/null
  rm -f /mnt/SDCARD/lunch.sh
  log "removed lunch.sh (one-shot)"
fi

if [ ! -f "$NEW" ] || [ ! -f "$NEWSHA" ]; then
  log "ERROR: missing $NEW or $NEWSHA (stage on UDISK)"
  echo "missing-new" > "$STATUS"; exit 1
fi

BOOTDEV=$(resolve_bootdev)
if [ -z "$BOOTDEV" ]; then
  log "ERROR: /dev/by-name/boot missing — refuse to guess"
  echo "no-bootdev" > "$STATUS"; exit 1
fi
log "BOOTDEV=$BOOTDEV NEW=$NEW"
ls -l /dev/by-name/ >>"$LOG" 2>&1
cat /proc/partitions >>"$LOG" 2>&1

NEWSIZE=$(wc -c < "$NEW")
PARTSIZE=$(blockdev --getsize64 "$BOOTDEV" 2>/dev/null || echo 0)
log "NEWSIZE=$NEWSIZE PARTSIZE=$PARTSIZE"
if [ "$PARTSIZE" -le 0 ]; then
  log "ERROR: could not read PARTSIZE"
  echo "no-partsize" > "$STATUS"; exit 1
fi
if [ "$NEWSIZE" -le 0 ] || [ "$NEWSIZE" -gt "$PARTSIZE" ]; then
  log "ERROR: NEWSIZE not in (0, PARTSIZE]"
  echo "size-gate" > "$STATUS"; exit 1
fi

# FAT free space must fit full partition backup (+ ~256K headroom for log/sha)
NEED=$((PARTSIZE + 262144))
FREE=$(df -B1 /mnt/SDCARD 2>/dev/null | awk 'NR==2{print $4}')
log "SDCARD free=$FREE need>=$NEED"
if [ -n "$FREE" ] && [ "$FREE" -lt "$NEED" ]; then
  log "ERROR: SDCARD free $FREE < need $NEED — move boot-new off FAT / free space"
  echo "sdcard-nospace" > "$STATUS"; exit 1
fi
# If NEW is still on SDCARD, it consumes space that backup needs — refuse
case "$NEW" in
  /mnt/SDCARD/*)
    log "ERROR: boot-new.img must be on UDISK, not SDCARD (8MiB too small for both)"
    echo "new-on-sdcard" > "$STATUS"; exit 1
    ;;
esac

expect=$(awk '{print $1}' "$NEWSHA")
got=$(sha256sum "$NEW" | awk '{print $1}')
log "staged sha expect=$expect got=$got"
if [ "$expect" != "$got" ]; then
  log "ERROR: staged sha256 mismatch"; echo "sha-mismatch-staged" > "$STATUS"; exit 1
fi

# Backup FULL partition to FAT (Mac-readable) + UDISK
log "backing up boot partition ($PARTSIZE bytes) to $STOCK"
dd if="$BOOTDEV" of="$STOCK" bs=1M conv=fsync 2>>"$LOG"
STOCKSZ=$(wc -c < "$STOCK")
if [ "$STOCKSZ" -ne "$PARTSIZE" ]; then
  log "ERROR: backup truncated $STOCKSZ != $PARTSIZE"
  rm -f "$STOCK"
  echo "backup-truncated" > "$STATUS"; exit 1
fi
sha256sum "$STOCK" | tee /mnt/SDCARD/boot-stock.img.sha256 >>"$LOG"
if [ -d /mnt/UDISK ] && touch /mnt/UDISK/.w 2>/dev/null; then
  rm -f /mnt/UDISK/.w
  # Independent read — do not copy potentially stale FAT file only
  dd if="$BOOTDEV" of="$STOCKUD" bs=1M conv=fsync 2>>"$LOG"
  sha256sum "$STOCKUD" > /mnt/UDISK/boot-stock.img.sha256
  log "also dumped stock to UDISK"
fi

if [ -f /mnt/SDCARD/boot-swap.dry ]; then
  log "DRY-RUN: backup done, skipping write/reboot"
  echo "dry-ok" > "$STATUS"; exit 0
fi

log "writing new boot image ($NEWSIZE bytes)"
# Write image then pad? Prefer write only NEWSIZE then leave rest; dd of image is fine if NEWSIZE<=PARTSIZE
dd if="$NEW" of="$BOOTDEV" bs=1M conv=fsync 2>>"$LOG"
# Read-back first NEWSIZE bytes
dd if="$BOOTDEV" bs=1 count="$NEWSIZE" 2>/dev/null | sha256sum | awk '{print $1}' > /tmp/rb.sha
rb=$(cat /tmp/rb.sha)
log "readback sha=$rb"
if [ "$rb" != "$expect" ]; then
  log "ERROR: readback mismatch — restoring stock"
  dd if="$STOCK" of="$BOOTDEV" bs=1M conv=fsync 2>>"$LOG"
  sync
  echo "sha-mismatch-readback-restored" > "$STATUS"; exit 1
fi

echo "armed-$(date -Iseconds 2>/dev/null || date)" > "$ARMED"
echo "ok-rebooting" > "$STATUS"
echo "ok" > "$DONE"
sync
log "write verified; rebooting into new kernel"
sleep 1
reboot -f 2>/dev/null || reboot
