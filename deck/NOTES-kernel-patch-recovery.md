# Kernel patch (ep3 ISO→INT) — anti-brick recovery & flash-back plan

**Date:** 2026-10-18 (~10:00 SGT)  
**Branch:** `feature/p2-computer-use`  
**Scope:** Research only — **no flash, no FEL write, no PhoenixCard write tonight.**  
**Goal:** Ranked, evidence-backed path to free a second INT EP (`ep3-iso`→`ep3-int`) for
`ACM + HID (+ optional ECM)` without leaving the MK20 unrecoverable.

---

## Executive summary

| Question | Answer (confidence) |
|---|---|
| Where does the T113 OS live? | **Entirely on the microSD (TF) card** (~4 GB class). Confirmed. |
| Can official ScreenKey “Update Device” reflash kernel/rootfs? | **No** — it only pushes **userspace** `KeyboardDevice` (+ optional GD32 QMK bins). Confirmed. |
| Official full restore? | **PhoenixCard v4.2.7** + `MK20_V1.0.img` (IMAGEWTY). Confirmed in OSS pack. |
| Safest backup before any kernel write? | **Power off → pull TF → whole-card `dd` on Mac** (+ sha256). Highest priority. |
| Can we patch UDC without flashing? | **Probably not** — `sunxi_usb_udc` is likely **built-in** (see §4). Verify with `lsmod` once adb works. |
| Best first test of a new kernel? | **`xfel` DRAM load + exec** (RAM boot) **before** writing the `boot` partition. |
| Physical actions needed from user? | (1) Pull TF for `dd`; (2) optional open case for UART; (3) FEL pad/button on MK20 **unverified**. |

**Still prefer the no-flash composite** (`ADB + combined HID`, Protocol A over adb-forward) for P2 unless absolute pointer + ACM + ECM on one cable is mandatory.

---

## 1. Official Waveshare / ScreenKey resources

### 1.1 Wiki & product

| Resource | URL / path | Notes |
|---|---|---|
| MK20 wiki | https://www.waveshare.com/wiki/MK20 | Dual Linux+QMK architecture; Wi-Fi dongle notes |
| Product page | https://www.waveshare.com/mk20-b.htm | T113-S3 + GD32 |
| Local mirror | `/workspace/mk20-research/wiki.html` | Snapshot |
| ScreenKey module docs | docs.waveshare.com `0.85inch_ScreenKey_Module` | Key LCD only |

**Caveat:** The wiki section titled “Use Raspberry Pi Imager to Flash Latest Official Image” is a **Home Assistant / Pi template** (mentions HDMI, `homeassistant.local:8123`). It is **not** a verified MK20 T113 restore procedure. Do **not** treat RPi Imager as the official MK20 flash path unless Waveshare publishes an MK20-specific `.img` + Imager instructions separately.

### 1.2 GitHub `waveshareteam/Waveshare-ScreenKey`

- Releases **V1.0 / V1.1** = **host apps only** (Windows `.exe`, macOS `.dmg`).
- **No** T113 SDK, Tina BSP, or device kernel images in that repo.
- Local research: `/workspace/mk20-research/screenkey-gh/NOTES.md`.

### 1.3 Official firmware image + PhoenixCard (OSS pack)

Path (box):

```text
/workspace/mk20-research/dl/oss/MK Series Upper Computer Open Source_V1.0/
  …/OpenSourceLicenseFirware_V1.0/
    MK20_V1.0.img          # 848451584 B
    MK10_V1.0.img
    PhoenixCardv4.2.7.7z.7z
```

| Item | Evidence |
|---|---|
| `MK20_V1.0.img` size | 848 451 584 B |
| sha256 | `1c9885b292417436b568913531ba17fc3d1ad6a8932cefaaf3d36f3d8e0c66bc` |
| Format | Allwinner **IMAGEWTY** (PhoenixCard / PhoenixSuit), **not** raw `dd` |
| Tool in pack | **PhoenixCard v4.2.7** |
| Flash mode | “**启动卡** / Boot card” into TF (standard Tina flow) |

Unpacked IMAGEWTY contents (local dump `/workspace/mk20-research/fw/mk20_dump/`):

- `BOOT_0`, `UBOOT`, `BOOTPKG`, `VMLINUX`, `TINACONF`, `___GPT` / `___MBR`
- Payload: `boot-resource`, `env`, `env-redund`, `boot`, `rootfs`
- Parallel `VBOOT*` / `VROOTFS*` / `VENV*` = **download verification copies** in the packer, **not** a live A/B slot scheme on the running device

