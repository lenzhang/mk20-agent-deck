#include "gd32io.h"
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <string.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>

int gd32_open(const char *dev, int rw)
{
    int fd = open(dev, (rw ? O_RDWR : O_RDONLY) | O_NOCTTY | O_CLOEXEC);
    if (fd < 0) return -1;
    struct termios t;
    if (tcgetattr(fd, &t) == 0) {
        cfmakeraw(&t); cfsetispeed(&t, B115200); cfsetospeed(&t, B115200);
        t.c_cflag |= CLOCAL | CREAD; t.c_cflag &= ~(CSTOPB | PARENB | CRTSCTS);
        t.c_cc[VMIN] = 1; t.c_cc[VTIME] = 0;
        tcsetattr(fd, TCSANOW, &t);
    }
    return fd;
}

int gd32_tx(int fd, const uint8_t *pl, size_t len)
{
    uint8_t fr[GD32_MAX_PAYLOAD + GD32_FRAME_OVERHEAD];
    int n = gd32_encode(pl, len, fr, sizeof fr);
    if (n < 0 || gd32_raw_is_forbidden(fr, (size_t)n)) { errno = EPERM; return -1; }
    const uint8_t *b = fr; size_t left = (size_t)n;
    while (left) {
        ssize_t w = write(fd, b, left);
        if (w < 0) { if (errno == EINTR) continue; return -1; }
        b += w; left -= (size_t)w;
    }
    return 0;
}

const char *gd32_knob_state_name(int s)
{
    static const char *n[] = {"not_run", "ok", "partial", "failed", "disabled"};
    return (s >= 0 && s <= 4) ? n[s] : "?";
}

static int64_t now_ms(void)
{
    struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts);
    return (int64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

/* wait for the echo of want[] for up to timeout_ms; 1 = got it, 0 = timeout, -1 = read error */
static int wait_echo(int fd, gd32_parser *p, const uint8_t *want, int timeout_ms, gd32_knob_status *st, gd32_frame_cb cb, void *ctx)
{
    int64_t deadline = now_ms() + timeout_ms;
    for (;;) {
        int64_t left = deadline - now_ms();
        if (left <= 0) return 0;
        struct pollfd pf = {fd, POLLIN, 0};
        int r = poll(&pf, 1, (int)left);
        if (r < 0) { if (errno == EINTR) continue; return -1; }
        if (r == 0) return 0;
        uint8_t buf[256];
        ssize_t n = read(fd, buf, sizeof buf);
        if (n < 0) { if (errno == EINTR || errno == EAGAIN) continue; return -1; }
        if (n == 0) return -1;
        int got = 0;
        for (ssize_t i = 0; i < n; i++) {
            int len = gd32_feed(p, buf[i]);
            if (len <= 0) continue;
            if (len == GD32_KNOB_PAYLOAD_LEN && !memcmp(p->buf, want, GD32_KNOB_PAYLOAD_LEN)) { got = 1; continue; }
            if (p->buf[0] == GD32_IN_ECHO) { st->stale_echoes++; continue; }  /* e.g. KD leftovers */
            st->foreign_frames++;
            if (cb) cb(ctx, p->buf, len);
        }
        if (got) return 1; /* rest of this read was already dispatched */
    }
}

int gd32_knob_init(int fd, gd32_parser *p, const gd32_knob_opts *o, gd32_knob_status *st, gd32_frame_cb cb, void *ctx)
{
    int to = (o && o->timeout_ms > 0) ? o->timeout_ms : 1000;
    int att = (o && o->attempts > 0) ? o->attempts : 3;
    memset(st, 0, sizeof *st);
    for (int i = 0; i < 6; i++) {
        uint8_t pl[GD32_KNOB_PAYLOAD_LEN];
        gd32_knob_payload(GD32_KNOB_SLOT_FIRST + i, pl);
        for (int a = 0; a < att && !st->slot_ok[i]; a++) {
            st->slot_attempts[i]++;
            if (gd32_tx(fd, pl, sizeof pl)) { st->tx_errors++; continue; }
            int r = wait_echo(fd, p, pl, to, st, cb, ctx);
            if (r == 1) st->slot_ok[i] = 1;
            else if (r == 0) st->timeouts++;
            else st->tx_errors++;
        }
        st->ok_count += st->slot_ok[i];
    }
    st->state = st->ok_count == 6 ? 1 : st->ok_count ? 2 : 3;
    return st->ok_count;
}
