#ifdef __APPLE__
#define _DARWIN_C_SOURCE
#endif
#define _POSIX_C_SOURCE 200809L
#include "web_store.h"
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
/* One source of truth: deck/share/providers.json -> tools/gen-providers.py. */
#include "web_providers.inc"
/* Mac addresses for the mac_proxy profile (agentcfg.MAC_HOSTS); the usb0 peer is added at runtime. */
static const char *mac_hosts[] = {"172.30.250.1","10.20.0.1","169.254.7.1"};
static const cJSON *get(const cJSON *j, const char *k) { return cJSON_GetObjectItemCaseSensitive(j,k); }
const web_provider_t *web_provider(const char *id)
{
    for (size_t i = 0; id && i < WEB_PROVIDER_COUNT; i++) if (!strcmp(id,WEB_PROVIDERS[i].id)) return &WEB_PROVIDERS[i];
    return NULL;
}
const web_model_t *web_model(const web_provider_t *p, const char *id)
{
    for (size_t i = 0; p && id && i < p->model_count; i++) if (!strcmp(id,p->models[i].id)) return &p->models[i];
    return NULL;
}
cJSON *web_schema(void)
{
    cJSON *j = cJSON_CreateObject(), *p = cJSON_AddObjectToObject(j,"providers"), *h = cJSON_AddArrayToObject(j,"mac_hosts");
    for (size_t i = 0; i < WEB_PROVIDER_COUNT; i++) {
        cJSON *v = cJSON_AddObjectToObject(p,WEB_PROVIDERS[i].id);
        cJSON_AddStringToObject(v,"base_url",WEB_PROVIDERS[i].base_url); cJSON_AddBoolToObject(v,"domestic",WEB_PROVIDERS[i].domestic);
    }
    for (size_t i = 0; i < sizeof mac_hosts/sizeof *mac_hosts; i++) cJSON_AddItemToArray(h,cJSON_CreateString(mac_hosts[i]));
    return j;
}
/* usb0 peer of the bound device address: a.b.c.2 -> a.b.c.1 (configurable ECM_SUBNET). */
static int usb0_peer(const web_t *w, char out[16])
{
    unsigned a, b, c, d, port; char tail;
    if (sscanf(w->host,"%3u.%3u.%3u.%3u:%5u%c",&a,&b,&c,&d,&port,&tail) != 5 || a > 255 || b > 255 || c > 255 || d != 2) return -1;
    snprintf(out,16,"%u.%u.%u.1",a,b,c); return 0;
}
/* The single proxy-host rule, also published to the page through web_catalogue(). */
static size_t proxy_hosts(const web_t *w, char peer[16], const char *out[4])
{
    size_t n = 0;
    if (!usb0_peer(w,peer)) out[n++] = peer;
    for (size_t i = 0; i < sizeof mac_hosts/sizeof *mac_hosts; i++) {
        int dup = 0; for (size_t k = 0; k < n; k++) if (!strcmp(out[k],mac_hosts[i])) dup = 1;
        if (!dup) out[n++] = mac_hosts[i];
    }
    return n;
}
cJSON *web_catalogue(web_t *w)
{
    cJSON *j = cJSON_CreateObject(), *ps = cJSON_AddArrayToObject(j,"providers");
    for (size_t i = 0; i < WEB_PROVIDER_COUNT; i++) {
        const web_provider_t *p = &WEB_PROVIDERS[i]; cJSON *v = cJSON_CreateObject(); cJSON_AddItemToArray(ps,v);
        cJSON_AddStringToObject(v,"id",p->id); cJSON_AddStringToObject(v,"label",p->label);
        cJSON_AddBoolToObject(v,"domestic",p->domestic); cJSON_AddBoolToObject(v,"supported",p->supported);
        cJSON *ms = cJSON_AddArrayToObject(v,"models");
        for (size_t k = 0; k < p->model_count; k++) {
            cJSON *m = cJSON_CreateObject(); cJSON_AddItemToArray(ms,m);
            cJSON_AddStringToObject(m,"id",p->models[k].id); cJSON_AddBoolToObject(m,"vision",p->models[k].vision); cJSON_AddBoolToObject(m,"tools",p->models[k].tools);
        }
    }
    char peer[16]; const char *hosts[4]; size_t n = proxy_hosts(w,peer,hosts);
    cJSON *h = cJSON_AddArrayToObject(j,"mac_hosts"); for (size_t i = 0; i < n; i++) cJSON_AddItemToArray(h,cJSON_CreateString(hosts[i]));
    cJSON *s = cJSON_AddArrayToObject(j,"secrets");
    for (size_t i = 0; i < WEB_PROVIDER_COUNT; i++) cJSON_AddItemToArray(s,cJSON_CreateString(WEB_PROVIDERS[i].id));
    cJSON_AddItemToArray(s,cJSON_CreateString("vnc"));
    return j;
}
int web_only(const cJSON *j, const char *fields)
{
    if (!cJSON_IsObject(j)) return 0;
    for (const cJSON *a = j->child; a; a = a->next) {
        int found = 0; size_t len = strlen(a->string);
        for (const char *p = fields; *p;) {
            const char *comma = strchr(p,','); size_t n = comma ? (size_t)(comma-p) : strlen(p);
            if (n == len && !memcmp(p,a->string,n)) found = 1;
            if (!comma) break;
            p = comma+1;
        }
        if (!found) return 0;
    }
    return 1;
}
int web_secret_name(const char *name, char out[64])
{
    if (!strcmp(name,"vnc")) { strcpy(out,"vnc.pass"); return 0; }
    const web_provider_t *p = web_provider(name); if (!p) return -1;
    snprintf(out,64,"%s.key",p->id); return 0;
}
/* Walk every component using directory fds: neither final nor ancestor symlinks
 * can redirect an install. Refuse dot-dot and paths too long for local storage. */
