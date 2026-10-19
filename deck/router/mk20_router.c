/* mk20-router: on-board key router skeleton (T113, armhf, static).  See ../ONBOARD-ROUTER-PLAN.md.
 *
 * Input : Protocol A byte stream (KeyboardDevice -> host direction), from a tap socket fed by
 *         ttygs_tcp_bridge (--src unix:PATH) or a file/stdin (--src file:PATH | -) for tests.
 *         Frames: A1 A5 5A 5E | id | cmd | size | crc32(size LE) | data | crc32(data); cmd 101 = JSON.
 *         We only act on {"method":"keyStateChanged", ... "col":C,"row":R,"pressed":B}.
 * Output: HID keyboard reports (report id 1, 9 bytes, from the g1 composite) written to --hid PATH
 *         (/dev/hidg0 on device).  Phase-1 routing: PTT key -> Right Alt/Option held while pressed;
 *         every other key -> "local" (no HID).  Knob codes (col==row>=100) are local.
 * Trace : MK20_KEYTRACE=1 -> "<CLOCK_MONOTONIC ns> <recv|routed|hid_written|local> <col,row> <0|1>"
 *         to MK20_KEYTRACE_PATH (default /tmp/mk20/keytrace.log), rotated to .1 at MK20_KEYTRACE_MAX.
 * Safety: never opens ttyS1 / GD32, never writes outside --hid and the trace file.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#define CMD_JSON 101
#define MAX_PAYLOAD (1u << 20)
#define MOD_RALT 0x40

/* ---------------------------------------------------------------- crc32 (zlib) */
static uint32_t crc_tab[256];
static void crc_init(void) {
    for (uint32_t i = 0; i < 256; i++) {
        uint32_t c = i;
        for (int k = 0; k < 8; k++) c = (c & 1) ? 0xEDB88320u ^ (c >> 1) : c >> 1;
        crc_tab[i] = c;
    }
}
static uint32_t crc32b(const uint8_t *p, size_t n) {
    uint32_t c = 0xFFFFFFFFu;
    while (n--) c = crc_tab[(c ^ *p++) & 0xFF] ^ (c >> 8);
    return c ^ 0xFFFFFFFFu;
}
static uint32_t rd32(const uint8_t *p) { return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }

/* ---------------------------------------------------------------- keytrace */
static FILE *tr_f;
static char tr_path[256];
static long tr_max = 256 * 1024, tr_size;
static uint64_t now_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + ts.tv_nsec;
}
static void trace_init(void) {
    const char *e = getenv("MK20_KEYTRACE");
    if (!e || strcmp(e, "1")) return;
    const char *p = getenv("MK20_KEYTRACE_PATH");
    snprintf(tr_path, sizeof tr_path, "%s", p ? p : "/tmp/mk20/keytrace.log");
    if (getenv("MK20_KEYTRACE_MAX")) tr_max = atol(getenv("MK20_KEYTRACE_MAX"));
    tr_f = fopen(tr_path, "a");
    if (tr_f) { setvbuf(tr_f, NULL, _IOLBF, 0); tr_size = ftell(tr_f); }
}
static void trace(const char *stage, int col, int row, int pressed) {
    if (!tr_f) return; /* zero cost when off */
    char line[96];
    int n = snprintf(line, sizeof line, "%llu %s %d,%d %d\n", (unsigned long long)now_ns(), stage, col, row, pressed);
    if (tr_size + n > tr_max) {
        char old[264];
        fclose(tr_f);
        snprintf(old, sizeof old, "%s.1", tr_path);
        rename(tr_path, old);
        tr_f = fopen(tr_path, "a");
        if (!tr_f) return;
        setvbuf(tr_f, NULL, _IOLBF, 0);
        tr_size = 0;
    }
    fputs(line, tr_f);
    tr_size += n;
}

/* ---------------------------------------------------------------- minimal JSON field scan */
static int json_int(const char *s, size_t n, const char *key, long *out) {
    char pat[32];
    int pl = snprintf(pat, sizeof pat, "\"%s\"", key);
    for (const char *p = s; p && (size_t)(p - s) + pl < n; p++) {
        p = memmem(p, n - (p - s), pat, pl);
        if (!p) return 0;
        const char *q = p + pl;
        while ((size_t)(q - s) < n && (*q == ' ' || *q == ':')) q++;
        if ((size_t)(q - s) >= n) return 0;
        if (!strncmp(q, "true", 4) && (size_t)(q - s) + 4 <= n) { *out = 1; return 1; }
        if (!strncmp(q, "false", 5) && (size_t)(q - s) + 5 <= n) { *out = 0; return 1; }
        char *end;
        char buf[24];
        size_t k = 0;
        while ((size_t)(q - s) + k < n && k < sizeof buf - 1 && (q[k] == '-' || (q[k] >= '0' && q[k] <= '9'))) {
            buf[k] = q[k]; k++;
        }
        buf[k] = 0;
        if (!k) return 0;
        *out = strtol(buf, &end, 10);
        return 1;
    }
    return 0;
}

