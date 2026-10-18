#!/bin/sh
# Prove the on-device restore path: rewrite stock p4 from the FAT copy (content no-op) and verify from media.
S=/mnt/SDCARD; E=9ac68ed98a70a3b5211dc8a25cf9427afdc54543ebfe003337c77032929029a8
DEV=$(readlink -f /dev/by-name/boot); echo "DEV=$DEV size=$(($(cat /sys/class/block/${DEV##*/}/size)*512))"
F=$(sha256sum $S/boot-stock.img | awk '{print $1}'); echo "fat copy=$F"; [ "$F" = "$E" ] || { echo "ABORT fat copy bad"; exit 1; }
F2=$(sha256sum /data/mk20/boot-stock.img | awk '{print $1}'); echo "overlay copy=$F2"
C=$(sha256sum $DEV | awk '{print $1}'); echo "current p4=$C"; [ "$C" = "$E" ] || { echo "ABORT p4 is not stock; not touching"; exit 1; }
dd if=$S/boot-stock.img of=$DEV bs=1M conv=fsync 2>&1 || { echo "dd rc!=0"; exit 1; }
sync; echo 3 > /proc/sys/vm/drop_caches
R=$(sha256sum $DEV | awk '{print $1}'); echo "readback p4=$R"; [ "$R" = "$E" ] && echo "RESTORE-PROOF OK" || { echo "RESTORE-PROOF FAIL"; exit 1; }
echo "KD usb mode hints:"; grep -i -E 'usb|uac|serial' $S/config.json /data/*.json 2>/dev/null | head -20
