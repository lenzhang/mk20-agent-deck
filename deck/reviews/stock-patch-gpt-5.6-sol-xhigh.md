## Verdict

**NO-GO for the current integrated arm.**  
The 3-byte kernel patch and fixed-length zImage repack look technically sound for a controlled test. The blockers are artifact verification, recovery proof, and USB automation—not the endpoint patch itself.

A conditional **GO** is reasonable for a first boot using the untouched stock ACM gadget, with all ADB/ECM/HID automation disabled.

### 1. ep3: sufficient?

**Yes for the intended ECM/HID descriptors, with high confidence.**

- `caps 0x32→0x38` makes ep3 match interrupt IN or OUT.
- `bmAttributes 1→3` is necessary because `sunxi_udc` retains this static field for reset and its ISO-special queue path.
- Hardware transfer type is programmed from the function’s endpoint descriptor, so ep3 is configured as INT at enable time.
- Keeping the name `ep3-iso` is safe and preserves the `ep_fifo[]` lookup.
- Keeping the 1024-byte single FIFO and 1024 maxpacket limit is safe for the targeted 9-byte HID and 16-byte ECM notification packets. The source patch’s 512-byte limit is stricter, but not required here.
- DMA selection is based on length, endpoint number and direction—not ISO/INT type. Small interrupt transfers stay PIO.
- The ISO update path is entered from the descriptor transfer type, so it will not execute for INT.

The binary patch is not fully source-equivalent—the documented patch also renames the endpoint and lowers maxpacket:

```41:59:deck/kernel/patches/0001-ep3-iso-to-int.patch
-			.name		= ep3_iso_name,
+			.name		= ep3_int_name,
-			.maxpacket	= SW_UDC_EP_ISO_FIFO_SIZE,
-			.maxpacket_limit = SW_UDC_EP_ISO_FIFO_SIZE,
+			.maxpacket	= SW_UDC_EP_FIFO_SIZE,
+			.maxpacket_limit = SW_UDC_EP_FIFO_SIZE,
...
-		.bmAttributes	    = USB_ENDPOINT_XFER_ISOC,
+		.bmAttributes	    = USB_ENDPOINT_XFER_INT,
```

That difference is safe for these functions, but ep3 no longer supports UAC/UVC/other ISO gadgets.

### 2. ep5out and allocation

**The ep5 capability change is technically sound.**

`0x24→0x2c` means bulk + interrupt + OUT. The controller’s RX endpoint code supports programmable INT type, including double-buffered RX. With HID maxpacket 9, the FIFO code rounds each bank to 512 bytes, fitting the existing 1024-byte allocation.

The expected allocation is correct:

1. ECM bulk IN/OUT → ep1 pair
2. ECM notification → ep3 IN
3. HID IN → ep4
4. HID OUT → ep5out
5. ADB bulk IN/OUT → ep2 pair

Configfs binds functions in symlink-creation order, so **ECM before HID is required and correct**. This assumes a clean configuration; stale links in `g_mk20` can invalidate the order.

However, the current composite script is not ready:

- It binds the UDC before starting `adbd`. FunctionFS binding requires descriptors already written by `adbd`, otherwise binding returns `-ENODEV`.
- `report_length` is 8, but keyboard report ID 1 is 9 bytes including the report ID.
- `set -e` is active, but the failsafe trap catches only INT/TERM. Any ordinary failure after unbinding can exit with USB still disconnected.
- It kills `KeyboardDevice`/`qt_app2` and never reliably restarts them.
- Existing `g_mk20` functions and links are not cleaned before reuse.

```73:105:deck/m2-scripts/gadget-ecm-hid-adb.sh
# --- ADB FunctionFS (optional; skip if mkdir fails) ---
...
mount -t functionfs adb /dev/usb-ffs/adb 2>>"$LOG" || true
ADB_OK=1
...
# Bind
echo "$UDC" > UDC 2>>"$LOG" || { FAILSAFE bind-failed; exit 1; }
...
# Start adbd if FFS ready
...
adbd &
```

Therefore the endpoint plan is good, but **ECM+HID+ADB as scripted is NO-GO**.

### 3. zImage repack

The fixed-length repack is sound:

