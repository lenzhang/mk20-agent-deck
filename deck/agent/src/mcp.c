#include "mcp.h"
#include <inttypes.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
static const cJSON *get(const cJSON *j, const char *s) { return cJSON_GetObjectItemCaseSensitive(j,s); }
static int string(const cJSON *v, const char *s) { return cJSON_IsString(v) && !strcmp(v->valuestring,s); }
/* One registry drives list, schemas and dispatch. No enable/grant operation. */
typedef struct { const char *name; int action; const char *fields; } tool_t;
static const tool_t registry[] = {
    {"status",-1,""},{"screenshot",-1,""},
    {"click",A_CLICK,"x,y,risk,target"},{"double_click",A_DOUBLE_CLICK,"x,y,risk,target"},
    {"move",A_MOVE,"x,y,risk,target"},{"drag",A_DRAG,"x1,y1,x2,y2,risk,target"},
    {"scroll",A_SCROLL,"x,y,dx,dy,risk,target"},{"type_text",A_TYPE,"text,risk,target"},
    {"key",A_KEY,"combo,risk,target"},{"estop",-1,""},
    {"run_task",-1,"goal"},{"task_status",-1,"id"},{"cancel_task",-1,"id"}
};
#define NTOOLS (sizeof registry/sizeof *registry)
static cJSON *schema(const tool_t *t)
{
    cJSON *j = cJSON_CreateObject(); cJSON_AddStringToObject(j,"type","object");
    cJSON_AddBoolToObject(j,"additionalProperties",0);
    cJSON *props = cJSON_AddObjectToObject(j,"properties"), *required = cJSON_AddArrayToObject(j,"required");
    char b[128]; snprintf(b,sizeof b,"%s",t->fields);
    for (char *p = b; *p;) {
        char *comma = strchr(p,','); if (comma) *comma = 0;
        int integer = !strcmp(p,"x") || !strcmp(p,"y") || !strcmp(p,"x1") || !strcmp(p,"y1") || !strcmp(p,"x2") || !strcmp(p,"y2") || !strcmp(p,"dx") || !strcmp(p,"dy");
        cJSON *f = cJSON_AddObjectToObject(props,p); cJSON_AddStringToObject(f,"type",integer ? "integer" : "string");
        if (integer) { int delta = !strcmp(p,"dx") || !strcmp(p,"dy"); cJSON_AddNumberToObject(f,"minimum",delta ? -127 : 0); cJSON_AddNumberToObject(f,"maximum",delta ? 127 : 1000000); }
        else if (!strcmp(p,"risk")) { cJSON *e = cJSON_AddArrayToObject(f,"enum"); cJSON_AddItemToArray(e,cJSON_CreateString("low")); cJSON_AddItemToArray(e,cJSON_CreateString("high")); }
        else { cJSON_AddNumberToObject(f,"maxLength",!strcmp(p,"goal") ? 4096 : !strcmp(p,"text") ? 256 : !strcmp(p,"target") ? 512 : !strcmp(p,"combo") ? 95 : 23); }
        if (strcmp(p,"dx")) cJSON_AddItemToArray(required,cJSON_CreateString(p));
        if (!comma) break;
        p = comma+1;
    }
    return j;
}
cJSON *mcp_tools(void)
{
    cJSON *j = cJSON_CreateObject(), *array = cJSON_AddArrayToObject(j,"tools");
    for (size_t i = 0; i < NTOOLS; i++) {
        cJSON *t = cJSON_CreateObject(); cJSON_AddStringToObject(t,"name",registry[i].name);
        cJSON_AddStringToObject(t,"description",registry[i].action >= 0 ? "Keyboard-authorized action in latest screenshot pixels; risk and target required. May require physical confirmation." : registry[i].name);
        cJSON_AddItemToObject(t,"inputSchema",schema(&registry[i])); cJSON_AddItemToArray(array,t);
    }
    return j;
}
static cJSON *text_result(const char *s, int error)
{
    cJSON *j = cJSON_CreateObject(), *a = cJSON_AddArrayToObject(j,"content"), *c = cJSON_CreateObject();
    cJSON_AddStringToObject(c,"type","text"); cJSON_AddStringToObject(c,"text",s); cJSON_AddItemToArray(a,c); cJSON_AddBoolToObject(j,"isError",error); return j;
}
static void reply(http_response_t *r, const cJSON *id, cJSON *result, int code, const char *message)
{
    cJSON *j = cJSON_CreateObject(); cJSON_AddStringToObject(j,"jsonrpc","2.0");
    cJSON_AddItemToObject(j,"id",id ? cJSON_Duplicate(id,1) : cJSON_CreateNull());
    if (code) { cJSON *e = cJSON_AddObjectToObject(j,"error"); cJSON_AddNumberToObject(e,"code",code); cJSON_AddStringToObject(e,"message",message); cJSON_Delete(result); }
    else cJSON_AddItemToObject(j,"result",result);
    r->status = 200; r->body = cJSON_PrintUnformatted(j); r->len = r->body ? strlen(r->body) : 0; if (!r->body) r->status = 500;
    cJSON_Delete(j);
}
static void shots_clear(mcp_t *m)
{
    for (size_t i = 0; i < 5; i++) { free(m->shots[i].jpeg); memset(&m->shots[i],0,sizeof m->shots[i]); }
    m->shot_next = 0;
}
static void frame_ready(void *v, const loop_frame_t *f, unsigned step)
{
    mcp_t *m = v; if (!m->task.live || !f->jpeg || !f->jpeg_len || f->jpeg_len > OAI_IMAGE_MAX) return;
    unsigned char *p = malloc(f->jpeg_len); if (!p) return;
    memcpy(p,f->jpeg,f->jpeg_len); agent_shot_t *s = &m->shots[m->shot_next++ % 5];
    free(s->jpeg); *s = (agent_shot_t){step,p,f->jpeg_len};
}
void mcp_init(mcp_t *m, agent_loop_t *loop, const char token[65], const char *host)
{
    memset(m,0,sizeof *m); m->loop = loop; memcpy(m->token,token,65); snprintf(m->host,sizeof m->host,"%s",host);
    loop->frame_ready = frame_ready; loop->frame_ctx = m;
}
void mcp_free(mcp_t *m) { shots_clear(m); m->loop->frame_ready = NULL; m->loop->frame_ctx = NULL; cJSON_Delete(m->pending_id); m->pending_id = NULL; memset(m->token,0,sizeof m->token); memset(m->task.goal,0,sizeof m->task.goal); }
static void log_status(mcp_t *, const char *, const char *, int, int64_t);
static int active(const agent_loop_t *l) { return l->state >= S_OBSERVING && l->state <= S_AWAITING_CONFIRM; }
static int session_busy(const mcp_t *m, const mcp_session_t *s)
{
    return (m->pending && !strcmp(m->pending_owner,s->id)) ||
        (m->task.live && !strcmp(m->task.owner,s->id));
}
static void session_remove(mcp_t *m, mcp_session_t *s)
{
    if (m->pending && !strcmp(m->pending_owner,s->id)) loop_stop(m->loop);
    if (m->task.live && !strcmp(m->task.owner,s->id)) (void)mcp_task_cancel(m,m->task.id);
    memset(s,0,sizeof *s);
}
static void sessions_expire(mcp_t *m, int64_t now)
{
    for (int i = 0; i < MCP_SESSIONS; i++) {
        mcp_session_t *s = &m->sessions[i];
        if (*s->id && (now < s->last_activity || now-s->last_activity > MCP_SESSION_IDLE_MS)) session_remove(m,s);
    }
}
static const char *gate(mcp_t *m)
{
    (void)loop_service(m->loop);
    if (m->loop->cancelled) return "stopped";
    if (!m->loop->allowed) return "disabled";
    if (m->task.live || m->pending || active(m->loop)) return "busy";
    return NULL;
}
static const char *task_create(mcp_t *m, const char *goal, const char *owner)
{
    if (!goal || !*goal || strlen(goal) > 4096 || m->task.serial == 999999999) return "rejected";
    unsigned serial = m->task.serial+1; shots_clear(m); m->loop->paused = 0;
    memset(&m->task,0,sizeof m->task); m->task.serial = serial;
    m->task.stop_epoch = m->loop->stop_epoch;
    snprintf(m->task.id,sizeof m->task.id,"t%u",serial); strcpy(m->task.goal,goal);
    snprintf(m->task.owner,sizeof m->task.owner,"%s",owner ? owner : "");
    strcpy(m->task.state,m->loop->allowed && !m->loop->cancelled ? "queued" : "waiting_allow"); m->task.live = m->task.queued = 1;
    for (int i = 0; i < MCP_SESSIONS; i++) m->sessions[i].has_frame = 0;
    return NULL;
}
const char *mcp_task_submit(mcp_t *m, const char *goal, const char *owner)
{
    const char *why = gate(m); return why ? why : task_create(m,goal,owner);
}
const char *mcp_task_queue(mcp_t *m, const char *goal, const char *owner)
{
    (void)loop_service(m->loop);
    if (m->task.live || m->pending || active(m->loop)) return "busy";
    return task_create(m,goal,owner);
}
const char *mcp_task_cancel(mcp_t *m, const char *id)
{
    if (!id || strcmp(id,m->task.id) || !*id) return "rejected";
    if (m->task.live) { loop_stop(m->loop); shots_clear(m); m->task.live = m->task.queued = 0; strcpy(m->task.state,"stopped"); memset(m->task.goal,0,sizeof m->task.goal); }
    return NULL;
}
void mcp_attach_pump(void *v, void (*pump)(void *), void *ctx)
{
    mcp_t *m = v; m->loop->cooperate = pump; m->loop->cooperate_ctx = ctx;
}
void mcp_tick(void *v)
{
    mcp_t *m = v; sessions_expire(m,m->loop->ports.now(m->loop->ports.ctx)); (void)loop_service(m->loop);
    /* A stop after acceptance cancels queued work too; a future allow cannot
     * revive a goal the keyboard has stopped. New work queued while already
     * stopped may wait for a fresh physical allow. */
    if (m->task.live && m->task.queued && m->task.stop_epoch != m->loop->stop_epoch) (void)mcp_task_cancel(m,m->task.id);
    if (m->task.live && m->task.queued && m->loop->allowed && !m->loop->cancelled && !m->loop->paused) {
        m->task.queued = 0;
        if (m->task_goal) m->task_goal(m->ctx,m->task.goal);
        if (loop_submit(m->loop)) { m->task.live = 0; strcpy(m->task.state,m->loop->cancelled ? "stopped" : "disabled"); memset(m->task.goal,0,sizeof m->task.goal); }
    }
    if (active(m->loop)) loop_tick(m->loop);
    if (m->task.live && !m->task.queued) {
        snprintf(m->task.summary,sizeof m->task.summary,"%s",m->loop->history);
        m->task.steps = m->loop->steps; snprintf(m->task.state,sizeof m->task.state,"%s",m->loop->paused && active(m->loop) ? "paused" : loop_state_name(m->loop->state));
        if (!active(m->loop)) { m->task.live = 0; shots_clear(m); memset(m->task.goal,0,sizeof m->task.goal); }
    }
}
void mcp_disconnect(void *v, uint64_t tag)
{
    mcp_t *m = v; if (!m->pending || tag != m->pending) return;
    log_status(m,"tools/call",m->pending_tool,499,m->pending_start);
    loop_stop(m->loop); m->pending = 0; cJSON_Delete(m->pending_id); m->pending_id = NULL;
}
static const char *outcome(mcp_t *m)
{
    agent_loop_t *l = m->loop; const char *r = l->reason;
    if (l->cancelled) return "stopped";
    if (l->state == S_DONE && !strcmp(r,"done")) return "ok";
    if (!strcmp(r,"confirm_timeout")) return "needs_confirmation: timeout";
    if (!strcmp(r,"confirm_denied")) return "needs_confirmation: denied";
    if (strstr(r,"budget")) return "budget";
    if (strstr(r,"unsupported")) return "unsupported";
    if (!strcmp(r,"never_do") || !strcmp(r,"secure_input") || !strcmp(r,"lock_screen") || !strcmp(r,"schema") || !strcmp(r,"coordinates")) return "rejected";
    return "error";
}
static int capture_guard(void *v) { mcp_t *m = v; (void)loop_service(m->loop); return m->loop->cancelled; }
static cJSON *screenshot(mcp_t *m, mcp_session_t *s)
{
    s->has_frame = 0; loop_frame_t f = {0};
    if (!m->snapshot || m->snapshot(m->ctx,&f,capture_guard,m) || !f.jpeg || f.jpeg_len > OAI_IMAGE_MAX || f.width < 1 || f.height < 1) return text_result(m->loop->cancelled ? "stopped" : "error",1);
    size_t n = (f.jpeg_len+2)/3*4; char *b = malloc(n+1); if (!b) return text_result("error",1);
    const char *base = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"; size_t at = 0;
    for (size_t i = 0; i < f.jpeg_len; i += 3) {
        unsigned x = (unsigned)f.jpeg[i]<<16; if (i+1 < f.jpeg_len) x |= (unsigned)f.jpeg[i+1]<<8; if (i+2 < f.jpeg_len) x |= f.jpeg[i+2];
        b[at++] = base[x>>18]; b[at++] = base[(x>>12)&63]; b[at++] = i+1 < f.jpeg_len ? base[(x>>6)&63] : '='; b[at++] = i+2 < f.jpeg_len ? base[x&63] : '=';
    }
    b[at] = 0;
    s->frame = f; s->frame.jpeg = NULL; s->frame.jpeg_len = 0; s->has_frame = 1; s->frame_number = ++m->frames;
    char info[256]; snprintf(info,sizeof info,"frame=%u width=%d height=%d source=%dx%d scale_x=%.6f scale_y=%.6f coordinates=screenshot_pixels calibration=%s",s->frame_number,f.width,f.height,f.source_width,f.source_height,
        f.source_width > 0 ? (double)f.source_width/f.width : 1.0,f.source_height > 0 ? (double)f.source_height/f.height : 1.0,m->calibrated && m->calibrated(m->ctx,&f) ? "valid" : "required");
    cJSON *result = text_result(info,0), *image = cJSON_CreateObject();
    cJSON_AddStringToObject(image,"type","image"); cJSON_AddStringToObject(image,"data",b); cJSON_AddStringToObject(image,"mimeType","image/jpeg");
    cJSON_AddItemToArray(cJSON_GetObjectItemCaseSensitive(result,"content"),image); free(b); return result;
}
static int valid_args(const tool_t *t, const cJSON *args)
{
    if (!cJSON_IsObject(args)) return 0;
    cJSON *sch = schema(t); const cJSON *props = get(sch,"properties"), *required = get(sch,"required"), *v;
    int valid = 1;
    cJSON_ArrayForEach(v,required) if (!get(args,v->valuestring)) valid = 0;
    cJSON_ArrayForEach(v,args) {
        const cJSON *f = get(props,v->string); if (!f) { valid = 0; break; }
        if (string(get(f,"type"),"integer")) {
            if (!cJSON_IsNumber(v) || !isfinite(v->valuedouble) || floor(v->valuedouble) != v->valuedouble || v->valuedouble < get(f,"minimum")->valuedouble || v->valuedouble > get(f,"maximum")->valuedouble) valid = 0;
        } else {
            if (!cJSON_IsString(v)) valid = 0;
            else if (get(f,"maxLength") && strlen(v->valuestring) > get(f,"maxLength")->valuedouble * (!strcmp(v->string,"text") ? 4 : 1)) valid = 0;
            if (!strcmp(v->string,"risk") && !string(v,"low") && !string(v,"high")) valid = 0;
        }
    }
    cJSON_Delete(sch); return valid;
}
static cJSON *call(mcp_t *m, mcp_session_t *s, const tool_t *t, const cJSON *args, const cJSON *id, uint64_t tag)
{
    const char *name = t->name, *why = NULL;
    if (!strcmp(name,"status")) {
        char b[160]; (void)loop_service(m->loop);
        snprintf(b,sizeof b,"state=%s allowed=%s steps=%u task=%s",loop_state_name(m->loop->state),m->loop->allowed ? "true" : "false",m->loop->steps,m->task.live ? m->task.id : "none"); return text_result(b,0);
    }
    if (!strcmp(name,"estop")) { loop_stop(m->loop); if (m->task.live) (void)mcp_task_cancel(m,m->task.id); return text_result("stopped",0); }
    if (!strcmp(name,"task_status")) {
        if (strcmp(get(args,"id")->valuestring,m->task.id) || !*m->task.id) return text_result("rejected",1);
        char b[2300]; snprintf(b,sizeof b,"id=%s state=%s steps=%u summary=%s",m->task.id,m->task.state,m->task.steps,m->task.summary); return text_result(b,0);
    }
    if (!strcmp(name,"cancel_task")) { why = mcp_task_cancel(m,get(args,"id")->valuestring); return text_result(why ? why : "stopped",why != NULL); }
    if (!strcmp(name,"run_task")) { why = mcp_task_submit(m,get(args,"goal")->valuestring,s->id); return text_result(why ? why : m->task.id,why != NULL); }
    if (!strcmp(name,"screenshot")) {
        if (m->loop->cancelled) return text_result("stopped",1);
        if (m->pending || m->task.live) return text_result("busy",1);
        return screenshot(m,s);
    }
    why = gate(m); if (why) return text_result(why,1);
    if (!s->has_frame || s->frame_number != m->frames || !m->calibrated || !m->calibrated(m->ctx,&s->frame)) return text_result("rejected: screenshot/calibration required",1);
    if (m->preflight && m->preflight(m->ctx,&s->frame)) return text_result("rejected",1);
    action_kind_t kind = (action_kind_t)t->action;
    if (kind == A_TYPE) {
        const cJSON *text = get(args,"text");
        for (const unsigned char *p = (const unsigned char *)text->valuestring; *p; p++) if (*p > 127) { kind = A_PASTE; break; }
    }
    cJSON *a = cJSON_Duplicate(args,1); cJSON_AddStringToObject(a,"action",action_name(kind)); action_t action;
    int rc = action_parse(a,&action); cJSON_Delete(a); if (rc) return text_result("rejected",1);
    if (loop_direct(m->loop,&action,&s->frame)) return text_result("error",1);
    if (!active(m->loop)) { why = outcome(m); return text_result(why,strcmp(why,"ok") != 0); }
    m->pending_start = m->loop->ports.now(m->loop->ports.ctx); m->pending_tool = t->name;
    m->pending = tag; m->pending_id = cJSON_Duplicate(id,1); strcpy(m->pending_owner,s->id); return NULL;
}
static mcp_session_t *session(mcp_t *m, const char *id)
{
    for (int i = 0; i < MCP_SESSIONS; i++) if (*m->sessions[i].id && !strcmp(m->sessions[i].id,id)) return &m->sessions[i];
    return NULL;
}
static void log_status(mcp_t *m, const char *method, const char *tool, int status, int64_t start)
{
    if (!m->log) return;
    const char *fixed = "none";
    for (size_t i = 0; i < NTOOLS; i++) if (tool == registry[i].name) fixed = registry[i].name;
    char raw[256], safe[256]; const char *secrets[] = {m->token};
    snprintf(raw,sizeof raw,"method=%s tool=%s status=%d latency_ms=%" PRId64,method,fixed,status,m->loop->ports.now(m->loop->ports.ctx)-start);
    agent_redact(raw,secrets,1,safe,sizeof safe); m->log(m->ctx,safe);
}
int mcp_handle(void *v, const http_request_t *q, http_response_t *r)
{
    mcp_t *m = v; int64_t start = m->loop->ports.now(m->loop->ports.ctx);
    const char *logged_method = "request", *logged_tool = "none";
    cJSON *j = NULL;
    sessions_expire(m,start);
    if (!httpd_authorized(q->has_auth ? q->auth : NULL,m->token)) { r->status = 401; goto end; }
    if (strcmp(q->host,m->host) || (q->has_origin && strcmp(q->origin,"http://172.30.250.2:8765")) || (q->has_version && strcmp(q->version,MCP_VERSION))) { r->status = 400; goto end; }
    if (strcmp(q->path,"/mcp")) { r->status = 404; goto end; }
    if (strcmp(q->method,"POST") && strcmp(q->method,"DELETE")) { r->status = 405; goto end; }
    if (m->pending == q->tag && q->tag) {
        if (active(m->loop)) return 1;
        const char *why = outcome(m); reply(r,m->pending_id,text_result(why,strcmp(why,"ok") != 0),0,NULL);
        m->pending = 0; cJSON_Delete(m->pending_id); m->pending_id = NULL; logged_method = "tools/call"; logged_tool = m->pending_tool; start = m->pending_start; goto end;
    }
    mcp_session_t *s = q->has_session ? session(m,q->session) : NULL;
    if (q->has_session && !s) { r->status = 404; goto end; }
    if (s) s->last_activity = start;
    if (!strcmp(q->method,"DELETE")) {
        if (!s) { r->status = 400; goto end; }
        session_remove(m,s); r->status = 200; goto end;
    }
    if (strcasecmp(q->content_type,"application/json") && strcasecmp(q->content_type,"application/json; charset=utf-8")) { r->status = 415; goto end; }
    j = agent_json_parse(q->body,q->body_len,HTTPD_BODY);
    if (!j) { reply(r,NULL,NULL,-32700,"Parse error"); goto end; }
    const cJSON *id = get(j,"id"), *method = get(j,"method"), *params = get(j,"params");
    if (!cJSON_IsObject(j) || !string(get(j,"jsonrpc"),"2.0") || (id && !cJSON_IsNull(id) && !cJSON_IsString(id) && (!cJSON_IsNumber(id) || !isfinite(id->valuedouble))) || (method && !cJSON_IsString(method))) { reply(r,NULL,NULL,-32600,"Invalid Request"); goto end; }
    int init = string(method,"initialize");
    if (!s && !init) { r->status = 400; goto end; }
    if (!method) {
        if (id && (!!get(j,"result") != !!get(j,"error"))) r->status = 202;
        else reply(r,NULL,NULL,-32600,"Invalid Request");
        goto end;
    }
    if (!id) { if (string(method,"notifications/initialized") && s) s->initialized = 1; r->status = 202; goto end; }
    if (init) {
        logged_method = "initialize";
        if (s || !cJSON_IsObject(params) || !cJSON_IsString(get(params,"protocolVersion")) || !cJSON_IsObject(get(params,"capabilities")) || !cJSON_IsObject(get(params,"clientInfo"))) { reply(r,id,NULL,-32602,"Invalid params"); goto end; }
        for (int i = 0; i < MCP_SESSIONS; i++) if (!*m->sessions[i].id) { s = &m->sessions[i]; break; }
        if (!s) {
            for (int i = 0; i < MCP_SESSIONS; i++) {
                mcp_session_t *candidate = &m->sessions[i];
                if (!session_busy(m,candidate) && (!s || candidate->last_activity < s->last_activity)) s = candidate;
            }
            if (s) session_remove(m,s);
        }
        if (!s || httpd_random_hex(s->id,16)) { r->status = 503; goto end; }
        s->last_activity = start;
        cJSON *result = cJSON_CreateObject(); cJSON_AddStringToObject(result,"protocolVersion",MCP_VERSION);
        cJSON *cap = cJSON_AddObjectToObject(result,"capabilities"), *tools = cJSON_AddObjectToObject(cap,"tools"); cJSON_AddBoolToObject(tools,"listChanged",0);
        cJSON *info = cJSON_AddObjectToObject(result,"serverInfo"); cJSON_AddStringToObject(info,"name","mk20-agent"); cJSON_AddStringToObject(info,"version","A6");
        strcpy(r->session,s->id); reply(r,id,result,0,NULL); goto end;
    }
    if (params && !cJSON_IsObject(params) && !cJSON_IsArray(params)) { reply(r,id,NULL,-32602,"Invalid params"); goto end; }
    if (string(method,"ping")) {
        logged_method = "ping";
        if (params && (!cJSON_IsObject(params) || params->child)) reply(r,id,NULL,-32602,"Invalid params");
        else reply(r,id,cJSON_CreateObject(),0,NULL);
    }
    else if (string(method,"tools/list")) {
        logged_method = "tools/list";
        if (params && (!cJSON_IsObject(params) || params->child)) reply(r,id,NULL,-32602,"Invalid params");
        else reply(r,id,mcp_tools(),0,NULL);
    } else if (string(method,"tools/call")) {
        logged_method = "tools/call"; const cJSON *name = get(params,"name"), *args = get(params,"arguments"); const tool_t *t = NULL;
        if (cJSON_IsString(name)) for (size_t i = 0; i < NTOOLS; i++) if (!strcmp(registry[i].name,name->valuestring)) t = &registry[i];
        cJSON *empty = NULL; if (!args) args = empty = cJSON_CreateObject();
        if (!s->initialized || !t || !valid_args(t,args)) reply(r,id,NULL,-32602,"Invalid params");
        else {
            logged_tool = t->name; cJSON *result = call(m,s,t,args,id,q->tag);
            if (!result) { cJSON_Delete(empty); cJSON_Delete(j); return 1; }
            reply(r,id,result,0,NULL);
        }
        cJSON_Delete(empty);
    } else reply(r,id,NULL,-32601,"Method not found");
end:
    cJSON_Delete(j); log_status(m,logged_method,logged_tool,r->status,start); return 0;
}
