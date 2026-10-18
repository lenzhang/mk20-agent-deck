#!/bin/sh
# Mac: arm the KERNEL-ONLY ep3 test boot (stock ACM, no adb/boot-every, no ECM/HID). Run only after:
#   both reviews GO for (a), restore-proof OK, KD usb mode = serial, user present to power-cycle.
set -eu; cd "$(dirname "$0")"
T=$(adb devices -l | grep -v WATCH-SERIAL | grep '^(no serial number)' | sed -n 's/.*transport_id:\([0-9]*\).*/\1/p' | head -1)
[ -n "$T" ] || { echo "MK20 adb not found"; exit 1; }
E=38703734a1c78407b5db77e05d3f0961a5561d4b0970ee0077d55afbc5bc1ece
shasum -a 256 remote/bootswap.sh | awk '{print $1"  bootswap.sh"}' > /tmp/bootswap.sh.sha256
adb -t $T push remote/bootswap.sh /mnt/SDCARD/mk20/bootswap.sh.stage
adb -t $T push /tmp/bootswap.sh.sha256 /mnt/SDCARD/mk20/bootswap.sh.sha256.stage
adb -t $T push remote/postswap-check.sh /data/mk20/postswap-check.sh
adb -t $T shell "set -e; S=/mnt/SDCARD; M=/data/mk20
  [ \"\$(sha256sum \$M/boot-221-ep3int.img | cut -d' ' -f1)\" = $E ]
  cp -f \$M/boot-221-ep3int.img \$M/boot-new.img; echo '$E  boot-new.img' > \$M/boot-new.img.sha256
  [ \"\$(sha256sum \$M/boot-new.img | cut -d' ' -f1)\" = $E ]
  touch \$S/mk20-no-adb \$S/mk20-no-bootevery; rm -f \$S/mk20/boot-delayed/*.sh \$S/boot-swap.rebackup \$S/mk20-no-bootswap
  mkdir -p \$S/mk20/boot-once; mv -f \$S/mk20/bootswap.sh.stage \$S/mk20/boot-once/bootswap.sh; mv -f \$S/mk20/bootswap.sh.sha256.stage \$S/mk20/boot-once/bootswap.sh.sha256
  rm -f \$S/boot-swap.dry \$S/mk20-swap-ok \$S/mk20/boot-once/postswap-check.sh*; echo armed-ep3 > \$S/boot-swap-status.txt; sync; sh -n \$M/postswap-check.sh; ls \$S | grep -E mk20-.boot-swap; ls \$S/mk20/boot-delayed 2>/dev/null; ls -la \$S/mk20/boot-once \$M/boot-new.img*; cat \$M/boot-new.img.sha256"
echo "ARMED. Next: reboot (soft reboot may hang -> user power-cycles). bootswap runs at boot, writes, verifies, reboots."
