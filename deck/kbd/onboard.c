#include "onboard.h"
#include "kbd.h"
#include "ctl.h"
#include "devstat.h"
#include "jpeg.h"
#include "ui_font.h"
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <time.h>
#define L4_BACK_FB 5   /* user layout 2026-10-21: L4 row1 = DESK HERD TMUX 新会话 回L1 */

#define ALL_PANELS ((1u << PANEL_COUNT) - 1)
#define RESERVED ((1u << 19) | (1u << 20))
#define FRAME_MAX (1u << 20)
#define HOST_TAPS 16
#define GRACE_MS 2000        /* ONLINE host page switch: no navigation until a fresh frame */
#define STOP_BANNER_MS 10000 /* host pages keep the host footer after this */
#define OPEN_SETTINGS_MS 3000
#define RESTORE_FB20 1u
#define RESTORE_FOOTER 2u
/* Mac keyboard keys on the live L1/L4 bottom row: logical fb -> HID keyboard usage (page 0x07).
 * User decision 2026-10-21 (new L1 layout; replaces the PRD rule "non-PTT keys never produce HID").
 * Same gate as the Mac PTT (fb16, Right Option, --ptt COL,ROW; a --ptt position wins over this table)
 * and never forwarded as keyStateChanged. Down on press, up on release: macOS auto-repeats while held.
 * fb20 stays the Mac mode key / e-stop. Every other page keeps these keys' on-board meaning. */
static const struct { int fb; uint8_t usage; } HOST_HID_KEYS[] = {
    {17, 0x2A},   /* DELETE  -> Backspace */
    {18, 0x28},   /* SEND/OK -> Return */
    {19, 0x29},   /* NO      -> Escape (also interrupts Claude Code / codex) */
};
/* GD32 key reports are edge-triggered without a state resync: a lost key-up must not leave an
 * auto-repeating Backspace/Return/Escape held. Longer holds are cut (press again to continue). */
#define HID_KEY_HOLD_MS 10000
struct onboard_s {
    ui_t ui;
    enum onboard_route route, back, host_page;
    enum onboard_health health;
    uint64_t epoch, decode_epoch;
    long generation;
    int negotiated, handshake_required, beats, fresh, publishing, panel;
    int64_t seen, first, last_beat, next_render, next_full, next_decode;
    uint32_t render;
    uint16_t *scratch;
    /* Press-time owner: 0 none, 1 host key event, 2 Mac PTT (Right Option), 3 agent page,
     * 4 L4 back, 5 Mac HID key (hid_usage, pressed at hid_at). */
    int key_owner[20], knob_down[2], knob_long[2];
    uint8_t hid_usage[20]; int64_t hid_at[20];
    struct { int page, client; uint64_t generation; } agent_owner[20];
    uint32_t pressed;
    uint32_t pending_up;
    int taps[HOST_TAPS], tap_head, tap_count, tap_up;
    uint16_t *tile_px; int tile_fb, tile_x, tile_y, tile_w, tile_h;
    uint64_t tile_epoch;
    enum onboard_route tile_route;
    long tile_generation;
    int64_t budget_at, panel_at[PANEL_COUNT]; size_t bus_left[2];
    int64_t knob_at[2];
    int last_page, last_stop, confirm_pending, incompatible; long confirm_id; unsigned estops_seen;
    /* 2026-10-20 UX: boot auto-L1, host-owned fb20/footer, grace window, status probes */
    int auto_l1, grace_shown, stop_banner, opened, face20, footer_host, agent_was, footer_sig;
    uint32_t restore;
    int64_t switch_at, stop_at, open_at;
    devstat_t *ds; ds_result_t dsr; int ds_kick, ds_stale;
    int suspend, stop_ack; int64_t input_at; /* host forwarding paused after an e-stop on L1/L4 */
    pthread_t decoder;
    pthread_mutex_t mu; pthread_cond_t cv;
    int stop, job, ready, result; uint8_t *jpg, *next_jpg; size_t jpg_size, next_size; uint64_t next_epoch;
    kbd_t *kbd;
};
static void host_tap(kbd_t *k, int slot);
static int host_route(const onboard_t *o) { return o->route == ONBOARD_L1 || o->route == ONBOARD_L4; }
static int safety_footer(const onboard_t *o) { return host_route(o) && (o->fresh || o->health==HOST_SUSPECT); }
static int page_id(enum onboard_route r) { return r == ONBOARD_L5 ? 1 : r == ONBOARD_L1 || r == ONBOARD_L4 ? 2 : 0; }
/* Keyboard agent active = allowed (a lease implies allowed: ctl revokes it on disable/stop). */
static int agent_active(const kbd_t *k) { return k->ctl && (k->ctl->allowed || k->ctl->token); }
/* After an e-stop on L1/L4 nothing reaches the host until every key/knob is up and 1 s
 * passed without input; fb20 is the e-stop meanwhile. */
