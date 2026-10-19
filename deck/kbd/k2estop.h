#ifndef MK20_K2ESTOP_H
#define MK20_K2ESTOP_H
#include <stdint.h>
#define K2_HOLD_MS 1000
#define K2_TRIPLE_MS 1500
#define K2_PRESS_SLOT 103
/* Starts in press-only mode; the first observed release enables held-press timing.
 * --k2-release initializes reports_release=1 once that firmware capability is known. */
typedef struct { int reports_release, held, fired, npress; int64_t down_ms, presses[3]; } k2_estop_t;
void k2_estop_init(k2_estop_t *k, int reports_release);
const char *k2_estop_event(k2_estop_t *k, int slot, int pressed, int64_t now);
const char *k2_estop_tick(k2_estop_t *k, int64_t now);
int k2_estop_timeout(const k2_estop_t *k, int64_t now, int dflt);
#endif
