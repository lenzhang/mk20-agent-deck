#ifndef MK20_RUNTIME_H
#define MK20_RUNTIME_H
#include "kbdc.h"
#include "rfb.h"
typedef struct {
    kbdc_t *kbd; rfb_t *rfb;
    const llm_provider_t *provider; const netcfg_t *net;
    net_options_t transport;
    const char *task;
    /* Window/secure-input detection is a host-provided trusted hint. If absent,
     * fail closed: capture cannot authorize an action in an unknown window. */
    int (*window_hint)(void *, loop_frame_t *); void *hint_ctx;
    unsigned char *jpeg; char *tools;
    loop_guard_t guard; void *guard_ctx;
    void (*sink)(void *, const char *); void *sink_ctx;
    const char *const *secrets; size_t secret_count;
} runtime_t;
int runtime_ports(runtime_t *, loop_ports_t *);
void runtime_free(runtime_t *);
#endif