static int suspended(const onboard_t *o) { return o->suspend && host_route(o); }
/* On L1/L4 the Mac host owns fb20 (its mode key) unless the keyboard agent is active. */
static int fb20_host(const kbd_t *k) { return host_route(k->onboard) && !agent_active(k) && !k->onboard->suspend; }
static int grace(const onboard_t *o, int64_t now)
{ return host_route(o) && o->health == HOST_ONLINE && !o->fresh && now - o->switch_at < GRACE_MS; }
/* Same legacy-host liveness as an explicitly entered host page, only until the first ONLINE. */
static int legacy_probe(const onboard_t *o) { return o->auto_l1 && !o->negotiated && o->route == ONBOARD_HOME; }
static void footer_ctx(kbd_t *k)
{
    onboard_t *o = k->onboard; ui_t *u = &o->ui; int ptt = 0;
    for (int i = 0; i < 20; i++) ptt |= o->key_owner[i] == 2;
    u->ctx = o->route == ONBOARD_HOME ? UI_CTX_HOME : o->route == ONBOARD_LS ? UI_CTX_LS :
        o->route == ONBOARD_L5 ? UI_CTX_L5 : host_route(o) ? UI_CTX_HOST : UI_CTX_AGENT;
    u->health_stale = o->health == HOST_SUSPECT; u->ptt_held = ptt;
    u->incompatible = o->incompatible; u->stop_banner = o->stop_banner;
    u->stop_notice = k->ctl && k->ctl->stop_agent && !o->stop_ack;
}
static int attention(kbd_t *k) { footer_ctx(k); return ui_footer_attention(&k->onboard->ui); }
int onboard_host_flush(kbd_t *k)
{
    onboard_t *o=k->onboard;
    if (!o) return 0;
    if (k->host_fd < 0) {
        for (int i=0;i<20;i++) if (o->key_owner[i]==1) o->key_owner[i]=0;
        o->pending_up=0; o->tap_head=o->tap_count=o->tap_up=0;
        return 0;
    }
    /* At most 20 releases and HOST_TAPS tap pairs. No waits or recursion:
     * ownership is retained until the complete up is in the transport queue. */
    for (int i=0;i<20;i++) if (o->pending_up & (1u<<i)) {
        char j[160]; int n=host_json_key_state(j,sizeof j,i%5,i/5,0);
        if (n<=0 || kbd_host_queue(k,KBD_EVENT_ID,HOST_CMD_JSON,j,(size_t)n)) return -1;
        o->pending_up &= ~(1u<<i); o->key_owner[i]=0;
    }
    while (o->tap_count) {
        int slot=o->taps[o->tap_head];
        char j[160]; int n=host_json_key_state(j,sizeof j,slot,slot,!o->tap_up);
        if (n<=0 || kbd_host_queue(k,KBD_EVENT_ID,HOST_CMD_JSON,j,(size_t)n)) return -1;
        if (!o->tap_up) o->tap_up=1;
        else { o->tap_up=0; o->tap_head=(o->tap_head+1)%HOST_TAPS; o->tap_count--; }
    }
    return 0;
}
static uint8_t host_hid_usage(int fb)
{
    for (size_t i = 0; i < sizeof HOST_HID_KEYS / sizeof *HOST_HID_KEYS; i++)
        if (HOST_HID_KEYS[i].fb == fb) return HOST_HID_KEYS[i].usage;
    return 0;
}
/* Up of a physical key that owns Mac HID since its press: only its own modifier/usage. */
static void mac_up(kbd_t *k, int i, int owner)
{
    if (!k->hid) return;
    if (owner == 2) hid_set_mod(k->hid, HID_MOD_RALT, 0);
    else if (owner == 5) hid_key(k->hid, k->onboard->hid_usage[i], 0);
}
static void release_mac(kbd_t *k)
{
    onboard_t *o = k->onboard;
    for (int i = 0; i < 20; i++) if (o->key_owner[i] == 2 || o->key_owner[i] == 5) {
        int owner = o->key_owner[i];
        o->key_owner[i] = 0; mac_up(k,i,owner);
    }
}
static void release_key(kbd_t *k, int i)
{
    onboard_t *o = k->onboard; int owner = o->key_owner[i];
    if (owner == 1) {
        o->pending_up |= 1u<<i;
        kbd_host_drain(k);
        return;
    }
    o->key_owner[i] = 0; /* notify may disconnect a client */
    if (owner == 2 || owner == 5) mac_up(k,i,owner);
    else if (owner == 3 && k->ctl) {
        int ci=o->agent_owner[i].client;
        if (ci >= 0 && ci < CTL_MAX_CLIENTS &&
            k->ctl->client_generation[ci] == o->agent_owner[i].generation) {
            char ev[160];
            snprintf(ev,sizeof ev,"{\"ev\":\"key\",\"page\":%d,\"row\":%d,\"col\":%d,\"down\":false}",o->agent_owner[i].page,i/5,i%5);
            ctl_notify(k->ctl,ci,ev);
        }
    }
}
static void boundary(kbd_t *k, int redraw)
{
    onboard_t *o = k->onboard;
    for (int i=0;i<20;i++) release_key(k,i);
    if (k->ctl) { k->ctl->confirm.shown=0; k->ctl->confirm.shown_ms=0; }
    o->tile_fb=0; o->restore=0;
    free(o->next_jpg); o->next_jpg=NULL;
    /* The grace window counts from the first of a chain of switches (capped at 2 s). */
    int64_t now = kbd_now(k);
    if (!(host_route(o) && o->health == HOST_ONLINE && !o->fresh && now - o->switch_at < GRACE_MS)) o->switch_at = now;
    o->epoch++; o->fresh = o->publishing = 0;
    /* Preserve framing and acknowledgements for even a partial old frame; poison its origin. */
    k->hp.barrier = k->hp.len;
    int queued=0;
    if (k->host_fd >= 0 && ioctl(k->host_fd,FIONREAD,&queued)) queued=0;
    k->host_rx_barrier=queued > 0 ? (size_t)queued : 0;
    if (redraw || host_route(o)) {
        screen_discard(&k->scr); o->render = ALL_PANELS; o->next_render = 0;
    } else if (o->route == ONBOARD_HOME) o->render |= 1u | (1u << 20);
    o->next_full = o->next_decode = 0;
}
void onboard_route(kbd_t *k, enum onboard_route r)
{
    onboard_t *o = k->onboard;
    if (!o || o->route == r) return;
    if (r == ONBOARD_LS) o->back = o->route;
    o->route = r; o->ui.route = r == ONBOARD_LS ? UI_LS : UI_HOME;
    if (r != ONBOARD_LS) o->ui.toast = UI_NONE;
    if (r == ONBOARD_HOME || r == ONBOARD_LS) o->ds_kick = 1; /* fresh status on entry */
    boundary(k,1);
    if (k->ctl && r != ONBOARD_AGENT) k->ctl->screen.current = page_id(r);
    if (k->ctl) o->last_page = k->ctl->screen.current;
    if (host_route(o) && o->health == HOST_ONLINE && o->host_page != r) {
        o->host_page=r; host_tap(k,103);
    }
}
static void *decode(void *ctx)
{
    onboard_t *o = ctx;
    pthread_mutex_lock(&o->mu);
    while (!o->stop) {
        while (!o->job && !o->stop) pthread_cond_wait(&o->cv, &o->mu);
        if (o->stop) break;
        pthread_mutex_unlock(&o->mu);
        int rc = jpeg_decode565(o->jpg, o->jpg_size, o->kbd->canvas, CANVAS_W, CANVAS_H);
        pthread_mutex_lock(&o->mu);
        free(o->jpg); o->jpg = NULL; o->result = rc; o->job = 0; o->ready = 1;
    }
    pthread_mutex_unlock(&o->mu);
    return NULL;
}
int onboard_enable(kbd_t *k)
{
    onboard_t *o = calloc(1, sizeof *o);
    if (!o) return -1;
    o->scratch = malloc(428 * 142 * 2);
    if (!o->scratch) { free(o); return -1; }
    o->tile_px = malloc(428 * 142 * 2);
    if (!o->tile_px) { free(o->scratch); free(o); return -1; }
    /* LS is read-only: no Apply/Test consumer is reachable from keys (web is the editor). */
    ui_init(&o->ui);
    /* Status files are read on devstat's own worker thread, never on this loop. */
    devstat_defaults(&o->dsr); o->ds = devstat_new_env(); o->ds_kick = 1;
    if (o->ds && devstat_start(o->ds)) { devstat_free(o->ds); o->ds = NULL; }
    o->kbd = k; o->host_page=ONBOARD_L1; o->render = ALL_PANELS; o->epoch = 1; o->auto_l1 = 1;
    pthread_mutex_init(&o->mu, NULL); pthread_cond_init(&o->cv, NULL);
    if (pthread_create(&o->decoder, NULL, decode, o)) {
        pthread_mutex_destroy(&o->mu); pthread_cond_destroy(&o->cv); free(o->tile_px); free(o->scratch); free(o); return -1;
    }
    k->onboard = o; k->local_ui = 0;
    return 0;
}
void onboard_status_set(kbd_t *k, devstat_t *d)
{
    onboard_t *o = k->onboard; if (!o) { devstat_free(d); return; }
    devstat_free(o->ds); o->ds = d; devstat_defaults(&o->dsr); o->ds_stale = 0; o->ds_kick = 1;
    if (d && !d->started && devstat_start(d)) { devstat_free(d); o->ds = NULL; }
}
void onboard_status_root(kbd_t *k, const char *root, const char *pair_path, const char *version, const char *rel)
{ onboard_status_set(k, devstat_new(root, pair_path, version, rel)); }
void onboard_free(kbd_t *k)
{
    onboard_t *o = k->onboard;
    if (!o) return;
    pthread_mutex_lock(&o->mu); o->stop = 1; pthread_cond_signal(&o->cv); pthread_mutex_unlock(&o->mu);
    pthread_join(o->decoder, NULL); free(o->jpg); free(o->next_jpg); devstat_free(o->ds);
    pthread_mutex_destroy(&o->mu); pthread_cond_destroy(&o->cv);
    free(o->tile_px); free(o->scratch); free(o); k->onboard = NULL;
}
void onboard_loss(kbd_t *k)
{
    onboard_t *o = k->onboard; if (!o) return;
    if (k->host_fd<0) onboard_host_flush(k); /* no old events on a replacement connection */
    o->health = HOST_OFFLINE; o->beats = 0; o->handshake_required = 1;
    boundary(k,0);
}
static void sample(kbd_t *k, int64_t now)
{
    onboard_t *o = k->onboard;
    if (o->handshake_required) return;
    if (o->health == HOST_OFFLINE) o->health = HOST_PROBING;
    if (!o->beats || now - o->last_beat > 1500) { o->beats = 1; o->first = now; }
    else if (now - o->last_beat >= 1000) o->beats++;
    else { o->seen = now; return; }
    o->seen = o->last_beat = now;
    if (o->health == HOST_PROBING && o->beats >= 3 && now - o->first >= 2000) {
        o->health = HOST_ONLINE; o->render |= RESERVED;
        /* First ONLINE since process start: follow the Mac to L1 once, but only from the
         * boot HOME and only if nobody touched a key/knob yet. Never again afterwards. */
        if (o->auto_l1) {
            o->auto_l1 = 0;
            if (o->route == ONBOARD_HOME) { onboard_route(k,ONBOARD_L1); return; }
        }
        if (host_route(o) && o->host_page != o->route) {
            o->host_page=o->route; boundary(k,1); host_tap(k,103);
        }
    }
}
void onboard_valid_frame(kbd_t *k, int64_t now) { if (!k->onboard->negotiated) sample(k, now); }
int onboard_json(kbd_t *k, const char *m, const char *s, size_t n, char *r, size_t cap)
{
    onboard_t *o = k->onboard; long v;
    if (!strncmp(m,"ui.",3)) {
        char method[64];
        if (n >= CTL_LINE_MAX || ctl_json_str(s,n,"method",method,sizeof method) < 0 || strcmp(method,m)) return -1;
    }
    if (!strcmp(m, "getInfo")) {
        if (o->health == HOST_OFFLINE) {
            o->negotiated=0;
            o->handshake_required = 0; o->health = HOST_PROBING; o->beats = 0; o->seen = kbd_now(k);
        }
        return 0;
    }
    if (!strcmp(m, "ui.hello")) {
        if (ctl_json_int(s,n,"version",&v) || v != 1) return -1;
        onboard_loss(k); o->negotiated = 1; o->handshake_required = 0;
        o->health = HOST_PROBING; o->seen = kbd_now(k);
        snprintf(r,cap,"{\"version\":1,\"heartbeat_ms\":1000,\"capabilities\":[\"ui.page\",\"ui.heartbeat\"],\"generation\":%ld}",o->generation);
        return 1;
    }
    if (!strcmp(m, "ui.heartbeat")) {
        if (!o->negotiated || o->handshake_required || o->health == HOST_SUSPECT) return -1;
        sample(k, kbd_now(k)); snprintf(r,cap,"{}"); return 1;
    }
    if (!strcmp(m, "ui.page")) {
        char p[8];
        if (!o->negotiated || o->health != HOST_ONLINE || !host_route(o) ||
            ctl_json_int(s,n,"generation",&v) || v <= o->generation ||
            ctl_json_str(s,n,"page",p,sizeof p) < 0 || (strcmp(p,"L1") && strcmp(p,"L4"))) return -1;
        o->generation = v;
        enum onboard_route route = !strcmp(p,"L1") ? ONBOARD_L1 : ONBOARD_L4;
        o->host_page=route; /* host explicitly requested this content; no synthetic toggle */
        if (route == o->route) boundary(k,1); else onboard_route(k,route);
        snprintf(r,cap,"{\"page\":\"%s\",\"generation\":%ld}",p,v); return 1;
    }
    return 0;
}
void onboard_jpeg(kbd_t *k, const uint8_t *data, size_t n)
{
    onboard_t *o = k->onboard;
    if ((!host_route(o) && !legacy_probe(o)) || o->handshake_required || o->health == HOST_SUSPECT || n > FRAME_MAX || !n || kbd_now(k) < o->next_decode) return;
    pthread_mutex_lock(&o->mu);
    if ((o->job || o->ready) && o->decode_epoch != o->epoch) {
        uint8_t *next=malloc(n);
        if (next) {
            memcpy(next,data,n); free(o->next_jpg); o->next_jpg=next;
            o->next_size=n; o->next_epoch=o->epoch; o->next_decode=kbd_now(k)+250;
        }
    } else if (!o->job && !o->ready && !o->publishing) {
        o->jpg = malloc(n);
        if (o->jpg) {
            memcpy(o->jpg,data,n); o->jpg_size = n; o->decode_epoch = o->epoch;
            o->tile_fb=0; /* a newer complete frame supersedes an unproduced tile */
            o->next_decode=kbd_now(k)+250; o->job = 1; pthread_cond_signal(&o->cv);
        }
    }
    pthread_mutex_unlock(&o->mu);
}
int onboard_tile_allowed(kbd_t *k, int fb, int y, int h)
{
    onboard_t *o = k->onboard;
    if (!(host_route(o) && o->health == HOST_ONLINE && o->fresh && !o->publishing && fb >= 1 && fb <= 21)) return 0;
    if (fb == 20) return fb20_host(k);
    /* The footer belongs to the host unless a safety/attention line is shown there. */
    return fb != 21 || (y >= 0 && h > 0 && y <= 142-h && (y <= 112-h || !attention(k)));
}
int onboard_tile(kbd_t *k, int fb, int x, int y, int w, int h, const uint16_t *px)
{
    onboard_t *o=k->onboard;
    if (!onboard_tile_allowed(k,fb,y,h) || x<0 || y<0 || w<=0 || h<=0 ||
        x>PANELS[fb-1].w-w || y>PANELS[fb-1].h-h) return -1;
    /* One bounded slot. Same-rectangle updates coalesce; other work gets the
     * existing E response until this slot is published. Acks still mean handled. */
    if (o->tile_fb && (o->tile_fb!=fb || o->tile_x!=x || o->tile_y!=y || o->tile_w!=w || o->tile_h!=h)) return -1;
    memcpy(o->tile_px,px,(size_t)w*h*2);
    o->tile_fb=fb; o->tile_x=x; o->tile_y=y; o->tile_w=w; o->tile_h=h;
    o->tile_epoch=o->epoch; o->tile_route=o->route; o->tile_generation=o->generation;
    return 0;
}
static int budget(onboard_t *o, int fb, int64_t now)
{
    const panel_t *p=&PANELS[fb-1]; size_t bytes=(size_t)p->w*p->h*2;
    if (fb==20) return 1; /* safety preempts ordinary production */
    if (now>=o->budget_at) {
        o->bus_left[0]=416464; o->bus_left[1]=360448; o->budget_at=now+250;
    }
    if (now<o->panel_at[fb-1] || bytes>o->bus_left[p->bus]) return 0;
    o->bus_left[p->bus]-=bytes; o->panel_at[fb-1]=now+250;
    return 1;
}
static void stop(kbd_t *k, const char *reason, int64_t now)
{
    onboard_t *o = k->onboard;
    if (k->ctl && !k->ctl->estopped) ctl_estop(k->ctl, reason, now);
    ui_key(&o->ui,20); /* the accepted U1 stop transition */
    if (host_route(o)) {
        /* Stay on the Mac page: ctl_estop() already revoked the lease and sent all-up;
         * release a held Mac PTT too and show a bounded stop banner in the footer. */
        release_mac(k);
        if (k->ctl) { k->ctl->screen.current = page_id(o->route); o->last_page = k->ctl->screen.current; }
        o->stop_banner = 1; o->stop_at = now; o->render |= RESERVED;
        o->suspend = 1; o->input_at = now; /* panic presses right after the stop never reach the Mac */
    } else if (o->route == ONBOARD_HOME) boundary(k,1);
    else onboard_route(k,ONBOARD_HOME);
}
static void status_sync(kbd_t *k)
{
    onboard_t *o = k->onboard; const ds_result_t *r = &o->dsr; const ctl_t *c = k->ctl;
    ui_status_t s; ui_status_defaults(&s);
    s.link = o->health == HOST_ONLINE ? UI_LINK_ONLINE : o->health == HOST_SUSPECT ? UI_LINK_STALE :
        o->health == HOST_PROBING ? UI_LINK_PROBING : r->udc == DS_UDC_UP ? UI_LINK_NOAPP :
        r->udc == DS_UDC_DOWN ? UI_LINK_USB_DOWN : r->udc == DS_UDC_SUSPENDED ? UI_LINK_USB_SLEEP : UI_LINK_UNSEEN;
    if (c) {
        int client = 0;
        for (int i = 0; i < CTL_MAX_CLIENTS; i++) client |= c->cfd[i] >= 0;
        s.agent = c->estopped ? UI_AGENT_STOPPED : c->allowed ? (c->token ? UI_AGENT_LEASED : UI_AGENT_ALLOWED) :
            client ? UI_AGENT_DISABLED : UI_AGENT_OFF;
        static const char *const states[] = {"idle", "observing", "planning", "acting", "awaiting_confirm", "done", "stopped", "error"};
        for (int i = 0; i < 8; i++) if (!strcmp(c->ui.state, states[i])) s.task = i;
        s.confirm = c->confirm.pending;
    }
    s.cfg = r->marker == 1 ? UI_CONF_APPLYING : r->marker == 2 ? UI_CONF_INVALID :
        r->net == DS_ABSENT && r->prov == DS_ABSENT ? UI_CONF_DEFAULT : r->net == DS_OK && r->prov == DS_OK ? UI_CONF_OK : UI_CONF_INVALID;
    s.key = r->key == DS_KEY_SET ? UI_KEYST_SET : r->key == DS_KEY_MISSING ? UI_KEYST_MISSING :
        r->key == DS_KEY_INSECURE ? UI_KEYST_INSECURE : UI_KEYST_UNKNOWN;
    s.wlan = r->wlan; s.profile = r->profile; s.provider = r->provider; s.model = r->model;
    s.ip_default = !r->ip_known;
    snprintf(s.ip, sizeof s.ip, "%s", r->ip);
    snprintf(s.fw, sizeof s.fw, "%s", r->version);
    snprintf(s.kbd, sizeof s.kbd, "%s", k->version ? k->version : "");
    snprintf(s.proxy, sizeof s.proxy, "%s", r->proxy);
    if (!o->ds_stale) snprintf(s.pair, sizeof s.pair, "%s", r->pair); /* expiry unverifiable when stale */
    s.stale = o->ds_stale;
    snprintf(s.route_dom, sizeof s.route_dom, "%s", r->route_dom);
    snprintf(s.route_for, sizeof s.route_for, "%s", r->route_for);
    if (memcmp(&s, &o->ui.st, sizeof s)) {
        int pair_changed = strcmp(s.pair, o->ui.st.pair) || s.confirm != o->ui.st.confirm;
        memcpy(&o->ui.st, &s, sizeof s);
        if (o->route <= ONBOARD_LS) o->render = ALL_PANELS;
        else if (pair_changed) o->render |= 1u << 20;
    }
}
void onboard_tick(kbd_t *k, int64_t now)
{
    onboard_t *o = k->onboard; if (!o) return;
    if ((o->health == HOST_ONLINE || o->health == HOST_PROBING || o->health == HOST_SUSPECT) && now-o->seen >= 5000) onboard_loss(k);
    else if ((o->health == HOST_ONLINE || o->health == HOST_PROBING) && now-o->seen >= 3000) {
        o->health = HOST_SUSPECT; release_mac(k); o->epoch++; o->fresh = o->publishing = 0;
        o->tile_fb=0; o->restore=0;
        free(o->next_jpg); o->next_jpg=NULL;
        k->hp.barrier = k->hp.len; o->render |= RESERVED; /* stale footer; fb20 reads "K2 长按急停" */
    }
    int online = o->health == HOST_ONLINE;
    if (o->ui.host_online != online) o->render |= (o->route <= ONBOARD_LS ? ALL_PANELS : 0u) | (1u << 20);
    o->ui.host_online = online;
    if (k->ctl) {
        /* Every e-stop (also a repeated one while latched: ctl_screen_reset() runs again). */
        if (k->ctl->st.estops != o->estops_seen) {
            o->estops_seen = k->ctl->st.estops; o->last_stop = 1;
            if (k->ctl->stop_agent) o->stop_ack = 0; /* a new stop of an active agent re-arms the notice */
            stop(k,"socket",now);
        }
        int pid = k->ctl->screen.current; /* after stop(): host pages keep their ctl page */
        o->last_stop = k->ctl->estopped; o->ui.stopped = k->ctl->estopped;
        if (!k->ctl->estopped) o->stop_banner = 0;
        if (pid != o->last_page) {
            enum onboard_route r=pid == 1 ? ONBOARD_L5 : pid >= 3 ? ONBOARD_AGENT : pid == 2 ? ONBOARD_L1 : ONBOARD_HOME;
            if (r==o->route) boundary(k,1); else onboard_route(k,r);
            o->last_page = pid;
        }
        /* A confirmation request never navigates: HOME/LS/footers show a badge and the
         * user enters L5 explicitly. On L5 itself, re-present for the new request. */
        if (k->ctl->confirm.pending && (!o->confirm_pending || o->confirm_id != k->ctl->confirm.cid)) {
            if (o->route == ONBOARD_L5) boundary(k,1);
            o->confirm_id = k->ctl->confirm.cid;
        }
        if (o->confirm_pending != k->ctl->confirm.pending) o->render = ALL_PANELS;
        o->confirm_pending = k->ctl->confirm.pending;
        if (ctl_ui_dirty(k->ctl)) o->render = ALL_PANELS;
    }
    if (o->stop_banner && now - o->stop_at >= STOP_BANNER_MS) o->stop_banner = 0;
    int active = agent_active(k);
    if (active != o->agent_was) {
        /* Agent became active while fb20 is held as the Mac's key: the host gets its up
         * first, then fail safe (e-stop) and fb20 is redrawn as the e-stop face. */
        if (active && o->key_owner[19] == 1) { release_key(k,19); stop(k,"fb20_held",now); }
        o->agent_was = agent_active(k); o->render |= 1u << 19;
    }
    for (int i = 0; i < 20; i++) if (o->key_owner[i] == 5 && now - o->hid_at[i] >= HID_KEY_HOLD_MS) {
        fprintf(stderr, "mk20-kbd: Mac key held %d s: released (lost key-up guard)\n", HID_KEY_HOLD_MS / 1000);
        release_key(k,i);
    }
    if (o->suspend) {
        /* Bounded: 5 s with no key/knob event at all resyncs the held state, so a GD32 key-up lost while
         * suspended (edge-triggered reports, no state resync) cannot leave L1/L4 dead. Safe: a key's up
         * still releases to its press-time owner (release_key does not consult `pressed`); swallowed
         * presses have owner 0; a knob held that long has already had its long-press decision. */
        if (now - o->input_at >= 5000 && (o->pressed || o->knob_down[0] || o->knob_down[1])) {
            o->pressed = 0; o->knob_down[0] = o->knob_down[1] = 0;
            if (o->ui.down[0]) ui_knob(&o->ui,1,UI_UP,now);
            fprintf(stderr, "mk20-kbd: e-stop pause: no input for 5 s, held state resynced\n");
        }
        int held = o->pressed != 0 || o->knob_down[0] || (o->knob_down[1] && k->k2.reports_release);
        if (!held && now - o->input_at >= 1000) { o->suspend = 0; o->render |= 1u << 19; }
    }
    if (o->grace_shown && !grace(o,now)) { o->grace_shown = 0; o->render = ALL_PANELS; }
    status_sync(k);
    footer_ctx(k); /* other callers may refresh ctx too; only this signature triggers redraws */
    int sig = (int)o->ui.ctx | o->ui.health_stale << 4 | o->ui.ptt_held << 5 | o->ui.incompatible << 6 | o->ui.stop_banner << 7 |
        o->ui.stop_notice << 8;
    if (sig != o->footer_sig) { o->footer_sig = sig; o->render |= 1u << 20; }
    for (int i = 0; i < 2; i++) if (o->knob_down[i] && !o->knob_long[i] && now-o->knob_at[i] >= 1000) {
        o->knob_long[i] = 1;
        if (i == 0 && !suspended(o)) { onboard_route(k,ONBOARD_HOME); o->render = ALL_PANELS; }
        /* K2 uses k2estop.c capability detection, never a speculative first hold. */
    }
}
static void host_tap(kbd_t *k, int slot)
{
    onboard_t *o=k->onboard;
    kbd_host_drain(k);
    if (k->host_fd<0) return;
    if (o->tap_count==HOST_TAPS) { k->st.host_tx_errors++; return; }
    o->taps[(o->tap_head+o->tap_count)%HOST_TAPS]=slot; o->tap_count++;
    kbd_host_drain(k);
}
/* One device-initiated cmd101 event, exactly {"method":"ui.openSettings"}: the Mac builds
 * the URL from its own configuration. Only while ONLINE, at most once per 3 s. */
