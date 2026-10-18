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
  log "REVERT: $*"
  SRC=""; for c in $STOCK $STOCK2; do [ -f $c ] && [ "$(sha256sum $c | awk '{print $1}')" = "$PS" ] && { SRC=$c; break; }; done
  [ -n "$SRC" ] || { log "NO VALID STOCK COPY - leave as is; pull TF + tf-restore-boot.sh --slice s4"; echo "revert-failed-no-stock: $*" > $ST; sync; exit 1; }
  dd if=$SRC of=$DEV bs=1M conv=fsync 2>&1 | tail -1 >> $L; sync; echo 3 > /proc/sys/vm/drop_caches
  R=$(sha256sum $DEV | awk '{print $1}')
  if [ "$R" = "$PS" ]; then log "stock restored ($R)"; echo "reverted: $*" > $ST; else log "RESTORE READBACK BAD $R"; echo "revert-readback-bad: $*" > $ST; fi
  sync; log "rebooting (soft reboot may hang: power-cycle)"; sleep 2; reboot -f
  exit 1
}
[ "${1:-}" = "--revert-timeout" ] && revert "no host confirmation within 600s"
log "BEGIN uname=$(uname -v)"
[ -n "$E" ] && [ -n "$PS" ] || { log "missing sha files; skip"; exit 0; }
NS=$(wc -c < $NEW); H=$(head -c $NS $DEV | sha256sum | awk '{print $1}')
[ "$H" = "$E" ] || { log "p4 is not the patched image ($H); nothing to check"; exit 0; }
echo "patched-booted-checking" > $ST; sync
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
i=0; while [ \$i -lt 300 ]; do [ -f $S/mk20-swap-ok ] && { echo "post confirmed by host at \$(cut -d' ' -f1 /proc/uptime)" >> $L; echo "patched-confirmed" > $ST; sync; exit 0; }; sleep 2; i=\$((i+1)); done
echo "no host confirmation" >> $L
EOC
( exec 9>&-; setsid sh -c "sh /tmp/mk20-confirm.sh; [ -f $S/mk20-swap-ok ] || sh $0 --revert-timeout" </dev/null >/dev/null 2>&1 & )
exit 0
