#ifndef MK20_POLICY_H
#define MK20_POLICY_H
#include "json_util.h"
#include <stdint.h>
#define POLICY_PATH "/data/mk20/agent/policy.json"
#define POLICY_LIST 32
#define POLICY_TEXT 1025
/* Single action representation, coordinates always screenshot pixels. */
typedef enum { A_CLICK, A_DOUBLE_CLICK, A_MOVE, A_DRAG, A_SCROLL, A_TYPE,
    A_PASTE, A_KEY, A_WAIT, A_DONE, A_ASK_USER, A_COUNT } action_kind_t;
typedef struct {
    action_kind_t kind;
    int x1, y1, x2, y2, dx, dy, ms, high;
    unsigned modifiers, usage;
    char text[POLICY_TEXT], combo[96], target[513];
} action_t;
typedef struct {
    unsigned max_steps, model_calls, wall_ms, exec_ms, confirm_ms;
    int confirm_first, confirm_app_switch;
    size_t nkeys, ntitles;
    char keys[POLICY_LIST][96], titles[POLICY_LIST][128];
} policy_config_t;
typedef struct {
    unsigned step;
    int locked, secure_input, input_vnc;
    const char *title, *app, *previous_title, *previous_app;
    int width, height;
} policy_context_t;
typedef enum { P_ALLOW, P_CONFIRM, P_REJECT, P_UNSUPPORTED } policy_result_t;
void policy_defaults(policy_config_t *c);
/* 0 valid/missing; -1 invalid -> complete defaults, warning=1. */
int policy_load(const char *path, policy_config_t *c, int *warning);
int policy_parse(const char *s, size_t n, policy_config_t *c);
int action_parse(const cJSON *j, action_t *a);
int action_validate(const action_t *a);
int action_key(const char *combo, unsigned *modifiers, unsigned *usage);
const char *action_name(action_kind_t k);
policy_result_t policy_check(const policy_config_t *c, const policy_context_t *ctx,
                              const action_t *a, const char **reason);
uint64_t agent_hash(const void *p, size_t n);
/* Redacts entire line on sensitive markers or a known secret. No raw fallback. */
void agent_redact(const char *line, const char *const *secrets, size_t count,
                  char *out, size_t cap);
#endif
