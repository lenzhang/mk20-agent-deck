#ifndef MK20_UI_FONT_H
#define MK20_UI_FONT_H
#include <stddef.h>
#include <stdint.h>
typedef struct { uint32_t codepoint; unsigned char width; uint16_t rows[16]; } ui_glyph_t;
extern const ui_glyph_t UI_GLYPHS[];
extern const size_t UI_GLYPH_COUNT;
/* Strict UTF-8, malformed input consumes one byte and yields visible replacement. */
uint32_t ui_font_next(const char **s);
int ui_font_has(uint32_t cp);
int ui_font_coverage(const char *s);
void ui_font_draw(uint16_t *p, int w, int h, int x, int y, const char *s, uint16_t color, int max_width);
/* Same glyphs at an integer scale (2 = 16x32/32x32 cells); clipped, ellipsis on overflow. */
void ui_font_draw_scaled(uint16_t *p, int w, int h, int x, int y, const char *s, uint16_t color, int max_width, int scale);
int ui_font_width(const char *s); /* unscaled pixel width */
#endif
