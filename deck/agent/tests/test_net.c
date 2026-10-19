#define _POSIX_C_SOURCE 200809L
#include "net.h"
#include <fcntl.h>
#include "json_util.h"
#include "mbedtls/build_info.h"
#include "mbedtls/ssl_ciphersuites.h"
#include "mbedtls/ssl.h"
#if defined(MBEDTLS_SSL_SRV_C) || defined(MBEDTLS_SSL_RENEGOTIATION) || defined(MBEDTLS_FS_IO) || defined(MBEDTLS_SSL_PROTO_DTLS)
#error "Unexpected server/renegotiation/filesystem/DTLS support"
#endif
#if defined(MBEDTLS_SHA1_C) || defined(MBEDTLS_SHA512_C) || defined(MBEDTLS_CIPHER_MODE_CBC)
#error "Unexpected legacy/unrequested algorithms"
#endif
#if !defined(MBEDTLS_X509_CHECK_KEY_USAGE) || !defined(MBEDTLS_X509_CHECK_EXTENDED_KEY_USAGE) || !defined(MBEDTLS_SSL_EXTENDED_MASTER_SECRET)
#error "Required certificate key usage checks and Extended Master Secret must be enabled"
#endif
#include <assert.h>
#include <arpa/inet.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include <utime.h>
#include <time.h>

