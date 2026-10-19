/* Screen manager: RGB565 shadow per panel, dirty tracking, direct writes to
 * <fbdir>/fbN (fbdir = "/dev" on device, a temp dir in tests).
 * Threaded mode (screen_start_writers): one writer thread per SPI bus
 * (bus 0: fb1..9 + fb21, bus 1: fb10..20). Producers (key/host handling) only
 * copy into `pending` under the bus mutex and signal; the writer copies the
 * panel out under the lock and does the slow write+fsync unlocked, so producers
 * never wait on SPI. Per panel the latest content wins (intermediate frames are
 * coalesced); a panel re-dirtied while being written is written again. */
#ifndef MK20_SCREEN_H
#define MK20_SCREEN_H
#include <pthread.h>
#include <stdint.h>
#include "panels.h"
typedef struct {
    char fbdir[128];
    uint16_t *shadow[PANEL_COUNT];   /* last content written */
    uint16_t *pending[PANEL_COUNT];  /* desired content */
    int dirty[PANEL_COUNT];
    unsigned writes[PANEL_COUNT], write_errors;
    /* threading */
    int threaded, stop[2], busy[2];
    pthread_mutex_t mu[2];
    pthread_cond_t cv[2], idle[2];
    pthread_t th[2];
    uint16_t *wbuf[2];
    struct screen_warg { void *s; int bus; } warg[2];
    unsigned write_delay_us;         /* test hook: simulate slow SPI */
    unsigned retry_ms;               /* back-off after a failed write (default 200) */
} screen_t;
int screen_init(screen_t *s, const char *fbdir);
void screen_free(screen_t *s);       /* stops writers (no drain) if running */
/* Diff a full RGB565 canvas (CANVAS_W x CANVAS_H) into pending; marks changed panels dirty. Returns #dirty. */
int screen_set_canvas565(screen_t *s, const uint16_t *canvas);
/* Set a sub-rectangle of one panel (fb number 1..21). Returns 1 if changed, 0 unchanged, -1 bad args. */
int screen_set_tile565(screen_t *s, int fb, int x, int y, int w, int h, const uint16_t *px);
/* Synchronous: write all dirty panels on one bus (0/1). Returns #written or -1. Not with writers running. */
int screen_flush_bus(screen_t *s, int bus);
/* Force every panel dirty (e.g. after start). */
void screen_invalidate(screen_t *s);
int screen_start_writers(screen_t *s);
/* drain=1: write everything still dirty first (bounded by the caller's patience: gives up after timeout_ms). */
void screen_stop_writers(screen_t *s, int drain, int timeout_ms);
/* Wait until both buses are clean and idle. 0 = idle, -1 = timeout. */
int screen_wait_idle(screen_t *s, int timeout_ms);
/* Consistent snapshot of counters. */
/* Test hook (atomic): simulate slow SPI. */
void screen_set_write_delay(screen_t *s, unsigned us);
void screen_stats(screen_t *s, unsigned writes[PANEL_COUNT], unsigned *errors, int *dirty_count);
#endif
