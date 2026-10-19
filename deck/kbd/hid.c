#include "hid.h"
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

static int64_t mono_ms(void *ctx) { (void)ctx; struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return (int64_t)t.tv_sec * 1000 + t.tv_nsec / 1000000; }

void hid_init(hid_t *h, const char *path, const char *stop_file)
{
    memset(h, 0, sizeof *h);
    h->fd = -1;
    h->clock_ms = mono_ms;
    snprintf(h->path, sizeof h->path, "%s", path ? path : HID_DEFAULT_PATH);
    snprintf(h->stop_file, sizeof h->stop_file, "%s", stop_file ? stop_file : "");
    h->dirty = 1;
    h->backoff_ms = 100;
    h->ptr_len = HID_PTR_LEN; h->ptr_sent[0] = 2;
}

void hid_set_scroll(hid_t *h, int on) { h->ptr_len = on ? HID_PTR_LEN_SCROLL : HID_PTR_LEN; }

static int gone_errno(int e) { return e == ENXIO || e == ESHUTDOWN || e == ENODEV || e == EPIPE || e == EBADF; }

static void kbd_report(const hid_t *h, uint8_t *r)
{
    r[0] = 1; r[1] = h->mods; r[2] = 0; memcpy(r + 3, h->keys, 6);
}

static int wr(hid_t *h, const uint8_t *rep, size_t n)
{
    if (h->fd < 0) { errno = EBADF; return -1; }
    ssize_t w;
    do w = write(h->fd, rep, n); while (w < 0 && errno == EINTR && !h->close_req);
    if (w == (ssize_t)n) { h->writes++; h->hard_fail = 0; h->backoff_ms = 100; return 0; }
    if (w >= 0) errno = EIO;
    if (errno == EAGAIN) h->eagain++; else { h->errors++; h->hard_fail++; if (gone_errno(errno)) h->reopen_req = 1; }   /* -> identity check now, nothing closed */
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
        if (wr(h, h->ptrq[h->ptr_head], (size_t)h->ptr_len)) return;
        memcpy(h->ptr_sent, h->ptrq[h->ptr_head], HID_PTR_LEN_SCROLL);
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
    h->ptr_n = 0; h->watch = 0; h->reopen_req = 0; h->keep = 0;
}

void hid_close_parked(hid_t *h)
{
    unsigned n = h->nparked < HID_MAX_PARKED ? h->nparked : HID_MAX_PARKED;
    for (unsigned i = 0; i < n; i++) close(h->parked[i]);
    h->nparked = 0;
}

int hid_node_replaced(const hid_t *h)
{
    struct stat a, b;
    if (h->fd < 0 || fstat(h->fd, &a)) return 1;
    if (a.st_nlink == 0) return 1;                       /* "/dev/hidg0 (deleted)" */
    if (stat(h->path, &b)) return 1;                     /* node gone (link down, re-creating) */
    return a.st_dev != b.st_dev || a.st_ino != b.st_ino || a.st_rdev != b.st_rdev;
}

/* reopen after a node replacement: all-keys-up on the new node, then re-send what is still held */
static void reattach_keep(hid_t *h, int fd)
{
    static const uint8_t up[HID_KBD_LEN] = {1, 0, 0, 0, 0, 0, 0, 0, 0};
    h->fd = fd; h->opens++; h->reopens++; h->hard_fail = 0; h->dirty = 1;
    if (wr(h, up, sizeof up) == 0) { memcpy(h->sent, up, sizeof up); h->dirty = 0; }
    flush(h);
}

void hid_request_close(hid_t *h) { h->close_req = 1; }

