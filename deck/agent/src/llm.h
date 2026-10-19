#ifndef MK20_LLM_H
#define MK20_LLM_H
#include "net.h"
#include "oai.h"
#define LLM_PROVIDER_MAX 16384u
#define LLM_BODY_MAX (1024u * 1024u)
typedef enum { LLM_OK, LLM_AUTH, LLM_RATE, LLM_SERVER, LLM_BAD_REQUEST,
    LLM_TIMEOUT, LLM_NETWORK, LLM_PROXY, LLM_TLS, LLM_TLS_VERIFY, LLM_CLOCK,
    LLM_TOO_LARGE, LLM_BAD_RESPONSE, LLM_BLOCKED, LLM_CONFIG,
    LLM_COORD_RANGE, LLM_COORD_TYPE, LLM_DNS, LLM_CONNECT, LLM_IO } llm_error_t;
typedef struct {
    unsigned valid;
    char provider[129], model[129], base_url[1025], route[9], api_key_file[4096];
    int domestic, pixel_coords, max_tokens, timeout_ms, supports_tool_choice, vision;
    double temperature;
} llm_provider_t;
typedef struct {
    const char *system, *user, *tools_json;
    const unsigned char *jpeg;
    size_t jpeg_len;
    int image_w, image_h;
    /* Optional transport settings. URL/method/key/route/body/headers/caps are
     * always replaced by llm_chat. set_clock is always explicitly cleared. */
    const net_options_t *transport;
} llm_request_t;
typedef struct {
    llm_error_t error;
    int retryable, http_status;
    const char *reason; /* Stable literal, e.g. truncated; never peer errors. */
    oai_response_t message;
} llm_response_t;
/* Fresh destinations only. Failed loads leave a zeroed, invalid config. */
int llm_provider_parse(const char *json, size_t len, llm_provider_t *out);
int llm_provider_load(const char *path, llm_provider_t *out);
int llm_chat(const llm_provider_t *cfg, const netcfg_t *netcfg,
             const llm_request_t *request, llm_response_t *response);
/* Factored response handling also permits deterministic offline acceptance. */
int llm_decode(const llm_provider_t *cfg, const llm_request_t *request,
               const net_response_t *net, llm_response_t *response);
llm_error_t llm_http_error(int status);
llm_error_t llm_net_error(const char *class_name);
const char *llm_error_name(llm_error_t error);
int llm_error_retryable(llm_error_t error);
void llm_response_free(llm_response_t *response);
#endif
