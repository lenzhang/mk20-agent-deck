#ifndef MK20_SCALE_H
#define MK20_SCALE_H
#include "mem.h"
#include <stdint.h>
typedef struct {
    unsigned sw, sh, w, h, sx, sy, dx, dy, bytes, shift;
    uint64_t denom, pixels; void *data; mem_t *mem; int finished;
} scale_t;
int scale_init(scale_t *s, mem_t *m, unsigned sw, unsigned sh, unsigned max_w, unsigned max_h);
/* RGB888 tile/row, stride in bytes. Full snapshots must deliver each source pixel once.
 * Rectangles and tiles may arrive in any order. No full-size source image is retained. */
int scale_tile(scale_t *s, unsigned x, unsigned y, unsigned w, unsigned h, const unsigned char *rgb, size_t stride);
int scale_finish(scale_t *s); /* compacts sums in place to RGB888; data valid until scale_free */
void scale_free(scale_t *s);
#endif
