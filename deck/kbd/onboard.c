#include "onboard.h"
#include "kbd.h"
#include "ctl.h"
#include "jpeg.h"
#include "ui_font.h"
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>

#define ALL_PANELS ((1u << PANEL_COUNT) - 1)
#define RESERVED ((1u << 19) | (1u << 20))
#define FRAME_MAX (1u << 20)
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
    int key_owner[20], knob_down[2], knob_long[2];
    int64_t knob_at[2];
    int last_page, last_stop, confirm_pending, incompatible; long confirm_id;
    pthread_t decoder;
    pthread_mutex_t mu; pthread_cond_t cv;
    int stop, job, ready, result; uint8_t *jpg, *next_jpg; size_t jpg_size, next_size; uint64_t next_epoch;
    kbd_t *kbd;
};
static void host_tap(kbd_t *k, int slot);
static int host_route(const onboard_t *o) { return o->route == ONBOARD_L1 || o->route == ONBOARD_L4; }
static int page_id(enum onboard_route r) { return r == ONBOARD_L5 ? 1 : r == ONBOARD_L1 || r == ONBOARD_L4 ? 2 : 0; }
static void release_mac(kbd_t *k)
{
    onboard_t *o = k->onboard;
    for (int i = 0; i < 20; i++) if (o->key_owner[i] == 2) {
        if (k->hid) hid_set_mod(k->hid, HID_MOD_RALT, 0);
        o->key_owner[i] = 0;
    }
}
static void boundary(kbd_t *k, int redraw)
{
    onboard_t *o = k->onboard;
    release_mac(k);
    if (redraw || host_route(o)) memset(o->key_owner,0,sizeof o->key_owner);
    free(o->next_jpg); o->next_jpg=NULL;
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
/* U3 owns durable config/test workers; never pretend an in-memory Apply was durable. */
static int unavailable_apply(void *ctx, const uicfg_t *cfg) { (void)ctx; (void)cfg; return -1; }
int onboard_enable(kbd_t *k)
{
    onboard_t *o = calloc(1, sizeof *o);
    if (!o) return -1;
    o->scratch = malloc(428 * 142 * 2);
    if (!o->scratch) { free(o); return -1; }
    ui_init(&o->ui, NULL, unavailable_apply, NULL);
    o->kbd = k; o->host_page=ONBOARD_L1; o->render = ALL_PANELS; o->epoch = 1;
    pthread_mutex_init(&o->mu, NULL); pthread_cond_init(&o->cv, NULL);
    if (pthread_create(&o->decoder, NULL, decode, o)) {
        pthread_mutex_destroy(&o->mu); pthread_cond_destroy(&o->cv); free(o->scratch); free(o); return -1;
    }
    k->onboard = o; k->local_ui = 0;
    return 0;
}
void onboard_free(kbd_t *k)
{
    onboard_t *o = k->onboard;
    if (!o) return;
    pthread_mutex_lock(&o->mu); o->stop = 1; pthread_cond_signal(&o->cv); pthread_mutex_unlock(&o->mu);
    pthread_join(o->decoder, NULL); free(o->jpg); free(o->next_jpg);
    pthread_mutex_destroy(&o->mu); pthread_cond_destroy(&o->cv);
    free(o->scratch); free(o); k->onboard = NULL;
}
void onboard_loss(kbd_t *k)
{
    onboard_t *o = k->onboard; if (!o) return;
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
    if (!host_route(o) || o->handshake_required || o->health == HOST_SUSPECT || n > FRAME_MAX || !n || kbd_now(k) < o->next_decode) return;
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
            o->next_decode=kbd_now(k)+250; o->job = 1; pthread_cond_signal(&o->cv);
        }
    }
    pthread_mutex_unlock(&o->mu);
}
int onboard_tile_allowed(kbd_t *k, int fb, int y, int h)
{
    onboard_t *o = k->onboard;
    return host_route(o) && o->health == HOST_ONLINE && o->fresh && !o->publishing &&
        fb >= 1 && fb <= 21 && fb != 20 && (fb != 21 || (y >= 0 && h > 0 && y <= 112-h));
}
static void stop(kbd_t *k, const char *reason, int64_t now)
{
    onboard_t *o = k->onboard;
    if (k->ctl && !k->ctl->estopped) ctl_estop(k->ctl, reason, now);
    ui_key(&o->ui,20); /* disarm draft confirmations through the accepted U1 transition */
    if (o->route == ONBOARD_HOME) boundary(k,1); else onboard_route(k,ONBOARD_HOME);
}
void onboard_tick(kbd_t *k, int64_t now)
{
    onboard_t *o = k->onboard; if (!o) return;
    if ((o->health == HOST_ONLINE || o->health == HOST_PROBING || o->health == HOST_SUSPECT) && now-o->seen >= 5000) onboard_loss(k);
    else if ((o->health == HOST_ONLINE || o->health == HOST_PROBING) && now-o->seen >= 3000) {
        o->health = HOST_SUSPECT; release_mac(k); o->epoch++; o->fresh = o->publishing = 0;
        k->hp.barrier = k->hp.len; o->render |= 1u << 20;
    }
    int online = o->health == HOST_ONLINE;
    if (o->ui.host_online != online) o->render |= (o->route == ONBOARD_HOME ? 1u : 0u) | (1u << 20);
    o->ui.host_online = online;
    if (k->ctl) {
        int pid = k->ctl->screen.current;
        if (k->ctl->estopped && !o->last_stop) { o->last_stop = 1; stop(k,"socket",now); }
        o->last_stop = k->ctl->estopped; o->ui.stopped = k->ctl->estopped;
        if (pid != o->last_page) {
            onboard_route(k,pid == 1 ? ONBOARD_L5 : pid >= 3 ? ONBOARD_AGENT : pid == 2 ? ONBOARD_L1 : ONBOARD_HOME);
            o->last_page = pid;
        }
        if (k->ctl->confirm.pending && (!o->confirm_pending || o->confirm_id != k->ctl->confirm.cid)) {
            onboard_route(k, ONBOARD_L5); boundary(k,1); o->confirm_id = k->ctl->confirm.cid;
        }
        if (o->confirm_pending != k->ctl->confirm.pending) o->render = ALL_PANELS;
        o->confirm_pending = k->ctl->confirm.pending;
        if (ctl_ui_dirty(k->ctl)) o->render = ALL_PANELS;
    }
    for (int i = 0; i < 2; i++) if (o->knob_down[i] && !o->knob_long[i] && now-o->knob_at[i] >= 1000) {
        o->knob_long[i] = 1;
        if (i == 0) {
            if (o->route == ONBOARD_LS) { ui_knob(&o->ui,1,UI_DOWN,o->knob_at[i]); ui_tick(&o->ui,now); if (o->ui.route == UI_HOME) onboard_route(k,ONBOARD_HOME); }
            else onboard_route(k,ONBOARD_HOME);
            o->render = ALL_PANELS;
        }
        /* K2 uses k2estop.c capability detection, never a speculative first hold. */
    }
}
static void host_tap(kbd_t *k, int slot)
{
    for (int down=1;down>=0;down--) {
        char j[160]; int n=host_json_key_state(j,sizeof j,slot,slot,down);
        if(n>0) kbd_host_send(k,KBD_EVENT_ID,HOST_CMD_JSON,j,(size_t)n);
    }
}
static void short_knob(kbd_t *k, int knob, int64_t now)
{
    onboard_t *o = k->onboard;
    if (knob == 2 && o->route == ONBOARD_HOME) {
        int fb=o->ui.home_focus+1;
        onboard_input(k,(fb-1)/5,(fb-1)%5,0,1,0,now);
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
    if (keycode) { o->incompatible=1; o->render |= 1u << 20; }
    if (knob) {
        int i = knob-1, slot = 100 + i*3;
        if (row == slot) {
            if (down && !o->knob_down[i]) { o->knob_down[i] = 1; o->knob_long[i] = 0; o->knob_at[i] = now; }
            else if (!down && o->knob_down[i]) {
                o->knob_down[i] = 0;
                if (i == 0 && o->ui.down[0]) ui_knob(&o->ui,1,UI_UP,now);
                if (!o->knob_long[i] && now-o->knob_at[i] < 1000 && !keycode) short_knob(k,knob,now);
            }
        } else if (down && !keycode && o->route <= ONBOARD_LS) ui_knob(&o->ui,knob,row == slot+1 ? UI_CCW : UI_CW,now);
        else if (down && !keycode && host_route(o) && o->health == HOST_ONLINE && o->fresh) return 0;
        o->render |= host_route(o) && o->fresh ? RESERVED : ALL_PANELS;
        return 1;
    }
    int fb = row*5+col+1;
    if (fb < 1 || fb > 20) return 1;
    if (!down) {
        int owner = o->key_owner[fb-1]; o->key_owner[fb-1] = 0;
        if (owner == 2 && k->hid) hid_set_mod(k->hid,HID_MOD_RALT,0);
        if (owner == 4 && o->route == ONBOARD_L4) onboard_route(k,ONBOARD_L1);
        if (owner == 3 && k->ctl && o->route == ONBOARD_AGENT) ctl_key_event(k->ctl,row,col,0,now);
        return owner != 1; /* never reinterpret an up on a new route */
    }
    if (fb == 20) { stop(k,"page_key",now); return 1; }
    if (keycode) return 1; /* incompatible GD32 mapping: never synthesize a second action */
    if (host_route(o) && o->health == HOST_SUSPECT) return 1;
    if (host_route(o) && o->health == HOST_ONLINE && o->fresh) {
        o->key_owner[fb-1] = 1;
        if (o->route == ONBOARD_L4 && fb == 14) { o->key_owner[fb-1]=4; return 1; }
        if (col == k->ptt_col && row == k->ptt_row) {
            o->key_owner[fb-1] = 2;
            if (k->hid) hid_set_mod(k->hid,HID_MOD_RALT,1);
            return 1; /* physical PTT is consumed; no second Mac action */
        }
        return 0;
    }
    o->key_owner[fb-1] = 0;
    if (o->route == ONBOARD_HOME) {
        if (fb == 6) onboard_route(k,ONBOARD_L5);
        else if (fb == 7 || fb == 8) onboard_route(k,fb == 7 ? ONBOARD_L1 : ONBOARD_L4);
        else { ui_key(&o->ui,fb); if (o->ui.route == UI_LS) onboard_route(k,ONBOARD_LS); }
    } else if (o->route == ONBOARD_LS) {
        ui_key(&o->ui,fb);
        if (o->ui.route == UI_HOME) onboard_route(k,o->back);
    } else if (o->route == ONBOARD_L5) {
        if (fb == 1 && k->ctl) ctl_set_allowed(k->ctl,!k->ctl->allowed);
        else if ((fb == 11 || fb == 12) && k->ctl) ctl_confirm_key(k->ctl,fb == 11,now);
        else if (fb == 13) onboard_route(k,ONBOARD_HOME);
        else if (fb == 14) onboard_route(k,ONBOARD_LS);
    } else if (o->route == ONBOARD_AGENT) {
        if (fb != 16 && k->ctl) { o->key_owner[fb-1] = 3; ctl_key_event(k->ctl,row,col,1,now); }
    } else {
        if (fb == 5) onboard_route(k,ONBOARD_LS);
        else if (fb == 6 || fb == 18) onboard_route(k,ONBOARD_HOME);
        else if (fb == 7) onboard_route(k,ONBOARD_L5);
    }
    o->render = ALL_PANELS;
    return 1;
}
static void label(uint16_t *p, int w, int h, int y, const char *s, uint16_t color)
{ ui_font_draw(p,w,h,8,y,s,color,w-16); }
static void render(kbd_t *k, int fb)
{
    onboard_t *o = k->onboard; const panel_t *p = &PANELS[fb-1];
    uint16_t *px = o->scratch; int w = p->w, h = p->h;
    ui_render_panel(&o->ui,fb,px,(size_t)w*h);
    if (fb != 20 && o->route > ONBOARD_LS) {
        for (int i = 0; i < w*h; i++) px[i] = 0x1082;
        const char *s = ""; char detail[512] = "";
        if (host_route(o)) {
            if (fb == 21) s = o->health == HOST_SUSPECT ? "Mac stale / actions frozen" : "Mac offline / waiting for full frame";
            else if (fb == 1) s = "Mac offline";
            else if (fb == 2) s = "USB link: unknown";
            else if (fb == 3) { s = "Last seen"; snprintf(detail,sizeof detail,"%lld ms",(long long)(kbd_now(k)-o->seen)); }
            else if (fb == 4) s = o->health == HOST_PROBING ? "Probing" : "Reconnect required";
            else if (fb == 5) s = "Settings";
            else if (fb == 6 || fb == 18) s = "Home";
            else if (fb == 7) s = "Agent L5";
            else if (fb == 16) s = "Mac PTT unavailable";
            else if (fb == 19) s = "Web setup";
        } else if (o->route == ONBOARD_L5) {
            static const char *const names[] = {"Allow / disable", "Agent state", "Goal", "Step / budget", "Input", "Pause unavailable", "Task", "Provider", "Model", "Network", "Confirm", "Cancel", "Home", "Settings", "Screen source", "PTT: ASR unavailable", "Recording: false", "New goal", "Web tasks"};
            s = fb == 21 ? "Keyboard agent (L5)" : names[fb-1];
            if (k->ctl) {
                if (fb == 1) snprintf(detail,sizeof detail,"%s",k->ctl->allowed ? "allowed" : "disabled");
                if (fb == 2) snprintf(detail,sizeof detail,"%s",k->ctl->estopped ? "stopped" : !k->ctl->allowed ? "disabled" : k->ctl->ui.state);
                if (fb == 4) {
                    if (k->ctl->confirm.pending) { s="Risk"; snprintf(detail,sizeof detail,"%s",k->ctl->confirm.risk); }
                    else snprintf(detail,sizeof detail,"%d%%",k->ctl->ui.progress);
                }
                if (fb == 3 || fb == 21) snprintf(detail,sizeof detail,"%s",k->ctl->confirm.pending ? k->ctl->confirm.summary : k->ctl->ui.summary);
                if (fb == 11) snprintf(detail,sizeof detail,"%s",k->ctl->confirm.shown ? "300 ms guard" : "await presentation");
            }
        } else { s = fb == 21 ? "Agent page / keyboard safety" : fb == 16 ? "PTT: ASR unavailable" : ""; }
        label(px,w,h,8,s,0xef7d); label(px,w,h,40,detail,0x9492);
    }
    if (fb == 21) {
        for (int y = 112; y < h; y++) for (int x = 0; x < w; x++) px[y*w+x] = 0x18e4;
        label(px,w,h,112,host_route(o) ? "Voice: Mac / Right Option" : "Voice: keyboard agent (L5)",0xef7d);
        label(px,w,h,126,o->incompatible ? (o->ui.stopped ? "STOPPED / GD32 mapping incompatible" : "GD32 mapping incompatible") : o->ui.stopped ? "STOPPED / K1 hold: Home" : o->health == HOST_SUSPECT && host_route(o) ? "Mac stale / actions frozen" : host_route(o) ? "K1: settings (L4) / K2: L1-L4" : "K1 hold: Home / K2 hold: E-STOP",0xef7d);
    }
    /* On live host pages only reserved pixels may be refreshed by local status. */
    if (fb == 21 && host_route(o) && (o->fresh || o->health == HOST_SUSPECT)) screen_set_tile565(&k->scr,fb,0,112,w,30,px+112*w);
    else screen_set_tile565(&k->scr,fb,0,0,w,h,px);
}
void onboard_service(kbd_t *k, int64_t now)
{
    onboard_t *o = k->onboard; if (!o) return;
    pthread_mutex_lock(&o->mu);
    if (o->ready) {
        int rc = o->result; o->ready = 0;
        if (!rc) k->st.jpg_ok++; else if (rc == -2) k->st.jpg_size++; else k->st.jpg_bad++;
        if (!rc && o->decode_epoch == o->epoch && host_route(o) && !o->handshake_required) {
            onboard_valid_frame(k,now);
            if (o->decode_epoch == o->epoch && o->health == HOST_ONLINE && now >= o->next_full) { o->next_full=now+250; o->publishing = 1; o->panel = 1; o->render = RESERVED; }
        }
    }
    if (!o->job && !o->ready && !o->publishing && o->next_jpg) {
        o->jpg=o->next_jpg; o->jpg_size=o->next_size; o->decode_epoch=o->next_epoch;
        o->next_jpg=NULL; o->job=1; pthread_cond_signal(&o->cv);
    }
    pthread_mutex_unlock(&o->mu);
    if (o->publishing) {
        int fb = o->panel++; const panel_t *p = &PANELS[fb-1];
        int h = fb == 21 ? 112 : p->h;
        if (fb != 20) {
            for (int y = 0; y < h; y++) memcpy(o->scratch+y*p->w,k->canvas+(p->y+y)*CANVAS_W+p->x,(size_t)p->w*2);
            screen_set_tile565(&k->scr,fb,0,0,p->w,h,o->scratch);
        }
        if (o->panel > 21) { o->publishing = 0; o->fresh = 1; o->render |= RESERVED; }
    } else {
        if (host_route(o) && (o->fresh || o->health == HOST_SUSPECT)) o->render &= RESERVED;
        if (o->render && now >= o->next_render) {
        /* One panel per poll boundary; no full-canvas local allocation. Safety faces first. */
        int i = o->render & (1u << 19) ? 19 : o->render & (1u << 20) ? 20 : 0;
        while (!(o->render & (1u << i))) i++;
        render(k,i+1); o->render &= ~(1u << i);
        if (!o->render) o->next_render = now + 250;
        }
    }
    if (k->ctl && o->route == ONBOARD_L5 && o->confirm_pending && !k->ctl->confirm.shown && !o->render && !screen_wait_idle(&k->scr,0)) ctl_confirm_shown(k->ctl,now);
}
const ui_t *onboard_ui(const kbd_t *k) { return &k->onboard->ui; }
enum onboard_route onboard_current(const kbd_t *k) { return k->onboard->route; }
enum onboard_health onboard_health(const kbd_t *k) { return k->onboard->health; }

int onboard_timeout(kbd_t *k, int64_t now, int dflt)
{
    onboard_t *o = k->onboard; if (!o) return dflt;
    pthread_mutex_lock(&o->mu); int active = o->job || o->ready; pthread_mutex_unlock(&o->mu);
    if (active && dflt > 10) dflt = 10;
    if (o->publishing || (o->render && now >= o->next_render)) return 0;
    int64_t left = dflt;
    if (o->render && o->next_render-now < left) left = o->next_render-now;
    for (int i=0;i<2;i++) if (o->knob_down[i] && !o->knob_long[i] && o->knob_at[i]+1000-now < left) left=o->knob_at[i]+1000-now;
    return left < 0 ? 0 : (int)left;
}
