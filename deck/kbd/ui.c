#include "ui.h"
#include "ui_font.h"
#include <stdio.h>
#include <string.h>
const char *const UI_LABELS[UI_LABEL_COUNT] = {
#define X(id, value) value,
#include "ui_labels.inc"
#undef X
};
#define L(id) UI_LABELS[id]
/* Status/detail items shared by HOME tiles and the read-only LS page. */
enum item { IT_NONE, IT_LINK, IT_PROXY, IT_AGENT, IT_FW, IT_PROFILE, IT_MODEL, IT_USBIP, IT_WIFI,
    IT_CONFIG, IT_KEY, IT_VOICE, IT_TASK, IT_WEB, IT_PROXYURL, IT_ROUTE_DOM, IT_ROUTE_FOR,
    IT_PROVIDER, IT_VISION, IT_TOOLS, IT_ADAPTER, IT_BASEURL, IT_KEYFILE, IT_KEYWRITE, IT_KEYREAD,
    IT_TESTRUN, IT_CLOCK, IT_KBD, IT_BRIGHT, IT_VOLUME, IT_KERNEL, IT_GD32, IT_AGENTVER, IT_HEALTH,
    IT_COUNT };
static const enum ui_label item_label[IT_COUNT] = {UI_NONE, UI_LINK, UI_PROXY, UI_AGENT, UI_FW,
    UI_PROFILE, UI_MODEL, UI_USBIP, UI_WIFI, UI_CONFIG, UI_KEY, UI_VOICE, UI_TASK, UI_WEB,
    UI_PROXYURL, UI_ROUTE_DOM, UI_ROUTE_FOR, UI_PROVIDER, UI_VISION, UI_TOOLS, UI_ADAPTER,
    UI_BASEURL, UI_KEYFILE, UI_KEYWRITE, UI_KEYREAD, UI_TESTRUN, UI_CLOCK, UI_KBD, UI_BRIGHTNESS,
    UI_VOLUME, UI_KERNEL, UI_GD32, UI_AGENTVER, UI_HEALTH};
/* HOME positions are unchanged; fb5/6/7/8/15 navigate, fb18 is the current page. */
static const enum ui_label home[] = {UI_LINK, UI_PROXY, UI_AGENT, UI_FW, UI_SETTINGS,
    UI_AGENTPAGE, UI_SESSIONS, UI_MODES, UI_PROFILE, UI_MODEL, UI_USBIP, UI_WIFI, UI_CONFIG,
    UI_KEY, UI_INFO, UI_VOICE, UI_TASK, UI_HOME_TITLE, UI_WEB, UI_STOP};
static const enum item home_item[] = {IT_LINK, IT_PROXY, IT_AGENT, IT_FW, IT_NONE, IT_NONE,
    IT_NONE, IT_NONE, IT_PROFILE, IT_MODEL, IT_USBIP, IT_WIFI, IT_CONFIG, IT_KEY, IT_NONE,
    IT_VOICE, IT_TASK, IT_NONE, IT_WEB, IT_NONE};
