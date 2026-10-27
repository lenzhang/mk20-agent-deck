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
tools/cam/snap.sh            # stream1, 8-frame average -> cam-stream1-<ts>.jpg + cam-stream1-<ts>-mk20.jpg (crop, sharpened, 2x)
tools/cam/snap.sh stream2    # small frame
MK20_CAM_STACK=1 tools/cam/snap.sh          # single frame
MK20_CAM_CROP=none tools/cam/snap.sh        # no crop
```

Password comes from `$MK20_CAM_PASS` or `~/.config/mk20-cam/cred` (chmod 600).
ffmpeg: `$FFMPEG`, PATH, or `~/mk20-build/tools/ffmpeg/ffmpeg` (evermeet static build on the iMac).

On macOS 15 a downloaded ffmpeg is blocked from the LAN by Local Network privacy
("No route to host"); the script relays RTSP through `rtsp_relay.py` on 127.0.0.1:8554
using system python, which has LAN access.

At stream1 resolution the key icons and sub-screen layout are visible but small text is not readable.

## Why the image is soft (tested 2026-10-27)

- Lens is **4 mm fixed focus, fixed aperture** (TP-Link spec). ONVIF advertises focus AUTO/MANUAL and a
  continuous focus Move (speed -1..1); the camera accepts Move/Stop but nothing changes (Laplacian
  sharpness identical within noise after +/- moves) -> no focus motor.
- PTZ: pan/tilt only; zoom range is 0..0 (no optical or ONVIF digital zoom). No presets saved.
- Encoder: main stream H.265 2560x1440, VBR 2048 kbps = already the max (range 256..2048), quality 3/5.
  Low bitrate + 3D noise reduction smear fine text.
- Main limit is resolution: the MK20 is only ~600x580 px of the frame, so key/sub-screen glyphs are
  ~5-7 px tall and the emissive screens bloom. Lowering ISP Brightness (50->15, tried and restored)
  did not make text readable.
- Averaging 8 frames (tmix) removes most compression noise; that is the default in snap.sh.
- To read screen text use the device itself (adb screencap / app screenshot), or put the camera
  closer so the keyboard fills more of the frame (keep >= ~30-50 cm for the fixed-focus lens).
