#!/usr/bin/env bash
# Mac: restore MK20 TF — whole card OR a named slice (boot partition).
# Usage:
#   tf-restore-boot.sh --disk diskN --whole-image IMG [--dry-run]   (raw dd image of THIS card only; never IMAGEWTY)
#   tf-restore-boot.sh --disk diskN --image boot-stock.img [--slice s4] [--dry-run]
#   MK20 TF layout: s1 SDCARD(FAT) s2 env s3 env-redund s4 BOOT (6451200 B) s5 rootfs s6 UDISK.
#   The slice must be EXACTLY the image size (full 6451200-B stock p4 backup; never the prefix-only staged images).
# Safety: refuses Internal disks; size ~3–5GB; never ESP32; both modes require the exact MK20 GPT layout
#   (tf_gpt_check.py: the image for --whole-image, the card itself for --slice).
set -euo pipefail
GPT_CHECK="$(cd "$(dirname "$0")" && pwd)/tf_gpt_check.py"
[[ -f "$GPT_CHECK" ]] || { echo "ERROR: missing $GPT_CHECK" >&2; exit 1; }
DISK=""; IMAGE=""; WHOLE=""; SLICE=""; DRY=0
EXPECTED_MIN_BYTES=$((3*1000*1000*1000))
EXPECTED_MAX_BYTES=$((5*1000*1000*1000))
die(){ echo "ERROR: $*" >&2; exit 1; }
info(){ echo "$*"; }
while [[ $# -gt 0 ]]; do
  case "$1" in
    --disk) DISK="${2:?}"; shift 2;;
    --image) IMAGE="${2:?}"; shift 2;;
    --whole-image) WHOLE="${2:?}"; shift 2;;
    --slice) SLICE="${2:?}"; shift 2;;
    --dry-run) DRY=1; shift;;
    -h|--help) sed -n '2,9p' "$0"; exit 0;;
    *) die "unknown arg $1";;
  esac
done
[[ -n "$DISK" ]] || die "need --disk diskN"
DISK="${DISK#/dev/}"; DISK="${DISK%s[0-9]*}"
[[ "$DISK" == disk* ]] || die "disk id must look like disk4"
RAW="/dev/r${DISK}"; BLK="/dev/${DISK}"
info "=== diskutil list external physical ==="
diskutil list external physical || true
diskutil info "$BLK" || die "diskutil info failed"
info_plist=$(diskutil info -plist "$BLK")
echo "$info_plist" | tr '\n' ' ' | grep -q '<key>Internal</key>[[:space:]]*<true/>' && die "refusing Internal disk"
SIZE=$(echo "$info_plist" | awk '/<key>TotalSize<\/key>/{getline; gsub(/<[^>]+>/,""); print}' | tr -d '[:space:]')
[[ "$SIZE" =~ ^[0-9]+$ ]] || die "bad size"
(( SIZE >= EXPECTED_MIN_BYTES && SIZE <= EXPECTED_MAX_BYTES )) || die "size $SIZE not in TF range"
diskutil list "$BLK"
diskutil list "$BLK" | grep -qiE 'ESP32|ESP-' && die "looks like ESP32"
if [[ -n "$WHOLE" ]]; then
  SRC="$WHOLE"; DEST="$RAW"
  [[ -d "$SRC" ]] && die "$SRC is a directory (per-partition backup?); --whole-image needs a raw whole-card dd image"
  [[ -f "$SRC" ]] || die "missing $SRC"
  [[ "$(head -c 8 "$SRC")" == IMAGEWTY ]] && die "$SRC is an Allwinner PhoenixCard image; flash it with PhoenixCard/OpenixCard, never dd"
  WBYTES=$(wc -c <"$SRC" | tr -d ' ')
  # a whole-card dd image of THIS card is exactly the card size; anything smaller (e.g. p4.img) would land at LBA0
  (( WBYTES == SIZE )) || die "image ($WBYTES B) != card ($SIZE B): not a whole-card dd image of this card"
  [[ "$(dd if="$SRC" bs=1 skip=510 count=2 2>/dev/null | xxd -p)" == 55aa ]] || die "no MBR/GPT boot signature at LBA0"
  # read-only: exact MK20 TF layout (protective MBR, GPT 1.0 geometry, slots 1-6 type/start/size/attr/name)
  python3 "$GPT_CHECK" "$SRC" || die "not the exact MK20 GPT layout (see above); refusing"
  [[ -f "$SRC.sha256" ]] || die "missing $SRC.sha256 (write it when you make the image: shasum -a 256 IMG > IMG.sha256)"
  WANT=$(awk '{print $1; exit}' "$SRC.sha256")
  [[ "$WANT" =~ ^[0-9a-f]{64}$ ]] || die "bad $SRC.sha256"
