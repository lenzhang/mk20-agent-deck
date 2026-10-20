# Build/test environment

Any Linux or macOS host with Docker (colima on a Mac works):

```sh
docker build -t mk20-build:trixie deck/tools/build-image
docker run --rm -v ~/mk20-tools:/out mk20-build:trixie sh /work/deck/tools/build-image/build-busybox127.sh /out \
  # (mount the repo at /work first) -> ~/mk20-tools/busybox = BusyBox 1.27.2 static
cd deck && docker run --rm -v "$PWD":/work/wt/deck -v ~/mk20-tools:/bb127:ro -w /work/wt/deck \
  -e MK20_TEST_BUSYBOX=/bb127/busybox mk20-build:trixie make test
```

- `procps` is required (tests call `pkill`/`ps`); without it ~46 tests fail with FileNotFoundError.
- TSan in a colima VM may need `colima ssh -- sudo sysctl -w vm.mmap_rnd_bits=28` after each start.
- Tests expose only the commands that exist on the device (`tests/fixtures/device-commands.txt`, captured via adb);
  the device BusyBox has no `stat`, `timeout`, `od` etc. Refresh the list when the rootfs changes.
- Release tarballs: `sh tools/mk20-release-build.sh firmware out <build-id>` inside the image (needs GNU stat/tar).

Verified 2026-10-20 on dev-mbp (arm64, colima): `tests/` 715 passed / 1 skipped with BusyBox 1.27.2.

Pinned: base image `debian:trixie@sha256:913f6706…` (multi-arch index, 2026-10-20) and uv 0.13.3 (versioned installer URL).
Apt packages are not pinned (Debian stable updates); rebuild deliberately and re-run `make test` after a bump.
