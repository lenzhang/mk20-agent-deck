#define _POSIX_C_SOURCE 200809L
#ifdef __APPLE__
#define _DARWIN_C_SOURCE
#endif
#include "rfb.h"
#include "mcp.h"
#include "loop_test.h"
#include "img.h"
#include "net.h"
#include "llm.h"
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
#include "cJSON.h"
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>

static int number(const char *s, unsigned max, unsigned *out)
{
    if (!*s) return -1;
    for (const char *p = s; *p; p++) if (*p < '0' || *p > '9') return -1;
    char *end; errno = 0; unsigned long n = strtoul(s, &end, 10);
    if (errno || *end || !n || n > max) return -1;
    *out = (unsigned)n; return 0;
}
static int password(const char *path, unsigned char pw[8], size_t *n)
{
    unsigned char b[4097]; size_t len = 0; int bad = 0;
    if (path) {
        FILE *f = fopen(path, "rb"); if (!f) return -1;
        len = fread(b, 1, sizeof b, f); bad = ferror(f) || len == sizeof b; if (fclose(f)) bad = 1;
        if (len && b[len - 1] == '\n') len--;
    } else {
        const char *env = getenv("MK20_VNC_PASSWORD");
        if (env) { while (len < sizeof b && env[len]) len++; if (len == sizeof b) bad = 1; else memcpy(b, env, len); }
    }
    if (!bad) { *n = len < 8 ? len : 8; memcpy(pw, b, *n); }
    mem_wipe(b, sizeof b); return bad ? -1 : 0;
}
static long rss_kb(void)
{
#ifdef __linux__
    /* Unlike ru_maxrss, this per-mm high-water mark resets across exec. */
    FILE *f = fopen("/proc/self/status", "r");
    if (f) {
        char line[256]; long peak = 0;
        while (fgets(line, sizeof line, f)) if (sscanf(line, "VmHWM: %ld kB", &peak) == 1) break;
        fclose(f); if (peak > 0) return peak;
    }
#endif
    struct rusage r; if (getrusage(RUSAGE_SELF, &r)) return 0;
#ifdef __APPLE__
    return r.ru_maxrss / 1024;
#else
    return r.ru_maxrss;
#endif
}
static int network_cli(int argc, char **argv)
{
    net_options_t o = {0}; netcfg_t config = {0}; net_response_t response = {0};
    const char *path = "/data/mk20/agent/net.json", *mac = NULL;
    unsigned port = 80, timeout = 60, cap = NET_BODY_MAX;
    int sync = !strcmp(argv[1], "--time-sync"), rc = -1;
    o.provider_route = "foreign"; o.config = &config;
#ifdef MK20_NET_TEST
    o.connected_fd = -1;
    const char *fd = getenv("MK20_NET_TEST_FD"); if (fd) o.connected_fd = atoi(fd);
#endif
    int i = 2;
    if (!sync) { if (i == argc) goto bad; o.url = argv[i++]; }
    while (i < argc) {
        const char *arg = argv[i++];
        if (!strcmp(arg, "--trust-clock") && !sync) { o.clock.trust_clock = 1; continue; }
        if (!strcmp(arg, "--set-clock") && sync) { o.clock.set_clock = 1; o.clock.setter = agent_clock_system_set; continue; }
        if (i == argc) goto bad;
        const char *v = argv[i++];
        if (!strcmp(arg, "--net") && !sync) path = v;
        else if (!strcmp(arg, "--route") && !sync) o.provider_route = v;
        else if (!strcmp(arg, "--key-file") && !sync) o.key_file = v;
        else if (!strcmp(arg, "--ca") && !sync) o.ca_file = v;
        else if (!strcmp(arg, "--timeout") && !sync) { if (number(v, 600, &timeout)) goto bad; }
        else if (!strcmp(arg, "--body-cap") && !sync) { if (number(v, NET_BODY_MAX, &cap)) goto bad; }
        else if (!strcmp(arg, "--mac-host") && sync) mac = v;
        else if (!strcmp(arg, "--port") && sync) { if (number(v, 65535, &port)) goto bad; }
        else if (!strcmp(arg, "--clock-marker")) o.clock.marker_path = v;
        else goto bad;
    }
    if (sync) { if (!mac) goto bad; rc = net_time_sync(mac, port, &o.clock, &response); }
    else {
        if (netcfg_load(path, &config)) goto bad;
        o.timeout_ms = (int)timeout * 1000; o.body_cap = cap;
        rc = net_request(&o, &response);
    }
    goto output;
bad:
    response.class_name = "config"; strcpy(response.detail, "invalid arguments or network configuration");
output:;
    /* Peer headers/body, URLs, paths and keys are never included in diagnostics. */
    cJSON *j = cJSON_CreateObject();
    if (!j) { net_response_free(&response); netcfg_free(&config); puts("{\"ok\":false,\"class\":\"io\",\"detail\":\"allocation failed\",\"http_status\":0,\"bytes\":0,\"via\":\"\"}"); return 1; }
    cJSON_AddBoolToObject(j, "ok", rc == 0);
    cJSON_AddStringToObject(j, "class", response.class_name ? response.class_name : "config");
    cJSON_AddStringToObject(j, "detail", response.detail);
    cJSON_AddNumberToObject(j, "http_status", response.http_status);
    cJSON_AddNumberToObject(j, "bytes", (double)response.bytes);
    cJSON_AddStringToObject(j, "via", response.via);
    cJSON_AddNumberToObject(j, "peak_rss_kb", (double)rss_kb());
    char *json = cJSON_PrintUnformatted(j);
    if (json) { puts(json); free(json); } else puts("{\"ok\":false,\"class\":\"io\"}");
    cJSON_Delete(j); net_response_free(&response); netcfg_free(&config); return rc == 0 ? 0 : 1;
}
/* Bounded regular files: no FIFO/device can bypass a request deadline. */
static unsigned char *input_file(const char *path, size_t cap, size_t *len)
{
    int fd = open(path, O_RDONLY | O_NONBLOCK); if (fd < 0) return NULL;
    struct stat st;
    if (fstat(fd, &st)) { close(fd); return NULL; }
    if (!S_ISREG(st.st_mode) || st.st_size < 1) { close(fd); errno = EINVAL; return NULL; }
    if ((uintmax_t)st.st_size > cap) { close(fd); errno = EFBIG; return NULL; }
    unsigned char *b = malloc(cap + 1); if (!b) { close(fd); return NULL; }
    size_t n = 0; int bad = 0;
    while (n <= cap) {
        ssize_t k = read(fd, b + n, cap + 1 - n);
        if (k < 0 && errno == EINTR) continue;
        if (k < 0) { bad = 1; break; }
        if (!k) break;
        n += (size_t)k;
    }
    if (close(fd)) bad = 1;
    if (bad || n > cap) { free(b); if (n > cap) errno = EFBIG; return NULL; }
    b[n] = 0; *len = n; return b;
}
static int llm_cli(int argc, char **argv)
{
    const char *provider = NULL, *net = NULL, *image = NULL, *tools = NULL;
    llm_provider_t cfg = {0}; netcfg_t nc = {0}; llm_response_t r = {0};
    llm_request_t q = {0}; net_options_t transport = {0};
    unsigned w = 0, h = 0, timeout = 0;
    unsigned char *jpeg = NULL, *schema = NULL; size_t schema_len = 0;
    int64_t start = rfb_now_ms(); int rc = -1;
#ifdef MK20_NET_TEST
    transport.connected_fd = -1;
    const char *fd = getenv("MK20_NET_TEST_FD"); if (fd) transport.connected_fd = atoi(fd);
#endif
    for (int i = 2; i < argc;) {
        const char *arg = argv[i++];
        if (!strcmp(arg, "--trust-clock")) { transport.clock.trust_clock = 1; continue; }
        if (i == argc) goto bad;
        const char *v = argv[i++];
        if (!strcmp(arg, "--provider")) provider = v;
        else if (!strcmp(arg, "--net")) net = v;
        else if (!strcmp(arg, "--image")) image = v;
        else if (!strcmp(arg, "--tools")) tools = v;
        else if (!strcmp(arg, "--system")) q.system = v;
        else if (!strcmp(arg, "--user")) q.user = v;
        else if (!strcmp(arg, "--ca")) transport.ca_file = v;
        else if (!strcmp(arg, "--image-w")) { if (number(v, 1000000, &w)) goto bad; }
        else if (!strcmp(arg, "--image-h")) { if (number(v, 1000000, &h)) goto bad; }
        else if (!strcmp(arg, "--timeout")) { if (number(v, 600000, &timeout)) goto bad; }
        else goto bad;
    }
    if (!q.user || !provider || !net || (image && (!w || !h)) ||
        llm_provider_load(provider, &cfg) || netcfg_load(net, &nc)) goto bad;
    if (image) {
        jpeg = input_file(image, OAI_IMAGE_MAX, &q.jpeg_len);
        if (!jpeg) {
            if (errno == EFBIG) { r.error = LLM_TOO_LARGE; r.reason = "image_cap"; goto output; }
            goto bad;
        }
        q.jpeg = jpeg;
    }
    if (tools) {
        schema = input_file(tools, OAI_SCHEMA_MAX, &schema_len);
        if (!schema || memchr(schema, 0, schema_len)) goto bad;
        q.tools_json = (const char *)schema;
    }
    q.image_w = (int)w; q.image_h = (int)h;
    transport.timeout_ms = (int)timeout; q.transport = &transport;
    rc = llm_chat(&cfg, &nc, &q, &r); goto output;
bad:
    r.error = LLM_CONFIG; r.reason = "arguments";
output:;
    cJSON *j = cJSON_CreateObject(), *calls = cJSON_CreateArray();
    if (j && calls) {
        cJSON_AddStringToObject(j, "class", llm_error_name(r.error));
        cJSON_AddStringToObject(j, "error", r.reason ? r.reason : "config");
        cJSON_AddBoolToObject(j, "retryable", r.retryable);
        cJSON_AddNumberToObject(j, "http_status", r.http_status);
        cJSON_AddStringToObject(j, "finish_reason", r.message.finish_reason);
        /* Truncate at 2000 Unicode characters, preserving UTF-8 boundaries. */
        size_t at = 0, chars = 0;
        while (r.message.content[at]) {
            if (((unsigned char)r.message.content[at] & 0xc0) != 0x80 && chars++ == 2000) break;
            at++;
        }
        r.message.content[at] = 0;
        cJSON_AddStringToObject(j, "content", r.message.content);
        for (size_t i = 0; i < r.message.ncalls; i++) {
            oai_tool_call_t *call = &r.message.calls[i]; cJSON *entry = cJSON_CreateObject();
            cJSON_AddStringToObject(entry, "name", call->name);
            cJSON_AddItemToObject(entry, "args", cJSON_Duplicate(call->arguments, 1));
            cJSON_AddItemToArray(calls, entry);
        }
        cJSON_AddItemToObject(j, "calls", calls); calls = NULL;
        cJSON_AddNumberToObject(j, "elapsed_ms", (double)(rfb_now_ms() - start));
        cJSON_AddNumberToObject(j, "peak_rss_kb", (double)rss_kb());
        char *json = cJSON_PrintUnformatted(j);
        if (json) { puts(json); cJSON_free(json); }
        else { puts("{\"class\":\"config\",\"error\":\"allocation\"}"); rc = -1; }
    } else { puts("{\"class\":\"config\",\"error\":\"allocation\"}"); rc = -1; }
    cJSON_Delete(calls); cJSON_Delete(j); free(jpeg); free(schema);
    llm_response_free(&r); netcfg_free(&nc); return rc == 0 ? 0 : 1;
}
int main(int argc, char **argv)
{
    if (argc > 1 && (!strcmp(argv[1], "--mcp") || !strcmp(argv[1], "--web"))) return mcp_cli(argc,argv);
    if (argc > 1 && !strcmp(argv[1], "--loop-test")) return loop_test_cli(argc, argv);
    if (argc > 1 && !strcmp(argv[1], "--llm-test")) return llm_cli(argc, argv);
    if (argc > 1 && (!strcmp(argv[1], "--net-test") || !strcmp(argv[1], "--time-sync"))) return network_cli(argc, argv);
    const char *out = NULL, *host = "127.0.0.1", *passfile = NULL, *error = "bad_args";
    unsigned port = 5900, mw = 1280, mh = 800, quality = 70, timeout = 5000;
    rfb_t *c = NULL; unsigned char pw[8] = {0}, *jpeg = NULL; size_t pn = 0, bytes = 0;
    int64_t start = rfb_now_ms();
    for (int i = 1; i < argc; i += 2) {
        if (i + 1 == argc) goto bad;
        const char *key = argv[i], *v = argv[i + 1];
        if (!strcmp(key, "--snap")) out = v;
        else if (!strcmp(key, "--host")) host = v;
        else if (!strcmp(key, "--password-file")) passfile = v;
        else if (!strcmp(key, "--port")) { if (number(v, 65535, &port)) goto bad; }
        else if (!strcmp(key, "--max-w")) { if (number(v, 8192, &mw)) goto bad; }
        else if (!strcmp(key, "--max-h")) { if (number(v, 8192, &mh)) goto bad; }
        else if (!strcmp(key, "--quality")) { if (number(v, 100, &quality)) goto bad; }
        else if (!strcmp(key, "--timeout-ms")) { if (number(v, 600000, &timeout)) goto bad; }
        else goto bad;
    }
    if (!out || !*out) goto bad;
    if (password(passfile, pw, &pn)) { error = "password_file"; goto bad; }
    rfb_options_t o = {host, port, pw, pn, (int)timeout};
    int rc;
#ifdef MK20_RFB_TEST
    /* Only explicit test builds accept an inherited socketpair; production has no fd/env override. */
    const char *test_fd = getenv("MK20_RFB_TEST_FD");
    if (test_fd) rc = rfb_open_connected(&c, &o, atoi(test_fd), &error);
    else
#endif
    rc = rfb_open(&c, &o, &error);
    mem_wipe(pw, sizeof pw); if (rc) goto bad;
    if (rfb_snapshot(c, mw, mh)) { error = rfb_error(c); goto bad; }
    const scale_t *s = rfb_frame(c); int64_t enc = rfb_now_ms();
    if (rfb_check_deadline(c)) { error = rfb_error(c); goto bad; }
    rc = img_jpeg(rfb_memory(c), s->data, s->w, s->h, (int)quality, &jpeg, &bytes);
    if (rc) { error = rc == -2 ? "image_limit" : "jpeg"; goto bad; }
    if (rfb_check_deadline(c)) { error = rfb_error(c); goto bad; }
    double enc_ms = (double)(rfb_now_ms() - enc) + rfb_info(c)->finish_ms;
    FILE *f = fopen(out, "wb"); if (!f) { error = "output_file"; goto bad; }
    int write_bad = fwrite(jpeg, 1, bytes, f) != bytes; if (fclose(f)) write_bad = 1;
    if (write_bad) { error = "output_file"; goto bad; }
    const rfb_info_t *info = rfb_info(c);
    printf("{\"ok\":true,\"src_w\":%u,\"src_h\":%u,\"w\":%u,\"h\":%u,\"jpeg_bytes\":%zu,"
           "\"peak_rss_kb\":%ld,\"heap_hw_bytes\":%zu,\"extended_clipboard\":%s,"
           "\"ms\":{\"connect\":%.0f,\"auth\":%.0f,\"fb\":%.0f,\"scale_enc\":%.0f,\"total\":%lld}}\n",
           info->w, info->h, s->w, s->h, bytes, rss_kb(), rfb_memory(c)->high, info->extended_clipboard ? "true" : "false",
           info->connect_ms, info->auth_ms, info->fb_ms, enc_ms, (long long)(rfb_now_ms() - start));
    mem_free(rfb_memory(c), jpeg); rfb_close(c); return 0;
bad:
    mem_wipe(pw, sizeof pw);
    printf("{\"ok\":false,\"error\":\"%s\"}\n", error);
    if (c) mem_free(rfb_memory(c), jpeg);
    rfb_close(c); return 1;
}
