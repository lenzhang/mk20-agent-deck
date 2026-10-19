#define _GNU_SOURCE
#include "ctl.h"
#include "kbd.h"
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

void ctl_init(ctl_t *c, hid_t *hid)
{
    memset(c, 0, sizeof *c);
    c->lfd = -1; c->hid = hid; c->holder = -1;
    for (int i = 0; i < CTL_MAX_CLIENTS; i++) c->cfd[i] = -1;
    struct timespec t; clock_gettime(CLOCK_REALTIME, &t);
    c->next_token = (uint32_t)((t.tv_nsec ^ (t.tv_sec << 7) ^ getpid()) % 900000000u) + 1000u;
}

static void notify(ctl_t *c, int ci, const char *line)
{
    if (ci < 0 || ci >= CTL_MAX_CLIENTS || c->cfd[ci] < 0) return;
    char b[256]; int n = snprintf(b, sizeof b, "%s\n", line);
    if (n <= 0 || (size_t)n >= sizeof b) return;
    ssize_t w = send(c->cfd[ci], b, (size_t)n, MSG_NOSIGNAL | MSG_DONTWAIT);
    if (w != n) { close(c->cfd[ci]); c->cfd[ci] = -1; c->inlen[ci] = 0; c->st.dropped++; }   /* not reading: drop */
}

/* revoke the lease (if any) and put every key / pointer button up. Bounded: never blocks > CTL_ESTOP_WRITE_MS. */
static void lease_revoke(ctl_t *c, const char *reason, int always_allup)
{
    int had = c->token != 0, holder = c->holder;
    c->token = 0; c->holder = -1; c->expiry_ms = 0; c->owner[0] = 0;
    if ((had || always_allup) && c->hid) { hid_release_all(c->hid, CTL_ESTOP_WRITE_MS); hid_pointer_release(c->hid); }
    if (had && holder >= 0) {
        char ev[96]; snprintf(ev, sizeof ev, "{\"ev\":\"lease_revoked\",\"reason\":\"%s\"}", reason);
        notify(c, holder, ev);
    }
}

void ctl_estop(ctl_t *c, const char *reason, long now_ms)
{
    (void)now_ms;
    lease_revoke(c, "estop", 1);
    c->estopped = 1; c->allowed = 0; c->st.estops++;
    snprintf(c->stop_reason, sizeof c->stop_reason, "%s", reason ? reason : "estop");
    char ev[96]; snprintf(ev, sizeof ev, "{\"ev\":\"estop\",\"reason\":\"%s\"}", c->stop_reason);
    for (int i = 0; i < CTL_MAX_CLIENTS; i++) notify(c, i, ev);
}

void ctl_human_input(ctl_t *c, long now_ms)
{
    (void)now_ms;
    if (!c->token) return;
    c->st.preempts++;
    lease_revoke(c, "human", 0);
}

void ctl_set_allowed(ctl_t *c, int on)
{
    c->allowed = on ? 1 : 0;
    if (on) { c->estopped = 0; c->stop_reason[0] = 0; }
    else lease_revoke(c, "disabled", 0);
}

void ctl_tick(ctl_t *c, long now_ms)
{
    if (c->token && now_ms >= c->expiry_ms) { c->st.expiries++; lease_revoke(c, "expired", 0); }
}

int ctl_timeout_ms(const ctl_t *c, long now_ms, int dflt)
{
    if (!c->token) return dflt;
    long left = c->expiry_ms - now_ms;
    if (left < 0) left = 0;
    return left < dflt ? (int)left : dflt;
}

static int err(char *r, size_t cap, long id, const char *e)
{
    int n = id >= 0 ? snprintf(r, cap, "{\"ok\":false,\"id\":%ld,\"err\":\"%s\"}", id, e) : snprintf(r, cap, "{\"ok\":false,\"err\":\"%s\"}", e);
    return n > 0 && (size_t)n < cap ? n : 0;
}

static const char *state(const ctl_t *c) { return c->estopped ? "stopped" : !c->allowed ? "disabled" : c->token ? "leased" : "idle"; }

static int lease_ok(ctl_t *c, int ci, const char *s, size_t n, long now, const char **e)
{
    long tok;
    if (kbd_json_int(s, n, "token", &tok) || tok <= 0) { *e = "bad_token"; return 0; }
    if (c->estopped) { *e = "stopped"; return 0; }
    if (!c->token || (uint32_t)tok != c->token || c->holder != ci) { *e = "no_lease"; return 0; }
    if (now >= c->expiry_ms) { c->st.expiries++; lease_revoke(c, "expired", 0); *e = "no_lease"; return 0; }
    return 1;
}

