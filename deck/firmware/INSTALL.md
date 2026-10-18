# MK20 Agent firmware: install, upgrade, Mac setup

All commands run on the Mac from `deck/` unless marked **device**. Every device command goes through adb aimed at
the MK20 **only**:

```sh
export MK20_ADB=~/mk20-backup/mkadb.sh   # picks the MK20 transport; never the watch (WATCH-SERIAL / 192.168.88.103)
```

House rules:
- Never `killall adbd` in a foreground adb shell.
- Never soft `reboot` (it hangs on this board). Reboots go through the watcher (`cmd-reboot`, which triggers a
  hardreset through the WDT).
- Anything that changes USB runs detached, with a rollback that restores ACM+KD.

## 1. Prerequisites (once)

- Back up first:
  - TF partitions: `~/mk20-backup/tf/live-221-parts/`, with sha values in NOTES-crash-20261018-and-tf-backup.md.
  - `/data`: use `tar -X tools/backup-exclude.txt`. This keeps `/data/mk20/secrets/` (the VNC password) on the
    device.
- Kernel inventory on the device: `/data/mk20/kernel/<sha12>/{boot.img,MANIFEST,RECOVERY.txt}` plus `ALLOWLIST`
  (`<p4 sha256> <name> <features>`). mk20d makes no USB changes on a p4 that isn't listed.
- Host tools: Python 3 and `uv` (for pyserial), `adb`. For the test suite, the box with BusyBox 1.27.2 at
  `/workspace/tools/bb127/busybox`.

## 2. First install (from the ad-hoc v3.5 layout)

1. **Rescue tier:** build first (`make release` creates `out/rescue/` with `.sha256` files), then:
   ```sh
   sh tools/mk20-rescue-update.sh out/rescue loader.sh hardreset.sh watcher.sh lunch.sh
   ```
   Each file goes to `/data/mk20/rescue/` and then `/mnt/SDCARD/mk20/`, verified before the atomic rename, so one
   valid copy always exists. Never update `watcher.sh` while a watcher job is running.
   The live boot stub is `/mnt/SDCARD/lunch.sh`; the tool only stages `/mnt/SDCARD/mk20/lunch.sh`. On a first install
   (or a stub change) copy it over: `$MK20_ADB shell 'cp /mnt/SDCARD/mk20/lunch.sh /mnt/SDCARD/lunch.sh.new && mv
   /mnt/SDCARD/lunch.sh.new /mnt/SDCARD/lunch.sh && sync'`. The stub starts the first verified watcher at boot.
2. **First release:** `sh tools/mk20-install.sh out/mk20-fw-<v>.tar --reboot --wait`.
3. **Legacy jobs:** once a release launches, the watcher skips `boot-every/`. The old composite job is parked in
   `/mnt/SDCARD/mk20/boot-every.disabled/`; `loader uninstall` also does this. `boot-every/adb.sh` stays as the
   legacy-mode adb keeper.

## 3. Upgrade (normal path)

```sh
(cd ~/mk20-backup/releases && shasum -a 256 -c mk20-fw-<v>.tar.sha256)   # the .sha256 holds a relative name
sh tools/mk20-install.sh ~/mk20-backup/releases/mk20-fw-<v>.tar --reboot --wait
```

- The installer pins the tarball sha and pushes it to `/data/mk20/incoming/upload.tar`. It then runs the
  verified rescue loader: `loader install`, which makes a private copy, checks the sha and free space, validates
  the tar listing, extracts into `.incoming-<v>`, compares the file set with the MANIFEST, and atomically renames
  to `releases/<v>`. Then `switch` marks the release **pending** and the loader prunes old releases.
- `--reboot` drops `cmd-reboot` on the FAT; the watcher's hardreset reboots the board about 30 s later.
- **Probation:** the loader boots the pending release, and mk20d runs a self-test (KD=1, UDC, ttyGS0, watchdog,
  bridge on the ECM IP and 169.254.7.2, HID). A pass means `confirm`, so the release becomes good. Any of these
  roll back automatically to the previous good release, with no host needed:
  - the self-test fails;
  - the composite fails its own setup during probation (1.1.1+);
  - nothing confirms within 600 s.
- If the installer refuses with "is pending", the previous install hasn't been booted yet. Reboot, or let
  probation finish.

Check:

```sh
$MK20_ADB shell 'sh /data/mk20/rescue/loader.sh status'          # current=.. good=<v> pending= ...
$MK20_ADB shell '/data/mk20/releases/<v>/sbin/mk20d health'       # JSON, "status":"ok"
$MK20_ADB shell 'cat /data/mk20/state/hardreset.last'             # fat_ro=y mount_hung=n (clean FAT remount)
```

Logs: `/data/mk20/logs/{mk20d,gadget}.log` (rotated), `/mnt/SDCARD/mk20/logs/{loader,watcher}.log`, and
`/tmp/mk20/bridge.log`.

