#define _POSIX_C_SOURCE 200809L
#include "rfb.h"
#include "des.h"
#include "zlib.h"
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#define TEXT_MAX (1024u * 1024u)
#define SKIP_MAX (16u * 1024u * 1024u)
struct rfb {
    int (*service)(void *); void *service_ctx;
    int fd, timeout, zinit; int64_t deadline; const char *error;
    mem_t mem; rfb_info_t info; scale_t scale; z_stream z;
    unsigned char input[8192], tile[64 * 64 * 3], row[8192 * 4];
    uint32_t compressed; uint64_t expanded, expand_max;
};
int64_t rfb_now_ms(void)
{
    struct timespec t; if (clock_gettime(CLOCK_MONOTONIC, &t)) return 0;
    return (int64_t)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}
static int fail(rfb_t *c, const char *s) { c->error = s; return -1; }
void rfb_set_service(rfb_t *c, int (*fn)(void *), void *ctx) { c->service = fn; c->service_ctx = ctx; }
int rfb_check_deadline(rfb_t *c) { if (c->service && c->service(c->service_ctx)) return fail(c,"cancelled"); return rfb_now_ms() >= c->deadline ? fail(c, "timeout") : 0; }
static unsigned u16(const unsigned char *p) { return (unsigned)p[0] * 256 + p[1]; }
static uint32_t u32(const unsigned char *p) { return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3]; }
static void p16(unsigned char *p, unsigned n) { p[0] = (unsigned char)(n >> 8); p[1] = (unsigned char)n; }
static void p32(unsigned char *p, uint32_t n) { for (int i = 3; i >= 0; i--) { p[i] = (unsigned char)n; n >>= 8; } }
static int ready(rfb_t *c, short events)
{
    for (;;) {
        if (rfb_check_deadline(c)) return -1;
        int64_t left = c->deadline - rfb_now_ms(); if (left <= 0) return fail(c, "timeout");
        struct pollfd p = {c->fd, events, 0}; int r = poll(&p, 1, c->service && left > 25 ? 25 : (int)left);
        if (r > 0) return 0;
        if (!r) continue;
        if (errno != EINTR) return fail(c, "io");
    }
}
static int rd(rfb_t *c, void *buf, size_t n)
{
    unsigned char *p = buf;
    while (n) {
        if (ready(c, POLLIN)) return -1;
        ssize_t r = recv(c->fd, p, n, 0);
        if (!r) return fail(c, "truncated_stream");
        if (r < 0) { if (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK) continue; return fail(c, "io"); }
        p += r; n -= (size_t)r;
    }
    return 0;
}
static int wr(rfb_t *c, const void *buf, size_t n)
{
    const unsigned char *p = buf;
    while (n) {
        if (ready(c, POLLOUT)) return -1;
#ifdef MSG_NOSIGNAL
        ssize_t r = send(c->fd, p, n, MSG_NOSIGNAL);
#else
        ssize_t r = send(c->fd, p, n, 0);
#endif
        if (r <= 0) { if (r < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK)) continue; return fail(c, "io"); }
        p += r; n -= (size_t)r;
    }
    return 0;
}
static int skip(rfb_t *c, uint64_t n)
{
    unsigned char b[4096]; if (n > SKIP_MAX) return fail(c, "length_limit");
    while (n) { size_t k = n > sizeof b ? sizeof b : (size_t)n; if (rd(c, b, k)) return -1; n -= k; }
    return 0;
}
static int sized_skip(rfb_t *c, uint32_t cap)
{
    unsigned char b[4]; if (rd(c, b, 4)) return -1;
    uint32_t n = u32(b); if (n > cap) return fail(c, "length_limit");
    return skip(c, n);
}
static int dial(rfb_t *c, const char *host, unsigned port)
{
    if (!strcmp(host,"localhost")) host = "127.0.0.1"; /* preserve local VNC alias */
    /* VNC authentication must never be redirected by DNS/NSS. Validate before socket(). */
    if (strchr(host,'%')) return fail(c,"vnc_host_not_literal");
    struct sockaddr_storage addr; memset(&addr,0,sizeof addr); socklen_t len;
    struct sockaddr_in *v4 = (void *)&addr; struct sockaddr_in6 *v6 = (void *)&addr;
    if (inet_pton(AF_INET,host,&v4->sin_addr) == 1) {
        v4->sin_family = AF_INET; v4->sin_port = htons((uint16_t)port); len = sizeof *v4;
    } else if (inet_pton(AF_INET6,host,&v6->sin6_addr) == 1) {
        v6->sin6_family = AF_INET6; v6->sin6_port = htons((uint16_t)port); len = sizeof *v6;
    } else return fail(c,"vnc_host_not_literal");
    if (rfb_check_deadline(c)) return -1;
    c->fd = socket(addr.ss_family, SOCK_STREAM, 0);
    if (c->fd < 0) return fail(c,"connect");
#ifdef SO_NOSIGPIPE
    int one = 1; (void)setsockopt(c->fd, SOL_SOCKET, SO_NOSIGPIPE, &one, sizeof one);
#endif
    if (fcntl(c->fd, F_SETFL, O_NONBLOCK)) return fail(c,"connect");
    int r = connect(c->fd, (const void *)&addr, len);
    if (!r || (errno == EINPROGRESS && !ready(c, POLLOUT))) {
        int err = 0; socklen_t errlen = sizeof err;
        if (!getsockopt(c->fd, SOL_SOCKET, SO_ERROR, &err, &errlen) && !err) return 0;
    }
    return fail(c, c->error ? c->error : "connect");
}
static voidpf zalloc(voidpf opaque, uInt n, uInt size)
{
    if (size && n > SIZE_MAX / size) return NULL;
    return mem_alloc(opaque, (size_t)n * size);
}
static void zfree(voidpf opaque, voidpf p) { mem_free(opaque, p); }
static int dimensions(rfb_t *c, unsigned w, unsigned h)
{
    if (!w || !h || w > 8192 || h > 8192) return fail(c, "framebuffer_size");
    c->info.w = w; c->info.h = h; return 0;
}
static int handshake(rfb_t *c, const rfb_options_t *o)
{
    unsigned char b[256]; unsigned version, sec = 0;
    if (rd(c, b, 12)) return -1;
    if (!memcmp(b, "RFB 003.008\n", 12)) version = 8;
    else if (!memcmp(b, "RFB 003.007\n", 12)) version = 7;
    else if (!memcmp(b, "RFB 003.003\n", 12)) version = 3;
    else return fail(c, "unsupported_version");
    if (wr(c, b, 12)) return -1;
    if (version == 3) {
        if (rd(c, b, 4)) return -1;
        sec = u32(b);
        if (!sec) { if (sized_skip(c, TEXT_MAX)) return -1; return fail(c, "security_refused"); }
    } else {
        if (rd(c, b, 1)) return -1;
        unsigned count = b[0];
        if (!count) { if (sized_skip(c, TEXT_MAX)) return -1; return fail(c, "security_refused"); }
        if (rd(c, b, count)) return -1;
        for (unsigned i = 0; i < count; i++) { if (b[i] == 2) { sec = 2; break; } if (b[i] == 1) sec = 1; }
        if (!sec) return fail(c, "unsupported_security");
        b[0] = (unsigned char)sec; if (wr(c, b, 1)) return -1;
    }
    if (sec != 1 && sec != 2) return fail(c, "unsupported_security");
    if (sec == 2) {
        unsigned char key[8] = {0}, response[16]; if (rd(c, b, 16)) return -1;
        for (size_t i = 0; i < o->password_len && i < 8; i++)
            for (unsigned j = 0; j < 8; j++) key[i] |= ((o->password[i] >> j) & 1u) << (7 - j);
        des_encrypt_block(key, b, response); des_encrypt_block(key, b + 8, response + 8);
        mem_wipe(key, sizeof key); int r = wr(c, response, sizeof response); mem_wipe(response, sizeof response); if (r) return -1;
    }
    if (sec == 2 || version == 8) {
        if (rd(c, b, 4)) return -1;
        if (u32(b)) { /* Never echo the server's reason (it may contain credentials). */
            if (version == 8 && sized_skip(c, TEXT_MAX)) return -1;
            return fail(c, "auth_failed");
        }
    }
    b[0] = 1; if (wr(c, b, 1) || rd(c, b, 24)) return -1;
    if (dimensions(c, u16(b), u16(b + 2))) return -1;
    if (u32(b + 20) > TEXT_MAX) return fail(c, "length_limit");
    if (skip(c, u32(b + 20))) return -1;
    static const unsigned char pf[20] = {0,0,0,0,32,24,0,1,0,255,0,255,0,255,16,8,0,0,0,0};
    const uint32_t encs[] = {16, 5, 0, 1, (uint32_t)-223, (uint32_t)-239, RFB_EXT_CLIPBOARD};
    unsigned char enc[4 + sizeof encs]; memset(enc, 0, sizeof enc); enc[0] = 2; p16(enc + 2, sizeof encs / sizeof encs[0]);
    for (size_t i = 0; i < sizeof encs / sizeof encs[0]; i++) p32(enc + 4 + i * 4, encs[i]);
    return wr(c, pf, sizeof pf) || wr(c, enc, sizeof enc) ? -1 : 0;
}
int rfb_open_connected(rfb_t **out, const rfb_options_t *o, int fd, const char **error)
{
    *out = NULL; *error = "bad_args";
    if (!o || !o->host || !o->port || o->port > 65535 || o->timeout_ms < 0 || o->timeout_ms > 600000 ||
        (o->password_len && !o->password)) { if (fd >= 0) close(fd); return -1; }
    rfb_t *c = calloc(1, sizeof *c); if (!c) { if (fd >= 0) close(fd); *error = "memory_limit"; return -1; }
    c->fd = fd; c->mem.live = c->mem.high = sizeof *c; c->timeout = o->timeout_ms ? o->timeout_ms : 5000;
    int64_t start = rfb_now_ms(); c->deadline = start + c->timeout;
    if (fd < 0) { if (dial(c, o->host, o->port)) goto bad; }
    else {
#ifdef SO_NOSIGPIPE
        int one = 1; (void)setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &one, sizeof one);
#endif
        if (fcntl(fd, F_SETFL, O_NONBLOCK)) { fail(c, "io"); goto bad; }
    }
    int64_t auth = rfb_now_ms(); c->info.connect_ms = (double)(auth - start); c->deadline = auth + c->timeout;
    if (handshake(c, o)) goto bad;
    c->info.auth_ms = (double)(rfb_now_ms() - auth); *out = c; return 0;
