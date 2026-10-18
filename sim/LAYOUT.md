# MK20 physical layout (from photos + vendor specs) vs. our code

## From photos / spec
- **20 keys, 5 columns × 4 rows**, every key is a mechanical switch with a **0.85" 128×128 LCD** under the keycap.
- **2.8" secondary screen, 428×142**, centred above the key grid.
- **Two knobs (encoders, rotate + press)**, top-left (K1) and top-right (K2), flanking the sub-screen.
- No other keys/buttons on top. Rear edge: knobs overhang, USB-C ports.
- ScreenKey theme editor canvases: key 128×128; "main screen" (20 keys) **640×512**; sub-screen 428×142; "Encoder" area 214×142 (= half the sub-screen width, i.e. the sub-screen splits into two knob-labelled halves).
- Body 130.53 × 149.62 mm (dimension drawing).
- Traced photo geometry (800×800 top-down photo): key caps x = 222/295/367/439/512, y = 286/369/451/534, 66×76 px; sub-screen 284,186 233×80; knobs K1 (241,226) r25, K2 (557,224) r25. Stored in `deck/mk20sim/server.py:GEOM`.

## Cross-check with repo
| Item | Photos/spec | Code | Status |
|---|---|---|---|
| Grid | 5×4 | `layout.COLS, ROWS = 5, 4` | ✅ |
| Key LCD | 128×128 | `KEY_SIZE = 128` | ✅ |
| Sub-screen | 428×142, above keys | `SUB_W, SUB_H = 428, 142`, centred at top | ✅ |
| Whole canvas | ScreenKey "main screen" 640×512 (keys only) | default guess 640×656 (= 142 + 2 + 512) | ✅ consistent (code adds sub-screen band); real value always from `getInfo` |
| Knobs | 2, left/right of sub-screen | K1 left = effort, K2 right = paging/scroll (PRD §6.4); codes K1 100/101/102, K2 103/104/105 (press/ccw/cw) | ✅ (left/right assignment of codes measured on device, not visible in photos) |
| PRD §6.2 L1/L4 tables | 5×4 | `controller.role_at` | ✅ |

## Conflicts / open points
- None structural. The ScreenKey "Encoder 214×142" area suggests the stock UI draws knob labels in the two halves of the sub-screen — our sub-screen renderer does not reserve those halves (design choice, not a bug).
- Firmware coordinate base (0/1) is inferred from `getInfo`; the sim uses the default layout (fw_base 0). Raw events in the sim are therefore 0-based.
