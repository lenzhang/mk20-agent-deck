# MK20 hang 2026-10-18 ~12:52 SGT: root cause, recovery, TF backup

## Root cause (agent error)
- The mk20-add-adb.sh v2 helper ran in the FOREGROUND through `adb shell`. Its "already composite" check
  failed (the ep1/UDC condition), so it ran `killall adbd`. That killed the adb session running it. The
  script died after unbinding the UDC and before rebinding, so the T113 dropped off USB.
- KeyboardDevice (qt_app2) lost ttyGS0 and stalled, leaving the screens backlit but black. The GD32 was
  unaffected. A replug (power cycle) recovered it on the stock kernel.
- Fix: the v2.1 helper redirects adbd's stdio (`</dev/null >/tmp/adbd.log`) and uses
  `adbd_up = pidof || pgrep`. It must never run in the foreground from adb; use
  `setsid sh ... </dev/null >/dev/null 2>&1 &` or a watcher job.

## Secondary issue: boot race
- boot-every/adb.sh v2 ran 17 s after boot, before KD's `setusbconfig serial` had created g1
  ("FAIL no g1"). The keepalive re-adds then failed ("adbd died"), which made ACM blip.
- v2.2 waits up to 120 s for g1 to be ready (UDC bound + acm.usb0 linked + /dev/ttyGS0 + KeyboardDevice),
  then waits another 5 s. If g1 never becomes ready, it skips and changes nothing.
- The v2.1 helper ran as a watcher job at uptime 142 s: OK, ACM + ffs.adb, adbd running, KD getInfo
  V2.32 OK.

