#!/bin/sh
# Build the static armhf release binaries from source into firmware/release/bin (1.2.0+):
#   ttygs_tcp_bridge (m2-scripts/bridge) and mk20-router (router/). Run before tools/mk20-release-build.sh.
# Both live in the release on /data (never on the FAT); the FAT keeps only the boot stub.
set -eu
D=$(cd "$(dirname "$0")/.." && pwd)
CC=${CROSS:-arm-linux-gnueabihf-}gcc
command -v "$CC" >/dev/null || { echo "missing $CC" >&2; exit 1; }
F="-Wall -Wextra -Werror -O2 -static"
B=$D/firmware/release/bin
"$CC" $F -o "$B/ttygs_tcp_bridge.new" "$D/m2-scripts/bridge/ttygs_tcp_bridge.c"
"$CC" $F -o "$B/mk20-router.new" "$D/router/mk20_router.c"
for n in ttygs_tcp_bridge mk20-router; do
  ${CROSS:-arm-linux-gnueabihf-}strip "$B/$n.new"; chmod 755 "$B/$n.new"; mv -f "$B/$n.new" "$B/$n"
  echo "$(sha256sum "$B/$n" | cut -d' ' -f1) $(wc -c < "$B/$n") bin/$n"
done
