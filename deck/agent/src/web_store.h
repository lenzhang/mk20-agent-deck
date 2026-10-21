#ifndef MK20_WEB_STORE_H
#define MK20_WEB_STORE_H
#include "web.h"
typedef struct { const char *id; int vision, tools; } web_model_t;
typedef struct {
    const char *id, *label, *base_url;
    int domestic, supported;
    const web_model_t *models;
    size_t model_count;
} web_provider_t;
int web_directory(const char *, int create);
int web_atomic(int dir, const char *, const void *, size_t);
int web_remove(int dir, const char *);
/* NULL with errno=EBUSY while .apply-in-progress marks an incomplete commit. */
char *web_read_config(web_t *, const char *, size_t *);
/* Locked, marker-gated atomic replace: 0 ok, -1 storage, -2 busy (lock held). */
int web_config_commit(web_t *, const char *name, const char *data, size_t len);
int web_secret_name(const char *, char out[64]);
int web_only(const cJSON *, const char *);
const web_provider_t *web_provider(const char *id);
const web_model_t *web_model(const web_provider_t *, const char *id);
/* UI choices from the generated catalogue plus the proxy-host rule's hosts. */
cJSON *web_catalogue(web_t *);
/* Pairing challenge file: code=NNNNNN\nexpires_uptime=N\n, 0600, tmp+rename. */
int web_uptime(web_t *, long long *seconds);
int web_pair_publish(web_t *, const char code[7], long long expires_uptime);
void web_pair_clear(web_t *);
/* Maintenance (web_maint.c): whitelisted status and fixed-name hook requests. */
cJSON *web_status(web_t *);
int web_maint_request(web_t *, const char *hook, const char *body, size_t len);
void web_clean_temps(const char *dir);
#endif
