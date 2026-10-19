#define _POSIX_C_SOURCE 200809L
#define _DEFAULT_SOURCE
#ifdef __APPLE__
#define _DARWIN_C_SOURCE
#include <sys/sysctl.h>
#endif
#include "clock.h"
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <unistd.h>

/* Proleptic Gregorian calendar, with years constrained before conversion. */
static int64_t days(int y, unsigned m, unsigned d)
{
    y -= m <= 2;
    int era = y / 400;
    unsigned yo = (unsigned)(y - era * 400);
    unsigned mp = m > 2 ? m - 3 : m + 9;
    return (int64_t)era * 146097 + yo * 365 + yo / 4 - yo / 100 + (153 * mp + 2) / 5 + d - 1 - 719468;
}
static int month(const char *s)
{
    static const char *const names[] = {"Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"};
    for (int i = 0; i < 12; i++) if (!memcmp(s, names[i], 3)) return i + 1;
    return 0;
}
static int dec(const char *s, size_t n)
{
    int v = 0;
    for (size_t i = 0; i < n; i++) { if (s[i] < '0' || s[i] > '9') return -1; v = v * 10 + s[i] - '0'; }
    return v;
}
int agent_date_parse(const char *s, time_t *out)
{
    static const char *const week[] = {"Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"};
    if (!s || strlen(s) != 29 || memcmp(s + 3, ", ", 2) || s[7] != ' ' || s[11] != ' ' ||
        s[16] != ' ' || s[19] != ':' || s[22] != ':' || memcmp(s + 25, " GMT", 4)) return -1;
    int wd = -1;
    for (int i = 0; i < 7; i++) if (!memcmp(s, week[i], 3)) wd = i;
    int d = dec(s + 5, 2), m = month(s + 8), y = dec(s + 12, 4);
    int h = dec(s + 17, 2), min = dec(s + 20, 2), sec = dec(s + 23, 2);
    if (wd < 0 || y < 1970 || y > 9999 || !m || d < 1 || h < 0 || h > 23 || min < 0 || min > 59 || sec < 0 || sec > 59) return -1;
    static const int md[] = {31,28,31,30,31,30,31,31,30,31,30,31};
    int leap = y % 4 == 0 && (y % 100 != 0 || y % 400 == 0);
    if (d > md[m - 1] + (m == 2 && leap)) return -1;
    int64_t ds = days(y, (unsigned)m, (unsigned)d);
    if ((ds + 4) % 7 != wd) return -1;
    int64_t e = ds * 86400 + h * 3600 + min * 60 + sec;
    time_t t = (time_t)e; if ((int64_t)t != e) return -1;
    *out = t; return 0;
}
time_t agent_clock_floor(void)
{
#ifdef MK20_TIME_FLOOR
    return (time_t)MK20_TIME_FLOOR;
#else
    const char *s = __DATE__;
    int d = s[4] == ' ' ? s[5] - '0' : dec(s + 4, 2);
    return (time_t)(days(dec(s + 7, 4), (unsigned)month(s), (unsigned)d) * 86400);
#endif
}
static const char *marker(const agent_clock_options_t *o) { return o && o->marker_path ? o->marker_path : MK20_CLOCK_MARKER; }
int agent_clock_trusted_at(const agent_clock_options_t *o, time_t now, time_t boot)
{
    if (now < agent_clock_floor()) return 0;
    if (o && o->trust_clock) return 1;
    struct stat st;
    return !lstat(marker(o), &st) && S_ISREG(st.st_mode) && st.st_mtime > boot && st.st_mtime <= now;
}
int agent_clock_trusted(const agent_clock_options_t *o)
{
    time_t now = time(NULL), boot;
    if (o && o->trust_clock) return now >= agent_clock_floor();
#ifdef __APPLE__
    struct timeval b; size_t n = sizeof b; int mib[] = {CTL_KERN, KERN_BOOTTIME};
    if (sysctl(mib, 2, &b, &n, NULL, 0)) return 0;
    boot = b.tv_sec;
#else
    struct timespec up;
#ifdef CLOCK_BOOTTIME
    if (clock_gettime(CLOCK_BOOTTIME, &up)) return 0;
#else
    if (clock_gettime(CLOCK_MONOTONIC, &up)) return 0;
#endif
    boot = now - up.tv_sec;
#endif
    return agent_clock_trusted_at(o, now, boot);
}
int agent_clock_system_set(time_t epoch, void *ctx)
{
    (void)ctx; struct timeval tv = {epoch, 0}; return settimeofday(&tv, NULL);
}
int agent_clock_sync(const agent_clock_options_t *o, time_t source, time_t now)
{
    if (source < agent_clock_floor()) return -1;
    double skew = difftime(source, now);
    if (skew > 120 || skew < -120) {
        if (!o || !o->set_clock || !o->setter || o->setter(source, o->setter_ctx)) return -1;
    }
    const char *path = marker(o); size_t cap = strlen(path) + 16;
    char *temp = malloc(cap); if (!temp) return -1;
    snprintf(temp, cap, "%s.tmp.XXXXXX", path);
    int fd = mkstemp(temp);
    if (fd < 0) { free(temp); return -1; }
    int bad = write(fd, "synced\n", 7) != 7;
    if (!bad && fsync(fd)) bad = 1;
    if (close(fd)) bad = 1;
    if (!bad && rename(temp, path)) bad = 1;
    if (bad) unlink(temp);
    free(temp); return bad ? -1 : 0;
}
int agent_clock_sync_date(const agent_clock_options_t *o, const char *s, time_t now)
{
    time_t epoch;
    return agent_date_parse(s, &epoch) ? -1 : agent_clock_sync(o, epoch, now);
}
