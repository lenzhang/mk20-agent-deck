#!/bin/sh
# Host-side installer for MK20 Agent firmware releases (macOS or Linux host, over adb).
#   tools/mk20-install.sh <mk20-fw-X.Y.Z.tar> [--no-switch] [--reboot] [--wait]
#   MK20_ADB  adb command that targets the MK20 only (default: adb; e.g. ~/mk20-backup/mkadb.sh, never the watch)
# Steps: pin the tarball sha (checked against <tar>.sha256 when present) -> push to /data/mk20/incoming/ ->
# run the VERIFIED rescue loader (`loader install <tar> <sha>`, crash-safe, idempotent) -> optional reboot via the
# watcher (cmd-reboot -> hardreset; never `reboot`) -> optional wait for the probation outcome (good or rolled back).
set -eu
ADB=${MK20_ADB:-adb}
TB=${1:?usage: $0 <mk20-fw-X.Y.Z.tar> [--no-switch] [--reboot] [--wait]}; shift
NOSW=""; RB=0; WAIT=0
for a in "$@"; do case "$a" in --no-switch) NOSW=--no-switch;; --reboot) RB=1;; --wait) WAIT=1;; *) echo "bad option $a" >&2; exit 2;; esac; done
[ -f "$TB" ] || { echo "no such file $TB" >&2; exit 2; }
base=$(basename "$TB")
echo "$base" | grep -Eq '^mk20-fw-[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.tar$' || { echo "unexpected name $base" >&2; exit 2; }
V=${base#mk20-fw-}; V=${V%.tar}
if command -v sha256sum >/dev/null; then SHA=$(sha256sum "$TB" | cut -d' ' -f1); else SHA=$(shasum -a 256 "$TB" | cut -d' ' -f1); fi
if [ -f "$TB.sha256" ]; then [ "$(cut -d' ' -f1 "$TB.sha256")" = "$SHA" ] || { echo "sha mismatch vs $TB.sha256" >&2; exit 1; }; fi
echo "release $V sha256 $SHA"
$ADB shell 'mkdir -p /data/mk20/incoming && chmod 700 /data/mk20/incoming' >/dev/null
REMOTE=/data/mk20/incoming/upload.tar   # fixed remote name: nothing host-controlled reaches the remote shell but the hex sha
echo "$SHA" | grep -Eq '^[0-9a-f]{64}$' || { echo "bad sha" >&2; exit 1; }
$ADB push "$TB" "$REMOTE" >/dev/null
# verified loader copy (the same check the watcher does), then install; output and rc come back on one line
out=$($ADB shell "H=/data/mk20/rescue; L=/tmp/mk20/inst-loader.sh; mkdir -p /tmp/mk20
  w=\$(cut -d' ' -f1 \$H/loader.sh.sha256 2>/dev/null); cp \$H/loader.sh \$L.new 2>/dev/null
  if [ -n \"\$w\" ] && [ \"\$(sha256sum \$L.new | cut -d' ' -f1)\" = \"\$w\" ]; then mv -f \$L.new \$L
    sh \$L install $REMOTE $SHA $NOSW; echo \"rc=\$?\"
  else rm -f \$L.new; echo 'rescue loader failed verification'; echo rc=9; fi" | tr -d '\r')
echo "$out"
rc=$(echo "$out" | sed -n 's/^rc=//p' | tail -n 1)
[ "$rc" = 0 ] || { echo "install failed (rc=$rc)" >&2; exit 1; }
$ADB shell "rm -f $REMOTE" >/dev/null
[ "$RB" = 1 ] || { echo "installed; reboot (watcher cmd-reboot) to start probation"; exit 0; }
$ADB shell 'touch /mnt/SDCARD/cmd-reboot && sync' >/dev/null
echo "reboot requested (watcher -> hardreset, WDT reset in ~30 s)"
[ "$WAIT" = 1 ] || exit 0
sleep 45; i=0
while [ $i -lt 120 ]; do
  s=$($ADB shell 'sh /data/mk20/rescue/loader.sh status 2>/dev/null' 2>/dev/null | tr -d '\r') || s=""
  case "$s" in
    *"good=$V pending= "*) echo "$s"; echo "OK: $V confirmed good"; exit 0;;
    *"last_rollback="*"$V->"*) case "$s" in *"pending=$V"*) ;; *) echo "$s"; echo "ROLLED BACK from $V" >&2; exit 3;; esac;;
  esac
  sleep 10; i=$((i+1))
done
echo "timeout waiting for the probation outcome" >&2; exit 4
