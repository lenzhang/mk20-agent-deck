#!/bin/sh
# MK20 boot stub v2 (FAT /mnt/SDCARD/lunch.sh; qt_app1 copies it to /data/lunch.sh and runs it synchronously, so it
# must return at once). Starts the RESCUE watcher only; the watcher runs the release loader later (after KD).
# Rescue home candidates, first whose watcher.sh matches its .sha256 wins: /data/mk20/rescue (ext4), /mnt/SDCARD/mk20.
# Legacy compatibility: a FAT watcher WITHOUT any .sha256 (pre-1.0 install) still runs, flagged UNVERIFIED.
# A checksum MISMATCH means corruption/tampering: nothing runs (recovery: TF card, see RECOVERY.md).
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
  # liveness: if the (verified) ext4 watcher is not running its main loop after 60 s, start the FAT one
  if [ "$H" != "$R/mnt/SDCARD/mk20" ] && [ -f "$R/mnt/SDCARD/mk20/watcher.sh.sha256" ]; then
    ( sleep "${MK20_STUB_LIVENESS:-60}"; p=$(cat "$R/tmp/mk20/watch.pid" 2>/dev/null)
      if [ -z "$p" ] || ! kill -0 "$p" 2>/dev/null; then
        F=$R/mnt/SDCARD/mk20/watcher.sh; e=$(cut -d' ' -f1 "$F.sha256"); T2=$R/tmp/mk20-watcher-fat.sh
        cp -f "$F" "$T2" && [ "$(sha256sum "$T2" | cut -d' ' -f1)" = "$e" ] && { echo "stub: ext4 watcher dead after 60 s -> FAT watcher" >> "$R/tmp/mk20/stub.status"; exec sh "$T2" "$R/mnt/SDCARD/mk20" </dev/null >/dev/null 2>&1; }
      fi ) </dev/null >/dev/null 2>&1 &
  fi
  exit 0
done
W=$R/mnt/SDCARD/mk20/watcher.sh
[ -f "$W" ] || exit 0
[ -f "$W.sha256" ] && { echo "stub: no verified watcher (checksum mismatch) -> not running any" > "$R/tmp/mk20/stub.status"; exit 0; }
T=$R/tmp/mk20-watcher.$$.sh
cp -f "$W" "$T" 2>/dev/null || exit 0
echo "stub: UNVERIFIED fallback $W" > "$R/tmp/mk20/stub.status"
( sh "$T" "$R/mnt/SDCARD/mk20" </dev/null >/dev/null 2>&1 & )
exit 0
