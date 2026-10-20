# MK20 build/test image (Debian trixie; x86_64 or arm64 host via colima/docker).
# Mirrors the former box toolchain: Debian trixie, gcc 14, arm-linux-gnueabihf gcc 14.
FROM debian:trixie@sha256:913f6706df59a68922d1dd08f78c2476560a8d367897200a6005b00e5f67c2d5
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc-arm-linux-gnueabihf libc6-dev-armhf-cross \
    bc bison flex libssl-dev libelf-dev kmod cpio xz-utils zstd file \
    python3 python3-venv python3-pip shellcheck git ca-certificates curl \
    qemu-system-x86 busybox-static libasan8 libubsan1 procps netcat-openbsd \
 && rm -rf /var/lib/apt/lists/*
RUN curl -LsSf https://astral.sh/uv/0.13.3/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
WORKDIR /work
RUN apt-get update && apt-get install -y --no-install-recommends python3-pil && rm -rf /var/lib/apt/lists/*