bad:
    *error = c->error; rfb_close(c); return -1;
}
int rfb_open(rfb_t **out, const rfb_options_t *o, const char **error) { return rfb_open_connected(out, o, -1, error); }
void rfb_close(rfb_t *c)
{
    if (!c) return;
    if (c->fd >= 0) close(c->fd);
    if (c->zinit) inflateEnd(&c->z);
    scale_free(&c->scale); free(c);
}
const char *rfb_error(const rfb_t *c) { return c->error ? c->error : "protocol"; }
const rfb_info_t *rfb_info(const rfb_t *c) { return &c->info; }
mem_t *rfb_memory(rfb_t *c) { return &c->mem; }
const scale_t *rfb_frame(const rfb_t *c) { return &c->scale; }
int rfb_request(rfb_t *c, int incremental)
{
    unsigned char b[10] = {3,0,0,0,0,0,0,0,0,0}; b[1] = incremental != 0;
    p16(b + 6, c->info.w); p16(b + 8, c->info.h);
    c->deadline = rfb_now_ms() + c->timeout; return wr(c, b, sizeof b);
}
static int inside(rfb_t *c, unsigned x, unsigned y, unsigned w, unsigned h)
{
    return x <= c->info.w && y <= c->info.h && w <= c->info.w - x && h <= c->info.h - y ? 0 : fail(c, "rect_bounds");
}
static int tile(rfb_t *c, unsigned x, unsigned y, unsigned w, unsigned h)
{
    if (rfb_check_deadline(c)) return -1;
    return scale_tile(&c->scale, x, y, w, h, c->tile, (size_t)w * 3) ? fail(c, "invalid_frame") : 0;
}
static void rgb(unsigned char *out, const unsigned char *b) { out[0] = b[2]; out[1] = b[1]; out[2] = b[0]; }
static int raw(rfb_t *c, unsigned x, unsigned y, unsigned w, unsigned h)
{
    for (unsigned yy = 0; yy < h; yy++) {
        if (rd(c, c->row, (size_t)w * 4)) return -1;
        /* In-place row conversion is safe (destination advances more slowly). */
        for (unsigned xx = 0; xx < w; xx++) { unsigned char p[3]; rgb(p, c->row + xx * 4); memcpy(c->row + xx * 3, p, 3); }
        if (rfb_check_deadline(c)) return -1;
        if (scale_tile(&c->scale, x, y + yy, w, 1, c->row, (size_t)w * 3)) return fail(c, "invalid_frame");
    }
    return 0;
}
static int hextile(rfb_t *c, unsigned x, unsigned y, unsigned w, unsigned h)
{
    unsigned char bg[3] = {0}, fg[3] = {0}, b[4]; int have_bg = 0, have_fg = 0;
    for (unsigned yy = 0; yy < h; yy += 16) for (unsigned xx = 0; xx < w; xx += 16) {
        unsigned tw = w - xx < 16 ? w - xx : 16, th = h - yy < 16 ? h - yy : 16;
        if (rd(c, b, 1)) return -1;
        unsigned flags = b[0]; if (flags & ~31u) return fail(c, "hextile");
        if (flags & 1) {
            if (rd(c, c->row, (size_t)tw * th * 4)) return -1;
            for (unsigned i = 0; i < tw * th; i++) rgb(c->tile + i * 3, c->row + i * 4);
        } else {
            if (flags & 2) { if (rd(c, b, 4)) return -1; rgb(bg, b); have_bg = 1; }
            if (flags & 4) { if (rd(c, b, 4)) return -1; rgb(fg, b); have_fg = 1; }
            if (!have_bg || ((flags & 16) && !(flags & 8))) return fail(c, "hextile");
            for (unsigned i = 0; i < tw * th; i++) memcpy(c->tile + i * 3, bg, 3);
            if (flags & 8) {
                if (rd(c, b, 1)) return -1;
                unsigned count = b[0];
                if (count && !(flags & 16) && !have_fg) return fail(c, "hextile");
                for (unsigned i = 0; i < count; i++) {
                    unsigned char color[3]; memcpy(color, fg, 3);
                    if (flags & 16) { if (rd(c, b, 4)) return -1; rgb(color, b); }
                    if (rd(c, b, 2)) return -1;
                    unsigned sx = b[0] >> 4, sy = b[0] & 15, rw = (b[1] >> 4) + 1, rh = (b[1] & 15) + 1;
                    if (sx >= tw || sy >= th || rw > tw - sx || rh > th - sy) return fail(c, "hextile_bounds");
                    for (unsigned ty = sy; ty < sy + rh; ty++) for (unsigned tx = sx; tx < sx + rw; tx++) memcpy(c->tile + (ty * tw + tx) * 3, color, 3);
                }
            }
        }
        if (tile(c, x + xx, y + yy, tw, th)) return -1;
    }
    return 0;
}
/* Inflate exactly the demanded bytes: never allocate the advertised compressed or
 * decompressed length. The zlib stream persists across rectangles/requests. */
