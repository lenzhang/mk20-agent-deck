#include "oai.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static int checks, fails;
#define CHECK(c) do { checks++; if (!(c)) { fails++; fprintf(stderr, "FAIL %d: %s\n", __LINE__, #c); } } while (0)
static const cJSON *get(const cJSON *j, const char *key) { return cJSON_GetObjectItemCaseSensitive(j, key); }
static void request_test(void)
{
    const char *tools = "[{\"type\":\"function\",\"function\":{\"name\":\"click\",\"parameters\":{\"type\":\"object\"}}}]";
    oai_request_t q = {"fake", "system", "中文 \"quoted\"", tools, NULL, 0, 512, 1, 0.2}; char *b = NULL;
    CHECK(!oai_build_body(&q, &b) && b); cJSON *j = cJSON_Parse(b);
    CHECK(!strcmp(get(j, "model")->valuestring, "fake")); CHECK(!get(j, "api_key"));
    CHECK(!strcmp(get(j, "tool_choice")->valuestring, "auto")); CHECK(cJSON_GetArraySize(get(j, "messages")) == 2);
    cJSON_Delete(j); cJSON_free(b); q.supports_tool_choice = 0;
    CHECK(!oai_build_body(&q, &b)); j = cJSON_Parse(b); CHECK(!get(j, "tool_choice")); cJSON_Delete(j); cJSON_free(b);
    const unsigned char image[] = {0xff, 0xd8, 0xff, 0xd9}; q.jpeg = image; q.jpeg_len = sizeof image;
    CHECK(!oai_build_body(&q, &b) && strstr(b, "data:image/jpeg;base64,/9j/2Q==")); cJSON_free(b);
    q.jpeg_len = OAI_IMAGE_MAX + 1; CHECK(oai_build_body(&q, &b) && !b); q.jpeg_len = 0;
    q.max_tokens = 0; CHECK(oai_build_body(&q, &b)); q.max_tokens = 512;
    q.temperature = NAN; CHECK(oai_build_body(&q, &b)); q.temperature = 3; CHECK(oai_build_body(&q, &b)); q.temperature = 0;
    q.tools_json = "{}"; CHECK(oai_build_body(&q, &b)); q.tools_json = "[{\"type\":\"function\",\"function\":{\"name\":\"x\"}}]"; CHECK(oai_build_body(&q, &b));
    q.tools_json = "[] trailing"; CHECK(oai_build_body(&q, &b)); q.tools_json = NULL;
    char *large = malloc(OAI_TEXT_MAX + 2); memset(large, 'a', OAI_TEXT_MAX + 1); large[OAI_TEXT_MAX + 1] = 0;
    q.user = large; CHECK(oai_build_body(&q, &b)); free(large);
    char h[600], url[1100]; CHECK(!oai_auth_header("sk-secret", h, sizeof h) && !strcmp(h, "Authorization: Bearer sk-secret"));
    CHECK(oai_auth_header("x\r\nInjected: 1", h, sizeof h)); CHECK(oai_auth_header("", h, sizeof h)); CHECK(oai_auth_header("x", h, 2));
    CHECK(!oai_endpoint("https://example.test/v1///", url, sizeof url) && !strcmp(url, "https://example.test/v1/chat/completions"));
    CHECK(oai_endpoint("file:///x", url, sizeof url)); CHECK(oai_endpoint("https://user:key@example.test", url, sizeof url));
    CHECK(oai_endpoint("https://example.test/\r\nx", url, sizeof url));
}
static void response_test(void)
{
    oai_response_t r; const char *s = "{\"choices\":[{\"finish_reason\":\"tool_calls\",\"message\":{\"content\":null,\"tool_calls\":[{\"id\":\"1\",\"type\":\"function\",\"function\":{\"name\":\"click\",\"arguments\":\"{\\\"x\\\":12}\"}},{\"id\":\"2\",\"type\":\"function\",\"function\":{\"name\":\"bad\",\"arguments\":\"broken\"}},{\"id\":\"3\",\"type\":\"function\",\"function\":{\"name\":\"bad\",\"arguments\":\"[]\"}}]}}]}";
    CHECK(!oai_parse_response(s, strlen(s), 200, &r)); CHECK(r.ncalls == 3 && !*r.content);
    CHECK(cJSON_IsObject(r.calls[0].arguments) && get(r.calls[0].arguments, "x")->valueint == 12);
    CHECK(!strcmp(r.calls[1].error, "invalid_arguments") && !r.calls[1].arguments);
    CHECK(!strcmp(r.calls[2].error, "invalid_arguments")); oai_response_free(&r); CHECK(!r.ncalls);
    s = "{\"choices\":[{\"finish_reason\":\"stop\",\"message\":{\"content\":\"done\"}}]}";
    CHECK(!oai_parse_response(s, strlen(s), 200, &r) && !strcmp(r.content, "done")); oai_response_free(&r);
    const char *bad[] = {"garbage", "{}", "{\"choices\":[]}", "null", "{}junk", "{\"choices\":[{\"message\":{}}]}", "[[]]"};
    for (size_t i = 0; i < sizeof bad / sizeof *bad; i++) { CHECK(oai_parse_response(bad[i], strlen(bad[i]), 200, &r) && r.error.kind == OAI_BAD_RESPONSE); oai_response_free(&r); }
    s = "{\"error\":{\"message\":\"slow down\",\"type\":\"quota\",\"code\":123}}";
    const int statuses[] = {401, 403, 429, 500, 503, 400};
    const oai_error_kind kinds[] = {OAI_AUTH, OAI_AUTH, OAI_RATE, OAI_SERVER, OAI_SERVER, OAI_BAD_RESPONSE};
    for (size_t i = 0; i < sizeof statuses / sizeof *statuses; i++) {
        CHECK(oai_parse_response(s, strlen(s), statuses[i], &r) && r.error.kind == kinds[i]);
        CHECK(!strcmp(r.error.message, "slow down") && !strcmp(r.error.type, "quota") && !strcmp(r.error.code, "123")); oai_response_free(&r);
        CHECK(oai_parse_response("bad", 3, statuses[i], &r) && r.error.kind == kinds[i]); oai_response_free(&r);
    }
    char deep[100]; memset(deep, '[', 40); memset(deep + 40, ']', 40);
    CHECK(oai_parse_response(deep, 80, 200, &r)); oai_response_free(&r);
    CHECK(oai_parse_response("x", OAI_BODY_MAX + 1, 200, &r)); oai_response_free(&r);
}
static void coordinate_test(void)
{
    CHECK(oai_norm_to_pixel(0, 1920) == 0 && oai_norm_to_pixel(1000, 1920) == 1919);
    CHECK(oai_pixel_to_norm(0, 1920) == 0 && oai_pixel_to_norm(1919, 1920) == 1000);
    CHECK(oai_norm_to_pixel(500, 101) == 50 && oai_pixel_to_norm(50, 101) == 500);
    CHECK(oai_norm_to_pixel(1000, 1) == 0 && oai_pixel_to_norm(0, 1) == 0);
    CHECK(oai_norm_to_pixel(-1, 10) == -1 && oai_norm_to_pixel(1001, 10) == -1 && oai_norm_to_pixel(1, 0) == -1);
    CHECK(oai_pixel_to_norm(10, 10) == -1 && oai_pixel_to_norm(-1, 10) == -1 && oai_pixel_to_norm(0, 0) == -1);
    for (int n = 0; n <= 1000; n++) CHECK(oai_pixel_to_norm(oai_norm_to_pixel(n, 1920), 1920) == n);
}
int main(void) { request_test(); response_test(); coordinate_test(); printf("%d checks, %d failures\n", checks, fails); return fails ? 1 : 0; }
