Overall verdict: **no-go for a kernel write yet**. The recovery architecture is sensible, but the restore path has not been rehearsed and the proposed patch cannot satisfy `ACM + HID + ECM`.

Evidence note: several primary artifacts under `/workspace/mk20-research/` were unavailable locally, so I could verify the local M1 capture and internal consistency, but not independently re-read the firmware image, PDF, or host-update analysis.

## Blockers

1. **The patch does not meet the stated three-function goal.**
   - ACM: 1 interrupt IN + 1 bulk pair.
   - Combined keyboard/pointer HID: 1 interrupt IN.
   - ECM: 1 interrupt IN + 1 bulk pair.
   - Total: **3 interrupt endpoints**.
   - Stock has 1; ep3 ISO→INT gives **2**, so `ACM + HID + ECM` still cannot enumerate. The calculation in §4.3 is correct, but the title/goal’s “optional ECM” is misleading.
   - The stock build also apparently lacks `usb_f_ecm`, an independent blocker.

2. **No boot-tested TF clone exists.**
   A `dd` file plus SHA-256 verifies the backup file’s later identity, not that it can restore and boot. Before touching a kernel:
   - restore the image onto an equal-or-larger spare TF;
   - read it back or compare hashes;
   - boot the MK20 from that clone;
   - verify UI, ACM/Protocol A, rootfs and writable overlay.
   
   The original TF should then remain untouched while kernel experiments happen on the clone.

3. **The live partition map is not established.**
   The table mixes:
   - partition sizes from the V1.0 IMAGEWTY package;
   - mounts observed on the shipping V2.32 unit;
   - `mmcblk0p5` from another UART capture.
   
   Those are compatible but do not prove the shipping card has exactly that geometry. Capture live `by-name` targets, block start/size, GPT/MBR, cmdline and hashes before selecting any write target.

4. **No matching, reproducible kernel build exists.**
   Required before patching:
   - exact Waveshare/Allwinner vendor 5.4.61 source and patches;
   - shipping `.config`;
   - DTB/DTS and boot command line;
   - Tina boot-image packing procedure;
   - confirmation of signing/verified-boot requirements;
   - a successful boot of an **unmodified rebuilt image**.

   Matching `uname`/vermagic alone is insufficient.

5. **The proposed FEL RAM boot is not yet an executable procedure.**
   `xfel ddr t113-s3`, load and `xfel exec` is only an outline. Linux needs correct load addresses, DTB, boot arguments and ARM entry state. MK20 FEL entry is also unverified. Prove an exact RAM-boot procedure—or use UART/U-Boot alternate boot—before treating this as a safety gate.

6. **No recovery drill has succeeded.**
   At least one of these must be demonstrated:
   - boot-tested spare TF clone, preferably keeping the original untouched;
   - UART/U-Boot alternate-kernel boot;
   - MK20-specific FEL entry plus successful RAM boot;
   - PhoenixCard-created spare card that boots.

   The first option is sufficient and safest; FEL need not block experimentation if all writes are confined to a proven clone.

7. **Return the device to a known baseline first.**
   Any staged `lunch.sh`, gadget helper, `m2-window.flag`, or `/data/setusbconfig.real` state should be resolved before cloning or recovery tests. Otherwise the clone may reproduce an unrelated USB-dark condition.

## Risks

- **V2.32 conclusion is too absolute.**  
  The evidence supports: “the observed V2.32 package updated `KeyboardDevice` userspace and possibly GD32 firmware.” It does **not** prove the host’s “Update Device” mechanism can never deliver a T113 kernel or `.swu`. Correct operational conclusion: do not rely on it for kernel recovery.

- **PhoenixCard is plausible, not yet a verified MK20 restore.**  
  The bundled tool, packed IMAGEWTY image and full payload strongly support it. But “official full restore confirmed” should mean a generated card has actually booted this hardware. The V1.0 open-source-license image may also lose V2.32 applications or configuration.

