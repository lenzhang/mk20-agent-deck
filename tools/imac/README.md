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
Read-only: mihomo API `127.0.0.1:9090` group `AI` + `chatgpt.com/cdn-cgi/trace` via TUN and the mixed port.
Pass = AI == `$WANT_NODE` (default `US-Exit-Hy2`) and exit `203.0.113.10` both ways.
Note: the iMac's mihomo has no `US-Exit-Hy2`; its AI group has only `US-Exit-SS` (Shadowsocks), exit
203.0.113.10 US. With the default it reports ROUTE_FAIL on node name; whether `WANT_NODE=US-Exit-SS` is
acceptable is the user's decision.

## ECM (usb0) on this iMac — not up
The composite enumerates (ECM@0, data@1, HID@2, ADB@3) but no `enX` appears: the control interface is
matched by the kext `AppleUSBECMControl` (com.apple.driver.usb.cdc.ecm) while the data interface goes to the
DriverKit `AppleUserECMData` (com.apple.DriverKit.AppleUserECM), which stays `!registered` — no network
interface, so `deck/tools/mk20_host_net.py` (keyed on host MAC 02:11:22:33:44:56) has nothing to configure.
Device side is fine (`usb.ecm_state=inactive`, usb0 172.30.250.2, bridge + web listening).