## TF backup (via adb pull of block devices; device sha == local sha)
| part | size | sha256 |
|---|---|---|
| disk head (sectors 0..41463: boot0/uboot/GPT) | 21229568 | fc5120bef68599d3dab3ca18210d535f9b27f6071e42e9917c7bdc7d60d79da4 |
| p1 boot-resource/SDCARD | 33554432 | 171cba89751ab053e4ab8396840ecb058c90c3c3f450a86b416131d0477c1252 |
| p2 env | 258048 | be8b60fed84878474c09394985a2d5e2cd18d8536dca853a38bad74560fd504b |
| p3 env-redund | 258048 | be8b60fed84878474c09394985a2d5e2cd18d8536dca853a38bad74560fd504b |
| p4 boot (#221) | 6451200 | 9ac68ed98a70a3b5211dc8a25cf9427afdc54543ebfe003337c77032929029a8 |
| p5 rootfs | 1073741824 | d1d8c96b25f995b6d5b5bd9b5adcdcf824faced1464b4bccd348e7f869b9a2e1 |
- Not backed up: p6 UDISK (2.7 GiB, rw overlay).
- Copies: Mac ~/mk20-backup/tf/live-221-parts/ and box /workspace/mk20-backup/tf/.

## 13:17 reboot test (mk20ctl reboot, stock #221, boot partition untouched)
- The watcher accepted cmd-reboot and the T113 dropped off USB at 13:17:09 SGT. Neither the T113
  (ACM/adb) nor anything else from the MK20 re-enumerated within 10+ min, so the soft `reboot` did not
  bring the unit back. A replug (power cycle) recovered it at 12:5x. The suspicion is that a soft
  reboot hangs in shutdown or in the BROM/SPL path on this board; to be confirmed after the next
  replug (see logs/watcher.log, every-adb.log).
- Consequence: bootswap.sh ends with `reboot`. Any swap test must expect that a human power cycle is
  needed after the write, so the "auto-restore on failure" path can't rely on a soft reboot.

## Independent verification of boot-221-ep3int.img (verify-staged.py, separate from mkboot221.py)
- sha 38703734a1c78407b5db77e05d3f0961a5561d4b0970ee0077d55afbc5bc1ece, 5433344 B (prefix of a 6451200 B partition)
- Header page identical to stock (ks 5429672, page 2048, ramdisk 12 B "ramdisk.img\n" beyond the prefix, untouched).
- zImage decompressor prefix (15884 B) and tail identical. piggy length identical (5413726).
- Decompressed Image diffs: exactly 0xd326d8 0x32->0x38, 0xd326f5 0x01->0x03.
- Expected full-partition sha after the write: f2a19d3cb8fddc7f177cb518bb64e69645fadd3ce1b1422c4f056db39e4ee4c0

## 14:0x kernel-only test: PASSED, patched ep3 kernel is the new baseline
- p4 = f2a19d3cb8fddc7f177cb518bb64e69645fadd3ce1b1422c4f056db39e4ee4c0. Host confirmed at ~14:07 SGT
  (SHOW_JPG test pattern seen by the user, KD getInfo V2.32).
- Copies: Mac ~/mk20-backup/tf/patched-ep3/p4-patched-ep3int.img, box /workspace/mk20-backup/tf/patched-ep3/,
  device /data/mk20/boot-baseline.img (+.sha256). Stock copies (FAT, /data, Mac, box) are unchanged.
- ep3 is INT on hardware: the host-side config descriptor shows the ACM notify endpoint at **0x83** attr 3
  (the stock kernel used 0x84).

## postswap auto-revert did not fire: root cause + fix
- This Tina busybox has **no setsid and no nohup**. v1 started the 600 s confirm wait with
  `setsid sh -c ...` and its stderr went to /dev/null, so the detached waiter never started. The log
  stopped at "waiting up to 600s".
- Fix: start the waiter as `( trap '' HUP INT; exec sh -c ... ) &`, reparented to init once the job exits,
  then check it is alive with `kill -0`. If the detach fails, revert immediately. Tested on the device
  in a sandbox (fake p4 under /data): timeout -> revert -> stock sha. Also tested in a Mac simulation.
- Related: **adbd kills the whole process group of an adb shell session** when it ends (trap HUP is not
  enough). So "detached" scripts started from adb die too. Long-running USB scripts (gadget v3,
  ecm-bind-test) now queue themselves into the watcher (`/mnt/SDCARD/job.sh` + sha). That is the likely
  mechanism of the 12:52 crash.

## ECM bind test on the patched kernel (ecm-bind-test.sh, via watcher, auto-rollback)
- usb_f_ecm.ko insmod OK. ACM + ADB + ECM bound. Host config:
  - ACM notify 0x83 INT
  - ACM bulk 0x81/0x01
  - ADB 0x02/0x82
  - ECM notify 0x84 INT
  - ECM data 0x85/0x05 (ep5 pair)
- macOS created en13 (02:11:22:33:44:56, active, 100baseTX).
- **Traffic: device->host OK** (all 7 frames the device sent reached the host). **host->device FAILED:**
  the host sent 114 frames and usb0 got 1 RX. So bulk OUT on **ep5out** delivered only the first transfer.
  ep1/ep2 OUT are fine (ACM, ADB). Suspect the ep5out DMA/double-FIFO RX path.
- The rollback after the 180 s keep window worked: ACM + ADB restored, KD and adbd unchanged, getInfo OK.

## 14:4x ECM at MTU 478 works (Opus DMA hypothesis confirmed)
- Same ECM bind test (ECM data on the ep5 pair) with `ifconfig usb0 mtu 478` set before `up`: 300 pings of
  450 B at 20 ms intervals, 0 % loss, about 1.25 ms average. With MTU 478 every OUT request is at most
  512 B, so it uses PIO. Larger requests go through the sunxi_udc DMA/bounce path, which is broken (it
  also risks memory corruption on every endpoint). Note: IPv6 is disabled at an MTU below 1280, so use
  IPv4 link-local (Mac en13 169.254.x.x, device 169.254.7.2). Over-MTU frames from the host are dropped.
  TCP is fine thanks to MSS.
- gadget v3 fixes from the Opus composite review:
  - KDSVC is hardcoded to qt_app2. qt_app1 is a plain sh script that kills adbd.
  - The script checks that KD stays stopped.
  - MTU 478 is set before `up`.
  - `--rollback` goes through the watcher queue; the waiter uses `--rollback-now`.
  - The bridge runs under a supervisor loop (with a stop file and a pidfile).
  - The bridge drops data instead of blocking when the client is slow.
- postswap re-arms itself in boot-once until the swap is confirmed or reverted (a power cycle no longer
  disarms it). bootswap now refuses to run without postswap-check.sh.

## 14:44 first composite run (no-HID: ECM + ADB + KD over the PTY<->TCP bridge) on the ep3 kernel: OK, confirmed
- gadget v3 queued via the watcher. Links: ecm.usb0 + ffs.adb (no ACM). KD restarted on /dev/ttyGS0, a
  symlink to /dev/pts/0. setusbconfig is shadowed. MTU is 478.
- Mac en13 169.254.x.x <-> device 169.254.7.2: ping 0 % loss. `mk20ctl info` over TCP (deck/tools/en-relay.py
  binds to en13, then mk20ctl-url.py with socket://127.0.0.1:17891) returned V2.32. SHOW_JPG
  ("ECM+TCP 网络通道 OK") was ACKed by the device. adb works alongside. Confirmed with mk20-composite-ok.
- Both composite reviews (deck/reviews/composite-*.md): (c) no-HID GO. GPT's (a) was conditional
  (noreboot + independent verification), Opus's (a) was GO-with-conditions. Both say (b) the full
  composite is NO-GO until the swap is confirmed and the no-HID run has passed.

## 14:47 second swap written live (bootswap job, noreboot): boot-221-ep3int-ep5out.img
- readback 1fc8525a…; full p4 = 7326d9e5ab9fd9f140b6cc7597b4eb0539b019139d12b52b4c133771e35843cc.
  Stock copies kept; the baseline (ep3) is backed up at /data/mk20/boot-baseline.img + Mac + box.
- postswap (re-arming) is staged in boot-once. mk20-no-bootevery is set for the first boot. The status
  is "ok-power-cycle-now". On failure, or with no confirmation in 600 s, it auto-reverts to **stock**.

## Remote hard reset (15:3x SGT) — design, NOT yet tested on hardware
- Soft reboot hang RCA (from kallsyms + tina54 source): `psci_sys_reset` exists, so ARM `machine_restart()` uses
  `arm_pm_restart = psci_sys_reset` (PSCI SYSTEM_RESET to secure firmware) and never reaches `sunxi_wdt_restart`.
  Before that the reboot notifiers stop the sunxi WDT (`watchdog_reboot_notifier`, `sunxi_wdt_shutdown`), so a hung
  PSCI call is never rescued. Matches 13:17: no USB at all, GD32 gone (hub held/powered by T113), until power cycle.
- sysrq: enabled (1). `b` -> emergency_restart -> same PSCI call, but skips notifiers, so the WDT procd armed stays armed.
- devmem: CONFIG_DEVMEM not set -> no register poke.
- procd holds /dev/watchdog (fd 3), `ubus call system watchdog` = running, timeout 16, frequency 5, magicclose false,
  API supports `stop`. procd stop = cancel pings (+ close without 'V') -> core logs "watchdog did not stop", HW fires.
- mmc0 3.3 V high-speed (not UHS) -> no 1.8 V warm-reset trap for the boot ROM.
- Implementation: remote/hardreset.sh (sync; ubus watchdog stop; HW WDT reset <=16 s; fallback after 30 s sysrq s,u,b).
  watcher.sh cmd-reboot now calls it (reboot only as last resort) => `mk20ctl reboot` uses it from the next boot
  (running watcher is a /tmp copy). Deployed to SD: watcher fd24a6cf…, hardreset ce61e3df…; backup watcher.sh.bak-pre-hardreset.

## 15:37-15:45 SGT: WDT hard reset PROVEN, ep5out confirmed, full composite flaps -> rolled back
- hardreset.sh via watcher job at uptime 6322.7 (procd WDT pings stopped) -> USB dropped 15:38:02, ACM back 15:38:36
  (34 s after drop), getInfo V2.32 OK. Clean cold-like boot (uptime restarted, postswap ran). `mk20ctl reboot` uses it
  from now on (new watcher active since this boot).
- ep5out kernel confirmed by host at uptime 52 (status patched-confirmed), p4 = 7326d9e5…; device baseline updated
  (/data/mk20/boot-baseline.img = ep5out; previous ep3 kept as boot-baseline-ep3.img). Backups: Mac
  ~/mk20-backup/tf/patched-ep5out/, box /workspace/mk20-backup/tf/patched-ep5out/. mk20-no-bootevery removed (adb at boot).
- Full composite (ECM+HID+ADB+bridge) bound OK (links ecm.usb0 hid.usb0 ffs.adb, /dev/hidg0, usb0 mtu 478).
  HID works: absolute pointer report moved the Mac cursor exactly +6,+6 px; shift tap written OK. Confirmed at uptime 202.
- BUT the link flapped from the first second: Mac log shows "terminateDevice … hardware connection lost" every ~3 s
  (device-side disconnect), alternating enumerations, transaction errors on 0x81/0x82/0x84 then ep0/0x83, ep0 stalls.
  Device dmesg: sunxi usb manager `rmmod_device_driver`/`insmod_device_driver` (+ "get power supply failed") per cycle;
  something clears dmesg so only the last cycle survives. en13 never got IPv4 (DHCP/linklocal never settled) -> ECM ping
  and TCP bridge untested. The no-HID composite (ECM+ADB) was stable >1 h, so the HID function (ep4 INT IN and/or
  patched ep5out INT OUT) is the prime suspect.
- Rolled back via --rollback at uptime 367: FAILSAFE done, ACM+KD up, adb re-added by keepalive, getInfo OK, no flaps.
- Logs: deck/logs-mac-usb-flap-20261018-1540.txt, deck/logs-dm-flap-20261018-1543.txt.

## 15:45-15:57 SGT: ROOT CAUSE of the composite flapping + working ECM+HID+ADB composite
- Isolation (deck/m2-scripts/hid-bind-test.sh, ACM+ADB+HID, no ECM, auto-rollback, live `cat /proc/kmsg` to SD,
  Mac `log stream` to ~/mk20-backup/remote-logs/usb-hid-*.log via ~/mk20-backup/hidtest-run.sh):
  - full HID, KD running: 34 host disconnects / 40 s -> flapping reproduced WITHOUT ECM.
  - full HID, KD stopped: 0 rmmod -> KD is the trigger.
- Device-side cycle: CONFIGURED -> "ERR: Operation not supported" -> ~2.5 s later usb manager rmmod/insmod.
  hw_scan_debug showed the scan thread idle, so the rmmod came from a read of usbc0/usb_device (device_chose()).
- KeyboardDevice (not stripped) UsbCDCStateThread::run(): loop { QProcess("dmesg -c"); if output contains
  "ERR: Operation not supported" -> usbCdcDeviceError() -> `cat .../usbc0@0/usb_device` (USB re-init); sleep(3) }.
  (This is also why dmesg kept being empty.) sunxi_udc prints that line whenever a gadget setup() returns
  -EOPNOTSUPP; tina f_hid 5.4 stalls SET_REPORT on ep0 and SET_IDLE (unknown request), which macOS sends to a
  boot keyboard. ep5out / INT endpoints are NOT the problem. f_hid 5.4 has no no_out_endpoint option.
- Fix (no kernel change): KD gets a PATH-first `dmesg` shim that filters that one line
  (/tmp/mk20-shim/dmesg; /data/appLunch.sh bind-mounted with `export PATH=/tmp/mk20-shim:$PATH` after qt_env).
  ACM+HID test with shim: only KD's one-off startup reset, then stable.
- gadget-ecm-hid-adb.sh v3.1 (sha 37682d13…) includes the shim (+ umount in cleanup). Full composite 15:55:
  one KD-startup reconnect at 15:55:39, then stable; ECM ping 30/30 (0.7-1.8 ms), getInfo over TCP V2.32,
  SHOW_JPG over TCP ACKed, adb OK, HID pointer moved the Mac cursor +6,+6 px exactly, shift tap OK.
  Confirmed (mk20-composite-ok) at uptime 1094.

## 16:00-16:12 SGT: composite ON BY DEFAULT at boot, fixed addressing, daemon TCP-first
- boot-every/composite.sh v1 (sha edefbf00…): waits for KD's g1 (ACM+ttyGS0+KD, <=120 s), +20 s, then
  `gadget-ecm-hid-adb.sh --boot` (sha-checked against /data/mk20/gadget-ecm-hid-adb.sh.sha256).
  Kill switch: /mnt/SDCARD/mk20-no-composite (then boot-every/adb.sh v2.3 gives ACM+KD+adb as before).
  adb.sh v2.3 (sha 7f3c1493…) skips itself (and its keepalive) when the composite job is enabled+valid.
- gadget-ecm-hid-adb.sh v3.2 (sha ab3c7e08…): --boot = no host confirm; detached flap monitor
  (/tmp/mk20-flapmon.sh polls /sys/class/udc/<udc>/state every 0.3 s; >=5 configured->down drops in 60 s ->
  --rollback-now). Every rollback ends with readd_adb (ACM+KD+adb). usb0 10.20.0.2/24 + usb0:1 169.254.7.2/16,
  fixed MACs dev 02:11:22:33:44:55 / host 02:11:22:33:44:56.
- Mac: `networksetup -setmanual Serial 10.20.0.1 255.255.255.0` (service "Serial" = en13, no router -> default
  route untouched). mihomo (read-only check): tun.route-exclude-address has 10.0.0.0/8 and rule
  IP-CIDR,10.0.0.0/8,DIRECT -> no proxy change; `route -n get 10.20.0.2` -> en13.
- Deck daemon (branch feature/p0-mvp 6c3bce7): find_ports() returns tcp://10.20.0.2:17890 first when reachable,
  then ACM; TcpTransport binds to the ECM iface (found by MAC) via IP_BOUND_IF; route self-check warns if the
  route goes via utun* with bypass rules for mihomo/Surge/sing-box/WireGuard; never edits proxy configs.
- E2E 16:02: `mk20ctl reboot` over TCP (watchdog hard reset) -> link down +11 s, ACM (KD stock) +47 s, composite
  +71 s (uptime 55 OK mode=--boot; one KD-startup drop at 70 counted by flapmon), adb skipped by adb.sh and
  provided by composite. Ping 50/50 ~1 ms, getInfo over TCP V2.32, daemon find_ports->tcp handshake 640x656
  + SHOW_JPG ACK, adb OK, HID matched by macOS (AppleUserHIDDevice), reports written OK. Stable 8+ min
  (4 host terminates total, all during boot). 15:40 keepalive (pid 2263) killed before the reboot.

## 16:28 SGT: failsafes proven on hardware (gadget v3.5, sha 73b96c83)
- Flap rollback (16:1x): 6 soft disconnect/connect cycles on the UDC -> flapmon "5 in 60s -> FLAPPING -> rollback"
  -> FAILSAFE -> ACM+KD+adb. Found /data/appLunch.sh still bind-mounted (EBUSY) -> v3.3: umount after KD exits.
- Forced boot failure (one-shot hook /mnt/SDCARD/mk20-composite-failtest, consumed on use):
  - #1 (v3.3): rollback OK and no shim mounts, but TWO KeyboardDevice (manual-fallback race) -> v3.4 procd-only kd_start.
  - #2 (v3.4): still TWO KD. Cause: procd respawns appLunch.sh right after `qt_app2 stop`; killing that sh
    afterwards orphaned its fresh KD (PPid 1). v3.5: kd_stop() kills appLunch.sh first, then KD, and loops until
    both stay gone (15 s cap, then KILL); kd_start reaps orphan KDs (parent not appLunch.sh).
  - #3 (v3.5, 16:21): FAIL FAILTEST -> FAILSAFE -> adb re-added (KD dropped it once, loop re-added) -> FAILSAFE
    done acm=y; exactly 1 KD, no shim mounts, g1 = acm.usb0 + ffs.adb, Mac /dev/cu.usbmodem1101 present.
    Rollback takes ~2 min end to end (most of it is the readd_adb loop).
- Restore (16:25 cmd-reboot -> WDT): composite up at 57 s uptime (ecm+hid+adb), 1 KD, en13 10.20.0.1, ping 1 ms.

## 16:36 SGT: VNC milestone pre-check (read-only, nothing changed on the Mac)
- macOS 26.6.2. Screen Sharing is ALREADY enabled (launchd com.apple.screensharing => enabled, :5900 listening).
- From the T113 over ECM: `nc 10.20.0.1 5900` -> "RFB 003.889" (Apple's RFB variant: ARD/Apple auth with a macOS
  account, or the legacy VNC password if "VNC viewers may control screen with password" is set). So the PRD §14
  capture path (T113 -> Mac VNC over the USB link) is reachable today with no proxy change.
- Still needs the user: which credential to use (a dedicated macOS account is recommended, or the VNC password).
  It is stored 600 on the T113 and never uploaded. Also Screen Recording/control consent if macOS prompts.
- VNC work resumes after packaging M0.5-M2 (plan v0.2).

## 16:50 SGT: M0.5 reset path. 16:37 hang root cause + hardreset v2.1 verified
- 16:37 hang: busybox 1.27 `flock` has no `-w`. hardreset v2 ran `flock -w 600` -> usage error -> logged
  "REFUSED: kernel-write lock held" (713.21) and returned. The watcher still running from the /tmp copy was the OLD
  v2, which falls back to `reboot` -> PSCI hang -> power-cycle (user, 16:46). The RO remount was never reached.
- Fixes: v2.1 polls `flock -n` (600 s cap); remount ro is backgrounded and bounded (5 s). bootswap/postswap use
  `flock -n`. Watcher v2.1 (now running) has no `reboot` fallback: a refused reset reports reboot-failed and keeps
  the board up.
- Verified 16:49: cmd-reboot -> lock ok -> remount ro EBUSY (files open; synced, benign) -> pings stopped -> USB
  drop after 15 s -> adb back 61 s later -> composite OK at 57 s uptime, 1 KD, ping 10.20.0.2 1.2 ms.
- Kernel inventory: /data/mk20/kernel/{9ac68ed98a70 stock, f2a19d3cb8fd ep3, 7326d9e5ab9f ep5out}/{boot.img,MANIFEST}
  + ALLOWLIST (sha -> features). Running p4 = ep5out.
- Device recovery path noted for RECOVERY.md: qt_app1 copies /mnt/SDCARD/overlayDir/* onto / at every boot and then
  empties it. A Mac with a TF reader can drop files there (and a security note: same path = root on boot).

## 16:58 SGT: VNC credential install + auth check
- Password installed at /data/mk20/secrets/vnc.pass (dir 700, file 600, root:root, 6 bytes). Path: box env -> 600
  file -> CopyFromBox into a 700 Mac dir -> adb push -> `rm -P` on the Mac + rm on the box. The box has no ssh/LAN
  route to the Mac, so a direct stdin stream wasn't possible. No copy remains on the Mac or the box. Never printed.
- Backups must exclude it: deck/tools/backup-exclude.txt (use with `tar -X`). The 16:33 /data backup predates the
  secret.
- Checker: m2-scripts/vnc/vnc_authcheck.c + des.c, static ARM, /data/mk20/vnc_authcheck. RFB 3.8 + VNC auth type 2.
  Never prints the password. DES verified against the FIPS vector and openssl.
- Result 16:57 from the T113 -> 10.20.0.1:5900: server offers type 2, but `SecurityResult != 0` (wrong password).
  Mac (read-only): VNCLegacyConnectionsEnabled=1, but /Library/Preferences/com.apple.VNCSettings.txt was last
  modified 2026-09-13 11:16. The password set "today" may not have been applied, or the env value differs.
  -> user: re-enter the VNC password in Screen Sharing settings and press OK (or confirm the env value). Then rerun
  `/data/mk20/vnc_authcheck 10.20.0.1 5900 /data/mk20/secrets/vnc.pass`.
