#define _POSIX_C_SOURCE 200809L
#include "llm.h"
#include "json_util.h"
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define PROVIDER_VALID 0x4c4c4d31u
static const cJSON *get(const cJSON *j, const char *k) { return cJSON_GetObjectItemCaseSensitive(j, k); }
static int string(const cJSON *j, char *out, size_t cap)
{
    if (!cJSON_IsString(j) || !*j->valuestring || strlen(j->valuestring) >= cap) return -1;
    for (const unsigned char *p = (const unsigned char *)j->valuestring; *p; p++) if (*p < 32 || *p == 127) return -1;
    strcpy(out, j->valuestring); return 0;
}
static int integer(const cJSON *j, int low, int high, int *v)
{
    if (!cJSON_IsNumber(j) || !isfinite(j->valuedouble) || j->valuedouble < low ||
        j->valuedouble > high || floor(j->valuedouble) != j->valuedouble) return -1;
    *v = (int)j->valuedouble; return 0;
}
int llm_provider_parse(const char *s, size_t n, llm_provider_t *out)
{
    memset(out, 0, sizeof *out);
    llm_provider_t c = {.max_tokens = 512, .timeout_ms = 60000, .supports_tool_choice = 1, .vision = 1};
    cJSON *j = agent_json_parse(s, n, LLM_PROVIDER_MAX); int rc = -1, version;
    net_url_t url; const cJSON *v;
    if (!cJSON_IsObject(j) || integer(get(j, "version"), 1, 1, &version) ||
        string(get(j, "provider"), c.provider, sizeof c.provider) ||
        string(get(j, "model"), c.model, sizeof c.model) ||
        string(get(j, "base_url"), c.base_url, sizeof c.base_url) || net_https_url(c.base_url, &url) ||
        strchr(c.base_url, '?') || string(get(j, "route"), c.route, sizeof c.route) ||
        (strcmp(c.route, "domestic") && strcmp(c.route, "foreign")) ||
        !cJSON_IsBool(get(j, "domestic")) ||
        string(get(j, "api_key_file"), c.api_key_file, sizeof c.api_key_file)) goto done;
    c.domestic = cJSON_IsTrue(get(j, "domestic"));
    v = get(j, "coord_mode");
    if (v) {
        if (!cJSON_IsString(v)) goto done;
        if (!strcmp(v->valuestring, "pixel")) c.pixel_coords = 1;
        else if (strcmp(v->valuestring, "norm1000")) goto done;
    }
    v = get(j, "max_tokens"); if (v && integer(v, 1, 32768, &c.max_tokens)) goto done;
    v = get(j, "timeout_ms"); if (v && integer(v, 1, 600000, &c.timeout_ms)) goto done;
    v = get(j, "temperature");
    if (v) { if (!cJSON_IsNumber(v) || !isfinite(v->valuedouble) || v->valuedouble < 0 || v->valuedouble > 2) goto done; c.temperature = v->valuedouble; }
    v = get(j, "supports_tool_choice"); if (v) { if (!cJSON_IsBool(v)) goto done; c.supports_tool_choice = cJSON_IsTrue(v); }
    v = get(j, "vision"); if (v) { if (!cJSON_IsBool(v)) goto done; c.vision = cJSON_IsTrue(v); }
    c.valid = PROVIDER_VALID; *out = c; rc = 0;
done:
    cJSON_Delete(j); return rc;
}
int llm_provider_load_reader(const char *path, llm_provider_t *out,
                             ssize_t (*reader)(int, void *, size_t))
{
    memset(out, 0, sizeof *out);
    int fd = path ? open(path, O_RDONLY | O_NONBLOCK) : -1;
    if (fd < 0) return -1;
    struct stat st;
    if (fstat(fd, &st) || !S_ISREG(st.st_mode) || st.st_size < 1 || st.st_size > LLM_PROVIDER_MAX) { close(fd); return -1; }
    char *s = malloc(LLM_PROVIDER_MAX + 1); if (!s) { close(fd); return -1; }
    size_t n = 0; int bad = 0;
    while (n <= LLM_PROVIDER_MAX) {
        ssize_t k = (reader ? reader : read)(fd, s + n, LLM_PROVIDER_MAX + 1 - n);
        if (k < 0 && errno == EINTR) continue;
        if (k < 0) { bad = 1; break; }
        if (!k) break;
        n += (size_t)k;
    }
    if (close(fd)) bad = 1;
    int rc = bad ? -1 : llm_provider_parse(s, n, out); free(s); return rc;
}
int llm_provider_load(const char *path, llm_provider_t *out)
{
    return llm_provider_load_reader(path, out, NULL);
}
const char *llm_error_name(llm_error_t e)
{
    switch (e) {
    case LLM_OK: return "ok"; case LLM_AUTH: return "auth"; case LLM_RATE: return "rate";
    case LLM_SERVER: return "server"; case LLM_BAD_REQUEST: return "bad_request";
    case LLM_TIMEOUT: return "timeout"; case LLM_NETWORK: return "network";
    case LLM_PROXY: return "proxy"; case LLM_TLS: return "tls"; case LLM_TLS_VERIFY: return "tls_verify";
    case LLM_CLOCK: return "clock"; case LLM_TOO_LARGE: return "too_large";
    case LLM_BAD_RESPONSE: return "bad_response"; case LLM_BLOCKED: return "blocked";
    case LLM_COORD_RANGE: return "coord_range"; case LLM_COORD_TYPE: return "coord_type";
    case LLM_DNS: return "dns"; case LLM_CONNECT: return "connect"; case LLM_IO: return "io";
    default: return "config";
    }
}
int llm_error_retryable(llm_error_t e)
{
    /* Explicit retryable set; adding an enum never silently permits retries. */
    static const llm_error_t retry[] = {LLM_RATE, LLM_SERVER, LLM_TIMEOUT,
        LLM_NETWORK, LLM_DNS, LLM_CONNECT, LLM_IO};
    for (size_t i = 0; i < sizeof retry / sizeof *retry; i++) if (e == retry[i]) return 1;
    return 0;
}
llm_error_t llm_http_error(int s)
{
    if (s >= 200 && s < 300) return LLM_OK;
    if (s == 401 || s == 403) return LLM_AUTH;
    if (s == 429) return LLM_RATE;
    if (s >= 500 && s <= 599) return LLM_SERVER;
    if (s == 400 || s == 404 || s == 422) return LLM_BAD_REQUEST;
    return LLM_BAD_RESPONSE;
}
static int known_net(const char *s)
{
    static const char *names[] = {"timeout", "network", "proxy", "tls", "tls_verify",
        "clock", "too_large", "bad_response", "blocked", "config", "coord_range",
        "coord_type", "dns", "connect", "io", "http_parse"};
    for (size_t i = 0; s && i < sizeof names / sizeof *names; i++)
        if (!strcmp(s, names[i])) return 1;
    return 0;
}
llm_error_t llm_net_error(const char *s)
{
    if (!known_net(s)) return LLM_NETWORK;
    for (int i = LLM_TIMEOUT; i <= LLM_IO; i++) if (!strcmp(s, llm_error_name((llm_error_t)i))) return (llm_error_t)i;
    return LLM_BAD_RESPONSE; /* http_parse */
}
void llm_response_free(llm_response_t *r)
{
    if (r) { oai_response_free(&r->message); memset(r, 0, sizeof *r); }
}
static int fail(llm_response_t *r, llm_error_t e, const char *reason)
{
    /* No partial/invalid call batch can escape to an executor. No peer error
     * strings (which can echo credentials) are retained on failures. */
    oai_response_free(&r->message); memset(&r->message, 0, sizeof r->message);
    r->error = e; r->reason = reason; r->retryable = llm_error_retryable(e); return -1;
}
static const cJSON *tool_schema(const cJSON *tools, const char *name)
{
    const cJSON *t;
    cJSON_ArrayForEach(t, tools) {
        const cJSON *f = get(t, "function"), *n = get(f, "name");
        if (cJSON_IsString(n) && !strcmp(name, n->valuestring)) return get(f, "parameters");
    }
    return NULL;
}
static llm_error_t coordinates(const llm_provider_t *c, const llm_request_t *q, oai_tool_call_t *call, const cJSON *props)
{
    static const char *names[] = {"x", "y", "x2", "y2", "x1", "y1"};
    for (size_t i = 0; i < sizeof names / sizeof *names; i++) {
        cJSON *v = cJSON_GetObjectItemCaseSensitive(call->arguments, names[i]);
        if (!v && !get(props, names[i])) continue;
        if (!cJSON_IsNumber(v) || !isfinite(v->valuedouble) || floor(v->valuedouble) != v->valuedouble) return LLM_COORD_TYPE;
        int extent = i % 2 ? q->image_h : q->image_w;
        if (extent < 1 || extent > 1000000) return LLM_COORD_RANGE;
        int max = c->pixel_coords ? extent - 1 : 1000;
        /* Inclusive ranges; tolerance is floor(2% of max), never rounded up.
         * Pixel mode with a one-pixel extent permits only zero. */
        int tolerance = max / 50; double d = v->valuedouble;
        if (d < -tolerance || d > (double)max + tolerance) return LLM_COORD_RANGE;
        int value = d < 0 ? 0 : d > max ? max : (int)d;
        if (!c->pixel_coords) value = oai_norm_to_pixel(value, extent);
        cJSON_SetNumberValue(v, value);
    }
    return LLM_OK;
}
int llm_decode(const llm_provider_t *c, const llm_request_t *q, const net_response_t *n, llm_response_t *r)
{
    memset(r, 0, sizeof *r); r->reason = "ok";
    if (!c || c->valid != PROVIDER_VALID || !q || !n) return fail(r, LLM_CONFIG, "request");
    r->http_status = n->http_status;
    if (!n->ok) {
        int rc = fail(r, llm_net_error(n->class_name), "transport");
        if (!known_net(n->class_name)) r->retryable = 0;
        return rc;
    }
    if (n->bytes > LLM_BODY_MAX) return fail(r, LLM_TOO_LARGE, "body_cap");
    if (n->close_delimited && !n->tls_close_notify) return fail(r, LLM_BAD_RESPONSE, "truncated");
    llm_error_t error = llm_http_error(n->http_status);
    if (error != LLM_OK) return fail(r, error, "http_status");
    if (oai_parse_response_limit((const char *)n->body, n->bytes, LLM_BODY_MAX, n->http_status, &r->message)) return fail(r, LLM_BAD_RESPONSE, "response");
    cJSON *tools = q->tools_json ? agent_json_parse(q->tools_json, strlen(q->tools_json), OAI_SCHEMA_MAX) : NULL;
    if (q->tools_json && !cJSON_IsArray(tools)) { cJSON_Delete(tools); return fail(r, LLM_CONFIG, "request"); }
    int required = c->supports_tool_choice && cJSON_GetArraySize(tools);
    if (required && !r->message.ncalls) { cJSON_Delete(tools); return fail(r, LLM_BAD_RESPONSE, "missing_tool_call"); }
    for (size_t i = 0; i < r->message.ncalls; i++) {
        oai_tool_call_t *call = &r->message.calls[i];
        if (*call->error) { cJSON_Delete(tools); return fail(r, LLM_BAD_RESPONSE, "arguments"); }
        const cJSON *schema = tool_schema(tools, call->name);
        if (!schema) { cJSON_Delete(tools); return fail(r, LLM_BAD_RESPONSE, "unknown_tool"); }
        error = coordinates(c, q, call, get(schema, "properties"));
        if (error != LLM_OK) { cJSON_Delete(tools); return fail(r, error, llm_error_name(error)); }
    }
    cJSON_Delete(tools);
    cJSON *root = agent_json_parse((const char *)n->body, n->bytes, LLM_BODY_MAX);
    const cJSON *usage = get(root,"usage"), *in = get(usage,"prompt_tokens"), *out = get(usage,"completion_tokens");
    int value;
    if (in && !integer(in,0,1000000000,&value)) r->input_tokens = (unsigned)value;
    if (out && !integer(out,0,1000000000,&value)) r->output_tokens = (unsigned)value;
    cJSON_Delete(root); return 0;
}
int llm_chat(const llm_provider_t *c, const netcfg_t *nc, const llm_request_t *q, llm_response_t *r)
{
    memset(r, 0, sizeof *r); r->reason = "ok";
    if (!c || c->valid != PROVIDER_VALID || !q || !q->user ||
        (q->jpeg_len && (!c->vision || q->image_w < 1 || q->image_h < 1 || q->image_w > 1000000 || q->image_h > 1000000))) return fail(r, LLM_CONFIG, "request");
    if (q->jpeg_len > OAI_IMAGE_MAX) return fail(r, LLM_TOO_LARGE, "image_cap");
    char endpoint[1100], *body = NULL;
    oai_request_t request = {c->model, q->system ? q->system : "", q->user, q->tools_json,
        q->jpeg, q->jpeg_len, c->max_tokens, c->supports_tool_choice, c->temperature};
    if (oai_endpoint(c->base_url, endpoint, sizeof endpoint)) return fail(r, LLM_CONFIG, "request");
    int built = oai_build_body_choice(&request, "required", &body);
    if (built) return fail(r, built == -2 ? LLM_TOO_LARGE : LLM_CONFIG, "request");
    net_options_t o = {0};
#ifdef MK20_NET_TEST
    o.connected_fd = -1;
#endif
    if (q->transport) o = *q->transport;
    o.clock.set_clock = 0; /* Only the usb0 /time path may change wall time. */
    o.clock.setter = NULL; o.clock.setter_ctx = NULL;
    net_header_t header = {"Content-Type", "application/json"};
    o.url = endpoint; o.method = "POST"; o.key_file = c->api_key_file;
    o.config = nc; o.provider_route = c->route;
    o.headers = &header; o.header_count = 1;
    o.body = (const unsigned char *)body; o.body_len = strlen(body); o.body_cap = LLM_BODY_MAX;
    if (o.timeout_ms <= 0) o.timeout_ms = c->timeout_ms;
    net_response_t net = {0}; net_request(&o, &net);
    cJSON_free(body); int rc = llm_decode(c, q, &net, r); net_response_free(&net); return rc;
}
