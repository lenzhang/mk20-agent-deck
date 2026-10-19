#include "rfb.h"
#include "des.h"
#include "img.h"
#include "oai.h"
#define STBI_WRITE_NO_STDIO
#include "../vendor/stb/stb_image_write.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void count_jpeg(void *context, void *data, int n) { (void)data; assert(n >= 0); *(size_t *)context += (size_t)n; }
static void units(void)
{
    const unsigned char key[8] = {0x13,0x34,0x57,0x79,0x9b,0xbc,0xdf,0xf1};
    const unsigned char block[8] = {0x01,0x23,0x45,0x67,0x89,0xab,0xcd,0xef};
    const unsigned char want[8] = {0x85,0xe8,0x13,0x54,0x0f,0x0a,0xb4,0x05}; unsigned char out[8];
    des_encrypt_block(key, block, out); assert(!memcmp(out, want, 8));
    mem_t m = {0}; scale_t s;
    assert(scale_init(&s, &m, 8193, 1, 1, 1));
    assert(!scale_init(&s, &m, 2, 2, 1, 1));
    const unsigned char pixels[12] = {0,10,20, 100,110,120, 100,110,120, 200,210,220};
    assert(!scale_tile(&s, 0, 1, 2, 1, pixels + 6, 6));
    assert(scale_finish(&s)); /* incomplete snapshot cannot be published */
    assert(!scale_tile(&s, 0, 0, 2, 1, pixels, 6)); assert(!scale_finish(&s));
    assert(!memcmp(s.data, pixels + 3, 3)); assert(scale_finish(&s));
    scale_free(&s); assert(!m.live);
    assert(!scale_init(&s, &m, 7, 5, 3, 3)); assert(s.w == 3 && s.h == 2); scale_free(&s);
    assert(!scale_init(&s, &m, 2880, 1800, 1280, 800));
    assert(s.bytes == 2 && s.w == 1280 && s.h == 800 && m.live < 7u * 1024 * 1024); scale_free(&s);
    /* Worst coprime numerator uses bounded 32-bit sums, not a huge 64-bit image. */
    assert(!scale_init(&s, &m, 8191, 8191, 1280, 800)); assert(s.bytes == 4 && s.shift); scale_free(&s);
    assert(!mem_alloc(&m, MEM_LIMIT)); assert(!m.live);
    unsigned char *jpg = NULL; size_t n = 0;
    assert(!img_jpeg(&m, pixels, 2, 2, 70, &jpg, &n));
    assert(n < OAI_IMAGE_MAX && n > 2 && jpg[0] == 255 && jpg[1] == 216); mem_free(&m, jpg);
    assert(img_jpeg(&m, pixels, 8193, 1, 70, &jpg, &n)); assert(!m.live);
    unsigned char *gradient = mem_alloc(&m, 1280u * 800 * 3); assert(gradient);
    for (unsigned y = 0; y < 800; y++) for (unsigned x = 0; x < 1280; x++) {
        size_t at = ((size_t)y * 1280 + x) * 3;
        gradient[at] = (unsigned char)x; gradient[at + 1] = (unsigned char)y; gradient[at + 2] = (unsigned char)(x ^ y);
    }
    size_t high = 0, low = 0;
    assert(stbi_write_jpg_to_func(count_jpeg, &high, 1280, 800, 3, gradient, 100));
    assert(stbi_write_jpg_to_func(count_jpeg, &low, 1280, 800, 3, gradient, 66));
    assert(high > OAI_IMAGE_MAX && low <= OAI_IMAGE_MAX);
    assert(!img_jpeg(&m, gradient, 1280, 800, 100, &jpg, &n)); assert(n == low);
    mem_free(&m, jpg); mem_free(&m, gradient); assert(!m.live);
    puts("rfb/scale/img unit checks passed");
}
/* Host-only test probe, exposing lossless scaled pixels instead of JPEG errors.
 * Usage: test_rfb PORT RGB_FILE MAX_W MAX_H FRAMES [UTF8_CLIPBOARD]. */
int main(int argc, char **argv)
{
    if (argc == 1) { units(); return 0; }
    assert(argc == 6 || argc == 7);
    const char *pw = getenv("MK20_VNC_PASSWORD"); if (!pw) pw = "secret12";
    rfb_options_t o = {"127.0.0.1", (unsigned)atoi(argv[1]), (const unsigned char *)pw, strlen(pw), 5000};
    const char *error; rfb_t *c = NULL;
    const char *fd = getenv("MK20_RFB_TEST_FD");
    int opened = fd ? rfb_open_connected(&c, &o, atoi(fd), &error) : rfb_open(&c, &o, &error);
    if (opened) { printf("{\"ok\":false,\"error\":\"%s\"}\n", error); return 1; }
    int clip = 0;
    if (argc == 7) clip = rfb_cut_text(c, argv[6], strlen(argv[6]));
    const char *clip_error = clip ? rfb_error(c) : "";
    for (int i = 0; i < atoi(argv[5]); i++) if (rfb_snapshot(c, (unsigned)atoi(argv[3]), (unsigned)atoi(argv[4]))) {
        printf("{\"ok\":false,\"error\":\"%s\"}\n", rfb_error(c)); rfb_close(c); return 1;
    }
    const scale_t *s = rfb_frame(c); FILE *f = fopen(argv[2], "wb"); assert(f);
    assert(fwrite(s->data, 3, (size_t)s->w * s->h, f) == (size_t)s->w * s->h); assert(!fclose(f));
    printf("{\"ok\":true,\"w\":%u,\"h\":%u,\"extended_clipboard\":%s,\"clip_error\":\"%s\"}\n",
           s->w, s->h, rfb_info(c)->extended_clipboard ? "true" : "false", clip_error);
    rfb_close(c); return 0;
}
