#define _GNU_SOURCE
#include "ctl.h"
#include "kbd.h"
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

/* Flat control objects only: bounded, strict JSON primitives; never match keys inside a string. */
static const char *ws(const char *p, const char *end) { while (p < end && strchr(" \t\r\n", *p)) p++; return p; }
static int hex4(const char *p, unsigned *v)
{
    *v = 0;
    for (int i = 0; i < 4; i++) {
        unsigned b = (unsigned char)p[i], d;
        if (b >= '0' && b <= '9') d = b - '0';
        else if (b >= 'a' && b <= 'f') d = b - 'a' + 10;
        else if (b >= 'A' && b <= 'F') d = b - 'A' + 10;
        else return -1;
        *v = *v * 16 + d;
    }
    return 0;
}
static const char *jstr(const char *p, const char *end, char *out, size_t cap)
{
    size_t n = 0;
    if (p == end || *p++ != '"') return NULL;
    while (p < end && *p != '"') {
        unsigned u = (unsigned char)*p++; char b[4]; size_t nb = 1;
        if (u < 32) return NULL;
        b[0] = (char)u;
        if (u == '\\') {
            if (p == end) return NULL;
            char e = *p++;
            const char *q = strchr("\"\\/bfnrt", e);
            if (q) b[0] = "\"\\/\b\f\n\r\t"[q - "\"\\/bfnrt"];
            else if (e == 'u') {
                if (end - p < 4 || hex4(p, &u)) return NULL;
                p += 4;
                if (u >= 0xd800 && u <= 0xdbff) {
                    unsigned lo;
                    if (end - p < 6 || p[0] != '\\' || p[1] != 'u' || hex4(p + 2, &lo) || lo < 0xdc00 || lo > 0xdfff) return NULL;
                    p += 6; u = 0x10000 + ((u - 0xd800) << 10) + lo - 0xdc00;
                } else if (u >= 0xdc00 && u <= 0xdfff) return NULL;
                if (!u) return NULL; /* C string API cannot represent embedded NUL */
                if (u < 0x80) b[0] = (char)u;
                else if (u < 0x800) { b[0] = (char)(0xc0 | (u >> 6)); b[1] = (char)(0x80 | (u & 63)); nb = 2; }
                else if (u < 0x10000) { b[0] = (char)(0xe0 | (u >> 12)); b[1] = (char)(0x80 | ((u >> 6) & 63)); b[2] = (char)(0x80 | (u & 63)); nb = 3; }
                else { b[0] = (char)(0xf0 | (u >> 18)); b[1] = (char)(0x80 | ((u >> 12) & 63)); b[2] = (char)(0x80 | ((u >> 6) & 63)); b[3] = (char)(0x80 | (u & 63)); nb = 4; }
            } else return NULL;
        }
        if (out) { if (n + nb >= cap) return NULL; memcpy(out + n, b, nb); }
        n += nb;
    }
    if (p == end) return NULL;
    if (out) out[n] = 0;
    return p + 1;
}
static const char *jval(const char *p, const char *end)
{
    if (p == end) return NULL;
    if (*p == '"') return jstr(p, end, NULL, 0);
    for (int i = 0; i < 3; i++) { const char *v = i == 0 ? "true" : i == 1 ? "false" : "null"; size_t n = strlen(v); if ((size_t)(end - p) >= n && !memcmp(p, v, n)) return p + n; }
    if (*p == '-') p++;
    if (p == end || *p < '0' || *p > '9') return NULL;
    if (*p == '0') p++;
    else while (p < end && *p >= '0' && *p <= '9') p++;
    if (p < end && *p == '.') { p++; const char *start = p; while (p < end && *p >= '0' && *p <= '9') p++; if (p == start) return NULL; }
    if (p < end && (*p == 'e' || *p == 'E')) { p++; if (p < end && (*p == '+' || *p == '-')) p++; const char *start = p; while (p < end && *p >= '0' && *p <= '9') p++; if (p == start) return NULL; }
    return p;
}
static const char *field(const char *s, size_t n, const char *key)
{
    const char *end = s + n, *p = ws(s, end), *found = NULL; char keys[24][32]; int nk = 0;
    if (p == end || *p++ != '{') return NULL;
    p = ws(p, end);
    if (p < end && *p == '}') return key ? NULL : (ws(p + 1, end) == end ? s : NULL);
    for (;;) {
        if (nk == 24 || !(p = jstr(p, end, keys[nk], sizeof keys[nk]))) return NULL;
        for (int i = 0; i < nk; i++) if (!strcmp(keys[i], keys[nk])) return NULL;
        p = ws(p, end); if (p == end || *p++ != ':') return NULL;
        p = ws(p, end); if (key && !strcmp(keys[nk], key)) found = p;
        nk++; p = jval(p, end); if (!p) return NULL;
        p = ws(p, end); if (p == end) return NULL;
        if (*p == '}') return ws(p + 1, end) == end ? (key ? found : s) : NULL;
        if (*p++ != ',') return NULL;
        p = ws(p, end);
    }
}
int ctl_json_str(const char *s, size_t n, const char *key, char *out, size_t cap)
{
    const char *p = field(s, n, key);
    return p && jstr(p, s + n, out, cap) ? (int)strlen(out) : -1;
}
int ctl_json_int(const char *s, size_t n, const char *key, long *v)
{
    const char *p = field(s, n, key); long x = 0; int neg = 0, nd = 0;
    if (!p) return -1;
    if (*p == '-') { neg = 1; p++; }
    while (p < s + n && *p >= '0' && *p <= '9') { if (++nd > 9) return -1; x = x * 10 + *p++ - '0'; }
    if (!nd || (p < s + n && !strchr(" ,}\t\r\n", *p))) return -1;
    *v = neg ? -x : x; return 0;
}
int ctl_json_has(const char *s, size_t n, const char *key) { return field(s, n, key) != NULL; }
int ctl_text_ok(const char *s, size_t maxchars)
{
    size_t count = 0;
    while (*s) {
        unsigned u = (unsigned char)*s++; int more = 0; unsigned min = 0;
        if (u < 0x80) { if (u < 32) return 0; }
        else if (u >= 0xc2 && u <= 0xdf) { more = 1; u &= 31; min = 0x80; }
        else if (u >= 0xe0 && u <= 0xef) { more = 2; u &= 15; min = 0x800; }
        else if (u >= 0xf0 && u <= 0xf4) { more = 3; u &= 7; min = 0x10000; }
        else return 0;
        for (int i = 0; i < more; i++) { unsigned b = (unsigned char)*s++; if ((b & 0xc0) != 0x80) return 0; u = (u << 6) | (b & 63); }
        if (u < min || u > 0x10ffff || (u >= 0xd800 && u <= 0xdfff) || ++count > maxchars) return 0;
    }
    return 1;
}
int ctl_json_quote(char *out, size_t cap, const char *s)
{
    size_t n = 0;
    if (cap < 3) return -1;
    out[n++] = '"';
    for (; *s; s++) { if (n + 3 >= cap || (unsigned char)*s < 32) return -1; if (*s == '"' || *s == '\\') out[n++] = '\\'; out[n++] = *s; }
    out[n++] = '"'; out[n] = 0; return (int)n;
}

