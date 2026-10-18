#!/bin/sh
# Build usb_f_ecm.ko for the STOCK MK20 kernel (5.4.61 #221, MODVERSIONS off).
# K = clean tina-linux-5.4 tree; config = /proc/config.gz from device (== ikconfig).
set -e
K=${K:?clean kernel tree}; CFG=${CFG:?stock config}; M=${M:-$PWD/ecm-mod}
cp "$CFG" "$K/.config"
make -C "$K" ARCH=arm CROSS_COMPILE=arm-linux-gnueabihf- olddefconfig
make -C "$K" ARCH=arm CROSS_COMPILE=arm-linux-gnueabihf- LOCALVERSION= modules_prepare
# gcc14 has no ARM SSP-per-task plugin -> olddefconfig drops it; restore the define so
# struct thread_info layout matches stock, and build the module with -fno-stack-protector.
grep -q CONFIG_STACKPROTECTOR_PER_TASK "$K/include/generated/autoconf.h" || \
  echo '#define CONFIG_STACKPROTECTOR_PER_TASK 1' >> "$K/include/generated/autoconf.h"
mkdir -p "$M"; cp "$K/drivers/usb/gadget/function/f_ecm.c" "$M/"; cp "$(dirname "$0")/usb_f_ecm.Kbuild" "$M/Kbuild"
make -C "$K" ARCH=arm CROSS_COMPILE=arm-linux-gnueabihf- M="$M" modules
arm-linux-gnueabihf-readelf -p .modinfo "$M/usb_f_ecm.ko" | grep vermagic
