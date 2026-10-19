#ifndef MK20_NET_H
#define MK20_NET_H
#include "netcfg.h"
#include "clock.h"
#include <stddef.h>
#include <stdint.h>
#include <sys/socket.h>
#define NET_BODY_MAX (8u * 1024u * 1024u)
#define NET_CA_DEFAULT "vendor/ca/cacert.pem"
typedef struct { net_endpoint_t endpoint; char path[4096]; } net_url_t;
typedef struct { const char *name, *value; } net_header_t;
typedef struct {
    const char *url, *method, *key_file, *ca_file, *provider_route;
    const netcfg_t *config;
    const net_header_t *headers;
    size_t header_count;
    const unsigned char *body;
    size_t body_len, body_cap;
    int timeout_ms;
    agent_clock_options_t clock;
    /* Optional socket factory, for accounting/fault injection. NULL = socket(). */
    int (*socket_fn)(int domain, int type, int protocol);
    int (*connect_fn)(int fd, const struct sockaddr *address, socklen_t len);
#ifdef MK20_NET_TEST
    int connected_fd; /* Test-only CLI transports set this to an inherited FD. */
#endif
} net_options_t;
typedef struct {
    int ok, http_status;
    const char *class_name; /* Stable literal, never raw peer data. */
    char detail[160], via[280], date[128], proxy_date[128];
    unsigned char *body;
    size_t bytes;
} net_response_t;
int net_https_url(const char *s, net_url_t *out);
int net_read_key(const char *path, char key[4097]);
/* Incremental parsers: 1=complete, 0=need data, -1=invalid, -2=body cap.
 * CONNECT additionally returns -3 for a non-200 status. */
int net_connect_reply(const unsigned char *data, size_t len, int *status, char date[128]);
int net_http_parse(const unsigned char *data, size_t len, int eof, size_t cap, net_response_t *out);
int net_request(const net_options_t *o, net_response_t *out);
void net_response_free(net_response_t *out);
/* Plain HTTP bootstrap is restricted to numeric ECM Mac addresses, bound usb0.
 * Tests alone allow loopback. Never uses the model route or proxy. */
int net_time_sync(const char *mac_host, unsigned port, const agent_clock_options_t *clock, net_response_t *out);
#endif
