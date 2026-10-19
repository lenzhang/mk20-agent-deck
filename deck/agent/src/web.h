#ifndef MK20_WEB_H
#define MK20_WEB_H
#include "mcp.h"
#include "runtime.h"
#include <sys/types.h>
#define WEB_SESSIONS 2
#ifndef WEB_EVENT_INTERVAL_MS
#define WEB_EVENT_INTERVAL_MS 1000
#endif
#define WEB_PAIR_MS INT64_C(300000)
#define WEB_IDLE_MS INT64_C(1800000)
#define WEB_ABSOLUTE_MS INT64_C(43200000)
typedef struct { char cookie[33], csrf[33]; int64_t created, activity; } web_session_t;
typedef struct { uint64_t tag; char owner[33]; int64_t sent; } web_event_t;
typedef struct {
    mcp_t *mcp; runtime_t *runtime;
    llm_provider_t provider; netcfg_t net;
    char host[128], secrets[4096], config[4096], code[7];
    int64_t pair_at; unsigned attempts; int pairing;
    web_session_t sessions[WEB_SESSIONS]; web_event_t events[2];
    uint64_t probe_tag; pid_t probe_pid; int probe_fd; int64_t probe_deadline;
    char probe_data[512]; size_t probe_used;
    void *display_ctx; int (*display)(void *, const char code[7]);
    /* Optional test ports, never supplied by a remote request. */
    void *test_ctx; int (*connection_test)(void *, const llm_provider_t *, const netcfg_t *, const char **);
} web_t;
void web_init(web_t *, mcp_t *, runtime_t *, const char *, const char *, const char *);
void web_free(web_t *);
int web_handle(void *, const http_request_t *, http_response_t *);
void web_tick(void *);
void web_disconnect(void *, uint64_t);
void web_attach_pump(void *, void (*)(void *), void *);
int web_config_validate(web_t *, const char *, const char *, size_t);
/* Static allowlist exported as JSON for read-only schema drift checks. */
cJSON *web_schema(void);
const char *web_page(int chat);
const char *web_csp(void);
#endif
