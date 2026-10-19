/* Panel map. fbN == getInfo rect N (KD ScreenCapture::parseImage writes rect i
 * to /dev/fb(i+1)). fbN -> SPI from device sysfs (2026-10-19): fb1..fb9 = spi0.0..0.8,
 * fb10..fb20 = spi1.0..1.10, fb21 (subscreen, nv3007) = spi0.9.
 * Physical key<->fb wiring is assumed row-major like KD; verify with a test pattern. */
#ifndef MK20_PANELS_H
#define MK20_PANELS_H
#define CANVAS_W 640
#define CANVAS_H 656
#define PANEL_COUNT 21
typedef struct { int fb, bus, x, y, w, h, col, row, is_key; } panel_t;
extern const panel_t PANELS[PANEL_COUNT];
#endif
