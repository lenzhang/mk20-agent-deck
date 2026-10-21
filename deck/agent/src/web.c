#ifdef __APPLE__
#define _DARWIN_C_SOURCE
#endif
#define _POSIX_C_SOURCE 200809L
#include "web_store.h"
#include "mem.h"
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <signal.h>
#include "dns.h"
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
/* Challenge end (paired, expired, exhausted, locked): forget the code and remove
 * the keyboard display file. */
static void pair_end(web_t *w) { w->pairing = 0; w->attempts = 0; mem_wipe(w->code,sizeof w->code); web_pair_clear(w); }
static void expire(web_t *w)
{
    int64_t t = now(w);
    if (w->pairing && expired(w->pair_at,t,WEB_PAIR_MS)) pair_end(w);
    for (int i = 0; i < WEB_SESSIONS; i++) {
        web_session_t *s = &w->sessions[i];
        int watching = 0;
        for (int k = 0; k < 2; k++) if (w->events[k].tag && !strcmp(w->events[k].owner,s->cookie)) watching = 1;
        if (watching && t >= s->activity) s->activity = t;
        if (*s->cookie && (expired(s->created,t,WEB_ABSOLUTE_MS) || expired(s->activity,t,WEB_IDLE_MS))) {
            if (w->mcp->task.live && !strcmp(w->mcp->task.owner,s->cookie)) (void)mcp_task_cancel(w->mcp,w->mcp->task.id);
            mem_wipe(s,sizeof *s);
        }
    }
}
void web_init(web_t *w, mcp_t *m, runtime_t *r, const char *host, const char *secrets, const char *config)
{
    memset(w,0,sizeof *w); w->mcp = m; w->runtime = r;
    snprintf(w->host,sizeof w->host,"%s",host); snprintf(w->origin,sizeof w->origin,"http://%s",w->host);
    snprintf(w->secrets,sizeof w->secrets,"%s",secrets); snprintf(w->config,sizeof w->config,"%s",config);
    /* Prefer validated persisted device configuration when it exists. */
    if (r) reload_configs(w);
}
static void probe_close(web_t *);
void web_free(web_t *w)
{
    probe_close(w); netcfg_free(&w->net); mem_wipe(w->sessions,sizeof w->sessions);
    pair_end(w);
}
void web_tick(void *v) { web_t *w = v; expire(w); if (w->agent) mcp_tick(w->mcp); }
/* Settings-only service with no live probe has no time-driven work: let the pump block. */
int web_idle(void *v) { web_t *w = v; return !w->agent && !w->probe_tag; }
void web_attach_pump(void *v, void (*p)(void *), void *ctx) { mcp_attach_pump(((web_t *)v)->mcp,p,ctx); }
void web_disconnect(void *v, uint64_t tag)
{
    web_t *w = v; if (w->probe_tag == tag) probe_close(w);
    for (int i = 0; i < 2; i++) if (w->events[i].tag == tag) memset(&w->events[i],0,sizeof w->events[i]);
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
/* Sliding-window rings of event times (0 = empty). Time going backwards counts. */
static size_t recent(const int64_t *ring, size_t n, int64_t t, int64_t *oldest)
{
    size_t c = 0; *oldest = t;
    for (size_t i = 0; i < n; i++) if (ring[i] && t-ring[i] < WEB_RATE_WINDOW_MS) { c++; if (ring[i] < *oldest) *oldest = ring[i]; }
    return c;
}
static void remember(int64_t *ring, size_t n, int64_t t)
{
    size_t slot = 0;
    for (size_t i = 1; i < n; i++) if (ring[i] < ring[slot]) slot = i;
    ring[slot] = t > 0 ? t : 1;
}
static void rate_limited(http_response_t *r, int64_t until, int64_t t)
{
    char b[96]; snprintf(b,sizeof b,"{\"error\":\"rate_limited\",\"retry_after_s\":%" PRId64 "}",(until-t+999)/1000 > 0 ? (until-t+999)/1000 : 1);
    response(r,429,b);
}
static int locked(web_t *w, int64_t t, http_response_t *r)
{
    if (!w->locked_until) return 0;
    if (t >= w->locked_until) { w->locked_until = 0; memset(w->fails,0,sizeof w->fails); return 0; }
    rate_limited(r,w->locked_until,t); return 1;
}
static void pair_started(web_t *w, http_response_t *r, int64_t t)
{
    char b[64]; int64_t left = WEB_PAIR_MS-(t-w->pair_at); if (left < 0) left = 0;
    snprintf(b,sizeof b,"{\"started\":true,\"expires_in_s\":%" PRId64 "}",left/1000); response(r,200,b);
}
/* The keyboard cannot show the code: never answer "started". */
static void display_unavailable(http_response_t *r)
{
    response(r,503,"{\"error\":\"display_unavailable\",\"message\":\"无法在键盘上显示配对码（写入配对码文件失败），请稍后重试\"}");
}
static int session_free(web_t *w)
{
    for (int i = 0; i < WEB_SESSIONS; i++) if (!*w->sessions[i].cookie) return 1;
    return 0;
}
static void sessions_full(http_response_t *r)
{
    char b[192]; snprintf(b,sizeof b,"{\"error\":\"sessions_full\",\"message\":\"已有 %d 个已配对的浏览器，请等待过期或在已配对页面退出\"}",WEB_SESSIONS);
    response(r,503,b);
}
static void pair_start(web_t *w, http_response_t *r)
{
    int64_t t = now(w), oldest; long long up;
    if (locked(w,t,r)) return;
    /* No free session slot: showing a code could not lead to a session. */
    if (!session_free(w)) { sessions_full(r); return; }
    /* A repeated start neither replaces a live challenge nor resets its attempt
     * count; it republishes the same code (e.g. after a keyboard restart). */
    if (w->pairing) {
        if (web_uptime(w,&up) || web_pair_publish(w,w->code,up+(WEB_PAIR_MS-(t-w->pair_at))/1000)) { display_unavailable(r); return; }
        pair_started(w,r,t); return;
    }
    if (recent(w->starts,WEB_PAIR_STARTS,t,&oldest) >= WEB_PAIR_STARTS) { rate_limited(r,oldest+WEB_RATE_WINDOW_MS,t); return; }
    mem_wipe(w->code,sizeof w->code);
    if (pairing_code(w->code) || web_uptime(w,&up) || web_pair_publish(w,w->code,up+WEB_PAIR_MS/1000)) {
        pair_end(w); display_unavailable(r); return;
    }
    remember(w->starts,WEB_PAIR_STARTS,t);
    w->pairing = 1; w->pair_at = t; w->attempts = 0;
    pair_started(w,r,t);
}
static void pair(web_t *w, const cJSON *j, http_response_t *r)
{
    int64_t t = now(w), oldest;
    if (locked(w,t,r)) return;
    const cJSON *code = get(j,"code"); const char *s = cJSON_IsString(code) ? code->valuestring : NULL;
    int good = equal(s,w->code,6) && web_only(j,"code") && w->pairing;
    if (!good) {
        /* Every failed /pair counts in the global ring, including one sent while no
         * challenge is live: probing cannot run unbounded. WEB_PAIR_FAILS failures in
         * WEB_RATE_WINDOW_MS lock /pair and /pair/start for one window (429). */
        remember(w->fails,WEB_PAIR_FAILS,t);
        if (w->pairing && ++w->attempts >= WEB_PAIR_ATTEMPTS) pair_end(w);
        if (recent(w->fails,WEB_PAIR_FAILS,t,&oldest) >= WEB_PAIR_FAILS) { w->locked_until = t+WEB_RATE_WINDOW_MS; pair_end(w); }
        response(r,401,"{\"error\":\"unauthorized\"}"); return;
    }
    web_session_t *slot = NULL;
    for (int i = 0; i < WEB_SESSIONS; i++) if (!*w->sessions[i].cookie) { slot = &w->sessions[i]; break; }
    /* Another browser took the last slot after this code was shown: the code is void. */
    if (!slot) { pair_end(w); sessions_full(r); return; }
    char cookie[33], csrf[33];
    if (httpd_random_hex(cookie,16) || httpd_random_hex(csrf,16)) { response(r,500,"{}"); return; }
    strcpy(slot->cookie,cookie); strcpy(slot->csrf,csrf); slot->created = slot->activity = now(w);
    pair_end(w);
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
        /* agent_json_parse rejects decoded U+0000 before cJSON allocation;
         * unmodified cJSON has no decoded-length member to compare to strlen. */
        if (!web_only(j,"value") || !cJSON_IsString(value) || !(n = strlen(value->valuestring)) || n > 4096 || strpbrk(value->valuestring,"\r\n")) { response(r,400,"{}"); return; }
    }
    int dir = web_directory(w->secrets,write);
    if (dir < 0) {
        if (!write && errno == ENOENT) response(r,200,remove ? "{}" : "{\"set\":false}");
        else response(r,500,"{\"error\":\"storage\"}");
        return;
    }
    /* Write-only: the response never echoes the value or its length. */
    if (write) {
        if (web_atomic(dir,file,value->valuestring,n)) response(r,500,"{\"error\":\"storage\"}");
        else response(r,200,"{\"set\":true}");
    }
    else if (remove) response(r,web_remove(dir,file) ? 500 : 200,"{}");
    else {
        struct stat st; int rc = fstatat(dir,file,&st,AT_SYMLINK_NOFOLLOW);
        if (!rc && (!S_ISREG(st.st_mode) || st.st_uid != geteuid() || (st.st_mode & 07777) != 0600)) response(r,500,"{\"error\":\"storage\"}");
        else if (rc && errno != ENOENT) response(r,500,"{\"error\":\"storage\"}");
        else response(r,200,!rc && st.st_size > 0 ? "{\"set\":true}" : "{\"set\":false}");
    }
    close(dir);
}
static void commit(web_t *w, const char *name, const char *data, size_t len, http_response_t *r)
{
    if (w->mcp->task.live || w->mcp->pending) { response(r,409,"{\"error\":\"busy\"}"); return; }
    if (web_config_validate(w,name,data,len)) { response(r,400,"{\"error\":\"config\"}"); return; }
    int rc = web_config_commit(w,name,data,len);
    if (!rc && w->runtime) reload_configs(w);
    if (rc == -2) response(r,409,"{\"error\":\"busy\"}");
    else response(r,rc ? 500 : 200,rc ? "{\"error\":\"storage\"}" : "{\"saved\":true}");
}
static void config(web_t *w, const char *name, const http_request_t *q, http_response_t *r)
{
    if (strcmp(name,"net") && strcmp(name,"provider")) { response(r,404,"{}"); return; }
    if (!strcmp(q->method,"GET")) {
        r->body = web_read_config(w,name,&r->len);
        if (r->body) r->status = 200;
        else response(r,errno == EBUSY ? 409 : 404,errno == EBUSY ? "{\"error\":\"apply_incomplete\"}" : "{}");
        return;
    }
    if (strcmp(q->method,"PUT")) { response(r,405,"{}"); return; }
    commit(w,name,q->body,q->body_len,r);
}
/* Page form: only catalogue ids; URL, route and key path are derived here. */
static void provider_choice(web_t *w, const cJSON *j, http_response_t *r)
{
    const cJSON *id = get(j,"provider"), *model = get(j,"model");
    if (!web_only(j,"provider,model") || !cJSON_IsString(id) || !cJSON_IsString(model)) { response(r,400,"{\"error\":\"config\"}"); return; }
    const web_provider_t *p = web_provider(id->valuestring);
    if (!p || !web_model(p,model->valuestring)) { response(r,400,"{\"error\":\"config\"}"); return; }
    char key[4200]; snprintf(key,sizeof key,"%s/%s.key",w->secrets,p->id);
    cJSON *o = cJSON_CreateObject();
    cJSON_AddNumberToObject(o,"version",1); cJSON_AddStringToObject(o,"provider",p->id); cJSON_AddStringToObject(o,"model",model->valuestring);
    cJSON_AddStringToObject(o,"base_url",p->base_url); cJSON_AddBoolToObject(o,"domestic",p->domestic);
    cJSON_AddStringToObject(o,"route",p->domestic ? "domestic" : "foreign"); cJSON_AddStringToObject(o,"api_key_file",key);
    char *s = cJSON_PrintUnformatted(o); cJSON_Delete(o);
    if (!s) { response(r,500,"{}"); return; }
    commit(w,"provider",s,strlen(s),r); free(s);
}
static void maint(web_t *w, const char *hook, const http_request_t *q, const cJSON *j, http_response_t *r)
{
    if (strcmp(q->method,"POST")) { response(r,405,"{}"); return; }
    const cJSON *confirm = get(j,"confirm"), *verbose = get(j,"verbose"); const char *body = ""; int ok;
    /* Each action carries an explicit confirmation naming itself (page asks first). */
    ok = cJSON_IsString(confirm) && !strcmp(confirm->valuestring,hook);
    if (!strcmp(hook,"debug")) { ok = ok && web_only(j,"confirm,verbose") && cJSON_IsBool(verbose); body = cJSON_IsTrue(verbose) ? "verbose=1\n" : "verbose=0\n"; }
    else if (!strcmp(hook,"restart-kbd") || !strcmp(hook,"exit-safe-mode")) ok = ok && web_only(j,"confirm");
    else { response(r,404,"{}"); return; }
    if (!ok) { response(r,400,"{\"error\":\"confirm\"}"); return; }
    int rc = web_maint_request(w,hook,body,strlen(body));
    if (rc == -2) response(r,503,"{\"error\":\"maint_unavailable\"}");
    else response(r,rc ? 500 : 202,rc ? "{\"error\":\"storage\"}" : "{\"queued\":true}");
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
#ifdef MK20_WEB_TEST
/* Reserve a synthetic model stream in the parent before fork so subsequent
 * task requests consume the next fixture, exactly as production sockets do. */
static int probe_test_fd = -1;
static int probe_test_socket(int domain, int type, int protocol)
{
    (void)domain; (void)type; (void)protocol; return dup(probe_test_fd);
}
#endif
static void probe_run(web_t *w, http_response_t *r)
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
            options.config = &n; options.provider_route = p.route; options.timeout_ms = 1500; options.body_cap = 16384;
#ifdef MK20_WEB_TEST
            if (probe_test_fd >= 0) options.socket_fn = probe_test_socket;
#endif
            options.service = NULL; options.service_ctx = NULL; options.cancelled = NULL;
            net_response_t out = {0}; (void)net_request(&options,&out);
            ok = out.http_status > 0; error = ok ? "none" : out.class_name ? out.class_name : "network"; net_response_free(&out);
        }
    }
    cJSON *out = cJSON_CreateObject(); cJSON_AddBoolToObject(out,"ok",ok); cJSON_AddNumberToObject(out,"latency_ms",(double)(now(w)-start)); cJSON_AddStringToObject(out,"error_class",ok ? "none" : error);
    netcfg_free(&n); json_response(r,200,out);
}
/* One bounded probe child. Parent never waits for network or a full pipe;
 * disconnect/shutdown/deadline always kill and reap it. Child emits JSON only. */