static int64_t mono_ms(void *ctx)
{
    (void)ctx;
    struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return (int64_t)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}

void ctl_init(ctl_t *c, hid_t *hid)
{
    memset(c, 0, sizeof *c);
    strcpy(c->ui.state, "idle"); strcpy(c->ui.input, "hid");
    ctl_screen_init(c);
    c->lfd = -1; c->hid = hid; c->holder = -1; c->clock_ms = mono_ms;
    for (int i = 0; i < CTL_MAX_CLIENTS; i++) c->cfd[i] = -1;
    struct timespec t; clock_gettime(CLOCK_REALTIME, &t);
    c->next_token = (uint32_t)(((uint32_t)t.tv_nsec ^ ((uint32_t)t.tv_sec << 7) ^ getpid()) % 900000000u) + 1000u;
}

void ctl_notify(ctl_t *c, int ci, const char *line)
{
    if (ci < 0 || ci >= CTL_MAX_CLIENTS || c->cfd[ci] < 0) return;
    char b[CTL_LINE_MAX + 1]; int n = snprintf(b, sizeof b, "%s\n", line);
    if (n <= 0 || (size_t)n >= sizeof b) return;
    ssize_t w = send(c->cfd[ci], b, (size_t)n, MSG_NOSIGNAL | MSG_DONTWAIT);
    if (w != n) { c->st.dropped++; ctl_client_gone(c, ci, 0); }   /* not reading: drop */
}

