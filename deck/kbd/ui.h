#ifndef MK20_UI_H
#define MK20_UI_H
#include <stdint.h>
#include "panels.h"
#include "uicfg.h"
enum ui_label {
#define X(id, value) UI_##id,
#include "ui_labels.inc"
#undef X
    UI_LABEL_COUNT
};
extern const char *const UI_LABELS[UI_LABEL_COUNT];
enum ui_route { UI_HOME, UI_LS };
enum ui_tab { UI_NET, UI_PROVIDERS_TAB, UI_MODELS, UI_TESTS, UI_DEVICE };
enum ui_action { UI_DOWN, UI_UP, UI_CW, UI_CCW, UI_PRESS };
enum ui_result { UI_RESULT_NONE, UI_RESULT_OK, UI_RESULT_BLOCKED, UI_RESULT_TIMEOUT, UI_RESULT_AUTH };
typedef int (*ui_apply_fn)(void *, const uicfg_t *);
typedef struct {
    enum ui_route route;
    enum ui_tab tab;
    uicfg_t saved, draft;
    int focus, home_focus, model_start, confirm_net, confirm_back, back_home;
    int editor, cursor; /* 1 Mac host, 2 WiFi host; edit buffer commits with K2 */
    char edit[254];
    int down[2], held[2]; int64_t pressed[2];
    int host_online, key_present, stopped, test_running, latency_ms;
    enum ui_result injected_result, shown_result;
    enum ui_label toast;
    ui_apply_fn apply; void *apply_ctx;
} ui_t;
void ui_init(ui_t *u, const uicfg_t *saved, ui_apply_fn apply, void *ctx);
void ui_key(ui_t *u, int fb); /* fb1..20, press only, no ctl/HID wiring */
void ui_knob(ui_t *u, int knob, enum ui_action action, int64_t now_ms);
void ui_tick(ui_t *u, int64_t now_ms); /* caller supplies monotonic fake/real clock */
int ui_pending(const ui_t *u);
int ui_change_count(const ui_t *u);
/* No I/O, allocation or clocks. Caller owns exactly PANELS[fb-1].w*h pixels. */
int ui_render_panel(const ui_t *u, int fb, uint16_t *pixels, size_t count);
#endif
