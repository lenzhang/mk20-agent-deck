#include "panels.h"
/* Logical key L at (col c, row r) of the 640x656 canvas is physical /dev/fb<dev>.
 * Evidence: photo of 1.4.2 L1 (2026-10-21 23:00) with every key drawn from logical slot
 * L on /dev/fbL: all 20 positions, including the 8 agent slots in their known order, show
 * up exactly where /dev/fb(k) = column 4-(k-1)/4, row (k-1)%4 puts them, i.e. the key LCDs
 * are wired column-major from the top-right corner, not row-major like KD's rect order.
 * GD32 key events (row,col) are physical row-major (K2, verified by pressing), so the
 * logical index is right for input and only the device number differs.
 * Asserted by tests/test_kbd.c test_panel_map(); re-verify on device with one photo of --pattern. */
#define K(L, dev, c, r) { L, dev, PANEL_DEV_BUS(dev), (c) * 128, 144 + (r) * 128, 128, 128, c, r, 1 }
const panel_t PANELS[PANEL_COUNT] = {
    /* K(logical, /dev/fbN, col, row)
     *               col 0            col 1            col 2            col 3            col 4 */
    /* row 0 */ K( 1, 17, 0, 0), K( 2, 13, 1, 0), K( 3,  9, 2, 0), K( 4,  5, 3, 0), K( 5,  1, 4, 0),
    /* row 1 */ K( 6, 18, 0, 1), K( 7, 14, 1, 1), K( 8, 10, 2, 1), K( 9,  6, 3, 1), K(10,  2, 4, 1),
    /* row 2 */ K(11, 19, 0, 2), K(12, 15, 1, 2), K(13, 11, 2, 2), K(14,  7, 3, 2), K(15,  3, 4, 2),
    /* row 3 */ K(16, 20, 0, 3), K(17, 16, 1, 3), K(18, 12, 2, 3), K(19,  8, 3, 3), K(20,  4, 4, 3),
    /* sub-screen (nv3007, spi0.9) */
    { 21, 21, PANEL_DEV_BUS(21), 106, 0, 428, 142, -1, -1, 0 },
};
