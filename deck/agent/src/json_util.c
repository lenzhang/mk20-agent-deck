#include "json_util.h"
#include <string.h>
/* Validate strict JSON grammar and bound complexity before any cJSON allocation.
 * cJSON's strtod-based number parser also accepts non-JSON forms such as 01 / 1. */
typedef struct { const char *p, *end; size_t nodes; } json_scan_t;
static void json_ws(json_scan_t *j) { while (j->p < j->end && strchr(" \t\r\n", *j->p)) j->p++; }
static int json_string(json_scan_t *j)
{
    if (j->p == j->end || *j->p++ != '"') return -1;
    while (j->p < j->end) {
        unsigned b = (unsigned char)*j->p++;
        if (b == '"') return 0;
        if (b < 32) return -1;
        if (b != '\\') continue;
        if (j->p == j->end) return -1;
        b = (unsigned char)*j->p++;
        if (b == 'u') {
            unsigned u = 0;
            for (int i = 0; i < 4; i++) {
                if (j->p == j->end) return -1;
                b = (unsigned char)*j->p++; unsigned d;
                if (b >= '0' && b <= '9') d = b - '0';
                else if (b >= 'a' && b <= 'f') d = b - 'a' + 10;
                else if (b >= 'A' && b <= 'F') d = b - 'A' + 10;
                else return -1;
                u = u * 16 + d;
            }
            if (!u) return -1; /* cJSON's C strings cannot represent embedded NUL */
        } else if (!strchr("\"\\/bfnrt", (int)b)) return -1;
    }
    return -1;
}
static int json_digits(json_scan_t *j)
{
    const char *start = j->p;
    while (j->p < j->end && *j->p >= '0' && *j->p <= '9') j->p++;
    return j->p == start ? -1 : 0;
}
static int json_value(json_scan_t *j, int depth)
{
    json_ws(j);
    if (j->p == j->end || ++j->nodes > 4096) return -1;
    char b = *j->p;
    if (b == '"') return json_string(j);
    if (b == '{' || b == '[') {
        if (depth == 32) return -1;
        j->p++; json_ws(j); char close = b == '{' ? '}' : ']';
        if (j->p < j->end && *j->p == close) { j->p++; return 0; }
        for (;;) {
            if (b == '{') {
                if (++j->nodes > 4096 || json_string(j)) return -1;
                json_ws(j); if (j->p == j->end || *j->p++ != ':') return -1;
            }
            if (json_value(j, depth + 1)) return -1;
            json_ws(j); if (j->p == j->end) return -1;
            if (*j->p == close) { j->p++; return 0; }
            if (*j->p++ != ',') return -1;
            json_ws(j);
        }
    }
    const char *literal = b == 't' ? "true" : b == 'f' ? "false" : b == 'n' ? "null" : NULL;
    if (literal) {
        size_t n = strlen(literal);
        if ((size_t)(j->end - j->p) < n || memcmp(j->p, literal, n)) return -1;
        j->p += n; return 0;
    }
    if (b == '-') j->p++;
    if (j->p < j->end && *j->p == '0') j->p++;
    else if (json_digits(j)) return -1;
    if (j->p < j->end && *j->p == '.') { j->p++; if (json_digits(j)) return -1; }
    if (j->p < j->end && (*j->p == 'e' || *j->p == 'E')) {
        j->p++; if (j->p < j->end && (*j->p == '+' || *j->p == '-')) j->p++;
        if (json_digits(j)) return -1;
    }
    return 0;
}
static int duplicates(const cJSON *j)
{
    for (const cJSON *a = j->child; a; a = a->next) {
        if (cJSON_IsObject(j)) for (const cJSON *b = a->next; b; b = b->next)
            if (!strcmp(a->string, b->string)) return 1;
        if (duplicates(a)) return 1;
    }
    return 0;
}
static int utf8(const unsigned char *s, size_t n)
{
    for (size_t i = 0; i < n;) {
        unsigned c = s[i++], u, need, min;
        if (c < 128) continue;
        if (c >= 0xc2 && c <= 0xdf) { u = c & 31; need = 1; min = 128; }
        else if (c >= 0xe0 && c <= 0xef) { u = c & 15; need = 2; min = 2048; }
        else if (c >= 0xf0 && c <= 0xf4) { u = c & 7; need = 3; min = 65536; }
        else return -1;
        if (need > n - i) return -1;
        while (need--) { c = s[i++]; if ((c & 0xc0) != 0x80) return -1; u = (u << 6) | (c & 63); }
        if (u < min || u > 0x10ffff || (u >= 0xd800 && u <= 0xdfff)) return -1;
    }
    return 0;
}
cJSON *agent_json_parse(const char *s, size_t n, size_t max)
{
    if (!s || !n || n > max || memchr(s, 0, n) || utf8((const unsigned char *)s, n)) return NULL;
    json_scan_t scan = {s, s + n, 0};
    if (json_value(&scan, 0)) return NULL;
    json_ws(&scan); if (scan.p != scan.end) return NULL;
    const char *end = NULL; cJSON *j = cJSON_ParseWithLengthOpts(s, n, &end, 0);
    while (end && end < s + n && (*end == ' ' || *end == '\n' || *end == '\r' || *end == '\t')) end++;
    if (!j || end != s + n) { cJSON_Delete(j); return NULL; }
    if (duplicates(j)) { cJSON_Delete(j); return NULL; }
    return j;
}
