#include "jpeg.h"
#include <string.h>
#define STBI_ONLY_JPEG
#define STBI_NO_STDIO
#define STBI_NO_LINEAR
#define STBI_NO_HDR
#define STBI_NO_FAILURE_STRINGS
#define STBI_MAX_DIMENSIONS 4096
#define STB_IMAGE_IMPLEMENTATION
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wsign-compare"
#pragma GCC diagnostic ignored "-Wunused-function"
#pragma GCC diagnostic ignored "-Wunused-parameter"
#pragma GCC diagnostic ignored "-Wimplicit-fallthrough"
#pragma GCC diagnostic ignored "-Wmisleading-indentation"
#pragma GCC diagnostic ignored "-Wtype-limits"
#pragma GCC diagnostic ignored "-Wdouble-promotion"
#include "vendor/stb_image.h"
#pragma GCC diagnostic pop

static uint8_t *dec(const uint8_t *jpg, size_t n, int w, int h, int *err)
{
    int iw, ih, comp;
    if (!jpg || n < 4 || n > (64u << 20)) { *err = -1; return NULL; }
    if (!stbi_info_from_memory(jpg, (int)n, &iw, &ih, &comp)) { *err = -1; return NULL; }
    if (iw != w || ih != h) { *err = -2; return NULL; }   /* check before allocating */
    uint8_t *px = stbi_load_from_memory(jpg, (int)n, &iw, &ih, &comp, 3);
    if (!px) { *err = -1; return NULL; }
    if (iw != w || ih != h) { stbi_image_free(px); *err = -2; return NULL; }
    *err = 0; return px;
}

int jpeg_decode565(const uint8_t *jpg, size_t n, uint16_t *canvas, int w, int h)
{
    int err; uint8_t *px = dec(jpg, n, w, h, &err);
    if (!px) return err;
    size_t cnt = (size_t)w * h;
    for (size_t i = 0; i < cnt; i++) canvas[i] = rgb565(px[3 * i], px[3 * i + 1], px[3 * i + 2]);
    stbi_image_free(px);
    return 0;
}

int jpeg_decode888(const uint8_t *jpg, size_t n, uint8_t *rgb, int w, int h)
{
    int err; uint8_t *px = dec(jpg, n, w, h, &err);
    if (!px) return err;
    memcpy(rgb, px, (size_t)w * h * 3);
    stbi_image_free(px);
    return 0;
}
