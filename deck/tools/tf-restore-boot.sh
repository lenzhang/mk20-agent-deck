#!/usr/bin/env bash
# Mac: restore MK20 TF — whole card OR a named slice (boot partition).
# Usage:
#   tf-restore-boot.sh --disk diskN --whole-image IMG [--dry-run]
#   tf-restore-boot.sh --disk diskN --image boot-stock.img --slice sN [--dry-run]
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
elif [[ -n "$IMAGE" ]]; then
  [[ -n "$SLICE" ]] || die "boot-partition restore needs --slice sN (e.g. s3). Run diskutil list $BLK first."
  SRC="$IMAGE"
  DEST="/dev/r${DISK}${SLICE}"   # e.g. /dev/rdisk4s3
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
