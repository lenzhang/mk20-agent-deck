#include "host.h"
#include "panels.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static const uint8_t HDR[4] = {0xA1, 0xA5, 0x5A, 0x5E};

uint32_t host_crc32(const uint8_t *p, size_t n)
{
    static uint32_t t[256]; static int init;
    if (!init) {
        for (uint32_t i = 0; i < 256; i++) { uint32_t c = i; for (int k = 0; k < 8; k++) c = (c & 1) ? 0xEDB88320u ^ (c >> 1) : c >> 1; t[i] = c; }
        init = 1;
    }
    uint32_t c = 0xFFFFFFFFu;
    for (size_t i = 0; i < n; i++) c = t[(c ^ p[i]) & 0xFF] ^ (c >> 8);
    return c ^ 0xFFFFFFFFu;
}
static void put32(uint8_t *o, uint32_t v) { o[0] = v; o[1] = v >> 8; o[2] = v >> 16; o[3] = v >> 24; }
static uint32_t get32(const uint8_t *o) { return o[0] | o[1] << 8 | o[2] << 16 | (uint32_t)o[3] << 24; }

int host_parser_init(host_parser *p, size_t cap)
{ memset(p, 0, sizeof *p); p->buf = malloc(cap); p->cap = cap; return p->buf ? 0 : -1; }
void host_parser_free(host_parser *p) { free(p->buf); p->buf = NULL; }

static void drop(host_parser *p, size_t n)
{ if (n > p->len) n = p->len; memmove(p->buf, p->buf + n, p->len - n); p->len -= n; }

int host_push(host_parser *p, const uint8_t *d, size_t n)
{
    if (n > p->cap - p->len) { /* overflow: keep newest data, resync later */
        p->dropped_bytes += p->len; p->len = 0;
        if (n > p->cap) { p->dropped_bytes += n - p->cap; d += n - p->cap; n = p->cap; }
    }
    memcpy(p->buf + p->len, d, n); p->len += n; return 0;
}

int host_next(host_parser *p, host_frame *f)
{
    if (p->consumed) { drop(p, p->consumed); p->consumed = 0; }
    for (;;) {
        size_t i = 0;
        while (i + 4 <= p->len && memcmp(p->buf + i, HDR, 4)) i++;
        if (i + 4 > p->len) { size_t keep = p->len < 3 ? p->len : 3; p->dropped_bytes += p->len - keep; drop(p, p->len - keep); return 0; }
        if (i) { p->dropped_bytes += i; drop(p, i); }
        if (p->len < HOST_HDR) return 0;
        uint32_t size = get32(p->buf + 12);
        uint8_t sz[4]; put32(sz, size);
        if (get32(p->buf + 16) != host_crc32(sz, 4)) { p->bad_size_crc++; drop(p, 1); continue; }
        if (size > HOST_MAX_PAYLOAD || size + HOST_HDR + 4 > p->cap) { p->oversize++; drop(p, 1); continue; }
        if (p->len < HOST_HDR + size + 4) return 0;
        if (get32(p->buf + HOST_HDR + size) != host_crc32(p->buf + HOST_HDR, size)) { p->bad_data_crc++; drop(p, 1); continue; }
        f->id = get32(p->buf + 4); f->cmd = get32(p->buf + 8); f->size = size; f->data = p->buf + HOST_HDR;
        p->consumed = HOST_HDR + size + 4; p->frames_ok++;
        return 1;
    }
}

int host_encode(uint32_t id, uint32_t cmd, const uint8_t *pl, size_t n, uint8_t *out, size_t cap)
{
    if (n > HOST_MAX_PAYLOAD || cap < n + HOST_HDR + 4) return -1;
    memcpy(out, HDR, 4); put32(out + 4, id); put32(out + 8, cmd); put32(out + 12, (uint32_t)n);
    uint8_t sz[4]; put32(sz, (uint32_t)n); put32(out + 16, host_crc32(sz, 4));
    if (n) memcpy(out + HOST_HDR, pl, n);
    put32(out + HOST_HDR + n, host_crc32(pl, n));
    return (int)(n + HOST_HDR + 4);
}

int host_json_getinfo_ack(char *out, size_t cap, const char *ver)
{
    size_t o = 0; int r;
#define AP(...) do { r = snprintf(out + o, cap - o, __VA_ARGS__); if (r < 0 || (size_t)r >= cap - o) return -1; o += (size_t)r; } while (0)
    AP("{\"ack_method\":\"getInfo\",\"success\":true,\"result\":{\"deviceModel\":\"MK20\",\"deviceVersion\":\"%s\","
       "\"deviceWidth\":%d,\"deviceHeight\":%d,\"devicePanel\":{\"rectCols\":5,\"rectRows\":4,\"rects\":[",
       ver, CANVAS_W, CANVAS_H);
    for (int i = 0; i < PANEL_COUNT; i++) {
        const panel_t *pn = &PANELS[i];
        if (pn->is_key)
            AP("%s{\"col\":%d,\"height\":%d,\"isKey\":true,\"row\":%d,\"width\":%d,\"x\":%d,\"y\":%d}", i ? "," : "",
               pn->col, pn->h, pn->row, pn->w, pn->x, pn->y);
        else
            AP("%s{\"height\":%d,\"isKey\":false,\"width\":%d,\"x\":%d,\"y\":%d}", i ? "," : "", pn->h, pn->w, pn->x, pn->y);
    }
    AP("],\"screenHeight\":%d,\"screenWidth\":%d}}}", CANVAS_H, CANVAS_W);
#undef AP
    return (int)o;
}

int host_json_key_state(char *out, size_t cap, int col, int row, int pressed)
{
    int r = snprintf(out, cap, "{\"method\":\"keyStateChanged\",\"parameters\":{\"col\":%d,\"pressed\":%s,\"row\":%d}}",
                     col, pressed ? "true" : "false", row);
    return (r < 0 || (size_t)r >= cap) ? -1 : r;
}
