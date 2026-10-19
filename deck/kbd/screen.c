#include "screen.h"
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static size_t px_count(int i) { return (size_t)PANELS[i].w * PANELS[i].h; }

int screen_init(screen_t *s, const char *fbdir)
{
    memset(s, 0, sizeof *s);
    snprintf(s->fbdir, sizeof s->fbdir, "%s", fbdir);
    s->retry_ms = 200;
    for (int b = 0; b < 2; b++) {
        pthread_mutex_init(&s->mu[b], NULL); pthread_cond_init(&s->cv[b], NULL); pthread_cond_init(&s->idle[b], NULL);
    }
    for (int i = 0; i < PANEL_COUNT; i++) {
        s->shadow[i] = calloc(px_count(i), 2);
        s->pending[i] = calloc(px_count(i), 2);
        if (!s->shadow[i] || !s->pending[i]) return -1;
        s->dirty[i] = 1;
    }
    return 0;
}

void screen_free(screen_t *s)
{
    if (s->threaded) screen_stop_writers(s, 0, 0);
    for (int i = 0; i < PANEL_COUNT; i++) { free(s->shadow[i]); free(s->pending[i]); s->shadow[i] = s->pending[i] = NULL; }
    for (int b = 0; b < 2; b++) {
        pthread_mutex_destroy(&s->mu[b]); pthread_cond_destroy(&s->cv[b]); pthread_cond_destroy(&s->idle[b]);
    }
}

static void lock_all(screen_t *s) { pthread_mutex_lock(&s->mu[0]); pthread_mutex_lock(&s->mu[1]); }
static void unlock_all(screen_t *s) { pthread_mutex_unlock(&s->mu[1]); pthread_mutex_unlock(&s->mu[0]); }

void screen_discard(screen_t *s)
{
    lock_all(s);
    for (int i = 0; i < PANEL_COUNT; i++) {
        if (i==19) continue; /* e-stop stays queued exactly as rendered */
        /* Keep pixels (especially fb21's footer) until a replacement is ready.
         * Invalidate even identical replacements after an old in-flight write. */
        s->epoch[i]++; s->invalid[i]=1; s->dirty[i]=0;
    }
    unlock_all(s);
}

void screen_invalidate(screen_t *s)
{
    lock_all(s);
    for (int i = 0; i < PANEL_COUNT; i++) s->dirty[i] = 1;
    pthread_cond_signal(&s->cv[0]); pthread_cond_signal(&s->cv[1]);
    unlock_all(s);
}

int screen_set_canvas565(screen_t *s, const uint16_t *cv)
{
    int n = 0, sig[2] = {0, 0};
    lock_all(s);
    for (int i = 0; i < PANEL_COUNT; i++) {
        const panel_t *p = &PANELS[i];
        int changed = 0;
        for (int y = 0; y < p->h; y++) {
            const uint16_t *src = cv + (size_t)(p->y + y) * CANVAS_W + p->x;
            uint16_t *dst = s->pending[i] + (size_t)y * p->w;
            if (memcmp(dst, src, (size_t)p->w * 2)) { memcpy(dst, src, (size_t)p->w * 2); changed = 1; }
        }
        if (changed || s->invalid[i]) { s->invalid[i]=0; s->dirty[i] = 1; sig[p->bus] = 1; }
        n += s->dirty[i];
    }
    for (int b = 0; b < 2; b++) if (sig[b]) pthread_cond_signal(&s->cv[b]);
    unlock_all(s);
    return n;
}

int screen_set_tile565(screen_t *s, int fb, int x, int y, int w, int h, const uint16_t *px)
{
    if (fb < 1 || fb > PANEL_COUNT || w <= 0 || h <= 0 || x < 0 || y < 0) return -1;
    int i = fb - 1; const panel_t *p = &PANELS[i];
    if (x + w > p->w || y + h > p->h) return -1;
    int changed = 0, ret = 0;
    pthread_mutex_lock(&s->mu[p->bus]);
    for (int r = 0; r < h; r++) {
        uint16_t *dst = s->pending[i] + (size_t)(y + r) * p->w + x;
        if (memcmp(dst, px + (size_t)r * w, (size_t)w * 2)) { memcpy(dst, px + (size_t)r * w, (size_t)w * 2); changed = 1; }
    }
    if (changed || s->invalid[i]) {
        s->invalid[i]=0;
        s->dirty[i] = 1; ret = 1; pthread_cond_signal(&s->cv[p->bus]);
    }
    pthread_mutex_unlock(&s->mu[p->bus]);
    return ret;
}

