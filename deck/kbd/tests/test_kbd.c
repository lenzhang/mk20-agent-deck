#include "gd32.h"
#include "host.h"
#include "screen.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <poll.h>

static int fails, checks;
#define CHECK(c) do { checks++; if (!(c)) { fails++; fprintf(stderr, "FAIL %s:%d %s\n", __FILE__, __LINE__, #c); } } while (0)

static int feed_all(gd32_parser *p, const uint8_t *b, size_t n, uint8_t *last, int *lastlen)
{
    int frames = 0;
    for (size_t i = 0; i < n; i++) { int l = gd32_feed(p, b[i]); if (l > 0) { frames++; memcpy(last, p->buf, l); *lastlen = l; } }
    return frames;
}

static void test_gd32_roundtrip(void)
{
    uint8_t pl[6] = {0x16, 1, 3, 2, 0x00, 0x00}, fr[32], last[256]; int ll = 0;
    int n = gd32_build_test_frame(pl, 6, fr, sizeof fr);
    CHECK(n == 13 && fr[0] == 0xAA && fr[1] == 0x55 && fr[3] == 6 && fr[4] == 0xF9 && fr[11] == 0xF5 && fr[12] == 0x5F);
    CHECK(fr[2] == (uint8_t)(0x16 + 1 + 3 + 2));
    gd32_parser p; gd32_parser_init(&p);
    CHECK(feed_all(&p, fr, n, last, &ll) == 1 && ll == 6 && !memcmp(last, pl, 6));
    gd32_key_event ev; CHECK(gd32_decode_key(last, ll, &ev) == 0);
    CHECK(ev.pressed == 1 && ev.row == 3 && ev.col == 2 && ev.keycode == 0 && ev.knob == 0);
    uint8_t kn[6] = {0x16, 0, 101, 101, 0x12, 0x34};
    CHECK(gd32_decode_key(kn, 6, &ev) == 0 && ev.col == 101 && ev.row == 101 && !ev.pressed && ev.keycode == 0x1234);
    CHECK(ev.knob == 1 && ev.knob_action == KNOB_CCW);
    uint8_t notkey[2] = {0x05, 0};
    CHECK(gd32_decode_key(notkey, 2, &ev) == -1);
}

static void test_gd32_corrupt(void)
{
    uint8_t pl[6] = {0x16, 1, 0, 0, 0, 0}, fr[32], last[256]; int ll;
    int n = gd32_build_test_frame(pl, 6, fr, sizeof fr);
    gd32_parser p;
    /* each single-byte corruption must not yield a wrong frame */
    for (int i = 0; i < n; i++) for (int bit = 0; bit < 8; bit++) {
        uint8_t c[32]; memcpy(c, fr, n); c[i] ^= (uint8_t)(1 << bit);
        gd32_parser_init(&p); ll = 0;
        int f = feed_all(&p, c, n, last, &ll);
        CHECK(f == 0 || (ll == 6 && !memcmp(last, pl, 6) && i >= 5 && i < 11 && 0));
        (void)f;
    }
    /* resync: garbage + frame + truncated + frame */
    uint8_t s[128]; size_t k = 0;
    const uint8_t junk[] = {0x00, 0xAA, 0xAA, 0x55, 0x01, 0xFF, 0x13, 0xF5};
    memcpy(s + k, junk, sizeof junk); k += sizeof junk;
    memcpy(s + k, fr, n); k += n;
    memcpy(s + k, fr, 7); k += 7;
    memcpy(s + k, fr, n); k += n;
    gd32_parser_init(&p);
    int f = feed_all(&p, s, k, last, &ll);
    CHECK(f == 2);
    /* inbound LEN cap: a 65-byte payload is refused, 64 accepted */
    uint8_t bigp[65] = {0x0D}, bigf[80];
    gd32_parser_init(&p); n = gd32_build_test_frame(bigp, 65, bigf, sizeof bigf);
    CHECK(feed_all(&p, bigf, n, last, &ll) == 0 && p.bad_len >= 1);
    gd32_parser_init(&p); n = gd32_build_test_frame(bigp, 64, bigf, sizeof bigf);
    CHECK(feed_all(&p, bigf, n, last, &ll) == 1 && ll == 64);
}

