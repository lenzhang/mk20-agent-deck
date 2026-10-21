# K2 on-device verification (2026-10-19 10:25-10:35 SGT, fw 1.1.4)

- KD is respawned by procd (`/etc/init.d/qt_app2`, respawn). Stop with `/etc/init.d/qt_app2 stop`, restore with `start` (or reboot).
- fb mapping: /dev/fb1..fb20 are the 20 key LCDs, fb21 the 428x142 sub-screen; RGB565 LE, 128x128, orientation correct (TL marker top-left). Photo: user attachment 2026-10-19.
  **Correction (2026-10-21, fw 1.4.3-rc4):** this photo verified only that the twenty nodes are keys and each panel's
  orientation, NOT their order: the `--pattern` encoded the fb id as a bar count that nobody counted, and the
  row-major order (fb1 top-left ... fb20 bottom-right, as in KD's getInfo rects) was assumed. A photo of 1.4.2 L1
  (2026-10-21 23:00; every logical key L drawn on /dev/fbL, Mac canvas verified correct) shows all 20 keys,
  including the 8 agent slots in their known order, permuted exactly as **/dev/fb(k) (k=1..20) at column 4-(k-1)/4,
  row (k-1)%4** (0-based, natural orientation): fb1..fb4 = rightmost column top->bottom, ..., fb17..fb20 = leftmost
  column. Logical key L (col (L-1)%5, row (L-1)/5) is therefore /dev/fb((4-col)*4+row+1); fb21 stays the sub-screen.
  The table lives in `panels.c` (`PANELS[].dev`, asserted by `test_panel_map`); `--pattern` now prints
  "R<row> C<col>" + "fb<dev>" per logical key, so one photo of it verifies the whole map.
- With KD stopped, GD32 sends NOTHING unprompted. (A burst of id=0x05 len=6 frames seen at first open was stale data buffered from KD.)
- Key event payload `16 P A B 00 00`: P=1 press/0 release; A=0..3, B=0..4 (20 keys, user pressed natural-orientation rows left->right: A=row, B=column). Keycode bytes always 0x0000 for keys. These ARE physical row-major positions (verified by pressing), so the input mapping `fb = row*5+col+1` is the logical index and correct; only the display node differed.
- Left knob (K1): ccw `16 01 65 65` (101), cw `16 01 66 66` (102), press `16 01 64 64` / release `16 00 64 64` (100). Rotation sends press only, no release.
- Right knob (K2): NOTHING on ttyS1. GD32 handles it itself as USB media keys straight to the host (press launched Apple Music = Play/Pause). Owning K2 needs a GD32 keymap/encoder change via VIA (config write, not flashing) - needs user decision.
- Raw log: Mac ~/mk20-backup/k2/mon.log.