static int write_buf(screen_t *s, int i, const uint16_t *buf)
{
    char path[160];
    snprintf(path, sizeof path, "%s/fb%d", s->fbdir, PANELS[i].fb);
    int fd = open(path, O_WRONLY | O_CLOEXEC);
    if (fd < 0) return -1;
    const uint8_t *b = (const uint8_t *)buf;
    size_t left = px_count(i) * 2;
    while (left) {
        ssize_t w = write(fd, b, left);
        if (w <= 0) { if (w < 0 && errno == EINTR) continue; close(fd); return -1; }
        b += w; left -= (size_t)w;
    }
    if (fsync(fd)) { close(fd); return -1; } /* fbtft: forces the deferred-io flush now (KD does the same) */
    close(fd);
    unsigned d = __atomic_load_n(&s->write_delay_us, __ATOMIC_RELAXED);
    if (d) usleep(d);
    return 0;
}

int screen_flush_bus(screen_t *s, int bus)
{
    int n = 0;
    for (int i = 0; i < PANEL_COUNT; i++) {
        if (PANELS[i].bus != bus || !s->dirty[i]) continue;
        if (write_buf(s, i, s->pending[i])) { __atomic_fetch_add(&s->write_errors, 1, __ATOMIC_RELAXED); return -1; }
        memcpy(s->shadow[i], s->pending[i], px_count(i) * 2);
        s->dirty[i] = 0; s->writes[i]++; n++;
    }
    return n;
}

static int next_dirty(screen_t *s, int bus, int *rr)
{
    for (int k = 0; k < PANEL_COUNT; k++) {
        int i = (*rr + k) % PANEL_COUNT;
        if (PANELS[i].bus == bus && s->dirty[i]) { *rr = i + 1; return i; }
    }
    return -1;
}


static void *writer(void *arg)
{
    struct screen_warg *wa = arg; screen_t *s = wa->s; int bus = wa->bus, rr = 0;
    pthread_mutex_lock(&s->mu[bus]);
    for (;;) {
        int i;
        while ((i = next_dirty(s, bus, &rr)) < 0 && !s->stop[bus]) {
            pthread_cond_broadcast(&s->idle[bus]);
            pthread_cond_wait(&s->cv[bus], &s->mu[bus]);
        }
        if (s->stop[bus] == 2 || (s->stop[bus] && i < 0)) break;   /* 2 = stop now, 1 = drain */
        memcpy(s->wbuf[bus], s->pending[i], px_count(i) * 2);     /* snapshot: latest content wins */
        uint64_t epoch=s->epoch[i];
        s->dirty[i] = 0; s->busy[bus] = 1;
        pthread_mutex_unlock(&s->mu[bus]);
        int rc = write_buf(s, i, s->wbuf[bus]);
        pthread_mutex_lock(&s->mu[bus]);
        s->busy[bus] = 0;
        if (rc == 0) {
            memcpy(s->shadow[i], s->wbuf[bus], px_count(i) * 2);
            s->writes[i]++;
            /* re-dirtied meanwhile but back to what we just wrote? then it is clean */
            if (epoch==s->epoch[i] && s->dirty[i] && !memcmp(s->pending[i], s->shadow[i], px_count(i) * 2)) s->dirty[i] = 0;
        } else {
            __atomic_fetch_add(&s->write_errors, 1, __ATOMIC_RELAXED);
            if (epoch==s->epoch[i]) s->dirty[i] = 1; /* cancelled old snapshots must not retry */
            if (s->stop[bus]) break;
            struct timespec ts; clock_gettime(CLOCK_REALTIME, &ts);
            ts.tv_nsec += (long)(s->retry_ms % 1000) * 1000000L; ts.tv_sec += s->retry_ms / 1000 + ts.tv_nsec / 1000000000L;
            ts.tv_nsec %= 1000000000L;
            pthread_cond_broadcast(&s->idle[bus]);
            pthread_cond_timedwait(&s->cv[bus], &s->mu[bus], &ts);
        }
    }
    s->busy[bus] = 0;
    pthread_cond_broadcast(&s->idle[bus]);
    pthread_mutex_unlock(&s->mu[bus]);
    return NULL;
}