Uninstall (back to stock ACM+KD+adb with the rescue watcher): **device** `sh /data/mk20/rescue/loader.sh
uninstall`, then reboot through the watcher.

## 4. Configuration (optional)

`/data/mk20/config/mk20.conf` is a whitelist of `KEY=VALUE` lines. It is never sourced, and invalid lines are
ignored with a warning (`mk20d config` shows the effective values).

| key | default | range |
|---|---|---|
| `ECM_SUBNET` | `172.30.250.0/24` (1.1.0+) | a /24 network address; device .2, Mac .1 |
| `ECM_DEV_MAC` / `ECM_HOST_MAC` | `02:11:22:33:44:55` / `02:11:22:33:44:56` | unicast MAC |
| `FEATURE_COMPOSITE` `FEATURE_HID` `FEATURE_ADB` | 1 | 0/1 |
| `BRIDGE_PORT` | 17890 | 1024–65535 |
| `FLAP_DROPS` / `FLAP_WINDOW_S` | 5 / 60 | 2–50 / 10–600 |
| `LOG_MAX_KB` / `LOG_KEEP` | 256 / 3 | 16–4096 / 1–9 |

Flag files on the FAT (`/mnt/SDCARD/…`) override the config: `mk20-no-composite`, `mk20-no-adb`,
`mk20-gadget-nohid`. See RECOVERY.md for the rescue flags.

## 5. Mac setup (M4, firmware 1.1.0+)

The ECM link is the Mac network service whose hardware address is **02:11:22:33:44:56** (here: en13, service
"Serial"). `tools/mk20_host_net.py` changes **only** that service. Every change is verified (`route get` must use
the MK20 interface, and ping must answer) and restored in full on failure or Ctrl-C.

```sh
python3 tools/mk20_host_net.py status       # read-only
python3 tools/mk20_host_net.py sync         # Mac side := device's active subnet (read over adb). Use after any up/downgrade
python3 tools/mk20_host_net.py apply        # 172.30.250.1/24; refuses unless the device is confirmed on 172.30.250.0/24
python3 tools/mk20_host_net.py linklocal    # add "MK20 link-local" = 169.254.7.1/16 on the same port
```

State on this Mac (since 19:01 SGT, 2026-10-18):
- **Serial** (en13): manual 172.30.250.1 / 255.255.255.0, no router, so the default route is untouched.
- **MK20 link-local** (en13, same hardware port): manual 169.254.7.1 / 255.255.0.0. This is a second service, at
  position 5 in the service order. Link-local traffic for other networks still uses the primary interface (en0).
- Service order before M4: `~/mk20-backup/netservices-before-m4.txt`.

**Undo:**

```sh
python3 tools/mk20_host_net.py linklocal-remove      # removes the "MK20 link-local" service only
python3 tools/mk20_host_net.py revert --force        # Serial back to 10.20.0.1/24 (the 1.0.x address)
# by hand, equivalent:
networksetup -removenetworkservice "MK20 link-local"
networksetup -setmanual Serial 10.20.0.1 255.255.255.0
```

`revert` without `--force` refuses unless the device actually runs a 1.0.x subnet. On a 1.1.x device you'd lose the
primary path, but the link-local path still works.

**TUN (mihomo or any other):** mihomo's `tun.route-exclude-address` already includes 172.16.0.0/12 and
10.0.0.0/8, so nothing needs changing. Check with `route -n get 172.30.250.2`, which should say `interface: en13`.
The daemon and the host tools also pin their sockets to the MK20 interface (`IP_BOUND_IF`), so a TUN can't
capture them even without the exclude.

**Daemon and tools:** these follow the device automatically.
- The daemon (`feature/p0-mvp`, `mk20deck/transport.py`) tries 172.30.250.2, then 10.20.0.2, then 169.254.7.2,
  all bound to the interface.
- `tools/tcp_transport.py connect_device()` tries 172.30.250.2, then 169.254.7.2.
- `mk20ctl` over TCP:
  `MK20_PORT=socket://172.30.250.2:17890 uv run --with pyserial python tools/mk20ctl-url.py info`.

## 6. Acceptance check and soak

```sh
python3 tools/mk20_accept.py [--reboot] [--no-hid] [--json out.json]   # checks + HID probe: two all-released reports, detached, never killed; --reboot = one WDT reboot cycle first
mkdir -p ~/mk20-backup/soak && caffeinate -i nohup python3 tools/mk20_soak.py --hours 24 --interval 300 \
  > ~/mk20-backup/soak/soak.out 2>&1 &   # unattended; caffeinate keeps a laptop from idle-sleeping
```

The soak writes `~/mk20-backup/soak/soak-<start>.jsonl` (one sample every 5 min) and a `.summary.json` that it
keeps up to date. Stop it with `kill $(cat ~/mk20-backup/soak/soak.pid)`; the summary is still written.
