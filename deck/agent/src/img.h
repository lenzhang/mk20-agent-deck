#ifndef MK20_IMG_H
#define MK20_IMG_H
#include "mem.h"
/* 0 success, -1 invalid/encoding/allocation failure, -2 exceeds OAI_IMAGE_MAX
 * after one retry at 2/3 quality. Caller releases *out with mem_free. */
int img_jpeg(mem_t *m, const unsigned char *rgb, unsigned w, unsigned h, int quality, unsigned char **out, size_t *n);
#endif
