/* Screen manager: RGB565 shadow per panel, dirty tracking, direct writes to
 * <fbdir>/fbN (fbdir = "/dev" on device, a temp dir in tests). One writer per
 * SPI bus (bus 0: fb1..9 + fb21, bus 1: fb10..20) so the two buses run in parallel. */
#ifndef MK20_SCREEN_H
#define MK20_SCREEN_H
#include <stdint.h>
#include "panels.h"
typedef struct {
    char fbdir[128];
    uint16_t *shadow[PANEL_COUNT];   /* last content written */
    uint16_t *pending[PANEL_COUNT];  /* desired content */
    int dirty[PANEL_COUNT];
    unsigned writes[PANEL_COUNT], write_errors;
} screen_t;
int screen_init(screen_t *s, const char *fbdir);
void screen_free(screen_t *s);
/* Diff a full RGB565 canvas (CANVAS_W x CANVAS_H) into pending; marks changed panels dirty. Returns #dirty. */
int screen_set_canvas565(screen_t *s, const uint16_t *canvas);
/* Set a sub-rectangle of one panel (fb number 1..21). Returns 1 if changed, 0 unchanged, -1 bad args. */
int screen_set_tile565(screen_t *s, int fb, int x, int y, int w, int h, const uint16_t *px);
/* Write all dirty panels on one bus (0/1). Returns #written or -1 on error. */
int screen_flush_bus(screen_t *s, int bus);
/* Force every panel dirty (e.g. after start). */
void screen_invalidate(screen_t *s);
#endif
