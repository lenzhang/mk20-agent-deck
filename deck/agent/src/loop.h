#ifndef MK20_LOOP_H
#define MK20_LOOP_H
#include "policy.h"
#include "llm.h"
typedef enum { S_DISABLED, S_IDLE, S_OBSERVING, S_PLANNING, S_ACTING,
    S_AWAITING_CONFIRM, S_DONE, S_STOPPED, S_ERROR } loop_state_t;
typedef enum { INPUT_HID, INPUT_FROZEN, INPUT_VNC } input_state_t;
typedef struct { input_state_t state; unsigned failures, healthy; } arbitration_t;
/* Probe transition is pure. Frozen must complete all-up + VNC lease before commit. */
void arbitration_probe(arbitration_t *a, int healthy);
int arbitration_wants_hid(const arbitration_t *a, int idle);
typedef enum { K_ALLOW, K_ESTOP, K_LEASE_LOST, K_CONFIRM, K_HID_HEALTH } kbd_event_kind_t;
typedef struct { kbd_event_kind_t kind; unsigned cid; int ok; } kbd_event_t;
typedef struct {
    const unsigned char *jpeg; size_t jpeg_len; uint64_t hash;
    int width, height, locked, secure_input;
    int source_width, source_height;
    char title[513], app[129];
} loop_frame_t;
typedef struct {
    action_t action; llm_error_t error; int retryable;
    unsigned input_tokens, output_tokens; char model[129];
} loop_reply_t;
typedef int (*loop_guard_t)(void *ctx);
typedef enum { LEASE_HID, LEASE_VNC, LEASE_RENEW, LEASE_RELEASE, LEASE_ALLUP, LEASE_ESTOP } lease_op_t;
/* Async ports return 1=pending, 0=complete, -1=error. Execute -2=HID fault
 * consumes the action and MUST NOT retry it. Synchronous ports MUST call guard
 * at <=25ms intervals and before every write. Owned frame valid until next observe. */
typedef struct {
    void *ctx;
    int64_t (*now)(void *);
    int (*poll)(void *, kbd_event_t *); /* 1 event, 0 drained, -1 disconnected */
    int (*observe)(void *, loop_frame_t *, loop_guard_t, void *);
    int (*model)(void *, const loop_frame_t *, const char *history, loop_reply_t *, loop_guard_t, void *);
    int (*lease)(void *, lease_op_t);
    int (*execute)(void *, const action_t *, int vnc, int64_t deadline, loop_guard_t, void *);
    int (*confirm)(void *, unsigned cid, const char *summary, unsigned timeout_ms);
    int (*ui)(void *, const char *state, int vnc, unsigned progress);
    void (*cancel)(void *);
    void (*log)(void *, const char *line);
    const char *const *secrets; size_t secret_count;
} loop_ports_t;
typedef struct {
    /* Optional server I/O pump: ports still use this same safety guard. */
    void (*cooperate)(void *); void *cooperate_ctx;
    loop_state_t state; policy_config_t policy; loop_ports_t ports; arbitration_t input;
    int allowed, cancelled, leased, busy, confirmed, ask_user, retry_wait;
    int direct, direct_epoch;
    unsigned steps, model_calls, retries, cid, same_hash;
    int64_t start, action_start, model_start, renew_at, confirm_until, retry_at, last_now;
    uint64_t previous_hash;
    loop_frame_t frame; loop_reply_t reply;
    char reason[40], history[2049], previous_title[513], previous_app[129];
} agent_loop_t;
void loop_init(agent_loop_t *, const policy_config_t *, const loop_ports_t *);
int loop_submit(agent_loop_t *); /* submits only when already allowed by kbd */
/* Uses the same policy/confirmation/execute states without observing or calling
 * the model. Repeated direct calls share budget until a keyboard allow cycle. */
int loop_direct(agent_loop_t *, const action_t *, const loop_frame_t *);
void loop_stop(agent_loop_t *);  /* local e-stop; no enable API exists */
void loop_tick(agent_loop_t *);
/* Nonzero aborts ports after any finish; idle/disabled outside a task return 0. */
int loop_service(void *); /* cooperative cancellation + budget + lease renewal */
const char *loop_state_name(loop_state_t);
#endif
