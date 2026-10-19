#define _POSIX_C_SOURCE 200809L
#include "dns.h"
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <time.h>
#include <sys/stat.h>
#include <unistd.h>
static unsigned u16(const unsigned char *b) { return (unsigned)b[0]*256+b[1]; }
static void p16(unsigned char *b, unsigned v) { b[0] = (unsigned char)(v>>8); b[1] = (unsigned char)v; }
int64_t dns_now_ms(void) { struct timespec t; if (clock_gettime(CLOCK_MONOTONIC,&t)) return 0; return (int64_t)t.tv_sec*1000+t.tv_nsec/1000000; }
static int ldh(unsigned char c) { return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '-'; }
int dns_host_valid(const char *s)
{
    size_t n = s ? strlen(s) : 0, label = 0;
    if (!n || n > 253) return 0;
    for (size_t i = 0; i <= n; i++) {
        if (!s[i] || s[i] == '.') {
            if (!label || label > 63 || s[i-label] == '-' || s[i-1] == '-') return 0;
            label = 0;
        } else { if (!ldh((unsigned char)s[i])) return 0; label++; }
    }
    return 1;
}
/* consumed is the encoded length, independently of compression traversal.
 * A visited bitmap catches forward/backward/self pointer cycles. */
static int name(const unsigned char *b, size_t n, size_t *off, char out[254])
{
    unsigned char seen[DNS_PACKET_MAX] = {0}; size_t p = *off, used = 0; int jumped = 0;
    for (;;) {
        if (p >= n || seen[p]) return -1;
        seen[p] = 1; unsigned k = b[p++];
        if ((k & 192) == 192) {
            if (p >= n) return -1;
            if (!jumped) *off = p+1;
            p = ((k & 63)<<8) | b[p]; jumped = 1; continue;
        }
        if (k & 192) return -1;
        if (!k) { if (!jumped) *off = p; out[used] = 0; return 0; }
        if (k > n-p || used+k+(used != 0) > 253) return -1;
        if (used) out[used++] = '.';
        if (b[p] == '-' || b[p+k-1] == '-') return -1;
        for (unsigned i = 0; i < k; i++) { if (!ldh(b[p+i])) return -1; out[used++] = (char)b[p+i]; }
        p += k;
    }
}
static void address(dns_addresses_t *out, unsigned type, unsigned port, const void *data)
{
    if (out->n == DNS_ADDR_MAX) return;
    unsigned i = out->n++; memset(&out->addr[i],0,sizeof out->addr[i]);
    if (type == 1) {
        struct sockaddr_in a = {.sin_family=AF_INET,.sin_port=htons((uint16_t)port)};
        memcpy(&a.sin_addr,data,4); memcpy(&out->addr[i],&a,sizeof a); out->len[i] = sizeof a;
    } else {
        struct sockaddr_in6 a = {.sin6_family=AF_INET6,.sin6_port=htons((uint16_t)port)};
        memcpy(&a.sin6_addr,data,16); memcpy(&out->addr[i],&a,sizeof a); out->len[i] = sizeof a;
    }
}
int dns_parse(const unsigned char *b, size_t n, uint16_t id, const char *host, unsigned type, unsigned port, dns_addresses_t *out)
{
    memset(out,0,sizeof *out);
    if (n < 12 || n > DNS_PACKET_MAX) return -1;
    unsigned flags = u16(b+2);
    if (u16(b) != id || !(flags & 0x8000) || (flags & 0x7800) || u16(b+4) != 1) return -2;
    size_t off = 12; char decoded[254];
    if (name(b,n,&off,decoded) || n-off < 4) return -1;
    if (strcasecmp(host,decoded) || u16(b+off) != type || u16(b+off+2) != 1) return -2;
    off += 4;
    if (flags & 0x0200) return -1; /* TC: deliberately no TCP fallback. */
    unsigned answers = u16(b+6), total = answers+u16(b+8)+u16(b+10);
    size_t rr[128];
    if (total > 128) return -1;
    for (unsigned i = 0; i < total; i++) {
        rr[i] = off;
        if (name(b,n,&off,decoded) || n-off < 10) return -1;
        unsigned t = u16(b+off), len = u16(b+off+8); off += 10;
        if (len > n-off || (t == 1 && len != 4) || (t == 28 && len != 16)) return -1;
        if (t == 5) { size_t end = off; if (name(b,n,&end,decoded) || end != off+len || !*decoded) return -1; }
        off += len;
    }
    if (off != n) return -1;
    if ((flags & 15) == 3) return -3;
    if (flags & 15) return -1;
    char current[254]; snprintf(current,sizeof current,"%s",host);
    for (unsigned depth = 0; depth <= 8; depth++) {
        char alias[254] = {0};
        for (unsigned i = 0; i < answers; i++) {
            size_t p = rr[i]; if (name(b,n,&p,decoded)) return -1;
            if (strcasecmp(current,decoded) || u16(b+p+2) != 1) continue;
            unsigned t = u16(b+p); p += 10;
            if (t == type) address(out,type,port,b+p);
            else if (t == 5) {
                if (name(b,n,&p,decoded)) return -1;
                if (*alias && strcasecmp(alias,decoded)) return -1;
                strcpy(alias,decoded);
            }
        }
        if (out->n) { if (*alias) { out->n = 0; return -1; } return 0; }
        if (!*alias) return 0;
        if (depth == 8 || !strcasecmp(current,alias)) return -1;
        strcpy(current,alias);
    }
    return -1;
}
static int check(const dns_options_t *o, int64_t deadline)
{
    if (o && ((o->cancelled && *o->cancelled) || (o->service && o->service(o->ctx)))) { errno = ECANCELED; return -1; }
    if (dns_now_ms() >= deadline) { errno = ETIMEDOUT; return -1; }
    return 0;
}
static int literal(const char *host, unsigned port, dns_addresses_t *out)
{
    unsigned char data[16];
    if (inet_pton(AF_INET,host,data) == 1) { address(out,1,port,data); return 1; }
    if (inet_pton(AF_INET6,host,data) == 1) { address(out,28,port,data); return 1; }
    return 0;
}
static int server(char *s, int ports, dns_addresses_t *out)
{
    unsigned port = 53; char *p = NULL;
    if (ports && *s == '[') { char *end = strchr(s,']'); if (!end || end[1] != ':') return -1; *end = 0; p = end+2; s++; }
    else if (ports && strchr(s,':') == strrchr(s,':') && strchr(s,':')) { p = strchr(s,':'); *p++ = 0; }
    if (p) {
        port = 0; if (!*p) return -1;
        for (; *p; p++) { if (*p < '0' || *p > '9' || port > 6553) return -1; port = port*10+(unsigned)(*p-'0'); }
        if (!port || port > 65535) return -1;
    }
    return literal(s,port,out) ? 0 : -1;
}
static int same(const struct sockaddr_storage *a, const struct sockaddr_storage *b)
{
    if (a->ss_family != b->ss_family) return 0;
    if (a->ss_family == AF_INET) {
        const struct sockaddr_in *x = (const void *)a, *y = (const void *)b;
        return x->sin_port == y->sin_port && x->sin_addr.s_addr == y->sin_addr.s_addr;
    }
    const struct sockaddr_in6 *x = (const void *)a, *y = (const void *)b;
    return x->sin6_port == y->sin6_port && x->sin6_scope_id == y->sin6_scope_id && !memcmp(&x->sin6_addr,&y->sin6_addr,16);
}
static int query(const char *host, unsigned type, unsigned port, const dns_addresses_t *servers, unsigned i, int64_t deadline, const dns_options_t *o, dns_addresses_t *out)
{
    unsigned char b[DNS_PACKET_MAX+1], q[12+255+4] = {0}; uint16_t id;
    int random = open("/dev/urandom",O_RDONLY | O_CLOEXEC); if (random < 0) return -1;
    size_t got = 0;
    while (got < sizeof id) { ssize_t k = read(random,(unsigned char *)&id+got,sizeof id-got); if (k < 0 && errno == EINTR) continue; if (k <= 0) break; got += (size_t)k; }
    close(random); if (got != sizeof id) return -1;
    p16(q,id); p16(q+2,0x100); p16(q+4,1); size_t n = 12;
    for (const char *s = host; *s;) { size_t k = strcspn(s,"."); q[n++] = (unsigned char)k; memcpy(q+n,s,k); n += k; s += k; if (*s) s++; }
    q[n++] = 0; p16(q+n,type); p16(q+n+2,1); n += 4;
    int fd = o && o->socket_fn ? o->socket_fn(servers->addr[i].ss_family,SOCK_DGRAM,0) : socket(servers->addr[i].ss_family,SOCK_DGRAM,0); if (fd < 0) return -1;
    int f = fcntl(fd,F_GETFL); int rc = -1;
    if (f < 0 || fcntl(fd,F_SETFL,f | O_NONBLOCK) || fcntl(fd,F_SETFD,FD_CLOEXEC)) goto end;
    /* Kernel selects an ephemeral source port. Never bind a predictable port. */
    for (;;) {
        if (check(o,deadline)) goto end;
        ssize_t k = sendto(fd,q,n,0,(const void *)&servers->addr[i],servers->len[i]);
        if (k == (ssize_t)n) break;
        if (k < 0 && errno == EINTR) continue;
        if (k < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) { struct pollfd p = {fd,POLLOUT,0}; (void)poll(&p,1,1); continue; }
        goto end;
    }
    for (;;) {
        if (check(o,deadline)) goto end;
        int64_t left = deadline-dns_now_ms();
        if (left <= 0) { errno = ETIMEDOUT; goto end; }
        struct pollfd p = {fd,POLLIN,0};
        int k = poll(&p,1,left > 25 ? 25 : (int)left);
        if (k < 0) { if (errno == EINTR) continue; goto end; }
        if (!k) continue;
        struct sockaddr_storage from = {0}; socklen_t len = sizeof from;
        ssize_t count = recvfrom(fd,b,sizeof b,0,(void *)&from,&len);
        if (count < 0) { if (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK) continue; goto end; }
        if (!same(&from,&servers->addr[i])) continue;
        rc = dns_parse(b,(size_t)count,id,host,type,port,out);
        if (rc == -2) continue;
        goto end;
    }
end:;
    int saved = errno; close(fd); errno = saved; return rc;
}
int dns_resolve(const char *host, unsigned port, int64_t deadline, const dns_options_t *o, dns_addresses_t *out, const char **error)
{
    memset(out,0,sizeof *out); errno = 0; *error = "dns: resolution failed";
    if (!host || !port || port > 65535) { errno = EINVAL; return -1; }
    if (check(o,deadline)) goto failed;
    if (literal(host,port,out)) return 0;
    if (!dns_host_valid(host)) { *error = "dns: invalid hostname"; errno = EINVAL; return -1; }
    dns_addresses_t servers = {0};
    int fd = open(o && o->resolv_path ? o->resolv_path : "/etc/resolv.conf",O_RDONLY | O_CLOEXEC | O_NONBLOCK);
    struct stat st;
    if (fd < 0 || fstat(fd,&st) || !S_ISREG(st.st_mode) || st.st_size > 65536) {
        if (fd >= 0) close(fd);
        *error = "dns: cannot read bounded regular resolv.conf"; return -1;
    }
    FILE *f = fdopen(fd,"r");
    if (!f) { close(fd); *error = "dns: cannot read resolv.conf"; return -1; }
    char line[512]; unsigned lines = 0;
    while (servers.n < 3 && lines++ < 1024 && fgets(line,sizeof line,f)) {
        if (check(o,deadline)) { fclose(f); goto failed; }
        if (!strchr(line,'\n') && !feof(f)) { int c; while ((c = fgetc(f)) != '\n' && c != EOF) {} continue; }
        char *comment = strpbrk(line,"#;"); if (comment) *comment = 0;
        char *s = line+strspn(line," \t\r\n");
        if (strncmp(s,"nameserver",10) || (s[10] != ' ' && s[10] != '\t')) continue;
        s += 10; s += strspn(s," \t"); size_t n = strcspn(s," \t\r\n");
        char *end = s+n; if (*end) { *end++ = 0; if (end[strspn(end," \t\r\n")]) continue; }
        (void)server(s,o && o->test_ports,&servers);
    }
    fclose(f);
    if (!servers.n) { *error = "dns: no nameservers in resolv.conf"; errno = EHOSTUNREACH; return -1; }
    for (unsigned type = 1; type <= 28; type += 27) {
        for (unsigned i = 0; i < servers.n; i++) {
            if (check(o,deadline)) goto failed;
            int64_t end = dns_now_ms()+2000; if (end > deadline) end = deadline;
            dns_addresses_t found;
            int rc = query(host,type,port,&servers,i,end,o,&found);
            if (rc == -3) { if (out->n) return 0; *error = "dns: NXDOMAIN"; errno = EHOSTUNREACH; return -1; }
            if (!rc) {
                for (unsigned k = 0; k < found.n && out->n < DNS_ADDR_MAX; k++) {
                    unsigned at = out->n++; out->addr[at] = found.addr[k]; out->len[at] = found.len[k];
                }
                break;
            } /* NODATA: try AAAA, not the next recursive server. */
            if (errno == ECANCELED) goto failed;
        }
    }
    if (out->n) return 0;
    errno = EHOSTUNREACH;
failed:
    if (errno == ECANCELED) *error = "dns: cancelled";
    else if (dns_now_ms() >= deadline) { if (out->n) return 0; errno = ETIMEDOUT; *error = "dns: deadline exceeded"; }
    return -1;
}
