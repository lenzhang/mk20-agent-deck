#include "kbd.h"
#include "jpeg.h"
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int kbd_init(kbd_t *k, const char *fbdir, const char *bl_dir, const char *version)
{
    memset(k, 0, sizeof *k);
    k->host_fd = -1; k->backlight = -1; k->version = version ? version : "mk20-kbd";
    snprintf(k->bl_dir, sizeof k->bl_dir, "%s", bl_dir ? bl_dir : "");
    if (screen_init(&k->scr, fbdir)) return -1;
    if (host_parser_init(&k->hp, 2u << 20)) return -1;   /* full-canvas JPEG is << 1 MiB */
    k->canvas = calloc((size_t)CANVAS_W * CANVAS_H, 2);
    return k->canvas ? 0 : -1;
}

void kbd_free(kbd_t *k)
{
    screen_free(&k->scr); host_parser_free(&k->hp); free(k->canvas); k->canvas = NULL;
}

int kbd_host_send(kbd_t *k, uint32_t id, uint32_t cmd, const void *pl, size_t n)
{
    if (k->host_fd < 0) return -1;
    size_t cap = n + HOST_HDR + 4;
    uint8_t stackbuf[4096], *out = cap <= sizeof stackbuf ? stackbuf : malloc(cap);
    if (!out) return -1;
    int len = host_encode(id, cmd, pl, n, out, cap), rc = 0;
    if (len < 0) rc = -1;
    for (int off = 0; rc == 0 && off < len;) {
        ssize_t w = write(k->host_fd, out + off, (size_t)(len - off));
        if (w < 0) { if (errno == EINTR) continue; rc = -1; break; }
        off += (int)w;
    }
    if (out != stackbuf) free(out);
    if (rc) k->st.host_tx_errors++; else k->st.host_tx++;
    return rc;
}

int kbd_flush(kbd_t *k)
{
    if (k->scr.threaded) return 0;   /* writer threads pick dirty panels up themselves */
    int a = screen_flush_bus(&k->scr, 0), b = screen_flush_bus(&k->scr, 1);
    if (a < 0 || b < 0) { k->st.flush_errors++; return -1; }
    k->st.panel_writes += (uint32_t)(a + b);
    return a + b;
}

static void fill_panel(kbd_t *k, int fb, uint16_t bg, uint16_t fg, int bars)
{
    const panel_t *p = &PANELS[fb - 1];
    uint16_t *px = malloc((size_t)p->w * p->h * 2);
    if (!px) return;
    for (int y = 0; y < p->h; y++) for (int x = 0; x < p->w; x++) {
        int bar = bars ? (x * (bars * 2 + 1)) / p->w : 0;
        int edge = y < 4 && bars;  /* small top marker */
        px[y * p->w + x] = ((bar & 1) || edge) ? fg : bg;
    }
    screen_set_tile565(&k->scr, fb, 0, 0, p->w, p->h, px);
    free(px);
}

void kbd_draw_idle(kbd_t *k)
{
    for (int i = 0; i < PANEL_COUNT; i++) fill_panel(k, PANELS[i].fb, 0x0841, 0x4208, PANELS[i].is_key ? 1 : 0);
}

void kbd_draw_key(kbd_t *k, int row, int col, int pressed)
{
    if (row < 0 || row > 3 || col < 0 || col > 4) return;
    int fb = row * 5 + col + 1;
    if (pressed) fill_panel(k, fb, 0xFFFF, 0xFFFF, 0); else fill_panel(k, fb, 0x0841, 0x4208, 1);
}

void kbd_gd32_cb(void *ctx, const uint8_t *pl, int len) { kbd_on_gd32((kbd_t *)ctx, pl, len); }

void kbd_on_gd32(kbd_t *k, const uint8_t *pl, int len)
{
    if (len <= 0) return;
    if (pl[0] == GD32_IN_ECHO) { k->st.echo_frames++; return; }   /* 0x05 echo: never an event */
    gd32_key_event ev;
    if (gd32_decode_key(pl, len, &ev)) { k->st.other_frames++; return; }
    if (ev.knob) k->st.knob_events++;
    else if (ev.row > 3 || ev.col > 4) { k->st.other_frames++; return; }
    else k->st.key_events++;
    if (ev.keycode) k->st.keycode_nonzero++;   /* GD32 already sent HID itself; still report the state */
    char j[160];
    int n = host_json_key_state(j, sizeof j, ev.col, ev.row, ev.pressed);
    if (n > 0 && k->host_fd >= 0) kbd_host_send(k, KBD_EVENT_ID, HOST_CMD_JSON, j, (size_t)n);
    if (k->local_ui && !ev.knob) { kbd_draw_key(k, ev.row, ev.col, ev.pressed); kbd_flush(k); }
}

