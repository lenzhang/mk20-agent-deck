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
/* LS is read-only (2026-10-20): the usb0 web page is the single settings entry. */
enum ui_tab { UI_NET, UI_MODELS, UI_KEYS, UI_TESTS, UI_DEVICE, UI_TAB_COUNT };
enum ui_action { UI_DOWN, UI_UP, UI_CW, UI_CCW, UI_PRESS };
enum ui_link { UI_LINK_UNSEEN, UI_LINK_USB_DOWN, UI_LINK_USB_SLEEP, UI_LINK_NOAPP,
               UI_LINK_PROBING, UI_LINK_ONLINE, UI_LINK_STALE };
enum ui_agent_state { UI_AGENT_OFF, UI_AGENT_DISABLED, UI_AGENT_ALLOWED, UI_AGENT_LEASED, UI_AGENT_STOPPED };
enum ui_cfg_state { UI_CONF_DEFAULT, UI_CONF_OK, UI_CONF_INVALID, UI_CONF_APPLYING };
enum ui_key_state { UI_KEYST_UNKNOWN, UI_KEYST_MISSING, UI_KEYST_SET, UI_KEYST_INSECURE };
/* Where the sub-screen footer is drawn; onboard.c sets it for every route. */
enum ui_context { UI_CTX_HOME, UI_CTX_LS, UI_CTX_L5, UI_CTX_HOST, UI_CTX_AGENT };
/* Status snapshot supplied by the caller (onboard.c gathers it with devstat.c).
 * Plain values only; the renderer never reads files, env or clocks. */
typedef struct {
    int link, agent, cfg, key, wlan;  /* enums above; wlan: 0 no interface, 1 present */
    int profile;                      /* -1 default (Mac proxy), 0 mac_proxy, 1 board_wifi, -2 unknown */
    int provider, model;              /* catalogue indices; -1 default, -2 unknown */
    int ip_default, task, confirm;    /* task: ctl ui.state index 0..7, -1 none */
    char fw[24], kbd[24], ip[16], proxy[64], pair[7];
    char route_dom[8], route_for[8];  /* "direct"/"proxy"/"blocked" or "" (default) */
} ui_status_t;
typedef struct {
    enum ui_route route;
    enum ui_tab tab;
    int focus, home_focus, back_home;
    int down[2], held[2]; int64_t pressed[2];
    int host_online, stopped;
    enum ui_label toast;
    ui_status_t st;
    /* footer context, set by the compositor (not by keys) */
    enum ui_context ctx;
    int health_stale, ptt_held, incompatible, stop_banner;
} ui_t;
void ui_init(ui_t *u);
void ui_status_defaults(ui_status_t *s);
void ui_key(ui_t *u, int fb); /* fb1..20, press only, no ctl/HID wiring; never edits configuration */
void ui_knob(ui_t *u, int knob, enum ui_action action, int64_t now_ms);
void ui_tick(ui_t *u, int64_t now_ms); /* caller supplies monotonic fake/real clock */
int ui_item_count(enum ui_tab tab);
/* Full usb0 settings URL, e.g. "http://172.30.250.2:8780". */
void ui_web_url(const ui_t *u, char *out, size_t cap);
/* No I/O, allocation or clocks. Caller owns exactly PANELS[fb-1].w*h pixels. */
int ui_render_panel(const ui_t *u, int fb, uint16_t *pixels, size_t count);
/* Footer rows 112..141 of a 428x142 fb21 buffer for u->ctx; returns 1 if it shows
 * an attention state (pairing code, stop, confirmation, stale Mac, PTT, mapping). */
int ui_footer_attention(const ui_t *u);
void ui_render_footer(const ui_t *u, uint16_t *pixels, int w, int h);
#endif