int screen_start_writers(screen_t *s)
{
    if (s->threaded) return 0;
    for (int b = 0; b < 2; b++) {
        s->wbuf[b] = malloc(428 * 142 * 2);   /* largest panel */
        if (!s->wbuf[b]) return -1;
        s->stop[b] = 0;
    }
    for (int b = 0; b < 2; b++) {
        s->warg[b].s = s; s->warg[b].bus = b;
        if (pthread_create(&s->th[b], NULL, writer, &s->warg[b])) {
            if (b == 1) { pthread_mutex_lock(&s->mu[0]); s->stop[0] = 2; pthread_cond_signal(&s->cv[0]); pthread_mutex_unlock(&s->mu[0]); pthread_join(s->th[0], NULL); }
            return -1;
        }
    }
    s->threaded = 1;
    return 0;
}

static int bus_clean(screen_t *s, int b)
{
    if (s->busy[b]) return 0;
    for (int i = 0; i < PANEL_COUNT; i++) if (PANELS[i].bus == b && s->dirty[i]) return 0;
    return 1;
}

static void deadline(struct timespec *ts, int ms)
{
    clock_gettime(CLOCK_REALTIME, ts);
    ts->tv_sec += ms / 1000; ts->tv_nsec += (long)(ms % 1000) * 1000000L;
    if (ts->tv_nsec >= 1000000000L) { ts->tv_sec++; ts->tv_nsec -= 1000000000L; }
}

int screen_wait_idle(screen_t *s, int timeout_ms)
{
    struct timespec ts; deadline(&ts, timeout_ms);
    for (int b = 0; b < 2; b++) {
        pthread_mutex_lock(&s->mu[b]);
        while (!bus_clean(s, b)) {
            if (!s->threaded) break;
            if (pthread_cond_timedwait(&s->idle[b], &s->mu[b], &ts) == ETIMEDOUT) { pthread_mutex_unlock(&s->mu[b]); return -1; }
        }
        int ok = bus_clean(s, b);
        pthread_mutex_unlock(&s->mu[b]);
        if (!ok) return -1;
    }
    return 0;
}

void screen_stop_writers(screen_t *s, int drain, int timeout_ms)
{
    if (!s->threaded) return;
    if (drain) screen_wait_idle(s, timeout_ms);
    for (int b = 0; b < 2; b++) {
        pthread_mutex_lock(&s->mu[b]); s->stop[b] = 2; pthread_cond_signal(&s->cv[b]); pthread_mutex_unlock(&s->mu[b]);
    }
    for (int b = 0; b < 2; b++) { pthread_join(s->th[b], NULL); free(s->wbuf[b]); s->wbuf[b] = NULL; }
    s->threaded = 0;
}

void screen_stats(screen_t *s, unsigned writes[PANEL_COUNT], unsigned *errors, int *dirty_count)
{
    lock_all(s);
    if (writes) memcpy(writes, s->writes, sizeof s->writes);
    if (errors) *errors = __atomic_load_n(&s->write_errors, __ATOMIC_RELAXED);
    if (dirty_count) { int d = 0; for (int i = 0; i < PANEL_COUNT; i++) d += s->dirty[i]; *dirty_count = d; }
    unlock_all(s);
}

void screen_set_write_delay(screen_t *s, unsigned us) { __atomic_store_n(&s->write_delay_us, us, __ATOMIC_RELAXED); }
