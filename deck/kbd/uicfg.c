#define _DARWIN_C_SOURCE
#ifndef _DEFAULT_SOURCE
#define _DEFAULT_SOURCE
#endif
#define _POSIX_C_SOURCE 200809L
#include "uicfg.h"
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include "providers.inc"

void uicfg_defaults(uicfg_t *c)
{
    memset(c, 0, sizeof *c);
    c->proxy_port = 7890; c->brightness = 80; c->volume = 50;
    strcpy(c->proxy_host, "172.30.250.1");
}
static int host_ok(const char *s)
{
    size_t n = strlen(s), label = 0; int numeric = 1;
    if (!n || n > 253) return 0;
    for (size_t i = 0; i < n; i++) {
        char c = s[i];
        if (!(c >= '0' && c <= '9') && c != '.') numeric = 0;
        if (c == '.') {
            if (!label || s[i-1] == '-') return 0;
            label = 0;
        } else {
            if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
                (c >= '0' && c <= '9') || c == '-')) return 0;
            if ((!label && c == '-') || ++label > 63) return 0;
        }
    }
    if (!label || s[n-1] == '-') return 0;
    if (numeric) {
        unsigned parts = 0;
        const char *p = s;
        while (*p) {
            unsigned v = 0, digits = 0;
            if (p[0] == '0' && p[1] && p[1] != '.') return 0;
            while (*p >= '0' && *p <= '9') { v = v*10+(unsigned)(*p++-'0'); if (++digits > 3 || v > 255) return 0; }
            if (!digits || ++parts > 4) return 0;
            if (*p == '.') p++; else if (*p) return 0;
        }
        return parts == 4;
    }
    return 1;
}
int uicfg_valid(const uicfg_t *c)
{
    if (!c || !memchr(c->proxy_host, 0, sizeof c->proxy_host) ||
        !memchr(c->wifi_proxy, 0, sizeof c->wifi_proxy) ||
        c->provider < 0 || (size_t)c->provider >= UI_PROVIDER_COUNT || c->model < 0 ||
        (size_t)c->model >= UI_PROVIDERS[c->provider].model_count ||
        (c->wifi != 0 && c->wifi != 1) || c->proxy_port < 1 || c->proxy_port > 65535 ||
        c->brightness < 10 || c->brightness > 100 || c->volume < 0 || c->volume > 100 ||
        !host_ok(c->proxy_host)) return 0;
    if (*c->wifi_proxy) {
        char host[300], *colon, *end; long port;
        if (strncmp(c->wifi_proxy, "http://", 7) || strlen(c->wifi_proxy + 7) >= sizeof host) return 0;
        strcpy(host, c->wifi_proxy + 7); colon = strrchr(host, ':');
        if (!colon) return 0;
        *colon++ = 0;
        for (const char *p = colon; *p; p++) if (*p < '0' || *p > '9') return 0;
        port = strtol(colon, &end, 10);
        if (!*colon || *end || port < 1 || port > 65535 || !host_ok(host)) return 0;
    }
    return 1;
}
int uicfg_quote(const char *src, char *dst, size_t cap)
{
    size_t n = 0;
#define PUT(ch) do { if (n + 1 >= cap) return -1; dst[n++] = (ch); } while (0)
    PUT('"');
    for (; *src; src++) {
        unsigned char c = (unsigned char)*src;
        if (c == '"' || c == '\\') { PUT('\\'); PUT((char)c); }
        else if (c == '\b' || c == '\f' || c == '\n' || c == '\r' || c == '\t') {
            PUT('\\'); PUT(c == '\b' ? 'b' : c == '\f' ? 'f' : c == '\n' ? 'n' : c == '\r' ? 'r' : 't');
        } else if (c < 32) {
            const char *hex = "0123456789abcdef";
            PUT('\\'); PUT('u'); PUT('0'); PUT('0'); PUT(hex[c >> 4]); PUT(hex[c & 15]);
        } else PUT((char)c);
    }
    PUT('"'); dst[n] = 0;
#undef PUT
    return 0;
}
int uicfg_serialize(const uicfg_t *c, char *net, size_t nn, char *prov, size_t pn)
{
    const ui_provider_t *p; char proxy[700], url[320]; int n, m;
    if (!uicfg_valid(c)) { errno = EINVAL; return -1; }
    p = &UI_PROVIDERS[c->provider];
    m = snprintf(prov, pn, "{\n \"version\": 1,\n \"provider\": \"%s\",\n \"model\": \"%s\",\n \"base_url\": \"%s\",\n \"domestic\": %s,\n \"route\": \"%s\",\n \"api_key_file\": \"/data/mk20/secrets/%s.key\"\n}",
        p->id, p->models[c->model].id, p->base_url, p->domestic ? "true" : "false", p->domestic ? "domestic" : "foreign", p->id);
    if (c->wifi) {
        if (*c->wifi_proxy) { if (uicfg_quote(c->wifi_proxy, proxy, sizeof proxy)) return -1; }
        else strcpy(proxy, "null");
        n = snprintf(net, nn, "{\n \"version\": 1,\n \"profile\": \"board_wifi\",\n \"proxy\": {\n  \"url\": %s,\n  \"fallbacks\": []\n },\n \"routes\": {\n  \"domestic\": \"direct\",\n  \"foreign\": \"%s\"\n }\n}", proxy, *c->wifi_proxy ? "proxy" : "blocked");
    } else {
        snprintf(url, sizeof url, "http://%s:%d", c->proxy_host, c->proxy_port);
        if (uicfg_quote(url, proxy, sizeof proxy)) return -1;
        char fallbacks[200] = "[]";
        if (!strcmp(c->proxy_host, "172.30.250.1"))
            snprintf(fallbacks, sizeof fallbacks, "[\n   \"http://10.20.0.1:%d\",\n   \"http://169.254.7.1:%d\"\n  ]", c->proxy_port, c->proxy_port);
        n = snprintf(net, nn, "{\n \"version\": 1,\n \"profile\": \"mac_proxy\",\n \"proxy\": {\n  \"url\": %s,\n  \"fallbacks\": %s\n },\n \"routes\": {\n  \"domestic\": \"proxy\",\n  \"foreign\": \"proxy\"\n },\n \"note\": \"Mac mihomo rules split domestic/foreign; never fall back to direct for foreign\"\n}", proxy, fallbacks);
    }
    if (n < 0 || (size_t)n >= nn || m < 0 || (size_t)m >= pn) { errno = EOVERFLOW; return -1; }
    return 0;
}
static ssize_t hook(const uicfg_hooks_t *h, enum uicfg_op op, size_t n)
{ return h && h->fault ? h->fault(h->ctx, op, n) : (ssize_t)n; }
static int syncfd(int fd, int dir, const uicfg_hooks_t *h)
{ return hook(h, dir ? UICFG_DIR_FSYNC : UICFG_FSYNC, 0) < 0 ? -1 : fsync(fd); }
static int sync_parent(int fd, int created, const uicfg_hooks_t *h)
{
    /* Fault injection must fail even on an existing read-only ancestor. */
    if (hook(h, UICFG_DIR_FSYNC, 0) < 0) return -1;
    if (hook(h, UICFG_PARENT_FSYNC_SYSCALL, 0) >= 0 && !fsync(fd)) return 0;
    /* Unsupported/read-only filesystems cannot have a pending mkdir here. */
    return !created && (errno == EINVAL || errno == EROFS) ? 0 : -1;
}
static int write_all(int fd, const char *s, const uicfg_hooks_t *h)
{
    size_t n = strlen(s);
    while (n) {
        ssize_t k = hook(h, UICFG_WRITE, n);
        if (k >= 0) k = write(fd, s, (size_t)k > n ? n : (size_t)k);
        if (k < 0 && errno == EINTR) continue;
        if (k <= 0) { if (!k) errno = EIO; return -1; }
        s += k; n -= (size_t)k;
    }
    return 0;
}
/* Walk every component using directory fds: no symlinked ancestors, '.' or '..'. */
static int open_root(const char *path, const uicfg_hooks_t *h)
{
    char copy[1024], *part, *save; int fd, next, components = 0;
    if (!path || !*path || strlen(path) >= sizeof copy) { errno = EINVAL; return -1; }
    strcpy(copy, path); fd = open(path[0] == '/' ? "/" : ".", O_RDONLY | O_DIRECTORY | O_NOFOLLOW);
    if (fd < 0) return -1;
    for (part = strtok_r(copy, "/", &save); part; part = strtok_r(NULL, "/", &save)) {
        int created = 0;
        components++;
        if (!strcmp(part, ".") || !strcmp(part, "..")) { errno = EINVAL; goto bad; }
        next = openat(fd, part, O_RDONLY | O_DIRECTORY | O_NOFOLLOW);
        if (next < 0 && errno == ENOENT) {
            if (mkdirat(fd, part, 0700)) goto bad;
            created = 1;
            next = openat(fd, part, O_RDONLY | O_DIRECTORY | O_NOFOLLOW);
        }
        if (next < 0) goto bad;
        /* Retry parent durability even if an earlier Apply left this component. */
        if (sync_parent(fd, created, h)) { int e = errno; close(next); errno = e; goto bad; }
        close(fd); fd = next;
    }
    if (!components) { errno = EINVAL; goto bad; }
    struct stat st;
    if (fstat(fd, &st) || st.st_uid != geteuid() || fchmod(fd, 0700)) goto bad;
    return fd;
bad: { int e = errno; close(fd); errno = e; return -1; }
}
static int regular_or_absent(int fd, const char *name)
{
    struct stat st;
    if (fstatat(fd, name, &st, AT_SYMLINK_NOFOLLOW)) return errno == ENOENT ? 0 : -1;
    if (!S_ISREG(st.st_mode) || st.st_uid != geteuid() || st.st_nlink != 1) { errno = EINVAL; return -1; }
    return 0;
}
static int replace(int dir, const char *name, const char *data, const uicfg_hooks_t *h)
{
    char tmp[80]; int fd = -1, rc = -1;
    for (unsigned i = 0; i < 100; i++) {
        snprintf(tmp, sizeof tmp, ".%s.tmp.%ld.%u", name, (long)getpid(), i);
        fd = openat(dir, tmp, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0600);
        if (fd >= 0 || errno != EEXIST) break;
    }
    if (fd < 0) return -1;
    if (fchmod(fd, 0600) || write_all(fd, data, h) || syncfd(fd, 0, h)) goto done;
    if (close(fd)) { fd = -1; goto done; } fd = -1;
    if (regular_or_absent(dir, name) || hook(h, UICFG_RENAME, 0) < 0 || renameat(dir, tmp, dir, name) || syncfd(dir, 1, h)) goto done;
    rc = 0;
done: { int e = errno; if (fd >= 0) close(fd); unlinkat(dir, tmp, 0); errno = e; return rc; }
}
int uicfg_apply(const char *root, const uicfg_t *c, const uicfg_hooks_t *h)
{
    char net[4096], prov[4096]; int dir, lock = -1, marker = -1, rc = -1;
    struct flock fl;
    if (uicfg_serialize(c, net, sizeof net, prov, sizeof prov)) return -1;
    dir = open_root(root, h); if (dir < 0) return -1;
    if (regular_or_absent(dir, ".apply-lock")) goto done;
    lock = openat(dir, ".apply-lock", O_WRONLY | O_CREAT | O_NOFOLLOW, 0600);
    memset(&fl, 0, sizeof fl); fl.l_type = F_WRLCK; fl.l_whence = SEEK_SET;
    if (lock < 0 || fchmod(lock, 0600) || fcntl(lock, F_SETLK, &fl) ||
        regular_or_absent(dir, "net.json") || regular_or_absent(dir, "provider.json") ||
        regular_or_absent(dir, ".apply-in-progress")) goto done;
    marker = openat(dir, ".apply-in-progress", O_WRONLY | O_CREAT | O_NOFOLLOW, 0600);
    if (marker < 0 || fchmod(marker, 0600) || write_all(marker, "1", h) || syncfd(marker, 0, h) || syncfd(dir, 1, h)) goto done;
    if (replace(dir, "net.json", net, h) || replace(dir, "provider.json", prov, h)) goto done;
    if (unlinkat(dir, ".apply-in-progress", 0)) goto done;
    if (syncfd(dir, 1, h)) {
        /* Re-create a visible gate if final durability cannot be confirmed. */
        int gate = openat(dir, ".apply-in-progress", O_WRONLY | O_CREAT | O_NOFOLLOW, 0600);
        if (gate >= 0) { (void)write(gate, "1", 1); (void)fsync(gate); close(gate); (void)fsync(dir); }
        goto done;
    }
    rc = 0;
done: { int e = errno; if (marker >= 0) close(marker); if (lock >= 0) close(lock); close(dir); errno = e; return rc; }
}