/* revoke the lease (if any) and put every key / pointer button up. Bounded: never blocks > CTL_ESTOP_WRITE_MS. */
static void lease_revoke(ctl_t *c, const char *reason, int always_allup)
{
    int had = c->token != 0, holder = c->holder;
    c->token = 0; c->holder = -1; c->expiry_ms = 0; c->owner[0] = 0;
    if ((had || always_allup) && c->hid) { hid_release_all(c->hid, CTL_ESTOP_WRITE_MS); }
    if (had && holder >= 0) {
        char ev[96]; snprintf(ev, sizeof ev, "{\"ev\":\"lease_revoked\",\"reason\":\"%s\"}", reason);
        ctl_notify(c, holder, ev);
    }
}

static void confirm_result(ctl_t *c, int ok, const char *reason)
{
    if (!c->confirm.pending) return;
    int ci = c->confirm.client; long cid = c->confirm.cid;
    c->confirm.pending = 0; c->confirm.shown = 0;
    char ev[160]; snprintf(ev, sizeof ev, "{\"ev\":\"confirm.result\",\"cid\":%ld,\"ok\":%s,\"reason\":\"%s\"}", cid, ok ? "true" : "false", reason);
    ctl_notify(c, ci, ev);
}
int ctl_ui_dirty(ctl_t *c) { int dirty = c->ui.dirty; c->ui.dirty = 0; return dirty; }
void ctl_confirm_shown(ctl_t *c, int64_t now)
{
    if (c->confirm.pending && !c->confirm.shown) { c->confirm.shown = 1; c->confirm.shown_ms = now; }
}
void ctl_confirm_key(ctl_t *c, int ok, int64_t now)
{
    if (!c->confirm.pending) return;
    if (now >= c->confirm.deadline_ms) { confirm_result(c, 0, "timeout"); return; }
    if (!ok) { confirm_result(c, 0, "cancel"); return; }
    if (!c->confirm.shown || now - c->confirm.shown_ms < CTL_CONFIRM_GUARD) { c->st.confirm_ignored++; return; }
    confirm_result(c, 1, "confirmed");
}

void ctl_estop(ctl_t *c, const char *reason, int64_t now_ms)
{
    (void)now_ms;
    ctl_screen_reset(c);
    confirm_result(c, 0, "estop");
    lease_revoke(c, "estop", 1);
    c->estopped = 1; c->allowed = 0; c->st.estops++;
    snprintf(c->stop_reason, sizeof c->stop_reason, "%s", reason ? reason : "estop");
    char ev[96]; snprintf(ev, sizeof ev, "{\"ev\":\"estop\",\"reason\":\"%s\"}", c->stop_reason);
    for (int i = 0; i < CTL_MAX_CLIENTS; i++) ctl_notify(c, i, ev);
}

void ctl_human_input(ctl_t *c, int64_t now_ms)
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
    else { ctl_screen_reset(c); confirm_result(c, 0, "disabled"); lease_revoke(c, "disabled", 0); }
}

void ctl_tick(ctl_t *c, int64_t now_ms)
{
    if (c->screen.grant_until && now_ms >= c->screen.grant_until) c->screen.grant_until = 0;
    if (c->confirm.pending && now_ms >= c->confirm.deadline_ms) confirm_result(c, 0, "timeout");
    if (c->token && now_ms >= c->expiry_ms) { c->st.expiries++; lease_revoke(c, "expired", 0); }
}

