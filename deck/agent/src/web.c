#ifdef __APPLE__
#define _DARWIN_C_SOURCE
#endif
#define _POSIX_C_SOURCE 200809L
#include "web_store.h"
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <unistd.h>
static const cJSON *get(const cJSON *j, const char *k) { return cJSON_GetObjectItemCaseSensitive(j,k); }
static int load_configs(web_t *, llm_provider_t *, netcfg_t *);
static void reload_configs(web_t *);
static int64_t now(web_t *w) { return w->mcp->loop->ports.now(w->mcp->loop->ports.ctx); }
static void response(http_response_t *r, int status, const char *s)
{
    r->status = status; r->len = strlen(s); r->body = malloc(r->len+1);
    if (r->body) memcpy(r->body,s,r->len+1); else { r->len = 0; r->status = 500; }
}
static void json_response(http_response_t *r, int status, cJSON *j)
{
    r->body = cJSON_PrintUnformatted(j); r->len = r->body ? strlen(r->body) : 0;
    r->status = r->body ? status : 500; cJSON_Delete(j);
}
static int equal(const char *a, const char *b, size_t n)
{
    size_t size = a ? strlen(a) : 0; volatile unsigned diff = (unsigned)(size != n);
    for (size_t i = 0; i < n; i++) diff |= (unsigned)((i < size ? (unsigned char)a[i] : 0) ^ (unsigned char)b[i]);
    return !diff;
}
static int expired(int64_t start, int64_t t, int64_t ttl) { return t < start || t-start >= ttl; }
static void expire(web_t *w)
{
    int64_t t = now(w);
    if (w->pairing && expired(w->pair_at,t,WEB_PAIR_MS)) { w->pairing = 0; mem_wipe(w->code,sizeof w->code); }
    for (int i = 0; i < WEB_SESSIONS; i++) {
        web_session_t *s = &w->sessions[i];
        if (*s->cookie && (expired(s->created,t,WEB_ABSOLUTE_MS) || expired(s->activity,t,WEB_IDLE_MS))) {
            if (w->mcp->task.live && !strcmp(w->mcp->task.owner,s->cookie)) (void)mcp_task_cancel(w->mcp,w->mcp->task.id);
            mem_wipe(s,sizeof *s);
        }
    }
}
void web_init(web_t *w, mcp_t *m, runtime_t *r, const char *host, const char *secrets, const char *config)
{
    memset(w,0,sizeof *w); w->mcp = m; w->runtime = r;
    snprintf(w->host,sizeof w->host,"%s",host); snprintf(w->secrets,sizeof w->secrets,"%s",secrets); snprintf(w->config,sizeof w->config,"%s",config);
    /* Prefer validated persisted device configuration when it exists. */
    if (r) reload_configs(w);
}
void web_free(web_t *w) { netcfg_free(&w->net); mem_wipe(w->sessions,sizeof w->sessions); mem_wipe(w->code,sizeof w->code); }
void web_tick(void *v) { web_t *w = v; expire(w); mcp_tick(w->mcp); }
void web_attach_pump(void *v, void (*p)(void *), void *ctx) { mcp_attach_pump(((web_t *)v)->mcp,p,ctx); }
void web_disconnect(void *v, uint64_t tag)
{
    web_t *w = v; for (int i = 0; i < 2; i++) if (w->events[i].tag == tag) memset(&w->events[i],0,sizeof w->events[i]);
}
static web_session_t *session(web_t *w, const http_request_t *q)
{
    /* Accept one named cookie, refuse ambiguous duplicates and non-hex values. */
    const char *p = q->has_cookie ? q->cookie : ""; char cookie[33] = {0}; int found = 0;
    while (*p) {
        while (*p == ' ' || *p == ';') p++;
        const char *end = strchr(p,';'); size_t n = end ? (size_t)(end-p) : strlen(p);
        if (n >= 5 && !memcmp(p,"mk20=",5)) {
            if (found++ || n != 37) return NULL;
            memcpy(cookie,p+5,32);
        }
        p += n;
    }
    web_session_t *match = NULL;
    for (int i = 0; i < WEB_SESSIONS; i++) if (equal(cookie,w->sessions[i].cookie,32) && *w->sessions[i].cookie) match = &w->sessions[i];
    mem_wipe(cookie,sizeof cookie); return match;
}
static int pairing_code(char code[7])
{
    /* Rejection sampling on 24 random bits: largest multiple of 1,000,000. */
    char hex[7]; unsigned n;
    do { if (httpd_random_hex(hex,3)) return -1; n = (unsigned)strtoul(hex,NULL,16); } while (n >= 16000000);
    snprintf(code,7,"%06u",n % 1000000); mem_wipe(hex,sizeof hex); return 0;
}
static void pair_start(web_t *w, http_response_t *r)
{
    /* Do not let repeated starts bypass the five-attempt limit or replace a
     * displayed live challenge. Explicit expiry/attempt exhaustion starts anew. */
    if (w->pairing) { response(r,200,"{\"started\":true}"); return; }
    mem_wipe(w->code,sizeof w->code);
    if (pairing_code(w->code) || !w->display || w->display(w->display_ctx,w->code)) {
        mem_wipe(w->code,sizeof w->code); w->pairing = 0;
        response(r,503,"{\"error\":\"display_unavailable\"}"); return;
    }
    w->pairing = 1; w->pair_at = now(w); w->attempts = 0;
    response(r,200,"{\"started\":true}");
}
static void pair(web_t *w, const cJSON *j, http_response_t *r)
{
    const cJSON *code = get(j,"code"); const char *s = cJSON_IsString(code) ? code->valuestring : NULL;
    int good = equal(s,w->code,6) && web_only(j,"code") && w->pairing;
    if (!good) {
        if (w->pairing && ++w->attempts >= 5) { w->pairing = 0; mem_wipe(w->code,sizeof w->code); }
        response(r,401,"{\"error\":\"unauthorized\"}"); return;
    }
    web_session_t *slot = NULL;
    for (int i = 0; i < WEB_SESSIONS; i++) if (!*w->sessions[i].cookie) { slot = &w->sessions[i]; break; }
    if (!slot) { response(r,503,"{\"error\":\"busy\"}"); return; }
    char cookie[33], csrf[33];
    if (httpd_random_hex(cookie,16) || httpd_random_hex(csrf,16)) { response(r,500,"{}"); return; }
    strcpy(slot->cookie,cookie); strcpy(slot->csrf,csrf); slot->created = slot->activity = now(w);
    w->pairing = 0; mem_wipe(w->code,sizeof w->code);
    snprintf(r->cookie,sizeof r->cookie,"mk20=%s; HttpOnly; SameSite=Strict; Path=/",cookie);
    cJSON *out = cJSON_CreateObject(); cJSON_AddStringToObject(out,"csrf",csrf); json_response(r,200,out);
    mem_wipe(cookie,sizeof cookie); mem_wipe(csrf,sizeof csrf);
}
static void secret(web_t *w, const char *name, const http_request_t *q, const cJSON *j, http_response_t *r)
{
    char file[64]; if (web_secret_name(name,file)) { response(r,404,"{}"); return; }
    int write = !strcmp(q->method,"POST"), remove = !strcmp(q->method,"DELETE");
    if (!write && !remove && strcmp(q->method,"GET")) { response(r,405,"{}"); return; }
    const cJSON *value = get(j,"value"); size_t n = 0;
    if (write) {
        if (!web_only(j,"value") || !cJSON_IsString(value) || !(n = strlen(value->valuestring)) || n > 4096 || strpbrk(value->valuestring,"\r\n")) { response(r,400,"{}"); return; }
    }
    int dir = web_directory(w->secrets,write);
    if (dir < 0) {
        if (!write && errno == ENOENT) response(r,200,remove ? "{}" : "{\"set\":false}");
        else response(r,500,"{\"error\":\"storage\"}");
        return;
    }
    if (write) {
        int rc = web_atomic(dir,file,value->valuestring,n);
        if (rc) response(r,500,"{\"error\":\"storage\"}");
        else { char out[64]; snprintf(out,sizeof out,"{\"set\":true,\"len\":%zu}",n); response(r,200,out); }
    } else if (remove) response(r,web_remove(dir,file) ? 500 : 200,"{}");
    else {
        struct stat st; int rc = fstatat(dir,file,&st,AT_SYMLINK_NOFOLLOW);
        if (!rc && (!S_ISREG(st.st_mode) || st.st_uid != geteuid() || (st.st_mode & 07777) != 0600)) response(r,500,"{\"error\":\"storage\"}");
        else if (rc && errno != ENOENT) response(r,500,"{\"error\":\"storage\"}");
        else response(r,200,!rc && st.st_size > 0 ? "{\"set\":true}" : "{\"set\":false}");
    }
    close(dir);
}
static void config(web_t *w, const char *name, const http_request_t *q, http_response_t *r)
{
    if (strcmp(name,"net") && strcmp(name,"provider")) { response(r,404,"{}"); return; }
    if (!strcmp(q->method,"GET")) {
        r->body = web_read_config(w,name,&r->len); r->status = r->body ? 200 : 404; return;
    }
    if (strcmp(q->method,"PUT")) { response(r,405,"{}"); return; }
    if (w->mcp->task.live || w->mcp->pending) { response(r,409,"{\"error\":\"busy\"}"); return; }
    if (web_config_validate(w,name,q->body,q->body_len)) { response(r,400,"{\"error\":\"config\"}"); return; }
    int dir = web_directory(w->config,1); char file[32]; snprintf(file,sizeof file,"%s.json",name);
    int bad = dir < 0 || web_atomic(dir,file,q->body,q->body_len); if (dir >= 0) close(dir);
    if (!bad && w->runtime) reload_configs(w);
    response(r,bad ? 500 : 200,bad ? "{\"error\":\"storage\"}" : "{\"saved\":true}");
}
static int load_configs(web_t *w, llm_provider_t *p, netcfg_t *net)
{
    size_t pn, nn; char *ps = web_read_config(w,"provider",&pn), *ns = web_read_config(w,"net",&nn);
    int bad = !ps || !ns || llm_provider_parse(ps,pn,p) || netcfg_parse(ns,nn,net);
    free(ps); free(ns); return bad ? -1 : 0;
}
static void reload_configs(web_t *w)
{
    netcfg_free(&w->net); memset(&w->provider,0,sizeof w->provider);
    if (load_configs(w,&w->provider,&w->net)) { netcfg_free(&w->net); memset(&w->provider,0,sizeof w->provider); }
    if (w->runtime) { w->runtime->provider = &w->provider; w->runtime->net = &w->net; }
}
static int test_guard(void *v) { web_t *w = v; (void)loop_service(w->mcp->loop); return 0; }
static void connection_test(web_t *w, http_response_t *r)
{
    /* The synchronous reachability probe must not occupy the HTTP pump while
     * another task/direct call needs that pump for cancellation. */
    if (w->mcp->task.live || w->mcp->pending) { response(r,409,"{\"error\":\"busy\"}"); return; }
    llm_provider_t p = {0}; netcfg_t n = {0}; const char *error = "config"; int ok = 0; int64_t start = now(w);
    if (!load_configs(w,&p,&n)) {
        net_route_t route;
        if (netcfg_route_for(&n,p.route,&route) || route == NET_BLOCKED) error = "blocked";
        else if (w->connection_test) ok = !w->connection_test(w->test_ctx,&p,&n,&error);
        else {
            net_options_t options = w->runtime ? w->runtime->transport : (net_options_t){0};
            options.url = p.base_url; options.method = "GET"; options.key_file = p.api_key_file;
            options.config = &n; options.provider_route = p.route; options.timeout_ms = 4000; options.body_cap = 16384;
            options.service = test_guard; options.service_ctx = w;
            net_response_t out = {0}; (void)net_request(&options,&out);
            ok = out.http_status > 0; error = ok ? "none" : out.class_name ? out.class_name : "network"; net_response_free(&out);
        }
    }
    cJSON *out = cJSON_CreateObject(); cJSON_AddBoolToObject(out,"ok",ok); cJSON_AddNumberToObject(out,"latency_ms",(double)(now(w)-start)); cJSON_AddStringToObject(out,"error_class",ok ? "none" : error);
    netcfg_free(&n); json_response(r,200,out);
}
static cJSON *task_status(web_t *w)
{
    agent_task_t *t = &w->mcp->task; cJSON *j = cJSON_CreateObject();
    cJSON_AddStringToObject(j,"id",t->id); cJSON_AddStringToObject(j,"state",t->state); cJSON_AddNumberToObject(j,"steps",t->steps);
    cJSON *steps = cJSON_AddArrayToObject(j,"actions");
    /* The loop history contains fixed action names only; emit a bounded list. */
    char history[sizeof t->summary]; snprintf(history,sizeof history,"%s",t->summary);
    for (char *p = history; *p;) {
        while (*p == ' ') p++;
        if (!*p) break;
        char *end = strchr(p,';'); if (end) *end = 0;
        cJSON_AddItemToArray(steps,cJSON_CreateString(p)); if (!end) break; p = end+1;
    }
    cJSON *shots = cJSON_AddArrayToObject(j,"shots");
    for (size_t i = 0; i < 5; i++) if (w->mcp->shots[i].jpeg) cJSON_AddItemToArray(shots,cJSON_CreateNumber(w->mcp->shots[i].step));
    return j;
}
static void tasks(web_t *w, const http_request_t *q, const cJSON *j, web_session_t *s, http_response_t *r)
{
    mcp_t *m = w->mcp;
    if (!strcmp(q->path,"/api/tasks") && !strcmp(q->method,"POST")) {
        const cJSON *goal = get(j,"goal");
        if (!web_only(j,"goal") || !cJSON_IsString(goal)) { response(r,400,"{}"); return; }
        /* Load only validated device configs. Missing config still queues; the
         * autonomous runtime will fail closed until a valid provider exists. */
        if (!m->task.live && !m->pending && w->runtime) {
            reload_configs(w);
        }
        const char *why = mcp_task_queue(m,goal->valuestring,s->cookie);
        if (why) { response(r,!strcmp(why,"busy") ? 409 : 400,"{\"error\":\"rejected\"}"); return; }
        json_response(r,202,task_status(w)); return;
    }
    if (strlen(q->path) < 12) { response(r,405,"{}"); return; }
    const char *path = q->path+11; size_t n = strcspn(path,"/");
    if (!n || strlen(m->task.id) != n || memcmp(path,m->task.id,n)) { response(r,404,"{}"); return; }
    path += n;
    if (!*path && !strcmp(q->method,"GET")) { json_response(r,200,task_status(w)); return; }
    if (!strncmp(path,"/steps/",7) && !strcmp(q->method,"GET")) {
        const char *p = path+7; unsigned step = 0;
        if (!*p || *p == '0') { response(r,404,"{}"); return; }
        while (*p >= '0' && *p <= '9') { if (step > 999999) { response(r,404,"{}"); return; } step = step*10+(unsigned)(*p++-'0'); }
        if (!strcmp(p,"/shot.jpg")) for (size_t i = 0; i < 5; i++) {
            agent_shot_t *shot = &m->shots[i]; if (!shot->jpeg || shot->step != step) continue;
            r->body = malloc(shot->len); if (!r->body) { response(r,500,"{}"); return; }
            memcpy(r->body,shot->jpeg,shot->len); r->len = shot->len; r->status = 200; r->content_type = "image/jpeg"; return;
        }
        response(r,404,"{}"); return;
    }
    if (!strcmp(q->method,"POST")) {
        if (!strcmp(path,"/stop")) { loop_stop(m->loop); if (m->task.live) (void)mcp_task_cancel(m,m->task.id); else strcpy(m->task.state,"stopped"); json_response(r,200,task_status(w)); return; }
        if (m->task.live && (!strcmp(path,"/pause") || !strcmp(path,"/resume"))) {
            m->loop->paused = !strcmp(path,"/pause");
            if (m->loop->paused) strcpy(m->task.state,"paused");
            else if (m->task.queued) strcpy(m->task.state,m->loop->allowed && !m->loop->cancelled ? "queued" : "waiting_allow");
            json_response(r,200,task_status(w)); return;
        }
    }
    response(r,404,"{}");
}
static int events(web_t *w, const http_request_t *q, web_session_t *s, http_response_t *r)
{
    web_event_t *e = NULL;
    for (int i = 0; i < 2; i++) if (w->events[i].tag == q->tag) e = &w->events[i];
    if (!e) {
        for (int i = 0; i < 2; i++) if (!w->events[i].tag) { e = &w->events[i]; break; }
        if (!e || !q->tag) { response(r,503,"{\"error\":\"busy\"}"); return 0; }
        e->tag = q->tag; strcpy(e->owner,s->cookie); e->sent = now(w)-WEB_EVENT_INTERVAL_MS;
    }
    if (strcmp(e->owner,s->cookie)) return 3;
    if (now(w)-e->sent < WEB_EVENT_INTERVAL_MS) return 1;
    cJSON *status = task_status(w); char *data = cJSON_PrintUnformatted(status); cJSON_Delete(status);
    if (!data) return 3;
    size_t n = strlen(data); r->body = malloc(n+32);
    if (!r->body) { free(data); return 3; }
    int size = snprintf(r->body,n+32,"event: task\ndata: %s\n\n",data); free(data);
    r->len = (size_t)size; r->status = 200; r->stream = 1; r->content_type = "text/event-stream"; e->sent = now(w); return 2;
}
int web_handle(void *v, const http_request_t *q, http_response_t *r)
{
    web_t *w = v; expire(w);
    const char *path = q->path; int read = !strcmp(q->method,"GET");
    int public_page = read && (!strcmp(path,"/") || !strcmp(path,"/chat"));
    int start = !strcmp(q->method,"POST") && !strcmp(path,"/pair/start"), pairing = !strcmp(q->method,"POST") && !strcmp(path,"/pair");
    web_session_t *s = session(w,q);
    if (!public_page && !start && !pairing && !s) {
        for (int i = 0; i < 2; i++) if (w->events[i].tag == q->tag) return 3;
        response(r,401,"{\"error\":\"unauthorized\"}"); return 0;
    }
    if (strcmp(q->host,w->host) || (q->has_origin && strcmp(q->origin,"http://172.30.250.2:8780"))) { response(r,403,"{\"error\":\"forbidden\"}"); return 0; }
    if (!read && (!q->has_origin && (!q->has_fetch_site || strcmp(q->fetch_site,"same-origin")))) { response(r,403,"{\"error\":\"forbidden\"}"); return 0; }
    if (!read && !start && !pairing && (!q->has_csrf || !s || !equal(q->csrf,s->csrf,32))) { response(r,403,"{\"error\":\"forbidden\"}"); return 0; }
    if (s && strcmp(path,"/api/events")) s->activity = now(w);
    if (public_page) {
        response(r,200,web_page(!strcmp(path,"/chat")));
        r->content_type = "text/html; charset=utf-8";
        r->csp = web_csp();
        return 0;
    }
    if (start) { pair_start(w,r); return 0; }
    cJSON *j = NULL;
    if (!read && q->body_len) {
        if (strcasecmp(q->content_type,"application/json") && strcasecmp(q->content_type,"application/json; charset=utf-8")) { response(r,415,"{}"); return 0; }
        j = agent_json_parse(q->body,q->body_len,HTTPD_BODY);
        if (!j) { response(r,400,"{}"); return 0; }
    }
    int rc = 0;
    if (pairing) pair(w,j,r);
    else if (!strncmp(path,"/secret/",8)) secret(w,path+8,q,j,r);
    else if (!strncmp(path,"/config/",8)) config(w,path+8,q,r);
    else if (!strcmp(path,"/test") && !strcmp(q->method,"POST")) connection_test(w,r);
    else if (!strncmp(path,"/api/tasks",10) && (!path[10] || path[10] == '/')) tasks(w,q,j,s,r);
    else if (!strcmp(path,"/api/events") && read) rc = events(w,q,s,r);
    else response(r,404,"{}");
    /* Secret input strings are wiped before freeing the JSON allocation. */
    if (!strncmp(path,"/secret/",8)) { const cJSON *value = get(j,"value"); if (cJSON_IsString(value)) mem_wipe(value->valuestring,strlen(value->valuestring)); }
    cJSON_Delete(j); return rc;
}
