#include "screen.h"
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static size_t px_count(int i) { return (size_t)PANELS[i].w * PANELS[i].h; }

int screen_init(screen_t *s, const char *fbdir)
{
    memset(s, 0, sizeof *s);
    snprintf(s->fbdir, sizeof s->fbdir, "%s", fbdir);
    for (int i = 0; i < PANEL_COUNT; i++) {
        s->shadow[i] = calloc(px_count(i), 2);
        s->pending[i] = calloc(px_count(i), 2);
        if (!s->shadow[i] || !s->pending[i]) return -1;
        s->dirty[i] = 1;
    }
    return 0;
}
void screen_free(screen_t *s) { for (int i = 0; i < PANEL_COUNT; i++) { free(s->shadow[i]); free(s->pending[i]); } }
void screen_invalidate(screen_t *s) { for (int i = 0; i < PANEL_COUNT; i++) s->dirty[i] = 1; }

int screen_set_canvas565(screen_t *s, const uint16_t *cv)
{
    int n = 0;
    for (int i = 0; i < PANEL_COUNT; i++) {
        const panel_t *p = &PANELS[i];
        int changed = 0;
        for (int y = 0; y < p->h; y++) {
            const uint16_t *src = cv + (size_t)(p->y + y) * CANVAS_W + p->x;
            uint16_t *dst = s->pending[i] + (size_t)y * p->w;
            if (memcmp(dst, src, (size_t)p->w * 2)) { memcpy(dst, src, (size_t)p->w * 2); changed = 1; }
        }
        if (changed && memcmp(s->pending[i], s->shadow[i], px_count(i) * 2)) s->dirty[i] = 1;
        n += s->dirty[i];
    }
    return n;
}

int screen_set_tile565(screen_t *s, int fb, int x, int y, int w, int h, const uint16_t *px)
{
    if (fb < 1 || fb > PANEL_COUNT || w <= 0 || h <= 0 || x < 0 || y < 0) return -1;
    int i = fb - 1; const panel_t *p = &PANELS[i];
    if (x + w > p->w || y + h > p->h) return -1;
    int changed = 0;
    for (int r = 0; r < h; r++) {
        uint16_t *dst = s->pending[i] + (size_t)(y + r) * p->w + x;
        if (memcmp(dst, px + (size_t)r * w, (size_t)w * 2)) { memcpy(dst, px + (size_t)r * w, (size_t)w * 2); changed = 1; }
    }
    if (changed && memcmp(s->pending[i], s->shadow[i], px_count(i) * 2)) { s->dirty[i] = 1; return 1; }
    return 0;
}

static int write_panel(screen_t *s, int i)
{
    char path[160];
    snprintf(path, sizeof path, "%s/fb%d", s->fbdir, PANELS[i].fb);
    int fd = open(path, O_WRONLY | O_CLOEXEC);
    if (fd < 0) return -1;
    const uint8_t *b = (const uint8_t *)s->pending[i];
    size_t left = px_count(i) * 2;
    while (left) {
        ssize_t w = write(fd, b, left);
        if (w < 0) { if (errno == EINTR) continue; close(fd); return -1; }
        b += w; left -= (size_t)w;
    }
    fsync(fd); /* fbtft: forces the deferred-io flush now (KD does the same) */
    close(fd);
    return 0;
}

int screen_flush_bus(screen_t *s, int bus)
{
    int n = 0;
    for (int i = 0; i < PANEL_COUNT; i++) {
        if (PANELS[i].bus != bus || !s->dirty[i]) continue;
        if (write_panel(s, i)) { s->write_errors++; return -1; }
        memcpy(s->shadow[i], s->pending[i], px_count(i) * 2);
        s->dirty[i] = 0; s->writes[i]++; n++;
    }
    return n;
}
