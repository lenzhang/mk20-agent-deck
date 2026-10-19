#define _POSIX_C_SOURCE 200809L
#include "netcfg.h"
#include "json_util.h"
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
#include <stdlib.h>
#include <string.h>

static int digit(char c) { return c >= '0' && c <= '9'; }
int net_host_valid(const char *s)
{
    size_t n = strlen(s), label = 0; int numeric = 1;
    if (!n || n > 253) return 0;
    for (size_t i = 0; i < n; i++) {
        char c = s[i];
        if (!digit(c) && c != '.') numeric = 0;
        if (c == '.') {
            if (!label || s[i - 1] == '-') return 0;
            label = 0;
        } else {
            if (!(digit(c) || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || c == '-')) return 0;
            if ((!label && c == '-') || ++label > 63) return 0;
        }
    }
    if (!label || s[n - 1] == '-') return 0;
    if (numeric) { struct in_addr a; return inet_pton(AF_INET, s, &a) == 1; }
    return 1;
}
int net_proxy_url(const char *s, net_endpoint_t *out)
{
    if (!s || strncmp(s, "http://", 7)) return -1;
    const char *colon = strchr(s + 7, ':');
    if (!colon || colon == s + 7 || (size_t)(colon - s - 7) > 253) return -1;
    net_endpoint_t e = {{0}, 0}; memcpy(e.host, s + 7, (size_t)(colon - s - 7));
    if (!net_host_valid(e.host) || !colon[1]) return -1;
    for (const char *p = colon + 1; *p; p++) {
        if (!digit(*p) || e.port > 6553) return -1;
        e.port = e.port * 10 + (unsigned)(*p - '0');
        if (e.port > 65535) return -1;
    }
    if (!e.port) return -1;
    *out = e; return 0;
}
void netcfg_free(netcfg_t *c) { if (c) { free(c->proxies); memset(c, 0, sizeof *c); } }
static const cJSON *get(const cJSON *j, const char *k) { return cJSON_GetObjectItemCaseSensitive(j, k); }
static int eq(const cJSON *j, const char *s) { return cJSON_IsString(j) && !strcmp(j->valuestring, s); }
static int route(const cJSON *j, net_route_t *r)
{
    if (eq(j, "proxy")) *r = NET_PROXY;
    else if (eq(j, "direct")) *r = NET_DIRECT;
    else if (eq(j, "blocked")) *r = NET_BLOCKED;
    else return -1;
    return 0;
}
int netcfg_parse(const char *s, size_t n, netcfg_t *out)
{
    memset(out, 0, sizeof *out); netcfg_t c = {0}; int rc = -1;
    cJSON *j = agent_json_parse(s, n, NETCFG_MAX);
    if (!cJSON_IsObject(j)) goto done;
    const cJSON *ver = get(j, "version"), *p = get(j, "profile"), *routes = get(j, "routes");
    if (!cJSON_IsNumber(ver) || ver->valuedouble != 1) goto done;
    if (eq(p, "mac_proxy")) c.profile = NET_MAC_PROXY;
    else if (eq(p, "board_wifi")) c.profile = NET_BOARD_WIFI;
    else if (eq(p, "usb_wifi")) c.profile = NET_USB_WIFI;
    else goto done;
    if (c.profile == NET_USB_WIFI) {
        const cJSON *mode = get(j, "mode");
        if (eq(mode, "direct")) c.domestic = c.foreign = NET_DIRECT;
        else if (eq(mode, "proxy")) c.domestic = c.foreign = NET_PROXY;
        else goto done;
        /* If supplied, routes cannot override the explicit mode. */
        if (routes) {
            net_route_t d, f;
            if (!cJSON_IsObject(routes) || route(get(routes, "domestic"), &d) ||
                route(get(routes, "foreign"), &f) || d != c.domestic || f != c.foreign) goto done;
        }
    } else {
        if (!cJSON_IsObject(routes) || route(get(routes, "domestic"), &c.domestic) ||
            route(get(routes, "foreign"), &c.foreign)) goto done;
        if (c.profile == NET_MAC_PROXY && (c.domestic != NET_PROXY || c.foreign != NET_PROXY)) goto done;
        if (c.profile == NET_BOARD_WIFI && (c.domestic != NET_DIRECT || c.foreign == NET_DIRECT)) goto done;
    }
    const cJSON *proxy = get(j, "proxy"), *url = get(proxy, "url"), *fb = get(proxy, "fallbacks");
    if (proxy && !cJSON_IsObject(proxy)) goto done;
    if (url && !cJSON_IsNull(url) && !cJSON_IsString(url)) goto done;
    if (fb && !cJSON_IsArray(fb)) goto done;
    int count = fb ? cJSON_GetArraySize(fb) : 0;
    if (count < 0 || count >= (int)NETCFG_PROXIES) goto done;
    if (cJSON_IsString(url)) {
        c.proxies = calloc((size_t)count + 1, sizeof *c.proxies);
        if (!c.proxies || net_proxy_url(url->valuestring, c.proxies)) goto done;
        c.proxy_count = 1;
        for (const cJSON *a = fb ? fb->child : NULL; a; a = a->next) {
            if (!cJSON_IsString(a) || net_proxy_url(a->valuestring, &c.proxies[c.proxy_count])) goto done;
            c.proxy_count++;
        }
    } else if (count || c.domestic == NET_PROXY || c.foreign == NET_PROXY) goto done;
    c.valid = 0x4e455431u; *out = c; memset(&c, 0, sizeof c); rc = 0;
done:
    cJSON_Delete(j); netcfg_free(&c); return rc;
}
int netcfg_load(const char *path, netcfg_t *out)
{
    memset(out, 0, sizeof *out);
    int fd = path ? open(path, O_RDONLY | O_NONBLOCK) : -1;
    if (fd < 0) return -1;
    struct stat st;
    if (fstat(fd, &st) || !S_ISREG(st.st_mode) || st.st_size < 1 || st.st_size > NETCFG_MAX) { close(fd); return -1; }
    char *s = malloc(NETCFG_MAX + 1); if (!s) { close(fd); return -1; }
    size_t n = 0; int bad = 0;
    while (n <= NETCFG_MAX) {
        ssize_t k = read(fd, s + n, NETCFG_MAX + 1 - n);
        if (k < 0 && errno == EINTR) continue;
        if (k < 0) { bad = 1; break; }
        if (!k) break;
        n += (size_t)k;
    }
    if (close(fd)) bad = 1;
    int rc = bad ? -1 : netcfg_parse(s, n, out); free(s); return rc;
}
int netcfg_route_for(const netcfg_t *c, const char *r, net_route_t *out)
{
    if (!c || c->valid != 0x4e455431u || !r) return -1;
    net_route_t v;
    if (!strcmp(r, "domestic")) v = c->domestic;
    else if (!strcmp(r, "foreign")) v = c->foreign;
    else return -1;
    if (v == NET_DIRECT && !(c->profile == NET_USB_WIFI ||
        (c->profile == NET_BOARD_WIFI && !strcmp(r, "domestic")))) return -1;
    if (v == NET_PROXY && (!c->proxy_count || !c->proxies)) return -1;
    if (v != NET_DIRECT && v != NET_PROXY && v != NET_BLOCKED) return -1;
    *out = v; return 0;
}
