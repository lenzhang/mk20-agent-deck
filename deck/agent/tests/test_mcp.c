#define _POSIX_C_SOURCE 200809L
#ifdef __APPLE__
#define _DARWIN_C_SOURCE
#endif
#include "mcp.h"
#include <assert.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
static unsigned checks;
#define CHECK(x) do { checks++; assert(x); } while (0)
static void parser(void)
{
    http_request_t q;
    const char *good = "POST /mcp HTTP/1.1\r\nHost: 127.0.0.1:8765\r\nContent-Length: 2\r\n\r\n{}";
    size_t n = strlen(good);
    for (size_t i = 0; i < n; i++) CHECK(httpd_parse(good,i,&q) == 0);
    CHECK(httpd_parse(good,n,&q) == 1); CHECK(q.body_len == 2 && q.consumed == n && !memcmp(q.body,"{}",2));
    char pipeline[512]; snprintf(pipeline,sizeof pipeline,"%s%s",good,good);
    CHECK(httpd_parse(pipeline,2*n,&q) == 1 && q.consumed == n);
    CHECK(httpd_parse(pipeline+q.consumed,n,&q) == 1);
    const char *bad[] = {
        "POST /mcp HTTP/1.1\r\nHost: a\r\n\r\n",
        "POST /mcp HTTP/1.1\r\nHost: a\r\nTransfer-Encoding: chunked\r\n\r\n",
        "POST /mcp HTTP/1.1\r\nHost: a\r\nContent-Length: 65537\r\n\r\n",
        "POST /mcp HTTP/1.1\r\nHost: a\r\nContent-Length: 999999999999999999999999\r\n\r\n",
        "POST /mcp HTTP/1.1\r\nHost: a\r\nContent-Length: 1\r\nContent-Length: 1\r\n\r\nx",
        "POST /mcp HTTP/1.1\r\nHost: a\r\nContent-Length: 1\r\nTransfer-Encoding: chunked\r\n\r\nx",
        "POST /mcp HTTP/1.1\r\nHost: a\r\nHost: a\r\nContent-Length: 0\r\n\r\n",
        "POST /mcp HTTP/1.1\r\nHost : a\r\nContent-Length: 0\r\n\r\n",
        "POST /mcp HTTP/1.1\r\nHost: a\r\n x: y\r\nContent-Length: 0\r\n\r\n",
        "POST /mcp HTTP/1.0\r\nHost: a\r\nContent-Length: 0\r\n\r\n",
        "POST /mcp HTTP/1.1\r\nHost: a\r\nContent-Length: -1\r\n\r\n",
        "POST /mcp HTTP/1.1\r\nHost: a\r\nContent-Length: 1x\r\n\r\n",
        "POST /mcp HTTP/1.1\r\nHost: a\r\nAuthorization: x\r\nAuthorization: y\r\nContent-Length: 0\r\n\r\n"
    };
    for (size_t i = 0; i < sizeof bad/sizeof *bad; i++) CHECK(httpd_parse(bad[i],strlen(bad[i]),&q) >= 400);
    char *body = malloc(HTTPD_BODY+256); CHECK(body);
    int header = snprintf(body,256,"POST /mcp HTTP/1.1\r\nHost: x\r\nContent-Length: %u\r\n\r\n",HTTPD_BODY);
    memset(body+header,'a',HTTPD_BODY); CHECK(httpd_parse(body,(size_t)header+HTTPD_BODY,&q) == 1 && q.body_len == HTTPD_BODY); free(body);
    char boundary[HTTPD_HEADERS+1]; const char *prefix = "POST /mcp HTTP/1.1\r\nHost: x\r\nX: ", *tail = "\r\nContent-Length: 0\r\n\r\n";
    size_t prefix_len = strlen(prefix), tail_len = strlen(tail); memcpy(boundary,prefix,prefix_len); memset(boundary+prefix_len,'a',HTTPD_HEADERS-prefix_len-tail_len); memcpy(boundary+HTTPD_HEADERS-tail_len,tail,tail_len);
    CHECK(httpd_parse(boundary,HTTPD_HEADERS,&q) == 1);
    char huge[HTTPD_HEADERS+20]; memset(huge,'x',sizeof huge); CHECK(httpd_parse(huge,sizeof huge,&q) == 431);
    CHECK(!httpd_expired(INT64_C(5000000000),INT64_C(5000004999)));
    CHECK(httpd_expired(INT64_C(5000000000),INT64_C(5000005000)));
    CHECK(httpd_expired(5,4));
}
static void tokens(void)
{
    char dir[] = "/tmp/a6-token-XXXXXX"; CHECK(mkdtemp(dir)); char path[256]; snprintf(path,sizeof path,"%s/token",dir);
    char token[65], again[65], auth[80]; CHECK(!httpd_token(path,token)); CHECK(strlen(token) == 64);
    struct stat st; CHECK(!stat(path,&st) && (st.st_mode & 07777) == 0600 && st.st_uid == geteuid());
    CHECK(!httpd_token(path,again) && !strcmp(token,again));
    snprintf(auth,sizeof auth,"Bearer %s",token); CHECK(httpd_authorized(auth,token));
    CHECK(!httpd_authorized(NULL,token)); CHECK(!httpd_authorized("",token)); CHECK(!httpd_authorized("Bearer x",token));
    for (size_t i = 7; i < 71; i++) { char old = auth[i]; auth[i] = old == 'a' ? 'b' : 'a'; CHECK(!httpd_authorized(auth,token)); auth[i] = old; }
    CHECK(!chmod(path,0644)); CHECK(httpd_token(path,again)); CHECK(!chmod(path,0600));
    if (geteuid() == 0) { CHECK(!chown(path,1,1)); CHECK(httpd_token(path,again)); CHECK(!chown(path,0,0)); }
    char link[256]; snprintf(link,sizeof link,"%s/link",dir); CHECK(!symlink(path,link)); CHECK(httpd_token(link,again)); CHECK(!unlink(link));
    CHECK(!unlink(path)); CHECK(!mkfifo(path,0600)); CHECK(httpd_token(path,again)); CHECK(!unlink(path)); CHECK(!mkdir(path,0700)); CHECK(httpd_token(path,again)); CHECK(!rmdir(path)); CHECK(!rmdir(dir));
    char host[128]; CHECK(httpd_address("0.0.0.0:8765",1,host)); CHECK(httpd_listen("0.0.0.0:8765",1) < 0);
    CHECK(httpd_address("[::]:8765",1,host)); CHECK(httpd_address("172.30.250.2:8765",1,host)); CHECK(httpd_address("127.0.0.1:8765",0,host));
    CHECK(!httpd_address("127.0.0.1:8765",1,host)); CHECK(!httpd_address("172.30.250.2:8765",0,host));
}
typedef struct { int64_t now; int event, executes, models, leases, stops, confirm; } fake_t;
static int64_t now(void *v) { return ((fake_t *)v)->now; }
static int poll_event(void *v, kbd_event_t *e) { fake_t *f = v; if (!f->event) return 0; *e = (kbd_event_t){.kind=K_ALLOW}; f->event = 0; return 1; }
static int lease(void *v, lease_op_t op) { fake_t *f = v; if (op == LEASE_ESTOP) f->stops++; if (op == LEASE_HID) f->leases++; return 0; }
static int confirm(void *v, unsigned cid, const char *s, unsigned ms) { (void)cid; (void)s; (void)ms; ((fake_t *)v)->confirm++; return 0; }
static int execute(void *v, const action_t *a, int input, int64_t d, loop_guard_t g, void *ctx) { (void)a; (void)input; (void)d; if (g(ctx)) return -1; ((fake_t *)v)->executes++; return 0; }
static int capture(void *v, loop_frame_t *f, loop_guard_t g, void *ctx) { (void)v; if (g(ctx)) return -1; static const unsigned char jpeg[] = {255,216,255,217}; *f = (loop_frame_t){.jpeg=jpeg,.jpeg_len=4,.width=320,.height=200}; strcpy(f->title,"Notes"); strcpy(f->app,"Notes"); return 0; }
static int model(void *v, const loop_frame_t *f, const char *history, loop_reply_t *out, loop_guard_t g, void *ctx) { (void)f; (void)history; if (g(ctx)) return -1; ((fake_t *)v)->models++; memset(out,0,sizeof *out); out->action.kind = A_DONE; strcpy(out->action.target,"Notes"); return 0; }
static int calibrated(void *v, const loop_frame_t *f) { (void)v; return f->width == 320; }
static cJSON *request(mcp_t *m, http_request_t *q, http_response_t *r, const char *body)
{
    q->body = body; q->body_len = strlen(body); memset(r,0,sizeof *r); int rc = mcp_handle(m,q,r); CHECK(!rc);
    cJSON *j = r->body ? agent_json_parse(r->body,r->len,1024*1024) : NULL; free(r->body); r->body = NULL; return j;
}
static void dispatch(void)
{
    fake_t fake = {0}; loop_ports_t ports = {.ctx=&fake,.now=now,.poll=poll_event,.lease=lease,.confirm=confirm,.execute=execute,.observe=capture,.model=model};
    policy_config_t policy; policy_defaults(&policy); policy.confirm_first = policy.confirm_app_switch = 0; policy.max_steps = 1;
    agent_loop_t loop; loop_init(&loop,&policy,&ports);
    char token[65]; memset(token,'a',64); token[64] = 0; mcp_t m; mcp_init(&m,&loop,token,"127.0.0.1:8765"); m.ctx = &fake; m.snapshot = capture; m.calibrated = calibrated;
    http_request_t q = {.tag=1}; strcpy(q.method,"POST"); strcpy(q.path,"/mcp"); strcpy(q.host,"127.0.0.1:8765"); strcpy(q.content_type,"application/json");
    http_response_t r; cJSON *j = request(&m,&q,&r,"{}"); CHECK(r.status == 401); cJSON_Delete(j);
    q.has_auth = 1; snprintf(q.auth,sizeof q.auth,"Bearer %s",token);
    q.has_origin = 1; strcpy(q.origin,"http://evil"); j = request(&m,&q,&r,"{}"); CHECK(r.status == 400); cJSON_Delete(j); q.has_origin = 0;
    strcpy(q.host,"evil"); j = request(&m,&q,&r,"{}"); CHECK(r.status == 400); cJSON_Delete(j); strcpy(q.host,"127.0.0.1:8765");
    q.has_version = 1; strcpy(q.version,"2024-11-05"); j = request(&m,&q,&r,"{}"); CHECK(r.status == 400); cJSON_Delete(j); q.has_version = 0;
    j = request(&m,&q,&r,"{"); CHECK(cJSON_GetObjectItem(cJSON_GetObjectItem(j,"error"),"code")->valueint == -32700); cJSON_Delete(j);
    j = request(&m,&q,&r,"[]"); CHECK(cJSON_GetObjectItem(cJSON_GetObjectItem(j,"error"),"code")->valueint == -32600); cJSON_Delete(j);
    const char *init = "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2025-06-18\",\"capabilities\":{},\"clientInfo\":{}}}";
    j = request(&m,&q,&r,init); CHECK(r.status == 200 && strlen(r.session) == 32); CHECK(cJSON_GetObjectItem(j,"result")); cJSON_Delete(j); strcpy(q.session,r.session); q.has_session = 1;
    j = request(&m,&q,&r,"{\"jsonrpc\":\"2.0\",\"method\":\"notifications/initialized\"}"); CHECK(r.status == 202 && !j);
    j = request(&m,&q,&r,"{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/list\"}"); CHECK(cJSON_GetArraySize(cJSON_GetObjectItem(cJSON_GetObjectItem(j,"result"),"tools")) == 13); cJSON_Delete(j);
    j = request(&m,&q,&r,"{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"enable\"}"); CHECK(cJSON_GetObjectItem(cJSON_GetObjectItem(j,"error"),"code")->valueint == -32601); cJSON_Delete(j);
    j = request(&m,&q,&r,"{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/call\",\"params\":{\"name\":\"enable\"}}"); CHECK(cJSON_GetObjectItem(cJSON_GetObjectItem(j,"error"),"code")->valueint == -32602); cJSON_Delete(j);
    CHECK(!strcmp(mcp_task_submit(&m,"goal",q.session),"disabled")); CHECK(!m.task.live);
    fake.event = 1; mcp_tick(&m); CHECK(loop.allowed);
    const char *click = "{\"jsonrpc\":\"2.0\",\"id\":4,\"method\":\"tools/call\",\"params\":{\"name\":\"click\",\"arguments\":{\"x\":3,\"y\":2,\"risk\":\"low\",\"target\":\"Notes\"}}}";
    j = request(&m,&q,&r,click); CHECK(strstr(cJSON_GetObjectItem(cJSON_GetArrayItem(cJSON_GetObjectItem(cJSON_GetObjectItem(j,"result"),"content"),0),"text")->valuestring,"screenshot")); cJSON_Delete(j);
    j = request(&m,&q,&r,"{\"jsonrpc\":\"2.0\",\"id\":3,\"method\":\"tools/call\",\"params\":{\"name\":\"screenshot\"}}"); CHECK(j && m.sessions[0].has_frame); cJSON_Delete(j);
    q.body = click; q.body_len = strlen(click); memset(&r,0,sizeof r); CHECK(mcp_handle(&m,&q,&r) == 1); CHECK(!fake.models);
    mcp_tick(&m); CHECK(loop.state == S_DONE && fake.executes == 1 && fake.leases == 1);
    j = request(&m,&q,&r,click); CHECK(j); cJSON_Delete(j);
    q.tag++; j = request(&m,&q,&r,click); CHECK(strstr(cJSON_GetObjectItem(cJSON_GetArrayItem(cJSON_GetObjectItem(cJSON_GetObjectItem(j,"result"),"content"),0),"text")->valuestring,"budget")); cJSON_Delete(j); CHECK(fake.executes == 1);
    loop_stop(&loop); CHECK(!strcmp(mcp_task_submit(&m,"goal",q.session),"stopped")); CHECK(fake.stops == 1);
    fake.event = 1; mcp_tick(&m); CHECK(!mcp_task_submit(&m,"private goal",q.session)); CHECK(!strcmp(mcp_task_submit(&m,"other",q.session),"busy"));
    CHECK(!mcp_task_cancel(&m,m.task.id)); CHECK(!m.task.live && !*m.task.goal);
    fake.event = 1; mcp_tick(&m); CHECK(!mcp_task_submit(&m,"another private goal",q.session));
    mcp_tick(&m); mcp_tick(&m); mcp_tick(&m);
    CHECK(!m.task.live && !strcmp(m.task.state,"done") && m.task.steps == 1 && !*m.task.goal); CHECK(fake.models == 1);
    strcpy(q.method,"GET"); j = request(&m,&q,&r,""); CHECK(r.status == 405); cJSON_Delete(j);
    strcpy(q.method,"DELETE"); j = request(&m,&q,&r,""); CHECK(r.status == 200); cJSON_Delete(j);
    j = request(&m,&q,&r,""); CHECK(r.status == 404); cJSON_Delete(j);
    mcp_free(&m);
}
static char last_log[256];
static void log_line(void *ctx, const char *line) { (void)ctx; snprintf(last_log,sizeof last_log,"%s",line); }
static void session_lifecycle(void)
{
    fake_t f = {.now=100}; loop_ports_t ports = {.ctx=&f,.now=now,.poll=poll_event,.lease=lease};
    agent_loop_t l; policy_config_t p; policy_defaults(&p); loop_init(&l,&p,&ports);
    char token[65]; memset(token,'b',64); token[64] = 0;
    mcp_t m; mcp_init(&m,&l,token,"127.0.0.1:8765"); m.log = log_line;
    http_request_t q = {.tag=1,.has_auth=1}; http_response_t r;
    strcpy(q.method,"POST"); strcpy(q.path,"/mcp"); strcpy(q.host,m.host); strcpy(q.content_type,"application/json");
    snprintf(q.auth,sizeof q.auth,"Bearer %s",token);
    const char *init = "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2025-06-18\",\"capabilities\":{},\"clientInfo\":{}}}";
    char ids[MCP_SESSIONS][33];
    for (int i = 0; i < MCP_SESSIONS; i++) { cJSON_Delete(request(&m,&q,&r,init)); CHECK(r.status == 200); strcpy(ids[i],r.session); f.now++; }
    /* Touch oldest slot; second slot becomes LRU. */
    q.has_session = 1; strcpy(q.session,ids[0]); cJSON_Delete(request(&m,&q,&r,"{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"ping\"}"));
    q.has_session = 0; f.now++; cJSON_Delete(request(&m,&q,&r,init)); CHECK(r.status == 200);
    q.has_session = 1; strcpy(q.session,ids[1]); cJSON_Delete(request(&m,&q,&r,"{}")); CHECK(r.status == 404);
    /* Pending owner and task owner cannot be chosen for capacity eviction. */
    strcpy(m.pending_owner,ids[2]); m.pending = 99; strcpy(m.task.owner,ids[3]); m.task.live = 1;
    q.has_session = 0; f.now++; cJSON_Delete(request(&m,&q,&r,init)); CHECK(r.status == 200);
    CHECK(!strcmp(m.sessions[2].id,ids[2]) && !strcmp(m.sessions[3].id,ids[3]));
    /* Silent busy owners survive expiry; idle age starts again afterward. */
    m.task.queued = 1; m.task.stop_epoch = l.stop_epoch;
    f.now += MCP_SESSION_IDLE_MS+1; mcp_tick(&m);
    CHECK(m.task.live && !strcmp(m.sessions[2].id,ids[2]) && !strcmp(m.sessions[3].id,ids[3]));
    mcp_disconnect(&m,99); CHECK(strstr(last_log,"method=tools/call tool=none status=499")); CHECK(!m.pending);
    CHECK(!strstr(last_log,token)); m.task.live = 0;
    f.now += MCP_SESSION_IDLE_MS; mcp_tick(&m);
    CHECK(*m.sessions[2].id && *m.sessions[3].id);
    f.now++; mcp_tick(&m);
    for (int i = 0; i < MCP_SESSIONS; i++) CHECK(!*m.sessions[i].id);
    q.has_session = 1; strcpy(q.session,ids[2]); cJSON_Delete(request(&m,&q,&r,"{}")); CHECK(r.status == 404);
    /* Expire on access without a server tick, strict greater-than boundary. */
    q.has_session = 0; cJSON_Delete(request(&m,&q,&r,init)); strcpy(q.session,r.session); q.has_session = 1;
    f.now += MCP_SESSION_IDLE_MS; cJSON_Delete(request(&m,&q,&r,"{}")); CHECK(r.status == 200);
    f.now += MCP_SESSION_IDLE_MS+1; cJSON_Delete(request(&m,&q,&r,"{}")); CHECK(r.status == 404);
    mcp_free(&m);
}
int main(void) { parser(); tokens(); dispatch(); session_lifecycle(); printf("A6: %u checks passed\n",checks); return 0; }