static const enum ui_label tabs[UI_TAB_COUNT] = {UI_NETWORK, UI_MODEL, UI_KEYTAB, UI_TEST, UI_INFO};
static const enum item tab_items[UI_TAB_COUNT][10] = {
    {IT_PROFILE, IT_PROXYURL, IT_ROUTE_DOM, IT_ROUTE_FOR, IT_USBIP, IT_WIFI, IT_LINK, IT_PROXY},
    {IT_PROVIDER, IT_MODEL, IT_VISION, IT_TOOLS, IT_ADAPTER, IT_BASEURL, IT_CONFIG},
    {IT_KEY, IT_KEYFILE, IT_KEYWRITE, IT_KEYREAD},
    {IT_TESTRUN, IT_PROXY, IT_CLOCK},
    {IT_FW, IT_KBD, IT_USBIP, IT_BRIGHT, IT_VOLUME, IT_KERNEL, IT_GD32, IT_AGENTVER, IT_HEALTH},
};
static const int item_counts[UI_TAB_COUNT] = {8, 7, 4, 3, 9};
int ui_item_count(enum ui_tab t) { return (int)t >= 0 && t < UI_TAB_COUNT ? item_counts[t] : 0; }
static int home_nav(int fb) { return fb == 5 || fb == 6 || fb == 7 || fb == 8 || fb == 15; }
void ui_status_defaults(ui_status_t *s)
{
    memset(s, 0, sizeof *s);
    s->link = UI_LINK_UNSEEN; s->agent = UI_AGENT_OFF; s->cfg = UI_CONF_DEFAULT; s->key = UI_KEYST_MISSING;
    s->profile = -1; s->provider = -1; s->model = -1; s->ip_default = 1; s->task = -1;
    strcpy(s->ip, "172.30.250.2");
}
void ui_init(ui_t *u)
{
    memset(u, 0, sizeof *u); ui_status_defaults(&u->st); u->toast = UI_NONE;
}
void ui_web_url(const ui_t *u, char *out, size_t cap)
{ snprintf(out, cap, "http://%s:8780", *u->st.ip ? u->st.ip : "172.30.250.2"); }
static void enter(ui_t *u, enum ui_tab tab)
{ u->route = UI_LS; u->tab = tab; u->focus = 0; u->back_home = 0; u->toast = UI_NONE; }
/* The compositor sends ui.openSettings only when the host is ONLINE; the toast says which. */
static void web(ui_t *u) { u->toast = u->host_online ? UI_SUB_WEB_OPENED : UI_SUB_WEB_OFFLINE; }
void ui_key(ui_t *u, int fb)
{
    if (fb < 1 || fb > 20) return;
    u->toast = UI_NONE;
    if (fb == 20) { u->stopped = 1; return; }
    if (u->route == UI_HOME) {
        u->home_focus = fb-1;
        if (fb == 5) enter(u, UI_NET);
        else if (fb == 15) enter(u, UI_DEVICE);
        else if (fb == 19) web(u);
        return;
    }
    /* Read-only LS: keys only choose what to look at; nothing is edited or applied. */
    if (fb <= 5) { u->tab = (enum ui_tab)(fb-1); u->focus = 0; }
    else if (fb <= 15) { if (fb-6 < ui_item_count(u->tab)) u->focus = fb-6; }
    else if (fb == 16 || fb == 18) { u->route = UI_HOME; u->back_home = fb == 18; }
    else if (fb == 19) web(u);
}
static void rotate(ui_t *u, int knob, int sign)
{
    u->toast = UI_NONE;
    if (u->route == UI_HOME) { u->home_focus = (u->home_focus + sign + 19) % 19; return; }
    if (knob == 1) { u->tab = (enum ui_tab)(((int)u->tab + sign + UI_TAB_COUNT) % UI_TAB_COUNT); u->focus = 0; return; }
    int n = ui_item_count(u->tab);
    u->focus = (u->focus + sign + n) % n;
}
static void short_press(ui_t *u, int knob)
{
    if (knob == 1) { u->toast = u->toast == UI_SUB_SUMMARY ? UI_NONE : UI_SUB_SUMMARY; return; }
    if (u->route == UI_HOME) ui_key(u, u->home_focus+1);
}
void ui_tick(ui_t *u, int64_t now)
{
    for (int k = 0; k < 2; k++) if (u->down[k] && !u->held[k] && now >= u->pressed[k] && now-u->pressed[k] >= 1000) {
        u->held[k] = 1; u->toast = UI_NONE;
        if (k == 1) u->stopped = 1;
        else if (u->route == UI_LS) { u->route = UI_HOME; u->back_home = 1; }
    }
}
void ui_knob(ui_t *u, int k, enum ui_action a, int64_t now)
{
    if (k < 1 || k > 2) return;
    ui_tick(u, now);
    if (a == UI_DOWN && !u->down[k-1]) { u->down[k-1] = 1; u->held[k-1] = 0; u->pressed[k-1] = now; }
    else if (a == UI_UP && u->down[k-1]) { u->down[k-1] = 0; if (!u->held[k-1]) short_press(u, k); }
    else if (a == UI_CW || a == UI_CCW) rotate(u, k, a == UI_CW ? 1 : -1);
    else if (a == UI_PRESS) short_press(u, k);
}
/* Deterministic integer-only RGB565 rasterizer. */
#define RGB(r,g,b) (uint16_t)((((r)>>3)<<11)|(((g)>>2)<<5)|((b)>>3))
#define BG RGB(14,16,20)
#define FG RGB(235,235,240)
#define GREY RGB(130,138,150)
#define DIM RGB(90,96,108)
#define BLUE RGB(55,125,250)
#define GREEN RGB(50,205,125)
#define AMBER RGB(245,180,60)
#define RED RGB(235,55,65)
#define EDGE RGB(48,54,65)
#define FOOT RGB(24,28,35)
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
/* Effective selection: -1 means the documented default (catalogue entry 0). */
static const ui_provider_t *provider(const ui_status_t *s)
{
    int i = s->provider == -1 ? 0 : s->provider;
    return i >= 0 && (size_t)i < UI_PROVIDER_COUNT ? &UI_PROVIDERS[i] : NULL;
}
static const ui_model_t *model(const ui_status_t *s)
{
    const ui_provider_t *p = provider(s); int i = s->model == -1 ? 0 : s->model;
    return p && i >= 0 && (size_t)i < p->model_count ? &p->models[i] : NULL;
}
static enum ui_label route_label(const char *r, const ui_status_t *s)
{
    if (!*r) return s->profile == -1 ? UI_R_PROXY : UI_DASH;
    return !strcmp(r,"direct") ? UI_R_DIRECT : !strcmp(r,"proxy") ? UI_R_PROXY : !strcmp(r,"blocked") ? UI_R_BLOCKED : UI_DASH;
}
static const char *strip_http(const char *s) { return strncmp(s,"http://",7) ? s : s+7; }
/* value/sub text for one item; returns the value colour. Never prints secret material:
 * only presence/permission state and the fixed logical key path are known here. */
