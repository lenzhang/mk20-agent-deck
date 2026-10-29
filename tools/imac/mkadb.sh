#!/bin/sh
# mkadb.sh — adb wrapper that targets the MK20 ONLY (iMac dev-imac, 2026-10-27).
# The MK20 gadget reports an empty USB serial; adb 37.x crashes (EXC_GUARD) on it under
# macOS 15 and hides it with libusb, so this pins platform-tools r35.0.2.
# Selection: exactly one USB transport listed as "(no serial number)"; refuses the CD watch
# (WATCH-SERIAL / 192.168.88.103:5555), any network transport and any ambiguity.
# Keep `shell` payloads < 400 bytes (device adbd EOVERFLOWs > 512 B and re-enumerates the gadget).
ADB="${MK20_ADB_BIN:-$HOME/mk20-build/tools/pt-old/r35.0.2/platform-tools/adb}"
[ -x "$ADB" ] || { echo "mkadb: adb not found at $ADB" >&2; exit 90; }
unset ANDROID_SERIAL
"$ADB" start-server >/dev/null 2>&1
L=$("$ADB" devices -l 2>/dev/null | sed 1d | grep -v '^$')
N=$(printf '%s\n' "$L" | grep -c '^(no serial number)[[:space:]].*device usb:')
if [ "$N" != 1 ]; then
  echo "mkadb: expected exactly 1 MK20 transport (empty serial, usb), found $N" >&2
  printf '%s\n' "$L" | sed 's/^/mkadb:   /' >&2; exit 91
fi
T=$(printf '%s\n' "$L" | grep '^(no serial number)[[:space:]].*device usb:' | sed -n 's/.*transport_id:\([0-9][0-9]*\).*/\1/p')
case "$T" in ''|*[!0-9]*) echo "mkadb: no transport id" >&2; exit 92;; esac
if [ "${1:-}" = "--which" ]; then echo "transport_id=$T"; exit 0; fi
case "${1:-}" in -s|-t|-e|-d|connect|disconnect|pair|tcpip|usb|root|unroot|reboot|reboot-bootloader|kill-server)
  echo "mkadb: '$1' is not allowed through mkadb.sh" >&2; exit 93;; esac
exec "$ADB" -t "$T" "$@"