- **PC4/FEL remains board-generic evidence.**  
  The PC4 strap claim may be correct for T113-S3, but there is no identified MK20 pad or schematic. `RECOVERY.md` also mentions shorting SPI pins, which conflicts with the newer PC4 guidance. Do not short either unknown pins or flash pins.

- **“SPI is irrelevant” is premature.**  
  Rootfs booting from TF is strong evidence, but `/proc/mtd`, UBI and U-Boot environment placement have not been captured. SPI may still contain auxiliary boot/environment data.

- **Supplementary adb backup instructions will fail.**  
  §5.2 writes rootfs/UDISK images under the 8 MiB `/mnt/SDCARD`; they cannot fit. Dump directly to the host over adb or use an external reader. A mounted, changing UDISK also cannot be considered a consistent raw backup.

- **The module probe commands need adjustment.**
  M1 ran `ls /sys/module`, not `lsmod`. `/sys/module` includes built-ins. Also, `rg` and `modinfo` may not exist on this minimal device. Check `/proc/modules`, kernel config and the live module tree instead.

- **“Write boot only” is still an assumption.**
  First prove that `/dev/by-name/boot` contains the running kernel/DTB and determine its image format and partition size. A short image written over a larger old image may also leave stale trailing data unless the format/write procedure accounts for it.

- **Same-card rollback is not instant.**
  A backup stored elsewhere on the experimental TF is inaccessible if boot fails unless UART/U-Boot is already proven. Keep the rollback image on the Mac and retain the original card.

## What looks sound

- TF is clearly the active Linux storage:
  - M1 shows `/dev/mmcblk0p1` mounted at `/mnt/SDCARD`;
  - `/rom` and the UDISK overlay are live;
  - the cited UART boot loads from SD0 and selects `mmcblk0p5`.
  
  “Linux boot chain and root filesystem are on TF” is well supported. “Entirely” is slightly stronger than the available evidence until MTD/UBI are inspected.

- Prioritizing an offline whole-card image is correct.

- Treating the packed IMAGEWTY file as **not directly `dd`-able** is correct.

- No evidence supports live DT/module-parameter remapping of ep3. Tina’s documented source change in `sunxi_udc.h` and `sunxi_udc.c` is a reasonable basis for the patch, subject to validation against the exact vendor tree.

- **`sunxi_usb_udc` being built-in is likely.**
  `/sys/module` alone proves nothing about modularity, but its presence combined with no corresponding `.ko` in the extracted module tree is strong evidence. “Likely built-in” is fair; “almost certainly” should wait for:
  - absence from `/proc/modules`;
  - `CONFIG_…SUNXI…UDC=y` in `/proc/config.gz`;
  - inspection of live `/lib/modules/$(uname -r)` or `modules.builtin`.

- The endpoint calculation in §4.3 is correct: ep3→INT supports `ACM + combined HID`, but not `ACM + HID + ECM`.

- Physical intervention is mentioned, but its mandatory/optional status is inconsistent: the plan requires FEL RAM boot while the summary calls case opening/FEL optional.

## Suggested next physical steps for the user

1. **Do not write a kernel yet.**
2. Return the MK20 to verified stock ACM operation and disarm pending boot hooks.
3. Cleanly power down and disconnect power.
4. Remove the TF card; photograph its orientation and label it as the original.
5. Make the whole-card image, record the card’s exact byte capacity and hash the image.
6. Put the original card somewhere safe and write the image to an equal-or-larger spare TF.
7. Boot the MK20 from the spare and perform a functional smoke test.
8. With the clone running, collect the exact live partition map, current boot partition, DTB, config, cmdline and U-Boot log.
9. If opening the case, identify UART pads first. Use a 3.3 V adapter, connect GND and adapter RX initially, and do not connect adapter VCC or RS-232 levels.
10. Do not attempt PC4 or SPI-pin shorting until the MK20 schematic/pad identity is known.
11. Optionally create and boot-test a separate PhoenixCard V1.0 spare as a second, lower-fidelity recovery path.
12. Only after obtaining the matching BSP: boot an unmodified rebuilt kernel from the clone/U-Boot first, then test ep3→INT.
