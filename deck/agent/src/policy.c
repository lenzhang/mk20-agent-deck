#define _POSIX_C_SOURCE 200809L
#include "policy.h"
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
static const char *names[] = {"click", "double_click", "move", "drag", "scroll", "type",
    "paste", "key", "wait", "done", "ask_user"};
static const cJSON *get(const cJSON *j, const char *k) { return cJSON_GetObjectItemCaseSensitive(j, k); }
static unsigned char lower(unsigned char c) { return c >= 'A' && c <= 'Z' ? c + 32 : c; }
static int contains(const char *s, const char *word)
{
    if (!s || !word || !*word) return 0;
    for (; *s; s++) {
        size_t n = 0;
        while (s[n] && word[n] && lower((unsigned char)s[n]) == lower((unsigned char)word[n])) n++;
        if (!word[n]) return 1;
    }
    return 0;
}
const char *action_name(action_kind_t k) { return k >= 0 && k < A_COUNT ? names[k] : "invalid"; }
uint64_t agent_hash(const void *v, size_t n)
{
    const unsigned char *s = v; uint64_t h = UINT64_C(14695981039346656037);
    for (size_t i = 0; i < n; i++) { h ^= s[i]; h *= UINT64_C(1099511628211); }
    return h;
}
int action_key(const char *combo, unsigned *mods, unsigned *usage)
{
    if (!combo || !*combo || strlen(combo) >= 96) return -1;
    char b[96]; size_t n = strlen(combo);
    for (size_t i = 0; i <= n; i++) b[i] = (char)lower((unsigned char)combo[i]);
    *mods = 0; *usage = 0; char *p = b;
    for (;;) {
        char *plus = strchr(p, '+');
        if (!plus) break;
        *plus = 0; unsigned m = !strcmp(p, "ctrl") || !strcmp(p, "control") ? 1 :
            !strcmp(p, "shift") ? 2 : !strcmp(p, "opt") || !strcmp(p, "alt") || !strcmp(p, "option") ? 4 :
            !strcmp(p, "cmd") || !strcmp(p, "meta") || !strcmp(p, "command") ? 8 : 0;
        if (!m || (*mods & m)) return -1;
        *mods |= m; p = plus + 1;
    }
    if (p[0] && !p[1]) {
        if (*p >= 'a' && *p <= 'z') *usage = (unsigned)(*p - 'a' + 4);
        else if (*p >= '1' && *p <= '9') *usage = (unsigned)(*p - '1' + 30);
        else if (*p == '0') *usage = 39;
    }
    static const struct { const char *name; unsigned usage; } keys[] = {
        {"return",40},{"enter",40},{"esc",41},{"escape",41},{"delete",42},{"backspace",42},
        {"tab",43},{"space",44},{"minus",45},{"equal",46},{"leftbracket",47},{"rightbracket",48},
        {"backslash",49},{"semicolon",51},{"quote",52},{"grave",53},{"comma",54},{"period",55},
        {"slash",56},{"capslock",57},{"home",74},{"pageup",75},{"forwarddelete",76},
        {"end",77},{"pagedown",78},{"right",79},{"left",80},{"down",81},{"up",82}};
    for (size_t i = 0; i < sizeof keys / sizeof *keys; i++) if (!strcmp(p, keys[i].name)) *usage = keys[i].usage;
    if (*p == 'f') {
        char *end; long f = strtol(p + 1, &end, 10);
        if (!*end && f >= 1 && f <= 12) *usage = (unsigned)f + 57;
    }
    return *usage ? 0 : -1;
}
static int copy(const cJSON *v, char *out, size_t cap, int required)
{
    if (!v && !required) { *out = 0; return 0; }
    if (!cJSON_IsString(v) || strlen(v->valuestring) >= cap) return -1;
    strcpy(out, v->valuestring); return 0;
}
static int number(const cJSON *j, const char *key, int low, int high, int *out, int required)
{
    const cJSON *v = get(j, key);
    if (!v && !required) return 0;
    if (!cJSON_IsNumber(v) || !isfinite(v->valuedouble) || floor(v->valuedouble) != v->valuedouble ||
        v->valuedouble < low || v->valuedouble > high) return -1;
    *out = (int)v->valuedouble; return 0;
}
static int utf8_chars(const char *s)
{
    int chars = 0; const unsigned char *p = (const unsigned char *)s;
    while (*p) {
        unsigned u = *p++, min = 0; int more = 0;
        if (u < 128) { chars++; continue; }
        if (u >= 0xc2 && u <= 0xdf) { more = 1; min = 0x80; u &= 31; }
        else if (u >= 0xe0 && u <= 0xef) { more = 2; min = 0x800; u &= 15; }
        else if (u >= 0xf0 && u <= 0xf4) { more = 3; min = 0x10000; u &= 7; }
        else return -1;
        for (int i = 0; i < more; i++) { if ((*p & 0xc0) != 0x80) return -1; u = (u << 6) | (*p++ & 63); }
        if (u < min || u > 0x10ffff || (u >= 0xd800 && u <= 0xdfff)) return -1;
        chars++;
    }
    return chars;
}
int action_validate(const action_t *a)
{
    if (!a || a->kind < 0 || a->kind >= A_COUNT || (a->high != 0 && a->high != 1) ||
        !memchr(a->text,0,sizeof a->text) || !memchr(a->combo,0,sizeof a->combo) || !memchr(a->target,0,sizeof a->target) ||
        utf8_chars(a->target) < 0) return -1;
    for (const unsigned char *p = (const unsigned char *)a->target; *p; p++) if (*p < 32 || *p == 127) return -1;
    if (a->kind <= A_SCROLL && (a->x1 < 0 || a->y1 < 0 || a->x1 > 1000000 || a->y1 > 1000000)) return -1;
    if (a->kind == A_DRAG && (a->x2 < 0 || a->y2 < 0 || a->x2 > 1000000 || a->y2 > 1000000)) return -1;
    if (a->kind == A_SCROLL && (a->dx < -127 || a->dx > 127 || a->dy < -127 || a->dy > 127)) return -1;
    if (a->kind == A_TYPE || a->kind == A_PASTE) {
        int n = utf8_chars(a->text); if (n < 0 || n > 256) return -1;
        if (a->kind == A_TYPE) for (const unsigned char *p = (const unsigned char *)a->text; *p; p++)
            if (*p > 126 || (*p < 32 && *p != '\n' && *p != '\t' && *p != '\r')) return -1;
    }
    if (a->kind == A_KEY) { unsigned m,u; if (action_key(a->combo,&m,&u) || m != a->modifiers || u != a->usage) return -1; }
    if (a->kind == A_WAIT && (a->ms < 0 || a->ms > 5000)) return -1;
    return 0;
}
int action_parse(const cJSON *j, action_t *a)
{
    memset(a, 0, sizeof *a); a->kind = A_COUNT;
    if (!cJSON_IsObject(j)) return -1;
    const cJSON *k = get(j, "action"), *r = get(j, "risk");
    if (!cJSON_IsString(k) || !cJSON_IsString(r) ||
        (strcmp(r->valuestring, "low") && strcmp(r->valuestring, "high")) ||
        copy(get(j, "target"), a->target, sizeof a->target, 1)) return -1;
    for (int i = 0; i < A_COUNT; i++) if (!strcmp(k->valuestring, names[i])) a->kind = (action_kind_t)i;
    if (a->kind == A_COUNT) return -1;
    a->high = !strcmp(r->valuestring, "high");
    for (const unsigned char *p = (const unsigned char *)a->target; *p; p++) if (*p < 32 || *p == 127) return -1;
    if (a->kind <= A_SCROLL) {
        int drag = a->kind == A_DRAG;
        if (number(j, drag ? "x1" : "x", 0, 1000000, &a->x1, 1) ||
            number(j, drag ? "y1" : "y", 0, 1000000, &a->y1, 1)) return -1;
        if (drag && (number(j, "x2", 0, 1000000, &a->x2, 1) || number(j, "y2", 0, 1000000, &a->y2, 1))) return -1;
        if (a->kind == A_SCROLL && (number(j, "dx", -127, 127, &a->dx, 0) || number(j, "dy", -127, 127, &a->dy, 1))) return -1;
    }
    if (a->kind == A_TYPE || a->kind == A_PASTE) {
        if (copy(get(j, "text"), a->text, sizeof a->text, 1)) return -1;
        unsigned chars = 0;
        for (const unsigned char *p = (const unsigned char *)a->text; *p; p++) {
            if (a->kind == A_TYPE && (*p > 126 || (*p < 32 && *p != '\n' && *p != '\t' && *p != '\r'))) return -1;
            if ((*p & 0xc0) != 0x80) chars++;
        }
        if (chars > 256) return -1;
    }
    if (a->kind == A_KEY && (copy(get(j, "combo"), a->combo, sizeof a->combo, 1) ||
        action_key(a->combo, &a->modifiers, &a->usage))) return -1;
    if (a->kind == A_WAIT && number(j, "ms", 0, 5000, &a->ms, 1)) return -1;
    return action_validate(a);
}
void policy_defaults(policy_config_t *c)
{
    memset(c, 0, sizeof *c); c->max_steps = 30; c->model_calls = 40; c->wall_ms = 600000;
    c->exec_ms = 2000; c->confirm_ms = 30000; c->confirm_first = c->confirm_app_switch = 1;
    static const char *keys[] = {"cmd+q","cmd+w","cmd+delete","cmd+opt+esc","ctrl+cmd+q","cmd+shift+delete"};
    static const char *titles[] = {"loginwindow","login window","lock screen","锁屏","登录窗口",
        "security","privacy","安全","隐私","screen sharing","屏幕共享","mihomo","proxy","代理"};
    for (size_t i = 0; i < sizeof keys / sizeof *keys; i++) strcpy(c->keys[c->nkeys++], keys[i]);
    for (size_t i = 0; i < sizeof titles / sizeof *titles; i++) strcpy(c->titles[c->ntitles++], titles[i]);
}
int policy_parse(const char *s, size_t n, policy_config_t *out)
{
    policy_defaults(out); policy_config_t c = *out;
    cJSON *j = agent_json_parse(s, n, 16384); int rc = -1;
    if (!cJSON_IsObject(j)) goto end;
    const char *nums[] = {"max_steps","model_calls","wall_ms","exec_ms","confirm_ms"};
    unsigned *dest[] = {&c.max_steps,&c.model_calls,&c.wall_ms,&c.exec_ms,&c.confirm_ms};
    const unsigned caps[] = {100,40,600000,2000,120000};
    for (size_t i = 0; i < 5; i++) {
        const cJSON *v = get(j, nums[i]); if (!v) continue;
        if (!cJSON_IsNumber(v) || !isfinite(v->valuedouble) || floor(v->valuedouble) != v->valuedouble) goto end;
        unsigned low = i == 4 ? 1000 : 1;
        *dest[i] = v->valuedouble < low ? low : v->valuedouble > caps[i] ? caps[i] : (unsigned)v->valuedouble;
    }
    const char *bools[] = {"confirm_first","confirm_app_switch"};
    int *bd[] = {&c.confirm_first,&c.confirm_app_switch};
    for (size_t i = 0; i < 2; i++) { const cJSON *v = get(j, bools[i]); if (v) { if (!cJSON_IsBool(v)) goto end; *bd[i] = cJSON_IsTrue(v); } }
    const cJSON *v = get(j, "dangerous_keys"), *entry;
    if (v) {
        if (!cJSON_IsArray(v)) goto end;
        cJSON_ArrayForEach(entry, v) {
            unsigned m, u;
            if (c.nkeys == POLICY_LIST || copy(entry, c.keys[c.nkeys], sizeof c.keys[0], 1) || action_key(c.keys[c.nkeys], &m, &u)) goto end;
            c.nkeys++;
        }
    }
    v = get(j, "window_blacklist");
    if (v) {
        if (!cJSON_IsArray(v)) goto end;
        cJSON_ArrayForEach(entry, v) {
            if (c.ntitles == POLICY_LIST || copy(entry, c.titles[c.ntitles], sizeof c.titles[0], 1) || !*c.titles[c.ntitles]) goto end;
            c.ntitles++;
        }
    }
    *out = c; rc = 0;
end:
    cJSON_Delete(j); return rc;
}
int policy_load(const char *path, policy_config_t *c, int *warning)
{
    policy_defaults(c); if (warning) *warning = 0;
    int fd = open(path ? path : POLICY_PATH, O_RDONLY | O_NONBLOCK);
    if (fd < 0 && errno == ENOENT) return 0;
    struct stat st; char b[16385]; size_t n = 0; int rc = -1;
    if (fd >= 0 && !fstat(fd, &st) && S_ISREG(st.st_mode) && st.st_size > 0 && st.st_size <= 16384) {
        while (n < sizeof b) { ssize_t k = read(fd, b + n, sizeof b - n); if (k < 0 && errno == EINTR) continue; if (k < 0) goto end; if (!k) break; n += (size_t)k; }
        rc = policy_parse(b, n, c);
    }
end:
    if (fd >= 0) close(fd);
    if (rc) { policy_defaults(c); if (warning) *warning = 1; }
    return rc;
}
policy_result_t policy_check(const policy_config_t *c, const policy_context_t *x, const action_t *a, const char **reason)
{
    *reason = "allowed";
    if (!c || !x || action_validate(a)) { *reason = "schema"; return P_REJECT; }
    if (a->kind <= A_SCROLL && ((x->width > 0 && a->x1 >= x->width) || (x->height > 0 && a->y1 >= x->height) ||
        (a->kind == A_DRAG && ((x->width > 0 && a->x2 >= x->width) || (x->height > 0 && a->y2 >= x->height))))) { *reason = "coordinates"; return P_REJECT; }
    if (contains(a->target,"unlock") || contains(a->target,"解锁")) { *reason = "never_do"; return P_REJECT; }
    if (x->secure_input || contains(a->target, "password") || contains(a->target, "密码") || contains(a->target, "secure input")) { *reason = "secure_input"; return P_REJECT; }
    if (x->locked && (x->input_vnc || (a->kind != A_MOVE && a->kind != A_WAIT && a->kind != A_DONE && a->kind != A_ASK_USER))) { *reason = "lock_screen"; return P_REJECT; }
    for (size_t i = 0; i < c->ntitles; i++) if (contains(x->title, c->titles[i]) || contains(x->app, c->titles[i]) || contains(a->target, c->titles[i])) { *reason = "never_do"; return P_REJECT; }
    if (a->kind == A_PASTE) { *reason = "unsupported"; return P_UNSUPPORTED; }
    if (a->kind == A_DONE || a->kind == A_ASK_USER) return P_ALLOW;
    int high = a->high || (!x->step && c->confirm_first);
    if (c->confirm_app_switch && x->step &&
        ((x->previous_title && strcmp(x->title ? x->title : "", x->previous_title)) ||
         (x->previous_app && strcmp(x->app ? x->app : "", x->previous_app)))) high = 1;
    if (a->kind == A_KEY) {
        for (size_t i = 0; i < c->nkeys; i++) { unsigned m, u; if (!action_key(c->keys[i], &m, &u) && m == a->modifiers && u == a->usage) high = 1; }
        if (a->usage == 40 && (contains(x->title, "send") || contains(x->title, "message") || contains(x->title, "chat") || contains(x->title, "发送"))) high = 1;
    }
    static const char *targets[] = {"删除","发送","付款","购买","提交","确认支付","delete","send","pay","submit"};
    for (size_t i = 0; i < sizeof targets / sizeof *targets; i++) if (contains(a->target, targets[i])) high = 1;
    if (a->kind == A_TYPE) {
        static const char *shell[] = {"sudo","rm","curl","wget","bash","sh","terminal","终端","powershell","python"};
        for (size_t i = 0; i < sizeof shell / sizeof *shell; i++) if (contains(a->text, shell[i]) &&
            (i < 4 || strchr(a->text, '\n') || strchr(a->text, '\r') || strchr(a->text, '|'))) high = 1;
    }
    if (high) { *reason = "risk"; return P_CONFIRM; }
    return P_ALLOW;
}
void agent_redact(const char *line, const char *const *secrets, size_t count, char *out, size_t cap)
{
    int redact = contains(line, "authorization") || contains(line, "bearer") || contains(line, "sk-");
    for (size_t i = 0; i < count; i++) if (secrets[i] && *secrets[i] && strstr(line, secrets[i])) redact = 1;
    if (redact) { snprintf(out, cap, "[redacted]"); return; }
    if (!cap) return;
    size_t i = 0; for (; line[i] && i + 1 < cap; i++) out[i] = (unsigned char)line[i] < 32 ? ' ' : line[i]; out[i] = 0;
}
