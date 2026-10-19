#define _POSIX_C_SOURCE 200809L
#ifdef __APPLE__
#define _DARWIN_C_SOURCE
#endif
#include "rfb.h"
#include "img.h"
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
        fclose(f); return peak;
    }
#endif
    struct rusage r; if (getrusage(RUSAGE_SELF, &r)) return 0;
#ifdef __APPLE__
    return r.ru_maxrss / 1024;
#else
    return r.ru_maxrss;
#endif
}
int main(int argc, char **argv)
{
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
    if (rfb_check_deadline(c)) { error = rfb_error(c); goto bad; }
    if (rc) { error = rc == -2 ? "image_limit" : "jpeg"; goto bad; }
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