**Warning:** OSS “开源协议版” V1.0 may **differ** from the shipping ScreenKey/AI image (KeyboardDevice V2.32, themes). Prefer restoring a **user-made whole-TF `dd`** over `MK20_V1.0.img` when possible.

### 1.4 How V2.31 → V2.32 was pushed (official app)

From ScreenKey host strings + embedded ELF analysis (`screenkey-gh/NOTES.md`, `dl/win_strings.txt`):

1. Host builds/ships an upgrade manifest JSON, e.g. `"version": "V2.32"` with
   `fileMessage[{ name: "KeyboardDevice", path: "data/KeyboardDevice", … }]`.
2. Files land under **`/data/DeviceUpgradeFirmware`** on the device.
3. Changelog for V2.32: *“Synchronize software UI control modifications”* — **userspace only**.
4. Same channel can carry **GD32** binaries (`syk_keyboards_mk20_plus_via.bin`, etc.) via DFU-class paths in the Windows app — still **not** T113 kernel/rootfs.
5. Online endpoints (`cbytecat.com` … `qmk_firmware`, `manifest.json`) were **404** when probed earlier.

**Conclusion:** Official “Update Device” **cannot** reflash T113 kernel/UDC. Kernel recovery must use TF rewrite / FEL / U-Boot, not the ScreenKey OTA button.

### 1.5 Device-side OTA (`swupdate`)

- `S99swupdate_autorun` → `swupdate_cmd.sh`; env keys `swu_*`; log `/mnt/UDISK/swupdate.log`.
- **No** stock `.swu` package in our research tree.
- **Do not** invoke without a known-good `.swu` + TF backup.

---

## 2. Storage layout (evidence)

### 2.1 Boot medium — TF card (confirmed)

| Source | Finding |
|---|---|
| `sys_config` / RECOVERY | `storage_type = 1` → MMC/TF boot |
| Fabien UART log (2026-03-20) | BOOT0: `***Try SD card 0***` → `3840 MB` → `Loading boot-pkg Succeed`; U-Boot `storage type = 1`; `set root to /dev/mmcblk0p5` |
| M1 probe (2026-10-18) | `/dev/mmcblk0p1` **8 MiB** → `/mnt/SDCARD`; `/dev/root` ~785 MiB → `/rom`; `/dev/by-name/UDISK` **2.6 GiB** → overlay |

So: the **whole Linux system is on the TF**. The 8 MiB `SDCARD` partition is only a small FAT area used for `lunch.sh` / themes — **not** the full OS.

### 2.2 Partition map (from IMAGEWTY `sys_config` / DLINFO)

Sizes in **512-byte sectors**:

| Name | Sectors | ≈ Size | Role |
|---|---|---|---|
| boot-resource | 65536 | 32 MiB | boot assets (logo, etc.) |
| env | 504 | 0.25 MiB | U-Boot env |
| env-redund | 504 | 0.25 MiB | env redundant |
| **boot** | **12600** | **~6.15 MiB** | **Kernel / boot.fex (patch target)** |
| rootfs | 2097152 | 1024 MiB | squash/root → `/rom` |
| *(live)* UDISK | — | ~2.6 GiB | writable overlay `/` |
| *(live)* mmcblk0p1 | — | 8 MiB | `/mnt/SDCARD` FAT |

Fabien: `root_partition is rootfs` → `/dev/mmcblk0p5`. Exact `by-name` ↔ `mmcblk0pN` mapping should be re-dumped via adb (`ls -l /dev/by-name`, `cat /proc/partitions`, `/proc/cmdline`) before any write.

### 2.3 A/B / recovery partitions?

- **No evidence** of runtime A/B boot slots on the live MK20.
- `VBOOT` / `VROOTFS` in the IMAGEWTY pack = **flash-time verify blobs**, not alternate boot banks.
- U-Boot log: `no vendor_boot partition is found` — Android-style vendor_boot absent.

### 2.4 Kernel build identity (for matching modules)

M1: `Linux #221 SMP PREEMPT Mon Sep 29 02:53:25 UTC 2025 armv7l`  
Any out-of-tree `.ko` or replacement `boot` image must match this tree / vermagic (`5.4.61` …).

---

## 3. Recovery routes (ranked)

### Rank A — Whole TF card swap / rewrite (**best anti-brick**)

**Why #1:** BROM tries SD0 early; device already boots from TF. A known-good card always rescues.

