#!/bin/sh
# MK20 persistent remote-control stub (v1). Minimal + removable.
# Disable: create /mnt/SDCARD/mk20-no-watch  (or delete this lunch.sh)
# qt_app1 runs lunch.sh synchronously -> must return immediately.
[ -f /mnt/SDCARD/mk20-no-watch ] && exit 0
W=/mnt/SDCARD/mk20/watcher.sh
[ -f "$W" ] || exit 0
T=/tmp/mk20-watcher.$$.sh   # unique copy: never overwrite a running watcher's script
cp -f "$W" "$T" 2>/dev/null || exit 0
( sh "$T" </dev/null >/dev/null 2>&1 & )
exit 0
