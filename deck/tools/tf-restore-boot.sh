#!/usr/bin/env bash
# Mac: restore MK20 TF — whole card OR a named slice (boot partition).
# Usage:
#   tf-restore-boot.sh --disk diskN --whole-image IMG [--dry-run]   (raw dd image of THIS card only; never IMAGEWTY)
#   tf-restore-boot.sh --disk diskN --image boot-stock.img [--slice s4] [--dry-run]
#   MK20 TF layout: s1 SDCARD(FAT) s2 env s3 env-redund s4 BOOT (6451200 B) s5 rootfs s6 UDISK.
#   The slice must be EXACTLY the image size (full 6451200-B stock p4 backup; never the prefix-only staged images).
# Safety: refuses Internal disks; size ~3–5GB; never ESP32.
set -euo pipefail
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
    -h|--help) sed -n '1,12p' "$0"; exit 0;;
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
  (( WBYTES <= SIZE )) || die "image ($WBYTES B) larger than card ($SIZE B)"
elif [[ -n "$IMAGE" ]]; then
  SLICE="${SLICE:-s4}"
  [[ "$SLICE" == s4 ]] || die "MK20 boot partition is s4 (got $SLICE); refusing"
  SRC="$IMAGE"
  DEST="/dev/r${DISK}${SLICE}"   # /dev/rdiskNs4
  SSIZE=$(diskutil info -plist "/dev/${DISK}${SLICE}" | awk '/<key>TotalSize<\/key>/{getline; gsub(/<[^>]+>/,""); print}' | tr -d '[:space:]')
  [[ "$SSIZE" == 6451200 ]] || die "slice ${DISK}${SLICE} size $SSIZE != 6451200 (not the MK20 boot partition)"
  [[ -f "$SRC" ]] && [[ "$(wc -c <"$SRC" | tr -d ' ')" == "$SSIZE" ]] || die "image size must equal slice size $SSIZE (use the full stock p4 backup)"
else
  die "need --whole-image or --image+--slice"
fi
[[ -f "$SRC" ]] || die "missing $SRC"
SUM=$(shasum -a 256 "$SRC" | awk '{print $1}')
BYTES=$(wc -c <"$SRC" | tr -d ' ')
info "source=$SRC sha256=$SUM bytes=$BYTES dest=$DEST"
if (( DRY )); then info "DRY-RUN: would unmount $BLK and dd -> $DEST"; exit 0; fi
diskutil unmountDisk force "$BLK" || die "unmount failed"
info "Writing..."
sudo dd if="$SRC" of="$DEST" bs=4m status=progress
sync
info "Verifying readback..."
RB=$(sudo dd if="$DEST" bs=1m count=$(( (BYTES+1048575)/1048576 )) 2>/dev/null | dd bs=1 count="$BYTES" 2>/dev/null | shasum -a 256 | awk '{print $1}')
info "readback=$RB"
[[ "$RB" == "$SUM" ]] || die "readback mismatch"
info "OK restore verified"
