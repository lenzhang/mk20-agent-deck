#define _POSIX_C_SOURCE 200809L
#define _DEFAULT_SOURCE
#ifdef __APPLE__
#define _DARWIN_C_SOURCE
#endif
#include "net.h"
#include "mem.h"
#include "json_util.h"
#include "mbedtls/ssl.h"
#include "mbedtls/entropy.h"
#include "mbedtls/ctr_drbg.h"
#include "mbedtls/x509_crt.h"
#include "psa/crypto.h"
#include "mbedtls/psa_util.h"
#include <arpa/inet.h>
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <netdb.h>
#include <net/if.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

#define HTTP_HEAD_MAX 16384u
#define CONNECT_MAX 8192u
static int fail(net_response_t *r, const char *cl, const char *detail)
{
    r->ok = 0; r->class_name = cl; snprintf(r->detail, sizeof r->detail, "%s", detail); return -1;
}
void net_response_free(net_response_t *r) { if (r) { free(r->body); r->body = NULL; r->bytes = 0; } }
static int digits(const char *s, size_t n, size_t *out, unsigned base)
{
    size_t v = 0; if (!n) return -1;
    for (size_t i = 0; i < n; i++) {
        unsigned c = (unsigned char)s[i], d;
        if (c >= '0' && c <= '9') d = c - '0';
        else if (base == 16 && c >= 'a' && c <= 'f') d = c - 'a' + 10;
        else if (base == 16 && c >= 'A' && c <= 'F') d = c - 'A' + 10;
        else return -1;
        if (v > (SIZE_MAX - d) / base) return -1;
        v = v * base + d;
    }
    *out = v; return 0;
}
int net_https_url(const char *s, net_url_t *out)
{
    if (!s || strncmp(s, "https://", 8)) return -1;
    net_url_t u = {{{0}, 443}, {0}};
    const char *p = s + 8, *end = p;
    while (*end && *end != '/' && *end != '?' && *end != '#') end++;
    const char *colon = memchr(p, ':', (size_t)(end - p));
    size_t hn = (size_t)((colon ? colon : end) - p);
    if (!hn || hn > 253) return -1;
    memcpy(u.endpoint.host, p, hn);
    if (!net_host_valid(u.endpoint.host)) return -1;
    if (colon) {
        size_t port;
        if (digits(colon + 1, (size_t)(end - colon - 1), &port, 10) || !port || port > 65535) return -1;
        u.endpoint.port = (unsigned)port;
    }
    /* No fragments, userinfo, control characters, spaces or non-ASCII request targets. */
    for (const char *q = end; *q; q++) if ((unsigned char)*q <= 32 || (unsigned char)*q >= 127 || *q == '#') return -1;
    size_t pn = strlen(end), prefix = *end != '/';
    if (pn + prefix >= sizeof u.path) return -1;
    if (prefix) u.path[0] = '/';
    memcpy(u.path + prefix, end, pn + 1); *out = u; return 0;
}
int net_read_key(const char *path, char key[4097])
{
    memset(key, 0, 4097);
    int fd = open(path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK); if (fd < 0) return -1;
    struct stat st; int bad = fstat(fd, &st) || !S_ISREG(st.st_mode) ||
        ((st.st_mode & 0777) != 0600 && (st.st_mode & 0777) != 0400);
    size_t n = 0;
    if (!bad && (st.st_size < 1 || st.st_size > 4096)) bad = 1;
    while (!bad && n < 4097) {
        ssize_t k = read(fd, key + n, 4097 - n);
        if (k < 0) { if (errno == EINTR) continue; bad = 1; break; }
        if (!k) break;
        n += (size_t)k;
    }
    if (close(fd)) bad = 1;
    if (n > 4096) bad = 1;
    while (n && isspace((unsigned char)key[n - 1])) key[--n] = 0;
    if (!n) bad = 1;
    for (size_t i = 0; i < n; i++) if ((unsigned char)key[i] <= 32 || (unsigned char)key[i] >= 127) bad = 1;
    if (bad) { mem_wipe(key, 4097); return -1; }
    key[n] = 0; return 0;
}
static int token(unsigned char c)
{
    return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') ||
        (c && strchr("!#$%&'*+-.^_`|~", c));
}
static int field(char *line, char **name, char **value)
{
    char *colon = strchr(line, ':'); if (!colon || colon == line) return -1;
    for (char *p = line; p < colon; p++) if (!token((unsigned char)*p)) return -1;
    *colon = 0; *name = line; char *v = colon + 1;
    while (*v == ' ' || *v == '\t') v++;
    for (char *p = v; *p; p++) if (((unsigned char)*p < 32 && *p != '\t') || (unsigned char)*p == 127) return -1;
    char *e = v + strlen(v); while (e > v && (e[-1] == ' ' || e[-1] == '\t')) *--e = 0;
    *value = v; return 0;
}
static char *line_end(char *s)
{
    char *e = strstr(s, "\r\n"); if (!e) return NULL;
    for (char *p = s; p < e; p++) if (*p == '\r' || *p == '\n') return NULL;
    *e = 0; return e + 2;
}
static int status_line(const char *s, int *status)
{
    size_t n = strlen(s), v;
    if (n < 12 || (strncmp(s, "HTTP/1.0 ", 9) && strncmp(s, "HTTP/1.1 ", 9)) ||
        digits(s + 9, 3, &v, 10) || v < 100 || v > 599 || (n > 12 && s[12] != ' ')) return -1;
    for (size_t i = 12; i < n; i++) if ((unsigned char)s[i] < 32 || (unsigned char)s[i] == 127) return -1;
    *status = (int)v; return 0;
}
int net_connect_reply(const unsigned char *b, size_t n, int *status, char date[128])
{
    *status = 0; date[0] = 0;
    if (n > CONNECT_MAX || memchr(b, 0, n)) return -1;
    size_t end = 0;
    for (size_t i = 3; i < n; i++) if (!memcmp(b + i - 3, "\r\n\r\n", 4)) { end = i + 1; break; }
    if (!end) return n == CONNECT_MAX ? -1 : 0;
    char buf[CONNECT_MAX + 1]; memcpy(buf, b, end); buf[end] = 0;
    char *p = line_end(buf); if (!p || status_line(buf, status)) return -1;
    while (*p) {
        char *next = line_end(p), *name, *value;
        if (!next) return -1;
        if (!*p) break;
        if (field(p, &name, &value)) return -1;
        if (!strcasecmp(name, "Date")) {
            if (date[0] || strlen(value) >= 128) return -1;
            strcpy(date, value);
        }
        p = next;
    }
    if (end != n) return -1;
    return *status == 200 ? 1 : -3;
}