void kbd_host_feed(kbd_t *k, const uint8_t *d, size_t n)
{
    host_push(&k->hp, d, n);
    host_frame f;
    while (host_next(&k->hp, &f)) kbd_on_host_frame(k, &f);
}

/* ------------------------------------------------------------------ JSON */
static const char *find_key(const char *s, size_t n, const char *key)
{
    size_t kl = strlen(key);
    for (size_t i = 0; i + kl + 2 <= n; i++) {
        if (s[i] != '"' || memcmp(s + i + 1, key, kl) || s[i + 1 + kl] != '"') continue;
        size_t j = i + kl + 2;
        while (j < n && (s[j] == ' ' || s[j] == '\t' || s[j] == '\r' || s[j] == '\n')) j++;
        if (j < n && s[j] == ':') {
            j++;
            while (j < n && (s[j] == ' ' || s[j] == '\t' || s[j] == '\r' || s[j] == '\n')) j++;
            return j < n ? s + j : NULL;
        }
    }
    return NULL;
}

int kbd_json_str(const char *s, size_t n, const char *key, char *out, size_t cap)
{
    const char *v = find_key(s, n, key), *end = s + n;
    if (!v || *v != '"' || cap == 0) return -1;
    size_t o = 0;
    for (v++; v < end && *v != '"'; v++) {
        if (*v == '\\' || o + 1 >= cap) return -1;   /* method names never need escapes */
        out[o++] = *v;
    }
    if (v >= end) return -1;
    out[o] = 0; return (int)o;
}

int kbd_json_int(const char *s, size_t n, const char *key, long *val)
{
    const char *v = find_key(s, n, key), *end = s + n;
    if (!v) return -1;
    int neg = 0; long x = 0; int digits = 0;
    if (v < end && *v == '-') { neg = 1; v++; }
    for (; v < end && *v >= '0' && *v <= '9' && digits < 9; v++, digits++) x = x * 10 + (*v - '0');
    if (!digits) return -1;
    if (v < end && *v == '.') { v++; while (v < end && *v >= '0' && *v <= '9') v++; }  /* truncate */
    *val = neg ? -x : x; return 0;
}

/* ------------------------------------------------------------------ backlight */
int kbd_bl_param(int level)
{
    /* dispdbg setbl takes the sunxi PWM duty 0..255. Floor 8 so "0" from a slider
     * never blacks the panels out completely (plan §4: never dark). */
    int v = level * 255 / 100;
    return v < 8 ? 8 : v > 255 ? 255 : v;
}

static int put_file(const char *dir, const char *name, const char *val)
{
    char path[200]; snprintf(path, sizeof path, "%s/%s", dir, name);
    int fd = open(path, O_WRONLY | O_CLOEXEC | O_TRUNC);
    if (fd < 0) return -1;
    size_t l = strlen(val);
    ssize_t w = write(fd, val, l);
    close(fd);
    return w == (ssize_t)l ? 0 : -1;
}

int kbd_set_backlight(kbd_t *k, int level)
{
    if (level < 0 || level > 100) return -1;
    k->backlight = level;
    if (!k->bl_dir[0]) return 0;
    char p[16]; snprintf(p, sizeof p, "%d", kbd_bl_param(level));
    if (put_file(k->bl_dir, "name", "lcd0") || put_file(k->bl_dir, "command", "setbl") ||
        put_file(k->bl_dir, "param", p) || put_file(k->bl_dir, "start", "1"))
        return -2;
    return 0;
}

/* ------------------------------------------------------------------ host frames */
static void ack_json(kbd_t *k, uint32_t id, const char *method, int ok, const char *err)
{
    char j[256]; int n;
    if (ok) n = snprintf(j, sizeof j, "{\"ack_method\":\"%s\",\"success\":true,\"result\":{}}", method);
    else n = snprintf(j, sizeof j, "{\"ack_method\":\"%s\",\"success\":false,\"errorString\":\"%s\"}", method, err);
    if (n > 0 && (size_t)n < sizeof j) kbd_host_send(k, id, HOST_CMD_JSON, j, (size_t)n);
}