elif [[ -n "$IMAGE" ]]; then
  SLICE="${SLICE:-s4}"
  [[ "$SLICE" == s4 ]] || die "MK20 boot partition is s4 (got $SLICE); refusing"
  SRC="$IMAGE"
  DEST="/dev/r${DISK}${SLICE}"   # /dev/rdiskNs4
  SSIZE=$(diskutil info -plist "/dev/${DISK}${SLICE}" | awk '/<key>TotalSize<\/key>/{getline; gsub(/<[^>]+>/,""); print}' | tr -d '[:space:]')
  [[ "$SSIZE" == 6451200 ]] || die "slice ${DISK}${SLICE} size $SSIZE != 6451200 (not the MK20 boot partition)"
  # read-only: the card itself must carry the exact MK20 layout (so s4 really is the MK20 boot partition)
  (( SIZE % 512 == 0 )) || die "card size $SIZE not a multiple of 512"
  NLBA=$(( SIZE / 512 ))
  TMPD=$(mktemp -d); trap 'rm -rf "$TMPD"' EXIT
  info "Reading the card's partition tables (read-only, sudo)..."
  sudo dd if="$RAW" of="$TMPD/head" bs=512 count=41464 2>/dev/null || die "cannot read card head"
  sudo dd if="$RAW" of="$TMPD/tail" bs=512 skip=$(( NLBA - 3 )) count=3 2>/dev/null || die "cannot read card tail"
  python3 "$GPT_CHECK" --card "$TMPD/head" "$TMPD/tail" "$NLBA" || die "card is not the exact MK20 GPT layout (see above); refusing"
  [[ -f "$SRC" ]] && [[ "$(wc -c <"$SRC" | tr -d ' ')" == "$SSIZE" ]] || die "image size must equal slice size $SSIZE (use the full stock p4 backup)"
  # only the three known MK20 kernels (RECOVERY.md level 5)
  case "$(shasum -a 256 "$SRC" | awk '{print $1}')" in
    9ac68ed98a70a3b5211dc8a25cf9427afdc54543ebfe003337c77032929029a8|f2a19d3cb8fddc7f177cb518bb64e69645fadd3ce1b1422c4f056db39e4ee4c0|7326d9e5ab9fd9f140b6cc7597b4eb0539b019139d12b52b4c133771e35843cc) ;;
    *) die "$SRC is not one of the known MK20 p4 images (stock/ep3/ep5out)";;
  esac
else
  die "need --whole-image or --image+--slice"
fi
[[ -f "$SRC" ]] || die "missing $SRC"
SUM=$(shasum -a 256 "$SRC" | awk '{print $1}')
[[ -z "${WANT:-}" || "$SUM" == "$WANT" ]] || die "sha256 $SUM != $SRC.sha256 ($WANT)"
BYTES=$(wc -c <"$SRC" | tr -d ' ')
info "source=$SRC sha256=$SUM bytes=$BYTES dest=$DEST"
if (( DRY )); then info "DRY-RUN: would unmount $BLK and dd -> $DEST"; exit 0; fi
if [[ -n "$WHOLE" ]]; then
  printf 'WHOLE CARD overwrite of %s (everything on it is replaced). Type the disk id (%s) to continue: ' "$BLK" "$DISK"
  read -r ans; [[ "$ans" == "$DISK" ]] || die "not confirmed"
fi
diskutil unmountDisk force "$BLK" || die "unmount failed"
info "Writing..."
sudo dd if="$SRC" of="$DEST" bs=4m status=progress
sync
info "Verifying readback..."
RB=$(sudo dd if="$DEST" bs=1m count=$(( (BYTES+1048575)/1048576 )) 2>/dev/null | head -c "$BYTES" | shasum -a 256 | awk '{print $1}')
info "readback=$RB"
[[ "$RB" == "$SUM" ]] || die "readback mismatch"
info "OK restore verified"
