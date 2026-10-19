#include "hid.h"
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static long mono_ms(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec * 1000L + t.tv_nsec / 1000000L; }

void hid_init(hid_t *h, const char *path, const char *stop_file)
{
    memset(h, 0, sizeof *h);
    h->fd = -1;
    snprintf(h->path, sizeof h->path, "%s", path ? path : HID_DEFAULT_PATH);
    snprintf(h->stop_file, sizeof h->stop_file, "%s", stop_file ? stop_file : "");
    h->dirty = 1;
}

static void kbd_report(const hid_t *h, uint8_t *r)
{
    r[0] = 1; r[1] = h->mods; r[2] = 0; memcpy(r + 3, h->keys, 6);
}

static int wr(hid_t *h, const uint8_t *rep, size_t n)
{
    if (h->fd < 0) { errno = EBADF; return -1; }
    ssize_t w;
    do w = write(h->fd, rep, n); while (w < 0 && errno == EINTR && !h->close_req);
    if (w == (ssize_t)n) { h->writes++; h->hard_fail = 0; return 0; }
    if (w >= 0) errno = EIO;
    if (errno == EAGAIN) h->eagain++; else { h->errors++; h->hard_fail++; }
    return -1;
}

/* try to deliver the desired keyboard state, then queued pointer reports */
static void flush(hid_t *h)
{
    uint8_t r[HID_KBD_LEN]; kbd_report(h, r);
    if (h->dirty || memcmp(r, h->sent, sizeof r)) {
        h->dirty = 1;
        if (wr(h, r, sizeof r) == 0) { memcpy(h->sent, r, sizeof r); h->dirty = 0; }
        else return;   /* keep order: keyboard first */
    }
    while (h->ptr_n) {
        if (wr(h, h->ptrq[h->ptr_head], HID_PTR_LEN)) return;
        h->ptr_head = (h->ptr_head + 1) % 16; h->ptr_n--;
    }
}

static int stop_present(const hid_t *h) { return h->stop_file[0] && access(h->stop_file, F_OK) == 0; }

int hid_attach_fd(hid_t *h, int fd)
{
    h->fd = fd; h->opens++; h->hard_fail = 0;
    memset(h->keys, 0, sizeof h->keys); h->mods = 0; h->dirty = 1;   /* startup all-keys-up */
    flush(h);
    return 0;
}

void hid_close(hid_t *h, int release)
{
    if (h->fd < 0) return;
    if (release) hid_release_all(h, 300);   /* short: teardown is waiting */
    close(h->fd); h->fd = -1; h->closes++;
    h->ptr_n = 0;
}

void hid_request_close(hid_t *h) { h->close_req = 1; }

void hid_service(hid_t *h, long now)
{
    if (h->fd >= 0 && (h->close_req || stop_present(h))) {
        hid_close(h, 1);
        h->next_open_ms = now + 200;
    }
    if (h->fd < 0) {
        if (h->close_req && !stop_present(h)) h->close_req = 0;   /* one-shot request; reopen next tick */
        if (h->close_req || stop_present(h) || now < h->next_open_ms) return;
        int fd = open(h->path, O_WRONLY | O_CLOEXEC | O_NONBLOCK);
        if (fd < 0) { h->next_open_ms = now + 1000; return; }
        hid_attach_fd(h, fd);
        return;
    }
    if (h->dirty || h->ptr_n) {
        if (h->hard_fail && now - h->last_hard_ms < 100) return;   /* throttle after hard errors */
        h->last_hard_ms = now;
        flush(h);
    }
}

int hid_wants_pollout(const hid_t *h) { return h->fd >= 0 && (h->dirty || h->ptr_n) && !h->hard_fail; }

void hid_set_mod(hid_t *h, uint8_t mod, int on)
{
    uint8_t m = on ? (h->mods | mod) : (h->mods & (uint8_t)~mod);
    if (m != h->mods || !on) { h->mods = m; h->dirty = 1; }
    if (h->fd >= 0 && !h->hard_fail) flush(h);
}

int hid_key(hid_t *h, uint8_t usage, int down)
{
    int i, free_i = -1;
    for (i = 0; i < 6; i++) { if (h->keys[i] == usage) break; if (!h->keys[i] && free_i < 0) free_i = i; }
    if (down) { if (i < 6) return 0; if (free_i < 0) return -1; h->keys[free_i] = usage; }
    else { if (i == 6) return 0; h->keys[i] = 0; }
    h->dirty = 1;
    if (h->fd >= 0 && !h->hard_fail) flush(h);
    return 0;
}

int hid_pointer_abs(hid_t *h, uint8_t buttons, uint16_t x, uint16_t y)
{
    if (x > 32767) x = 32767;
    if (y > 32767) y = 32767;
    uint8_t r[HID_PTR_LEN] = {2, (uint8_t)(buttons & 7), (uint8_t)x, (uint8_t)(x >> 8), (uint8_t)y, (uint8_t)(y >> 8)};
    if (h->ptr_n == 16) {   /* full: coalesce into the newest move if buttons unchanged, else drop the oldest */
        uint8_t *last = h->ptrq[(h->ptr_head + 15) % 16];
        if (last[1] == r[1]) { memcpy(last, r, sizeof r); goto out; }
        h->ptr_head = (h->ptr_head + 1) % 16; h->ptr_n--; h->ptr_dropped++;
    }
    memcpy(h->ptrq[(h->ptr_head + h->ptr_n) % 16], r, sizeof r); h->ptr_n++;
out:
    if (h->fd >= 0 && !h->hard_fail) flush(h);
    return 0;
}

int hid_release_all(hid_t *h, int timeout_ms)
{
    h->mods = 0; memset(h->keys, 0, sizeof h->keys);
    uint8_t up[HID_KBD_LEN] = {1, 0, 0, 0, 0, 0, 0, 0, 0};
    if (h->fd < 0) return -1;
    if (!h->dirty && !memcmp(h->sent, up, sizeof up)) return 0;
    h->dirty = 1;
    long end = mono_ms() + timeout_ms;
    for (;;) {
        if (wr(h, up, sizeof up) == 0) { memcpy(h->sent, up, sizeof up); h->dirty = 0; return 0; }
        long left = end - mono_ms();
        if (left <= 0) break;
        struct pollfd pf = {h->fd, POLLOUT, 0};
        poll(&pf, 1, left < 50 ? (int)left : 50);
    }
    fprintf(stderr, "mk20-kbd: all-keys-up not delivered: %s\n", strerror(errno));
    return -1;
}