static void probe_close(web_t *w)
{
    if (!w->probe_tag) return;
    close(w->probe_fd); kill(w->probe_pid,SIGKILL);
    while (waitpid(w->probe_pid,NULL,0) < 0 && errno == EINTR) {}
    w->probe_tag = 0; w->probe_used = 0;
}
static int connection_test(web_t *w, const http_request_t *q, http_response_t *r)
{
    if (w->probe_tag && w->probe_tag != q->tag) { response(r,409,"{\"error\":\"busy\"}"); return 0; }
    if (!w->probe_tag) {
        if (w->mcp->task.live || w->mcp->pending) { response(r,409,"{\"error\":\"busy\"}"); return 0; }
        int fds[2]; if (pipe(fds)) { response(r,500,"{}"); return 0; }
        if (fcntl(fds[0],F_SETFL,O_NONBLOCK)) { close(fds[0]); close(fds[1]); response(r,500,"{}"); return 0; }
        int keep = -1;
#ifdef MK20_WEB_TEST
        if (!w->connection_test && w->runtime && w->runtime->transport.socket_fn) keep = w->runtime->transport.socket_fn(AF_INET,SOCK_STREAM,0);
#endif
        pid_t pid = fork();
        if (pid < 0) { if (keep >= 0) close(keep); close(fds[0]); close(fds[1]); response(r,500,"{}"); return 0; }
        if (!pid) {
            close(fds[0]);
            /* Do not retain listener/client descriptors while probing. */
            long maxfd = sysconf(_SC_OPEN_MAX); if (maxfd < 0) maxfd = 1024;
            for (int fd = 3; fd < maxfd; fd++) if (fd != fds[1] && fd != keep) close(fd);
#ifdef MK20_WEB_TEST
            probe_test_fd = keep;
#endif
            http_response_t out = {0}; probe_run(w,&out);
            if (out.body && out.len < sizeof w->probe_data) {
                size_t sent = 0;
                while (sent < out.len) { ssize_t k = write(fds[1],out.body+sent,out.len-sent); if (k < 0 && errno == EINTR) continue; if (k <= 0) break; sent += (size_t)k; }
            }
            free(out.body); close(fds[1]); _exit(0);
        }
        if (keep >= 0) close(keep);
        close(fds[1]); w->probe_fd = fds[0]; w->probe_pid = pid; w->probe_tag = q->tag;
        w->probe_deadline = dns_now_ms()+1500; w->probe_used = 0; return 1;
    }
    ssize_t n = read(w->probe_fd,w->probe_data+w->probe_used,sizeof w->probe_data-1-w->probe_used);
    if (n > 0) w->probe_used += (size_t)n;
    if (!n) {
        w->probe_data[w->probe_used] = 0;
        cJSON *j = agent_json_parse(w->probe_data,w->probe_used,sizeof w->probe_data);
        if (j) json_response(r,200,j); else response(r,500,"{}");
        probe_close(w); return 0;
    }
    if (dns_now_ms() >= w->probe_deadline) {
        probe_close(w); response(r,200,"{\"ok\":false,\"latency_ms\":1500,\"error_class\":\"timeout\"}"); return 0;
    }
    if ((n < 0 && errno != EINTR && errno != EAGAIN && errno != EWOULDBLOCK) || w->probe_used == sizeof w->probe_data-1) {
        probe_close(w); response(r,500,"{}"); return 0;
    }
    return 1;
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
        if (!strcmp(path,"/stop")) { loop_stop(m->loop); if (m->task.live) (void)mcp_task_cancel(m,m->task.id); json_response(r,200,task_status(w)); return; }
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
/* Frees this browser's slot (WEB_SESSIONS in total); its own task stops as on expiry. */
static void logout(web_t *w, web_session_t *s, http_response_t *r)
{
    if (w->mcp->task.live && !strcmp(w->mcp->task.owner,s->cookie)) (void)mcp_task_cancel(w->mcp,w->mcp->task.id);
    mem_wipe(s,sizeof *s);
    snprintf(r->cookie,sizeof r->cookie,"mk20=; Max-Age=0; HttpOnly; SameSite=Strict; Path=/");
    response(r,200,"{\"logged_out\":true}");
}
static void session_info(web_t *w, web_session_t *s, http_response_t *r)
{
    /* Same-origin only (Host + SameSite cookie); lets a new tab recover its CSRF token. */
    cJSON *o = cJSON_CreateObject(); cJSON_AddStringToObject(o,"csrf",s->csrf);
    cJSON_AddBoolToObject(o,"agent",w->agent); cJSON_AddStringToObject(o,"web_url",w->origin);
    json_response(r,200,o);
}
static int handle(void *v, const http_request_t *q, http_response_t *r)
{
    web_t *w = v; expire(w);
    /* DNS rebinding: only the exact numeric authority the service is bound to,
     * checked before any route, page or session work. */
    if (strcmp(q->host,w->host) || (q->has_origin && strcmp(q->origin,w->origin))) { response(r,403,"{\"error\":\"forbidden\"}"); return 0; }
    const char *path = q->path; int read = !strcmp(q->method,"GET");
    int public_page = read && (!strcmp(path,"/") || !strcmp(path,"/chat"));
    int start = !strcmp(q->method,"POST") && !strcmp(path,"/pair/start"), pairing = !strcmp(q->method,"POST") && !strcmp(path,"/pair");
    web_session_t *s = session(w,q);
    if (!public_page && !start && !pairing && !s) {
        for (int i = 0; i < 2; i++) if (w->events[i].tag == q->tag) return 3;
        if (w->probe_tag == q->tag) probe_close(w);
        response(r,401,"{\"error\":\"unauthorized\"}"); return 0;
    }
    /* CSRF: every state change needs a same-origin request; paired ones also the session token. */
    if (!read && (!q->has_origin && (!q->has_fetch_site || strcmp(q->fetch_site,"same-origin")))) { response(r,403,"{\"error\":\"forbidden\"}"); return 0; }
    if (!read && !start && !pairing && (!q->has_csrf || !s || !equal(q->csrf,s->csrf,32))) { response(r,403,"{\"error\":\"forbidden\"}"); return 0; }
    if (s && strcmp(path,"/api/events")) s->activity = now(w);
    if (public_page) {
        r->body = web_page(!strcmp(path,"/chat"),&r->len);
        if (!r->body) { response(r,500,"{}"); return 0; }
        r->status = 200; r->content_type = "text/html; charset=utf-8"; r->csp = web_csp();
        return 0;
    }
    if (start) { pair_start(w,r); return 0; }
    cJSON *j = NULL;
    if (!read && q->body_len) {
        if (strcasecmp(q->content_type,"application/json") && strcasecmp(q->content_type,"application/json; charset=utf-8")) { response(r,415,"{}"); return 0; }
        j = agent_json_parse(q->body,q->body_len,HTTPD_BODY);
        if (!j) { response(r,400,"{}"); return 0; }
    }
    int rc = 0, task_api = !strncmp(path,"/api/tasks",10) && (!path[10] || path[10] == '/');
    if (pairing) pair(w,j,r);
    else if (!strcmp(path,"/api/session") && read) session_info(w,s,r);
    else if (!strcmp(path,"/api/logout") && !strcmp(q->method,"POST")) logout(w,s,r);
    else if (!strcmp(path,"/api/catalogue") && read) json_response(r,200,web_catalogue(w));
    else if (!strcmp(path,"/api/status") && read) json_response(r,200,web_status(w));
    else if (!strncmp(path,"/api/maint/",11)) maint(w,path+11,q,j,r);
    else if (!strcmp(path,"/api/provider") && !strcmp(q->method,"PUT")) provider_choice(w,j,r);
    else if (!strncmp(path,"/secret/",8)) secret(w,path+8,q,j,r);
    else if (!strncmp(path,"/config/",8)) config(w,path+8,q,r);
    else if (!strcmp(path,"/test") && !strcmp(q->method,"POST")) rc = connection_test(w,q,r);
    else if ((task_api || !strcmp(path,"/api/events")) && !w->agent) response(r,404,"{\"error\":\"agent_disabled\"}");
    else if (task_api) tasks(w,q,j,s,r);
    else if (!strcmp(path,"/api/events") && read) rc = events(w,q,s,r);
    else response(r,404,"{}");
    /* Secret input strings are wiped before freeing the JSON allocation. */
    if (!strncmp(path,"/secret/",8)) { const cJSON *value = get(j,"value"); if (cJSON_IsString(value)) mem_wipe(value->valuestring,strlen(value->valuestring)); }
    cJSON_Delete(j); return rc;
}

int web_handle(void *v, const http_request_t *q, http_response_t *r)
{
    int rc = handle(v,q,r);
    if (!strncmp(q->path,"/secret/",8) && q->body && q->body_len) mem_wipe((void *)q->body,q->body_len);
    return rc;
}