int ctl_timeout_ms(const ctl_t *c, int64_t now_ms, int dflt)
{
    int64_t deadline = c->token ? c->expiry_ms : 0;
    if (c->confirm.pending && (!deadline || c->confirm.deadline_ms < deadline)) deadline = c->confirm.deadline_ms;
    if (!deadline) return dflt;
    int64_t left = deadline > now_ms ? deadline - now_ms : 0;
    return dflt < 0 || left < dflt ? (int)left : dflt;
}

static int err(char *r, size_t cap, long id, const char *e)
{
    int n = id >= 0 ? snprintf(r, cap, "{\"ok\":false,\"id\":%ld,\"err\":\"%s\"}", id, e) : snprintf(r, cap, "{\"ok\":false,\"err\":\"%s\"}", e);
    return n > 0 && (size_t)n < cap ? n : 0;
}

static const char *state(const ctl_t *c) { return c->estopped ? "stopped" : !c->allowed ? "disabled" : c->token ? "leased" : "idle"; }

static int lease_ok(ctl_t *c, int ci, const char *s, size_t n, int64_t now, const char **e)
{
    long tok;
    if (ctl_json_int(s, n, "token", &tok) || tok <= 0) { *e = "bad_token"; return 0; }
    if (c->estopped) { *e = "stopped"; return 0; }
    if (!c->token || (uint32_t)tok != c->token || c->holder != ci) { *e = "no_lease"; return 0; }
    if (now >= c->expiry_ms) { c->st.expiries++; lease_revoke(c, "expired", 0); *e = "no_lease"; return 0; }
    return 1;
}

static int act(ctl_t *c, const char *s, size_t n, const char **e)
{
    char kind[16];
    if (ctl_json_str(s, n, "kind", kind, sizeof kind) < 0) { *e = "bad_kind"; return -1; }
    if (!c->hid) { *e = "no_hid"; return -1; }
    if (!strcmp(kind, "key")) {
        long u, d;
        if (ctl_json_int(s, n, "usage", &u) || ctl_json_int(s, n, "down", &d)) { *e = "bad_args"; return -1; }
        if (u >= 0xE0 && u <= 0xE7) { hid_set_mod(c->hid, (uint8_t)(1u << (u - 0xE0)), d != 0); return 0; }
        if (u < 4 || u > 0x65) { *e = "bad_usage"; return -1; }
        if (hid_key(c->hid, (uint8_t)u, d != 0)) { *e = "rollover"; return -1; }
        return 0;
    }
    if (!strcmp(kind, "ptr")) {
        long b, x, y;
        if (ctl_json_int(s, n, "buttons", &b) || ctl_json_int(s, n, "x", &x) || ctl_json_int(s, n, "y", &y)) { *e = "bad_args"; return -1; }
        if (b < 0 || b > 7 || x < 0 || x > 32767 || y < 0 || y > 32767) { *e = "out_of_range"; return -1; }
        return hid_pointer_abs(c->hid, (uint8_t)b, (uint16_t)x, (uint16_t)y);
    }
    if (!strcmp(kind, "scroll")) {
        long w = 0, p = 0;
        int hw = ctl_json_int(s, n, "wheel", &w) == 0, hp = ctl_json_int(s, n, "pan", &p) == 0;
        if ((!hw && !hp) || (ctl_json_has(s, n, "wheel") && !hw) || (ctl_json_has(s, n, "pan") && !hp)) { *e = "bad_args"; return -1; }
        if (w < -127 || w > 127 || p < -127 || p > 127) { *e = "out_of_range"; return -1; }
        if (hid_scroll(c->hid, (int)w, (int)p)) { *e = "unsupported"; return -1; }
        return 0;
    }
    if (!strcmp(kind, "allup")) { hid_release_all(c->hid, CTL_ESTOP_WRITE_MS); return 0; }
    *e = "bad_kind"; return -1;
}