void hid_service(hid_t *h, int64_t now)
{
    if ((h->fd >= 0 || h->nparked) && (h->close_req || stop_present(h))) {   /* teardown: no hidg0 fd at all */
        hid_close(h, 1);
        hid_close_parked(h);
        h->next_open_ms = now + 200;
    }
    if (h->fd >= 0 && (h->reopen_req || (h->watch && now >= h->next_check_ms))) {
        h->next_check_ms = now + 1000; h->reopen_req = 0;
        if (hid_node_replaced(h)) {   /* stale node: PARK the fd (never close it), reopen the path with back-off */
            fprintf(stderr, "mk20-kbd: %s replaced/gone: parking stale fd (#%u), reopening\n", h->path, h->nparked + 1);
            if (h->nparked < HID_MAX_PARKED) h->parked[h->nparked] = h->fd;
            h->nparked++;   /* past HID_MAX_PARKED the fd is leaked, still never closed */
            h->fd = -1; h->replaced++; h->ptr_n = 0; h->watch = 0; h->keep = 1;
            h->next_open_ms = now + h->backoff_ms;
            return;
        }
    }
    if (h->fd < 0) {
        if (h->close_req && !stop_present(h)) h->close_req = 0;   /* one-shot request; reopen next tick */
        if (h->close_req || stop_present(h) || now < h->next_open_ms) return;
        int fd = open(h->path, O_WRONLY | O_CLOEXEC | O_NONBLOCK);
        if (fd < 0) {   /* ENXIO until the link is up again: back-off 100 ms .. 1 s */
            h->backoff_ms = h->backoff_ms * 2 > 1000 ? 1000 : h->backoff_ms * 2;
            h->next_open_ms = now + h->backoff_ms;
            return;
        }
        struct stat st;
        if (fstat(fd, &st) == 0) { h->dev = st.st_dev; h->ino = st.st_ino; h->rdev = st.st_rdev; }
        if (h->keep) reattach_keep(h, fd); else hid_attach_fd(h, fd);
        h->keep = 0; h->watch = 1; h->next_check_ms = now + 1000;   /* back-off resets only on a delivered report */
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

static void ptr_queue(hid_t *h, const uint8_t *r)
{
    if (h->ptr_n == 16) {   /* full: coalesce into the newest move if buttons unchanged and no scroll, else drop the oldest */
        uint8_t *last = h->ptrq[(h->ptr_head + 15) % 16];
        if (last[1] == r[1] && !last[6] && !last[7] && !r[6] && !r[7]) { memcpy(last, r, HID_PTR_LEN_SCROLL); goto out; }
        h->ptr_head = (h->ptr_head + 1) % 16; h->ptr_n--; h->ptr_dropped++;
    }
    memcpy(h->ptrq[(h->ptr_head + h->ptr_n) % 16], r, HID_PTR_LEN_SCROLL); h->ptr_n++;
out:
    if (h->fd >= 0 && !h->hard_fail) flush(h);
}

int hid_pointer_abs(hid_t *h, uint8_t buttons, uint16_t x, uint16_t y)
{
    if (x > 32767) x = 32767;
    if (y > 32767) y = 32767;
    uint8_t r[HID_PTR_LEN_SCROLL] = {2, (uint8_t)(buttons & 7), (uint8_t)x, (uint8_t)(x >> 8), (uint8_t)y, (uint8_t)(y >> 8), 0, 0};
    h->ptr_btn = buttons & 7; h->ptr_x = x; h->ptr_y = y;
    ptr_queue(h, r);
    return 0;
}

int hid_scroll(hid_t *h, int wheel, int pan)
{
    if (h->ptr_len != HID_PTR_LEN_SCROLL) return -1;
    wheel = wheel < -127 ? -127 : wheel > 127 ? 127 : wheel;
    pan = pan < -127 ? -127 : pan > 127 ? 127 : pan;
    uint8_t r[HID_PTR_LEN_SCROLL] = {2, h->ptr_btn, (uint8_t)h->ptr_x, (uint8_t)(h->ptr_x >> 8), (uint8_t)h->ptr_y,
                                     (uint8_t)(h->ptr_y >> 8), (uint8_t)(int8_t)wheel, (uint8_t)(int8_t)pan};
    ptr_queue(h, r);
    return 0;
}

/* Cancel before any neutral write. Retain only the last delivered position, never a queued move. */
static int pointer_cancel(hid_t *h)
{
    int need = h->ptr_n || h->ptr_btn || h->ptr_sent[1];
    h->ptr_head = 0; h->ptr_n = 0; h->ptr_btn = 0;
    h->ptr_x = (uint16_t)(h->ptr_sent[2] | (h->ptr_sent[3] << 8));
    h->ptr_y = (uint16_t)(h->ptr_sent[4] | (h->ptr_sent[5] << 8));
    if (need) {
        memcpy(h->ptrq[0], h->ptr_sent, HID_PTR_LEN_SCROLL);
        h->ptrq[0][1] = h->ptrq[0][6] = h->ptrq[0][7] = 0; h->ptr_n = 1;
    }
    return need != 0;
}
int hid_pointer_release(hid_t *h)
{
    int need = pointer_cancel(h);
    if (h->fd >= 0 && !h->hard_fail) flush(h);
    return need;
}

int hid_release_all(hid_t *h, int timeout_ms)
{
    pointer_cancel(h);
    h->mods = 0; memset(h->keys, 0, sizeof h->keys);
    uint8_t up[HID_KBD_LEN] = {1, 0, 0, 0, 0, 0, 0, 0, 0};
    if (h->fd < 0) return -1;
    if (!h->dirty && !memcmp(h->sent, up, sizeof up)) { flush(h); return 0; }
    h->dirty = 1;
    int64_t end = h->clock_ms(h->clock_ctx) + timeout_ms;
    for (;;) {
        if (wr(h, up, sizeof up) == 0) { memcpy(h->sent, up, sizeof up); h->dirty = 0; flush(h); return 0; }
        int64_t left = end - h->clock_ms(h->clock_ctx);
        if (left <= 0) break;
        struct pollfd pf = {h->fd, POLLOUT, 0};
        poll(&pf, 1, left < 50 ? (int)left : 50);
    }
    fprintf(stderr, "mk20-kbd: all-keys-up not delivered: %s\n", strerror(errno));
    return -1;
}
