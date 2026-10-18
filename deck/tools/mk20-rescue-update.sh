#!/bin/sh
# Update rescue-tier files on the MK20 over adb (host side). Usage: tools/mk20-rescue-update.sh <rescue-dir> <file>...
#   MK20_ADB  adb command targeting the MK20 only (e.g. ~/mk20-backup/mkadb.sh)
# For each home (/data/mk20/rescue first, then /mnt/SDCARD/mk20 - one home stays valid at every instant):
# push to <home>/<f>.new, verify against the build's .sha256, then rename .sha256 and file into place; reads back.
# Only plain file copies: no USB change, safe in an adb shell. The running watcher picks the new copy up on its next
# verified rescue_copy (no restart needed). Never use for watcher.sh while a job is running.
set -eu
ADB=${MK20_ADB:-adb}
D=${1:?usage: $0 <rescue-dir> <file>...}; shift
[ $# -gt 0 ] || { echo "no files" >&2; exit 2; }
for f in "$@"; do
  case "$f" in loader.sh|hardreset.sh|watcher.sh|lunch.sh) ;; *) echo "not a rescue file: $f" >&2; exit 2;; esac
  [ -f "$D/$f" ] && [ -f "$D/$f.sha256" ] || { echo "missing $D/$f(.sha256)" >&2; exit 2; }
  want=$(cut -d' ' -f1 "$D/$f.sha256")
  if command -v sha256sum >/dev/null; then got=$(sha256sum "$D/$f" | cut -d' ' -f1); else got=$(shasum -a 256 "$D/$f" | cut -d' ' -f1); fi
  [ "$want" = "$got" ] || { echo "$f does not match its .sha256" >&2; exit 1; }
  for H in /data/mk20/rescue /mnt/SDCARD/mk20; do
    [ "$f" = lunch.sh ] && [ "$H" = /data/mk20/rescue ] && continue   # the stub lives on FAT only (qt_app1 copies it)
    $ADB push "$D/$f" "$H/$f.new" >/dev/null
    $ADB push "$D/$f.sha256" "$H/$f.sha256.new" >/dev/null
    r=$($ADB shell "cd $H && [ \"\$(sha256sum $f.new | cut -d' ' -f1)\" = $want ] && [ \"\$(cut -d' ' -f1 $f.sha256.new)\" = $want ] && sync \
      && mv -f $f.sha256.new $f.sha256 && mv -f $f.new $f && sync && [ \"\$(sha256sum $f | cut -d' ' -f1)\" = $want ] && echo OK || { rm -f $f.new $f.sha256.new; echo FAIL; }" | tr -d '\r')
    echo "$H/$f: $r"; [ "$r" = OK ] || exit 1
  done
done
