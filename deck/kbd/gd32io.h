/* GD32 tty I/O: the ONLY code path that writes to /dev/ttyS1.
 * gd32_tx() refuses anything gd32_encode() refuses (strict allow-list, see gd32.h). */
#ifndef MK20_GD32IO_H
#define MK20_GD32IO_H
#include "gd32.h"
#include <stddef.h>
#include <stdint.h>

#define GD32_NO_WRITE_FILE "/data/mk20/kbd-no-gd32-write"

/* Open the GD32 tty raw 115200 8N1. rw=0 -> O_RDONLY (no write path). */
int gd32_open(const char *dev, int rw);
/* Encode + write one allow-listed payload. Returns 0, or -1 (errno=EPERM if refused). */
int gd32_tx(int fd, const uint8_t *payload, size_t len);

typedef void (*gd32_frame_cb)(void *ctx, const uint8_t *payload, int len);

typedef struct {
    int timeout_ms;   /* per attempt, default 1000 */
    int attempts;     /* per slot, default 3 */
} gd32_knob_opts;

typedef struct {
    int state;                 /* 0 not run, 1 ok (6/6), 2 partial, 3 failed (0/6), 4 disabled (read-only) */
    int slot_ok[6];            /* 1 if echo seen for slot 100+i */
    int slot_attempts[6];
    int ok_count, tx_errors, timeouts, foreign_frames, stale_echoes;
} gd32_knob_status;

const char *gd32_knob_state_name(int state);
/* Configure encoder slots 100..105 to KC_NO (serial reporting), one at a time,
 * waiting for the identical echo. Frames that are not the awaited echo are
 * passed to cb (key events during init are NOT lost); stale 0x05 echoes are
 * dropped. Never fails hard: returns st->ok_count. */
int gd32_knob_init(int fd, gd32_parser *p, const gd32_knob_opts *o, gd32_knob_status *st, gd32_frame_cb cb, void *ctx);
#endif
