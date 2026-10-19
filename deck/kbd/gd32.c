#include "gd32.h"
#include <string.h>

_Static_assert(GD32_OUT_SET_KEYCODE != GD32_FORBIDDEN_BOOTLOADER_JUMP && GD32_OUT_SET_KEYCODE != 0x02,
               "forbidden GD32 command in allow-list");
_Static_assert(GD32_KNOB_SLOT_FIRST == 100 && GD32_KNOB_SLOT_LAST == 105,
               "0x05 rows <100 write the QMK EEPROM keymap; only encoder slots 100..105 are RAM");

static const uint8_t forbidden_clear[4] = {0x02, 0xAA, 0xE2, 0x78};

enum { S_AA, S_55, S_SUM, S_LEN, S_NLEN, S_DATA, S_F5, S_5F };

void gd32_parser_init(gd32_parser *p) { memset(p, 0, sizeof *p); }
static int feed1(gd32_parser *p, uint8_t b);

/* Wrapper with re-scan: when a candidate frame fails (bad ~LEN, tail or sum),
 * the bytes after its AA are fed again, so a truncated frame cannot swallow the
 * start of the next valid one. */
int gd32_feed(gd32_parser *p, uint8_t b)
{
    int was_idle = (p->state == 0);
    uint32_t fails_before = p->bad_len + p->bad_tail + p->bad_sum;
    if (!was_idle || b == 0xAA) {
        if (p->rawn < (int)sizeof p->raw) p->raw[p->rawn++] = b;
    }
    int r = feed1(p, b);
    if (r > 0 || p->state == 0) {
        int failed = (p->bad_len + p->bad_tail + p->bad_sum) != fails_before;
        if (failed && p->depth < 4 && p->rawn > 1) {
            uint8_t tmp[sizeof p->raw]; int n = p->rawn - 1;
            memcpy(tmp, p->raw + 1, (size_t)n);
            p->rawn = 0; p->state = 0; p->depth++;
            int got = 0; uint8_t stash[GD32_MAX_PAYLOAD];
            for (int i = 0; i < n; i++) { int g = gd32_feed(p, tmp[i]); if (g > 0) { got = g; memcpy(stash, p->buf, (size_t)g); } }
            p->depth--;
            if (got > 0) memcpy(p->buf, stash, (size_t)got); /* later partial candidate may have reused buf */
            return got;
        }
        if (p->state == 0) p->rawn = 0;
    } else if (p->state == 1 && p->rawn > 1 && b == 0xAA) {
        p->raw[0] = 0xAA; p->rawn = 1; /* restarted on a new AA */
    }
    return r;
}

static int feed1(gd32_parser *p, uint8_t b)
{
    switch (p->state) {
    case S_AA: if (b == 0xAA) p->state = S_55; else p->resync_bytes++; return 0;
    case S_55:
        if (b == 0x55) p->state = S_SUM;
        else { p->resync_bytes++; p->state = (b == 0xAA) ? S_55 : S_AA; }
        return 0;
    case S_SUM: p->sum = b; p->state = S_LEN; return 0;
    case S_LEN: p->len = b; p->state = S_NLEN; return 0;
    case S_NLEN:
        if ((uint8_t)~p->len != b || p->len == 0 || p->len > GD32_MAX_IN_PAYLOAD) { p->bad_len++; p->state = (b == 0xAA) ? S_55 : S_AA; return 0; }
        p->n = 0; p->state = S_DATA; return 0;
    case S_DATA:
        p->buf[p->n++] = b;
        if (p->n == p->len) p->state = S_F5;
        return 0;
    case S_F5:
        if (b != 0xF5) { p->bad_tail++; p->state = (b == 0xAA) ? S_55 : S_AA; return 0; }
        p->state = S_5F; return 0;
    case S_5F: {
        p->state = S_AA;
        if (b != 0x5F) { p->bad_tail++; if (b == 0xAA) p->state = S_55; return 0; }
        uint8_t s = 0;
        for (int i = 0; i < p->len; i++) s += p->buf[i];
        if (s != p->sum) { p->bad_sum++; return 0; }
        p->frames_ok++;
        return p->len;
    }
    }
    p->state = S_AA;
    return 0;
}

