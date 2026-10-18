I'll read the notes, patch tooling, and boot/gadget scripts first, then check the sunxi_udc specifics.Read all the listed files. Next I'll look for the sunxi_udc source and the related notes and staging files.Now the remaining scripts, the reviews folder, and the ep3 patch apply script.Prior stock-patch reviews exist, so I'll read them to see what's been raised and what's changed since.The previous GPT review is in; the Claude review file is empty. I'll check whether the Tina kernel source is reachable locally so I can verify the sunxi_udc paths rather than reason from memory.The `sunxi_usb` source tarball and both staged images are on this Mac. I'll stream the source out of the tarball to stdout (nothing gets extracted to disk) so I can check the ISO/FIFO/DMA paths.Backup metadata checks out (p4 sha matches the device; staged image hashes match the notes). Next I'll grep the sunxi_udc source for ISO-specific branches, FIFO sizing, and DMA selection.The rootfs image has a gadget config script near those offsets; I'll print the surrounding lines to see every function the stock modes use.The rootfs gadget script has ISO-dependent modes (`uac1`, `uac1,hid`). I'll check which `setusbconfig` modes `KeyboardDevice` and the rest of the rootfs actually invoke.I have nearly everything. One last check: whether the ISO-only queue branch is compiled in at all (it depends on UVC plus SMP).**(a) The kernel-only ACM test boot with `boot-221-ep3int.img` is a GO, once you do the preflight below. (b) The full composite is a NO-GO.** I checked against the `sunxi_usb` and Tina 5.4 sources in `~/mk20-backup/kernel` and verified both staged images byte-for-byte, without writing anything.

**1. ep3 patch: sufficient**
- Endpoint matching only looks at `caps`. Tina's `usb_gadget_ep_match_desc` requires `type_int` under `SUNXI_UDC0`, and the endpoint number comes from `name[2]`, so "ep3-iso" still gives 3.
- `ep_enable` takes the transfer type from the descriptor, so ep3 is set up as INT. The FIFO lookup by name is unchanged (offset 4608, 1024 bytes, single buffer).
- DMA is only used when the length exceeds maxpacket, so notifications of 16 bytes or less use PIO. Interrupts for physical ep3 go to `ep[5]`, which is correct.
- In this build `bmAttributes` only affects `ep_config_reset`. The ISO branch in `queue` needs UVC, which is off. The byte is harmless and consistent.
- Side effect: even stock ACM's notify endpoint moves from 0x84 to 0x83. So test (a) really does exercise ep3 as INT on hardware.
- Side effect: **USB audio (UAC1) is lost.** The stock config has `USB_CONFIGFS_F_UAC1=y`, and the stock Qt app in rootfs contains `/data/setusbconfig uac1` next to `serial`. If KD's saved USB mode ever becomes audio on this kernel, every boot ends with no gadget until the TF is restored.

**2. ep5out (0x24 to 0x2c): safe and correct**
- The RX setup path handles INT. A 9-byte maxpacket on the double FIFO rounds to two 512-byte banks, which exactly fills its 1024-byte slot.
- Bulk still picks ep1out and ep2out first. Allocation comes out as you predicted: ECM on the ep1 pair with notify on ep3, HID on ep4 IN and ep5out, ADB on the ep2 pair.
- configfs binds functions in the order they were linked, so ECM before HID is right. That only holds on a clean gadget: stale links or `ln -sf` re-links change the order.

**3. Repack: sound**
- I confirmed that the header page, decompressor prefix and tail match stock, and that inflate, CRC and ISIZE all check out.
- The Image differs only at 0xd326d8 and 0xd326f5, plus 0xd327e0 in the ep5out image. The empty FNAME is skipped by Tina's `decompress_inflate.c`.
- Pitfall: **the stock header `id` is a valid AOSP SHA-1** over kernel and ramdisk, so it's now stale in both images. I found no id or hash check in U-Boot's Android code. The signature check appears to be secure-boot only, and the cmdline has no `verifiedbootstate`. So the risk is low, but recomputing the `id` is strictly safer.
- Pitfall: the staged image holds only the header and kernel pages. The ramdisk page comes from the existing p4, so the image only works written over stock p4. Never use it as a restore image.
- The QEMU run proves the stock decompressor accepts the new piggy. It doesn't test U-Boot `boota`, T113 kernel init, or the UDC.

