/* K2 knob init, JPEG, daemon core tests (included by test_kbd.c). */
#include "gd32io.h"
#include "jpeg.h"
#include "kbd.h"
#include <signal.h>
#include <sys/socket.h>
#include <sys/wait.h>

#ifndef TEST_DATA
#define TEST_DATA "tests/data"
#endif

/* ---- fake GD32 (child process on a socketpair) ---- */
enum { FAKE_ECHO, FAKE_SILENT, FAKE_FLAKY, FAKE_NOISY };

static void fake_send(int fd, const uint8_t *pl, size_t n)
{
    uint8_t fr[64]; int l = gd32_build_test_frame(pl, n, fr, sizeof fr);
    if (write(fd, fr, (size_t)l) != l) _exit(3);
}

static void fake_gd32(int fd, int mode, int logfd)
{
    gd32_parser p; gd32_parser_init(&p);
    int seen[256] = {0};
    uint8_t b[256];
    for (;;) {
        ssize_t n = read(fd, b, sizeof b);
        if (n <= 0) _exit(0);
        if (write(logfd, b, (size_t)n) != n) _exit(4);
        for (ssize_t i = 0; i < n; i++) {
            int l = gd32_feed(&p, b[i]);
            if (l <= 0) continue;
            uint8_t pl[64]; memcpy(pl, p.buf, (size_t)l);
            int slot = pl[2];
            seen[slot]++;
            if (mode == FAKE_SILENT) continue;
            if (mode == FAKE_FLAKY && slot == 104 && seen[slot] < 2) continue;  /* first 104 lost */
            if (mode == FAKE_FLAKY && slot == 105) continue;                    /* 105 never echoed */
            if (mode == FAKE_NOISY) {
                uint8_t key[6] = {0x16, 1, 2, 3, 0, 0};           /* key press arrives first */
                fake_send(fd, key, 6);
                uint8_t stale[6] = {0x05, 0, 104, 104, 0, 0xAE}; /* KD leftover echo */
                fake_send(fd, stale, 6);
                uint8_t junk[3] = {0xAA, 0x55, 0x01};
                if (write(fd, junk, 3) != 3) _exit(5);
            }
            fake_send(fd, pl, (size_t)l);
        }
    }
}

typedef struct { int frames, keys; uint8_t last[64]; } cb_rec;
static void rec_cb(void *ctx, const uint8_t *pl, int len)
{
    cb_rec *r = ctx; r->frames++; if (pl[0] == 0x16) r->keys++; memcpy(r->last, pl, (size_t)len);
}

static int run_knob(int mode, gd32_knob_status *st, cb_rec *rec, uint8_t *wire, size_t *wire_n, int timeout_ms)
{
    int sv[2]; socketpair(AF_UNIX, SOCK_STREAM, 0, sv);
    char logp[] = "/tmp/mk20knobXXXXXX"; int logfd = mkstemp(logp);
    pid_t pid = fork();
    if (pid == 0) { close(sv[0]); fake_gd32(sv[1], mode, logfd); _exit(0); }
    close(sv[1]);
    gd32_parser p; gd32_parser_init(&p);
    gd32_knob_opts o = {timeout_ms, 3};
    memset(rec, 0, sizeof *rec);
    int ok = gd32_knob_init(sv[0], &p, &o, st, rec_cb, rec);
    close(sv[0]);
    int status; waitpid(pid, &status, 0);
    *wire_n = (size_t)pread(logfd, wire, 4096, 0);
    close(logfd); unlink(logp);
    return ok;
}

