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
typedef struct { int64_t now; int displays, unavailable, allow, stopped, observes, models, executes, tests; char code[7]; } fake_t;
typedef struct { fake_t fake; agent_loop_t loop; mcp_t mcp; web_t web; http_request_t q; http_response_t r; char root[4096]; } fixture_t;
static int64_t now(void *v) { return ((fake_t *)v)->now; }
static int display(void *v, const char code[7]) { fake_t *f = v; f->displays++; memcpy(f->code,code,7); return f->unavailable ? -1 : 0; }
static int poll_event(void *v, kbd_event_t *e) { fake_t *f = v; if (!f->allow) return 0; f->allow = 0; *e = (kbd_event_t){.kind=K_ALLOW}; return 1; }
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
static void setup(fixture_t *f)
{
    memset(f,0,sizeof *f); f->fake.now = INT64_C(5000000000);
    char path[] = "/tmp/a7-unit-XXXXXX"; CHECK(mkdtemp(path)); CHECK(realpath(path,f->root));
    char secrets[4096], config[4096]; CHECK(strlen(f->root) < 4000);
    snprintf(secrets,sizeof secrets,"%.4000s/secrets",f->root); snprintf(config,sizeof config,"%.4000s/config",f->root);
    loop_ports_t ports = {.ctx=&f->fake,.now=now,.poll=poll_event,.lease=lease,.observe=observe,.model=model,.execute=execute};
    policy_config_t p; policy_defaults(&p); p.confirm_first = p.confirm_app_switch = 0;
    loop_init(&f->loop,&p,&ports); char token[65] = {0}; mcp_init(&f->mcp,&f->loop,token,"127.0.0.1:8765");
    web_init(&f->web,&f->mcp,NULL,"127.0.0.1:8780",secrets,config); f->web.display = display; f->web.display_ctx = &f->fake;
    f->web.connection_test = test_connection; f->web.test_ctx = &f->fake;
    strcpy(f->q.host,f->web.host); strcpy(f->q.content_type,"application/json"); f->q.has_origin = 1; strcpy(f->q.origin,"http://172.30.250.2:8780"); f->q.tag = 1;
}
static void cleanup(fixture_t *f)
{
    free(f->r.body); web_free(&f->web); mcp_free(&f->mcp);
    /* All test-created files have fixed names; no shell/path expansion. */
    const char *dirs[] = {f->web.secrets,f->web.config,f->root};
    for (size_t i = 0; i < 3; i++) {
        DIR *d = opendir(dirs[i]); if (!d) continue;
        int dir = dirfd(d); struct dirent *e;
        while ((e = readdir(d))) if (strcmp(e->d_name,".") && strcmp(e->d_name,"..")) (void)unlinkat(dir,e->d_name,0);
        closedir(d); CHECK(!rmdir(dirs[i]));
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
static void pair_browser(fixture_t *f)
{
    cJSON_Delete(request(f,"POST","/pair/start","{}")); CHECK(f->r.status == 200);
    CHECK(strlen(f->fake.code) == 6); for (size_t i = 0; i < 6; i++) CHECK(f->fake.code[i] >= '0' && f->fake.code[i] <= '9');
    char body[64]; snprintf(body,sizeof body,"{\"code\":\"%s\"}",f->fake.code);
    cJSON *j = request(f,"POST","/pair",body); CHECK(f->r.status == 200 && j);
    CHECK(strstr(f->r.cookie,"HttpOnly; SameSite=Strict; Path=/") && !strstr(f->r.cookie,"Secure"));
    memcpy(f->q.cookie,f->r.cookie,37); f->q.cookie[37] = 0; f->q.has_cookie = 1;
    strcpy(f->q.csrf,cJSON_GetObjectItemCaseSensitive(j,"csrf")->valuestring); f->q.has_csrf = 1; cJSON_Delete(j);
    CHECK(!f->web.pairing && !*f->web.code);
}
static void pairing(void)
{
    fixture_t f; setup(&f); f.fake.unavailable = 1;
    cJSON_Delete(request(&f,"POST","/pair/start","{}")); CHECK(f.r.status == 503 && strstr(f.r.body,"display_unavailable"));
    CHECK(!f.web.pairing && !*f.web.code);
    char code[64]; snprintf(code,sizeof code,"{\"code\":\"%s\"}",f.fake.code);
    cJSON_Delete(request(&f,"POST","/pair",code)); CHECK(f.r.status == 401);
    f.fake.unavailable = 0; cJSON_Delete(request(&f,"POST","/pair/start","{}")); CHECK(f.web.pairing);
    snprintf(code,sizeof code,"{\"code\":\"%s\"}",f.fake.code);
    f.fake.now += WEB_PAIR_MS; cJSON_Delete(request(&f,"POST","/pair",code)); CHECK(f.r.status == 401 && !f.web.pairing);
    cJSON_Delete(request(&f,"POST","/pair/start","{}"));
    int displays = f.fake.displays; cJSON_Delete(request(&f,"POST","/pair/start","{}")); CHECK(f.fake.displays == displays);
    char wrong[7]; strcpy(wrong,f.fake.code); wrong[0] = wrong[0] == '0' ? '1' : '0';
    snprintf(code,sizeof code,"{\"code\":\"%s\"}",wrong);
    for (int i = 0; i < 5; i++) { cJSON_Delete(request(&f,"POST","/pair",code)); CHECK(f.r.status == 401); }
    CHECK(!f.web.pairing && !*f.web.code); pair_browser(&f);
    /* One-time code cannot establish another session. */
    snprintf(code,sizeof code,"{\"code\":\"%s\"}",f.fake.code); cJSON_Delete(request(&f,"POST","/pair",code)); CHECK(f.r.status == 401);
    pair_browser(&f); cJSON_Delete(request(&f,"POST","/pair/start","{}"));
    snprintf(code,sizeof code,"{\"code\":\"%s\"}",f.fake.code); cJSON_Delete(request(&f,"POST","/pair",code)); CHECK(f.r.status == 503);
    f.fake.now += WEB_IDLE_MS; web_tick(&f.web);
    CHECK(!*f.web.sessions[0].cookie && !*f.web.sessions[1].cookie); cleanup(&f);
}
static void auth(void)
{
    fixture_t f; setup(&f);
    const char *paths[] = {"/secret/qwen","/config/net","/test","/api/tasks","/api/events","/unknown","/web.js"};
    for (size_t i = 0; i < sizeof paths/sizeof *paths; i++) { cJSON_Delete(request(&f,"GET",paths[i],NULL)); CHECK(f.r.status == 401 && !strcmp(f.r.body,"{\"error\":\"unauthorized\"}")); }
    cJSON_Delete(request(&f,"GET","/",NULL)); CHECK(f.r.status == 200 && f.r.len >= 9000 && f.r.csp && strstr(f.r.csp,"sha256-"));
    cJSON_Delete(request(&f,"GET","/chat",NULL)); CHECK(f.r.status == 200 && f.r.len >= 10000);
    pair_browser(&f);
    f.q.has_csrf = 0; cJSON_Delete(request(&f,"POST","/secret/qwen","{\"value\":\"fixture-value\"}")); CHECK(f.r.status == 403);
    f.q.has_csrf = 1; char saved = f.q.csrf[0]; f.q.csrf[0] ^= 1;
    cJSON_Delete(request(&f,"POST","/secret/qwen","{}")); CHECK(f.r.status == 403); f.q.csrf[0] = saved;
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
    cJSON_Delete(request(&f,"POST","/secret/qwen","{\"value\":\"fixture-value\"}")); CHECK(f.r.status == 200 && !strcmp(f.r.body,"{\"set\":true,\"len\":13}"));
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
    cJSON_Delete(request(&f,"POST","/secret/vnc",large)); CHECK(f.r.status == 200);
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
    web_free(&f.web); web_init(&f.web,&f.mcp,NULL,"127.0.0.1:8780",secrets,config);
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
            {-1,web_fds,2,{&f.web,web_handle,web_tick,web_disconnect,web_attach_pump}},
            {-1,&mcp_fd,1,{&f.mcp,mcp_handle,NULL,mcp_disconnect,mcp_attach_pump}}
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
int main(void) { slow_probe(); secret_nul(); pairing(); auth(); secrets(); configs(); glm_coding(); tasks(); events(); paused_budget(); queued_stop(); printf("A7 web: %u checks passed\n",checks); return 0; }
