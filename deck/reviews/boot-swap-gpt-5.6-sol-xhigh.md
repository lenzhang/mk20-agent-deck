Verdict: **NO-GO for the current one-replug plan.** Removable TF makes permanent hardware bricking unlikely, but the current workflow can overwrite `boot` without producing a usable backup.

## Blockers

1. **The 8 MiB FAT capacity calculation is impossible.**

The staged image is 5,435,392 B. A complete dump of the 12,600-sector boot partition is approximately 6,451,200 B. Together they require 11,886,592 B before FAT metadata, logs, hashes, or scripts.

The script stages the new image on FAT and then dumps the entire boot partition there:

```93:126:deck/m2-scripts/lunch-boot-swap.sh
NEWSIZE=$(wc -c < "$NEW")
PARTSIZE=$(blockdev --getsize64 "$BOOTDEV" 2>/dev/null || echo 0)
# ...
if [ "$NEWSIZE" -gt 7000000 ]; then
  # ...
fi
# ...
dd if="$BOOTDEV" of="$STOCK" bs=1M conv=fsync 2>>"$LOG"
sha256sum "$STOCK" | tee /mnt/SDCARD/boot-stock.img.sha256 >>"$LOG"
# ...
cp -f "$STOCK" "$STOCKUD"
```

Worse:

- There is no fail-fast handling.
- FAT exhaustion leaves a truncated `boot-stock.img`.
- The UDISK backup copies that truncated file rather than reading `BOOTDEV` independently.
- A readback mismatch would “restore” the truncated image, potentially creating a corrupt hybrid boot partition.

The new image and stock backup must not coexist on the 8 MiB FAT. A viable layout is new image on UDISK, full boot-partition backup alone on FAT, with strict free-space and output-size checks.

2. **The 7 MB gate is unsound.**

The known boot partition is approximately 6,451,200 B, so 7,000,000 B is already too large. The dynamic `PARTSIZE` check is the correct gate, but the script permits `PARTSIZE=0` and proceeds unchecked.

Before writing, require:

- `PARTSIZE` successfully obtained and equal to the observed live partition size.
- `0 < NEWSIZE <= PARTSIZE`.
- Enough actual filesystem free space for the complete backup plus metadata.
- Backup size exactly equals `PARTSIZE`.
- Backup hash independently verified before touching `boot`.

3. **Pull-TF boot-only restore is not implemented.**

Although `--image` is advertised, the restore script deliberately aborts:

```83:90:deck/tools/tf-restore-boot.sh
if [[ -n "$WHOLE" ]]; then
  sudo dd if="$SRC" of="$RAW" bs=4m status=progress
else
  # Partition-only restore: user must have identified boot slice.
  # Prefer writing whole-card images. For boot-only, require --slice.
  die "boot-partition-only restore on Mac needs GPT slice mapping; prefer --whole-image ..."
fi
```

“Copy `boot-stock.img`, then restore via device-side `dd` after stock boot” is circular when the card no longer boots. Current proven recovery is only a pre-existing whole-card image, preferably restored to and boot-tested from a spare TF.

Also, `--image --dry-run` misleadingly says it would write to the whole raw disk even though the real operation later refuses.

4. **The patch applicator can report success after an incomplete patch.**

Conceptually, changing the endpoint name, FIFO sizing, capabilities, and `bmAttributes` follows the guide. But if its structured match fails, the script only renames the symbol and still prints success:

```31:41:deck/kernel/patches/apply-ep3-int.sh
t3,n=pat.subn(repl,t2,count=1)
if n!=1:
  print('WARN: structured ep[5] replace count',n,'— applying broader INT flip for ep3 block')
  t3=t2
open(p,'w').write(t3)
# ...
rg -n 'ep3_int_name|ep3-iso|ep3_iso' "$H" "$C" | head -40
echo "OK: ep3→int applied under $ROOT"
```

That fallback does not perform the advertised broader INT conversion. It can produce an endpoint named `ep3-int` that still has ISO capabilities. The patch must hard-fail unless every expected transformation occurs, followed by review of the exact source diff.

5. **The ECM+HID+ADB gadget is not ready to be the post-boot success path.**

Major issues:

- Combined keyboard reports are 9 bytes including Report ID, but `report_length` is 8.
- FunctionFS is bound before `adbd` supplies descriptors. Standard ordering is mount FFS → start `adbd` → wait for bulk endpoints → bind UDC.
- Mount and symlink failures are ignored while `ADB_OK` is set.
- Killing `KeyboardDevice` does not stop its procd service; it may respawn and restore the serial gadget.
- The PTY bridge starts after `KeyboardDevice` was killed, but the script never deliberately restarts it.
- `set -e` failures are not caught by the INT/TERM-only failsafe.
- The boot-swap arming steps do not stage or schedule this gadget script.