int ctl_handle_line(ctl_t *c, int ci, const char *s, size_t n, int64_t now, char *r, size_t cap)
{
    char op[24]; long id = -1; const char *e = NULL; int len;
    if (ci < 0 || ci >= CTL_MAX_CLIENTS || n >= CTL_LINE_MAX || memchr(s, 0, n) || !field(s, n, NULL)) return err(r, cap, -1, "bad_request");
    ctl_tick(c, now);
    if (ctl_json_has(s, n, "id") && (ctl_json_int(s, n, "id", &id) || id < 0)) { c->st.bad++; return err(r, cap, -1, "bad_request"); }
    if (ctl_json_str(s, n, "op", op, sizeof op) < 0) { c->st.bad++; return err(r, cap, id, "bad_request"); }
    if (!strcmp(op, "hello") || !strcmp(op, "status")) {
        char ui_summary[CTL_SUMMARY_CAP * 2 + 3];
        if (ctl_json_quote(ui_summary, sizeof ui_summary, c->ui.summary) < 0) return err(r, cap, id, "bad_args");
        len = snprintf(r, cap, "{\"ok\":true,\"id\":%ld,\"v\":1,\"state\":\"%s\",\"allowed\":%d,\"estopped\":%d,\"leased\":%d,"
                       "\"scroll\":%d,\"hid_open\":%d,\"ttl_left_ms\":%" PRId64 ",\"confirm\":{\"pending\":%s,\"cid\":%ld,\"shown\":%s,\"deadline_ms\":%" PRId64 ",\"ignored\":%u,\"risk\":\"%s\"},\"ui\":{\"state\":\"%s\",\"input\":\"%s\",\"summary\":%s,\"progress\":%d}}", id, state(c), c->allowed, c->estopped, c->token != 0,
                       c->hid && c->hid->ptr_len == HID_PTR_LEN_SCROLL, c->hid && c->hid->fd >= 0, c->token ? c->expiry_ms - now : INT64_C(0), c->confirm.pending ? "true" : "false", c->confirm.cid,
                       c->confirm.shown ? "true" : "false", c->confirm.deadline_ms, c->st.confirm_ignored, c->confirm.risk, c->ui.state, c->ui.input, ui_summary, c->ui.progress);
        return len > 0 && (size_t)len < cap ? len : 0;
    }
    if (!strncmp(op, "screen.", 7)) {
        len = ctl_screen_handle(c, ci, op, s, n, now, id, r, cap, &e);
        if (e) { c->st.rejects++; return err(r, cap, id, e); }
        return len;
    }
    if (!strcmp(op, "ui.agent")) {
        char st[20], input[4], summary[CTL_SUMMARY_CAP]; long progress;
        if (c->estopped) e = "stopped";
        else if (!c->allowed) e = "disabled";
        else if (ctl_json_str(s, n, "state", st, sizeof st) < 0 ||
                 ctl_json_str(s, n, "input", input, sizeof input) < 0 || (strcmp(input, "hid") && strcmp(input, "vnc")) ||
                 ctl_json_str(s, n, "summary", summary, sizeof summary) < 0 || !ctl_text_ok(summary, 120) ||
                 ctl_json_int(s, n, "progress", &progress) || progress < 0 || progress > 100) e = "bad_args";
        if (!e) {
            const char *states[] = {"idle", "observing", "planning", "acting", "awaiting_confirm", "done", "stopped", "error"}; int valid = 0;
            for (size_t i = 0; i < sizeof states / sizeof *states; i++) if (!strcmp(st, states[i])) valid = 1;
            if (!valid) e = "bad_args";
        }
        if (e) { c->st.rejects++; return err(r, cap, id, e); }
        if (strcmp(c->ui.state, st) || strcmp(c->ui.input, input) || strcmp(c->ui.summary, summary) || c->ui.progress != progress) c->ui.dirty = 1;
        strcpy(c->ui.state, st); strcpy(c->ui.input, input); strcpy(c->ui.summary, summary); c->ui.progress = (int)progress;
        len = snprintf(r, cap, "{\"ok\":true,\"id\":%ld}", id);
        return len > 0 && (size_t)len < cap ? len : 0;
    }
    if (!strcmp(op, "confirm.request")) {
        long cid, timeout = CTL_CONFIRM_DEFAULT; char summary[CTL_SUMMARY_CAP], risk[5];
        if (c->estopped) e = "stopped";
        else if (!c->allowed) e = "disabled";
        else if (c->confirm.pending) e = "busy";
        else if (ctl_json_int(s, n, "cid", &cid) || cid < 0 ||
                 ctl_json_str(s, n, "summary", summary, sizeof summary) < 0 || !ctl_text_ok(summary, 120) ||
                 ctl_json_str(s, n, "risk", risk, sizeof risk) < 0 || (strcmp(risk, "low") && strcmp(risk, "high")) ||
                 (ctl_json_has(s, n, "timeout_ms") && ctl_json_int(s, n, "timeout_ms", &timeout)) ||
                 timeout < CTL_CONFIRM_MIN || timeout > CTL_CONFIRM_MAX) e = "bad_args";
        if (e) { c->st.rejects++; return err(r, cap, id, e); }
        c->confirm.pending = 1; c->confirm.client = ci; c->confirm.cid = cid;
        c->confirm.shown = 0; c->confirm.deadline_ms = now + timeout;
        snprintf(c->confirm.summary, sizeof c->confirm.summary, "%s", summary);
        snprintf(c->confirm.risk, sizeof c->confirm.risk, "%s", risk);
        len = snprintf(r, cap, "{\"ok\":true,\"id\":%ld}", id);
        return len > 0 && (size_t)len < cap ? len : 0;
    }
    if (!strcmp(op, "estop")) {
        ctl_estop(c, "socket", now);
        len = snprintf(r, cap, "{\"ok\":true,\"id\":%ld,\"state\":\"stopped\"}", id);
        return len > 0 && (size_t)len < cap ? len : 0;
    }
    if (!strcmp(op, "lease.acquire")) {
        long ttl = CTL_TTL_DEFAULT; char owner[32] = "agent";
        int bad_ttl = ctl_json_has(s, n, "ttl_ms") && ctl_json_int(s, n, "ttl_ms", &ttl);
        int bad_owner = ctl_json_has(s, n, "owner") && (ctl_json_str(s, n, "owner", owner, sizeof owner) < 0 || !ctl_text_ok(owner, 31));
        if (c->estopped) e = "stopped";
        else if (!c->allowed) e = "disabled";
        else if (c->token && now < c->expiry_ms) e = "busy";
        else if (bad_ttl || ttl < CTL_TTL_MIN || ttl > CTL_TTL_MAX) e = "bad_ttl";
        else if (bad_owner) e = "bad_args";
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
            long ttl = CTL_TTL_DEFAULT;
            int bad_ttl = ctl_json_has(s, n, "ttl_ms") && ctl_json_int(s, n, "ttl_ms", &ttl);
            if (bad_ttl || ttl < CTL_TTL_MIN || ttl > CTL_TTL_MAX) { c->st.rejects++; return err(r, cap, id, "bad_ttl"); }
            c->expiry_ms = now + ttl; c->st.renews++;
            len = snprintf(r, cap, "{\"ok\":true,\"id\":%ld,\"ttl_ms\":%ld}", id, ttl);
        } else if (!strcmp(op, "lease.release")) {
            c->st.releases++; lease_revoke(c, "released", 0);
            len = snprintf(r, cap, "{\"ok\":true,\"id\":%ld}", id);
        } else {
            long seq;
            if (ctl_json_int(s, n, "seq", &seq) || seq <= (long)c->last_seq) { c->st.rejects++; return err(r, cap, id, "bad_seq"); }
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
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) return -1;
    if (fcntl(fd, F_SETFD, FD_CLOEXEC) || fcntl(fd, F_SETFL, O_NONBLOCK)) { close(fd); return -1; }
    struct stat st;
    if (lstat(path, &st) == 0) { if (!S_ISSOCK(st.st_mode)) { close(fd); errno = EEXIST; return -1; } unlink(path); }
    mode_t old = umask(077);
    int rc = bind(fd, (struct sockaddr *)&a, sizeof a);
    umask(old);
    if (rc || chmod(path, 0600) || listen(fd, 4)) { close(fd); return -1; }
    c->lfd = fd;
    return 0;
}

