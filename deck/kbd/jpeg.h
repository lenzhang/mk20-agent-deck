/* JPEG -> RGB565 canvas (vendored stb_image v2.30, JPEG only, no stdio). */
#ifndef MK20_JPEG_H
#define MK20_JPEG_H
#include <stddef.h>
#include <stdint.h>
/* Decode jpg into canvas (w*h RGB565 LE). The image must be exactly w x h.
 * Returns 0, -1 corrupt/unsupported, -2 wrong dimensions. canvas untouched on error. */
int jpeg_decode565(const uint8_t *jpg, size_t n, uint16_t *canvas, int w, int h);
/* Same, RGB888 output (tests). */
int jpeg_decode888(const uint8_t *jpg, size_t n, uint8_t *rgb, int w, int h);
static inline uint16_t rgb565(uint8_t r, uint8_t g, uint8_t b)
{ return (uint16_t)(((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)); }
#endif
