#!/bin/sh
# Boot-partition swap job (run once by watcher). DRY if /mnt/SDCARD/boot-swap.dry exists.
# New image staged on overlay (/data/mk20), stock backup to FAT SDCARD (Mac-readable) + /data/mk20.
S=/mnt/SDCARD
NEW=/data/mk20/boot-new.img; NEWSHA=$NEW.sha256
STOCK=$S/boot-stock.img; STOCK2=/data/mk20/boot-stock.img
ST=$S/boot-swap-status.txt
fail() { echo "FAIL: $*"; echo "fail: $*" > $ST; sync; exit 1; }
echo "uname: $(uname -a)"; echo "cmdline: $(cat /proc/cmdline)"
ls -l /dev/by-name/ ; cat /proc/partitions; df -k
[ -f $S/mk20-no-bootswap ] && fail "kill switch"
DEV=$(readlink -f /dev/by-name/boot); [ -b "$DEV" ] || fail "no /dev/by-name/boot ($DEV)"
SEC=$(cat /sys/class/block/${DEV##*/}/size 2>/dev/null); [ -n "$SEC" ] || fail "no sysfs size"
PART=$((SEC*512)); echo "DEV=$DEV PART=$PART"
[ -f $NEW ] && [ -f $NEWSHA ] || fail "missing $NEW/.sha256"
[ -f /data/mk20/postswap-check.sh ] || fail "missing /data/mk20/postswap-check.sh (no auto-revert) - refusing"
NS=$(wc -c < $NEW); echo "NEWSIZE=$NS"
[ "$NS" -gt 0 ] && [ "$NS" -le "$PART" ] || fail "size gate NS=$NS PART=$PART"
E=$(awk '{print $1}' $NEWSHA); G=$(sha256sum $NEW | awk '{print $1}')
echo "new sha expect=$E got=$G"; [ "$E" = "$G" ] || fail "staged sha mismatch"
H=$(head -c $NS $DEV | sha256sum | awk '{print $1}'); echo "current-boot-prefix sha=$H (same as new? $([ "$H" = "$E" ] && echo YES || echo no))"
[ "$H" = "$E" ] && [ ! -f $S/boot-swap.dry ] && { echo "already applied"; echo "already-new sha=$E" > $ST; sync; exit 0; }
PSHA=$(head -c $PART $DEV | sha256sum | awk '{print $1}'); echo "partition sha=$PSHA"
# never clobber a recorded stock backup with a different (e.g. already-swapped) partition
SKIPBK=0; BL=$(awk '{print $1}' /data/mk20/boot-baseline.sha256 2>/dev/null)
if [ -f $S/boot-stock.img.sha256 ] && [ ! -f $S/boot-swap.rebackup ]; then
  O=$(awk '{print $1}' $S/boot-stock.img.sha256)
  if [ "$O" = "$PSHA" ]; then :
  elif [ -n "$BL" ] && [ "$PSHA" = "$BL" ]; then
    # patched->patched swap: current p4 is the recorded (host-confirmed) baseline, which is backed up
    # separately (/data/mk20/boot-baseline.img + Mac/box). Keep BOTH stock copies untouched.
    [ "$(sha256sum /data/mk20/boot-baseline.img 2>/dev/null | awk '{print $1}')" = "$BL" ] || fail "baseline backup missing/bad"
    [ "$(sha256sum $STOCK | awk '{print $1}')" = "$O" ] || fail "FAT stock copy bad"
    echo "current p4 = recorded baseline $BL; stock copies kept"; SKIPBK=1
  else fail "partition sha $PSHA != recorded stock $O nor baseline; refusing (touch boot-swap.rebackup to override)"; fi
fi
# kernel-write lock (fd 8): hardreset.sh waits for it, so no reset can tear a p4 write. Held until exit.
mkdir -p /tmp/mk20; exec 8>/tmp/mk20/kwrite.lock; flock -n 8 || fail "kernel-write lock busy"
if [ $SKIPBK = 0 ]; then
rm -f $STOCK
FREE=$(( $(df -k $S | awk 'NR==2{print $4}') * 1024 )); echo "SDCARD free=$FREE need=$((PART+131072))"
[ "$FREE" -gt $((PART+131072)) ] || fail "SDCARD space"
dd if=$DEV of=$STOCK bs=1M conv=fsync 2>&1
[ "$(wc -c < $STOCK)" -eq "$PART" ] || { rm -f $STOCK; fail "backup size"; }
B=$(sha256sum $STOCK | awk '{print $1}'); [ "$B" = "$PSHA" ] || fail "backup sha mismatch"
echo "$B  boot-stock.img" > $S/boot-stock.img.sha256
dd if=$DEV of=$STOCK2 bs=1M conv=fsync 2>&1; echo "stock2 sha=$(sha256sum $STOCK2 | awk '{print $1}')"
sync
fi
[ $SKIPBK = 1 ] && PSHA=$(awk '{print $1}' $S/boot-stock.img.sha256)   # revert target = stock
if [ -f $S/boot-swap.dry ]; then echo "DRY-RUN OK (no write)"; echo "dry-ok part=$PART sha=$PSHA" > $ST; sync; exit 0; fi
echo "WRITING new boot"
dd if=$NEW of=$DEV bs=1M conv=fsync 2>&1 || { dd if=$STOCK of=$DEV bs=1M conv=fsync; sync; fail "dd write error -> stock restored"; }; sync
echo 3 > /proc/sys/vm/drop_caches 2>/dev/null  # readback from media, not page cache
R=$(head -c $NS $DEV | sha256sum | awk '{print $1}'); echo "readback=$R"
if [ "$R" != "$E" ]; then [ "$(sha256sum $STOCK | awk '{print $1}')" = "$PSHA" ] || fail "readback mismatch AND stock copy bad - DO NOT REBOOT, pull TF"; dd if=$STOCK of=$DEV bs=1M conv=fsync; sync; fail "readback mismatch -> stock restored"; fi
# stage the one-shot post-boot check for the patched kernel (auto-revert on failure / no host confirm)
PC=/data/mk20/postswap-check.sh
if [ -f $PC ]; then mkdir -p $S/mk20/boot-once; cp -f $PC $S/mk20/boot-once/postswap-check.sh
  sha256sum $S/mk20/boot-once/postswap-check.sh | awk '{print $1"  postswap-check.sh"}' > $S/mk20/boot-once/postswap-check.sh.sha256
  echo "staged postswap-check"; else echo "WARNING: no $PC (no auto-revert)"; fi
rm -f $S/mk20-swap-ok
echo "ok-rebooting" > $ST; sync
if [ -f $S/boot-swap.noreboot ]; then rm -f $S/boot-swap.noreboot; echo "ok-power-cycle-now" > $ST; sync; echo "write verified; NO reboot (soft reboot hangs): user must power-cycle"; exit 0; fi
echo "write verified; hard reset (WDT) in 3s"; sleep 3; exec 8>&-; sh $S/mk20/hardreset.sh bootswap; echo "hardreset rc=$? -> power-cycle needed"; echo "ok-power-cycle-now" > $ST; sync
