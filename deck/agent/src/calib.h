#ifndef MK20_CALIB_H
#define MK20_CALIB_H
#include <stddef.h>
#define CALIB_PATH "/data/mk20/agent/calib.json"
typedef struct { double hx, hy, px, py; } calib_pair_t;
typedef struct {
    int valid, width, height, logical_width, logical_height;
    double forward[2][3], inverse[2][3], max_error;
    calib_pair_t pairs[5];
} calib_t;
/* Exactly four HID corners and center, in any order. Pixels are logical pixels. */
int calib_fit(const calib_pair_t pairs[5], int width, int height, calib_t *out);
/* For scaled/Retina frames, residuals are checked in logical pixels. */
int calib_fit_logical(const calib_pair_t pairs[5], int width, int height,
                       int logical_width, int logical_height, calib_t *out);
int calib_map(const calib_t *c, double px, double py, int *hx, int *hy);
int calib_save(const char *path, const calib_t *c);
int calib_load(const char *path, calib_t *c);
#endif
