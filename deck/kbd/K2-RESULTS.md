# K2 on-device verification (2026-10-19 10:25-10:35 SGT, fw 1.1.4)

- KD is respawned by procd (`/etc/init.d/qt_app2`, respawn). Stop with `/etc/init.d/qt_app2 stop`, restore with `start` (or reboot).
- fb mapping: /dev/fb1..fb20 are the 20 key LCDs, fb21 the 428x142 sub-screen; RGB565 LE, 128x128, orientation correct (TL marker top-left). Photo: user attachment 2026-10-19.
- With KD stopped, GD32 sends NOTHING unprompted. (A burst of id=0x05 len=6 frames seen at first open was stale data buffered from KD.)
- Key event payload `16 P A B 00 00`: P=1 press/0 release; A=0..3, B=0..4 (20 keys, user pressed natural-orientation rows left->right: A=row, B=column). Keycode bytes always 0x0000 for keys.
- Left knob (K1): ccw `16 01 65 65` (101), cw `16 01 66 66` (102), press `16 01 64 64` / release `16 00 64 64` (100). Rotation sends press only, no release.
- Right knob (K2): NOTHING on ttyS1. GD32 handles it itself as USB media keys straight to the host (press launched Apple Music = Play/Pause). Owning K2 needs a GD32 keymap/encoder change via VIA (config write, not flashing) - needs user decision.
- Raw log: Mac ~/mk20-backup/k2/mon.log.