static void open_settings(kbd_t *k, int64_t now)
{
    static const char j[] = "{\"method\":\"ui.openSettings\"}";
    onboard_t *o = k->onboard;
    if (o->health != HOST_ONLINE || k->host_fd < 0 || (o->opened && now - o->open_at < OPEN_SETTINGS_MS)) return;
    o->opened = 1; o->open_at = now; /* rate limit counts attempts, not successful sends */
    kbd_host_send(k,KBD_EVENT_ID,HOST_CMD_JSON,j,sizeof j - 1);
}
static void short_knob(kbd_t *k, int knob, int64_t now)
{
    onboard_t *o = k->onboard;
    if (host_route(o) && grace(o,now)) return; /* grace: K1/K2 short are swallowed too */
    if (knob == 2 && o->route == ONBOARD_HOME) {
        int fb=o->ui.home_focus+1;
        onboard_input(k,(fb-1)/5,(fb-1)%5,0,1,0,now);
        o->pressed &= ~(1u << (fb-1)); /* synthetic tap: no physical release follows */
    }
    else if (knob == 2 && host_route(o)) {
        onboard_route(k,o->route == ONBOARD_L1 ? ONBOARD_L4 : ONBOARD_L1);
    }
    else if (knob == 1 && o->route == ONBOARD_L1 && o->health == HOST_ONLINE && o->fresh) host_tap(k,100);
    else if (knob == 1 && o->route == ONBOARD_L4) onboard_route(k,ONBOARD_LS);
    else if (o->route <= ONBOARD_LS) {
        ui_knob(&o->ui,knob,UI_PRESS,now);
        if (o->ui.route == UI_LS && o->route != ONBOARD_LS) onboard_route(k,ONBOARD_LS);
    }
}
int onboard_input(kbd_t *k, int row, int col, int knob, int down, int keycode, int64_t now)
{
    onboard_t *o = k->onboard;
    o->auto_l1 = 0; /* any physical key/knob event: the user drives navigation from now on */
    o->input_at = now;
    if (keycode) { o->incompatible=1; o->render |= 1u << 20; }
    if (knob) {
        int i = knob-1, slot = 100 + i*3;
        if (row == slot) {
            if (down && !o->knob_down[i]) { o->knob_down[i] = 1; o->knob_long[i] = 0; o->knob_at[i] = now; }
            else if (!down && o->knob_down[i]) {
                o->knob_down[i] = 0;
                if (i == 0 && o->ui.down[0]) ui_knob(&o->ui,1,UI_UP,now);
                if (!suspended(o) && !o->knob_long[i] && now-o->knob_at[i] < 1000 && !keycode) short_knob(k,knob,now);
            }
        } else if (suspended(o)) return 1; /* rotation after an e-stop: swallowed */
        else if (down && !keycode && o->route <= ONBOARD_LS) ui_knob(&o->ui,knob,row == slot+1 ? UI_CCW : UI_CW,now);
        else if (down && !keycode && host_route(o) && o->health == HOST_ONLINE && o->fresh) return 0;
        o->render |= host_route(o) && o->fresh ? RESERVED : ALL_PANELS;
        return 1;
    }
    int fb = row*5+col+1;
    if (fb < 1 || fb > 20) return 1;
    if (down) o->pressed |= 1u << (fb-1); else o->pressed &= ~(1u << (fb-1));
    if (!down) {
        int owner = o->key_owner[fb-1];
        release_key(k,fb-1);
        if (owner == 4 && o->route == ONBOARD_L4) onboard_route(k,ONBOARD_L1);
        if (!host_route(o)) o->render |= 1u << (fb-1);
        return 1; /* release_key delivers to the press-time owner */
    }
    /* fb20 is e-stop on every on-board page and whenever the keyboard agent is active.
     * On L1/L4 with the agent inactive it is the Mac host's mode key (PRD §6.2). */
    if (fb == 20 && !fb20_host(k)) { stop(k,"page_key",now); return 1; }
    /* Repeated down of a held Mac HID key (its GD32 up was lost): still one down; the next up releases. */
    if (o->key_owner[fb-1] == 5) return 1;
    /* Do not overwrite a still-owned host press with a new press while its up
     * is backpressured. Safety keys and deadlines have already been handled. */
    if (o->pending_up & (1u<<(fb-1))) {
        kbd_host_drain(k);
        if (o->pending_up & (1u<<(fb-1))) return 1;
    }
    if (keycode) return 1; /* incompatible GD32 mapping: never synthesize a second action */
    if (suspended(o)) { o->key_owner[fb-1] = 0; return 1; } /* swallowed down; its up is swallowed too */
    if (host_route(o) && o->health == HOST_SUSPECT) return 1;
    if (host_route(o) && o->health == HOST_ONLINE && o->fresh) {
        o->key_owner[fb-1] = 1;
        /* L4 "回 L1" is decided locally on release: fb5 (row1,col5) in the 2026-10-21 layout (was fb14). */
        if (o->route == ONBOARD_L4 && fb == L4_BACK_FB) { o->key_owner[fb-1]=4; return 1; }
        if (col == k->ptt_col && row == k->ptt_row) {
            o->key_owner[fb-1] = 2;
            if (k->hid) hid_set_mod(k->hid,HID_MOD_RALT,1);
            return 1; /* physical PTT is consumed; no second Mac action */
        }
        uint8_t usage = host_hid_usage(fb);
        if (usage) {
            o->key_owner[fb-1] = 5; o->hid_usage[fb-1] = usage; o->hid_at[fb-1] = now;
            if (k->hid) hid_key(k->hid,usage,1);
            return 1; /* a Mac keyboard key, consumed like the PTT: no host key event */
        }
        return 0;
    }
    o->key_owner[fb-1] = 0;
    if (fb == 20) return 1; /* host mode key while the Mac page is not live: inert */
    /* Page-switch grace: the old page's labels may still be under the user's finger.
     * Swallow everything (no navigation, no host event) until a fresh frame or 2 s. */
    if (grace(o,now)) return 1;
    if (o->route == ONBOARD_HOME) {
        if (fb == 6) onboard_route(k,ONBOARD_L5);
        else if (fb == 7 || fb == 8) onboard_route(k,fb == 7 ? ONBOARD_L1 : ONBOARD_L4);
        else {
            ui_key(&o->ui,fb);
            if (fb == 19) open_settings(k,now);
            if (o->ui.route == UI_LS) onboard_route(k,ONBOARD_LS);
        }
    } else if (o->route == ONBOARD_LS) {
        ui_key(&o->ui,fb);
        if (fb == 19) open_settings(k,now);
        if (o->ui.route == UI_HOME) onboard_route(k,o->ui.back_home ? ONBOARD_HOME : o->back);
    } else if (o->route == ONBOARD_L5) {
        if (fb == 1 && k->ctl) ctl_set_allowed(k->ctl,!k->ctl->allowed);
        else if (fb == 11 && k->ctl && !k->ctl->confirm.pending && k->ctl->stop_agent) o->stop_ack = 1; /* acknowledge */
        else if ((fb == 11 || fb == 12) && k->ctl) ctl_confirm_key(k->ctl,fb == 11,now);
        else if (fb == 13) onboard_route(k,ONBOARD_HOME);
        else if (fb == 14) onboard_route(k,ONBOARD_LS);
    } else if (o->route == ONBOARD_AGENT) {
        if (fb != 16 && k->ctl) {
            for (int i=3;i<CTL_SCREEN_PAGES;i++) {
                ctl_page_t *p=&k->ctl->screen.page[i]; int ci=p->client;
                if (p->used && p->id==k->ctl->screen.current && ci>=0 && ci<CTL_MAX_CLIENTS &&
                    k->ctl->allowed && !k->ctl->estopped && (p->bound & (1u << (fb-1)))) {
                    o->key_owner[fb-1]=3; o->agent_owner[fb-1].page=p->id;
                    o->agent_owner[fb-1].client=ci; o->agent_owner[fb-1].generation=k->ctl->client_generation[ci];
                    ctl_key_event(k->ctl,row,col,1,now); break;
                }
            }
        }
    } else {
        if (fb == 5) onboard_route(k,ONBOARD_LS);
        else if (fb == 6 || fb == 18) onboard_route(k,ONBOARD_HOME);
        else if (fb == 7) onboard_route(k,ONBOARD_L5);
    }
    o->render = ALL_PANELS;
    return 1;
}
#define PH_BG 0x1082
#define PH_FG 0xef7d
#define PH_DIM 0x9492
static void label(uint16_t *p, int w, int h, int y, const char *s, uint16_t color)
{ ui_font_draw(p,w,h,8,y,s,color,w-16); }
#define LB(id) UI_LABELS[UI_##id]
/* Placeholder L1/L4 page while no fresh host frame is shown. Navigation labels appear
 * only when those keys act (never during the post-switch grace window). */
