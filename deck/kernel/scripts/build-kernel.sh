#!/usr/bin/env bash
# Build Tina 5.4.61 for T113 (sun8iw20) from ikconfig + optional ECM enable.
set -euo pipefail
ROOT=/workspace/mk20-research/kernel-build/tina-linux-5.4
IKCFG=/workspace/mk20-research/kernel-build/boot-extract/ikconfig
OUT=/workspace/mk20-research/kernel-build/out
JOBS=${JOBS:-$(nproc)}
CROSS=${CROSS:-arm-linux-gnueabihf-}
MODE=${1:-patched}  # unmodified | patched

cd "$ROOT"
export ARCH=arm CROSS_COMPILE="$CROSS"

# Base config from shipping /proc/config.gz extract
cp "$IKCFG" .config
# Fix: ikconfig from extract-ikconfig is already a .config-like file
# Ensure ECM/NCM for patched builds
if [[ "$MODE" == patched ]]; then
  scripts/config --enable CONFIG_USB_CONFIGFS_ECM || true
  scripts/config --enable CONFIG_USB_CONFIGFS_ECM_SUBSET || true
  scripts/config --enable CONFIG_USB_CONFIGFS_NCM || true
  # F_ECM implied by configfs ecm in many trees; also ensure:
  scripts/config --enable CONFIG_USB_F_ECM || true
  scripts/config --enable CONFIG_USB_F_NCM || true
  scripts/config --enable CONFIG_USB_F_HID || true
  scripts/config --enable CONFIG_USB_F_ACM || true
  scripts/config --enable CONFIG_USB_CONFIGFS_F_HID || true
  scripts/config --enable CONFIG_USB_CONFIGFS_ACM || true
  scripts/config --enable CONFIG_USB_CONFIGFS_F_FS || true
fi
# Keep LOCALVERSION empty to match shipping vermagic 5.4.61
scripts/config --set-str CONFIG_LOCALVERSION "" || true
scripts/config --disable CONFIG_LOCALVERSION_AUTO || true

make olddefconfig
echo "=== building $MODE ==="
make -j"$JOBS" zImage
mkdir -p "$OUT"
cp -f arch/arm/boot/zImage "$OUT/zImage-$MODE"
python3 /workspace/mk20-research/kernel-build/scripts/pack-bootimg.py \
  "$OUT/zImage-$MODE" -o "$OUT/boot-$MODE.img"
sha256sum "$OUT/boot-$MODE.img" | tee "$OUT/boot-$MODE.img.sha256"
ls -la "$OUT/boot-$MODE.img"
# Size gate for SDCARD backup plan
python3 - <<PY
from pathlib import Path
p=Path("$OUT/boot-$MODE.img")
sz=p.stat().st_size
print(f"boot image {sz} bytes; SDCARD 8MiB; stock backup needs ~{sz}+margin")
assert sz < 7_000_000, "too big for SDCARD stock+new coexistence"
PY
