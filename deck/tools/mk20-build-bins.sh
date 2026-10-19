#!/bin/sh
# Build the static armhf release binaries from source into firmware/release/bin (1.2.0+):
#   ttygs_tcp_bridge (m2-scripts/bridge), mk20-router (router/) and mk20-kbd (kbd/, KD replacement; only used with
#   FEATURE_KBD=1). Run before tools/mk20-release-build.sh.
# Both live in the release on /data (never on the FAT); the FAT keeps only the boot stub.
set -eu
D=$(cd "$(dirname "$0")/.." && pwd)
CC=${CROSS:-arm-linux-gnueabihf-}gcc
command -v "$CC" >/dev/null || { echo "missing $CC" >&2; exit 1; }
F="-Wall -Wextra -Werror -O2 -static"
B=$D/firmware/release/bin
"$CC" $F -o "$B/ttygs_tcp_bridge.new" "$D/m2-scripts/bridge/ttygs_tcp_bridge.c"
"$CC" $F -o "$B/mk20-router.new" "$D/router/mk20_router.c"
K=$D/kbd   # same sources/flags as kbd/Makefile `armhf` (+ -Werror via $F)
"$CC" $F -std=c11 -D_DEFAULT_SOURCE -o "$B/mk20-kbd.new" "$K/mk20_kbd.c" "$K/gd32.c" "$K/gd32io.c" "$K/host.c" "$K/panels.c" \
  "$K/screen.c" "$K/jpeg.c" "$K/kbd.c" "$K/hid.c" -pthread
for n in ttygs_tcp_bridge mk20-router mk20-kbd; do
  ${CROSS:-arm-linux-gnueabihf-}strip "$B/$n.new"; chmod 755 "$B/$n.new"; mv -f "$B/$n.new" "$B/$n"
  echo "$(sha256sum "$B/$n" | cut -d' ' -f1) $(wc -c < "$B/$n") bin/$n"
done
