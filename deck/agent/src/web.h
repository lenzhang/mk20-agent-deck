#ifndef MK20_WEB_H
#define MK20_WEB_H
#include "mcp.h"
#include "runtime.h"
#include <sys/types.h>
#define WEB_SESSIONS 2
#ifndef WEB_EVENT_INTERVAL_MS
#define WEB_EVENT_INTERVAL_MS 1000
#endif
/* Pairing challenge: TTL (<=10 min), per-code attempts, start and failure rate limits. */
#define WEB_PAIR_MS INT64_C(300000)
#define WEB_PAIR_ATTEMPTS 5u
#define WEB_RATE_WINDOW_MS INT64_C(600000)
#define WEB_PAIR_STARTS 6
#define WEB_PAIR_FAILS 10
#define WEB_IDLE_MS INT64_C(1800000)
#define WEB_ABSOLUTE_MS INT64_C(43200000)
#define WEB_PORT "8780"
typedef struct { char cookie[33], csrf[33]; int64_t created, activity; } web_session_t;
typedef struct { uint64_t tag; char owner[33]; int64_t sent; } web_event_t;
typedef struct {
    mcp_t *mcp; runtime_t *runtime;
    llm_provider_t provider; netcfg_t net;
    /* host = exact numeric listen authority ("a.b.c.d:8780"); origin = "http://" + host. */
    char host[128], origin[160], secrets[4096], config[4096], code[7];
    /* Device files. Empty disables the feature (fail closed); mk20_cli sets production paths. */
    char pair_path[4096], uptime_path[256], health_path[4096], maint_dir[4096], debug_path[4096];
    /* agent=0: settings/maintenance only, no keyboard client or task API. */
    int agent;
    int64_t pair_at; unsigned attempts; int pairing;
    int64_t starts[WEB_PAIR_STARTS], fails[WEB_PAIR_FAILS], locked_until;
    web_session_t sessions[WEB_SESSIONS]; web_event_t events[2];
    uint64_t probe_tag; pid_t probe_pid; int probe_fd; int64_t probe_deadline;
    char probe_data[512]; size_t probe_used;
    /* Optional test ports, never supplied by a remote request. */
    void *test_ctx; int (*connection_test)(void *, const llm_provider_t *, const netcfg_t *, const char **);
} web_t;
void web_init(web_t *, mcp_t *, runtime_t *, const char *, const char *, const char *);
void web_free(web_t *);
int web_handle(void *, const http_request_t *, http_response_t *);
void web_tick(void *);
int web_idle(void *);
void web_disconnect(void *, uint64_t);
void web_attach_pump(void *, void (*)(void *), void *);
int web_config_validate(web_t *, const char *, const char *, size_t);
/* Static allowlist exported as JSON for read-only schema drift checks. */
cJSON *web_schema(void);
/* Full page bytes (malloc) and the CSP with hashes of its single inline script/style. */
char *web_page(int chat, size_t *len);
const char *web_csp(void);
#endif