static void test_knob_init(void)
{
    gd32_knob_status st; cb_rec rec; uint8_t wire[4096]; size_t wn;
    /* happy path: exactly the six frames, in order, nothing else on the wire */
    CHECK(run_knob(FAKE_ECHO, &st, &rec, wire, &wn, 300) == 6);
    CHECK(st.state == 1 && st.ok_count == 6 && st.timeouts == 0 && rec.frames == 0);
    CHECK(wn == 6 * 13);
    for (int i = 0; i < 6 && wn == 78; i++) {
        uint8_t pl[6], fr[16]; gd32_knob_payload(100 + i, pl); gd32_encode(pl, 6, fr, sizeof fr);
        CHECK(!memcmp(wire + 13 * i, fr, 13));
        CHECK(st.slot_attempts[i] == 1);
    }
    CHECK(!gd32_raw_is_forbidden(wire, wn));
    /* silent GD32: 3 attempts per slot, then give up without failing hard */
    CHECK(run_knob(FAKE_SILENT, &st, &rec, wire, &wn, 40) == 0);
    CHECK(st.state == 3 && st.timeouts == 18 && wn == 18 * 13);
    for (int i = 0; i < 6; i++) CHECK(st.slot_attempts[i] == 3 && !st.slot_ok[i]);
    /* flaky: 104 needs a retry, 105 never answers */
    CHECK(run_knob(FAKE_FLAKY, &st, &rec, wire, &wn, 60) == 5);
    CHECK(st.state == 2 && st.slot_attempts[4] == 2 && st.slot_ok[4] && !st.slot_ok[5] && st.slot_attempts[5] == 3);
    CHECK(st.timeouts == 1 + 3);
    /* noisy: key events during init are delivered, stale echoes swallowed */
    { int nk = run_knob(FAKE_NOISY, &st, &rec, wire, &wn, 300); if (nk != 6) fprintf(stderr, "  noisy ok=%d to=%d keys=%d frames=%d stale=%d att=%d,%d,%d,%d,%d,%d\n", nk, st.timeouts, rec.keys, rec.frames, st.stale_echoes, st.slot_attempts[0], st.slot_attempts[1], st.slot_attempts[2], st.slot_attempts[3], st.slot_attempts[4], st.slot_attempts[5]); CHECK(nk == 6); }
    CHECK(st.state == 1 && rec.keys == 6 && rec.frames == 6 && st.stale_echoes >= 5);
    CHECK(rec.last[0] == 0x16 && rec.last[2] == 2 && rec.last[3] == 3);
    /* closed fd: tx error path, no crash */
    int sv[2]; socketpair(AF_UNIX, SOCK_STREAM, 0, sv); close(sv[1]);
    gd32_parser p; gd32_parser_init(&p); gd32_knob_opts o = {20, 2};
    signal(SIGPIPE, SIG_IGN);
    CHECK(gd32_knob_init(sv[0], &p, &o, &st, NULL, NULL) == 0 && st.state == 3 && st.tx_errors > 0);
    close(sv[0]);
    /* gd32_tx refuses non-allow-listed payloads before touching the fd */
    uint8_t bad[6] = {0x05, 0, 3, 3, 0, 0};
    errno = 0; CHECK(gd32_tx(-1, bad, 6) == -1 && errno == EPERM);
    uint8_t jump[1] = {0x19};
    errno = 0; CHECK(gd32_tx(-1, jump, 1) == -1 && errno == EPERM);
}

/* ---- JPEG ---- */
static uint8_t *slurp(const char *name, size_t *n)
{
    char path[256]; snprintf(path, sizeof path, "%s/%s", TEST_DATA, name);
    FILE *f = fopen(path, "rb"); if (!f) { fprintf(stderr, "missing %s\n", path); *n = 0; return NULL; }
    fseek(f, 0, SEEK_END); long l = ftell(f); fseek(f, 0, SEEK_SET);
    uint8_t *b = malloc((size_t)l); *n = fread(b, 1, (size_t)l, f); fclose(f); return b;
}

