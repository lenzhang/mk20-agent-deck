#include "ui.h"
#include "ui_font.h"
#include <stdio.h>
#include <string.h>
const char *const UI_LABELS[UI_LABEL_COUNT] = {
#define X(id, value) value,
#include "ui_labels.inc"
#undef X
};
static const enum ui_label home[] = {UI_LINK, UI_PROXY, UI_AGENT, UI_FW, UI_SETTINGS,
    UI_AGENT, UI_SESSIONS, UI_MODES, UI_PROFILE, UI_MODEL, UI_USBIP, UI_WIFI, UI_CONFIG,
    UI_KEY, UI_INFO, UI_VOICE, UI_TASK, UI_HOME_TITLE, UI_WEB, UI_STOP};
static const enum ui_label tabs[] = {UI_NETWORK, UI_PROVIDER, UI_MODEL, UI_TEST, UI_INFO};
static const enum ui_label netlabels[] = {UI_MAC, UI_USBWIFI, UI_HOST, UI_PORT, UI_SSID,
    UI_WIFIHOST, UI_WIFIPORT, UI_POLICY, UI_LINK, UI_HELP};
static const enum ui_label testlabels[] = {UI_RUN, UI_RESULT, UI_LATENCY, UI_PROFILE,
    UI_PROXY, UI_PROVIDER, UI_MODEL, UI_CLOCK, UI_KEY};
static const enum ui_label infolabels[] = {UI_BRIGHTNESS, UI_VOLUME, UI_FW, UI_KERNEL,
    UI_USBIP, UI_GD32, UI_POLICY, UI_KBD, UI_AGENTVER, UI_HEALTH};
