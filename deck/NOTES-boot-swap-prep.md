# In-place boot swap prep (bold path) — 2026-10-18 ~11:00 SGT

**Strategy change (user 10:48 SGT):** TF is removable → skip clone-first. Arm one USB
replug: `lunch.sh` backs up current `boot` to FAT `/mnt/SDCARD` (Mac-readable) + UDISK,
writes staged `boot-new.img`, verifies sha256 readback, reboots. On failure user pulls TF
and restores from Mac. Prefer unmodified-match boot first if both ready; else patched.

## Ready (box)

| Artifact | Path | Notes |
|---|---|---|
| Tina 5.4.61 tree | `/workspace/mk20-research/kernel-build/tina-linux-5.4` | from `paulwratt/mangopi-sbc-tina-linux-5.4` |
| ep3 ISO→INT apply | `deck/kernel/patches/apply-ep3-int.sh` | Official USB guide §3.3 example 2 |
| ikconfig (shipping) | `kernel-build/boot-extract/ikconfig` | 5.4.61 PREEMPT, `USB_SUNXI_UDC0=y`, ECM **off** |
| boot.img packer | `kernel-build/scripts/pack-bootimg.py` | Matches stock layout (`t113-pro`, `ramdisk.img\n`) |
| build script | `kernel-build/scripts/build-kernel.sh {unmodified\|patched}` | Enables ECM/NCM/HID/ACM/FFS on patched |
| lunch boot-swap | `deck/m2-scripts/lunch-boot-swap.sh` | SDCARD+UDISK backup, sha verify, one-shot |
| fail marker | `deck/m2-scripts/boot-swap-mark-fail.sh` | Restores stock on next boot |
| gadget composite | `deck/m2-scripts/gadget-ecm-hid-adb.sh` | ECM+HID(+ADB) + failsafe serial |
| pty↔TCP bridge | `artifacts/m2-pty-bridge/ttygs_tcp_bridge{,.c}` | `--listen 0.0.0.0:17890 --link /dev/ttyGS0` |
| Mac TCP transport | `deck/tools/tcp_transport.py` + tests **PASS** | `tcp:192.168.7.2:17890` |
| TF Mac restore | `deck/tools/tf-identify.sh`, `tf-restore-boot.sh` | Recovery path (whole-image) |

## ECM / IP plan

| Side | Address | Notes |
|---|---|---|
| Device `usb0` | **192.168.7.2/24** | Set in gadget script after bind |
| Mac (host) | **192.168.7.1/24** | Prefer fixed; link-local also works for ssh/adb but daemon uses fixed |
| Protocol A | TCP **:17890** on device | Via `ttygs_tcp_bridge` |
| ADB | USB FFS (if EP OK) | Optional; ECM alone enough for TCP |

### Mac auto-config check (after ECM enumerates)

```bash
# Interface name varies (enX). Look for the new Ethernet with MAC 02:11:22:33:44:56
networksetup -listallhardwareports
ifconfig | rg -A6 '02:11:22:33:44:56|192.168.7'
# If macOS didn't auto-assign .1:
sudo ifconfig enX inet 192.168.7.1 netmask 255.255.255.0 up
ping -c2 192.168.7.2
python3 -c 'from tcp_transport import TcpTransport; t=TcpTransport("192.168.7.2",17890); print("ok")'
```

macOS supports **ECM natively** (preferred). NCM also OK on recent macOS; we enable both
in kernel config, gadget uses ECM.

## Size / SDCARD gate

| Item | Size |
|---|---|
| Stock boot.img | 5 435 392 B (~5.2 MiB) |
| SDCARD (mmcblk0p1) | **8 MiB FAT** — fits stock backup + new + logs |
| Gate in lunch | refuse if new > 7 000 000 B |

## Unverified / blockers before ARM

1. **Kernel build finish** — unmodified then patched `boot-*.img` (in progress on box).
2. **vermagic / boot identity** — confirm built zImage `uname -r` string matches shipping
   `5.4.61` (LOCALVERSION empty). Live boot is the real test.
