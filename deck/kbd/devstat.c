#ifndef _DEFAULT_SOURCE
#define _DEFAULT_SOURCE
#endif
#include "devstat.h"
#include "uicfg.h"
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static const int period[DS_PROBES] = {DS_PAIR_PERIOD_MS, DS_PERIOD_MS, DS_PERIOD_MS, DS_PERIOD_MS,
    DS_PERIOD_MS, DS_PERIOD_MS, DS_PERIOD_MS, DS_PERIOD_MS, 0};

static int join(char *out, size_t cap, const char *a, const char *b)
{
    int n = snprintf(out, cap, "%s%s", a, b);
    if (n < 0 || (size_t)n >= cap) { out[0] = 0; return -1; }
    return 0;
}
static void set_str(char *dst, size_t cap, const char *src)
{
    memset(dst, 0, cap);
    size_t n = strlen(src); if (n >= cap) n = cap-1;
    memcpy(dst, src, n);
}
static int charset(const char *s, const char *extra)
{
    if (!*s) return 0;
    for (; *s; s++) {
        unsigned char c = (unsigned char)*s;
        if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || strchr(extra, c))) return 0;
    }
    return 1;
}
enum { RF_SIZED, RF_PSEUDO, RF_PREFIX };
/* Regular file only; a final symlink, FIFO, device or directory is rejected without
 * blocking (O_NOFOLLOW|O_NONBLOCK, then fstat). RF_SIZED files must fit in cap-1 bytes
 * and must not change while read; sysfs/procfs report fake sizes (RF_PSEUDO); RF_PREFIX
 * reads at most cap-1 leading bytes. private_only requires mode 0600 and our uid.
 * Returns the length, -2 when absent and -1 when present but unacceptable. */
