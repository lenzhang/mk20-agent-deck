# iMac (dev-imac, macOS 15.8 x86_64) MK20 host tooling — 2026-10-27 SGT

The MK20 moved from dev-mbp.lan to the iMac on 2026-10-27. Install locations on the iMac:

| file | installed at |
|---|---|
| `mkadb.sh` | `~/mk20-backup/mkadb.sh` (`export MK20_ADB=~/mk20-backup/mkadb.sh`) |
| `route-check.sh` | `~/worktrees/route-check.sh` |

## adb
- The MK20 gadget reports an **empty USB serial**. platform-tools **37.0.1** on this iMac: the default macOS
  backend dies with `EXC_GUARD / INVALID_NAME on mach port` (SIGKILL) right after "Add usb device", and the
  libusb backend (`ADB_LIBUSB=1`) skips the device ("received empty serial"). **r35.0.2 works**:
  `(no serial number) device usb:20-1.1 transport_id:1`.
- Installed (user space, no sudo): `~/mk20-build/tools/pt-old/r35.0.2/platform-tools/adb` (used by mkadb.sh),
  also r34.0.5 and latest (37.0.1, unusable for the MK20) under `~/mk20-build/tools/`.
- mkadb.sh selects exactly one USB transport with an empty serial and uses `-t <transport_id>`; refuses
  ambiguity, network transports, `-s/-t/connect/reboot/root/...`. Keep shell payloads < 400 bytes.

## route-check.sh
Read-only: follows mihomo API `127.0.0.1:9090` group chain `AI > ... > leaf` and checks
`chatgpt.com/cdn-cgi/trace` via TUN and the mixed port :7890.
PASS = exit `203.0.113.10` both ways AND the AI leaf in `$OK_NODES` (default `US-Exit-Hy2 US-Exit-SS`);
prints the active node and WARNs if the leaf is not `$PREF_NODE` (default `US-Exit-Hy2`).
Exit 0 / `ROUTE_OK`, else `ROUTE_FAIL`. Env overrides: `MIHOMO_API MIHOMO_SECRET WANT_IP PREF_NODE OK_NODES`.

### mihomo routing (user decision 2026-10-27: Hy2 preferred, SS backup, exit IP is what matters)
The iMac config (`/Library/Application Support/mihomo`, root-only) already has `US-Exit-Hy2`, but only
inside the Fallback group `AI-Auto` = [US-Exit-SS, US-Exit-Hy2, US-Alt-SS]; Selector `AI` cannot pick
Hy2 directly. Runtime change via API (no config edit, no sudo), 2026-10-27 10:23 SGT:
```
PUT /proxies/AI-Auto {"name":"US-Exit-Hy2"}   # fixed preference; mihomo falls back if Hy2 dies
PUT /proxies/AI      {"name":"AI-Auto"}
```
Verified: route-check `AI=US-Exit-Hy2 (AI > AI-Auto > US-Exit-Hy2)` exit 203.0.113.10 both ways,
general connectivity OK. Backup of prior state: `~/mk20-backup/mihomo/proxies-before-20261027-1023.json`.
Revert: `PUT /proxies/AI {"name":"US-Exit-SS"}` and `DELETE /proxies/AI-Auto` (unfix).
Caveats: not written to config (survives only if mihomo store-selected persists it); if both US-Exit nodes die
AI-Auto falls to US-Alt-SS (different exit IP) -> route-check FAILs (intended guard).
Persisting needs a sudo config edit (user's call).

## Reboot
Sanctioned path only: `touch /mnt/SDCARD/cmd-reboot && sync` (watcher -> WDT hardreset ~30 s). Tested
2026-10-27 10:22:53 SGT: USB drop 10:23:18, re-enum 10:23:55, adb back 10:24:22; loader current=B good=1.7.1,
health ok, selftest pass. Never plain `reboot`.

## ECM (usb0) on this iMac — not up (root cause: macOS dext crash)
After every enumeration Apple's own DriverKit ECM driver crashes:
`/Library/Logs/DiagnosticReports/com.apple.DriverKit.AppleUserECM-2026-10-27-{094943,102417}.ips` =
SIGABRT, `__assert_rtn` in `IOService::init()` <- `IOUserNetworkEthernet::init()` <- `AppleUserECM::init()`
(assert inside the OS driver before any device I/O, i.e. not a descriptor problem). The kext
`AppleUSBECMControl` (score 59000) then takes the control interface while the data interface goes to
`AppleUserECMData` (dext score 100000) which never pairs -> no `enX`. Unified log (`/usr/bin/log`, zsh has a
`log` builtin!) shows the data server `exit before start()`.
Device side fine: usb0 172.30.250.2, bridge on 172.30.250.2/169.254.7.2:17890. Device kernel 5.4.61:
`CONFIG_USB_CONFIGFS_RNDIS=y`, NCM/ECM not built in; ECM comes from out-of-tree `usb_f_ecm.ko` in releases.
Fix options: (1) gadget CDC-NCM: build `usb_f_ncm.ko` for 5.4.61 like usb_f_ecm.ko; macOS matches it with the
kext `AppleUSBNCM` (data protocol 1, no competing dext); (2) host-side workaround without network iface:
`adb forward` to the bridge (needs bridge to also listen on 127.0.0.1); (3) macOS update / Feedback report;
no sudo/kext changes applied.

## Docker build/test image

`sh tools/imac/build-image.sh ~/worktrees/<any deck worktree>` builds `mk20-build:trixie` from the repo's
`deck/tools/build-image/Dockerfile` (procps → `pkill`, netcat). Do not keep a separate Dockerfile copy on the iMac:
the hand-kept `~/mk20-build/docker/Dockerfile` (no procps) cost 46 spurious test failures on 2026-10-27; it is now a
copy of the repo file (`Dockerfile.pre-procps` = the old one). Run the tests with `~/mk20-build/docker/run.sh`.
