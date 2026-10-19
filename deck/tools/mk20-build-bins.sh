#!/bin/sh
# Build the static armhf release binaries from source into firmware/release/bin (1.2.0+):
#   ttygs_tcp_bridge (m2-scripts/bridge), mk20-router (router/), mk20-kbd (kbd/)
#   and mk20-agent (agent/). Run before tools/mk20-release-build.sh.
# Binaries live in the release on /data (never on the FAT); the FAT keeps only the boot stub.
set -eu
D=$(cd "$(dirname "$0")/.." && pwd)
TZ=UTC
SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH:-$(cat "$D/agent/SOURCE_DATE_EPOCH")}
export TZ SOURCE_DATE_EPOCH
CC=${CROSS:-arm-linux-gnueabihf-}gcc
command -v "$CC" >/dev/null || { echo "missing $CC" >&2; exit 1; }
F="-Wall -Wextra -Werror -O2 -static"
B=$D/firmware/release/bin
"$CC" $F "-ffile-prefix-map=$D=." "-fdebug-prefix-map=$D=." -o "$B/ttygs_tcp_bridge.new" "$D/m2-scripts/bridge/ttygs_tcp_bridge.c"
"$CC" $F "-ffile-prefix-map=$D=." "-fdebug-prefix-map=$D=." -o "$B/mk20-router.new" "$D/router/mk20_router.c"
K=$D/kbd   # same sources/flags as kbd/Makefile `armhf` (+ -Werror via $F)
"$CC" $F "-ffile-prefix-map=$D=." "-fdebug-prefix-map=$D=." -std=c11 -D_DEFAULT_SOURCE -o "$B/mk20-kbd.new" "$K/mk20_kbd.c" "$K/gd32.c" "$K/gd32io.c" "$K/host.c" "$K/panels.c" \
  "$K/screen.c" "$K/jpeg.c" "$K/kbd.c" "$K/hid.c" "$K/ctl.c" "$K/ctlscr.c" "$K/k2estop.c" -pthread
# Reuse the agent source/crypto lists and vendor-specific flags verbatim.
# A fresh object tree prevents stale host/sanitizer objects entering a release.
A=$(mktemp -d "$D/agent/build-release.XXXXXX")
trap 'rm -rf "$A"' EXIT HUP INT TERM
make -s -C "$D/agent" BUILD="$A" CC="$CC" SAN= LDFLAGS=-static \
  CFLAGS="-std=c11 -Wall -Wextra -Werror -O2 -ffile-prefix-map=$D=. -fdebug-prefix-map=$D=." \
  "$A/mk20-agent-nosan"
cp "$A/mk20-agent-nosan" "$B/mk20-agent.new"
# release-build enumerates every regular payload file into MANIFEST.
cp "$D/agent/vendor/ca/cacert.pem" "$D/firmware/release/share/ca-bundle.pem"
chmod 644 "$D/firmware/release/share/ca-bundle.pem"
for n in ttygs_tcp_bridge mk20-router mk20-kbd mk20-agent; do
  ${CROSS:-arm-linux-gnueabihf-}strip "$B/$n.new"; chmod 755 "$B/$n.new"; mv -f "$B/$n.new" "$B/$n"
  echo "$(sha256sum "$B/$n" | cut -d' ' -f1) $(wc -c < "$B/$n") bin/$n"
done