typedef enum { H_HEADER, H_FIXED, H_CLOSE, H_CHUNK_SIZE, H_CHUNK_DATA, H_CR, H_LF, H_TRAILER, H_DONE } hstate_t;
typedef struct {
    hstate_t state; char line[HTTP_HEAD_MAX + 1]; size_t used, remaining, cap, alloc, trailer_bytes;
    net_response_t *r; int error;
} parser_t;
static int headers(parser_t *h)
{
    char *p = line_end(h->line); net_response_t *r = h->r;
    if (!p || status_line(h->line, &r->http_status) || r->http_status < 200) return -1;
    int cl = 0, te = 0; size_t length = 0;
    while (*p) {
        char *next = line_end(p), *name, *v;
        if (!next) return -1;
        if (!*p) break;
        if (field(p, &name, &v)) return -1;
        if (!strcasecmp(name, "Content-Length")) {
            if (cl++ || digits(v, strlen(v), &length, 10)) return -1;
        } else if (!strcasecmp(name, "Transfer-Encoding")) {
            if (te++ || strcasecmp(v, "chunked")) return -1;
        } else if (!strcasecmp(name, "Date")) {
            if (r->date[0] || strlen(v) >= sizeof r->date) return -1;
            strcpy(r->date, v);
        }
        p = next;
    }
    if (te && cl) return -1;
    if (r->http_status == 204 || r->http_status == 304) { h->state = H_DONE; return 0; }
    if (cl && length > h->cap) return -2;
    r->close_delimited = !te && !cl;
    h->remaining = length;
    h->state = te ? H_CHUNK_SIZE : cl ? (length ? H_FIXED : H_DONE) : H_CLOSE;
    return 0;
}
static int append(parser_t *h, const unsigned char *b, size_t n)
{
    net_response_t *r = h->r;
    if (n > h->cap - r->bytes) return -2;
    if (r->bytes + n > h->alloc) {
        size_t next = h->alloc ? h->alloc : 4096;
        while (next < r->bytes + n) { if (next > h->cap / 2) { next = h->cap; break; } next *= 2; }
        if (next > h->cap) next = h->cap;
        unsigned char *p = realloc(r->body, next + 1); if (!p) return -3;
        r->body = p; h->alloc = next;
    }
    if (n) { memcpy(r->body + r->bytes, b, n); r->bytes += n; r->body[r->bytes] = 0; }
    return 0;
}
/* RFC chunk extensions: token[=token|quoted-string], never bare garbage. */
static int extensions(const char *s)
{
    while (*s) {
        while (*s == ' ' || *s == '\t') s++;
        if (*s++ != ';') return -1;
        while (*s == ' ' || *s == '\t') s++;
        const char *start = s; while (token((unsigned char)*s)) s++;
        if (s == start) return -1;
        while (*s == ' ' || *s == '\t') s++;
        if (*s == '=') {
            s++; while (*s == ' ' || *s == '\t') s++;
            if (*s == '"') {
                s++;
                while (*s && *s != '"') {
                    if (*s == '\\') { s++; if (!*s) return -1; }
                    if ((unsigned char)*s < 32 || (unsigned char)*s == 127) return -1;
                    s++;
                }
                if (*s++ != '"') return -1;
            } else {
                start = s; while (token((unsigned char)*s)) s++;
                if (s == start) return -1;
            }
        }
        while (*s == ' ' || *s == '\t') s++;
    }
    return 0;
}
static int feed(parser_t *h, const unsigned char *b, size_t n)
{
    size_t i = 0;
    while (i < n) {
        if (h->state == H_DONE) return -1;
        if (h->state == H_FIXED || h->state == H_CLOSE || h->state == H_CHUNK_DATA) {
            size_t k = n - i;
            if (h->state != H_CLOSE && k > h->remaining) k = h->remaining;
            int rc = append(h, b + i, k); if (rc) return rc;
            i += k;
            if (h->state != H_CLOSE) {
                h->remaining -= k;
                if (!h->remaining) h->state = h->state == H_FIXED ? H_DONE : H_CR;
            }
            continue;
        }
        unsigned char c = b[i++];
        if (h->state == H_CR) { if (c != '\r') return -1; h->state = H_LF; continue; }
        if (h->state == H_LF) { if (c != '\n') return -1; h->state = H_CHUNK_SIZE; continue; }
        if (!c || h->used == HTTP_HEAD_MAX) return -1;
        h->line[h->used++] = (char)c; h->line[h->used] = 0;
        if (h->state == H_HEADER) {
            if (h->used >= 4 && !memcmp(h->line + h->used - 4, "\r\n\r\n", 4)) {
                int rc = headers(h); h->used = 0; if (rc) return rc;
            }
        } else if (c == '\n') {
            if (h->used < 2 || h->line[h->used - 2] != '\r') return -1;
            h->line[h->used - 2] = 0;
            if (strchr(h->line, '\r') || strchr(h->line, '\n')) return -1;
            if (h->state == H_CHUNK_SIZE) {
                char *s = h->line;
                while (isxdigit((unsigned char)*s)) s++;
                if (digits(h->line, (size_t)(s - h->line), &h->remaining, 16) || (*s && extensions(s))) return -1;
                if (h->remaining > h->cap - h->r->bytes) return -2;
                h->state = h->remaining ? H_CHUNK_DATA : H_TRAILER;
            } else {
                h->trailer_bytes += h->used;
                if (h->trailer_bytes > HTTP_HEAD_MAX) return -1;
                if (!h->line[0]) h->state = H_DONE;
                else {
                    char *name, *v;
                    if (field(h->line, &name, &v) || !strcasecmp(name, "Content-Length") ||
                        !strcasecmp(name, "Transfer-Encoding")) return -1;
                }
            }
            h->used = 0;
        }
    }
    return 0;
}
int net_http_parse(const unsigned char *b, size_t n, int eof, size_t cap, net_response_t *r)
{
    memset(r, 0, sizeof *r);
    if (!cap || cap > NET_BODY_MAX) cap = NET_BODY_MAX;
    parser_t p = {.cap = cap, .r = r}; int rc = feed(&p, b, n);
    if (rc) { net_response_free(r); return rc == -2 ? -2 : -1; }
    if (eof && p.state == H_CLOSE) p.state = H_DONE;
    if (p.state == H_DONE) return 1;
    net_response_free(r); return eof ? -1 : 0;
}

