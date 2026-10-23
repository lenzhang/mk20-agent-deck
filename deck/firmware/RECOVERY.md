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

## K4 / 1.3.0-rc1 rescue

Rescue is **mk20-kbd safe mode + usb0 admin web + adb + loader A/B rollback**.
K4 never issues `qt_app2 start` or hands the panels back to stock KD. Vendor binaries
and rootfs p5 stay untouched; the previous release remains in the other A/B slot.

A crash loop (>=3 exits within 300 s) latches `--safe-mode` and supervision continues
with backoff. All panels show `MK20 SAFE MODE` and `admin: 172.30.250.2:8780` (wrapped
on small panels); GD32 typing, local PTT HID and K2 e-stop remain. USB/ECM and adb
are kept up. Safe-mode WARN alone does not cause rollback, even on probation.
A missing/dead binary, no GD32 proof, stale status, incomplete panels or panel write
errors still fail probation; the loader rolls the unconfirmed slot back. Confirmed
kbd failures are WARN and keep the admin/update path.

Open `http://172.30.250.2:8780` (the configured subnet's `.2`) in a Mac browser: the
usb0 web is on by default (`FEATURE_WEB=1`, `mk20d web`). Pair with the code shown on the
keyboard, then use 维护 → 退出安全模式 / 重启键盘程序; it publishes the
[MAINT-HOOKS.md](MAINT-HOOKS.md) requests. If the web is unavailable (or the safe-mode
screen cannot show the pairing code), use adb and A/B. To exit safe mode over adb,
publish an **empty regular file** atomically:

```sh
$MK20_ADB shell 'umask 077; d=/data/mk20/state/maint; mkdir -p "$d"; : > "$d/exit-safe-mode.new"; mv "$d/exit-safe-mode.new" "$d/exit-safe-mode"'
$MK20_ADB shell 'cat /tmp/mk20/maint/state.json /run/mk20/kbd.status'
```

`restart-kbd` similarly requests a graceful restart, never counted as a crash;
`debug` accepts only `verbose=0|1` for the next start. No arbitrary commands or paths,
no keytrace or secret readback. `use-kd`, FAT `mk20-use-kd` and `state/kbd-fallback`
are ignored with one notice per boot. No hidden keyboard combo exists.

**HID after restart:** Tina's re-bind recreates hidg0; kbd parks stale fds while alive.
A crash or maintenance restart may close those fds, leave new opens at ENXIO and/or
leave a host modifier held. Selftest WARNs, never FAILs merely for lost HID after a
restart. Reboot via watcher `cmd-reboot` to clear it; do not repeatedly restart kbd.
The kernel hidfix acceptance is separate; K4 does not add a HID guardian.

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
| blank/error panels, USB present | kbd/status/panel failure or safe mode | K4 section above, then 0 / 2 |
| Mac daemon connects time out, adb + ping work, keys dark | pty input stall (2026-10-23 on 1.6.0-rc4). 1.6.1+ heals itself: `/tmp/mk20/bridge.log` `pty input stalled N s: flushed (#k)` (once per streak), `/tmp/mk20/bridge.stalls` = total, kbd.status `host_flushes`; the Mac reconnects | INSTALL.md §6 soak `host_link` |
| Mac daemon cannot connect, ping 172.30.250.2 fails, adb works; device usb0 RX frozen (`/proc/net/dev`), health `usb.ecm_state` `stalled` / `gave-up` | ECM host→device dead with the gadget configured (2026-10-23 23:07 on 1.6.1-rc2). 1.6.2+ re-arms itself (one soft re-enumeration, ≤ 3 per hour; mk20d.log `ecm`, gadget.log `REARM`); on probation it is not confirmed (rollback). Gave up / older release: reboot via `cmd-reboot` (the proven fix); a manual re-arm is below | 1 (`cmd-reboot`) |
| USB keeps re-enumerating | flap; release flapmon requests loader A/B after 5 drops in 60 s (Mac sleep/suspend is not counted, 1.1.2+) | 0, then 1 |
| `good=` empty / `stub: … mismatch` | release or rescue files corrupt | 2 / 3 |
| nothing enumerates after a power cycle | boot image or kernel broken | 5 |

## 1. Flag files (FAT `/mnt/SDCARD/`, read at boot; then reboot)

| flag | effect |
|---|---|
| `mk20-no-composite` | composite disabled: ACM (+adb), no ECM/HID/bridge; mk20-kbd remains the only UI in K4. **Set automatically** (1.1.4+) when flapmon's rollback fails 3 times; flapmon then hard-resets. Delete it once the cause is fixed |
| `mk20-gadget-nohid` | composite without HID (ECM + ADB + bridge). Not a fix for the ep3 kernel: 1.1.x releases don't run on it at all (see level 5) |
| `mk20-no-adb` | no adb in any mode |
| `mk20-no-router` | (1.2.0+) the on-board router (`mk20d router` → `bin/mk20-router`, PTT → HID) is not started; same as `FEATURE_ROUTER=0` in mk20.conf or env `MK20_ROUTER=0`. To stop it now without a reboot: `touch /tmp/mk20-router-stop; killall mk20-router` (the stop file keeps the mk20d supervisor from restarting it; SIGTERM/SIGINT make the router send an all-keys-up report before it exits, so no modifier stays held; avoid `kill -9`). The router never gates confirm |
| `mk20-no-ecm-rearm` | (1.6.2+) the ECM RX watchdog only observes and reports (no re-enumeration); the probation gate still requires host→device traffic when a host is present, so a stall then fails probation without a re-arm. Read at every watchdog step (no reboot needed) |
| `mk20-no-release` | loader skips releases: legacy `boot-every/` jobs only (stock + adb keeper) |
| `mk20-no-watch` | boot stub does nothing at all: pure vendor behaviour, no rescue watcher, no adb |

Over adb: `$MK20_ADB shell 'touch /mnt/SDCARD/mk20-no-composite && sync'`, then reboot. Without adb, **unplug the
MK20 first** (rootfs and overlay live on the card), then put the TF card in the Mac (if macOS offers to
"Initialize" the card, click **Ignore**; never Initialize or Erase) (the FAT volume mounts in Finder), create the empty file, eject cleanly, and put the card back.
Delete the flag to undo.

**Manual ECM re-arm (1.6.2+)**, e.g. to try the soft path before a reboot: one soft re-enumeration of the running
composite, queued to the watcher (adb drops for a few seconds; HID re-enumerates; nothing else restarts):

```sh
$MK20_ADB shell 'MK20_REL=/tmp/mk20/rel MK20_GADGET_LOG=/data/mk20/logs/gadget.log sh /tmp/mk20/gadget-boot.sh --rearm'
$MK20_ADB shell 'grep -E "REARM|rearm" /data/mk20/logs/gadget.log | tail -n 5'    # after adb is back
```

It is refused (logged, nothing touched) during a setup/rollback, an install, a queued watcher job, within
flapmon's window after a link drop, or with no host configured. Never run `--rearm-now` from an adb shell.

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

- **Release gadget rollback (K4):** flapmon or `gadget.sh --rollback-now` publishes
  watcher `cmd-rollback`, leaving USB up until the A/B reset. A stale composite
  generation is ignored. This is distinct from setup's bounded failsafe, which
  quiesces HID/stops kbd before configfs changes and restores ACM+adb without KD.
  Safe-mode entry and kbd maintenance requests never tear down the gadget.
  Rolling back to a previous 1.2.x release runs that release's historical behavior.

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
