#include "runtime.h"
#include "img.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static int64_t now(void *v) { (void)v; return kbdc_now(); }
static int poll_kbd(void *v, kbd_event_t *e) { return kbdc_poll(((runtime_t *)v)->kbd,e); }
static int lease(void *v, lease_op_t op) { return kbdc_lease(((runtime_t *)v)->kbd,op); }
static int confirm(void *v, unsigned cid, const char *s, unsigned timeout) { return kbdc_confirm(((runtime_t *)v)->kbd,cid,s,timeout); }
static int ui(void *v, const char *s, int input, unsigned progress) { return kbdc_ui(((runtime_t *)v)->kbd,s,input,progress); }
static int execute(void *v, const action_t *a, int input, int64_t deadline, loop_guard_t g, void *ctx) { return kbdc_execute(((runtime_t *)v)->kbd,a,input,deadline,g,ctx); }
static void log_line(void *v, const char *line) { runtime_t *r = v; if (r->sink) r->sink(r->sink_ctx,line); }
static int observe(void *v, loop_frame_t *frame, loop_guard_t g, void *ctx)
{
    runtime_t *r = v;
    if (!r->rfb || !r->window_hint || g(ctx)) return -1;
    mem_free(rfb_memory(r->rfb),r->jpeg); r->jpeg = NULL;
    rfb_set_service(r->rfb,g,ctx);
    if (rfb_snapshot(r->rfb,1280,800)) return -1;
    const scale_t *f = rfb_frame(r->rfb);
    memset(frame,0,sizeof *frame); frame->hash = agent_hash(f->data,(size_t)f->w*f->h*3);
    frame->width = (int)f->w; frame->height = (int)f->h;
    if (!r->kbd->calibration.valid || r->kbd->calibration.width != frame->width || r->kbd->calibration.height != frame->height) return -1;
    if (g(ctx) || img_jpeg(rfb_memory(r->rfb),f->data,f->w,f->h,70,&r->jpeg,&frame->jpeg_len) || g(ctx)) return -1;
    frame->jpeg = r->jpeg;
    return r->window_hint(r->hint_ctx,frame);
}
static int model(void *v, const loop_frame_t *f, const char *history, loop_reply_t *out, loop_guard_t g, void *ctx)
{
    runtime_t *r = v; char user[8193];
    if (!r->task || strlen(r->task) > 4096 || snprintf(user,sizeof user,"Task: %s\nHistory: %s",r->task,history) >= (int)sizeof user || g(ctx)) return -1;
    net_options_t transport = r->transport; transport.service = g; transport.service_ctx = ctx;
    llm_request_t q = {.system="Use one action per turn. Report risk low/high and target. Never enter passwords or alter security, sharing, or proxy settings.",
        .user=user,.tools_json=r->tools,.jpeg=f->jpeg,.jpeg_len=f->jpeg_len,.image_w=f->width,.image_h=f->height,.transport=&transport};
    llm_response_t reply; int rc = llm_chat(r->provider,r->net,&q,&reply);
    out->error = reply.error; out->retryable = reply.retryable;
    snprintf(out->model,sizeof out->model,"%s",r->provider->model);
    out->input_tokens = reply.input_tokens; out->output_tokens = reply.output_tokens;
    if (!rc) {
        if (reply.message.ncalls != 1) rc = -1;
        else {
            oai_tool_call_t *call = &reply.message.calls[0];
            if (cJSON_GetObjectItemCaseSensitive(call->arguments,"action")) rc = -1;
            else { cJSON_AddStringToObject(call->arguments,"action",call->name); rc = action_parse(call->arguments,&out->action); }
        }
        if (rc) { out->error = LLM_BAD_RESPONSE; out->retryable = 0; }
    }
    llm_response_free(&reply); return rc;
}
static char *tools(void)
{
    cJSON *array = cJSON_CreateArray();
    for (int k = 0; k < A_COUNT; k++) {
        cJSON *t = cJSON_CreateObject(); cJSON_AddItemToArray(array,t); cJSON_AddStringToObject(t,"type","function");
        cJSON *fn = cJSON_AddObjectToObject(t,"function"); cJSON_AddStringToObject(fn,"name",action_name((action_kind_t)k));
        cJSON *params = cJSON_AddObjectToObject(fn,"parameters"); cJSON_AddStringToObject(params,"type","object");
        cJSON *props = cJSON_AddObjectToObject(params,"properties"), *req = cJSON_AddArrayToObject(params,"required");
        const char *fields[10] = {"risk","target"}; unsigned n = 2;
        if (k <= A_SCROLL) { fields[n++] = k == A_DRAG ? "x1" : "x"; fields[n++] = k == A_DRAG ? "y1" : "y"; }
        if (k == A_DRAG) { fields[n++] = "x2"; fields[n++] = "y2"; }
        if (k == A_SCROLL) { fields[n++] = "dx"; fields[n++] = "dy"; }
        if (k == A_TYPE || k == A_PASTE) fields[n++] = "text";
        if (k == A_KEY) fields[n++] = "combo";
        if (k == A_WAIT) fields[n++] = "ms";
        for (unsigned i = 0; i < n; i++) {
            cJSON *p = cJSON_AddObjectToObject(props,fields[i]);
            int string = i < 2 || !strcmp(fields[i],"text") || !strcmp(fields[i],"combo");
            cJSON_AddStringToObject(p,"type",string ? "string" : "integer");
            if (!i) { cJSON *e = cJSON_AddArrayToObject(p,"enum"); cJSON_AddItemToArray(e,cJSON_CreateString("low")); cJSON_AddItemToArray(e,cJSON_CreateString("high")); }
            cJSON_AddItemToArray(req,cJSON_CreateString(fields[i]));
        }
    }
    char *s = cJSON_PrintUnformatted(array); cJSON_Delete(array); return s;
}
int runtime_ports(runtime_t *r, loop_ports_t *p)
{
    if (!r->kbd || !r->provider || !r->net) return -1;
    r->tools = tools(); if (!r->tools) return -1;
    *p = (loop_ports_t){.ctx=r,.now=now,.poll=poll_kbd,.observe=observe,.model=model,.lease=lease,
        .execute=execute,.confirm=confirm,.ui=ui,.log=log_line,.secrets=r->secrets,.secret_count=r->secret_count}; return 0;
}
void runtime_free(runtime_t *r) { mem_free(rfb_memory(r->rfb),r->jpeg); r->jpeg = NULL; cJSON_free(r->tools); r->tools = NULL; }