typedef struct { int fd; int64_t deadline; } transport_t;
static int64_t now_ms(void)
{
    struct timespec ts; if (clock_gettime(CLOCK_MONOTONIC, &ts)) return 0;
    return (int64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}
static int wait_fd(int fd, short events, int64_t deadline)
{
    for (;;) {
        int64_t left = deadline - now_ms(); if (left <= 0) { errno = ETIMEDOUT; return -1; }
        struct pollfd p = {fd, events, 0}; int rc = poll(&p, 1, left > INT_MAX ? INT_MAX : (int)left);
        if (rc > 0) return 0;
        if (!rc) { errno = ETIMEDOUT; return -1; }
        if (errno != EINTR) return -1;
    }
}
static int io_error(net_response_t *r, const char *cl)
{
    return fail(r, errno == ETIMEDOUT ? "timeout" : cl,
                errno == ETIMEDOUT ? "request deadline exceeded" : "transport failed");
}
static int nonblock(int fd)
{
    int f = fcntl(fd, F_GETFL, 0); if (f < 0 || fcntl(fd, F_SETFL, f | O_NONBLOCK)) return -1;
#ifdef SO_NOSIGPIPE
    int yes = 1; if (setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &yes, sizeof yes)) return -1;
#endif
    return 0;
}
static ssize_t socket_send(int fd, const void *b, size_t n)
{
#ifdef MSG_NOSIGNAL
    return send(fd, b, n, MSG_NOSIGNAL);
#else
    return send(fd, b, n, 0);
#endif
}
static int raw_send(transport_t *t, const unsigned char *b, size_t n)
{
    while (n) {
        if (now_ms() >= t->deadline) { errno = ETIMEDOUT; return -1; }
        ssize_t k = socket_send(t->fd, b, n);
        if (k > 0) { b += k; n -= (size_t)k; continue; }
        if (k < 0 && errno == EINTR) continue;
        if (k < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) { if (!wait_fd(t->fd, POLLOUT, t->deadline)) continue; }
        return -1;
    }
    return 0;
}
static ssize_t raw_recv(transport_t *t, unsigned char *b, size_t cap)
{
    for (;;) {
        if (now_ms() >= t->deadline) { errno = ETIMEDOUT; return -1; }
        ssize_t k = recv(t->fd, b, cap, 0);
        if (k >= 0) return k;
        if (errno == EINTR) continue;
        if (errno == EAGAIN || errno == EWOULDBLOCK) { if (!wait_fd(t->fd, POLLIN, t->deadline)) continue; }
        return -1;
    }
}
/* Resolve in a short-lived child: libc getaddrinfo has no deadline API.
 * Parent bounds DNS with the same monotonic deadline, then reaps on every path. */