/* ---------------------------------------------------------------- HID */
static int hid_fd = -1;
static int ptt_col = 1, ptt_row = 4; /* doc coords 1-based, PRD §6.2; fw base 0 assumed unless --fw-base */
static int fw_base = 0;
static uint8_t mods;          /* desired modifier state */
static uint8_t sent;          /* last state the gadget accepted */
static int dirty = 1;         /* desired != delivered (or unknown: startup sends all-keys-up) */
static unsigned hard_fail;    /* consecutive non-EAGAIN write errors (throttles the retry) */
static volatile sig_atomic_t stop_sig;

static int hid_try(uint8_t m) { /* one non-blocking write; state changes only on success */
    uint8_t rep[9] = {1, m, 0, 0, 0, 0, 0, 0, 0}; /* report id 1: mods, reserved, 6 keys */
    if (hid_fd < 0) return -1;
    ssize_t w;
    do w = write(hid_fd, rep, sizeof rep); while (w < 0 && errno == EINTR && !stop_sig);
    if (w == (ssize_t)sizeof rep) { sent = m; dirty = (sent != mods); hard_fail = 0; return 0; }
    if (w >= 0) errno = EIO;
    if (errno != EAGAIN) hard_fail++;
    dirty = 1;
    return -1;
}

/* bounded (<= ~500 ms) delivery of all-keys-up: used at EOF and on SIGTERM/SIGINT */
static void hid_release_all(void) {
    mods = 0;
    if (!dirty && sent == 0) return;
    for (int tries = 0; tries < 10; tries++) {
        if (hid_try(0) == 0) return;
        struct pollfd pf = {hid_fd, POLLOUT, 0};
        poll(&pf, 1, 50);
    }
    fprintf(stderr, "mk20-router: all-keys-up not delivered: %s\n", strerror(errno));
}

static void on_key(long col, long row, long pressed) {
    trace("recv", (int)col, (int)row, (int)pressed);
    int dc = (int)col - fw_base + 1, dr = (int)row - fw_base + 1;
    trace("routed", (int)col, (int)row, (int)pressed);
    if (col < 100 && dc == ptt_col && dr == ptt_row) {
        mods = pressed ? (mods | MOD_RALT) : (mods & ~MOD_RALT);
        if (mods != sent || !pressed) {
            dirty = 1;
            if (hid_try(mods) == 0) {
                trace("hid_written", (int)col, (int)row, (int)pressed);
                return;
            }
            if (errno != EAGAIN) fprintf(stderr, "mk20-router: hid write failed: %s (retrying)\n", strerror(errno));
        }
    }
    trace("local", (int)col, (int)row, (int)pressed);
}

static void on_json(const uint8_t *d, size_t n) {
    const char *s = (const char *)d;
    if (!memmem(s, n, "\"keyStateChanged\"", 17)) return;
    long col, row, pressed = 0;
    if (!json_int(s, n, "col", &col) || !json_int(s, n, "row", &row)) return;
    json_int(s, n, "pressed", &pressed);
    on_key(col, row, pressed);
}

/* ---------------------------------------------------------------- frame parser */
static uint8_t *buf;
static size_t blen, bcap = 64 * 1024;
static unsigned long bad_crc;

static void feed(const uint8_t *p, size_t n) {
    static const uint8_t H[4] = {0xA1, 0xA5, 0x5A, 0x5E};
    if (blen + n > bcap) { /* resync: keep the tail only */
        size_t keep = blen > 3 ? 3 : blen;
        memmove(buf, buf + blen - keep, keep);
        blen = keep;
        if (n > bcap - blen) { p += n - (bcap - blen); n = bcap - blen; }
    }
    memcpy(buf + blen, p, n);
    blen += n;
    for (;;) {
        uint8_t *h = memmem(buf, blen, H, 4);
        if (!h) { size_t keep = blen > 3 ? 3 : blen; memmove(buf, buf + blen - keep, keep); blen = keep; return; }
        if (h != buf) { blen -= h - buf; memmove(buf, h, blen); }
        if (blen < 20) return;
        uint32_t cmd = rd32(buf + 8), size = rd32(buf + 12), scrc = rd32(buf + 16);
        if (scrc != crc32b(buf + 12, 4) || size > MAX_PAYLOAD) { bad_crc++; memmove(buf, buf + 1, --blen); continue; }
        if (size + 24 > bcap) { bad_crc++; memmove(buf, buf + 1, --blen); continue; }
        if (blen < 24 + size) return;
        if (rd32(buf + 20 + size) != crc32b(buf + 20, size)) { bad_crc++; memmove(buf, buf + 1, --blen); continue; }
        if (cmd == CMD_JSON) on_json(buf + 20, size);
        blen -= 24 + size;
        memmove(buf, buf + 24 + size, blen);
    }
}