static void conf(const char *base, int w, int h)
{
    char a[64], b[64]; snprintf(a, sizeof a, "%s.jpg", base); snprintf(b, sizeof b, "%s.rgb", base);
    size_t jn, rn; uint8_t *j = slurp(a, &jn), *ref = slurp(b, &rn);
    CHECK(j && ref && rn == (size_t)w * h * 3);
    if (!j || !ref) { free(j); free(ref); return; }
    uint8_t *got = malloc(rn);
    CHECK(jpeg_decode888(j, jn, got, w, h) == 0);
    int maxd = 0; double sum = 0; size_t big = 0;
    for (size_t i = 0; i < rn; i++) { int d = abs(got[i] - ref[i]); if (d > maxd) maxd = d; sum += d; big += d > 24; }
    /* stb vs libjpeg: different IDCT and chroma-upsampling filters (edges of odd-sized
     * 4:2:x images differ most); must stay visually identical on average */
    int ok = sum / rn < 2.0 && big * 100 <= rn && maxd <= 96;
    if (!ok) fprintf(stderr, "  %s maxd=%d mean=%.2f big=%zu\n", base, maxd, sum / rn, big);
    CHECK(ok);
    free(j); free(ref); free(got);
}

static void test_jpeg(void)
{
    conf("small_420", 64, 48); conf("small_444", 64, 48); conf("small_prog", 61, 37); conf("small_422", 33, 17);
    size_t n; uint8_t *j = slurp("canvas.jpg", &n);
    uint16_t *cv = calloc(CANVAS_W * CANVAS_H, 2);
    CHECK(j && jpeg_decode565(j, n, cv, CANVAS_W, CANVAS_H) == 0);
    for (int r = 0; r < 4; r++) for (int c = 0; c < 5; c++) {
        uint16_t px = cv[(144 + r * 128 + 64) * CANVAS_W + c * 128 + 64];
        int R = (px >> 11) << 3, G = ((px >> 5) & 63) << 2, B = (px & 31) << 3;
        int er = (c * 60) & 255, eg = (r * 80) & 255, eb = (255 - c * 40 - r * 20) & 255;
        CHECK(abs(R - er) <= 12 && abs(G - eg) <= 12 && abs(B - eb) <= 12);
    }
    CHECK(cv[10 * CANVAS_W + 300] == 0xFFFF || cv[10 * CANVAS_W + 300] >= 0xF7BE);
    /* wrong size / corrupt / truncated: rejected, canvas untouched */
    size_t wn; uint8_t *w = slurp("wrong.jpg", &wn);
    uint16_t before = cv[0];
    CHECK(w && jpeg_decode565(w, wn, cv, CANVAS_W, CANVAS_H) == -2 && cv[0] == before);
    CHECK(jpeg_decode565(j, n / 3, cv, CANVAS_W, CANVAS_H) <= 0);
    CHECK(jpeg_decode565((const uint8_t *)"nope", 4, cv, CANVAS_W, CANVAS_H) == -1);
    CHECK(jpeg_decode565(NULL, 0, cv, CANVAS_W, CANVAS_H) == -1);
    /* fuzz: bit flips in a valid JPEG must never crash (ASan) */
    srand(99);
    uint8_t *m = malloc(n);
    for (int it = 0; it < 300 && j; it++) {
        memcpy(m, j, n);
        for (int k = 0; k < 1 + it % 8; k++) m[(size_t)rand() % n] ^= (uint8_t)(1 << (rand() % 8));
        (void)jpeg_decode565(m, n, cv, CANVAS_W, CANVAS_H);
    }
    CHECK(rgb565(255, 255, 255) == 0xFFFF && rgb565(255, 0, 0) == 0xF800 && rgb565(0, 255, 0) == 0x07E0 && rgb565(0, 0, 255) == 0x001F);
    free(m); free(j); free(w); free(cv);
}

/* ---- daemon core over a socketpair host link ---- */
typedef struct { host_parser hp; int json, jpg, tile; char lastjson[9000]; uint32_t lastid; } hostrx;

