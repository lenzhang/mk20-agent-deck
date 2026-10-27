#!/bin/bash
# Grab one JPEG from the TP-Link TL-IPC44AW camera that watches the MK20.
# Usage: tools/cam/snap.sh [stream1|stream2] [outdir]
# Password: $MK20_CAM_PASS, else ~/.config/mk20-cam/cred (chmod 600). See tools/cam/README.md.
# macOS Local Network privacy blocks an unsigned downloaded ffmpeg from the LAN
# ("No route to host"), so RTSP goes through a local python relay (system python is allowed).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
STREAM="${1:-stream1}"; OUT="${2:-$HOME/mk20-backup/cam}"
CAM_IP="${MK20_CAM_IP:-192.168.88.125}"; CAM_USER="${MK20_CAM_USER:-admin}"
FF="${FFMPEG:-$(command -v ffmpeg || echo "$HOME/mk20-build/tools/ffmpeg/ffmpeg")}"
PORT="${MK20_CAM_RELAY_PORT:-8554}"
if [ -z "${MK20_CAM_PASS:-}" ]; then MK20_CAM_PASS="$(cat "$HOME/.config/mk20-cam/cred")"; fi
export MK20_CAM_PASS
ENC="$(python3 -c 'import os,urllib.parse;print(urllib.parse.quote(os.environ["MK20_CAM_PASS"],safe=""))')"
mkdir -p "$OUT"; F="$OUT/cam-$STREAM-$(date +%Y%m%d-%H%M%S).jpg"
python3 "$HERE/rtsp_relay.py" "$PORT" "$CAM_IP" 554 & RP=$!; trap 'kill $RP 2>/dev/null || true' EXIT
sleep 1
"$FF" -hide_banner -loglevel error -rtsp_transport tcp -i "rtsp://$CAM_USER:$ENC@127.0.0.1:$PORT/$STREAM" \
  -frames:v 1 -q:v 2 -y "$F" 2>&1 | sed -E 's#//[^@/]*@#//***@#g'
[ -s "$F" ] && echo "$F"
