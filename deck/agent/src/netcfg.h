#ifndef MK20_NETCFG_H
#define MK20_NETCFG_H
#include <stddef.h>
#include <stdint.h>
#define NETCFG_MAX 16384u
/* Each URL needs at least 12 bytes including its JSON quotes. */
#define NETCFG_PROXIES (NETCFG_MAX / 12u + 1u)
typedef struct { char host[254]; unsigned port; } net_endpoint_t;
typedef enum { NET_BLOCKED, NET_PROXY, NET_DIRECT } net_route_t;
typedef struct {
    unsigned valid;
    enum { NET_MAC_PROXY, NET_BOARD_WIFI, NET_USB_WIFI } profile;
    net_route_t domestic, foreign;
    net_endpoint_t *proxies;
    size_t proxy_count;
} netcfg_t;
int net_host_valid(const char *host);
int net_proxy_url(const char *url, net_endpoint_t *out);
/* Always zero-initialize the destination; free before reuse. No socket/DNS calls. */
int netcfg_parse(const char *json, size_t len, netcfg_t *out);
int netcfg_load(const char *path, netcfg_t *out);
int netcfg_route_for(const netcfg_t *cfg, const char *provider_route, net_route_t *route);
void netcfg_free(netcfg_t *cfg);
#endif
