#define _POSIX_C_SOURCE 200809L
#include "kbdc.h"
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>
#ifndef MSG_NOSIGNAL
#define MSG_NOSIGNAL 0
#endif
int64_t kbdc_now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t); return (int64_t)t.tv_sec * 1000 + t.tv_nsec / 1000000; }
static const cJSON *get(const cJSON *j, const char *k) { return cJSON_GetObjectItemCaseSensitive(j,k); }
static int truth(const cJSON *j) { return cJSON_IsTrue(j) || (cJSON_IsNumber(j) && j->valuedouble == 1); }
static int istext(const cJSON *j, const char *s) { return cJSON_IsString(j) && !strcmp(j->valuestring,s); }
static int enqueue(kbdc_t *c, kbd_event_t e)
{
    if (c->count == 64) { c->stopped = 1; return -1; }
    c->events[(c->head + c->count++) % 64] = e; return 0;
}
static int event(kbdc_t *c, const cJSON *j)
{
    const cJSON *ev = get(j,"ev"); kbd_event_t e = {0};
    if (istext(ev,"estop")) { c->stopped = 1; c->allowed = c->notified_allowed = 0; c->token = 0; e.kind = K_ESTOP; }
    else if (istext(ev,"lease_revoked")) {
        if ((c->releasing || !c->token) && istext(get(j,"reason"),"released")) return 0;
        c->stopped = 1; c->token = 0; e.kind = K_LEASE_LOST;
    } else if (istext(ev,"confirm.result")) {
        const cJSON *cid = get(j,"cid"), *ok = get(j,"ok");
        if (!cJSON_IsNumber(cid) || cid->valuedouble < 0 || cid->valuedouble > 999999999 || cid->valuedouble != cid->valueint || !cJSON_IsBool(ok)) return -1;
        e.kind = K_CONFIRM; e.cid = (unsigned)cid->valueint; e.ok = cJSON_IsTrue(ok);
    } else if (istext(ev,"hid.health")) { e.kind = K_HID_HEALTH; e.ok = truth(get(j,"ok")); }
    else return 0;
    return enqueue(c,e);
}
/* One byte at a time preserves the 1024-byte framing across interleaved events.
 * Deadline covers split writes and slowloris, and recv is always nonblocking. */
