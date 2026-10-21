#ifdef __APPLE__
#define _DARWIN_C_SOURCE
#endif
#define _POSIX_C_SOURCE 200809L
#define _XOPEN_SOURCE 700
#include "web_store.h"
#include <assert.h>
#include <errno.h>
#include <dirent.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include <poll.h>
#include <sys/wait.h>
#include "dns.h"
static unsigned checks;
#define CHECK(x) do { checks++; assert(x); } while (0)
typedef struct { int64_t now; int allow, polls, stopped, observes, models, executes, tests; char code[7]; } fake_t;
typedef struct { fake_t fake; agent_loop_t loop; mcp_t mcp; web_t web; http_request_t q; http_response_t r; char root[4096]; } fixture_t;
static int64_t now(void *v) { return ((fake_t *)v)->now; }
static int poll_event(void *v, kbd_event_t *e) { fake_t *f = v; f->polls++; if (!f->allow) return 0; f->allow = 0; *e = (kbd_event_t){.kind=K_ALLOW}; return 1; }
static int lease(void *v, lease_op_t op) { if (op == LEASE_ESTOP) ((fake_t *)v)->stopped++; return 0; }
static int observe(void *v, loop_frame_t *frame, loop_guard_t guard, void *ctx)
{
    fake_t *f = v; if (guard(ctx)) return -1;
    static const unsigned char jpeg[] = {255,216,255,217};
    *frame = (loop_frame_t){.width=320,.height=200,.jpeg=jpeg,.jpeg_len=4,.hash=(unsigned)++f->observes};
    strcpy(frame->app,"Notes"); strcpy(frame->title,"Notes"); return 0;
}
static int model(void *v, const loop_frame_t *frame, const char *history, loop_reply_t *reply, loop_guard_t guard, void *ctx)
{
    (void)frame; (void)history; fake_t *f = v; if (guard(ctx)) return -1;
    f->models++; memset(reply,0,sizeof *reply); reply->action.kind = f->models >= 8 ? A_DONE : A_WAIT;
    reply->action.ms = 1; strcpy(reply->action.target,"Notes"); return 0;
}
static int execute(void *v, const action_t *a, int input, int64_t deadline, loop_guard_t guard, void *ctx)
{
    (void)a; (void)input; (void)deadline; if (guard(ctx)) return -1; ((fake_t *)v)->executes++; return 0;
}
static int test_connection(void *v, const llm_provider_t *p, const netcfg_t *n, const char **error)
{
    fake_t *f = v; net_route_t route; CHECK(!netcfg_route_for(n,p->route,&route) && route == NET_PROXY);
    if (f->tests < 0) (void)poll(NULL,0,f->tests == -2 ? 2500 : 900);
    f->tests++; f->now += 17; *error = "none"; return 0;
}
static void write_file(const char *path, const char *data, mode_t mode)
{
    int fd = open(path,O_WRONLY | O_CREAT | O_TRUNC,mode); CHECK(fd >= 0);
    CHECK(write(fd,data,strlen(data)) == (ssize_t)strlen(data)); CHECK(!fchmod(fd,mode)); CHECK(!close(fd));
}
/* Device files under the private test root; never the real /run, /tmp/mk20 or /proc. */
static void paths(fixture_t *f)
{
    web_t *w = &f->web;
    snprintf(w->pair_path,sizeof w->pair_path,"%.4000s/run/pair-code",f->root);
    snprintf(w->uptime_path,sizeof w->uptime_path,"%.200s/uptime",f->root);
    snprintf(w->health_path,sizeof w->health_path,"%.4000s/health.json",f->root);
    snprintf(w->maint_dir,sizeof w->maint_dir,"%.4000s/maint",f->root);
    snprintf(w->debug_path,sizeof w->debug_path,"%.4000s/debug.conf",f->root);
    CHECK(strlen(f->root) < 200); write_file(w->uptime_path,"1234.56 999.00\n",0644);
    CHECK(!mkdir(w->maint_dir,0700) || errno == EEXIST);
}
static void setup(fixture_t *f)
{
    memset(f,0,sizeof *f); f->fake.now = INT64_C(5000000000);
    char path[] = "/tmp/a7-unit-XXXXXX"; CHECK(mkdtemp(path)); CHECK(realpath(path,f->root));
    char secrets[4096], config[4096]; CHECK(strlen(f->root) < 4000);
    snprintf(secrets,sizeof secrets,"%.4000s/secrets",f->root); snprintf(config,sizeof config,"%.4000s/config",f->root);
    loop_ports_t ports = {.ctx=&f->fake,.now=now,.poll=poll_event,.lease=lease,.observe=observe,.model=model,.execute=execute};
    policy_config_t p; policy_defaults(&p); p.confirm_first = p.confirm_app_switch = 0;
    loop_init(&f->loop,&p,&ports); char token[65] = {0}; mcp_init(&f->mcp,&f->loop,token,"127.0.0.1:8765");
    web_init(&f->web,&f->mcp,NULL,"127.0.0.1:8780",secrets,config); f->web.agent = 1;
    f->web.connection_test = test_connection; f->web.test_ctx = &f->fake;
    paths(f);
    strcpy(f->q.host,f->web.host); strcpy(f->q.content_type,"application/json"); f->q.has_origin = 1; strcpy(f->q.origin,"http://127.0.0.1:8780"); f->q.tag = 1;
}
static void cleanup(fixture_t *f)
{
    free(f->r.body); web_free(&f->web); mcp_free(&f->mcp);
    /* All test-created files have fixed names; no shell/path expansion. */
    char run[4096]; snprintf(run,sizeof run,"%.4000s/run",f->root);
    const char *dirs[] = {f->web.secrets,f->web.config,run,f->web.maint_dir,f->root};
    for (size_t i = 0; i < 5; i++) {
        DIR *d = opendir(dirs[i]); if (!d) continue;
        int dir = dirfd(d); struct dirent *e;
        while ((e = readdir(d))) if (strcmp(e->d_name,".") && strcmp(e->d_name,"..")) (void)unlinkat(dir,e->d_name,0);
        closedir(d); CHECK(!rmdir(dirs[i]) || errno == ENOENT);
    }
}
static cJSON *request(fixture_t *f, const char *method, const char *path, const char *body)
{
    free(f->r.body); memset(&f->r,0,sizeof f->r);
    char raw[HTTPD_BODY+1]; size_t len = body ? strlen(body) : 0; CHECK(len <= HTTPD_BODY);
    if (body) memcpy(raw,body,len+1);
    strcpy(f->q.method,method); strcpy(f->q.path,path); f->q.body = body ? raw : NULL; f->q.body_len = len;
    int rc = web_handle(&f->web,&f->q,&f->r);
    int64_t deadline = dns_now_ms()+2500;
    while (rc == 1 && dns_now_ms() < deadline) { (void)poll(NULL,0,5); rc = web_handle(&f->web,&f->q,&f->r); }
    CHECK(rc == 0);
    if (!strncmp(path,"/secret/",8)) { int wiped = 1; for (size_t i = 0; i < len; i++) if (raw[i]) wiped = 0; CHECK(wiped); }
    return f->r.body ? agent_json_parse(f->r.body,f->r.len,HTTPD_BODY) : NULL;
}
/* Reads the challenge exactly as mk20-kbd does; checks the documented file format. */
static int read_pair(fixture_t *f, long long *expires)
{
    int fd = open(f->web.pair_path,O_RDONLY); if (fd < 0) { *f->fake.code = 0; return -1; }
    struct stat st; CHECK(!fstat(fd,&st) && S_ISREG(st.st_mode) && (st.st_mode & 07777) == 0600 && st.st_size <= 64);
    char b[80] = {0}; ssize_t n = read(fd,b,sizeof b-1); close(fd); CHECK(n > 0 && n == st.st_size);
    char code[8] = {0}; long long e = -1; int used = 0;
    CHECK(sscanf(b,"code=%6[0-9]\nexpires_uptime=%lld\n%n",code,&e,&used) == 2 && used == n && strlen(code) == 6);
    char again[80]; snprintf(again,sizeof again,"code=%s\nexpires_uptime=%lld\n",code,e); CHECK(!strcmp(again,b));
    memcpy(f->fake.code,code,7); if (expires) *expires = e; return 0;
}
static void pair_browser(fixture_t *f)
{
    cJSON_Delete(request(f,"POST","/pair/start","{}")); CHECK(f->r.status == 200);
    long long expires; CHECK(!read_pair(f,&expires) && expires > 1234 && expires <= 1234+600);
    char body[64]; snprintf(body,sizeof body,"{\"code\":\"%s\"}",f->fake.code);
    cJSON *j = request(f,"POST","/pair",body); CHECK(f->r.status == 200 && j);
    CHECK(strstr(f->r.cookie,"HttpOnly; SameSite=Strict; Path=/") && !strstr(f->r.cookie,"Secure"));
    memcpy(f->q.cookie,f->r.cookie,37); f->q.cookie[37] = 0; f->q.has_cookie = 1;
    strcpy(f->q.csrf,cJSON_GetObjectItemCaseSensitive(j,"csrf")->valuestring); f->q.has_csrf = 1; cJSON_Delete(j);
    CHECK(!f->web.pairing && !*f->web.code && access(f->web.pair_path,F_OK) && errno == ENOENT);
}
static void pairing(void)
{
    fixture_t f; setup(&f);
    /* No uptime source (or no writable display directory): no challenge exists. */
    char uptime[256]; strcpy(uptime,f.web.uptime_path); CHECK(!unlink(uptime));
    cJSON_Delete(request(&f,"POST","/pair/start","{}")); CHECK(f.r.status == 503 && strstr(f.r.body,"display_unavailable"));
    CHECK(!f.web.pairing && !*f.web.code && read_pair(&f,NULL));
    cJSON_Delete(request(&f,"POST","/pair","{\"code\":\"000000\"}")); CHECK(f.r.status == 401);
    write_file(uptime,"1234.99 1.00\n",0644);
    cJSON_Delete(request(&f,"POST","/pair/start","{}")); CHECK(f.web.pairing && strstr(f.r.body,"\"expires_in_s\":300"));
    long long expires; CHECK(!read_pair(&f,&expires) && expires == 1234+300);
    char code[64]; snprintf(code,sizeof code,"{\"code\":\"%s\"}",f.fake.code);
    /* Expiry removes the display file even without a request. */
    f.fake.now += WEB_PAIR_MS; web_tick(&f.web); CHECK(!f.web.pairing && read_pair(&f,NULL));
    cJSON_Delete(request(&f,"POST","/pair",code)); CHECK(f.r.status == 401);
    cJSON_Delete(request(&f,"POST","/pair/start","{}")); CHECK(!read_pair(&f,NULL));
    char first[7]; strcpy(first,f.fake.code);
    f.fake.now += 1000; cJSON_Delete(request(&f,"POST","/pair/start","{}")); CHECK(strstr(f.r.body,"\"expires_in_s\":299"));
    CHECK(!read_pair(&f,NULL) && !strcmp(first,f.fake.code));
    char wrong[7]; strcpy(wrong,f.fake.code); wrong[0] = wrong[0] == '0' ? '1' : '0';
    snprintf(code,sizeof code,"{\"code\":\"%s\"}",wrong);
    for (int i = 0; i < 4; i++) { cJSON_Delete(request(&f,"POST","/pair",code)); CHECK(f.r.status == 401 && f.web.pairing); }
    /* A repeated start cannot reset the attempt budget. */
    cJSON_Delete(request(&f,"POST","/pair/start","{}")); CHECK(f.web.attempts == 4);
    cJSON_Delete(request(&f,"POST","/pair",code)); CHECK(f.r.status == 401);
    CHECK(!f.web.pairing && !*f.web.code && read_pair(&f,NULL)); pair_browser(&f);
    /* One-time code cannot establish another session. */
    snprintf(code,sizeof code,"{\"code\":\"%s\"}",f.fake.code); cJSON_Delete(request(&f,"POST","/pair",code)); CHECK(f.r.status == 401);
    /* Both session slots taken: no code is shown, the reason is explicit. */
    pair_browser(&f); cJSON_Delete(request(&f,"POST","/pair/start","{}"));
    CHECK(f.r.status == 503 && strstr(f.r.body,"\"error\":\"sessions_full\"") && strstr(f.r.body,"已有 2 个已配对的浏览器，请等待过期或在已配对页面退出"));
    CHECK(!f.web.pairing && read_pair(&f,NULL));
    snprintf(code,sizeof code,"{\"code\":\"%s\"}",f.fake.code); cJSON_Delete(request(&f,"POST","/pair",code)); CHECK(f.r.status == 401);
    CHECK(!f.web.locked_until);   /* 9 failures in the window so far (with and without a challenge) */
    f.fake.now += WEB_IDLE_MS; web_tick(&f.web);
    CHECK(!*f.web.sessions[0].cookie && !*f.web.sessions[1].cookie);
    /* A symlinked display directory is refused (never written through). */
    CHECK(!f.web.pairing); char run[4096], moved[4096]; snprintf(run,sizeof run,"%.4000s/run",f.root); snprintf(moved,sizeof moved,"%.4000s/elsewhere",f.root);
    CHECK(!rename(run,moved) && !symlink(moved,run));
    cJSON_Delete(request(&f,"POST","/pair/start","{}")); CHECK(f.r.status == 503 && !f.web.pairing);
    char target[4200]; snprintf(target,sizeof target,"%s/pair-code",moved); CHECK(access(target,F_OK));
    CHECK(!unlink(run) && !rename(moved,run)); cleanup(&f);
}
static void pair_limits(void)
{
    fixture_t f; setup(&f); char code[64], wrong[7];
    /* New challenges per window are bounded (each one lights the keyboard display). */
    for (int i = 0; i < WEB_PAIR_STARTS; i++) {
        cJSON_Delete(request(&f,"POST","/pair/start","{}")); CHECK(f.r.status == 200 && !read_pair(&f,NULL));
        strcpy(wrong,f.fake.code); wrong[0] = wrong[0] == '0' ? '1' : '0'; snprintf(code,sizeof code,"{\"code\":\"%s\"}",wrong);
        if (i < 1) for (unsigned k = 0; k < WEB_PAIR_ATTEMPTS; k++) cJSON_Delete(request(&f,"POST","/pair",code));
        else { f.web.pairing = 0; memset(f.web.code,0,sizeof f.web.code); web_pair_clear(&f.web); } /* white-box: end without spending guesses */
        CHECK(!f.web.pairing); f.fake.now += 1000;
    }
    cJSON_Delete(request(&f,"POST","/pair/start","{}")); CHECK(f.r.status == 429 && strstr(f.r.body,"rate_limited") && read_pair(&f,NULL));
    f.fake.now += WEB_RATE_WINDOW_MS; cJSON_Delete(request(&f,"POST","/pair/start","{}")); CHECK(f.r.status == 200);
    /* Global guess budget: WEB_PAIR_FAILS wrong codes (two challenges) lock pairing. */
    unsigned fails = 0;
    for (int round = 0; round < 3 && fails < WEB_PAIR_FAILS; round++) {
        if (round) { cJSON_Delete(request(&f,"POST","/pair/start","{}")); CHECK(f.r.status == 200); }
        CHECK(!read_pair(&f,NULL)); strcpy(wrong,f.fake.code); wrong[0] = wrong[0] == '0' ? '1' : '0'; snprintf(code,sizeof code,"{\"code\":\"%s\"}",wrong);
        for (unsigned k = 0; k < WEB_PAIR_ATTEMPTS && fails < WEB_PAIR_FAILS; k++, fails++) { cJSON_Delete(request(&f,"POST","/pair",code)); CHECK(f.r.status == 401); }
    }
    CHECK(f.web.locked_until && !f.web.pairing && read_pair(&f,NULL));
    cJSON_Delete(request(&f,"POST","/pair/start","{}")); CHECK(f.r.status == 429 && strstr(f.r.body,"\"retry_after_s\":600"));
    cJSON_Delete(request(&f,"POST","/pair","{\"code\":\"123456\"}")); CHECK(f.r.status == 429);
    f.fake.now += WEB_RATE_WINDOW_MS; pair_browser(&f); CHECK(!f.web.locked_until);
    /* Failures without any live challenge count too: blind probing is bounded the same way. */
    f.fake.now += WEB_RATE_WINDOW_MS; CHECK(!f.web.pairing);
    for (int i = 0; i < WEB_PAIR_FAILS; i++) { cJSON_Delete(request(&f,"POST","/pair","{\"code\":\"000000\"}")); CHECK(f.r.status == 401); }
    CHECK(f.web.locked_until);
    cJSON_Delete(request(&f,"POST","/pair/start","{}")); CHECK(f.r.status == 429 && read_pair(&f,NULL));
    cleanup(&f);
}
/* WEB_SESSIONS slots: explicit refusal for a third browser, logout frees a slot. */
static void sessions_logout(void)
{
    fixture_t f; setup(&f); pair_browser(&f);
    char a_cookie[256], a_csrf[128]; strcpy(a_cookie,f.q.cookie); strcpy(a_csrf,f.q.csrf);
    cJSON_Delete(request(&f,"POST","/api/tasks","{\"goal\":\"owned by A\"}")); CHECK(f.r.status == 202 && f.mcp.task.live);
    /* One slot left; another browser takes it after this code was shown: the code is void. */
    cJSON_Delete(request(&f,"POST","/pair/start","{}")); CHECK(f.r.status == 200 && !read_pair(&f,NULL));
    strcpy(f.web.sessions[1].cookie,"0123456789abcdef0123456789abcdef"); strcpy(f.web.sessions[1].csrf,"fedcba9876543210fedcba9876543210");
    f.web.sessions[1].created = f.web.sessions[1].activity = f.fake.now;
    char code[64]; snprintf(code,sizeof code,"{\"code\":\"%s\"}",f.fake.code);
    cJSON_Delete(request(&f,"POST","/pair",code)); CHECK(f.r.status == 503 && strstr(f.r.body,"sessions_full") && !f.web.pairing && read_pair(&f,NULL));
    snprintf(f.q.cookie,sizeof f.q.cookie,"mk20=%s",f.web.sessions[1].cookie); strcpy(f.q.csrf,f.web.sessions[1].csrf);
    cJSON_Delete(request(&f,"POST","/pair/start","{}")); CHECK(f.r.status == 503 && strstr(f.r.body,"sessions_full"));
    /* Logout: paired + CSRF; frees exactly this slot and expires the cookie. */
    f.q.has_csrf = 0; cJSON_Delete(request(&f,"POST","/api/logout","{}")); CHECK(f.r.status == 403); f.q.has_csrf = 1;
    cJSON_Delete(request(&f,"POST","/api/logout","{}")); CHECK(f.r.status == 200 && strstr(f.r.body,"logged_out") && strstr(f.r.cookie,"mk20=; Max-Age=0"));
    CHECK(!*f.web.sessions[1].cookie && *f.web.sessions[0].cookie);
    cJSON_Delete(request(&f,"GET","/api/session",NULL)); CHECK(f.r.status == 401);
    cJSON_Delete(request(&f,"POST","/api/logout","{}")); CHECK(f.r.status == 401);
    cJSON_Delete(request(&f,"POST","/pair/start","{}")); CHECK(f.r.status == 200 && !read_pair(&f,NULL));
    /* A's session survives; logging A out also stops the task A owns. */
    strcpy(f.q.cookie,a_cookie); strcpy(f.q.csrf,a_csrf);
    cJSON_Delete(request(&f,"GET","/api/session",NULL)); CHECK(f.r.status == 200);
    cJSON_Delete(request(&f,"POST","/api/logout","{}")); CHECK(f.r.status == 200 && !f.mcp.task.live && !*f.mcp.task.goal);
    CHECK(!*f.web.sessions[0].cookie && !*f.web.sessions[1].cookie);
    cleanup(&f);
}
/* The display file cannot be (re)published: 503 with a reason, never "started". */
static void pair_publish_failure(void)
{
    fixture_t f; setup(&f);
    cJSON_Delete(request(&f,"POST","/pair/start","{}")); CHECK(f.r.status == 200 && f.web.pairing && !read_pair(&f,NULL));
    char run[4096]; snprintf(run,sizeof run,"%.4000s/run",f.root); CHECK(!chmod(run,0777));
    cJSON_Delete(request(&f,"POST","/pair/start","{}"));
    CHECK(f.r.status == 503 && strstr(f.r.body,"display_unavailable") && strstr(f.r.body,"无法在键盘上显示配对码") && !strstr(f.r.body,"started"));
    CHECK(f.web.pairing && !f.web.attempts);   /* the live challenge (and its budget) is unchanged */
    CHECK(!chmod(run,0700)); cJSON_Delete(request(&f,"POST","/pair/start","{}")); CHECK(f.r.status == 200 && strstr(f.r.body,"started"));
    CHECK(!read_pair(&f,NULL));
    char code[64]; snprintf(code,sizeof code,"{\"code\":\"%s\"}",f.fake.code); cJSON_Delete(request(&f,"POST","/pair",code)); CHECK(f.r.status == 200);
    /* New challenge whose first publish fails: nothing is live afterwards. */
    CHECK(!chmod(run,0777)); cJSON_Delete(request(&f,"POST","/pair/start","{}"));
    CHECK(f.r.status == 503 && strstr(f.r.body,"display_unavailable") && !f.web.pairing && !*f.web.code);
    CHECK(!chmod(run,0700)); cleanup(&f);
}
static void auth(void)
{
    fixture_t f; setup(&f);
    const char *paths[] = {"/secret/qwen","/config/net","/test","/api/tasks","/api/events","/unknown","/web.js","/api/session","/api/catalogue","/api/status","/api/maint/restart-kbd"};
    for (size_t i = 0; i < sizeof paths/sizeof *paths; i++) { cJSON_Delete(request(&f,"GET",paths[i],NULL)); CHECK(f.r.status == 401 && !strcmp(f.r.body,"{\"error\":\"unauthorized\"}")); }
    cJSON_Delete(request(&f,"GET","/",NULL)); CHECK(f.r.status == 200 && f.r.len >= 6000 && f.r.csp && strstr(f.r.csp,"sha256-"));
    CHECK(strstr(f.r.body,"<script>") == strstr(f.r.body,"<script") && !strstr(strstr(f.r.body,"<script>")+1,"<script"));
    cJSON_Delete(request(&f,"GET","/chat",NULL)); CHECK(f.r.status == 200 && f.r.len >= 6000);
    /* DNS rebinding: a different Host (name or address) is refused even for public pages. */
    const char *hosts[] = {"mk20.example:8780","127.0.0.1:8781","127.0.0.1","172.30.250.2:8780","localhost:8780"};
    for (size_t i = 0; i < sizeof hosts/sizeof *hosts; i++) {
        strcpy(f.q.host,hosts[i]);
        cJSON_Delete(request(&f,"GET","/",NULL)); CHECK(f.r.status == 403 && !f.r.csp);
        cJSON_Delete(request(&f,"POST","/pair/start","{}")); CHECK(f.r.status == 403 && !f.web.pairing);
    }
    strcpy(f.q.host,f.web.host);
    strcpy(f.q.origin,"http://172.30.250.2:8780"); cJSON_Delete(request(&f,"POST","/pair/start","{}")); CHECK(f.r.status == 403 && !f.web.pairing);
    strcpy(f.q.origin,f.web.origin);
    pair_browser(&f);
    f.q.has_csrf = 0; cJSON_Delete(request(&f,"POST","/secret/qwen","{\"value\":\"fixture-value\"}")); CHECK(f.r.status == 403);
    f.q.has_csrf = 1; char saved = f.q.csrf[0]; f.q.csrf[0] ^= 1;
    cJSON_Delete(request(&f,"POST","/secret/qwen","{}")); CHECK(f.r.status == 403); f.q.csrf[0] = saved;
    cJSON_Delete(request(&f,"GET","/api/session",NULL)); CHECK(f.r.status == 200 && strstr(f.r.body,f.q.csrf) && strstr(f.r.body,"\"agent\":true"));
    strcpy(f.q.origin,"http://evil"); cJSON_Delete(request(&f,"GET","/config/net",NULL)); CHECK(f.r.status == 403);
    f.q.has_origin = 0; cJSON_Delete(request(&f,"POST","/secret/qwen","{}")); CHECK(f.r.status == 403);
    f.q.has_fetch_site = 1; strcpy(f.q.fetch_site,"cross-site"); cJSON_Delete(request(&f,"POST","/secret/qwen","{}")); CHECK(f.r.status == 403);
    strcpy(f.q.fetch_site,"same-origin"); cJSON_Delete(request(&f,"POST","/secret/qwen","{}")); CHECK(f.r.status == 400);
    char cookie[256]; strcpy(cookie,f.q.cookie); strcat(f.q.cookie,"; "); strcat(f.q.cookie,cookie);
    cJSON_Delete(request(&f,"GET","/config/net",NULL)); CHECK(f.r.status == 401); strcpy(f.q.cookie,cookie);
    f.q.cookie[5] ^= 1; cJSON_Delete(request(&f,"GET","/config/net",NULL)); CHECK(f.r.status == 401); f.q.cookie[5] ^= 1;
    f.fake.now += WEB_IDLE_MS; cJSON_Delete(request(&f,"GET","/config/net",NULL)); CHECK(f.r.status == 401);
    pair_browser(&f); int64_t created = f.fake.now;
    for (int i = 0; i < 24; i++) { f.fake.now = created+(i+1)*(WEB_IDLE_MS-1); cJSON_Delete(request(&f,"GET","/config/net",NULL)); CHECK(f.r.status == 404); }
    f.fake.now = created+WEB_ABSOLUTE_MS; cJSON_Delete(request(&f,"GET","/config/net",NULL)); CHECK(f.r.status == 401); cleanup(&f);
}
static void secrets(void)
{
    fixture_t f; setup(&f); pair_browser(&f);
    cJSON_Delete(request(&f,"POST","/secret/qwen","{\"value\":\"fixture-value\"}")); CHECK(f.r.status == 200 && !strcmp(f.r.body,"{\"set\":true}"));
    struct stat st; CHECK(!stat(f.web.secrets,&st) && (st.st_mode & 07777) == 0700);
    int dir = web_directory(f.web.secrets,0); CHECK(dir >= 0); int old = openat(dir,"qwen.key",O_RDONLY); CHECK(old >= 0);
    CHECK(!fstat(old,&st) && (st.st_mode & 07777) == 0600);
    cJSON_Delete(request(&f,"POST","/secret/qwen","{\"value\":\"replacement\"}")); CHECK(f.r.status == 200);
    char b[64] = {0}; CHECK(read(old,b,sizeof b) == 13 && !strcmp(b,"fixture-value")); close(old);
    cJSON_Delete(request(&f,"GET","/secret/qwen",NULL)); CHECK(f.r.status == 200 && !strcmp(f.r.body,"{\"set\":true}"));
    const char *bad[] = {"{\"value\":\"\"}","{\"value\":\"a\\nb\"}","{\"value\":\"a\\rb\"}","{\"value\":\"a\\u0000b\"}","{\"value\":\"a\",\"api_key\":\"b\"}"};
    for (size_t i = 0; i < sizeof bad/sizeof *bad; i++) { cJSON_Delete(request(&f,"POST","/secret/qwen",bad[i])); CHECK(f.r.status == 400); }
    cJSON_Delete(request(&f,"POST","/secret/mcp","{\"value\":\"bad\"}")); CHECK(f.r.status == 404);
    cJSON_Delete(request(&f,"POST","/secret/../file","{\"value\":\"bad\"}")); CHECK(f.r.status == 404);
    char *large = malloc(4200); CHECK(large); strcpy(large,"{\"value\":\""); memset(large+10,'x',4096); strcpy(large+4106,"\"}");
    cJSON_Delete(request(&f,"POST","/secret/vnc",large)); CHECK(f.r.status == 200 && !strcmp(f.r.body,"{\"set\":true}"));
    memset(large+10,'x',4097); strcpy(large+4107,"\"}"); cJSON_Delete(request(&f,"POST","/secret/vnc",large)); CHECK(f.r.status == 400); free(large);
    CHECK(!symlinkat("qwen.key",dir,"glm.key")); cJSON_Delete(request(&f,"POST","/secret/glm","{\"value\":\"new\"}")); CHECK(f.r.status == 500);
    cJSON_Delete(request(&f,"GET","/secret/glm",NULL)); CHECK(f.r.status == 500);
    cJSON_Delete(request(&f,"DELETE","/secret/glm",NULL)); CHECK(f.r.status == 500); CHECK(!unlinkat(dir,"glm.key",0));
    CHECK(!mkdirat(dir,"glm.key",0700)); cJSON_Delete(request(&f,"POST","/secret/glm","{\"value\":\"new\"}")); CHECK(f.r.status == 500); CHECK(!unlinkat(dir,"glm.key",AT_REMOVEDIR));
    cJSON_Delete(request(&f,"DELETE","/secret/qwen",NULL)); CHECK(f.r.status == 200);
    cJSON_Delete(request(&f,"GET","/secret/qwen",NULL)); CHECK(f.r.status == 200 && !strcmp(f.r.body,"{\"set\":false}"));
    close(dir);
    char link[4096]; snprintf(link,sizeof link,"%.4000s/link",f.root); CHECK(!symlink(f.web.secrets,link));
    char original[4096]; strcpy(original,f.web.secrets); strcpy(f.web.secrets,link);
    cJSON_Delete(request(&f,"POST","/secret/glm","{\"value\":\"new\"}")); CHECK(f.r.status == 500);
    snprintf(f.web.secrets,sizeof f.web.secrets,"%.4000s/link/child",f.root);
    cJSON_Delete(request(&f,"POST","/secret/glm","{\"value\":\"new\"}")); CHECK(f.r.status == 500);
    CHECK(!unlink(link)); strcpy(f.web.secrets,original); cleanup(&f);
}
static void secret_nul(void)
{
    fixture_t f; setup(&f); pair_browser(&f);
    const char *bad[] = {"{\"value\":\"abc\\u0000def\"}","{\"value\":\"\\u0000\"}","{\"value\":\"abc\\u0000\"}"};
    for (size_t i = 0; i < sizeof bad/sizeof *bad; i++) {
        cJSON_Delete(request(&f,"POST","/secret/glm",bad[i])); CHECK(f.r.status == 400);
        cJSON_Delete(request(&f,"GET","/secret/glm",NULL)); CHECK(strstr(f.r.body,"false"));
    }
    /* Rejected auth/content-type paths scrub the raw body too. */
    f.q.has_csrf = 0; cJSON_Delete(request(&f,"POST","/secret/glm","{\"value\":\"fixture\"}")); CHECK(f.r.status == 403);
    cleanup(&f);
}
static const char *net_json = "{\"version\":1,\"profile\":\"mac_proxy\",\"proxy\":{\"url\":\"http://172.30.250.1:7890\",\"fallbacks\":[\"http://10.20.0.1:7890\",\"http://169.254.7.1:7890\"]},\"routes\":{\"domestic\":\"proxy\",\"foreign\":\"proxy\"}}";
static void configs(void)
{
    fixture_t f; setup(&f); pair_browser(&f);
    cJSON_Delete(request(&f,"PUT","/config/net",net_json)); CHECK(f.r.status == 200);
    cJSON_Delete(request(&f,"GET","/config/net",NULL)); CHECK(f.r.status == 200 && !strcmp(f.r.body,net_json));
    char provider[8192]; snprintf(provider,sizeof provider,"{\"version\":1,\"provider\":\"qwen\",\"model\":\"qwen-vl-max\",\"base_url\":\"https://dashscope.aliyuncs.com/compatible-mode/v1\",\"domestic\":true,\"route\":\"domestic\",\"api_key_file\":\"%s/qwen.key\"}",f.web.secrets);
    cJSON_Delete(request(&f,"PUT","/config/provider",provider)); CHECK(f.r.status == 200);
    cJSON_Delete(request(&f,"POST","/test","{}")); CHECK(f.r.status == 200 && strstr(f.r.body,"\"latency_ms\":17"));
    const char *invalid[] = {
        "{\"version\":1,\"profile\":\"usb_wifi\",\"mode\":\"direct\",\"routes\":{\"domestic\":\"direct\",\"foreign\":\"direct\"},\"proxy\":{\"url\":null,\"fallbacks\":[]}}",
        "{\"version\":1,\"profile\":\"board_wifi\",\"routes\":{\"domestic\":\"direct\",\"foreign\":\"direct\"},\"proxy\":{\"url\":null,\"fallbacks\":[]}}",
        "{\"version\":1,\"profile\":\"mac_proxy\",\"proxy\":{\"url\":\"http://8.8.8.8:7890\",\"fallbacks\":[]},\"routes\":{\"domestic\":\"proxy\",\"foreign\":\"proxy\"}}",
        "{\"version\":1,\"profile\":\"mac_proxy\",\"unknown\":true,\"proxy\":{\"url\":\"http://172.30.250.1:7890\"},\"routes\":{\"domestic\":\"proxy\",\"foreign\":\"proxy\"}}",
        "{\"version\":1,\"profile\":\"board_wifi\",\"proxy\":{\"url\":null,\"password\":\"x\"},\"routes\":{\"domestic\":\"direct\",\"foreign\":\"blocked\"}}",
        "{\"version\":1,\"profile\":\"board_wifi\",\"proxy\":{\"url\":null},\"routes\":{\"domestic\":\"direct\",\"foreign\":\"blocked\",\"extra\":1}}"
    };
    for (size_t i = 0; i < sizeof invalid/sizeof *invalid; i++) { cJSON_Delete(request(&f,"PUT","/config/net",invalid[i])); CHECK(f.r.status == 400); }
    cJSON *j = agent_json_parse(provider,strlen(provider),LLM_PROVIDER_MAX); CHECK(j);
    const char *fields[] = {"base_url","api_key_file","provider","route"};
    for (size_t i = 0; i < sizeof fields/sizeof *fields; i++) {
        cJSON *copy = cJSON_Duplicate(j,1); cJSON_ReplaceItemInObjectCaseSensitive(copy,fields[i],cJSON_CreateString("invalid"));
        char *s = cJSON_PrintUnformatted(copy); cJSON_Delete(request(&f,"PUT","/config/provider",s)); CHECK(f.r.status == 400); free(s); cJSON_Delete(copy);
    }
    cJSON *copy = cJSON_Duplicate(j,1); cJSON_ReplaceItemInObjectCaseSensitive(copy,"base_url",cJSON_CreateString("https://example.invalid/v1"));
    char *unpinned = cJSON_PrintUnformatted(copy); cJSON_Delete(copy);
    cJSON_Delete(request(&f,"PUT","/config/provider",unpinned)); CHECK(f.r.status == 400); free(unpinned);
    const char *board = "{\"version\":1,\"profile\":\"board_wifi\",\"proxy\":{\"url\":\"http://127.0.0.1:7890\",\"fallbacks\":[]},\"routes\":{\"domestic\":\"direct\",\"foreign\":\"proxy\"}}";
    CHECK(!web_config_validate(&f.web,"net",board,strlen(board)));
    cJSON_AddStringToObject(j,"api_key","never-allowed"); char *bad = cJSON_PrintUnformatted(j); cJSON_Delete(j);
    cJSON_Delete(request(&f,"PUT","/config/provider",bad)); CHECK(f.r.status == 400); free(bad);
    struct stat st; CHECK(!stat(f.web.config,&st) && (st.st_mode & 07777) == 0700);
    int dir = web_directory(f.web.config,0); CHECK(dir >= 0); CHECK(!fstatat(dir,"net.json",&st,0) && (st.st_mode & 07777) == 0600); close(dir); cleanup(&f);
}
static void glm_coding(void)
{
    fixture_t f; setup(&f); pair_browser(&f);
    char file[64]; CHECK(!web_secret_name("glm-coding",file) && !strcmp(file,"glm-coding.key"));
    char provider[8192]; snprintf(provider,sizeof provider,"{\"version\":1,\"provider\":\"glm-coding\",\"model\":\"glm-5.3\",\"base_url\":\"https://open.bigmodel.cn/api/coding/paas/v4\",\"domestic\":true,\"route\":\"domestic\",\"api_key_file\":\"%s/glm-coding.key\"}",f.web.secrets);
    cJSON_Delete(request(&f,"PUT","/config/provider",provider)); CHECK(f.r.status == 200);
    cJSON_Delete(request(&f,"GET","/config/provider",NULL)); CHECK(f.r.status == 200 && !strcmp(f.r.body,provider));
    llm_provider_t p; CHECK(!llm_provider_parse(provider,strlen(provider),&p));
    const char *board = "{\"version\":1,\"profile\":\"board_wifi\",\"proxy\":{\"url\":null,\"fallbacks\":[]},\"routes\":{\"domestic\":\"direct\",\"foreign\":\"blocked\"}}";
    netcfg_t net = {0}; net_route_t route;
    CHECK(!netcfg_parse(board,strlen(board),&net));
    CHECK(!netcfg_route_for(&net,p.route,&route) && route == NET_DIRECT); netcfg_free(&net);
    cJSON_Delete(request(&f,"POST","/secret/glm-coding","{\"value\":\"fixture-value\"}")); CHECK(f.r.status == 200);
    int dir = web_directory(f.web.secrets,0); CHECK(dir >= 0); struct stat st;
    CHECK(!fstatat(dir,file,&st,0) && (st.st_mode & 07777) == 0600); close(dir);
    cJSON_Delete(request(&f,"GET","/secret/glm-coding",NULL)); CHECK(f.r.status == 200 && !strcmp(f.r.body,"{\"set\":true}"));
    cJSON_Delete(request(&f,"DELETE","/secret/glm-coding",NULL)); CHECK(f.r.status == 200);
    cleanup(&f);
}
static void tasks(void)
{
    fixture_t f; setup(&f); pair_browser(&f);
    cJSON_Delete(request(&f,"POST","/api/tasks","{\"goal\":\"private-goal-fixture\"}")); CHECK(f.r.status == 202 && strstr(f.r.body,"waiting_allow") && !strstr(f.r.body,"private-goal-fixture"));
    for (int i = 0; i < 3; i++) web_tick(&f.web);
    CHECK(!f.fake.observes && !f.fake.models && !f.fake.executes && !f.loop.allowed);
    cJSON_Delete(request(&f,"POST","/test","{}")); CHECK(f.r.status == 409 && !f.fake.tests);
    cJSON_Delete(request(&f,"POST","/api/tasks/t1/pause","{}")); CHECK(f.r.status == 200);
    f.fake.allow = 1; web_tick(&f.web); CHECK(f.loop.allowed && !f.fake.observes);
    cJSON_Delete(request(&f,"POST","/api/tasks/t1/resume","{}")); CHECK(f.r.status == 200);
    for (int i = 0; i < 19; i++) web_tick(&f.web);
    cJSON_Delete(request(&f,"POST","/api/tasks/t1/pause","{}")); CHECK(f.loop.paused && f.mcp.task.live);
    unsigned shots = 0; for (size_t i = 0; i < 5; i++) if (f.mcp.shots[i].jpeg) shots++;
    CHECK(shots == 5);
    cJSON_Delete(request(&f,"GET","/api/tasks/t1/steps/1/shot.jpg",NULL)); CHECK(f.r.status == 404);
    cJSON_Delete(request(&f,"GET","/api/tasks/t1/steps/7/shot.jpg",NULL)); CHECK(f.r.status == 200 && f.r.len == 4 && !strcmp(f.r.content_type,"image/jpeg"));
    cJSON_Delete(request(&f,"GET","/api/tasks/t1",NULL)); CHECK(f.r.status == 200 && !strstr(f.r.body,"private-goal-fixture") && strstr(f.r.body,"wait"));
    cJSON_Delete(request(&f,"POST","/api/tasks/t1/resume","{}"));
    for (int i = 0; i < 10; i++) web_tick(&f.web);
    CHECK(!f.mcp.task.live && f.mcp.task.steps == 8 && !*f.mcp.task.goal);
    for (size_t i = 0; i < 5; i++) CHECK(!f.mcp.shots[i].jpeg);
    cJSON_Delete(request(&f,"GET","/api/tasks/t1/steps/7/shot.jpg",NULL)); CHECK(f.r.status == 404);
    CHECK(!strcmp(f.mcp.task.state,"done"));
    cJSON_Delete(request(&f,"POST","/api/tasks/t1/stop","{}"));
    CHECK(!strcmp(f.mcp.task.state,"done") && f.loop.cancelled);
    cJSON_Delete(request(&f,"POST","/api/tasks","{\"goal\":\"new-goal-fixture\"}")); CHECK(f.r.status == 202);
    for (size_t i = 0; i < 5; i++) CHECK(!f.mcp.shots[i].jpeg);
    cJSON_Delete(request(&f,"POST","/api/tasks/t2/stop","{}")); CHECK(f.r.status == 200 && f.fake.stopped >= 1 && !f.loop.allowed && f.loop.cancelled && !*f.mcp.task.goal);
    cJSON_Delete(request(&f,"POST","/api/tasks","{\"goal\":\"queued\"}")); CHECK(f.r.status == 202 && strstr(f.r.body,"waiting_allow"));
    f.fake.now += WEB_IDLE_MS; web_tick(&f.web); CHECK(!f.mcp.task.live && !*f.mcp.task.goal); cleanup(&f);
}
static void events(void)
{
    fixture_t f; setup(&f); pair_browser(&f);
    strcpy(f.q.method,"GET"); strcpy(f.q.path,"/api/events"); f.q.body = NULL; f.q.body_len = 0;
    for (uint64_t tag = 1; tag <= 2; tag++) {
        free(f.r.body); memset(&f.r,0,sizeof f.r); f.q.tag = tag;
        CHECK(web_handle(&f.web,&f.q,&f.r) == 2 && f.r.stream && f.r.len < 8192);
        CHECK(strstr(f.r.body,"event: task"));
    }
    free(f.r.body); memset(&f.r,0,sizeof f.r); f.q.tag = 3;
    CHECK(!web_handle(&f.web,&f.q,&f.r) && f.r.status == 503);
    web_disconnect(&f.web,1); free(f.r.body); memset(&f.r,0,sizeof f.r);
    CHECK(web_handle(&f.web,&f.q,&f.r) == 2);
    free(f.r.body); memset(&f.r,0,sizeof f.r);
    CHECK(web_handle(&f.web,&f.q,&f.r) == 1 && !f.r.body);
    f.fake.now += WEB_IDLE_MS; web_tick(&f.web);
    CHECK(*f.web.sessions[0].cookie);
    CHECK(web_handle(&f.web,&f.q,&f.r) == 2); free(f.r.body); memset(&f.r,0,sizeof f.r);
    f.fake.now = f.web.sessions[0].created+WEB_ABSOLUTE_MS;
    CHECK(web_handle(&f.web,&f.q,&f.r) == 3); web_disconnect(&f.web,3);
    cleanup(&f);
}
static void paused_budget(void)
{
    fixture_t f; setup(&f); pair_browser(&f); f.fake.allow = 1; web_tick(&f.web);
    cJSON_Delete(request(&f,"POST","/api/tasks","{\"goal\":\"paused private goal\"}")); CHECK(f.r.status == 202);
    web_tick(&f.web); CHECK(f.loop.state == S_PLANNING);
    cJSON_Delete(request(&f,"POST","/api/tasks/t1/pause","{}")); CHECK(f.loop.paused);
    f.fake.now += f.loop.policy.wall_ms; web_tick(&f.web);
    CHECK(!f.loop.paused && !f.mcp.task.live && !strcmp(f.mcp.task.state,"error") && !*f.mcp.task.goal);
    f.fake.allow = 1; web_tick(&f.web); CHECK(f.loop.allowed);
    action_t a = {.kind=A_WAIT,.ms=1}; strcpy(a.target,"Notes");
    CHECK(!loop_direct(&f.loop,&a,&f.loop.frame)); loop_tick(&f.loop); CHECK(f.fake.executes == 1);
    /* Restart invalidates even a still-valid cookie. */
    char secrets[4096], config[4096]; strcpy(secrets,f.web.secrets); strcpy(config,f.web.config);
    web_free(&f.web); web_init(&f.web,&f.mcp,NULL,"127.0.0.1:8780",secrets,config); f.web.agent = 1; paths(&f);
    cJSON_Delete(request(&f,"GET","/config/net",NULL)); CHECK(f.r.status == 401);
    cleanup(&f);
}
static void queued_stop(void)
{
    fixture_t f; setup(&f); pair_browser(&f);
    cJSON_Delete(request(&f,"POST","/api/tasks","{\"goal\":\"waiting private goal\"}")); CHECK(f.r.status == 202);
    loop_stop(&f.loop); web_tick(&f.web);
    CHECK(!f.mcp.task.live && !*f.mcp.task.goal && !strcmp(f.mcp.task.state,"stopped"));
    f.fake.allow = 1; web_tick(&f.web); CHECK(!f.fake.observes);
    loop_stop(&f.loop);
    cJSON_Delete(request(&f,"POST","/api/tasks","{\"goal\":\"new waiting goal\"}")); CHECK(f.r.status == 202);
    web_tick(&f.web); CHECK(f.mcp.task.live && f.mcp.task.queued && !f.fake.observes);
    f.fake.allow = 1; web_tick(&f.web); CHECK(f.fake.observes == 1);
    CHECK(!mcp_task_cancel(&f.mcp,f.mcp.task.id));
    f.fake.allow = 1; web_tick(&f.web); CHECK(!mcp_task_submit(&f.mcp,"MCP queued goal",NULL));
    loop_stop(&f.loop); web_tick(&f.web); CHECK(!f.mcp.task.live && !*f.mcp.task.goal);
    cleanup(&f);
}
static void send_http(int fd, fixture_t *f, const char *path)
{
    char packet[2048];
    int n = snprintf(packet,sizeof packet,"POST %s HTTP/1.1\r\nHost: %s\r\nCookie: %s\r\nX-CSRF-Token: %s\r\nSec-Fetch-Site: same-origin\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}",path,f->web.host,f->q.cookie,f->q.csrf);
    CHECK(n > 0 && (size_t)n < sizeof packet); CHECK(write(fd,packet,(size_t)n) == n);
}
static void read_http(int fd, char body[4096], int timeout)
{
    size_t used = 0; int64_t end = dns_now_ms()+timeout;
    for (;;) {
        int64_t left = end-dns_now_ms(); CHECK(left > 0);
        struct pollfd p = {fd,POLLIN,0}; CHECK(poll(&p,1,(int)left) > 0);
        ssize_t n = read(fd,body+used,4095-used); CHECK(n > 0); used += (size_t)n; body[used] = 0;
        char *head = strstr(body,"\r\n\r\n"), *length = strstr(body,"Content-Length: ");
        if (head && length && used >= (size_t)(head+4-body)+strtoul(length+16,NULL,10)) return;
        CHECK(used < 4095);
    }
}
static void slow_probe(void)
{
    fixture_t f; setup(&f); pair_browser(&f);
    cJSON_Delete(request(&f,"PUT","/config/net",net_json)); CHECK(f.r.status == 200);
    char provider[8192]; snprintf(provider,sizeof provider,"{\"version\":1,\"provider\":\"qwen\",\"model\":\"qwen-vl-max\",\"base_url\":\"https://dashscope.aliyuncs.com/compatible-mode/v1\",\"domestic\":true,\"route\":\"domestic\",\"api_key_file\":\"%s/qwen.key\"}",f.web.secrets);
    cJSON_Delete(request(&f,"PUT","/config/provider",provider)); CHECK(f.r.status == 200);
    f.fake.tests = -1; strcpy(f.mcp.task.id,"t1"); strcpy(f.mcp.task.state,"error");
    memset(f.mcp.token,'a',64); f.mcp.token[64] = 0;
    int pair[3][2]; for (int i = 0; i < 3; i++) CHECK(!socketpair(AF_UNIX,SOCK_STREAM,0,pair[i]));
    pid_t pid = fork(); CHECK(pid >= 0);
    if (!pid) {
        for (int i = 0; i < 3; i++) close(pair[i][0]);
        int web_fds[] = {pair[0][1],pair[1][1]}, mcp_fd = pair[2][1];
        http_service_t services[] = {
            {-1,web_fds,2,{&f.web,web_handle,web_tick,web_disconnect,web_attach_pump,NULL}},
            {-1,&mcp_fd,1,{&f.mcp,mcp_handle,NULL,mcp_disconnect,mcp_attach_pump,NULL}}
        };
        volatile sig_atomic_t stop = 0;
        int rc = httpd_run_services(services,2,&stop); web_free(&f.web); mcp_free(&f.mcp); _exit(rc ? 1 : 0);
    }
    for (int i = 0; i < 3; i++) close(pair[i][1]);
    send_http(pair[0][0],&f,"/test");
    struct pollfd pending = {pair[0][0],POLLIN,0}; CHECK(poll(&pending,1,100) == 0);
    int64_t start = dns_now_ms(); send_http(pair[1][0],&f,"/api/tasks/t1/stop");
    char body[4096]; read_http(pair[1][0],body,500);
    CHECK(strstr(body,"200 OK") && strstr(body,"\"state\":\"error\"")); CHECK(dns_now_ms()-start < 500);
    char packet[2048]; const char *ping = "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2025-06-18\",\"capabilities\":{},\"clientInfo\":{}}}";
    int n = snprintf(packet,sizeof packet,"POST /mcp HTTP/1.1\r\nHost: %s\r\nAuthorization: Bearer %s\r\nContent-Type: application/json\r\nContent-Length: %zu\r\n\r\n%s",f.mcp.host,f.mcp.token,strlen(ping),ping);
    CHECK(n > 0 && (size_t)n < sizeof packet); CHECK(write(pair[2][0],packet,(size_t)n) == n);
    read_http(pair[2][0],body,500); CHECK(strstr(body,"200 OK") && strstr(body,"protocolVersion"));
    CHECK(poll(&pending,1,0) == 0);
    read_http(pair[0][0],body,2000); CHECK(strstr(body,"200 OK") && strstr(body,"\"ok\":true"));
    for (int i = 0; i < 3; i++) close(pair[i][0]);
    int status; CHECK(waitpid(pid,&status,0) == pid && WIFEXITED(status) && !WEXITSTATUS(status));
    /* An uncooperative child is bounded too; both timeout and disconnect reap. */
    f.fake.tests = -2; start = dns_now_ms();
    cJSON_Delete(request(&f,"POST","/test","{}"));
    CHECK(f.r.status == 200 && strstr(f.r.body,"timeout") && !f.web.probe_tag);
    CHECK(dns_now_ms()-start >= 1450 && dns_now_ms()-start < 2000);
    free(f.r.body); memset(&f.r,0,sizeof f.r); f.q.body = "{}"; f.q.body_len = 2;
    CHECK(web_handle(&f.web,&f.q,&f.r) == 1); pid = f.web.probe_pid;
    web_disconnect(&f.web,f.q.tag); CHECK(!f.web.probe_tag);
    CHECK(waitpid(pid,NULL,WNOHANG) == -1 && errno == ECHILD);
    cleanup(&f);
}
/* Same marker/lock contract as kbd uicfg_apply; strict catalogue on write only. */
static void commit_marker(void)
{
    fixture_t f; setup(&f); pair_browser(&f);
    cJSON_Delete(request(&f,"PUT","/api/provider","{\"provider\":\"qwen\",\"model\":\"qwen-vl-max\"}")); CHECK(f.r.status == 200);
    char want[8192]; snprintf(want,sizeof want,"{\"version\":1,\"provider\":\"qwen\",\"model\":\"qwen-vl-max\",\"base_url\":\"https://dashscope.aliyuncs.com/compatible-mode/v1\",\"domestic\":true,\"route\":\"domestic\",\"api_key_file\":\"%s/qwen.key\"}",f.web.secrets);
    cJSON_Delete(request(&f,"GET","/config/provider",NULL)); CHECK(f.r.status == 200 && !strcmp(f.r.body,want));
    const char *refused[] = {"{\"provider\":\"qwen\",\"model\":\"not-in-catalogue\"}","{\"provider\":\"anthropic\",\"model\":\"claude-sonnet-4.5\"}",
        "{\"provider\":\"nope\",\"model\":\"x\"}","{\"provider\":\"qwen\"}","{\"provider\":\"qwen\",\"model\":\"qwen-vl-max\",\"base_url\":\"https://evil.invalid\"}"};
    for (size_t i = 0; i < sizeof refused/sizeof *refused; i++) { cJSON_Delete(request(&f,"PUT","/api/provider",refused[i])); CHECK(f.r.status == 400); }
    char free_model[8192]; snprintf(free_model,sizeof free_model,"{\"version\":1,\"provider\":\"qwen\",\"model\":\"fake-vl\",\"base_url\":\"https://dashscope.aliyuncs.com/compatible-mode/v1\",\"domestic\":true,\"route\":\"domestic\",\"api_key_file\":\"%s/qwen.key\"}",f.web.secrets);
    cJSON_Delete(request(&f,"PUT","/config/provider",free_model)); CHECK(f.r.status == 400);
    /* An older saved non-catalogue model still loads (no silent loss of a working device config). */
    int dir = web_directory(f.web.config,0); CHECK(dir >= 0); CHECK(!web_atomic(dir,"provider.json",free_model,strlen(free_model)));
    cJSON_Delete(request(&f,"GET","/config/provider",NULL)); CHECK(f.r.status == 200 && strstr(f.r.body,"fake-vl"));
    /* Interrupted commit: readers refuse the pair until a later successful save. */
    int m = openat(dir,".apply-in-progress",O_WRONLY | O_CREAT,0600); CHECK(m >= 0); close(m);
    cJSON_Delete(request(&f,"GET","/config/provider",NULL)); CHECK(f.r.status == 409 && strstr(f.r.body,"apply_incomplete"));
    cJSON_Delete(request(&f,"POST","/test","{}")); CHECK(f.r.status == 200 && strstr(f.r.body,"\"error_class\":\"config\""));
    cJSON_Delete(request(&f,"PUT","/config/net",net_json)); CHECK(f.r.status == 200);
    struct stat st; CHECK(fstatat(dir,".apply-in-progress",&st,AT_SYMLINK_NOFOLLOW) && errno == ENOENT);
    cJSON_Delete(request(&f,"GET","/config/provider",NULL)); CHECK(f.r.status == 200);
    /* Another writer (kbd LS) holding .apply-lock: busy, nothing changed. */
    int ready[2]; CHECK(!pipe(ready)); pid_t pid = fork(); CHECK(pid >= 0);
    if (!pid) {
        int lock = openat(dir,".apply-lock",O_WRONLY | O_CREAT,0600); struct flock fl; memset(&fl,0,sizeof fl); fl.l_type = F_WRLCK; fl.l_whence = SEEK_SET;
        if (lock < 0 || fcntl(lock,F_SETLK,&fl)) _exit(1);
        if (write(ready[1],"r",1) != 1) _exit(1);
        pause(); _exit(0);
    }
    char c; CHECK(read(ready[0],&c,1) == 1);
    cJSON_Delete(request(&f,"PUT","/api/provider","{\"provider\":\"kimi\",\"model\":\"kimi-k2\"}")); CHECK(f.r.status == 409 && strstr(f.r.body,"busy"));
    kill(pid,SIGKILL); waitpid(pid,NULL,0); close(ready[0]); close(ready[1]);
    cJSON_Delete(request(&f,"GET","/config/provider",NULL)); CHECK(f.r.status == 200 && strstr(f.r.body,"fake-vl"));
    cJSON_Delete(request(&f,"PUT","/api/provider","{\"provider\":\"kimi\",\"model\":\"kimi-k2\"}")); CHECK(f.r.status == 200);
    /* Symlinked/hard-linked pair members are refused before any mutation. */
    CHECK(!linkat(dir,"net.json",dir,"net.copy",0));
    cJSON_Delete(request(&f,"PUT","/api/provider","{\"provider\":\"qwen\",\"model\":\"qwen3-max\"}")); CHECK(f.r.status == 500);
    CHECK(fstatat(dir,".apply-in-progress",&st,AT_SYMLINK_NOFOLLOW) && errno == ENOENT);
    CHECK(!unlinkat(dir,"net.copy",0)); close(dir); cleanup(&f);
}
static void catalogue(void)
{
    fixture_t f; setup(&f); pair_browser(&f);
    cJSON *j = request(&f,"GET","/api/catalogue",NULL); CHECK(f.r.status == 200 && j);
    const cJSON *ps = cJSON_GetObjectItemCaseSensitive(j,"providers"); CHECK(cJSON_GetArraySize(ps) == 8);
    const cJSON *qwen = cJSON_GetArrayItem(ps,0); CHECK(!strcmp(cJSON_GetObjectItemCaseSensitive(qwen,"id")->valuestring,"qwen"));
    CHECK(cJSON_GetArraySize(cJSON_GetObjectItemCaseSensitive(qwen,"models")) == 3);
    CHECK(strstr(f.r.body,"\"id\":\"anthropic\"") && strstr(f.r.body,"\"supported\":false") && strstr(f.r.body,"\"vnc\""));
    CHECK(!strstr(f.r.body,"base_url") && !strstr(f.r.body,f.web.secrets));
    const cJSON *hosts = cJSON_GetObjectItemCaseSensitive(j,"mac_hosts"); CHECK(cJSON_GetArraySize(hosts) == 3); cJSON_Delete(j);
    /* A configured ECM_SUBNET: the usb0 peer (.1) joins the single proxy-host rule. */
    snprintf(f.web.host,sizeof f.web.host,"172.30.9.2:8780"); snprintf(f.web.origin,sizeof f.web.origin,"http://172.30.9.2:8780");
    strcpy(f.q.host,f.web.host); strcpy(f.q.origin,f.web.origin);
    j = request(&f,"GET","/api/catalogue",NULL); hosts = cJSON_GetObjectItemCaseSensitive(j,"mac_hosts");
    CHECK(cJSON_GetArraySize(hosts) == 4 && !strcmp(cJSON_GetArrayItem(hosts,0)->valuestring,"172.30.9.1")); cJSON_Delete(j);
    const char *peer = "{\"version\":1,\"profile\":\"mac_proxy\",\"proxy\":{\"url\":\"http://172.30.9.1:7890\",\"fallbacks\":[\"http://172.30.250.1:7890\"]},\"routes\":{\"domestic\":\"proxy\",\"foreign\":\"proxy\"}}";
    cJSON_Delete(request(&f,"PUT","/config/net",peer)); CHECK(f.r.status == 200);
    const char *other = "{\"version\":1,\"profile\":\"mac_proxy\",\"proxy\":{\"url\":\"http://172.30.8.1:7890\",\"fallbacks\":[]},\"routes\":{\"domestic\":\"proxy\",\"foreign\":\"proxy\"}}";
    cJSON_Delete(request(&f,"PUT","/config/net",other)); CHECK(f.r.status == 400);
    cleanup(&f);
}
static void maintenance(void)
{
    fixture_t f; setup(&f);
    cJSON_Delete(request(&f,"POST","/api/maint/restart-kbd","{\"confirm\":\"restart-kbd\"}")); CHECK(f.r.status == 401);
    pair_browser(&f);
    /* Health snapshot with fields that must never reach the page. */
    write_file(f.web.health_path,"{\"version\":\"1.4.0\",\"uptime_s\":5,\"kernel\":{\"p4\":\"abc\"},\"vendor\":\"ok\","
        "\"net\":{\"usb0\":\"172.30.250.2\"},\"kbd\":{\"enabled\":true,\"mode\":\"safe\",\"running\":true,\"restarts\":4,\"recent_crashes\":3,\"degraded\":\"\",\"status\":\"{\\\"hid_open\\\":1,\\\"private\\\":\\\"KBD-STATUS-RAW\\\"}\"},"
        "\"agent\":{\"enabled\":false,\"provider\":\"SECRET-PROVIDER-STATE\"},\"mem_avail_kb\":41000,\"fat_free_kb\":900,\"data_free_kb\":123456,"
        "\"release\":{\"slot\":\"B\",\"good\":\"1.3.1\",\"pending\":\"1.4.0\",\"last_rollback\":\"\\u001b[31m1.3.0->1.2.1 boot\"},"
        "\"config\":{\"src\":\"/data/mk20/config/mk20.conf\",\"warnings\":\"\"},"
        "\"selftest\":{\"pass\":true,\"detail\":\"PASS kd=0 (mk20-kbd mode) PASS mk20-kbd running WARN mk20-kbd safe mode (admin: http://172.30.250.2:8780) INFO agent disabled WARN FAT free 900 KiB\"},\"status\":\"degraded\"}",0600);
    char state[4200]; snprintf(state,sizeof state,"%s/state.json",f.web.maint_dir);
    write_file(state,"{\"mode\":\"safe\",\"restarts\":3,\"recent_crashes\":3,\"last_rc\":139,\"reason\":\"crash-loop\",\"version\":\"1.4.0\",\"extra\":\"STATE-EXTRA\"}",0444);
    write_file(f.web.debug_path,"verbose=1\n",0600);
    cJSON *j = request(&f,"GET","/api/status",NULL); CHECK(f.r.status == 200 && j);
    const char *forbidden[] = {"KBD-STATUS-RAW","SECRET-PROVIDER-STATE","STATE-EXTRA","mk20.conf","p4","secrets","\x1b"};
    for (size_t i = 0; i < sizeof forbidden/sizeof *forbidden; i++) CHECK(!strstr(f.r.body,forbidden[i]));
    CHECK(strstr(f.r.body,"\"uptime_s\":1234") && strstr(f.r.body,"\"usb0_ip\":\"127.0.0.1\"") && strstr(f.r.body,"\"slot\":\"B\"") && strstr(f.r.body,"\"pending\":\"1.4.0\""));
    CHECK(strstr(f.r.body,"\"last_rollback\":\"?[31m1.3.0->1.2.1 boot\"") && strstr(f.r.body,"\"fat_free_kb\":900"));
    CHECK(strstr(f.r.body,"\"pass_count\":2") && strstr(f.r.body,"\"warn_count\":2") && strstr(f.r.body,"\"fail_count\":0"));
    CHECK(strstr(f.r.body,"\"WARN mk20-kbd safe mode (admin: http://172.30.250.2:8780)\"") && strstr(f.r.body,"\"WARN FAT free 900 KiB\""));
    CHECK(strstr(f.r.body,"\"last_rc\":139") && strstr(f.r.body,"\"reason\":\"crash-loop\"") && strstr(f.r.body,"\"debug_verbose\":true") && strstr(f.r.body,"\"pending\":[]"));
    CHECK(strstr(f.r.body,"\"health_state\":\"ok\"") && strstr(f.r.body,"\"state_status\":\"ok\""));
    cJSON_Delete(j);
    /* Forgeable snapshots (group/other-writable, foreign owner) are never parsed: 状态不可用. */
    CHECK(!chmod(f.web.health_path,0666) && !chmod(state,0464) && !chmod(f.web.debug_path,0622));
    j = request(&f,"GET","/api/status",NULL); CHECK(f.r.status == 200 && strstr(f.r.body,"\"health\":false") && strstr(f.r.body,"\"health_state\":\"unavailable\""));
    CHECK(strstr(f.r.body,"\"state\":null") && strstr(f.r.body,"\"state_status\":\"unavailable\"") && strstr(f.r.body,"\"debug_verbose\":null") && !strstr(f.r.body,"1.4.0")); cJSON_Delete(j);
    CHECK(!chmod(f.web.health_path,0600) && !chmod(state,0444) && !chmod(f.web.debug_path,0600));
    if (!geteuid()) {
        CHECK(!chown(f.web.health_path,4321,4321));
        j = request(&f,"GET","/api/status",NULL); CHECK(strstr(f.r.body,"\"health_state\":\"unavailable\"") && !strstr(f.r.body,"\"slot\"")); cJSON_Delete(j);
        CHECK(!chown(f.web.health_path,0,0));
    }
    j = request(&f,"GET","/api/status",NULL); CHECK(strstr(f.r.body,"\"health_state\":\"ok\"") && strstr(f.r.body,"\"slot\":\"B\"")); cJSON_Delete(j);
    /* Each action: CSRF + explicit self-naming confirmation; fixed names and bodies only. */
    const char *bad[][2] = {{"restart-kbd","{}"},{"restart-kbd","{\"confirm\":\"exit-safe-mode\"}"},{"restart-kbd","{\"confirm\":\"restart-kbd\",\"x\":1}"},
        {"debug","{\"confirm\":\"debug\"}"},{"debug","{\"confirm\":\"debug\",\"verbose\":\"1\"}"},{"debug","{\"confirm\":\"debug\",\"verbose\":true,\"keytrace\":true}"}};
    for (size_t i = 0; i < sizeof bad/sizeof *bad; i++) {
        char path[64]; snprintf(path,sizeof path,"/api/maint/%s",bad[i][0]);
        cJSON_Delete(request(&f,"POST",path,bad[i][1])); CHECK(f.r.status == 400 && strstr(f.r.body,"confirm"));
    }
    cJSON_Delete(request(&f,"POST","/api/maint/reboot","{\"confirm\":\"reboot\"}")); CHECK(f.r.status == 404);
    cJSON_Delete(request(&f,"POST","/api/maint/rollback","{\"confirm\":\"rollback\"}")); CHECK(f.r.status == 404);
    cJSON_Delete(request(&f,"GET","/api/maint/restart-kbd",NULL)); CHECK(f.r.status == 405);
    f.q.has_csrf = 0; cJSON_Delete(request(&f,"POST","/api/maint/restart-kbd","{\"confirm\":\"restart-kbd\"}")); CHECK(f.r.status == 403); f.q.has_csrf = 1;
    int dir = web_directory(f.web.maint_dir,0); CHECK(dir >= 0); struct stat st;
    for (size_t i = 0; i < 3; i++) CHECK(fstatat(dir,(const char *[]){"restart-kbd","exit-safe-mode","debug"}[i],&st,AT_SYMLINK_NOFOLLOW) && errno == ENOENT);
    cJSON_Delete(request(&f,"POST","/api/maint/restart-kbd","{\"confirm\":\"restart-kbd\"}")); CHECK(f.r.status == 202 && strstr(f.r.body,"queued"));
    CHECK(!fstatat(dir,"restart-kbd",&st,AT_SYMLINK_NOFOLLOW) && S_ISREG(st.st_mode) && st.st_size == 0 && (st.st_mode & 07777) == 0600 && st.st_nlink == 1);
    cJSON_Delete(request(&f,"POST","/api/maint/exit-safe-mode","{\"confirm\":\"exit-safe-mode\"}")); CHECK(f.r.status == 202);
    cJSON_Delete(request(&f,"POST","/api/maint/debug","{\"confirm\":\"debug\",\"verbose\":false}")); CHECK(f.r.status == 202);
    char b[32] = {0}; int fd = openat(dir,"debug",O_RDONLY); CHECK(fd >= 0 && read(fd,b,sizeof b) == 10 && !strcmp(b,"verbose=0\n")); close(fd);
    cJSON_Delete(request(&f,"POST","/api/maint/debug","{\"confirm\":\"debug\",\"verbose\":true}")); CHECK(f.r.status == 202);
    memset(b,0,sizeof b); fd = openat(dir,"debug",O_RDONLY); CHECK(fd >= 0 && read(fd,b,sizeof b) == 10 && !strcmp(b,"verbose=1\n")); close(fd);
    j = request(&f,"GET","/api/status",NULL); CHECK(strstr(f.r.body,"\"pending\":[\"restart-kbd\",\"exit-safe-mode\",\"debug\"]")); cJSON_Delete(j);
    /* No temporaries are left in the supervisor inbox; a planted symlink target is refused. */
    DIR *d = opendir(f.web.maint_dir); struct dirent *e; int temps = 0; while ((e = readdir(d))) if (!strncmp(e->d_name,".web-",5)) temps++; closedir(d); CHECK(!temps);
    CHECK(!unlinkat(dir,"restart-kbd",0) && !symlinkat("/etc/passwd",dir,"restart-kbd"));
    cJSON_Delete(request(&f,"POST","/api/maint/restart-kbd","{\"confirm\":\"restart-kbd\"}")); CHECK(f.r.status == 500);
    CHECK(!unlinkat(dir,"restart-kbd",0));
    int abandoned = openat(dir,".web-0123456789abcdef.tmp",O_WRONLY | O_CREAT,0600); CHECK(abandoned >= 0); close(abandoned);
    web_clean_temps(f.web.maint_dir); CHECK(fstatat(dir,".web-0123456789abcdef.tmp",&st,0) && errno == ENOENT);
    for (size_t i = 0; i < 3; i++) (void)unlinkat(dir,(const char *[]){"exit-safe-mode","debug","state.json"}[i],0);
    close(dir);
    /* Supervisor inbox missing or not private: unavailable, never created by the web. */
    CHECK(!chmod(f.web.maint_dir,0755));
    cJSON_Delete(request(&f,"POST","/api/maint/restart-kbd","{\"confirm\":\"restart-kbd\"}")); CHECK(f.r.status == 503 && strstr(f.r.body,"maint_unavailable"));
    CHECK(!rmdir(f.web.maint_dir));
    cJSON_Delete(request(&f,"POST","/api/maint/restart-kbd","{\"confirm\":\"restart-kbd\"}")); CHECK(f.r.status == 503);
    CHECK(access(f.web.maint_dir,F_OK) && errno == ENOENT);
    j = request(&f,"GET","/api/status",NULL); CHECK(f.r.status == 200 && strstr(f.r.body,"\"available\":false")); cJSON_Delete(j);
    /* No health snapshot yet: status still answers with live fields. */
    CHECK(!unlink(f.web.health_path)); j = request(&f,"GET","/api/status",NULL); CHECK(f.r.status == 200 && strstr(f.r.body,"\"health\":false") && strstr(f.r.body,"\"uptime_s\":1234")); cJSON_Delete(j);
    cleanup(&f);
}
/* FEATURE_AGENT=0 default: settings/maintenance only, no task API, no keyboard polling. */
static void settings_only(void)
{
    fixture_t f; setup(&f); f.web.agent = 0; pair_browser(&f);
    CHECK(web_idle(&f.web));
    cJSON_Delete(request(&f,"GET","/api/session",NULL)); CHECK(strstr(f.r.body,"\"agent\":false"));
    cJSON_Delete(request(&f,"POST","/api/tasks","{\"goal\":\"never\"}")); CHECK(f.r.status == 404 && strstr(f.r.body,"agent_disabled") && !f.mcp.task.live);
    cJSON_Delete(request(&f,"GET","/api/tasks/t1",NULL)); CHECK(f.r.status == 404 && strstr(f.r.body,"agent_disabled"));
    cJSON_Delete(request(&f,"GET","/api/events",NULL)); CHECK(f.r.status == 404 && strstr(f.r.body,"agent_disabled"));
    int polls = f.fake.polls; for (int i = 0; i < 5; i++) web_tick(&f.web); CHECK(f.fake.polls == polls);
    cJSON_Delete(request(&f,"PUT","/api/provider","{\"provider\":\"deepseek\",\"model\":\"deepseek-chat\"}")); CHECK(f.r.status == 200);
    cJSON_Delete(request(&f,"PUT","/config/net",net_json)); CHECK(f.r.status == 200);
    cJSON_Delete(request(&f,"POST","/test","{}")); CHECK(f.r.status == 200 && strstr(f.r.body,"\"ok\":true"));
    cJSON_Delete(request(&f,"POST","/secret/deepseek","{\"value\":\"fixture-value\"}")); CHECK(f.r.status == 200 && !strcmp(f.r.body,"{\"set\":true}"));
    f.web.agent = 1; CHECK(!web_idle(&f.web));
    cleanup(&f);
}
int main(void) { slow_probe(); secret_nul(); pairing(); pair_limits(); sessions_logout(); pair_publish_failure(); auth(); secrets(); configs(); commit_marker(); catalogue(); maintenance(); settings_only(); glm_coding(); tasks(); events(); paused_budget(); queued_stop(); printf("A7 web: %u checks passed\n",checks); return 0; }
