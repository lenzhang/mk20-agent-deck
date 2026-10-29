#!/bin/bash
# Grab a JPEG from the TP-Link TL-IPC44AW camera that watches the MK20.
# Usage: tools/cam/snap.sh [stream1|stream2] [outdir]
#   MK20_CAM_STACK=N   average N frames (sampled every 5th frame) to cut H.265 noise; default 8 (1 = single frame)
#   MK20_CAM_CROP=w:h:x:y  also write a cropped, mildly sharpened copy (default: MK20 area 600:580:955:450 at preset mk20;
#                      set to "none" to skip). Coordinates are for stream1 2560x1440 and the current camera aim.
#   MK20_CAM_GOTO=1    first GotoPreset "mk20" (token 2) so the crop box matches.
# Password: $MK20_CAM_PASS, else ~/.config/mk20-cam/cred (chmod 600). See tools/cam/README.md.
# macOS Local Network privacy blocks an unsigned downloaded ffmpeg from the LAN
# ("No route to host"), so RTSP goes through a local python relay (system python is allowed).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
STREAM="${1:-stream1}"; OUT="${2:-$HOME/mk20-backup/cam}"
CAM_IP="${MK20_CAM_IP:-192.168.88.125}"; CAM_USER="${MK20_CAM_USER:-admin}"
FF="${FFMPEG:-$(command -v ffmpeg || echo "$HOME/mk20-build/tools/ffmpeg/ffmpeg")}"
PORT="${MK20_CAM_RELAY_PORT:-8554}"
N="${MK20_CAM_STACK:-8}"; CROP="${MK20_CAM_CROP:-600:580:955:450}"
[ "$STREAM" = stream1 ] || CROP=none
if [ -z "${MK20_CAM_PASS:-}" ]; then MK20_CAM_PASS="$(cat "$HOME/.config/mk20-cam/cred")"; fi
export MK20_CAM_PASS
ENC="$(python3 -c 'import os,urllib.parse;print(urllib.parse.quote(os.environ["MK20_CAM_PASS"],safe=""))')"
if [ "${MK20_CAM_GOTO:-0}" = 1 ]; then python3 "$HERE/ptz.py" goto 2 >/dev/null; fi
mkdir -p "$OUT"; TS="$(date +%Y%m%d-%H%M%S)"; F="$OUT/cam-$STREAM-$TS.jpg"
python3 "$HERE/rtsp_relay.py" "$PORT" "$CAM_IP" 554 & RP=$!; trap 'kill $RP 2>/dev/null || true' EXIT
sleep 1
VF="select=not(mod(n\,5))"; [ "$N" -gt 1 ] && VF="$VF,tmix=frames=$N"
# -update 1 keeps overwriting the file; the last written frame is the average of N samples.
"$FF" -hide_banner -loglevel error -rtsp_transport tcp -i "rtsp://$CAM_USER:$ENC@127.0.0.1:$PORT/$STREAM" \
  -vf "$VF" -fps_mode vfr -frames:v "$N" -update 1 -q:v 1 -y "$F" 2>&1 | sed -E 's#//[^@/]*@#//***@#g'
[ -s "$F" ] || { echo "no frame" >&2; exit 1; }
echo "$F"
if [ "$CROP" != none ]; then
  C="$OUT/cam-$STREAM-$TS-mk20.jpg"
  "$FF" -hide_banner -loglevel error -i "$F" -vf "crop=$CROP,unsharp=5:5:0.8,scale=iw*2:ih*2:flags=lanczos" -q:v 1 -y "$C" && echo "$C"
fi
