#include "ctl.h"
#include <stdio.h>
#include <string.h>

void ctl_screen_init(ctl_t *c)
{
    c->screen.next_id = 3;
    for (int i = 0; i < 3; i++) { ctl_page_t *p = &c->screen.page[i]; p->used = 1; p->id = i; p->client = -1; strcpy(p->name, i == 0 ? "system" : i == 1 ? "agent control" : "user"); }
}
void ctl_screen_reset(ctl_t *c)
{
    c->screen.current = CTL_SCREEN_SYSTEM; c->screen.grant_until = 0;
    for (int i = 0; i < CTL_SCREEN_PAGES; i++) { c->screen.page[i].bound = 0; memset(c->screen.page[i].action, 0, sizeof c->screen.page[i].action); }
}
void ctl_screen_gone(ctl_t *c, int ci)
{
    for (int i = 3; i < CTL_SCREEN_PAGES; i++) if (c->screen.page[i].used && c->screen.page[i].client == ci) {
        if (c->screen.current == c->screen.page[i].id) c->screen.current = CTL_SCREEN_SYSTEM;
        memset(&c->screen.page[i], 0, sizeof c->screen.page[i]);
    }
}
void ctl_screen_grant(ctl_t *c, long ttl, int64_t now)
{
    c->screen.grant_until = c->allowed && !c->estopped && ttl > 0 && ttl <= CTL_SCREEN_GRANT_MAX ? now + ttl : 0;
}
static ctl_page_t *page(ctl_t *c, long id)
{
    for (int i = 0; i < CTL_SCREEN_PAGES; i++) if (c->screen.page[i].used && c->screen.page[i].id == id) return &c->screen.page[i];
    return NULL;
}
void ctl_key_event(ctl_t *c, int row, int col, int pressed, int64_t now)
{
    if (row < 0 || row >= 4 || col < 0 || col >= 5) return;
    ctl_page_t *p = page(c, c->screen.current);
    if (!p) return;
    if (pressed && row == CTL_SCREEN_ESTOP_ROW && col == CTL_SCREEN_ESTOP_COL && (p->id == CTL_SCREEN_CONTROL || p->id >= 3)) { ctl_estop(c, "page_key", now); return; }
    if (!c->allowed || c->estopped || p->id < 3 || p->client < 0 || !(p->bound & (1u << (row * 5 + col)))) return;
    char ev[128]; snprintf(ev, sizeof ev, "{\"ev\":\"key\",\"page\":%d,\"row\":%d,\"col\":%d,\"down\":%s}", p->id, row, col, pressed ? "true" : "false");
    ctl_notify(c, p->client, ev);
}
static int rate(ctl_t *c, int fb, int64_t now)
{
    int n = 0, per = 0;
    for (int i = 0; i < c->screen.nwrites; i++) if (now - c->screen.writes[i].at < 1000) {
        c->screen.writes[n++] = c->screen.writes[i];
        if (c->screen.writes[i].fb == fb) per++;
    }
    c->screen.nwrites = n;
    if (n == CTL_SCREEN_RATE || per == CTL_SCREEN_FB_RATE) return -1;
    c->screen.writes[n].at = now; c->screen.writes[n].fb = fb; c->screen.nwrites++;
    return 0;
}
static int layout(ctl_t *c, long id, char *r, size_t cap)
{
    size_t at = 0; int k;
#define ADD(...) do { k = snprintf(r + at, cap > at ? cap - at : 0, __VA_ARGS__); if (k < 0 || (size_t)k >= cap - at) return 0; at += (size_t)k; } while (0)
    ADD("{\"ok\":true,\"id\":%ld,\"current_page\":%d,\"control_page\":1,\"estop_key\":{\"row\":3,\"col\":4},\"screen_fields\":[\"fb\",\"w\",\"h\",\"bus\",\"row\",\"col\"],\"screens\":[", id, c->screen.current);
    for (int fb = 1; fb <= 21; fb++) {
        ADD("%s[%d,%d,%d,%d", fb == 1 ? "" : ",", fb, fb == 21 ? 428 : 128, fb == 21 ? 142 : 128, fb >= 10 && fb <= 20);
        if (fb <= 20) ADD(",%d,%d]", (fb - 1) / 5, (fb - 1) % 5);
        else ADD(",null,null]");
    }
    ADD("],\"page_fields\":[\"page_id\",\"owner\"],\"pages\":["); int first = 1;
    for (int i = 0; i < CTL_SCREEN_PAGES; i++) if (c->screen.page[i].used) {
        ctl_page_t *p = &c->screen.page[i];
        ADD("%s[%d,\"%s\"]", first ? "" : ",", p->id, i < 2 ? "system" : i == 2 ? "user" : "agent"); first = 0;
    }
    ADD("]}");
#undef ADD
    return (int)at;
}
int ctl_screen_handle(ctl_t *c, int ci, const char *op, const char *s, size_t n,
                      int64_t now, long id, char *r, size_t cap, const char **e)
{
    int len; long pid;
#define FAIL(reason) do { *e = reason; return 0; } while (0)
    if (c->estopped) FAIL("stopped");
    if (!c->allowed) FAIL("disabled");
    if (!strcmp(op, "screen.layout")) return layout(c, id, r, cap);
    if (!strcmp(op, "screen.page.create")) {
        char name[129];
        if (ctl_json_str(s, n, "name", name, sizeof name) <= 0 || !ctl_text_ok(name, 32)) FAIL("bad_args");
        int i; for (i = 3; i < CTL_SCREEN_PAGES && c->screen.page[i].used; i++) {}
        if (i == CTL_SCREEN_PAGES || c->screen.next_id >= 999999999) FAIL("busy");
        ctl_page_t *p = &c->screen.page[i]; memset(p, 0, sizeof *p); p->used = 1; p->id = c->screen.next_id++; p->client = ci; strcpy(p->name, name);
        len = snprintf(r, cap, "{\"ok\":true,\"id\":%ld,\"page_id\":%d}", id, p->id);
        return len > 0 && (size_t)len < cap ? len : 0;
    }
    if (strcmp(op, "screen.page.delete") && strcmp(op, "screen.page.show") && strcmp(op, "screen.draw") && strcmp(op, "screen.text") && strcmp(op, "screen.bind")) FAIL("unknown_op");
    if (ctl_json_int(s, n, "page_id", &pid)) FAIL("bad_args");
    ctl_page_t *p = page(c, pid); if (!p) FAIL("not_found");
    if (!strcmp(op, "screen.page.show")) {
        if (pid != CTL_SCREEN_CONTROL && (pid < 3 || p->client != ci)) FAIL("forbidden");
        c->screen.current = (int)pid;
    } else {
        if (pid == CTL_SCREEN_CONTROL) FAIL("reserved");
        if ((pid >= 3 && p->client != ci) || (pid < 3 && now >= c->screen.grant_until)) FAIL("forbidden");
        if (!strcmp(op, "screen.page.delete")) {
            if (pid < 3) FAIL("forbidden"); /* builtin pages never removed, even with a grant */
            memset(p, 0, sizeof *p); if (c->screen.current == pid) c->screen.current = CTL_SCREEN_SYSTEM;
        } else if (!strcmp(op, "screen.bind")) {
            long row, col; char action[481];
            if (ctl_json_int(s, n, "row", &row) || ctl_json_int(s, n, "col", &col) || row < 0 || row > 3 || col < 0 || col > 4 ||
                ctl_json_str(s, n, "action", action, sizeof action) <= 0 || !ctl_text_ok(action, 120)) FAIL("bad_args");
            if (row == CTL_SCREEN_ESTOP_ROW && col == CTL_SCREEN_ESTOP_COL) FAIL("reserved");
            int key = (int)(row * 5 + col); p->bound |= 1u << key; strcpy(p->action[key], action);
        } else {
            long fb;
            if (ctl_json_int(s, n, "fb", &fb) || fb < 1 || fb > 21) FAIL("bad_args");
            if (fb == CTL_SCREEN_ESTOP_ROW * 5 + CTL_SCREEN_ESTOP_COL + 1) FAIL("reserved");
            if (!strcmp(op, "screen.draw")) {
                long x, y, w, h, bytes; char fmt[8]; int fw = fb == 21 ? 428 : 128, fh = fb == 21 ? 142 : 128;
                if (ctl_json_int(s, n, "x", &x) || ctl_json_int(s, n, "y", &y) || ctl_json_int(s, n, "w", &w) || ctl_json_int(s, n, "h", &h) ||
                    x < 0 || y < 0 || w < 1 || h < 1 || w > fw || h > fh || x > fw - w || y > fh - h ||
                    ctl_json_str(s, n, "fmt", fmt, sizeof fmt) < 0 || (strcmp(fmt, "rgb565") && strcmp(fmt, "jpeg")) ||
                    ctl_json_int(s, n, "bytes", &bytes) || bytes < 1 || bytes > CTL_SCREEN_BYTES_MAX || (!strcmp(fmt, "rgb565") && bytes != w * h * 2)) FAIL("bad_args");
            } else {
                long size, fg, bg; char text[481];
                if (ctl_json_str(s, n, "text", text, sizeof text) < 0 || !ctl_text_ok(text, 120) || ctl_json_int(s, n, "size", &size) || size < 1 || size > 128 ||
                    ctl_json_int(s, n, "fg", &fg) || fg < 0 || fg > 65535 || ctl_json_int(s, n, "bg", &bg) || bg < 0 || bg > 65535) FAIL("bad_args");
            }
            if (rate(c, (int)fb, now)) FAIL("rate_limited");
            len = snprintf(r, cap, "{\"ok\":true,\"id\":%ld,\"rendered\":false}", id);
            return len > 0 && (size_t)len < cap ? len : 0;
        }
    }
    len = snprintf(r, cap, "{\"ok\":true,\"id\":%ld}", id);
    return len > 0 && (size_t)len < cap ? len : 0;
#undef FAIL
}
