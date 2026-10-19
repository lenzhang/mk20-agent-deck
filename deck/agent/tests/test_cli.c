/* Exercise the real CLI control flow with deterministic CPU/deadline failures. */
#define _POSIX_C_SOURCE 200809L
#ifdef __APPLE__
#define _DARWIN_C_SOURCE
#endif
#include "rfb.h"
#include "img.h"
#include "net.h"
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>

static int jpeg_rc, deadline_calls;
static char output[2048];
static FILE *status_file;
static FILE *mock_fopen(const char *p, const char *mode)
{
    if (!strcmp(p, "/proc/self/status")) return status_file;
    return fopen(p, mode);
}
static int mock_usage(int who, struct rusage *r)
{
    (void)who; memset(r, 0, sizeof *r);
#ifdef __APPLE__
    r->ru_maxrss = 7654 * 1024;
#else
    r->ru_maxrss = 7654;
#endif
    return 0;
}
static int mock_print(const char *fmt, ...)
{
    va_list ap; va_start(ap, fmt); int n = vsnprintf(output, sizeof output, fmt, ap); va_end(ap); return n;
}
static int mock_open(rfb_t **c, const rfb_options_t *o, const char **error)
{
    (void)o; (void)error; static int handle; *c = (rfb_t *)&handle; return 0;
}
static int mock_snapshot(rfb_t *c, unsigned w, unsigned h) { (void)c; (void)w; (void)h; return 0; }
static const scale_t *mock_frame(const rfb_t *c) { (void)c; static scale_t s; return &s; }
static int64_t mock_now(void) { return 0; }
static int mock_deadline(rfb_t *c) { (void)c; return ++deadline_calls >= 2; }
static const char *mock_error(const rfb_t *c) { (void)c; return "timeout"; }
static mem_t *mock_memory(rfb_t *c) { (void)c; static mem_t m; return &m; }
static const rfb_info_t *mock_info(const rfb_t *c) { (void)c; static rfb_info_t info; return &info; }
static void mock_close(rfb_t *c) { (void)c; }
static int mock_jpeg(mem_t *m, const unsigned char *rgb, unsigned w, unsigned h, int q, unsigned char **out, size_t *n)
{
    (void)m; (void)rgb; (void)w; (void)h; (void)q; (void)out; (void)n; return jpeg_rc;
}
/* Test the Linux procfs branch even on macOS, with unit-normalized getrusage. */
#ifndef __linux__
#define __linux__
#endif
#define fopen mock_fopen
#define getrusage mock_usage
#define printf mock_print
#define rfb_open mock_open
#define rfb_snapshot mock_snapshot
#define rfb_frame mock_frame
#define rfb_now_ms mock_now
#define rfb_check_deadline mock_deadline
#define rfb_error mock_error
#define rfb_memory mock_memory
#define rfb_info mock_info
#define rfb_close mock_close
#define img_jpeg mock_jpeg
#define main agent_main
#include "../src/agent_cli.c"
#undef main
#undef printf
int main(void)
{
    status_file = tmpfile(); assert(status_file); fputs("Name: test\nVmRSS: 99 kB\n", status_file); rewind(status_file);
    assert(rss_kb() == 7654); /* opened procfs, missing VmHWM -> getrusage */
    status_file = tmpfile(); assert(status_file); fputs("VmHWM: 1234 kB\n", status_file); rewind(status_file);
    assert(rss_kb() == 1234);
    char *args[] = {"test", "--snap", "/tmp/not-written", NULL};
    const int codes[] = {-2, -1, 0}; const char *errors[] = {"image_limit", "jpeg", "timeout"};
    for (unsigned i = 0; i < 3; i++) {
        jpeg_rc = codes[i]; deadline_calls = 0;
        assert(agent_main(3, args) == 1);
        assert(strstr(output, errors[i]));
        assert(deadline_calls == (codes[i] ? 1 : 2));
    }
    puts("CLI RSS fallback and JPEG/deadline precedence checks passed"); return 0;
}
