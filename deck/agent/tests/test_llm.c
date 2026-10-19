#define _POSIX_C_SOURCE 200809L
#include "llm.h"
#include "json_util.h"
#include <arpa/inet.h>
#include <netinet/in.h>
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

static unsigned checks;
#define CHECK(x) do { checks++; if (!(x)) { fprintf(stderr, "line %d: %s\n", __LINE__, #x); abort(); } } while (0)
static const char *provider = "{\"version\":1,\"provider\":\"arbitrary\",\"model\":\"test\",\"base_url\":\"https://localhost/api/coding/paas/v4\",\"domestic\":true,\"route\":\"domestic\",\"api_key_file\":\"/tmp/no-key\"}";
static const char *tools = "[{\"type\":\"function\",\"function\":{\"name\":\"click\",\"parameters\":{\"type\":\"object\",\"properties\":{\"x\":{\"type\":\"integer\"},\"y\":{\"type\":\"integer\"}}}}}]";
static llm_provider_t config(void)
{
    llm_provider_t c; CHECK(!llm_provider_parse(provider, strlen(provider), &c)); return c;
}
static char *grow_path;
static ssize_t growing_read(int fd, void *buf, size_t n)
{
    int writer = open(grow_path, O_WRONLY); CHECK(writer >= 0);
    CHECK(!ftruncate(writer, LLM_PROVIDER_MAX + 1));
    CHECK(lseek(writer,(off_t)strlen(provider),SEEK_SET) == (off_t)strlen(provider));
    char spaces[1024]; memset(spaces,' ',sizeof spaces);
    size_t left = LLM_PROVIDER_MAX + 1 - strlen(provider);
    while (left) { size_t part = left < sizeof spaces ? left : sizeof spaces; CHECK(write(writer,spaces,part) == (ssize_t)part); left -= part; }
    close(writer);
    return read(fd, buf, n);
}
static void configs(void)
{
    llm_provider_t c = config(); CHECK(c.max_tokens == 512 && c.timeout_ms == 60000 && !c.pixel_coords && c.vision && c.supports_tool_choice);
    cJSON *j = cJSON_Parse(provider);
    const char *fields[] = {"version", "provider", "model", "base_url", "domestic", "route", "api_key_file"};
    for (size_t i = 0; i < sizeof fields / sizeof *fields; i++) {
        cJSON *copy = cJSON_Duplicate(j, 1); cJSON_DeleteItemFromObjectCaseSensitive(copy, fields[i]); char *s = cJSON_PrintUnformatted(copy);
        CHECK(llm_provider_parse(s, strlen(s), &c) && !c.valid); free(s); cJSON_Delete(copy);
    }
    const char *bad[] = {"", "{}", "null", "[]", "{} trailing", "{\"version\":01}", "{\"version\":1,\"version\":1}", "{\"x\":\"\\u0000\"}", "{\"x\":\"\xff\"}"};
    for (size_t i = 0; i < sizeof bad / sizeof *bad; i++) CHECK(llm_provider_parse(bad[i], strlen(bad[i]), &c));
    struct { const char *key, *json; } changes[] = {
        {"version","2"}, {"version","1.1"}, {"version","\"1\""}, {"provider","\"\""}, {"model","false"},
        {"base_url","\"http://localhost/v1\""}, {"base_url","\"https://user:pass@localhost/v1\""},
        {"base_url","\"https://localhost/v1?key=x\""}, {"base_url","\"https://localhost/v1#x\""},
        {"base_url","\"https://localhost/\\r\\nX\""}, {"route","\"direct\""}, {"domestic","1"},
        {"api_key_file","null"}, {"api_key_file","\"\""}, {"coord_mode","\"normalized\""},
        {"coord_mode","false"}, {"max_tokens","0"}, {"max_tokens","32769"}, {"max_tokens","1.1"},
        {"temperature","-1"}, {"temperature","2.01"}, {"temperature","1e999"}, {"timeout_ms","0"},
        {"timeout_ms","600001"}, {"timeout_ms","\"50\""}, {"supports_tool_choice","1"}, {"vision","null"}
    };
    for (size_t i = 0; i < sizeof changes / sizeof *changes; i++) {
        cJSON *copy = cJSON_Duplicate(j, 1), *v = cJSON_Parse(changes[i].json);
        cJSON_DeleteItemFromObjectCaseSensitive(copy, changes[i].key); cJSON_AddItemToObject(copy, changes[i].key, v);
        char *s = cJSON_PrintUnformatted(copy); CHECK(llm_provider_parse(s, strlen(s), &c) && !c.valid);
        free(s); cJSON_Delete(copy);
    }
    const char *long_fields[] = {"provider", "model", "base_url", "api_key_file"};
    const size_t lengths[] = {129, 129, 1025, 4096};
    for (size_t i = 0; i < sizeof lengths / sizeof *lengths; i++) {
        char *large = malloc(lengths[i] + 1); memset(large, 'x', lengths[i]); large[lengths[i]] = 0;
        cJSON *copy = cJSON_Duplicate(j, 1); cJSON_DeleteItemFromObjectCaseSensitive(copy, long_fields[i]);
        cJSON_AddStringToObject(copy, long_fields[i], large); char *raw = cJSON_PrintUnformatted(copy);
        CHECK(llm_provider_parse(raw, strlen(raw), &c) && !c.valid); free(raw); free(large); cJSON_Delete(copy);
    }
    cJSON_AddStringToObject(j, "coord_mode", "pixel"); cJSON_AddNumberToObject(j, "max_tokens", 100);
    cJSON_AddNumberToObject(j, "temperature", 0.3); cJSON_AddNumberToObject(j, "timeout_ms", 123);
    cJSON_AddBoolToObject(j, "supports_tool_choice", 0); cJSON_AddBoolToObject(j, "vision", 0);
    cJSON_AddStringToObject(j, "unknown", "ignored"); char *s = cJSON_PrintUnformatted(j);
    CHECK(!llm_provider_parse(s, strlen(s), &c) && c.pixel_coords && c.max_tokens == 100 && c.temperature == 0.3 && c.timeout_ms == 123 && !c.vision && !c.supports_tool_choice);
    free(s); cJSON_Delete(j);
    CHECK(llm_provider_parse("{}\0x", 4, &c));
    char *big = malloc(LLM_PROVIDER_MAX + 1); memset(big, ' ', LLM_PROVIDER_MAX + 1);
    CHECK(llm_provider_parse(big, LLM_PROVIDER_MAX + 1, &c)); free(big);
    char path[] = "/tmp/agent-a4-provider-XXXXXX"; int fd = mkstemp(path); CHECK(fd >= 0);
    CHECK(write(fd, provider, strlen(provider)) == (ssize_t)strlen(provider)); close(fd);
    CHECK(!llm_provider_load(path, &c));
    grow_path = path;
    CHECK(llm_provider_load_reader(path, &c, growing_read) && !c.valid);
    fd = open(path, O_WRONLY); CHECK(fd >= 0); CHECK(!ftruncate(fd, 0)); close(fd);
    CHECK(llm_provider_load(path, &c) && !c.valid);
    fd = open(path, O_WRONLY); CHECK(fd >= 0); CHECK(!ftruncate(fd, LLM_PROVIDER_MAX + 1)); close(fd);
    CHECK(llm_provider_load(path, &c) && !c.valid); unlink(path);
    CHECK(llm_provider_load(path, &c)); CHECK(llm_provider_load(NULL, &c)); CHECK(llm_provider_load("/tmp", &c));
    CHECK(!mkfifo(path, 0600)); CHECK(llm_provider_load(path, &c)); unlink(path);
}
static int decode(llm_provider_t *c, llm_request_t *q, const char *args, llm_response_t *r)
{
    cJSON *root = cJSON_CreateObject(), *choices = cJSON_AddArrayToObject(root, "choices"), *choice = cJSON_CreateObject();
    cJSON_AddItemToArray(choices, choice); cJSON_AddStringToObject(choice, "finish_reason", "tool_calls");
    cJSON *message = cJSON_AddObjectToObject(choice, "message"), *calls = cJSON_AddArrayToObject(message, "tool_calls"), *call = cJSON_CreateObject();
    cJSON_AddItemToArray(calls, call); cJSON_AddStringToObject(call, "id", "id"); cJSON_AddStringToObject(call, "type", "function");
    cJSON *fun = cJSON_AddObjectToObject(call, "function"); cJSON_AddStringToObject(fun, "name", "click"); cJSON_AddStringToObject(fun, "arguments", args);
    char *s = cJSON_PrintUnformatted(root); net_response_t n = {.ok = 1, .http_status = 200, .body = (unsigned char *)s, .bytes = strlen(s)};
    int rc = llm_decode(c, q, &n, r); free(s); cJSON_Delete(root); return rc;
}
static void responses(void)
{
    llm_provider_t c = config(); llm_request_t q = {.user = "go", .tools_json = tools, .image_w = 201, .image_h = 101}; llm_response_t r;
    const char *bad_tools[] = {"{bad", "{}", "null"};
    for (size_t i = 0; i < 3; i++) {
        q.tools_json = bad_tools[i]; CHECK(decode(&c, &q, "{\"x\":0,\"y\":0}", &r));
        CHECK(r.error == LLM_CONFIG && !strcmp(r.reason, "request")); llm_response_free(&r);
    }
    q.tools_json = tools;
    const char *unknown[] = {NULL, "permanent_future", "NETWORK", ""};
    for (size_t i = 0; i < 4; i++) {
        net_response_t n = {.class_name = unknown[i]}; CHECK(llm_decode(&c, &q, &n, &r));
        CHECK(r.error == LLM_NETWORK && !r.retryable); llm_response_free(&r);
    }
    const struct { const char *args; int pixel; llm_error_t error; int x, y; } cases[] = {
        {"{\"x\":500,\"y\":1000}",0,LLM_OK,100,100}, {"{\"x\":-20,\"y\":1020}",0,LLM_OK,0,100},
        {"{\"x\":-21,\"y\":0}",0,LLM_COORD_RANGE,0,0}, {"{\"x\":1021,\"y\":0}",0,LLM_COORD_RANGE,0,0},
        {"{\"x\":200,\"y\":100}",1,LLM_OK,200,100}, {"{\"x\":-4,\"y\":102}",1,LLM_OK,0,100},
        {"{\"x\":205,\"y\":0}",1,LLM_COORD_RANGE,0,0}, {"{\"x\":0,\"y\":103}",1,LLM_COORD_RANGE,0,0},
        {"{\"x\":0.5,\"y\":0}",0,LLM_COORD_TYPE,0,0}, {"{\"x\":\"0\",\"y\":0}",0,LLM_COORD_TYPE,0,0},
        {"{\"x\":0}",0,LLM_COORD_TYPE,0,0}, {"{}",0,LLM_COORD_TYPE,0,0},
        {"{\"x\":0,\"y\":0,\"x2\":1021}",0,LLM_COORD_RANGE,0,0},
        {"{\"x\":0,\"y\":0,\"y2\":true}",0,LLM_COORD_TYPE,0,0},
        {"{\"x\":0,\"y\":0,\"x1\":1001,\"y1\":-1}",0,LLM_OK,0,0},
        {"{\"x\":0,\"x\":1,\"y\":0}",0,LLM_BAD_RESPONSE,0,0}, {"{bad",0,LLM_BAD_RESPONSE,0,0},
        {"{\"x\":1e99,\"y\":0}",0,LLM_COORD_RANGE,0,0}
    };
    for (size_t i = 0; i < sizeof cases / sizeof *cases; i++) {
        c.pixel_coords = cases[i].pixel; int rc = decode(&c, &q, cases[i].args, &r);
        CHECK((rc == 0) == (cases[i].error == LLM_OK)); CHECK(r.error == cases[i].error);
        if (!rc) { CHECK(r.message.ncalls == 1); CHECK(cJSON_GetObjectItem(r.message.calls[0].arguments, "x")->valueint == cases[i].x); CHECK(cJSON_GetObjectItem(r.message.calls[0].arguments, "y")->valueint == cases[i].y); }
        else CHECK(!r.message.ncalls);
        llm_response_free(&r);
    }
    c.pixel_coords = 1; q.image_w = q.image_h = 1;
    CHECK(!decode(&c, &q, "{\"x\":0,\"y\":0}", &r)); llm_response_free(&r);
    CHECK(decode(&c, &q, "{\"x\":1,\"y\":0}", &r) && r.error == LLM_COORD_RANGE); llm_response_free(&r);
    const char *s = "{\"choices\":[{\"finish_reason\":\"stop\",\"message\":{\"content\":\"done\"}}]}";
    net_response_t n = {.ok = 1, .http_status = 200, .body = (unsigned char *)s, .bytes = strlen(s)};
    CHECK(llm_decode(&c, &q, &n, &r) && r.error == LLM_BAD_RESPONSE); llm_response_free(&r);
    q.tools_json = NULL;
    CHECK(!llm_decode(&c, &q, &n, &r)); llm_response_free(&r);
    n.close_delimited = 1;
    CHECK(llm_decode(&c, &q, &n, &r) && r.error == LLM_BAD_RESPONSE && !strcmp(r.reason, "truncated")); llm_response_free(&r);
    n.tls_close_notify = 1; CHECK(!llm_decode(&c, &q, &n, &r)); llm_response_free(&r);
    n.bytes = LLM_BODY_MAX + 1; CHECK(llm_decode(&c, &q, &n, &r) && r.error == LLM_TOO_LARGE); llm_response_free(&r);
    const char *http = "HTTP/1.1 200 OK\r\nConnection: close\r\n\r\nok"; net_response_t parsed;
    CHECK(net_http_parse((const unsigned char *)http, strlen(http), 1, LLM_BODY_MAX, &parsed) == 1);
    CHECK(parsed.close_delimited && !parsed.tls_close_notify); net_response_free(&parsed);
    struct { int status; llm_error_t error; } statuses[] = {{200,LLM_OK},{201,LLM_OK},{401,LLM_AUTH},{403,LLM_AUTH},{429,LLM_RATE},{500,LLM_SERVER},{599,LLM_SERVER},{400,LLM_BAD_REQUEST},{404,LLM_BAD_REQUEST},{422,LLM_BAD_REQUEST},{302,LLM_BAD_RESPONSE}};
    for (size_t i = 0; i < sizeof statuses / sizeof *statuses; i++) CHECK(llm_http_error(statuses[i].status) == statuses[i].error);
    for (int i = LLM_OK; i <= LLM_IO; i++) {
        llm_error_t e = (llm_error_t)i;
        CHECK(llm_error_retryable(e) == (e == LLM_RATE || e == LLM_SERVER || e == LLM_TIMEOUT || e == LLM_NETWORK || e == LLM_CONNECT || e == LLM_DNS || e == LLM_IO));
        CHECK(*llm_error_name(e));
        if (e >= LLM_TIMEOUT) CHECK(llm_net_error(llm_error_name(e)) == e);
    }
    CHECK(llm_net_error("http_parse") == LLM_BAD_RESPONSE); CHECK(llm_net_error(NULL) == LLM_NETWORK);
}
static int pairfd, setter_calls, socket_calls;
static int factory(int a, int b, int c) { (void)a; (void)b; (void)c; socket_calls++; return dup(pairfd); }
static int connector(int fd, const struct sockaddr *a, socklen_t n) { (void)fd; (void)a; (void)n; return 0; }
static int setter(time_t t, void *ctx) { (void)t; (void)ctx; setter_calls++; return 0; }
static void no_clock_set(void)
{
    int fds[2]; CHECK(!socketpair(AF_UNIX, SOCK_STREAM, 0, fds));
    pid_t pid = fork(); CHECK(pid >= 0);
    if (!pid) {
        close(fds[0]); char b[2048]; size_t n = 0;
        while (n < sizeof b - 1) { ssize_t k = read(fds[1], b + n, sizeof b - 1 - n); if (k <= 0) _exit(1); n += (size_t)k; b[n] = 0; if (strstr(b, "\r\n\r\n")) break; }
        const char *reply = "HTTP/1.1 200 OK\r\nDate: Sat, 01 Jan 2050 00:00:00 GMT\r\n\r\n";
        if (write(fds[1], reply, strlen(reply)) != (ssize_t)strlen(reply)) _exit(1);
        read(fds[1], b, sizeof b); close(fds[1]); _exit(0);
    }
    close(fds[1]); pairfd = fds[0];
    llm_provider_t c = config(); char key[] = "/tmp/agent-a4-key-XXXXXX"; int fd = mkstemp(key); CHECK(fd >= 0); CHECK(write(fd, "fake", 4) == 4); close(fd); strcpy(c.api_key_file, key);
    netcfg_t nc; const char *net = "{\"version\":1,\"profile\":\"mac_proxy\",\"routes\":{\"domestic\":\"proxy\",\"foreign\":\"proxy\"},\"proxy\":{\"url\":\"http://127.0.0.1:7890\"}}";
    CHECK(!netcfg_parse(net, strlen(net), &nc));
    char marker[] = "/tmp/agent-a4-marker-XXXXXX"; fd = mkstemp(marker); CHECK(fd >= 0); close(fd); unlink(marker);
    net_options_t o = {.socket_fn = factory, .connect_fn = connector, .clock = {.trust_clock = 1, .set_clock = 1, .setter = setter, .marker_path = marker}};
#ifdef MK20_NET_TEST
    o.connected_fd = -1;
#endif
    llm_request_t q = {.user = "test", .transport = &o}; llm_response_t r;
    char fifo[] = "/tmp/agent-a4-ca-XXXXXX"; fd = mkstemp(fifo); CHECK(fd >= 0); close(fd); unlink(fifo);
    CHECK(!mkfifo(fifo, 0600)); o.ca_file = fifo;
    CHECK(llm_chat(&c, &nc, &q, &r) && r.error == LLM_CONFIG && socket_calls == 0);
    llm_response_free(&r); unlink(fifo); o.ca_file = NULL;
    CHECK(llm_chat(&c, &nc, &q, &r)); CHECK(r.error == LLM_TLS); CHECK(!setter_calls && access(marker, F_OK));
    llm_response_free(&r); netcfg_free(&nc); close(pairfd); unlink(key);
    int status; CHECK(waitpid(pid, &status, 0) == pid && WIFEXITED(status) && !WEXITSTATUS(status));
}
static int dial_count;
static unsigned expected_port;
static int refused_socket(int a, int b, int c)
{
    (void)a; (void)b; (void)c; int fds[2]; CHECK(!socketpair(AF_UNIX, SOCK_STREAM, 0, fds));
    close(fds[1]); dial_count++; return fds[0];
}
static int refused_connect(int fd, const struct sockaddr *a, socklen_t n) { (void)fd; (void)n; CHECK(a->sa_family == AF_INET); CHECK(ntohs(((const struct sockaddr_in *)a)->sin_port) == expected_port); CHECK(((const struct sockaddr_in *)a)->sin_addr.s_addr == htonl(0x7f000001u)); errno = ECONNREFUSED; return -1; }
static int probe(const char *prov, const char *net, const char *ca)
{
    llm_provider_t c; netcfg_t nc; CHECK(!llm_provider_load(prov, &c)); CHECK(!netcfg_load(net, &nc));
    expected_port = nc.proxies[0].port;
    net_options_t o = {.ca_file = ca, .socket_fn = refused_socket, .connect_fn = refused_connect, .clock = {.trust_clock = 1}};
#ifdef MK20_NET_TEST
    o.connected_fd = -1;
#endif
    llm_request_t q = {.user = "go", .transport = &o}; llm_response_t r; int rc = llm_chat(&c, &nc, &q, &r);
    CHECK(rc && r.error == LLM_CONNECT && dial_count == 1);
    printf("{\"class\":\"connect\",\"attempts\":%d}\n", dial_count);
    llm_response_free(&r); netcfg_free(&nc); return 0;
}
int main(int argc, char **argv)
{
    if (argc == 4) return probe(argv[1], argv[2], argv[3]);
    configs(); responses(); no_clock_set(); printf("%u LLM checks passed\n", checks); return 0;
}