static uint16_t item_text(const ui_t *u, enum item it, char *v, size_t vc, char *sub, size_t sc)
{
    const ui_status_t *s = &u->st; const ui_provider_t *p = provider(s); const ui_model_t *m = model(s);
    uint16_t col = FG; *v = 0; *sub = 0;
#define V(lbl) snprintf(v,vc,"%s",L(lbl))
#define S(lbl) snprintf(sub,sc,"%s",L(lbl))
    switch (it) {
    case IT_LINK: {
        static const enum ui_label val[] = {UI_MAC_UNSEEN, UI_USB_DOWN, UI_USB_SLEEP, UI_MAC_NOAPP, UI_MAC_PROBING, UI_MAC_ONLINE, UI_MAC_STALE};
        V(val[s->link >= 0 && s->link <= UI_LINK_STALE ? s->link : 0]);
        if (s->link == UI_LINK_NOAPP) S(UI_USB_UP);
        else if (s->link == UI_LINK_USB_DOWN || s->link == UI_LINK_USB_SLEEP) S(UI_CHECK_CABLE);
        else if (s->link == UI_LINK_UNSEEN) S(UI_NEED_APP);
        col = s->link == UI_LINK_ONLINE ? GREEN : s->link == UI_LINK_STALE ? AMBER : FG;
        break;
    }
    case IT_PROXY: V(UI_UNTESTED); S(UI_TEST_IN_WEB); break;
    case IT_AGENT:
        if (s->agent == UI_AGENT_STOPPED) { V(UI_AG_STOPPED); S(UI_AG_ALLOW_HINT); col = AMBER; }
        else if (s->agent == UI_AGENT_ALLOWED || s->agent == UI_AGENT_LEASED) { V(UI_AG_ALLOWED); S(s->agent == UI_AGENT_LEASED ? UI_AG_LEASED : UI_AG_IDLE); }
        else if (s->agent == UI_AGENT_DISABLED) { V(UI_AG_DISABLED); S(UI_AG_ALLOW_HINT); }
        else { V(UI_AG_OFF); S(UI_AG_DEFAULT_OFF); }
        break;
    case IT_FW: case IT_KBD:
        if (*(it == IT_FW ? s->fw : s->kbd)) snprintf(v,vc,"%s",it == IT_FW ? s->fw : s->kbd);
        else { V(UI_DASH); col = DIM; }
        break;
    case IT_PROFILE:
        if (s->profile == -2) { V(UI_DASH); col = DIM; }
        else { V(s->profile == 1 ? UI_PROF_WIFI : UI_PROF_MAC); if (s->profile == -1) S(UI_DEFAULT); }
        break;
    case IT_MODEL: case IT_PROVIDER: case IT_VISION: case IT_TOOLS: case IT_ADAPTER: case IT_BASEURL:
        if (s->provider == -2 || s->model == -2 || !p || !m) { V(UI_DASH); col = DIM; break; }
        if (it == IT_MODEL) { snprintf(v,vc,"%s",m->id); snprintf(sub,sc,"%s%s%s",p->id,s->provider == -1 ? " · " : "",s->provider == -1 ? L(UI_DEFAULT) : ""); }
        else if (it == IT_PROVIDER) { snprintf(v,vc,"%s",p->label); if (s->provider == -1) S(UI_DEFAULT); }
        else if (it == IT_VISION) V(m->vision ? UI_SUPPORTED : UI_UNSUPPORTED);
        else if (it == IT_TOOLS) V(m->tools ? UI_SUPPORTED : UI_UNSUPPORTED);
        else if (it == IT_ADAPTER) { V(p->supported ? UI_SUPPORTED : UI_UNSUPPORTED); if (!p->supported) col = AMBER; }
        else snprintf(v,vc,"%s",p->base_url);
        break;
    case IT_USBIP: snprintf(v,vc,"%s",s->ip); S(s->ip_default ? UI_IP_DEFAULT : UI_IP_USB0); break;
    case IT_WIFI: V(s->wlan ? UI_WLAN_OFF : UI_WLAN_NONE); if (s->wlan) S(UI_WLAN_NOSTACK); break;
    case IT_CONFIG: case IT_HEALTH: {
        static const enum ui_label val[] = {UI_CFG_DEFAULT, UI_CFG_OK, UI_CFG_INVALID, UI_CFG_APPLYING};
        V(val[s->cfg >= 0 && s->cfg <= UI_CONF_APPLYING ? s->cfg : 0]);
        if (s->cfg == UI_CONF_INVALID) col = AMBER;
        break;
    }
    case IT_KEY: case IT_KEYFILE:
        if (it == IT_KEYFILE) {
            if (!p || s->provider == -2) { V(UI_DASH); col = DIM; }
            else { snprintf(v,vc,"%s.key",p->id); snprintf(sub,sc,"/data/mk20/secrets"); }
            break;
        }
        if (s->key == UI_KEYST_SET) V(UI_KEY_SET);
        else if (s->key == UI_KEYST_INSECURE) { V(UI_KEY_INSECURE); col = AMBER; }
        else if (s->key == UI_KEYST_MISSING) { V(UI_KEY_MISSING); S(UI_WEB); }
        else { V(UI_DASH); col = DIM; }
        if ((s->key == UI_KEYST_SET || s->key == UI_KEYST_INSECURE) && p) snprintf(sub,sc,"%s.key",p->id);
        break;
    case IT_VOICE: V(UI_VOICE_MAC); S(UI_VOICE_HOLD); break;
    case IT_TASK: {
        static const enum ui_label val[] = {UI_TASK_NONE, UI_TASK_OBSERVING, UI_TASK_PLANNING, UI_TASK_ACTING,
            UI_TASK_CONFIRM, UI_TASK_DONE, UI_TASK_STOPPED, UI_TASK_ERROR};
        if (s->agent == UI_AGENT_OFF) V(UI_AG_OFF);
        else if (s->agent == UI_AGENT_STOPPED) V(UI_TASK_STOPPED);
        else if (s->agent == UI_AGENT_DISABLED || s->task < 0 || s->task > 7) V(UI_TASK_NONE);
        else { V(val[s->task]); if (s->task == 7) col = AMBER; }
        break;
    }
    case IT_WEB: snprintf(v,vc,"%s",s->ip); snprintf(sub,sc,":8780"); break;
    case IT_PROXYURL:
        if (*s->proxy) snprintf(v,vc,"%s",strip_http(s->proxy));
        else if (s->profile == -1) { snprintf(v,vc,"172.30.250.1:7890"); S(UI_DEFAULT); }
        else { V(UI_DASH); col = DIM; }
        break;
    case IT_ROUTE_DOM: case IT_ROUTE_FOR: {
        enum ui_label r = route_label(it == IT_ROUTE_DOM ? s->route_dom : s->route_for, s);
        V(r); if (r == UI_DASH) col = DIM; if (s->profile == -1) S(UI_DEFAULT);
        break;
    }
    case IT_KEYWRITE: V(UI_KEYWRITE_WEB); break;
    case IT_KEYREAD: V(UI_KEYREAD_NEVER); break;
    case IT_TESTRUN: V(UI_TEST_IN_WEB); break;
    default: V(UI_DASH); col = DIM; break;
    }
#undef V
#undef S
    /* Snapshot from the status worker is old: keep the last values, but say so. */
    if (s->stale && it != IT_AGENT && it != IT_TASK && it != IT_VOICE && it != IT_PROXY && it != IT_KEYWRITE &&
        it != IT_KEYREAD && it != IT_TESTRUN && !(it == IT_LINK && s->link >= UI_LINK_PROBING)) {
        snprintf(sub,sc,"%s",L(UI_STALE)); col = DIM;
    }
    return col;
}
/* Status tile: secondary label, primary value, dim optional second line. */
static void status_tile(uint16_t *p, int w, int h, const char *label, const char *v, const char *sub,
                        uint16_t vcol, uint16_t border, uint16_t subcol)
{
    face(p,w,h,border);
    text(p,w,h,13,24,label,GREY,w-26);
    text(p,w,h,13,54,v,vcol,w-26);
    text(p,w,h,13,78,sub,subcol,w-26);
}
/* Explicit "enter" chevron (integer strokes), bottom-right of a navigation tile. */
static void chevron(uint16_t *p, int w, int h, int x, int y, uint16_t c)
{ for (int i = 0; i < 16; i++) { rect(p,w,h,x+i,y+i,5,2,c); rect(p,w,h,x+i,y+31-i,5,2,c); } }
/* Navigation tile: a bright label and the chevron, no value line. */
static void nav_tile(uint16_t *p, int w, int h, const char *label, uint16_t border)
{
    face(p,w,h,border);
    text(p,w,h,13,24,label,FG,w-26);
    chevron(p,w,h,90,72,BLUE);
}
static void estop_tile(const ui_t *u, uint16_t *p, int w, int h)
{
    face(p,w,h,RED);
    text(p,w,h,13,24,L(UI_STOP),RED,w-26);
    if (u->stopped) text(p,w,h,13,54,L(UI_TASK_STOPPED),AMBER,w-26);
}
static void status_line(const ui_t *u, char *out, size_t cap)
{
    char a[96], b[96], c[96], sub[96];
    item_text(u,IT_LINK,a,sizeof a,sub,sizeof sub);
    item_text(u,IT_PROFILE,b,sizeof b,sub,sizeof sub);
    item_text(u,IT_MODEL,c,sizeof c,sub,sizeof sub);
    snprintf(out,cap,"%s · %s · %s",a,b,c);
}
static void sub_screen(const ui_t *u, uint16_t *p, int w, int h)
{
    char line[512], url[64]; const ui_status_t *s = &u->st;
    ui_web_url(u,url,sizeof url);
    if (u->route == UI_HOME) {
        text(p,w,h,8,4,L(UI_HOME_TITLE),FG,w-16);
        status_line(u,line,sizeof line);
        text(p,w,h,8,25,line,s->link == UI_LINK_ONLINE ? GREEN : GREY,w-16);
        enum ui_label msg; uint16_t mc = FG;
        if (u->toast != UI_NONE) msg = u->toast;
        else if (s->confirm) { msg = UI_SUB_CONFIRM; mc = AMBER; }
        else if (u->stopped) { msg = UI_SUB_STOPPED; mc = AMBER; }
        else if (s->stale) { msg = UI_SUB_STALE_STATUS; mc = AMBER; }
        else if (s->link == UI_LINK_ONLINE) msg = UI_SUB_READY;
        else if (s->link == UI_LINK_USB_DOWN || s->link == UI_LINK_USB_SLEEP) msg = UI_SUB_USB_DOWN;
        else if (s->link == UI_LINK_PROBING) msg = UI_SUB_PROBING;
        else if (s->link == UI_LINK_STALE) { msg = UI_SUB_STALE; mc = AMBER; }
        else msg = UI_SUB_NOAPP;
        text(p,w,h,8,49,L(msg),mc,w-16);
        if (u->toast == UI_SUB_WEB_OPENED || u->toast == UI_SUB_WEB_OFFLINE)
            ui_font_draw_scaled(p,w,h,8,72,url,BLUE,w-16,2);
        else if (u->toast == UI_SUB_SUMMARY) {
            char a[96], b[96], sub[96]; item_text(u,IT_KEY,a,sizeof a,sub,sizeof sub); item_text(u,IT_CONFIG,b,sizeof b,sub,sizeof sub);
            snprintf(line,sizeof line,"%s %s · %s %s",L(UI_KEY),a,L(UI_CONFIG),b);
            text(p,w,h,8,76,line,FG,w-16);
        } else {
            snprintf(line,sizeof line,"%s%s",L(UI_SUB_WEB_URL),url);
            text(p,w,h,8,80,line,GREY,w-16);
        }
    } else {
        snprintf(line,sizeof line,"%s · %s",L(UI_LS_TITLE),L(tabs[u->tab]));
        text(p,w,h,8,4,line,FG,w-16);
        enum item it = u->focus < ui_item_count(u->tab) ? tab_items[u->tab][u->focus] : IT_NONE;
        char v[256], sub[128]; item_text(u,it,v,sizeof v,sub,sizeof sub);
        snprintf(line,sizeof line,"%s：%s%s%s",L(item_label[it]),v,*sub ? " · " : "",sub);
        text(p,w,h,8,25,line,GREY,w-16);
        enum ui_label msg = u->toast != UI_NONE ? u->toast : s->confirm ? UI_SUB_CONFIRM : UI_SUB_EDIT_WEB;
        text(p,w,h,8,49,L(msg),msg == UI_SUB_CONFIRM ? AMBER : FG,w-16);
        if (msg == UI_SUB_SUMMARY) { status_line(u,line,sizeof line); text(p,w,h,8,76,line,FG,w-16); }
        else ui_font_draw_scaled(p,w,h,8,72,url,BLUE,w-16,2);
    }
    ui_render_footer(u,p,w,h);
}
/* Footer: at most two lines; attention lines first, then the page's hint and voice line. */
static int footer_lines(const ui_t *u, const char *line[2], char buf[2][64], uint16_t col[2])
{
    int n = 0, attention = 0;
#define ADD(s,c) do { if (n < 2) { line[n] = (s); col[n] = (c); n++; } } while (0)
    if (*u->st.pair) {
        snprintf(buf[0],sizeof buf[0],"%s%s",L(UI_PAIR),u->st.pair);
        ADD(buf[0],AMBER); attention = 1;
    }
    /* Agent was active at the stop: persistent on every page until re-allowed/acknowledged. */
    if (u->stop_notice) { ADD(L(UI_SUB_STOP_NOTICE),AMBER); attention = 1; }
    else if (u->ctx == UI_CTX_HOST && u->stop_banner) { ADD(L(UI_SUB_STOPPED),AMBER); attention = 1; }
    if ((u->ctx == UI_CTX_HOST || u->ctx == UI_CTX_AGENT) && u->st.confirm) { ADD(L(UI_SUB_CONFIRM),AMBER); attention = 1; }
    if (u->ctx == UI_CTX_HOST && u->health_stale) { ADD(L(UI_SUB_STALE),AMBER); attention = 1; }
    if (u->ctx == UI_CTX_HOST && u->ptt_held) { ADD(L(UI_FT_PTT),GREEN); attention = 1; }
    if (u->incompatible) { ADD(L(UI_FT_INCOMPAT),AMBER); attention = 1; }
    static const enum ui_label hint[] = {UI_FT_HOME, UI_FT_LS, UI_FT_AGENT, UI_FT_HOST, UI_FT_AGENT};
    static const enum ui_label voice[] = {UI_FT_VOICE_LOCAL, UI_FT_VOICE_LOCAL, UI_FT_VOICE_AGENT, UI_FT_VOICE_HOST, UI_FT_VOICE_AGENT};
    int ctx = u->ctx <= UI_CTX_AGENT ? (int)u->ctx : 0;
    ADD(L(hint[ctx]),GREY);
    ADD(L(ctx == UI_CTX_HOST && !u->host_online ? UI_FT_VOICE_HOST_OFF : voice[ctx]),GREY);
#undef ADD
    return attention;
}
int ui_footer_attention(const ui_t *u)
{ const char *line[2]; char buf[2][64]; uint16_t col[2]; return footer_lines(u,line,buf,col); }
void ui_render_footer(const ui_t *u, uint16_t *p, int w, int h)
{
    const char *line[2]; char buf[2][64]; uint16_t col[2];
    footer_lines(u,line,buf,col);
    rect(p,w,h,0,112,w,h-112,FOOT);
    for (int i = 0; i < 2; i++) ui_font_draw(p,w,h,8,112+14*i,line[i],col[i],w-16);
}
int ui_render_panel(const ui_t *u, int fb, uint16_t *p, size_t count)
{
    if (!u || fb<1 || fb>PANEL_COUNT || !p) return -1;
    const panel_t *panel=&PANELS[fb-1]; int w=panel->w,h=panel->h;
    if (count < (size_t)w*h) return -1;
    for (int i=0;i<w*h;i++) p[i]=BG;
    if (fb == 21) { sub_screen(u,p,w,h); return 0; }
    if (fb == 20) { estop_tile(u,p,w,h); return 0; }
    char v[256], sub[128];
    if (u->route == UI_HOME) {
        int focused = u->home_focus == fb-1;
        if (fb == 6 && u->st.confirm) {
            face(p,w,h,focused ? BLUE : AMBER);
            text(p,w,h,13,24,L(home[fb-1]),FG,w-26);
            text(p,w,h,13,54,L(UI_TASK_CONFIRM),AMBER,w-26);
            chevron(p,w,h,90,72,AMBER);
        } else if (home_nav(fb)) nav_tile(p,w,h,L(home[fb-1]),focused ? BLUE : EDGE);
        else if (fb == 18) {
            face(p,w,h,focused ? BLUE : GREEN);
            text(p,w,h,13,24,L(home[fb-1]),FG,w-26);
            text(p,w,h,94,94,L(UI_SELECTED),GREEN,22);
        } else {
            enum item it = home_item[fb-1];
            uint16_t vc = item_text(u,it,v,sizeof v,sub,sizeof sub);
            status_tile(p,w,h,L(home[fb-1]),v,sub,fb == 19 ? FG : vc,focused ? BLUE : EDGE,fb == 19 ? FG : GREY);
        }
        return 0;
    }
    if (fb <= 5) {
        int chosen = fb-1 == (int)u->tab;
        face(p,w,h,chosen ? GREEN : EDGE);
        text(p,w,h,13,24,L(tabs[fb-1]),FG,w-26);
        if (chosen) text(p,w,h,94,94,L(UI_SELECTED),GREEN,22);
    } else if (fb <= 15) {
        int i = fb-6;
        if (i < ui_item_count(u->tab)) {
            enum item it = tab_items[u->tab][i];
            uint16_t vc = item_text(u,it,v,sizeof v,sub,sizeof sub);
            status_tile(p,w,h,L(item_label[it]),v,sub,vc,i == u->focus ? BLUE : EDGE,GREY);
        }
    } else if (fb == 16 || fb == 18) {
        face(p,w,h,EDGE); text(p,w,h,13,24,L(fb == 16 ? UI_BACK : UI_HOME_TITLE),FG,w-26);
    } else if (fb == 19) {
        item_text(u,IT_WEB,v,sizeof v,sub,sizeof sub);
        status_tile(p,w,h,L(UI_WEB),v,sub,FG,EDGE,FG);
    }
    return 0;
}
