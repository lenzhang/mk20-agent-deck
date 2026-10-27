#!/bin/sh
# Build usb_f_ncm.ko for the STOCK MK20 kernel (5.4.61 #221, MODVERSIONS off).
# Same recipe as build-usb_f_ecm.sh. u_ether (gether_*) and libcomposite are built in
# (CONFIG_USB_U_ETHER=y, CONFIG_USB_LIBCOMPOSITE via configfs) -> only f_ncm.c goes in the module.
# K = clean tina-linux-5.4 tree; CFG = /proc/config.gz content from device (== ikconfig, #221).
# PREPARED=1 skips olddefconfig/modules_prepare when K is already prepared with CFG.
set -e
K=${K:?clean kernel tree}; M=${M:-$PWD/ncm-mod}
if [ "${PREPARED:-0}" != 1 ]; then
  CFG=${CFG:?stock config}
  cp "$CFG" "$K/.config"
  make -C "$K" ARCH=arm CROSS_COMPILE=arm-linux-gnueabihf- olddefconfig
  make -C "$K" ARCH=arm CROSS_COMPILE=arm-linux-gnueabihf- LOCALVERSION= modules_prepare
fi
# gcc14 has no ARM SSP-per-task plugin -> olddefconfig drops it; restore the define so
# struct thread_info layout matches stock, and build the module with -fno-stack-protector.
# KCFLAGS=-march=armv7-a: gcc14 defaults to armv5 here (strex rejected); stock modules are Tag_CPU_name 7-A.
grep -q CONFIG_STACKPROTECTOR_PER_TASK "$K/include/generated/autoconf.h" || \
  echo '#define CONFIG_STACKPROTECTOR_PER_TASK 1' >> "$K/include/generated/autoconf.h"
# f_ncm-mk20.patch: <=512-byte NTBs (sunxi_udc PIO-only bulk requests), see the comment in the patch.
D=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$M"; rm -f "$M"/f_ncm.c "$M"/f_ncm.o "$M"/usb_f_ncm.ko
cp "$K/drivers/usb/gadget/function/f_ncm.c" "$M/"; cp "$D/usb_f_ncm.Kbuild" "$M/Kbuild"
(cd "$M" && patch -p5 --no-backup-if-mismatch < "$D/f_ncm-mk20.patch")
grep -q MK20_NTB_IN_CAP "$M/f_ncm.c"
make -C "$K" ARCH=arm CROSS_COMPILE=arm-linux-gnueabihf- KCFLAGS=-march=armv7-a M="$M" modules
arm-linux-gnueabihf-readelf -p .modinfo "$M/usb_f_ncm.ko" | grep -E 'vermagic|depends|name'
sha256sum "$M/usb_f_ncm.ko"; wc -c < "$M/usb_f_ncm.ko"
