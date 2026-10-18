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
  # a whole-card dd image of THIS card is exactly the card size; anything smaller (e.g. p4.img) would land at LBA0
  (( WBYTES == SIZE )) || die "image ($WBYTES B) != card ($SIZE B): not a whole-card dd image of this card"
  [[ "$(dd if="$SRC" bs=1 skip=510 count=2 2>/dev/null | xxd -p)" == 55aa ]] || die "no MBR/GPT boot signature at LBA0"
  # read-only GPT check: both headers + CRCs, exactly 6 partitions, MK20 p1-p5 boundaries and p6 start
  python3 - "$SRC" <<'PY' || die "not an MK20 GPT layout (see above); refusing"
import struct, sys, zlib
# MK20 card layout (from the live #221 card): slots 1-6 in this order, exact start + length
EXPECT = [(41464, 65536), (107000, 504), (107504, 504), (108008, 12600), (120608, 2097152), (2217760, 5644512)]
f = open(sys.argv[1], "rb"); f.seek(0, 2); nlba = f.tell() // 512
if nlba < 2217760 + 5644512 + 34: sys.exit("image smaller than the MK20 layout")
def hdr(lba):
    f.seek(lba * 512); h = f.read(512)
    if h[:8] != b"EFI PART": sys.exit(f"no GPT header at LBA {lba}")
    hs = struct.unpack_from("<I", h, 12)[0]
    if hs < 92 or hs > 512: sys.exit(f"bad GPT header size {hs} at LBA {lba}")
    z = bytearray(h[:hs]); z[16:20] = b"\0\0\0\0"
    if zlib.crc32(bytes(z)) & 0xFFFFFFFF != struct.unpack_from("<I", h, 16)[0]: sys.exit(f"GPT header CRC bad at LBA {lba}")
    cur, alt, first, last = struct.unpack_from("<QQQQ", h, 24); guid = h[56:72]
    ent, n, es, ecrc = struct.unpack_from("<QIII", h, 72)
    if es != 128 or not 6 <= n <= 1024: sys.exit(f"unexpected GPT entry geometry n={n} size={es}")
    f.seek(ent * 512); e = f.read(n * es)
    if len(e) != n * es or zlib.crc32(e) & 0xFFFFFFFF != ecrc: sys.exit(f"GPT entries CRC bad (header LBA {lba})")
    return dict(cur=cur, alt=alt, first=first, last=last, guid=guid, ent=ent, n=n, e=e)
P = hdr(1)
if P["cur"] != 1 or P["alt"] != nlba - 1 or P["ent"] != 2: sys.exit("primary GPT geometry does not match the image size")
B = hdr(nlba - 1)
tbl = (P["n"] * 128 + 511) // 512
if B["cur"] != nlba - 1 or B["alt"] != 1 or B["ent"] != nlba - 1 - tbl: sys.exit("backup GPT geometry wrong")
for k in ("first", "last", "guid", "n", "e"):
    if P[k] != B[k]: sys.exit(f"primary/backup GPT differ in {k}")
if P["first"] < 2 + tbl or P["last"] > nlba - 2 - tbl or P["first"] > P["last"]: sys.exit("usable LBA range out of bounds")
e = P["e"]; used = []
for i in range(P["n"]):
    ent = e[i * 128:(i + 1) * 128]
    if ent[:16] == b"\0" * 16: continue
    a, b = struct.unpack_from("<QQ", ent, 32); used.append((i + 1, a, b))
if [u[0] for u in used] != [1, 2, 3, 4, 5, 6]: sys.exit(f"partition slots {[u[0] for u in used]}, MK20 uses exactly 1-6")
prev_end = P["first"] - 1
for (slot, a, b), (s0, n0) in zip(used, EXPECT):
    if b < a or a <= prev_end or b > P["last"]: sys.exit(f"p{slot} extent {a}-{b} out of order/bounds")
    if a != s0 or b - a + 1 != n0: sys.exit(f"p{slot} {a}+{b - a + 1} != MK20 {s0}+{n0}")
    prev_end = b
print("GPT ok: slots 1-6 = MK20 layout, primary+backup headers/tables valid and identical")
PY
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
