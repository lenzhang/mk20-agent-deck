#ifdef __APPLE__
#define _DARWIN_C_SOURCE
#endif
#define _POSIX_C_SOURCE 200809L
/* Maintenance page backend (MAINT-HOOKS.md). Read-only device status from mk20d
 * health/state snapshots, copied field by field through an allow-list; requests
 * are fixed-name files published into the supervisor's private runtime inbox. */
#include "web_store.h"
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
static const cJSON *get(const cJSON *j, const char *k) { return cJSON_GetObjectItemCaseSensitive(j,k); }
/* Snapshots come from root supervisors: a file owned by anyone else, or writable
 * by group/other, could be forged and is not shown (cf. web_read_config). */
static int trusted(const struct stat *st)
{
    return S_ISREG(st->st_mode) && (st->st_uid == 0 || st->st_uid == geteuid()) && !(st->st_mode & 022);
}
enum { SNAP_OK, SNAP_MISSING, SNAP_UNTRUSTED };
static const char *snap_name(int s) { return s == SNAP_OK ? "ok" : s == SNAP_MISSING ? "missing" : "unavailable"; }
/* Bounded regular-file JSON read; never follows a final symlink. */
static cJSON *read_json(int dir, const char *path, size_t cap, long long *age, int *state)
{
    int fd = dir >= 0 ? openat(dir,path,O_RDONLY | O_NONBLOCK | O_NOFOLLOW | O_CLOEXEC) : open(path,O_RDONLY | O_NONBLOCK | O_NOFOLLOW | O_CLOEXEC);
    *state = fd < 0 && errno == ENOENT ? SNAP_MISSING : SNAP_UNTRUSTED;
    if (fd < 0) return NULL;
    struct stat st; char *data = NULL; size_t n = 0; cJSON *j = NULL;
    if (fstat(fd,&st) || !trusted(&st) || st.st_size < 0 || (size_t)st.st_size > cap || !(data = malloc(cap+1))) goto end;
    while (n <= cap) {
        ssize_t k = read(fd,data+n,cap+1-n);
        if (k < 0 && errno == EINTR) continue;
        if (k <= 0) break;
        n += (size_t)k;
    }
    if (n <= cap) j = agent_json_parse(data,n,cap);
    if (cJSON_IsObject(j)) *state = SNAP_OK;
    if (age) { long long a = (long long)time(NULL)-(long long)st.st_mtime; *age = a < 0 ? 0 : a; }
end:
    free(data); close(fd); return j;
}
/* Printable ASCII only, bounded: snapshot strings are diagnostics, never markup. */
static void clean(char *dst, size_t cap, const char *src, size_t n)
{
    size_t k = 0;
    for (const unsigned char *p = (const unsigned char *)src; k < n && p[k] && k+1 < cap; k++) dst[k] = p[k] >= 32 && p[k] < 127 ? (char)p[k] : '?';
    dst[k] = 0;
}
static void add_string(cJSON *o, const char *name, const cJSON *v, size_t cap)
{
    if (!cJSON_IsString(v)) { cJSON_AddNullToObject(o,name); return; }
    char b[160]; clean(b,cap+1 < sizeof b ? cap+1 : sizeof b,v->valuestring,strlen(v->valuestring)); cJSON_AddStringToObject(o,name,b);
}
static void add_number(cJSON *o, const char *name, const cJSON *v)
{
    if (cJSON_IsNumber(v) && v->valuedouble >= 0 && v->valuedouble < 1e12) cJSON_AddNumberToObject(o,name,(double)(long long)v->valuedouble);
    else cJSON_AddNullToObject(o,name);
}
static void add_bool(cJSON *o, const char *name, const cJSON *v)
{
    if (cJSON_IsBool(v)) cJSON_AddBoolToObject(o,name,cJSON_IsTrue(v)); else cJSON_AddNullToObject(o,name);
}
/* mk20d joins selftest lines with spaces: split at each level keyword. */
static void selftest(cJSON *o, const cJSON *st)
{
    cJSON *s = cJSON_AddObjectToObject(o,"selftest");
    add_bool(s,"pass",get(st,"pass"));
    const cJSON *detail = get(st,"detail"); unsigned counts[4] = {0}; static const char *levels[] = {"PASS ","INFO ","WARN ","FAIL "};
    cJSON *problems = cJSON_AddArrayToObject(s,"problems");
    const char *p = cJSON_IsString(detail) ? detail->valuestring : "";
    while (*p) {
        int level = -1;
        for (int i = 0; i < 4; i++) if (!strncmp(p,levels[i],5)) level = i;
        if (level < 0) { p++; continue; }
        const char *start = p, *end = p+5;
        for (; *end; end++) {
            int next = 0;
            if (end[-1] == ' ') for (int i = 0; i < 4; i++) if (!strncmp(end,levels[i],5)) next = 1;
            if (next) break;
        }
        counts[level]++;
        if (level >= 2 && cJSON_GetArraySize(problems) < 12) {
            size_t n = (size_t)(end-start); while (n && start[n-1] == ' ') n--;
            char b[121]; clean(b,sizeof b,start,n); cJSON_AddItemToArray(problems,cJSON_CreateString(b));
        }
        p = end;
    }
    cJSON_AddNumberToObject(s,"pass_count",counts[0]); cJSON_AddNumberToObject(s,"warn_count",counts[2]); cJSON_AddNumberToObject(s,"fail_count",counts[3]);
}
static int debug_verbose(web_t *w)
{
    if (!*w->debug_path) return -1;
    int fd = open(w->debug_path,O_RDONLY | O_NONBLOCK | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return errno == ENOENT ? 0 : -1;
    struct stat st; char b[130]; ssize_t n = -1;
    if (!fstat(fd,&st) && trusted(&st) && st.st_size <= 128) { do n = read(fd,b,sizeof b-1); while (n < 0 && errno == EINTR); }
    close(fd); if (n < 0) return -1;
    b[n] = 0; return !strcmp(b,"verbose=1") || !strcmp(b,"verbose=1\n");
}
static const char *hooks[] = {"restart-kbd","exit-safe-mode","debug"};
cJSON *web_status(web_t *w)
{
    cJSON *o = cJSON_CreateObject(); long long up, age = 0;
    if (!web_uptime(w,&up)) cJSON_AddNumberToObject(o,"uptime_s",(double)up); else cJSON_AddNullToObject(o,"uptime_s");
    char ip[sizeof w->host]; memcpy(ip,w->host,sizeof ip); char *colon = strrchr(ip,':'); if (colon) *colon = 0;
    cJSON_AddStringToObject(o,"usb0_ip",ip); cJSON_AddStringToObject(o,"web_url",w->origin);
    cJSON_AddBoolToObject(o,"agent",w->agent);
    int hs = SNAP_MISSING, ss = SNAP_MISSING;
    cJSON *h = *w->health_path ? read_json(-1,w->health_path,65536,&age,&hs) : NULL;
    if (h && !cJSON_IsObject(h)) hs = SNAP_UNTRUSTED;
    cJSON_AddBoolToObject(o,"health",cJSON_IsObject(h)); cJSON_AddStringToObject(o,"health_state",snap_name(hs));
    if (cJSON_IsObject(h)) {
        cJSON_AddNumberToObject(o,"health_age_s",(double)age);
        add_string(o,"version",get(h,"version"),32); add_string(o,"status",get(h,"status"),16);
        const cJSON *rel = get(h,"release"); cJSON *r = cJSON_AddObjectToObject(o,"release");
        add_string(r,"slot",get(rel,"slot"),8); add_string(r,"good",get(rel,"good"),32);
        add_string(r,"pending",get(rel,"pending"),32); add_string(r,"last_rollback",get(rel,"last_rollback"),96);
        add_number(o,"mem_avail_kb",get(h,"mem_avail_kb")); add_number(o,"fat_free_kb",get(h,"fat_free_kb")); add_number(o,"data_free_kb",get(h,"data_free_kb"));
        const cJSON *k = get(h,"kbd"); cJSON *kb = cJSON_AddObjectToObject(o,"kbd");
        add_bool(kb,"running",get(k,"running")); add_string(kb,"mode",get(k,"mode"),16);
        add_number(kb,"restarts",get(k,"restarts")); add_number(kb,"recent_crashes",get(k,"recent_crashes"));
        selftest(o,get(h,"selftest"));
    }
    cJSON_Delete(h);
    /* Supervisor snapshot (about once per second) is fresher than health. */
    int dir = *w->maint_dir ? web_directory(w->maint_dir,0) : -1;
    cJSON *s = dir >= 0 ? read_json(dir,"state.json",4096,NULL,&ss) : NULL;
    if (s && !cJSON_IsObject(s)) ss = SNAP_UNTRUSTED;
    cJSON *m = cJSON_AddObjectToObject(o,"maint"); cJSON_AddBoolToObject(m,"available",dir >= 0);
    cJSON_AddStringToObject(m,"state_status",snap_name(dir >= 0 ? ss : SNAP_MISSING));
    if (cJSON_IsObject(s)) {
        cJSON *st = cJSON_AddObjectToObject(m,"state");
        add_string(st,"mode",get(s,"mode"),16); add_number(st,"restarts",get(s,"restarts"));
        add_number(st,"recent_crashes",get(s,"recent_crashes")); add_number(st,"last_rc",get(s,"last_rc"));
        add_string(st,"reason",get(s,"reason"),24); add_string(st,"version",get(s,"version"),32);
    } else cJSON_AddNullToObject(m,"state");
    cJSON_Delete(s);
    cJSON *pending = cJSON_AddArrayToObject(m,"pending"); struct stat st;
    for (size_t i = 0; dir >= 0 && i < sizeof hooks/sizeof *hooks; i++) if (!fstatat(dir,hooks[i],&st,AT_SYMLINK_NOFOLLOW)) cJSON_AddItemToArray(pending,cJSON_CreateString(hooks[i]));
    if (dir >= 0) close(dir);
    int verbose = debug_verbose(w); if (verbose < 0) cJSON_AddNullToObject(m,"debug_verbose"); else cJSON_AddBoolToObject(m,"debug_verbose",verbose);
    return o;
}
/* 0 queued, -1 storage failure, -2 inbox unavailable (supervisor not running). */
int web_maint_request(web_t *w, const char *hook, const char *body, size_t len)
{
    int known = 0; for (size_t i = 0; i < sizeof hooks/sizeof *hooks; i++) if (!strcmp(hook,hooks[i])) known = 1;
    if (!known || !*w->maint_dir) return -1;
    int dir = web_directory(w->maint_dir,0); if (dir < 0) return -2;
    int rc = web_atomic(dir,hook,body,len); close(dir); return rc ? -1 : 0;
}
/* Remove this producer's abandoned temporaries (".web-*.tmp") left by a crash. */
void web_clean_temps(const char *path)
{
    int dir = path && *path ? web_directory(path,0) : -1; if (dir < 0) return;
    int scan = dup(dir); DIR *d = scan >= 0 ? fdopendir(scan) : NULL;
    if (!d) { if (scan >= 0) close(scan); close(dir); return; }
    struct dirent *e;
    while ((e = readdir(d))) {
        size_t n = strlen(e->d_name);
        if (n > 9 && !strncmp(e->d_name,".web-",5) && !strcmp(e->d_name+n-4,".tmp")) (void)unlinkat(dir,e->d_name,0);
    }
    closedir(d); close(dir);
}