static void test_gd32_fuzz(void)
{
    srand(12345);
    gd32_parser p; gd32_parser_init(&p);
    uint8_t last[256]; int ll;
    for (int round = 0; round < 2000; round++) {
        uint8_t b[512]; for (int i = 0; i < 512; i++) b[i] = (uint8_t)rand();
        feed_all(&p, b, sizeof b, last, &ll); /* must not crash (ASan) */
    }
    /* valid frames still parse after fuzz noise */
    uint8_t pl[6] = {0x16, 0, 4, 3, 0, 0}, fr[32];
    int n = gd32_build_test_frame(pl, 6, fr, sizeof fr);
    int f = 0; for (int r = 0; r < 3; r++) f += feed_all(&p, fr, n, last, &ll);
    CHECK(f >= 2);
}

static int expect_allowed(const uint8_t *p, size_t n)
{
    return n == 6 && p[0] == 0x05 && p[1] == 0 && p[2] >= 100 && p[2] <= 105 && p[3] == p[2] && p[4] == 0 && p[5] == 0;
}

static void test_gd32_allowlist(void)
{
    uint8_t out[300];
    uint8_t boot[1] = {0x19};
    CHECK(!gd32_out_allowed(boot, 1));
    CHECK(gd32_encode(boot, 1, out, sizeof out) == -1);
    uint8_t boot6[6] = {0x19, 0, 100, 100, 0, 0};
    CHECK(gd32_encode(boot6, 6, out, sizeof out) == -1);
    uint8_t clear[4] = {0x02, 0xAA, 0xE2, 0x78};
    CHECK(gd32_encode(clear, 4, out, sizeof out) == -1);
    CHECK(!gd32_out_allowed(NULL, 6));
    /* every id x every row, the canonical shape: only 05 with 100..105 */
    int allowed = 0;
    for (int id = 0; id < 256; id++) for (int r = 0; r < 256; r++) {
        uint8_t pl[6] = {(uint8_t)id, 0, (uint8_t)r, (uint8_t)r, 0, 0};
        int ok = gd32_encode(pl, 6, out, sizeof out) > 0;
        allowed += ok;
        if (ok != (id == 5 && r >= 100 && r <= 105)) { CHECK(0); fprintf(stderr, "  id=%d r=%d\n", id, r); }
    }
    CHECK(allowed == 6);
    /* EEPROM rows (<100) refused for every col/keycode */
    for (int r = 0; r < 100; r++) for (int c = 0; c < 256; c += 3) {
        uint8_t pl[6] = {5, 0, (uint8_t)r, (uint8_t)c, 0, 0};
        if (gd32_out_allowed(pl, 6)) CHECK(0);
    }
    /* single-byte mutations of each valid payload: only the identity passes */
    for (int s = 100; s <= 105; s++) {
        uint8_t v[6]; CHECK(gd32_knob_payload(s, v) == 6);
        for (int i = 0; i < 6; i++) for (int x = 0; x < 256; x++) {
            uint8_t m[6]; memcpy(m, v, 6); m[i] = (uint8_t)x;
            if (gd32_out_allowed(m, 6) != expect_allowed(m, 6)) CHECK(0);
        }
        /* any length other than 6 with a valid prefix */
        uint8_t big[16] = {0}; memcpy(big, v, 6);
        for (size_t n = 0; n <= 16; n++) if (n != 6 && gd32_out_allowed(big, n)) CHECK(0);
    }
    CHECK(gd32_knob_payload(99, out) == -1 && gd32_knob_payload(106, out) == -1);
    /* random fuzz over all ids / lengths */
    srand(424242);
    long agree = 0, total = 0;
    for (int it = 0; it < 400000; it++) {
        uint8_t pl[12]; size_t n = (size_t)(rand() % 12) + 1;
        for (size_t i = 0; i < n; i++) pl[i] = (uint8_t)rand();
        if (it & 1) { pl[0] = 5; if (n > 1 && (it & 2)) pl[1] = 0; if (n > 3 && (it & 4)) { pl[2] = (uint8_t)(98 + rand() % 10); pl[3] = pl[2]; } if (n > 5 && (it & 8)) pl[4] = pl[5] = 0; }
        if (it % 97 == 0 && n >= 4) memcpy(pl + (n - 4), clear, 4);
        int got = gd32_encode(pl, n, out, sizeof out) > 0;
        total++; agree += got == expect_allowed(pl, n);
        if (got) CHECK(!gd32_raw_is_forbidden(out, (size_t)(n + 7)));
    }
    CHECK(agree == total);
    /* raw gate catches the sequences anywhere */
    uint8_t raw1[] = {0x00, 0x02, 0xAA, 0xE2, 0x78, 0x00};
    CHECK(gd32_raw_is_forbidden(raw1, sizeof raw1));
    uint8_t bootfr[16]; int n = gd32_build_test_frame(boot, 1, bootfr, sizeof bootfr);
    CHECK(n > 0 && gd32_raw_is_forbidden(bootfr, (size_t)n));
    uint8_t keyfr[16]; uint8_t kp[6] = {0x16, 1, 0, 0, 0, 0}; n = gd32_build_test_frame(kp, 6, keyfr, sizeof keyfr);
    CHECK(!gd32_raw_is_forbidden(keyfr, (size_t)n));
}

