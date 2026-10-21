/* Device status probes for the on-board HOME/LS pages and the safe-mode rescue panels.
 * All filesystem work runs on ONE low-priority worker thread; the safety/poll loop only
 * copies the latest immutable snapshot with a trylock (it never waits on the worker), so a
 * stuck read only makes the snapshot stale. Reads are bounded, nonblocking and no-follow;
 * secret files are only lstat'ed (presence/type/mode/owner/size), never opened or logged. */
#ifndef MK20_DEVSTAT_H
#define MK20_DEVSTAT_H
#include <pthread.h>
#include <stdint.h>
enum ds_udc { DS_UDC_UNKNOWN, DS_UDC_DOWN, DS_UDC_SUSPENDED, DS_UDC_UP };
enum ds_file { DS_ABSENT, DS_OK, DS_INVALID };
enum ds_key { DS_KEY_UNKNOWN, DS_KEY_MISSING, DS_KEY_SET, DS_KEY_INSECURE };
enum ds_probe { DS_PAIR, DS_UDC, DS_GADGET, DS_WLAN, DS_NET, DS_PROVIDER, DS_MARKER, DS_SECRET, DS_VERSION, DS_PROBES };
#define DS_PAIR_PERIOD_MS 1000
#define DS_PERIOD_MS 2000
#define DS_PAIR_MAX_AHEAD_S 600
#define DS_FILE_MAX 16384
#define DS_STALE_MS 10000
typedef struct {
    char version[24], ip[16], pair[7], proxy[64], route_dom[8], route_for[8];
    int ip_known, udc, wlan, net, prov, marker, key;
    int profile;          /* -1 default (no net.json), 0 mac_proxy, 1 board_wifi, -2 invalid */
    int provider, model;  /* catalogue indices; -1 default, -2 invalid */
} ds_result_t;
/* Test hook, called on the worker before each probe (may block or sleep). */
typedef void (*ds_hook_fn)(void *ctx, int probe);
typedef struct devstat_s {
    /* configuration + worker-private state (never touched by the main loop after start) */
    char root[128], pair_path[192], manifest[192], env_version[24], udc_name[64];
    ds_result_t work;
    int64_t next[DS_PROBES];
    ds_hook_fn hook; void *hook_ctx; int delay_ms;
    char buf[DS_FILE_MAX + 1];
    /* mailbox, under mu */
    pthread_mutex_t mu; pthread_cond_t cv, done;
    pthread_t th; int started, stop, exited, orphan, kick, running, running_all;
    ds_result_t box; uint32_t gen, seq, kicks_done; int64_t published_ms;
    /* main-side */
    uint32_t taken_gen; int stale_ms;
} devstat_t;
/* root: path prefix for /tmp, /sys, /proc, /run and /data (""=device); pair_path NULL =
 * <root>/run/mk20/pair-code; version = MK20_VERSION value or NULL; rel = MK20_REL or NULL. */
devstat_t *devstat_new(const char *root, const char *pair_path, const char *version, const char *rel);
/* Production: MK20_ROOT, MK20_KBD_PAIR_CODE, MK20_VERSION, MK20_REL and the test-only
 * MK20_KBD_PROBE_DELAY_MS (sleep before every probe, 0..60000, to prove nothing waits). */
devstat_t *devstat_new_env(void);
int devstat_start(devstat_t *d);          /* worker thread; -1 if it cannot be created */
/* Stops the worker; waits at most ~1 s. A worker stuck in a read is detached and frees the
 * object itself when the read returns, so a hung filesystem cannot block shutdown. */
void devstat_free(devstat_t *d);
/* Main loop, never blocks: 1 when a newer snapshot was copied to *out; *stale is set when
 * no probe cycle completed for stale_ms (DS_STALE_MS by default). */
int devstat_take(devstat_t *d, ds_result_t *out, int *stale);
int devstat_kick(devstat_t *d);           /* all probes due now; -1 when busy (retry later) */
void devstat_defaults(ds_result_t *r);
/* Tests only (blocking): kick and wait until a full cycle after the kick was published. */
int devstat_sync(devstat_t *d, int timeout_ms);
int devstat_take_wait(devstat_t *d, ds_result_t *out, int *stale); /* tests: blocking lock */
/* Tests only: run every due probe (all if all!=0) synchronously on an unstarted object. */
int devstat_run(devstat_t *d, int64_t now, int all);
/* Strict pairing-code text ("code=NNNNNN\nexpires_uptime=<s>\n") against the current
 * uptime in seconds; 0 and code[7] when valid and not expired (<= 600 s ahead). */
int devstat_parse_pair(const char *s, long long uptime, char code[7]);
#endif