void ctl_client_gone(ctl_t *c, int ci, int64_t now_ms)
{
    (void)now_ms;
    if (ci < 0 || ci >= CTL_MAX_CLIENTS) return;
    c->client_generation[ci]++;
    if (c->cfd[ci] >= 0) close(c->cfd[ci]);
    c->cfd[ci] = -1; c->inlen[ci] = 0;
    ctl_screen_gone(c, ci);
    if (c->confirm.pending && c->confirm.client == ci) confirm_result(c, 0, "client_gone");
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
    if (fcntl(fd, F_SETFD, FD_CLOEXEC) || fcntl(fd, F_SETFL, O_NONBLOCK)) { close(fd); return; }
    uid_t uid;
#ifdef __linux__
    struct ucred cr; socklen_t l = sizeof cr;
    if (getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &cr, &l)) { close(fd); c->st.rejects++; return; }
    uid = cr.uid;
#else
    gid_t gid;
    if (getpeereid(fd, &uid, &gid)) { close(fd); c->st.rejects++; return; }
#endif
    if (uid != geteuid() && uid != 0) { close(fd); c->st.rejects++; return; }
    for (int i = 0; i < CTL_MAX_CLIENTS; i++) if (c->cfd[i] < 0) { c->cfd[i] = fd; c->inlen[i] = 0; c->st.clients++; return; }
    static const char busy[] = "{\"ok\":false,\"err\":\"too_many_clients\"}\n";
    if (send(fd, busy, sizeof busy - 1, MSG_NOSIGNAL | MSG_DONTWAIT) < 0) {}
    close(fd); c->st.rejects++;
}

