# MK20 Agent firmware: recovery

Work from the top of this list down, and stop at the first level that works. Levels 1–3 need only adb, or a TF
card reader if adb is gone. Level 5 needs the TF card in the Mac. Only the whole-card restore at the end of level 5
writes the rootfs (p5) and the overlay (p6); everything else leaves them alone.
(The P0 GD32/VIA backup and restore manual is `deck/RECOVERY.md`.)

`$MK20_ADB` is `~/mk20-backup/mkadb.sh` (MK20 only, never the watch). "Reboot" always means one of:
- `$MK20_ADB shell 'touch /mnt/SDCARD/cmd-reboot && sync'`: the watcher runs a hardreset through the WDT, about
  30 s later; or
- a USB power cycle (unplug and replug), which needs a human.

Never use `reboot`: the soft reboot hangs on this board.

## 0. Look first (read-only)

```sh
$MK20_ADB shell 'sh /data/mk20/rescue/loader.sh status'
$MK20_ADB shell 'v=$(cat /data/mk20/state/good); /data/mk20/releases/$v/sbin/mk20d health'
$MK20_ADB shell 'tail -n 30 /data/mk20/logs/mk20d.log /mnt/SDCARD/mk20/logs/loader.log /mnt/SDCARD/mk20/logs/watcher.log'
$MK20_ADB shell 'cat /tmp/mk20/stub.status /data/mk20/state/hardreset.last /mnt/SDCARD/gadget-ecm-hid.status'
python3 tools/mk20_host_net.py status       # Mac side: service, routes, device subnet, ping
python3 tools/mk20_accept.py                # read-only check (writes nothing; see INSTALL.md §6 for opt-ins)
```

Symptoms and where to go:

| symptom | likely cause | go to |
|---|---|---|
| ping to 172.30.250.2 fails, adb works | Mac/device subnet mismatch (device rolled back to 1.0.x) | `mk20_host_net.py sync` |
| black KD screens, USB present | KD lost ttyGS0 (composite half-down) | 1 (`mk20-no-composite`) |
| USB keeps re-enumerating | flap; flapmon rolls back to stock after 5 drops in 60 s (Mac sleep/suspend is not counted, 1.1.2+) | 0, then 1 |
| `good=` empty / `stub: … mismatch` | release or rescue files corrupt | 2 / 3 |
| nothing enumerates after a power cycle | boot image or kernel broken | 5 |

## 1. Flag files (FAT `/mnt/SDCARD/`, read at boot; then reboot)

| flag | effect |
|---|---|
| `mk20-no-composite` | release runs in stock mode: ACM + KD (+adb), no ECM/HID/bridge. **Set automatically** (1.1.4+) when flapmon's rollback fails 3 times; flapmon then hard-resets. Delete it once the cause is fixed |
| `mk20-gadget-nohid` | composite without HID (ECM + ADB + bridge). Not a fix for the ep3 kernel: 1.1.x releases don't run on it at all (see level 5) |
| `mk20-no-adb` | no adb in any mode |
| `mk20-no-router` | (1.2.0+) the on-board router (`mk20d router` → `bin/mk20-router`, PTT → HID) is not started; same as `FEATURE_ROUTER=0` in mk20.conf or env `MK20_ROUTER=0`. To stop it now without a reboot: `touch /tmp/mk20-router-stop; killall mk20-router` (the stop file keeps the mk20d supervisor from restarting it; SIGTERM/SIGINT make the router send an all-keys-up report before it exits, so no modifier stays held; avoid `kill -9`). The router never gates confirm |
| `mk20-no-release` | loader skips releases: legacy `boot-every/` jobs only (stock + adb keeper) |
| `mk20-no-watch` | boot stub does nothing at all: pure vendor behaviour, no rescue watcher, no adb |

Over adb: `$MK20_ADB shell 'touch /mnt/SDCARD/mk20-no-composite && sync'`, then reboot. Without adb, **unplug the
MK20 first** (rootfs and overlay live on the card), then put the TF card in the Mac (if macOS offers to
"Initialize" the card, click **Ignore**; never Initialize or Erase) (the FAT volume mounts in Finder), create the empty file, eject cleanly, and put the card back.
Delete the flag to undo.