3. **`/dev/by-name/boot`** — lunch refuses to guess; must exist (seen on Tina). Confirm on
   first probe after any shell.
4. **Self-fallback depth** — U-Boot `bootcount`/alt partition **not** wired (no second boot
   slot on this image). Fallback = SDCARD `boot-stock.img` + re-armed lunch / pull-TF.
5. **Full Tina SDK bit-exactness** — tree is MangoPi Tina 5.4.61 / sun8iw20, **not** the
   Waveshare private tree. Expect possible DTS/driver drift; unmodified boot test is the
   gate before patched.

## Exact steps when ready to ARM (one USB replug)

1. Agent has `boot-new.img` + `.sha256` (unmodified-match **or** patched).
2. Via Protocol A `saveToFile` (seek 0, base64) to device:
   - `/mnt/SDCARD/boot-new.img`
   - `/mnt/SDCARD/boot-new.img.sha256`
   - `/mnt/SDCARD/lunch.sh` ← contents of `lunch-boot-swap.sh`
   - Optional dry-run first: also `/mnt/SDCARD/boot-swap.dry` then replug; pull log; remove dry.
3. User: **one USB unplug/replug** (or soft reboot if already available).
4. Watch: ACM may drop during reboot; after boot, check either:
   - Success: ECM `192.168.7.2` up, or serial returns + `/mnt/SDCARD/boot-swap-status.txt=ok-*`
   - Failure: pull TF → Mac mounts 8 MB FAT → copy `boot-stock.img` → reflash boot via
     device-side dd after stock boot, **or** whole-card restore if you took one.
5. Kill switch before arming: `saveToFile /mnt/SDCARD/mk20-no-bootswap` body `1`.

## TF insert recovery (Mac)

```bash
~/mk20-backup/tf/../  # scripts also in repo deck/tools/
./tf-identify.sh
# If whole-card image exists:
./tf-restore-boot.sh --disk diskN --whole-image ~/mk20-backup/tf/mk20-tf.img --dry-run
# Stock boot from SDCARD FAT volume after failed swap:
#   copy boot-stock.img off the TF's small FAT partition to ~/mk20-backup/tf/
```

## Build results (2026-10-18 ~11:07 SGT)

| Image | Size | sha256 |
|---|---|---|
| `boot-unmodified.img` | 4 999 168 | `afc7c70435e5c15c826c0702e89ceb1cf11355b33471f8a083305627b688b199` |
| `boot-patched.img` | 5 007 360 | `59c47e2b194e20eee4416eef94146ba3cafc6e23ee5b12ec8faa4af3c3f014b4` |
| Stock BOOT_FEX | 5 435 392 | (IMAGEWTY; different tree) |

Source: MangoPi Tina `linux-5.4.61` + shipping ikconfig + binutils-2.44 asm fixes.
Patched: ep3 ISO→INT + `CONFIG_USB_CONFIGFS_ECM=y` (+ subset) + F_ECM.
Both fit SDCARD 8 MiB gate (<7 MB).

**NOT ready to arm until:** user confirms one USB replug; prefer dry-run lunch first;
unmodified boot smoke (optional same cycle) before patched if time.

Mac copies: `~/mk20-backup/tf/boot-{unmodified,patched}.img`

## GPT review amendments (2026-10-18 ~11:10 SGT) — applied

1. **SDCARD 8MiB cannot hold new+stock** → `boot-new.img` staged on **UDISK**;
   full partition `boot-stock.img` alone on FAT SDCARD (Mac-readable).
2. Size gate: require live `PARTSIZE`; `0 < NEWSIZE <= PARTSIZE`; free-space check;
   backup size must equal `PARTSIZE` or abort.
3. Mac `tf-restore-boot.sh` now supports `--image + --slice sN` for boot-only restore.
4. `apply-ep3-int.sh` hard-fails if ep[5] INT transform incomplete (patched vmlinux
   contains `ep3-int` string — verified).

**Still NO-GO until:** dry-run lunch succeeds (backup only), then real arm with user present.
