/* Offline simulator bridge: fake clock, no writer threads, sockets, HID or tty. */
#include "kbd.h"
#include "ctl.h"
#include "onboard.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
static int64_t now;
static int fail_apply(void *ctx, const uicfg_t *cfg) { (void)ctx; (void)cfg; return -1; }
static int64_t clock_ms(void *ctx) { (void)ctx; return now; }
static void pump(kbd_t *k) { for (int i=0;i<24;i++) kbd_service(k,now); }
int main(void)
{
    kbd_t k; ctl_t c; char line[256];
    if (kbd_init(&k,"/nonexistent-onboard-headless","","headless") || onboard_enable(&k)) return 1;
    k.clock_ms=clock_ms; ctl_init(&c,NULL); k.ctl=&c;
    k.k2_estop=1; k2_estop_init(&k.k2,1);
    /* Simulator-only fake Apply. Production refuses Apply until U3's worker exists. */
    ui_t *u=(ui_t *)onboard_ui(&k); u->apply=NULL;
    while (fgets(line,sizeof line,stdin)) {
        int row,col,down,result,present; long long ms;
        if (sscanf(line,"key %d %d %d",&row,&col,&down)==3) {
            if (row<0 || row>105 || col<0 || col>105) return 2;
            uint8_t ev[]={0x16,(uint8_t)down,(uint8_t)row,(uint8_t)col,0,0}; kbd_on_gd32(&k,ev,6);
        } else if (sscanf(line,"time %lld",&ms)==1 && ms>=now) now=ms;
        else if (sscanf(line,"test %d %d",&result,&present)==2 && result>=0 && result<=UI_RESULT_AUTH) { u->injected_result=(enum ui_result)result; u->key_present=!!present; }
        else if (!strncmp(line,"host ",5)) {
            const char *m=line+5; host_frame f={1,HOST_CMD_JSON,(uint32_t)strcspn(m,"\n"),(const uint8_t *)m}; kbd_on_host_frame(&k,&f);
        } else if (!strcmp(line,"loss\n")) onboard_loss(&k);
        else if (!strcmp(line,"fault\n")) u->apply=fail_apply;
        else if (!strcmp(line,"canvas\n")) {
            pump(&k); printf("P6\n%d %d\n255\n",CANVAS_W,CANVAS_H);
            for (int y=0;y<CANVAS_H;y++) for (int x=0;x<CANVAS_W;x++) {
                uint16_t v=0;
                for (int i=0;i<PANEL_COUNT;i++) { const panel_t *p=&PANELS[i]; if (x>=p->x && x<p->x+p->w && y>=p->y && y<p->y+p->h) { v=k.scr.pending[i][(y-p->y)*p->w+x-p->x]; break; } }
                unsigned char rgb[]={(unsigned char)((v>>11)*255/31),(unsigned char)(((v>>5)&63)*255/63),(unsigned char)((v&31)*255/31)};
                if (fwrite(rgb,1,3,stdout)!=3) return 1;
            }
            fflush(stdout); continue;
        } else if (strcmp(line,"state\n")) return 2;
        pump(&k);
        static const char *const routes[]={"HOME","LS","L5","L1","L4","AGENT"};
        static const char *const health[]={"offline","probing","online","suspect"};
        printf("{\"page\":\"%s\",\"host_health\":\"%s\",\"stopped\":%s,\"ctl_page\":%d,\"settings\":{\"active\":%s,\"tab\":%d,\"focus\":%d,\"provider\":\"%s\",\"model\":\"%s\",\"profile\":\"%s\",\"pending\":%d,\"confirm_net\":%d,\"confirm_back\":%d,\"editor\":%d,\"proxy_port\":%d,\"brightness\":%d,\"test_result\":%d},\"toast\":\"%s\"}\n",
            routes[onboard_current(&k)],health[onboard_health(&k)],c.estopped?"true":"false",c.screen.current,u->route==UI_LS?"true":"false",u->tab,u->focus,UI_PROVIDERS[u->draft.provider].id,UI_PROVIDERS[u->draft.provider].models[u->draft.model].id,u->draft.wifi?"board_wifi":"mac_proxy",ui_change_count(u),u->confirm_net,u->confirm_back,u->editor,u->draft.proxy_port,u->draft.brightness,u->shown_result,UI_LABELS[u->toast]);
        fflush(stdout);
    }
    kbd_free(&k); return 0;
}
