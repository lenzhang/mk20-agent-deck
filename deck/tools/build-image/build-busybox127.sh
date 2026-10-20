#!/bin/sh
# Build a static BusyBox 1.27.2 (= the MK20 rootfs version) for test fidelity: `make test` uses it via
# MK20_TEST_BUSYBOX so device scripts run against the same applet set/semantics as on the keyboard.
#   sh build-busybox127.sh <outdir>      (inside mk20-build:trixie; writes <outdir>/busybox)
# glibc >= 2.31 removed stime(): patch date.c to clock_settime (date -s is never used by our scripts).
set -eu
OUT=${1:?outdir}; V=1.27.2; SHA=9d4be516b61e6480f156b11eb42577a13529f75d3383850bb75c50c285de63df
W=$(mktemp -d); cd "$W"
curl -fsSL -o bb.tar.bz2 "https://busybox.net/downloads/busybox-$V.tar.bz2"
echo "$SHA  bb.tar.bz2" | sha256sum -c -
tar xjf bb.tar.bz2 && cd "busybox-$V"
make -s defconfig >/dev/null
sed -i 's/^# CONFIG_STATIC is not set/CONFIG_STATIC=y/; s/^CONFIG_TC=y/# CONFIG_TC is not set/' .config
yes "" | make -s oldconfig >/dev/null 2>&1 || true
sed -i 's/stime(&ts.tv_sec) < 0/clock_settime(CLOCK_REALTIME, \&ts) < 0/' coreutils/date.c
sed -i 's/stime(&remote_time) < 0/({ struct timespec bb_ts = { .tv_sec = remote_time }; clock_settime(CLOCK_REALTIME, \&bb_ts); }) < 0/' util-linux/rdate.c
make -s -j"$(nproc)" busybox
mkdir -p "$OUT" && cp busybox "$OUT/busybox" && "$OUT/busybox" | head -n 1
