#include "img.h"
#include "oai.h"
#define STBI_WRITE_NO_STDIO
#include "../vendor/stb/stb_image_write.h"
#include <string.h>
typedef struct { unsigned char *data; size_t n; int overflow; } jpeg_t;
static void append(void *context, void *data, int size)
{
    jpeg_t *j = context;
    if (size < 0 || (size_t)size > OAI_IMAGE_MAX - j->n) { j->overflow = 1; return; }
    if (!j->overflow) { memcpy(j->data + j->n, data, (size_t)size); j->n += (size_t)size; }
}
int img_jpeg(mem_t *m, const unsigned char *rgb, unsigned w, unsigned h, int q, unsigned char **out, size_t *n)
{
    *out = NULL; *n = 0;
    if (!rgb || !w || !h || w > 8192 || h > 8192 || q < 1 || q > 100) return -1;
    jpeg_t j = {mem_alloc(m, OAI_IMAGE_MAX), 0, 0}; if (!j.data) return -1;
    for (int attempt = 0; attempt < 2; attempt++) {
        j.n = 0; j.overflow = 0;
        if (!stbi_write_jpg_to_func(append, &j, (int)w, (int)h, 3, rgb, q)) { mem_free(m, j.data); return -1; }
        if (!j.overflow) { *out = j.data; *n = j.n; return 0; }
        q = q * 2 / 3; if (!q) q = 1;
    }
    mem_free(m, j.data); return -2;
}