- Original Image round-tripping byte-identically through `gzip -n -9` is strong evidence.
- Keeping the piggy length unchanged preserves `input_data_end`, linker tables, zImage end address, Android `kernel_size`, page offsets and U-Boot load behavior.
- An empty gzip FNAME is standards-compliant, and the stock kernel decompressor explicitly skips NUL-terminated FNAME.
- The gzip CRC/trailer is regenerated correctly.

QEMU evidence is meaningful but narrow: it proves QEMU loaded the zImage, the stock decompressor accepted the modified gzip header, decompression completed, and control reached the kernel. It does **not** test the Android/U-Boot path, T113 hardware, kernel initialization, or UDC operation.

Remaining concern: `mkboot221.py` hardcodes a 2048-byte page and emits only through the kernel’s padded end. It does not parse/assert `page_size`, `ramdisk_size`, `second_size`, or verified-boot state. That is safe only as an in-place prefix write over the verified #221 partition.

The final `boot-221-ep3int-ep5out.img` is absent from the repository, so its full hash and claimed three-byte decompressed diff cannot be independently verified here.

### 4. `usb_f_ecm.ko` ABI

The GCC 14 versus GCC 6.4 difference is not itself a blocker. Matching ARM kernel ABI, source structures and configuration matter more.

Good evidence:

- Matching vermagic.
- MODVERSIONS and module signatures disabled.
- `insmod` succeeded.
- Creating `functions/ecm.test` exercised module relocation, registration and allocation.

Residual risk:

- With MODVERSIONS off, incompatible structure/function signatures are not detected.
- `mkdir functions/ecm.test` does not exercise `ecm_bind`, endpoint setup, `u_ether`, notifications or packet traffic.
- Restoring `CONFIG_STACKPROTECTOR_PER_TASK` is correct because it changes `struct thread_info`; `-fno-stack-protector` avoids the unavailable per-task canary plugin.
- The actual rebuilt/staged module is not in the repo. The tracked older module is a different vanilla-kernel build and still imports `__stack_chk_guard`/`__stack_chk_fail`.

Before depending on it, prove an ECM-only configuration on the stock kernel using ep4, including host enumeration and bidirectional traffic.

### 5. Brick and persistence risk

This should not permanently brick the hardware: only the removable TF boot partition is written. It can nevertheless leave the card unbootable and all USB channels absent.

`bootswap.sh` has good size, hash, full-partition backup and readback checks:

```30:43:deck/m2-scripts/remote/bootswap.sh
dd if=$DEV of=$STOCK bs=1M conv=fsync 2>&1
...
dd if=$NEW of=$DEV bs=1M conv=fsync 2>&1; sync
R=$(head -c $NS $DEV | sha256sum | awk '{print $1}')
if [ "$R" != "$E" ]; then dd if=$STOCK of=$DEV bs=1M conv=fsync; sync; fail "readback mismatch -> stock restored"; fi
```

But:

- Kernel failure before userspace means no automatic rollback.
- FAT and overlay backups are on the same physical card.
- Power loss or media failure during write/restore can corrupt boot.
- The external slice restore path requires manually selecting the correct Mac slice and has not been demonstrated here.
- Persistent ADB runs immediately after the next boot and can race `KeyboardDevice`, obscure kernel validation, or repeatedly remove serial.

## Concrete arm blockers

1. Copy the verified full stock backup off-card and prove either boot-slice restoration or a whole-card restore.
2. Independently verify the exact staged image: full SHA-256, Android header fields, unchanged non-kernel data, and exactly three decompressed byte changes.
3. Disable first-boot USB automation with `mk20-no-adb`/`mk20-no-bootevery` and remove any delayed `try-adb` job. First patched boot should use stock ACM only.
4. Do not run `gadget-ecm-hid-adb.sh` until FunctionFS ordering, HID length, failure cleanup, stale-config cleanup, and service restart are corrected.
5. Verify the actual staged ECM module is the new build and test ECM bind/enumeration/traffic.

After blockers 1–3, I would give **GO for a kernel-only smoke boot under stock ACM**. The full ECM+HID+ADB rollout remains **NO-GO** until blockers 4–5 are resolved.
