#include "kbd.h"
#include "jpeg.h"
#include "ctl.h"
#include "onboard.h"
#include <errno.h>
#include <poll.h>
#include <time.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int64_t kbd_now(kbd_t *k)
{
    if (k->clock_ms) return k->clock_ms(k->clock_ctx);
    struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t);
    return (int64_t)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}

int kbd_init(kbd_t *k, const char *fbdir, const char *bl_dir, const char *version)
{
    memset(k, 0, sizeof *k);
    k->host_fd = -1; k->backlight = -1; k->ptt_col = 0; k->ptt_row = 3; k->version = version ? version : "mk20-kbd";
    snprintf(k->bl_dir, sizeof k->bl_dir, "%s", bl_dir ? bl_dir : "");
    if (screen_init(&k->scr, fbdir)) return -1;
    if (host_parser_init(&k->hp, (1u << 20) + HOST_HDR + 4)) return -1;   /* full-canvas JPEG is << 1 MiB */
    k->canvas = calloc((size_t)CANVAS_W * CANVAS_H, 2);
    return k->canvas ? 0 : -1;
}

void kbd_free(kbd_t *k)
{
    onboard_free(k);
    screen_free(&k->scr); host_parser_free(&k->hp); free(k->canvas); k->canvas = NULL;
}

void kbd_safety_tick(kbd_t *k, int64_t now)
{
    if (!k->ctl) { onboard_tick(k,now); return; }
    if (k->k2_estop) { const char *reason = k2_estop_tick(&k->k2, now); if (reason) ctl_estop(k->ctl, reason, now); }
    ctl_tick(k->ctl, now);
    onboard_tick(k,now);
}

void kbd_ctl_safety(void *ctx, int64_t now) { kbd_safety_tick((kbd_t *)ctx, now); }

void kbd_service(kbd_t *k, int64_t now)
{
    kbd_safety_tick(k, now);
    if (k->hid) hid_service(k->hid, now);
    if (k->onboard) kbd_host_drain(k);
    onboard_service(k,now);
    if (k->onboard) kbd_safety_tick(k,kbd_now(k));
}

static void host_write(kbd_t *k)
{
    if (k->host_fd < 0 || k->host_out_off == k->host_out_len) return;
    ssize_t n = write(k->host_fd,k->host_out+k->host_out_off,k->host_out_len-k->host_out_off);
    if (n > 0) {
        k->host_out_off += (size_t)n;
        if (k->host_out_off == k->host_out_len) k->host_out_len = k->host_out_off = 0;
    } else if (n < 0 && errno != EINTR) {
        k->st.host_tx_errors++;
        if (errno == EAGAIN) k->st.host_tx_timeouts++;
    }
}

void kbd_host_drain(kbd_t *k)
{
    host_write(k);
    if (k->onboard) {
        onboard_host_flush(k);
        host_write(k);
    }
}

static int host_protocol_ack(uint32_t cmd, size_t n)
{
    return (cmd == HOST_CMD_JPG && n == 0) || (cmd == HOST_CMD_TILE565 && n <= 1);
}

int kbd_host_queue(kbd_t *k, uint32_t id, uint32_t cmd, const void *pl, size_t n)
{
    if (k->host_fd < 0) return -1;
    size_t len = k->host_out_len-k->host_out_off;
    /* Reserve 64 empty JPEG acks against all other output, including tile
     * floods, plus 64 worst-case tile acks (one-byte E) against ordinary
     * output. Compliant image hosts are stop-and-wait. Keep the fixed bound. */
    size_t limit = sizeof k->host_out;
    if (!(cmd == HOST_CMD_JPG && n == 0)) limit -= 64*(HOST_HDR+4);
    if (!host_protocol_ack(cmd,n)) limit -= 64*(HOST_HDR+5);
    if (len > limit || n > limit-len || HOST_HDR+4 > limit-len-n) {
        k->st.host_tx_errors++; return -1;
    }
    memmove(k->host_out,k->host_out+k->host_out_off,len);
    k->host_out_off=0; k->host_out_len=len;
    int count=host_encode(id,cmd,pl,n,k->host_out+len,sizeof k->host_out-len);
    if (count < 0) return -1;
    k->host_out_len += (size_t)count; k->st.host_tx++;
    return 0;
}

