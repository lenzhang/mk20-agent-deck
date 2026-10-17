usb_f_ecm.ko / usb_f_ncm.ko — overnight cross-build (2026-10-18)

Source: vanilla linux-5.4.61 (cdn.kernel.org)
Toolchain: arm-linux-gnueabihf-gcc 14.2 (Debian)
Flags: CONFIG_PREEMPT=y, KCFLAGS=-march=armv7-a -mfloat-abi=hard
Vermagic produced: "5.4.61 SMP preempt mod_unload ARMv7 p2v8"
Device fuse.ko vermagic:     "5.4.61 SMP preempt mod_unload ARMv7 p2v8"
→ STRING MATCH.

Caveats (must verify live under adb):
1. Built without Tina Module.symvers — if device has CONFIG_MODVERSIONS=y,
   insmod will reject with "disagrees about version of symbol …".
2. Device already has u_ether built-in (kernel_strings); module depends on
   u_ether symbols from the running kernel.
3. Exact .config unknown until /proc/config.gz is pulled — struct layouts
   may still ABI-mismatch even with matching vermagic.
4. Even if ECM loads, EP budget: ECM needs 1 INT EP; stock sunxi_udc has
   only ONE INT (ep4-int), already consumed by ACM → ACM+ECM impossible
   without sunxi_udc.h patch (ep3-iso→ep3-int) + kernel reflash.

Morning test (after adb window):
  adb push usb_f_ecm.ko /tmp/
  adb shell "insmod /tmp/usb_f_ecm.ko; echo exit=\$?; ls /sys/module/usb_f_ecm; dmesg | tail -20"
