#ifndef MK20_DNS_H
#define MK20_DNS_H
#include <stddef.h>
#include <stdint.h>
#include <sys/socket.h>
#define DNS_ADDR_MAX 8
#define DNS_PACKET_MAX 4096
/* No NSS, search suffixes, cache, TCP fallback or public DNS fallback. */
typedef struct { unsigned n; struct sockaddr_storage addr[DNS_ADDR_MAX]; socklen_t len[DNS_ADDR_MAX]; } dns_addresses_t;
typedef struct {
    const char *resolv_path; /* NULL = /etc/resolv.conf */
    int test_ports; /* Explicit local test seam: nameserver literal:port / [v6]:port. */
    int (*service)(void *); void *ctx;
    const volatile int *cancelled;
    int (*socket_fn)(int, int, int); /* Optional host fault/accounting seam. */
} dns_options_t;
int64_t dns_now_ms(void);
int dns_host_valid(const char *);
int dns_resolve(const char *, unsigned, int64_t, const dns_options_t *, dns_addresses_t *, const char **);
/* Packet parser shared with hostile-packet tests. -2 unrelated, -3 NXDOMAIN,
 * -1 invalid/server error, 0 valid (possibly NODATA). Only answers supply addresses. */
int dns_parse(const unsigned char *, size_t, uint16_t, const char *, unsigned, unsigned, dns_addresses_t *);
#endif
