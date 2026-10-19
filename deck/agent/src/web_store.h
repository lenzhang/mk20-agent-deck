#ifndef MK20_WEB_STORE_H
#define MK20_WEB_STORE_H
#include "web.h"
int web_directory(const char *, int create);
int web_atomic(int dir, const char *, const void *, size_t);
int web_remove(int dir, const char *);
char *web_read_config(web_t *, const char *, size_t *);
int web_secret_name(const char *, char out[64]);
int web_only(const cJSON *, const char *);
#endif
