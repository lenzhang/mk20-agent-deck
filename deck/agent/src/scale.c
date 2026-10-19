#include "scale.h"
#include <limits.h>
#include <string.h>
static unsigned gcd(unsigned a, unsigned b) { while (b) { unsigned t = a % b; a = b; b = t; } return a; }
void scale_free(scale_t *s) { if (s->mem) mem_free(s->mem, s->data); memset(s, 0, sizeof *s); }
int scale_init(scale_t *s, mem_t *m, unsigned sw, unsigned sh, unsigned mw, unsigned mh)
{
    memset(s, 0, sizeof *s);
    if (!sw || !sh || sw > 8192 || sh > 8192 || !mw || !mh || mw > 8192 || mh > 8192) return -1;
    s->sw = sw; s->sh = sh; s->w = sw; s->h = sh; s->mem = m;
    if (s->w > mw) { s->w = mw; s->h = (unsigned)((uint64_t)sh * mw / sw); if (!s->h) s->h = 1; }
    if (s->h > mh) { s->h = mh; s->w = (unsigned)((uint64_t)sw * mh / sh); if (!s->w) s->w = 1; }
    unsigned gx = gcd(sw, s->w), gy = gcd(sh, s->h);
    s->sx = sw / gx; s->dx = s->w / gx; s->sy = sh / gy; s->dy = s->h / gy;
    s->denom = (uint64_t)s->sx * s->sy;
    uint64_t max = s->denom * 255; size_t n = (size_t)s->w * s->h * 3;
    s->bytes = max <= UINT16_MAX ? 2 : max <= UINT32_MAX ? 4 : 8;
    if (sw == s->w && sh == s->h) s->bytes = 1;
    /* Rare coprime large geometries need >32-bit numerators. For large targets,
     * truncate at most three low numerator bits per contribution to stay bounded.
     * Error < contributors*7/denom (<1 channel unit here); small targets stay exact. */
    if (s->bytes == 8 && n > 3u * 512u * 1024u) {
        s->bytes = 4; while ((max >> s->shift) > UINT32_MAX) s->shift++;
    }
    s->data = mem_alloc(m, n * s->bytes); return s->data ? 0 : -1;
}
static uint64_t value(const scale_t *s, size_t i)
{
    if (s->bytes == 2) return ((uint16_t *)s->data)[i];
    if (s->bytes == 4) return ((uint32_t *)s->data)[i];
    return ((uint64_t *)s->data)[i];
}
static int accum(scale_t *s, size_t i, uint64_t v)
{
    uint64_t sum = value(s, i), max = s->bytes == 2 ? UINT16_MAX : s->bytes == 4 ? UINT32_MAX : UINT64_MAX;
    if (v > max - sum) return -1;
    sum += v;
    if (s->bytes == 2) ((uint16_t *)s->data)[i] = (uint16_t)sum;
    else if (s->bytes == 4) ((uint32_t *)s->data)[i] = (uint32_t)sum;
    else ((uint64_t *)s->data)[i] = sum;
    return 0;
}
int scale_tile(scale_t *s, unsigned x, unsigned y, unsigned w, unsigned h, const unsigned char *rgb, size_t stride)
{
    if (!s->data || s->finished || !rgb || x > s->sw || y > s->sh || w > s->sw - x || h > s->sh - y ||
        stride < (size_t)w * 3 || s->pixels + (uint64_t)w * h > (uint64_t)s->sw * s->sh) return -1;
    s->pixels += (uint64_t)w * h;
    for (unsigned yy = 0; yy < h; yy++) {
        if (s->bytes == 1) { memcpy((unsigned char *)s->data + ((size_t)(y + yy) * s->w + x) * 3, rgb + yy * stride, (size_t)w * 3); continue; }
        unsigned ya = (y + yy) * s->dy, yb = ya + s->dy;
        for (unsigned xx = 0; xx < w; xx++) {
            unsigned xa = (x + xx) * s->dx, xb = xa + s->dx;
            for (unsigned ty = ya / s->sy; ty <= (yb - 1) / s->sy; ty++) {
                unsigned lo = ya > ty * s->sy ? ya : ty * s->sy;
                unsigned hi = yb < (ty + 1) * s->sy ? yb : (ty + 1) * s->sy;
                for (unsigned tx = xa / s->sx; tx <= (xb - 1) / s->sx; tx++) {
                    unsigned l = xa > tx * s->sx ? xa : tx * s->sx;
                    unsigned r = xb < (tx + 1) * s->sx ? xb : (tx + 1) * s->sx;
                    uint64_t weight = (uint64_t)(hi - lo) * (r - l); size_t at = ((size_t)ty * s->w + tx) * 3;
                    for (unsigned c = 0; c < 3; c++)
                        if (accum(s, at + c, ((uint64_t)rgb[yy * stride + xx * 3 + c] * weight) >> s->shift)) return -1;
                }
            }
        }
    }
    return 0;
}
int scale_finish(scale_t *s)
{
    if (!s->data || s->finished || s->pixels != (uint64_t)s->sw * s->sh) return -1;
    if (s->bytes != 1) for (size_t i = 0; i < (size_t)s->w * s->h * 3; i++) {
        uint64_t v = ((value(s, i) << s->shift) + s->denom / 2) / s->denom;
        if (v > 255) return -1;
        ((unsigned char *)s->data)[i] = (unsigned char)v;
    }
    s->finished = 1; return 0;
}
