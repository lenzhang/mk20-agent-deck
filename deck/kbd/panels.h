/* Panel map (single source of truth: the PANELS table in panels.c).
 *
 * Two different numbers per panel:
 *   fb  = LOGICAL index 1..21. Used everywhere except the device node: UI tables, GD32 key input
 *         (fb = row*5 + col + 1, row-major, natural orientation with the knobs/sub-screen on top),
 *         the host protocol (getInfo rect fb-1, cmd 200 tile fb), canvas rects, ctl `fb`,
 *         screen_set_tile565(fb) and the screen_t per-panel arrays (index fb-1).
 *   dev = PHYSICAL device: the panel is /dev/fb<dev>. Used ONLY to open the device node (screen.c);
 *         the SPI bus is derived from it.
 * They are NOT the same for keys (photo of 1.4.2 L1, 2026-10-21): /dev/fb(k), k=1..20, sits at
 * column 4-(k-1)/4, row (k-1)%4 (0-based) -- fb1..fb4 are the rightmost column top->bottom, ...,
 * fb17..fb20 the leftmost. So logical key L (col (L-1)%5, row (L-1)/5) is dev (4-col)*4 + row + 1.
 * The sub-screen is fb21 == dev 21.
 * Bus from device sysfs (2026-10-19, KD-REPLACE-PLAN.md): /dev/fb1..fb9 = spi0.0..0.8 and
 * /dev/fb21 (nv3007) = spi0.9 -> bus 0 (9 keys + sub-screen); /dev/fb10..fb20 = spi1.0..1.10 -> bus 1. */
#ifndef MK20_PANELS_H
#define MK20_PANELS_H
#define CANVAS_W 640
#define CANVAS_H 656
#define PANEL_COUNT 21
#define PANEL_DEV_BUS(dev) ((dev) <= 9 || (dev) == 21 ? 0 : 1)
typedef struct { int fb, dev, bus, x, y, w, h, col, row, is_key; } panel_t;
extern const panel_t PANELS[PANEL_COUNT];
#endif
