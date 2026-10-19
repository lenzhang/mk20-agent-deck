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
typedef struct { const char *name, *url; int domestic; } provider_entry_t;
static const provider_entry_t providers[] = {
    {"qwen","https://dashscope.aliyuncs.com/compatible-mode/v1",1},
    {"glm","https://open.bigmodel.cn/api/paas/v4",1},
    {"kimi","https://api.moonshot.cn/v1",1},
    {"deepseek","https://api.deepseek.com/v1",1},
    {"openai","https://api.openai.com/v1",0},
    {"anthropic","https://api.anthropic.com/v1",0},
    {"gemini","https://generativelanguage.googleapis.com/v1beta/openai",0}
};
static const char *mac_hosts[] = {"172.30.250.1","10.20.0.1","169.254.7.1"};
static const cJSON *get(const cJSON *j, const char *k) { return cJSON_GetObjectItemCaseSensitive(j,k); }
cJSON *web_schema(void)
{
    cJSON *j = cJSON_CreateObject(), *p = cJSON_AddObjectToObject(j,"providers"), *h = cJSON_AddArrayToObject(j,"mac_hosts");
    for (size_t i = 0; i < sizeof providers/sizeof *providers; i++) {
        cJSON *v = cJSON_AddObjectToObject(p,providers[i].name);
        cJSON_AddStringToObject(v,"base_url",providers[i].url); cJSON_AddBoolToObject(v,"domestic",providers[i].domestic);
    }
    for (size_t i = 0; i < sizeof mac_hosts/sizeof *mac_hosts; i++) cJSON_AddItemToArray(h,cJSON_CreateString(mac_hosts[i]));
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
    for (size_t i = 0; i < sizeof providers/sizeof *providers; i++) if (!strcmp(name,providers[i].name)) {
        snprintf(out,64,"%s.key",providers[i].name); return 0;
    }
    return -1;
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
    close(dir); return -1;
}
static int destination(int dir, const char *name)
{
    struct stat st;
    if (fstatat(dir,name,&st,AT_SYMLINK_NOFOLLOW)) return errno == ENOENT ? 0 : -1;
    return S_ISREG(st.st_mode) && st.st_uid == geteuid() ? 0 : -1;
}
int web_atomic(int dir, const char *name, const void *value, size_t len)
{
    char random[33], tmp[64]; if (destination(dir,name) || httpd_random_hex(random,16)) return -1;
    snprintf(tmp,sizeof tmp,".web-%s.tmp",random);
    int fd = openat(dir,tmp,O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC,0600); if (fd < 0) return -1;
    int bad = fchmod(fd,0600); size_t n = 0;
    while (!bad && n < len) {
        ssize_t k = write(fd,(const char *)value+n,len-n);
        if (k < 0 && errno == EINTR) continue;
        if (k <= 0) bad = 1; else n += (size_t)k;
    }
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
int web_config_validate(web_t *w, const char *name, const char *s, size_t n)
{
    cJSON *j = agent_json_parse(s,n,LLM_PROVIDER_MAX); int rc = -1;
    if (!strcmp(name,"provider")) {
        llm_provider_t p;
        if (!web_only(j,"version,provider,model,base_url,domestic,route,api_key_file") || llm_provider_parse(s,n,&p)) goto end;
        char key[4096]; int k = snprintf(key,sizeof key,"%s/%s.key",w->secrets,p.provider);
        if (k < 0 || (size_t)k >= sizeof key || strcmp(key,p.api_key_file)) goto end;
        for (size_t i = 0; i < sizeof providers/sizeof *providers; i++) {
            const provider_entry_t *v = &providers[i];
            if (!strcmp(p.provider,v->name) && !strcmp(p.base_url,v->url) && p.domestic == v->domestic && !strcmp(p.route,v->domestic ? "domestic" : "foreign")) rc = 0;
        }
    } else if (!strcmp(name,"net")) {
        netcfg_t net = {0};
        if (!web_only(j,"version,profile,mode,proxy,routes,note") || !web_only(get(j,"proxy"),"url,fallbacks") ||
            !web_only(get(j,"routes"),"domestic,foreign") ||
            (get(j,"note") && !cJSON_IsString(get(j,"note"))) || netcfg_parse(s,n,&net)) goto end;
        int bad = net.foreign == NET_DIRECT;
        if (net.profile != NET_USB_WIFI && get(j,"mode")) bad = 1;
        for (size_t i = 0; i < net.proxy_count; i++) {
            const char *host = net.proxies[i].host; int allowed = 0;
            for (size_t k = 0; k < sizeof mac_hosts/sizeof *mac_hosts; k++) if (!strcmp(host,mac_hosts[k])) allowed = 1;
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
char *web_read_config(web_t *w, const char *name, size_t *len)
{
    *len = 0; int dir = web_directory(w->config,0); if (dir < 0) return NULL;
    char file[32]; snprintf(file,sizeof file,"%s.json",name);
    int fd = openat(dir,file,O_RDONLY | O_NONBLOCK | O_NOFOLLOW | O_CLOEXEC); close(dir);
    if (fd < 0) return NULL;
    char *data = malloc(LLM_PROVIDER_MAX+1); struct stat st; size_t n = 0;
    int bad = !data || fstat(fd,&st) || !S_ISREG(st.st_mode) || st.st_uid != geteuid() || (st.st_mode & 07777) != 0600;
    while (!bad && n <= LLM_PROVIDER_MAX) {
        ssize_t k = read(fd,data+n,LLM_PROVIDER_MAX+1-n);
        if (k < 0 && errno == EINTR) continue;
        if (k < 0) bad = 1;
        if (k <= 0) break;
        n += (size_t)k;
    }
    if (close(fd)) bad = 1;
    if (!bad && web_config_validate(w,name,data,n)) bad = 1;
    if (bad) { free(data); return NULL; }
    data[n] = 0; *len = n; return data;
}
