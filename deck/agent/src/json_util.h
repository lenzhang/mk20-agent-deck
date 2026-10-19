#ifndef MK20_JSON_UTIL_H
#define MK20_JSON_UTIL_H
#include "cJSON.h"
#include <stddef.h>
/* Strict, bounded grammar; rejects embedded NUL and duplicate object keys. */
cJSON *agent_json_parse(const char *s, size_t n, size_t max);
#endif