static int act(ctl_t *c, const char *s, size_t n, const char **e)
{
    char kind[16];
    if (kbd_json_str(s, n, "kind", kind, sizeof kind) < 0) { *e = "bad_kind"; return -1; }
    if (!c->hid) { *e = "no_hid"; return -1; }
    if (!strcmp(kind, "key")) {
        long u, d;
        if (kbd_json_int(s, n, "usage", &u) || kbd_json_int(s, n, "down", &d)) { *e = "bad_args"; return -1; }
        if (u >= 0xE0 && u <= 0xE7) { hid_set_mod(c->hid, (uint8_t)(1u << (u - 0xE0)), d != 0); return 0; }
        if (u < 4 || u > 0x65) { *e = "bad_usage"; return -1; }
        if (hid_key(c->hid, (uint8_t)u, d != 0)) { *e = "rollover"; return -1; }
        return 0;
    }
    if (!strcmp(kind, "ptr")) {
        long b, x, y;
        if (kbd_json_int(s, n, "buttons", &b) || kbd_json_int(s, n, "x", &x) || kbd_json_int(s, n, "y", &y)) { *e = "bad_args"; return -1; }
        if (b < 0 || b > 7 || x < 0 || x > 32767 || y < 0 || y > 32767) { *e = "out_of_range"; return -1; }
        return hid_pointer_abs(c->hid, (uint8_t)b, (uint16_t)x, (uint16_t)y);
    }
    if (!strcmp(kind, "scroll")) {
        long w = 0, p = 0;
        int hw = kbd_json_int(s, n, "wheel", &w) == 0, hp = kbd_json_int(s, n, "pan", &p) == 0;
        if (!hw && !hp) { *e = "bad_args"; return -1; }
        if (w < -127 || w > 127 || p < -127 || p > 127) { *e = "out_of_range"; return -1; }
        if (hid_scroll(c->hid, (int)w, (int)p)) { *e = "unsupported"; return -1; }
        return 0;
    }
    if (!strcmp(kind, "allup")) { hid_release_all(c->hid, CTL_ESTOP_WRITE_MS); hid_pointer_release(c->hid); return 0; }
    *e = "bad_kind"; return -1;
}

int ctl_handle_line(ctl_t *c, int ci, const char *s, size_t n, long now, char *r, size_t cap)
{
    char op[24]; long id = -1; const char *e = NULL; int len;
    kbd_json_int(s, n, "id", &id);
    if (kbd_json_str(s, n, "op", op, sizeof op) < 0) { c->st.bad++; return err(r, cap, id, "bad_request"); }
    if (!strcmp(op, "hello") || !strcmp(op, "status")) {
        len = snprintf(r, cap, "{\"ok\":true,\"id\":%ld,\"v\":1,\"state\":\"%s\",\"allowed\":%d,\"estopped\":%d,\"leased\":%d,"
                       "\"scroll\":%d,\"hid_open\":%d,\"ttl_left_ms\":%ld}", id, state(c), c->allowed, c->estopped, c->token != 0,
                       c->hid && c->hid->ptr_len == HID_PTR_LEN_SCROLL, c->hid && c->hid->fd >= 0, c->token ? c->expiry_ms - now : 0L);
        return len > 0 && (size_t)len < cap ? len : 0;
    }
    if (!strcmp(op, "estop")) {
        ctl_estop(c, "socket", now);
        len = snprintf(r, cap, "{\"ok\":true,\"id\":%ld,\"state\":\"stopped\"}", id);
        return len > 0 && (size_t)len < cap ? len : 0;
    }
    if (!strcmp(op, "lease.acquire")) {
        long ttl = CTL_TTL_DEFAULT; char owner[32] = "agent";
        kbd_json_int(s, n, "ttl_ms", &ttl); kbd_json_str(s, n, "owner", owner, sizeof owner);
        if (c->estopped) e = "stopped";
        else if (!c->allowed) e = "disabled";
        else if (c->token && now < c->expiry_ms) e = "busy";
        else if (ttl < CTL_TTL_MIN || ttl > CTL_TTL_MAX) e = "bad_ttl";
        if (e) { c->st.rejects++; return err(r, cap, id, e); }
        if (c->token) { c->st.expiries++; lease_revoke(c, "expired", 0); }
        c->token = c->next_token; c->next_token = c->next_token % 999000000u + 7919u;
        c->holder = ci; c->expiry_ms = now + ttl; c->last_seq = 0; c->st.acquires++;
        snprintf(c->owner, sizeof c->owner, "%s", owner);
        len = snprintf(r, cap, "{\"ok\":true,\"id\":%ld,\"token\":%u,\"ttl_ms\":%ld}", id, c->token, ttl);
        return len > 0 && (size_t)len < cap ? len : 0;
    }
    if (!strcmp(op, "lease.renew") || !strcmp(op, "lease.release") || !strcmp(op, "act")) {
        if (!lease_ok(c, ci, s, n, now, &e)) { c->st.rejects++; return err(r, cap, id, e); }
        if (!strcmp(op, "lease.renew")) {
            long ttl = CTL_TTL_DEFAULT; kbd_json_int(s, n, "ttl_ms", &ttl);
            if (ttl < CTL_TTL_MIN || ttl > CTL_TTL_MAX) { c->st.rejects++; return err(r, cap, id, "bad_ttl"); }
            c->expiry_ms = now + ttl; c->st.renews++;
            len = snprintf(r, cap, "{\"ok\":true,\"id\":%ld,\"ttl_ms\":%ld}", id, ttl);
        } else if (!strcmp(op, "lease.release")) {
            c->st.releases++; lease_revoke(c, "released", 0);
            len = snprintf(r, cap, "{\"ok\":true,\"id\":%ld}", id);
        } else {
            long seq;
            if (kbd_json_int(s, n, "seq", &seq) || seq <= (long)c->last_seq) { c->st.rejects++; return err(r, cap, id, "bad_seq"); }
            if (act(c, s, n, &e)) { c->st.rejects++; return err(r, cap, id, e); }
            c->last_seq = (uint32_t)seq; c->st.acts++;
            len = snprintf(r, cap, "{\"ok\":true,\"id\":%ld,\"seq\":%ld}", id, seq);
        }
        return len > 0 && (size_t)len < cap ? len : 0;
    }
    c->st.bad++;
    return err(r, cap, id, "unknown_op");
}

