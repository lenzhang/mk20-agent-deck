# GPL-2.0 corresponding source for the shipped kernel modules

`deck/firmware/release/lib/usb_f_ncm.ko` and `deck/firmware/release/lib/usb_f_ecm.ko` are
Linux kernel modules and are licensed under **GPL-2.0** (the source files carry
`SPDX-License-Identifier: GPL-2.0+`). They are NOT covered by this repository's MIT license.

| Module | Complete source in this directory | Origin |
|---|---|---|
| `usb_f_ncm.ko` (sha256 `6699be5f…`, see `../stock-patch/usb_f_ncm.ko.sha256`) | `f_ncm.c` (already patched) | upstream `drivers/usb/gadget/function/f_ncm.c` + `../stock-patch/f_ncm-mk20.patch` |
| `usb_f_ecm.ko` | `f_ecm.c` (unmodified) | upstream `drivers/usb/gadget/function/f_ecm.c` (byte-identical in Tina and vanilla v5.4.61) |

- Kernel tree: MangoPi / Allwinner Tina Linux 5.4.61 (T113 / sun8iw20),
  <https://github.com/paulwratt/mangopi-sbc-tina-linux-5.4> at commit
  `0d4903ebd9d2194ad914686d5b0fc1ddacf11a9d` (headers come from that tree).
  Verified: that commit's `f_ncm.c` + `f_ncm-mk20.patch` is byte-identical to `f_ncm.c` here.
- Kernel configuration: `config-5.4.61-221` (the device's `/proc/config.gz`, stock kernel #221).
- Build scripts: `../stock-patch/build-usb_f_ncm.sh`, `../stock-patch/build-usb_f_ecm.sh`,
  Kbuild files `../stock-patch/usb_f_*.Kbuild`; toolchain: Debian trixie
  `gcc-arm-linux-gnueabihf` 14.2 (see `deck/tools/build-image/Dockerfile`).
- License text: `LICENSES/GPL-2.0`.

The other kernel patches under `deck/kernel/patches` and `deck/kernel/stock-patch` modify
GPL-2.0 kernel sources and are likewise distributed under GPL-2.0.
