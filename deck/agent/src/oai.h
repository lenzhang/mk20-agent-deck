/* Offline OpenAI-compatible chat/completions adapter. No transport, files or key storage. */
#ifndef MK20_OAI_H
#define MK20_OAI_H
#include <stddef.h>
#include "cJSON.h"
#define OAI_BODY_MAX (256u * 1024u)
#define OAI_IMAGE_MAX (128u * 1024u)
#define OAI_TEXT_MAX 16384u
#define OAI_CONTENT_MAX 32768u
#define OAI_TOOLS_MAX 16
#define OAI_SCHEMA_MAX 32768u
#define OAI_ARGUMENT_MAX 8192u
#define OAI_KEY_MAX 512u

typedef struct {
    const char *model, *system, *user, *tools_json;
    const unsigned char *jpeg; size_t jpeg_len;
    int max_tokens, supports_tool_choice; double temperature;
} oai_request_t; /* intentionally has no API key field */
typedef enum { OAI_OK, OAI_AUTH, OAI_RATE, OAI_SERVER, OAI_BAD_RESPONSE } oai_error_kind;
typedef struct { oai_error_kind kind; int http_status; char message[513], type[129], code[129]; } oai_error_t;
typedef struct { char id[129], name[65], error[65]; cJSON *arguments; } oai_tool_call_t;
typedef struct {
    char finish_reason[33], content[OAI_CONTENT_MAX + 1];
    size_t ncalls; oai_tool_call_t calls[OAI_TOOLS_MAX]; oai_error_t error;
} oai_response_t;
/* Output strings use cJSON_free. All input C strings must be NUL terminated. */
/* The model path requests required tools; the offline API retains auto. */
/* Body builders return -2 when serialization exceeds OAI_BODY_MAX, -1 for
 * invalid fields or allocation failure. */
int oai_build_body_choice(const oai_request_t *q, const char *choice, char **json);
int oai_build_body(const oai_request_t *q, char **json);
int oai_endpoint(const char *base_url, char *out, size_t cap);
int oai_auth_header(const char *key, char *out, size_t cap); /* Authorization: Bearer ...; never body */
/* Initialize a fresh response for each parse; call oai_response_free before reusing it. */
int oai_parse_response(const char *body, size_t n, int http_status, oai_response_t *out);
int oai_parse_response_limit(const char *body, size_t n, size_t cap, int http_status, oai_response_t *out);
void oai_response_free(oai_response_t *r);
const char *oai_error_name(oai_error_kind kind);
/* Inclusive endpoints: norm 0..1000 maps to pixels 0..extent-1, rounded to nearest. */
int oai_norm_to_pixel(int norm, int extent);
int oai_pixel_to_norm(int pixel, int extent);
#endif
