#include "k2estop.h"
#include <string.h>
void k2_estop_init(k2_estop_t *k, int reports_release) { memset(k, 0, sizeof *k); k->reports_release = reports_release != 0; }
const char *k2_estop_tick(k2_estop_t *k, long now)
{
    if (k->reports_release && k->held && !k->fired && now - k->down_ms >= K2_HOLD_MS) { k->fired = 1; return "k2_hold"; }
    return NULL;
}
const char *k2_estop_event(k2_estop_t *k, int slot, int pressed, long now)
{
    if (slot != K2_PRESS_SLOT) return NULL;
    if (!pressed) {
        const char *reason = k2_estop_tick(k, now);
        k->reports_release = 1; k->held = 0; k->fired = 0; k->npress = 0;
        return reason;
    }
    if (k->reports_release) {
        if (!k->held) { k->held = 1; k->fired = 0; k->down_ms = now; }
        return k2_estop_tick(k, now);
    }
    if (k->npress == 3) { k->presses[0] = k->presses[1]; k->presses[1] = k->presses[2]; k->npress = 2; }
    k->presses[k->npress++] = now;
    if (k->npress == 3 && now - k->presses[0] <= K2_TRIPLE_MS) { k->npress = 0; return "k2_triple"; }
    return NULL;
}
int k2_estop_timeout(const k2_estop_t *k, long now, int dflt)
{
    if (!k->reports_release || !k->held || k->fired) return dflt;
    long left = k->down_ms + K2_HOLD_MS - now; if (left < 0) left = 0;
    return dflt < 0 || left < dflt ? (int)left : dflt;
}