| Step | Action |
|---|---|
| Backup | Power off → **pull TF** → Mac reader → `dd` whole card + `shasum -a 256` (see §5) |
| Restore (preferred) | `dd` the user’s backup image back |
| Restore (vendor) | Windows: PhoenixCard 4.2.7, load `MK20_V1.0.img`, mode **启动卡**, write TF; insert & boot |
| Alternate unpack | OpenixCard `-d` IMAGEWTY → raw image → `dd` (Mac/Linux; **not yet verified here**) |

**Physical:** yes — remove TF (tray/slot; usually **no full teardown**).

### Rank B — FEL + `xfel` / `sunxi-fel`

| Topic | Evidence |
|---|---|
| USB id | `1f3a:efe8` |
| T113-S3 enter FEL | Hardware guide: **no dedicated FEL pin**; **hold PC4 low** before power-on, then release (or empty/corrupt boot media → BROM FEL fallback) |
| MK20 FEL button/pad | **Unknown** — needs case open + continuity to PC4 / Waveshare schematic (not in our tree) |
| `xfel` | `xfel ddr t113-s3`; spinor/spinand R/W supported in upstream xfel |
| SD-boot boards | FEL is mainly for **empty/bad TF** or forced PC4; writing SPI NAND is **irrelevant** if OS is on TF — focus FEL on **RAM boot tests** and emergency boot0 |

**Physical:** likely **open case** to find PC4/FEL; or induce FEL by removing/corrupting TF (risky — have backup first).

### Rank C — U-Boot console over UART

Fabien: open case → **6 pads** on the side; USB-UART **115200**; BOOT0 + U-Boot log;  
`Hit any key to stop autoboot`.

Uses:

- Inspect `mmc part`, `printenv`, load kernel from TF without host flash tools
- Possible `sunxi` fastboot / `efex` depending on build (**not verified** on this U-Boot `2018.05-g6c4e831`)

**Physical:** **open case** + 3.3 V UART adapter. Do not connect RS232 levels.

### Rank D — PhoenixSuit / LiveSuit USB burn

- Traditional Allwinner USB burn while in FEL.
- For TF-centric products, **PhoenixCard (TF)** is the documented path in the OSS pack; USB PhoenixSuit is secondary and less evidenced for MK20 specifically.

---

## 4. Safe patch strategy (kernel / UDC)

### 4.0 Prefer still: no kernel patch

Stock EP budget allows **ADB + one combined HID** without flash (see `NOTES-computer-use-spike.md`). Use that unless ACM+HID+ECM on one cable is a hard requirement.

### 4.1 Runtime / no-flash attempts (try before any write)

#### Is `sunxi_usb_udc` a loadable module?

| Evidence | Implication |
|---|---|
| M1 listed `sunxi_usb_udc` under `ls /sys/module` | `/sys/module` includes **built-ins** — **not** proof of `rmmod` |
| Rootfs `/lib/modules/5.4.61/` has only netfilter/`fuse` `.ko`s — **no** `sunxi_usb_udc.ko` | Strongly suggests **built-in** |
| Gadget functions (`usb_f_*`) also appear built-in from earlier spike | Same |

**Action once adb works:**

```sh
lsmod | rg sunxi_usb_udc || true
modinfo sunxi_usb_udc 2>&1 | head
ls /sys/module/sunxi_usb_udc/holders 2>/dev/null
# If built-in: cannot rmmod/insmod a patched udc.ko without replacing the kernel image.
```

**Device-tree overlay / module param to flip ep3 ISO→INT:**  
Tina documents a **source change** in `sunxi_udc.h` / `sunxi_udc.c` (ep_fifo + ep[] caps). There is **no** evidenced DT property or module parameter that remaps ep3 type at runtime. Treat runtime remap as **unavailable** unless proven otherwise.

#### Official code change (Tina USB 开发指南 §3.3)

Local PDF: `/workspace/mk20-research/docs/tina-usb-guide.pdf`

> Default: 3× BULK pairs + **1× ISO (ep3)** + **1× INT (ep4)**.  
> To get **two INT**: simplest is change ISO endpoint to INT (`ep3-int`) in:
>
> 1. `drivers/usb/sunxi_usb/include/sunxi_udc.h` (`ep_fifo[]`, names)
> 2. `drivers/usb/sunxi_usb/udc/sunxi_udc.c` (`sunxi_udc.ep[]` caps / `bmAttributes`)

After patch: **2× INT** → feasible composites include **ACM + combined HID** (and **not** ACM+HID+ECM — that still needs 3 INT).