/* ------------------------------------------------------------------ socket plumbing */
int ctl_listen(ctl_t *c, const char *path)
{
    struct sockaddr_un a; memset(&a, 0, sizeof a); a.sun_family = AF_UNIX;
    if (!path || strlen(path) >= sizeof a.sun_path) return -1;
    snprintf(a.sun_path, sizeof a.sun_path, "%s", path); snprintf(c->path, sizeof c->path, "%s", path);
    int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
    if (fd < 0) return -1;
    struct stat st;
    if (lstat(path, &st) == 0) { if (!S_ISSOCK(st.st_mode)) { close(fd); errno = EEXIST; return -1; } unlink(path); }
    mode_t old = umask(077);
    int rc = bind(fd, (struct sockaddr *)&a, sizeof a);
    umask(old);
    if (rc || chmod(path, 0600) || listen(fd, 4)) { close(fd); return -1; }
    c->lfd = fd;
    return 0;
}

void ctl_client_gone(ctl_t *c, int ci, long now_ms)
{
    (void)now_ms;
    if (c->cfd[ci] >= 0) close(c->cfd[ci]);
    c->cfd[ci] = -1; c->inlen[ci] = 0;
    if (c->token && c->holder == ci) lease_revoke(c, "client_gone", 0);
}

void ctl_close(ctl_t *c)
{
    for (int i = 0; i < CTL_MAX_CLIENTS; i++) if (c->cfd[i] >= 0) ctl_client_gone(c, i, 0);
    if (c->lfd >= 0) { close(c->lfd); c->lfd = -1; unlink(c->path); }
}

int ctl_pollfds(const ctl_t *c, struct pollfd *pf, int max)
{
    int n = 0;
    if (c->lfd >= 0 && n < max) pf[n++] = (struct pollfd){c->lfd, POLLIN, 0};
    for (int i = 0; i < CTL_MAX_CLIENTS && n < max; i++) if (c->cfd[i] >= 0) pf[n++] = (struct pollfd){c->cfd[i], POLLIN, 0};
    return n;
}

static void accept_one(ctl_t *c)
{
    int fd = accept(c->lfd, NULL, NULL);
    if (fd < 0) return;
    fcntl(fd, F_SETFD, FD_CLOEXEC); fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) | O_NONBLOCK);
    struct ucred cr; socklen_t l = sizeof cr;
    if (getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &cr, &l) || (cr.uid != geteuid() && cr.uid != 0)) { close(fd); c->st.rejects++; return; }
    for (int i = 0; i < CTL_MAX_CLIENTS; i++) if (c->cfd[i] < 0) { c->cfd[i] = fd; c->inlen[i] = 0; c->st.clients++; return; }
    static const char busy[] = "{\"ok\":false,\"err\":\"too_many_clients\"}\n";
    if (send(fd, busy, sizeof busy - 1, MSG_NOSIGNAL | MSG_DONTWAIT) < 0) {}
    close(fd); c->st.rejects++;
}

static void read_client(ctl_t *c, int ci, long now)
{
    char b[512];
    ssize_t r = recv(c->cfd[ci], b, sizeof b, MSG_DONTWAIT);
    if (r == 0 || (r < 0 && errno != EAGAIN && errno != EINTR)) { ctl_client_gone(c, ci, now); return; }
    for (ssize_t i = 0; i < r && c->cfd[ci] >= 0; i++) {
        if (b[i] != '\n') {
            if (c->inlen[ci] + 1 >= CTL_LINE_MAX) { c->st.bad++; ctl_client_gone(c, ci, now); return; }   /* overlong: drop */
            c->in[ci][c->inlen[ci]++] = b[i]; continue;
        }
        char rep[512];
        int n = ctl_handle_line(c, ci, c->in[ci], c->inlen[ci], now, rep, sizeof rep);
        c->inlen[ci] = 0;
        if (n > 0) { rep[n] = 0; notify(c, ci, rep); }
    }
}

void ctl_on_poll(ctl_t *c, const struct pollfd *pf, int n, long now)
{
    for (int k = 0; k < n; k++) {
        if (!(pf[k].revents & (POLLIN | POLLERR | POLLHUP))) continue;
        if (pf[k].fd == c->lfd) { accept_one(c); continue; }
        for (int i = 0; i < CTL_MAX_CLIENTS; i++) if (c->cfd[i] == pf[k].fd) { read_client(c, i, now); break; }
    }
}