static int zr(rfb_t *c, void *out, unsigned n)
{
    c->z.next_out = out; c->z.avail_out = n;
    while (c->z.avail_out) {
        if (!c->z.avail_in && c->compressed) {
            unsigned k = c->compressed > sizeof c->input ? sizeof c->input : c->compressed;
            if (rd(c, c->input, k)) return -1;
            c->compressed -= k; c->z.next_in = c->input; c->z.avail_in = k;
        }
        unsigned before = c->z.avail_out, input = c->z.avail_in;
        int r = inflate(&c->z, Z_SYNC_FLUSH); unsigned made = before - c->z.avail_out;
        c->expanded += made;
        if (c->expanded > c->expand_max || (r != Z_OK && r != Z_BUF_ERROR) || (!made && input == c->z.avail_in)) return fail(c, "zrle");
    }
    return 0;
}
static int run(rfb_t *c, unsigned remain, unsigned *length)
{
    unsigned char b; unsigned n = 1;
    do { if (zr(c, &b, 1)) return -1; if (b > remain - n) return fail(c, "zrle_run"); n += b; } while (b == 255);
    *length = n; return 0;
}
static int zrle(rfb_t *c, unsigned x, unsigned y, unsigned w, unsigned h)
{
    unsigned char b[4], pal[127][3]; if (rd(c, b, 4)) return -1;
    c->expand_max = (uint64_t)w * h * 8 + 65536;
    c->compressed = u32(b); if (!c->compressed || c->compressed > c->expand_max + 65536) return fail(c, "length_limit");
    if (!c->zinit) { c->z.zalloc = zalloc; c->z.zfree = zfree; c->z.opaque = &c->mem; if (inflateInit(&c->z) != Z_OK) return fail(c, "memory_limit"); c->zinit = 1; }
    c->expanded = 0;
    for (unsigned yy = 0; yy < h; yy += 64) for (unsigned xx = 0; xx < w; xx += 64) {
        unsigned tw = w - xx < 64 ? w - xx : 64, th = h - yy < 64 ? h - yy : 64, total = tw * th;
        if (zr(c, b, 1)) return -1;
        unsigned mode = b[0], np = mode & 127;
        if (mode == 129 || mode == 127) return fail(c, "zrle_mode");
        if (mode == 0) {
            if (zr(c, c->tile, total * 3)) return -1;
            for (unsigned i = 0; i < total; i++) { unsigned char t = c->tile[i * 3]; c->tile[i * 3] = c->tile[i * 3 + 2]; c->tile[i * 3 + 2] = t; }
        } else {
            for (unsigned i = 0; i < np; i++) { if (zr(c, b, 3)) return -1; rgb(pal[i], b); }
            if (mode == 1) for (unsigned i = 0; i < total; i++) memcpy(c->tile + i * 3, pal[0], 3);
            else if (mode < 128) {
                if (np > 16) return fail(c, "zrle_mode");
                unsigned bits = np <= 2 ? 1 : np <= 4 ? 2 : 4;
                for (unsigned ty = 0; ty < th; ty++) {
                    unsigned left = 0, byte = 0;
                    for (unsigned tx = 0; tx < tw; tx++) {
                        if (!left) { if (zr(c, b, 1)) return -1; byte = b[0]; left = 8; }
                        left -= bits; unsigned idx = (byte >> left) & ((1u << bits) - 1);
                        if (idx >= np) return fail(c, "zrle_palette");
                        memcpy(c->tile + (ty * tw + tx) * 3, pal[idx], 3);
                    }
                }
            } else {
                for (unsigned i = 0; i < total;) {
                    unsigned char color[3]; unsigned length = 1;
                    if (mode == 128) { if (zr(c, b, 3)) return -1; rgb(color, b); if (run(c, total - i, &length)) return -1; }
                    else { if (zr(c, b, 1)) return -1; unsigned idx = b[0] & 127, rle = b[0] & 128;
                        if (idx >= np) return fail(c, "zrle_palette");
                        memcpy(color, pal[idx], 3); if (rle && run(c, total - i, &length)) return -1;
                    }
                    for (unsigned k = 0; k < length; k++) memcpy(c->tile + (i++ * 3), color, 3);
                }
            }
        }
        if (tile(c, x + xx, y + yy, tw, th)) return -1;
    }
    /* Consume only the flush trailer; additional decoded data is malformed. */
    for (;;) {
        if (!c->z.avail_in && c->compressed) {
            unsigned k = c->compressed > sizeof c->input ? sizeof c->input : c->compressed;
            if (rd(c, c->input, k)) return -1;
            c->compressed -= k; c->z.next_in = c->input; c->z.avail_in = k;
        }
        c->z.next_out = b; c->z.avail_out = 1; unsigned before = c->z.avail_in;
        int r = inflate(&c->z, Z_SYNC_FLUSH);
        if (!c->z.avail_out || (r != Z_OK && r != Z_BUF_ERROR)) return fail(c, "zrle_trailing");
        if (!c->compressed && !c->z.avail_in) break;
        if (before == c->z.avail_in) return fail(c, "zrle_trailing");
    }
    return 0;
}
static int cut_discard(rfb_t *c)
{
    unsigned char b[7]; if (rd(c, b, 7)) return -1;
    uint32_t n = u32(b + 3);
    if (n & UINT32_C(0x80000000)) {
        n = 0u - n; if (n > TEXT_MAX) return skip(c, n);
        if (n < 4) return fail(c, "length_limit");
        if (rd(c, b, 4)) return -1;
        uint32_t flags = u32(b);
        if (flags & UINT32_C(0x01000000)) {
            unsigned formats = 0; for (unsigned i = 0; i < 16; i++) formats += (flags >> i) & 1u;
            if (n < 4 + formats * 4) return fail(c, "clipboard_caps");
            if (flags & 1) c->info.extended_clipboard = 1;
        }
        return skip(c, n - 4);
    }
    /* Oversize classic text is drained through a fixed scratch buffer, never stored. */
    return skip(c, n);
}
int rfb_snapshot(rfb_t *c, unsigned mw, unsigned mh)
{
    int64_t start = rfb_now_ms(); unsigned refreshes = 0;
refresh:
    scale_free(&c->scale);
    if (scale_init(&c->scale, &c->mem, c->info.w, c->info.h, mw, mh)) return fail(c, "memory_limit");
    if (rfb_request(c, 0)) return -1;
    for (unsigned messages = 0; messages < 1024; messages++) {
        unsigned char b[12]; if (rd(c, b, 1)) return -1;
        unsigned type = b[0];
        if (type == 2) continue; /* Bell */
        if (type == 3) { if (cut_discard(c)) return -1; continue; }
        if (type == 1) { /* True colour ignores colour-map entries. */
            if (rd(c, b, 5)) return -1;
            if (skip(c, (uint64_t)u16(b + 3) * 6)) return -1;
            continue;
        }
        if (type != 0) return fail(c, "server_message");
        if (rd(c, b, 3)) return -1;
        unsigned count = u16(b + 1); int again = 0;
        for (unsigned i = 0; i < count; i++) {
            if (rd(c, b, 12)) return -1;
            unsigned x = u16(b), y = u16(b + 2), w = u16(b + 4), h = u16(b + 6); uint32_t enc = u32(b + 8);
            if (enc == (uint32_t)-223) {
                if (x || y || dimensions(c, w, h)) return fail(c, "framebuffer_size");
                scale_free(&c->scale);
                if (scale_init(&c->scale, &c->mem, w, h, mw, mh)) return fail(c, "memory_limit");
                again = 1; continue;
            }
            if (enc == RFB_EXT_CLIPBOARD) {
                if (x || y || w || h) return fail(c, "rect_bounds");
                c->info.extended_clipboard = 1; continue;
            }
            if (enc == (uint32_t)-239) { /* x/y are cursor hotspots, not framebuffer coordinates. */
                if (w > c->info.w || h > c->info.h || (w ? x >= w : x != 0) || (h ? y >= h : y != 0)) return fail(c, "rect_bounds");
                if (skip(c, (uint64_t)w * h * 4 + (uint64_t)((w + 7) / 8) * h)) return -1;
                continue;
            }
            if (inside(c, x, y, w, h)) return -1;
            if (!w || !h) return fail(c, "rect_bounds");
            if (enc == 1) {
                if (rd(c, b, 4) || inside(c, u16(b), u16(b + 2), w, h)) return -1;
                again = 1;
            } else if (enc == 0) { if (raw(c, x, y, w, h)) return -1; }
            else if (enc == 5) { if (hextile(c, x, y, w, h)) return -1; }
            else if (enc == 16) { if (zrle(c, x, y, w, h)) return -1; }
            else return fail(c, "unsupported_encoding");
        }
        if (again) { if (++refreshes > 3) return fail(c, "refresh_limit"); goto refresh; }
        if (!c->scale.pixels) continue; /* capability-only or empty update */
        if (rfb_check_deadline(c)) return -1;
        int64_t done = rfb_now_ms();
        if (scale_finish(&c->scale)) return fail(c, "incomplete_frame");
        if (rfb_check_deadline(c)) return -1;
        c->info.finish_ms = (double)(rfb_now_ms() - done); c->info.fb_ms = (double)(done - start); return 0;
    }
    return fail(c, "message_limit");
}
static int latin1(const unsigned char *p, size_t n, unsigned char *out, size_t *length)
{
    size_t k = 0;
    for (size_t i = 0; i < n; i++) {
        unsigned b = p[i];
        if (b >= 128) { if ((b != 0xc2 && b != 0xc3) || i + 1 == n || (p[i + 1] & 0xc0) != 0x80) return -1; b = ((b & 3) << 6) | (p[++i] & 63); }
        if (out) out[k] = (unsigned char)b;
        k++;
    }
    *length = k; return 0;
}
int rfb_cut_text(rfb_t *c, const char *utf8, size_t n)
{
    size_t len; if (!utf8 || n > TEXT_MAX || latin1((const unsigned char *)utf8, n, NULL, &len)) return fail(c, "unsupported");
    unsigned char *text = mem_alloc(&c->mem, len ? len : 1); if (!text) return fail(c, "memory_limit");
    (void)latin1((const unsigned char *)utf8, n, text, &len);
    unsigned char b[8] = {6,0,0,0,0,0,0,0}; p32(b + 4, (uint32_t)len); c->deadline = rfb_now_ms() + c->timeout;
    int rc = wr(c, b, 8) || wr(c, text, len) ? -1 : 0; mem_wipe(text, len); mem_free(&c->mem, text); return rc;
}