/* exact bytes from K2-KNOB-CONFIG.md */
static void test_knob_frames(void)
{
    static const uint8_t want[6][13] = {
        {0xAA,0x55,0xCD,0x06,0xF9,0x05,0x00,0x64,0x64,0x00,0x00,0xF5,0x5F},
        {0xAA,0x55,0xCF,0x06,0xF9,0x05,0x00,0x65,0x65,0x00,0x00,0xF5,0x5F},
        {0xAA,0x55,0xD1,0x06,0xF9,0x05,0x00,0x66,0x66,0x00,0x00,0xF5,0x5F},
        {0xAA,0x55,0xD3,0x06,0xF9,0x05,0x00,0x67,0x67,0x00,0x00,0xF5,0x5F},
        {0xAA,0x55,0xD5,0x06,0xF9,0x05,0x00,0x68,0x68,0x00,0x00,0xF5,0x5F},
        {0xAA,0x55,0xD7,0x06,0xF9,0x05,0x00,0x69,0x69,0x00,0x00,0xF5,0x5F},
    };
    for (int i = 0; i < 6; i++) {
        uint8_t pl[6], fr[32]; gd32_knob_payload(100 + i, pl);
        int n = gd32_encode(pl, 6, fr, sizeof fr);
        CHECK(n == 13 && !memcmp(fr, want[i], 13));
    }
    /* K2 events decode as knob 2 */
    gd32_key_event ev;
    uint8_t p1[6] = {0x16, 1, 0x67, 0x67, 0, 0}, p0[4] = {0x16, 0, 0x67, 0x67}, ccw[4] = {0x16, 1, 0x68, 0x68}, cw[4] = {0x16, 1, 0x69, 0x69};
    CHECK(gd32_decode_key(p1, 6, &ev) == 0 && ev.knob == 2 && ev.knob_action == KNOB_PRESS && ev.pressed);
    CHECK(gd32_decode_key(p0, 4, &ev) == 0 && ev.knob == 2 && ev.knob_action == KNOB_PRESS && !ev.pressed);
    CHECK(gd32_decode_key(ccw, 4, &ev) == 0 && ev.knob == 2 && ev.knob_action == KNOB_CCW);
    CHECK(gd32_decode_key(cw, 4, &ev) == 0 && ev.knob == 2 && ev.knob_action == KNOB_CW);
    uint8_t k1[4] = {0x16, 1, 100, 100}, kx[4] = {0x16, 1, 106, 106}, mix[4] = {0x16, 1, 100, 101};
    CHECK(gd32_decode_key(k1, 4, &ev) == 0 && ev.knob == 1 && ev.knob_action == KNOB_PRESS);
    CHECK(gd32_decode_key(kx, 4, &ev) == 0 && ev.knob == 0);
    CHECK(gd32_decode_key(mix, 4, &ev) == 0 && ev.knob == 0);
}