static void drain(int fd, hostrx *rx)
{
    uint8_t b[65536];
    for (;;) {
        struct pollfd pf = {fd, POLLIN, 0};
        if (poll(&pf, 1, 0) <= 0) break;
        ssize_t n = read(fd, b, sizeof b); if (n <= 0) break;
        host_push(&rx->hp, b, (size_t)n);
    }
    host_frame f;
    while (host_next(&rx->hp, &f)) {
        rx->lastid = f.id;
        if (f.cmd == HOST_CMD_JSON) { rx->json++; size_t l = f.size < sizeof rx->lastjson - 1 ? f.size : sizeof rx->lastjson - 1; memcpy(rx->lastjson, f.data, l); rx->lastjson[l] = 0; }
        if (f.cmd == HOST_CMD_JPG) rx->jpg++;
        if (f.cmd == HOST_CMD_TILE565) rx->tile++;
    }
}

static void send_frame(kbd_t *k, uint32_t id, uint32_t cmd, const void *pl, size_t n)
{
    uint8_t *b = malloc(n + 24); int l = host_encode(id, cmd, pl, n, b, n + 24);
    /* deliver in odd-sized chunks to exercise reassembly */
    for (int off = 0; off < l; off += 777) kbd_host_feed(k, b + off, (size_t)(l - off < 777 ? l - off : 777));
    free(b);
}

