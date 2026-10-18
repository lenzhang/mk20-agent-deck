#!/bin/sh
# Run on the Mac after the MK20 is back (adb or not). Uses adb if present, else mk20ctl (ACM).
set -e; cd "$(dirname "$0")"
# MK20 adbd reports "(no serial number)"; NEVER pick the watch (WATCH-SERIAL)
T=$(adb devices -l | grep -v WATCH-SERIAL | grep '^(no serial number)' | sed -n 's/.*transport_id:\([0-9]*\).*/\1/p' | head -1)
if [ -n "$T" ]; then
  for f in watcher.sh mk20-add-adb.sh; do adb -t $T push $f /mnt/SDCARD/mk20/$f; done
  adb -t $T push adb.sh /mnt/SDCARD/mk20/boot-every/adb.sh; adb -t $T push adb.sh.sha256 /mnt/SDCARD/mk20/boot-every/adb.sh.sha256
  adb -t $T shell 'sync; sha256sum /mnt/SDCARD/mk20/watcher.sh /mnt/SDCARD/mk20/mk20-add-adb.sh /mnt/SDCARD/mk20/boot-every/adb.sh'
else
  PY=~/worktrees/mk20-agent-deck-p0/.venv/bin/python; C=~/mk20-backup/mk20-stage-remote/mk20ctl.py
  $PY $C put watcher.sh /mnt/SDCARD/mk20/watcher.sh; $PY $C put mk20-add-adb.sh /mnt/SDCARD/mk20/mk20-add-adb.sh
  $PY $C put adb.sh /mnt/SDCARD/mk20/boot-every/adb.sh; $PY $C put adb.sh.sha256 /mnt/SDCARD/mk20/boot-every/adb.sh.sha256
fi