/* Ticks may themselves wait for neutral HID output. Re-sample and check again if time advanced,
 * so a lease revoke crossing a K2/confirm/grant deadline cannot leave validation with stale time.
 * Never service queued HID here: safety must purge it before any implicit act flush. */
static int64_t dispatch_time(ctl_t *c)
{
    for (;;) {
        int64_t now = c->clock_ms(c->clock_ctx);
        if (c->safety_tick) c->safety_tick(c->safety_ctx, now);
        ctl_tick(c, now);
        if (c->clock_ms(c->clock_ctx) == now) return now;
    }
}

static void read_client(ctl_t *c, int ci)
{
    char b[512];
    ssize_t r = recv(c->cfd[ci], b, sizeof b, MSG_DONTWAIT);
    if (r == 0 || (r < 0 && errno != EAGAIN && errno != EINTR)) { ctl_client_gone(c, ci, c->clock_ms(c->clock_ctx)); return; }
    for (ssize_t i = 0; i < r && c->cfd[ci] >= 0; i++) {
        if (b[i] != '\n') {
            if (c->inlen[ci] + 1 >= CTL_LINE_MAX) { c->st.bad++; ctl_client_gone(c, ci, c->clock_ms(c->clock_ctx)); return; }   /* overlong: drop */
            c->in[ci][c->inlen[ci]++] = b[i]; continue;
        }
        char rep[CTL_LINE_MAX];
        int64_t now = dispatch_time(c);
        if (c->cfd[ci] < 0) return; /* a safety notification may have dropped this client */
        int n = ctl_handle_line(c, ci, c->in[ci], c->inlen[ci], now, rep, sizeof rep);
        c->inlen[ci] = 0;
        dispatch_time(c);   /* includes the last request in a batch: allup/release may have waited */
        if (n > 0) { rep[n] = 0; ctl_notify(c, ci, rep); }
    }
}

void ctl_on_poll(ctl_t *c, const struct pollfd *pf, int n)
{
    for (int k = 0; k < n; k++) {
        if (!(pf[k].revents & (POLLIN | POLLERR | POLLHUP))) continue;
        if (pf[k].fd == c->lfd) { accept_one(c); continue; }
        for (int i = 0; i < CTL_MAX_CLIENTS; i++) if (c->cfd[i] == pf[k].fd) { read_client(c, i); break; }
    }
}