int kbd_host_send(kbd_t *k, uint32_t id, uint32_t cmd, const void *pl, size_t n)
{
    if (k->host_fd < 0) return -1;
    if (k->onboard) {
        kbd_host_drain(k);
        /* Pending releases/taps fence ordinary output, but must not fence
         * protocol acks: their reserved space keeps stop-and-wait alive.
         * A failed flush retains release ownership and navigation ordering. */
        if (onboard_host_flush(k) && !host_protocol_ack(cmd,n)) { k->st.host_tx_errors++; return -1; }
        if (kbd_host_queue(k,id,cmd,pl,n)) return -1;
        kbd_host_drain(k); return 0;
    }
    size_t cap = n + HOST_HDR + 4;
    uint8_t stackbuf[4096], *out = cap <= sizeof stackbuf ? stackbuf : malloc(cap);
    if (!out) return -1;
    int len = host_encode(id, cmd, pl, n, out, cap), rc = 0;
    if (len < 0) rc = -1;
    /* Safety opt-in: never wait for the host. Drop the unwritten remainder on backpressure (counted in
     * host_tx_errors/timeouts); the host parser resyncs on the next A1A55A5E magic. No retry queue.
     * Without safety flags retain the legacy bounded wait and fail-fast-after-timeout behaviour. */
    int64_t deadline = 0;
    for (int off = 0; rc == 0 && off < len;) {
        ssize_t w = write(k->host_fd, out + off, (size_t)(len - off));
        if (w > 0) { off += (int)w; continue; }
        if (w < 0 && errno == EINTR) continue;
        if (w < 0 && errno == EAGAIN) {
            if (k->onboard || k->ctl || k->k2_estop) { rc = -1; k->st.host_tx_timeouts++; k->host_tx_stuck = 1; break; }
            struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts);
            int64_t now = (int64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
            if (!deadline) deadline = now + (k->host_tx_stuck ? 0 : KBD_HOST_TX_MS);   /* stuck: fail fast */
            if (now >= deadline) { rc = -1; k->st.host_tx_timeouts++; k->host_tx_stuck = 1; break; }
            struct pollfd pf = {k->host_fd, POLLOUT, 0};
            poll(&pf, 1, (int)(deadline - now));
            continue;
        }
        rc = -1;
    }
    if (out != stackbuf) free(out);
    if (rc) k->st.host_tx_errors++; else { k->st.host_tx++; k->host_tx_stuck = 0; }
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
    struct timespec t; int64_t now = kbd_now(k);
    kbd_safety_tick(k, now);
    /* agent A1: a physical key press takes input back from the agent (lease revoked + all-up) BEFORE the PTT edge */
    if (k->ctl && ev.knob == 2 && k->k2_estop) {
        const char *reason = k2_estop_event(&k->k2, ev.row, ev.pressed, now);
        if (reason) ctl_estop(k->ctl, reason, now);
    }
    if (k->onboard) {
        if (k->ctl && !ev.knob && ev.pressed) ctl_human_input(k->ctl,now);
        now=kbd_now(k); kbd_safety_tick(k,now);
        int consumed = onboard_input(k,ev.row,ev.col,ev.knob,ev.pressed,ev.keycode,now);
        if (!consumed && k->host_fd >= 0) {
            char j[160]; int n = host_json_key_state(j,sizeof j,ev.col,ev.row,ev.pressed);
            if (n > 0) kbd_host_send(k,KBD_EVENT_ID,HOST_CMD_JSON,j,(size_t)n);
        }
        return;
    }
    if (k->ctl && !ev.knob) {
        if (ev.pressed) ctl_human_input(k->ctl, now);
        ctl_key_event(k->ctl, ev.row, ev.col, ev.pressed, now);
    }
    char j[160];
    int n = host_json_key_state(j, sizeof j, ev.col, ev.row, ev.pressed);
    if (n > 0 && k->host_fd >= 0) kbd_host_send(k, KBD_EVENT_ID, HOST_CMD_JSON, j, (size_t)n);
    /* router (r120): PTT key holds Right Alt/Option while pressed; every other key and the knobs are local */
    if (k->hid && !ev.knob && ev.col == k->ptt_col && ev.row == k->ptt_row) {
        clock_gettime(CLOCK_MONOTONIC, &t); now = (int64_t)t.tv_sec * 1000 + t.tv_nsec / 1000000;
        kbd_safety_tick(k, now);   /* forwarding may have crossed a deadline before this implicit HID flush */
        hid_set_mod(k->hid, HID_MOD_RALT, ev.pressed);
    }
    if (k->local_ui && !ev.knob) { kbd_draw_key(k, ev.row, ev.col, ev.pressed); kbd_flush(k); }
}

