#!/bin/sh
# MK20 boot stub v2 (FAT /mnt/SDCARD/lunch.sh; qt_app1 copies it to /data/lunch.sh and runs it synchronously, so it
# must return at once). Starts the RESCUE watcher only; the watcher runs the release loader later (after KD).
# Rescue home candidates, first whose watcher.sh matches its .sha256 wins: /data/mk20/rescue (ext4), /mnt/SDCARD/mk20.
# If none verifies, the FAT watcher runs unverified (v1 behaviour): remote recovery beats integrity here.
# Disable everything: /mnt/SDCARD/mk20-no-watch (or delete this file).
R=${MK20_ROOT:-}
[ -f "$R/mnt/SDCARD/mk20-no-watch" ] && exit 0
mkdir -p "$R/tmp/mk20" 2>/dev/null
for H in "$R/data/mk20/rescue" "$R/mnt/SDCARD/mk20"; do
  W=$H/watcher.sh
  [ -f "$W" ] && [ -f "$W.sha256" ] || continue
  e=$(cut -d' ' -f1 "$W.sha256" 2>/dev/null)
  T=$R/tmp/mk20-watcher.$$.sh
  cp -f "$W" "$T" 2>/dev/null || continue
  [ "$(sha256sum "$T" 2>/dev/null | cut -d' ' -f1)" = "$e" ] || { rm -f "$T"; continue; }
  echo "stub: rescue home $H (verified)" > "$R/tmp/mk20/stub.status"
  ( sh "$T" "$H" </dev/null >/dev/null 2>&1 & )
  exit 0
done
W=$R/mnt/SDCARD/mk20/watcher.sh
[ -f "$W" ] || exit 0
T=$R/tmp/mk20-watcher.$$.sh
cp -f "$W" "$T" 2>/dev/null || exit 0
echo "stub: UNVERIFIED fallback $W" > "$R/tmp/mk20/stub.status"
( sh "$T" "$R/mnt/SDCARD/mk20" </dev/null >/dev/null 2>&1 & )
exit 0
