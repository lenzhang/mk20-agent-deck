# Unblocking the real swap: binary-patch the STOCK kernel (approach A) — 2026-10-18 ~13:00 SGT

## Key correction
The device does NOT run the IMAGEWTY kernel (#230 Oct 15). It runs **5.4.61 #221 (Sep 29 2025)**
from mmcblk0p4 (dry-run backup sha 9ac68ed9…, 6451200 B). /proc/config.gz == IMAGEWTY ikconfig.
#221 vs #230 decompressed Image: 50 bytes differ (version/timestamps). All patching below is
against the **live #221 partition backup** (pulled via adb).

## A — chosen. Evidence
- Boot image: Android v0, page 2048, kernel = ARM zImage (magic 0x016f2818), gzip piggy at
  0x3e0c, decompresses to 14,031,052-byte Image; table: kernel_bss 211332, piggy size word OK.
- `gzip -n -9` (1.13) re-compression of the stock Image is **byte-identical** to the stock
  piggy (#221 and #230) → round-trip proven.
- sunxi_udc ep table located from the "ep3-iso" string → pointer refs (ep_fifo table + struct
  sunxi_udc.ep[]). Layout cross-checked against our own patched build (caps @+44,
  maxpacket @+50, limit @+52, num @+68, bEndpointAddress @+72, bmAttributes @+73).
  Live #221 Image endpoint table:
  ```
  ep1in 0x14 ep1out 0x24 ep2in 0x14 ep2out 0x24  ep3-iso caps 0x32 bmA 1  ep4-int 0x38 bmA 3
  ep5in 0x14 ep5out 0x24       (caps bits: ctrl1 iso2 bulk4 int8 in0x10 out0x20)
  ```
- Matching only uses `caps` (+maxpacket_limit) — usb_gadget_ep_match_desc (SUNXI_UDC0 requires
  caps.type_int for INT). ep_enable derives ts_type from the descriptor; ep name only used for
  the FIFO lookup (name string left untouched, so lookup is unchanged).
- Patch (Image offsets): ep3 caps 0x32→0x38 (@0xd326d8), ep3 bmAttributes 1→3 (@0xd326f5).
  Same bytes as our source-patched build. Optional 3rd byte: ep5out caps 0x24→0x2c (bulk+INT).
- Repack: new piggy spliced into the stock zImage; decompressor, table, tail and boot.img header
  **byte-identical** to stock. ep3-only variant: piggy exactly same length (no padding).
  ep3+ep5out variant: piggy 1 byte short → padded with an empty gzip FNAME (FLG.FNAME), which
  lib/decompress_inflate.c __gunzip explicitly skips.
- Verified: python zlib + gzip(1) gunzip == patched Image; Image diff == exactly the intended bytes;
  **qemu-system-arm (virt, cortex-a7) running the STOCK decompressor** on both variants reaches the
  kernel (PC in 0xc0xxxxxx) and RAM dumps show caps/bmA patched, first 9 MB identical to stock run.
- All private drivers (LCDs fb_gc9107/nv3007, RTL8710BU, ATSHA204A) retained — kernel is stock.

## ECM / HID on the stock kernel (live, via adb)
- Stock has USB_F_HID=y, U_ETHER=y (RNDIS), LIBCOMPOSITE=y, F_FS=y; MODVERSIONS/MODULE_SIG off;
  vermagic `5.4.61 SMP preempt mod_unload ARMv7 p2v8`.
- usb_f_ecm.ko built against /proc/config.gz (clean tree, PER_TASK define restored,
  -fno-stack-protector): vermagic matches; 41 undefined syms all in stock __ksymtab_strings.
- **Live: `insmod /data/mk20/usb_f_ecm.ko` rc=0 on stock #221; `mkdir functions/ecm.test` OK
  (dev_addr/host_addr/ifname/qmult); `mkdir functions/hid.test` OK.**

## Endpoint budget correction (important)
f_hid in this 5.4 tree **always** allocates IN + OUT interrupt endpoints (OUT mandatory, bind fails
otherwise). So ECM(1 INT) + HID(2 INT) = 3 INT; ep3→INT alone gives only 2 → ECM+HID do not fit.
Fix staged: ep5out also INT-capable (keeps bulk). Allocation with link order **ECM before HID**:
ECM notify→ep3, ECM bulk→ep1in/ep1out, HID IN→ep4, HID OUT→ep5out, ADB→ep2in/ep2out.
(If HID were linked first it would take ep3+ep4 and ECM notify would fail.) No ACM in that
composite (would need a 4th INT).

## B / C
- B: not applicable — the LCD drivers are built-in (no .ko in /lib/modules/5.4.61, only
  fuse/iptables/xt_*). Moot anyway since A keeps the stock kernel.
- C: not needed.

## Staged (dry flag still set)
/data/mk20/boot-new.img = boot-221-ep3int-ep5out.img sha 1fc8525a… (+ .sha256 lunch format)
/data/mk20/boot-221-ep3int.img = ep3-only fallback sha 38703734…