static void on_json(kbd_t *k, const host_frame *f)
{
    const char *s = (const char *)f->data; size_t n = f->size;
    char m[64];
    k->st.json_in++;
    if (kbd_json_str(s, n, "method", m, sizeof m) < 0) { k->st.json_bad++; return; }
    if (!strcmp(m, "getInfo")) {
        char j[8192]; int l = host_json_getinfo_ack(j, sizeof j, k->version);
        if (l > 0) kbd_host_send(k, f->id, HOST_CMD_JSON, j, (size_t)l);
    } else if (!strcmp(m, "setBacklight") || !strcmp(m, "setDeviceBl")) {
        long lv;
        if (kbd_json_int(s, n, "level", &lv) || lv < 0 || lv > 100)
            ack_json(k, f->id, m, 0, "Backlight level must be between 0 and 100");
        else if (kbd_set_backlight(k, (int)lv))
            ack_json(k, f->id, m, 0, "backlight write failed");
        else ack_json(k, f->id, m, 1, NULL);
    } else {
        k->st.json_unsupported++;
        ack_json(k, f->id, m, 0, "unsupported");
    }
}

static uint16_t rd16(const uint8_t *p) { return (uint16_t)(p[0] | p[1] << 8); }

static void on_tile(kbd_t *k, const host_frame *f)
{
    /* u8 fb, u16 x, y, w, h, w*h RGB565 LE */
    int ok = 0;
    if (f->size >= 9) {
        int fb = f->data[0], x = rd16(f->data + 1), y = rd16(f->data + 3), w = rd16(f->data + 5), h = rd16(f->data + 7);
        if (w > 0 && h > 0 && (size_t)w * h * 2 + 9 == f->size) {
            uint16_t *px = malloc((size_t)w * h * 2);
            if (px) {
                memcpy(px, f->data + 9, (size_t)w * h * 2);   /* alignment */
                ok = screen_set_tile565(&k->scr, fb, x, y, w, h, px) >= 0;
                free(px);
            }
        }
    }
    if (ok) { k->st.tiles++; kbd_flush(k); }
    kbd_host_send(k, f->id, HOST_CMD_TILE565, ok ? "" : "E", ok ? 0 : 1);
}

void kbd_on_host_frame(kbd_t *k, const host_frame *f)
{
    switch (f->cmd) {
    case HOST_CMD_JPG: {
        int r = jpeg_decode565(f->data, f->size, k->canvas, CANVAS_W, CANVAS_H);
        if (r == 0) { k->st.jpg_ok++; screen_set_canvas565(&k->scr, k->canvas); kbd_flush(k); }
        else if (r == -2) k->st.jpg_size++;
        else k->st.jpg_bad++;
        /* KD semantics: empty cmd-100 frame with the same id once handled; always ack so the
         * host's one-frame-in-flight pacing never stalls on a bad frame. */
        kbd_host_send(k, f->id, HOST_CMD_JPG, "", 0);
        break;
    }
    case HOST_CMD_JSON: on_json(k, f); break;
    case HOST_CMD_TILE565: on_tile(k, f); break;
    default: break;
    }
}

int kbd_write_status(kbd_t *k, const char *path)
{
    char tmp[256]; snprintf(tmp, sizeof tmp, "%s.tmp", path);
    FILE *f = fopen(tmp, "w");
    if (!f) return -1;
    const gd32_knob_status *ks = &k->knob;
    int panels_written = 0, dirty = 0; unsigned wr[PANEL_COUNT], werr;
    screen_stats(&k->scr, wr, &werr, &dirty);
    for (int i = 0; i < PANEL_COUNT; i++) panels_written += wr[i] > 0;
    fprintf(f, "{\"version\":\"%s\",\"gd32_rw\":%d,\"knob_init\":\"%s\",\"knob_ok\":%d,\"knob_timeouts\":%d,"
               "\"knob_tx_errors\":%d,\"panels_written\":%d,\"panels_dirty\":%d,\"write_errors\":%u,\"key_events\":%u,\"knob_events\":%u,"
               "\"echo_frames\":%u,\"other_frames\":%u,\"jpg_ok\":%u,\"jpg_bad\":%u,\"jpg_size\":%u,\"json_in\":%u,"
               "\"host\":%d,\"host_tx_errors\":%u,\"backlight\":%d}\n",
            k->version, k->gd32_rw, gd32_knob_state_name(ks->state), ks->ok_count, ks->timeouts, ks->tx_errors,
            panels_written, dirty, werr, k->st.key_events, k->st.knob_events, k->st.echo_frames,
            k->st.other_frames, k->st.jpg_ok, k->st.jpg_bad, k->st.jpg_size, k->st.json_in, k->host_fd >= 0,
            k->st.host_tx_errors, k->backlight);
    if (fclose(f)) { unlink(tmp); return -1; }
    return rename(tmp, path);
}