```60:77:deck/m2-scripts/gadget-ecm-hid-adb.sh
mkdir -p functions/hid.usb0
echo 1 > functions/hid.usb0/protocol
echo 1 > functions/hid.usb0/subclass
echo 8 > functions/hid.usb0/report_length
# ...
if mkdir -p functions/ffs.adb 2>>"$LOG"; then
  ln -sf functions/ffs.adb configs/c.1/ || true
  # ...
  mount -t functionfs adb /dev/usb-ffs/adb 2>>"$LOG" || true
  ADB_OK=1
fi
```

6. **“Self-fallback” is manual and only works after a sufficiently successful boot.**

`boot-swap-mark-fail.sh` must be invoked from the new userspace; nothing automatically calls it when gadget probing fails:

```1:11:deck/m2-scripts/boot-swap-mark-fail.sh
touch /mnt/SDCARD/boot-swap-fail
# Re-arm lunch with the backup copy ...
# ...
reboot -f 2>/dev/null || reboot
```

It cannot help with kernel panic, MMC/rootfs failure, or loss of every control interface. With no A/B or U-Boot bootcount, external TF recovery is the only dependable fallback.

7. **Do not use the patched image if the unmodified rebuild is unavailable.**

“Unmodified-match or patched” is not an acceptable choice here. The unmodified MangoPi rebuild must boot first; otherwise packaging/BSP failure cannot be distinguished from the endpoint patch.

## Risks

- MangoPi Tina 5.4.61 is the right SoC family but not the Waveshare board BSP. Matching `5.4.61`, PREEMPT, and vermagic does not establish compatibility.
- Highest-risk differences are DTB, regulator/GPIO assignments, display, MMC timing, rootfs boot arguments, vendor drivers, load addresses, and boot-image packaging.
- Preserve the stock DTB, ramdisk, command line, and header fields wherever possible; replace only the kernel component initially.
- Converting ep3 removes the ISO endpoint and may regress UAC/audio gadget modes.
- FAT and UDISK are two partitions on the same physical card, not independent anti-brick copies.
- The referenced build and packer artifacts are outside this worktree and could not be independently reviewed here.

## What looks sound

- Removable-TF boot makes recovery fundamentally safer than soldered flash.
- Refusing to guess when `/dev/by-name/boot` is absent is correct.
- Hashing the staged image and hashing partition readback are good controls.
- Self-deleting `lunch.sh` before reboot prevents an automatic repeated flash loop.
- The Allwinner ISO→INT modification is conceptually the correct way to obtain a second interrupt endpoint.
- Endpoint budget after one conversion is:

| Composite | INT | Bulk pairs | Result |
|---|---:|---:|---|
| ECM + combined HID | 2 | 1 | Fits |
| ECM + combined HID + ADB | 2 | 2 | Nominally fits |
| ACM + combined HID + ADB | 2 | 2 | Nominally fits |
| ACM + ECM + combined HID | 3 | 2 | Does not fit |
| ACM + ECM + HID + ADB | 3 | 3 | Does not fit |

This assumes one combined HID interface. Separate keyboard and pointer functions consume two INT endpoints.

## Suggested arming order

1. Make a whole-card image, restore it to a spare TF, and boot-test that spare. Keep the original untouched.
2. On stock firmware, record the live `/dev/by-name/boot` target, start sector, exact size, `/proc/cmdline`, and partition table.
3. Prove the Mac recovery path. Current boot-only restore is not sufficient; whole-card restore is.
4. Change the staging model conceptually: new image on UDISK, complete stock partition dump on FAT, with strict fail-fast checks.
5. Run a no-write/dry backup test and verify the FAT backup is exactly `PARTSIZE` bytes and readable on the Mac.
6. Build and boot the unpatched MangoPi-derived image on the spare card, retaining stock DTB/ramdisk where possible.
7. Apply the endpoint patch only with exact-match assertions; inspect the diff and boot the patched kernel while retaining stock ACM first.
8. Test ECM+HID without ADB, then add ADB after correcting FunctionFS ordering and HID report length.
9. Verify Protocol A, UI, MMC/rootfs, ECM, HID, and rollback.
10. Only then use the final one-replug workflow on the primary card. Ensure stale `DONE`, `FAILFLAG`, `DRY`, and `ARMED` markers are absent and remove—not create—`mk20-no-bootswap` immediately before arming.