void kbd_host_push(kbd_t *k, const uint8_t *d, size_t n)
{
    if (n > k->hp.cap) { kbd_host_push(k,d,n/2); kbd_host_push(k,d+n/2,n-n/2); return; }
    size_t old = n < k->host_rx_barrier ? n : k->host_rx_barrier;
    host_push(&k->hp,d,n);
    if (old) {
        size_t barrier = k->hp.len - n + old; /* loop reads are bounded below parser capacity */
        if (barrier > k->hp.barrier) k->hp.barrier = barrier;
        k->host_rx_barrier -= old;
    }
}
void kbd_host_feed(kbd_t *k, const uint8_t *d, size_t n)
{
    kbd_host_push(k, d, n);
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
    /* KD V2.32 exactly: BacklightAdjuster = Adjuster(min 0, max 100, ctrlMin 10, ctrlMax 255)
     * (@0xd16b8); Adjuster::mapToControl (@0xd1228):
     *   ctrl = ctrlMin + (int)((double)(v - min) / (max - min) * (ctrlMax - ctrlMin)), clamped.
     * v is clamped to [min,max] first by Adjuster::setValue (@0xd1284). Floor 10 = never dark. */
    if (level < 0) level = 0;
    if (level > 100) level = 100;
    int c = 10 + (int)((double)level / 100.0 * 245.0);
    return c < 10 ? 10 : c > 255 ? 255 : c;
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
static void ack_json_result(kbd_t *k, uint32_t id, const char *method, const char *result)
{
    char j[256];
    int n = snprintf(j, sizeof j, "{\"ack_method\":\"%s\",\"success\":true,\"result\":%s}", method, result);
    if (n > 0 && (size_t)n < sizeof j) kbd_host_send(k, id, HOST_CMD_JSON, j, (size_t)n);
}

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
    if (k->onboard) {
        /* Handshake/liveness carry no page content. A physical route entry may
         * race a hello already queued in the tty; only page requests are stale. */
        if (f->data == k->hp.buf + HOST_HDR && k->hp.frame_stale && !strcmp(m,"ui.page")) {
            ack_json(k,f->id,m,0,"stale UI request"); return;
        }
        char result[256]; int handled = onboard_json(k,m,s,n,result,sizeof result);
        if (handled) {
            if (handled > 0) ack_json_result(k,f->id,m,result);
            else ack_json(k,f->id,m,0,"unavailable or invalid UI request");
            return;
        }
    }
    if (!strcmp(m, "getInfo")) {
        char j[8192]; int l = host_json_getinfo_ack(j, sizeof j, k->version);
        if (l > 0) kbd_host_send(k, f->id, HOST_CMD_JSON, j, (size_t)l);
    } else if (!strcmp(m, "setBacklight") || !strcmp(m, "setDeviceBl")) {
        /* KD handleSetBacklight (@0x60eb0): needs "level" (JSON number), 0..100, then
         * replies {"success":true,"result":<level>} */
        long lv; char eb[96];
        if (kbd_json_int(s, n, "level", &lv)) ack_json(k, f->id, m, 0, "Missing or invalid level");
        else if (lv < 0 || lv > 100) {
            snprintf(eb, sizeof eb, "Backlight level must be between 0 and 100, got %ld", lv);
            ack_json(k, f->id, m, 0, eb);
        } else if (kbd_set_backlight(k, (int)lv)) ack_json(k, f->id, m, 0, "backlight write failed");
        else { char r[16]; snprintf(r, sizeof r, "%ld", lv); ack_json_result(k, f->id, m, r); }
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
        if (w > 0 && h > 0 && (size_t)w * h * 2 + 9 == f->size &&
            fb >= 1 && fb <= PANEL_COUNT && x <= PANELS[fb-1].w-w && y <= PANELS[fb-1].h-h &&
            (!k->onboard || onboard_tile_allowed(k,fb,y,h))) {
            uint16_t *px = malloc((size_t)w * h * 2);
            if (px) {
                memcpy(px, f->data + 9, (size_t)w * h * 2);   /* alignment */
                ok = (k->onboard ? onboard_tile(k,fb,x,y,w,h,px) :
                      screen_set_tile565(&k->scr, fb, x, y, w, h, px)) >= 0;
                free(px);
            }
        }
    }
    if (ok) { k->st.tiles++; if (k->onboard) onboard_valid_frame(k,kbd_now(k)); else kbd_flush(k); }
    kbd_host_send(k, f->id, HOST_CMD_TILE565, ok ? "" : "E", ok ? 0 : 1);
}

void kbd_on_host_frame(kbd_t *k, const host_frame *f)
{
    if (k->onboard) {
        kbd_safety_tick(k,kbd_now(k));
        if (f->data == k->hp.buf + HOST_HDR && k->hp.frame_stale) {
            if (f->cmd == HOST_CMD_JPG) kbd_host_send(k,f->id,f->cmd,"",0);
            else if (f->cmd == HOST_CMD_TILE565) kbd_host_send(k,f->id,f->cmd,"E",1);
            if (f->cmd == HOST_CMD_JPG || f->cmd == HOST_CMD_TILE565) return;
        }
    }
    switch (f->cmd) {
    case HOST_CMD_JPG: {
        if (k->onboard) {
            onboard_jpeg(k,f->data,f->size);
            kbd_host_send(k,f->id,HOST_CMD_JPG,"",0);
            break;
        }
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
               "\"host\":%d,\"host_tx_errors\":%u,\"host_tx_timeouts\":%u,\"backlight\":%d,\"hid_open\":%d,\"hid_reopens\":%u,\"hid_replaced\":%u}\n",
            k->version, k->gd32_rw, gd32_knob_state_name(ks->state), ks->ok_count, ks->timeouts, ks->tx_errors,
            panels_written, dirty, werr, k->st.key_events, k->st.knob_events, k->st.echo_frames,
            k->st.other_frames, k->st.jpg_ok, k->st.jpg_bad, k->st.jpg_size, k->st.json_in, k->host_fd >= 0,
            k->st.host_tx_errors, k->st.host_tx_timeouts, k->backlight, k->hid ? k->hid->fd >= 0 : -1, k->hid ? k->hid->reopens : 0u,
            k->hid ? k->hid->replaced : 0u);
    if (fclose(f)) { unlink(tmp); return -1; }
    return rename(tmp, path);
}
