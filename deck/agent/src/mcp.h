#ifndef MK20_MCP_H
#define MK20_MCP_H
#include "httpd.h"
#include "loop.h"
#define MCP_VERSION "2025-06-18"
#define MCP_SESSIONS 4
#ifndef MCP_SESSION_IDLE_MS
#define MCP_SESSION_IDLE_MS INT64_C(600000)
#endif
typedef struct {
    char id[33]; int initialized, has_frame; unsigned frame_number;
    int64_t last_activity;
    loop_frame_t frame;
} mcp_session_t;
/* In-process single-slot task store; A7 may submit/query/cancel through these
 * same functions. Only the loop's keyboard status can authorize submission. */
typedef struct {
    char id[24], goal[4097], state[32], summary[2049]; unsigned serial, steps;
    int live, queued; uint64_t stop_epoch; char owner[33];
} agent_task_t;
typedef struct { unsigned step; unsigned char *jpeg; size_t len; } agent_shot_t;
typedef struct {
    agent_loop_t *loop; void *ctx;
    int (*snapshot)(void *, loop_frame_t *, loop_guard_t, void *);
    int (*calibrated)(void *, const loop_frame_t *);
    int (*preflight)(void *, loop_frame_t *);
    void (*task_goal)(void *, const char *);
    void (*log)(void *, const char *);
    char token[65], host[128];
    mcp_session_t sessions[MCP_SESSIONS]; agent_task_t task;
    agent_shot_t shots[5]; unsigned shot_next;
    uint64_t pending; cJSON *pending_id; char pending_owner[33];
    unsigned frames;
    int64_t pending_start; const char *pending_tool;
} mcp_t;
void mcp_init(mcp_t *, agent_loop_t *, const char token[65], const char *host);
void mcp_free(mcp_t *);
void mcp_tick(void *);
void mcp_attach_pump(void *, void (*)(void *), void *);
int mcp_handle(void *, const http_request_t *, http_response_t *);
void mcp_disconnect(void *, uint64_t);
const char *mcp_task_submit(mcp_t *, const char *goal, const char *owner);
const char *mcp_task_queue(mcp_t *, const char *goal, const char *owner);
const char *mcp_task_cancel(mcp_t *, const char *id);
cJSON *mcp_tools(void);
int mcp_cli(int argc, char **argv);
#endif
