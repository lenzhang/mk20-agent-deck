/* Device status probes for the on-board HOME/LS pages (no Mac required).
 * Main loop only, never the safety tick: devstat_poll() runs at most one small probe per
 * call (all due probes on page entry), every read is bounded, nonblocking and no-follow,
 * there is no allocation after the first UDC lookup, and secret files are only lstat'ed
 * (presence/type/mode/owner/size) — their content is never opened, read or logged. */
#ifndef MK20_DEVSTAT_H
#define MK20_DEVSTAT_H
#include <stdint.h>
enum ds_udc { DS_UDC_UNKNOWN, DS_UDC_DOWN, DS_UDC_SUSPENDED, DS_UDC_UP };
enum ds_file { DS_ABSENT, DS_OK, DS_INVALID };
enum ds_key { DS_KEY_UNKNOWN, DS_KEY_MISSING, DS_KEY_SET, DS_KEY_INSECURE };
enum ds_probe { DS_PAIR, DS_UDC, DS_GADGET, DS_WLAN, DS_NET, DS_PROVIDER, DS_MARKER, DS_SECRET, DS_VERSION, DS_PROBES };
#define DS_PAIR_PERIOD_MS 1000
#define DS_PERIOD_MS 2000
#define DS_PAIR_MAX_AHEAD_S 600
#define DS_FILE_MAX 16384
typedef struct {
    char version[24], ip[16], pair[7], proxy[64], route_dom[8], route_for[8];
    int ip_known, udc, wlan, net, prov, marker, key;
    int profile;          /* -1 default (no net.json), 0 mac_proxy, 1 board_wifi, -2 invalid */
    int provider, model;  /* catalogue indices; -1 default, -2 invalid */
} ds_result_t;
typedef struct {
    ds_result_t r;
    uint32_t gen;         /* increments whenever a result changes */
    char root[128], pair_path[192], manifest[192], env_version[24], udc_name[64];
    int64_t next[DS_PROBES];
    char buf[DS_FILE_MAX + 1];
} devstat_t;
/* root: path prefix for /tmp, /sys, /proc, /run and /data (""=device); pair_path NULL =
 * <root>/run/mk20/pair-code; version = MK20_VERSION value or NULL; rel = MK20_REL or NULL. */
void devstat_init(devstat_t *d, const char *root, const char *pair_path, const char *version, const char *rel);
/* Production defaults from the environment: MK20_ROOT, MK20_KBD_PAIR_CODE, MK20_VERSION, MK20_REL. */
void devstat_init_env(devstat_t *d);
/* Run one due probe (all due probes if all!=0). Returns 1 when a visible result changed. */
int devstat_poll(devstat_t *d, int64_t now, int all);
void devstat_kick(devstat_t *d); /* every periodic probe due now (HOME/LS entry) */
/* Strict pairing-code text ("code=NNNNNN\nexpires_uptime=<s>\n") against the current
 * uptime in seconds; 0 and code[7] when valid and not expired (<= 600 s ahead). */
int devstat_parse_pair(const char *s, long long uptime, char code[7]);
#endif
