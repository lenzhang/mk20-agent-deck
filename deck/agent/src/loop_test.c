#define _POSIX_C_SOURCE 200809L
#include "loop_test.h"
#include "kbdc.h"
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
typedef struct {
    cJSON *script, *logs, *acts, *states; agent_loop_t *loop; kbdc_t kbd;
    int real, allow_sent, cancelled, confirm_sent, confirm_mode, exec_index, obs_index, model_index;
    int64_t time, confirm_at; unsigned cid, tick_ms, ticks, emitted[128], confirms, renews, switches, allups;
} fake_t;
static const cJSON *get(const cJSON *j, const char *k) { return cJSON_GetObjectItemCaseSensitive(j,k); }
static int integer(const cJSON *j, const char *k, int def) { const cJSON *v = get(j,k); return cJSON_IsNumber(v) && v->valuedouble >= 0 && v->valuedouble <= 10000000 && v->valuedouble == v->valueint ? v->valueint : def; }
static const char *string(const cJSON *j, const char *k, const char *def) { const cJSON *v = get(j,k); return cJSON_IsString(v) ? v->valuestring : def; }
static int64_t now(void *v) { fake_t *f = v; return f->real ? kbdc_now() : f->time; }
static int poll_event(void *v, kbd_event_t *e)
{
    fake_t *f = v;
    if (f->real) return kbdc_poll(&f->kbd,e);
    if (!f->allow_sent && cJSON_IsTrue(get(f->script,"allow"))) { f->allow_sent = 1; e->kind = K_ALLOW; return 1; }
    const cJSON *events = get(f->script,"events");
    for (int i = 0; i < cJSON_GetArraySize(events) && i < 128; i++) {
        const cJSON *event = cJSON_GetArrayItem(events,i);
        if (f->emitted[i] || strcmp(string(event,"state",""),loop_state_name(f->loop->state)) ||
            f->ticks < (unsigned)integer(event,"tick",0) || f->time < integer(event,"at_ms",0)) continue;
        f->emitted[i] = 1; const char *kind = string(event,"kind",""); memset(e,0,sizeof *e);
        if (!strcmp(kind,"estop")) e->kind = K_ESTOP;
        else if (!strcmp(kind,"allow")) e->kind = K_ALLOW;
        else if (!strcmp(kind,"lease_lost")) e->kind = K_LEASE_LOST;
        else if (!strcmp(kind,"hid")) { e->kind = K_HID_HEALTH; e->ok = cJSON_IsTrue(get(event,"ok")); }
        else if (!strcmp(kind,"confirm")) { e->kind = K_CONFIRM; e->cid = (unsigned)integer(event,"cid",0); e->ok = cJSON_IsTrue(get(event,"ok")); }
        else return -1;
        return 1;
    }
    if (f->cid && !f->confirm_sent && f->confirm_mode != 2 && f->time - f->confirm_at >= 300) {
        f->confirm_sent = 1; *e = (kbd_event_t){.kind=K_CONFIRM,.cid=f->cid,.ok=f->confirm_mode != 1}; return 1;
    }
    return 0;
}
static int delay(fake_t *f, int ms, loop_guard_t g, void *ctx)
{
    for (int spent = 0; spent < ms; spent += 5) {
        if (f->real) { struct timespec t = {0,5000000}; nanosleep(&t,NULL); } else f->time += ms - spent > 5 ? 5 : ms - spent;
        if (g(ctx)) return -1;
    }
    return g(ctx);
}
static int observe(void *v, loop_frame_t *out, loop_guard_t g, void *ctx)
{
    fake_t *f = v; const cJSON *a = get(f->script,"observations"), *j = cJSON_GetArrayItem(a,f->obs_index++);
    if (delay(f,integer(j,"delay_ms",0),g,ctx)) return -1;
    if (cJSON_IsTrue(get(j,"estop"))) { loop_stop(f->loop); return -1; }
    memset(out,0,sizeof *out); out->width = 1001; out->height = 801;
    out->hash = (uint64_t)integer(j,"hash",f->obs_index);
    snprintf(out->title,sizeof out->title,"%s",string(j,"title","Notes"));
    snprintf(out->app,sizeof out->app,"%s",string(j,"app","Notes"));
    out->locked = cJSON_IsTrue(get(j,"locked")); out->secure_input = cJSON_IsTrue(get(j,"secure_input"));
    return 0;
}
static int model(void *v, const loop_frame_t *frame, const char *history, loop_reply_t *out, loop_guard_t g, void *ctx)
{
    (void)frame; (void)history; fake_t *f = v;
    const cJSON *j = cJSON_GetArrayItem(get(f->script,"model"),f->model_index++);
    if (!j || delay(f,integer(j,"delay_ms",0),g,ctx)) return -1;
    if (cJSON_IsTrue(get(j,"estop"))) { loop_stop(f->loop); return -1; }
    const cJSON *error = get(j,"error");
    if (cJSON_IsString(error)) {
        out->error = llm_net_error(error->valuestring);
        for (int e = LLM_OK; e <= LLM_IO; e++) if (!strcmp(llm_error_name((llm_error_t)e),error->valuestring)) out->error = (llm_error_t)e;
        out->retryable = cJSON_IsTrue(get(j,"retryable")) && !strcmp(llm_error_name(out->error),error->valuestring); return -1;
    }
    snprintf(out->model,sizeof out->model,"%s",string(j,"model","fake-vl"));
    out->input_tokens = (unsigned)integer(j,"input_tokens",7); out->output_tokens = (unsigned)integer(j,"output_tokens",3);
    return action_parse(j,&out->action);
}
static int lease(void *v, lease_op_t op)
{
    fake_t *f = v;
    if (op == LEASE_RENEW) f->renews++;
    if (op == LEASE_VNC || op == LEASE_HID) f->switches++;
    if (op == LEASE_ALLUP) f->allups++;
    if (f->real) return kbdc_lease(&f->kbd,op);
    if (op == LEASE_ESTOP) f->allow_sent = 1;
    if (op == LEASE_RENEW && cJSON_IsTrue(get(f->script,"renew_fail"))) return -1;
    return 0;
}
static int execute(void *v, const action_t *a, int vnc, int64_t deadline, loop_guard_t g, void *ctx)
{
    fake_t *f = v;
    if (cJSON_IsTrue(get(f->script,"act_estop"))) { loop_stop(f->loop); return -1; }
    if (delay(f,integer(f->script,"act_delay_ms",0),g,ctx)) return -1;
    if (f->real) return kbdc_execute(&f->kbd,a,vnc,deadline,g,ctx);
    if (g(ctx)) return -1;
    cJSON *j = cJSON_CreateObject(); cJSON_AddStringToObject(j,"action",action_name(a->kind));
    cJSON_AddStringToObject(j,"input",vnc ? "vnc" : "hid"); cJSON_AddItemToArray(f->acts,j);
    const cJSON *fault = cJSON_GetArrayItem(get(f->script,"hid_faults"),f->exec_index++);
    return cJSON_IsTrue(fault) ? -2 : 0;
}
static int confirm(void *v, unsigned cid, const char *summary, unsigned timeout)
{
    fake_t *f = v; f->cid = cid; f->confirm_at = now(f); f->confirm_sent = 0; f->confirms++;
    return f->real ? kbdc_confirm(&f->kbd,cid,summary,timeout) : 0;
}
static int ui(void *v, const char *state, int vnc, unsigned progress)
{
    fake_t *f = v;
    const cJSON *last = cJSON_GetArrayItem(f->states,cJSON_GetArraySize(f->states)-1);
    if (!cJSON_IsString(last) || strcmp(last->valuestring,state)) cJSON_AddItemToArray(f->states,cJSON_CreateString(state));
    return f->real ? kbdc_ui(&f->kbd,state,vnc,progress) : 0;
}
static void cancel(void *v) { ((fake_t *)v)->cancelled++; }
static void log_line(void *v, const char *line) { cJSON_AddItemToArray(((fake_t *)v)->logs,cJSON_CreateString(line)); }
int loop_test_cli(int argc, char **argv)
{
    if (argc != 3) { puts("{\"state\":\"error\",\"reason\":\"script\"}"); return 1; }
    int fd = open(argv[2],O_RDONLY | O_NONBLOCK); struct stat st;
    char b[65537]; size_t n = 0; int bad = fd < 0;
    if (!bad && (fstat(fd,&st) || !S_ISREG(st.st_mode) || st.st_size < 1 || st.st_size > 65536)) bad = 1;
    while (!bad && n < sizeof b) { ssize_t k = read(fd,b+n,sizeof b-n); if (k < 0 && errno == EINTR) continue; if (k < 0) { bad = 1; break; } if (!k) break; n += (size_t)k; }
    if (fd >= 0) close(fd);
    cJSON *script = bad ? NULL : agent_json_parse(b,n,65536);
    if (!cJSON_IsObject(script)) { cJSON_Delete(script); puts("{\"state\":\"error\",\"reason\":\"script\"}"); return 1; }
    agent_loop_t l; fake_t f = {.script=script,.loop=&l,.logs=cJSON_CreateArray(),.acts=cJSON_CreateArray(),.states=cJSON_CreateArray(),.kbd={.fd=-1}};
    f.tick_ms = (unsigned)integer(script,"tick_ms",50); if (!f.tick_ms) f.tick_ms = 1;
    const char *cm = string(script,"confirm","ok"); f.confirm_mode = !strcmp(cm,"reject") ? 1 : !strcmp(cm,"timeout") ? 2 : 0;
    const cJSON *path = get(script,"kbd_socket"), *sfd = get(script,"kbd_fd");
    if (path || sfd) {
        int rc = cJSON_IsString(path) ? kbdc_open(&f.kbd,path->valuestring) : cJSON_IsNumber(sfd) ? kbdc_connected(&f.kbd,dup(sfd->valueint)) : -1;
        if (rc) { cJSON_Delete(script); cJSON_Delete(f.logs); cJSON_Delete(f.acts); cJSON_Delete(f.states); puts("{\"state\":\"error\",\"reason\":\"kbd_connect\"}"); return 1; }
        f.real = 1;
        calib_pair_t p[] = {{0,0,0,0},{32767,0,1000,0},{0,32767,0,800},{32767,32767,1000,800},{16383.5,16383.5,500,400}};
        (void)calib_fit(p,1001,801,&f.kbd.calibration); /* Fake observer geometry only. */
    }
    policy_config_t policy; policy_defaults(&policy);
    const cJSON *cfg = get(script,"policy"); if (cfg) { char *s = cJSON_PrintUnformatted(cfg); policy_parse(s,strlen(s),&policy); cJSON_free(s); }
    const char *secret_values[32]; size_t ns = 0; const cJSON *secret;
    cJSON_ArrayForEach(secret,get(script,"secrets")) if (cJSON_IsString(secret) && ns < 32) secret_values[ns++] = secret->valuestring;
    loop_ports_t ports = {.ctx=&f,.now=now,.poll=poll_event,.observe=observe,.model=model,.lease=lease,.execute=execute,
        .confirm=confirm,.ui=ui,.cancel=cancel,.log=log_line,.secrets=secret_values,.secret_count=ns};
    loop_init(&l,&policy,&ports); int submitted = 0, next_submit = 0;
    const cJSON *resubmit = get(script,"resubmit"); cJSON *submissions = cJSON_CreateArray();
    unsigned limit = (unsigned)integer(script,"max_ticks",20000);
    for (f.ticks = 0; f.ticks < limit; f.ticks++) {
        loop_tick(&l);
        if (!submitted && l.state == S_IDLE) { if (!loop_submit(&l)) submitted = 1; }
        const cJSON *request = cJSON_GetArrayItem(resubmit,next_submit);
        if (request && f.ticks >= (unsigned)integer(request,"tick",0) &&
            !strcmp(string(request,"state",""),loop_state_name(l.state)) &&
            !strcmp(string(request,"reason",l.reason),l.reason)) {
            cJSON *attempt = cJSON_CreateObject(); cJSON_AddItemToArray(submissions,attempt);
            cJSON_AddStringToObject(attempt,"state",loop_state_name(l.state)); cJSON_AddStringToObject(attempt,"reason",l.reason);
            cJSON_AddBoolToObject(attempt,"allowed",l.allowed); cJSON_AddBoolToObject(attempt,"cancelled",l.cancelled);
            cJSON_AddBoolToObject(attempt,"accepted",loop_submit(&l) == 0); next_submit++;
        }
        if (next_submit >= cJSON_GetArraySize(resubmit) &&
            (l.state == S_DONE || l.state == S_STOPPED || l.state == S_ERROR || (!submitted && f.ticks >= 5))) break;
        if (f.real) { struct timespec t = {0,5000000}; nanosleep(&t,NULL); } else f.time += f.tick_ms;
    }
    if (f.ticks == limit) loop_stop(&l);
    cJSON *out = cJSON_CreateObject(); cJSON_AddStringToObject(out,"state",loop_state_name(l.state));
    if (cJSON_IsTrue(get(script,"probe_service"))) {
        cJSON *results = cJSON_AddArrayToObject(out,"service_results");
        for (int i = 0; i < 2; i++) cJSON_AddItemToArray(results,cJSON_CreateNumber(loop_service(&l)));
    }
    cJSON_AddStringToObject(out,"reason",l.reason); cJSON_AddStringToObject(out,"kbd_error",f.kbd.error); cJSON_AddNumberToObject(out,"steps",l.steps);
    cJSON_AddNumberToObject(out,"model_calls",l.model_calls); cJSON_AddNumberToObject(out,"confirms",f.confirms);
    cJSON_AddNumberToObject(out,"renews",f.renews); cJSON_AddNumberToObject(out,"allups",f.allups);
    cJSON_AddNumberToObject(out,"switches",f.switches); cJSON_AddNumberToObject(out,"cancelled",f.cancelled);
    cJSON_AddNumberToObject(out,"elapsed_ms",f.real ? now(&f)-l.start : f.time); cJSON_AddBoolToObject(out,"ask_user",l.ask_user);
    cJSON_AddItemToObject(out,"logs",f.logs); cJSON_AddItemToObject(out,"acts",f.acts); cJSON_AddItemToObject(out,"states",f.states);
    cJSON_AddItemToObject(out,"submissions",submissions);
    char *s = cJSON_PrintUnformatted(out); if (s) { puts(s); cJSON_free(s); }
    int success = l.state == S_DONE; kbdc_close(&f.kbd); cJSON_Delete(out); cJSON_Delete(script); return success ? 0 : 1;
}