typedef struct { int error; unsigned n; struct sockaddr_storage addr[8]; socklen_t len[8]; } addresses_t;
static int resolve(const net_endpoint_t *e, int64_t deadline, addresses_t *out)
{
    memset(out, 0, sizeof *out);
    struct sockaddr_in v4 = {.sin_family = AF_INET, .sin_port = htons((uint16_t)e->port)};
    if (inet_pton(AF_INET, e->host, &v4.sin_addr) == 1) {
        memcpy(&out->addr[0], &v4, sizeof v4); out->len[0] = sizeof v4; out->n = 1; return 0;
    }
    int fds[2]; if (pipe(fds)) return -1;
    pid_t pid = fork();
    if (pid < 0) { close(fds[0]); close(fds[1]); return -1; }
    if (!pid) {
        close(fds[0]); addresses_t result = {0}; char port[6]; snprintf(port, sizeof port, "%u", e->port);
        struct addrinfo hints = {0}, *list = NULL; hints.ai_socktype = SOCK_STREAM; hints.ai_family = AF_UNSPEC;
        result.error = getaddrinfo(e->host, port, &hints, &list);
        if (!result.error) for (struct addrinfo *p = list; p && result.n < 8; p = p->ai_next) {
            if ((p->ai_family != AF_INET && p->ai_family != AF_INET6) || p->ai_addrlen > sizeof result.addr[0]) continue;
            memcpy(&result.addr[result.n], p->ai_addr, p->ai_addrlen); result.len[result.n++] = (socklen_t)p->ai_addrlen;
        }
        if (list) freeaddrinfo(list);
        const unsigned char *b = (const unsigned char *)&result; size_t n = sizeof result;
        while (n) { ssize_t k = write(fds[1], b, n); if (k < 0 && errno == EINTR) continue; if (k <= 0) break; b += k; n -= (size_t)k; }
        close(fds[1]); _exit(0);
    }
    close(fds[1]); size_t n = 0; int bad = 0;
    while (n < sizeof *out) {
        if (wait_fd(fds[0], POLLIN, deadline)) { bad = 1; break; }
        ssize_t k = read(fds[0], (unsigned char *)out + n, sizeof *out - n);
        if (k < 0 && errno == EINTR) continue;
        if (k <= 0) { bad = 1; break; }
        n += (size_t)k;
    }
    int saved = errno; close(fds[0]); if (bad) kill(pid, SIGKILL);
    while (waitpid(pid, NULL, 0) < 0 && errno == EINTR) {}
    errno = saved;
    if (bad) return -1;
    if (out->error || !out->n) { errno = EHOSTUNREACH; return -1; }
    return 0;
}
static int tcp_connect(const net_endpoint_t *e, int64_t deadline, int usb,
                       int (*factory)(int, int, int),
                       int (*connector)(int, const struct sockaddr *, socklen_t), net_response_t *r)
{
    addresses_t a;
    if (resolve(e, deadline, &a)) { io_error(r, "dns"); return -1; }
    int64_t limit = now_ms() + 10000; if (limit > deadline) limit = deadline;
    for (unsigned i = 0; i < a.n; i++) {
        if (now_ms() >= limit) { errno = ETIMEDOUT; break; }
        int fd = factory ? factory(a.addr[i].ss_family, SOCK_STREAM, 0) : socket(a.addr[i].ss_family, SOCK_STREAM, 0);
        if (fd < 0) continue;
        if (nonblock(fd)) { close(fd); continue; }
        if (usb) {
#ifdef __linux__
            if (setsockopt(fd, SOL_SOCKET, SO_BINDTODEVICE, "usb0", 5)) { close(fd); fail(r, "config", "cannot bind time source to usb0"); return -1; }
#elif defined(IP_BOUND_IF)
            unsigned idx = if_nametoindex("usb0");
            if (!idx || setsockopt(fd, IPPROTO_IP, IP_BOUND_IF, &idx, sizeof idx)) { close(fd); fail(r, "config", "cannot bind time source to usb0"); return -1; }
#else
            close(fd); fail(r, "config", "usb0 binding unavailable"); return -1;
#endif
        }
        int rc = connector ? connector(fd, (struct sockaddr *)&a.addr[i], a.len[i]) :
                            connect(fd, (struct sockaddr *)&a.addr[i], a.len[i]);
        if (rc && errno == EINPROGRESS && !wait_fd(fd, POLLOUT, limit)) {
            int error = 0; socklen_t n = sizeof error;
            if (!getsockopt(fd, SOL_SOCKET, SO_ERROR, &error, &n) && !error) rc = 0;
            else if (error) errno = error;
        }
        if (!rc) return fd;
        int saved = errno; close(fd); errno = saved;
    }
    io_error(r, "connect"); return -1;
}
static int tls_send(void *ctx, const unsigned char *b, size_t n)
{
    transport_t *t = ctx;
    if (now_ms() >= t->deadline) return MBEDTLS_ERR_SSL_TIMEOUT;
    ssize_t k = socket_send(t->fd, b, n);
    if (k >= 0) return (int)k;
    if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR) return MBEDTLS_ERR_SSL_WANT_WRITE;
    return MBEDTLS_ERR_SSL_INTERNAL_ERROR;
}
static int tls_recv(void *ctx, unsigned char *b, size_t n)
{
    transport_t *t = ctx;
    if (now_ms() >= t->deadline) return MBEDTLS_ERR_SSL_TIMEOUT;
    ssize_t k = recv(t->fd, b, n, 0);
    if (k >= 0) return (int)k;
    if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR) return MBEDTLS_ERR_SSL_WANT_READ;
    return MBEDTLS_ERR_SSL_INTERNAL_ERROR;
}
static int tls_wait(transport_t *t, int rc)
{
    if (rc == MBEDTLS_ERR_SSL_WANT_READ || rc == MBEDTLS_ERR_SSL_WANT_WRITE)
        return wait_fd(t->fd, rc == MBEDTLS_ERR_SSL_WANT_READ ? POLLIN : POLLOUT, t->deadline);
    errno = rc == MBEDTLS_ERR_SSL_TIMEOUT || now_ms() >= t->deadline ? ETIMEDOUT : EIO;
    return -1;
}
static int tls_write_all(mbedtls_ssl_context *ssl, transport_t *t, const unsigned char *b, size_t n)
{
    while (n) {
        if (now_ms() >= t->deadline) { errno = ETIMEDOUT; return -1; }
        size_t part = n > 4096 ? 4096 : n;
        int rc = mbedtls_ssl_write(ssl, b, part);
        if (rc > 0) { b += rc; n -= (size_t)rc; }
        else if (tls_wait(t, rc)) return -1;
    }
    return 0;
}
static int read_ca(mbedtls_x509_crt *ca, const char *path)
{
    int fd = open(path, O_RDONLY | O_NONBLOCK); if (fd < 0) return -1;
    struct stat st;
    if (fstat(fd, &st) || !S_ISREG(st.st_mode) || st.st_size < 1 || st.st_size > 4 * 1024 * 1024) {
        close(fd); return -1;
    }
    FILE *f = fdopen(fd, "rb"); if (!f) { close(fd); return -1; }
    size_t cap = (size_t)st.st_size; unsigned char *b = malloc(cap + 1);
    if (!b) { fclose(f); return -1; }
    size_t n = fread(b, 1, cap, f); int bad = fgetc(f) != EOF || ferror(f);
    if (fclose(f)) bad = 1;
    b[n] = 0; int rc = bad ? -1 : mbedtls_x509_crt_parse(ca, b, n + 1); free(b);
    /* The Mozilla bundle also contains roots using algorithms/curves outside
     * this deliberately small build. mbedTLS reports these as partial success;
     * retain supported roots, and still fail closed if none could be parsed. */
    return rc >= 0 && ca->raw.p ? 0 : -1;
}
static int proxy_connect(transport_t *t, const net_endpoint_t *target, net_response_t *r)
{
    char request[600]; int n = snprintf(request, sizeof request, "CONNECT %s:%u HTTP/1.1\r\nHost: %s:%u\r\n\r\n",
        target->host, target->port, target->host, target->port);
    if (n < 0 || (size_t)n >= sizeof request || raw_send(t, (unsigned char *)request, (size_t)n)) return io_error(r, "proxy");
    unsigned char b[CONNECT_MAX + 1]; size_t used = 0; int status = 0, rc;
    do {
        ssize_t k = raw_recv(t, b + used, sizeof b - used);
        if (k < 0) return io_error(r, "proxy");
        if (!k) return fail(r, "proxy", "incomplete CONNECT reply");
        used += (size_t)k; rc = net_connect_reply(b, used, &status, r->proxy_date);
    } while (!rc);
    if (rc != 1) { r->http_status = status; return fail(r, "proxy", rc == -3 ? "CONNECT refused" : "invalid CONNECT reply"); }
    /* No TLS server bytes may precede ClientHello. Also reject queued surplus. */
    unsigned char extra; ssize_t k = recv(t->fd, &extra, 1, MSG_PEEK);
    if (k > 0) return fail(r, "proxy", "extra bytes after CONNECT headers");
    return 0;
}
static int header_valid(const net_header_t *h)
{
    if (!h->name || !*h->name || !h->value || strlen(h->name) > 128 || strlen(h->value) > 4096) return 0;
    for (const char *p = h->name; *p; p++) if (!token((unsigned char)*p)) return 0;
    for (const char *p = h->value; *p; p++) if ((unsigned char)*p < 32 || (unsigned char)*p >= 127) return 0;
    return strcasecmp(h->name, "Host") && strcasecmp(h->name, "Connection") && strcasecmp(h->name, "Content-Length") &&
           strcasecmp(h->name, "Transfer-Encoding") && strcasecmp(h->name, "Authorization") && strcasecmp(h->name, "Proxy-Authorization");
}
int net_request(const net_options_t *o, net_response_t *r)
{
    memset(r, 0, sizeof *r); r->class_name = "config";
    net_url_t u; net_route_t route; char key[4097] = {0}; char *request = NULL;
    int result = -1, tls_ready = 0;
    transport_t t = {-1, now_ms() + (o && o->timeout_ms > 0 ? o->timeout_ms : 60000)};
    mbedtls_ssl_context ssl; mbedtls_ssl_config conf; mbedtls_x509_crt ca;
    mbedtls_entropy_context entropy; mbedtls_ctr_drbg_context rng;
    mbedtls_ssl_init(&ssl); mbedtls_ssl_config_init(&conf); mbedtls_x509_crt_init(&ca);
    mbedtls_entropy_init(&entropy); mbedtls_ctr_drbg_init(&rng);
    if (!o || net_https_url(o->url, &u) || netcfg_route_for(o->config, o->provider_route ? o->provider_route : "foreign", &route) ||
        (o->method && strcmp(o->method, "GET") && strcmp(o->method, "POST")) || o->header_count > 64 ||
        (o->header_count && !o->headers) || (o->body_len && !o->body) || o->body_cap > NET_BODY_MAX || o->body_len > NET_BODY_MAX) {
        fail(r, "config", "invalid request or network configuration"); goto done;
    }
    for (size_t i = 0; i < o->header_count; i++) if (!header_valid(&o->headers[i])) { fail(r, "config", "invalid extra header"); goto done; }
    if (route == NET_BLOCKED) { fail(r, "blocked", "route is blocked by selected profile"); goto done; }
    if (o->key_file && net_read_key(o->key_file, key)) { fail(r, "config", "invalid key file (***)"); goto done; }
    if (!agent_clock_trusted(&o->clock) && !(route == NET_PROXY && o->clock.set_clock)) { fail(r, "clock", "clock is not trusted"); goto done; }
    if (read_ca(&ca, o->ca_file ? o->ca_file : NET_CA_DEFAULT)) { fail(r, "config", "cannot parse CA bundle"); goto done; }
    if (route == NET_PROXY) {
        for (size_t i = 0; i < o->config->proxy_count; i++) {
            const net_endpoint_t *p = &o->config->proxies[i];
            snprintf(r->via, sizeof r->via, "proxy %s:%u", p->host, p->port);
#ifdef MK20_NET_TEST
            if (o->connected_fd >= 0) { t.fd = dup(o->connected_fd); if (t.fd >= 0 && nonblock(t.fd)) { close(t.fd); t.fd = -1; } }
            else
#endif
            t.fd = tcp_connect(p, t.deadline, 0, o->socket_fn, o->connect_fn, r);
            if (t.fd >= 0) break;
            /* Only TCP/DNS establishment failure advances to another proxy. */
            if (now_ms() >= t.deadline) break;
        }
    } else {
        strcpy(r->via, "direct");
#ifdef MK20_NET_TEST
        if (o->connected_fd >= 0) { t.fd = dup(o->connected_fd); if (t.fd >= 0 && nonblock(t.fd)) { close(t.fd); t.fd = -1; } }
        else
#endif
        t.fd = tcp_connect(&u.endpoint, t.deadline, 0, o->socket_fn, o->connect_fn, r);
    }
    if (t.fd < 0) goto done;
    if (route == NET_PROXY && proxy_connect(&t, &u.endpoint, r)) goto done;
    if (o->clock.set_clock && r->proxy_date[0] && agent_clock_sync_date(&o->clock, r->proxy_date, time(NULL))) {
        fail(r, "clock", "proxy Date synchronization failed"); goto done;
    }
    if (!agent_clock_trusted(&o->clock)) { fail(r, "clock", "clock is not trusted"); goto done; }
    if (psa_crypto_init() != PSA_SUCCESS || mbedtls_ctr_drbg_seed(&rng, mbedtls_entropy_func, &entropy,
        (const unsigned char *)"mk20-agent", 10) ||
        mbedtls_ssl_config_defaults(&conf, MBEDTLS_SSL_IS_CLIENT, MBEDTLS_SSL_TRANSPORT_STREAM, MBEDTLS_SSL_PRESET_DEFAULT)) {
        fail(r, "tls", "TLS initialization failed"); goto done;
    }
    mbedtls_ssl_conf_authmode(&conf, MBEDTLS_SSL_VERIFY_REQUIRED);
    mbedtls_ssl_conf_ca_chain(&conf, &ca, NULL); mbedtls_ssl_conf_rng(&conf, mbedtls_ctr_drbg_random, &rng);
    if (mbedtls_ssl_setup(&ssl, &conf) || mbedtls_ssl_set_hostname(&ssl, u.endpoint.host)) { fail(r, "tls", "TLS setup failed"); goto done; }
    mbedtls_ssl_set_bio(&ssl, &t, tls_send, tls_recv, NULL);
    for (;;) {
        int rc = mbedtls_ssl_handshake(&ssl); if (!rc) break;
        if (!tls_wait(&t, rc)) continue;
        uint32_t flags = mbedtls_ssl_get_verify_result(&ssl);
        if (flags && flags != UINT32_MAX) {
            char detail[160] = "certificate verification failed:";
            if (flags & MBEDTLS_X509_BADCERT_EXPIRED) strcat(detail, " expired");
            if (flags & MBEDTLS_X509_BADCERT_FUTURE) strcat(detail, " not-yet-valid");
            if (flags & MBEDTLS_X509_BADCERT_CN_MISMATCH) strcat(detail, " name mismatch");
            if (flags & MBEDTLS_X509_BADCERT_NOT_TRUSTED) strcat(detail, " unknown CA");
            if (flags & MBEDTLS_X509_BADCERT_KEY_USAGE) strcat(detail, " key usage");
            if (flags & MBEDTLS_X509_BADCERT_EXT_KEY_USAGE) strcat(detail, " extended key usage");
            fail(r, "tls_verify", detail);
        } else {
            if (errno == ETIMEDOUT) io_error(r, "tls");
            else { char detail[80]; snprintf(detail, sizeof detail, "TLS handshake failed (-0x%04x)", (unsigned)-rc); fail(r, "tls", detail); }
        }
        goto done;
    }
    tls_ready = 1;
    request = malloc(HTTP_HEAD_MAX + 1);
    if (!request) { fail(r, "io", "allocation failed"); goto done; }
    int n = snprintf(request, HTTP_HEAD_MAX + 1, "%s %s HTTP/1.1\r\nHost: %s:%u\r\nConnection: close\r\nContent-Length: %zu\r\n",
        o->method ? o->method : "GET", u.path, u.endpoint.host, u.endpoint.port, o->body_len);
    if (n < 0 || (unsigned)n > HTTP_HEAD_MAX) { fail(r, "config", "request headers too large"); goto done; }
    size_t used = (size_t)n;
    for (size_t i = 0; i < o->header_count + (key[0] ? 1u : 0u); i++) {
        const char *name = i < o->header_count ? o->headers[i].name : "Authorization";
        const char *value = i < o->header_count ? o->headers[i].value : key;
        n = snprintf(request + used, HTTP_HEAD_MAX + 1 - used, "%s: %s%s\r\n", name, i < o->header_count ? "" : "Bearer ", value);
        if (n < 0 || (size_t)n > HTTP_HEAD_MAX - used) { fail(r, "config", "request headers too large (***)"); goto done; }
        used += (size_t)n;
    }
    if (used > HTTP_HEAD_MAX - 2) { fail(r, "config", "request headers too large"); goto done; }
    memcpy(request + used, "\r\n", 2); used += 2;
    mem_wipe(key, sizeof key);
    int sent = tls_write_all(&ssl, &t, (unsigned char *)request, used);
    mem_wipe(request, HTTP_HEAD_MAX + 1); free(request); request = NULL;
    if (sent || tls_write_all(&ssl, &t, o->body, o->body_len)) { io_error(r, "io"); goto done; }
    parser_t parser = {.cap = o->body_cap ? o->body_cap : NET_BODY_MAX, .r = r};
    while (parser.state != H_DONE) {
        unsigned char b[4096];
        if (now_ms() >= t.deadline) { errno = ETIMEDOUT; io_error(r, "io"); goto done; }
        int rc = mbedtls_ssl_read(&ssl, b, sizeof b);
        if (rc == MBEDTLS_ERR_SSL_PEER_CLOSE_NOTIFY) { r->tls_close_notify = 1; rc = 0; }
        if (rc < 0) { if (!tls_wait(&t, rc)) continue; io_error(r, "io"); goto done; }
        if (!rc) {
            if (parser.state == H_CLOSE) { parser.state = H_DONE; break; }
            fail(r, "http_parse", "truncated HTTP response"); goto done;
        }
        rc = feed(&parser, b, (size_t)rc);
        if (rc) { fail(r, rc == -2 ? "too_large" : rc == -3 ? "io" : "http_parse",
            rc == -2 ? "response body exceeds cap" : "invalid HTTP response"); goto done; }
    }
    if (o->clock.set_clock && r->date[0] && agent_clock_sync_date(&o->clock, r->date, time(NULL))) {
        fail(r, "clock", "HTTP Date synchronization failed"); goto done;
    }
    r->ok = 1; r->class_name = ""; r->detail[0] = 0; result = 0;
done:
    mem_wipe(key, sizeof key);
    if (request) { mem_wipe(request, HTTP_HEAD_MAX + 1); free(request); }
    if (tls_ready && now_ms() < t.deadline) (void)mbedtls_ssl_close_notify(&ssl);
    if (t.fd >= 0) close(t.fd);
    mbedtls_ssl_free(&ssl); mbedtls_ssl_config_free(&conf); mbedtls_x509_crt_free(&ca);
    mbedtls_ctr_drbg_free(&rng); mbedtls_entropy_free(&entropy); mbedtls_psa_crypto_free();
    if (result) net_response_free(r);
    return result;
}
int net_time_sync(const char *host, unsigned port, const agent_clock_options_t *clock, net_response_t *r)
{
    memset(r, 0, sizeof *r); r->class_name = "config";
    int usb = 1;
    if (!host || (strcmp(host, "172.30.250.1") && strcmp(host, "10.20.0.1") && strcmp(host, "169.254.7.1"))) {
#ifdef MK20_NET_TEST
        if (host && (!strcmp(host, "127.0.0.1") || !strcmp(host, "localhost"))) usb = 0;
        else
#endif
        return fail(r, "config", "time source must be the configured ECM Mac host");
    }
    if (!port || port > 65535) return fail(r, "config", "invalid time port");
    net_endpoint_t e = {{0}, port}; strcpy(e.host, host);
    transport_t t = {-1, now_ms() + 10000};
#ifdef MK20_NET_TEST
    const char *test_fd = getenv("MK20_TIME_TEST_FD");
    if (test_fd) { t.fd = dup(atoi(test_fd)); if (t.fd >= 0 && nonblock(t.fd)) { close(t.fd); t.fd = -1; } }
    else
#endif
    t.fd = tcp_connect(&e, t.deadline, usb, NULL, NULL, r);
    if (t.fd < 0) return -1;
    char request[600]; int n = snprintf(request, sizeof request, "GET /time HTTP/1.1\r\nHost: %s:%u\r\nConnection: close\r\n\r\n", host, port);
    int rc = -1;
    if (n < 0 || (size_t)n >= sizeof request || raw_send(&t, (unsigned char *)request, (size_t)n)) { io_error(r, "io"); goto done; }
    parser_t parser = {.cap = 4096, .r = r};
    while (parser.state != H_DONE) {
        unsigned char b[4096]; ssize_t k = raw_recv(&t, b, sizeof b);
        if (k < 0) { io_error(r, "io"); goto done; }
        if (!k) { if (parser.state == H_CLOSE) { parser.state = H_DONE; break; } fail(r, "http_parse", "truncated time response"); goto done; }
        int parsed = feed(&parser, b, (size_t)k);
        if (parsed) { fail(r, parsed == -2 ? "too_large" : "http_parse", "invalid time response"); goto done; }
    }
    if (r->http_status != 200) { fail(r, "clock", "time source refused request"); goto done; }
    cJSON *j = agent_json_parse((char *)r->body, r->bytes, 4096);
    const cJSON *epoch = cJSON_GetObjectItemCaseSensitive(j, "epoch");
    /* Require an integral JSON epoch, representable exactly and in time_t. */
    int valid = cJSON_IsObject(j) && cJSON_IsNumber(epoch) && epoch->valuedouble >= 0 && epoch->valuedouble <= 253402300799.0;
    if (valid) {
        const char *num = strchr((const char *)r->body, ':'); size_t integer = 0;
        if (!num || j->child->next) valid = 0;
        else {
            num++; while (*num && strchr(" \r\n\t", *num)) num++;
            const char *end = num; while (*end >= '0' && *end <= '9') end++;
            if (digits(num, (size_t)(end - num), &integer, 10)) valid = 0;
            while (*end && strchr(" \r\n\t", *end)) end++;
            if (*end != '}') valid = 0;
        }
    }
    time_t value = valid ? (time_t)(int64_t)epoch->valuedouble : 0;
    if (valid && ((double)value != epoch->valuedouble || agent_clock_sync(clock, value, time(NULL)))) valid = 0;
    cJSON_Delete(j);
    if (!valid) { fail(r, "clock", "invalid epoch or clock synchronization not authorized"); goto done; }
    r->ok = 1; r->class_name = ""; strcpy(r->via, "direct"); rc = 0;
done:
    close(t.fd); net_response_free(r); return rc;
}
