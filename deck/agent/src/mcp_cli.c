#ifdef __APPLE__
#define _DARWIN_C_SOURCE
#endif
#define _POSIX_C_SOURCE 200809L
#include "mcp.h"
#include "runtime.h"
#include "web_store.h"
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <unistd.h>
#ifdef __linux__
#include <sys/prctl.h>
#endif
static volatile sig_atomic_t stopping;
static void stop_signal(int sig) { (void)sig; stopping = 1; }
static int snapshot(void *v, loop_frame_t *f, loop_guard_t g, void *ctx) { return runtime_snapshot(v,f,g,ctx); }
static int calibrated(void *v, const loop_frame_t *f)
{
    runtime_t *r = v; return r->kbd->calibration.valid && r->kbd->calibration.width == f->width && r->kbd->calibration.height == f->height;
}
static int preflight(void *v, loop_frame_t *f)
{
    runtime_t *r = v; if (!r->window_hint) { f->secure_input = 1; return 0; }
    return r->window_hint(r->hint_ctx,f);
}
static void task_goal(void *v, const char *goal) { ((runtime_t *)v)->task = goal; }
static void sink(void *v, const char *line) { (void)v; fputs(line,stderr); fputc('\n',stderr); }
static int number(const char *s, int max, int *out)
{
    if (!*s) return -1;
    unsigned n = 0; for (; *s; s++) { if (*s < '0' || *s > '9' || n > (unsigned)max/10) return -1; n = n*10+(unsigned)(*s-'0'); if (n > (unsigned)max) return -1; }
    *out = (int)n; return 0;
}
#if defined(MK20_MCP_TEST) || defined(MK20_WEB_TEST)
/* Host test binary only: trusted detector fixture, never a remote parameter. */
static int hint(void *v, loop_frame_t *f) { (void)v; strcpy(f->app,"Notes"); strcpy(f->title,"Notes"); return 0; }
#endif
#ifdef MK20_WEB_TEST
static int model_fds[128]; static size_t model_count, model_next;
static int test_socket(int domain, int type, int protocol)
{
    (void)domain; (void)type; (void)protocol;
    return model_next < model_count ? dup(model_fds[model_next++]) : -1;
}
static int test_connect(int fd, const struct sockaddr *a, socklen_t n) { (void)fd; (void)a; (void)n; return 0; }
#endif
int mcp_cli(int argc, char **argv)
{
    int use_mcp = !strcmp(argv[1],"--mcp"), use_web = !strcmp(argv[1],"--web");
    const char *web_listen = "172.30.250.2:" WEB_PORT, *secrets_dir = "/data/mk20/secrets", *config_dir = "/data/mk20/agent", *ca_file = NULL;
    /* Device files shared with mk20-kbd (pairing display) and mk20d (health, maintenance inbox). */
    const char *pair_file = "/run/mk20/pair-code", *uptime_file = "/proc/uptime", *health_file = "/tmp/mk20/health.json";
    const char *maint_dir = "/tmp/mk20/maint", *debug_file = "/data/mk20/state/maint/debug.conf";
    int kbd_given = 0;
    int web_no_device = 0, web_listener = -1; int web_fds[HTTPD_CONNECTIONS]; size_t web_count = 0;
    const char *listen = "172.30.250.2:8765", *token_path = "/data/mk20/secrets/mcp.token";
    const char *kbd_path = "/run/mk20/kbd.sock", *policy_path = NULL, *calib_path = CALIB_PATH;
    const char *vnc_host = NULL, *password_path = "/data/mk20/secrets/vnc.pass", *provider_path = NULL, *net_path = NULL;
    int fds[HTTPD_CONNECTIONS]; size_t fd_count = 0;
    int no_device = 0, kbd_fd = -1, vnc_fd = -1, port = 5900;
    kbdc_t kbd; memset(&kbd,0,sizeof kbd); kbd.fd = -1;
    rfb_t *rfb = NULL; llm_provider_t provider = {0}; netcfg_t net = {0};
    runtime_t runtime = {0}; mcp_t m; int initialized = 0, rc = 1, listener = -1;
    web_t web; int web_initialized = 0;
    char token[65] = {0}, host[128], web_host[128]; unsigned char pw[8] = {0}; size_t pw_len = 0;
    for (int i = 2; i < argc;) {
        const char *arg = argv[i++];
        if (!strcmp(arg,"--mcp")) { use_mcp = 1; continue; }
        if (!strcmp(arg,"--web")) { use_web = 1; continue; }
        if (!strcmp(arg,"--web-no-bindtodevice")) { web_no_device = 1; continue; }
#ifdef MK20_WEB_TEST
        if (!strcmp(arg,"--web-trust-clock")) { runtime.transport.clock.trust_clock = 1; continue; }
#endif
        if (!strcmp(arg,"--mcp-no-bindtodevice")) { no_device = 1; continue; }
        if (i == argc) goto bad;
        const char *v = argv[i++];
        if (!strcmp(arg,"--mcp-listen")) listen = v;
        else if (!strcmp(arg,"--mcp-token-file")) token_path = v;
        else if (!strcmp(arg,"--web-listen")) web_listen = v;
        else if (!strcmp(arg,"--web-secrets-dir")) secrets_dir = v;
        else if (!strcmp(arg,"--web-config-dir")) config_dir = v;
        else if (!strcmp(arg,"--ca-file")) ca_file = v;
        else if (!strcmp(arg,"--kbd-sock")) { kbd_path = v; kbd_given = 1; }
        else if (!strcmp(arg,"--web-pair-file")) pair_file = v;
        else if (!strcmp(arg,"--web-uptime-file")) uptime_file = v;
        else if (!strcmp(arg,"--web-health-file")) health_file = v;
        else if (!strcmp(arg,"--web-maint-dir")) maint_dir = v;
        else if (!strcmp(arg,"--web-debug-file")) debug_file = v;
        else if (!strcmp(arg,"--policy")) policy_path = v;
        else if (!strcmp(arg,"--calibration")) calib_path = v;
        else if (!strcmp(arg,"--vnc-host")) vnc_host = v;
        else if (!strcmp(arg,"--vnc-port")) { if (number(v,65535,&port) || !port) goto bad; }
        else if (!strcmp(arg,"--password-file")) password_path = v;
        else if (!strcmp(arg,"--provider")) provider_path = v;
        else if (!strcmp(arg,"--net")) net_path = v;
#if defined(MK20_MCP_TEST) || defined(MK20_WEB_TEST)
        else if (!strcmp(arg,"--mcp-connected-fd")) { if (fd_count == HTTPD_CONNECTIONS || number(v,INT_MAX,&fds[fd_count])) goto bad; fd_count++; }
#ifdef MK20_WEB_TEST
        else if (!strcmp(arg,"--web-connected-fd")) { if (web_count == HTTPD_CONNECTIONS || number(v,INT_MAX,&web_fds[web_count])) goto bad; web_count++; }
        else if (!strcmp(arg,"--web-model-fd")) { if (model_count == 128 || number(v,INT_MAX,&model_fds[model_count])) goto bad; model_count++; }
        else if (!strcmp(arg,"--web-schema-fd")) {
            int fd; if (number(v,INT_MAX,&fd)) goto bad;
            cJSON *schema = web_schema(); char *data = cJSON_PrintUnformatted(schema); cJSON_Delete(schema);
            if (!data) goto bad;
            size_t n = strlen(data); int bad_write = write(fd,data,n) != (ssize_t)n; free(data); return bad_write;
        }
#endif
        else if (!strcmp(arg,"--kbd-fd")) { if (number(v,INT_MAX,&kbd_fd)) goto bad; kbd_given = 1; }
        else if (!strcmp(arg,"--vnc-fd")) { if (number(v,INT_MAX,&vnc_fd)) goto bad; }
#endif
        else goto bad;
    }
    /* The web binds the usb0 address mk20d read from gadget.env; the port is fixed. */
    size_t wl = strlen(web_listen);
    if (httpd_address(listen,no_device,host) || httpd_address(web_listen,web_no_device,web_host) ||
        (!web_no_device && (wl < 5 || strcmp(web_listen+wl-5,":" WEB_PORT)))) goto bad;
    if (strlen(secrets_dir) >= 4000 || strlen(config_dir) >= 4000 || fd_count+web_count > HTTPD_CONNECTIONS) goto bad;
    if (strlen(pair_file) >= 4000 || strlen(uptime_file) >= 250 || strlen(health_file) >= 4000 || strlen(maint_dir) >= 4000 || strlen(debug_file) >= 4000) goto bad;
    /* Settings-only web (agent off): no keyboard client, capture or model runtime inputs. */
    int want_kbd = use_mcp || kbd_given;
    if (!want_kbd && (vnc_host || vnc_fd >= 0 || provider_path || net_path)) goto bad;
    /* Disable dumps before loading any secret. */
    struct rlimit core = {0,0}; if (setrlimit(RLIMIT_CORE,&core)) goto bad;
#ifdef __linux__
    if (prctl(PR_SET_DUMPABLE,0)) goto bad;
#endif
    if (use_mcp && httpd_token(token_path,token)) { sink(NULL,"mcp startup: token refused"); goto end; }
    if (provider_path && (!net_path || llm_provider_load(provider_path,&provider) || netcfg_load(net_path,&net))) goto bad;
    if (!provider_path && net_path) goto bad;
    if (want_kbd) {
        if (kbd_fd >= 0 ? kbdc_connected(&kbd,kbd_fd) : kbdc_open(&kbd,kbd_path)) { sink(NULL,"mcp startup: keyboard unavailable"); goto end; }
        (void)calib_load(calib_path,&kbd.calibration);
    }
    if (vnc_host || vnc_fd >= 0) {
        int loopback = vnc_host && (!strcmp(vnc_host,"127.0.0.1") || !strcmp(vnc_host,"localhost"));
        if (!vnc_host || (strcmp(vnc_host,"172.30.250.1") && strcmp(vnc_host,"10.20.0.1") && strcmp(vnc_host,"169.254.7.1") && !((no_device || web_no_device) && loopback))) goto bad;
        int fd = open(password_path,O_RDONLY | O_NONBLOCK | O_NOFOLLOW); struct stat st;
        if (fd < 0) goto bad;
        int invalid = fstat(fd,&st) || !S_ISREG(st.st_mode) || (st.st_mode & 07777) != 0600 || st.st_uid != geteuid() || st.st_size < 1 || st.st_size > 9;
        if (!invalid) { unsigned char b[9]; ssize_t n = read(fd,b,sizeof b); if (n != st.st_size) invalid = 1; else { if (n && b[n-1] == '\n') n--; if (n > 8) invalid = 1; else { pw_len = (size_t)n; memcpy(pw,b,pw_len); } } mem_wipe(b,sizeof b); }
        close(fd); if (invalid) goto bad;
        const char *error; rfb_options_t o = {vnc_host,(unsigned)port,pw,pw_len,5000};
        int failed = vnc_fd >= 0 ? rfb_open_connected(&rfb,&o,vnc_fd,&error) : rfb_open(&rfb,&o,&error);
        mem_wipe(pw,sizeof pw); if (failed) { sink(NULL,"mcp startup: capture unavailable"); goto end; }
    }
    /* Only fixed MCP metadata is logged; direct A5 action records are omitted. */
    runtime.kbd = &kbd; runtime.rfb = rfb;
    if (provider_path) { runtime.provider = &provider; runtime.net = &net; }
#if defined(MK20_MCP_TEST) || defined(MK20_WEB_TEST)
    runtime.window_hint = hint;
#endif
    runtime.transport.ca_file = ca_file;
#ifdef MK20_WEB_TEST
    if (model_count) { runtime.transport.socket_fn = test_socket; runtime.transport.connect_fn = test_connect; }
#endif
    loop_ports_t ports; if (runtime_ports(&runtime,&ports)) goto bad;
    policy_config_t policy; int warning; (void)policy_load(policy_path,&policy,&warning);
    if (warning) sink(NULL,"mcp startup: invalid policy, confirmation defaults active");
    agent_loop_t loop; loop_init(&loop,&policy,&ports);
    mcp_init(&m,&loop,token,host); initialized = 1;
    m.ctx = &runtime; m.snapshot = snapshot; m.calibrated = calibrated; m.preflight = preflight; m.task_goal = task_goal; m.log = sink;
    if (use_web) {
        web_init(&web,&m,&runtime,web_host,secrets_dir,config_dir); web_initialized = 1; web.agent = want_kbd;
        snprintf(web.pair_path,sizeof web.pair_path,"%s",pair_file); snprintf(web.uptime_path,sizeof web.uptime_path,"%s",uptime_file);
        snprintf(web.health_path,sizeof web.health_path,"%s",health_file); snprintf(web.maint_dir,sizeof web.maint_dir,"%s",maint_dir);
        snprintf(web.debug_path,sizeof web.debug_path,"%s",debug_file);
        /* A previous instance's challenge is void; drop crash leftovers of our own temporaries. */
        web_pair_clear(&web); web_clean_temps(secrets_dir); web_clean_temps(config_dir); web_clean_temps(maint_dir);
        if (!web_count) { web_listener = httpd_listen(web_listen,web_no_device); if (web_listener < 0) { fprintf(stderr,"web startup: bind refused errno=%d\n",errno); goto end; } }
    }
    if (use_mcp && !fd_count) {
        listener = httpd_listen(listen,no_device);
        if (listener < 0) { int saved = errno; sink(NULL,"mcp startup: bind refused"); fprintf(stderr,"mcp bind errno=%d\n",saved); goto end; }
    }
    struct sigaction sa; memset(&sa,0,sizeof sa); sa.sa_handler = stop_signal; sigemptyset(&sa.sa_mask);
    if (sigaction(SIGTERM,&sa,NULL) || sigaction(SIGINT,&sa,NULL)) goto bad;
    stopping = 0; http_service_t services[2]; size_t service_count = 0;
    if (use_mcp) { services[service_count++] = (http_service_t){listener,fds,fd_count,{&m,mcp_handle,use_web ? NULL : mcp_tick,mcp_disconnect,mcp_attach_pump,NULL}}; sink(NULL,"mcp ready"); }
    if (use_web) { services[service_count++] = (http_service_t){web_listener,web_fds,web_count,{&web,web_handle,web_tick,web_disconnect,web_attach_pump,use_mcp ? NULL : web_idle}}; sink(NULL,want_kbd ? "web ready" : "web ready (settings only)"); }
    rc = httpd_run_services(services,service_count,&stopping) ? 1 : 0; listener = web_listener = -1;
    if (loop.leased || m.task.live || m.pending) loop_stop(&loop);
    goto end;
bad:
    sink(NULL,"mcp startup: invalid configuration");
end:
    if (listener >= 0) close(listener);
    if (web_listener >= 0) close(web_listener);
    if (web_initialized) web_free(&web);
    if (initialized) mcp_free(&m);
    mem_wipe(token,sizeof token); mem_wipe(pw,sizeof pw);
#ifdef MK20_WEB_TEST
    for (size_t i = 0; i < model_count; i++) close(model_fds[i]);
#endif
    runtime_free(&runtime); rfb_close(rfb); kbdc_close(&kbd); netcfg_free(&net);
    return rc;
}
