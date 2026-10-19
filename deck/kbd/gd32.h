/* mk20-kbd: GD32 <-> T113 link on /dev/ttyS1 (115200 8N1).
 * Frame (from KD V2.32 STM32CommunicationObject::packFrame/parseByte):
 *   AA 55 SUM LEN ~LEN payload[LEN] F5 5F      SUM = 8-bit sum of payload
 * SAFETY: outbound frames are restricted to an allow-list. 0x19
 * (request_bootloader_jump) and the raw clearState bytes 02 AA E2 78 can
 * never be emitted by this module (compile-time + runtime + tests). */
#ifndef MK20_GD32_H
#define MK20_GD32_H
#include <stddef.h>
#include <stdint.h>

#define GD32_MAX_PAYLOAD 255
#define GD32_FRAME_OVERHEAD 7

/* inbound payload[0] ids observed in KD processData() */
enum gd32_in_id {
    GD32_IN_KEYCODE_ACK = 0x05,
    GD32_IN_MACRO_SIZE  = 0x0D,
    GD32_IN_MACRO_ACK   = 0x0F,
    GD32_IN_MACRO_RESET = 0x10,
    GD32_IN_KEY_EVENT   = 0x16,
};

/* Forbidden outbound ids: never listed in the allow-list. */
#define GD32_FORBIDDEN_BOOTLOADER_JUMP 0x19

/* Outbound allow-list. Phase 1 is RECEIVE-ONLY: the list is empty, so
 * gd32_encode() refuses everything. Adding an id requires editing this
 * macro AND passes the compile-time guard below. */
#define GD32_OUT_ALLOW_LIST(X) /* phase 1: none. e.g. X(0x17) */

typedef struct {
    int state;
    uint8_t sum, len, n;
    uint8_t buf[GD32_MAX_PAYLOAD];
    uint8_t raw[GD32_MAX_PAYLOAD + GD32_FRAME_OVERHEAD]; /* bytes of the current candidate, for re-scan */
    int rawn, depth;
    /* stats */
    uint32_t frames_ok, bad_sum, bad_len, bad_tail, resync_bytes;
} gd32_parser;

typedef struct {
    uint8_t pressed;   /* payload[1] (!=0 => pressed) */
    uint8_t col;       /* payload[2]; knobs use col==row >= 100 */
    uint8_t row;       /* payload[3] */
    uint16_t keycode;  /* payload[4]<<8 | payload[5]: QMK keycode at (row,col),
                          0 == KC_NO. KD's VideoPlayer::keyStateChanged only runs
                          its local action when both bytes are 0 (keyCodeH/L). */
} gd32_key_event;

void gd32_parser_init(gd32_parser *p);
/* Feed one byte. Returns payload length (>0) when a full valid frame is in
 * p->buf, else 0. */
int gd32_feed(gd32_parser *p, uint8_t b);
/* Decode a 0x16 payload. Returns 0 on success, -1 if not a key event. */
int gd32_decode_key(const uint8_t *payload, int len, gd32_key_event *ev);

/* Is this outbound payload allowed? (runtime check) */
int gd32_out_allowed(const uint8_t *payload, size_t len);
/* Encode an outbound frame into out (cap bytes). Returns frame length or
 * -1 if refused (not allow-listed / forbidden / too long). */
int gd32_encode(const uint8_t *payload, size_t len, uint8_t *out, size_t cap);
/* Inbound-style frame builder for tests/simulators ONLY (no allow-list,
 * never linked to a tty writer). */
int gd32_build_test_frame(const uint8_t *payload, size_t len, uint8_t *out, size_t cap);
/* Final gate used by the tty writer: scans the raw bytes for the forbidden
 * clearState sequence and for a 0x19 command frame. */
int gd32_raw_is_forbidden(const uint8_t *raw, size_t len);
#endif
