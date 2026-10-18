#!/bin/sh
# Runs ONCE on the first boot of the patched kernel (staged into boot-once/ by bootswap.sh after a
# verified write). If the patched kernel boots but the USB/KD checks fail, or the host does not confirm
# within 10 min (touch /mnt/SDCARD/mk20-swap-ok via mk20ctl/adb), it restores stock p4 from the FAT copy.
S=/mnt/SDCARD; ST=$S/boot-swap-status.txt; L=$S/mk20/logs/postswap.log
STOCK=$S/boot-stock.img; STOCK2=/data/mk20/boot-stock.img
NEW=/data/mk20/boot-new.img; E=$(awk '{print $1}' $NEW.sha256 2>/dev/null)
PS=$(awk '{print $1}' $S/boot-stock.img.sha256 2>/dev/null)
DEV=$(readlink -f /dev/by-name/boot)
log() { echo "post $(cut -d' ' -f1 /proc/uptime): $*" | tee -a $L; }
revert() {
  log "REVERT: $*"   # boot-once re-arm is removed only after a verified restore
  SRC=""; for c in $STOCK $STOCK2; do [ -f $c ] && [ "$(sha256sum $c | awk '{print $1}')" = "$PS" ] && { SRC=$c; break; }; done
  [ -n "$SRC" ] || { log "NO VALID STOCK COPY - leave as is; pull TF + tf-restore-boot.sh --slice s4"; echo "revert-failed-no-stock: $*" > $ST; sync; exit 1; }
  # never bypass the kernel-write lock: wait up to 300 s, else abort WITHOUT touching p4 (recovery stays armed)
  mkdir -p /tmp/mk20; exec 8>/tmp/mk20/kwrite.lock; i=0
  while ! flock -n 8; do i=$((i+1)); [ $i -gt 300 ] && { log "ABORT revert: kernel-write lock busy >300 s; p4 untouched"; echo "revert-lock-busy: $*" > $ST; sync; exit 1; }; sleep 1; done
  ubus call system watchdog 2>/dev/null | grep -q '"running"' || { log "ABORT revert: watchdog not running (a reset may be in flight); p4 untouched"; exit 1; }
  for try in 1 2; do dd if=$SRC of=$DEV bs=1M conv=fsync > /tmp/mk20/revert-dd.log 2>&1; drc=$?; tail -n 1 /tmp/mk20/revert-dd.log >> $L
    sync; echo 3 > /proc/sys/vm/drop_caches; [ $drc = 0 ] && [ "$(sha256sum $DEV | awk '{print $1}')" = "$PS" ] && break; log "revert dd try $try failed (rc=$drc)"; done
  sync; echo 3 > /proc/sys/vm/drop_caches
  R=$(sha256sum $DEV | awk '{print $1}')
  if [ $drc = 0 ] && [ "$R" = "$PS" ]; then log "stock restored ($R)"; echo "reverted: $*" > $ST; rm -f $S/mk20/boot-once/postswap-check.sh*
  else log "RESTORE FAILED dd_rc=$drc readback=$R -> NO reset (would boot a partial p4); pull TF + tf-restore-boot.sh --slice s4"; echo "revert-readback-bad-DO-NOT-POWER-OFF: $*" > $ST; sync; exit 1; fi
  sync; exec 8>&-; log "hard reset (WDT)"; sleep 2; sh $S/mk20/hardreset.sh postswap; log "hardreset rc=$? -> power-cycle needed"
  exit 1
}
[ "${1:-}" = "--revert-timeout" ] && revert "no host confirmation within 600s"
log "BEGIN uname=$(uname -v)"
[ -n "$E" ] && [ -n "$PS" ] || { log "missing sha files; skip"; exit 0; }
NS=$(wc -c < $NEW); H=$(head -c $NS $DEV | sha256sum | awk '{print $1}')
[ "$H" = "$E" ] || { log "p4 is not the patched image ($H); nothing to check"; rm -f $S/mk20/boot-once/postswap-check.sh*; exit 0; }
[ -f $S/mk20-swap-ok ] && { log "already host-confirmed"; echo "patched-confirmed" > $ST; rm -f $S/mk20/boot-once/postswap-check.sh*; sync; exit 0; }
echo "patched-booted-checking" > $ST
# re-arm for the next boot until confirmed/reverted (a power cycle inside the window must not disarm us)
BO=$S/mk20/boot-once; mkdir -p $BO; cp -f "$0" $BO/postswap-check.sh
sha256sum $BO/postswap-check.sh | awk '{print $1"  postswap-check.sh"}' > $BO/postswap-check.sh.sha256; sync
G=/sys/kernel/config/usb_gadget/g1; i=0
until [ -n "$(cat $G/UDC 2>/dev/null)" ] && [ -e $G/configs/c.1/acm.usb0 ] && [ -e /dev/ttyGS0 ] && pgrep -f KeyboardDevice >/dev/null; do
  i=$((i+1)); [ $i -gt 90 ] && revert "ACM/KD not up after 90s"; sleep 1
done
log "ACM+KD up after ${i}s"
sleep 20
pgrep -f KeyboardDevice >/dev/null || revert "KD died"
[ -n "$(cat $G/UDC 2>/dev/null)" ] || revert "UDC unbound"
B=$(dmesg | grep -i -E 'sunxi_udc.*(err|fail)|Oops|BUG:|Unable to handle|ep.*mismatch' | head -5)
[ -z "$B" ] || revert "dmesg: $B"
log "local checks OK; waiting up to 600s for host confirmation ($S/mk20-swap-ok)"
echo "patched-ok-awaiting-host-confirm" > $ST; sync
# detach the confirmation wait (watcher caps jobs at 300s)
cat > /tmp/mk20-confirm.sh <<EOC
i=0; while [ \$i -lt 300 ]; do [ -f $S/mk20-swap-ok ] && { echo "post confirmed by host at \$(cut -d' ' -f1 /proc/uptime)" >> $L; echo "patched-confirmed" > $ST; rm -f $S/mk20/boot-once/postswap-check.sh*; sync; exit 0; }; sleep 2; i=\$((i+1)); done
echo "no host confirmation" >> $L
EOC
# NOTE: this Tina busybox has NO setsid/nohup (v1 used setsid -> detach silently never ran, 2026-10-18).
# Portable detach: background subshell ignoring HUP/INT; it is reparented to init when this job exits.
cp -f "$0" /tmp/mk20-postswap.sh
( trap '' HUP INT; exec sh -c "sh /tmp/mk20-confirm.sh; [ -f $S/mk20-swap-ok ] || sh /tmp/mk20-postswap.sh --revert-timeout" </dev/null >/dev/null 2>&1 9>&- ) &
sleep 1; kill -0 $! 2>/dev/null && log "confirm-wait detached pid=$!" || { log "DETACH FAILED -> revert now (failsafe)"; revert "could not start confirm wait"; }
exit 0