**4. `usb_f_ecm.ko`**
- Good: vermagic matches, there are no `__stack_chk_*` imports, and `insmod` succeeded, so relocations are fine. The only ABI-relevant GCC plugin is per-task stack protection (RANDSTRUCT and STRUCTLEAK are off), and restoring that define keeps `thread_info` correct.
- Risk: with MODVERSIONS off, nothing checks struct layouts. The built-in u_ether reads `struct gether`, which embeds `usb_function`. Any layout drift between the vendor tree and the MangoPi tree would only show up at bind, connect or traffic, and `mkdir` doesn't reach those.

**5. Brick and channel loss**
A hardware brick isn't possible, because boot0, U-Boot and the GPT are never written. Losing USB until you pull the TF is possible: a kernel hang (`PANIC_TIMEOUT=0`, no auto-rollback), uac1 mode, or a composite that fails without restoring a gadget. Gaps:
- `tf-restore-boot.sh` suggests `s3`, but the boot partition is **s4**. It also doesn't check slice size, so a wrong slice would overwrite env-redund or rootfs.
- `boot-swap.rebackup` overwrites **both** on-card stock copies with whatever p4 currently holds. Any later swap or revert done that way leaves the Mac as the only stock copy.
- The readback reads from the page cache, and `dd` exit codes are ignored.

**(a) Preflight**
1. Restage `/data/mk20/boot-new.img` and its `.sha256` with the ep3-only image. It currently holds the ep5out image (`1fc8525a…`). The log must show `expect=38703734…`.
2. Re-stage `boot-once/bootswap.sh` and its `.sha256`, since the dry run consumed them. Delete `boot-swap.dry`. Make sure there's no `boot-swap.rebackup` or `mk20-no-bootswap`, and that `boot-delayed/` is empty. Keep `mk20-no-adb` and `mk20-no-bootevery`.
3. Fix the restore tool: use s4 and check that the slice is 6451200 bytes.
4. Confirm KD's saved USB mode is serial, and don't touch the audio or AI USB settings during the test.
5. Optional: recompute the `id`, and drop caches before the readback.

After boot, check that ACM enumerates with notify at endpoint 0x83, that the port survives repeated open and close, that `getInfo` works, and that `dmesg` shows no sunxi_udc errors.

**(b) Blockers**
1. `gadget-ecm-hid-adb.sh` binds the UDC before adbd writes its descriptors, so the bind fails with `-ENODEV`. If g1 still owns the "adb" FunctionFS name, `mkdir ffs.adb` fails with EBUSY and ADB is silently dropped. Use the `mk20-add-adb.sh` order, and check which gadget adbd's own UDC bind targets.
2. The script uses `set -eu` but only traps INT and TERM. Any write that fails after the unbind (EBUSY on a reused `g_mk20`, or `insmod`) exits with no gadget. Trap EXIT and tear down stale config first.
3. `report_length` 8 is too small: keyboard reports with ID 1 are 9 bytes.
4. Because the script uses `killall`, procd respawns KD. KD and the `adb.sh` keepalive then both run `setusbconfig serial` and fight for the UDC. Stop the service with `/etc/init.d/qt_app2 stop` and set `mk20-no-adb`.
5. Prove an ECM-only gadget binds and passes traffic on the **stock** kernel first. Its notify endpoint will land on ep4 there.
6. Before any second swap, add a revert-to-stock job or keep the stock backup under a separate name. Ideally also add a delayed auto-revert that restores stock unless the host confirms within N minutes.