static unsigned checks, sockets;
#define CHECK(x) do { checks++; if (!(x)) { fprintf(stderr, "line %d: %s\n", __LINE__, #x); abort(); } } while (0)
static int count_socket(int a, int b, int c) { (void)a; (void)b; (void)c; sockets++; errno = ECONNREFUSED; return -1; }
static void invalid(const char *s)
{
    netcfg_t c; CHECK(netcfg_parse(s, strlen(s), &c) == -1);
    net_options_t o = {.url = "https://localhost/", .config = &c, .socket_fn = count_socket};
    net_response_t r; CHECK(net_request(&o, &r) == -1); CHECK(!strcmp(r.class_name, "config"));
    CHECK(sockets == 0); net_response_free(&r); netcfg_free(&c);
}
static void configs(void)
{
    const char *bad[] = {"", "{", "null", "[]", "{}", "{\"version\":1}",
        "{\"version\":2,\"profile\":\"usb_wifi\",\"mode\":\"direct\"}",
        "{\"version\":1,\"profile\":\"unknown\",\"mode\":\"direct\"}",
        "{\"version\":1,\"profile\":\"usb_wifi\",\"mode\":\"DIRECT\"}",
        "{\"version\":1,\"profile\":\"usb_wifi\",\"mode\":\"proxy\"}",
        "{\"version\":01,\"profile\":\"usb_wifi\",\"mode\":\"direct\"}",
        "{\"version\":1.,\"profile\":\"usb_wifi\",\"mode\":\"direct\"}",
        "{\"version\":1,\"version\":1,\"profile\":\"usb_wifi\",\"mode\":\"direct\"}",
        "{\"version\":1,\"profile\":\"usb_wifi\\u0000evil\",\"mode\":\"direct\"}",
        "{\"version\":1,\"profile\":\"usb_wifi\",\"mode\":\"direct\",\"routes\":{\"domestic\":\"direct\",\"foreign\":\"proxy\"}}",
        "{\"version\":1,\"profile\":\"board_wifi\",\"routes\":{\"domestic\":\"direct\",\"foreign\":\"direct\"}}",
        "{\"version\":1,\"profile\":\"board_wifi\",\"routes\":{\"domestic\":\"direct\",\"foreign\":\"PROXY\"}}",
        "{\"version\":1,\"profile\":\"board_wifi\",\"routes\":{\"domestic\":\"proxy\",\"foreign\":\"blocked\"}}",
        "{\"version\":1,\"profile\":\"mac_proxy\",\"routes\":{\"domestic\":\"direct\",\"foreign\":\"proxy\"}}",
        "{\"version\":1,\"profile\":\"mac_proxy\",\"routes\":{\"domestic\":\"proxy\",\"foreign\":\"direct\"}}",
        "{\"version\":1,\"profile\":\"mac_proxy\",\"routes\":{\"domestic\":\"proxy\",\"foreign\":\"blocked\"}}",
        "{\"version\":1,\"profile\":\"usb_wifi\",\"mode\":\"proxy\",\"proxy\":{\"url\":null}}",
        "{\"version\":1,\"profile\":\"usb_wifi\",\"mode\":\"proxy\",\"proxy\":{\"url\":\"http://localhost:80\",\"fallbacks\":[\"https://localhost:80\"]}}",
        "{\"version\":1,\"profile\":\"usb_wifi\",\"mode\":\"direct\",\"proxy\":{\"url\":\"http://localhost:0\"}}",
        "{\"version\":1,\"profile\":\"usb_wifi\",\"mode\":\"direct\"} trailing"};
    for (size_t i = 0; i < sizeof bad / sizeof bad[0]; i++) invalid(bad[i]);
    char *oversize = malloc(NETCFG_MAX + 2); CHECK(oversize != NULL); memset(oversize, ' ', NETCFG_MAX + 1); oversize[NETCFG_MAX + 1] = 0;
    invalid(oversize); free(oversize);
    netcfg_t c; CHECK(netcfg_load("/definitely/missing/net.json", &c) == -1); CHECK(!c.valid);
    const char *valid[] = {
        "{\"version\":1,\"profile\":\"mac_proxy\",\"proxy\":{\"url\":\"http://172.30.250.1:7890\",\"fallbacks\":[\"http://10.20.0.1:7890\",\"http://169.254.7.1:7890\"]},\"routes\":{\"domestic\":\"proxy\",\"foreign\":\"proxy\"},\"note\":\"writer output\"}",
        "{\"version\":1,\"profile\":\"board_wifi\",\"proxy\":{\"url\":null,\"fallbacks\":[]},\"routes\":{\"domestic\":\"direct\",\"foreign\":\"blocked\"}}",
        "{\"version\":1,\"profile\":\"board_wifi\",\"proxy\":{\"url\":\"http://localhost:7890\",\"fallbacks\":[]},\"routes\":{\"domestic\":\"direct\",\"foreign\":\"proxy\"}}",
        "{\"version\":1,\"profile\":\"usb_wifi\",\"mode\":\"direct\"}",
        "{\"version\":1,\"profile\":\"usb_wifi\",\"mode\":\"proxy\",\"proxy\":{\"url\":\"http://localhost:1234\"}}"};
    net_route_t d[] = {NET_PROXY, NET_DIRECT, NET_DIRECT, NET_DIRECT, NET_PROXY};
    net_route_t f[] = {NET_PROXY, NET_BLOCKED, NET_PROXY, NET_DIRECT, NET_PROXY}, route;
    for (size_t i = 0; i < 5; i++) {
        CHECK(!netcfg_parse(valid[i], strlen(valid[i]), &c));
        CHECK(!netcfg_route_for(&c, "domestic", &route) && route == d[i]);
        CHECK(!netcfg_route_for(&c, "foreign", &route) && route == f[i]);
        CHECK(netcfg_route_for(&c, "bogus", &route) == -1); netcfg_free(&c);
    }
}
static void urls(void)
{
    net_endpoint_t e; net_url_t u;
    const char *bad[] = {"https://h:80", "http://h", "http://h:0", "http://h:65536", "http://h:-1", "http://h:80/", "http://h:80?x", "http://a@h:80", "http://[::1]:80", "http://a..b:80", "http://-a:80", "http://a-:80", "http://999.1.1.1:80", "http://h:80\r\n"};
    for (size_t i = 0; i < sizeof bad / sizeof bad[0]; i++) CHECK(net_proxy_url(bad[i], &e) == -1);
    CHECK(!net_proxy_url("http://localhost:1", &e) && e.port == 1);
    CHECK(!net_proxy_url("http://127.0.0.1:65535", &e) && e.port == 65535);
    CHECK(!net_https_url("https://localhost", &u) && !strcmp(u.path, "/") && u.endpoint.port == 443);
    CHECK(!net_https_url("https://localhost:8443?a=b", &u) && !strcmp(u.path, "/?a=b") && u.endpoint.port == 8443);
    const char *bad_https[] = {"http://localhost", "https://localhost:0/", "https://h/#a", "https://h/ x", "https://a@h/", "https://h/\r\nAuthorization: x", "https://[::1]/"};
    for (size_t i = 0; i < sizeof bad_https / sizeof bad_https[0]; i++) CHECK(net_https_url(bad_https[i], &u) == -1);
    char host[255]; memset(host, 'a', sizeof host); host[254] = 0; CHECK(!net_host_valid(host));
    host[63] = '.'; host[127] = '.'; host[191] = '.'; host[253] = 0; CHECK(net_host_valid(host));
}
static void connect_replies(void)
{
    struct { const char *s; int rc, status; } cases[] = {
        {"HTTP/1.1 200 OK\r\n\r\n", 1, 200}, {"HTTP/1.0 200 OK\r\nDate: Sun, 06 Nov 1994 08:49:37 GMT\r\n\r\n", 1, 200},
        {"HTTP/1.1 407 Auth\r\n\r\n", -3, 407}, {"HTTP/1.1 407 Auth\r\n\r\nbody", -1, 407}, {"HTTP/1.1 403 Forbidden\r\n\r\n", -3, 403},
        {"garbage\r\n\r\n", -1, 0}, {"HTTP/1.1 2000 OK\r\n\r\n", -1, 0},
        {"HTTP/1.2 200 OK\r\n\r\n", -1, 0}, {"HTTP/1.1 200 OK\r\n\r\nx", -1, 200},
        {"HTTP/1.1 200 OK\r\nBad header\r\n\r\n", -1, 200}, {"HTTP/1.1 200 OK\r\n", 0, 0}};
    for (size_t i = 0; i < sizeof cases / sizeof cases[0]; i++) {
        int status; char date[128]; CHECK(net_connect_reply((const unsigned char *)cases[i].s, strlen(cases[i].s), &status, date) == cases[i].rc);
        CHECK(status == cases[i].status);
    }
    unsigned char b[8193]; memset(b, 'a', sizeof b); int status; char date[128];
    CHECK(net_connect_reply(b, 8192, &status, date) == -1); CHECK(net_connect_reply(b, 8193, &status, date) == -1);
}
static void http(void)
{
    struct { const char *s; int rc; size_t cap; } cases[] = {
        {"HTTP/1.1 200 OK\r\nContent-Length: 3\r\n\r\nabc", 1, 3},
        {"HTTP/1.1 401 No\r\nDate: Sun, 06 Nov 1994 08:49:37 GMT\r\n\r\nabc", 1, 3},
        {"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n3\r\nabc\r\n0\r\n\r\n", 1, 3},
        {"HTTP/1.1 200 OK\r\nTransfer-Encoding: Chunked\r\n\r\n1; foo=\"a\\\"b\"\r\na\r\n2\r\nbc\r\n0\r\nX-Extra: yes\r\n\r\n", 1, 3},
        {"HTTP/1.1 204 No Content\r\n\r\n", 1, 3},
        {"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\n", -2, 3},
        {"HTTP/1.1 200 OK\r\n\r\nabcd", -2, 3},
        {"HTTP/1.1 200 OK\r\nContent-Length: 3\r\n\r\nab", -1, 3},
        {"HTTP/1.1 200 OK\r\nContent-Length: 3\r\n\r\nabcd", -1, 8},
        {"HTTP/1.1 200 OK\r\nContent-Length: -1\r\n\r\n", -1, 8},
        {"HTTP/1.1 200 OK\r\nContent-Length: 1\r\nContent-Length: 1\r\n\r\na", -1, 8},
        {"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n", -1, 8},
        {"HTTP/1.1 200 OK\r\nTransfer-Encoding: gzip, chunked\r\n\r\n", -1, 8},
        {"HTTP/1.1 200 OK\r\n Bad: fold\r\n\r\n", -1, 8},
        {"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nx\r\n", -1, 8},
        {"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n9\r\n", -2, 8},
        {"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nFFFFFFFFFFFFFFFFFFFFFFFF\r\n", -1, 8},
        {"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n1\r\naX\r\n0\r\n\r\n", -1, 8},
        {"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n1\r\na\r\n0\r\n", -1, 8},
        {"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n0\r\nContent-Length: 0\r\n\r\n", -1, 8},
        {"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n0\r\nBad trailer\r\n\r\n", -1, 8},
        {"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n1;=bad\r\na\r\n0\r\n\r\n", -1, 8}};
    for (size_t i = 0; i < sizeof cases / sizeof cases[0]; i++) {
        net_response_t r;
        CHECK(net_http_parse((const unsigned char *)cases[i].s, strlen(cases[i].s), 1, cases[i].cap, &r) == cases[i].rc);
        if (i < 4) CHECK(r.bytes == 3 && !memcmp(r.body, "abc", 3));
        if (i == 1) CHECK(!strcmp(r.date, "Sun, 06 Nov 1994 08:49:37 GMT") && r.http_status == 401);
        net_response_free(&r);
    }
    const char *s = cases[2].s; size_t n = strlen(s);
    for (size_t i = 0; i < n; i++) { net_response_t r; CHECK(net_http_parse((const unsigned char *)s, i, 0, 8, &r) == 0); net_response_free(&r); }
    unsigned char large[16386]; memset(large, 'a', sizeof large); net_response_t r;
    CHECK(net_http_parse(large, sizeof large, 1, 8, &r) == -1); net_response_free(&r);
}
static void config_files(void)
{
    char path[] = "/tmp/mk20-a3-config-XXXXXX"; int fd = mkstemp(path); CHECK(fd >= 0); close(fd);
    netcfg_t c; CHECK(netcfg_load(path, &c) == -1); CHECK(!c.valid);
    FILE *f = fopen(path, "w"); CHECK(f != NULL);
    for (unsigned i = 0; i <= NETCFG_MAX; i++) fputc(' ', f);
    fclose(f); CHECK(netcfg_load(path, &c) == -1);
    CHECK(!chmod(path, 0)); if (geteuid() != 0) CHECK(netcfg_load(path, &c) == -1);
    CHECK(!chmod(path, 0600)); unlink(path);
    CHECK(!mkfifo(path, 0600)); CHECK(netcfg_load(path, &c) == -1); unlink(path);
    CHECK(netcfg_load("/tmp", &c) == -1); CHECK(sockets == 0);
    const char *direct = "{\"version\":1,\"profile\":\"usb_wifi\",\"mode\":\"direct\"}";
    CHECK(!netcfg_parse(direct, strlen(direct), &c));
    net_options_t o = {.url = "https://127.0.0.1/", .config = &c, .clock = {.trust_clock = 1}, .socket_fn = count_socket};
#ifdef MK20_NET_TEST
    o.connected_fd = -1;
#endif
    net_response_t r;
    CHECK(net_request(&o, &r) == -1); CHECK(!strcmp(r.class_name, "connect")); CHECK(sockets == 1);
    net_response_free(&r); netcfg_free(&c); sockets = 0;
}
static int setter(time_t epoch, void *ctx) { *(time_t *)ctx = epoch; return 0; }
static int reject_setter(time_t epoch, void *ctx) { (void)epoch; (void)ctx; return -1; }
static void clock_keys(void)
{
    time_t epoch; CHECK(!agent_date_parse("Sun, 06 Nov 1994 08:49:37 GMT", &epoch) && epoch == 784111777);
    CHECK(!agent_date_parse("Thu, 29 Feb 2024 00:00:00 GMT", &epoch));
    const char *bad[] = {"Sunday, 06-Nov-94 08:49:37 GMT", "Sun Nov  6 08:49:37 1994", "Sun, 06 Nov 1994 08:49:37 UTC", "Mon, 06 Nov 1994 08:49:37 GMT", "Sun, 31 Feb 1994 08:49:37 GMT", "Thu, 29 Feb 2023 00:00:00 GMT", "Sun, 06 Nov 1994 24:49:37 GMT", "Sun, 06 Nov 1994 08:60:00 GMT", "Sun, 06 Nov 1994 08:49:60 GMT", "Sun, 6 Nov 1994 08:49:37 GMT", "Sun, 06 Nov 1994 08:49:37 GMT garbage"};
    for (size_t i = 0; i < sizeof bad / sizeof bad[0]; i++) CHECK(agent_date_parse(bad[i], &epoch) == -1);
    char path[] = "/tmp/mk20-a3-unit-XXXXXX"; int fd = mkstemp(path); CHECK(fd >= 0); close(fd); unlink(path);
    time_t floor = agent_clock_floor(), set = 0, now = time(NULL);
    agent_clock_options_t o = {.marker_path = path, .setter = setter, .setter_ctx = &set};
    CHECK(!agent_clock_trusted_at(&o, now, now - 100));
    o.trust_clock = 1; CHECK(agent_clock_trusted_at(&o, floor, floor - 1)); CHECK(!agent_clock_trusted_at(&o, floor - 1, floor - 100));
    o.trust_clock = 0;
    CHECK(agent_clock_sync(&o, now + 121, now) == -1 && !set && access(path, F_OK));
    o.set_clock = 1; o.setter = reject_setter;
    CHECK(agent_clock_sync(&o, now + 121, now) == -1 && access(path, F_OK)); o.setter = setter;
    CHECK(!agent_clock_sync(&o, now + 121, now) && set == now + 121);
    CHECK(agent_clock_trusted_at(&o, now + 1, now - 100));
    struct utimbuf old = {now - 1000, now - 1000}; CHECK(!utime(path, &old)); CHECK(!agent_clock_trusted_at(&o, now, now - 100));
    CHECK(agent_clock_sync(&o, floor - 1, now) == -1);
    CHECK(!agent_clock_sync(&o, now - 121, now) && set == now - 121);
    o.set_clock = 0; CHECK(!agent_clock_sync(&o, now + 120, now)); unlink(path);
    FILE *f = fopen(path, "w"); CHECK(f != NULL); fputs("test-secret \t\r\n", f); fclose(f);
    char key[4097]; CHECK(!chmod(path, 0600)); CHECK(!net_read_key(path, key) && !strcmp(key, "test-secret"));
    CHECK(!chmod(path, 0400)); CHECK(!net_read_key(path, key));
    const mode_t modes[] = {0644, 0640, 0660, 0604, 0700};
    for (size_t i = 0; i < sizeof modes / sizeof modes[0]; i++) { CHECK(!chmod(path, modes[i])); CHECK(net_read_key(path, key) == -1 && !key[0]); }
    CHECK(!chmod(path, 0600));
    f = fopen(path, "w"); CHECK(f != NULL); for (unsigned i = 0; i < 4097; i++) fputc('x', f); fclose(f);
    CHECK(net_read_key(path, key) == -1); unlink(path);
    CHECK(net_read_key("/tmp", key) == -1); CHECK(net_read_key(path, key) == -1);
    CHECK(!mkfifo(path, 0600)); CHECK(net_read_key(path, key) == -1); unlink(path);
    CHECK(!symlink("/etc/passwd", path)); CHECK(net_read_key(path, key) == -1); unlink(path);
}
static unsigned attempts[16], attempt_count;
static int record_connect(int fd, const struct sockaddr *a, socklen_t n)
{
    if (attempt_count < 16 && a->sa_family == AF_INET) attempts[attempt_count++] = ntohs(((const struct sockaddr_in *)a)->sin_port);
    return connect(fd, a, n);
}
static int request_driver(char **argv)
{
    netcfg_t c; if (netcfg_load(argv[1], &c)) return 2;
    net_header_t header = {"Content-Type", "application/json"};
    net_options_t o = {.config = &c, .url = argv[2], .key_file = strcmp(argv[3], "-") ? argv[3] : NULL,
        .ca_file = argv[4], .method = "POST", .body = (const unsigned char *)"payload", .body_len = 7,
        .headers = &header, .header_count = 1, .clock = {.trust_clock = 1}, .connect_fn = record_connect};
#ifdef MK20_NET_TEST
    const char *fd = getenv("MK20_NET_TEST_FD"); o.connected_fd = fd ? atoi(fd) : -1;
#endif
    net_response_t r; int rc = net_request(&o, &r);
    printf("{\"ok\":%s,\"class\":\"%s\",\"bytes\":%zu,\"attempts\":[", rc ? "false" : "true", r.class_name, r.bytes);
    for (unsigned i = 0; i < attempt_count; i++) printf("%s%u", i ? "," : "", attempts[i]);
    puts("]}"); net_response_free(&r); netcfg_free(&c); return rc ? 1 : 0;
}
static int cancel_fd, cancel_started, cancel_calls;
static int64_t cancel_start;
static int64_t test_now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t); return (int64_t)t.tv_sec*1000+t.tv_nsec/1000000; }
static int cancel_factory(int a,int b,int c) { (void)a;(void)b;(void)c;cancel_started=1;cancel_start=test_now();return dup(cancel_fd); }
static int cancel_connect(int fd,const struct sockaddr *a,socklen_t n) { (void)fd;(void)a;(void)n;return 0; }
static int safety_service(void *ctx) { (void)ctx;cancel_calls++;return cancel_started && test_now()-cancel_start>=40 ? -1 : 0; }
static void cancellation(void)
{
    netcfg_t c; const char *s="{\"version\":1,\"profile\":\"mac_proxy\",\"routes\":{\"domestic\":\"proxy\",\"foreign\":\"proxy\"},\"proxy\":{\"url\":\"http://127.0.0.1:7890\"}}";
    CHECK(!netcfg_parse(s,strlen(s),&c));
    int cancelled_flag=1;
    net_options_t o={.url="https://localhost/",.config=&c,.clock={.trust_clock=1},.cancelled=&cancelled_flag,.socket_fn=count_socket};
#ifdef MK20_NET_TEST
    o.connected_fd=-1;
#endif
    net_response_t r;CHECK(net_request(&o,&r));CHECK(!strcmp(r.class_name,"cancelled")&&sockets==0);net_response_free(&r);
    cancelled_flag=0;int pair[2];CHECK(!socketpair(AF_UNIX,SOCK_STREAM,0,pair));cancel_fd=pair[0];
    o.socket_fn=cancel_factory;o.connect_fn=cancel_connect;o.service=safety_service;o.timeout_ms=5000;
    int64_t start=test_now();CHECK(net_request(&o,&r));CHECK(!strcmp(r.class_name,"cancelled"));
    CHECK(test_now()-start<500&&cancel_calls>=3);net_response_free(&r);close(pair[0]);close(pair[1]);netcfg_free(&c);
}
static unsigned dns_queries;
static int no_dns(int a,int b,int c) { (void)a; (void)b; (void)c; dns_queries++; return -1; }
typedef struct { int fd; char line[1024]; size_t used; } proxy_fixture_t;
static int proxy_service(void *v)
{
    proxy_fixture_t *f = v;
    ssize_t n = recv(f->fd,f->line+f->used,sizeof f->line-1-f->used,0);
    if (n > 0) {
        f->used += (size_t)n; f->line[f->used] = 0;
        if (strstr(f->line,"\r\n\r\n")) {
            const char *reply = "HTTP/1.1 403 Refused\r\n\r\n";
            CHECK(send(f->fd,reply,strlen(reply),0) == (ssize_t)strlen(reply));
        }
    }
    return 0;
}
static void proxy_target_no_dns(void)
{
    char path[] = "/tmp/a8-proxy-resolv-XXXXXX"; int fd = mkstemp(path); CHECK(fd >= 0);
    const char *ns = "nameserver 127.0.0.1\n"; CHECK(write(fd,ns,strlen(ns)) == (ssize_t)strlen(ns)); close(fd);
    const char *json = "{\"version\":1,\"profile\":\"mac_proxy\",\"proxy\":{\"url\":\"http://127.0.0.1:7890\"},\"routes\":{\"domestic\":\"proxy\",\"foreign\":\"proxy\"}}";
    netcfg_t c; CHECK(!netcfg_parse(json,strlen(json),&c));
    int pair[2]; CHECK(!socketpair(AF_UNIX,SOCK_STREAM,0,pair)); cancel_fd = pair[0];
    CHECK(!fcntl(pair[1],F_SETFL,O_NONBLOCK));
    proxy_fixture_t f = {.fd=pair[1]};
    net_options_t o = {.url="https://open.bigmodel.cn/",.config=&c,.clock={.trust_clock=1},
        .socket_fn=cancel_factory,.connect_fn=cancel_connect,.service=proxy_service,.service_ctx=&f,
        .dns={.resolv_path=path,.socket_fn=no_dns},.timeout_ms=500};
#ifdef MK20_NET_TEST
    o.connected_fd = -1;
#endif
    net_response_t r; CHECK(net_request(&o,&r)); CHECK(!strcmp(r.class_name,"proxy"));
    CHECK(!strncmp(f.line,"CONNECT open.bigmodel.cn:443 HTTP/1.1\r\n",strlen("CONNECT open.bigmodel.cn:443 HTTP/1.1\r\n"))); CHECK(dns_queries == 0);
    /* Prove the resolver accounting seam catches a nonliteral endpoint. */
    dns_addresses_t a; const char *error;
    CHECK(dns_resolve("open.bigmodel.cn",443,test_now()+100,&o.dns,&a,&error)); CHECK(dns_queries > 0);
    net_response_free(&r); netcfg_free(&c); close(pair[0]); close(pair[1]); unlink(path);
}
static void release_ca_default(void)
{
    const char *old = getenv("MK20_REL"); char *saved = old ? strdup(old) : NULL;
    CHECK(!setenv("MK20_REL","/definitely/missing/a8-release",1));
    netcfg_t c; const char *json="{\"version\":1,\"profile\":\"usb_wifi\",\"mode\":\"direct\"}";
    CHECK(!netcfg_parse(json,strlen(json),&c));
    net_options_t o={.url="https://127.0.0.1/",.config=&c,.clock={.trust_clock=1},.socket_fn=count_socket};
#ifdef MK20_NET_TEST
    o.connected_fd=-1;
#endif
    net_response_t r; sockets=0;
    CHECK(net_request(&o,&r) && !strcmp(r.class_name,"config") && sockets==0); net_response_free(&r);
    o.ca_file=NET_CA_DEFAULT;
    CHECK(net_request(&o,&r) && !strcmp(r.class_name,"connect") && sockets==1); net_response_free(&r);
    if (saved) { CHECK(!setenv("MK20_REL",saved,1)); free(saved); } else CHECK(!unsetenv("MK20_REL"));
    netcfg_free(&c); sockets=0;
}
int main(int argc, char **argv)
{
    if (argc == 5) return request_driver(argv);
    const int *suites = mbedtls_ssl_list_ciphersuites();
    unsigned suite_count = 0;
    for (; *suites; suites++) {
        const char *name = mbedtls_ssl_get_ciphersuite_name(*suites);
        CHECK(strstr(name, "GCM") || strstr(name, "CHACHA20-POLY1305")); suite_count++;
    }
    CHECK(suite_count == 9);
    configs(); urls(); connect_replies(); http(); clock_keys(); config_files(); cancellation(); proxy_target_no_dns(); release_ca_default();
    printf("net/clock: %u checks passed, invalid-config socket calls: %u\n", checks, sockets); return 0;
}