static long read_file(const char *path, char *buf, size_t cap, int mode, int private_only)
{
    buf[0] = 0;
    if (!*path) return -2;
    int fd = open(path, O_RDONLY|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC|O_NOCTTY);
    if (fd < 0) return errno == ENOENT || errno == ENOTDIR ? -2 : -1;
    struct stat st; size_t n = 0;
    if (fstat(fd, &st) || !S_ISREG(st.st_mode) ||
        (mode == RF_SIZED && (st.st_size < 0 || (unsigned long long)st.st_size >= cap)) ||
        (private_only && ((st.st_mode & 0777) != 0600 || st.st_uid != geteuid()))) { close(fd); return -1; }
    while (n < cap-1) {
        ssize_t r = read(fd, buf+n, cap-1-n);
        if (r < 0) { if (errno == EINTR) continue; close(fd); buf[0] = 0; return -1; }
        if (!r) break;
        n += (size_t)r;
    }
    close(fd);
    buf[n] = 0;
    if ((mode == RF_SIZED && (off_t)n != st.st_size) || (mode == RF_PSEUDO && n == cap-1) || strlen(buf) != n) { buf[0] = 0; return -1; }
    return (long)n;
}
static void trim(char *s) { size_t n = strlen(s); while (n && (s[n-1] == '\n' || s[n-1] == '\r' || s[n-1] == ' ')) s[--n] = 0; }
static int dotted_quad(const char *s)
{
    int parts = 0;
    while (*s) {
        int v = 0, digits = 0;
        if (s[0] == '0' && s[1] >= '0' && s[1] <= '9') return 0;
        while (*s >= '0' && *s <= '9') { v = v*10 + (*s++ - '0'); if (++digits > 3 || v > 255) return 0; }
        if (!digits || ++parts > 4) return 0;
        if (*s == '.') { s++; if (!*s) return 0; } else if (*s) return 0;
    }
    return parts == 4;
}
/* ---- bounded strict JSON walk that captures a few dotted paths (depth <= 8) ---- */
struct jcap { const char *path; char *out; size_t cap; int found; /* 1 string, 2 number, 3 null, -1 dup/bad */ };
typedef struct { const char *p, *e; } jw_t;
static void jws(jw_t *j) { while (j->p < j->e && (*j->p == ' ' || *j->p == '\n' || *j->p == '\r' || *j->p == '\t')) j->p++; }
/* Copies plain bytes; an escape or overflow makes the captured value unusable (returns 1). */
static int jstr(jw_t *j, char *out, size_t cap)
{
    size_t n = 0; int bad = 0;
    if (j->p >= j->e || *j->p != '"') return -1;
    j->p++;
    while (j->p < j->e && *j->p != '"') {
        unsigned char c = (unsigned char)*j->p++;
        if (c < 32) return -1;
        if (c == '\\') {
            if (j->p >= j->e) return -1;
            char x = *j->p++;
            if (x == 'u') {
                if (j->e - j->p < 4) return -1;
                for (int i = 0; i < 4; i++) if (!strchr("0123456789abcdefABCDEF", j->p[i]) || !j->p[i]) return -1;
                j->p += 4;
            } else if (!x || !strchr("\"\\/bfnrt", x)) return -1;
            bad = 1; continue;
        }
        if (out) { if (n+1 >= cap) bad = 1; else out[n++] = (char)c; }
    }
    if (j->p >= j->e) return -1;
    j->p++;
    if (out) out[n] = 0;
    return bad;
}
static struct jcap *jfind(struct jcap *caps, int n, const char *path)
{ for (int i = 0; i < n; i++) if (!strcmp(caps[i].path, path)) return &caps[i]; return NULL; }
static int jval(jw_t *j, int depth, char *path, size_t plen, struct jcap *caps, int ncaps)
{
    jws(j);
    if (j->p >= j->e || depth > 8) return -1;
    struct jcap *c = plen ? jfind(caps, ncaps, path) : NULL;
    char ch = *j->p;
    if (ch == '{' || ch == '[') {
        if (c) c->found = -1; /* containers are never valid captures */
        char close = ch == '{' ? '}' : ']';
        j->p++; jws(j);
        if (j->p < j->e && *j->p == close) { j->p++; return 0; }
        for (;;) {
            size_t np = plen;
            if (ch == '{') {
                char key[32]; jws(j);
                int r = jstr(j, key, sizeof key);
                if (r < 0) return -1;
                jws(j); if (j->p >= j->e || *j->p != ':') return -1;
                j->p++;
                /* Keys only extend the path when representable; others never match. */
                if (!r && plen + 1 + strlen(key) < 96) {
                    if (plen) path[np++] = '.';
                    strcpy(path+np, key); np += strlen(key);
                } else if (plen < 96) { path[plen] = '\x01'; path[plen+1] = 0; np = plen+1; }
            } else if (plen < 96) { path[plen] = '\x01'; path[plen+1] = 0; np = plen+1; }
            if (jval(j, depth+1, path, np, caps, ncaps)) return -1;
            path[plen] = 0;
            jws(j); if (j->p >= j->e) return -1;
            if (*j->p == ',') { j->p++; continue; }
            if (*j->p == close) { j->p++; return 0; }
            return -1;
        }
    }
    if (ch == '"') {
        if (!c) return jstr(j, NULL, 0) < 0 ? -1 : 0;
        int r = jstr(j, c->out, c->cap);
        if (r < 0) return -1;
        c->found = c->found ? -1 : r ? -1 : 1;
        return 0;
    }
    static const char *const lit[] = {"true", "false", "null"};
    for (int i = 0; i < 3; i++) {
        size_t n = strlen(lit[i]);
        if ((size_t)(j->e - j->p) >= n && !memcmp(j->p, lit[i], n)) {
            j->p += n;
            if (c) c->found = c->found ? -1 : i == 2 ? 3 : -1;
            return 0;
        }
    }
    const char *start = j->p;
    if (j->p < j->e && *j->p == '-') j->p++;
    if (j->p >= j->e || *j->p < '0' || *j->p > '9') return -1;
    while (j->p < j->e && ((*j->p >= '0' && *j->p <= '9') || strchr(".eE+-", *j->p))) j->p++;
    if (c) {
        size_t n = (size_t)(j->p - start);
        if (c->found || n >= c->cap) c->found = -1;
        else { memcpy(c->out, start, n); c->out[n] = 0; c->found = 2; }
    }
    return 0;
}
static int jparse(const char *s, size_t n, struct jcap *caps, int ncaps)
{
    char path[100] = ""; jw_t j = {s, s+n};
    jws(&j);
    if (j.p >= j.e || *j.p != '{') return -1;
    if (jval(&j, 0, path, 0, caps, ncaps)) return -1;
    jws(&j);
    return j.p == j.e ? 0 : -1;
}
/* ------------------------------------------------------------------ probes */
static void probe_pair(devstat_t *d, ds_result_t *r)
{
    char text[72], code[7] = "", p[200];
    if (read_file(d->pair_path, text, 65, RF_SIZED, 1) > 0 && !join(p, sizeof p, d->root, "/proc/uptime")) {
        char up[64];
        if (read_file(p, up, sizeof up, RF_PSEUDO, 0) > 0) {
            long long secs = 0; int digits = 0;
            for (const char *q = up; *q >= '0' && *q <= '9' && digits < 12; q++, digits++) secs = secs*10 + (*q-'0');
            if (digits && devstat_parse_pair(text, secs, code)) code[0] = 0;
        }
    }
    set_str(r->pair, sizeof r->pair, code);
}
int devstat_parse_pair(const char *s, long long uptime, char code[7])
{
    code[0] = 0;
    if (strncmp(s, "code=", 5)) return -1;
    s += 5;
    for (int i = 0; i < 6; i++) if (s[i] < '0' || s[i] > '9') return -1;
    if (s[6] != '\n' || strncmp(s+7, "expires_uptime=", 15)) return -1;
    const char *p = s + 22; long long e = 0; int digits = 0;
    while (*p >= '0' && *p <= '9') { if (++digits > 12) return -1; e = e*10 + (*p++ - '0'); }
    if (!digits || p[0] != '\n' || p[1]) return -1;
    if (e <= uptime || e - uptime > DS_PAIR_MAX_AHEAD_S) return -1;
    memcpy(code, s, 6); code[6] = 0;
    return 0;
}
static void probe_udc(devstat_t *d, ds_result_t *r)
{
    char p[256], name[64] = "", state[32];
    r->udc = DS_UDC_UNKNOWN;
    if (join(p, sizeof p, d->root, "/sys/kernel/config/usb_gadget/g1/UDC")) return;
    long n = read_file(p, d->buf, 64, RF_PSEUDO, 0);
    if (n >= 0) {
        trim(d->buf);
        if (!*d->buf) { r->udc = DS_UDC_DOWN; return; } /* gadget present but unbound */
        if (!charset(d->buf, "._:-")) return;
        set_str(name, sizeof name, d->buf);
    } else if (*d->udc_name) set_str(name, sizeof name, d->udc_name);
    else {
        if (join(p, sizeof p, d->root, "/sys/class/udc")) return;
        DIR *dir = opendir(p); struct dirent *e;
        if (!dir) return;
        while ((e = readdir(dir))) if (e->d_name[0] != '.' && strlen(e->d_name) < sizeof name && charset(e->d_name, "._:-")) {
            set_str(d->udc_name, sizeof d->udc_name, e->d_name); break;
        }
        closedir(dir);
        set_str(name, sizeof name, d->udc_name);
    }
    if (!*name) return;
    int k = snprintf(p, sizeof p, "%s/sys/class/udc/%s/state", d->root, name);
    if (k < 0 || (size_t)k >= sizeof p || read_file(p, state, sizeof state, RF_PSEUDO, 0) < 0) return;
    trim(state);
    r->udc = !strcmp(state, "configured") ? DS_UDC_UP : !strcmp(state, "suspended") ? DS_UDC_SUSPENDED :
        *state ? DS_UDC_DOWN : DS_UDC_UNKNOWN;
}
static void probe_gadget(devstat_t *d, ds_result_t *r)
{
    char p[256]; const char *ip = NULL;
    if (!join(p, sizeof p, d->root, "/tmp/mk20/gadget.env") && read_file(p, d->buf, 1025, RF_SIZED, 0) > 0) {
        for (char *line = d->buf; line && *line; ) {
            char *nl = strchr(line, '\n'); if (nl) *nl = 0;
            if (!strncmp(line, "MK20_ECM_IP=", 12) && strlen(line+12) < sizeof r->ip && dotted_quad(line+12)) ip = line+12;
            line = nl ? nl+1 : NULL;
        }
    }
    r->ip_known = ip != NULL;
    set_str(r->ip, sizeof r->ip, ip ? ip : "172.30.250.2");
}
static void probe_wlan(devstat_t *d, ds_result_t *r)
{
    char p[256]; struct stat st;
    r->wlan = !join(p, sizeof p, d->root, "/sys/class/net/wlan0") && !lstat(p, &st);
}
static int route_ok(const char *s) { return !strcmp(s,"direct") || !strcmp(s,"proxy") || !strcmp(s,"blocked"); }
static void probe_net(devstat_t *d, ds_result_t *r)
{
    char p[256], version[16], profile[16], url[64], dom[8], fr[8];
    set_str(r->proxy, sizeof r->proxy, ""); set_str(r->route_dom, sizeof r->route_dom, ""); set_str(r->route_for, sizeof r->route_for, "");
    long n = join(p, sizeof p, d->root, "/data/mk20/agent/net.json") ? -1 : read_file(p, d->buf, sizeof d->buf, RF_SIZED, 0);
    if (n == -2) { r->net = DS_ABSENT; r->profile = -1; return; }
    struct jcap caps[] = {{"version",version,sizeof version,0}, {"profile",profile,sizeof profile,0},
        {"proxy.url",url,sizeof url,0}, {"routes.domestic",dom,sizeof dom,0}, {"routes.foreign",fr,sizeof fr,0}};
    r->net = DS_INVALID; r->profile = -2;
    if (n <= 0 || jparse(d->buf, (size_t)n, caps, 5) || caps[0].found != 2 || strcmp(version, "1") ||
        caps[1].found != 1 || (strcmp(profile, "mac_proxy") && strcmp(profile, "board_wifi")) ||
        (caps[2].found != 1 && caps[2].found != 3) || caps[3].found != 1 || caps[4].found != 1 ||
        !route_ok(dom) || !route_ok(fr) || (caps[2].found == 1 && !charset(url, ":/._-@[]"))) return;
    r->net = DS_OK; r->profile = !strcmp(profile, "board_wifi");
    if (caps[2].found == 1) set_str(r->proxy, sizeof r->proxy, url);
    set_str(r->route_dom, sizeof r->route_dom, dom); set_str(r->route_for, sizeof r->route_for, fr);
}
static void probe_provider(devstat_t *d, ds_result_t *r)
{
    char p[256], version[16], prov[32], model[64];
    long n = join(p, sizeof p, d->root, "/data/mk20/agent/provider.json") ? -1 : read_file(p, d->buf, sizeof d->buf, RF_SIZED, 0);
    if (n == -2) { r->prov = DS_ABSENT; r->provider = r->model = -1; return; }
    struct jcap caps[] = {{"version",version,sizeof version,0}, {"provider",prov,sizeof prov,0}, {"model",model,sizeof model,0}};
    r->prov = DS_INVALID; r->provider = r->model = -2;
    if (n <= 0 || jparse(d->buf, (size_t)n, caps, 3) || caps[0].found != 2 || strcmp(version, "1") ||
        caps[1].found != 1 || caps[2].found != 1) return;
    for (size_t i = 0; i < UI_PROVIDER_COUNT; i++) if (!strcmp(UI_PROVIDERS[i].id, prov))
        for (size_t m = 0; m < UI_PROVIDERS[i].model_count; m++) if (!strcmp(UI_PROVIDERS[i].models[m].id, model)) {
            r->prov = DS_OK; r->provider = (int)i; r->model = (int)m; return;
        }
}
static void probe_marker(devstat_t *d, ds_result_t *r)
{
    char p[256]; struct stat st;
    if (join(p, sizeof p, d->root, "/data/mk20/agent/.apply-in-progress")) { r->marker = 2; return; }
    if (lstat(p, &st)) r->marker = errno == ENOENT || errno == ENOTDIR ? 0 : 2;
    else r->marker = S_ISREG(st.st_mode) ? 1 : 2;
}
/* Presence/permission check only: the key file is never opened. */
static void probe_secret(devstat_t *d, ds_result_t *r)
{
    char dir[256], p[300]; struct stat st;
    int i = r->prov == DS_OK ? r->provider : r->prov == DS_ABSENT ? 0 : -1;
    if (i < 0 || (size_t)i >= UI_PROVIDER_COUNT || join(dir, sizeof dir, d->root, "/data/mk20/secrets")) { r->key = DS_KEY_UNKNOWN; return; }
    if (lstat(dir, &st)) { r->key = errno == ENOENT || errno == ENOTDIR ? DS_KEY_MISSING : DS_KEY_UNKNOWN; return; }
    if (!S_ISDIR(st.st_mode) || (st.st_mode & 077) || st.st_uid != geteuid()) { r->key = DS_KEY_INSECURE; return; }
    int k = snprintf(p, sizeof p, "%s/%s.key", dir, UI_PROVIDERS[i].id);
    if (k < 0 || (size_t)k >= sizeof p) { r->key = DS_KEY_UNKNOWN; return; }
    if (lstat(p, &st)) { r->key = errno == ENOENT ? DS_KEY_MISSING : DS_KEY_UNKNOWN; return; }
    int mode = (int)(st.st_mode & 0777);
    /* Same acceptance as the agent's net_read_key(): regular, 0600/0400, 1..4096 bytes. */
    r->key = S_ISREG(st.st_mode) && (mode == 0600 || mode == 0400) && st.st_uid == geteuid() &&
        st.st_size >= 1 && st.st_size <= 4096 ? DS_KEY_SET : DS_KEY_INSECURE;
}
static void probe_version(devstat_t *d, ds_result_t *r)
{
    if (*d->env_version) { set_str(r->version, sizeof r->version, d->env_version); return; }
    if (read_file(d->manifest, d->buf, 4097, RF_PREFIX, 0) <= 0) return;
    for (char *line = d->buf; line && *line; ) {
        char *nl = strchr(line, '\n'); if (nl) *nl = 0;
        if (!strncmp(line, "version=", 8) && strlen(line+8) < sizeof r->version && charset(line+8, "._+-")) {
            set_str(r->version, sizeof r->version, line+8); return;
        }
        line = nl ? nl+1 : NULL;
    }
}
static void (*const probes[DS_PROBES])(devstat_t *, ds_result_t *) = {probe_pair, probe_udc, probe_gadget,
    probe_wlan, probe_net, probe_provider, probe_marker, probe_secret, probe_version};
