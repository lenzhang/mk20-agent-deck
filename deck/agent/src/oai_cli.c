/* Host fixture CLI. Stdout is JSON only; never makes HTTP requests. */
#include "oai.h"
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static char *read_file(const char *path, size_t max, size_t *n)
{
    FILE *f = fopen(path, "rb"); if (!f) return NULL;
    char *b = malloc(max + 1); if (!b) { fclose(f); return NULL; }
    *n = fread(b, 1, max + 1, f); int bad = ferror(f) || *n > max; fclose(f);
    if (bad) { free(b); return NULL; } b[*n] = 0; return b;
}
static const cJSON *get(const cJSON *j, const char *key) { return cJSON_GetObjectItemCaseSensitive(j, key); }
static int fail(void) { puts("{\"error\":\"bad_args\"}"); return 2; }
static int print(cJSON *j)
{
    char *b = cJSON_PrintUnformatted(j); cJSON_Delete(j); if (!b) return fail(); puts(b); cJSON_free(b); return 0;
}
static int request(const char *path)
{
    size_t n; char *s = read_file(path, OAI_BODY_MAX, &n); if (!s) return fail();
    const char *end; cJSON *j = cJSON_ParseWithLengthOpts(s, n + 1, &end, 1); free(s);
    if (!cJSON_IsObject(j)) { cJSON_Delete(j); return fail(); }
    const cJSON *model = get(j, "model"), *system = get(j, "system"), *user = get(j, "user"), *image = get(j, "jpeg_file"),
                *tokens = get(j, "max_tokens"), *temp = get(j, "temperature"), *choice = get(j, "supports_tool_choice"), *base = get(j, "base_url");
    char endpoint[1100];
    if (!cJSON_IsString(model) || !cJSON_IsString(system) || !cJSON_IsString(user) ||
        !cJSON_IsString(base) || oai_endpoint(base->valuestring, endpoint, sizeof endpoint) ||
        (image && !cJSON_IsString(image)) || (tokens && (!cJSON_IsNumber(tokens) || tokens->valuedouble != tokens->valueint)) ||
        (temp && !cJSON_IsNumber(temp)) || (choice && !cJSON_IsBool(choice))) { cJSON_Delete(j); return fail(); }
    char *tools = get(j, "tools") ? cJSON_PrintUnformatted(get(j, "tools")) : NULL;
    size_t image_len = 0; char *jpeg = image ? read_file(image->valuestring, OAI_IMAGE_MAX, &image_len) : NULL;
    if ((image && !jpeg) || (get(j, "tools") && !tools)) { cJSON_Delete(j); cJSON_free(tools); free(jpeg); return fail(); }
    oai_request_t q = {model->valuestring, system->valuestring, user->valuestring, tools,
                      (const unsigned char *)jpeg, image_len, tokens ? tokens->valueint : 512,
                      choice ? cJSON_IsTrue(choice) : 1, temp ? temp->valuedouble : 0};
    char *body = NULL; int rc = oai_build_body(&q, &body);
    cJSON_Delete(j); cJSON_free(tools); free(jpeg);
    if (rc) return fail();
    puts(body); cJSON_free(body); return 0;
}
static int response(const char *status_arg, const char *path)
{
    char *end; errno = 0; long status = strtol(status_arg, &end, 10);
    if (errno || *end || status < 100 || status > 599) return fail();
    size_t n; char *body = read_file(path, OAI_BODY_MAX, &n); if (!body) return fail();
    oai_response_t r; int rc = oai_parse_response(body, n, (int)status, &r); free(body);
    cJSON *out = cJSON_CreateObject();
    cJSON_AddBoolToObject(out, "ok", rc == 0);
    if (rc) {
        cJSON *e = cJSON_AddObjectToObject(out, "error");
        cJSON_AddStringToObject(e, "kind", oai_error_name(r.error.kind)); cJSON_AddNumberToObject(e, "http_status", r.error.http_status);
        cJSON_AddStringToObject(e, "message", r.error.message); cJSON_AddStringToObject(e, "type", r.error.type); cJSON_AddStringToObject(e, "code", r.error.code);
    } else {
        cJSON_AddStringToObject(out, "finish_reason", r.finish_reason); cJSON_AddStringToObject(out, "content", r.content);
        cJSON *calls = cJSON_AddArrayToObject(out, "tool_calls");
        for (size_t i = 0; i < r.ncalls; i++) {
            cJSON *t = cJSON_CreateObject(); cJSON_AddItemToArray(calls, t);
            cJSON_AddStringToObject(t, "id", r.calls[i].id); cJSON_AddStringToObject(t, "name", r.calls[i].name);
            if (r.calls[i].arguments) { cJSON_AddItemToObject(t, "arguments", r.calls[i].arguments); r.calls[i].arguments = NULL; }
            if (*r.calls[i].error) cJSON_AddStringToObject(t, "error", r.calls[i].error);
        }
    }
    oai_response_free(&r); return print(out);
}
int main(int argc, char **argv)
{
    if (argc == 3 && !strcmp(argv[1], "request")) return request(argv[2]);
    if (argc == 4 && !strcmp(argv[1], "parse")) return response(argv[2], argv[3]);
    if (argc == 3 && !strcmp(argv[1], "header")) {
        size_t n; char *key = read_file(argv[2], OAI_KEY_MAX + 1, &n); char header[OAI_KEY_MAX + 32];
        if (!key) return fail();
        if (n && key[n - 1] == '\n') key[--n] = 0;
        int rc = memchr(key, 0, n) ? -1 : oai_auth_header(key, header, sizeof header);
        memset(key, 0, n); free(key); if (rc) return fail();
        cJSON *out = cJSON_CreateObject(); cJSON_AddStringToObject(out, "authorization", header); memset(header, 0, sizeof header); return print(out);
    }
    return fail();
}