static void host_placeholder(kbd_t *k, int fb, const char **s, char *detail, size_t cap, int64_t now)
{
    onboard_t *o = k->onboard; const ds_result_t *r = &o->dsr; int nav = !grace(o,now);
    const char *mac = o->health == HOST_PROBING ? LB(MAC_PROBING) : o->health == HOST_ONLINE ? LB(PH_WAIT_FRAME) :
        o->health == HOST_SUSPECT ? LB(MAC_STALE) : r->udc == DS_UDC_DOWN ? LB(USB_DOWN) : r->udc == DS_UDC_UP ? LB(MAC_NOAPP) : LB(MAC_UNSEEN);
    if (fb == 21) {
        *s = o->health == HOST_PROBING ? LB(SUB_PROBING) : o->health == HOST_ONLINE ? LB(SUB_WAIT_FRAME) :
            o->health == HOST_SUSPECT ? LB(SUB_STALE) : r->udc == DS_UDC_DOWN || r->udc == DS_UDC_SUSPENDED ? LB(SUB_USB_DOWN) : LB(SUB_NOAPP);
        snprintf(detail,cap,"%s",o->route == ONBOARD_L1 ? LB(SESSIONS) : LB(MODES));
    } else if (fb == 1) { *s = LB(PH_MAC); snprintf(detail,cap,"%s",mac); }
    else if (fb == 2) {
        *s = LB(PH_USB);
        snprintf(detail,cap,"%s",r->udc == DS_UDC_UP ? LB(USB_UP) : r->udc == DS_UDC_DOWN ? LB(USB_DOWN) : r->udc == DS_UDC_SUSPENDED ? LB(USB_SLEEP) : LB(DASH));
    } else if (fb == 3) {
        *s = LB(PH_SEEN);
        if (o->seen) snprintf(detail,cap,"%lld%s",(long long)((now-o->seen)/1000),LB(SECONDS_AGO));
        else snprintf(detail,cap,"%s",LB(DASH));
    } else if (fb == 4) {
        *s = LB(PH_PHASE);
        snprintf(detail,cap,"%s",o->health == HOST_OFFLINE && o->handshake_required ? LB(PH_HANDSHAKE) : o->health == HOST_OFFLINE ? LB(DASH) : mac);
    } else if (fb == 5 && nav) *s = LB(SETTINGS);
    else if ((fb == 6 || fb == 18) && nav) *s = LB(HOME_TITLE);
    else if (fb == 7 && nav) *s = LB(AGENTPAGE);
    else if (fb == 16) { *s = LB(PH_PTT); snprintf(detail,cap,"%s",mac); } /* no Mac PTT until the page is live */
    else if (fb == 19) { *s = LB(WEB); snprintf(detail,cap,"%s",r->ip); }
    else if (fb == 20) { *s = LB(K2_HOLD_STOP); snprintf(detail,cap,"%s",LB(PH_MODEKEY)); } /* inert here */
    if (fb == 21 || fb == 5 || fb == 6 || fb == 7 || fb == 18) o->grace_shown |= !nav;
}
static void l5_text(kbd_t *k, int fb, const char **s, char *detail, size_t cap)
{
    static const enum ui_label names[] = {UI_L5_ALLOW, UI_L5_STATE, UI_L5_GOAL, UI_L5_STEP, UI_L5_INPUT,
        UI_L5_PAUSE, UI_TASK, UI_PROVIDER, UI_MODEL, UI_NETWORK, UI_L5_CONFIRM, UI_L5_CANCEL, UI_HOME_TITLE,
        UI_SETTINGS, UI_L5_SOURCE, UI_L5_PTT, UI_L5_REC, UI_L5_NEWGOAL, UI_L5_WEBTASK};
    static const enum ui_label task[] = {UI_TASK_NONE, UI_TASK_OBSERVING, UI_TASK_PLANNING, UI_TASK_ACTING,
        UI_TASK_CONFIRM, UI_TASK_DONE, UI_TASK_STOPPED, UI_TASK_ERROR};
    const ctl_t *c = k->ctl; const ui_status_t *st = &k->onboard->ui.st;
    *s = fb == 21 ? LB(L5_TITLE) : UI_LABELS[names[fb-1]];
    if (!c) return;
    if (fb == 1) snprintf(detail,cap,"%s",c->allowed ? LB(AG_ALLOWED) : LB(AG_DISABLED));
    if (fb == 2) snprintf(detail,cap,"%s",c->estopped ? LB(AG_STOPPED) : !c->allowed ? LB(AG_DISABLED) :
        UI_LABELS[task[st->task >= 0 && st->task < 8 ? st->task : 0]]);
    if (fb == 4) {
        if (c->confirm.pending) { *s = LB(L5_RISK); snprintf(detail,cap,"%s",c->confirm.risk); }
        else snprintf(detail,cap,"%d%%",c->ui.progress);
    }
    if (fb == 5) snprintf(detail,cap,"%s",c->ui.input);
    if (fb == 8 || fb == 9) {
        const ui_provider_t *p = &UI_PROVIDERS[st->provider >= 0 ? st->provider : 0];
        int m = st->model >= 0 ? st->model : 0;
        snprintf(detail,cap,"%s",st->provider == -2 ? LB(DASH) : fb == 8 ? p->id : p->models[m].id);
    }
    if (fb == 10) snprintf(detail,cap,"%s",st->profile == 1 ? LB(PROF_WIFI) : st->profile == -2 ? LB(DASH) : LB(PROF_MAC));
    if (fb == 3 || fb == 21) snprintf(detail,cap,"%s",c->confirm.pending ? c->confirm.summary : c->ui.summary);
    if (fb == 11) snprintf(detail,cap,"%s",!c->confirm.pending && k->onboard->ui.stop_notice ? LB(L5_ACK) :
        c->confirm.shown ? LB(L5_GUARD) : LB(L5_AWAIT));
}
static void render(kbd_t *k, int fb)
{
    onboard_t *o = k->onboard; const panel_t *p = &PANELS[fb-1];
    uint16_t *px = o->scratch; int w = p->w, h = p->h; int64_t now = kbd_now(k);
    footer_ctx(k);
    if (fb == 20 && fb20_host(k) && o->fresh) {
        if (!o->face20) o->restore |= RESTORE_FB20; /* host content, not a local face */
        return;
    }
    if (fb == 21 && safety_footer(o)) {
        /* Live/stale host page: only the footer band is ours, and only for attention. */
        if (ui_footer_attention(&o->ui)) {
            ui_render_footer(&o->ui,px,w,h);
            screen_set_tile565(&k->scr,fb,0,112,w,30,px+112*w); o->footer_host = 0;
        } else if (!o->footer_host && o->fresh) o->restore |= RESTORE_FOOTER;
        return;
    }
    ui_render_panel(&o->ui,fb,px,(size_t)w*h);
    if ((fb != 20 || fb20_host(k)) && o->route > ONBOARD_LS) {
        for (int i = 0; i < w*h; i++) px[i] = PH_BG;
        const char *s = ""; char detail[512] = "";
        if (host_route(o)) host_placeholder(k,fb,&s,detail,sizeof detail,now);
        else if (o->route == ONBOARD_L5) l5_text(k,fb,&s,detail,sizeof detail);
        else { s = fb == 21 ? LB(AGENT_PAGE_TITLE) : fb == 16 ? LB(L5_PTT) : ""; }
        label(px,w,h,8,s,PH_FG); label(px,w,h,40,detail,PH_DIM);
        if (host_route(o) && fb == 19) label(px,w,h,60,":8780",PH_DIM);
        if (fb == 21) ui_render_footer(&o->ui,px,w,h);
    }
    /* Local pressed feedback keeps labels and the reserved safety faces intact. */
    if (fb<20 && !host_route(o) && (o->pressed & (1u << (fb-1))))
        for (int x=0;x<w;x++) px[x]=0xffff;
    if (fb == 20) o->face20 = 0;
    if (fb == 21) o->footer_host = 0;
    screen_set_tile565(&k->scr,fb,0,0,w,h,px);
}
/* Put host pixels back on fb20/footer from the last fresh canvas (decoder idle only). */
static int restore_host(kbd_t *k)
{
    onboard_t *o = k->onboard;
    if (!o->restore) return 0;
    if (!host_route(o) || !o->fresh || o->publishing) { o->restore = 0; return 0; }
    pthread_mutex_lock(&o->mu); int idle = !o->job && !o->ready; pthread_mutex_unlock(&o->mu);
    if (!idle) return 0;
    int fb = o->restore & RESTORE_FB20 ? 20 : 21; const panel_t *p = &PANELS[fb-1];
    int y0 = fb == 21 ? 112 : 0;
    o->restore &= fb == 20 ? ~RESTORE_FB20 : ~RESTORE_FOOTER;
    if (fb == 20 ? !fb20_host(k) : attention(k)) return 1;
    for (int y = y0; y < p->h; y++) memcpy(o->scratch+(y-y0)*p->w,k->canvas+(p->y+y)*CANVAS_W+p->x,(size_t)p->w*2);
    screen_set_tile565(&k->scr,fb,0,y0,p->w,p->h-y0,o->scratch);
    if (fb == 20) o->face20 = 1; else o->footer_host = 1;
    return 1;
}
void onboard_service(kbd_t *k, int64_t now)
{
    onboard_t *o = k->onboard; if (!o) return;
    /* Status files: bounded and after HID service; one probe per call unless entering a page. */
    /* Copy the worker's latest snapshot (trylock, never waits); no file I/O on this thread. */
    if (o->ds) {
        if (o->ds_kick && !devstat_kick(o->ds)) o->ds_kick = 0;
        int stale = o->ds_stale;
        if (devstat_take(o->ds,&o->dsr,&stale) | (stale != o->ds_stale)) { o->ds_stale = stale; status_sync(k); }
    }
    pthread_mutex_lock(&o->mu);
    if (o->ready) {
        int rc = o->result; o->ready = 0;
        if (!rc) k->st.jpg_ok++; else if (rc == -2) k->st.jpg_size++; else k->st.jpg_bad++;
        if (!rc && o->decode_epoch == o->epoch && (host_route(o) || legacy_probe(o)) && !o->handshake_required) {
            pthread_mutex_unlock(&o->mu);
            onboard_valid_frame(k,now); /* may route HOME -> L1 once (boot auto-L1) */
            pthread_mutex_lock(&o->mu);
            if (host_route(o) && o->decode_epoch == o->epoch && o->health == HOST_ONLINE && now >= o->next_full) { o->next_full=now+250; o->publishing = 1; o->panel = 1; o->render = RESERVED; o->restore = 0; }
        }
    }
    if (!o->job && !o->ready && !o->publishing && o->next_jpg) {
        o->jpg=o->next_jpg; o->jpg_size=o->next_size; o->decode_epoch=o->next_epoch;
        o->next_jpg=NULL; o->job=1; pthread_cond_signal(&o->cv);
    }
    pthread_mutex_unlock(&o->mu);
    /* Staging acknowledges handling, not a promise to publish in a later epoch.
     * Validate again before any budget or overlay early return can retain it. */
    if (o->tile_fb && (o->tile_epoch!=o->epoch || o->tile_route!=o->route ||
        o->tile_generation!=o->generation ||
        !onboard_tile_allowed(k,o->tile_fb,o->tile_y,o->tile_h))) o->tile_fb=0;
    /* Reserved overlays win even when a stream of ready tiles keeps arriving. */
    if (o->render & (1u<<19)) {
        render(k,20); o->render &= ~(1u<<19); return;
    }
    if ((o->render & (1u<<20)) && safety_footer(o)) {
        render(k,21); o->render &= ~(1u<<20); return;
    }
    if (restore_host(k)) return;
    if (o->publishing) {
        int fb = o->panel; const panel_t *p = &PANELS[fb-1];
        if (!budget(o,fb,now)) return;
        o->panel++;
        /* The whole 142-px sub-screen is the host's unless an attention footer is due. */
        int h = fb == 21 && attention(k) ? 112 : p->h;
        if (fb != 20 || fb20_host(k)) {
            for (int y = 0; y < h; y++) memcpy(o->scratch+y*p->w,k->canvas+(p->y+y)*CANVAS_W+p->x,(size_t)p->w*2);
            screen_set_tile565(&k->scr,fb,0,0,p->w,h,o->scratch);
            if (fb == 20) o->face20 = 1;
            if (fb == 21) o->footer_host = h == p->h;
        }
        if (o->panel > 21) { o->publishing = 0; o->fresh = 1; o->render |= RESERVED; }
    } else if (o->tile_fb && budget(o,o->tile_fb,now)) {
        screen_set_tile565(&k->scr,o->tile_fb,o->tile_x,o->tile_y,o->tile_w,o->tile_h,o->tile_px);
        o->tile_fb=0;
    } else {
        if (host_route(o) && (o->fresh || o->health == HOST_SUSPECT)) o->render &= RESERVED;
        if (o->render && now >= o->next_render) {
        /* One panel per poll boundary; no full-canvas local allocation. Safety faces first. */
        int i = o->render & (1u << 19) ? 19 : o->render & (1u << 20) ? 20 : 0;
        while (!(o->render & (1u << i))) i++;
        if (!(i==20 && safety_footer(o)) && !budget(o,i+1,now)) return;
        render(k,i+1); o->render &= ~(1u << i);
        if (!o->render) o->next_render = now + 250;
        }
    }
    if (k->ctl && o->route == ONBOARD_L5 && o->confirm_pending && !k->ctl->confirm.shown && !o->render && !screen_wait_idle(&k->scr,0)) ctl_confirm_shown(k->ctl,now);
}
const ui_t *onboard_ui(const kbd_t *k) { return &k->onboard->ui; }
enum onboard_route onboard_current(const kbd_t *k) { return k->onboard->route; }
enum onboard_health onboard_health(const kbd_t *k) { return k->onboard->health; }

