#ifdef __APPLE__
#define _DARWIN_C_SOURCE
#endif
#define _GNU_SOURCE
#define _POSIX_C_SOURCE 200809L
#include "httpd.h"
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#ifndef MSG_NOSIGNAL
#define MSG_NOSIGNAL 0
#endif
static int64_t now_ms(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t); return (int64_t)t.tv_sec*1000+t.tv_nsec/1000000; }
int httpd_expired(int64_t start, int64_t now) { return now < start || now - start >= HTTPD_DEADLINE; }
int httpd_random_hex(char *out, size_t bytes)
{
    unsigned char b[32]; if (bytes > sizeof b) return -1;
    int fd = open("/dev/urandom",O_RDONLY | O_CLOEXEC); if (fd < 0) return -1;
    size_t n = 0;
    while (n < bytes) { ssize_t k = read(fd,b+n,bytes-n); if (k < 0 && errno == EINTR) continue; if (k <= 0) { close(fd); return -1; } n += (size_t)k; }
    close(fd); const char *hex = "0123456789abcdef";
    for (size_t i = 0; i < bytes; i++) { out[2*i] = hex[b[i] >> 4]; out[2*i+1] = hex[b[i] & 15]; }
    out[2*bytes] = 0; memset(b,0,sizeof b); return 0;
}
static int load_token(int dir, const char *name, char token[65])
{
    int fd = openat(dir,name,O_RDONLY | O_NONBLOCK | O_NOFOLLOW | O_CLOEXEC); if (fd < 0) return -1;
    struct stat st; char b[65]; size_t n = 0; int bad = fstat(fd,&st) || !S_ISREG(st.st_mode) ||
        (st.st_mode & 07777) != 0600 || st.st_uid != geteuid() || st.st_size != 64;
    while (!bad && n < sizeof b) { ssize_t k = read(fd,b+n,sizeof b-n); if (k < 0 && errno == EINTR) continue; if (k < 0) { bad = 1; break; } if (!k) break; n += (size_t)k; }
    if (close(fd)) bad = 1;
    if (n != 64) bad = 1;
    for (size_t i = 0; !bad && i < n; i++) if (!((b[i] >= '0' && b[i] <= '9') || (b[i] >= 'a' && b[i] <= 'f'))) bad = 1;
    if (!bad) { memcpy(token,b,64); token[64] = 0; }
    memset(b,0,sizeof b); return bad ? -1 : 0;
}
int httpd_token(const char *path, char token[65])
{
    if (!path || strlen(path) >= 4096) return -1;
    char p[4096]; strcpy(p,path); char *slash = strrchr(p,'/'), *name = p; const char *parent = ".";
    if (slash) { *slash = 0; name = slash+1; parent = *p ? p : "/"; }
    if (!*name || !strcmp(name,".") || !strcmp(name,"..")) return -1;
    int dir = open(parent,O_RDONLY | O_DIRECTORY | O_CLOEXEC); if (dir < 0) return -1;
    struct stat st; int rc = -1;
    if (!fstatat(dir,name,&st,AT_SYMLINK_NOFOLLOW)) { rc = load_token(dir,name,token); goto end; }
    if (errno != ENOENT) goto end;
    char suffix[33], tmp[64], generated[65];
    if (httpd_random_hex(suffix,16) || httpd_random_hex(generated,32)) goto end;
    snprintf(tmp,sizeof tmp,".mcp-%s.tmp",suffix);
    int fd = openat(dir,tmp,O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC,0600);
    if (fd < 0) goto end;
    size_t n = 0; int bad = fchmod(fd,0600);
    while (!bad && n < 64) { ssize_t k = write(fd,generated+n,64-n); if (k < 0 && errno == EINTR) continue; if (k <= 0) { bad = 1; break; } n += (size_t)k; }
    if (!bad && fsync(fd)) bad = 1;
    if (close(fd)) bad = 1;
    /* Serialize first creation with a directory-local exclusive lock. Never
     * overwrite a token another instance has just installed. */
    int lock = -1;
    if (!bad) { lock = openat(dir,".mcp-token.lock",O_CREAT | O_EXCL | O_WRONLY | O_CLOEXEC,0600); if (lock < 0) bad = 1; }
    if (!bad && fstatat(dir,name,&st,AT_SYMLINK_NOFOLLOW) == 0) bad = 1;
    if (!bad && errno != ENOENT) bad = 1;
    if (!bad && renameat(dir,tmp,dir,name)) bad = 1;
    if (!bad && fsync(dir)) bad = 1;
    if (lock >= 0) { close(lock); unlinkat(dir,".mcp-token.lock",0); }
    unlinkat(dir,tmp,0); memset(generated,0,sizeof generated);
    if (!bad) rc = load_token(dir,name,token);
end:
    close(dir); return rc;
}
int httpd_authorized(const char *header, const char token[65])
{
    /* Always visit all 71 expected bytes, including absent/short credentials.
     * Length is public; secret comparison never exits on a differing byte. */
    size_t n = header ? strlen(header) : 0; volatile unsigned diff = (unsigned)(n != 71);
    for (size_t i = 0; i < 71; i++) {
        unsigned char expected = (unsigned char)(i < 7 ? "Bearer "[i] : token[i-7]);
        diff |= (unsigned)((i < n ? (unsigned char)header[i] : 0) ^ expected);
    }
    return diff == 0;
}
static int field(char *dst, size_t cap, const char *v) { size_t n = strlen(v); if (n >= cap) return -1; memcpy(dst,v,n+1); return 0; }
static int token_char(unsigned char c) { return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || strchr("!#$%&'*+-.^_`|~",c) != NULL; }
int httpd_parse(const char *in, size_t n, http_request_t *r)
{
    memset(r,0,sizeof *r); size_t head = 0;
    for (size_t i = 0; i < n; i++) {
        if (!in[i]) return 400;
        if (i+3 < n && !memcmp(in+i,"\r\n\r\n",4)) { head = i+4; break; }
        if (i >= HTTPD_HEADERS-1) return 431;
    }
    if (!head) return n >= HTTPD_HEADERS ? 431 : 0;
    if (head > HTTPD_HEADERS) return 431;
    char b[HTTPD_HEADERS+1]; memcpy(b,in,head); b[head] = 0;
    char *line = strstr(b,"\r\n"); if (!line) return 400; *line = 0;
    char *sp = strchr(b,' '); if (!sp) return 400; *sp++ = 0;
    char *sp2 = strchr(sp,' '); if (!sp2) return 400; *sp2++ = 0;
    if (strcmp(sp2,"HTTP/1.1") || !*b || field(r->method,sizeof r->method,b) || field(r->path,sizeof r->path,sp) || *sp != '/') return 400;
    for (const char *c = b; *c; c++) if (*c < 'A' || *c > 'Z') return 400;
    for (const unsigned char *c = (const unsigned char *)sp; *c; c++) if (*c < 33 || *c > 126) return 400;
    int length = 0, host = 0, te = 0, connection = 0, ct = 0;
    for (char *p = line+2; *p && strcmp(p,"\r\n"); ) {
        char *end = strstr(p,"\r\n"); if (!end) return 400; *end = 0;
        char *colon = strchr(p,':'); if (!colon || colon == p) return 400; *colon++ = 0;
        for (char *c = p; *c; c++) if (!token_char((unsigned char)*c)) return 400;
        while (*colon == ' ' || *colon == '\t') colon++;
        char *last = colon+strlen(colon); while (last > colon && (last[-1] == ' ' || last[-1] == '\t')) *--last = 0;
        for (char *c = colon; *c; c++) if ((unsigned char)*c < 32 || (unsigned char)*c == 127) return 400;
#define HDR(key, dest, count) if (!strcasecmp(p,key)) { if ((count)++ || field(dest,sizeof dest,colon)) return 400; }
        HDR("Host",r->host,host)
        else HDR("Origin",r->origin,r->has_origin)
        else HDR("Authorization",r->auth,r->has_auth)
        else HDR("Mcp-Session-Id",r->session,r->has_session)
        else HDR("MCP-Protocol-Version",r->version,r->has_version)
        else HDR("Content-Type",r->content_type,ct)
        else if (!strcasecmp(p,"Content-Length")) {
            if (length++ || !*colon) return 400;
            for (char *c = colon; *c; c++) { if (*c < '0' || *c > '9') return 400; if (r->body_len > (HTTPD_BODY-(unsigned)(*c-'0'))/10) return 413; r->body_len = r->body_len*10+(unsigned)(*c-'0'); }
        } else if (!strcasecmp(p,"Transfer-Encoding")) { if (te++) return 400; }
        else if (!strcasecmp(p,"Connection")) { if (connection++) return 400; if (!strcasecmp(colon,"close")) r->close = 1; else if (strcasecmp(colon,"keep-alive")) return 400; }
        else if (!strcasecmp(p,"Expect")) return 417;
#undef HDR
        p = end+2;
    }
    if (host != 1 || !*r->host) return 400;
    if (te) return length ? 400 : 411;
    if (!strcmp(r->method,"POST") && !length) return 411;
    if (r->body_len > HTTPD_BODY) return 413;
    if (n-head < r->body_len) return 0;
    r->body = in+head; r->consumed = head+r->body_len; return 1;
}
static int address(const char *s, int no_device, struct sockaddr_in *a, char host[128])
{
    if (!s || strlen(s) >= 128) return -1;
    char b[128]; strcpy(b,s); char *colon = strrchr(b,':'); if (!colon || colon == b) return -1; *colon++ = 0;
    if (!*colon) return -1;
    unsigned port = 0; for (const char *p = colon; *p; p++) { if (*p < '0' || *p > '9' || port > 6553) return -1; port = port*10+(unsigned)(*p-'0'); } if (!port || port > 65535) return -1;
    memset(a,0,sizeof *a); a->sin_family = AF_INET; a->sin_port = htons((uint16_t)port);
    if (inet_pton(AF_INET,b,&a->sin_addr) != 1 || !a->sin_addr.s_addr || (ntohl(a->sin_addr.s_addr) >> 24) >= 224) return -1;
    int loopback = (ntohl(a->sin_addr.s_addr) >> 24) == 127;
    if ((no_device && !loopback) || (!no_device && strcmp(b,"172.30.250.2"))) return -1;
    snprintf(host,128,"%.15s:%u",b,port); return 0; /* inet_pton-validated IPv4: <=15 chars */
}
int httpd_address(const char *s, int no_device, char host[128]) { struct sockaddr_in a; return address(s,no_device,&a,host); }
static int nonblock(int fd)
{
    int flags = fcntl(fd,F_GETFL); if (flags < 0 || fcntl(fd,F_SETFL,flags | O_NONBLOCK) || fcntl(fd,F_SETFD,FD_CLOEXEC)) return -1;
#ifdef SO_NOSIGPIPE
    int one = 1; if (setsockopt(fd,SOL_SOCKET,SO_NOSIGPIPE,&one,sizeof one)) return -1;
#endif
    return 0;
}
int httpd_listen(const char *s, int no_device)
{
    struct sockaddr_in a; char host[128]; if (address(s,no_device,&a,host)) { errno = EINVAL; return -1; }
    int fd = socket(AF_INET,SOCK_STREAM,0); if (fd < 0) return -1;
    if (!no_device) {
#ifdef __linux__
        if (setsockopt(fd,SOL_SOCKET,SO_BINDTODEVICE,"usb0",5)) goto bad;
#else
        errno = ENOTSUP; goto bad;
#endif
    }
    if (nonblock(fd) || bind(fd,(struct sockaddr *)&a,sizeof a) || listen(fd,4)) goto bad;
    return fd;
bad:;
    int saved = errno; close(fd); errno = saved; return -1;
}
typedef struct {
    int fd, pending, closing; unsigned requests;
    int64_t start; uint64_t tag;
    char *in, *out; size_t used, out_len, sent;
    http_request_t req;
} connection_t;
static void drop(connection_t *c, const http_handler_t *h)
{
    if (c->fd >= 0) { if (c->pending && h->disconnect) h->disconnect(h->ctx,c->tag); close(c->fd); }
    free(c->in); free(c->out); memset(c,0,sizeof *c); c->fd = -1;
}
static int respond(connection_t *c, http_response_t *r)
{
    const char *reason = r->status == 200 ? "OK" : r->status == 202 ? "Accepted" : r->status == 401 ? "Unauthorized" : r->status == 404 ? "Not Found" : r->status == 405 ? "Method Not Allowed" : "Error";
    char head[512]; int n = snprintf(head,sizeof head,"HTTP/1.1 %d %s\r\nContent-Length: %zu\r\nContent-Type: application/json\r\nConnection: %s\r\n%s%s%s%s\r\n",r->status,reason,r->len,c->closing ? "close" : "keep-alive",
        r->status == 401 ? "WWW-Authenticate: Bearer\r\n" : "",r->status == 405 ? "Allow: POST\r\n" : "",*r->session ? "Mcp-Session-Id: " : "",*r->session ? r->session : "");
    /* Session header needs its own CRLF before the final empty line. */
    if (*r->session && n > 2 && (size_t)n+2 < sizeof head) { head[n++] = '\r'; head[n++] = '\n'; head[n] = 0; }
    if (n < 0 || (size_t)n >= sizeof head || r->len > 4*1024*1024) return -1;
    c->out = malloc((size_t)n+r->len); if (!c->out) return -1;
    memcpy(c->out,head,(size_t)n); if (r->len) memcpy(c->out+n,r->body,r->len);
    c->out_len = (size_t)n+r->len; c->sent = 0; c->pending = 0; c->start = now_ms(); return 0;
}
typedef struct {
    int listener, failed, pumping;
    uint64_t tag;
    connection_t c[HTTPD_CONNECTIONS];
    const http_handler_t *handler;
} server_t;
static void pump(void *ctx)
{
    server_t *server = ctx; if (server->pumping) return; server->pumping = 1;
    connection_t *c = server->c; int listener = server->listener; const http_handler_t *h = server->handler;
        struct pollfd p[HTTPD_CONNECTIONS+1]; p[0] = (struct pollfd){listener,POLLIN,0};
        int64_t now = now_ms();
        for (int i = 0; i < HTTPD_CONNECTIONS; i++) {
            if (c[i].fd >= 0 && !c[i].pending && httpd_expired(c[i].start,now)) drop(&c[i],h);
            p[i+1] = (struct pollfd){c[i].fd,c[i].out ? POLLOUT : POLLIN,0};
        }
        int rc = poll(p,HTTPD_CONNECTIONS+1,0); if (rc < 0 && errno != EINTR) { server->failed = 1; server->pumping = 0; return; }
        if (listener >= 0 && (p[0].revents & POLLIN)) {
            int fd = accept(listener,NULL,NULL); if (fd >= 0) {
                int slot = -1; for (int i = 0; i < HTTPD_CONNECTIONS; i++) if (c[i].fd < 0) { slot = i; break; }
                if (slot < 0 || nonblock(fd)) close(fd);
                else { c[slot].fd = fd; c[slot].start = now_ms(); }
            }
        }
        for (int i = 0; i < HTTPD_CONNECTIONS; i++) {
            connection_t *v = &c[i]; if (v->fd < 0) continue;
            if (p[i+1].revents & (POLLERR | POLLNVAL)) { drop(v,h); continue; }
            if (v->out && (p[i+1].revents & POLLOUT)) {
                ssize_t k = send(v->fd,v->out+v->sent,v->out_len-v->sent,MSG_NOSIGNAL);
                if (k > 0) v->sent += (size_t)k;
                else if (!k || (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR)) { drop(v,h); continue; }
                if (v->sent == v->out_len) {
                    if (v->closing) { drop(v,h); continue; }
                    free(v->out); v->out = NULL; v->out_len = v->sent = 0;
                    memmove(v->in,v->in+v->req.consumed,v->used-v->req.consumed); v->used -= v->req.consumed; v->start = now_ms();
                }
            }
            if ((p[i+1].revents & POLLIN) && !v->out) {
                if (v->pending) { char ch; ssize_t k = recv(v->fd,&ch,1,MSG_PEEK); if (!k) { drop(v,h); continue; } }
                else {
                    if (!v->in) v->in = malloc(HTTPD_HEADERS+HTTPD_BODY+1);
                    if (!v->in) { drop(v,h); continue; }
                    ssize_t k = recv(v->fd,v->in+v->used,HTTPD_HEADERS+HTTPD_BODY-v->used,0);
                    if (k > 0) v->used += (size_t)k;
                    else if (!k || (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR)) { drop(v,h); continue; }
                }
            }
            if (!v->out && (v->used || v->pending)) {
                int parsed = v->pending ? 1 : httpd_parse(v->in,v->used,&v->req); if (!parsed) continue;
                http_response_t r = {0}; int pending = 0;
                if (parsed != 1) { r.status = parsed; v->closing = 1; }
                else {
                    if (!v->pending) { v->tag = ++server->tag; v->req.tag = v->tag; v->closing = v->req.close || ++v->requests >= 32; }
                    pending = h->handle(h->ctx,&v->req,&r);
                }
                if (pending) { v->pending = 1; continue; }
                if (!r.status) r.status = 500;
                if (respond(v,&r)) drop(v,h);
                free(r.body);
            }
        }
    server->pumping = 0;
}
int httpd_run_fds(int listener, const int *fds, size_t count, const http_handler_t *h, volatile sig_atomic_t *stop)
{
    server_t server; memset(&server,0,sizeof server); server.listener = listener; server.handler = h;
    connection_t *c = server.c; for (int i = 0; i < HTTPD_CONNECTIONS; i++) c[i].fd = -1;
    if (listener >= 0 && nonblock(listener)) { close(listener); return -1; }
    if (count > HTTPD_CONNECTIONS) return -1;
    for (size_t i = 0; i < count; i++) { if (nonblock(fds[i])) { for (size_t k = 0; k < i; k++) drop(&c[k],h); close(fds[i]); return -1; } c[i].fd = fds[i]; c[i].start = now_ms(); }
    if (h->attach_pump) h->attach_pump(h->ctx,pump,&server);
    while (!*stop && !server.failed) {
        if (h->tick) h->tick(h->ctx);
        pump(&server);
        if (listener < 0) { int open = 0; for (int i = 0; i < HTTPD_CONNECTIONS; i++) if (c[i].fd >= 0) open = 1; if (!open) break; }
        /* Short bounded idle sleep; cooperative action guards pump without it. */
        (void)poll(NULL,0,5);
    }
    if (h->attach_pump) h->attach_pump(h->ctx,NULL,NULL);
    for (int i = 0; i < HTTPD_CONNECTIONS; i++) drop(&c[i],h);
    if (listener >= 0) close(listener);
    return server.failed ? -1 : 0;
}

int httpd_run(int listener, int connected, const http_handler_t *h, volatile sig_atomic_t *stop)
{
    return httpd_run_fds(listener,connected >= 0 ? &connected : NULL,connected >= 0 ? 1 : 0,h,stop);
}
