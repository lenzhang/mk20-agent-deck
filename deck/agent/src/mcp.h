#ifndef MK20_MCP_H
#define MK20_MCP_H
#include "httpd.h"
#include "loop.h"
#define MCP_VERSION "2025-06-18"
#define MCP_SESSIONS 4
typedef struct {
    char id[33]; int initialized, has_frame; unsigned frame_number;
    loop_frame_t frame;
} mcp_session_t;
/* In-process single-slot task store; A7 may submit/query/cancel through these
 * same functions. Only the loop's keyboard status can authorize submission. */
typedef struct {
    char id[24], goal[4097], state[32], summary[2049]; unsigned serial, steps;
    int live, queued; char owner[33];
} agent_task_t;
typedef struct {
    agent_loop_t *loop; void *ctx;
    int (*snapshot)(void *, loop_frame_t *, loop_guard_t, void *);
    int (*calibrated)(void *, const loop_frame_t *);
    int (*preflight)(void *, loop_frame_t *);
    void (*task_goal)(void *, const char *);
    void (*log)(void *, const char *);
    char token[65], host[128];
    mcp_session_t sessions[MCP_SESSIONS]; agent_task_t task;
    uint64_t pending; cJSON *pending_id; char pending_owner[33];
    int confirmation_seen; unsigned frames;
    int64_t pending_start; const char *pending_tool;
} mcp_t;
void mcp_init(mcp_t *, agent_loop_t *, const char token[65], const char *host);
void mcp_free(mcp_t *);
void mcp_tick(void *);
void mcp_attach_pump(void *, void (*)(void *), void *);
int mcp_handle(void *, const http_request_t *, http_response_t *);
void mcp_disconnect(void *, uint64_t);
const char *mcp_task_submit(mcp_t *, const char *goal, const char *owner);
const char *mcp_task_cancel(mcp_t *, const char *id);
cJSON *mcp_tools(void);
int mcp_cli(int argc, char **argv);
#endif