int web_directory(const char *path, int create)
{
    if (!path || !*path || strlen(path) >= 4096) return -1;
    char b[4096]; strcpy(b,path); int dir = open(*path == '/' ? "/" : ".",O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (dir < 0) return -1;
    char *p = b;
    while (*p) {
        while (*p == '/') p++;
        if (!*p) break;
        char *end = strchr(p,'/'); if (end) *end = 0;
        if (!strcmp(p,".") || !strcmp(p,"..")) goto bad;
        int next = openat(dir,p,O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        if (next < 0 && errno == ENOENT && create) {
            if (mkdirat(dir,p,0700) && errno != EEXIST) goto bad;
            next = openat(dir,p,O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        }
        if (next < 0) goto bad;
        close(dir); dir = next; p = end ? end+1 : p+strlen(p);
    }
    struct stat st;
    if (fstat(dir,&st) || !S_ISDIR(st.st_mode) || st.st_uid != geteuid()) goto bad;
    if (create && fchmod(dir,0700)) goto bad;
    if ((st.st_mode & 07777) != 0700 && !create) goto bad;
    return dir;
bad:
    { int e = errno; close(dir); errno = e; return -1; }
}
static int destination(int dir, const char *name)
{
    struct stat st;
    if (fstatat(dir,name,&st,AT_SYMLINK_NOFOLLOW)) return errno == ENOENT ? 0 : -1;
    return S_ISREG(st.st_mode) && st.st_uid == geteuid() ? 0 : -1;
}
static int write_all(int fd, const void *value, size_t len)
{
    size_t n = 0;
    while (n < len) {
        ssize_t k = write(fd,(const char *)value+n,len-n);
        if (k < 0 && errno == EINTR) continue;
        if (k <= 0) return -1;
        n += (size_t)k;
    }
    return 0;
}
int web_atomic(int dir, const char *name, const void *value, size_t len)
{
    char random[33], tmp[64]; if (destination(dir,name) || httpd_random_hex(random,16)) return -1;
    snprintf(tmp,sizeof tmp,".web-%s.tmp",random);
    int fd = openat(dir,tmp,O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC,0600); if (fd < 0) return -1;
    int bad = fchmod(fd,0600) || write_all(fd,value,len);
    if (!bad && fsync(fd)) bad = 1;
    if (close(fd)) bad = 1;
    if (!bad && (destination(dir,name) || renameat(dir,tmp,dir,name))) bad = 1;
    if (!bad && fsync(dir)) bad = 1;
    unlinkat(dir,tmp,0); return bad ? -1 : 0;
}
int web_remove(int dir, const char *name)
{
    if (destination(dir,name)) return -1;
    if (unlinkat(dir,name,0) && errno != ENOENT) return -1;
    return fsync(dir);
}
/* Same contract as kbd uicfg_apply (ONBOARD-UI-PLAN U3): a process lock serializes
 * writers; .apply-in-progress is durable before mutation and cleared only after
 * the replacement is durable. Any failure leaves the marker, so readers refuse
 * the pair until a later successful save. */
static int regular_or_absent(int dir, const char *name)
{
    struct stat st;
    if (fstatat(dir,name,&st,AT_SYMLINK_NOFOLLOW)) return errno == ENOENT ? 0 : -1;
    return S_ISREG(st.st_mode) && st.st_uid == geteuid() && st.st_nlink == 1 ? 0 : -1;
}
static int marker_set(int dir)
{
    int fd = openat(dir,".apply-in-progress",O_WRONLY | O_CREAT | O_NOFOLLOW | O_CLOEXEC,0600);
    if (fd < 0) return -1;
    int bad = fchmod(fd,0600) || write_all(fd,"1",1) || fsync(fd);
    if (close(fd)) bad = 1;
    return bad || fsync(dir) ? -1 : 0;
}
int web_config_commit(web_t *w, const char *name, const char *data, size_t len)
{
    char file[32]; snprintf(file,sizeof file,"%s.json",name);
    int dir = web_directory(w->config,1), lock = -1, rc = -1;
    if (dir < 0) return -1;
    if (regular_or_absent(dir,".apply-lock")) goto done;
    lock = openat(dir,".apply-lock",O_WRONLY | O_CREAT | O_NOFOLLOW | O_CLOEXEC,0600);
    if (lock < 0 || fchmod(lock,0600)) goto done;
    struct flock fl; memset(&fl,0,sizeof fl); fl.l_type = F_WRLCK; fl.l_whence = SEEK_SET;
    if (fcntl(lock,F_SETLK,&fl)) { if (errno == EACCES || errno == EAGAIN) rc = -2; goto done; }
    if (regular_or_absent(dir,"net.json") || regular_or_absent(dir,"provider.json") ||
        regular_or_absent(dir,".apply-in-progress") || marker_set(dir)) goto done;
    if (web_atomic(dir,file,data,len)) goto done;
    if (unlinkat(dir,".apply-in-progress",0) || fsync(dir)) { (void)marker_set(dir); goto done; }
    rc = 0;
done:
    if (lock >= 0) close(lock);
    close(dir); return rc;
}
static int validate(web_t *w, const char *name, const char *s, size_t n, int strict)
{
    cJSON *j = agent_json_parse(s,n,LLM_PROVIDER_MAX); int rc = -1;
    if (!strcmp(name,"provider")) {
        llm_provider_t p;
        if (!web_only(j,"version,provider,model,base_url,domestic,route,api_key_file") || llm_provider_parse(s,n,&p)) goto end;
        char key[4096]; int k = snprintf(key,sizeof key,"%s/%s.key",w->secrets,p.provider);
        if (k < 0 || (size_t)k >= sizeof key || strcmp(key,p.api_key_file)) goto end;
        const web_provider_t *v = web_provider(p.provider);
        if (!v || strcmp(p.base_url,v->base_url) || p.domestic != v->domestic || strcmp(p.route,v->domestic ? "domestic" : "foreign")) goto end;
        /* New writes must use a supported adapter and a catalogue model; an older
         * saved model still loads so the device keeps its working configuration. */
        if (strict && (!v->supported || !web_model(v,p.model))) goto end;
        rc = 0;
    } else if (!strcmp(name,"net")) {
        netcfg_t net = {0};
        if (!web_only(j,"version,profile,mode,proxy,routes,note") || !web_only(get(j,"proxy"),"url,fallbacks") ||
            !web_only(get(j,"routes"),"domestic,foreign") ||
            (get(j,"note") && !cJSON_IsString(get(j,"note"))) || netcfg_parse(s,n,&net)) goto end;
        int bad = net.foreign == NET_DIRECT;
        if (net.profile != NET_USB_WIFI && get(j,"mode")) bad = 1;
        char peer[16]; const char *hosts[4]; size_t count = proxy_hosts(w,peer,hosts);
        for (size_t i = 0; i < net.proxy_count; i++) {
            const char *host = net.proxies[i].host; int allowed = 0;
            for (size_t k = 0; k < count; k++) if (!strcmp(host,hosts[k])) allowed = 1;
            /* The board proxy is numeric loopback, never an arbitrary LAN host. */
            if (net.profile != NET_MAC_PROXY && !strcmp(host,"127.0.0.1")) allowed = 1;
            if (!allowed) bad = 1;
        }
        if (!bad) rc = 0;
        netcfg_free(&net);
    }
end:
    cJSON_Delete(j); return rc;
}
int web_config_validate(web_t *w, const char *name, const char *s, size_t n) { return validate(w,name,s,n,1); }
char *web_read_config(web_t *w, const char *name, size_t *len)
{
    *len = 0; int dir = web_directory(w->config,0); if (dir < 0) return NULL;
    struct stat st; char file[32]; snprintf(file,sizeof file,"%s.json",name);
    if (!fstatat(dir,".apply-in-progress",&st,AT_SYMLINK_NOFOLLOW) || errno != ENOENT) { close(dir); errno = EBUSY; return NULL; }
    int fd = openat(dir,file,O_RDONLY | O_NONBLOCK | O_NOFOLLOW | O_CLOEXEC); close(dir);
    if (fd < 0) return NULL;
    char *data = malloc(LLM_PROVIDER_MAX+1); size_t n = 0;
    int bad = !data || fstat(fd,&st) || !S_ISREG(st.st_mode) || st.st_uid != geteuid() || (st.st_mode & 07777) != 0600;
    while (!bad && n <= LLM_PROVIDER_MAX) {
        ssize_t k = read(fd,data+n,LLM_PROVIDER_MAX+1-n);
        if (k < 0 && errno == EINTR) continue;
        if (k < 0) bad = 1;
        if (k <= 0) break;
        n += (size_t)k;
    }
    if (close(fd)) bad = 1;
    if (!bad && validate(w,name,data,n,0)) bad = 1;
    if (bad) { free(data); errno = EINVAL; return NULL; }
    data[n] = 0; *len = n; return data;
}
/* ---- pairing challenge file (displayed by mk20-kbd) ---- */
int web_uptime(web_t *w, long long *seconds)
{
    if (!*w->uptime_path) return -1;
    int fd = open(w->uptime_path,O_RDONLY | O_CLOEXEC); if (fd < 0) return -1;
    char b[64]; ssize_t n; do n = read(fd,b,sizeof b-1); while (n < 0 && errno == EINTR);
    close(fd); if (n <= 0) return -1;
    b[n] = 0; long long v = 0; int digits = 0;
    for (const char *p = b; *p >= '0' && *p <= '9'; p++) { if (v > 99999999999LL) return -1; v = v*10+(*p-'0'); digits++; }
    if (!digits) return -1;
    *seconds = v; return 0;
}
static int split(const char *path, char dir[4096], char base[256])
{
    size_t n = strlen(path); if (!n || n >= 4096) return -1;
    const char *slash = strrchr(path,'/'), *name = slash ? slash+1 : path;
    if (!*name || strlen(name) >= 200 || !strcmp(name,".") || !strcmp(name,"..")) return -1;
    if (!slash) strcpy(dir,".");
    else if (slash == path) strcpy(dir,"/");
    else { memcpy(dir,path,(size_t)(slash-path)); dir[slash-path] = 0; }
    strcpy(base,name); return 0;
}
/* /run/mk20 is shared with kbd.sock/status: never chmod it; refuse a directory
 * that is foreign-owned or writable by group/other. Create it 0700 if absent. */
static int pair_dir(const char *dir, int create)
{
    int fd = open(dir,O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0 && errno == ENOENT && create) {
        if (mkdir(dir,0700) && errno != EEXIST) return -1;
        fd = open(dir,O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    }
    if (fd < 0) return -1;
    struct stat st;
    if (fstat(fd,&st) || !S_ISDIR(st.st_mode) || st.st_uid != geteuid() || (st.st_mode & 022)) { close(fd); return -1; }
    return fd;
}
int web_pair_publish(web_t *w, const char code[7], long long expires)
{
    char dir[4096], base[256], body[64], rnd[17], tmp[256];
    if (!*w->pair_path || split(w->pair_path,dir,base) || httpd_random_hex(rnd,8)) return -1;
    int n = snprintf(body,sizeof body,"code=%.6s\nexpires_uptime=%lld\n",code,expires);
    if (n <= 0 || (size_t)n >= sizeof body) return -1;
    int d = pair_dir(dir,1); if (d < 0) { mem_wipe(body,sizeof body); return -1; }
    snprintf(tmp,sizeof tmp,".%.200s.%s.tmp",base,rnd);
    int fd = openat(d,tmp,O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC,0600), bad = fd < 0;
    if (!bad) {
        bad = fchmod(fd,0600) || write_all(fd,body,(size_t)n) || fsync(fd);
        if (close(fd)) bad = 1;
        if (!bad && renameat(d,tmp,d,base)) bad = 1;
        if (bad) unlinkat(d,tmp,0);
    }
    mem_wipe(body,sizeof body); close(d); return bad ? -1 : 0;
}
void web_pair_clear(web_t *w)
{
    char dir[4096], base[256];
    if (!*w->pair_path || split(w->pair_path,dir,base)) return;
    int d = pair_dir(dir,0); if (d < 0) return;
    (void)unlinkat(d,base,0); close(d);
}