static int open_src(const char *spec) {
    if (!strcmp(spec, "-")) return 0;
    if (!strncmp(spec, "file:", 5)) return open(spec + 5, O_RDONLY);
    if (!strncmp(spec, "unix:", 5)) {
        int fd = socket(AF_UNIX, SOCK_STREAM, 0);
        struct sockaddr_un a = {.sun_family = AF_UNIX};
        snprintf(a.sun_path, sizeof a.sun_path, "%s", spec + 5);
        if (fd >= 0 && connect(fd, (struct sockaddr *)&a, sizeof a) == 0) return fd;
        if (fd >= 0) close(fd);
        return -1;
    }
    errno = EINVAL;
    return -1;
}

static void on_stop(int sig) { (void)sig; stop_sig = 1; }

static void usage(void) {
    fprintf(stderr, "usage: mk20-router --src unix:PATH|file:PATH|- --hid PATH [--ptt COL,ROW] [--fw-base 0|1]\n");
    exit(2);
}

int main(int argc, char **argv) {
    const char *src = NULL, *hid = NULL;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--src") && i + 1 < argc) src = argv[++i];
        else if (!strcmp(argv[i], "--hid") && i + 1 < argc) hid = argv[++i];
        else if (!strcmp(argv[i], "--ptt") && i + 1 < argc) {
            if (sscanf(argv[++i], "%d,%d", &ptt_col, &ptt_row) != 2 || ptt_col < 1 || ptt_col > 5 || ptt_row < 1 ||
                ptt_row > 4) usage();
        } else if (!strcmp(argv[i], "--fw-base") && i + 1 < argc) {
            fw_base = atoi(argv[++i]);
            if (fw_base != 0 && fw_base != 1) usage();
        } else usage();
    }
    if (!src || !hid) usage();
    signal(SIGPIPE, SIG_IGN);
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = on_stop; /* no SA_RESTART: poll()/read() return EINTR */
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGINT, &sa, NULL);
    crc_init();
    trace_init();
    buf = malloc(bcap);
    hid_fd = open(hid, O_WRONLY | O_CLOEXEC | O_NONBLOCK);
    if (hid_fd < 0) { fprintf(stderr, "mk20-router: open %s: %s\n", hid, strerror(errno)); return 1; }
    int fd = open_src(src);
    if (fd < 0) { fprintf(stderr, "mk20-router: open src %s: %s\n", src, strerror(errno)); return 1; }
    uint8_t chunk[4096];
    hid_try(0); /* startup all-keys-up: a crashed predecessor may have left RAlt held */
    for (;;) {
        if (stop_sig) break;
        struct pollfd pf[2] = {{fd, POLLIN, 0}, {hid_fd, POLLOUT, 0}};
        /* while a report is undelivered, also wait for the gadget to become writable; after hard errors
         * (e.g. unbound) retry on a 100-ms tick instead so a permanently-writable error fd cannot spin */
        int want_out = dirty && !hard_fail;
        int to = dirty && hard_fail ? 100 : -1;
        int pr = poll(pf, want_out ? 2 : 1, to);
        if (pr < 0) { if (errno == EINTR) continue; break; }
        if (dirty && (pr == 0 || (want_out && (pf[1].revents & (POLLOUT | POLLERR))))) {
            uint8_t m = mods;
            if (hid_try(m) == 0) trace("hid_resent", 0, 0, m ? 1 : 0);
        }
        if (pf[0].revents & (POLLIN | POLLHUP | POLLERR)) {
            ssize_t r = read(fd, chunk, sizeof chunk);
            if (r > 0) feed(chunk, (size_t)r);
            else if (r == 0 || (errno != EINTR && errno != EAGAIN)) break;
        }
    }
    hid_release_all(); /* never leave a modifier stuck (EOF, error, SIGTERM/SIGINT) */
    if (stop_sig) { fprintf(stderr, "mk20-router: stopped by signal\n"); return 0; }
    fprintf(stderr, "mk20-router: source closed (bad_crc=%lu)\n", bad_crc);
    return 0;
}
