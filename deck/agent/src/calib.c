#define _POSIX_C_SOURCE 200809L
#include "calib.h"
#include "json_util.h"
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
static int solve(double a[3][4], double out[3])
{
    for (int i = 0; i < 3; i++) {
        int pivot = i;
        for (int j = i + 1; j < 3; j++) if (fabs(a[j][i]) > fabs(a[pivot][i])) pivot = j;
        if (fabs(a[pivot][i]) < 1e-10) return -1;
        for (int k = 0; k < 4; k++) { double t = a[i][k]; a[i][k] = a[pivot][k]; a[pivot][k] = t; }
        double d = a[i][i]; for (int k = i; k < 4; k++) a[i][k] /= d;
        for (int j = 0; j < 3; j++) if (j != i) { double f = a[j][i]; for (int k = i; k < 4; k++) a[j][k] -= f * a[i][k]; }
    }
    for (int i = 0; i < 3; i++) out[i] = a[i][3];
    return 0;
}
int calib_fit_logical(const calib_pair_t p[5], int w, int h, int lw, int lh, calib_t *out)
{
    memset(out, 0, sizeof *out); if (!p || w < 2 || h < 2 || w > 1000000 || h > 1000000 || lw < 2 || lh < 2 || lw > 1000000 || lh > 1000000) return -1;
    unsigned seen = 0;
    for (int i = 0; i < 5; i++) {
        if (!isfinite(p[i].hx) || !isfinite(p[i].hy) || !isfinite(p[i].px) || !isfinite(p[i].py) ||
            p[i].px < 0 || p[i].py < 0 || p[i].px >= w || p[i].py >= h) return -1;
        unsigned bit;
        if (fabs(p[i].hx - 16383.5) <= .5 && fabs(p[i].hy - 16383.5) <= .5) bit = 16;
        else {
            if ((p[i].hx != 0 && p[i].hx != 32767) || (p[i].hy != 0 && p[i].hy != 32767)) return -1;
            bit = 1u << ((p[i].hx > 0 ? 1 : 0) + (p[i].hy > 0 ? 2 : 0));
        }
        if (seen & bit) return -1;
        seen |= bit;
    }
    if (seen != 31) return -1;
    calib_t c = {.width = w, .height = h, .logical_width = lw, .logical_height = lh}; memcpy(c.pairs, p, sizeof c.pairs);
    for (int axis = 0; axis < 2; axis++) {
        double a[3][4] = {{0}};
        for (int i = 0; i < 5; i++) {
            double v[] = {p[i].hx / 32767., p[i].hy / 32767., 1};
            for (int j = 0; j < 3; j++) {
                for (int k = 0; k < 3; k++) a[j][k] += v[j] * v[k];
                a[j][3] += v[j] * (axis ? p[i].py : p[i].px);
            }
        }
        if (solve(a, c.forward[axis])) return -1;
        c.forward[axis][0] /= 32767.; c.forward[axis][1] /= 32767.;
    }
    double a = c.forward[0][0], b = c.forward[0][1], d = c.forward[1][0], e = c.forward[1][1];
    double det = a * e - b * d;
    if (!isfinite(det) || fabs(det) < 1e-12) return -1;
    c.inverse[0][0] = e / det; c.inverse[0][1] = -b / det;
    c.inverse[1][0] = -d / det; c.inverse[1][1] = a / det;
    for (int axis = 0; axis < 2; axis++) c.inverse[axis][2] = -c.inverse[axis][0] * c.forward[0][2] - c.inverse[axis][1] * c.forward[1][2];
    for (int i = 0; i < 5; i++) {
        double x = a * p[i].hx + b * p[i].hy + c.forward[0][2] - p[i].px;
        double y = d * p[i].hx + e * p[i].hy + c.forward[1][2] - p[i].py;
        double error = hypot(x * lw / w, y * lh / h); if (error > c.max_error) c.max_error = error;
    }
    if (!isfinite(c.max_error) || c.max_error > 2) return -1;
    c.valid = 1; *out = c; return 0;
}
int calib_fit(const calib_pair_t p[5], int w, int h, calib_t *out)
{
    return calib_fit_logical(p,w,h,w,h,out);
}
int calib_map(const calib_t *c, double px, double py, int *hx, int *hy)
{
    if (!c || !c->valid || !isfinite(px) || !isfinite(py) || px < 0 || py < 0 || px >= c->width || py >= c->height) return -1;
    int *v[] = {hx, hy};
    for (int i = 0; i < 2; i++) {
        double d = c->inverse[i][0] * px + c->inverse[i][1] * py + c->inverse[i][2];
        if (!isfinite(d) || d < -1 || d > 32768) return -1;
        *v[i] = d < 0 ? 0 : d > 32767 ? 32767 : (int)lround(d);
    }
    return 0;
}
int calib_save(const char *path, const calib_t *c)
{
    if (!path) path = CALIB_PATH;
    calib_t check; if (!c || !c->valid || calib_fit_logical(c->pairs, c->width, c->height, c->logical_width, c->logical_height, &check)) return -1;
    char tmp[4096], dir[4096];
    if (snprintf(tmp, sizeof tmp, "%s.tmp-XXXXXX", path) >= (int)sizeof tmp || strlen(path) >= sizeof dir) return -1;
    strcpy(dir, path); char *slash = strrchr(dir, '/');
    if (slash == dir) slash[1] = 0; else if (slash) *slash = 0; else strcpy(dir, ".");
    int dfd = open(dir, O_RDONLY); if (dfd < 0) return -1;
    int fd = mkstemp(tmp); if (fd < 0) { close(dfd); return -1; }
    cJSON *j = cJSON_CreateObject(); cJSON_AddNumberToObject(j, "version", 1);
    cJSON_AddNumberToObject(j,"logical_width",c->logical_width); cJSON_AddNumberToObject(j,"logical_height",c->logical_height);
    cJSON_AddNumberToObject(j, "width", c->width); cJSON_AddNumberToObject(j, "height", c->height);
    cJSON *pairs = cJSON_AddArrayToObject(j, "pairs");
    for (int i = 0; i < 5; i++) {
        double a[] = {c->pairs[i].hx,c->pairs[i].hy,c->pairs[i].px,c->pairs[i].py};
        cJSON_AddItemToArray(pairs, cJSON_CreateDoubleArray(a, 4));
    }
    char *s = cJSON_PrintUnformatted(j); int rc = -1;
    if (!s || fchmod(fd, 0600)) goto end;
    size_t n = strlen(s), off = 0;
    while (off < n) { ssize_t k = write(fd, s + off, n - off); if (k < 0 && errno == EINTR) continue; if (k <= 0) goto end; off += (size_t)k; }
    if (fsync(fd)) goto end;
    if (close(fd)) { fd = -1; goto end; } fd = -1;
    if (rename(tmp, path) || fsync(dfd)) goto end;
    rc = 0;
end:
    if (fd >= 0) close(fd);
    if (rc) unlink(tmp);
    close(dfd); cJSON_free(s); cJSON_Delete(j); return rc;
}
int calib_load(const char *path, calib_t *out)
{
    memset(out, 0, sizeof *out); int fd = open(path ? path : CALIB_PATH, O_RDONLY | O_NONBLOCK);
    if (fd < 0) return -1;
    struct stat st; char b[4097]; size_t n = 0; int bad = 0;
    if (fstat(fd, &st) || !S_ISREG(st.st_mode) || (st.st_mode & 0777) != 0600 || st.st_uid != geteuid()) bad = 1;
    while (!bad && n < sizeof b) { ssize_t k = read(fd, b + n, sizeof b - n); if (k < 0 && errno == EINTR) continue; if (k < 0) { bad = 1; break; } if (!k) break; n += (size_t)k; }
    close(fd); if (bad) return -1;
    cJSON *j = agent_json_parse(b, n, 4096); int rc = -1;
    const cJSON *v = cJSON_GetObjectItemCaseSensitive(j, "version"), *w = cJSON_GetObjectItemCaseSensitive(j, "width"),
        *h = cJSON_GetObjectItemCaseSensitive(j, "height"), *ps = cJSON_GetObjectItemCaseSensitive(j, "pairs");
    if (!cJSON_IsObject(j) || !cJSON_IsNumber(v) || v->valuedouble != 1 || !cJSON_IsNumber(w) || !cJSON_IsNumber(h) ||
        w->valuedouble < 2 || w->valuedouble > 1000000 || h->valuedouble < 2 || h->valuedouble > 1000000 ||
        floor(w->valuedouble) != w->valuedouble || floor(h->valuedouble) != h->valuedouble || !cJSON_IsArray(ps) || cJSON_GetArraySize(ps) != 5) goto end;
    calib_pair_t p[5];
    for (int i = 0; i < 5; i++) {
        const cJSON *a = cJSON_GetArrayItem(ps, i); double d[4];
        if (!cJSON_IsArray(a) || cJSON_GetArraySize(a) != 4) goto end;
        for (int k = 0; k < 4; k++) { v = cJSON_GetArrayItem(a, k); if (!cJSON_IsNumber(v) || !isfinite(v->valuedouble)) goto end; d[k] = v->valuedouble; }
        p[i] = (calib_pair_t){d[0],d[1],d[2],d[3]};
    }
    const cJSON *lw = cJSON_GetObjectItemCaseSensitive(j,"logical_width"), *lh = cJSON_GetObjectItemCaseSensitive(j,"logical_height");
    if (!cJSON_IsNumber(lw) || !cJSON_IsNumber(lh) || lw->valuedouble < 2 || lw->valuedouble > 1000000 ||
        lh->valuedouble < 2 || lh->valuedouble > 1000000 || floor(lw->valuedouble) != lw->valuedouble || floor(lh->valuedouble) != lh->valuedouble) goto end;
    rc = calib_fit_logical(p,w->valueint,h->valueint,lw->valueint,lh->valueint,out);
end:
    cJSON_Delete(j); return rc;
}