int onboard_status_wait(kbd_t *k, int timeout_ms)
{
    onboard_t *o = k->onboard; int stale = 0;
    if (!o || !o->ds || devstat_sync(o->ds,timeout_ms)) return -1;
    devstat_take_wait(o->ds,&o->dsr,&stale);
    o->ds_stale = stale; status_sync(k);
    return 0;
}
int onboard_timeout(kbd_t *k, int64_t now, int dflt)
{
    onboard_t *o = k->onboard; if (!o) return dflt;
    pthread_mutex_lock(&o->mu); int active = o->job || o->ready; pthread_mutex_unlock(&o->mu);
    if (active && dflt > 10) dflt = 10;
    if ((o->render & (1u<<19)) || ((o->render & (1u<<20)) && safety_footer(o))) return 0;
    if (o->restore && dflt > 10) dflt = 10;
    int producing=o->publishing || o->tile_fb || (o->render && now >= o->next_render);
    if (producing) {
        /* Budget exhaustion must sleep, not spin while waiting for fake/real time. */
        int fb=o->publishing ? o->panel : o->tile_fb;
        if (!fb) { int i=o->render & (1u<<19) ? 19 : o->render & (1u<<20) ? 20 : 0; while (!(o->render & (1u<<i))) i++; fb=i+1; }
        int64_t at=o->panel_at[fb-1];
        if (fb==20 || (!o->publishing && !o->tile_fb && fb==21 && safety_footer(o))) return 0;
        if (at<=now && o->bus_left[PANELS[fb-1].bus] >= (size_t)PANELS[fb-1].w*PANELS[fb-1].h*2) return 0;
        if (o->bus_left[PANELS[fb-1].bus] < (size_t)PANELS[fb-1].w*PANELS[fb-1].h*2 && at<o->budget_at) at=o->budget_at;
        if (at-now<dflt) dflt=(int)(at-now);
    }
    int64_t left = dflt;
    if (!producing && o->render && o->next_render-now < left) left = o->next_render-now;
    for (int i=0;i<2;i++) if (o->knob_down[i] && !o->knob_long[i] && o->knob_at[i]+1000-now < left) left=o->knob_at[i]+1000-now;
    for (int i=0;i<20;i++) if (o->key_owner[i]==5 && o->hid_at[i]+HID_KEY_HOLD_MS-now < left) left=o->hid_at[i]+HID_KEY_HOLD_MS-now;
    return left < 0 ? 0 : (int)left;
}
