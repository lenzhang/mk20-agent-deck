# MK20 overhead camera (TP-Link ONVIF)

Overhead camera that watches the MK20 on the desk. Read-only use: snapshots only,
no PTZ moves, no settings changes.

| Item | Value |
|---|---|
| Model | TP-Link TL-IPC44AW 全彩, fw 1.0.5 Build 260602 |
| IP | 192.168.88.125 (found by ONVIF WS-Discovery, 2026-10-27) |
| Username | admin |
| Password | REDACTED_CAM_PASSWORD (not published) |
| ONVIF | http://192.168.88.125:2020/onvif/device_service (media: /onvif/service), WS-Security digest auth |
| ONVIF profiles | profile_1 (main), profile_2 (sub) |
| RTSP | rtsp://admin:<pw>@192.168.88.125:554/stream1 (2560x1440), /stream2 (640x360) |

## Snapshot

```
tools/cam/snap.sh            # stream1 -> ~/mk20-backup/cam/cam-stream1-<ts>.jpg
tools/cam/snap.sh stream2    # small frame
```

Password comes from `$MK20_CAM_PASS` or `~/.config/mk20-cam/cred` (chmod 600).
ffmpeg: `$FFMPEG`, PATH, or `~/mk20-build/tools/ffmpeg/ffmpeg` (evermeet static build on the iMac).

On macOS 15 a downloaded ffmpeg is blocked from the LAN by Local Network privacy
("No route to host"); the script relays RTSP through `rtsp_relay.py` on 127.0.0.1:8554
using system python, which has LAN access.

At stream1 resolution the key icons and sub-screen layout are visible but small text is not readable.