### 4.2 Build & test order (if patching)

1. **TF whole-card backup** (§5) — mandatory gate.
2. Obtain matching Tina `t113-pro` kernel source for build `#221` / 5.4.61 (BSP not in Waveshare-ScreenKey repo — need Allwinner Tina SDK or Waveshare source drop; **currently a gap**).
3. Apply ep3-iso→ep3-int per guide; build `boot` image only.
4. **FEL RAM boot test** (no flash): `xfel ddr t113-s3` → load kernel/DTB/init to DRAM → `xfel exec` (exact addresses from board DRAM map / U-Boot). Confirm USB gadget enumerates with 2 INT on Mac.
5. Only then: write **`boot` partition** (not full card) via:
   - adb/`dd` of `/dev/by-name/boot` (or `mmcblk0pX`), **or**
   - U-Boot `mmc write`, **or**
   - replace `boot.fex` in a PhoenixCard image (heavier).
6. Keep previous `boot` image on Mac + on unused TF partition / file for instant rollback.
7. If boot fails: restore TF from Rank A backup (pull card).

### 4.3 What “ACM+HID+ECM” really needs

| INT consumers | Count |
|---|---|
| ACM | 1 |
| HID (combined reports) | 1 |
| ECM/RNDIS/NCM | 1 |
| **Total** | **3** |

ep3→int only yields **2** INT. So **ACM+HID+ECM still does not fit** after this patch alone. Options: drop ACM (Protocol A over adb), drop ECM (use adb reverse/forward or PPP), or further EP/FIFO surgery beyond the guide’s simple ISO→INT example.

---

## 5. Full backup plan

### 5.1 Gold standard (physical TF) — do this before any kernel work

```bash
# Mac, after clean shutdown of MK20 and card removal
diskutil list   # identify ~4GB TF — VERIFY size/name carefully
diskutil unmountDisk /dev/diskN
sudo dd if=/dev/rdiskN of=~/mk20-backup/mk20-tf-$(date +%Y%m%d).img bs=4m status=progress
shasum -a 256 ~/mk20-backup/mk20-tf-*.img | tee -a ~/mk20-backup/SHA256SUMS
diskutil eject /dev/diskN
```

Store at least one copy off the laptop if a kernel experiment is planned.

### 5.2 Once adb works (supplementary, not a substitute for §5.1)

```sh
mkdir -p /mnt/SDCARD/backup-$(date +%Y%m%d)
ls -l /dev/by-name /dev/mmcblk* 
cat /proc/partitions /proc/cmdline /proc/mtd
for n in boot-resource env env-redund boot rootfs UDISK; do
  [ -e /dev/by-name/$n ] || continue
  dd if=/dev/by-name/$n of=/mnt/SDCARD/backup-$(date +%Y%m%d)/$n.img bs=1M status=none
  sha256sum /mnt/SDCARD/backup-$(date +%Y%m%d)/$n.img
done
# pull via adb pull / TF reader
```

Also save: `fw_printenv`, `dmesg`, `lsmod`, `/proc/config.gz` if present, `uname -a`.

### 5.3 lunch.sh emergency dump

Possible but **8 MiB SDCARD is too small** for rootfs/boot images. Use only for small logs/checksums unless writing to UDISK (`/overlay` / `/mnt/UDISK`) then pulling.

---

## 6. Ranked plan to execute later (gates)

| Step | Gate | Flash? |
|---|---|---|
| 0 | Prove ACM+ADB on Mac (current morning hook) | No |
| 1 | Prefer ADB+HID design — skip kernel if acceptable | No |
| 2 | User: whole TF `dd` + sha256 | No (host only) |
| 3 | adb: confirm `sunxi_usb_udc` modularity; dump partitions | No |
| 4 | Obtain matching Tina kernel source; build ep3→int | No |
| 5 | `xfel` RAM boot test of new kernel | No persistent write |
| 6 | Write **boot** partition only; keep old boot.img | **Yes (narrow)** |
| 7 | If brick: rewrite TF from §5.1 or PhoenixCard `MK20_V1.0.img` | Restore |

---

## 7. Confirmed vs unknown

### Confirmed

