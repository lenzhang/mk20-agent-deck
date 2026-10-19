#ifndef MK20_CLOCK_H
#define MK20_CLOCK_H
#include <stdint.h>
#include <time.h>
#define MK20_CLOCK_MARKER "/run/mk20/clock.synced"
typedef int (*agent_clock_setter)(time_t epoch, void *ctx);
typedef struct {
    const char *marker_path;
    int trust_clock, set_clock;
    agent_clock_setter setter;
    void *setter_ctx;
} agent_clock_options_t;
time_t agent_clock_floor(void);
int agent_date_parse(const char *date, time_t *epoch);
int agent_clock_trusted_at(const agent_clock_options_t *o, time_t now, time_t boot);
int agent_clock_trusted(const agent_clock_options_t *o);
/* Apply a trusted source: large skew requires explicit set_clock and a setter.
 * The marker is written only after the setter succeeds (or skew <=120 s). */
int agent_clock_sync(const agent_clock_options_t *o, time_t source, time_t now);
int agent_clock_sync_date(const agent_clock_options_t *o, const char *date, time_t now);
int agent_clock_system_set(time_t epoch, void *ctx);
#endif