Commands the watcher polls for every 2 s:
- `cmd-reboot`: hardreset.
- `cmd-rollback`: switch to the other A/B slot, then hardreset.
- `cmd-ping`: replies "pong" on ttyGS0.
- `job.sh` with `job.sh.sha256`: verified one-shot job, 300 s cap.

## 2. Roll back the release

- **Automatic:** a pending release that fails its self-test, fails composite setup during probation (1.1.1+), or
  isn't confirmed within 600 s rolls back to `good` with at most one hard reset. Proven on hardware with 1.0.3
  rolling back to 1.0.2.
- **Manual:** `$MK20_ADB shell 'touch /mnt/SDCARD/cmd-rollback && sync'`. The watcher runs `loader rollback` and
  then a hardreset. Over a shell, `sh /data/mk20/rescue/loader.sh rollback`, then reboot.
- Afterwards, align the Mac: `python3 tools/mk20_host_net.py sync`. In a mismatch, the bound link-local path to
  169.254.7.2 still works.

- **Rollback guard (1.1.3+, reworked in 1.1.4):** `gadget.sh --rollback-now` may be requested by flapmon, the
  confirm waiter and mk20d. One owner runs it (`/tmp/mk20/rollback.once/pid`); the others wait. `done` is written
  only after stock is verified (ACM linked, UDC bound, KD running), and every caller re-verifies, so exit 0 always
  means stock is up. A dead owner is taken over automatically; an unverified rollback releases the guard and exits 1.
  A new successful composite setup clears the guard.

## 3. Uninstall the firmware, or remove the stub

- `$MK20_ADB shell 'sh /data/mk20/rescue/loader.sh uninstall'`, then reboot. This moves the slots and state aside
  (`/data/mk20/uninstalled-<ts>/`) and parks the legacy composite job. The next boot is stock ACM+KD+adb with the
  rescue watcher.
- **Remove the boot stub** (pure vendor, nothing of ours runs): delete `/mnt/SDCARD/lunch.sh`, over adb or on the
  Mac with a TF reader, or create `mk20-no-watch`. The vendor `qt_app1` only runs the stub if the file exists.
- The rescue copies are checksum-verified. The stub tries `/data/mk20/rescue` first and falls back to
  `/mnt/SDCARD/mk20`. A checksum *mismatch* runs nothing. If one copy is still good (adb works), fix it with
  `tools/mk20-rescue-update.sh`. If **both** copies fail, nothing of ours runs and there is no adb: write the good
  files (with their `.sha256`) through `overlayDir` (level 4), or delete/replace `/mnt/SDCARD/lunch.sh` with a TF reader.

## 4. overlayDir: write any file at the next boot (TF reader, no adb needed)

The vendor `/etc/init.d/qt_app1` copies `/mnt/SDCARD/overlayDir/*` onto `/` at **every** boot, then empties the
directory. This happens before `lunch.sh` runs.

With the TF card in the Mac, make the target path under `overlayDir/`. For example, to restore a vendor file from
backup: `overlayDir/data/appLunch.sh`, or `overlayDir/data/mk20/rescue/watcher.sh` together with its `.sha256`.
Eject and boot. The copy is a single shot and `overlayDir` is empty again afterwards.

- Vendor originals for comparison: the hashes in `share/vendor.sha256`; the backups are in `~/mk20-backup/`.
- Security note: anyone with the TF card gets root this way. That is inherent to the vendor image. Keep physical
  custody of the card.

## 5. Kernel (boot image, p4) restore from the Mac

Kernel images. All are 6451200 B and all report `uname` #221, so identify them **by sha only**:

| name | p4 sha256 | features | Mac copy | device copy |
|---|---|---|---|---|
| stock | `9ac68ed98a70a3b5211dc8a25cf9427afdc54543ebfe003337c77032929029a8` | acm,adb | `~/mk20-backup/tf/live-221-parts/p4.img` | `/data/mk20/kernel/9ac68ed98a70/boot.img`, `/mnt/SDCARD/boot-stock.img` |
| ep3 | `f2a19d3cb8fddc7f177cb518bb64e69645fadd3ce1b1422c4f056db39e4ee4c0` | acm,adb,ecm | `~/mk20-backup/tf/patched-ep3/p4-patched-ep3int.img` | `/data/mk20/kernel/f2a19d3cb8fd/boot.img` |
| ep5out (running) | `7326d9e5ab9fd9f140b6cc7597b4eb0539b019139d12b52b4c133771e35843cc` | acm,adb,ecm,hid | `~/mk20-backup/tf/patched-ep5out/p4-patched-ep3int-ep5out.img` | `/data/mk20/kernel/7326d9e5ab9f/boot.img` |

Steps:
1. Power off and take the TF card out. Put it in the Mac's reader. If macOS offers "Initialize…", click **Ignore**.
2. Run `bash tools/tf-identify.sh` and note `diskN`. `tf-restore-boot.sh` refuses internal disks, anything not
   3–5 GB, an s4 that isn't exactly 6451200 B, and any card whose GPT isn't exactly the MK20 layout
   (`tools/tf_gpt_check.py`: protective MBR, GPT 1.0 geometry, slots 1–6 type/start/size/attributes/name). It reads
   the card's partition tables with sudo first, so even `--dry-run` asks for your password.
3. Rehearse first: `bash tools/tf-restore-boot.sh --disk diskN --image ~/mk20-backup/tf/live-221-parts/p4.img
   --slice s4 --dry-run`.
4. Run the same command without `--dry-run`. It asks for sudo, writes slice **s4 only**, and reads back the sha.
5. Eject, put the card back, and power on.
   - **Stock or ep3 kernel:** `firmware/KERNELS` lists only ep5out, so the loader refuses every 1.1.x release
     ("kernel not in allow-list"; a pending one is marked failed). Only the legacy `boot-every/adb.sh` keeper runs:
     stock ACM+KD, plus adb if that job is still there. Flag files have no effect on releases then.
   - **Back to the production kernel:** re-flash `p4-patched-ep3int-ep5out.img` the same way.

Whole card (last resort; erases `/data` and everything else on the card):
- `--whole-image` takes only a **raw whole-card `dd` image of this card** (made as in `deck/RECOVERY.md` §3,
  e.g. `~/mk20-backup/mk20-tf-YYYYMMDD.img`). As of 1.1.4 no such image exists yet; make one before you need it,
  with its sidecar: `shasum -a 256 IMG > IMG.sha256`. Rehearse with `--dry-run`. The script refuses directories,
  Allwinner `IMAGEWTY` files, any size other than exactly the card size (so `p4.img` or `disk-head.img` can never
  land at LBA0), images without the GPT signature, and a missing or mismatching `IMG.sha256`.
- `~/mk20-backup/tf/live-221-parts/` is a **directory of per-partition images** (SHA256SUMS; p6 UDISK isn't in
  it), not a whole-card image. Never pass it to `--whole-image` and never `dd` it to the whole disk. Use only
  `p4.img` with `--image … --slice s4` as above. Restoring other partitions by hand is not documented or tested.
- The vendor `MK20_V1.0.img` is a PhoenixCard (`IMAGEWTY`) package and **must never be `dd`'d**. Flash it with
  PhoenixCard v4.2.7 on Windows in "startup card" mode, or convert it with OpenixCard first (untested here).
  See `deck/RECOVERY.md` §3. It is the vendor's open-source-licence build and may differ from the factory system.

Per-kernel notes are on the device in `/data/mk20/kernel/<sha12>/RECOVERY.txt` (source:
`firmware/kernel-notes/`).

## 6. Mac network (undo the M4 changes)

See INSTALL.md §5. In short: `python3 tools/mk20_host_net.py linklocal-remove` removes the "MK20 link-local"
service, and `revert --force` sets Serial back to 10.20.0.1/24. The service order before M4 is in
`~/mk20-backup/netservices-before-m4.txt`. No other service was changed, and the mihomo config wasn't touched.