static void test_host(void)
{
    uint8_t out[512], pl[] = "{\"method\":\"getInfo\"}";
    int n = host_encode(7, HOST_CMD_JSON, pl, sizeof pl - 1, out, sizeof out);
    CHECK(n == (int)(sizeof pl - 1) + 24);
    CHECK(host_crc32((const uint8_t *)"123456789", 9) == 0xCBF43926u);
    host_parser p; host_parser_init(&p, 1 << 16);
    uint8_t junk[5] = {1, 2, 0xA1, 0xA5, 9};
    host_push(&p, junk, 5);
    for (int i = 0; i < n; i++) host_push(&p, out + i, 1); /* byte-by-byte */
    host_frame f; int got = 0;
    while (host_next(&p, &f)) { got++; CHECK(f.id == 7 && f.cmd == 101 && f.size == sizeof pl - 1 && !memcmp(f.data, pl, f.size)); }
    CHECK(got == 1);
    /* corrupt data crc -> dropped, next good frame still parsed */
    uint8_t bad[512]; memcpy(bad, out, n); bad[25] ^= 1;
    host_push(&p, bad, n); host_push(&p, out, n);
    got = 0; while (host_next(&p, &f)) got++;
    CHECK(got == 1 && p.bad_data_crc >= 1);
    /* fuzz */
    srand(7); for (int r = 0; r < 500; r++) { uint8_t b[256]; for (int i = 0; i < 256; i++) b[i] = (uint8_t)rand(); host_push(&p, b, 256); while (host_next(&p, &f)) {} }
    host_parser_free(&p);

    char j[8192];
    int jl = host_json_getinfo_ack(j, sizeof j, "x");
    CHECK(jl > 0 && strstr(j, "\"deviceWidth\":640") && strstr(j, "\"deviceHeight\":656"));
    CHECK(strstr(j, "{\"col\":4,\"height\":128,\"isKey\":true,\"row\":3,\"width\":128,\"x\":512,\"y\":528}"));
    CHECK(strstr(j, "{\"height\":142,\"isKey\":false,\"width\":428,\"x\":106,\"y\":0}"));
    CHECK(strstr(j, "],\"screenHeight\":656,\"screenWidth\":640}"));
    CHECK(host_json_getinfo_ack(j, 50, "x") == -1);
    CHECK(host_json_key_state(j, sizeof j, 100, 100, 1) > 0 && !strcmp(j, "{\"method\":\"keyStateChanged\",\"parameters\":{\"col\":100,\"pressed\":true,\"row\":100}}"));
}

static long fsize(const char *p) { struct stat st; return stat(p, &st) ? -1 : st.st_size; }

