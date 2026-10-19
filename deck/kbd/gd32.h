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
/* Longest inbound payload we accept. GD32 replies are VIA-sized (<= 32 B); capping
 * LEN makes a false header (e.g. a truncated frame followed by AA 55 ...) fail fast
 * instead of swallowing up to 255 following bytes. */
#define GD32_MAX_IN_PAYLOAD 64

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

/* Outbound allow-list (K2-KNOB-CONFIG.md). EXACTLY ONE frame shape may ever be
 * sent: the 6-byte payload  05 00 r r 00 00  with r in 100..105, i.e. "set GD32
 * encoder slot r to KC_NO" (GD32 RAM only, .bss, cleared at GD32 reset). Every
 * other payload is refused, in particular:
 *   - id 0x19 (bootloader jump) and any buffer containing 02 AA E2 78,
 *   - id 0x05 with row < 100 (dynamic_keymap_set_keycode -> QMK EEPROM write),
 *   - id 0x05 with row > 105, non-zero layer/keycode, col != row, other lengths. */
#define GD32_OUT_SET_KEYCODE 0x05
#define GD32_KNOB_SLOT_FIRST 100
#define GD32_KNOB_SLOT_LAST  105
#define GD32_KNOB_PAYLOAD_LEN 6
#define GD32_IN_ECHO GD32_IN_KEYCODE_ACK  /* GD32 echoes each 0x05 frame identically */

typedef struct {
    int state;
    uint8_t sum, len, n;
    uint8_t buf[GD32_MAX_PAYLOAD];
    uint8_t raw[GD32_MAX_PAYLOAD + GD32_FRAME_OVERHEAD]; /* bytes of the current candidate, for re-scan */
    int rawn, depth;
    /* stats */
    uint32_t frames_ok, bad_sum, bad_len, bad_tail, resync_bytes;
} gd32_parser;

/* Event 16 P R C KH KL. Measured 2026-10-19 (K2-RESULTS.md): byte 2 is the
 * physical row 0..3, byte 3 the column 0..4; stock KD forwarded them to the host
 * as keyStateChanged{col=C,row=R} (0-based, no transposition: NOTES-device-
 * 2026-10-17.md; KD signature VideoPlayer::keyStateChanged(pressed,row,col)).
 * Knobs: R == C in 100..105 (K1 100 press/101 ccw/102 cw, K2 103/104/105). */
enum { KNOB_NONE = 0, KNOB_PRESS = 1, KNOB_CCW = 2, KNOB_CW = 3 };
typedef struct {
    uint8_t pressed;   /* payload[1] (!=0 => pressed) */
    uint8_t row;       /* payload[2]; knobs use row==col >= 100 */
    uint8_t col;       /* payload[3] */
    uint8_t knob;      /* 0 = matrix key, 1 = K1, 2 = K2 */
    uint8_t knob_action; /* KNOB_PRESS/CCW/CW when knob != 0 */
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

/* Build the one allow-listed payload for slot r (100..105). Returns 6 or -1. */
int gd32_knob_payload(int slot, uint8_t out[GD32_KNOB_PAYLOAD_LEN]);
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
