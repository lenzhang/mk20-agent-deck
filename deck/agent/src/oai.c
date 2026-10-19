#include "oai.h"
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static size_t bounded_len(const char *s, size_t max)
{
    size_t n = 0; if (!s) return max + 1;
    while (n <= max && s[n]) n++;
    return n;
}
/* Bound complexity before cJSON allocation as well as input bytes. */
static cJSON *parse(const char *s, size_t n, size_t max)
{
    if (!s || !n || n > max || memchr(s, 0, n)) return NULL;
    size_t nodes = 0; int depth = 0, string = 0, escape = 0;
    for (size_t i = 0; i < n; i++) {
        unsigned char b = (unsigned char)s[i];
        if (string) { if (escape) { if (b == 'u' && i + 4 < n && !memcmp(s + i + 1, "0000", 4)) return NULL; escape = 0; } else if (b == '\\') escape = 1; else if (b == '"') string = 0; }
        else if (b == '"') { string = 1; nodes++; }
        else if (b == '{' || b == '[') { if (++depth > 32) return NULL; nodes++; }
        else if (b == '}' || b == ']') { if (--depth < 0) return NULL; }
        else if (b == ',') nodes++;
        if (nodes > 4096) return NULL;
    }
    if (depth || string) return NULL;
    const char *end = NULL; cJSON *j = cJSON_ParseWithLengthOpts(s, n, &end, 0);
    while (end && end < s + n && (*end == ' ' || *end == '\n' || *end == '\r' || *end == '\t')) end++;
    if (!j || end != s + n) { cJSON_Delete(j); return NULL; }
    return j;
}
static int text(const cJSON *j, char *out, size_t cap, int optional)
{
    if (optional && (!j || cJSON_IsNull(j))) { out[0] = 0; return 0; }
    if (!cJSON_IsString(j) || bounded_len(j->valuestring, cap - 1) >= cap) return -1;
    strcpy(out, j->valuestring); return 0;
}
static const cJSON *get(const cJSON *j, const char *key) { return cJSON_GetObjectItemCaseSensitive(j, key); }
static int add(cJSON *o, const char *key, cJSON *v)
{
    if (!v) return -1;
    if (!cJSON_AddItemToObject(o, key, v)) { cJSON_Delete(v); return -1; }
    return 0;
}
static int append(cJSON *a, cJSON *v)
{
    if (!v) return -1;
    if (!cJSON_AddItemToArray(a, v)) { cJSON_Delete(v); return -1; }
    return 0;
}
static char *image_uri(const unsigned char *jpeg, size_t n)
{
    static const char alphabet[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    const char prefix[] = "data:image/jpeg;base64,"; size_t plen = sizeof prefix - 1;
    if (!jpeg || !n || n > OAI_IMAGE_MAX) return NULL;
    char *out = malloc(plen + ((n + 2) / 3) * 4 + 1); if (!out) return NULL;
    memcpy(out, prefix, plen); size_t at = plen;
    for (size_t i = 0; i < n; i += 3) {
        unsigned v = (unsigned)jpeg[i] << 16;
        if (i + 1 < n) v |= (unsigned)jpeg[i + 1] << 8;
        if (i + 2 < n) v |= jpeg[i + 2];
        out[at++] = alphabet[v >> 18]; out[at++] = alphabet[(v >> 12) & 63];
        out[at++] = i + 1 < n ? alphabet[(v >> 6) & 63] : '='; out[at++] = i + 2 < n ? alphabet[v & 63] : '=';
    }
    out[at] = 0; return out;
}
int oai_build_body(const oai_request_t *q, char **json)
{
    if (!json) return -1;
    *json = NULL;
    if (!q || !q->model || !*q->model || bounded_len(q->model, 128) > 128 ||
        bounded_len(q->system, OAI_TEXT_MAX) > OAI_TEXT_MAX || bounded_len(q->user, OAI_TEXT_MAX) > OAI_TEXT_MAX ||
        q->jpeg_len > OAI_IMAGE_MAX || (q->jpeg_len && !q->jpeg) || q->max_tokens < 1 || q->max_tokens > 32768 ||
        !isfinite(q->temperature) || q->temperature < 0 || q->temperature > 2 ||
        (q->supports_tool_choice != 0 && q->supports_tool_choice != 1)) return -1;
    cJSON *root = cJSON_CreateObject(), *messages = cJSON_CreateArray(), *system = cJSON_CreateObject(), *user = cJSON_CreateObject();
    cJSON *tools = NULL; char *uri = NULL; int rc = -1;
    if (!root || !messages || !system || !user) goto done;
    if (add(root, "model", cJSON_CreateString(q->model)) || add(root, "max_tokens", cJSON_CreateNumber(q->max_tokens)) ||
        add(root, "temperature", cJSON_CreateNumber(q->temperature)) || add(system, "role", cJSON_CreateString("system")) ||
        add(system, "content", cJSON_CreateString(q->system)) || add(user, "role", cJSON_CreateString("user"))) goto done;
    if (q->jpeg_len) {
        uri = image_uri(q->jpeg, q->jpeg_len); if (!uri) goto done;
        cJSON *content = cJSON_CreateArray(), *t = cJSON_CreateObject(), *image = cJSON_CreateObject(), *url = cJSON_CreateObject();
        if (!content || !t || !image || !url) { cJSON_Delete(content); cJSON_Delete(t); cJSON_Delete(image); cJSON_Delete(url); goto done; }
        int bad = add(t, "type", cJSON_CreateString("text")) || add(t, "text", cJSON_CreateString(q->user)) ||
                  add(image, "type", cJSON_CreateString("image_url")) || add(url, "url", cJSON_CreateString(uri));
        if (!bad) { bad = add(image, "image_url", url); url = NULL; }
        if (!bad) { bad = append(content, t); t = NULL; }
        if (!bad) { bad = append(content, image); image = NULL; }
        if (!bad) { bad = add(user, "content", content); content = NULL; }
        cJSON_Delete(content); cJSON_Delete(t); cJSON_Delete(image); cJSON_Delete(url); if (bad) goto done;
    } else if (add(user, "content", cJSON_CreateString(q->user))) goto done;
    int bad = append(messages, system); system = NULL; if (bad) goto done;
    bad = append(messages, user); user = NULL; if (bad) goto done;
    bad = add(root, "messages", messages); messages = NULL; if (bad) goto done;
    if (q->tools_json) {
        size_t n = bounded_len(q->tools_json, OAI_SCHEMA_MAX); tools = parse(q->tools_json, n, OAI_SCHEMA_MAX);
        if (!cJSON_IsArray(tools) || cJSON_GetArraySize(tools) > OAI_TOOLS_MAX) goto done;
        const cJSON *t;
        cJSON_ArrayForEach(t, tools) {
            const cJSON *f = get(t, "function"), *name = get(f, "name");
            if (!cJSON_IsObject(t) || !cJSON_IsString(get(t, "type")) || strcmp(get(t, "type")->valuestring, "function") ||
                !cJSON_IsObject(f) || !cJSON_IsString(name) || !*name->valuestring || bounded_len(name->valuestring, 64) > 64 ||
                !cJSON_IsObject(get(f, "parameters"))) goto done;
        }
        if (cJSON_GetArraySize(tools)) {
            bad = add(root, "tools", tools); tools = NULL; if (bad) goto done;
            if (q->supports_tool_choice && add(root, "tool_choice", cJSON_CreateString("auto"))) goto done;
        }
    }
    *json = cJSON_PrintUnformatted(root);
    if (*json && bounded_len(*json, OAI_BODY_MAX) <= OAI_BODY_MAX) rc = 0;
    else { cJSON_free(*json); *json = NULL; }
done:
    free(uri); cJSON_Delete(tools); cJSON_Delete(system); cJSON_Delete(user); cJSON_Delete(messages); cJSON_Delete(root); return rc;
}
int oai_endpoint(const char *base, char *out, size_t cap)
{
    size_t n = bounded_len(base, 1024);
    if (!out || !cap || n > 1024 || !n || (strncmp(base, "https://", 8) && strncmp(base, "http://", 7))) return -1;
    if (strpbrk(base, "\r\n\t ?#@") || !strstr(base, "://")[3]) return -1;
    const char *authority = strstr(base, "://") + 3;
    if (!*authority || *authority == '/') return -1;
    while (n && base[n - 1] == '/') n--;
    int r = snprintf(out, cap, "%.*s/chat/completions", (int)n, base); return r >= 0 && (size_t)r < cap ? 0 : -1;
}
int oai_auth_header(const char *key, char *out, size_t cap)
{
    size_t n = bounded_len(key, OAI_KEY_MAX);
    if (!n || n > OAI_KEY_MAX || !out || !cap) return -1;
    for (size_t i = 0; i < n; i++) if ((unsigned char)key[i] < 33 || (unsigned char)key[i] > 126) return -1;
    int r = snprintf(out, cap, "Authorization: Bearer %s", key); return r >= 0 && (size_t)r < cap ? 0 : -1;
}
const char *oai_error_name(oai_error_kind k)
{
    switch (k) { case OAI_OK: return "ok"; case OAI_AUTH: return "auth"; case OAI_RATE: return "rate"; case OAI_SERVER: return "server"; default: return "bad_response"; }
}
static void error_field(const cJSON *j, char *out, size_t cap)
{
    if (cJSON_IsString(j)) snprintf(out, cap, "%s", j->valuestring);
    else if (cJSON_IsNumber(j)) snprintf(out, cap, "%.17g", j->valuedouble);
}
int oai_parse_response(const char *body, size_t n, int status, oai_response_t *out)
{
    if (!out) return -1;
    memset(out, 0, sizeof *out); out->error.http_status = status;
    out->error.kind = status == 401 || status == 403 ? OAI_AUTH : status == 429 ? OAI_RATE : status >= 500 && status <= 599 ? OAI_SERVER : OAI_BAD_RESPONSE;
    cJSON *root = parse(body, n, OAI_BODY_MAX); int rc = -1;
    const cJSON *e = get(root, "error");
    if (e) {
        error_field(get(e, "message"), out->error.message, sizeof out->error.message);
        error_field(get(e, "type"), out->error.type, sizeof out->error.type);
        error_field(get(e, "code"), out->error.code, sizeof out->error.code);
        goto done;
    }
    if (status < 200 || status >= 300 || !cJSON_IsObject(root)) goto done;
    const cJSON *choices = get(root, "choices"), *choice = cJSON_GetArrayItem(choices, 0), *msg = get(choice, "message"), *calls = get(msg, "tool_calls");
    if (!cJSON_IsArray(choices) || !cJSON_IsObject(choice) || !cJSON_IsObject(msg) ||
        text(get(choice, "finish_reason"), out->finish_reason, sizeof out->finish_reason, 0) || !*out->finish_reason ||
        text(get(msg, "content"), out->content, sizeof out->content, 1) ||
        (calls && !cJSON_IsArray(calls)) || cJSON_GetArraySize(calls) > OAI_TOOLS_MAX) goto done;
    const cJSON *call;
    cJSON_ArrayForEach(call, calls) {
        oai_tool_call_t *t = &out->calls[out->ncalls++]; const cJSON *f = get(call, "function"), *args = get(f, "arguments");
        if (text(get(call, "id"), t->id, sizeof t->id, 0) || !*t->id || text(get(f, "name"), t->name, sizeof t->name, 0) || !*t->name ||
            !cJSON_IsString(get(call, "type")) || strcmp(get(call, "type")->valuestring, "function")) { strcpy(t->error, "bad_tool_call"); continue; }
        size_t len = cJSON_IsString(args) ? bounded_len(args->valuestring, OAI_ARGUMENT_MAX) : 0;
        t->arguments = len ? parse(args->valuestring, len, OAI_ARGUMENT_MAX) : NULL;
        if (!cJSON_IsObject(t->arguments)) { cJSON_Delete(t->arguments); t->arguments = NULL; strcpy(t->error, "invalid_arguments"); }
    }
    out->error.kind = OAI_OK; rc = 0;
done:
    cJSON_Delete(root);
    if (rc && !*out->error.message) strcpy(out->error.message, "invalid or unsuccessful model response");
    return rc;
}
void oai_response_free(oai_response_t *r)
{
    if (!r) return;
    for (size_t i = 0; i < r->ncalls; i++) { cJSON_Delete(r->calls[i].arguments); r->calls[i].arguments = NULL; }
    r->ncalls = 0;
}
int oai_norm_to_pixel(int norm, int extent)
{
    if (norm < 0 || norm > 1000 || extent < 1 || extent > 1000000) return -1;
    return (int)(((int64_t)norm * (extent - 1) + 500) / 1000);
}
int oai_pixel_to_norm(int pixel, int extent)
{
    if (extent < 1 || extent > 1000000 || pixel < 0 || pixel >= extent) return -1;
    return extent == 1 ? 0 : (int)(((int64_t)pixel * 1000 + (extent - 1) / 2) / (extent - 1));
}