void devstat_init(devstat_t *d, const char *root, const char *pair_path, const char *version, const char *rel)
{
    memset(d, 0, sizeof *d);
    set_str(d->root, sizeof d->root, root && strlen(root) < sizeof d->root ? root : "");
    if (pair_path && *pair_path) set_str(d->pair_path, sizeof d->pair_path, strlen(pair_path) < sizeof d->pair_path ? pair_path : "");
    else join(d->pair_path, sizeof d->pair_path, d->root, "/run/mk20/pair-code");
    if (version && strlen(version) < sizeof d->env_version && charset(version, "._+-")) set_str(d->env_version, sizeof d->env_version, version);
    if (rel && *rel && join(d->manifest, sizeof d->manifest, rel, "/MANIFEST")) d->manifest[0] = 0;
    /* Defaults match an unconfigured device until the first probes run. */
    d->r.udc = DS_UDC_UNKNOWN; d->r.profile = d->r.provider = d->r.model = -1; d->r.key = DS_KEY_MISSING;
    set_str(d->r.ip, sizeof d->r.ip, "172.30.250.2");
}
void devstat_init_env(devstat_t *d)
{
    devstat_init(d, getenv("MK20_ROOT"), getenv("MK20_KBD_PAIR_CODE"), getenv("MK20_VERSION"), getenv("MK20_REL"));
}
void devstat_kick(devstat_t *d) { for (int i = 0; i < DS_PROBES; i++) if (period[i]) d->next[i] = 0; }
int devstat_poll(devstat_t *d, int64_t now, int all)
{
    ds_result_t before; int ran = 0;
    memcpy(&before, &d->r, sizeof before);
    for (int i = 0; i < DS_PROBES && (all || !ran); i++) {
        if (d->next[i] == INT64_MAX || now < d->next[i]) continue;
        probes[i](d, &d->r);
        d->next[i] = period[i] ? now + period[i] : INT64_MAX;
        ran = 1;
    }
    d->buf[0] = 0; /* never keep file text around longer than a probe */
    if (!memcmp(&before, &d->r, sizeof before)) return 0;
    d->gen++;
    return 1;
}