static int line(kbdc_t *c, cJSON **out, int64_t deadline, int wait)
{
    *out = NULL;
    for (;;) {
        char ch; ssize_t n = recv(c->fd,&ch,1,0);
        if (n == 1) {
            if (ch == '\n') {
                *out = agent_json_parse(c->in,c->used,1023); c->used = 0;
                return cJSON_IsObject(*out) ? 1 : -1;
            }
            if (c->used >= 1023) return -1;
            c->in[c->used++] = ch; continue;
        }
        if (!n) return -1;
        if (errno == EINTR) continue;
        if (errno != EAGAIN && errno != EWOULDBLOCK) return -1;
        if (!wait) return 0;
        int64_t left = deadline - kbdc_now(); if (left <= 0) return -1;
        struct pollfd p = {c->fd,POLLIN,0}; int rc = poll(&p,1,left > 25 ? 25 : (int)left);
        if (rc < 0 && errno != EINTR) return -1;
    }
}
static int rpc(kbdc_t *c, cJSON *q, cJSON **reply)
{
    *reply = NULL; int rc = -1; char *wire = NULL;
    const cJSON *op = get(q,"op");
    if (c->fd < 0 || (c->stopped && !istext(op,"status") && !istext(op,"estop"))) goto end;
    /* Consume queued stop events BEFORE writing anything. */
    unsigned drained = 0;
    for (;;) {
        if (++drained > 64) goto end;
        cJSON *j = NULL; int n = line(c,&j,0,0);
        if (n <= 0) { cJSON_Delete(j); if (n < 0) goto end; break; }
        if (!get(j,"ev") || event(c,j)) { cJSON_Delete(j); goto end; } cJSON_Delete(j);
    }
    if (c->stopped && !istext(op,"status") && !istext(op,"estop")) goto end;
    if (++c->id > 999999999) goto end;
    cJSON_AddNumberToObject(q,"id",c->id); wire = cJSON_PrintUnformatted(q);
    if (!wire || strlen(wire) + 1 > 1024) goto end;
    char b[1024]; size_t n = strlen(wire); memcpy(b,wire,n); b[n++] = '\n'; size_t off = 0;
    int64_t deadline = kbdc_now() + 250;
    if (istext(op,"act") && c->action_deadline && c->action_deadline < deadline) deadline = c->action_deadline;
    while (off < n) {
        if (kbdc_now() >= deadline) goto end;
        ssize_t k = send(c->fd,b + off,n - off,MSG_NOSIGNAL);
        if (k > 0) off += (size_t)k;
        else if (k < 0 && errno == EINTR) continue;
        else if (k < 0 && (errno == EAGAIN || errno == EWOULDBLOCK) && kbdc_now() < deadline) {
            struct pollfd p = {c->fd,POLLOUT,0}; if (poll(&p,1,5) < 0 && errno != EINTR) goto end;
        } else goto end;
    }
    while (kbdc_now() < deadline) {
        cJSON *j = NULL; int k = line(c,&j,deadline,1);
        if (k < 1) { cJSON_Delete(j); goto end; }
        if (get(j,"ev")) { int bad = event(c,j); cJSON_Delete(j); if (bad) goto end; continue; }
        const cJSON *id = get(j,"id");
        if (!cJSON_IsNumber(id) || id->valuedouble != c->id || !cJSON_IsBool(get(j,"ok"))) { cJSON_Delete(j); goto end; }
        if (!cJSON_IsTrue(get(j,"ok"))) {
            const cJSON *err = get(j,"err");
            if (istext(err,"unsupported")) strcpy(c->error,"unsupported"); else strcpy(c->error,"kbd_rejected");
            cJSON_Delete(j); rc = -2; goto end;
        }
        *reply = j; rc = 0; goto end;
    }
end:
    cJSON_free(wire); cJSON_Delete(q);
    if (rc == -1) {
        c->stopped = 1; strcpy(c->error,"kbd_io");
        /* A broken control stream cannot keep partially pressed keys leased. */
        if (c->fd >= 0) { (void)shutdown(c->fd,SHUT_RDWR); close(c->fd); c->fd = -1; }
        c->token = 0;
    }
    return rc;
}
static cJSON *request(const char *op) { cJSON *q = cJSON_CreateObject(); cJSON_AddStringToObject(q,"op",op); return q; }
int kbdc_connected(kbdc_t *c, int fd)
{
    memset(c,0,sizeof *c); c->fd = fd; c->ttl = 3000;
    int flags = fcntl(fd,F_GETFL,0);
    if (flags < 0 || fcntl(fd,F_SETFL,flags | O_NONBLOCK)) { close(fd); c->fd = -1; return -1; }
#ifdef SO_NOSIGPIPE
    int one = 1; if (setsockopt(fd,SOL_SOCKET,SO_NOSIGPIPE,&one,sizeof one)) { close(fd); c->fd = -1; return -1; }
#endif
    return 0;
}
int kbdc_open(kbdc_t *c, const char *path)
{
    struct sockaddr_un a = {.sun_family = AF_UNIX}; memset(c,0,sizeof *c); c->fd = -1;
    if (!path || strlen(path) >= sizeof a.sun_path) return -1;
    strcpy(a.sun_path,path); int fd = socket(AF_UNIX,SOCK_STREAM,0); if (fd < 0) return -1;
    /* Unix connect is local, nonblocking and bounded. */
    if (kbdc_connected(c,fd)) return -1;
    int rc = connect(fd,(struct sockaddr *)&a,sizeof a);
    if (rc && errno == EINPROGRESS) { struct pollfd p = {fd,POLLOUT,0}; int err = 0; socklen_t n = sizeof err; rc = poll(&p,1,250) > 0 && !getsockopt(fd,SOL_SOCKET,SO_ERROR,&err,&n) && !err ? 0 : -1; }
    if (rc) { kbdc_close(c); return -1; } return 0;
}
void kbdc_close(kbdc_t *c) { if (c->fd >= 0) close(c->fd); c->fd = -1; c->token = 0; }
static int poll_inner(kbdc_t *c, kbd_event_t *out)
{
    if (!c->count) {
        unsigned drained = 0;
        for (;;) {
            if (++drained > 64) return -1;
            cJSON *j = NULL; int n = line(c,&j,0,0);
            if (n <= 0) { cJSON_Delete(j); if (n < 0) return -1; break; }
            int bad = !get(j,"ev") || event(c,j); cJSON_Delete(j); if (bad) return -1;
        }
    }
    if (!c->count && kbdc_now() >= c->next_status) {
        cJSON *r; int rc = rpc(c,request("status"),&r); if (rc) return -1;
        const cJSON *version = get(r,"v"), *allowed = get(r,"allowed"), *estopped = get(r,"estopped");
        int valid_allowed = cJSON_IsBool(allowed) || (cJSON_IsNumber(allowed) && (allowed->valuedouble == 0 || allowed->valuedouble == 1));
        int valid_stopped = cJSON_IsBool(estopped) || (cJSON_IsNumber(estopped) && (estopped->valuedouble == 0 || estopped->valuedouble == 1));
        if (!cJSON_IsNumber(version) || version->valuedouble != 1 || !valid_allowed || !valid_stopped) { cJSON_Delete(r); return -1; }
        c->allowed = truth(allowed); int stopped = truth(estopped);
        /* Only keyboard-owned status can originate K_ALLOW. */
        if (!c->allowed) c->require_off = 0;
        if (c->allowed && !stopped && !c->notified_allowed && !c->require_off) { enqueue(c,(kbd_event_t){.kind=K_ALLOW}); c->notified_allowed = 1; c->stopped = 0; }
        if ((!c->allowed || stopped) && c->notified_allowed) { enqueue(c,(kbd_event_t){.kind=K_ESTOP}); c->notified_allowed = 0; c->stopped = 1; }
        c->vnc_capable = cJSON_IsTrue(get(r,"input_lease_vnc"));
        cJSON_Delete(r); c->next_status = kbdc_now() + 250;
    }
    if (!c->count) return 0;
    *out = c->events[c->head]; c->head = (c->head + 1) % 64; c->count--; return 1;
}
int kbdc_poll(kbdc_t *c, kbd_event_t *out)
{
    int rc = poll_inner(c,out);
    if (rc < 0) {
        c->stopped = 1;
        if (c->fd >= 0) { (void)shutdown(c->fd,SHUT_RDWR); close(c->fd); c->fd = -1; }
        c->token = 0;
    }
    return rc;
}
static int command(kbdc_t *c, cJSON *q) { cJSON *r; int rc = rpc(c,q,&r); cJSON_Delete(r); return rc; }
static int act(kbdc_t *c, const char *kind, int a, int b, int d)
{
    if (!c->token || c->stopped || kbdc_now() >= c->expiry) return -1;
    cJSON *q = request("act"); cJSON_AddNumberToObject(q,"token",c->token);
    cJSON_AddNumberToObject(q,"seq",++c->seq); cJSON_AddStringToObject(q,"kind",kind);
    if (!strcmp(kind,"key")) { cJSON_AddNumberToObject(q,"usage",a); cJSON_AddNumberToObject(q,"down",b); }
    if (!strcmp(kind,"ptr")) { cJSON_AddNumberToObject(q,"buttons",a); cJSON_AddNumberToObject(q,"x",b); cJSON_AddNumberToObject(q,"y",d); }
    if (!strcmp(kind,"scroll")) { cJSON_AddNumberToObject(q,"wheel",a); cJSON_AddNumberToObject(q,"pan",b); }
    return command(c,q);
}
int kbdc_lease(kbdc_t *c, lease_op_t op)
{
    if (op == LEASE_ESTOP) {
        c->action_deadline = 0; int rc = command(c,request("estop"));
        c->stopped = 1; c->notified_allowed = 0; c->require_off = rc != 0; c->token = 0; return rc;
    }
    if (op == LEASE_VNC && !c->vnc_capable) { strcpy(c->error,"unsupported"); return -1; }
    if (op == LEASE_ALLUP) { if (!c->token && kbdc_lease(c,LEASE_HID)) return -1; return act(c,"allup",0,0,0); }
    if ((op == LEASE_HID || op == LEASE_VNC) && c->token && kbdc_lease(c,LEASE_RELEASE)) return -1;
    if (op == LEASE_RELEASE && !c->token) return 0;
    cJSON *q = request(op == LEASE_RENEW ? "lease.renew" : op == LEASE_RELEASE ? "lease.release" : "lease.acquire");
    if (op == LEASE_RELEASE || op == LEASE_RENEW) cJSON_AddNumberToObject(q,"token",c->token);
    else { cJSON_AddStringToObject(q,"owner","agent"); if (op == LEASE_VNC) cJSON_AddStringToObject(q,"input","vnc"); }
    if (op != LEASE_RELEASE) cJSON_AddNumberToObject(q,"ttl_ms",3000);
    c->releasing = op == LEASE_RELEASE; int64_t sent = kbdc_now(); cJSON *r; int rc = rpc(c,q,&r); c->releasing = 0;
    if (!rc && op == LEASE_RELEASE) c->token = 0;
    else if (!rc) {
        const cJSON *ttl = get(r,"ttl_ms"), *token = get(r,"token");
        if (!cJSON_IsNumber(ttl) || ttl->valuedouble != ttl->valueint || ttl->valueint < 200 || ttl->valueint > 10000 ||
            (op != LEASE_RENEW && (!cJSON_IsNumber(token) || token->valuedouble != token->valueint || token->valueint < 1 || token->valueint > 999999999)) ||
            (op == LEASE_VNC && !istext(get(r,"input"),"vnc"))) rc = -1;
        else { if (op != LEASE_RENEW) { c->token = (unsigned)token->valueint; c->seq = 0; } c->ttl = (unsigned)ttl->valueint; c->expiry = sent + c->ttl; }
    }
    cJSON_Delete(r); if (rc) c->stopped = 1; return rc;
}
int kbdc_confirm(kbdc_t *c, unsigned cid, const char *summary, unsigned timeout)
{
    cJSON *q = request("confirm.request"); cJSON_AddNumberToObject(q,"cid",cid);
    cJSON_AddStringToObject(q,"summary",summary); cJSON_AddStringToObject(q,"risk","high"); cJSON_AddNumberToObject(q,"timeout_ms",timeout); return command(c,q);
}
int kbdc_ui(kbdc_t *c, const char *state, int vnc, unsigned progress)
{
    cJSON *q = request("ui.agent"); cJSON_AddStringToObject(q,"state",state); cJSON_AddStringToObject(q,"input",vnc ? "vnc" : "hid");
    cJSON_AddStringToObject(q,"summary",""); cJSON_AddNumberToObject(q,"progress",progress); return command(c,q);
}
static int guard_action(kbdc_t *c, int64_t deadline, loop_guard_t guard, void *ctx)
{
    if ((guard && guard(ctx)) || c->stopped || kbdc_now() >= deadline) return -1;
    if (c->token && kbdc_now() >= c->expiry - (int64_t)c->ttl * 2 / 3 && kbdc_lease(c,LEASE_RENEW)) return -1;
    return 0;
}
static int pause_ms(kbdc_t *c, unsigned ms, int64_t deadline, loop_guard_t g, void *ctx)
{
    int64_t end = kbdc_now() + ms;
    do { if (guard_action(c,deadline,g,ctx)) return -1; if (kbdc_now() >= end) return 0; struct timespec t = {0,5000000}; nanosleep(&t,NULL); } while (1);
}
static int chord(kbdc_t *c, unsigned mods, unsigned usage, int64_t d, loop_guard_t g, void *v)
{
    for (unsigned i = 0; i < 4; i++) if (mods & (1u << i)) { if (guard_action(c,d,g,v) || act(c,"key",224 + (int)i,1,0)) return -1; }
    if (guard_action(c,d,g,v) || act(c,"key",(int)usage,1,0) || pause_ms(c,8,d,g,v) || guard_action(c,d,g,v)) return -1;
    return act(c,"allup",0,0,0);
}
static int ascii_key(unsigned ch, unsigned *m, unsigned *u)
{
    *m = 0; *u = 0;
    if (ch >= 'a' && ch <= 'z') *u = ch - 'a' + 4;
    else if (ch >= 'A' && ch <= 'Z') { *u = ch - 'A' + 4; *m = 2; }
    else if (ch >= '1' && ch <= '9') *u = ch - '1' + 30;
    else if (ch == '0') *u = 39;
    else if (ch == '\n' || ch == '\r') *u = 40;
    else if (ch == '\t') *u = 43;
    else if (ch == ' ') *u = 44;
    else {
        const char *plain = "-=[]\\;'`,./", *shift = "_+{}|:\"~<>?", *digits = "!@#$%^&*()";
        const unsigned codes[] = {45,46,47,48,49,51,52,53,54,55,56};
        const char *p = strchr(plain,(int)ch), *s = strchr(shift,(int)ch), *n = strchr(digits,(int)ch);
        if (p) *u = codes[p - plain]; else if (s) { *u = codes[s - shift]; *m = 2; }
        else if (n) { *u = n - digits == 9 ? 39 : 30 + (unsigned)(n - digits); *m = 2; }
    }
    return *u ? 0 : -1;
}
static int execute_inner(kbdc_t *c, const action_t *a, int vnc, int64_t d, loop_guard_t g, void *v)
{
    c->action_deadline = d;
    if (vnc || a->kind == A_PASTE) return -3; /* No RFB input writer yet. */
    if (guard_action(c,d,g,v)) return -1;
    if (a->kind == A_WAIT) return pause_ms(c,(unsigned)a->ms,d,g,v);
    if (a->kind == A_KEY) return chord(c,a->modifiers,a->usage,d,g,v);
    if (a->kind == A_TYPE) {
        for (const unsigned char *p = (const unsigned char *)a->text; *p; p++) { unsigned m,u; if (ascii_key(*p,&m,&u) || chord(c,m,u,d,g,v)) return -1; } return 0;
    }
    int x,y,ex,ey;
    if (calib_map(&c->calibration,a->x1,a->y1,&x,&y)) return -1;
    if (a->kind == A_SCROLL) { if (act(c,"ptr",0,x,y) || guard_action(c,d,g,v)) return -1; return act(c,"scroll",a->dy,a->dx,0); }
    if (a->kind == A_DRAG && calib_map(&c->calibration,a->x2,a->y2,&ex,&ey)) return -1;
    if (act(c,"ptr",0,x,y)) return -1;
    if (a->kind == A_MOVE) return 0;
    if (guard_action(c,d,g,v) || act(c,"ptr",1,x,y)) return -1;
    if (a->kind == A_DRAG) {
        for (int i = 1; i <= 20; i++) { if (pause_ms(c,10,d,g,v) || act(c,"ptr",1,x + (ex-x)*i/20,y + (ey-y)*i/20)) return -1; }
        x = ex; y = ey;
    } else if (pause_ms(c,50,d,g,v)) return -1;
    if (guard_action(c,d,g,v) || act(c,"ptr",0,x,y)) return -1;
    if (a->kind == A_DOUBLE_CLICK) {
        if (pause_ms(c,80,d,g,v) || act(c,"ptr",1,x,y) || pause_ms(c,50,d,g,v) || act(c,"ptr",0,x,y)) return -1;
    }
    return 0;
}

int kbdc_execute(kbdc_t *c, const action_t *a, int vnc, int64_t d, loop_guard_t g, void *v)
{
    c->error[0] = 0;
    int rc = execute_inner(c,a,vnc,d,g,v); c->action_deadline = 0;
    if (rc && rc != -3) return !strcmp(c->error,"unsupported") ? -3 : -1;
    return rc;
}
