/* mk20-kbd local control socket /run/mk20/kbd.sock (agent A1; protocol: CTL-PROTOCOL.md).
 * Opt-in (--ctl PATH); without it mk20-kbd behaves exactly as before.
 * mk20-kbd stays the ONLY holder of /dev/hidg0: mk20-agent sends input over this socket under an input lease.
 * Safety rules handled here, in-process, independent of the agent:
 *  - lease TTL (default 3 s, 200..10000 ms): not renewed in time -> revoked + all-keys-up + pointer buttons up;
 *  - e-stop (socket op "estop", reserved page key / opt-in K2 hold or triple press): revoke + all-up synchronously (bounded
 *    50 ms write), latch "stopped", allowed=0; only the keyboard (ctl_set_allowed) can re-allow - never the socket;
 *  - human priority: a physical key press revokes the lease (no latch);
 *  - peers must have our uid (SO_PEERCRED), socket 0600, at most CTL_MAX_CLIENTS, lines <= CTL_LINE_MAX,
 *    a client that does not read its replies is dropped (never blocks the key/HID loop). */
#ifndef MK20_CTL_H
#define MK20_CTL_H
#include "hid.h"
#include "ctlscr.h"
#include <poll.h>
#include <stddef.h>
#include <stdint.h>

#define CTL_MAX_CLIENTS 2
#define CTL_LINE_MAX 1024
#define CTL_TTL_DEFAULT 3000
#define CTL_TTL_MIN 200
#define CTL_TTL_MAX 10000
#define CTL_ESTOP_WRITE_MS 50
#define CTL_SUMMARY_CAP 481 /* 120 UTF-8 characters */
#define CTL_CONFIRM_DEFAULT 30000
#define CTL_CONFIRM_MIN 1000
#define CTL_CONFIRM_MAX 120000
#define CTL_CONFIRM_GUARD 300

typedef struct ctl_s {
    int lfd; char path[108];
    int cfd[CTL_MAX_CLIENTS]; char in[CTL_MAX_CLIENTS][CTL_LINE_MAX]; size_t inlen[CTL_MAX_CLIENTS];
    ctl_screen_t screen;
    hid_t *hid;              /* NULL = no HID (acts fail "no_hid") */
    int allowed, estopped;
    uint32_t token, next_token, last_seq; int holder; int64_t expiry_ms; char owner[32];
    char stop_reason[32];
    struct { char state[20], input[4], summary[CTL_SUMMARY_CAP]; int progress, dirty; } ui;
    struct { int pending, client, shown; long cid; int64_t deadline_ms, shown_ms; char summary[CTL_SUMMARY_CAP], risk[5]; } confirm;
    struct { unsigned acquires, renews, releases, expiries, preempts, estops, acts, rejects, bad, clients, dropped, confirm_ignored; } st;
} ctl_t;

void ctl_init(ctl_t *c, hid_t *hid);
int ctl_listen(ctl_t *c, const char *path);   /* 0 ok */
void ctl_close(ctl_t *c);
/* Pure request handler (unit-tested): one JSON line from client ci -> reply (no newline). Returns reply length. */
int ctl_handle_line(ctl_t *c, int ci, const char *line, size_t n, int64_t now_ms, char *reply, size_t cap);
int ctl_ui_dirty(ctl_t *c); /* renderer consumes and clears the cached-state dirty bit */
void ctl_confirm_shown(ctl_t *c, int64_t now_ms);
void ctl_confirm_key(ctl_t *c, int ok, int64_t now_ms);
/* Shared bounded flat-JSON helpers for the control rule engine. */
int ctl_json_str(const char *s, size_t n, const char *key, char *out, size_t cap);
int ctl_json_int(const char *s, size_t n, const char *key, long *v);
int ctl_json_has(const char *s, size_t n, const char *key);
int ctl_json_quote(char *out, size_t cap, const char *s);
int ctl_text_ok(const char *s, size_t maxchars);
void ctl_notify(ctl_t *c, int ci, const char *line);
void ctl_tick(ctl_t *c, int64_t now_ms);                 /* lease, confirmation and grant expiry */
int ctl_timeout_ms(const ctl_t *c, int64_t now_ms, int dflt);   /* poll bounded by lease + confirmation deadlines */
int ctl_pollfds(const ctl_t *c, struct pollfd *pf, int max);
void ctl_on_poll(ctl_t *c, const struct pollfd *pf, int n, int64_t now_ms);
void ctl_estop(ctl_t *c, const char *reason, int64_t now_ms);
void ctl_human_input(ctl_t *c, int64_t now_ms);          /* physical key press: lease yields to the human */
void ctl_set_allowed(ctl_t *c, int on);               /* keyboard only (agent page); clears the e-stop latch */
void ctl_client_gone(ctl_t *c, int ci, int64_t now_ms);
#endif
