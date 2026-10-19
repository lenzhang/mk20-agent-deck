#include "loop.h"
#include <inttypes.h>
#include <stdio.h>
#include <string.h>
const char *loop_state_name(loop_state_t s)
{
    static const char *n[] = {"disabled","idle","observing","planning","acting","awaiting_confirm","done","stopped","error"};
    return s >= S_DISABLED && s <= S_ERROR ? n[s] : "error";
}
void arbitration_probe(arbitration_t *a, int healthy)
{
    if (healthy) { a->failures = 0; if (a->healthy < 3) a->healthy++; }
    else { a->healthy = 0; if (a->failures < 2) a->failures++; if (a->state == INPUT_HID && a->failures == 2) a->state = INPUT_FROZEN; }
}
int arbitration_wants_hid(const arbitration_t *a, int idle) { return idle && a->state == INPUT_VNC && a->healthy >= 3; }
static int active(const agent_loop_t *l) { return l->state >= S_OBSERVING && l->state <= S_AWAITING_CONFIRM; }
static void finish(agent_loop_t *l, loop_state_t s, const char *why, int emergency)
{
    int was_busy = l->busy; l->paused = 0;
    l->state = s; snprintf(l->reason, sizeof l->reason, "%s", why); l->busy = 0;
    /* Ordinary task completion preserves keyboard permission for a new task. */
    if (emergency) { l->allowed = 0; l->cancelled = 1; l->stop_epoch++; }
    if ((emergency || was_busy) && l->ports.cancel) l->ports.cancel(l->ports.ctx);
    /* After e-stop, kbd owns all-up/revocation. Send no release or input. */
    if (l->leased && !emergency && l->ports.lease) (void)l->ports.lease(l->ports.ctx, LEASE_RELEASE);
    l->leased = 0;
    if (!emergency && l->allowed && l->ports.ui &&
        l->ports.ui(l->ports.ctx,loop_state_name(s),l->input.state == INPUT_VNC,s == S_DONE ? 100 : l->steps * 100 / l->policy.max_steps)) {
        l->state = S_STOPPED; l->allowed = 0; l->cancelled = 1;
        strcpy(l->reason,"kbd_lost"); if (l->ports.cancel) l->ports.cancel(l->ports.ctx);
    }
}
void loop_init(agent_loop_t *l, const policy_config_t *c, const loop_ports_t *p)
{
    memset(l, 0, sizeof *l); if (c) l->policy = *c; else policy_defaults(&l->policy);
    l->ports = *p; l->state = S_DISABLED;
    if (!c) {
        int warning; (void)policy_load(NULL,&l->policy,&warning);
        if (warning && l->ports.log) {
            char line[128]; agent_redact("policy_invalid: using confirmation defaults",p->secrets,p->secret_count,line,sizeof line);
            l->ports.log(p->ctx,line);
        }
    }
}
int loop_submit(agent_loop_t *l)
{
    if (!l->allowed || l->cancelled || (l->state != S_IDLE && l->state != S_DONE && l->state != S_STOPPED && l->state != S_ERROR)) return -1;
    l->direct = l->direct_epoch = 0;
    l->steps = l->model_calls = l->retries = l->same_hash = 0;
    l->busy = l->cancelled = l->confirmed = l->ask_user = l->retry_wait = 0;
    l->history[0] = l->previous_title[0] = l->previous_app[0] = l->reason[0] = 0;
    l->start = l->last_now = l->ports.now(l->ports.ctx); l->state = S_OBSERVING; return 0;
}
void loop_stop(agent_loop_t *l)
{
    finish(l, S_STOPPED, "estop", 1);
    if (l->ports.lease) (void)l->ports.lease(l->ports.ctx, LEASE_ESTOP);
}
int loop_service(void *v)
{
    agent_loop_t *l = v; kbd_event_t e; int rc = 0;
    if (l->cooperate) l->cooperate(l->cooperate_ctx);
    /* Bound hostile event floods; a non-draining peer cannot allow an act. */
    unsigned count = 0; int confirmation = -1;
    while (l->ports.poll && (rc = l->ports.poll(l->ports.ctx, &e)) > 0) {
        if (++count > 64) { finish(l, S_STOPPED, "event_flood", 1); return -1; }
        if (e.kind == K_ESTOP || e.kind == K_LEASE_LOST) { finish(l, S_STOPPED, e.kind == K_ESTOP ? "estop" : "lease_lost", 1); return -1; }
        if (e.kind == K_ALLOW && (l->state == S_DISABLED || l->state == S_STOPPED || l->state == S_ERROR)) { l->allowed = 1; l->cancelled = 0; l->direct_epoch = 0; l->state = S_IDLE; }
        if (e.kind == K_HID_HEALTH) arbitration_probe(&l->input, e.ok);
        if (e.kind == K_CONFIRM && l->state == S_AWAITING_CONFIRM && e.cid == l->cid) {
            confirmation = e.ok;
        }
    }
    if (rc < 0) { finish(l, S_STOPPED, "kbd_lost", 1); return -1; }
    if (l->cancelled) return -1;
    if (confirmation >= 0) {
        if (!confirmation || l->ports.now(l->ports.ctx) >= l->confirm_until) { finish(l,S_STOPPED,l->direct && l->ports.now(l->ports.ctx) >= l->confirm_until ? "confirm_timeout" : "confirm_denied",0); return -1; }
        l->confirmed = 1;
    }
    /* A port may check again after finish: terminal states must keep aborting.
     * Idle/disabled servicing outside a task still permits callers to wait. */
    if (!active(l)) return l->state == S_IDLE || l->state == S_DISABLED ? 0 : -1;
    int64_t now = l->ports.now(l->ports.ctx);
    if (now < l->last_now) { finish(l, S_ERROR, "clock", 0); return -1; } l->last_now = now;
    if (now - l->start >= l->policy.wall_ms) { finish(l, S_ERROR, "wall_budget", 0); return -1; }
    if (l->state == S_ACTING && l->busy && now - l->action_start >= l->policy.exec_ms) { finish(l, S_ERROR, "action_timeout", 0); return -1; }
    if (l->leased && now >= l->renew_at) {
        if (!l->ports.lease || l->ports.lease(l->ports.ctx, LEASE_RENEW)) { finish(l, S_STOPPED, "lease_lost", 1); return -1; }
        l->renew_at = now + 1000;
    }
    return 0;
}
static int switch_input(agent_loop_t *l)
{
    if (l->input.state != INPUT_FROZEN && !arbitration_wants_hid(&l->input, 1)) return 0;
    if (l->frame.locked && l->input.state == INPUT_FROZEN) { finish(l, S_STOPPED, "lock_screen", 0); return -1; }
    if (!l->leased) {
        if (!l->ports.lease || l->ports.lease(l->ports.ctx,LEASE_HID)) { finish(l,S_STOPPED,"lease_lost",1); return -1; }
        l->leased = 1; l->renew_at = l->ports.now(l->ports.ctx) + 1000;
    }
    if (!l->ports.lease || l->ports.lease(l->ports.ctx, LEASE_ALLUP)) { finish(l, S_STOPPED, "allup_failed", 1); return -1; }
    lease_op_t op = l->input.state == INPUT_FROZEN ? LEASE_VNC : LEASE_HID;
    if (l->ports.lease(l->ports.ctx, op)) { finish(l, S_STOPPED, "input_unsupported", 0); return -1; }
    l->input.state = op == LEASE_VNC ? INPUT_VNC : INPUT_HID; l->input.failures = l->input.healthy = 0;
    l->leased = 1; l->renew_at = l->ports.now(l->ports.ctx) + 1000; return 0;
}
static void record(agent_loop_t *l, int fault)
{
    char raw[1024], safe[1024]; const action_t *a = &l->reply.action;
    /* Quote model id with JSON; no text, target, window, task, or provider response is logged. */
    cJSON *m = cJSON_CreateString(l->reply.model); char *model = cJSON_PrintUnformatted(m);
    snprintf(raw, sizeof raw, "{\"step\":%u,\"action\":\"%s\",\"coords\":[%d,%d,%d,%d],\"model\":%s,\"latency_ms\":%" PRId64 ",\"input_tokens\":%u,\"output_tokens\":%u,\"text_len\":%zu,\"text_hash\":\"%016" PRIx64 "\",\"hid_fault\":%s}",
        l->steps + 1, action_name(a->kind), a->x1,a->y1,a->x2,a->y2,model ? model : "null",
        l->ports.now(l->ports.ctx) - l->model_start, l->reply.input_tokens,l->reply.output_tokens,
        strlen(a->text),agent_hash(a->text,strlen(a->text)),fault ? "true" : "false");
    cJSON_free(model); cJSON_Delete(m);
    agent_redact(raw,l->ports.secrets,l->ports.secret_count,safe,sizeof safe);
    if (l->ports.log) l->ports.log(l->ports.ctx,safe);
    char line[120]; snprintf(line,sizeof line,"step %u: %s%s; ",++l->steps,action_name(a->kind),fault ? " failed" : "");
    size_t n = strlen(l->history), add = strlen(line);
    if (n + add >= sizeof l->history) { memmove(l->history,l->history + add,n - add + 1); n -= add; }
    memcpy(l->history + n,line,add + 1);
    strcpy(l->previous_title,l->frame.title); strcpy(l->previous_app,l->frame.app);
}
static void validate_action(agent_loop_t *l)
{
    policy_context_t x = {l->steps,l->frame.locked,l->frame.secure_input,l->input.state == INPUT_VNC,
        l->frame.title,l->frame.app,l->previous_title,l->previous_app,l->frame.width,l->frame.height};
    const char *reason; policy_result_t decision = policy_check(&l->policy,&x,&l->reply.action,&reason);
    if (decision == P_REJECT || decision == P_UNSUPPORTED) { finish(l,S_ERROR,reason,0); return; }
    l->confirmed = 0; l->state = S_ACTING;
    if (decision == P_CONFIRM) {
        l->state = S_AWAITING_CONFIRM; l->confirm_until = l->ports.now(l->ports.ctx) + l->policy.confirm_ms;
        /* Fixed bounded summary excludes model text and secrets. */
        if (!l->ports.confirm || l->ports.confirm(l->ports.ctx,++l->cid,"Confirm next agent action",l->policy.confirm_ms)) finish(l,S_STOPPED,"confirm_denied",0);
    }
}
int loop_direct(agent_loop_t *l, const action_t *a, const loop_frame_t *frame)
{
    (void)loop_service(l);
    if (!l->allowed || l->cancelled || active(l)) return -1;
    if (!l->direct_epoch) {
        l->steps = l->model_calls = l->same_hash = 0;
        l->start = l->last_now = l->ports.now(l->ports.ctx);
        l->previous_title[0] = l->previous_app[0] = l->history[0] = 0;
        l->direct_epoch = 1;
    }
    l->direct = 1; l->busy = l->confirmed = 0; l->reason[0] = 0;
    l->frame = *frame; memset(&l->reply,0,sizeof l->reply); l->reply.action = *a;
    l->model_start = l->ports.now(l->ports.ctx); l->state = S_PLANNING;
    if (loop_service(l)) return 0;
    if (l->steps >= l->policy.max_steps) { finish(l,S_ERROR,"step_budget",0); return 0; }
    if (switch_input(l)) return 0;
    validate_action(l); return 0;
}
void loop_tick(agent_loop_t *l)
{
    if (loop_service(l) || !active(l)) return;
    int64_t now = l->ports.now(l->ports.ctx); int rc;
    if (l->ports.ui && l->ports.ui(l->ports.ctx,loop_state_name(l->state),l->input.state == INPUT_VNC,l->steps * 100 / l->policy.max_steps)) {
        finish(l,S_STOPPED,"kbd_lost",1); return;
    }
    if (loop_service(l) || !active(l)) return;
    if (l->paused && (l->state == S_OBSERVING || (l->state == S_ACTING && !l->busy))) return;
    switch (l->state) {
    case S_OBSERVING:
        if (l->steps >= l->policy.max_steps) { finish(l,S_ERROR,"step_budget",0); return; }
        if (switch_input(l)) return;
        rc = l->ports.observe(l->ports.ctx,&l->frame,loop_service,l);
        if (loop_service(l) || !active(l)) return;
        if (rc == 1) return;
        if (rc || l->frame.width < 1 || l->frame.height < 1 || l->frame.jpeg_len > OAI_IMAGE_MAX || (l->frame.jpeg_len && !l->frame.jpeg)) { finish(l,S_ERROR,"observe",0); return; }
        if (l->frame_ready) l->frame_ready(l->frame_ctx,&l->frame,l->steps+1);
        l->frame.title[sizeof l->frame.title - 1] = l->frame.app[sizeof l->frame.app - 1] = 0;
        if (l->same_hash && l->frame.hash == l->previous_hash) l->same_hash++; else l->same_hash = 1;
        l->previous_hash = l->frame.hash;
        if (l->same_hash >= 3) { l->ask_user = 1; finish(l,S_STOPPED,"no_progress",0); return; }
        l->busy = l->retries = l->retry_wait = 0; l->state = S_PLANNING; return;
    case S_PLANNING:
        if (l->retry_wait && now < l->retry_at) return;
        l->retry_wait = 0;
        if (!l->busy) {
            if (l->model_calls >= l->policy.model_calls) { finish(l,S_ERROR,"model_budget",0); return; }
            l->model_calls++; l->busy = 1; l->model_start = now; memset(&l->reply,0,sizeof l->reply);
        }
        rc = l->ports.model(l->ports.ctx,&l->frame,l->history,&l->reply,loop_service,l);
        if (loop_service(l) || !active(l)) return;
        if (rc == 1) return;
        l->busy = 0;
        if (rc) {
            if (l->reply.retryable && llm_error_retryable(l->reply.error) && l->retries < 2) {
                l->retry_at = l->ports.now(l->ports.ctx) + (500 << l->retries++); l->retry_wait = 1; return;
            }
            finish(l,S_ERROR,"model",0); return;
        }
        l->reply.model[128] = 0;
        validate_action(l);
        return;
    case S_AWAITING_CONFIRM:
        if (now >= l->confirm_until) { finish(l,S_STOPPED,l->direct ? "confirm_timeout" : "confirm_denied",0); return; }
        if (l->confirmed) l->state = S_ACTING;
        return;
    case S_ACTING:
        if (l->reply.action.kind == A_DONE || l->reply.action.kind == A_ASK_USER) {
            l->ask_user = l->reply.action.kind == A_ASK_USER; record(l,0);
            finish(l,l->ask_user ? S_STOPPED : S_DONE,l->ask_user ? "ask_user" : "done",0); return;
        }
        if (!l->busy) {
            if (!l->leased) {
                if (!l->ports.lease || l->ports.lease(l->ports.ctx,l->input.state == INPUT_VNC ? LEASE_VNC : LEASE_HID)) { finish(l,S_STOPPED,"lease_lost",1); return; }
                l->leased = 1; l->renew_at = now + 1000;
            }
            l->action_start = now; l->busy = 1;
        }
        if (loop_service(l) || !active(l)) return;
        rc = l->ports.execute(l->ports.ctx,&l->reply.action,l->input.state == INPUT_VNC,l->action_start + l->policy.exec_ms,loop_service,l);
        if (loop_service(l) || !active(l)) return;
        if (rc == 1) return;
        l->busy = 0;
        if (rc && rc != -2) { finish(l,S_ERROR,rc == -3 ? "unsupported" : "action",0); return; }
        if (rc == -2) arbitration_probe(&l->input,0);
        record(l,rc == -2);
        if (l->direct) finish(l,S_DONE,rc == -2 ? "action" : "done",0);
        else l->state = S_OBSERVING;
        return;
    default: return;
    }
}
