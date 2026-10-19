/* mk20-kbd host link (/dev/ttyGS0), KD-compatible subset.
 * Frame: A1 A5 5A 5E | id u32 | cmd u32 | size u32 | crc32(size LE) | payload | crc32(payload)
 * all little-endian; crc32 = zlib CRC-32. cmd 100 = whole-canvas JPEG, 101 = JSON.
 * New fast path (ours, not in KD): cmd 200 = raw RGB565 tile, 201 = widget/state JSON. */
#ifndef MK20_HOST_H
#define MK20_HOST_H
#include <stddef.h>
#include <stdint.h>

#define HOST_CMD_JPG   100
#define HOST_CMD_JSON  101
#define HOST_CMD_TILE565 200   /* payload: u8 panel(1..21) u16 x u16 y u16 w u16 h + w*h*2 RGB565 LE */
#define HOST_CMD_WIDGET  201
#define HOST_HDR 20
#define HOST_MAX_PAYLOAD (4u << 20)

uint32_t host_crc32(const uint8_t *p, size_t n);

typedef struct {
    uint8_t *buf; size_t len, cap;
    size_t consumed;
    uint32_t frames_ok, bad_size_crc, bad_data_crc, dropped_bytes, oversize;
} host_parser;

typedef struct { uint32_t id, cmd, size; const uint8_t *data; } host_frame;

int host_parser_init(host_parser *p, size_t cap);
void host_parser_free(host_parser *p);
/* Append bytes; then call host_next() until it returns 0. */
int host_push(host_parser *p, const uint8_t *d, size_t n);
/* Returns 1 and fills f (valid until next push/next) when a frame is ready. */
int host_next(host_parser *p, host_frame *f);
/* Encode. Returns total length or -1. */
int host_encode(uint32_t id, uint32_t cmd, const uint8_t *pl, size_t n, uint8_t *out, size_t cap);

/* JSON builders (compact, matching KD keys). Return length or -1. */
int host_json_getinfo_ack(char *out, size_t cap, const char *version);
int host_json_key_state(char *out, size_t cap, int col, int row, int pressed);
#endif
