#ifndef MK20_HTTPD_H
#define MK20_HTTPD_H
#include <stddef.h>
#include <stdint.h>
#include <signal.h>
#define HTTPD_HEADERS 8192
#define HTTPD_BODY 65536
#define HTTPD_CONNECTIONS 4
#define HTTPD_DEADLINE 5000
/* Fixed request metadata; bodies are bounded and never logged. */
typedef struct {
    char method[16], path[256], host[128], origin[256], auth[256];
    char session[128], version[64], content_type[128];
    int has_origin, has_auth, has_session, has_version, close;
    size_t body_len, consumed; const char *body;
    uint64_t tag;
} http_request_t;
typedef struct { int status; char session[33]; char *body; size_t len; } http_response_t;
/* 0 incomplete, 1 complete, otherwise HTTP error. No request smuggling via CL/TE. */
int httpd_parse(const char *, size_t, http_request_t *);
int httpd_expired(int64_t start, int64_t now);
int httpd_authorized(const char *header, const char token[65]);
int httpd_token(const char *path, char token[65]);
int httpd_random_hex(char *out, size_t bytes);
/* Numeric IPv4, explicit address only. no_device is restricted to loopback. */
int httpd_address(const char *listen, int no_device, char host[128]);
int httpd_listen(const char *listen, int no_device);
typedef struct {
    void *ctx;
    /* Handler may return 1 while an action awaits keyboard confirmation. */
    int (*handle)(void *, const http_request_t *, http_response_t *);
    void (*tick)(void *);
    void (*disconnect)(void *, uint64_t);
    void (*attach_pump)(void *, void (*)(void *), void *);
} http_handler_t;
/* connected_fd is a socketpair test seam, -1 for accept(). Owns supplied fds. */
int httpd_run_fds(int listener, const int *fds, size_t count, const http_handler_t *, volatile sig_atomic_t *stop);
int httpd_run(int listener, int connected_fd, const http_handler_t *, volatile sig_atomic_t *stop);
#endif