int gd32_decode_key(const uint8_t *pl, int len, gd32_key_event *ev)
{
    if (len < 4 || pl[0] != GD32_IN_KEY_EVENT) return -1;
    ev->pressed = pl[1] ? 1 : 0;
    ev->row = pl[2];
    ev->col = pl[3];
    ev->keycode = (len >= 6) ? (uint16_t)((pl[4] << 8) | pl[5]) : 0;
    ev->knob = 0; ev->knob_action = KNOB_NONE;
    if (ev->row == ev->col && ev->row >= 100 && ev->row <= 105) {
        int i = ev->row - 100;
        ev->knob = (uint8_t)(1 + i / 3);
        ev->knob_action = (uint8_t)(KNOB_PRESS + i % 3);
    }
    return 0;
}

int gd32_raw_is_forbidden(const uint8_t *raw, size_t len)
{
    for (size_t i = 0; i + 4 <= len; i++)
        if (!memcmp(raw + i, forbidden_clear, 4)) return 1;
    /* any frame whose first payload byte is 0x19 */
    for (size_t i = 0; i + 6 <= len; i++)
        if (raw[i] == 0xAA && raw[i + 1] == 0x55 && (uint8_t)~raw[i + 3] == raw[i + 4] &&
            raw[i + 5] == GD32_FORBIDDEN_BOOTLOADER_JUMP)
            return 1;
    return 0;
}

int gd32_knob_payload(int slot, uint8_t out[GD32_KNOB_PAYLOAD_LEN])
{
    if (slot < GD32_KNOB_SLOT_FIRST || slot > GD32_KNOB_SLOT_LAST) return -1;
    out[0] = GD32_OUT_SET_KEYCODE; out[1] = 0; out[2] = (uint8_t)slot; out[3] = (uint8_t)slot; out[4] = 0; out[5] = 0;
    return GD32_KNOB_PAYLOAD_LEN;
}

int gd32_out_allowed(const uint8_t *pl, size_t len)
{
    if (!pl || len != GD32_KNOB_PAYLOAD_LEN) return 0;
    if (pl[0] != GD32_OUT_SET_KEYCODE) return 0;          /* also excludes 0x19, 0x02, 0x0A, 0x0B, ... */
    if (pl[1] != 0) return 0;                               /* layer 0 */
    if (pl[2] < GD32_KNOB_SLOT_FIRST || pl[2] > GD32_KNOB_SLOT_LAST) return 0; /* <100 = EEPROM keymap */
    if (pl[3] != pl[2]) return 0;                           /* col == row, as KD writes it */
    if (pl[4] != 0 || pl[5] != 0) return 0;                 /* KC_NO only: report over serial */
    if (gd32_raw_is_forbidden(pl, len)) return 0;
    return 1;
}

static int build(const uint8_t *pl, size_t len, uint8_t *out, size_t cap)
{
    if (len == 0 || len > GD32_MAX_PAYLOAD || cap < len + GD32_FRAME_OVERHEAD) return -1;
    uint8_t s = 0;
    for (size_t i = 0; i < len; i++) s += pl[i];
    out[0] = 0xAA; out[1] = 0x55; out[2] = s; out[3] = (uint8_t)len; out[4] = (uint8_t)~len;
    memcpy(out + 5, pl, len);
    out[5 + len] = 0xF5; out[6 + len] = 0x5F;
    return (int)(len + GD32_FRAME_OVERHEAD);
}

int gd32_encode(const uint8_t *pl, size_t len, uint8_t *out, size_t cap)
{
    if (!gd32_out_allowed(pl, len)) return -1;
    int n = build(pl, len, out, cap);
    if (n < 0 || gd32_raw_is_forbidden(out, (size_t)n)) return -1;
    return n;
}

int gd32_build_test_frame(const uint8_t *pl, size_t len, uint8_t *out, size_t cap)
{
    return build(pl, len, out, cap);
}