static void test_kbd_core(void)
{
    char dir[] = "/tmp/mk20kbdXXXXXX", bl[] = "/tmp/mk20blXXXXXX", path[256];
    CHECK(mkdtemp(dir) && mkdtemp(bl));
    for (int i = 1; i <= 21; i++) { snprintf(path, sizeof path, "%s/fb%d", dir, i); FILE *f = fopen(path, "w"); fclose(f); }
    const char *blf[] = {"name", "command", "param", "start"};
    for (int i = 0; i < 4; i++) { snprintf(path, sizeof path, "%s/%s", bl, blf[i]); FILE *f = fopen(path, "w"); fclose(f); }
    kbd_t k; CHECK(kbd_init(&k, dir, bl, "test-1") == 0);
    int sv[2]; socketpair(AF_UNIX, SOCK_STREAM, 0, sv);
    k.host_fd = sv[0];
    hostrx rx; memset(&rx, 0, sizeof rx); host_parser_init(&rx.hp, 1 << 20);
    kbd_draw_idle(&k); CHECK(kbd_flush(&k) == 21);

    /* getInfo with the console's 1 MiB-style '0' preamble in front */
    uint8_t *pre = malloc(300000); memset(pre, '0', 300000); kbd_host_feed(&k, pre, 300000); free(pre);
    const char gi[] = "{\"method\":\"getInfo\"}";
    send_frame(&k, 7, HOST_CMD_JSON, gi, sizeof gi - 1); drain(sv[1], &rx);
    CHECK(rx.json == 1 && rx.lastid == 7 && strstr(rx.lastjson, "\"ack_method\":\"getInfo\"") && strstr(rx.lastjson, "\"deviceVersion\":\"test-1\""));

    /* key event: GD32 16 01 R=2 C=3 -> keyStateChanged col=3,row=2, id 0xFFFFFFFF */
    uint8_t kp[6] = {0x16, 1, 2, 3, 0, 0};
    kbd_on_gd32(&k, kp, 6); drain(sv[1], &rx);
    CHECK(rx.json == 2 && rx.lastid == 0xFFFFFFFFu && !strcmp(rx.lastjson, "{\"method\":\"keyStateChanged\",\"parameters\":{\"col\":3,\"pressed\":true,\"row\":2}}"));
    /* K2 cw -> col=row=105 */
    uint8_t k2[4] = {0x16, 1, 105, 105};
    kbd_on_gd32(&k, k2, 4); drain(sv[1], &rx);
    CHECK(strstr(rx.lastjson, "\"col\":105") && strstr(rx.lastjson, "\"row\":105") && k.st.knob_events == 1);
    /* echo frames are consumed, not events */
    uint8_t echo[6] = {0x05, 0, 103, 103, 0, 0};
    int before = rx.json; kbd_on_gd32(&k, echo, 6); drain(sv[1], &rx);
    CHECK(rx.json == before && k.st.echo_frames == 1 && k.st.key_events == 1);
    /* out-of-matrix / unknown frames ignored */
    uint8_t odd[4] = {0x16, 1, 7, 9}, unk[2] = {0x0D, 1};
    kbd_on_gd32(&k, odd, 4); kbd_on_gd32(&k, unk, 2); drain(sv[1], &rx);
    CHECK(rx.json == before && k.st.other_frames == 2);

    /* JPEG canvas: ack-paced (same id, empty cmd 100), only changed panels written */
    size_t jn; uint8_t *j = slurp("canvas.jpg", &jn);
    unsigned w0[PANEL_COUNT]; memcpy(w0, k.scr.writes, sizeof w0);
    send_frame(&k, 41, HOST_CMD_JPG, j, jn); drain(sv[1], &rx);
    CHECK(rx.jpg == 1 && rx.lastid == 41 && k.st.jpg_ok == 1);
    int changed = 0; for (int i = 0; i < PANEL_COUNT; i++) changed += k.scr.writes[i] != w0[i];
    CHECK(changed == 21);
    snprintf(path, sizeof path, "%s/fb20", dir);   /* key col 4,row 3 */
    FILE *f = fopen(path, "rb"); uint16_t px[128 * 128]; CHECK(f && fread(px, 2, 128 * 128, f) == 128 * 128); if (f) fclose(f);
    int R = (px[64 * 128 + 64] >> 11) << 3; CHECK(abs(R - 240) <= 12);
    /* same JPEG again: nothing rewritten, still acked */
    memcpy(w0, k.scr.writes, sizeof w0);
    send_frame(&k, 42, HOST_CMD_JPG, j, jn); drain(sv[1], &rx);
    CHECK(rx.jpg == 2 && rx.lastid == 42 && !memcmp(w0, k.scr.writes, sizeof w0));
    /* corrupt JPEG: still acked so the host never stalls */
    send_frame(&k, 43, HOST_CMD_JPG, "garbage", 7); drain(sv[1], &rx);
    CHECK(rx.jpg == 3 && rx.lastid == 43 && k.st.jpg_bad == 1);
    free(j);

    /* setBacklight */
    const char sb[] = "{\"method\":\"setBacklight\",\"parameters\":{\"level\":80}}";
    send_frame(&k, 50, HOST_CMD_JSON, sb, sizeof sb - 1); drain(sv[1], &rx);
    CHECK(strstr(rx.lastjson, "\"ack_method\":\"setBacklight\",\"success\":true") && k.backlight == 80);
    snprintf(path, sizeof path, "%s/param", bl); f = fopen(path, "r"); char pv[16] = {0}; if (f) { CHECK(fgets(pv, sizeof pv, f) != NULL); fclose(f); }
    CHECK(!strcmp(pv, "204"));
    snprintf(path, sizeof path, "%s/command", bl); f = fopen(path, "r"); char cv[16] = {0}; if (f) { CHECK(fgets(cv, sizeof cv, f) != NULL); fclose(f); }
    CHECK(!strcmp(cv, "setbl"));
    const char sbx[] = "{\"method\":\"setBacklight\",\"parameters\":{\"level\":101}}";
    send_frame(&k, 51, HOST_CMD_JSON, sbx, sizeof sbx - 1); drain(sv[1], &rx);
    CHECK(strstr(rx.lastjson, "\"success\":false") && k.backlight == 80);
    CHECK(kbd_bl_param(0) == 8 && kbd_bl_param(100) == 255);
    /* unsupported method */
    const char sv2[] = "{\"method\":\"setVolume\",\"parameters\":{\"level\":3}}";
    send_frame(&k, 52, HOST_CMD_JSON, sv2, sizeof sv2 - 1); drain(sv[1], &rx);
    CHECK(rx.lastid == 52 && !strcmp(rx.lastjson, "{\"ack_method\":\"setVolume\",\"success\":false,\"errorString\":\"unsupported\"}"));
    /* ack_method in a JSON must not be mistaken for method */
    int jb = rx.json;
    const char am[] = "{\"ack_method\":\"getInfo\"}";
    send_frame(&k, 53, HOST_CMD_JSON, am, sizeof am - 1); drain(sv[1], &rx);
    CHECK(rx.json == jb && k.st.json_bad == 1);

    /* cmd 200 tile: 2x2 into fb15 at (10,20) */
    uint8_t tile[9 + 8] = {15, 10, 0, 20, 0, 2, 0, 2, 0, 0x00, 0xF8, 0x00, 0xF8, 0x00, 0xF8, 0x00, 0xF8};
    unsigned w15 = k.scr.writes[14];
    send_frame(&k, 60, HOST_CMD_TILE565, tile, sizeof tile); drain(sv[1], &rx);
    CHECK(rx.tile == 1 && rx.lastid == 60 && k.scr.writes[14] == w15 + 1 && k.scr.shadow[14][20 * 128 + 10] == 0xF800);
    tile[0] = 22; send_frame(&k, 61, HOST_CMD_TILE565, tile, sizeof tile); drain(sv[1], &rx);
    CHECK(rx.tile == 2 && k.st.tiles == 1);

    /* JSON helpers */
    long v; char s[16];
    CHECK(kbd_json_int("{\"level\" : 42.7}", 16, "level", &v) == 0 && v == 42);
    CHECK(kbd_json_int("{\"level\":}", 10, "level", &v) == -1);
    CHECK(kbd_json_str("{\"method\":\"abc", 14, "method", s, sizeof s) == -1);
    CHECK(kbd_json_str("{\"method\": \"getInfo\"}", 21, "method", s, sizeof s) == 7 && !strcmp(s, "getInfo"));

    /* local mode: key press paints the panel, no host */
    k.host_fd = -1; k.local_ui = 1;
    unsigned w8 = k.scr.writes[7];
    uint8_t kl[6] = {0x16, 1, 1, 2, 0, 0};   /* row 1 col 2 -> fb8 */
    kbd_on_gd32(&k, kl, 6);
    CHECK(k.scr.writes[7] == w8 + 1 && k.scr.shadow[7][64 * 128 + 64] == 0xFFFF);
    kl[1] = 0; kbd_on_gd32(&k, kl, 6);
    CHECK(k.scr.writes[7] == w8 + 2 && k.scr.shadow[7][64 * 128 + 64] != 0xFFFF);

    /* status file */
    snprintf(path, sizeof path, "%s/status.json", dir);
    k.knob.state = 1; k.knob.ok_count = 6;
    CHECK(kbd_write_status(&k, path) == 0);
    f = fopen(path, "r"); char sbuf[2048] = {0}; if (f) { CHECK(fread(sbuf, 1, sizeof sbuf - 1, f) > 0); fclose(f); }
    CHECK(strstr(sbuf, "\"knob_init\":\"ok\"") && strstr(sbuf, "\"panels_written\":21") && strstr(sbuf, "\"echo_frames\":1"));
    unlink(path);

    close(sv[0]); close(sv[1]); host_parser_free(&rx.hp); kbd_free(&k);
    for (int i = 1; i <= 21; i++) { snprintf(path, sizeof path, "%s/fb%d", dir, i); unlink(path); }
    for (int i = 0; i < 4; i++) { snprintf(path, sizeof path, "%s/%s", bl, blf[i]); unlink(path); }
    rmdir(dir); rmdir(bl);
}