- OS boots from **TF**; U-Boot `storage type = 1`; root `mmcblk0p5` (Fabien).
- Partition names/sizes from IMAGEWTY sys_config; live overlay + 8 MiB SDCARD from M1.
- Official app update = **KeyboardDevice** (and GD32) only — not kernel.
- Official full image + **PhoenixCard 4.2.7** in Waveshare OSS pack; img sha256 known.
- Tina documents **ep3-iso→ep3-int** in `sunxi_udc.h/.c`.
- UART pads exist (open case); 115200; autoboot interruptible.
- FEL USB id; T113-S3 FEL via **PC4** (SoC guide).
- `xfel` supports `t113-s3`.

### Unknown / needs user physical action

| Item | User action |
|---|---|
| Exact TF geometry & `by-name` ↔ `pN` on **this** unit | After adb, or mount TF on Mac and record GPT |
| Matching Tina BSP / kernel source for build `#221` | Obtain from Waveshare support or Allwinner Tina drop |
| MK20 **FEL** button or PC4 test pad location | **Open case** / ask Waveshare for schematic |
| PhoenixCard on this Mac (need Windows VM) / OpenixCard verify | Host setup |
| Whether `sunxi_usb_udc` can ever be modular on this build | adb `lsmod`/`modinfo` |
| Whether U-Boot has fastboot | UART interrupt + `help` |

---

## 8. Artifact index

- M1 probe: `~/mk20-backup/m1-probe/probe-20261018.log`
- Fabien MK20 UART boot: https://www.fabienm.eu/wordpress/2026/03/20/mk20-boot/
- Tina USB guide: `/workspace/mk20-research/docs/tina-usb-guide.pdf` §3.3
- IMAGEWTY dump: `/workspace/mk20-research/fw/mk20_dump/`
- OSS firmware: `…/OpenSourceLicenseFirware_V1.0/MK20_V1.0.img`
- ScreenKey update notes: `/workspace/mk20-research/screenkey-gh/NOTES.md`
- Prior spike: `deck/NOTES-computer-use-spike.md`, `deck/RECOVERY.md`

---

*Author: Grok Bot executor · 2026-10-18 · research only · no device flash*

---

## 9. Cursor review amendments (2026-10-18 ~10:05 SGT)

Independent ask-mode reviews (`claude-opus-5-5-max`, `gpt-5.6-sol-xhigh`); mihomo
confirmed `api2.cursor.sh` → `US-Exit-Hy2|AI`. Artifacts:
`deck/reviews/kernel-plan-*.md`.

### Accepted blockers (must clear before any kernel write)

1. **Goal honesty:** ep3→INT enables **ACM + combined HID** (2 INT), **not**
   ACM+HID+ECM (still 3 INT). Also stock kernel lacks `usb_f_ecm` until a separate
   module/build — treat ECM as a later milestone, not bundled into this patch.
2. **Boot-tested spare TF clone:** a `dd` + sha256 is necessary but not sufficient.
   Restore onto a **spare** card of equal-or-larger size, **boot the MK20 from it**,
   smoke-test ACM/`getInfo`/UI, and keep the **original TF untouched** in a safe place
   while experimenting on the clone.
3. **Live partition map** on the shipping card before selecting a write target
   (`/dev/by-name`, start/size, cmdline) — do not assume V1.0 IMAGEWTY geometry ≡ V2.32.
4. **Matching BSP:** need vendor 5.4.61 source + `.config` + DTB + pack recipe; first
   boot an **unmodified rebuilt** `boot` image on the clone before applying ep3→INT.
5. **FEL RAM boot** is only an outline until addresses/DTB/cmdline and MK20 FEL entry
   are proven — alternatively use UART/U-Boot alternate load on the clone. Prefer
   **clone + U-Boot** over unproven FEL as the safety gate if FEL pads stay unknown.
6. **Disarm** any leftover `lunch.sh` / gadget experiments before cloning so the
   backup is a clean baseline.

### Softened wording

- `sunxi_usb_udc`: "**likely** built-in" pending `/proc/modules` + `config.gz` once adb works
  (reviewer agrees `/sys/module` alone is insufficient; missing `.ko` still strong).
- FEL/case-open: **optional** if all writes stay on a proven spare TF clone; **required**
  only if using FEL RAM boot or UART as the primary safety gate.

### Revised physical sequence (user)

1. Do **not** write a kernel yet.
2. Confirm stock ACM + disarm hooks.
3. Power off → pull TF → photograph orientation → whole-card `dd` + sha256 + note capacity.
4. Write image to **spare** TF → boot MK20 from spare → smoke-test → store original TF safely.
5. On clone: collect live partition map, U-Boot log (open case + UART if willing), config.
6. Only after BSP in hand: unmodified rebuild boot test on clone, then ep3→INT.