static void test_screen(void)
{
    char dir[] = "/tmp/mk20fbXXXXXX"; CHECK(mkdtemp(dir) != NULL);
    char path[200];
    for (int i = 1; i <= 21; i++) { snprintf(path, sizeof path, "%s/fb%d", dir, i); FILE *f = fopen(path, "w"); fclose(f); }
    screen_t s; CHECK(screen_init(&s, dir) == 0);
    /* first flush writes everything */
    CHECK(screen_flush_bus(&s, 0) == 10 && screen_flush_bus(&s, 1) == 11);
    snprintf(path, sizeof path, "%s/fb1", dir); CHECK(fsize(path) == 128 * 128 * 2);
    snprintf(path, sizeof path, "%s/fb21", dir); CHECK(fsize(path) == 428 * 142 * 2);
    /* unchanged canvas -> nothing dirty */
    uint16_t *cv = calloc(CANVAS_W * CANVAS_H, 2);
    CHECK(screen_set_canvas565(&s, cv) == 0);
    CHECK(screen_flush_bus(&s, 0) == 0 && screen_flush_bus(&s, 1) == 0);
    /* change one pixel inside key (col 2,row 1) = fb8 (bus 0) */
    cv[(144 + 128 + 5) * CANVAS_W + 256 + 7] = 0xF800;
    CHECK(screen_set_canvas565(&s, cv) == 1 && s.dirty[7]);
    CHECK(screen_flush_bus(&s, 1) == 0 && screen_flush_bus(&s, 0) == 1 && s.writes[7] == 2);
    snprintf(path, sizeof path, "%s/fb8", dir);
    FILE *f = fopen(path, "rb"); uint16_t px[128 * 128]; CHECK(fread(px, 2, 128 * 128, f) == 128 * 128); fclose(f);
    CHECK(px[5 * 128 + 7] == 0xF800 && px[0] == 0);
    /* pixel in the gap between subscreen and keys (y=143) belongs to no panel */
    cv[143 * CANVAS_W + 10] = 0x1234; CHECK(screen_set_canvas565(&s, cv) == 0);
    /* subscreen pixel -> fb21 on bus 0 */
    cv[10 * CANVAS_W + 106 + 3] = 0x07E0; CHECK(screen_set_canvas565(&s, cv) == 1 && s.dirty[20] && PANELS[20].bus == 0);
    /* tile API: fb15 (bus 1) */
    uint16_t t[4] = {1, 2, 3, 4};
    CHECK(screen_set_tile565(&s, 15, 126, 126, 2, 2, t) == 1);
    CHECK(screen_set_tile565(&s, 15, 126, 126, 2, 2, t) == 0 || s.dirty[14]);
    CHECK(screen_set_tile565(&s, 15, 127, 127, 2, 2, t) == -1);
    CHECK(screen_set_tile565(&s, 22, 0, 0, 1, 1, t) == -1);
    CHECK(screen_flush_bus(&s, 1) == 1 && screen_flush_bus(&s, 0) == 1);
    /* missing fb -> error, panel stays dirty */
    snprintf(path, sizeof path, "%s/fb10", dir); unlink(path);
    t[0] = 9; screen_set_tile565(&s, 10, 0, 0, 1, 1, t);
    CHECK(screen_flush_bus(&s, 1) == -1 && s.dirty[9] && s.write_errors == 1);
    /* bus map */
    for (int i = 0; i < 9; i++) CHECK(PANELS[i].bus == 0);
    for (int i = 9; i < 20; i++) CHECK(PANELS[i].bus == 1);
    free(cv); screen_free(&s);
    for (int i = 1; i <= 21; i++) { snprintf(path, sizeof path, "%s/fb%d", dir, i); unlink(path); }
    rmdir(dir);
}

#include "tests_k3.inc"
#include "tests_ctl.inc"

int main(void)
{
    test_gd32_roundtrip(); test_gd32_corrupt(); test_gd32_fuzz(); test_gd32_allowlist(); test_knob_frames();
    test_host(); test_screen();
    test_knob_init(); test_jpeg(); test_kbd_core(); test_writers(); test_hid(); test_hid_replace();
    test_hid_scroll(); test_ctl_lease(); test_ctl_scroll_act(); test_ctl_socket();
    printf("%d checks, %d failures\n", checks, fails);
    return fails ? 1 : 0;
}