static const enum ui_label results[] = {UI_UNKNOWN, UI_OK, UI_BLOCKED, UI_TIMEOUT, UI_AUTH};
#define L(id) UI_LABELS[id]
int ui_change_count(const ui_t *u)
{
    const uicfg_t *a = &u->saved, *b = &u->draft;
    return (a->provider != b->provider) + (a->model != b->model) + (a->wifi != b->wifi) +
        (a->proxy_port != b->proxy_port) + (a->brightness != b->brightness) +
        (a->volume != b->volume) + !!strcmp(a->proxy_host,b->proxy_host) + !!strcmp(a->wifi_proxy,b->wifi_proxy);
}
int ui_pending(const ui_t *u) { return ui_change_count(u) != 0; }
static int network_changed(const ui_t *u)
{
    return u->saved.wifi != u->draft.wifi || u->saved.proxy_port != u->draft.proxy_port ||
        strcmp(u->saved.proxy_host, u->draft.proxy_host) || strcmp(u->saved.wifi_proxy, u->draft.wifi_proxy);
}
static void disarm(ui_t *u) { u->confirm_net = u->confirm_back = 0; u->toast = UI_UNKNOWN; }
void ui_init(ui_t *u, const uicfg_t *saved, ui_apply_fn apply, void *ctx)
{
    memset(u, 0, sizeof *u); uicfg_defaults(&u->saved);
    if (saved && uicfg_valid(saved)) u->saved = *saved;
    u->draft = u->saved; u->apply = apply; u->apply_ctx = ctx; u->toast = UI_UNKNOWN;
}
static int item_count(const ui_t *u)
{
    if (u->tab == UI_PROVIDERS_TAB) return (int)UI_PROVIDER_COUNT;
    if (u->tab == UI_MODELS) return (int)UI_PROVIDERS[u->draft.provider].model_count;
    return u->tab == UI_TESTS ? 9 : 10;
}
static void enter(ui_t *u, enum ui_tab tab)
{ disarm(u); u->route = UI_LS; u->tab = tab; u->focus = u->model_start = 0; }
static void leave(ui_t *u, int to_home)
{
    u->editor = 0;
    if (ui_pending(u) && !(u->confirm_back && u->back_home == to_home)) {
        u->confirm_net = 0; u->confirm_back = 1; u->back_home = to_home; u->toast = UI_DISCARD; return;
    }
    u->draft = u->saved; disarm(u); u->route = UI_HOME;
}
static void apply(ui_t *u)
{
    u->confirm_back = 0;
    if (!ui_pending(u)) return;
    if (network_changed(u) && !u->confirm_net) { u->confirm_net = 1; u->toast = UI_CONFIRM; return; }
    u->confirm_net = 0;
    if (!uicfg_valid(&u->draft) || (u->apply && u->apply(u->apply_ctx, &u->draft))) { u->toast = UI_FAILED; return; }
    u->saved = u->draft; u->shown_result = UI_RESULT_NONE; u->toast = UI_APPLIED;
}
static void start_editor(ui_t *u, int wifi)
{
    u->editor = wifi ? 2 : 1; u->cursor = 0;
    if (wifi && *u->draft.wifi_proxy) {
        char host[300];
        memcpy(host,u->draft.wifi_proxy+7,strlen(u->draft.wifi_proxy+7)+1);
        char *p = strrchr(host, ':'); if (p) *p = 0;
        size_t n = strlen(host); if (n >= sizeof u->edit) n = sizeof u->edit-1;
        memcpy(u->edit,host,n); u->edit[n]=0;
    } else snprintf(u->edit, sizeof u->edit, "%s", wifi ? "proxy.local" : u->draft.proxy_host);
    u->toast = UI_EDIT;
}
static int wifi_port(const ui_t *u)
{ const char *c = strrchr(u->draft.wifi_proxy, ':'); int p = 7890; if (c) (void)sscanf(c+1, "%d", &p); return p; }
static void wifi_set(ui_t *u, const char *host, int port)
{ snprintf(u->draft.wifi_proxy, sizeof u->draft.wifi_proxy, "http://%.253s:%d", host, port); }
static void choose(ui_t *u)
{
    disarm(u);
    if (u->tab == UI_NET) {
        if (u->focus < 2) u->draft.wifi = u->focus;
        else if (u->focus == 2 || u->focus == 5) start_editor(u, u->focus == 5);
        else if (u->focus == 4 || u->focus == 9) u->toast = UI_WEB;
    } else if (u->tab == UI_PROVIDERS_TAB) {
        if (u->draft.provider != u->focus) { u->draft.provider = u->focus; u->draft.model = 0; }
    } else if (u->tab == UI_MODELS) u->draft.model = u->focus;
    else if (u->tab == UI_TESTS && u->focus == 0) {
        if (ui_pending(u)) { u->toast = UI_FIRSTAPPLY; return; }
        if (u->test_running) { u->test_running = 0; return; }
        u->shown_result = u->key_present ? u->injected_result : UI_RESULT_NONE;
        u->toast = u->key_present ? UI_FAKETEST : UI_MISSING;
        /* Injected results only: U3 owns asynchronous tests and generations. */
    }
}
void ui_key(ui_t *u, int fb)
{
    if (fb < 1 || fb > 20) return;
    if (fb == 20) { disarm(u); u->stopped = 1; return; }
    if (u->route == UI_HOME) {
        disarm(u); u->home_focus = fb-1;
        if (fb == 5) enter(u, UI_NET);
        else if (fb == 15) enter(u, UI_DEVICE);
        else if (fb == 19) u->toast = UI_WEB;
        else if (fb == 6 || fb == 7 || fb == 8) u->toast = UI_PENDING;
        return;
    }
    if (u->editor) {
        if (fb == 16) { u->editor = 0; disarm(u); }
        return;
    }
    if (fb == 16) { leave(u, 0); return; }
    if (fb == 17) { apply(u); return; }
    disarm(u);
    if (fb <= 5) { u->tab = (enum ui_tab)(fb-1); u->focus = u->model_start = 0; }
    else if (fb <= 15) {
        int i = fb-6 + u->model_start;
        if (i < item_count(u)) { u->focus = i; choose(u); }
    } else if (fb == 18) { u->draft = u->saved; u->toast = UI_UNDO; }
    else if (fb == 19) u->toast = UI_WEB;
}
static int clamp(int n, int lo, int hi) { return n < lo ? lo : n > hi ? hi : n; }
static void rotate(ui_t *u, int knob, int sign)
{
    disarm(u);
    if (u->editor) {
        if (knob == 2) { u->cursor = clamp(u->cursor + sign, 0, (int)strlen(u->edit)); return; }
        static const char alphabet[] = " abcdefghijklmnopqrstuvwxyz0123456789.-";
        size_t len = strlen(u->edit); char *p = strchr(alphabet, u->edit[u->cursor]);
        int i = p && *p ? (int)(p-alphabet) : 0;
        i = (i + sign + (int)sizeof alphabet-1) % ((int)sizeof alphabet-1);
        if (u->cursor < 252) {
            u->edit[u->cursor] = alphabet[i];
            if ((size_t)u->cursor == len) u->edit[u->cursor+1] = 0;
            if (!i) u->edit[u->cursor] = 0;
        }
        u->toast = UI_EDIT; return;
    }
    if (u->route == UI_HOME) { u->home_focus = (u->home_focus + sign + 19) % 19; return; }
    if (knob == 2) {
        u->focus = (u->focus + sign + item_count(u)) % item_count(u);
        u->model_start = u->focus / 10 * 10; return;
    }
    if (u->tab == UI_NET && u->focus == 3) u->draft.proxy_port = clamp(u->draft.proxy_port+sign, 1, 65535);
    if (u->tab == UI_NET && u->focus == 6) {
        char host[300]; int port = clamp(wifi_port(u)+sign, 1, 65535);
        snprintf(host, sizeof host, "%s", *u->draft.wifi_proxy ? u->draft.wifi_proxy+7 : "proxy.local:7890");
        char *p = strrchr(host, ':'); if (p) *p = 0; wifi_set(u, host, port);
    }
    if (u->tab == UI_DEVICE && u->focus == 0) u->draft.brightness = clamp(u->draft.brightness+sign*10, 10, 100);
    if (u->tab == UI_DEVICE && u->focus == 1) u->draft.volume = clamp(u->draft.volume+sign*10, 0, 100);
}
static void short_press(ui_t *u, int knob)
{
    if (u->editor) {
        if (knob == 2) {
            uicfg_t trial = u->draft;
            if (u->editor == 1) snprintf(trial.proxy_host, sizeof trial.proxy_host, "%s", u->edit);
            else snprintf(trial.wifi_proxy, sizeof trial.wifi_proxy, "http://%s:%d", u->edit, wifi_port(u));
            if (uicfg_valid(&trial)) { u->draft = trial; u->editor = 0; disarm(u); }
            else u->toast = UI_FAILED;
        }
        return;
    }
    disarm(u);
    if (knob == 1) u->toast = UI_SUMMARY;
    else if (u->route == UI_HOME) ui_key(u, u->home_focus+1);
    else choose(u);
}
void ui_tick(ui_t *u, int64_t now)
{
    for (int k = 0; k < 2; k++) if (u->down[k] && !u->held[k] && now >= u->pressed[k] && now-u->pressed[k] >= 1000) {
        u->held[k] = 1;
        if (k == 1) { disarm(u); u->stopped = 1; }
        else if (u->route == UI_LS) leave(u, 1);
        else disarm(u);
    }
}
void ui_knob(ui_t *u, int k, enum ui_action a, int64_t now)
{
    if (k < 1 || k > 2) return;
    ui_tick(u, now);
    if (a == UI_DOWN && !u->down[k-1]) { u->down[k-1] = 1; u->held[k-1] = 0; u->pressed[k-1] = now; if (!(k==1 && u->confirm_back && u->back_home)) disarm(u); }
    else if (a == UI_UP && u->down[k-1]) { u->down[k-1] = 0; if (!u->held[k-1]) short_press(u,k); }
    else if (a == UI_CW || a == UI_CCW) rotate(u, k, a == UI_CW ? 1 : -1);
    else if (a == UI_PRESS) short_press(u, k);
}
/* Deterministic integer-only RGB565 rasterizer. */
#define RGB(r,g,b) (uint16_t)((((r)>>3)<<11)|(((g)>>2)<<5)|((b)>>3))
#define BG RGB(14,16,20)
#define FG RGB(235,235,240)
#define GREY RGB(130,138,150)
#define BLUE RGB(55,125,250)
#define GREEN RGB(50,205,125)
#define RED RGB(235,55,65)
static void rect(uint16_t *p, int w, int h, int x, int y, int rw, int rh, uint16_t c)
{ for (int j=y; j<y+rh; j++) for (int i=x; i<x+rw; i++) if (i>=0 && j>=0 && i<w && j<h) p[j*w+i]=c; }
static void face(uint16_t *p, int w, int h, uint16_t border)
{
    for (int y=5; y<h-5; y++) for (int x=5; x<w-5; x++) {
        int dx = x < 15 ? 15-x : x >= w-15 ? x-(w-16) : 0;
        int dy = y < 15 ? 15-y : y >= h-15 ? y-(h-16) : 0;
        if (dx*dx+dy*dy <= 100) p[y*w+x] = border;
    }
    rect(p,w,h,9,16,w-18,h-32,RGB(26,30,38));
    rect(p,w,h,16,9,w-32,h-18,RGB(26,30,38));
}
static void text(uint16_t *p,int w,int h,int x,int y,const char *s,uint16_t c,int max)
{ ui_font_draw(p,w,h,x,y,s,c,max); }
static void item(const ui_t *u, int i, const char **label, char *v, size_t cap, int *chosen, int *ro)
{
    const uicfg_t *c=&u->draft; const ui_provider_t *p=&UI_PROVIDERS[c->provider];
    *label=""; *v=0; *chosen=0; *ro=0;
    if (i >= item_count(u)) return;
    if (u->tab == UI_PROVIDERS_TAB) { *label=UI_PROVIDERS[i].label; *chosen=c->provider==i; snprintf(v,cap,"%s",UI_PROVIDERS[i].supported?UI_PROVIDERS[i].id:L(UI_UNAVAILABLE)); }
    else if (u->tab == UI_MODELS) { *label=p->models[i].id; *chosen=c->model==i; snprintf(v,cap,"vision:%d tools:%d",p->models[i].vision,p->models[i].tools); }
    else if (u->tab == UI_NET) {
        *label=L(netlabels[i]); *ro=(i==4||i>=7);
        if (i<2) { *chosen=c->wifi==i; snprintf(v,cap,"%s",i?"board_wifi":"mac_proxy"); }
        else if (i==2) snprintf(v,cap,"%s",c->proxy_host);
        else if (i==3) snprintf(v,cap,"%d",c->proxy_port);
        else if (i==5) snprintf(v,cap,"%s",*c->wifi_proxy?c->wifi_proxy:L(UI_UNKNOWN));
        else if (i==6) snprintf(v,cap,"%d",wifi_port(u));
        else if (i==7) snprintf(v,cap,"%s",c->wifi?"direct / proxy":"proxy only");
        else snprintf(v,cap,"%s",L(i==9?UI_WEB:UI_UNKNOWN));
    } else if (u->tab == UI_TESTS) {
        *label=L(testlabels[i]); *ro=i!=0;
        switch(i) {
        case 0: snprintf(v,cap,"%s",L(UI_FAKETEST)); break;
        case 1: snprintf(v,cap,"%s",L(results[u->shown_result])); break;
        case 2: snprintf(v,cap,"%d ms",u->latency_ms); break;
        case 3: snprintf(v,cap,"%s",u->saved.wifi?"board_wifi":"mac_proxy"); break;
        case 4: snprintf(v,cap,"%s",u->saved.wifi?u->saved.wifi_proxy:u->saved.proxy_host); break;
        case 5: snprintf(v,cap,"%s",UI_PROVIDERS[u->saved.provider].id); break;
        case 6: snprintf(v,cap,"%s",UI_PROVIDERS[u->saved.provider].models[u->saved.model].id); break;
        case 8: snprintf(v,cap,"%s",L(u->key_present?UI_SET:UI_MISSING)); break;
        default: snprintf(v,cap,"%s",L(UI_UNKNOWN));
        }
    } else {
        *label=L(infolabels[i]); *ro=i>=2;
        if (i==0||i==1) snprintf(v,cap,"%d%% %s",i?c->volume:c->brightness,i?L(UI_UNAVAILABLE):"");
        else if (i==6) snprintf(v,cap,"%s",c->wifi?"board_wifi":"mac_proxy");
        else snprintf(v,cap,"%s",L(UI_UNKNOWN));
    }
}
int ui_render_panel(const ui_t *u, int fb, uint16_t *p, size_t count)
{
    if (!u || fb<1 || fb>PANEL_COUNT || !p) return -1;
    const panel_t *panel=&PANELS[fb-1]; int w=panel->w,h=panel->h;
    if (count < (size_t)w*h) return -1;
    for (int i=0;i<w*h;i++) p[i]=BG;
    const char *label=""; char value[512]=""; int chosen=0,ro=0,focused=0;
    if (fb==21) {
        text(p,w,h,8,4,L(u->route==UI_HOME?UI_HOME_TITLE:tabs[u->tab]),FG,w-16);
        snprintf(value,sizeof value,"%s %s/%s %s",u->draft.wifi?"board_wifi":"mac_proxy",UI_PROVIDERS[u->draft.provider].id,UI_PROVIDERS[u->draft.provider].models[u->draft.model].id,ui_pending(u)?L(UI_DRAFT):"");
        if (ui_pending(u)) {
            size_t len=strlen(value);
            (void)snprintf(value+len,sizeof value-len,": %d",ui_change_count(u));
        }
        text(p,w,h,8,25,value,ui_pending(u)?GREEN:GREY,w-16);
        if (u->editor) snprintf(value,sizeof value,"%s [%d]",u->edit,u->cursor);
        else if (u->route==UI_LS && u->toast==UI_UNKNOWN) { int c,r; item(u,u->focus,&label,value,sizeof value,&c,&r); }
        else snprintf(value,sizeof value,"%s",L(u->stopped?UI_STOP:u->toast));
        text(p,w,h,8,49,value,u->stopped?RED:FG,w-16);
        text(p,w,h,8,76,L(UI_VOICESTRIP),GREY,w-16);
        rect(p,w,h,0,112,w,30,RGB(24,28,35));
        /* Each 214px hint wraps into exactly two bitmap lines. */
        text(p,w,h,4,112,"K1 ⟲⟳ 调整",FG,206); text(p,w,h,4,126,"PTT: agent (L5)",GREY,206);
        text(p,w,h,218,112,"K2 ⟲⟳ 选择",FG,206); text(p,w,h,218,126,"recording: false",GREY,206);
        return 0;
    }
    if (fb==20) { label=L(UI_STOP); snprintf(value,sizeof value,"%s",u->stopped?"STOPPED":"E-STOP"); chosen=1; }
    else if (u->route==UI_HOME) {
        label=L(home[fb-1]); ro=!(fb==5||fb==6||fb==7||fb==8||fb==15||fb==19);
        focused=u->home_focus==fb-1; chosen=fb==18;
        if (fb==1) snprintf(value,sizeof value,"%s",L(u->host_online?UI_ONLINE:UI_OFFLINE));
        else if (fb==3) snprintf(value,sizeof value,"%s",L(UI_DISABLED));
        else if (fb==9) snprintf(value,sizeof value,"%s",u->saved.wifi?"board_wifi":"mac_proxy");
        else if (fb==10) snprintf(value,sizeof value,"%s/%s",UI_PROVIDERS[u->saved.provider].id,UI_PROVIDERS[u->saved.provider].models[u->saved.model].id);
        else if (fb==14) snprintf(value,sizeof value,"%s",L(u->key_present?UI_SET:UI_MISSING));
        else if (fb==16) snprintf(value,sizeof value,"%s","agent (L5)");
        else snprintf(value,sizeof value,"%s",L(UI_UNKNOWN));
    } else if (fb<=5) { label=L(tabs[fb-1]); chosen=fb-1==(int)u->tab; }
    else if (fb<=15) { int i=fb-6+u->model_start; item(u,i,&label,value,sizeof value,&chosen,&ro); focused=i==u->focus; }
    else { const enum ui_label bottom[]={UI_BACK,UI_APPLY,UI_UNDO,UI_WEB}; label=L(bottom[fb-16]);
        if (fb==17 && ui_pending(u)) { snprintf(value,sizeof value,"%s",L(u->confirm_net?UI_CONFIRM:UI_DRAFT)); chosen=1; }
        if (fb==16 && u->confirm_back) snprintf(value,sizeof value,"%s",L(UI_DISCARD)); }
    face(p,w,h,fb==20?RED:focused?BLUE:chosen?GREEN:RGB(48,54,65));
    text(p,w,h,13,30,label,fb==20?RED:ro?GREY:FG,w-26);
    text(p,w,h,13,64,value,ro?GREY:FG,w-26);
    if (chosen && fb!=20) text(p,w,h,94,94,L(UI_SELECTED),GREEN,22);
    return 0;
}
